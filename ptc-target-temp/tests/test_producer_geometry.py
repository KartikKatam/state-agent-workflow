"""Tests for pure geometry functions in ops_tracking and ops_detection.

Covers: bbox/position conversion, IoU, aspect ratio, IoU matrix,
and both geometric filter variants (tracking without reasons,
detection with reasons). Heavy use of Hypothesis for roundtrip,
symmetry, and range properties.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings, strategies as st

from producer.ops_detection import _filter_detections_geometric
from producer.ops_tracking import (
    bbox_to_position,
    compute_aspect_ratio,
    compute_iou,
    compute_iou_matrix,
    filter_detections_geometric,
    position_to_bbox,
)

from .conftest_producer import make_config, make_detection, st_bbox, st_position

# ===== bbox_to_position / position_to_bbox =====


class TestBboxToPosition:
    """Tests for bbox_to_position conversion."""

    def test_bbox_to_position_known(self):
        """Golden: [100,200,300,400] → [200,300,200,200]."""
        result = bbox_to_position([100.0, 200.0, 300.0, 400.0])
        assert result == [200.0, 300.0, 200.0, 200.0]

    def test_bbox_to_position_zero_area(self):
        """Edge: x1==x2 → w==0."""
        result = bbox_to_position([150.0, 200.0, 150.0, 400.0])
        assert result[2] == 0.0  # w
        assert result[0] == 150.0  # cx

    @given(bbox=st_bbox())
    @settings(max_examples=50)
    def test_bbox_to_position_center_midpoint(self, bbox):
        """Property: cx == (x1+x2)/2."""
        result = bbox_to_position(bbox)
        x1, y1, x2, y2 = bbox
        assert result[0] == pytest.approx((x1 + x2) / 2)  # cx
        assert result[1] == pytest.approx((y1 + y2) / 2)  # cy

    @given(bbox=st_bbox())
    @settings(max_examples=50)
    def test_bbox_to_position_width_positive(self, bbox):
        """Property: w > 0 for valid (x2 > x1) input."""
        result = bbox_to_position(bbox)
        assert result[2] > 0  # w
        assert result[3] > 0  # h


class TestPositionToBbox:
    """Tests for position_to_bbox conversion."""

    def test_position_to_bbox_known(self):
        """Golden: [200,300,200,200] → [100,200,300,400]."""
        result = position_to_bbox([200.0, 300.0, 200.0, 200.0])
        assert result == [100.0, 200.0, 300.0, 400.0]


class TestRoundtrip:
    """Property-based roundtrip tests for coordinate conversions."""

    @given(bbox=st_bbox())
    @settings(max_examples=50)
    def test_roundtrip_bbox_position_bbox(self, bbox):
        """Property: position_to_bbox(bbox_to_position(bbox)) ≈ bbox."""
        result = position_to_bbox(bbox_to_position(bbox))
        for a, b in zip(result, bbox, strict=True):
            assert a == pytest.approx(b)

    @given(pos=st_position())
    @settings(max_examples=50)
    def test_roundtrip_position_bbox_position(self, pos):
        """Property: bbox_to_position(position_to_bbox(pos)) ≈ pos."""
        result = bbox_to_position(position_to_bbox(pos))
        for a, b in zip(result, pos, strict=True):
            assert a == pytest.approx(b)


# ===== compute_iou =====


class TestComputeIou:
    """Tests for IoU computation."""

    def test_iou_identical(self):
        """Golden: identical bboxes → 1.0."""
        bbox = [100.0, 200.0, 300.0, 400.0]
        assert compute_iou(bbox, bbox) == 1.0

    def test_iou_non_overlapping(self):
        """Golden: non-overlapping → 0.0."""
        a = [0.0, 0.0, 100.0, 100.0]
        b = [200.0, 200.0, 300.0, 300.0]
        assert compute_iou(a, b) == 0.0

    def test_iou_edge_touching(self):
        """Edge: A.x2 == B.x1 → 0.0 (code uses < at line 70)."""
        a = [0.0, 0.0, 100.0, 100.0]
        b = [100.0, 0.0, 200.0, 100.0]
        assert compute_iou(a, b) == 0.0

    def test_iou_containment(self):
        """Core: one inside other → area_small / area_large."""
        outer = [0.0, 0.0, 200.0, 200.0]
        inner = [50.0, 50.0, 150.0, 150.0]
        # inner area = 100*100 = 10000, outer area = 200*200 = 40000
        # intersection = inner area = 10000
        # union = 40000 + 10000 - 10000 = 40000
        # iou = 10000/40000 = 0.25
        assert compute_iou(outer, inner) == pytest.approx(0.25)

    def test_iou_zero_union(self):
        """Edge: union <= 0 → 0.0 (degenerate zero-area bboxes)."""
        # Both have zero area (x1==x2)
        a = [100.0, 100.0, 100.0, 100.0]
        b = [100.0, 100.0, 100.0, 100.0]
        assert compute_iou(a, b) == 0.0

    @given(bbox=st_bbox())
    @settings(max_examples=50)
    def test_iou_symmetric(self, bbox):
        """Property: iou(A, B) == iou(B, A)."""
        other = [bbox[0] + 10.0, bbox[1] + 10.0, bbox[2] + 10.0, bbox[3] + 10.0]
        assert compute_iou(bbox, other) == pytest.approx(compute_iou(other, bbox))

    @given(a=st_bbox(), b=st_bbox())
    @settings(max_examples=50)
    def test_iou_range(self, a, b):
        """Property: 0 <= iou(A, B) <= 1."""
        result = compute_iou(a, b)
        assert 0.0 <= result <= 1.0

    @given(bbox=st_bbox())
    @settings(max_examples=50)
    def test_iou_self(self, bbox):
        """Property: iou(A, A) == 1.0 for valid bbox (x2 > x1)."""
        assert compute_iou(bbox, bbox) == pytest.approx(1.0)


# ===== compute_aspect_ratio =====


class TestComputeAspectRatio:
    """Tests for aspect ratio computation."""

    def test_aspect_ratio_normal(self):
        """Golden: normal bbox → w/h."""
        # bbox: 200 wide, 100 tall → aspect = 2.0
        assert compute_aspect_ratio([0.0, 0.0, 200.0, 100.0]) == pytest.approx(2.0)

    def test_aspect_ratio_square(self):
        """Golden: square → 1.0."""
        assert compute_aspect_ratio([0.0, 0.0, 100.0, 100.0]) == pytest.approx(1.0)

    def test_aspect_ratio_zero_height(self):
        """Edge: h == 0 → 0.0."""
        assert compute_aspect_ratio([0.0, 100.0, 200.0, 100.0]) == 0.0

    def test_aspect_ratio_negative_height(self):
        """Edge: h < 0 → 0.0."""
        assert compute_aspect_ratio([0.0, 200.0, 200.0, 100.0]) == 0.0


# ===== compute_iou_matrix =====


class TestComputeIouMatrix:
    """Tests for IoU matrix computation."""

    def test_iou_matrix_shape(self):
        """Constraint: shape is (N, M)."""
        preds = [[0.0, 0.0, 100.0, 100.0], [50.0, 50.0, 150.0, 150.0]]
        dets = [[10.0, 10.0, 90.0, 90.0], [60.0, 60.0, 140.0, 140.0], [200.0, 200.0, 300.0, 300.0]]
        result = compute_iou_matrix(preds, dets)
        assert result.shape == (2, 3)

    def test_iou_matrix_range(self):
        """Constraint: all values in [0, 1]."""
        preds = [[0.0, 0.0, 100.0, 100.0], [50.0, 50.0, 150.0, 150.0]]
        dets = [[10.0, 10.0, 90.0, 90.0], [200.0, 200.0, 300.0, 300.0]]
        result = compute_iou_matrix(preds, dets)
        assert np.all(result >= 0.0)
        assert np.all(result <= 1.0)

    def test_iou_matrix_empty(self):
        """Edge: empty inputs → zeros with correct shape."""
        # Empty predictions
        result = compute_iou_matrix([], [[0.0, 0.0, 100.0, 100.0]])
        assert result.shape == (0, 1)

        # Empty detections
        result = compute_iou_matrix([[0.0, 0.0, 100.0, 100.0]], [])
        assert result.shape == (1, 0)

        # Both empty
        result = compute_iou_matrix([], [])
        assert result.shape == (0, 0)

    def test_iou_matrix_diagonal(self):
        """Constraint: self-comparison diagonal == 1.0."""
        bboxes = [
            [0.0, 0.0, 100.0, 100.0],
            [50.0, 50.0, 200.0, 200.0],
            [300.0, 300.0, 400.0, 400.0],
        ]
        result = compute_iou_matrix(bboxes, bboxes)
        for i in range(len(bboxes)):
            assert result[i, i] == pytest.approx(1.0)


# ===== filter_detections_geometric (ops_tracking variant) =====


class TestFilterGeometricTracking:
    """Tests for ops_tracking.filter_detections_geometric (without reasons)."""

    def test_filter_geometric_tracking_empty(self):
        """Edge: empty → empty."""
        cfg = make_config()
        assert filter_detections_geometric([], cfg) == []

    def test_filter_geometric_tracking_all_pass(self):
        """Core: all pass → same list."""
        cfg = make_config(
            min_bbox_width=50, min_bbox_height=20, min_aspect_ratio=1.0, max_aspect_ratio=10.0
        )
        dets = [
            make_detection(bbox=[0.0, 0.0, 200.0, 100.0]),  # w=200, h=100, ar=2.0
            make_detection(bbox=[0.0, 0.0, 300.0, 150.0]),  # w=300, h=150, ar=2.0
        ]
        result = filter_detections_geometric(dets, cfg)
        assert len(result) == 2
        assert result[0] is dets[0]
        assert result[1] is dets[1]

    def test_filter_geometric_tracking_all_fail(self):
        """Core: all fail → empty."""
        cfg = make_config(min_bbox_width=500, min_bbox_height=500)
        dets = [
            make_detection(bbox=[0.0, 0.0, 100.0, 100.0]),  # w=100 < 500
            make_detection(bbox=[0.0, 0.0, 50.0, 50.0]),  # w=50 < 500
        ]
        result = filter_detections_geometric(dets, cfg)
        assert result == []

    def test_filter_geometric_tracking_at_threshold(self):
        """Edge: w == min_bbox_width → passes (uses <, not <=)."""
        cfg = make_config(
            min_bbox_width=100, min_bbox_height=40, min_aspect_ratio=0.1, max_aspect_ratio=100.0
        )
        det = make_detection(bbox=[0.0, 0.0, 100.0, 40.0])  # w=100, h=40
        result = filter_detections_geometric([det], cfg)
        assert len(result) == 1

    def test_filter_geometric_tracking_below_threshold(self):
        """Core: w < min_bbox_width → rejected."""
        cfg = make_config(
            min_bbox_width=100, min_bbox_height=40, min_aspect_ratio=0.1, max_aspect_ratio=100.0
        )
        det = make_detection(bbox=[0.0, 0.0, 99.0, 40.0])  # w=99 < 100
        result = filter_detections_geometric([det], cfg)
        assert result == []

    def test_filter_geometric_tracking_aspect_boundaries(self):
        """Edge: aspect ratio at min/max boundaries."""
        cfg = make_config(
            min_bbox_width=10, min_bbox_height=10, min_aspect_ratio=1.5, max_aspect_ratio=5.0
        )

        # At min boundary: w/h = 1.5 exactly → passes (uses <)
        det_min = make_detection(bbox=[0.0, 0.0, 150.0, 100.0])  # ar=1.5
        result = filter_detections_geometric([det_min], cfg)
        assert len(result) == 1

        # Below min boundary: w/h = 1.49 → rejected
        det_below = make_detection(bbox=[0.0, 0.0, 149.0, 100.0])  # ar=1.49
        result = filter_detections_geometric([det_below], cfg)
        assert result == []

        # At max boundary: w/h = 5.0 exactly → passes (uses >)
        det_max = make_detection(bbox=[0.0, 0.0, 500.0, 100.0])  # ar=5.0
        result = filter_detections_geometric([det_max], cfg)
        assert len(result) == 1

        # Above max boundary: w/h = 5.01 → rejected
        det_above = make_detection(bbox=[0.0, 0.0, 501.0, 100.0])  # ar=5.01
        result = filter_detections_geometric([det_above], cfg)
        assert result == []

    def test_filter_geometric_tracking_order(self):
        """Constraint: preserves input order."""
        cfg = make_config(
            min_bbox_width=50, min_bbox_height=20, min_aspect_ratio=0.1, max_aspect_ratio=100.0
        )
        dets = [
            make_detection(bbox=[0.0, 0.0, 200.0, 100.0], confidence=0.9),
            make_detection(bbox=[0.0, 0.0, 10.0, 10.0], confidence=0.8),  # fails width
            make_detection(bbox=[0.0, 0.0, 300.0, 100.0], confidence=0.7),
        ]
        result = filter_detections_geometric(dets, cfg)
        assert len(result) == 2
        assert result[0].confidence == 0.9
        assert result[1].confidence == 0.7


# ===== _filter_detections_geometric (ops_detection variant) =====


class TestFilterGeometricDetection:
    """Tests for ops_detection._filter_detections_geometric (with reasons)."""

    def test_filter_geometric_detection_returns_tuple(self):
        """Core: returns (filtered, reasons) tuple."""
        cfg = make_config()
        result = _filter_detections_geometric([], cfg)
        assert isinstance(result, tuple)
        assert len(result) == 2
        filtered, reasons = result
        assert isinstance(filtered, list)
        assert isinstance(reasons, dict)

    def test_filter_geometric_detection_accounting(self):
        """Constraint: len(filtered) + len(reasons) == len(input)."""
        cfg = make_config(
            min_bbox_width=50, min_bbox_height=20, min_aspect_ratio=1.0, max_aspect_ratio=10.0
        )
        dets = [
            make_detection(bbox=[0.0, 0.0, 200.0, 100.0]),  # passes
            make_detection(bbox=[0.0, 0.0, 10.0, 100.0]),  # fails width
            make_detection(bbox=[0.0, 0.0, 300.0, 150.0]),  # passes
            make_detection(bbox=[0.0, 0.0, 200.0, 5.0]),  # fails height
        ]
        filtered, reasons = _filter_detections_geometric(dets, cfg)
        assert len(filtered) + len(reasons) == len(dets)

    def test_filter_geometric_detection_reason_keys(self):
        """Constraint: reason keys are indices into original list."""
        cfg = make_config(
            min_bbox_width=50, min_bbox_height=20, min_aspect_ratio=1.0, max_aspect_ratio=10.0
        )
        dets = [
            make_detection(bbox=[0.0, 0.0, 200.0, 100.0]),  # idx 0: passes
            make_detection(bbox=[0.0, 0.0, 10.0, 100.0]),  # idx 1: fails
            make_detection(bbox=[0.0, 0.0, 300.0, 150.0]),  # idx 2: passes
        ]
        _filtered, reasons = _filter_detections_geometric(dets, cfg)
        for key in reasons:
            assert 0 <= key < len(dets)
        assert 1 in reasons

    def test_filter_geometric_detection_reason_strings(self):
        """Core: reason strings contain dimension info."""
        cfg = make_config(
            min_bbox_width=50, min_bbox_height=20, min_aspect_ratio=1.0, max_aspect_ratio=10.0
        )
        dets = [make_detection(bbox=[0.0, 0.0, 10.0, 100.0])]  # w=10 < 50
        _, reasons = _filter_detections_geometric(dets, cfg)
        reason = reasons[0]
        assert "10.0" in reason
        assert "50" in reason

    def test_filter_geometric_detection_width_reason(self):
        """Core: width too small gives correct reason."""
        cfg = make_config(
            min_bbox_width=100, min_bbox_height=20, min_aspect_ratio=0.1, max_aspect_ratio=100.0
        )
        det = make_detection(bbox=[0.0, 0.0, 50.0, 100.0])  # w=50 < 100
        _, reasons = _filter_detections_geometric([det], cfg)
        assert 0 in reasons
        assert "width_too_small" in reasons[0]

    def test_filter_geometric_detection_height_reason(self):
        """Core: height too small gives correct reason."""
        cfg = make_config(
            min_bbox_width=10, min_bbox_height=100, min_aspect_ratio=0.1, max_aspect_ratio=100.0
        )
        det = make_detection(bbox=[0.0, 0.0, 200.0, 50.0])  # h=50 < 100
        _, reasons = _filter_detections_geometric([det], cfg)
        assert 0 in reasons
        assert "height_too_small" in reasons[0]

    def test_filter_geometric_detection_narrow_reason(self):
        """Core: aspect too narrow gives correct reason."""
        cfg = make_config(
            min_bbox_width=10, min_bbox_height=10, min_aspect_ratio=2.0, max_aspect_ratio=10.0
        )
        det = make_detection(bbox=[0.0, 0.0, 100.0, 100.0])  # ar=1.0 < 2.0
        _, reasons = _filter_detections_geometric([det], cfg)
        assert 0 in reasons
        assert "aspect_too_narrow" in reasons[0]

    def test_filter_geometric_detection_wide_reason(self):
        """Core: aspect too wide gives correct reason."""
        cfg = make_config(
            min_bbox_width=10, min_bbox_height=10, min_aspect_ratio=1.0, max_aspect_ratio=3.0
        )
        det = make_detection(bbox=[0.0, 0.0, 500.0, 100.0])  # ar=5.0 > 3.0
        _, reasons = _filter_detections_geometric([det], cfg)
        assert 0 in reasons
        assert "aspect_too_wide" in reasons[0]

    def test_filter_geometric_detection_zero_height(self):
        """Edge: h == 0 → aspect = 0.0 → filtered as narrow."""
        cfg = make_config(
            min_bbox_width=10, min_bbox_height=10, min_aspect_ratio=1.0, max_aspect_ratio=10.0
        )
        det = make_detection(bbox=[0.0, 100.0, 200.0, 100.0])  # h=0
        _, reasons = _filter_detections_geometric([det], cfg)
        assert 0 in reasons
        # h=0 fails height check first (0 < 10)
        assert "height_too_small" in reasons[0]

    @given(
        min_w=st.integers(min_value=10, max_value=200),
        min_h=st.integers(min_value=10, max_value=100),
    )
    @settings(max_examples=50)
    def test_filter_geometric_relaxing_thresholds(self, min_w, min_h):
        """Property: relaxing thresholds never decreases filtered count."""
        dets = [
            make_detection(bbox=[0.0, 0.0, 150.0, 80.0]),
            make_detection(bbox=[0.0, 0.0, 50.0, 30.0]),
            make_detection(bbox=[0.0, 0.0, 300.0, 100.0]),
            make_detection(bbox=[0.0, 0.0, 20.0, 60.0]),
        ]
        cfg_strict = make_config(
            min_bbox_width=min_w,
            min_bbox_height=min_h,
            min_aspect_ratio=1.0,
            max_aspect_ratio=10.0,
        )
        cfg_relaxed = make_config(
            min_bbox_width=max(1, min_w - 10),
            min_bbox_height=max(1, min_h - 10),
            min_aspect_ratio=1.0,
            max_aspect_ratio=10.0,
        )
        strict_filtered, _ = _filter_detections_geometric(dets, cfg_strict)
        relaxed_filtered, _ = _filter_detections_geometric(dets, cfg_relaxed)
        assert len(relaxed_filtered) >= len(strict_filtered)
