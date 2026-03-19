"""Tests for recipe planner: config, types, schema constants, sub-planners, and entry point."""

from __future__ import annotations

import logging
import uuid

import numpy as np
import pytest
from hypothesis import given, settings, strategies as st

from consumer.config import ConsumerConfig
from consumer.models import EnhancedBatchSelection, RichQualityMetrics, RoiRichQuality
from producer.models import FastQualityMetrics, RoiFastQuality, RoiImage

# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_metrics(**overrides: object) -> RichQualityMetrics:
    """Factory for RichQualityMetrics with clean defaults (good-quality plate)."""
    defaults: dict[str, object] = {
        "track_id": uuid.uuid4(),
        "frame_idx": 0,
        "canonical_width": 256,
        "canonical_height": 128,
        "plate_width_px": 200.0,
        "plate_height_px": 80.0,
        "crop_clip_fraction": 0.0,
        "detection_confidence": 0.95,
        "keypoints": [(100, 10), (10, 10), (100, 70), (10, 70)],
        "keypoint_scores": [0.9, 0.9, 0.9, 0.9],
        "keypoint_confidence_min": 0.9,
        "keypoint_confidence_mean": 0.9,
        "skew_degrees": 0.0,
        "perspective_score": 1.0,
        "perspective_direction": 0.0,
        "luminance_mean": 128.0,
        "luminance_p05": 30.0,
        "luminance_p95": 220.0,
        "black_clip_fraction": 0.02,
        "white_clip_fraction": 0.02,
        "global_contrast": 60.0,
        "local_contrast": 15.0,
        "tenengrad": 3500.0,
        "tenengrad_horizontal": 2500.0,
        "tenengrad_vertical": 2500.0,
        "blur_anisotropy": 1.0,
        "noise_std": 5.0,
        "flat_region_fraction": 0.3,
        "too_small": False,
        "clipped": False,
        "very_blurry": False,
        "mildly_soft": False,
        "exposure_bad": False,
        "low_contrast": False,
        "noisy": False,
        "vertical_edges_weak": False,
        "horizontal_edges_weak": False,
        "top8_eligible": True,
        "enhance_eligible": True,
        "homography_eligible": True,
        "bbox_center_x_normalized": 0.5,
        "bbox_center_y_normalized": 0.5,
        "diversity_signature": np.array([0.0, 0.5, 0.5, 0.0], dtype=np.float32),
        "quad_area_px": 5000.0,
        "edge_ratio": 2.0,
    }
    defaults.update(overrides)
    return RichQualityMetrics(**defaults)  # type: ignore[arg-type]


def make_cfg(**overrides: object) -> ConsumerConfig:
    """Factory for ConsumerConfig with optional overrides."""
    return ConsumerConfig(**overrides)  # type: ignore[arg-type]


def make_roi_rq(**metric_overrides: object) -> RoiRichQuality:
    """Factory for RoiRichQuality with controllable RichQualityMetrics fields."""
    metrics = make_metrics(**metric_overrides)
    crop = np.zeros((1, 1, 3), dtype=np.uint8)
    roi_img = RoiImage(
        track_id=str(metrics.track_id),
        crop_img=crop,
        bbox=[0.0, 0.0, 200.0, 80.0],
        padded_bbox=[0.0, 0.0, 200.0, 80.0],
        frame_idx=metrics.frame_idx,
        confidence=0.9,
        frame_width=1920,
        frame_height=1080,
        keypoints=metrics.keypoints,
        keypoint_scores=[0.9, 0.9, 0.9, 0.9] if metrics.keypoints else None,
        crop_width=200,
        crop_height=80,
    )
    fast_metrics = FastQualityMetrics(
        focus_tenengrad=float(metrics.tenengrad),
        brightness_mean=float(metrics.luminance_mean) / 255.0,
        contrast_std=float(metrics.global_contrast) / 100.0,
        over_exposed_frac=float(metrics.white_clip_fraction),
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
    return RoiRichQuality(roi_fq=roi_fq, metrics=metrics)


def make_batch(
    base_overrides: list[dict[str, object]] | None = None,
    enhance_overrides: list[dict[str, object]] | None = None,
    *,
    duplicate_groups: dict[str, list[int]] | None = None,
    track_id: str = "T-test",
    version: int = 1,
) -> EnhancedBatchSelection:
    """Factory for EnhancedBatchSelection from lists of metric overrides."""
    base_list = base_overrides or []
    enhance_list = enhance_overrides or []
    base_rois = [make_roi_rq(track_id=track_id, **ovr) for ovr in base_list]
    enhance_rois = [make_roi_rq(track_id=track_id, **ovr) for ovr in enhance_list]
    if duplicate_groups is None:
        duplicate_groups = {}
    return EnhancedBatchSelection(
        track_id=track_id,
        version=version,
        base_rois=base_rois,
        enhance_rois=enhance_rois,
        duplicate_groups=duplicate_groups,
    )


# ---------------------------------------------------------------------------
# Config existence and defaults
# ---------------------------------------------------------------------------


class TestRecipeConfig:
    """Tests for recipe/processor config fields on ConsumerConfig."""

    def test_config_constructible_with_defaults(self):
        """ConsumerConfig() succeeds with no args; instance returned."""
        from consumer.config import ConsumerConfig

        cfg = ConsumerConfig()
        assert cfg is not None

    def test_config_has_all_recipe_fields(self):
        """All 28 new fields exist on ConsumerConfig() with correct types."""
        from consumer.config import ConsumerConfig

        cfg = ConsumerConfig()

        # 5 target params
        assert isinstance(cfg.target_luma, float)
        assert isinstance(cfg.target_contrast, float)
        assert isinstance(cfg.target_sharpness, float)
        assert isinstance(cfg.target_noise_max, float)
        assert isinstance(cfg.luma_range, float)

        # 5 mapping gains
        assert isinstance(cfg.k_gain, float)
        assert isinstance(cfg.k_gamma, float)
        assert isinstance(cfg.k_contrast, float)
        assert isinstance(cfg.k_denoise, float)
        assert isinstance(cfg.k_sharpen, float)

        # 7 clamp ranges
        assert isinstance(cfg.gain_min, float)
        assert isinstance(cfg.gain_max, float)
        assert isinstance(cfg.gamma_min, float)
        assert isinstance(cfg.gamma_max, float)
        assert isinstance(cfg.contrast_max, float)
        assert isinstance(cfg.denoise_max, float)
        assert isinstance(cfg.sharpen_max, float)

        # 7 CLAHE params
        assert isinstance(cfg.clahe_blend_max, float)
        assert isinstance(cfg.clahe_clip_min, float)
        assert isinstance(cfg.clahe_clip_max, float)
        assert isinstance(cfg.clahe_tiles_min, int)
        assert isinstance(cfg.clahe_tiles_max, int)
        assert isinstance(cfg.clahe_max_white_clip, float)
        assert isinstance(cfg.clahe_min_sharpness, float)

        # 3 geometry
        assert isinstance(cfg.warp_margin, float)
        assert isinstance(cfg.recipe_output_width, int)
        assert isinstance(cfg.recipe_output_height, int)

        # 1 photometric
        assert isinstance(cfg.contrast_steepness, float)

    def test_config_default_values_match_spec(self):
        """Spot-check 7 representative defaults."""
        from consumer.config import ConsumerConfig

        cfg = ConsumerConfig()
        assert cfg.target_luma == 128.0
        assert cfg.k_gain == 1.0
        assert cfg.gain_min == 0.75
        assert cfg.gain_max == 1.25
        assert cfg.clahe_tiles_min == 6
        assert cfg.warp_margin == 0.04
        assert cfg.contrast_steepness == 10.0


# ---------------------------------------------------------------------------
# Config validation (constraint tests)
# ---------------------------------------------------------------------------


class TestRecipeConfigValidation:
    """Tests for __post_init__ validation of new recipe config fields."""

    def test_rejects_gain_min_gt_gain_max(self):
        """ConsumerConfig(gain_min=2.0, gain_max=1.0) raises ValueError."""
        from consumer.config import ConsumerConfig

        with pytest.raises(ValueError):
            ConsumerConfig(gain_min=2.0, gain_max=1.0)

    def test_rejects_gamma_min_gt_gamma_max(self):
        """ConsumerConfig(gamma_min=2.0, gamma_max=0.5) raises ValueError."""
        from consumer.config import ConsumerConfig

        with pytest.raises(ValueError):
            ConsumerConfig(gamma_min=2.0, gamma_max=0.5)

    def test_rejects_negative_k_gain(self):
        """ConsumerConfig(k_gain=-1.0) raises ValueError."""
        from consumer.config import ConsumerConfig

        with pytest.raises(ValueError):
            ConsumerConfig(k_gain=-1.0)

    def test_rejects_target_luma_out_of_range(self):
        """ConsumerConfig(target_luma=300.0) raises ValueError."""
        from consumer.config import ConsumerConfig

        with pytest.raises(ValueError):
            ConsumerConfig(target_luma=300.0)

    def test_rejects_negative_target_contrast(self):
        """ConsumerConfig(target_contrast=-5.0) raises ValueError."""
        from consumer.config import ConsumerConfig

        with pytest.raises(ValueError):
            ConsumerConfig(target_contrast=-5.0)

    def test_rejects_clahe_clip_min_gt_max(self):
        """ConsumerConfig(clahe_clip_min=5.0, clahe_clip_max=2.0) raises ValueError."""
        from consumer.config import ConsumerConfig

        with pytest.raises(ValueError):
            ConsumerConfig(clahe_clip_min=5.0, clahe_clip_max=2.0)

    def test_rejects_clahe_tiles_min_gt_max(self):
        """ConsumerConfig(clahe_tiles_min=15, clahe_tiles_max=6) raises ValueError."""
        from consumer.config import ConsumerConfig

        with pytest.raises(ValueError):
            ConsumerConfig(clahe_tiles_min=15, clahe_tiles_max=6)

    def test_rejects_warp_margin_out_of_range(self):
        """ConsumerConfig(warp_margin=0.6) raises ValueError."""
        from consumer.config import ConsumerConfig

        with pytest.raises(ValueError):
            ConsumerConfig(warp_margin=0.6)


# ---------------------------------------------------------------------------
# New dataclasses
# ---------------------------------------------------------------------------


class TestRecipeDataclasses:
    """Tests for RecipeRow, PostProcessingMeta, ProcessorOutput dataclasses."""

    def test_recipe_row_constructible(self):
        """RecipeRow constructible; all 8 fields accessible."""
        from consumer.models import RecipeRow

        row = RecipeRow(
            luma_deficit=0.1,
            contrast_deficit=0.2,
            sharpness_deficit=0.3,
            noise_excess=0.05,
            gates_passed=["is_enhanced", "low_contrast"],
            gates_failed=["noisy"],
            active_ops=["gain", "gamma"],
            primary_reason="underexposed",
        )
        assert row.luma_deficit == 0.1
        assert row.contrast_deficit == 0.2
        assert row.sharpness_deficit == 0.3
        assert row.noise_excess == 0.05
        assert row.gates_passed == ["is_enhanced", "low_contrast"]
        assert row.gates_failed == ["noisy"]
        assert row.active_ops == ["gain", "gamma"]
        assert row.primary_reason == "underexposed"

    def test_post_processing_meta_constructible(self):
        """PostProcessingMeta constructible; all 4 fields accessible."""
        from consumer.models import PostProcessingMeta

        meta = PostProcessingMeta(
            post_luma_mean=0.5,
            post_global_contrast=0.6,
            luma_delta=-0.1,
            contrast_delta=0.05,
        )
        assert meta.post_luma_mean == 0.5
        assert meta.post_global_contrast == 0.6
        assert meta.luma_delta == -0.1
        assert meta.contrast_delta == 0.05

    def test_processor_output_constructible(self):
        """ProcessorOutput constructible; fields have correct types."""
        import torch

        from consumer.models import PostProcessingMeta, ProcessorOutput

        tensor = torch.zeros(1, 3, 32, 128)
        is_enh = np.array([False])
        post = [
            PostProcessingMeta(
                post_luma_mean=0.5,
                post_global_contrast=0.6,
                luma_delta=-0.1,
                contrast_delta=0.05,
            )
        ]
        out = ProcessorOutput(
            batch_tensor=tensor,
            is_enhanced=is_enh,
            post_meta=post,
        )
        assert out.batch_tensor.shape == (1, 3, 32, 128)
        assert isinstance(out.is_enhanced, np.ndarray)
        assert len(out.post_meta) == 1


# ---------------------------------------------------------------------------
# Recipe tensor schema constants
# ---------------------------------------------------------------------------


class TestRecipeSchema:
    """Tests for RECIPE_KEYS, IDX_*, RECIPE_IDENTITY, RECIPE_RANGES constants."""

    def test_recipe_keys_length_12(self):
        """len(RECIPE_KEYS) == 12."""
        from consumer.ops_recipe import RECIPE_KEYS

        assert len(RECIPE_KEYS) == 12

    def test_recipe_identity_length_12(self):
        """len(RECIPE_IDENTITY) == 12."""
        from consumer.ops_recipe import RECIPE_IDENTITY

        assert len(RECIPE_IDENTITY) == 12

    def test_recipe_ranges_length_12(self):
        """len(RECIPE_RANGES) == 12."""
        from consumer.ops_recipe import RECIPE_RANGES

        assert len(RECIPE_RANGES) == 12

    def test_idx_constants_match_keys(self):
        """RECIPE_KEYS[IDX_*] matches the expected key name for all 12 indices."""
        from consumer.ops_recipe import (
            IDX_CLAHE,
            IDX_CLAHE_CLIP,
            IDX_CLAHE_TILES,
            IDX_CONTRAST,
            IDX_DENOISE,
            IDX_GAIN,
            IDX_GAMMA,
            IDX_RESERVED,
            IDX_SHARPEN,
            IDX_TIGHT_CROP,
            IDX_WARP_ENABLE,
            IDX_WARP_MARGIN,
            RECIPE_KEYS,
        )

        assert RECIPE_KEYS[IDX_WARP_ENABLE] == "warp_enable"
        assert RECIPE_KEYS[IDX_TIGHT_CROP] == "tight_crop_enable"
        assert RECIPE_KEYS[IDX_RESERVED] == "reserved"
        assert RECIPE_KEYS[IDX_WARP_MARGIN] == "warp_margin"
        assert RECIPE_KEYS[IDX_GAIN] == "gain"
        assert RECIPE_KEYS[IDX_GAMMA] == "gamma"
        assert RECIPE_KEYS[IDX_CONTRAST] == "contrast"
        assert RECIPE_KEYS[IDX_DENOISE] == "denoise"
        assert RECIPE_KEYS[IDX_SHARPEN] == "sharpen"
        assert RECIPE_KEYS[IDX_CLAHE] == "clahe"
        assert RECIPE_KEYS[IDX_CLAHE_CLIP] == "clahe_clip"
        assert RECIPE_KEYS[IDX_CLAHE_TILES] == "clahe_tiles"

    def test_identity_values_correct(self):
        """RECIPE_IDENTITY has correct identity values for all keys."""
        from consumer.ops_recipe import RECIPE_IDENTITY

        assert RECIPE_IDENTITY["gain"] == 1.0
        assert RECIPE_IDENTITY["gamma"] == 1.0
        assert RECIPE_IDENTITY["contrast"] == 0.0
        assert RECIPE_IDENTITY["denoise"] == 0.0
        assert RECIPE_IDENTITY["sharpen"] == 0.0
        assert RECIPE_IDENTITY["clahe"] == 0.0
        assert RECIPE_IDENTITY["reserved"] == 0.0
        assert RECIPE_IDENTITY["warp_enable"] == 0.0


# ---------------------------------------------------------------------------
# Chunk-02: Sub-planner functions
# ---------------------------------------------------------------------------


class TestPlanGeometry:
    """Tests for plan_geometry sub-planner."""

    def test_geometry_homography_eligible_returns_warp(self):
        """Homography-eligible metrics produce warp_enable=1, tight_crop=1, margin from cfg."""
        from consumer.ops_recipe import plan_geometry

        m = make_metrics(homography_eligible=True)
        cfg = make_cfg(warp_margin=0.04)
        result = plan_geometry(m, cfg)
        assert result == (1.0, 1.0, 0.04)

    def test_geometry_not_eligible_returns_identity(self):
        """Non-eligible metrics produce all zeros."""
        from consumer.ops_recipe import plan_geometry

        m = make_metrics(homography_eligible=False)
        cfg = make_cfg()
        result = plan_geometry(m, cfg)
        assert result == (0.0, 0.0, 0.0)

    def test_geometry_custom_margin_propagates(self):
        """Custom warp_margin in cfg propagates to result."""
        from consumer.ops_recipe import plan_geometry

        m = make_metrics(homography_eligible=True)
        cfg = make_cfg(warp_margin=0.07)
        result = plan_geometry(m, cfg)
        assert result[2] == 0.07


class TestPlanExposure:
    """Tests for plan_exposure sub-planner."""

    def test_exposure_dark_image_brightens(self):
        """Dark image (luma=80) -> gain>1, gamma<1."""
        from consumer.ops_recipe import plan_exposure

        m = make_metrics(luminance_mean=80.0)
        cfg = make_cfg(target_luma=128.0, luma_range=255.0, k_gain=1.0, k_gamma=1.0)
        gain, gamma = plan_exposure(m, cfg)
        assert gain == pytest.approx(1.188, abs=0.001)
        assert gamma == pytest.approx(0.829, abs=0.001)
        assert gain > 1.0
        assert gamma < 1.0

    def test_exposure_bright_image_darkens(self):
        """Bright image (luma=200) -> gain clamped to 0.75, gamma>1."""
        from consumer.ops_recipe import plan_exposure

        m = make_metrics(luminance_mean=200.0)
        cfg = make_cfg(target_luma=128.0, luma_range=255.0, k_gain=1.0, k_gamma=1.0)
        gain, gamma = plan_exposure(m, cfg)
        assert gain == 0.75  # clamped to gain_min
        assert gamma == pytest.approx(1.326, abs=0.001)
        assert gain < 1.0
        assert gamma > 1.0

    def test_exposure_adequate_identity(self):
        """Adequate luminance -> identity (gain~1.0, gamma~1.0)."""
        from consumer.ops_recipe import plan_exposure

        m = make_metrics(luminance_mean=128.0)
        cfg = make_cfg(target_luma=128.0)
        gain, gamma = plan_exposure(m, cfg)
        assert gain == pytest.approx(1.0)
        assert gamma == pytest.approx(1.0)

    def test_exposure_white_clip_guard_caps_brightening(self):
        """High white_clip prevents brightening of dark image."""
        from consumer.ops_recipe import plan_exposure

        m = make_metrics(luminance_mean=80.0, white_clip_fraction=0.20)
        cfg = make_cfg(target_luma=128.0)
        gain, gamma = plan_exposure(m, cfg)
        assert gain == 1.0
        assert gamma == 1.0

    def test_exposure_black_clip_guard_caps_darkening(self):
        """High black_clip prevents darkening of bright image."""
        from consumer.ops_recipe import plan_exposure

        m = make_metrics(luminance_mean=200.0, black_clip_fraction=0.20)
        cfg = make_cfg(target_luma=128.0)
        gain, gamma = plan_exposure(m, cfg)
        assert gain == 1.0
        assert gamma == 1.0

    def test_exposure_gain_clamped_to_range(self):
        """Extreme delta clamps gain to gain_max."""
        from consumer.ops_recipe import plan_exposure

        m = make_metrics(luminance_mean=30.0)
        cfg = make_cfg(target_luma=200.0, k_gain=3.0, gain_max=1.25)
        gain, _gamma = plan_exposure(m, cfg)
        assert gain == 1.25

    def test_exposure_gamma_clamped_to_range(self):
        """Extreme delta clamps gamma to gamma_min."""
        from consumer.ops_recipe import plan_exposure

        m = make_metrics(luminance_mean=30.0)
        cfg = make_cfg(target_luma=200.0, k_gamma=5.0, gamma_min=0.70)
        gain, gamma = plan_exposure(m, cfg)
        assert gamma == 0.70


class TestPlanContrast:
    """Tests for plan_contrast sub-planner."""

    def test_contrast_low_contrast_produces_blend(self):
        """Low contrast with large deficit -> clamped to contrast_max."""
        from consumer.ops_recipe import plan_contrast

        m = make_metrics(low_contrast=True, global_contrast=30.0)
        cfg = make_cfg(target_contrast=60.0, k_contrast=1.0, contrast_max=0.40)
        result = plan_contrast(m, cfg)
        assert result == pytest.approx(0.40)

    def test_contrast_not_low_contrast_returns_zero(self):
        """Not low_contrast -> returns 0.0 regardless."""
        from consumer.ops_recipe import plan_contrast

        m = make_metrics(low_contrast=False, global_contrast=30.0)
        cfg = make_cfg(target_contrast=60.0)
        result = plan_contrast(m, cfg)
        assert result == 0.0

    def test_contrast_mild_deficit_proportional(self):
        """Mild deficit returns proportional value below contrast_max."""
        from consumer.ops_recipe import plan_contrast

        m = make_metrics(low_contrast=True, global_contrast=50.0)
        cfg = make_cfg(target_contrast=60.0, k_contrast=1.0)
        result = plan_contrast(m, cfg)
        assert result == pytest.approx(0.167, abs=0.001)

    def test_contrast_above_target_returns_zero(self):
        """Contrast already above target -> deficit negative -> returns 0.0."""
        from consumer.ops_recipe import plan_contrast

        m = make_metrics(low_contrast=True, global_contrast=80.0)
        cfg = make_cfg(target_contrast=60.0)
        result = plan_contrast(m, cfg)
        assert result == 0.0


class TestPlanDenoiseSharpen:
    """Tests for plan_denoise_sharpen sub-planner."""

    def test_denoise_sharpen_very_blurry_returns_both_zero(self):
        """Very blurry -> (0, 0) even if noisy and mildly_soft."""
        from consumer.ops_recipe import plan_denoise_sharpen

        m = make_metrics(very_blurry=True, noisy=True, mildly_soft=True, noise_std=20.0)
        cfg = make_cfg()
        denoise, sharpen = plan_denoise_sharpen(m, cfg)
        assert denoise == 0.0
        assert sharpen == 0.0

    def test_denoise_sharpen_noisy_returns_denoise_only(self):
        """Noisy -> denoise proportional, sharpen=0."""
        from consumer.ops_recipe import plan_denoise_sharpen

        m = make_metrics(noisy=True, noise_std=20.0, very_blurry=False, mildly_soft=False)
        cfg = make_cfg(target_noise_max=12.0, k_denoise=1.0, denoise_max=0.40)
        denoise, sharpen = plan_denoise_sharpen(m, cfg)
        assert denoise == pytest.approx(0.40)  # excess=0.667, clamped to 0.40
        assert sharpen == 0.0

    def test_denoise_sharpen_mildly_soft_returns_sharpen_only(self):
        """Mildly soft -> sharpen proportional, denoise=0."""
        from consumer.ops_recipe import plan_denoise_sharpen

        m = make_metrics(mildly_soft=True, noisy=False, very_blurry=False, tenengrad=1500.0)
        cfg = make_cfg(target_sharpness=3000.0, k_sharpen=1.0, sharpen_max=0.35)
        denoise, sharpen = plan_denoise_sharpen(m, cfg)
        assert denoise == 0.0
        assert sharpen == pytest.approx(0.35)  # deficit=0.5, clamped to 0.35

    def test_denoise_sharpen_neither_returns_both_zero(self):
        """Neither noisy nor soft -> (0, 0)."""
        from consumer.ops_recipe import plan_denoise_sharpen

        m = make_metrics(very_blurry=False, noisy=False, mildly_soft=False)
        cfg = make_cfg()
        denoise, sharpen = plan_denoise_sharpen(m, cfg)
        assert denoise == 0.0
        assert sharpen == 0.0

    @given(
        noise_std=st.floats(min_value=0.0, max_value=50.0),
        tenengrad=st.floats(min_value=0.0, max_value=10000.0),
        very_blurry=st.booleans(),
        noisy=st.booleans(),
        mildly_soft=st.booleans(),
    )
    @settings(max_examples=200)
    def test_denoise_sharpen_mutual_exclusivity_invariant(
        self,
        noise_std: float,
        tenengrad: float,
        very_blurry: bool,
        noisy: bool,
        mildly_soft: bool,
    ):
        """Property: denoise and sharpen are never both > 0."""
        from consumer.ops_recipe import plan_denoise_sharpen

        m = make_metrics(
            noise_std=noise_std,
            tenengrad=tenengrad,
            very_blurry=very_blurry,
            noisy=noisy,
            mildly_soft=mildly_soft,
        )
        cfg = make_cfg()
        denoise, sharpen = plan_denoise_sharpen(m, cfg)
        assert not (denoise > 0 and sharpen > 0)

    @given(
        noise_std=st.floats(min_value=0.0, max_value=50.0),
        tenengrad=st.floats(min_value=0.0, max_value=10000.0),
        very_blurry=st.booleans(),
        noisy=st.booleans(),
        mildly_soft=st.booleans(),
    )
    @settings(max_examples=200)
    def test_denoise_sharpen_outputs_within_ranges(
        self,
        noise_std: float,
        tenengrad: float,
        very_blurry: bool,
        noisy: bool,
        mildly_soft: bool,
    ):
        """Property: outputs within [0, max] bounds."""
        from consumer.ops_recipe import plan_denoise_sharpen

        m = make_metrics(
            noise_std=noise_std,
            tenengrad=tenengrad,
            very_blurry=very_blurry,
            noisy=noisy,
            mildly_soft=mildly_soft,
        )
        cfg = make_cfg()
        denoise, sharpen = plan_denoise_sharpen(m, cfg)
        assert 0.0 <= denoise <= cfg.denoise_max
        assert 0.0 <= sharpen <= cfg.sharpen_max


class TestPlanClahe:
    """Tests for plan_clahe sub-planner."""

    def test_clahe_gate_fail_not_enhanced(self):
        """is_enhanced=False -> clahe==0.0."""
        from consumer.ops_recipe import plan_clahe

        m = make_metrics(
            enhance_eligible=True,
            low_contrast=True,
            global_contrast=30.0,
            white_clip_fraction=0.02,
            noisy=False,
            very_blurry=False,
            tenengrad=3500.0,
        )
        clahe, _clip, _tiles = plan_clahe(m, False, make_cfg())
        assert clahe == 0.0

    def test_clahe_gate_fail_not_enhance_eligible(self):
        """enhance_eligible=False -> clahe==0.0."""
        from consumer.ops_recipe import plan_clahe

        m = make_metrics(
            enhance_eligible=False,
            low_contrast=True,
            global_contrast=30.0,
            white_clip_fraction=0.02,
            noisy=False,
            very_blurry=False,
            tenengrad=3500.0,
        )
        clahe, _clip, _tiles = plan_clahe(m, True, make_cfg())
        assert clahe == 0.0

    def test_clahe_gate_fail_not_low_contrast(self):
        """low_contrast=False -> clahe==0.0."""
        from consumer.ops_recipe import plan_clahe

        m = make_metrics(
            enhance_eligible=True,
            low_contrast=False,
            global_contrast=80.0,
            white_clip_fraction=0.02,
            noisy=False,
            very_blurry=False,
            tenengrad=3500.0,
        )
        clahe, _clip, _tiles = plan_clahe(m, True, make_cfg())
        assert clahe == 0.0

    def test_clahe_gate_fail_white_clip_too_high(self):
        """white_clip >= clahe_max_white_clip -> clahe==0.0."""
        from consumer.ops_recipe import plan_clahe

        m = make_metrics(
            enhance_eligible=True,
            low_contrast=True,
            global_contrast=30.0,
            white_clip_fraction=0.15,
            noisy=False,
            very_blurry=False,
            tenengrad=3500.0,
        )
        cfg = make_cfg(clahe_max_white_clip=0.10)
        clahe, _clip, _tiles = plan_clahe(m, True, cfg)
        assert clahe == 0.0

    def test_clahe_gate_fail_noisy(self):
        """noisy=True -> clahe==0.0."""
        from consumer.ops_recipe import plan_clahe

        m = make_metrics(
            enhance_eligible=True,
            low_contrast=True,
            global_contrast=30.0,
            white_clip_fraction=0.02,
            noisy=True,
            very_blurry=False,
            tenengrad=3500.0,
        )
        clahe, _clip, _tiles = plan_clahe(m, True, make_cfg())
        assert clahe == 0.0

    def test_clahe_gate_fail_very_blurry(self):
        """very_blurry=True -> clahe==0.0."""
        from consumer.ops_recipe import plan_clahe

        m = make_metrics(
            enhance_eligible=True,
            low_contrast=True,
            global_contrast=30.0,
            white_clip_fraction=0.02,
            noisy=False,
            very_blurry=True,
            tenengrad=3500.0,
        )
        clahe, _clip, _tiles = plan_clahe(m, True, make_cfg())
        assert clahe == 0.0

    def test_clahe_gate_fail_inadequate_sharpness(self):
        """tenengrad < clahe_min_sharpness -> clahe==0.0."""
        from consumer.ops_recipe import plan_clahe

        m = make_metrics(
            enhance_eligible=True,
            low_contrast=True,
            global_contrast=30.0,
            white_clip_fraction=0.02,
            noisy=False,
            very_blurry=False,
            mildly_soft=True,
            tenengrad=300.0,
        )
        cfg = make_cfg(clahe_min_sharpness=500.0)
        clahe, _clip, _tiles = plan_clahe(m, True, cfg)
        assert clahe == 0.0

    def test_clahe_all_gates_pass_proportional(self):
        """All gates pass -> proportional clahe, clip, tiles."""
        from consumer.ops_recipe import plan_clahe

        m = make_metrics(
            enhance_eligible=True,
            low_contrast=True,
            global_contrast=30.0,
            white_clip_fraction=0.02,
            noisy=False,
            very_blurry=False,
            tenengrad=3500.0,
        )
        cfg = make_cfg(target_contrast=60.0)
        clahe, clip, tiles = plan_clahe(m, True, cfg)
        assert clahe > 0.0
        assert 1.5 <= clip <= 4.0
        assert 6 <= tiles <= 12

    def test_clahe_base_image_always_zero(self):
        """Base image (is_enhanced=False) -> clahe==0.0 always."""
        from consumer.ops_recipe import plan_clahe

        m = make_metrics(
            enhance_eligible=True,
            low_contrast=True,
            global_contrast=30.0,
            white_clip_fraction=0.02,
            noisy=False,
            very_blurry=False,
            tenengrad=3500.0,
        )
        clahe, _clip, _tiles = plan_clahe(m, False, make_cfg(target_contrast=60.0))
        assert clahe == 0.0


# ---------------------------------------------------------------------------
# Chunk-03: Recipe tensor assembly and entry point
# ---------------------------------------------------------------------------


class TestGenerateRecipeTensor:
    """Tests for generate_recipe_tensor entry point."""

    def test_empty_batch_returns_zero_tensor(self):
        """Empty batch -> (0,12) tensor, empty is_enhanced, empty metadata."""
        from consumer.ops_recipe import generate_recipe_tensor

        batch = make_batch([], [])
        cfg = make_cfg()
        recipe_tensor, _recipe_keys, is_enhanced, _group_ids, metadata = generate_recipe_tensor(
            batch, cfg
        )
        assert recipe_tensor.shape == (0, 12)
        assert recipe_tensor.dtype == np.float32
        assert is_enhanced.shape == (0,)
        assert metadata == []

    def test_single_base_roi_shape(self):
        """Single base ROI -> shape==(1,12), is_enhanced==[False], 1 metadata row."""
        from consumer.ops_recipe import generate_recipe_tensor

        batch = make_batch([{}])
        cfg = make_cfg()
        recipe_tensor, _recipe_keys, is_enhanced, _group_ids, metadata = generate_recipe_tensor(
            batch, cfg
        )
        assert recipe_tensor.shape == (1, 12)
        assert list(is_enhanced) == [False]
        assert len(metadata) == 1

    def test_single_enhanced_roi_shape(self):
        """Single enhanced ROI -> shape==(1,12), is_enhanced==[True], 1 metadata row."""
        from consumer.ops_recipe import generate_recipe_tensor

        batch = make_batch([], [{}])
        cfg = make_cfg()
        recipe_tensor, _recipe_keys, is_enhanced, _group_ids, metadata = generate_recipe_tensor(
            batch, cfg
        )
        assert recipe_tensor.shape == (1, 12)
        assert list(is_enhanced) == [True]
        assert len(metadata) == 1

    def test_mixed_batch_4_base_2_enhance(self):
        """4 base + 2 enhanced -> (6,12), is_enhanced order: base first."""
        from consumer.ops_recipe import generate_recipe_tensor

        batch = make_batch([{}] * 4, [{}] * 2)
        cfg = make_cfg()
        recipe_tensor, _recipe_keys, is_enhanced, _group_ids, _metadata = generate_recipe_tensor(
            batch, cfg
        )
        assert recipe_tensor.shape == (6, 12)
        assert list(is_enhanced) == [False, False, False, False, True, True]

    def test_reserved_slot_always_zero(self):
        """Reserved slot (index 2) is always 0 for all rows, even with varied inputs."""
        from consumer.ops_recipe import IDX_RESERVED, generate_recipe_tensor

        batch = make_batch(
            [{"homography_eligible": True}, {"luminance_mean": 60.0}],
            [{"low_contrast": True, "global_contrast": 30.0}],
        )
        cfg = make_cfg()
        recipe_tensor, *_ = generate_recipe_tensor(batch, cfg)
        assert np.all(recipe_tensor[:, IDX_RESERVED] == 0.0)

    def test_base_rows_clahe_always_zero(self):
        """Base ROIs always have clahe=0 even with low_contrast + enhance_eligible."""
        from consumer.ops_recipe import IDX_CLAHE, generate_recipe_tensor

        batch = make_batch(
            [
                {"low_contrast": True, "enhance_eligible": True, "global_contrast": 30.0},
                {"low_contrast": True, "enhance_eligible": True, "global_contrast": 20.0},
            ],
        )
        cfg = make_cfg()
        recipe_tensor, *_ = generate_recipe_tensor(batch, cfg)
        # All base rows should have clahe=0
        assert np.all(recipe_tensor[:, IDX_CLAHE] == 0.0)

    def test_all_values_within_recipe_ranges(self):
        """All recipe values clamped to RECIPE_RANGES for varied luminance inputs."""
        from consumer.ops_recipe import RECIPE_KEYS, RECIPE_RANGES, generate_recipe_tensor

        batch = make_batch(
            [{"luminance_mean": 60.0}, {"luminance_mean": 200.0}],
            [{"luminance_mean": 100.0, "low_contrast": True, "global_contrast": 30.0}],
        )
        cfg = make_cfg()
        recipe_tensor, *_ = generate_recipe_tensor(batch, cfg)
        for j, key in enumerate(RECIPE_KEYS):
            lo, hi = RECIPE_RANGES[key]
            col = recipe_tensor[:, j]
            assert np.all(col >= lo), f"{key}: min {col.min()} < {lo}"
            assert np.all(col <= hi), f"{key}: max {col.max()} > {hi}"

    def test_recipe_keys_returned(self):
        """Returned recipe_keys matches RECIPE_KEYS exactly."""
        from consumer.ops_recipe import RECIPE_KEYS, generate_recipe_tensor

        batch = make_batch([{}])
        cfg = make_cfg()
        _, recipe_keys, *_ = generate_recipe_tensor(batch, cfg)
        assert recipe_keys == RECIPE_KEYS

    def test_group_ids_passthrough(self):
        """Returned group_ids matches batch.duplicate_groups."""
        from consumer.ops_recipe import generate_recipe_tensor

        groups = {"group-0": [0, 2], "group-1": [1, 3]}
        batch = make_batch([{}] * 2, [{}] * 2, duplicate_groups=groups)
        cfg = make_cfg()
        _, _, _, group_ids, _ = generate_recipe_tensor(batch, cfg)
        assert group_ids == groups

    def test_deterministic_replay(self):
        """Same batch + cfg called twice produces byte-identical tensors."""
        from consumer.ops_recipe import generate_recipe_tensor

        batch = make_batch(
            [{"luminance_mean": 80.0}],
            [{"low_contrast": True, "global_contrast": 30.0}],
        )
        cfg = make_cfg()
        result1 = generate_recipe_tensor(batch, cfg)
        result2 = generate_recipe_tensor(batch, cfg)
        assert np.array_equal(result1[0], result2[0])

    def test_metadata_active_ops_populated(self):
        """Dark image -> 'gain' and 'gamma' in active_ops, primary_reason != 'none'."""
        from consumer.ops_recipe import generate_recipe_tensor

        batch = make_batch([{"luminance_mean": 80.0}])
        cfg = make_cfg()
        _, _, _, _, metadata = generate_recipe_tensor(batch, cfg)
        assert len(metadata) == 1
        row = metadata[0]
        assert "gain" in row.active_ops
        assert "gamma" in row.active_ops
        assert row.primary_reason != "none"

    def test_batch_summary_logged(self, caplog: pytest.LogCaptureFixture):
        """DEBUG log contains batch size, base count, and enhanced count."""
        from consumer.ops_recipe import generate_recipe_tensor

        batch = make_batch([{}] * 3, [{}] * 2)
        cfg = make_cfg()
        with caplog.at_level(logging.DEBUG):
            generate_recipe_tensor(batch, cfg)
        assert "5" in caplog.text
        assert "base" in caplog.text.lower()
        assert "enhanced" in caplog.text.lower()
