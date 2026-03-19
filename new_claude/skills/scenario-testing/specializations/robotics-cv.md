# Scenario Testing: Robotics-CV Specialization

Additional patterns for testing computer vision, robotics, and drone systems.
Extends the core scenario-testing skill with domain-specific tier guidance.

## Domain Routing

Load this file for general CV testing patterns. For specialized domains, load the relevant deep-dive:

| Testing... | File |
|------------|------|
| 2D detection, OCR, classification | This file (sections below) |
| 3D vision, SLAM, point clouds, depth estimation | `robotics-cv-3d-vision.md` |
| Latency budgets, GPU performance, real-time constraints | `robotics-cv-latency.md` |
| Sim-to-real transfer, domain randomization, simulation fidelity | `robotics-cv-sim2real.md` |
| Sensor fusion, multi-stage pipelines, temporal alignment | `robotics-cv-fusion.md` |

## CV-Specific Tier Adaptations

### Tier 1: Golden Path

Test the primary inference pipeline under ideal conditions:

```python
def test_detection_returns_boxes_for_known_image():
    """Golden: detector finds objects in a known reference image."""
    image = load_test_image("vehicle_clear_daylight.png")
    detections = detector.predict(image)
    assert len(detections) >= 1
    assert all(d.confidence >= 0.5 for d in detections)
    assert all(d.bbox.area > 0 for d in detections)

def test_ocr_reads_clear_plate():
    """Golden: OCR correctly reads a clear, well-lit license plate."""
    crop = load_test_image("plate_clear_ABC1234.png")
    result = ocr.read(crop)
    assert result.text == "ABC1234"
    assert result.confidence >= 0.8
```

### Tier 2: Edge Cases — Sensor/Environment Extremes

CV edge cases come from the physical world, not just data types:

```python
@pytest.mark.parametrize("condition,image_file", [
    ("overexposed", "plate_overexposed.png"),
    ("underexposed", "plate_night_dark.png"),
    ("motion_blur", "plate_moving_fast.png"),
    ("partial_occlusion", "plate_behind_car.png"),
    ("extreme_angle", "plate_45_degrees.png"),
    ("rain_droplets", "plate_wet_lens.png"),
    ("single_pixel_plate", "plate_1px_height.png"),
    ("empty_frame", "frame_no_vehicles.png"),
])
def test_detector_handles_environmental_edge_cases(condition, image_file):
    image = load_test_image(image_file)
    detections = detector.predict(image)
    assert all(d.confidence >= 0.0 for d in detections)
    assert all(d.bbox.is_valid() for d in detections)
```

**Altitude-specific edges (drone systems):**
- Minimum GSD (ground sampling distance) at maximum altitude
- Maximum GSD at minimum altitude
- Altitude transition boundaries (switching between detection models)

### Tier 3: Adversarial — CV Attack Patterns

```python
def test_adversarial_patch_does_not_crash():
    """Crafted input patch should not crash the model."""
    adversarial = generate_adversarial_patch(target_class="vehicle")
    image = overlay_patch(clean_image, adversarial)
    detections = detector.predict(image)
    assert isinstance(detections, list)

def test_oversized_image_rejected():
    """100MP image should not cause OOM."""
    huge = np.zeros((10000, 10000, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="exceeds maximum"):
        detector.predict(huge)
```

### Tier 4: Property-Based — CV Invariants

```python
@given(
    width=st.integers(min_value=1, max_value=4096),
    height=st.integers(min_value=1, max_value=4096),
)
def test_detection_boxes_within_image_bounds(width, height):
    """Invariant: all detection boxes must be within image dimensions."""
    image = np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)
    detections = detector.predict(image)
    for d in detections:
        assert 0 <= d.bbox.x1 <= width
        assert 0 <= d.bbox.y1 <= height

@given(seed=st.integers(min_value=0, max_value=10000))
def test_detection_idempotent(seed):
    """Invariant: same image produces same detections (deterministic mode)."""
    np.random.seed(seed)
    image = np.random.randint(0, 255, (640, 480, 3), dtype=np.uint8)
    result1 = detector.predict(image, deterministic=True)
    result2 = detector.predict(image, deterministic=True)
    assert len(result1) == len(result2)
```

## Probabilistic Testing Patterns

CV outputs are inherently probabilistic. Use tolerance-based assertions:

```python
# WRONG — exact assertion on probabilistic output
assert detector.predict(image)[0].confidence == 0.85

# RIGHT — tolerance range
assert 0.7 <= detector.predict(image)[0].confidence <= 0.95

# RIGHT — statistical assertion over batch (more stable)
confidences = [detector.predict(img)[0].confidence for img in test_batch]
assert sum(confidences) / len(confidences) >= 0.75
```

**Fixed seeds for reproducibility:**
```python
@pytest.fixture
def deterministic_model():
    torch.manual_seed(42)
    np.random.seed(42)
    return load_model(deterministic=True)
```

## Sub-Agent Prompt Adaptation for CV

Add to every CV sub-agent prompt:

```
Additional context for CV testing:
- Outputs are probabilistic. Use tolerance ranges, not exact values.
- Always set random seeds for reproducibility.
- Test with synthetic data where ground truth is known.
- For detection: assert bounding boxes are within image bounds.
- For tracking: assert track IDs are consistent across frames.
- For OCR: test with both clear and degraded inputs.
- Never test model accuracy with a single image — use batch assertions.
```

## Blind Reporting for CV

Describe failures as observable behavior gaps, never model internals:

```
# WRONG — references model internals
"The YOLO backbone's feature map at layer 3 has incorrect spatial dimensions"

# RIGHT — describes observable behavior gap
"Detection returns 0 boxes for images at 200ft altitude GSD.
Spec §3.1 requires >= 1 detection for vehicles > 3m length at any
supported altitude. Test image contains a 4.5m vehicle at 200ft GSD."
```
