# Robotics-CV: 3D Vision / Spatial Testing

Deep-dive for testing SLAM, point cloud registration, depth estimation, and 3D reconstruction.

## Standard Metrics and Thresholds

| Metric | Benchmark | Good Threshold | Source |
|--------|-----------|----------------|--------|
| ATE RMSE (stereo-inertial) | EuRoC MAV | < 3.6 cm | ORB-SLAM3, IEEE T-RO 2021 |
| ATE RMSE (stereo) | EuRoC MAV | < 5 cm | ORB-SLAM3 |
| Translational RPE | KITTI odometry | < 4.15% | ORB-SLAM3 KITTI eval |
| Rotational RPE | KITTI odometry | < 0.0027 deg/m | ORB-SLAM3 KITTI eval |
| Depth abs_rel (ViT-L) | NYU Depth v2 | < 0.056 | Depth Anything V2, NeurIPS 2024 |
| Depth δ₁ (ViT-L) | NYU Depth v2 | > 0.984 | Depth Anything V2 |
| Depth RMSE (ViT-L) | KITTI | < 1.861 m | Depth Anything V2 |
| ICP registration fitness | Open3D | > 0.6 (60% inlier) | Open3D docs, threshold=0.02 |
| ICP inlier RMSE | Open3D | < 0.007 m | Open3D tutorial examples |
| TSDF voxel RMSE | Voxblox | < 2× voxel_size | voxblox test_sdf_integrators.cc |
| TEASER++ rotation error | Point cloud reg. | < 0.15° | TEASER++, MIT SPARK |
| TEASER++ translation error | Point cloud reg. | < 0.17 m | TEASER++ |

## Key Test Patterns

### SLAM Trajectory Evaluation

```python
# Uses evo package (github.com/MichaelGrupp/evo)
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
    assert ate_rmse < 0.05, f"ATE RMSE {ate_rmse:.4f}m exceeds 5cm threshold"
```

### Point Cloud Registration

```python
import open3d as o3d

def test_icp_registration_quality():
    """ICP must achieve high fitness and low RMSE on aligned clouds."""
    source = o3d.io.read_point_cloud("source.pcd")
    target = o3d.io.read_point_cloud("target.pcd")
    reg = o3d.pipelines.registration.registration_icp(
        source, target, 0.02, np.eye(4),
        o3d.pipelines.registration.TransformationEstimationPointToPlane()
    )
    assert reg.fitness > 0.3, f"Fitness {reg.fitness:.3f} too low"
    assert reg.inlier_rmse < 0.01, f"Inlier RMSE {reg.inlier_rmse:.4f}m too high"
```

### Depth Estimation

```python
def test_depth_estimation_metrics():
    """Depth quality against ground truth with standard metrics."""
    pred_depth = model.predict(test_image)
    gt_depth = load_ground_truth_depth("test_depth.npy")
    valid = gt_depth > 0
    ratio = np.maximum(pred_depth[valid] / gt_depth[valid],
                       gt_depth[valid] / pred_depth[valid])
    delta1 = np.mean(ratio < 1.25)
    abs_rel = np.mean(np.abs(pred_depth[valid] - gt_depth[valid]) / gt_depth[valid])
    # Depth Anything V2 ViT-L baselines on NYU Depth v2
    assert delta1 > 0.95, f"δ₁={delta1:.3f}, expected > 0.95"
    assert abs_rel < 0.08, f"abs_rel={abs_rel:.3f}, expected < 0.08"
```

## Property-Based Invariants

```python
from hypothesis import given, strategies as st

@given(
    tx=st.floats(-10, 10), ty=st.floats(-10, 10), tz=st.floats(-10, 10),
    rx=st.floats(-np.pi, np.pi), ry=st.floats(-np.pi/2+0.01, np.pi/2-0.01),
    rz=st.floats(-np.pi, np.pi),
)
def test_pose_composition_invertibility(tx, ty, tz, rx, ry, rz):
    """Composing a pose with its inverse must yield identity."""
    T = make_transform(tx, ty, tz, rx, ry, rz)
    result = T @ np.linalg.inv(T)
    assert np.allclose(result, np.eye(4), atol=1e-6)

@given(scale=st.floats(0.01, 100.0), angle=st.floats(-np.pi, np.pi))
def test_rigid_transform_preserves_distances(scale, angle):
    """Rigid transforms preserve pairwise distances."""
    pts = np.random.randn(50, 3) * scale
    R = rotation_matrix_z(angle)
    pts_transformed = (R @ pts.T).T + np.array([1.0, 2.0, 3.0])
    assert np.allclose(pairwise_distances(pts), pairwise_distances(pts_transformed), atol=1e-6)

def test_depth_map_non_negativity():
    """Predicted depth must be non-negative everywhere."""
    for img in test_images:
        depth = model.predict(img)
        assert np.all(depth >= 0), f"Negative depth: min={depth.min()}"
```

## Failure Modes

| Failure Mode | Detection Strategy | Source |
|---|---|---|
| **Scale drift** (monocular SLAM) | Compare ATE at sequence start vs end; drift grows with distance | TUM RGB-D benchmark |
| **Loop closure failure** | Check ATE before/after loop closure; large drop = closure critical | ORB-SLAM3 |
| **Degenerate geometry** (corridors) | Monitor ICP covariance eigenvalues; ratio > 100:1 = rank-deficient | LiDAR SLAM literature |
| **Coordinate frame mismatch** | Test data in both NED and ENU frames; assert consistent convention | ROS REP-103, REP-105 |
| **Depth scale ambiguity** | Relative depth models produce scale-invariant output; test with known metric reference | Depth Anything V2 |
| **Voxel aliasing** | Test with voxel sizes near object feature sizes; small features vanish at coarse resolution | voxblox parametric tests |
| **Point cloud density variation** | Registration fails with sparse clouds; test at 10%, 25%, 50% density | Open3D downsampling |

## Edge Cases

- **Planar degeneracy**: All points coplanar (wall, floor) — ICP has 3 unconstrained DOF. Guard with `matrix_rank(pts) < 3`.
- **Gimbal lock near ±90° pitch**: Euler angles break. Test at pitch = ±89.9°.
- **Zero-baseline stereo**: Left/right cameras at identical position — infinite depth. Assert baseline > minimum.
- **Backward-facing depth**: Depth for pixels behind camera plane. Assert all depths > near_plane.
- **Single-point clouds**: Registration needs minimum point counts. Assert `len(cloud) >= minimum`.
- **Identical source/target**: Registration should return identity transform, not diverge.
- **Large-scale coordinates**: Float precision degrades far from origin. Test at (1e6, 1e6, 1e6).
- **Empty TSDF regions**: Querying unobserved voxels should return truncation_distance, not 0.
