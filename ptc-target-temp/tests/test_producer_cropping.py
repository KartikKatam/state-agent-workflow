"""Tests for producer/ops_cropping.py — Cropping Pipeline (Chunk 03).

Covers all 6 public functions:
  - compute_padded_bbox
  - extract_crop_from_bbox
  - adaptive_resize_crop
  - validate_crop_dimensions
  - crop_track_roi
  - crop_tracks

~48 tests including property-based (Hypothesis) tests for containment,
expansion, frame clamping, keypoint roundtrip, and count invariants.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from hypothesis import assume, given, settings, strategies as st

from producer.ops_cropping import (
    adaptive_resize_crop,
    compute_padded_bbox,
    crop_track_roi,
    crop_tracks,
    extract_crop_from_bbox,
    validate_crop_dimensions,
)

from .conftest_producer import make_config, make_track

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_frame(h: int = 1080, w: int = 1920) -> np.ndarray:
    """Create a synthetic BGR frame with reproducible pixel values."""
    rng = np.random.default_rng(42)
    return rng.integers(0, 256, (h, w, 3), dtype=np.uint8)


# ===========================================================================
# TestComputePaddedBbox
# ===========================================================================


class TestComputePaddedBbox:
    """Tests for compute_padded_bbox."""

    def test_padded_bbox_normal(self):
        """Normal bbox in center of frame is expanded on all 4 sides."""
        bbox = [500.0, 300.0, 700.0, 400.0]  # 200x100
        frame_shape = (1080, 1920)
        cfg = make_config()

        result = compute_padded_bbox(bbox, frame_shape, cfg)

        # pad_w = max(int(200*0.15), 12) = max(30, 12) = 30
        # pad_h = max(int(100*0.15), 12) = max(15, 12) = 15
        assert result == [470.0, 285.0, 730.0, 415.0]

    def test_padded_bbox_clamp_left(self):
        """Bbox at left edge: x1 padding clamped to 0."""
        bbox = [0.0, 300.0, 200.0, 400.0]  # 200x100
        frame_shape = (1080, 1920)
        cfg = make_config()

        result = compute_padded_bbox(bbox, frame_shape, cfg)

        # pad_w = 30 → x1_pad = max(0, 0-30) = 0
        assert result[0] == 0.0
        assert result[2] == 230.0  # right side padded normally

    def test_padded_bbox_clamp_right(self):
        """Bbox at right edge: x2 padding clamped to frame_w."""
        bbox = [1720.0, 300.0, 1920.0, 400.0]  # 200x100
        frame_shape = (1080, 1920)
        cfg = make_config()

        result = compute_padded_bbox(bbox, frame_shape, cfg)

        # pad_w = 30 → x2_pad = min(1920, 1920+30) = 1920
        assert result[2] == 1920.0
        assert result[0] == 1690.0  # left side padded normally

    def test_padded_bbox_clamp_top(self):
        """Bbox at top edge: y1 padding clamped to 0."""
        bbox = [500.0, 0.0, 700.0, 100.0]  # 200x100
        frame_shape = (1080, 1920)
        cfg = make_config()

        result = compute_padded_bbox(bbox, frame_shape, cfg)

        # pad_h = 15 → y1_pad = max(0, 0-15) = 0
        assert result[1] == 0.0
        assert result[3] == 115.0  # bottom padded normally

    def test_padded_bbox_clamp_bottom(self):
        """Bbox at bottom edge: y2 padding clamped to frame_h."""
        bbox = [500.0, 980.0, 700.0, 1080.0]  # 200x100
        frame_shape = (1080, 1920)
        cfg = make_config()

        result = compute_padded_bbox(bbox, frame_shape, cfg)

        # pad_h = 15 → y2_pad = min(1080, 1080+15) = 1080
        assert result[3] == 1080.0
        assert result[1] == 965.0  # top padded normally

    def test_padded_bbox_fills_frame(self):
        """Bbox fills entire frame: all padding clamped, output equals input."""
        bbox = [0.0, 0.0, 1920.0, 1080.0]
        frame_shape = (1080, 1920)
        cfg = make_config()

        result = compute_padded_bbox(bbox, frame_shape, cfg)

        assert result == [0.0, 0.0, 1920.0, 1080.0]

    def test_padded_bbox_small_min_floor(self):
        """Small bbox (20x10): min floor (12px) dominates over proportional."""
        bbox = [500.0, 300.0, 520.0, 310.0]  # 20x10
        frame_shape = (1080, 1920)
        cfg = make_config()

        result = compute_padded_bbox(bbox, frame_shape, cfg)

        # pad_w = max(int(20*0.15), 12) = max(3, 12) = 12
        # pad_h = max(int(10*0.15), 12) = max(1, 12) = 12
        assert result == [488.0, 288.0, 532.0, 322.0]

    def test_padded_bbox_large_proportional(self):
        """Large bbox (800x400): proportional (15%) dominates over min floor."""
        bbox = [500.0, 300.0, 1300.0, 700.0]  # 800x400
        frame_shape = (1080, 1920)
        cfg = make_config()

        result = compute_padded_bbox(bbox, frame_shape, cfg)

        # pad_w = max(int(800*0.15), 12) = max(120, 12) = 120
        # pad_h = max(int(400*0.15), 12) = max(60, 12) = 60
        assert result == [380.0, 240.0, 1420.0, 760.0]

    def test_padded_bbox_near_edge(self):
        """Bbox 1px from edge: padding limited on that side."""
        bbox = [1.0, 1.0, 201.0, 101.0]  # 200x100, 1px from origin
        frame_shape = (1080, 1920)
        cfg = make_config()

        result = compute_padded_bbox(bbox, frame_shape, cfg)

        # pad_w = 30 → x1_pad = max(0, 1-30) = 0, x2_pad = 231
        # pad_h = 15 → y1_pad = max(0, 1-15) = 0, y2_pad = 116
        assert result[0] == 0.0
        assert result[1] == 0.0
        assert result[2] == 231.0
        assert result[3] == 116.0

    def test_padded_bbox_int_truncation(self):
        """Padding uses int() truncation, NOT round() — anti-bias #4."""
        # bbox_w=90 → 90*0.15=13.5 → int(13.5)=13 (truncation)
        # round(13.5)=14 (banker's rounding to even) — would be wrong
        bbox = [500.0, 300.0, 590.0, 345.0]  # 90x45
        frame_shape = (1080, 1920)
        cfg = make_config()

        result = compute_padded_bbox(bbox, frame_shape, cfg)

        # pad_w = max(int(13.5), 12) = max(13, 12) = 13
        # pad_h = max(int(6.75), 12) = max(6, 12) = 12  (min floor)
        assert result[0] == 487.0  # 500 - 13, NOT 486 (500 - 14)
        assert result[2] == 603.0  # 590 + 13, NOT 604 (590 + 14)
        assert result[1] == 288.0  # 300 - 12  (min floor)
        assert result[3] == 357.0  # 345 + 12

    @given(
        x1=st.floats(min_value=0.0, max_value=1400.0),
        y1=st.floats(min_value=0.0, max_value=780.0),
        w=st.floats(min_value=1.0, max_value=500.0),
        h=st.floats(min_value=1.0, max_value=300.0),
    )
    @settings(max_examples=50)
    def test_padded_bbox_containment_property(self, x1, y1, w, h):
        """Property: 0 <= x1_pad <= x1 <= x2 <= x2_pad <= frame_w."""
        frame_w, frame_h = 1920, 1080
        # Skip if bbox extends beyond frame
        x2 = x1 + w
        y2 = y1 + h
        if x2 > frame_w or y2 > frame_h:
            return
        bbox = [x1, y1, x2, y2]
        frame_shape = (frame_h, frame_w)
        cfg = make_config()

        result = compute_padded_bbox(bbox, frame_shape, cfg)
        x1p, y1p, x2p, y2p = result

        assert 0.0 <= x1p <= x1
        assert x2 <= x2p <= float(frame_w)
        assert 0.0 <= y1p <= y1
        assert y2 <= y2p <= float(frame_h)

    @given(
        x1=st.floats(min_value=10.0, max_value=1400.0),
        y1=st.floats(min_value=10.0, max_value=700.0),
        w=st.floats(min_value=1.0, max_value=500.0),
        h=st.floats(min_value=1.0, max_value=300.0),
    )
    @settings(max_examples=50)
    def test_padded_bbox_expansion_property(self, x1, y1, w, h):
        """Property: padded area >= original area (for in-bounds bboxes)."""
        assume(x1 + w <= 1920.0)
        assume(y1 + h <= 1080.0)
        bbox = [x1, y1, x1 + w, y1 + h]
        frame_shape = (1080, 1920)
        cfg = make_config()

        result = compute_padded_bbox(bbox, frame_shape, cfg)
        x1p, y1p, x2p, y2p = result

        original_area = w * h
        padded_area = (x2p - x1p) * (y2p - y1p)
        assert padded_area >= original_area

    @given(
        x1=st.floats(min_value=0.0, max_value=1919.0),
        y1=st.floats(min_value=0.0, max_value=1079.0),
        w=st.floats(min_value=1.0, max_value=500.0),
        h=st.floats(min_value=1.0, max_value=300.0),
    )
    @settings(max_examples=50)
    def test_padded_bbox_frame_clamping_property(self, x1, y1, w, h):
        """Property: padded bbox stays within frame boundaries."""
        bbox = [x1, y1, x1 + w, y1 + h]
        frame_shape = (1080, 1920)
        cfg = make_config()

        result = compute_padded_bbox(bbox, frame_shape, cfg)
        x1p, y1p, x2p, y2p = result

        assert x1p >= 0.0
        assert y1p >= 0.0
        assert x2p <= 1920.0
        assert y2p <= 1080.0


# ===========================================================================
# TestExtractCropFromBbox
# ===========================================================================


class TestExtractCropFromBbox:
    """Tests for extract_crop_from_bbox."""

    def test_extract_crop_normal(self):
        """Normal extraction yields correct pixel values."""
        frame = _make_frame(100, 200)
        bbox = [10.0, 20.0, 50.0, 60.0]

        crop = extract_crop_from_bbox(frame, bbox)

        assert crop is not None
        np.testing.assert_array_equal(crop, frame[20:60, 10:50])

    def test_extract_crop_memory_independence(self):
        """Crop owns its data — modifying crop doesn't affect frame."""
        frame = _make_frame(100, 200)
        bbox = [10.0, 20.0, 50.0, 60.0]

        crop = extract_crop_from_bbox(frame, bbox)
        assert crop is not None
        assert crop.base is None  # owns its data

        original_pixel = frame[20, 10].copy()
        crop[0, 0] = [255, 255, 255]
        np.testing.assert_array_equal(frame[20, 10], original_pixel)

    def test_extract_crop_degenerate_x(self):
        """x2 <= x1 after rounding returns None."""
        frame = _make_frame(100, 200)
        bbox = [50.0, 20.0, 50.0, 60.0]  # x2 == x1

        assert extract_crop_from_bbox(frame, bbox) is None

    def test_extract_crop_degenerate_y(self):
        """y2 <= y1 after rounding returns None."""
        frame = _make_frame(100, 200)
        bbox = [10.0, 50.0, 50.0, 50.0]  # y2 == y1

        assert extract_crop_from_bbox(frame, bbox) is None

    def test_extract_crop_zero_size(self):
        """Very close coords that round to same value return None."""
        frame = _make_frame(100, 200)
        # round(50.3)=50, round(50.4)=50 → x2_int == x1_int
        bbox = [50.3, 20.0, 50.4, 60.0]

        assert extract_crop_from_bbox(frame, bbox) is None

    def test_extract_crop_exact_integer(self):
        """Exact integer coordinates — no rounding ambiguity."""
        frame = _make_frame(100, 200)
        bbox = [10.0, 20.0, 110.0, 60.0]

        crop = extract_crop_from_bbox(frame, bbox)

        assert crop is not None
        assert crop.shape == (40, 100, 3)
        np.testing.assert_array_equal(crop, frame[20:60, 10:110])

    def test_extract_crop_bankers_rounding(self):
        """Banker's rounding: round(0.5)=0, round(100.5)=100 — ambiguity #7."""
        frame = _make_frame(100, 200)
        # Python 3 banker's rounding: round to nearest even
        # round(0.5)=0, round(100.5)=100, round(50.5)=50
        bbox = [0.5, 0.5, 100.5, 50.5]

        crop = extract_crop_from_bbox(frame, bbox)

        assert crop is not None
        # x1=0, y1=0, x2=100, y2=50
        assert crop.shape == (50, 100, 3)
        np.testing.assert_array_equal(crop, frame[0:50, 0:100])

    def test_extract_crop_shape(self):
        """Crop shape matches expected (y2-y1, x2-x1, 3) dimensions."""
        frame = _make_frame(100, 200)
        bbox = [10.0, 5.0, 80.0, 55.0]

        crop = extract_crop_from_bbox(frame, bbox)

        assert crop is not None
        assert crop.shape == (50, 70, 3)
        assert crop.dtype == np.uint8


# ===========================================================================
# TestAdaptiveResizeCrop
# ===========================================================================


class TestAdaptiveResizeCrop:
    """Tests for adaptive_resize_crop."""

    def test_adaptive_resize_disabled_zero(self):
        """threshold=0 disables resizing regardless of crop width."""
        crop = np.zeros((100, 1000, 3), dtype=np.uint8)

        result, was_resized, scale, new_w, new_h = adaptive_resize_crop(crop, 0)

        assert result is crop
        assert was_resized is False
        assert scale == 1.0
        assert new_w == 1000
        assert new_h == 100

    def test_adaptive_resize_disabled_negative(self):
        """Negative threshold disables resizing."""
        crop = np.zeros((100, 1000, 3), dtype=np.uint8)

        result, was_resized, scale, _, _ = adaptive_resize_crop(crop, -1)

        assert result is crop
        assert was_resized is False
        assert scale == 1.0

    def test_adaptive_resize_below_threshold(self):
        """Width below threshold: not resized, returns same object."""
        crop = np.zeros((100, 400, 3), dtype=np.uint8)

        result, was_resized, scale, new_w, new_h = adaptive_resize_crop(crop, 800)

        assert result is crop  # same object identity
        assert was_resized is False
        assert scale == 1.0
        assert new_w == 400
        assert new_h == 100

    def test_adaptive_resize_at_threshold(self):
        """Width exactly at threshold: not resized (uses <=)."""
        crop = np.zeros((100, 800, 3), dtype=np.uint8)

        result, was_resized, scale, new_w, _ = adaptive_resize_crop(crop, 800)

        assert result is crop
        assert was_resized is False
        assert scale == 1.0
        assert new_w == 800

    def test_adaptive_resize_above_threshold(self):
        """Width == threshold + 1: resized."""
        crop = np.zeros((100, 801, 3), dtype=np.uint8)

        _, was_resized, scale, new_w, _ = adaptive_resize_crop(crop, 800)

        assert was_resized is True
        assert new_w == 800
        assert scale == pytest.approx(800 / 801)

    def test_adaptive_resize_scale_factor(self):
        """Scale factor = threshold / original_width."""
        crop = np.zeros((200, 1600, 3), dtype=np.uint8)

        _, _, scale, _, _ = adaptive_resize_crop(crop, 800)

        assert scale == pytest.approx(800 / 1600)
        assert scale == pytest.approx(0.5)

    def test_adaptive_resize_exact_width(self):
        """After resize, new_w == threshold exactly."""
        crop = np.zeros((200, 1600, 3), dtype=np.uint8)

        result, _, _, new_w, _ = adaptive_resize_crop(crop, 800)

        assert new_w == 800
        assert result.shape[1] == 800

    def test_adaptive_resize_aspect_preserved(self):
        """Aspect ratio preserved within +/- 1px rounding error."""
        crop = np.zeros((300, 1200, 3), dtype=np.uint8)  # aspect ratio 0.25

        _, _, _, new_w, new_h = adaptive_resize_crop(crop, 800)

        original_aspect = 300 / 1200
        new_aspect = new_h / new_w
        assert abs(new_aspect - original_aspect) < 2 / new_w

    def test_adaptive_resize_scale_range(self):
        """Scale factor in (0, 1.0] — never upsamples."""
        # Not resized: scale == 1.0
        crop_small = np.zeros((100, 400, 3), dtype=np.uint8)
        _, _, scale1, _, _ = adaptive_resize_crop(crop_small, 800)
        assert scale1 == 1.0

        # Resized: 0 < scale < 1.0
        crop_large = np.zeros((100, 5000, 3), dtype=np.uint8)
        _, _, scale2, _, _ = adaptive_resize_crop(crop_large, 800)
        assert 0 < scale2 < 1.0

    def test_adaptive_resize_inter_area(self):
        """Resize uses INTER_AREA interpolation (matches cv2.resize output)."""
        rng = np.random.default_rng(42)
        crop = rng.integers(0, 256, (200, 1600, 3), dtype=np.uint8)
        threshold = 800

        result, _, _, new_w, new_h = adaptive_resize_crop(crop, threshold)

        expected = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_AREA)
        np.testing.assert_array_equal(result, expected)


# ===========================================================================
# TestValidateCropDimensions
# ===========================================================================


class TestValidateCropDimensions:
    """Tests for validate_crop_dimensions."""

    def test_validate_crop_at_threshold(self):
        """Crop at exact threshold dimensions returns True (inclusive >=)."""
        crop = np.zeros((40, 100, 3), dtype=np.uint8)
        cfg = make_config(crop_min_width=100, crop_min_height=40)

        assert validate_crop_dimensions(crop, cfg) is True

    def test_validate_crop_below_width(self):
        """Width below threshold returns False."""
        crop = np.zeros((40, 99, 3), dtype=np.uint8)
        cfg = make_config(crop_min_width=100, crop_min_height=40)

        assert validate_crop_dimensions(crop, cfg) is False

    def test_validate_crop_below_height(self):
        """Height below threshold returns False."""
        crop = np.zeros((39, 100, 3), dtype=np.uint8)
        cfg = make_config(crop_min_width=100, crop_min_height=40)

        assert validate_crop_dimensions(crop, cfg) is False

    def test_validate_crop_both_below(self):
        """Both dimensions below threshold returns False."""
        crop = np.zeros((39, 99, 3), dtype=np.uint8)
        cfg = make_config(crop_min_width=100, crop_min_height=40)

        assert validate_crop_dimensions(crop, cfg) is False


# ===========================================================================
# TestCropTrackRoi
# ===========================================================================


class TestCropTrackRoi:
    """Tests for crop_track_roi."""

    def test_crop_track_roi_normal(self):
        """Normal track in center produces valid RoiImage."""
        frame = _make_frame(1080, 1920)
        track = make_track(position=[500.0, 300.0, 200.0, 100.0])
        cfg = make_config()

        roi = crop_track_roi(frame, track, 0, cfg)

        assert roi is not None
        assert roi.track_id == track.track_id
        assert roi.frame_idx == 0
        assert roi.crop_img is not None
        assert roi.crop_img.shape[2] == 3

    def test_crop_track_roi_field_consistency(self):
        """RoiImage fields match track and frame properties."""
        frame = _make_frame(1080, 1920)
        track = make_track(
            position=[500.0, 300.0, 200.0, 100.0],
            confidence=0.95,
        )
        cfg = make_config()

        roi = crop_track_roi(frame, track, 42, cfg)

        assert roi is not None
        assert roi.track_id == track.track_id
        assert roi.bbox == track.get_bbox()
        assert roi.confidence == 0.95
        assert roi.frame_idx == 42
        assert roi.frame_width == 1920
        assert roi.frame_height == 1080

    def test_crop_track_roi_keypoint_translation(self):
        """Fresh keypoints are translated to crop coordinates."""
        frame = _make_frame(1080, 1920)
        kp_frame = [(450.0, 280.0), (550.0, 320.0)]
        kp_scores = [0.9, 0.8]
        track = make_track(
            position=[500.0, 300.0, 200.0, 100.0],
            keypoints=kp_frame,
            keypoint_scores=kp_scores,
            last_seen_frame=5,
        )
        cfg = make_config()

        roi = crop_track_roi(frame, track, 5, cfg)

        assert roi is not None
        assert roi.keypoints is not None
        assert len(roi.keypoints) == 2
        assert roi.keypoint_scores == kp_scores

        # Verify translation: (x_frame - x0_padded) * scale
        x0_pad = roi.padded_bbox[0]
        y0_pad = roi.padded_bbox[1]
        scale = roi.resize_scale
        for i, (x_frame, y_frame) in enumerate(kp_frame):
            x_crop, y_crop = roi.keypoints[i]
            assert x_crop == pytest.approx((x_frame - x0_pad) * scale)
            assert y_crop == pytest.approx((y_frame - y0_pad) * scale)

    def test_crop_track_roi_stale_keypoints(self):
        """Stale keypoints (last_seen_frame != frame_idx) produce None keypoints."""
        frame = _make_frame(1080, 1920)
        track = make_track(
            position=[500.0, 300.0, 200.0, 100.0],
            keypoints=[(450.0, 280.0)],
            keypoint_scores=[0.9],
            last_seen_frame=3,  # stale: not equal to frame_idx=5
        )
        cfg = make_config()

        roi = crop_track_roi(frame, track, 5, cfg)

        assert roi is not None
        assert roi.keypoints is None

    def test_crop_track_roi_no_keypoints(self):
        """Track with no keypoints produces roi.keypoints=None."""
        frame = _make_frame(1080, 1920)
        track = make_track(position=[500.0, 300.0, 200.0, 100.0])
        cfg = make_config()

        roi = crop_track_roi(frame, track, 0, cfg)

        assert roi is not None
        assert roi.keypoints is None
        assert roi.keypoint_scores is None

    def test_crop_track_roi_too_small(self):
        """Very small track fails dimension validation, returns None."""
        frame = _make_frame(1080, 1920)
        # 20x10 bbox → after padding (12px) → 44x34 crop → below 100x40 min
        track = make_track(position=[500.0, 300.0, 20.0, 10.0])
        cfg = make_config(crop_min_width=100, crop_min_height=40)

        roi = crop_track_roi(frame, track, 0, cfg)

        assert roi is None

    def test_crop_track_roi_degenerate(self):
        """Small track with high min dims returns None."""
        frame = _make_frame(1080, 1920)
        track = make_track(position=[500.0, 300.0, 30.0, 15.0])
        cfg = make_config(crop_min_width=100, crop_min_height=100)

        roi = crop_track_roi(frame, track, 0, cfg)

        assert roi is None

    @given(
        kp_x=st.floats(min_value=420.0, max_value=580.0),
        kp_y=st.floats(min_value=260.0, max_value=340.0),
    )
    @settings(max_examples=50)
    def test_crop_track_roi_keypoint_roundtrip(self, kp_x, kp_y):
        """Property: kp_crop / scale + padded_x0 approx kp_frame."""
        frame = _make_frame(1080, 1920)
        track = make_track(
            position=[500.0, 300.0, 200.0, 100.0],
            keypoints=[(kp_x, kp_y)],
            keypoint_scores=[0.9],
            last_seen_frame=0,
        )
        cfg = make_config()

        roi = crop_track_roi(frame, track, 0, cfg)

        assert roi is not None
        assert roi.keypoints is not None

        x_crop, y_crop = roi.keypoints[0]
        x0_pad = roi.padded_bbox[0]
        y0_pad = roi.padded_bbox[1]
        scale = roi.resize_scale

        # Roundtrip: kp_frame ≈ kp_crop / scale + padded_origin
        assert x_crop / scale + x0_pad == pytest.approx(kp_x, abs=1e-6)
        assert y_crop / scale + y0_pad == pytest.approx(kp_y, abs=1e-6)


# ===========================================================================
# TestCropTracks
# ===========================================================================


class TestCropTracks:
    """Tests for crop_tracks."""

    def test_crop_tracks_empty(self):
        """Empty tracks list returns empty list."""
        frame = _make_frame(1080, 1920)
        cfg = make_config()

        result = crop_tracks(frame, [], 0, cfg)

        assert result == []

    def test_crop_tracks_all_fail(self):
        """All tracks too small for min dimensions returns empty list."""
        frame = _make_frame(1080, 1920)
        tracks = [
            make_track(position=[500.0, 300.0, 20.0, 10.0]),
            make_track(position=[600.0, 400.0, 20.0, 10.0]),
        ]
        cfg = make_config(crop_min_width=100, crop_min_height=40)

        result = crop_tracks(frame, tracks, 0, cfg)

        assert result == []

    def test_crop_tracks_mixed(self):
        """Mixed: large tracks pass, small ones fail."""
        frame = _make_frame(1080, 1920)
        tracks = [
            make_track(position=[500.0, 300.0, 200.0, 100.0]),  # passes
            make_track(position=[600.0, 400.0, 20.0, 10.0]),  # fails
            make_track(position=[800.0, 500.0, 300.0, 150.0]),  # passes
        ]
        cfg = make_config()

        result = crop_tracks(frame, tracks, 0, cfg)

        assert len(result) == 2
        assert result[0].track_id == tracks[0].track_id
        assert result[1].track_id == tracks[2].track_id

    def test_crop_tracks_memory_independence(self):
        """Each crop is memory-independent from others and from the frame."""
        frame = _make_frame(1080, 1920)
        tracks = [
            make_track(position=[500.0, 300.0, 200.0, 100.0]),
            make_track(position=[800.0, 500.0, 200.0, 100.0]),
        ]
        cfg = make_config()

        result = crop_tracks(frame, tracks, 0, cfg)

        assert len(result) == 2
        for roi in result:
            assert roi.crop_img.base is None

        # Mutating one crop doesn't affect another
        pixel_before = result[1].crop_img[0, 0].copy()
        result[0].crop_img[0, 0] = [255, 255, 255]
        np.testing.assert_array_equal(result[1].crop_img[0, 0], pixel_before)

    @given(n=st.integers(min_value=0, max_value=10))
    @settings(max_examples=50)
    def test_crop_tracks_count_property(self, n):
        """Property: len(result) <= len(tracks), no None in result."""
        frame = _make_frame(1080, 1920)
        tracks = [make_track(position=[500.0, 300.0, 200.0, 100.0]) for _ in range(n)]
        cfg = make_config()

        result = crop_tracks(frame, tracks, 0, cfg)

        assert len(result) <= len(tracks)
        assert all(roi is not None for roi in result)
