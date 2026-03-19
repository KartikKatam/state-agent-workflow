# Performance Testing

Patterns for latency benchmarks, throughput tests, and CI regression detection.

## pytest-benchmark

```python
@pytest.mark.benchmark
def test_critical_path_latency(benchmark, system, standard_input):
    """Critical path latency must not regress."""
    result = benchmark(system.process, standard_input)
    assert result is not None
```

## CI Regression Detection

```bash
# Save baseline in CI
pytest tests/benchmarks/ --benchmark-save=baseline

# Compare in CI (fail if mean is 20% slower)
pytest tests/benchmarks/ --benchmark-compare=0001_baseline --benchmark-compare-fail=mean:20%
```

## Resource Leak Detection

### Memory (pytest-memray)

```python
@pytest.mark.limit_leaks("512 KB")
def test_no_memory_leak(system, input):
    """Processing should not leak more than 512KB per call."""
    for _ in range(100):
        _ = system.process(input)
```

### Manual Memory Tracking

```python
import tracemalloc, gc

@pytest.fixture
def track_memory():
    gc.collect()
    tracemalloc.start()
    baseline = tracemalloc.take_snapshot()
    yield
    gc.collect()
    current = tracemalloc.take_snapshot()
    stats = current.compare_to(baseline, "lineno")
    leaked = sum(s.size_diff for s in stats if s.size_diff > 0)
    tracemalloc.stop()
    assert leaked < 10 * 1024 * 1024, f"Memory leak: {leaked / 1024 / 1024:.1f} MB"
```

### File Handle Leak Detection

```python
@pytest.fixture
def track_file_handles():
    import psutil, os
    proc = psutil.Process(os.getpid())
    initial = proc.num_fds()
    yield
    assert proc.num_fds() <= initial + 5, "File handle leak detected"
```
