"""Tests for consumer.ops_batch_select — batch selection pipeline.

Chunk-01: Eligibility gating and OCR-likelihood scoring.
Chunk-02: Anchor selection, pose distance, diversity fill.
Chunk-03: Enhancement duplicate selection.
Chunk-04: Output packaging with duplicate groups.
Chunk-05: Selection logging.
Chunk-06: Entry point, exports, and hardening.
Chunk-07: Pass B holistic integration tests.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest
from hypothesis import assume, given, settings, strategies as st

from consumer.config import ConsumerConfig, load_consumer_config
from consumer.models import EnhancedBatchSelection, RichQualityMetrics, RoiRichQuality
from consumer.ops_batch_select import (
    apply_eligibility_gate,
    compute_enhancement_upside_score,
    compute_ocr_likelihood_score,
    compute_pose_distance,
    diversity_fill,
    log_batch_selection_metrics,
    normalize_metric,
    package_batch_output,
    select_anchors,
    select_batch_with_enhancement,
    select_enhancement_duplicates,
)
from producer.models import FastQualityMetrics, RoiFastQuality, RoiImage

# ---------------------------------------------------------------------------
# Factory functions (module-level, NOT fixtures)
# ---------------------------------------------------------------------------


def make_roi_rq(
    *,
    plate_height_px: float = 80,
    tenengrad: float = 2000,
    global_contrast: float = 60,
    luminance_mean: float = 128,
    noise_std: float = 5.0,
    detection_confidence: float = 0.8,
    top8_eligible: bool = True,
    enhance_eligible: bool = False,
    very_blurry: bool = False,
    mildly_soft: bool = False,
    low_contrast: bool = False,
    exposure_bad: bool = False,
    noisy: bool = False,
    white_clip_fraction: float = 0.0,
    skew_degrees: float = 0.0,
    perspective_direction: float = 0.0,
    keypoints: list[tuple[float, float]] | None = None,
    bbox_center_x_normalized: float = 0.5,
    perspective_score: float = 1.0,
    plate_width_px: float = 200,
    track_id: str = "T-test",
    frame_idx: int = 0,
) -> RoiRichQuality:
    """Factory for RoiRichQuality with controllable fields and sensible defaults."""
    # Minimal crop image (1x1 BGR)
    crop = np.zeros((1, 1, 3), dtype=np.uint8)

    roi_img = RoiImage(
        track_id=track_id,
        crop_img=crop,
        bbox=[0.0, 0.0, plate_width_px, plate_height_px],
        padded_bbox=[0.0, 0.0, plate_width_px, plate_height_px],
        frame_idx=frame_idx,
        confidence=detection_confidence,
        frame_width=1920,
        frame_height=1080,
        keypoints=keypoints,
        keypoint_scores=[0.9, 0.9, 0.9, 0.9] if keypoints else None,
        crop_width=int(plate_width_px),
        crop_height=int(plate_height_px),
    )

    fast_metrics = FastQualityMetrics(
        focus_tenengrad=float(tenengrad),
        brightness_mean=luminance_mean / 255.0,
        contrast_std=global_contrast / 100.0,
        over_exposed_frac=white_clip_fraction,
        under_exposed_frac=0.0,
        band_edge_mean=100.0,
        gradient_histogram=np.zeros(25, dtype=np.float32),
    )

    roi_fq = RoiFastQuality(
        roi=roi_img,
        metrics=fast_metrics,
        quality_score=0.5,
        passes_min_quality=True,
        thumb_gray=np.zeros((16, 32), dtype=np.uint8),
    )

    rich_metrics = RichQualityMetrics(
        track_id=track_id,
        frame_idx=frame_idx,
        canonical_width=256,
        canonical_height=128,
        plate_width_px=plate_width_px,
        plate_height_px=plate_height_px,
        crop_clip_fraction=0.0,
        detection_confidence=detection_confidence,
        keypoints=keypoints,
        keypoint_scores=[0.9, 0.9, 0.9, 0.9] if keypoints else None,
        keypoint_confidence_min=0.9 if keypoints else None,
        keypoint_confidence_mean=0.9 if keypoints else None,
        skew_degrees=skew_degrees,
        perspective_score=perspective_score,
        perspective_direction=perspective_direction,
        luminance_mean=luminance_mean,
        luminance_p05=20.0,
        luminance_p95=230.0,
        black_clip_fraction=0.0,
        white_clip_fraction=white_clip_fraction,
        global_contrast=global_contrast,
        local_contrast=30.0,
        tenengrad=tenengrad,
        tenengrad_horizontal=tenengrad * 0.6,
        tenengrad_vertical=tenengrad * 0.4,
        blur_anisotropy=1.5,
        noise_std=noise_std,
        flat_region_fraction=0.3,
        too_small=plate_height_px < 40,
        clipped=False,
        very_blurry=very_blurry,
        mildly_soft=mildly_soft,
        exposure_bad=exposure_bad,
        low_contrast=low_contrast,
        noisy=noisy,
        vertical_edges_weak=False,
        horizontal_edges_weak=False,
        top8_eligible=top8_eligible,
        enhance_eligible=enhance_eligible,
        homography_eligible=keypoints is not None,
        bbox_center_x_normalized=bbox_center_x_normalized,
        bbox_center_y_normalized=0.5,
        diversity_signature=np.array(
            [
                skew_degrees / 45.0,
                (plate_height_px - 40) / 100.0,
                bbox_center_x_normalized,
                perspective_direction,
            ],
            dtype=np.float32,
        ),
        quad_area_px=None,
        edge_ratio=None,
    )

    return RoiRichQuality(roi_fq=roi_fq, metrics=rich_metrics)


def make_cfg(**overrides) -> ConsumerConfig:
    """Factory for ConsumerConfig with all batch-selector fields at defaults."""
    return load_consumer_config(**overrides)


# ---------------------------------------------------------------------------
# Shared test constants (chunk-08: extracted from ~10 tests)
# ---------------------------------------------------------------------------

# 8 positions with pairwise pose-distance >= 0.155 under DEFAULT diversity weights
# (skew=0.50, quad=0.30, persp=0.10, scale=0.10) and min_distance=0.15.
# Changing any default weight or min_distance may invalidate these positions.
WELL_SPREAD_POSITIONS_8: list[tuple[float, float, float, float]] = [
    #  (skew,  bbox_x, height, persp)
    (-56, 0.02, 50, -1.0),
    (-40, 0.15, 65, -0.75),
    (-24, 0.28, 80, -0.50),
    (-8, 0.41, 95, -0.25),
    (8, 0.54, 110, 0.0),
    (24, 0.67, 125, 0.25),
    (40, 0.80, 140, 0.50),
    (56, 0.93, 155, 0.75),
]


def assert_positions_well_spread(
    positions: list[tuple[float, float, float, float]],
    cfg: ConsumerConfig | None = None,
) -> None:
    """Guard assertion: verify all positions are pairwise >= cfg.diversity_min_distance apart."""
    if cfg is None:
        cfg = make_cfg()
    rois = [
        make_roi_rq(
            skew_degrees=s,
            bbox_center_x_normalized=b,
            plate_height_px=h,
            perspective_direction=p,
        )
        for s, b, h, p in positions
    ]
    for i in range(len(rois)):
        for j in range(i + 1, len(rois)):
            dist = compute_pose_distance(rois[i], rois[j], cfg)
            assert dist >= cfg.diversity_min_distance, (
                f"Positions {i} and {j} too close: dist={dist:.4f} < {cfg.diversity_min_distance}"
            )


# 6 positions for mixed-keypoints test with wide bbox_center_x separation.
# Positions 0-1 get keypoints, 2-5 get None (bbox fallback).
# bbox_center_x values spaced >= 0.15 apart for reliable bbox-fallback distances.
MIXED_KEYPOINT_POSITIONS_6: list[tuple[float, float, float, float]] = [
    #  (skew,  bbox_x, height, persp)
    (-50, 0.05, 55, -0.9),
    (-15, 0.30, 80, -0.3),
    (0, 0.50, 100, 0.0),
    (20, 0.70, 120, 0.4),
    (40, 0.85, 140, 0.7),
    (60, 0.95, 160, 1.0),
]


# ===========================================================================
# TestNormalizeMetric (7 tests)
# ===========================================================================


class TestNormalizeMetric:
    """Tests for normalize_metric."""

    def test_midpoint(self):
        """normalize_metric(50, 0, 100) == 0.5"""
        assert normalize_metric(50, 0, 100) == pytest.approx(0.5)

    def test_at_low(self):
        """normalize_metric(0, 0, 100) == 0.0"""
        assert normalize_metric(0, 0, 100) == pytest.approx(0.0)

    def test_at_high(self):
        """normalize_metric(100, 0, 100) == 1.0"""
        assert normalize_metric(100, 0, 100) == pytest.approx(1.0)

    def test_below_low_clamps(self):
        """normalize_metric(-10, 0, 100) == 0.0 (clamps below range)"""
        assert normalize_metric(-10, 0, 100) == pytest.approx(0.0)

    def test_above_high_clamps(self):
        """normalize_metric(200, 0, 100) == 1.0 (clamps above range)"""
        assert normalize_metric(200, 0, 100) == pytest.approx(1.0)

    def test_degenerate_range(self):
        """normalize_metric(5, 5, 5) == 0.5 (degenerate range returns midpoint)"""
        assert normalize_metric(5, 5, 5) == pytest.approx(0.5)

    def test_inverted_range(self):
        """normalize_metric(5, 10, 5) == 0.5 (inverted range returns midpoint)"""
        assert normalize_metric(5, 10, 5) == pytest.approx(0.5)


# ===========================================================================
# TestEligibilityGate (4 tests)
# ===========================================================================


class TestEligibilityGate:
    """Tests for apply_eligibility_gate."""

    def test_gate_empty_input(self):
        """Empty input returns empty list."""
        cfg = make_cfg()
        assert apply_eligibility_gate([], cfg) == []

    def test_gate_filters_ineligible(self):
        """Only ROIs passing both top8_eligible AND plate_height >= min survive."""
        # A: eligible, height 80 (>= 40) -> passes
        a = make_roi_rq(top8_eligible=True, plate_height_px=80)
        # B: not eligible -> filtered
        b = make_roi_rq(top8_eligible=False, plate_height_px=80)
        # C: eligible but height 30 (< 40) -> filtered
        c = make_roi_rq(top8_eligible=True, plate_height_px=30)

        result = apply_eligibility_gate([a, b, c], make_cfg())
        assert len(result) == 1
        assert result[0] is a

    def test_gate_all_fail(self):
        """All 5 ROIs with top8_eligible=False returns empty."""
        rois = [make_roi_rq(top8_eligible=False) for _ in range(5)]
        assert apply_eligibility_gate(rois, make_cfg()) == []

    def test_gate_tenengrad_floor_toggle(self):
        """ROI with tenengrad=500. Floors OFF: passes. Floors ON with floor=1000: filtered."""
        roi = make_roi_rq(tenengrad=500)

        # Floors OFF (default) -> passes
        cfg_off = make_cfg(batch_enable_signal_floors=False)
        result_off = apply_eligibility_gate([roi], cfg_off)
        assert len(result_off) == 1
        assert result_off[0] is roi

        # Floors ON with floor=1000 -> filtered
        cfg_on = make_cfg(batch_enable_signal_floors=True, batch_tenengrad_floor=1000)
        result_on = apply_eligibility_gate([roi], cfg_on)
        assert len(result_on) == 0


# ===========================================================================
# TestOcrLikelihoodScore (4 tests)
# ===========================================================================


class TestOcrLikelihoodScore:
    """Tests for compute_ocr_likelihood_score."""

    def test_ocr_score_bounded(self):
        """Extreme and max values both produce scores in [0.0, 1.0]."""
        cfg = make_cfg()

        # Extreme low values
        roi_low = make_roi_rq(
            tenengrad=0,
            plate_height_px=40,
            global_contrast=0,
            luminance_mean=0,
            noise_std=100,
            detection_confidence=0,
        )
        score_low = compute_ocr_likelihood_score(roi_low, [roi_low], cfg)
        assert 0.0 <= score_low <= 1.0

        # Extreme high values
        roi_high = make_roi_rq(
            tenengrad=10000,
            plate_height_px=200,
            global_contrast=100,
            luminance_mean=128,
            noise_std=0,
            detection_confidence=1.0,
        )
        score_high = compute_ocr_likelihood_score(roi_high, [roi_high], cfg)
        assert 0.0 <= score_high <= 1.0

    def test_ocr_score_weights(self):
        """Sharp ROI (tenengrad=5000) scores higher than soft ROI (tenengrad=500)."""
        cfg = make_cfg()
        sharp = make_roi_rq(tenengrad=5000)
        soft = make_roi_rq(tenengrad=500)
        eligible = [sharp, soft]

        score_sharp = compute_ocr_likelihood_score(sharp, eligible, cfg)
        score_soft = compute_ocr_likelihood_score(soft, eligible, cfg)
        assert score_sharp > score_soft

    def test_ocr_score_per_bin_normalization(self):
        """Among 5 ROIs with tenengrad [1000-5000], top scores higher than bottom."""
        cfg = make_cfg()
        rois = [make_roi_rq(tenengrad=t) for t in [1000, 2000, 3000, 4000, 5000]]

        score_top = compute_ocr_likelihood_score(rois[4], rois, cfg)
        score_bottom = compute_ocr_likelihood_score(rois[0], rois, cfg)
        assert score_top > score_bottom

    def test_ocr_score_single_candidate_no_crash(self):
        """Single ROI in eligible_list triggers fallback, no crash, result in [0, 1]."""
        cfg = make_cfg()
        roi = make_roi_rq()
        score = compute_ocr_likelihood_score(roi, [roi], cfg)
        assert 0.0 <= score <= 1.0

    def test_ocr_score_ideal_roi_floor(self):
        """Ideal ROI with excellent metrics scores >= 0.75 (hand-computed ~0.97).

        Pins a magnitude floor so scoring regressions that collapse all values
        near zero are caught even if relative ordering is preserved.
        """
        cfg = make_cfg()
        ideal = make_roi_rq(
            tenengrad=5000,
            plate_height_px=100,
            global_contrast=80,
            luminance_mean=128,
            noise_std=2.0,
            detection_confidence=0.95,
        )
        # 2-ROI eligible list to trigger per-bin normalization
        mediocre = make_roi_rq(tenengrad=1000, plate_height_px=50)
        eligible = [ideal, mediocre]
        score = compute_ocr_likelihood_score(ideal, eligible, cfg)
        assert score >= 0.75, f"Ideal ROI score {score:.3f} should be >= 0.75"


# ===========================================================================
# TestChunk01ConfigValidation (2 tests)
# ===========================================================================


class TestChunk01ConfigValidation:
    """Tests for ConsumerConfig validation of chunk-01 fields."""

    def test_config_rejects_negative_ocr_weight(self):
        """Negative OCR weight raises ValueError."""
        with pytest.raises(ValueError):
            make_cfg(ocr_score_weight_sharpness=-0.1)

    def test_config_rejects_zero_plate_height_ideal(self):
        """plate_height_ideal=0 raises ValueError."""
        with pytest.raises(ValueError):
            make_cfg(plate_height_ideal=0)


# ===========================================================================
# Golden example (1 test)
# ===========================================================================


def test_golden_chunk01_gate_and_score():
    """Golden: 6 ROIs with known eligibility and sharpness.

    Gate passes A, B, E, F. Score ordering: B > A > E > F.
    """
    cfg = make_cfg()

    # A: top8=T, h=80, ten=3000 -> passes gate
    a = make_roi_rq(top8_eligible=True, plate_height_px=80, tenengrad=3000)
    # B: top8=T, h=50, ten=4000 -> passes gate (h=50 >= 40)
    b = make_roi_rq(top8_eligible=True, plate_height_px=50, tenengrad=4000)
    # C: top8=F, h=80, ten=2000 -> fails gate (top8=False)
    c = make_roi_rq(top8_eligible=False, plate_height_px=80, tenengrad=2000)
    # D: top8=T, h=30, ten=5000 -> fails gate (h=30 < 40)
    d = make_roi_rq(top8_eligible=True, plate_height_px=30, tenengrad=5000)
    # E: top8=T, h=90, ten=1000 -> passes gate
    e = make_roi_rq(top8_eligible=True, plate_height_px=90, tenengrad=1000)
    # F: top8=T, h=60, ten=500 -> passes gate
    f = make_roi_rq(top8_eligible=True, plate_height_px=60, tenengrad=500)

    candidates = [a, b, c, d, e, f]

    # Gate: A, B, E, F pass; C (top8=F), D (h<40) fail
    eligible = apply_eligibility_gate(candidates, cfg)
    assert len(eligible) == 4
    eligible_ids = {id(r) for r in eligible}
    assert id(a) in eligible_ids
    assert id(b) in eligible_ids
    assert id(e) in eligible_ids
    assert id(f) in eligible_ids
    assert id(c) not in eligible_ids
    assert id(d) not in eligible_ids

    # Score ordering: A > B > E > F
    # B has highest sharpness (tenengrad=4000) but lowest height in bin (50px),
    # giving size_norm=0.0 via per-bin normalization. This 25% weight penalty
    # outweighs the sharpness advantage, so A (balanced h=80, t=3000) wins.
    score_a = compute_ocr_likelihood_score(a, eligible, cfg)
    score_b = compute_ocr_likelihood_score(b, eligible, cfg)
    score_e = compute_ocr_likelihood_score(e, eligible, cfg)
    score_f = compute_ocr_likelihood_score(f, eligible, cfg)

    assert score_a > score_b, f"A ({score_a:.3f}) should beat B ({score_b:.3f})"
    assert score_b > score_e, f"B ({score_b:.3f}) should beat E ({score_e:.3f})"
    assert score_e > score_f, f"E ({score_e:.3f}) should beat F ({score_f:.3f})"

    # Magnitude checks (audit-02): scoring must produce usable values, not near-zero
    assert score_a >= 0.55, f"Best score {score_a:.3f} should be >= 0.55 (meaningful magnitude)"
    assert score_a - score_f >= 0.10, (
        f"Score spread {score_a - score_f:.3f} should be >= 0.10 (meaningful discrimination)"
    )


# ===========================================================================
# Chunk-02: Anchor Selection + Diversity Fill
# ===========================================================================


# ===========================================================================
# TestAnchorSelection (3 tests)
# ===========================================================================


class TestAnchorSelection:
    """Tests for select_anchors."""

    def test_anchors_are_top2(self):
        """5 ROIs with distinct OCR scores -> returns top-2 by score."""
        cfg = make_cfg()
        rois = [
            make_roi_rq(tenengrad=5000, plate_height_px=100),  # A: highest
            make_roi_rq(tenengrad=4000, plate_height_px=90),  # B: second
            make_roi_rq(tenengrad=3000, plate_height_px=80),  # C
            make_roi_rq(tenengrad=2000, plate_height_px=70),  # D
            make_roi_rq(tenengrad=1000, plate_height_px=60),  # E: lowest
        ]
        eligible = rois

        # Compute OCR scores
        ocr_scores = {id(r): compute_ocr_likelihood_score(r, eligible, cfg) for r in eligible}

        result = select_anchors(eligible, ocr_scores, cfg)
        assert len(result) == 2
        assert result[0] is rois[0]
        assert result[1] is rois[1]

    def test_anchors_single_eligible(self):
        """1 ROI -> returns [A]."""
        cfg = make_cfg()
        a = make_roi_rq()
        ocr_scores = {id(a): 0.8}
        result = select_anchors([a], ocr_scores, cfg)
        assert len(result) == 1
        assert result[0] is a

    def test_anchor2_respects_pose_min_distance(self):
        """Anchor2 picks a diverse candidate over a higher-scoring near-copy."""
        cfg = make_cfg(anchor_min_pose_distance=0.07)
        a = make_roi_rq(skew_degrees=0.0, plate_height_px=80)  # anchor1
        b = make_roi_rq(skew_degrees=0.0, plate_height_px=80)  # near-copy of A
        c = make_roi_rq(skew_degrees=45.0, plate_height_px=80)  # diverse pose
        ocr_scores = {id(a): 0.9, id(b): 0.8, id(c): 0.7}

        result = select_anchors([a, b, c], ocr_scores, cfg)
        assert len(result) == 2
        assert result[0] is a  # anchor1 = top scorer
        assert result[1] is c  # anchor2 = diverse, not near-copy B

    def test_anchor2_fallback_when_no_diverse(self, caplog):
        """Anchor2 falls back to second-highest score when all near-copies."""
        cfg = make_cfg(anchor_min_pose_distance=0.07)
        a = make_roi_rq(skew_degrees=0.0, plate_height_px=80)
        b = make_roi_rq(skew_degrees=0.0, plate_height_px=80)
        c = make_roi_rq(skew_degrees=0.0, plate_height_px=80)
        ocr_scores = {id(a): 0.9, id(b): 0.8, id(c): 0.7}

        with caplog.at_level(logging.DEBUG):
            result = select_anchors([a, b, c], ocr_scores, cfg)
        assert len(result) == 2
        assert result[0] is a  # anchor1
        assert result[1] is b  # fallback: second-highest score
        assert "anchor2 fallback" in caplog.text

    def test_anchors_zero_eligible(self):
        """Empty list -> returns []."""
        cfg = make_cfg()
        result = select_anchors([], {}, cfg)
        assert result == []


# ===========================================================================
# TestPoseDistance (5 tests)
# ===========================================================================


class TestPoseDistance:
    """Tests for compute_pose_distance."""

    def test_pose_distance_identical(self):
        """Same ROI vs itself -> distance == 0.0."""
        cfg = make_cfg()
        roi = make_roi_rq(skew_degrees=10, bbox_center_x_normalized=0.3, plate_height_px=80)
        assert compute_pose_distance(roi, roi, cfg) == pytest.approx(0.0)

    def test_pose_distance_skew_dominant(self):
        """Skew 0 vs 45 degrees, no keypoints, same bbox/height/persp -> 0.25.

        skew_diff = 45/90 = 0.5, weight=0.50 -> contribution = 0.25
        quad_diff = 0 (same bbox_center), persp_diff = 0, scale_diff = 0
        Total = 0.25
        """
        cfg = make_cfg()
        a = make_roi_rq(skew_degrees=0, bbox_center_x_normalized=0.5, plate_height_px=80)
        b = make_roi_rq(skew_degrees=45, bbox_center_x_normalized=0.5, plate_height_px=80)
        dist = compute_pose_distance(a, b, cfg)
        assert dist == pytest.approx(0.25)

    def test_pose_distance_jitter_clamping(self):
        """Keypoints differ by 0.03 (< 0.05 threshold) -> quad component == 0.

        Same skew, persp, scale -> total distance = 0.0
        """
        cfg = make_cfg()
        # Keypoints in crop coords: 4 corners (TR, TL, BR, BL)
        # Use plate_width=200, plate_height=80 so normalized coords are known
        # Corners at exact positions
        kp_a = [(190.0, 10.0), (10.0, 10.0), (190.0, 70.0), (10.0, 70.0)]
        # Shift each corner by 3% * width=200 -> 6px in x, 3% * height=80 -> 2.4px in y
        # Mean L2 of normalized shifts: sqrt((6/200)^2 + (2.4/80)^2) = sqrt(0.03^2 + 0.03^2)
        # = sqrt(0.0018) ≈ 0.0424 per corner, mean ≈ 0.0424 < 0.05 threshold
        kp_b = [(196.0, 12.4), (16.0, 12.4), (196.0, 72.4), (16.0, 72.4)]

        a = make_roi_rq(
            keypoints=kp_a,
            plate_width_px=200,
            plate_height_px=80,
            skew_degrees=0,
            perspective_direction=0.0,
        )
        b = make_roi_rq(
            keypoints=kp_b,
            plate_width_px=200,
            plate_height_px=80,
            skew_degrees=0,
            perspective_direction=0.0,
        )
        dist = compute_pose_distance(a, b, cfg)
        assert dist == pytest.approx(0.0)

    def test_pose_distance_none_keypoints_fallback(self):
        """Both keypoints=None, bbox_center_x 0.2 vs 0.8 -> non-zero total.

        quad component = abs(0.2 - 0.8) = 0.6, weight=0.30 -> contribution = 0.18
        Total > 0.
        """
        cfg = make_cfg()
        a = make_roi_rq(
            keypoints=None,
            bbox_center_x_normalized=0.2,
            skew_degrees=0,
            plate_height_px=80,
            perspective_direction=0.0,
        )
        b = make_roi_rq(
            keypoints=None,
            bbox_center_x_normalized=0.8,
            skew_degrees=0,
            plate_height_px=80,
            perspective_direction=0.0,
        )
        dist = compute_pose_distance(a, b, cfg)
        assert dist > 0.0
        # quad contribution = 0.30 * 0.6 = 0.18 (all other components zero)
        assert dist == pytest.approx(0.18)

    def test_pose_distance_symmetric(self):
        """dist(A, B) == dist(B, A) for two different ROIs."""
        cfg = make_cfg()
        a = make_roi_rq(skew_degrees=15, bbox_center_x_normalized=0.3, plate_height_px=90)
        b = make_roi_rq(skew_degrees=-10, bbox_center_x_normalized=0.7, plate_height_px=60)
        assert compute_pose_distance(a, b, cfg) == pytest.approx(compute_pose_distance(b, a, cfg))


# ===========================================================================
# TestDiversityFill (3 tests)
# ===========================================================================


class TestDiversityFill:
    """Tests for diversity_fill."""

    def test_diversity_respects_max(self):
        """2 anchors + 20 novel remaining, max_batch_size=8 -> len(result) == 8."""
        cfg = make_cfg()
        # Anchors at extreme positions far from the grid
        anchors = [
            make_roi_rq(
                skew_degrees=-80,
                bbox_center_x_normalized=0.0,
                plate_height_px=40,
                perspective_direction=-1.0,
            ),
            make_roi_rq(
                skew_degrees=80,
                bbox_center_x_normalized=1.0,
                plate_height_px=160,
                perspective_direction=1.0,
            ),
        ]
        # 20 remaining on an evenly-spaced grid across 4 pose dimensions
        remaining = []
        for i in range(20):
            remaining.append(
                make_roi_rq(
                    skew_degrees=-45 + i * (90 / 19),
                    bbox_center_x_normalized=0.02 + i * (0.96 / 19),
                    plate_height_px=50 + i * (100 / 19),
                    perspective_direction=-0.9 + i * (1.8 / 19),
                )
            )
        all_rois = anchors + remaining
        ocr_scores = {id(r): 0.95 - i * 0.01 for i, r in enumerate(all_rois)}

        result = diversity_fill(anchors, remaining, ocr_scores, cfg)
        assert len(result) == 8

    def test_diversity_min_distance_enforced(self):
        """2 anchors + 10 remaining (8 near-identical + 2 novel) -> 4 total.

        Near-identical copies should be rejected by min_distance threshold.
        """
        cfg = make_cfg(diversity_min_distance=0.15)
        anchor1 = make_roi_rq(
            skew_degrees=0,
            bbox_center_x_normalized=0.5,
            plate_height_px=80,
            perspective_direction=0.0,
        )
        anchor2 = make_roi_rq(
            skew_degrees=30,
            bbox_center_x_normalized=0.2,
            plate_height_px=80,
            perspective_direction=0.5,
        )
        anchors = [anchor1, anchor2]

        remaining = []
        # 8 near-identical to anchor1 (tiny jitter, should be rejected)
        for i in range(8):
            remaining.append(
                make_roi_rq(
                    skew_degrees=0.5 * i,
                    bbox_center_x_normalized=0.50 + 0.01 * i,
                    plate_height_px=80,
                    perspective_direction=0.01 * i,
                )
            )
        # 2 very different from both anchors (should be accepted)
        remaining.append(
            make_roi_rq(
                skew_degrees=-45,
                bbox_center_x_normalized=0.9,
                plate_height_px=50,
                perspective_direction=-0.9,
            )
        )
        remaining.append(
            make_roi_rq(
                skew_degrees=45,
                bbox_center_x_normalized=0.1,
                plate_height_px=140,
                perspective_direction=0.9,
            )
        )

        all_rois = anchors + remaining
        ocr_scores = {id(r): 0.9 - i * 0.01 for i, r in enumerate(all_rois)}

        result = diversity_fill(anchors, remaining, ocr_scores, cfg)
        # 2 anchors + 2 novel = 4 (near-identical rejected)
        assert len(result) == 4
        # Anchors are always included
        result_ids = {id(r) for r in result}
        assert id(anchor1) in result_ids
        assert id(anchor2) in result_ids

    def test_diversity_fills_all_novel(self):
        """2 anchors + 6 remaining, all maximally different -> 8 total.

        All 8 positions are pairwise >= 0.15 apart across 4 pose dimensions,
        so every remaining candidate passes the diversity check.
        """
        cfg = make_cfg()
        assert_positions_well_spread(WELL_SPREAD_POSITIONS_8, cfg)
        all_rois = [
            make_roi_rq(
                skew_degrees=s,
                bbox_center_x_normalized=b,
                plate_height_px=h,
                perspective_direction=p,
            )
            for s, b, h, p in WELL_SPREAD_POSITIONS_8
        ]
        anchors = all_rois[:2]
        remaining = all_rois[2:]
        ocr_scores = {id(r): 0.9 - i * 0.05 for i, r in enumerate(all_rois)}

        result = diversity_fill(anchors, remaining, ocr_scores, cfg)
        assert len(result) == 8


# ===========================================================================
# TestChunk02ConfigValidation (2 tests)
# ===========================================================================


class TestChunk02ConfigValidation:
    """Tests for ConsumerConfig validation of chunk-02 diversity fields."""

    def test_config_rejects_all_zero_diversity_weights(self):
        """All 4 diversity weights = 0 -> raises ValueError."""
        with pytest.raises(ValueError):
            make_cfg(
                diversity_weight_skew=0,
                diversity_weight_quad=0,
                diversity_weight_persp=0,
                diversity_weight_scale=0,
            )

    def test_config_rejects_negative_min_distance(self):
        """diversity_min_distance=-0.1 -> raises ValueError."""
        with pytest.raises(ValueError):
            make_cfg(diversity_min_distance=-0.1)


# ===========================================================================
# Golden: Anchor + Diversity (1 test)
# ===========================================================================


def test_golden_anchor_diversity():
    """Golden: 8 eligible ROIs, 2 anchors, 4 novel accepted, 2 carbon copies excluded.

    Anchors: highest OCR scores (0.92, 0.88 range).
    Remaining 6: 4 with novel poses (dist >= 0.15 from all selected), 2 near-copies.
    Result: 6 total (2 anchors + 4 novel).
    """
    cfg = make_cfg()

    # 8 eligible ROIs with varied poses and quality
    rois = [
        # Anchors (will be top-2 by score due to high sharpness + good size)
        make_roi_rq(
            tenengrad=5000,
            plate_height_px=100,
            skew_degrees=0,
            bbox_center_x_normalized=0.5,
            perspective_direction=0.0,
        ),
        make_roi_rq(
            tenengrad=4500,
            plate_height_px=95,
            skew_degrees=10,
            bbox_center_x_normalized=0.4,
            perspective_direction=0.2,
        ),
        # Novel remaining (diverse poses)
        make_roi_rq(
            tenengrad=3000,
            plate_height_px=80,
            skew_degrees=-35,
            bbox_center_x_normalized=0.1,
            perspective_direction=-0.8,
        ),
        make_roi_rq(
            tenengrad=2800,
            plate_height_px=85,
            skew_degrees=40,
            bbox_center_x_normalized=0.9,
            perspective_direction=0.7,
        ),
        make_roi_rq(
            tenengrad=2500,
            plate_height_px=60,
            skew_degrees=-20,
            bbox_center_x_normalized=0.8,
            perspective_direction=-0.5,
        ),
        make_roi_rq(
            tenengrad=2200,
            plate_height_px=110,
            skew_degrees=25,
            bbox_center_x_normalized=0.2,
            perspective_direction=0.4,
        ),
        # Carbon copies (very similar to anchor 0 — should be rejected)
        make_roi_rq(
            tenengrad=2000,
            plate_height_px=100,
            skew_degrees=1,
            bbox_center_x_normalized=0.51,
            perspective_direction=0.01,
        ),
        make_roi_rq(
            tenengrad=1800,
            plate_height_px=100,
            skew_degrees=-1,
            bbox_center_x_normalized=0.49,
            perspective_direction=-0.01,
        ),
    ]

    eligible = rois

    # Compute OCR scores
    ocr_scores = {id(r): compute_ocr_likelihood_score(r, eligible, cfg) for r in eligible}

    # Select anchors (top 2)
    anchors = select_anchors(eligible, ocr_scores, cfg)
    assert len(anchors) == 2
    assert anchors[0] is rois[0]
    assert anchors[1] is rois[1]

    # Diversity fill
    remaining = [r for r in eligible if id(r) not in {id(a) for a in anchors}]
    result = diversity_fill(anchors, remaining, ocr_scores, cfg)

    # 2 anchors + 4 novel = 6 (carbon copies excluded by min_distance)
    assert len(result) == 6
    result_ids = {id(r) for r in result}
    # Anchors included
    assert id(rois[0]) in result_ids
    assert id(rois[1]) in result_ids
    # Novel ones included
    assert id(rois[2]) in result_ids
    assert id(rois[3]) in result_ids
    assert id(rois[4]) in result_ids
    assert id(rois[5]) in result_ids
    # Carbon copies excluded
    assert id(rois[6]) not in result_ids
    assert id(rois[7]) not in result_ids


# ===========================================================================
# Chunk-03: Enhancement Duplicate Selection
# ===========================================================================


# ===========================================================================
# TestUpsideScore (5 tests)
# ===========================================================================


class TestUpsideScore:
    """Tests for compute_enhancement_upside_score."""

    def test_upside_excellent_suppressed(self):
        """ROI with ocr_score=0.90 (> 0.85 threshold) -> upside == 0.0."""
        cfg = make_cfg()
        roi = make_roi_rq(low_contrast=True, enhance_eligible=True)
        upside = compute_enhancement_upside_score(roi, 0.90, cfg)
        assert upside == pytest.approx(0.0)

    def test_upside_blurry_zero(self):
        """ROI with very_blurry=True, ocr_score=0.50 -> upside == 0.0."""
        cfg = make_cfg()
        roi = make_roi_rq(very_blurry=True, low_contrast=True, enhance_eligible=True)
        upside = compute_enhancement_upside_score(roi, 0.50, cfg)
        assert upside == pytest.approx(0.0)

    def test_upside_blown_highlights_zero(self):
        """ROI with white_clip_fraction=0.35 (> 0.30 hard_max) -> upside == 0.0."""
        cfg = make_cfg()
        roi = make_roi_rq(
            white_clip_fraction=0.35,
            low_contrast=True,
            enhance_eligible=True,
        )
        upside = compute_enhancement_upside_score(roi, 0.50, cfg)
        assert upside == pytest.approx(0.0)

    def test_upside_low_contrast_only(self):
        """ROI with low_contrast=True only, ocr_score=0.50 -> upside == 0.40."""
        cfg = make_cfg()
        roi = make_roi_rq(low_contrast=True, enhance_eligible=True)
        upside = compute_enhancement_upside_score(roi, 0.50, cfg)
        assert upside == pytest.approx(0.40)

    def test_upside_all_deficiencies(self):
        """ROI with all 4 deficiencies, ocr_score=0.50 -> upside == 1.0."""
        cfg = make_cfg()
        roi = make_roi_rq(
            low_contrast=True,
            exposure_bad=True,
            mildly_soft=True,
            noisy=True,
            enhance_eligible=True,
        )
        upside = compute_enhancement_upside_score(roi, 0.50, cfg)
        assert upside == pytest.approx(1.0)


# ===========================================================================
# TestEnhancementSelection (3 tests)
# ===========================================================================


class TestEnhancementSelection:
    """Tests for select_enhancement_duplicates."""

    def test_enhance_max_duplicates(self):
        """8 base ROIs, all enhance_eligible with positive upside, max=4 -> 4."""
        cfg = make_cfg(max_enhance_duplicates=4)
        base = [
            make_roi_rq(
                enhance_eligible=True,
                low_contrast=True,
                tenengrad=2000 + i * 100,
            )
            for i in range(8)
        ]
        # All have low_contrast -> upside > 0, and ocr_score below excellence
        ocr_scores = {id(r): 0.50 + i * 0.01 for i, r in enumerate(base)}

        result = select_enhancement_duplicates(base, ocr_scores, cfg)
        assert len(result) == 4

    def test_enhance_empty_when_none_eligible(self):
        """4 base ROIs, all enhance_eligible=False -> returns []."""
        cfg = make_cfg()
        base = [make_roi_rq(enhance_eligible=False, low_contrast=True) for _ in range(4)]
        ocr_scores = {id(r): 0.50 for r in base}

        result = select_enhancement_duplicates(base, ocr_scores, cfg)
        assert result == []

    def test_enhance_only_eligible_positive(self):
        """6 base: 3 eligible (2 with upside, 1 ocr>0.85), 3 ineligible -> 2."""
        cfg = make_cfg()
        # 2 eligible with deficiency (positive upside)
        r1 = make_roi_rq(enhance_eligible=True, low_contrast=True)
        r2 = make_roi_rq(enhance_eligible=True, exposure_bad=True)
        # 1 eligible but excellent OCR (upside = 0)
        r3 = make_roi_rq(enhance_eligible=True)
        # 3 ineligible with deficiency
        r4 = make_roi_rq(enhance_eligible=False, low_contrast=True)
        r5 = make_roi_rq(enhance_eligible=False, exposure_bad=True)
        r6 = make_roi_rq(enhance_eligible=False, noisy=True)

        base = [r1, r2, r3, r4, r5, r6]
        ocr_scores = {
            id(r1): 0.50,
            id(r2): 0.60,
            id(r3): 0.90,  # above excellence threshold
            id(r4): 0.50,
            id(r5): 0.50,
            id(r6): 0.50,
        }

        result = select_enhancement_duplicates(base, ocr_scores, cfg)
        assert len(result) == 2
        # All returned must be enhance_eligible with positive upside
        for roi in result:
            assert roi.metrics.enhance_eligible is True
            assert compute_enhancement_upside_score(roi, ocr_scores[id(roi)], cfg) > 0


# ===========================================================================
# TestChunk03ConfigValidation (2 tests)
# ===========================================================================


class TestChunk03ConfigValidation:
    """Tests for ConsumerConfig validation of chunk-03 enhancement fields."""

    def test_config_rejects_negative_max_duplicates(self):
        """max_enhance_duplicates=-1 -> raises ValueError."""
        with pytest.raises(ValueError):
            make_cfg(max_enhance_duplicates=-1)

    def test_config_rejects_excellence_threshold_out_of_range(self):
        """enhance_excellence_threshold=1.5 -> raises ValueError."""
        with pytest.raises(ValueError):
            make_cfg(enhance_excellence_threshold=1.5)


# ===========================================================================
# Golden: Enhancement Selection (1 test)
# ===========================================================================


def test_golden_enhancement_selection():
    """Golden: 8 base, 2 excellent, 3 fixable, 1 blurry, 2 ineligible.

    max=4 -> Returns 3 fixable. Sorted by upside descending.
    """
    cfg = make_cfg(max_enhance_duplicates=4)

    # 2 excellent (ocr > 0.85) — upside suppressed
    excellent_1 = make_roi_rq(tenengrad=5000, enhance_eligible=True, low_contrast=True)
    excellent_2 = make_roi_rq(tenengrad=4500, enhance_eligible=True, exposure_bad=True)

    # 3 fixable — positive upside
    # fixable_1: low_contrast + exposure_bad = 0.40 + 0.25 = 0.65
    fixable_1 = make_roi_rq(
        tenengrad=2000, enhance_eligible=True, low_contrast=True, exposure_bad=True
    )
    # fixable_2: low_contrast + mildly_soft = 0.40 + 0.20 = 0.60
    fixable_2 = make_roi_rq(
        tenengrad=2000, enhance_eligible=True, low_contrast=True, mildly_soft=True
    )
    # fixable_3: noisy only = 0.15
    fixable_3 = make_roi_rq(tenengrad=2000, enhance_eligible=True, noisy=True)

    # 1 very_blurry — upside suppressed (guardrail)
    blurry = make_roi_rq(tenengrad=300, enhance_eligible=True, very_blurry=True, low_contrast=True)

    # 2 not eligible
    ineligible_1 = make_roi_rq(enhance_eligible=False, low_contrast=True, exposure_bad=True)
    ineligible_2 = make_roi_rq(enhance_eligible=False, noisy=True, mildly_soft=True)

    base = [
        excellent_1,
        excellent_2,
        fixable_1,
        fixable_2,
        fixable_3,
        blurry,
        ineligible_1,
        ineligible_2,
    ]
    ocr_scores = {
        id(excellent_1): 0.92,
        id(excellent_2): 0.88,
        id(fixable_1): 0.55,
        id(fixable_2): 0.50,
        id(fixable_3): 0.45,
        id(blurry): 0.30,
        id(ineligible_1): 0.50,
        id(ineligible_2): 0.50,
    }

    result = select_enhancement_duplicates(base, ocr_scores, cfg)

    # Only the 3 fixable should be selected
    assert len(result) == 3

    result_ids = {id(r) for r in result}
    assert id(fixable_1) in result_ids
    assert id(fixable_2) in result_ids
    assert id(fixable_3) in result_ids

    # Sorted by upside descending: fixable_1 (0.65) > fixable_2 (0.60) > fixable_3 (0.15)
    assert result[0] is fixable_1
    assert result[1] is fixable_2
    assert result[2] is fixable_3


# ===========================================================================
# Chunk-04: Output Packaging with Duplicate Groups
# ===========================================================================


# ===========================================================================
# TestPackaging (4 tests)
# ===========================================================================


class TestPackaging:
    """Tests for package_batch_output."""

    def test_package_indices_valid(self):
        """4 base + 2 enhanced -> for each group: base_idx < 4 and enh_idx >= 4."""
        base = [make_roi_rq(tenengrad=2000 + i * 100) for i in range(4)]
        # Enhanced are same identity as base[1] and base[3]
        enhance = [base[1], base[3]]

        result = package_batch_output(
            track_id="T-test", version=1, base_rois=base, enhance_rois=enhance
        )

        assert isinstance(result, EnhancedBatchSelection)
        assert len(result.base_rois) == 4
        assert len(result.enhance_rois) == 2
        for _group_id, indices in result.duplicate_groups.items():
            assert len(indices) == 2
            base_idx, enh_idx = indices[0], indices[1]
            assert base_idx < 4, f"base_idx {base_idx} should be < 4"
            assert enh_idx >= 4, f"enh_idx {enh_idx} should be >= 4"

    def test_package_empty_enhance(self):
        """3 base + 0 enhanced -> enhance_rois==[], duplicate_groups=={}."""
        base = [make_roi_rq() for _ in range(3)]

        result = package_batch_output(track_id="T-test", version=1, base_rois=base, enhance_rois=[])

        assert result.enhance_rois == []
        assert result.duplicate_groups == {}
        assert len(result.base_rois) == 3

    def test_package_group_count_matches(self):
        """N base + M enhanced (M=3) -> len(duplicate_groups) == 3."""
        base = [make_roi_rq(tenengrad=2000 + i * 100) for i in range(5)]
        enhance = [base[0], base[2], base[4]]

        result = package_batch_output(
            track_id="T-test", version=1, base_rois=base, enhance_rois=enhance
        )

        assert len(result.duplicate_groups) == 3

    def test_package_base_first_ordering(self):
        """4 base + 2 enhanced -> for each group [base_idx, enh_idx]: base_idx < enh_idx."""
        base = [make_roi_rq(tenengrad=2000 + i * 100) for i in range(4)]
        enhance = [base[1], base[3]]

        result = package_batch_output(
            track_id="T-test", version=1, base_rois=base, enhance_rois=enhance
        )

        for group_id, indices in result.duplicate_groups.items():
            base_idx, enh_idx = indices[0], indices[1]
            assert base_idx < enh_idx, (
                f"In {group_id}: base_idx={base_idx} should be < enh_idx={enh_idx}"
            )

    def test_package_orphan_enhance_roi_warns(self, caplog):
        """3 base + 1 orphan enhance (not in base) -> 0 groups, warning logged."""
        import logging

        base = [make_roi_rq(tenengrad=2000 + i * 100) for i in range(3)]
        orphan = make_roi_rq(tenengrad=9999)  # not in base_rois

        with caplog.at_level(logging.WARNING, logger="consumer.ops_batch_select"):
            result = package_batch_output(
                track_id="T-test", version=1, base_rois=base, enhance_rois=[orphan]
            )

        assert result.duplicate_groups == {}
        assert len(result.enhance_rois) == 1
        assert "no matching base ROI" in caplog.text


# ===========================================================================
# Golden: Packaging (1 test)
# ===========================================================================


def test_golden_packaging():
    """Golden: 5 base [B0..B4], 2 enhanced [B1, B3] (same identity).

    base_rois=5, enhance_rois=2, duplicate_groups: {"group-0": [1, 5], "group-1": [3, 6]}
    """
    base = [make_roi_rq(tenengrad=2000 + i * 100) for i in range(5)]
    # Enhanced are the SAME objects as base[1] and base[3]
    enhance = [base[1], base[3]]

    result = package_batch_output(track_id="T-001", version=3, base_rois=base, enhance_rois=enhance)

    assert result.track_id == "T-001"
    assert result.version == 3
    assert len(result.base_rois) == 5
    assert len(result.enhance_rois) == 2
    assert result.enhance_rois[0] is base[1]
    assert result.enhance_rois[1] is base[3]

    # Exactly 2 groups
    assert len(result.duplicate_groups) == 2

    # group-0: base[1] at index 1, enhance[0] at index 5 (len(base) + 0)
    assert result.duplicate_groups["group-0"] == [1, 5]
    # group-1: base[3] at index 3, enhance[1] at index 6 (len(base) + 1)
    assert result.duplicate_groups["group-1"] == [3, 6]


# ===========================================================================
# Chunk-05: Selection Logging
# ===========================================================================


# ===========================================================================
# TestLogging (4 tests)
# ===========================================================================


class TestLogging:
    """Tests for log_batch_selection_metrics."""

    def test_log_funnel_enabled(self, caplog):
        """Logging ON: funnel line contains total, eligible, base, enhanced counts."""
        import logging

        cfg = make_cfg(enable_batch_selection_logging=True)

        with caplog.at_level(logging.INFO, logger="consumer.ops_batch_select"):
            log_batch_selection_metrics(
                total_count=32,
                eligible_count=12,
                base_rois=[make_roi_rq() for _ in range(8)],
                enhance_rois=[make_roi_rq() for _ in range(3)],
                diversity_distances=[0.20, 0.30],
                enhancement_upsides={},
                track_id="T-test",
                version=1,
                cfg=cfg,
            )

        assert "funnel:" in caplog.text
        assert "32 total" in caplog.text
        assert "12 eligible" in caplog.text
        assert "8 base" in caplog.text
        assert "3 enhanced" in caplog.text

    def test_log_disabled_silent(self, caplog):
        """Logging OFF: no records from batch select logger."""
        import logging

        cfg = make_cfg(enable_batch_selection_logging=False)

        with caplog.at_level(logging.DEBUG, logger="consumer.ops_batch_select"):
            log_batch_selection_metrics(
                total_count=10,
                eligible_count=5,
                base_rois=[make_roi_rq() for _ in range(3)],
                enhance_rois=[],
                diversity_distances=[0.20],
                enhancement_upsides={},
                track_id="T-test",
                version=1,
                cfg=cfg,
            )

        batch_select_records = [r for r in caplog.records if r.name == "consumer.ops_batch_select"]
        assert len(batch_select_records) == 0

    def test_log_diversity_stats(self, caplog):
        """Diversity distances [0.20, 0.35, 0.50] -> min/mean/max logged."""
        import logging

        cfg = make_cfg(enable_batch_selection_logging=True)

        with caplog.at_level(logging.INFO, logger="consumer.ops_batch_select"):
            log_batch_selection_metrics(
                total_count=10,
                eligible_count=5,
                base_rois=[make_roi_rq() for _ in range(4)],
                enhance_rois=[],
                diversity_distances=[0.20, 0.35, 0.50],
                enhancement_upsides={},
                track_id="T-test",
                version=1,
                cfg=cfg,
            )

        assert "diversity:" in caplog.text
        assert "min=0.200" in caplog.text
        assert "mean=0.350" in caplog.text
        assert "max=0.500" in caplog.text

    def test_log_enhancement_reasons(self, caplog):
        """2 enhanced duplicates with known upside scores -> scores in caplog."""
        import logging

        cfg = make_cfg(enable_batch_selection_logging=True)

        enh_a = make_roi_rq(enhance_eligible=True, low_contrast=True)
        enh_b = make_roi_rq(enhance_eligible=True, exposure_bad=True)

        upsides = {id(enh_a): 0.65, id(enh_b): 0.25}

        with caplog.at_level(logging.INFO, logger="consumer.ops_batch_select"):
            log_batch_selection_metrics(
                total_count=10,
                eligible_count=5,
                base_rois=[make_roi_rq() for _ in range(4)],
                enhance_rois=[enh_a, enh_b],
                diversity_distances=[0.20],
                enhancement_upsides=upsides,
                track_id="T-test",
                version=1,
                cfg=cfg,
            )

        assert "enhancement:" in caplog.text
        assert "0.65" in caplog.text
        assert "0.25" in caplog.text


# ===========================================================================
# Golden: Logging (1 test)
# ===========================================================================


def test_golden_logging_full(caplog):
    """Golden: full logging with track/version, funnel, diversity, enhancement.

    track=T-001, version=3, total=20, eligible=10, base=6, enhanced=2,
    diversity_distances=[0.18, 0.22, 0.30, 0.40]
    -> caplog at INFO: funnel line, diversity min/mean/max, enhancement entries.
    """
    import logging

    cfg = make_cfg(enable_batch_selection_logging=True)

    base = [make_roi_rq() for _ in range(6)]
    enh_a = make_roi_rq(enhance_eligible=True, low_contrast=True)
    enh_b = make_roi_rq(enhance_eligible=True, exposure_bad=True)
    upsides = {id(enh_a): 0.40, id(enh_b): 0.25}

    with caplog.at_level(logging.INFO, logger="consumer.ops_batch_select"):
        log_batch_selection_metrics(
            total_count=20,
            eligible_count=10,
            base_rois=base,
            enhance_rois=[enh_a, enh_b],
            diversity_distances=[0.18, 0.22, 0.30, 0.40],
            enhancement_upsides=upsides,
            track_id="T-001",
            version=3,
            cfg=cfg,
        )

    # Funnel line
    assert "funnel:" in caplog.text
    assert "20 total" in caplog.text
    assert "10 eligible" in caplog.text
    assert "6 base" in caplog.text
    assert "2 enhanced" in caplog.text

    # Diversity stats: min=0.180, mean=0.275, max=0.400
    assert "diversity:" in caplog.text
    assert "min=0.180" in caplog.text
    assert "mean=0.275" in caplog.text
    assert "max=0.400" in caplog.text

    # Enhancement entries
    assert "enhancement:" in caplog.text
    assert "0.40" in caplog.text
    assert "0.25" in caplog.text

    # All records are INFO level
    batch_records = [r for r in caplog.records if r.name == "consumer.ops_batch_select"]
    for r in batch_records:
        assert r.levelno >= logging.INFO


# ===========================================================================
# Chunk-06: Entry Point, Exports, and Hardening
# ===========================================================================


# ===========================================================================
# TestE2E (8 tests)
# ===========================================================================


class TestE2E:
    """End-to-end tests for select_batch_with_enhancement."""

    def test_e2e_high_quality_bin(self):
        """32 candidates, 12 eligible (diverse poses, varied quality) -> 8 base, enhance > 0."""
        cfg = make_cfg()
        candidates: list[RoiRichQuality] = []

        # 8 best-quality eligible at well-spread positions
        assert_positions_well_spread(WELL_SPREAD_POSITIONS_8, cfg)
        for i, (skew, bbox_x, height, persp) in enumerate(WELL_SPREAD_POSITIONS_8):
            # First 4 are enhance-eligible with deficiencies
            enhance = i < 4
            kw: dict = {}
            if i == 0:
                kw["low_contrast"] = True
            elif i == 1:
                kw["exposure_bad"] = True
            elif i == 2:
                kw["mildly_soft"] = True
            elif i == 3:
                kw["noisy"] = True

            candidates.append(
                make_roi_rq(
                    top8_eligible=True,
                    plate_height_px=height,
                    skew_degrees=skew,
                    bbox_center_x_normalized=bbox_x,
                    perspective_direction=persp,
                    tenengrad=4000.0 - i * 100,
                    enhance_eligible=enhance,
                    **kw,
                )
            )

        # 4 more eligible with lower quality (won't make batch — already full at 8)
        for i in range(4):
            candidates.append(
                make_roi_rq(
                    top8_eligible=True,
                    plate_height_px=70,
                    tenengrad=1500.0 - i * 100,
                    skew_degrees=-10 + i * 5,
                    bbox_center_x_normalized=0.45 + i * 0.03,
                )
            )

        # 20 ineligible
        for _ in range(20):
            candidates.append(make_roi_rq(top8_eligible=False, plate_height_px=80))

        result = select_batch_with_enhancement("T-test", 1, candidates, cfg)

        assert isinstance(result, EnhancedBatchSelection)
        assert len(result.base_rois) == 8
        assert len(result.enhance_rois) > 0
        assert len(result.duplicate_groups) > 0

    def test_e2e_low_quality_bin(self):
        """32 candidates, 3 eligible -> len(base_rois) == 3 (not padded to 8)."""
        cfg = make_cfg()
        candidates: list[RoiRichQuality] = []

        # 3 eligible with distinct poses
        candidates.append(
            make_roi_rq(
                top8_eligible=True,
                plate_height_px=60,
                tenengrad=2000,
                skew_degrees=-30,
                bbox_center_x_normalized=0.2,
            )
        )
        candidates.append(
            make_roi_rq(
                top8_eligible=True,
                plate_height_px=50,
                tenengrad=1500,
                skew_degrees=0,
                bbox_center_x_normalized=0.5,
            )
        )
        candidates.append(
            make_roi_rq(
                top8_eligible=True,
                plate_height_px=70,
                tenengrad=1800,
                skew_degrees=30,
                bbox_center_x_normalized=0.8,
            )
        )

        # 29 ineligible
        for _ in range(29):
            candidates.append(make_roi_rq(top8_eligible=False))

        result = select_batch_with_enhancement("T-test", 1, candidates, cfg)

        assert len(result.base_rois) == 3

    def test_e2e_homogeneous_bin(self):
        """32 candidates, 20 eligible, all similar poses -> base_rois < 8."""
        cfg = make_cfg()
        candidates: list[RoiRichQuality] = []

        # 20 eligible with nearly identical poses
        for i in range(20):
            candidates.append(
                make_roi_rq(
                    top8_eligible=True,
                    plate_height_px=80,
                    tenengrad=3000.0 - i * 50,
                    skew_degrees=0.1 * i,
                    bbox_center_x_normalized=0.50 + 0.005 * i,
                    perspective_direction=0.005 * i,
                )
            )

        # 12 ineligible
        for _ in range(12):
            candidates.append(make_roi_rq(top8_eligible=False))

        result = select_batch_with_enhancement("T-test", 1, candidates, cfg)

        assert len(result.base_rois) < 8

    def test_e2e_hopeless_bin(self):
        """32 candidates, 0 eligible -> empty batch."""
        cfg = make_cfg()
        candidates: list[RoiRichQuality] = []

        # 16 with top8=False
        for _ in range(16):
            candidates.append(make_roi_rq(top8_eligible=False, plate_height_px=80))
        # 16 with height < 40
        for _ in range(16):
            candidates.append(make_roi_rq(top8_eligible=True, plate_height_px=30))

        result = select_batch_with_enhancement("T-test", 1, candidates, cfg)

        assert result.base_rois == []
        assert result.enhance_rois == []
        assert result.duplicate_groups == {}

    def test_e2e_all_none_keypoints(self):
        """10 eligible, all keypoints=None, varied bbox_center_x and skew -> no crash."""
        cfg = make_cfg()
        candidates: list[RoiRichQuality] = []

        for i in range(10):
            candidates.append(
                make_roi_rq(
                    top8_eligible=True,
                    keypoints=None,
                    skew_degrees=-45 + i * 10,
                    bbox_center_x_normalized=0.05 + i * 0.1,
                    plate_height_px=50 + i * 10,
                    tenengrad=3000.0 - i * 100,
                )
            )

        result = select_batch_with_enhancement("T-test", 1, candidates, cfg)

        assert len(result.base_rois) > 0
        assert isinstance(result, EnhancedBatchSelection)

    def test_e2e_single_candidate(self):
        """1 eligible candidate -> len(base_rois) == 1."""
        cfg = make_cfg()
        candidates = [make_roi_rq(top8_eligible=True, plate_height_px=80, tenengrad=3000)]

        result = select_batch_with_enhancement("T-test", 1, candidates, cfg)

        assert len(result.base_rois) == 1

    def test_e2e_exactly_8_all_novel(self):
        """8 eligible, all maximally different poses -> len(base_rois) == 8."""
        cfg = make_cfg()
        assert_positions_well_spread(WELL_SPREAD_POSITIONS_8, cfg)
        candidates = [
            make_roi_rq(
                top8_eligible=True,
                skew_degrees=s,
                bbox_center_x_normalized=b,
                plate_height_px=h,
                perspective_direction=p,
                tenengrad=3000.0 - i * 100,
            )
            for i, (s, b, h, p) in enumerate(WELL_SPREAD_POSITIONS_8)
        ]

        result = select_batch_with_enhancement("T-test", 1, candidates, cfg)

        assert len(result.base_rois) == 8

    def test_e2e_zero_enhancement_qualifying(self):
        """8 eligible, all excellent (no deficiencies) -> enhance_rois == []."""
        cfg = make_cfg()
        assert_positions_well_spread(WELL_SPREAD_POSITIONS_8, cfg)
        candidates = [
            make_roi_rq(
                top8_eligible=True,
                enhance_eligible=True,
                skew_degrees=s,
                bbox_center_x_normalized=b,
                plate_height_px=h,
                perspective_direction=p,
                tenengrad=5000,
                global_contrast=80,
                luminance_mean=128,
                noise_std=2.0,
                detection_confidence=0.95,
            )
            for s, b, h, p in WELL_SPREAD_POSITIONS_8
        ]

        result = select_batch_with_enhancement("T-test", 1, candidates, cfg)

        assert result.enhance_rois == []


# ===========================================================================
# TestExportsAndCleanup (2 tests)
# ===========================================================================


class TestExportsAndCleanup:
    """Tests for exports and type cleanup."""

    def test_import_from_consumer(self):
        """from consumer import select_batch_with_enhancement, EnhancedBatchSelection works."""
        from consumer import EnhancedBatchSelection as EBS, select_batch_with_enhancement as sbwe

        assert callable(sbwe)
        assert EBS is not None

    def test_old_batch_selection_removed(self):
        """from consumer.models import BatchSelection -> raises ImportError."""
        with pytest.raises(ImportError):
            from consumer.models import BatchSelection  # type: ignore[attr-defined]  # noqa: F401


# ===========================================================================
# TestE2EInvariants (2 tests)
# ===========================================================================


class TestE2EInvariants:
    """Cross-cutting invariant tests for select_batch_with_enhancement."""

    def test_e2e_base_rois_all_eligible(self):
        """Every ROI in base_rois has top8_eligible==True and plate_height_px >= 40."""
        cfg = make_cfg()
        candidates: list[RoiRichQuality] = []
        for i in range(12):
            candidates.append(
                make_roi_rq(
                    top8_eligible=True,
                    plate_height_px=50 + i * 10,
                    tenengrad=3000.0 - i * 100,
                    skew_degrees=-55 + i * 10,
                    bbox_center_x_normalized=0.05 + i * (0.9 / 11),
                )
            )
        for _ in range(20):
            candidates.append(make_roi_rq(top8_eligible=False))

        result = select_batch_with_enhancement("T-test", 1, candidates, cfg)

        for roi in result.base_rois:
            assert roi.metrics.top8_eligible is True
            assert roi.metrics.plate_height_px >= 40

    def test_e2e_enhance_rois_all_eligible(self):
        """Every ROI in enhance_rois has enhance_eligible==True."""
        cfg = make_cfg()
        candidates: list[RoiRichQuality] = []
        for i in range(12):
            candidates.append(
                make_roi_rq(
                    top8_eligible=True,
                    enhance_eligible=i < 6,
                    low_contrast=i < 6,
                    plate_height_px=50 + i * 10,
                    tenengrad=3000.0 - i * 100,
                    skew_degrees=-55 + i * 10,
                    bbox_center_x_normalized=0.05 + i * (0.9 / 11),
                )
            )
        for _ in range(20):
            candidates.append(make_roi_rq(top8_eligible=False))

        result = select_batch_with_enhancement("T-test", 1, candidates, cfg)

        for roi in result.enhance_rois:
            assert roi.metrics.enhance_eligible is True


# ===========================================================================
# Chunk-07: Pass B Holistic Integration Tests
# ===========================================================================


# ===========================================================================
# TestPassBIntegration (3 tests)
# ===========================================================================


class TestPassBIntegration:
    """Cross-chunk interaction tests exercising the full pipeline."""

    def test_full_pipeline_diverse_bin(self):
        """20 candidates (10 eligible diverse, 10 ineligible) -> 8 base, 1-4 enhance, valid groups."""
        cfg = make_cfg()
        assert_positions_well_spread(WELL_SPREAD_POSITIONS_8, cfg)

        candidates: list[RoiRichQuality] = []
        deficiencies = [
            {"low_contrast": True},
            {"exposure_bad": True},
            {"mildly_soft": True},
            {"noisy": True},
        ]
        for i, (skew, bbox_x, height, persp) in enumerate(WELL_SPREAD_POSITIONS_8):
            kw: dict = {}
            if i < 4:
                kw.update(deficiencies[i])
                kw["enhance_eligible"] = True
            candidates.append(
                make_roi_rq(
                    top8_eligible=True,
                    plate_height_px=height,
                    skew_degrees=skew,
                    bbox_center_x_normalized=bbox_x,
                    perspective_direction=persp,
                    tenengrad=4000.0 - i * 100,
                    **kw,
                )
            )

        # 2 more eligible at novel positions
        candidates.append(
            make_roi_rq(
                top8_eligible=True,
                plate_height_px=70,
                skew_degrees=-70,
                bbox_center_x_normalized=0.05,
                perspective_direction=-0.5,
                tenengrad=2000,
            )
        )
        candidates.append(
            make_roi_rq(
                top8_eligible=True,
                plate_height_px=130,
                skew_degrees=70,
                bbox_center_x_normalized=0.95,
                perspective_direction=0.5,
                tenengrad=1800,
            )
        )

        # 10 ineligible
        for _ in range(10):
            candidates.append(make_roi_rq(top8_eligible=False, plate_height_px=80))

        # Act
        result = select_batch_with_enhancement("T-pass-b", 1, candidates, cfg)

        # Assert
        assert isinstance(result, EnhancedBatchSelection)
        assert result.track_id == "T-pass-b"
        assert result.version == 1
        assert len(result.base_rois) == 8
        assert 1 <= len(result.enhance_rois) <= 4
        assert len(result.duplicate_groups) == len(result.enhance_rois)

        # All base_rois are eligible
        for roi in result.base_rois:
            assert roi.metrics.top8_eligible is True
            assert roi.metrics.plate_height_px >= 40

        # All enhance_rois are enhance_eligible
        for roi in result.enhance_rois:
            assert roi.metrics.enhance_eligible is True

        # Each group has valid [base_idx, enhance_idx]
        n_base = len(result.base_rois)
        for _group_id, indices in result.duplicate_groups.items():
            assert len(indices) == 2
            assert 0 <= indices[0] < n_base
            assert indices[1] >= n_base

    def test_scoring_diversity_interaction(self):
        """Lower-score diverse candidates beat higher-score cluster copies in batch selection.

        5 high-score cluster (skew 0-2, bbox 0.50-0.54) + 5 lower-score diverse.
        At most 2 cluster members in base (anchors); at least 4 diverse selected.
        """
        cfg = make_cfg()

        # 5 high-score cluster: very similar poses, high tenengrad
        cluster = []
        for i in range(5):
            cluster.append(
                make_roi_rq(
                    top8_eligible=True,
                    tenengrad=5000.0 - i * 50,
                    plate_height_px=100,
                    skew_degrees=i * 0.5,
                    bbox_center_x_normalized=0.50 + i * 0.01,
                    perspective_direction=0.0,
                )
            )

        # 5 lower-score diverse: well-spread poses
        diverse = [
            make_roi_rq(
                top8_eligible=True,
                tenengrad=2500,
                plate_height_px=60,
                skew_degrees=-45,
                bbox_center_x_normalized=0.05,
                perspective_direction=-0.8,
            ),
            make_roi_rq(
                top8_eligible=True,
                tenengrad=2400,
                plate_height_px=140,
                skew_degrees=50,
                bbox_center_x_normalized=0.95,
                perspective_direction=0.8,
            ),
            make_roi_rq(
                top8_eligible=True,
                tenengrad=2300,
                plate_height_px=50,
                skew_degrees=-30,
                bbox_center_x_normalized=0.20,
                perspective_direction=-0.5,
            ),
            make_roi_rq(
                top8_eligible=True,
                tenengrad=2200,
                plate_height_px=120,
                skew_degrees=35,
                bbox_center_x_normalized=0.80,
                perspective_direction=0.5,
            ),
            make_roi_rq(
                top8_eligible=True,
                tenengrad=2100,
                plate_height_px=70,
                skew_degrees=15,
                bbox_center_x_normalized=0.35,
                perspective_direction=0.2,
            ),
        ]

        candidates = cluster + diverse

        # Act
        result = select_batch_with_enhancement("T-diversity", 1, candidates, cfg)

        # Assert: count cluster members vs diverse in base_rois
        cluster_ids = {id(r) for r in cluster}
        diverse_ids = {id(r) for r in diverse}
        cluster_in_base = sum(1 for r in result.base_rois if id(r) in cluster_ids)
        diverse_in_base = sum(1 for r in result.base_rois if id(r) in diverse_ids)

        assert cluster_in_base <= 2, (
            f"Expected at most 2 cluster in base (anchors), got {cluster_in_base}"
        )
        assert diverse_in_base >= 4, f"Expected at least 4 diverse in base, got {diverse_in_base}"

    def test_scoring_enhancement_interaction(self):
        """Enhancement targets deficiency not quality.

        8 eligible all enhance_eligible: 4 excellent (ten=5000) + 4 deficient.
        Enhancement only from deficient set, zero from excellent.
        """
        cfg = make_cfg()

        assert_positions_well_spread(WELL_SPREAD_POSITIONS_8, cfg)

        # 4 excellent: high sharpness + large height, good metrics, no deficiency flags
        # Use positions[4:] (heights 110-155) so per-bin normalization produces scores > 0.85
        excellent = [
            make_roi_rq(
                top8_eligible=True,
                enhance_eligible=True,
                tenengrad=5000,
                plate_height_px=h,
                skew_degrees=s,
                bbox_center_x_normalized=b,
                perspective_direction=p,
                global_contrast=80,
                luminance_mean=128,
                noise_std=2.0,
                detection_confidence=0.95,
            )
            for s, b, h, p in WELL_SPREAD_POSITIONS_8[4:]
        ]

        # 4 deficient: lower sharpness + smaller height, each with one deficiency flag
        # Use positions[:4] (heights 50-95)
        s0, b0, h0, p0 = WELL_SPREAD_POSITIONS_8[0]
        s1, b1, h1, p1 = WELL_SPREAD_POSITIONS_8[1]
        s2, b2, h2, p2 = WELL_SPREAD_POSITIONS_8[2]
        s3, b3, h3, p3 = WELL_SPREAD_POSITIONS_8[3]
        deficient = [
            make_roi_rq(
                top8_eligible=True,
                enhance_eligible=True,
                tenengrad=2000,
                plate_height_px=h0,
                skew_degrees=s0,
                bbox_center_x_normalized=b0,
                perspective_direction=p0,
                low_contrast=True,
            ),
            make_roi_rq(
                top8_eligible=True,
                enhance_eligible=True,
                tenengrad=2000,
                plate_height_px=h1,
                skew_degrees=s1,
                bbox_center_x_normalized=b1,
                perspective_direction=p1,
                exposure_bad=True,
            ),
            make_roi_rq(
                top8_eligible=True,
                enhance_eligible=True,
                tenengrad=2000,
                plate_height_px=h2,
                skew_degrees=s2,
                bbox_center_x_normalized=b2,
                perspective_direction=p2,
                mildly_soft=True,
            ),
            make_roi_rq(
                top8_eligible=True,
                enhance_eligible=True,
                tenengrad=2000,
                plate_height_px=h3,
                skew_degrees=s3,
                bbox_center_x_normalized=b3,
                perspective_direction=p3,
                noisy=True,
            ),
        ]

        candidates = excellent + deficient

        # Precondition (audit-05): verify excellent ROIs actually score above threshold
        eligible = candidates  # all are top8_eligible
        ocr_scores = {id(r): compute_ocr_likelihood_score(r, eligible, cfg) for r in eligible}
        for roi in excellent:
            assert ocr_scores[id(roi)] > cfg.enhance_excellence_threshold, (
                f"Precondition failed: 'excellent' ROI scores {ocr_scores[id(roi)]:.3f} "
                f"<= threshold {cfg.enhance_excellence_threshold}"
            )

        # Act
        result = select_batch_with_enhancement("T-enhance", 1, candidates, cfg)

        # Assert: all 8 in base
        assert len(result.base_rois) == 8

        # Enhancement only from deficient set
        excellent_ids = {id(r) for r in excellent}
        deficient_ids = {id(r) for r in deficient}
        for roi in result.enhance_rois:
            assert id(roi) not in excellent_ids, "Excellent ROI should not be enhanced"
            assert id(roi) in deficient_ids, "Only deficient ROIs should be enhanced"

        assert len(result.enhance_rois) > 0, "Should have at least 1 enhancement"


# ===========================================================================
# TestPassBErrorRecovery (2 tests)
# ===========================================================================


class TestPassBErrorRecovery:
    """Error recovery tests for select_batch_with_enhancement."""

    def test_empty_candidate_list(self):
        """Literal [] input -> empty EnhancedBatchSelection."""
        cfg = make_cfg()

        # Act
        result = select_batch_with_enhancement("T-empty", 1, [], cfg)

        # Assert
        assert isinstance(result, EnhancedBatchSelection)
        assert result.track_id == "T-empty"
        assert result.version == 1
        assert result.base_rois == []
        assert result.enhance_rois == []
        assert result.duplicate_groups == {}

    def test_single_candidate_with_enhancement(self):
        """1 eligible (enhance_eligible, low_contrast) -> 1 base + 1 enhance + 1 group [0, 1]."""
        cfg = make_cfg()
        roi = make_roi_rq(
            top8_eligible=True,
            enhance_eligible=True,
            low_contrast=True,
            plate_height_px=80,
            tenengrad=2000,
        )

        # Act
        result = select_batch_with_enhancement("T-single", 1, [roi], cfg)

        # Assert
        assert len(result.base_rois) == 1
        assert len(result.enhance_rois) == 1
        assert result.base_rois[0] is roi
        assert result.enhance_rois[0] is roi
        assert len(result.duplicate_groups) == 1
        assert result.duplicate_groups["group-0"] == [0, 1]


# ===========================================================================
# TestPassBEdgeCases (4 tests)
# ===========================================================================


class TestPassBEdgeCases:
    """Edge case tests for select_batch_with_enhancement."""

    def test_mixed_keypoints_in_batch(self):
        """6 eligible: 2 with keypoints + 4 without, well-spread -> all 6 selected.

        Verifies that pose distance correctly uses keypoint path for kp-kp pairs
        and bbox_center_x fallback for kp-None and None-None pairs.
        Uses MIXED_KEYPOINT_POSITIONS_6 with bbox_center_x spaced >= 0.15 apart.
        """
        cfg = make_cfg()

        # Guard: verify all 6 positions are pairwise well-spread under bbox fallback
        assert_positions_well_spread(MIXED_KEYPOINT_POSITIONS_6, cfg)

        kp_a = [(190.0, 5.0), (10.0, 5.0), (190.0, 45.0), (10.0, 45.0)]
        kp_b = [(190.0, 10.0), (10.0, 10.0), (190.0, 85.0), (10.0, 85.0)]

        candidates = []
        for i, (skew, bbox_x, height, persp) in enumerate(MIXED_KEYPOINT_POSITIONS_6):
            kp = kp_a if i == 0 else (kp_b if i == 1 else None)
            candidates.append(
                make_roi_rq(
                    top8_eligible=True,
                    keypoints=kp,
                    plate_width_px=200,
                    plate_height_px=height,
                    skew_degrees=skew,
                    bbox_center_x_normalized=bbox_x,
                    perspective_direction=persp,
                    tenengrad=4000.0 - i * 200,
                )
            )

        # Act
        result = select_batch_with_enhancement("T-mixed-kp", 1, candidates, cfg)

        # Assert: all 6 selected (exact count, not weakened >= 4)
        assert isinstance(result, EnhancedBatchSelection)
        assert len(result.base_rois) == 6

    def test_boundary_diversity_distance(self):
        """skew=27 (dist=0.150) included, skew=26 (dist=0.144) rejected.

        2 anchors at skew=0 and skew=-45, same bbox/height/persp.
        Diversity only varies by skew from anchor1 (skew=0).
        distance = 0.50 * abs(delta_skew) / 90 (other components zero).

        Note: 27/90 = 0.3 exactly in IEEE 754 float64, so 0.50 * 0.3 = 0.15
        exactly. Similarly 26/90 = 0.28888... is not exact but 0.50 * (26/90)
        = 0.14444... < 0.15 regardless. These values were chosen for exact
        float representation at the boundary.
        """
        cfg = make_cfg(diversity_min_distance=0.15)

        # Anchor 1: highest OCR score
        anchor1 = make_roi_rq(
            top8_eligible=True,
            tenengrad=5000,
            plate_height_px=80,
            skew_degrees=0,
            bbox_center_x_normalized=0.5,
            perspective_direction=0.0,
        )
        # Anchor 2: second highest, far enough from anchor1
        anchor2 = make_roi_rq(
            top8_eligible=True,
            tenengrad=4500,
            plate_height_px=80,
            skew_degrees=-45,
            bbox_center_x_normalized=0.5,
            perspective_direction=0.0,
        )
        # Just below: dist to anchor1 = 0.50 * 26/90 = 0.1444 < 0.15
        just_below = make_roi_rq(
            top8_eligible=True,
            tenengrad=3000,
            plate_height_px=80,
            skew_degrees=26,
            bbox_center_x_normalized=0.5,
            perspective_direction=0.0,
        )
        # At boundary: dist to anchor1 = 0.50 * 27/90 = 0.150 >= 0.15
        at_boundary = make_roi_rq(
            top8_eligible=True,
            tenengrad=2000,
            plate_height_px=80,
            skew_degrees=27,
            bbox_center_x_normalized=0.5,
            perspective_direction=0.0,
        )

        candidates = [anchor1, anchor2, just_below, at_boundary]

        # Act
        result = select_batch_with_enhancement("T-boundary", 1, candidates, cfg)

        # Assert: 3 base (anchor1, anchor2, at_boundary); just_below rejected
        assert len(result.base_rois) == 3
        result_ids = {id(r) for r in result.base_rois}
        assert id(anchor1) in result_ids
        assert id(anchor2) in result_ids
        assert id(at_boundary) in result_ids
        assert id(just_below) not in result_ids

    def test_enhancement_disabled_by_config(self):
        """max_enhance_duplicates=0 -> 8 base, empty enhance, empty groups."""
        cfg = make_cfg(max_enhance_duplicates=0)
        assert_positions_well_spread(WELL_SPREAD_POSITIONS_8, cfg)

        candidates = [
            make_roi_rq(
                top8_eligible=True,
                enhance_eligible=True,
                low_contrast=True,
                skew_degrees=s,
                bbox_center_x_normalized=b,
                plate_height_px=h,
                perspective_direction=p,
                tenengrad=3000.0 - i * 100,
            )
            for i, (s, b, h, p) in enumerate(WELL_SPREAD_POSITIONS_8)
        ]

        # Act
        result = select_batch_with_enhancement("T-disabled", 1, candidates, cfg)

        # Assert
        assert len(result.base_rois) == 8
        assert result.enhance_rois == []
        assert result.duplicate_groups == {}

    def test_max_output_scenario(self):
        """8 base + 4 enhance (top by upside: 1.00, 0.85, 0.65, 0.55) + 4 groups.

        8 eligible all enhance_eligible with varied deficiencies and max_enhance_duplicates=4.
        """
        cfg = make_cfg(max_enhance_duplicates=4)
        assert_positions_well_spread(WELL_SPREAD_POSITIONS_8, cfg)

        # Deficiency combos → expected upside:
        #   all 4 flags: 0.40+0.25+0.20+0.15 = 1.00
        #   contrast+exposure+sharpness: 0.40+0.25+0.20 = 0.85
        #   contrast+exposure: 0.40+0.25 = 0.65
        #   contrast+noise: 0.40+0.15 = 0.55
        #   contrast only: 0.40
        #   exposure only: 0.25
        #   sharpness only: 0.20
        #   noise only: 0.15
        flag_combos: list[dict] = [
            {"low_contrast": True, "exposure_bad": True, "mildly_soft": True, "noisy": True},
            {"low_contrast": True, "exposure_bad": True, "mildly_soft": True},
            {"low_contrast": True, "exposure_bad": True},
            {"low_contrast": True, "noisy": True},
            {"low_contrast": True},
            {"exposure_bad": True},
            {"mildly_soft": True},
            {"noisy": True},
        ]

        candidates = []
        for i, (s, b, h, p) in enumerate(WELL_SPREAD_POSITIONS_8):
            candidates.append(
                make_roi_rq(
                    top8_eligible=True,
                    enhance_eligible=True,
                    tenengrad=2000,
                    skew_degrees=s,
                    bbox_center_x_normalized=b,
                    plate_height_px=h,
                    perspective_direction=p,
                    **flag_combos[i],
                )
            )

        # Act
        result = select_batch_with_enhancement("T-max", 1, candidates, cfg)

        # Assert: 8 base + 4 enhance + 4 groups
        assert len(result.base_rois) == 8
        assert len(result.enhance_rois) == 4
        assert len(result.duplicate_groups) == 4

        # Verify top-4 by upside are selected (candidates 0-3)
        enhance_ids = {id(r) for r in result.enhance_rois}
        for i in range(4):
            assert id(candidates[i]) in enhance_ids, (
                f"Expected candidate {i} (upside rank {i + 1}) in enhance_rois"
            )


# ===========================================================================
# TestPassBPerformance (3 tests)
# ===========================================================================


class TestPassBPerformance:
    """Performance benchmarks for select_batch_with_enhancement."""

    def test_latency_32_candidates(self):
        """32 candidates (12 eligible + 20 ineligible), median < 5ms over 100 runs.

        Target hardware class: 13th gen Intel + gaming GPU.
        """
        import statistics
        import time

        cfg = make_cfg()

        # Pre-create candidates using well-spread positions
        candidates: list[RoiRichQuality] = []
        for i, (s, b, h, p) in enumerate(WELL_SPREAD_POSITIONS_8):
            candidates.append(
                make_roi_rq(
                    top8_eligible=True,
                    plate_height_px=h,
                    skew_degrees=s,
                    bbox_center_x_normalized=b,
                    perspective_direction=p,
                    tenengrad=4000.0 - i * 100,
                )
            )
        for i in range(4):
            candidates.append(
                make_roi_rq(
                    top8_eligible=True,
                    plate_height_px=70,
                    tenengrad=1500.0 - i * 100,
                    skew_degrees=-10 + i * 5,
                    bbox_center_x_normalized=0.45 + i * 0.03,
                )
            )
        for _ in range(20):
            candidates.append(make_roi_rq(top8_eligible=False))

        # Warm-up
        for _ in range(5):
            select_batch_with_enhancement("T-perf", 1, candidates, cfg)

        # Measure
        times_ms = []
        for _ in range(100):
            start = time.perf_counter()
            select_batch_with_enhancement("T-perf", 1, candidates, cfg)
            elapsed_ms = (time.perf_counter() - start) * 1000
            times_ms.append(elapsed_ms)

        median_ms = statistics.median(times_ms)
        print(f"PERF: 32-candidate median={median_ms:.2f}ms")
        assert median_ms < 5.0, f"Median latency {median_ms:.2f}ms exceeds 5ms for 32 candidates"

    def test_latency_8_candidates(self):
        """8 eligible at standard positions, median < 2ms over 100 runs.

        Target hardware class: 13th gen Intel + gaming GPU.
        """
        import statistics
        import time

        cfg = make_cfg()

        candidates = [
            make_roi_rq(
                top8_eligible=True,
                skew_degrees=s,
                bbox_center_x_normalized=b,
                plate_height_px=h,
                perspective_direction=p,
                tenengrad=3000.0 - i * 100,
            )
            for i, (s, b, h, p) in enumerate(WELL_SPREAD_POSITIONS_8)
        ]

        # Warm-up
        for _ in range(5):
            select_batch_with_enhancement("T-perf8", 1, candidates, cfg)

        # Measure
        times_ms = []
        for _ in range(100):
            start = time.perf_counter()
            select_batch_with_enhancement("T-perf8", 1, candidates, cfg)
            elapsed_ms = (time.perf_counter() - start) * 1000
            times_ms.append(elapsed_ms)

        median_ms = statistics.median(times_ms)
        print(f"PERF: 8-candidate median={median_ms:.2f}ms")
        assert median_ms < 2.0, f"Median latency {median_ms:.2f}ms exceeds 2ms for 8 candidates"

    def test_memory_under_512kb(self):
        """32 candidates (pre-created), peak memory < 512KB during pipeline."""
        import tracemalloc

        cfg = make_cfg()

        # Pre-create candidates outside measurement
        candidates: list[RoiRichQuality] = []
        for i, (s, b, h, p) in enumerate(WELL_SPREAD_POSITIONS_8):
            candidates.append(
                make_roi_rq(
                    top8_eligible=True,
                    plate_height_px=h,
                    skew_degrees=s,
                    bbox_center_x_normalized=b,
                    perspective_direction=p,
                    tenengrad=4000.0 - i * 100,
                )
            )
        for i in range(4):
            candidates.append(
                make_roi_rq(
                    top8_eligible=True,
                    plate_height_px=70,
                    tenengrad=1500.0 - i * 100,
                    skew_degrees=-10 + i * 5,
                    bbox_center_x_normalized=0.45 + i * 0.03,
                )
            )
        for _ in range(20):
            candidates.append(make_roi_rq(top8_eligible=False))

        # Measure memory
        tracemalloc.start()
        tracemalloc.reset_peak()

        select_batch_with_enhancement("T-mem", 1, candidates, cfg)

        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        print(f"PERF: 32-candidate peak memory={peak_bytes / 1024:.1f}KB")
        assert peak_bytes < 512_000, f"Peak memory {peak_bytes / 1024:.1f}KB exceeds 512KB limit"


# ===========================================================================
# Chunk-08: Config-Sensitivity Tests
# ===========================================================================


# ===========================================================================
# TestConfigSensitivityL1 — Function-level parametrized (4 tests)
# ===========================================================================


class TestConfigSensitivityL1:
    """Level 1: function-level config-sensitivity tests with hand-computed expected values."""

    @pytest.mark.parametrize(
        "weights, expected_dist",
        [
            # Default weights: skew=0.50, quad=0.30, persp=0.10, scale=0.10
            # skew: |0-45|/90=0.5, quad: |0.2-0.8|=0.6, persp: |0-0.6|/2=0.3, scale: |80-130|/100=0.5
            # 0.50*0.5 + 0.30*0.6 + 0.10*0.3 + 0.10*0.5 = 0.25+0.18+0.03+0.05 = 0.51
            (
                {
                    "diversity_weight_skew": 0.50,
                    "diversity_weight_quad": 0.30,
                    "diversity_weight_persp": 0.10,
                    "diversity_weight_scale": 0.10,
                },
                0.51,
            ),
            # Skew-dominant: 0.80*0.5 + 0.10*0.6 + 0.05*0.3 + 0.05*0.5 = 0.40+0.06+0.015+0.025 = 0.50
            (
                {
                    "diversity_weight_skew": 0.80,
                    "diversity_weight_quad": 0.10,
                    "diversity_weight_persp": 0.05,
                    "diversity_weight_scale": 0.05,
                },
                0.50,
            ),
            # Quad-dominant: 0.10*0.5 + 0.70*0.6 + 0.10*0.3 + 0.10*0.5 = 0.05+0.42+0.03+0.05 = 0.55
            (
                {
                    "diversity_weight_skew": 0.10,
                    "diversity_weight_quad": 0.70,
                    "diversity_weight_persp": 0.10,
                    "diversity_weight_scale": 0.10,
                },
                0.55,
            ),
            # Equal: 0.25*0.5 + 0.25*0.6 + 0.25*0.3 + 0.25*0.5 = 0.125+0.15+0.075+0.125 = 0.475
            (
                {
                    "diversity_weight_skew": 0.25,
                    "diversity_weight_quad": 0.25,
                    "diversity_weight_persp": 0.25,
                    "diversity_weight_scale": 0.25,
                },
                0.475,
            ),
        ],
        ids=["default", "skew_dominant", "quad_dominant", "equal"],
    )
    def test_pose_distance_weight_sensitivity(self, weights, expected_dist):
        """Fixed ROI pair, varied weight configs -> hand-computed distances."""
        cfg = make_cfg(**weights)
        a = make_roi_rq(
            skew_degrees=0,
            bbox_center_x_normalized=0.2,
            plate_height_px=80,
            perspective_direction=0.0,
        )
        b = make_roi_rq(
            skew_degrees=45,
            bbox_center_x_normalized=0.8,
            plate_height_px=130,
            perspective_direction=0.6,
        )
        dist = compute_pose_distance(a, b, cfg)
        assert dist == pytest.approx(expected_dist, abs=0.001)

    def test_ocr_score_ranking_flips_with_weights(self):
        """3 ROIs (sharp/big/clear), 3 weight configs flip the winner."""
        # roi_sharp: very sharp, small, mediocre contrast
        roi_sharp = make_roi_rq(tenengrad=5000, plate_height_px=50, global_contrast=40)
        # roi_big: mediocre sharpness, very big, mediocre contrast
        roi_big = make_roi_rq(tenengrad=1000, plate_height_px=150, global_contrast=40)
        # roi_clear: mediocre sharpness, medium size, excellent contrast
        roi_clear = make_roi_rq(tenengrad=1000, plate_height_px=80, global_contrast=95)
        eligible = [roi_sharp, roi_big, roi_clear]

        # Sharpness-dominant -> roi_sharp wins
        cfg_sharp = make_cfg(
            ocr_score_weight_sharpness=0.80,
            ocr_score_weight_size=0.05,
            ocr_score_weight_contrast=0.05,
            ocr_score_weight_exposure=0.05,
            ocr_score_weight_noise=0.03,
            ocr_score_weight_detection=0.02,
        )
        scores_sharp = {
            id(r): compute_ocr_likelihood_score(r, eligible, cfg_sharp) for r in eligible
        }
        assert scores_sharp[id(roi_sharp)] > scores_sharp[id(roi_big)]
        assert scores_sharp[id(roi_sharp)] > scores_sharp[id(roi_clear)]

        # Size-dominant -> roi_big wins
        cfg_size = make_cfg(
            ocr_score_weight_sharpness=0.05,
            ocr_score_weight_size=0.80,
            ocr_score_weight_contrast=0.05,
            ocr_score_weight_exposure=0.05,
            ocr_score_weight_noise=0.03,
            ocr_score_weight_detection=0.02,
        )
        scores_size = {id(r): compute_ocr_likelihood_score(r, eligible, cfg_size) for r in eligible}
        assert scores_size[id(roi_big)] > scores_size[id(roi_sharp)]
        assert scores_size[id(roi_big)] > scores_size[id(roi_clear)]

        # Contrast-dominant -> roi_clear wins
        cfg_contrast = make_cfg(
            ocr_score_weight_sharpness=0.05,
            ocr_score_weight_size=0.05,
            ocr_score_weight_contrast=0.80,
            ocr_score_weight_exposure=0.05,
            ocr_score_weight_noise=0.03,
            ocr_score_weight_detection=0.02,
        )
        scores_contrast = {
            id(r): compute_ocr_likelihood_score(r, eligible, cfg_contrast) for r in eligible
        }
        assert scores_contrast[id(roi_clear)] > scores_contrast[id(roi_sharp)]
        assert scores_contrast[id(roi_clear)] > scores_contrast[id(roi_big)]

    @pytest.mark.parametrize(
        "weight, expected_upside",
        [
            (0.40, 0.40),
            (0.80, 0.80),
            (0.10, 0.10),
        ],
        ids=["default", "high", "low"],
    )
    def test_upside_score_weight_sensitivity(self, weight, expected_upside):
        """ROI with low_contrast=True only, vary contrast weight -> upside matches weight."""
        cfg = make_cfg(enhance_upside_weight_contrast=weight)
        roi = make_roi_rq(low_contrast=True, enhance_eligible=True)
        upside = compute_enhancement_upside_score(roi, 0.50, cfg)
        assert upside == pytest.approx(expected_upside)

    def test_pose_distance_zero_weight_ignores_component(self):
        """ROIs differ only in skew. weight=0.50 -> dist=0.250. weight=0.00 -> dist=0.000."""
        a = make_roi_rq(
            skew_degrees=0,
            bbox_center_x_normalized=0.5,
            plate_height_px=80,
            perspective_direction=0.0,
        )
        b = make_roi_rq(
            skew_degrees=45,
            bbox_center_x_normalized=0.5,
            plate_height_px=80,
            perspective_direction=0.0,
        )

        # Default skew weight -> non-zero distance
        cfg_on = make_cfg(diversity_weight_skew=0.50)
        assert compute_pose_distance(a, b, cfg_on) == pytest.approx(0.25)

        # Zero skew weight -> zero distance (no other components differ)
        cfg_off = make_cfg(diversity_weight_skew=0.00)
        assert compute_pose_distance(a, b, cfg_off) == pytest.approx(0.0)


# ===========================================================================
# TestConfigSensitivityL2 — Pipeline-level parametrized (3 tests)
# ===========================================================================


class TestConfigSensitivityL2:
    """Level 2: pipeline-level tests verifying config changes flip selection outcomes."""

    def test_diversity_outcome_flips_with_weight_dominance(self):
        """8 candidates: large skew spread, tiny bbox spread.

        Skew-dominant weights -> >=5 base (skew separates them).
        Quad-dominant weights -> <=3 base (bbox clusters them).
        """
        # Positions: wide skew range [-56..56], but bbox all near 0.50
        candidates = [
            make_roi_rq(
                top8_eligible=True,
                skew_degrees=-56 + i * 16,
                bbox_center_x_normalized=0.48 + i * 0.005,
                plate_height_px=80,
                perspective_direction=0.0,
                tenengrad=3000.0 - i * 50,
            )
            for i in range(8)
        ]

        # Skew-dominant: skew differences are large -> many pass diversity
        cfg_skew = make_cfg(
            diversity_weight_skew=0.80,
            diversity_weight_quad=0.05,
            diversity_weight_persp=0.05,
            diversity_weight_scale=0.10,
        )
        result_skew = select_batch_with_enhancement("T-L2-skew", 1, candidates, cfg_skew)
        assert len(result_skew.base_rois) >= 5

        # Quad-dominant: bbox differences are tiny -> most fail diversity
        cfg_quad = make_cfg(
            diversity_weight_skew=0.05,
            diversity_weight_quad=0.80,
            diversity_weight_persp=0.05,
            diversity_weight_scale=0.10,
            diversity_min_distance=0.15,
        )
        result_quad = select_batch_with_enhancement("T-L2-quad", 1, candidates, cfg_quad)
        assert len(result_quad.base_rois) <= 3

    def test_anchor_identity_changes_with_ocr_weights(self):
        """4 sharp+small vs 4 big+blurry at well-spread positions.

        Sharpness-dominant -> sharp ROIs are anchors.
        Size-dominant -> big ROIs are anchors.
        """
        assert_positions_well_spread(WELL_SPREAD_POSITIONS_8)

        # Use positions for diversity (skew/bbox/persp), override height for sharpness/size control
        sharp_rois = [
            make_roi_rq(
                top8_eligible=True,
                tenengrad=5000,
                plate_height_px=50,
                skew_degrees=s,
                bbox_center_x_normalized=b,
                perspective_direction=p,
            )
            for s, b, _h, p in WELL_SPREAD_POSITIONS_8[:4]
        ]
        big_rois = [
            make_roi_rq(
                top8_eligible=True,
                tenengrad=1000,
                plate_height_px=150,
                skew_degrees=s,
                bbox_center_x_normalized=b,
                perspective_direction=p,
            )
            for s, b, _h, p in WELL_SPREAD_POSITIONS_8[4:]
        ]
        candidates = sharp_rois + big_rois

        # Sharpness-dominant -> sharp ROIs anchor
        cfg_sharp = make_cfg(
            ocr_score_weight_sharpness=0.80,
            ocr_score_weight_size=0.05,
            ocr_score_weight_contrast=0.05,
            ocr_score_weight_exposure=0.05,
            ocr_score_weight_noise=0.03,
            ocr_score_weight_detection=0.02,
        )
        result_sharp = select_batch_with_enhancement("T-L2-sharp", 1, candidates, cfg_sharp)
        sharp_ids = {id(r) for r in sharp_rois}
        anchors_sharp = result_sharp.base_rois[:2]
        assert all(id(a) in sharp_ids for a in anchors_sharp), "Sharp ROIs should be anchors"

        # Size-dominant -> big ROIs anchor
        cfg_size = make_cfg(
            ocr_score_weight_sharpness=0.05,
            ocr_score_weight_size=0.80,
            ocr_score_weight_contrast=0.05,
            ocr_score_weight_exposure=0.05,
            ocr_score_weight_noise=0.03,
            ocr_score_weight_detection=0.02,
        )
        result_size = select_batch_with_enhancement("T-L2-size", 1, candidates, cfg_size)
        big_ids = {id(r) for r in big_rois}
        anchors_size = result_size.base_rois[:2]
        assert all(id(a) in big_ids for a in anchors_size), "Big ROIs should be anchors"

    def test_enhancement_threshold_sensitivity(self):
        """Same candidates, threshold=0.95 -> >0 enhance, threshold=0.40 -> 0 enhance."""
        assert_positions_well_spread(WELL_SPREAD_POSITIONS_8)

        candidates = [
            make_roi_rq(
                top8_eligible=True,
                enhance_eligible=True,
                low_contrast=True,
                skew_degrees=s,
                bbox_center_x_normalized=b,
                plate_height_px=h,
                perspective_direction=p,
                tenengrad=3000.0 - i * 100,
            )
            for i, (s, b, h, p) in enumerate(WELL_SPREAD_POSITIONS_8)
        ]

        # High threshold -> OCR scores below it -> upside not suppressed -> enhancement happens
        cfg_high = make_cfg(enhance_excellence_threshold=0.95)
        result_high = select_batch_with_enhancement("T-L2-high", 1, candidates, cfg_high)
        assert len(result_high.enhance_rois) > 0

        # Low threshold -> all OCR scores above it -> upside suppressed -> no enhancement
        cfg_low = make_cfg(enhance_excellence_threshold=0.40)
        result_low = select_batch_with_enhancement("T-L2-low", 1, candidates, cfg_low)
        assert len(result_low.enhance_rois) == 0


# ===========================================================================
# TestConfigSensitivityL3 — Property-based hypothesis tests (6 tests)
# ===========================================================================


class TestConfigSensitivityL3:
    """Level 3: property-based tests using hypothesis for structural invariants."""

    @pytest.mark.property
    @given(
        skew_a=st.floats(-90, 90),
        skew_b=st.floats(-90, 90),
        bbox_a=st.floats(0, 1),
        bbox_b=st.floats(0, 1),
        height_a=st.floats(40, 200),
        height_b=st.floats(40, 200),
        persp_a=st.floats(-1, 1),
        persp_b=st.floats(-1, 1),
        w_skew=st.floats(0, 1),
        w_quad=st.floats(0, 1),
        w_persp=st.floats(0, 1),
        w_scale=st.floats(0, 1),
    )
    @settings(max_examples=50, deadline=5000)
    def test_pose_distance_always_nonneg_and_symmetric(
        self,
        skew_a,
        skew_b,
        bbox_a,
        bbox_b,
        height_a,
        height_b,
        persp_a,
        persp_b,
        w_skew,
        w_quad,
        w_persp,
        w_scale,
    ):
        """Pose distance >= 0, <= weight_sum, symmetric, self == 0."""
        assume(w_skew + w_quad + w_persp + w_scale > 0)
        cfg = make_cfg(
            diversity_weight_skew=w_skew,
            diversity_weight_quad=w_quad,
            diversity_weight_persp=w_persp,
            diversity_weight_scale=w_scale,
        )
        a = make_roi_rq(
            skew_degrees=skew_a,
            bbox_center_x_normalized=bbox_a,
            plate_height_px=height_a,
            perspective_direction=persp_a,
        )
        b = make_roi_rq(
            skew_degrees=skew_b,
            bbox_center_x_normalized=bbox_b,
            plate_height_px=height_b,
            perspective_direction=persp_b,
        )

        dist_ab = compute_pose_distance(a, b, cfg)
        dist_ba = compute_pose_distance(b, a, cfg)
        dist_aa = compute_pose_distance(a, a, cfg)
        weight_sum = w_skew + w_quad + w_persp + w_scale

        assert dist_ab >= 0.0
        assert dist_ab <= weight_sum + 1e-9
        assert dist_ab == pytest.approx(dist_ba, abs=1e-9)
        assert dist_aa == pytest.approx(0.0, abs=1e-9)

    @pytest.mark.property
    @given(
        tenengrad=st.floats(0, 10000),
        height=st.floats(40, 200),
        contrast=st.floats(0, 100),
        luminance=st.floats(0, 255),
        noise=st.floats(0, 50),
        confidence=st.floats(0, 1),
    )
    @settings(max_examples=50, deadline=5000)
    def test_ocr_score_always_bounded(
        self,
        tenengrad,
        height,
        contrast,
        luminance,
        noise,
        confidence,
    ):
        """OCR score always in [0, 1] (function has final clamp)."""
        cfg = make_cfg()
        roi = make_roi_rq(
            tenengrad=tenengrad,
            plate_height_px=height,
            global_contrast=contrast,
            luminance_mean=luminance,
            noise_std=noise,
            detection_confidence=confidence,
        )
        mediocre = make_roi_rq()
        eligible = [roi, mediocre]
        score = compute_ocr_likelihood_score(roi, eligible, cfg)
        assert 0.0 <= score <= 1.0

    @pytest.mark.property
    @given(
        n_candidates=st.integers(1, 20),
        max_batch=st.integers(1, 12),
    )
    @settings(max_examples=50, deadline=5000)
    def test_diversity_fill_respects_max_batch_size(self, n_candidates, max_batch):
        """diversity_fill result <= max_batch_size."""
        cfg = make_cfg(max_batch_size=max_batch)
        candidates = [
            make_roi_rq(
                skew_degrees=-45 + i * (90 / max(n_candidates - 1, 1)),
                bbox_center_x_normalized=0.1 + i * (0.8 / max(n_candidates - 1, 1)),
                plate_height_px=50 + i * 5,
                tenengrad=3000.0 - i * 50,
            )
            for i in range(n_candidates)
        ]
        anchors = candidates[: min(2, n_candidates)]
        remaining = candidates[min(2, n_candidates) :]
        ocr_scores = {id(r): 0.9 - i * 0.01 for i, r in enumerate(candidates)}

        result = diversity_fill(anchors, remaining, ocr_scores, cfg)
        assert len(result) <= max_batch

    @pytest.mark.property
    @given(
        n_candidates=st.integers(3, 15),
        min_dist=st.floats(0.01, 0.30),
    )
    @settings(max_examples=50, deadline=5000)
    def test_diversity_fill_min_distance_invariant(self, n_candidates, min_dist):
        """Every non-anchor's min pairwise distance >= min_distance."""
        cfg = make_cfg(diversity_min_distance=min_dist)
        candidates = [
            make_roi_rq(
                skew_degrees=-45 + i * (90 / max(n_candidates - 1, 1)),
                bbox_center_x_normalized=0.1 + i * (0.8 / max(n_candidates - 1, 1)),
                plate_height_px=50 + i * 5,
                tenengrad=3000.0 - i * 50,
            )
            for i in range(n_candidates)
        ]
        anchors = candidates[:2]
        remaining = candidates[2:]
        ocr_scores = {id(r): 0.9 - i * 0.01 for i, r in enumerate(candidates)}

        result = diversity_fill(anchors, remaining, ocr_scores, cfg)
        anchor_ids = {id(a) for a in anchors}

        for roi in result:
            if id(roi) in anchor_ids:
                continue
            min_pair_dist = min(
                compute_pose_distance(roi, other, cfg) for other in result if other is not roi
            )
            assert min_pair_dist >= min_dist - 1e-9, (
                f"Non-anchor has min pairwise dist {min_pair_dist:.4f} < {min_dist}"
            )

    @pytest.mark.property
    @given(
        n_candidates=st.integers(1, 12),
        max_enhance=st.integers(0, 6),
    )
    @settings(max_examples=50, deadline=5000)
    def test_enhancement_count_respects_max(self, n_candidates, max_enhance):
        """Enhancement count <= max_enhance_duplicates."""
        cfg = make_cfg(max_enhance_duplicates=max_enhance)
        base = [
            make_roi_rq(
                enhance_eligible=True,
                low_contrast=True,
                tenengrad=2000 + i * 100,
            )
            for i in range(n_candidates)
        ]
        ocr_scores = {id(r): 0.50 + i * 0.01 for i, r in enumerate(base)}

        result = select_enhancement_duplicates(base, ocr_scores, cfg)
        assert len(result) <= max_enhance

    @pytest.mark.property
    @given(
        low_contrast=st.booleans(),
        exposure_bad=st.booleans(),
        mildly_soft=st.booleans(),
        noisy=st.booleans(),
        w_contrast=st.floats(0, 1),
        w_exposure=st.floats(0, 1),
        w_sharpness=st.floats(0, 1),
        w_noise=st.floats(0, 1),
    )
    @settings(max_examples=50, deadline=5000)
    def test_enhancement_upside_always_nonneg(
        self,
        low_contrast,
        exposure_bad,
        mildly_soft,
        noisy,
        w_contrast,
        w_exposure,
        w_sharpness,
        w_noise,
    ):
        """Upside >= 0 and <= sum of upside weights."""
        cfg = make_cfg(
            enhance_upside_weight_contrast=w_contrast,
            enhance_upside_weight_exposure=w_exposure,
            enhance_upside_weight_sharpness=w_sharpness,
            enhance_upside_weight_noise=w_noise,
        )
        roi = make_roi_rq(
            low_contrast=low_contrast,
            exposure_bad=exposure_bad,
            mildly_soft=mildly_soft,
            noisy=noisy,
            enhance_eligible=True,
        )
        upside = compute_enhancement_upside_score(roi, 0.50, cfg)
        weight_sum = w_contrast + w_exposure + w_sharpness + w_noise
        assert upside >= 0.0
        assert upside <= weight_sum + 1e-9
