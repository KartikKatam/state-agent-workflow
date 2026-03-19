# Test Design: Robotics-CV Specialization

Domain-specific test patterns for robotics and computer vision projects. Extends the core test-design skill with patterns for probabilistic algorithms, sensor data, hardware abstraction, and real-time constraints.

---

## Probabilistic Algorithm Testing

CV algorithms produce non-deterministic outputs. Standard exact-match assertions don't work. Use these patterns instead.

### Tolerance Ranges (Not Exact Values)

```python
# WRONG — exact match on probabilistic output
assert model.predict(frame).confidence == 0.85

# RIGHT — tolerance range
assert abs(model.predict(frame).confidence - 0.85) < 0.05

# RIGHT — pytest.approx for cleaner syntax
assert model.predict(frame).confidence == pytest.approx(0.85, abs=0.05)
```

### Statistical Properties Over Batches

For model outputs, test statistical properties over many samples rather than individual predictions:

```python
def test_detection_recall_on_test_set(model, labeled_test_set):
    """Recall over entire test set, not individual frames."""
    detections = [model.detect(frame) for frame in labeled_test_set.frames]
    recall = compute_recall(detections, labeled_test_set.labels)
    assert recall >= 0.90, f"Recall {recall:.3f} below threshold"

def test_confidence_distribution(model, test_frames):
    """Confidence distribution should be bimodal (high for real, low for noise)."""
    confidences = [d.confidence for f in test_frames for d in model.detect(f)]
    assert np.mean(confidences) > 0.5
    assert np.std(confidences) > 0.1  # Not all same value
```

### Fixed Random Seeds

```python
@pytest.fixture
def seeded_model(model):
    """Reproducible model outputs for testing."""
    torch.manual_seed(42)
    np.random.seed(42)
    yield model
```

### Degradation Testing

Test what happens when model confidence drops below usable thresholds:

```python
def test_low_confidence_filtered(pipeline, blurry_frame):
    """Low-confidence detections should be filtered, not propagated."""
    result = pipeline.process(blurry_frame)
    assert all(d.confidence >= pipeline.min_confidence for d in result.detections)
    assert "filtered low-confidence" in result.log_messages
```

---

## Computer Vision Metrics (IoU / AP)

### Test IoU Computation Independently

```python
class TestIoUComputation:
    def test_perfect_overlap(self):
        box = BBox(0, 0, 100, 100)
        assert compute_iou(box, box) == 1.0

    def test_no_overlap(self):
        a = BBox(0, 0, 50, 50)
        b = BBox(100, 100, 150, 150)
        assert compute_iou(a, b) == 0.0

    def test_partial_overlap(self):
        a = BBox(0, 0, 100, 100)
        b = BBox(50, 50, 150, 150)
        # Intersection: 50x50=2500, Union: 2*10000-2500=17500
        assert compute_iou(a, b) == pytest.approx(2500 / 17500, abs=1e-6)

    def test_contained_box(self):
        outer = BBox(0, 0, 100, 100)
        inner = BBox(25, 25, 75, 75)
        expected = (50 * 50) / (100 * 100)  # inner_area / outer_area
        assert compute_iou(outer, inner) == pytest.approx(expected, abs=1e-6)
```

### Test AP with Synthetic Predictions

```python
def test_ap_perfect_predictions():
    """Perfect predictions → AP = 1.0."""
    predictions = [Detection(box=gt.box, conf=0.99) for gt in ground_truths]
    ap = compute_ap(predictions, ground_truths, iou_threshold=0.5)
    assert ap == pytest.approx(1.0, abs=0.01)

def test_ap_no_predictions():
    """No predictions → AP = 0.0."""
    ap = compute_ap([], ground_truths, iou_threshold=0.5)
    assert ap == 0.0

def test_ap_decreases_with_false_positives():
    """Adding false positives should decrease AP."""
    good = [Detection(box=gt.box, conf=0.9) for gt in ground_truths]
    ap_clean = compute_ap(good, ground_truths)
    noisy = good + [Detection(box=random_box(), conf=0.8) for _ in range(10)]
    ap_noisy = compute_ap(noisy, ground_truths)
    assert ap_noisy < ap_clean
```

### Multi-Resolution Testing

For drone/aerial applications, test at different simulated altitudes/resolutions:

```python
@pytest.mark.parametrize("scale_factor,min_expected_ap", [
    (1.0, 0.90),    # Ground level — full resolution
    (0.5, 0.85),    # Medium altitude
    (0.25, 0.70),   # High altitude — small objects
    (0.1, 0.40),    # Very high — graceful degradation
])
def test_detection_at_altitude(model, test_frame, scale_factor, min_expected_ap):
    resized = cv2.resize(test_frame, None, fx=scale_factor, fy=scale_factor)
    detections = model.detect(resized)
    ap = compute_ap(detections, scale_ground_truth(ground_truth, scale_factor))
    assert ap >= min_expected_ap
```

---

## Tracking Algorithm Testing

### Identity Switch Detection

The critical failure mode for multi-object trackers (SAM2/3, DeepSORT, etc.):

```python
def test_no_identity_switches(tracker, synthetic_sequence):
    """Tracks should maintain identity across frames."""
    tracks = tracker.process_sequence(synthetic_sequence)
    for track in tracks:
        gt_ids = [frame.get_gt_id(track.bbox) for frame in synthetic_sequence]
        unique_gt_ids = set(id for id in gt_ids if id is not None)
        assert len(unique_gt_ids) <= 1, (
            f"Track {track.id} switched identity: {unique_gt_ids}"
        )

def test_track_continuity(tracker, occlusion_sequence):
    """Track survives brief occlusion without ID change."""
    tracks = tracker.process_sequence(occlusion_sequence)
    # Object is visible frames 0-10, occluded 11-15, visible 16-25
    pre_occlusion_id = tracks.get_id_at_frame(10)
    post_occlusion_id = tracks.get_id_at_frame(16)
    assert pre_occlusion_id == post_occlusion_id
```

### Synthetic Sequence Generation

Control object motion for deterministic tracking tests:

```python
@pytest.fixture
def linear_motion_sequence():
    """Object moving linearly — trivial tracking case."""
    frames = []
    for i in range(30):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        x = 50 + i * 10  # Linear horizontal motion
        cv2.rectangle(frame, (x, 200), (x + 60, 260), (255, 255, 255), -1)
        frames.append(frame)
    return frames

@pytest.fixture
def crossing_objects_sequence():
    """Two objects crossing paths — hard tracking case."""
    ...
```

---

## OCR / Text Recognition Testing

### Probabilistic Fusion

```python
def test_aggregation_improves_over_single(aggregator, multi_frame_readings):
    """Aggregated result should be more accurate than any single frame."""
    single_best = max(multi_frame_readings, key=lambda r: r.confidence)
    aggregated = aggregator.fuse(multi_frame_readings)
    assert aggregated.confidence >= single_best.confidence

def test_quality_gating(aggregator, low_quality_readings):
    """Readings below quality threshold should not contribute."""
    result = aggregator.fuse(low_quality_readings)
    assert result.confidence < aggregator.acceptance_threshold
    assert "insufficient quality" in result.rejection_reason
```

### Edge Case Inputs

```python
@pytest.mark.parametrize("plate_text,expected_clean", [
    ("ABC 1234", "ABC1234"),       # Standard with space
    ("AB-C123-4", "ABC1234"),       # Hyphens
    ("", None),                      # Empty
    ("A", None),                     # Too short
    ("ABC1234567890", None),         # Too long
    ("ABC12O4", "ABC1204"),          # O/0 confusion
    ("ABC12l4", "ABC1214"),          # l/1 confusion
])
def test_plate_normalization(normalizer, plate_text, expected_clean):
    result = normalizer.clean(plate_text)
    assert result == expected_clean
```

---

## Hardware Mocking and Sensor Simulation

### Camera Interface Mocking

```python
@pytest.fixture
def mock_camera():
    """Mock camera that yields stored frames."""
    class MockCamera:
        def __init__(self, frames):
            self._frames = iter(frames)
            self.frame_count = 0

        def grab(self):
            try:
                frame = next(self._frames)
                self.frame_count += 1
                return True, frame
            except StopIteration:
                return False, None

        @property
        def resolution(self):
            return (1920, 1080)

    return MockCamera

def test_pipeline_processes_camera_feed(mock_camera, test_frames, pipeline):
    camera = mock_camera(test_frames)
    pipeline.set_source(camera)
    pipeline.run()
    assert camera.frame_count == len(test_frames)
    assert pipeline.detections_count > 0
```

### GPS / IMU Simulation

```python
@pytest.fixture
def simulated_gps_track():
    """GPS coordinates following a realistic survey flight pattern.

    Models a lawnmower survey pattern at 60m AGL with:
    - Straight legs with known ground speed
    - 180-degree turns at leg ends
    - Altitude variation from terrain following
    - GPS jitter (±2m horizontal, ±3m vertical)
    """
    legs = 5
    leg_length_m = 200
    leg_spacing_m = 30
    altitude_m = 60.0
    speed_mps = 10.0
    gps_hz = 10

    waypoints = []
    t = 0.0
    for leg in range(legs):
        # Straight leg
        direction = 1 if leg % 2 == 0 else -1
        n_points = int(leg_length_m / speed_mps * gps_hz)
        for i in range(n_points):
            lat = 37.7749 + (leg * leg_spacing_m / 111_000)
            lon = -122.4194 + (direction * i * speed_mps / gps_hz / 111_000)
            alt = altitude_m + np.random.normal(0, 0.5)  # Terrain variation
            jitter_lat = np.random.normal(0, 2 / 111_000)
            jitter_lon = np.random.normal(0, 2 / 111_000)
            waypoints.append(GPSPoint(
                lat=lat + jitter_lat, lon=lon + jitter_lon,
                alt=alt + np.random.normal(0, 1.0),
                time=t, heading=0 if direction == 1 else 180,
            ))
            t += 1.0 / gps_hz
        # Turn segment
        t += 3.0  # 3-second turn
    return GPSTrack(waypoints)

@pytest.fixture
def imu_stream(simulated_gps_track):
    """IMU data synchronized with GPS, including vibration noise.

    Models typical drone IMU at 200Hz with:
    - Accelerometer with vibration noise (prop harmonics)
    - Gyroscope with bias drift
    - Magnetometer with hard/soft iron offsets
    """
    imu_hz = 200
    duration = simulated_gps_track.duration
    n_samples = int(duration * imu_hz)

    accel = np.zeros((n_samples, 3))
    accel[:, 2] = 9.81  # Gravity
    # Add propeller vibration harmonics (fundamental + 2nd harmonic)
    prop_freq = 120  # Hz
    t = np.linspace(0, duration, n_samples)
    accel[:, 2] += 0.3 * np.sin(2 * np.pi * prop_freq * t)
    accel[:, 2] += 0.1 * np.sin(2 * np.pi * 2 * prop_freq * t)

    gyro_bias = np.array([0.01, -0.005, 0.002])  # rad/s drift
    gyro = np.random.normal(0, 0.02, (n_samples, 3)) + gyro_bias

    return IMUStream(accel=accel, gyro=gyro, hz=imu_hz, timestamps=t)
```

### Sensor Fusion Testing

```python
def test_gps_imu_fusion_reduces_position_error(
    simulated_gps_track, imu_stream, ground_truth_path
):
    """Fused position should be more accurate than GPS alone."""
    gps_only_error = compute_rmse(simulated_gps_track.positions, ground_truth_path)
    fused = fuse_gps_imu(simulated_gps_track, imu_stream)
    fused_error = compute_rmse(fused.positions, ground_truth_path)
    assert fused_error < gps_only_error * 0.7, (
        f"Fusion didn't improve enough: GPS RMSE={gps_only_error:.2f}m, "
        f"Fused RMSE={fused_error:.2f}m"
    )

def test_gps_dropout_handled(simulated_gps_track, imu_stream):
    """System maintains position estimate during GPS dropout."""
    # Simulate 5-second GPS dropout
    track_with_dropout = simulated_gps_track.with_dropout(
        start_time=10.0, duration=5.0
    )
    result = fuse_gps_imu(track_with_dropout, imu_stream)
    # Position should still be estimated (from IMU dead-reckoning)
    assert result.has_position_at(12.5)
    # Error should be bounded even during dropout
    assert result.max_error_during(10.0, 15.0) < 10.0  # meters
```

---

## Timing Constraint Testing

### Inference Latency

```python
@pytest.mark.benchmark
def test_inference_within_frame_budget(model, test_frame):
    """Inference must complete within one frame period (33ms at 30fps)."""
    import time
    start = time.perf_counter()
    model.predict(test_frame)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 33, f"Inference took {elapsed_ms:.1f}ms, budget is 33ms"
```

### Pipeline Throughput

```python
def test_pipeline_maintains_framerate(pipeline, test_video):
    """Pipeline must sustain target FPS over sustained input."""
    import time
    start = time.perf_counter()
    frame_count = 0
    for frame in test_video:
        pipeline.process(frame)
        frame_count += 1
    elapsed = time.perf_counter() - start
    fps = frame_count / elapsed
    assert fps >= pipeline.target_fps * 0.9, (
        f"Pipeline FPS {fps:.1f} below 90% of target {pipeline.target_fps}"
    )
```

---

## ONNX / TensorRT Precision Testing

When deploying models to edge devices (Jetson, RTX edge servers), format conversion introduces precision drift. Test for it explicitly.

### Export Parity Testing

```python
class TestModelExportParity:
    """Verify exported model matches PyTorch reference within tolerance."""

    @pytest.fixture(scope="module")
    def reference_outputs(self, pytorch_model, test_batch):
        """PyTorch outputs are ground truth."""
        with torch.no_grad():
            return pytorch_model(test_batch)

    def test_onnx_matches_pytorch(self, onnx_session, test_batch, reference_outputs):
        """ONNX output should match PyTorch within FP32 tolerance."""
        onnx_out = onnx_session.run(None, {"input": test_batch.numpy()})
        np.testing.assert_allclose(
            onnx_out[0], reference_outputs.numpy(),
            rtol=1e-5, atol=1e-6,
            err_msg="ONNX export introduced unacceptable precision drift"
        )

    def test_tensorrt_fp16_matches_pytorch(
        self, trt_engine_fp16, test_batch, reference_outputs
    ):
        """TensorRT FP16 output should match PyTorch within FP16 tolerance."""
        trt_out = trt_engine_fp16.infer(test_batch)
        np.testing.assert_allclose(
            trt_out, reference_outputs.numpy(),
            rtol=1e-2, atol=1e-3,  # Wider tolerance for FP16
            err_msg="TensorRT FP16 precision drift exceeds threshold"
        )

    def test_tensorrt_int8_detection_quality(
        self, trt_engine_int8, test_frames, ground_truths
    ):
        """INT8 quantization should not drop AP below acceptable threshold."""
        detections = [trt_engine_int8.detect(f) for f in test_frames]
        ap = compute_ap(detections, ground_truths, iou_threshold=0.5)
        assert ap >= 0.85, (
            f"INT8 quantization dropped AP to {ap:.3f} (minimum 0.85)"
        )
```

### Calibration Data Testing

```python
def test_int8_calibration_covers_distribution(calibration_set, full_dataset):
    """Calibration data should represent the full input distribution."""
    cal_stats = compute_pixel_statistics(calibration_set)
    full_stats = compute_pixel_statistics(full_dataset)
    # Mean should be within 10%
    assert abs(cal_stats.mean - full_stats.mean) / full_stats.mean < 0.10
    # Std should be within 20%
    assert abs(cal_stats.std - full_stats.std) / full_stats.std < 0.20
```

### Edge-Specific Latency Testing

```python
@pytest.mark.benchmark
@pytest.mark.parametrize("precision,max_latency_ms", [
    ("fp32", 50),
    ("fp16", 25),
    ("int8", 15),
])
def test_inference_latency_by_precision(
    trt_engine_factory, test_frame, precision, max_latency_ms
):
    """Each precision level should meet its latency target."""
    engine = trt_engine_factory(precision=precision)
    # Warmup
    for _ in range(10):
        engine.infer(test_frame)
    # Measure
    import time
    times = []
    for _ in range(100):
        start = time.perf_counter()
        engine.infer(test_frame)
        times.append((time.perf_counter() - start) * 1000)
    p95 = sorted(times)[94]
    assert p95 < max_latency_ms, (
        f"{precision} P95 latency {p95:.1f}ms exceeds {max_latency_ms}ms budget"
    )
```

### Model Output Shape Stability

```python
def test_output_shape_preserved_across_exports(
    pytorch_model, onnx_session, trt_engine, test_batch
):
    """All export formats should produce identical output shapes."""
    pt_shape = pytorch_model(test_batch).shape
    onnx_shape = onnx_session.run(None, {"input": test_batch.numpy()})[0].shape
    trt_shape = trt_engine.infer(test_batch).shape
    assert pt_shape == onnx_shape == trt_shape, (
        f"Shape mismatch: PT={pt_shape}, ONNX={onnx_shape}, TRT={trt_shape}"
    )
```

---

## Hypothesis Strategies for CV

Custom strategies for property-based testing with CV data types:

```python
from hypothesis import strategies as st

# Bounding box strategy — valid boxes within image bounds
bbox_strategy = st.builds(
    BBox,
    x1=st.integers(0, 1920),
    y1=st.integers(0, 1080),
    width=st.integers(10, 500),
    height=st.integers(10, 500),
)

# Frame strategy — random but valid images
frame_strategy = st.builds(
    lambda h, w: np.random.randint(0, 256, (h, w, 3), dtype=np.uint8),
    h=st.integers(240, 1080),
    w=st.integers(320, 1920),
)

@given(boxes=st.lists(bbox_strategy, min_size=2, max_size=10))
def test_nms_reduces_count(boxes):
    """NMS should never output more boxes than input."""
    result = non_max_suppression(boxes, threshold=0.5)
    assert len(result) <= len(boxes)
```
