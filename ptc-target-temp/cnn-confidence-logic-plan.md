# CNN Corner Confidence Gate — Homography-Aware Filtering

## Problem

The corner CNN predicts 4 keypoints for license plate perspective correction (homography). Not all predictions are reliable enough for downstream OCR. We need a gate that decides whether to trust the corner predictions for a given frame, accounting for both **model confidence** and **geometric sensitivity**.

## Key Insight

A fixed confidence threshold is wrong. A 2px corner error on a large, straight-on plate barely affects the rectified image. The same 2px error on a small, oblique plate can shift characters by 50+ pixels. The gate must be **geometry-aware**.

## Approach: Condition Number Scaling

### The Variance Head

The corner CNN outputs 16 values per image:
- 8 coordinates: `[x0, y0, x1, y1, x2, y2, x3, y3]`
- 8 sigmas: per-coordinate predicted uncertainty in pixel units

Per-corner sigma (Euclidean):
```python
corner_sigma_i = sqrt(sigma_xi² + sigma_yi²)
```

### The Condition Number

The homography matrix H (computed from predicted corners to a canonical rectangle) has a condition number:
```python
H = cv2.getPerspectiveTransform(pred_corners, canonical_rect)
sensitivity = np.linalg.cond(H)
```

- `cond(H) ≈ 1-5`: Mild transform (plate nearly rectangular, large, straight-on). Corner errors barely amplified.
- `cond(H) ≈ 20-50`: Aggressive transform (small plate, steep angle). Corner errors amplified significantly.
- `cond(H) ≈ 100+`: Near-degenerate (plate almost edge-on, extremely small). Unstable.

### The Gate

```python
# Expected pixel displacement in the rectified plate
rectified_error_bound = max(corner_sigmas) * np.linalg.cond(H)

# Threshold in rectified-pixel units
# ~15px ≈ half a character width on a standard rectified plate
MAX_RECTIFIED_ERROR = 15.0

if rectified_error_bound < MAX_RECTIFIED_ERROR:
    eligible_for_homography = True
```

### Why This Works

| Scenario | sigma | cond(H) | error_bound | Decision |
|----------|-------|---------|-------------|----------|
| Large plate, straight-on, uncertain corners | 3.0px | 2 | 6 | PASS — mild H, errors don't matter |
| Small plate, oblique, confident corners | 1.0px | 40 | 40 | FAIL — aggressive H amplifies even small errors |
| Large plate, moderate angle, confident | 1.5px | 8 | 12 | PASS — good confidence, moderate H |
| Small plate, steep angle, uncertain | 3.5px | 25 | 87.5 | FAIL — bad confidence + aggressive H |

Less homography needed (low cond) → less confidence needed (higher sigma tolerated).
More homography needed (high cond) → more confidence needed (lower sigma required).

## Pipeline Integration

### In the Producer (batch assembly)

```python
for frame in tracked_plate_frames:
    corners, sigmas = corner_cnn.predict_with_variance(frame)
    H = cv2.getPerspectiveTransform(corners, canonical_rect)

    error_bound = max(corner_sigmas) * np.linalg.cond(H)
    frame.homography_eligible = error_bound < MAX_RECTIFIED_ERROR
    frame.error_bound = error_bound  # used for batch ranking
```

### In Batch Selection (choosing 8 of up to 32)

Two-tier ranking for OCR batch priority:
1. Homography-eligible frames ranked by `error_bound` (lower = better)
2. Non-eligible frames ranked last (fallback if not enough eligible frames)

### Best-Frame Selection (per plate track)

```python
# Across all frames where this plate is tracked:
best_frame = min(eligible_frames, key=lambda f: f.error_bound)
```

Zero waste — every frame contributes to tracking, but OCR runs on the single best frame per plate.

## Calibrating MAX_RECTIFIED_ERROR

The threshold has a physical meaning: **maximum tolerable pixel displacement in the rectified plate before OCR degrades.**

To calibrate:
1. Take validation images with ground-truth corners
2. Compute rectified plates using GT corners vs predicted corners
3. Measure pixel displacement at various error_bound values
4. Run ParseQ OCR on both → find where character error rate spikes
5. Set threshold just below the spike point

Alternatively, start with `MAX_RECTIFIED_ERROR = 15.0` (half a character width) and tune based on end-to-end OCR accuracy on real data.

## Experimental Validation (from variance prediction experiment)

The variance head was validated in experiment #22:
- PCK@4 = 0.892 (no accuracy degradation vs baseline 0.890)
- Sigma correlates with actual error: +0.31 correlation
- Rejection at sigma < 2px: 63.4% kept at PCK@4 = 0.929
- Mean sigma for correct corners: 1.86px vs incorrect: 2.22px

With condition number scaling, the gate becomes adaptive to plate geometry rather than using a fixed sigma threshold.

## Dependencies

- Variance prediction model: `training/regression/variance/model.py` (CornerRegressionNetWithVariance)
- Gaussian NLL loss: `training/regression/variance/losses.py`
- Corner canonicalization: `common/geometry.py` (order_keypoints_by_angle)
- OpenCV: `cv2.getPerspectiveTransform`, `numpy.linalg.cond`
