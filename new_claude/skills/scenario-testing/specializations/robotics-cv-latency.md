# Robotics-CV: Real-Time / Latency Testing

Deep-dive for testing inference latency, frame budgets, GPU performance, and real-time constraints.

## Frame Budgets

| Use Case | Budget | Percentile | Source |
|----------|--------|------------|--------|
| 30 fps video processing | 33.3 ms | P95 | Standard frame rate |
| 60 fps video processing | 16.7 ms | P95 | Standard frame rate |
| PX4 obstacle avoidance | < 33 ms @ 30 Hz | P99 | PX4 docs |
| PX4 global planner | < 100 ms @ 10 Hz | P95 | PX4 obstacle_avoidance docs |
| PX4 hold-mode failsafe | 500 ms timeout | Hard deadline | PX4: Hold if no setpoints >500ms |
| Drone edge inference | 6-11 ms typical | P95 | CMU SteelEagle benchmark |
| TensorRT single model | < 10 ms desktop GPU | P99 | TensorRT best practices |

**Leave 20% headroom** for pre/post-processing: inference budget = frame budget × 0.8.

## Warm-Up and Measurement Protocol

CUDA requires warm-up before stable measurements. Use CUDA events, not wall-clock.

```python
# Source: TensorRT best practices (200ms warm-up, ≥10 iterations measurement)

def benchmark_inference_latency(model, input_tensor, warmup=50, measure=200):
    """Standard warm-up + measurement with CUDA event timing."""
    with torch.no_grad():
        for _ in range(warmup):
            _ = model(input_tensor)
    torch.cuda.synchronize()

    latencies = []
    for _ in range(measure):
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record()
        with torch.no_grad():
            _ = model(input_tensor)
        end.record()
        torch.cuda.synchronize()
        latencies.append(start.elapsed_time(end))
    latencies = np.array(latencies)
    return {
        "mean": np.mean(latencies), "p95": np.percentile(latencies, 95),
        "p99": np.percentile(latencies, 99), "max": np.max(latencies),
    }

def test_inference_meets_frame_budget():
    """P95 latency must fit within frame budget for target FPS."""
    BUDGET_MS = (1000.0 / 30) * 0.8  # 30fps with 20% headroom = ~26.7ms
    stats = benchmark_inference_latency(model, sample_input)
    assert stats["p95"] < BUDGET_MS, f"P95 {stats['p95']:.1f}ms > {BUDGET_MS:.1f}ms"
```

## Regression and Stability Tests

### Statistical Regression Detection

```python
from scipy import stats as scipy_stats

def test_latency_no_regression(baseline_file="latency_baseline.json"):
    """Detect regression with Welch's t-test + 10% practical threshold."""
    baseline = load_baseline(baseline_file)
    current = benchmark_inference_latency(model, sample_input)
    t_stat, p_value = scipy_stats.ttest_ind(
        baseline["latencies"], current["latencies"], equal_var=False
    )
    mean_increase = (current["mean"] - baseline["mean"]) / baseline["mean"]
    assert not (p_value < 0.01 and mean_increase > 0.10), (
        f"Latency regression: {mean_increase:.1%} increase (p={p_value:.4f})"
    )
```

### GPU Memory Leak Detection

```python
def test_no_gpu_memory_leak(num_iterations=1000):
    """GPU memory must not grow unbounded during inference loop.
    Common leak sources: missing torch.no_grad, accumulating tensors in lists."""
    torch.cuda.reset_peak_memory_stats()
    initial_mem = torch.cuda.memory_allocated()
    with torch.no_grad():
        for i in range(num_iterations):
            output = model(sample_input)
            del output
    final_mem = torch.cuda.memory_allocated()
    growth = final_mem - initial_mem
    assert growth < 10 * 1024 * 1024, f"GPU memory grew {growth/1024/1024:.1f}MB"
```

### Thermal Throttling Detection

```python
def test_latency_stability_under_sustained_load(duration_seconds=60):
    """Detect thermal throttling: latency spike after sustained load.
    GPU throttles at ~85°C — detectable as >20% latency increase."""
    initial = benchmark_inference_latency(model, sample_input, warmup=50, measure=50)
    start = time.time()
    with torch.no_grad():
        while time.time() - start < duration_seconds:
            _ = model(sample_input)
    post = benchmark_inference_latency(model, sample_input, warmup=10, measure=50)
    degradation = (post["p95"] - initial["p95"]) / initial["p95"]
    assert degradation < 0.20, (
        f"Latency degraded {degradation:.1%} after {duration_seconds}s sustained load"
    )
```

### Input-Complexity Independence

```python
@given(num_objects=st.integers(0, 50))
def test_latency_bounded_regardless_of_detections(num_objects):
    """Inference latency should not scale with number of objects in frame."""
    image = generate_scene_with_n_objects(num_objects)
    stats = benchmark_inference_latency(model, image, warmup=10, measure=30)
    assert stats["p95"] < 50.0, f"P95={stats['p95']:.1f}ms with {num_objects} objects"
```

## Failure Modes

| Failure Mode | Detection | Source |
|---|---|---|
| **Cold-start spike** | First inference 10-100× slower (CUDA init, kernel JIT). Warm-up mandatory. | TensorRT best practices |
| **Thermal throttling** | GPU throttles at ~85°C. Latency increases >20% after sustained load. | NVIDIA GPU throttle docs |
| **CUDA context switching** | Multiple processes sharing GPU → spikes. Test under contention. | NVIDIA developer forums |
| **Batch size sensitivity** | Batch=1 underutilizes GPU; batch=max OOMs. Test at 1, 4, 8, max. | TensorRT benchmarking |
| **Dynamic shape recompilation** | New input shapes trigger kernel recompilation. Warm up each shape. | TensorRT docs |
| **GC pauses** | Python GC can pause 10-50ms. Use `gc.disable()` during benchmarking. | PyTorch community |
| **PX4 hold-mode timeout** | No setpoint for >500ms → Hold mode. Pipeline must produce within budget. | PX4 docs |

## Edge Cases

- **First inference after model load**: CUDA kernels compiled lazily. Always discard first N inferences.
- **Power-saving mode on edge devices**: Jetson may throttle unless in MAX perf mode (`nvpmodel -m 0`).
- **USB camera frame drops**: Camera delivers frames at irregular intervals; test with real timing variance.
- **Multi-model pipelines**: Latency of stage N depends on stage N-1's output size. Test with largest intermediate output.
- **TensorRT engine mismatch**: Engine built for different GPU → slow fallback. Assert engine GPU matches runtime.
