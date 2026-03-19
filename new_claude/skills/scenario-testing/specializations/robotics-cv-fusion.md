# Robotics-CV: Sensor Fusion / Multi-Stage Pipeline Testing

Deep-dive for testing multi-stage perception pipelines, cross-modal alignment, temporal consistency, and error propagation.

## Pipeline Stage Contracts

Define and validate invariants at every stage boundary:

```python
def validate_detection_output(detections, image_shape):
    """Contract: detection output invariants."""
    H, W = image_shape[:2]
    for det in detections:
        assert 0 <= det.x1 < det.x2 <= W, f"Invalid x-range [{det.x1}, {det.x2}]"
        assert 0 <= det.y1 < det.y2 <= H, f"Invalid y-range [{det.y1}, {det.y2}]"
        assert 0.0 <= det.confidence <= 1.0
        assert det.class_id in VALID_CLASS_IDS
        assert (det.x2 - det.x1) * (det.y2 - det.y1) >= 1.0  # Non-degenerate

def validate_track_output(tracks):
    """Contract: tracker output invariants."""
    seen_ids = set()
    for track in tracks:
        assert track.id not in seen_ids, f"Duplicate track ID {track.id}"
        seen_ids.add(track.id)
        assert len(track.detections) >= 1
        timestamps = [d.timestamp for d in track.detections]
        assert timestamps == sorted(timestamps), "Non-monotonic timestamps"

def test_pipeline_contracts_at_every_boundary():
    """Integration test: validate contracts between every pipeline stage."""
    image = load_test_image("test_frame.png")
    detections = detector.predict(image)
    validate_detection_output(detections, image.shape)
    tracks = tracker.update(detections, timestamp=0.0)
    validate_track_output(tracks)
    for track in tracks:
        crop = extract_crop(image, track.latest_detection)
        assert crop.shape[0] >= 8 and crop.shape[1] >= 8, "Crop too small for OCR"
```

## Cross-Modal Alignment

```python
# Source: BEVFusion calibration robustness (CVPR 2023 Workshop)

def test_camera_lidar_projection_consistency():
    """3D LiDAR points projected to camera should land on correct objects."""
    lidar_points = load_lidar_scan("test_scan.pcd")
    extrinsic = load_calibration("lidar_to_camera.json")
    target_points = lidar_points[lidar_points.label == "calibration_target"]
    projected = project_to_image(target_points, extrinsic, camera_intrinsic)
    bbox = get_calibration_target_bbox(camera_image)
    for pt in projected:
        assert bbox.contains(pt), f"Point ({pt.x:.1f}, {pt.y:.1f}) outside target bbox"

def test_calibration_perturbation_degrades_gracefully():
    """Fusion should tolerate small calibration errors, not crash on large ones."""
    baseline_ap = evaluate_fusion_model(model, correct_calibration)
    for perturb_deg in [0.15, 0.25, 0.5, 1.0]:
        perturbed = perturb_extrinsic(correct_calibration, rotation_deg=perturb_deg)
        ap = evaluate_fusion_model(model, perturbed)
        if perturb_deg <= 0.25:
            degradation = (baseline_ap - ap) / baseline_ap
            assert degradation < 0.05, (
                f"AP dropped {degradation:.1%} at only {perturb_deg}° perturbation"
            )
        assert ap > 0, "Model produces 0 AP — crash on misalignment"
```

## Temporal Consistency — Kalman Filter Validation

### NEES (Normalized Estimation Error Squared)

```python
# Source: kalman-filter.com/normalized-estimation-error-squared/
from scipy.stats import chi2

def test_kalman_filter_nees_consistency():
    """NEES should fall within chi-squared confidence bounds."""
    state_dim = 6  # [x, y, z, vx, vy, vz]
    r1 = chi2.ppf(0.025, state_dim)
    r2 = chi2.ppf(0.975, state_dim)
    nees_values = []
    for gt, est, cov in zip(ground_truth, estimates, covariances):
        error = est - gt
        nees_values.append(error.T @ np.linalg.inv(cov) @ error)
    in_bounds = sum(r1 <= n <= r2 for n in nees_values)
    assert in_bounds / len(nees_values) > 0.90, "Filter is inconsistent"
```

### NIS (Normalized Innovation Squared)

```python
def test_kalman_filter_nis_gate():
    """NIS gate rejection rate should be < 5% for a well-tuned filter."""
    meas_dim = 3
    gate = chi2.ppf(0.99, meas_dim)
    rejected = sum(1 for innov, S in zip(innovations, innovation_covs)
                   if innov.T @ np.linalg.inv(S) @ innov > gate)
    assert rejected / len(innovations) < 0.05, "Filter poorly tuned"
```

### Track Temporal Plausibility

```python
def test_tracking_temporal_consistency():
    """Track positions must be physically plausible between frames."""
    max_velocity = 50.0  # m/s
    dt = 1.0 / 30
    for track in tracks:
        for i in range(1, len(track.positions)):
            velocity = np.linalg.norm(track.positions[i] - track.positions[i-1]) / dt
            assert velocity < max_velocity, (
                f"Track {track.id} velocity {velocity:.1f}m/s — likely identity switch"
            )
```

## Error Propagation Testing

```python
def test_missed_detection_propagation():
    """If detector misses an object, downstream must handle gracefully."""
    tracks = tracker.update([], timestamp=1.0)
    for track in tracks:
        assert track.status in ("active", "lost", "tentative")
    assert len([t for t in tracks if t.age == 0]) == 0  # No phantom tracks

def test_false_positive_does_not_persist():
    """Single-frame false positive should not become a persistent track."""
    real_dets = detector.predict(test_image)
    fake = Detection(x1=0, y1=0, x2=10, y2=10, confidence=0.51, class_id=0)
    for i in range(10):
        dets = real_dets + [fake] if i == 5 else real_dets
        tracker.update(dets, timestamp=i / 30.0)
    fp_tracks = [t for t in tracker.tracks if t.status == "active" and t.num_detections == 1]
    assert len(fp_tracks) == 0, "False positive persisted as active track"

def test_sensor_disagreement_handled():
    """When sensors disagree, EKF should gate the outlier measurement."""
    gps = GPSReading(lat=37.7749, lon=-122.4194, alt=100.0)
    visual_odom = VisualOdometry(x=0.0, y=0.0, z=0.0)  # Hasn't moved
    fused = ekf.update(gps, visual_odom)
    assert ekf.last_gps_accepted is False, "EKF accepted 50m GPS outlier"
    assert np.linalg.norm(fused.position) < 10.0, "GPS outlier corrupted estimate"
```

## Metrics Testing

Test metric computation separately from model inference:

```python
def test_iou_computation_known_boxes():
    """IoU with hand-calculated expected values."""
    box_a = BBox(0, 0, 10, 10)  # Area = 100
    box_b = BBox(5, 5, 15, 15)  # Area = 100, intersection = 25
    expected_iou = 25 / (100 + 100 - 25)
    assert abs(compute_iou(box_a, box_b) - expected_iou) < 1e-6

def test_track_continuity_with_synthetic_sequence():
    """Tracker maintains identity across frames with linear motion."""
    frames = generate_linear_motion_sequence(start=(100, 100), velocity=(10, 0), num_frames=10)
    tracks = tracker.process_sequence(frames)
    assert len(tracks) == 1
    assert tracks[0].num_frames == 10
```

## End-to-End Pipeline Test

```python
def test_full_pipeline_with_synthetic_ground_truth():
    """End-to-end: synthetic scene → detect → track → OCR → verify."""
    scene = SyntheticScene(
        objects=[Vehicle(position=(5,3), size=(4.5,2.0), plate="ABC1234")],
        camera=Camera(position=(0,0,50), fov=60),
    )
    image = scene.render()
    gt = scene.get_ground_truth_detections()
    detections = detector.predict(image)
    assert len(detections) == len(gt)
    for det, gt_det in zip(sorted(detections, key=lambda d: d.x1),
                           sorted(gt, key=lambda d: d.x1)):
        assert compute_iou(det, gt_det) > 0.5

# Source: ROS 2 tf2 — sensor transforms must be temporally synchronized
def test_sensor_transforms_synchronized():
    """Camera and LiDAR transforms must be within 10ms of each other."""
    cam_tf = tf_buffer.lookup_transform("base_link", "camera_optical_frame", now)
    lidar_tf = tf_buffer.lookup_transform("base_link", "lidar_frame", now)
    time_diff = abs(cam_tf.header.stamp.sec - lidar_tf.header.stamp.sec)
    assert time_diff < 0.010, f"Transform timestamps differ by {time_diff*1000:.1f}ms"
```
