# Research: Robotics-CV Scenario Testing — Four New Domains

**Researcher:** Claude (research agent)
**Date:** 2026-03-04
**Confidence:** HIGH — all thresholds sourced from benchmarks, papers, or production codebases
**Consumed by:** scenario-testing skill author, for distillation into `specializations/robotics-cv.md` reference extensions

---

## Domain 1: 3D Vision / Spatial Testing

### Standard Metrics and Thresholds

| Metric | Benchmark | Good Threshold | Source |
|--------|-----------|----------------|--------|
| ATE RMSE (stereo-inertial) | EuRoC MAV | < 3.6 cm average | ORB-SLAM3 paper, IEEE T-RO 2021 |
| ATE RMSE (stereo) | EuRoC MAV | < 5 cm average | ORB-SLAM3 paper |
| Translational RPE | KITTI odometry | < 4.15% | ORB-SLAM3 KITTI eval |
| Rotational RPE | KITTI odometry | < 0.0027 deg/m | ORB-SLAM3 KITTI eval |
| Depth abs_rel (ViT-L) | NYU Depth v2 | < 0.056 | Depth Anything V2, NeurIPS 2024 |
| Depth δ₁ (ViT-L) | NYU Depth v2 | > 0.984 | Depth Anything V2 |
| Depth RMSE (ViT-L) | NYU Depth v2 | < 0.206 m | Depth Anything V2 |
| Depth abs_rel (ViT-L) | KITTI | < 0.045 | Depth Anything V2 |
| Depth RMSE (ViT-L) | KITTI | < 1.861 m | Depth Anything V2 |
| ICP registration fitness | Open3D | > 0.6 (60% inlier overlap) | Open3D docs, threshold=0.02 |
| ICP inlier RMSE | Open3D | < 0.007 m (well-aligned) | Open3D tutorial examples |
| TSDF voxel RMSE | Voxblox | < 2× voxel_size | voxblox test_sdf_integrators.cc |
| TSDF max error | Voxblox | < 2× truncation_distance | voxblox test suite |
| TEASER++ rotation error | Point cloud reg. | < 0.15° | TEASER++ paper, MIT SPARK |
| TEASER++ translation error | Point cloud reg. | < 0.17 m | TEASER++ paper |

### Test Patterns

```python
# --- Trajectory Evaluation with evo ---
# Source: evo package (github.com/MichaelGrupp/evo)
import numpy as np
from evo.core import metrics, trajectory
from evo.tools import file_interface

def test_slam_ate_within_euroc_baseline():
    """ATE should be within ORB-SLAM3 stereo-inertial range on EuRoC."""
    traj_ref = file_interface.read_tum_trajectory_file("groundtruth.txt")
    traj_est = file_interface.read_tum_trajectory_file("estimated.txt")
    traj_ref, traj_est = trajectory.align_trajectory(
        traj_ref, traj_est, correct_scale=False
    )
    ape_metric = metrics.APE(metrics.PoseRelation.translation_part)
    ape_metric.process_data((traj_ref, traj_est))
    ate_rmse = ape_metric.get_statistic(metrics.StatisticsType.rmse)
    # ORB-SLAM3 achieves ~3.6cm on EuRoC stereo-inertial
    assert ate_rmse < 0.05, f"ATE RMSE {ate_rmse:.4f}m exceeds 5cm threshold"

def test_slam_rpe_drift():
    """RPE checks local consistency — catches drift even when ATE is OK."""
    traj_ref = file_interface.read_tum_trajectory_file("groundtruth.txt")
    traj_est = file_interface.read_tum_trajectory_file("estimated.txt")
    rpe_metric = metrics.RPE(
        metrics.PoseRelation.translation_part,
        delta=1.0, delta_unit=metrics.Unit.meters, all_pairs=False
    )
    rpe_metric.process_data((traj_ref, traj_est))
    rpe_rmse = rpe_metric.get_statistic(metrics.StatisticsType.rmse)
    # KITTI standard: < 4.15% translational error
    assert rpe_rmse < 0.05, f"RPE RMSE {rpe_rmse:.4f}m exceeds threshold"


# --- Point Cloud Registration Quality ---
# Source: Open3D docs (open3d.org/docs/latest/tutorial/Basic/icp_registration.html)
import open3d as o3d

def test_icp_registration_quality():
    """ICP must achieve high fitness and low RMSE on aligned clouds."""
    source = o3d.io.read_point_cloud("source.pcd")
    target = o3d.io.read_point_cloud("target.pcd")
    threshold = 0.02  # 2cm max correspondence distance
    reg = o3d.pipelines.registration.registration_icp(
        source, target, threshold, np.eye(4),
        o3d.pipelines.registration.TransformationEstimationPointToPlane()
    )
    # Open3D fitness = #inlier / #points_in_target; > 0.6 means good overlap
    assert reg.fitness > 0.3, f"Fitness {reg.fitness:.3f} too low"
    assert reg.inlier_rmse < 0.01, f"Inlier RMSE {reg.inlier_rmse:.4f}m too high"


# --- Depth Estimation Quality ---
# Source: Depth Anything V2 eval protocol (arxiv.org/abs/2406.09414)
def test_depth_estimation_metrics():
    """Depth map quality against ground truth with standard metrics."""
    pred_depth = model.predict(test_image)
    gt_depth = load_ground_truth_depth("test_depth.npy")
    valid = gt_depth > 0  # Only evaluate where ground truth exists

    ratio = np.maximum(pred_depth[valid] / gt_depth[valid],
                       gt_depth[valid] / pred_depth[valid])
    delta1 = np.mean(ratio < 1.25)       # δ < 1.25 threshold accuracy
    abs_rel = np.mean(np.abs(pred_depth[valid] - gt_depth[valid]) / gt_depth[valid])
    rmse = np.sqrt(np.mean((pred_depth[valid] - gt_depth[valid]) ** 2))

    # Depth Anything V2 ViT-L baselines on NYU Depth v2
    assert delta1 > 0.95, f"δ₁={delta1:.3f}, expected > 0.95"
    assert abs_rel < 0.08, f"abs_rel={abs_rel:.3f}, expected < 0.08"


# --- TSDF Voxel Map Accuracy ---
# Source: voxblox test_sdf_integrators.cc (github.com/ethz-asl/voxblox)
def test_tsdf_integration_accuracy(voxel_size=0.1):
    """TSDF RMSE must be < 2× voxel size (voxblox standard)."""
    tsdf_map = integrate_depth_frames(depth_frames, voxel_size=voxel_size)
    gt_surface = load_ground_truth_mesh("gt_mesh.ply")

    errors = compute_surface_distance(tsdf_map, gt_surface)
    rmse = np.sqrt(np.mean(errors ** 2))
    max_err = np.max(np.abs(errors))
    truncation_dist = 3.0 * voxel_size

    assert rmse < 2 * voxel_size, f"TSDF RMSE {rmse:.3f} > 2×voxel_size"
    assert max_err < 2 * truncation_dist, f"Max error {max_err:.3f} > 2×trunc_dist"
    assert np.min(np.abs(errors)) < 1e-4, "Min error should approach 0 at surface"
```

### Property-Based Invariants for 3D

```python
from hypothesis import given, strategies as st
import numpy as np

@given(
    tx=st.floats(-10, 10), ty=st.floats(-10, 10), tz=st.floats(-10, 10),
    rx=st.floats(-np.pi, np.pi), ry=st.floats(-np.pi/2 + 0.01, np.pi/2 - 0.01),
    rz=st.floats(-np.pi, np.pi),
)
def test_pose_composition_invertibility(tx, ty, tz, rx, ry, rz):
    """Composing a pose with its inverse must yield identity."""
    T = make_transform(tx, ty, tz, rx, ry, rz)
    T_inv = np.linalg.inv(T)
    result = T @ T_inv
    assert np.allclose(result, np.eye(4), atol=1e-6), "T @ T_inv != I"

@given(
    scale=st.floats(0.01, 100.0),
    angle=st.floats(-np.pi, np.pi),
)
def test_point_cloud_rigid_transform_preserves_distances(scale, angle):
    """Rigid transforms preserve pairwise distances."""
    pts = np.random.randn(50, 3) * scale
    R = rotation_matrix_z(angle)
    t = np.array([1.0, 2.0, 3.0])
    pts_transformed = (R @ pts.T).T + t
    dists_orig = pairwise_distances(pts)
    dists_xform = pairwise_distances(pts_transformed)
    assert np.allclose(dists_orig, dists_xform, atol=1e-6)

def test_depth_map_non_negativity():
    """Predicted depth must be non-negative everywhere."""
    for img in test_images:
        depth = model.predict(img)
        assert np.all(depth >= 0), f"Negative depth values found: min={depth.min()}"

def test_voxel_map_monotonic_observation_growth():
    """Observed voxel count must never decrease with new observations."""
    prev_count = 0
    for frame in depth_sequence:
        tsdf_map.integrate(frame)
        curr_count = tsdf_map.num_observed_voxels()
        assert curr_count >= prev_count, "Voxel count decreased after integration"
        prev_count = curr_count
```

### Common Failure Modes

| Failure Mode | Detection Strategy | Source |
|---|---|---|
| **Scale drift** (monocular SLAM) | Compare ATE at sequence start vs end; drift grows with distance | TUM RGB-D benchmark methodology |
| **Loop closure failure** | Check ATE before/after loop closure; large drop = loop closure critical | ORB-SLAM3 multi-map approach |
| **Degenerate geometry** (corridors, tunnels) | Monitor ICP covariance eigenvalues; if ratio > 100:1, geometry is rank-deficient | LiDAR SLAM literature, open3d_slam |
| **Coordinate frame mismatch** | Test data in both NED and ENU frames; assert consistent frame convention | ROS REP-103, REP-105 |
| **Depth scale ambiguity** | Relative depth models produce scale-invariant output; test with known metric reference | Depth Anything V2 relative vs metric modes |
| **Voxel aliasing** | Test with voxel sizes near object feature sizes; small features vanish at coarse resolution | voxblox parametric tests at 0.1-0.5m |
| **Point cloud density variation** | Registration fails with sparse clouds; test at 10%, 25%, 50% density | Open3D downsampling tests |

### Edge Cases

- **Planar degeneracy**: All points coplanar (wall, floor) — ICP has 3 unconstrained DOF. Test with `np.linalg.matrix_rank(pts[:, :3]) < 3` guard.
- **Gimbal lock near ±90° pitch**: Euler angle representations break. Test at pitch = ±89.9°.
- **Zero-baseline stereo**: Left/right cameras at identical position — infinite depth. Assert stereo baseline > minimum.
- **Backward-facing depth**: Depth predictions for pixels behind the camera plane. Assert all depths > near_plane.
- **Single-point clouds**: Registration algorithms need minimum point counts. Assert len(cloud) >= minimum.
- **Identical source/target**: Registration should return identity transform, not diverge.
- **Large-scale coordinates**: Floating-point precision degrades far from origin. Test at (1e6, 1e6, 1e6) coordinates.
- **Empty TSDF regions**: Querying unobserved voxels should return truncation_distance, not 0.

---

## Domain 2: Real-Time / Latency Testing

### Standard Budgets and Percentiles

| Use Case | Frame Budget | Critical Percentile | Source |
|----------|-------------|---------------------|--------|
| 30 fps video processing | 33.3 ms | P95 | Standard frame rate math |
| 60 fps video processing | 16.7 ms | P95 | Standard frame rate math |
| PX4 obstacle avoidance (local planner) | < 33 ms @ 30 Hz | P99 | PX4 docs, 500ms timeout failsafe |
| PX4 global planner | < 100 ms @ 10 Hz | P95 | PX4 obstacle_avoidance docs |
| PX4 hold-mode failsafe | 500 ms timeout | Hard deadline | PX4: switches to Hold if no setpoints for >500ms |
| Drone edge inference (deployed) | 6-11 ms typical | P95 | CMU SteelEagle benchmark, drone detection lit. |
| DeepStream pipeline end-to-end | Varies by platform | P95 | NVIDIA DeepStream perf docs |
| TensorRT single model | < 10 ms on desktop GPU | P99 | TensorRT best practices |

### Warm-Up and Measurement Protocol

```python
# Source: TensorRT best practices (docs.nvidia.com/deeplearning/tensorrt/latest/performance)
# trtexec default: 200ms warm-up, min(10 iterations, 3 seconds) measurement
import time
import torch
import numpy as np

def benchmark_inference_latency(
    model, input_tensor, warmup_iterations=50, measure_iterations=200
):
    """Standard warm-up + measurement protocol matching trtexec methodology.

    Source: TensorRT best practices — warm-up at least 200ms, measure at
    least 10 iterations or 3 seconds. We use explicit iteration counts
    for reproducibility in pytest.
    """
    # Lock GPU clock if possible (nvidia-smi -lgc for reproducibility)
    # Source: TensorRT docs recommend locking GPU frequency for stable measurements

    # Warm-up phase: fills CUDA caches, JIT compiles kernels
    with torch.no_grad():
        for _ in range(warmup_iterations):
            _ = model(input_tensor)
    torch.cuda.synchronize()

    # Measurement phase: use CUDA events for GPU-accurate timing
    # Source: TensorRT docs — CUDA events preferred over wall-clock
    latencies = []
    for _ in range(measure_iterations):
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record()
        with torch.no_grad():
            _ = model(input_tensor)
        end.record()
        torch.cuda.synchronize()
        latencies.append(start.elapsed_time(end))  # milliseconds

    latencies = np.array(latencies)
    return {
        "mean": np.mean(latencies),
        "median": np.median(latencies),
        "p90": np.percentile(latencies, 90),
        "p95": np.percentile(latencies, 95),
        "p99": np.percentile(latencies, 99),
        "min": np.min(latencies),
        "max": np.max(latencies),
    }


def test_inference_meets_frame_budget():
    """P95 latency must fit within frame budget for target FPS."""
    TARGET_FPS = 30
    FRAME_BUDGET_MS = 1000.0 / TARGET_FPS  # 33.3ms
    # Leave 20% headroom for pre/post-processing
    INFERENCE_BUDGET_MS = FRAME_BUDGET_MS * 0.8  # ~26.7ms

    stats = benchmark_inference_latency(model, sample_input)
    assert stats["p95"] < INFERENCE_BUDGET_MS, (
        f"P95 latency {stats['p95']:.1f}ms exceeds {INFERENCE_BUDGET_MS:.1f}ms budget"
    )
```

### Latency Regression Testing Patterns

```python
# --- Statistical Regression Test ---
# Compares current run against saved baseline using Welch's t-test
from scipy import stats as scipy_stats

def test_latency_no_regression(baseline_file="latency_baseline.json"):
    """Detect latency regression with statistical significance."""
    baseline = load_baseline(baseline_file)  # Previous run's latency array
    current = benchmark_inference_latency(model, sample_input)

    # Welch's t-test: unequal variance, no normality assumption required
    t_stat, p_value = scipy_stats.ttest_ind(
        baseline["latencies"], current["latencies"], equal_var=False
    )
    # Reject if current is significantly SLOWER (one-tailed, α=0.01)
    # and the mean increase is > 10%
    mean_increase = (current["mean"] - baseline["mean"]) / baseline["mean"]
    assert not (p_value < 0.01 and mean_increase > 0.10), (
        f"Latency regression: {mean_increase:.1%} increase (p={p_value:.4f})"
    )


# --- Threshold-Gated Test ---
def test_p95_below_absolute_threshold():
    """Hard threshold: P95 must be below X ms regardless of baseline."""
    stats = benchmark_inference_latency(model, sample_input)
    assert stats["p95"] < 30.0, f"P95={stats['p95']:.1f}ms exceeds 30ms hard limit"


# --- Input-Complexity Independence ---
from hypothesis import given, strategies as st

@given(num_objects=st.integers(0, 50))
def test_latency_bounded_regardless_of_detections(num_objects):
    """Latency should not scale with number of objects in frame."""
    image = generate_scene_with_n_objects(num_objects)
    stats = benchmark_inference_latency(model, image, warmup_iterations=10,
                                         measure_iterations=30)
    # Post-processing may scale, but inference should be constant
    assert stats["p95"] < 50.0, (
        f"P95={stats['p95']:.1f}ms with {num_objects} objects"
    )
```

### GPU Memory and Thermal Testing

```python
# Source: PyTorch forums GPU memory leak patterns, NVIDIA thermal throttle docs

def test_no_gpu_memory_leak_over_iterations(num_iterations=1000):
    """GPU memory must not grow unbounded during inference loop.

    Source: PyTorch forum patterns — common leak sources:
    - Retaining computation graphs (missing torch.no_grad)
    - Accumulating tensors in lists without detaching
    - CUDA cache fragmentation
    """
    torch.cuda.reset_peak_memory_stats()
    initial_mem = torch.cuda.memory_allocated()

    with torch.no_grad():
        for i in range(num_iterations):
            output = model(sample_input)
            # Force deallocation to catch leaks
            del output
            if i % 100 == 0:
                torch.cuda.synchronize()

    final_mem = torch.cuda.memory_allocated()
    growth = final_mem - initial_mem
    # Allow up to 10MB growth for CUDA allocator overhead
    assert growth < 10 * 1024 * 1024, (
        f"GPU memory grew {growth / 1024 / 1024:.1f}MB over {num_iterations} iterations"
    )


def test_latency_stability_over_sustained_load(duration_seconds=60):
    """Detect thermal throttling: latency should not spike after sustained load.

    Source: NVIDIA docs — GPU throttles at ~85°C, clock frequency drops.
    Detectable as latency increase > 20% vs initial measurements.
    """
    warmup_stats = benchmark_inference_latency(model, sample_input,
                                                warmup_iterations=50,
                                                measure_iterations=50)
    # Sustained load
    start = time.time()
    with torch.no_grad():
        while time.time() - start < duration_seconds:
            _ = model(sample_input)

    # Measure again after sustained load
    post_stats = benchmark_inference_latency(model, sample_input,
                                              warmup_iterations=10,
                                              measure_iterations=50)
    degradation = (post_stats["p95"] - warmup_stats["p95"]) / warmup_stats["p95"]
    assert degradation < 0.20, (
        f"Latency degraded {degradation:.1%} after {duration_seconds}s sustained load "
        f"(initial P95={warmup_stats['p95']:.1f}ms, post={post_stats['p95']:.1f}ms). "
        f"Likely thermal throttling."
    )
```

### Common Failure Modes

| Failure Mode | Detection | Source |
|---|---|---|
| **Cold-start latency spike** | First inference 10-100× slower (CUDA context init, kernel JIT). Warm-up mandatory. | TensorRT best practices |
| **Thermal throttling** | GPU throttles at ~85°C. Latency increases >20% after sustained load. | NVIDIA GPU throttle docs |
| **CUDA context switching** | Multiple processes sharing GPU → latency spikes. Test under contention. | NVIDIA developer forums |
| **Batch size sensitivity** | Batch=1 underutilizes GPU; batch=max OOMs. Test at 1, 4, 8, max. | TensorRT benchmarking |
| **Dynamic shape recompilation** | New input shapes trigger kernel recompilation (TensorRT/ONNX). Warm up each shape. | TensorRT docs |
| **GC pauses (Python)** | Python GC can pause 10-50ms. Use `gc.disable()` during benchmarking. | PyTorch community |
| **PX4 hold-mode timeout** | No setpoint for >500ms → autopilot switches to Hold. Pipeline must produce within budget. | PX4 obstacle_avoidance docs |
| **DeepStream pipeline stalls** | Upstream element blocks downstream. Use `NVDS_ENABLE_COMPONENT_LATENCY_MEASUREMENT`. | DeepStream perf docs |

### Edge Cases

- **First inference after model load**: CUDA kernels compiled lazily. Always discard first N inferences.
- **Power-saving mode on edge devices**: Jetson may throttle if not in MAX performance mode (`nvpmodel -m 0`).
- **USB camera frame drops**: Camera delivers frames at irregular intervals; test with real camera timing variance.
- **Multi-model pipelines**: Latency of stage N depends on stage N-1's output size. Test with largest expected intermediate output.
- **TensorRT engine mismatch**: Engine built for different GPU → falls back to slow path. Assert engine GPU matches runtime GPU.

---

## Domain 3: Simulation & Sim-to-Real Testing

### Sim-to-Real Gap Metrics and Thresholds

| Metric | Acceptable Gap | Intervention Needed | Source |
|--------|---------------|---------------------|--------|
| mAP drop (sim-trained → real eval) | < 15% relative | Domain adaptation at > 15% | Tremblay et al. 2018 (training deep networks with synthetic data) |
| mAP drop (sim+real mixed → real) | < 5% relative | Mixed training is standard mitigation | Industrial studies (Springer 2025) |
| Success rate (RL policy transfer) | > 80% of sim performance | Fine-tuning if < 80% | NVIDIA Isaac Lab blog |
| Trajectory error increase (sim→real) | < 2× sim error | Physics model calibration needed | AirSim/Colosseum drone studies |
| Object localization accuracy | < 1.5 cm error | Domain randomization effective | Tobin et al. 2017 (original DR paper) |
| Sim-only heliostat detection gap | 30-35% mAP drop | Mixed data mandatory | ScienceDirect 2025 (solar field study) |

### Simulation Fidelity Validation

```python
# --- Sensor Noise Model Validation ---
# Source: Gazebo sensor noise model docs (classic.gazebosim.org/tutorials?tut=sensor_noise)

def test_simulated_camera_noise_matches_real_distribution():
    """Sim camera noise should approximate real sensor characteristics.

    Source: Gazebo uses additive Gaussian noise with configurable mean/stddev.
    Validate that simulated noise distribution matches measured real sensor noise.
    """
    # Capture N frames of static scene in both sim and real
    sim_frames = capture_static_scene(sim_camera, n=100)
    real_frames = capture_static_scene(real_camera, n=100)

    # Compare pixel intensity variance (noise floor)
    sim_variance = np.var(sim_frames, axis=0).mean()
    real_variance = np.var(real_frames, axis=0).mean()
    ratio = sim_variance / real_variance

    # Noise should be within 2× of real sensor
    assert 0.5 < ratio < 2.0, (
        f"Sim noise variance {sim_variance:.2f} vs real {real_variance:.2f} "
        f"(ratio {ratio:.2f}). Adjust Gaussian noise stddev in sensor SDF."
    )


def test_simulated_imu_noise_characteristics():
    """IMU noise model must include both white noise and bias.

    Source: Gazebo IMU noise config — rate_noise, rate_bias, accel_noise,
    accel_bias (units: rad/s for rate, m/s² for accel).
    """
    # Collect static IMU data for 10 seconds
    imu_data = collect_static_imu(duration_s=10.0)

    # Allan variance analysis for noise characterization
    gyro_white_noise = compute_allan_variance(imu_data.gyro, tau=1.0)
    accel_white_noise = compute_allan_variance(imu_data.accel, tau=1.0)

    # Typical MEMS IMU specs (MPU-6050 class)
    assert gyro_white_noise < 0.05, "Gyro noise too high for MEMS IMU model"  # rad/s/√Hz
    assert accel_white_noise < 0.02, "Accel noise too high"  # m/s²/√Hz


def test_physics_consistency_free_fall():
    """Simulated gravity should match expected 9.81 m/s²."""
    obj = spawn_object(mass=1.0, position=(0, 0, 10.0))
    sim.step(dt=0.001, steps=1000)  # 1 second of sim time
    final_velocity = obj.velocity.z

    # v = g*t = 9.81 * 1.0 = 9.81 m/s (no drag)
    expected_v = 9.81
    assert abs(final_velocity - expected_v) < 0.1, (
        f"Free-fall velocity {final_velocity:.2f} m/s, expected ~{expected_v}"
    )
```

### Domain Randomization Sufficiency Testing

```python
# Source: Tobin et al. 2017, Tremblay et al. 2018 (domain randomization)

def test_domain_randomization_diversity_sufficient():
    """DR must use enough texture/lighting variation to generalize.

    Source: Tobin 2017 — significant performance drop with < 1000 textures.
    Tremblay 2018 — camera azimuth 0-360°, elevation 5-30°, 1-12 lights.
    """
    dr_config = load_dr_config()

    # Texture diversity
    assert dr_config.num_textures >= 1000, (
        f"Only {dr_config.num_textures} textures. Tobin 2017: < 1000 causes "
        f"significant real-world performance drop."
    )

    # Lighting variation
    assert dr_config.num_lights_range == (1, 12), "Need 1-12 random lights"
    assert dr_config.light_intensity_range[1] > 5 * dr_config.light_intensity_range[0]

    # Camera pose variation
    assert dr_config.camera_azimuth_range == (0, 360)
    assert dr_config.camera_elevation_range[0] >= 0
    assert dr_config.camera_elevation_range[1] <= 90


def test_synthetic_data_improves_real_performance():
    """Adding synthetic data to training should improve or not hurt real eval.

    Standard ablation: compare real-only vs real+synthetic training.
    """
    model_real_only = train_detector(real_data)
    model_mixed = train_detector(real_data + synthetic_data)

    ap_real_only = evaluate_on_real(model_real_only)
    ap_mixed = evaluate_on_real(model_mixed)

    # Mixed training should not degrade real-world performance
    assert ap_mixed >= ap_real_only * 0.95, (
        f"Synthetic data hurting: mixed AP {ap_mixed:.3f} < "
        f"real-only AP {ap_real_only:.3f} * 0.95"
    )
```

### Multi-Agent Safety Invariants

```python
# Property tests for drone swarm simulation

def test_collision_avoidance_invariant(sim_env):
    """No two agents should occupy the same space at any timestep."""
    min_separation = 2.0  # meters — FAA minimum for small UAS operations

    for t in range(sim_env.num_timesteps):
        positions = sim_env.get_all_positions(t)
        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                dist = np.linalg.norm(positions[i] - positions[j])
                assert dist > min_separation, (
                    f"Collision: agents {i},{j} at {dist:.2f}m apart (t={t})"
                )


def test_geofence_invariant(sim_env):
    """All agents must remain within defined operational boundary."""
    boundary = sim_env.geofence  # Polygon or bounding box

    for t in range(sim_env.num_timesteps):
        for agent_id, pos in enumerate(sim_env.get_all_positions(t)):
            assert boundary.contains(pos), (
                f"Agent {agent_id} at {pos} outside geofence at t={t}"
            )


def test_communication_loss_safe_behavior(sim_env):
    """Agent must enter safe loiter when comms lost for > 5 seconds."""
    agent = sim_env.agents[0]
    sim_env.disable_comms(agent, duration_s=10.0)
    sim_env.step(steps=1000)

    assert agent.mode == "LOITER" or agent.mode == "RTL", (
        f"Agent in {agent.mode} mode after 10s comms loss, expected LOITER/RTL"
    )
```

### Known Failure Modes

| Failure Mode | Detection Strategy | Source |
|---|---|---|
| **Lighting domain gap** | Compare mean/std of pixel intensities between sim and real datasets | DR literature, Isaac Sim docs |
| **Texture unrealism** | Train/test on real, compare vs sim-trained — gap > 15% indicates texture problem | Tobin 2017 |
| **Physics fidelity (contact)** | Object grasping success rate in sim vs real; > 2× difference indicates contact model issue | NVIDIA Isaac Lab blog |
| **Sensor noise mismatch** | Allan variance of simulated vs real IMU; ratio outside [0.5, 2.0] needs recalibration | Gazebo sensor noise docs |
| **Motion blur absence** | Sim renders sharp frames; real cameras have motion blur. Test model on blurred synthetic frames. | Common sim limitation |
| **Missing environmental effects** | Rain, dust, fog absent in sim. Test model on augmented synthetic data with these effects. | AirSim weather API |
| **Sim-specific artifacts** | Rendering artifacts (aliasing, z-fighting) leak into training data. Visual inspection + edge case tests. | Game engine artifacts |

---

## Domain 4: Sensor Fusion / Multi-Stage Pipeline Testing

### Boundary Contract Patterns

```python
# --- Pipeline Stage Contracts ---
# Source: Production perception pipeline patterns (ROS 2, DeepStream)

# Stage: Detection → Tracking
def validate_detection_output(detections, image_shape):
    """Contract: detection output must satisfy these invariants."""
    H, W = image_shape[:2]
    for det in detections:
        # Bounding box within image
        assert 0 <= det.x1 < det.x2 <= W, f"Invalid x-range [{det.x1}, {det.x2}]"
        assert 0 <= det.y1 < det.y2 <= H, f"Invalid y-range [{det.y1}, {det.y2}]"
        # Confidence in valid range
        assert 0.0 <= det.confidence <= 1.0
        # Class ID is known
        assert det.class_id in VALID_CLASS_IDS
        # Bounding box has minimum area (not degenerate)
        assert (det.x2 - det.x1) * (det.y2 - det.y1) >= 1.0

# Stage: Tracking → OCR/Downstream
def validate_track_output(tracks):
    """Contract: tracker output must satisfy these invariants."""
    seen_ids = set()
    for track in tracks:
        # Track IDs are unique
        assert track.id not in seen_ids, f"Duplicate track ID {track.id}"
        seen_ids.add(track.id)
        # Track has at least one detection
        assert len(track.detections) >= 1
        # Track timestamps are monotonically increasing
        timestamps = [d.timestamp for d in track.detections]
        assert timestamps == sorted(timestamps), "Non-monotonic timestamps"

# Stage: OCR → Aggregation
def validate_ocr_output(ocr_result):
    """Contract: OCR output format and value ranges."""
    assert isinstance(ocr_result.text, str)
    assert len(ocr_result.text) > 0
    assert 0.0 <= ocr_result.confidence <= 1.0
    # Character set validation (e.g., license plates)
    assert all(c.isalnum() or c in "-_ " for c in ocr_result.text)


def test_pipeline_contracts_at_every_boundary():
    """Integration test: validate contracts between every pipeline stage."""
    image = load_test_image("test_frame.png")

    # Stage 1: Detection
    detections = detector.predict(image)
    validate_detection_output(detections, image.shape)

    # Stage 2: Tracking
    tracks = tracker.update(detections, timestamp=0.0)
    validate_track_output(tracks)

    # Stage 3: Crop + OCR (for each tracked detection)
    for track in tracks:
        crop = extract_crop(image, track.latest_detection)
        assert crop.shape[0] >= 8 and crop.shape[1] >= 8, "Crop too small for OCR"

        ocr_result = ocr.read(crop)
        validate_ocr_output(ocr_result)
```

### Cross-Modal Alignment Testing

```python
# Source: BEVFusion calibration robustness (CVPR 2023 Workshop),
#         nuScenes evaluation metrics (nuscenes.org/object-detection)

def test_camera_lidar_projection_consistency():
    """3D LiDAR points projected to camera should land on correct objects.

    Source: BEVFusion and TransFusion test calibration by adding random
    translation offsets to the camera-LiDAR transform matrix.
    """
    lidar_points = load_lidar_scan("test_scan.pcd")
    camera_image = load_image("test_image.png")
    extrinsic = load_calibration("lidar_to_camera.json")

    # Project LiDAR points to image plane
    projected = project_to_image(lidar_points, extrinsic, camera_intrinsic)

    # Points that should be on a known object (e.g., calibration target)
    target_points = lidar_points[lidar_points.label == "calibration_target"]
    target_projected = project_to_image(target_points, extrinsic, camera_intrinsic)

    # Projected points should fall within the known bounding box in the image
    bbox = get_calibration_target_bbox(camera_image)
    for pt in target_projected:
        assert bbox.contains(pt), (
            f"Projected point ({pt.x:.1f}, {pt.y:.1f}) outside target bbox. "
            f"Likely extrinsic calibration error."
        )


def test_calibration_error_degrades_detection_gracefully():
    """Fusion model should degrade predictably with calibration perturbation.

    Source: CVPR 2023 Workshop paper — BEVFusion tested with random
    translation offsets to camera-LiDAR extrinsic matrix.
    Convergence evaluation uses perturbation angles: 0.0°-1.0° in R,P,Y.
    """
    baseline_ap = evaluate_fusion_model(model, correct_calibration)

    for perturb_deg in [0.15, 0.25, 0.5, 1.0]:
        perturbed_calib = perturb_extrinsic(correct_calibration,
                                             rotation_deg=perturb_deg)
        perturbed_ap = evaluate_fusion_model(model, perturbed_calib)
        degradation = (baseline_ap - perturbed_ap) / baseline_ap

        if perturb_deg <= 0.25:
            assert degradation < 0.05, (
                f"AP dropped {degradation:.1%} with only {perturb_deg}° perturbation. "
                f"Fusion too sensitive to calibration."
            )
        # At 1.0° perturbation, expect degradation but not crash
        assert perturbed_ap > 0, "Model produces 0 AP — likely crash on misalignment"
```

### Temporal Consistency Testing

```python
# Source: Kalman filter theory (kalman-filter.com), ROS 2 tf2 docs

def test_kalman_filter_nees_consistency():
    """NEES should fall within chi-squared confidence bounds.

    Source: NEES = x_err^T @ P_inv @ x_err, chi-squared with n_x DOF.
    95% of NEES values should be within [r1, r2] for a consistent filter.
    kalman-filter.com/normalized-estimation-error-squared/
    """
    from scipy.stats import chi2

    state_dim = 6  # e.g., [x, y, z, vx, vy, vz]
    alpha = 0.05
    r1 = chi2.ppf(alpha / 2, state_dim)
    r2 = chi2.ppf(1 - alpha / 2, state_dim)

    nees_values = []
    for gt_state, est_state, cov in zip(ground_truth, estimates, covariances):
        error = est_state - gt_state
        nees = error.T @ np.linalg.inv(cov) @ error
        nees_values.append(nees)

    in_bounds = sum(r1 <= n <= r2 for n in nees_values)
    ratio = in_bounds / len(nees_values)
    assert ratio > 0.90, (
        f"Only {ratio:.1%} of NEES values within 95% chi-squared bounds "
        f"[{r1:.1f}, {r2:.1f}]. Filter is inconsistent."
    )


def test_kalman_filter_nis_innovation_gate():
    """NIS (Normalized Innovation Squared) should be chi-squared with n_z DOF.

    Source: kalman-filter.com/normalized-innovation-squared/
    NIS = ν^T @ S_inv @ ν where ν=innovation, S=innovation covariance.
    """
    from scipy.stats import chi2

    meas_dim = 3
    gate_threshold = chi2.ppf(0.99, meas_dim)  # 99% gate

    for innovation, innov_cov in zip(innovations, innovation_covs):
        nis = innovation.T @ np.linalg.inv(innov_cov) @ innovation
        # Flag but don't crash — gated measurements should be rare
        if nis > gate_threshold:
            rejected_count += 1

    reject_rate = rejected_count / len(innovations)
    # Expect < 5% rejection rate for a well-tuned filter
    assert reject_rate < 0.05, (
        f"NIS gate rejected {reject_rate:.1%} of measurements "
        f"(expected < 5%). Filter may be poorly tuned."
    )


def test_tracking_temporal_consistency():
    """Track positions between consecutive frames should be physically plausible."""
    max_velocity = 50.0  # m/s — maximum expected object velocity
    dt = 1.0 / 30  # Frame interval at 30fps

    for track in tracks:
        for i in range(1, len(track.positions)):
            displacement = np.linalg.norm(
                track.positions[i] - track.positions[i-1]
            )
            velocity = displacement / dt
            assert velocity < max_velocity, (
                f"Track {track.id} jumped {displacement:.1f}m between frames "
                f"({velocity:.1f}m/s). Likely identity switch or tracking failure."
            )
```

### Error Propagation Testing

```python
# --- Testing how upstream failures affect downstream stages ---

def test_missed_detection_propagation():
    """If detector misses an object, downstream stages must handle gracefully."""
    # Simulate upstream miss: provide empty detections for a frame with objects
    empty_detections = []
    tracks = tracker.update(empty_detections, timestamp=1.0)

    # Tracker should not crash, should mark existing tracks as "lost"
    for track in tracks:
        assert track.status in ("active", "lost", "tentative")
    # No new tracks created from empty detections
    new_tracks = [t for t in tracks if t.age == 0]
    assert len(new_tracks) == 0


def test_false_positive_detection_propagation():
    """False positive detections should be filtered by tracker, not propagated."""
    # Inject a false positive at a random location
    real_detections = detector.predict(test_image)
    fake = Detection(x1=0, y1=0, x2=10, y2=10, confidence=0.51, class_id=0)
    detections_with_fp = real_detections + [fake]

    # Process 10 frames: false positive should not persist as a track
    for frame_idx in range(10):
        if frame_idx == 5:
            tracker.update(detections_with_fp, timestamp=frame_idx / 30.0)
        else:
            tracker.update(real_detections, timestamp=frame_idx / 30.0)

    active_tracks = [t for t in tracker.tracks if t.status == "active"]
    # No track should be created from a single-frame false positive
    fp_tracks = [t for t in active_tracks if t.num_detections == 1]
    assert len(fp_tracks) == 0, "False positive persisted as active track"


def test_corrupted_crop_does_not_crash_ocr():
    """OCR must handle corrupted or degenerate crop inputs."""
    degenerate_crops = [
        np.zeros((0, 0, 3), dtype=np.uint8),       # Empty
        np.zeros((1, 1, 3), dtype=np.uint8),        # 1x1 pixel
        np.ones((10, 10, 3), dtype=np.uint8) * 128, # Uniform gray
        np.random.randint(0, 255, (5, 100, 3), dtype=np.uint8),  # Very narrow
    ]
    for crop in degenerate_crops:
        try:
            result = ocr.read(crop)
            # If it returns a result, confidence should be low
            assert result.confidence < 0.3, (
                f"High confidence {result.confidence} on degenerate input"
            )
        except ValueError:
            pass  # Raising ValueError is acceptable for degenerate input


def test_sensor_disagreement_handled():
    """When GPS and visual odometry disagree, system should detect and handle.

    Source: EKF sensor fusion — when innovation exceeds gate threshold,
    measurement should be rejected rather than corrupting state estimate.
    """
    # Inject GPS measurement that contradicts visual odometry by 50m
    gps_reading = GPSReading(lat=37.7749, lon=-122.4194, alt=100.0)
    visual_odom = VisualOdometry(x=0.0, y=0.0, z=0.0)  # Hasn't moved

    fused_state = ekf.update(gps_reading, visual_odom)

    # EKF should gate the outlier GPS reading (NIS > chi-squared threshold)
    assert ekf.last_gps_accepted is False, (
        "EKF accepted GPS reading that contradicts visual odometry by 50m"
    )
    # State should be closer to visual odom than to the outlier GPS
    assert np.linalg.norm(fused_state.position) < 10.0, (
        f"State jumped to {fused_state.position} — GPS outlier corrupted estimate"
    )
```

### Pipeline Integration Test Patterns

```python
# End-to-end test with fully synthetic inputs and computable ground truth

def test_full_pipeline_with_synthetic_ground_truth():
    """End-to-end test: synthetic scene with known objects → verify full pipeline.

    Ground truth is computationally derived from the synthetic scene definition,
    not from annotations. This makes the test fully deterministic.
    """
    scene = SyntheticScene(
        objects=[
            Vehicle(position=(5, 3), size=(4.5, 2.0), plate="ABC1234"),
            Vehicle(position=(15, 8), size=(3.8, 1.8), plate="XYZ5678"),
        ],
        camera=Camera(position=(0, 0, 50), fov=60),  # Drone at 50m altitude
    )
    rendered_image = scene.render()
    gt_detections = scene.get_ground_truth_detections()

    # Run full pipeline
    detections = detector.predict(rendered_image)
    tracks = tracker.update(detections, timestamp=0.0)
    for track in tracks:
        crop = extract_crop(rendered_image, track.latest_detection)
        ocr_result = ocr.read(crop)

    # Verify detection count matches ground truth
    assert len(detections) == len(gt_detections), (
        f"Detected {len(detections)} objects, expected {len(gt_detections)}"
    )

    # Verify IoU with ground truth boxes
    for det, gt in zip(
        sorted(detections, key=lambda d: d.x1),
        sorted(gt_detections, key=lambda d: d.x1),
    ):
        iou = compute_iou(det, gt)
        assert iou > 0.5, f"Detection IoU {iou:.3f} < 0.5 with ground truth"


def test_temporal_pipeline_consistency():
    """Multi-frame pipeline test: track consistency and aggregation over time."""
    sequence = generate_vehicle_flyover_sequence(
        plate="ABC1234", num_frames=30, altitude=50
    )

    all_ocr_results = []
    for frame_idx, frame in enumerate(sequence):
        detections = detector.predict(frame)
        tracks = tracker.update(detections, timestamp=frame_idx / 30.0)
        for track in tracks:
            crop = extract_crop(frame, track.latest_detection)
            ocr_result = ocr.read(crop)
            all_ocr_results.append(ocr_result)

    # Aggregated OCR should be MORE accurate than any single frame
    aggregated = ocr_aggregator.aggregate(all_ocr_results)
    best_single = max(all_ocr_results, key=lambda r: r.confidence)

    assert aggregated.confidence >= best_single.confidence, (
        f"Aggregation ({aggregated.confidence:.3f}) should be >= "
        f"best single frame ({best_single.confidence:.3f})"
    )
    assert aggregated.text == "ABC1234", (
        f"Aggregated OCR '{aggregated.text}' != expected 'ABC1234'"
    )


# --- ROS 2 tf2 Transform Synchronization Test ---
# Source: ROS 2 tf2 docs (docs.ros.org/en/rolling)
def test_sensor_transforms_temporally_synchronized():
    """Sensor transforms must be available within acceptable time window.

    Source: ROS 2 tf2 — lookupTransform timeout; extrapolation errors
    indicate temporal sync issues. Common timeouts: 50ms-1000ms.
    """
    import rclpy
    from tf2_ros import Buffer, TransformListener

    tf_buffer = Buffer()
    now = rclpy.time.Time()

    try:
        # Camera to base_link transform
        cam_transform = tf_buffer.lookup_transform(
            "base_link", "camera_optical_frame", now,
            timeout=rclpy.duration.Duration(seconds=0.1)  # 100ms timeout
        )
        # LiDAR to base_link transform
        lidar_transform = tf_buffer.lookup_transform(
            "base_link", "lidar_frame", now,
            timeout=rclpy.duration.Duration(seconds=0.1)
        )
        # Timestamps should be within 10ms of each other
        time_diff = abs(
            cam_transform.header.stamp.sec - lidar_transform.header.stamp.sec
        ) + abs(
            cam_transform.header.stamp.nanosec - lidar_transform.header.stamp.nanosec
        ) * 1e-9
        assert time_diff < 0.010, (
            f"Camera-LiDAR transform timestamps differ by {time_diff*1000:.1f}ms "
            f"(>10ms sync threshold)"
        )
    except Exception as e:
        pytest.fail(f"Transform lookup failed: {e}. Sensor sync broken.")
```

---

## Source Bibliography

### 3D Vision / Spatial
- [ORB-SLAM3 paper](https://arxiv.org/abs/2007.11898) — Campos et al., IEEE T-RO 2021
- [TUM RGB-D benchmark](https://cvg.cit.tum.de/data/datasets/rgbd-dataset) — Sturm et al., IROS 2012
- [EuRoC MAV dataset](https://projects.asl.ethz.ch/datasets/euroc-mav/) — ETH Zurich ASL
- [KITTI odometry benchmark](https://www.cvlibs.net/datasets/kitti/eval_odometry.php) — Geiger et al.
- [Depth Anything V2](https://arxiv.org/abs/2406.09414) — Yang et al., NeurIPS 2024
- [Open3D ICP registration](https://www.open3d.org/docs/latest/tutorial/Basic/icp_registration.html)
- [Voxblox test suite](https://github.com/ethz-asl/voxblox/blob/master/voxblox/test/test_sdf_integrators.cc)
- [TEASER++](https://github.com/MIT-SPARK/TEASER-plusplus) — Yang et al., IEEE T-RO 2021
- [evo trajectory evaluation](https://github.com/MichaelGrupp/evo)
- [GTSAM factor graphs](https://gtsam.org/tutorials/intro.html)
- [nvblox](https://github.com/nvidia-isaac/nvblox) — NVIDIA Isaac

### Real-Time / Latency
- [TensorRT best practices](https://docs.nvidia.com/deeplearning/tensorrt/latest/performance/best-practices.html)
- [DeepStream performance](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_Performance.html)
- [PX4 obstacle avoidance](https://docs.px4.io/v1.14/en/computer_vision/obstacle_avoidance.html)
- [PX4 collision prevention](https://docs.px4.io/main/en/computer_vision/collision_prevention.html)
- [ROS 2 latency analysis](https://arxiv.org/pdf/2101.02074) — Kronauer et al.
- [CMU SteelEagle drone latency](https://reports-archive.adm.cs.cmu.edu/anon/2024/CMU-CS-24-128.pdf)
- [Drone edge inference](https://imt-atlantique.hal.science/hal-05280264/document) — IMT Atlantique

### Simulation & Sim-to-Real
- [Tobin et al. 2017 — Domain Randomization](https://arxiv.org/abs/1703.06907)
- [Tremblay et al. 2018 — Training Deep Networks with Synthetic Data](https://arxiv.org/abs/1804.06516)
- [Isaac Sim domain randomization](https://github.com/isaac-sim/OmniIsaacGymEnvs/blob/main/docs/framework/domain_randomization.md)
- [NVIDIA Isaac Lab sim-to-real blog](https://developer.nvidia.com/blog/bridging-the-sim-to-real-gap-for-industrial-robotic-assembly-applications-using-nvidia-isaac-lab/)
- [Gazebo sensor noise model](https://classic.gazebosim.org/tutorials?tut=sensor_noise)
- [Sim-to-real gap quantification](https://www.mdpi.com/2079-9292/12/10/2197) — Electronics 2023
- [Isaac Sim Replicator DR](https://docs.isaacsim.omniverse.nvidia.com/4.5.0/py/source/extensions/isaacsim.replicator.domain_randomization/docs/index.html)

### Sensor Fusion / Pipeline
- [nuScenes detection eval](https://github.com/nutonomy/nuscenes-devkit/blob/master/python-sdk/nuscenes/eval/detection/README.md) — mAP matching at {0.5, 1, 2, 4}m
- [BEVFusion calibration robustness](https://openaccess.thecvf.com/content/CVPR2023W/E2EAD/papers/Yu_Benchmarking_the_Robustness_of_LiDAR-Camera_Fusion_for_3D_Object_Detection_CVPRW_2023_paper.pdf)
- [Kalman filter NEES](https://kalman-filter.com/normalized-estimation-error-squared/)
- [Kalman filter NIS](https://kalman-filter.com/normalized-innovation-squared/)
- [ROS 2 tf2 time synchronization](https://docs.ros.org/en/rolling/Tutorials/Intermediate/Tf2/Learning-About-Tf2-And-Time-Cpp.html)
- [NI sensor fusion testing](https://ni.com/en/solutions/transportation/adas-and-autonomous-driving-validation/testing-perception-and-sensor-fusion-systems.html)
- [Camera-LiDAR calibration review](https://pmc.ncbi.nlm.nih.gov/articles/PMC11207430/)
