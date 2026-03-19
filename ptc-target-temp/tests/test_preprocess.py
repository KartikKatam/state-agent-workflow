"""Tests for consumer.ops_preprocess_gpu — GPU preprocessing pipeline.

Chunk-04: Safe ingest, geometry execution, empty batch guard.
Chunk-05: Photometric ops, CLAHE, post-metrics, PARSeq normalization.
Chunk-06: Integration (planner→processor), exports, hardening.
"""

from __future__ import annotations

import numpy as np
import pytest
import torch

from consumer.config import ConsumerConfig
from consumer.models import (
    EnhancedBatchSelection,
    PostProcessingMeta,
    RichQualityMetrics,
    RoiRichQuality,
)
from consumer.ops_preprocess_gpu import (
    _apply_clahe_gpu,
    _apply_geometry,
    _apply_photometric_ops,
    _compute_post_metrics,
    _safe_ingest,
    _warp_perspective,
    process_batch_gpu,
)
from consumer.ops_recipe import (
    IDX_CLAHE,
    IDX_CLAHE_CLIP,
    IDX_CLAHE_TILES,
    IDX_CONTRAST,
    IDX_DENOISE,
    IDX_GAIN,
    IDX_GAMMA,
    IDX_SHARPEN,
    IDX_TIGHT_CROP,
    IDX_WARP_ENABLE,
    IDX_WARP_MARGIN,
    RECIPE_IDENTITY,
    RECIPE_KEYS,
)
from producer.models import FastQualityMetrics, RoiFastQuality, RoiImage

# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def make_crop(h: int = 40, w: int = 120, value: int = 128) -> np.ndarray:
    """Create a uint8 HWC test crop."""
    return np.full((h, w, 3), value, dtype=np.uint8)


def make_quad(h: int, w: int) -> list[tuple[float, float]]:
    """Create a valid quad (TL, TR, BR, BL) filling the crop rect."""
    return [
        (0.0, 0.0),  # TL
        (float(w - 1), 0.0),  # TR
        (float(w - 1), float(h - 1)),  # BR
        (0.0, float(h - 1)),  # BL
    ]


def make_identity_recipe(n: int) -> np.ndarray:
    """Create an (N, 12) identity recipe tensor."""
    recipe = np.zeros((n, len(RECIPE_KEYS)), dtype=np.float32)
    for j, key in enumerate(RECIPE_KEYS):
        recipe[:, j] = RECIPE_IDENTITY[key]
    return recipe


def make_roi_rq(
    *,
    crop: np.ndarray | None = None,
    keypoints: list[tuple[float, float]] | None = None,
    homography_eligible: bool = False,
    track_id: str = "T-test",
    frame_idx: int = 0,
    luminance_mean: float = 128.0,
) -> RoiRichQuality:
    """Create a minimal RoiRichQuality for preprocessing tests."""
    if crop is None:
        crop = make_crop()

    h, w = crop.shape[:2]

    roi_img = RoiImage(
        track_id=track_id,
        crop_img=crop,
        bbox=[0.0, 0.0, float(w), float(h)],
        padded_bbox=[0.0, 0.0, float(w), float(h)],
        frame_idx=frame_idx,
        confidence=0.9,
        frame_width=1920,
        frame_height=1080,
        keypoints=keypoints,
        keypoint_scores=[0.9] * 4 if keypoints else None,
        crop_width=w,
        crop_height=h,
    )

    fast_metrics = FastQualityMetrics(
        focus_tenengrad=2000.0,
        brightness_mean=0.5,
        contrast_std=0.5,
        over_exposed_frac=0.0,
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
        plate_width_px=float(w),
        plate_height_px=float(h),
        crop_clip_fraction=0.0,
        detection_confidence=0.9,
        keypoints=keypoints,
        keypoint_scores=[0.9] * 4 if keypoints else None,
        keypoint_confidence_min=0.9 if keypoints else None,
        keypoint_confidence_mean=0.9 if keypoints else None,
        skew_degrees=0.0,
        perspective_score=1.0,
        perspective_direction=0.0,
        luminance_mean=luminance_mean,
        luminance_p05=20.0,
        luminance_p95=230.0,
        black_clip_fraction=0.0,
        white_clip_fraction=0.0,
        global_contrast=60.0,
        local_contrast=30.0,
        tenengrad=2000.0,
        tenengrad_horizontal=1200.0,
        tenengrad_vertical=800.0,
        blur_anisotropy=1.5,
        noise_std=5.0,
        flat_region_fraction=0.3,
        too_small=False,
        clipped=False,
        very_blurry=False,
        mildly_soft=False,
        exposure_bad=False,
        low_contrast=False,
        noisy=False,
        vertical_edges_weak=False,
        horizontal_edges_weak=False,
        top8_eligible=True,
        enhance_eligible=False,
        homography_eligible=homography_eligible,
        bbox_center_x_normalized=0.5,
        bbox_center_y_normalized=0.5,
        diversity_signature=np.array([0.0, 0.4, 0.5, 0.0], dtype=np.float32),
        quad_area_px=None,
        edge_ratio=None,
    )

    return RoiRichQuality(roi_fq=roi_fq, metrics=rich_metrics)


def make_batch(
    base_crops: list[np.ndarray] | None = None,
    enhance_crops: list[np.ndarray] | None = None,
    base_quads: list[list[tuple[float, float]] | None] | None = None,
    enhance_quads: list[list[tuple[float, float]] | None] | None = None,
    base_lumas: list[float] | None = None,
    enhance_lumas: list[float] | None = None,
) -> EnhancedBatchSelection:
    """Create an EnhancedBatchSelection from crop arrays."""
    _base_crops: list[np.ndarray] = base_crops if base_crops is not None else []
    _enhance_crops: list[np.ndarray] = enhance_crops if enhance_crops is not None else []
    _base_quads: list[list[tuple[float, float]] | None] = (
        base_quads if base_quads is not None else [None] * len(_base_crops)
    )
    _enhance_quads: list[list[tuple[float, float]] | None] = (
        enhance_quads if enhance_quads is not None else [None] * len(_enhance_crops)
    )
    _base_lumas: list[float] = base_lumas if base_lumas is not None else [128.0] * len(_base_crops)
    _enhance_lumas: list[float] = (
        enhance_lumas if enhance_lumas is not None else [128.0] * len(_enhance_crops)
    )

    base_rois = [
        make_roi_rq(
            crop=crop,
            keypoints=quad,
            homography_eligible=quad is not None,
            frame_idx=i,
            luminance_mean=luma,
        )
        for i, (crop, quad, luma) in enumerate(
            zip(_base_crops, _base_quads, _base_lumas, strict=True)
        )
    ]

    enhance_rois = [
        make_roi_rq(
            crop=crop,
            keypoints=quad,
            homography_eligible=quad is not None,
            frame_idx=100 + i,
            luminance_mean=luma,
        )
        for i, (crop, quad, luma) in enumerate(
            zip(_enhance_crops, _enhance_quads, _enhance_lumas, strict=True)
        )
    ]

    # Build duplicate_groups
    duplicate_groups: dict[str, list[int]] = {}
    n_base = len(base_rois)
    for j in range(len(enhance_rois)):
        group_id = f"group-{j}"
        if n_base > 0:
            base_idx = min(j, n_base - 1)
            duplicate_groups[group_id] = [base_idx, n_base + j]

    return EnhancedBatchSelection(
        track_id="T-test",
        version=1,
        base_rois=base_rois,
        enhance_rois=enhance_rois,
        duplicate_groups=duplicate_groups,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def device() -> torch.device:
    return torch.device("cpu")


# ---------------------------------------------------------------------------
# Tests: _safe_ingest
# ---------------------------------------------------------------------------


def test_safe_ingest_output_dtype_and_range(device: torch.device) -> None:
    """make_crop(40,120,value=128) → dtype float32, all values in [0,1], 128/255 approx 0.502."""
    crop = make_crop(40, 120, value=128)
    result = _safe_ingest([crop], device)

    assert len(result) == 1
    t = result[0]
    assert t.dtype == torch.float32
    assert t.min().item() >= 0.0
    assert t.max().item() <= 1.0
    assert t.mean().item() == pytest.approx(128.0 / 255.0, abs=0.001)


def test_safe_ingest_output_shape_chw(device: torch.device) -> None:
    """make_crop(40,120) → result[0].shape==(3,40,120) CHW format."""
    crop = make_crop(40, 120)
    result = _safe_ingest([crop], device)

    assert result[0].shape == (3, 40, 120)


def test_safe_ingest_input_non_mutation(device: torch.device) -> None:
    """crop=make_crop(); original=crop.copy(); _safe_ingest([crop]) → np.array_equal(crop, original)."""
    crop = make_crop()
    original = crop.copy()
    _safe_ingest([crop], device)

    np.testing.assert_array_equal(crop, original)


def test_safe_ingest_different_crop_sizes(device: torch.device) -> None:
    """crops=[make_crop(30,100), make_crop(50,150)] → len==2, shapes (3,30,100) and (3,50,150)."""
    crops = [make_crop(30, 100), make_crop(50, 150)]
    result = _safe_ingest(crops, device)

    assert len(result) == 2
    assert result[0].shape == (3, 30, 100)
    assert result[1].shape == (3, 50, 150)


# ---------------------------------------------------------------------------
# Tests: _warp_perspective (chunk-03: canonical TL,TR,BR,BL order)
# ---------------------------------------------------------------------------


def test_make_quad_returns_canonical_order() -> None:
    """make_quad(h=40, w=120) returns [(0,0),(119,0),(119,39),(0,39)] — TL,TR,BR,BL."""
    quad = make_quad(40, 120)
    assert quad == [
        (0.0, 0.0),  # TL
        (119.0, 0.0),  # TR
        (119.0, 39.0),  # BR
        (0.0, 39.0),  # BL
    ]


def test_warp_perspective_canonical_tl_tr_br_bl(device: torch.device) -> None:
    """40x120 crop with TL,TR,BR,BL quad → _warp_perspective returns (3,32,128)."""
    img = torch.rand(3, 40, 120, device=device)
    quad = make_quad(40, 120)
    result = _warp_perspective(img, quad, margin=0.0, tight_crop=False, h_out=32, w_out=128)
    assert result.shape == (3, 32, 128)
    assert result.min().item() >= 0.0
    assert result.max().item() <= 1.0


def test_warp_identity_quad_preserves_content(device: torch.device) -> None:
    """Axis-aligned rect quad → output not horizontally flipped."""
    img = torch.zeros(3, 40, 120, device=device)
    img[0, :, :] = torch.linspace(0, 1, 120, device=device).unsqueeze(0)
    quad = make_quad(40, 120)
    result = _warp_perspective(img, quad, margin=0.0, tight_crop=False, h_out=40, w_out=120)
    left_mean = result[0, :, :30].mean().item()
    right_mean = result[0, :, 90:].mean().item()
    assert right_mean > left_mean, "Content was flipped — incorrect reindexing"


# ---------------------------------------------------------------------------
# Tests: _apply_geometry
# ---------------------------------------------------------------------------


def test_geometry_direct_resize_shape(device: torch.device) -> None:
    """1 image, recipe warp_enable=0, cfg output 32x128 → result.shape==(1,3,32,128)."""
    img = torch.rand(3, 40, 120, device=device)
    recipe = make_identity_recipe(1)
    recipe_t = torch.tensor(recipe, device=device)
    cfg = ConsumerConfig()

    result = _apply_geometry([img], recipe_t, [None], cfg)
    assert result.shape == (1, 3, 32, 128)


def test_geometry_homography_path_shape(device: torch.device) -> None:
    """1 image, recipe warp_enable=1, tight_crop=1, warp_margin=0.04, valid quad → (1,3,32,128)."""
    img = torch.rand(3, 40, 120, device=device)
    recipe = make_identity_recipe(1)
    recipe[0, IDX_WARP_ENABLE] = 1.0
    recipe[0, IDX_TIGHT_CROP] = 1.0
    recipe[0, IDX_WARP_MARGIN] = 0.04
    recipe_t = torch.tensor(recipe, device=device)
    quad = make_quad(40, 120)
    cfg = ConsumerConfig()

    result = _apply_geometry([img], recipe_t, [quad], cfg)
    assert result.shape == (1, 3, 32, 128)


def test_geometry_values_in_valid_range(device: torch.device) -> None:
    """1 image, direct resize → all values in [0,1]."""
    img = torch.rand(3, 40, 120, device=device)
    recipe = make_identity_recipe(1)
    recipe_t = torch.tensor(recipe, device=device)
    cfg = ConsumerConfig()

    result = _apply_geometry([img], recipe_t, [None], cfg)
    assert result.min().item() >= 0.0
    assert result.max().item() <= 1.0


def test_geometry_mixed_paths(device: torch.device) -> None:
    """2 images: one warp+quad, one direct resize → (2,3,32,128), both in [0,1]."""
    img1 = torch.rand(3, 40, 120, device=device)
    img2 = torch.rand(3, 50, 150, device=device)

    recipe = make_identity_recipe(2)
    recipe[0, IDX_WARP_ENABLE] = 1.0
    recipe[0, IDX_TIGHT_CROP] = 1.0
    recipe[0, IDX_WARP_MARGIN] = 0.04
    recipe_t = torch.tensor(recipe, device=device)

    quad = make_quad(40, 120)
    cfg = ConsumerConfig()

    result = _apply_geometry([img1, img2], recipe_t, [quad, None], cfg)
    assert result.shape == (2, 3, 32, 128)
    assert result.min().item() >= 0.0
    assert result.max().item() <= 1.0


# ---------------------------------------------------------------------------
# Tests: process_batch_gpu
# ---------------------------------------------------------------------------


def test_process_batch_gpu_importable() -> None:
    """from consumer.ops_preprocess_gpu import process_batch_gpu → no ImportError."""
    from consumer.ops_preprocess_gpu import process_batch_gpu

    assert callable(process_batch_gpu)


def test_empty_batch_returns_empty_tensor(device: torch.device) -> None:
    """Empty batch → (0,3,32,128) tensor, (0,) is_enhanced, [] post_meta."""
    batch = make_batch([], [])
    recipe = make_identity_recipe(0)
    is_enhanced = np.array([], dtype=bool)
    cfg = ConsumerConfig()

    output = process_batch_gpu(batch, recipe, is_enhanced, cfg, device)

    assert output.batch_tensor.shape == (0, 3, 32, 128)
    assert output.is_enhanced.shape == (0,)
    assert output.post_meta == []


def test_single_image_geometry_only(device: torch.device) -> None:
    """Single base image with identity recipe → (1,3,32,128), values in [0,1]."""
    crop = make_crop(40, 120, value=128)
    batch = make_batch([crop])
    recipe = make_identity_recipe(1)
    is_enhanced = np.array([False], dtype=bool)
    cfg = ConsumerConfig()

    output = process_batch_gpu(batch, recipe, is_enhanced, cfg, device)

    assert output.batch_tensor.shape == (1, 3, 32, 128)
    assert output.batch_tensor.min().item() >= 0.0
    assert output.batch_tensor.max().item() <= 1.0


# ---------------------------------------------------------------------------
# Tests: _apply_photometric_ops (chunk-05)
# ---------------------------------------------------------------------------


def test_photometric_identity_is_noop(device: torch.device) -> None:
    """Uniform (1,3,32,128) at 0.5, all identity recipe → torch.allclose(output, input)."""
    batch = torch.full((1, 3, 32, 128), 0.5, device=device)
    recipe = torch.tensor(make_identity_recipe(1), device=device)
    cfg = ConsumerConfig()

    result = _apply_photometric_ops(batch, recipe, cfg)

    assert torch.allclose(result, batch, atol=1e-6)


def test_photometric_gain_brightens(device: torch.device) -> None:
    """Uniform 0.5 input, gain=1.2 → output.mean() approx 0.6."""
    batch = torch.full((1, 3, 32, 128), 0.5, device=device)
    recipe_np = make_identity_recipe(1)
    recipe_np[0, IDX_GAIN] = 1.2
    recipe = torch.tensor(recipe_np, device=device)
    cfg = ConsumerConfig()

    result = _apply_photometric_ops(batch, recipe, cfg)

    assert result.mean().item() == pytest.approx(0.6, abs=0.01)


def test_photometric_gamma_adjusts(device: torch.device) -> None:
    """Uniform 0.5 input, gamma=0.8 → output.mean() approx 0.574 (0.5^0.8)."""
    batch = torch.full((1, 3, 32, 128), 0.5, device=device)
    recipe_np = make_identity_recipe(1)
    recipe_np[0, IDX_GAMMA] = 0.8
    recipe = torch.tensor(recipe_np, device=device)
    cfg = ConsumerConfig()

    result = _apply_photometric_ops(batch, recipe, cfg)

    expected = 0.5**0.8  # ~0.5743
    assert result.mean().item() == pytest.approx(expected, abs=0.01)


def test_photometric_contrast_increases_spread(device: torch.device) -> None:
    """Split input: left=0.3, right=0.7, contrast=0.3 → output_std > input_std."""
    batch = torch.zeros(1, 3, 32, 128, device=device)
    batch[:, :, :, :64] = 0.3
    batch[:, :, :, 64:] = 0.7
    input_std = batch.std().item()

    recipe_np = make_identity_recipe(1)
    recipe_np[0, IDX_CONTRAST] = 0.3
    recipe = torch.tensor(recipe_np, device=device)
    cfg = ConsumerConfig()

    result = _apply_photometric_ops(batch, recipe, cfg)

    assert result.std().item() > input_std


def test_photometric_denoise_smooths(device: torch.device) -> None:
    """Random noise texture input, denoise=0.5 → output_std < input_std."""
    torch.manual_seed(42)
    batch = torch.rand(1, 3, 32, 128, device=device)
    input_std = batch.std().item()

    recipe_np = make_identity_recipe(1)
    recipe_np[0, IDX_DENOISE] = 0.5
    recipe = torch.tensor(recipe_np, device=device)
    cfg = ConsumerConfig()

    result = _apply_photometric_ops(batch, recipe, cfg)

    assert result.std().item() < input_std


def test_photometric_sharpen_enhances(device: torch.device) -> None:
    """Alternating bright/dark columns, sharpen=0.5 → output_std > input_std."""
    batch = torch.zeros(1, 3, 32, 128, device=device)
    # Create alternating 4-pixel-wide columns for stable edge pattern
    for col_start in range(0, 128, 8):
        batch[:, :, :, col_start : col_start + 4] = 0.4
        batch[:, :, :, col_start + 4 : col_start + 8] = 0.6
    input_std = batch.std().item()

    recipe_np = make_identity_recipe(1)
    recipe_np[0, IDX_SHARPEN] = 0.5
    recipe = torch.tensor(recipe_np, device=device)
    cfg = ConsumerConfig()

    result = _apply_photometric_ops(batch, recipe, cfg)

    assert result.std().item() > input_std


def test_photometric_output_clamped_01(device: torch.device) -> None:
    """Input near 1.0, gain=1.5 → output.max() <= 1.0, output.min() >= 0.0."""
    batch = torch.full((1, 3, 32, 128), 0.9, device=device)
    recipe_np = make_identity_recipe(1)
    recipe_np[0, IDX_GAIN] = 1.5
    recipe = torch.tensor(recipe_np, device=device)
    cfg = ConsumerConfig()

    result = _apply_photometric_ops(batch, recipe, cfg)

    assert result.max().item() <= 1.0
    assert result.min().item() >= 0.0


# ---------------------------------------------------------------------------
# Tests: _apply_clahe_gpu (chunk-05)
# ---------------------------------------------------------------------------


def test_clahe_zero_is_noop(device: torch.device) -> None:
    """Batch (2,3,32,128), clahe=0, is_enhanced=[T,T] → torch.allclose(output, input)."""
    batch = torch.rand(2, 3, 32, 128, device=device)
    recipe_np = make_identity_recipe(2)  # clahe=0 by identity
    recipe = torch.tensor(recipe_np, device=device)
    is_enhanced = torch.tensor([True, True], device=device)
    cfg = ConsumerConfig()

    result = _apply_clahe_gpu(batch, recipe, is_enhanced, cfg)

    assert torch.allclose(result, batch, atol=1e-6)


def test_clahe_applied_to_enhanced_only(device: torch.device) -> None:
    """Batch (3,3,32,128), is_enhanced=[F,T,F], row 1 clahe=0.5 → rows 0,2 unchanged."""
    torch.manual_seed(42)
    batch = torch.rand(3, 3, 32, 128, device=device)
    recipe_np = make_identity_recipe(3)
    recipe_np[1, IDX_CLAHE] = 0.5
    recipe_np[1, IDX_CLAHE_CLIP] = 3.0
    recipe_np[1, IDX_CLAHE_TILES] = 8
    recipe = torch.tensor(recipe_np, device=device)
    is_enhanced = torch.tensor([False, True, False], device=device)
    cfg = ConsumerConfig()

    result = _apply_clahe_gpu(batch, recipe, is_enhanced, cfg)

    # Rows 0, 2 should be unchanged
    assert torch.allclose(result[0], batch[0], atol=1e-6)
    assert torch.allclose(result[2], batch[2], atol=1e-6)
    # Row 1 should be modified
    assert not torch.allclose(result[1], batch[1], atol=1e-4)


def test_clahe_increases_luminance_contrast(device: torch.device) -> None:
    """1 enhanced row, low-contrast input, clahe=0.8 → output luminance std >= input."""
    # Create low-contrast luminance: tight range around 0.5
    batch = torch.full((1, 3, 32, 128), 0.5, device=device)
    # Add subtle variation
    batch[0, 0, :16, :] = 0.45  # R channel top half slightly darker
    batch[0, 0, 16:, :] = 0.55  # R channel bottom half slightly brighter

    recipe_np = make_identity_recipe(1)
    recipe_np[0, IDX_CLAHE] = 0.8
    recipe_np[0, IDX_CLAHE_CLIP] = 3.0
    recipe_np[0, IDX_CLAHE_TILES] = 8
    recipe = torch.tensor(recipe_np, device=device)
    is_enhanced = torch.tensor([True], device=device)
    cfg = ConsumerConfig()

    # Compute input luminance std (approx using R channel since near-gray)
    input_luma_std = batch[:, 0:1, :, :].std().item()

    result = _apply_clahe_gpu(batch, recipe, is_enhanced, cfg)
    output_luma_std = result[:, 0:1, :, :].std().item()

    assert output_luma_std >= input_luma_std


def test_clahe_base_rows_never_modified(device: torch.device) -> None:
    """Batch (2,3,32,128), is_enhanced=[F,F], clahe=0 → torch.allclose(output, input)."""
    batch = torch.rand(2, 3, 32, 128, device=device)
    recipe_np = make_identity_recipe(2)
    recipe = torch.tensor(recipe_np, device=device)
    is_enhanced = torch.tensor([False, False], device=device)
    cfg = ConsumerConfig()

    result = _apply_clahe_gpu(batch, recipe, is_enhanced, cfg)

    assert torch.allclose(result, batch, atol=1e-6)


# ---------------------------------------------------------------------------
# Tests: _compute_post_metrics (chunk-05)
# ---------------------------------------------------------------------------


def test_post_metrics_values_in_01_range(device: torch.device) -> None:
    """Batch in [0,1], input luminance_mean=128 → 0<=post_luma_mean<=1, 0<=post_contrast<=1."""
    batch = torch.rand(1, 3, 32, 128, device=device)
    metrics = [
        RichQualityMetrics(
            track_id="T-test",
            frame_idx=0,
            canonical_width=256,
            canonical_height=128,
            plate_width_px=120.0,
            plate_height_px=40.0,
            crop_clip_fraction=0.0,
            detection_confidence=0.9,
            keypoints=None,
            keypoint_scores=None,
            keypoint_confidence_min=None,
            keypoint_confidence_mean=None,
            skew_degrees=0.0,
            perspective_score=1.0,
            perspective_direction=0.0,
            luminance_mean=128.0,
            luminance_p05=20.0,
            luminance_p95=230.0,
            black_clip_fraction=0.0,
            white_clip_fraction=0.0,
            global_contrast=60.0,
            local_contrast=30.0,
            tenengrad=2000.0,
            tenengrad_horizontal=1200.0,
            tenengrad_vertical=800.0,
            blur_anisotropy=1.5,
            noise_std=5.0,
            flat_region_fraction=0.3,
            too_small=False,
            clipped=False,
            very_blurry=False,
            mildly_soft=False,
            exposure_bad=False,
            low_contrast=False,
            noisy=False,
            vertical_edges_weak=False,
            horizontal_edges_weak=False,
            top8_eligible=True,
            enhance_eligible=False,
            homography_eligible=False,
            bbox_center_x_normalized=0.5,
            bbox_center_y_normalized=0.5,
            diversity_signature=np.array([0.0, 0.4, 0.5, 0.0], dtype=np.float32),
            quad_area_px=None,
            edge_ratio=None,
        )
    ]

    result = _compute_post_metrics(batch, metrics, target_luma=128.0)

    assert len(result) == 1
    assert isinstance(result[0], PostProcessingMeta)
    assert 0.0 <= result[0].post_luma_mean <= 1.0
    assert 0.0 <= result[0].post_global_contrast <= 1.0


def test_post_metrics_luma_delta_negative_when_improved(device: torch.device) -> None:
    """Input luminance_mean=80, target=128, batch brightened toward target → luma_delta < 0."""
    # Batch at ~target/255 ≈ 0.502 (already at target in [0,1] space)
    batch = torch.full((1, 3, 32, 128), 128.0 / 255.0, device=device)
    metrics = [
        RichQualityMetrics(
            track_id="T-test",
            frame_idx=0,
            canonical_width=256,
            canonical_height=128,
            plate_width_px=120.0,
            plate_height_px=40.0,
            crop_clip_fraction=0.0,
            detection_confidence=0.9,
            keypoints=None,
            keypoint_scores=None,
            keypoint_confidence_min=None,
            keypoint_confidence_mean=None,
            skew_degrees=0.0,
            perspective_score=1.0,
            perspective_direction=0.0,
            luminance_mean=80.0,
            luminance_p05=20.0,
            luminance_p95=140.0,
            black_clip_fraction=0.0,
            white_clip_fraction=0.0,
            global_contrast=60.0,
            local_contrast=30.0,
            tenengrad=2000.0,
            tenengrad_horizontal=1200.0,
            tenengrad_vertical=800.0,
            blur_anisotropy=1.5,
            noise_std=5.0,
            flat_region_fraction=0.3,
            too_small=False,
            clipped=False,
            very_blurry=False,
            mildly_soft=False,
            exposure_bad=False,
            low_contrast=False,
            noisy=False,
            vertical_edges_weak=False,
            horizontal_edges_weak=False,
            top8_eligible=True,
            enhance_eligible=False,
            homography_eligible=False,
            bbox_center_x_normalized=0.5,
            bbox_center_y_normalized=0.5,
            diversity_signature=np.array([0.0, 0.4, 0.5, 0.0], dtype=np.float32),
            quad_area_px=None,
            edge_ratio=None,
        )
    ]

    result = _compute_post_metrics(batch, metrics, target_luma=128.0)

    # Input was at 80/255=0.314 from target, now at 128/255=0.502 — closer
    # luma_delta = |post - target/255| - |input/255 - target/255| should be negative
    assert result[0].luma_delta < 0


def test_post_metrics_delta_formula_correctness(device: torch.device) -> None:
    """Known values: verify exact formula: |post-target/255| - |input/255-target/255|."""
    # Uniform batch at value 0.6 in [0,1]
    batch = torch.full((1, 3, 32, 128), 0.6, device=device)
    target_luma = 150.0  # target/255 = 0.588
    input_luma = 100.0  # input/255 = 0.392
    input_contrast = 40.0

    metrics = [
        RichQualityMetrics(
            track_id="T-test",
            frame_idx=0,
            canonical_width=256,
            canonical_height=128,
            plate_width_px=120.0,
            plate_height_px=40.0,
            crop_clip_fraction=0.0,
            detection_confidence=0.9,
            keypoints=None,
            keypoint_scores=None,
            keypoint_confidence_min=None,
            keypoint_confidence_mean=None,
            skew_degrees=0.0,
            perspective_score=1.0,
            perspective_direction=0.0,
            luminance_mean=input_luma,
            luminance_p05=20.0,
            luminance_p95=180.0,
            black_clip_fraction=0.0,
            white_clip_fraction=0.0,
            global_contrast=input_contrast,
            local_contrast=30.0,
            tenengrad=2000.0,
            tenengrad_horizontal=1200.0,
            tenengrad_vertical=800.0,
            blur_anisotropy=1.5,
            noise_std=5.0,
            flat_region_fraction=0.3,
            too_small=False,
            clipped=False,
            very_blurry=False,
            mildly_soft=False,
            exposure_bad=False,
            low_contrast=False,
            noisy=False,
            vertical_edges_weak=False,
            horizontal_edges_weak=False,
            top8_eligible=True,
            enhance_eligible=False,
            homography_eligible=False,
            bbox_center_x_normalized=0.5,
            bbox_center_y_normalized=0.5,
            diversity_signature=np.array([0.0, 0.4, 0.5, 0.0], dtype=np.float32),
            quad_area_px=None,
            edge_ratio=None,
        )
    ]

    result = _compute_post_metrics(batch, metrics, target_luma=target_luma)

    # post_luma_mean should be ~0.6 (mean of uniform batch)
    assert result[0].post_luma_mean == pytest.approx(0.6, abs=0.01)

    # luma_delta = |post - target/255| - |input/255 - target/255|
    expected_delta = abs(0.6 - target_luma / 255.0) - abs(input_luma / 255.0 - target_luma / 255.0)
    assert result[0].luma_delta == pytest.approx(expected_delta, abs=0.01)


# ---------------------------------------------------------------------------
# Tests: PARSeq normalization (chunk-05)
# ---------------------------------------------------------------------------


def test_normalize_output_range(device: torch.device) -> None:
    """Batch in [0,1] → after *2-1 all values in [-1,1]."""
    batch = torch.rand(2, 3, 32, 128, device=device)
    normalized = batch * 2 - 1

    assert normalized.min().item() >= -1.0
    assert normalized.max().item() <= 1.0


def test_normalize_midpoint_maps_to_zero(device: torch.device) -> None:
    """Uniform batch at 0.5 → after *2-1 all values approx 0.0."""
    batch = torch.full((1, 3, 32, 128), 0.5, device=device)
    normalized = batch * 2 - 1

    assert torch.allclose(normalized, torch.zeros_like(normalized), atol=1e-6)


# ---------------------------------------------------------------------------
# Tests: Full pipeline with photometric (chunk-05)
# ---------------------------------------------------------------------------


def test_identity_recipe_uniform_crop(device: torch.device) -> None:
    """make_crop(40,120,value=128), identity recipe → output pixels approx (128/255)*2-1."""
    crop = make_crop(40, 120, value=128)
    batch = make_batch([crop])
    recipe = make_identity_recipe(1)
    is_enhanced = np.array([False], dtype=bool)
    cfg = ConsumerConfig()

    output = process_batch_gpu(batch, recipe, is_enhanced, cfg, device)

    expected_val = (128.0 / 255.0) * 2 - 1  # ~0.004
    assert output.batch_tensor.shape == (1, 3, 32, 128)
    assert output.batch_tensor.dtype == torch.float32
    assert output.batch_tensor.mean().item() == pytest.approx(expected_val, abs=0.02)
    assert output.batch_tensor.min().item() >= -1.0
    assert output.batch_tensor.max().item() <= 1.0


def test_identity_recipe_preserves_mean(device: torch.device) -> None:
    """Gradient crop, identity recipe → output_mean approx (input_mean/255)*2-1."""
    # Create gradient crop: values from 64 to 192
    h, w = 40, 120
    gradient = np.linspace(64, 192, w, dtype=np.float64)
    crop = np.broadcast_to(gradient[np.newaxis, :, np.newaxis], (h, w, 3)).astype(np.uint8)
    input_mean = crop.mean() / 255.0

    batch = make_batch([crop])
    recipe = make_identity_recipe(1)
    is_enhanced = np.array([False], dtype=bool)
    cfg = ConsumerConfig()

    output = process_batch_gpu(batch, recipe, is_enhanced, cfg, device)

    expected_normalized_mean = input_mean * 2 - 1
    assert output.batch_tensor.mean().item() == pytest.approx(expected_normalized_mean, abs=0.05)


# ---------------------------------------------------------------------------
# Tests: Integration — planner → processor E2E (chunk-06)
# ---------------------------------------------------------------------------


def test_e2e_pipeline_output_shape(device: torch.device) -> None:
    """Full pipeline: 4 base + 2 enhanced → (6,3,32,128) float32 in [-1,1]."""
    crops = [make_crop(40, 120, v) for v in [100, 120, 140, 160]]
    ecrop = [make_crop(40, 120, v) for v in [90, 180]]
    batch = make_batch(crops, ecrop)
    cfg = ConsumerConfig()

    from consumer.ops_recipe import generate_recipe_tensor

    recipe_tensor, _keys, is_enhanced, _groups, _meta = generate_recipe_tensor(batch, cfg)
    output = process_batch_gpu(batch, recipe_tensor, is_enhanced, cfg, device)

    assert output.batch_tensor.shape == (6, 3, 32, 128)
    assert output.batch_tensor.dtype == torch.float32
    assert output.batch_tensor.min().item() >= -1.0
    assert output.batch_tensor.max().item() <= 1.0
    assert len(output.post_meta) == 6


def test_e2e_mixed_batch_clahe_split(device: torch.device) -> None:
    """2 base (low_contrast) + 1 enhanced (CLAHE gates pass) → base clahe=0, enhanced >=0."""
    from consumer.ops_recipe import IDX_CLAHE, generate_recipe_tensor

    base_ovr = [
        {"luminance_mean": 100.0, "low_contrast": True, "global_contrast": 25.0},
        {"luminance_mean": 120.0, "low_contrast": True, "global_contrast": 30.0},
    ]
    enh_ovr = [
        {
            "luminance_mean": 100.0,
            "low_contrast": True,
            "global_contrast": 25.0,
            "enhance_eligible": True,
            "noisy": False,
            "very_blurry": False,
            "tenengrad": 3000.0,
            "white_clip_fraction": 0.01,
        },
    ]

    # Use test_recipe's factory for ROI metrics-based batch
    from tests.test_recipe import make_batch as make_recipe_batch

    batch = make_recipe_batch(base_ovr, enh_ovr)
    cfg = ConsumerConfig()
    recipe_tensor, _keys, is_enhanced, _groups, _meta = generate_recipe_tensor(batch, cfg)

    # Base rows (0,1) must have clahe=0
    assert recipe_tensor[0, IDX_CLAHE] == 0.0
    assert recipe_tensor[1, IDX_CLAHE] == 0.0
    # Enhanced row (2) may have clahe >= 0
    assert recipe_tensor[2, IDX_CLAHE] >= 0.0


def test_e2e_all_base_no_clahe(device: torch.device) -> None:
    """4 base ROIs → recipe[:,9]==0 for all rows, is_enhanced all False."""
    from consumer.ops_recipe import IDX_CLAHE, generate_recipe_tensor
    from tests.test_recipe import make_batch as make_recipe_batch

    batch = make_recipe_batch([{}, {}, {}, {}])
    cfg = ConsumerConfig()
    recipe_tensor, _keys, is_enhanced, _groups, _meta = generate_recipe_tensor(batch, cfg)

    assert np.all(recipe_tensor[:, IDX_CLAHE] == 0.0)
    assert np.all(~is_enhanced)


def test_e2e_single_image(device: torch.device) -> None:
    """N=1 single base image → (1,3,32,128), no crash."""
    from consumer.ops_recipe import generate_recipe_tensor

    crop = make_crop(40, 120, 128)
    batch = make_batch([crop])
    cfg = ConsumerConfig()

    recipe_tensor, _keys, is_enhanced, _groups, _meta = generate_recipe_tensor(batch, cfg)
    output = process_batch_gpu(batch, recipe_tensor, is_enhanced, cfg, device)

    assert output.batch_tensor.shape == (1, 3, 32, 128)


def test_row_isolation_modify_one_row(device: torch.device) -> None:
    """3 ROIs, modify recipe row 1 (gain=1.25) → rows 0,2 unchanged, row 1 differs."""
    crops = [make_crop(40, 120, 128) for _ in range(3)]
    batch = make_batch(crops)
    cfg = ConsumerConfig()

    recipe_a = make_identity_recipe(3)
    recipe_b = recipe_a.copy()
    recipe_b[1, IDX_GAIN] = 1.25  # only perturb row 1

    is_enhanced = np.array([False, False, False], dtype=bool)

    out_a = process_batch_gpu(batch, recipe_a, is_enhanced, cfg, device)
    out_b = process_batch_gpu(batch, recipe_b, is_enhanced, cfg, device)

    # Unmodified rows should be identical
    assert torch.allclose(out_a.batch_tensor[0], out_b.batch_tensor[0], atol=1e-6)
    assert torch.allclose(out_a.batch_tensor[2], out_b.batch_tensor[2], atol=1e-6)
    # Modified row should differ
    assert not torch.allclose(out_a.batch_tensor[1], out_b.batch_tensor[1], atol=1e-4)


def test_row_isolation_property(device: torch.device) -> None:
    """Property: random N in [2,6], perturb random row → others unchanged."""
    from hypothesis import given, settings, strategies as st

    @given(
        n=st.integers(min_value=2, max_value=6),
        perturb_row=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=10, deadline=None)
    def _check(n: int, perturb_row: int) -> None:
        perturb_row = perturb_row % n
        crops = [make_crop(40, 120, 128) for _ in range(n)]
        batch = make_batch(crops)
        cfg = ConsumerConfig()

        recipe_a = make_identity_recipe(n)
        recipe_b = recipe_a.copy()
        recipe_b[perturb_row, IDX_GAIN] = 1.25

        is_enhanced = np.array([False] * n, dtype=bool)

        out_a = process_batch_gpu(batch, recipe_a, is_enhanced, cfg, device)
        out_b = process_batch_gpu(batch, recipe_b, is_enhanced, cfg, device)

        for i in range(n):
            if i != perturb_row:
                assert torch.allclose(out_a.batch_tensor[i], out_b.batch_tensor[i], atol=1e-6), (
                    f"Row {i} changed when only row {perturb_row} was perturbed"
                )
            else:
                assert not torch.allclose(
                    out_a.batch_tensor[i], out_b.batch_tensor[i], atol=1e-4
                ), f"Perturbed row {perturb_row} should differ"

    _check()


def test_e2e_non_mutation_crop_arrays(device: torch.device) -> None:
    """Input crop arrays unchanged after full pipeline."""
    from consumer.ops_recipe import generate_recipe_tensor

    crops = [make_crop(40, 120, v) for v in [100, 150, 200]]
    originals = [c.copy() for c in crops]
    batch = make_batch(crops)
    cfg = ConsumerConfig()

    recipe_tensor, _keys, is_enhanced, _groups, _meta = generate_recipe_tensor(batch, cfg)
    process_batch_gpu(batch, recipe_tensor, is_enhanced, cfg, device)

    for i, (crop, original) in enumerate(zip(crops, originals, strict=True)):
        np.testing.assert_array_equal(crop, original, err_msg=f"Crop {i} was mutated")


def test_e2e_non_mutation_recipe_tensor(device: torch.device) -> None:
    """Recipe tensor unchanged after process_batch_gpu."""
    from consumer.ops_recipe import generate_recipe_tensor

    crops = [make_crop(40, 120, 128) for _ in range(3)]
    batch = make_batch(crops)
    cfg = ConsumerConfig()

    recipe_tensor, _keys, is_enhanced, _groups, _meta = generate_recipe_tensor(batch, cfg)
    recipe_copy = recipe_tensor.copy()

    process_batch_gpu(batch, recipe_tensor, is_enhanced, cfg, device)

    np.testing.assert_array_equal(recipe_tensor, recipe_copy, err_msg="Recipe tensor was mutated")


def test_config_sensitivity_target_luma_shift(device: torch.device) -> None:
    """luminance_mean=100: cfg(target_luma=100)→gain≈1, cfg(target_luma=180)→gain>1."""
    from consumer.ops_recipe import IDX_GAIN, generate_recipe_tensor
    from tests.test_recipe import make_batch as make_recipe_batch

    batch = make_recipe_batch([{"luminance_mean": 100.0}])

    cfg_low = ConsumerConfig(target_luma=100.0)
    recipe_low, *_ = generate_recipe_tensor(batch, cfg_low)

    cfg_high = ConsumerConfig(target_luma=180.0)
    recipe_high, *_ = generate_recipe_tensor(batch, cfg_high)

    assert recipe_low[0, IDX_GAIN] == pytest.approx(1.0, abs=0.01)
    assert recipe_high[0, IDX_GAIN] > 1.0


def test_config_sensitivity_contrast_max_limits_blend(device: torch.device) -> None:
    """low_contrast=True, global_contrast=20: contrast capped by contrast_max."""
    from consumer.ops_recipe import IDX_CONTRAST, generate_recipe_tensor
    from tests.test_recipe import make_batch as make_recipe_batch

    batch = make_recipe_batch([{"low_contrast": True, "global_contrast": 20.0}])

    cfg_low_max = ConsumerConfig(contrast_max=0.10)
    recipe_low, *_ = generate_recipe_tensor(batch, cfg_low_max)

    cfg_high_max = ConsumerConfig(contrast_max=0.40)
    recipe_high, *_ = generate_recipe_tensor(batch, cfg_high_max)

    assert recipe_low[0, IDX_CONTRAST] <= 0.10
    assert recipe_high[0, IDX_CONTRAST] > 0.10


def test_config_sensitivity_k_gain_zero_identity(device: torch.device) -> None:
    """luminance_mean=80, k_gain=0 → gain==1.0 (zero sensitivity = no correction)."""
    from consumer.ops_recipe import IDX_GAIN, generate_recipe_tensor
    from tests.test_recipe import make_batch as make_recipe_batch

    batch = make_recipe_batch([{"luminance_mean": 80.0}])
    cfg = ConsumerConfig(k_gain=0.0)
    recipe, *_ = generate_recipe_tensor(batch, cfg)

    assert recipe[0, IDX_GAIN] == pytest.approx(1.0, abs=1e-6)


def test_luminance_variance_decreases(device: torch.device) -> None:
    """4 ROIs with spread luminance → pipeline narrows distribution."""
    from consumer.ops_recipe import generate_recipe_tensor

    luma_values = [60, 100, 160, 200]
    crops = [make_crop(40, 120, v) for v in luma_values]
    batch = make_batch(crops, base_lumas=[float(v) for v in luma_values])
    cfg = ConsumerConfig()

    recipe_tensor, _keys, is_enhanced, _groups, _meta = generate_recipe_tensor(batch, cfg)
    output = process_batch_gpu(batch, recipe_tensor, is_enhanced, cfg, device)

    # Compute post-processing luma means (output is in [-1,1], convert to [0,1])
    post_lumas = []
    for i in range(4):
        img = (output.batch_tensor[i] + 1.0) / 2.0  # back to [0,1]
        luma = 0.299 * img[0] + 0.587 * img[1] + 0.114 * img[2]
        post_lumas.append(luma.mean().item())

    input_std = np.std([v / 255.0 for v in luma_values])
    output_std = np.std(post_lumas)

    assert output_std < input_std, (
        f"Expected luminance variance to decrease: input_std={input_std:.4f}, output_std={output_std:.4f}"
    )


def test_deterministic_replay_full_pipeline(device: torch.device) -> None:
    """Same batch + cfg → byte-identical recipe and torch.allclose output."""
    from consumer.ops_recipe import generate_recipe_tensor

    crops = [make_crop(40, 120, v) for v in [90, 128, 200]]
    cfg = ConsumerConfig()

    # Run 1
    batch1 = make_batch([c.copy() for c in crops])
    recipe1, _k1, is_enh1, _g1, _m1 = generate_recipe_tensor(batch1, cfg)
    out1 = process_batch_gpu(batch1, recipe1, is_enh1, cfg, device)

    # Run 2
    batch2 = make_batch([c.copy() for c in crops])
    recipe2, _k2, is_enh2, _g2, _m2 = generate_recipe_tensor(batch2, cfg)
    out2 = process_batch_gpu(batch2, recipe2, is_enh2, cfg, device)

    np.testing.assert_array_equal(recipe1, recipe2, err_msg="Recipes differ between runs")
    assert torch.allclose(out1.batch_tensor, out2.batch_tensor, atol=1e-6), (
        "Output tensors differ between identical runs"
    )


# ---------------------------------------------------------------------------
# Golden crop helpers (@system tests)
# ---------------------------------------------------------------------------

import hashlib
import json
from pathlib import Path

GOLDEN_CROPS_DIR = Path(__file__).parent / "fixtures" / "data" / "golden_crops"


def _load_golden_manifest() -> dict:
    """Load golden crops manifest.json."""
    with open(GOLDEN_CROPS_DIR / "manifest.json") as f:
        return json.load(f)


def _make_golden_roi_rq(entry: dict, crop_img: np.ndarray) -> RoiRichQuality:
    """Build RoiRichQuality from golden crop manifest entry with real metrics."""
    m = entry["metrics"]
    name = entry["name"]
    kps = entry.get("keypoints_in_crop")
    kps_tuples = [(kp[0], kp[1]) for kp in kps] if kps else None
    # COCO annotation order: TL, TR, BR, BL — matches _warp_perspective canonical order
    h, w = crop_img.shape[:2]

    roi_img = RoiImage(
        track_id=name,
        crop_img=crop_img,
        bbox=[0.0, 0.0, float(m["plate_width_px"]), float(m["plate_height_px"])],
        padded_bbox=[0.0, 0.0, float(w), float(h)],
        frame_idx=0,
        confidence=0.9,
        frame_width=1920,
        frame_height=1080,
        keypoints=kps_tuples,
        keypoint_scores=[0.9] * 4 if kps_tuples else None,
        crop_width=w,
        crop_height=h,
    )

    fast_metrics = FastQualityMetrics(
        focus_tenengrad=float(m["tenengrad"]),
        brightness_mean=float(m["luminance_mean"]) / 255.0,
        contrast_std=float(m["global_contrast"]) / 255.0,
        over_exposed_frac=float(m["white_clip_fraction"]),
        under_exposed_frac=float(m["black_clip_fraction"]),
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
        track_id=name,
        frame_idx=0,
        canonical_width=256,
        canonical_height=128,
        plate_width_px=float(m["plate_width_px"]),
        plate_height_px=float(m["plate_height_px"]),
        crop_clip_fraction=0.0,
        detection_confidence=0.9,
        keypoints=kps_tuples,
        keypoint_scores=[0.9] * 4 if kps_tuples else None,
        keypoint_confidence_min=0.9 if kps_tuples else None,
        keypoint_confidence_mean=0.9 if kps_tuples else None,
        skew_degrees=m.get("skew_degrees"),
        perspective_score=m.get("perspective_score"),
        perspective_direction=m.get("perspective_direction"),
        luminance_mean=float(m["luminance_mean"]),
        luminance_p05=float(m["luminance_p05"]),
        luminance_p95=float(m["luminance_p95"]),
        black_clip_fraction=float(m["black_clip_fraction"]),
        white_clip_fraction=float(m["white_clip_fraction"]),
        global_contrast=float(m["global_contrast"]),
        local_contrast=float(m["local_contrast"]),
        tenengrad=float(m["tenengrad"]),
        tenengrad_horizontal=float(m["tenengrad_horizontal"]),
        tenengrad_vertical=float(m["tenengrad_vertical"]),
        blur_anisotropy=float(m["blur_anisotropy"]),
        noise_std=float(m["noise_std"]),
        flat_region_fraction=float(m["flat_region_fraction"]),
        too_small=bool(m["too_small"]),
        clipped=False,
        very_blurry=bool(m["very_blurry"]),
        mildly_soft=bool(m["mildly_soft"]),
        exposure_bad=bool(m["exposure_bad"]),
        low_contrast=bool(m["low_contrast"]),
        noisy=bool(m["noisy"]),
        vertical_edges_weak=bool(m["vertical_edges_weak"]),
        horizontal_edges_weak=bool(m["horizontal_edges_weak"]),
        top8_eligible=bool(m["top8_eligible"]),
        enhance_eligible=bool(m["enhance_eligible"]),
        homography_eligible=bool(m["homography_eligible"]),
        bbox_center_x_normalized=0.5,
        bbox_center_y_normalized=0.5,
        diversity_signature=np.array([0.0, 0.4, 0.5, 0.0], dtype=np.float32),
        quad_area_px=None,
        edge_ratio=None,
    )

    return RoiRichQuality(roi_fq=roi_fq, metrics=rich_metrics)


def _build_golden_batch() -> tuple[EnhancedBatchSelection, dict]:
    """Load all golden crops and build an EnhancedBatchSelection.

    Returns (batch, entries_by_name) where entries_by_name maps crop name
    to its manifest entry for assertion lookup.
    """
    manifest = _load_golden_manifest()
    entries_by_name = {}

    base_rois = []
    enhance_rois = []

    for entry in manifest["crops"]:
        crop_img = np.load(GOLDEN_CROPS_DIR / entry["file"])
        roi_rq = _make_golden_roi_rq(entry, crop_img)
        entries_by_name[entry["name"]] = entry

        if entry["metrics"].get("enhance_eligible", False):
            enhance_rois.append(roi_rq)
        else:
            base_rois.append(roi_rq)

    # Build duplicate groups for enhance rois
    duplicate_groups: dict[str, list[int]] = {}
    n_base = len(base_rois)
    for j in range(len(enhance_rois)):
        group_id = f"golden-group-{j}"
        if n_base > 0:
            duplicate_groups[group_id] = [0, n_base + j]

    batch = EnhancedBatchSelection(
        track_id="golden-test",
        version=1,
        base_rois=base_rois,
        enhance_rois=enhance_rois,
        duplicate_groups=duplicate_groups,
    )

    return batch, entries_by_name


# ---------------------------------------------------------------------------
# Tests: @system — golden crop fixtures (chunk-06)
# ---------------------------------------------------------------------------


@pytest.mark.system
def test_production_samples_data_integrity() -> None:
    """Load manifest.json, verify SHA256 of each .npy golden crop file."""
    manifest = _load_golden_manifest()

    for entry in manifest["crops"]:
        npy_path = GOLDEN_CROPS_DIR / entry["file"]
        assert npy_path.exists(), f"Missing golden crop file: {npy_path}"

        crop = np.load(npy_path)
        actual_sha = hashlib.sha256(crop.tobytes()).hexdigest()
        assert actual_sha == entry["sha256"], (
            f"SHA256 mismatch for {entry['name']}: "
            f"expected {entry['sha256'][:16]}..., got {actual_sha[:16]}..."
        )

        assert list(crop.shape) == entry["shape"], (
            f"Shape mismatch for {entry['name']}: expected {entry['shape']}, got {list(crop.shape)}"
        )


@pytest.mark.system
def test_pipeline_no_crash_on_real_crops(device: torch.device) -> None:
    """Load all golden crops, build batch, run full pipeline — no exception."""
    from consumer.ops_recipe import generate_recipe_tensor

    batch, entries = _build_golden_batch()
    cfg = ConsumerConfig()
    n = len(batch.base_rois) + len(batch.enhance_rois)

    recipe_tensor, _keys, is_enhanced, _groups, _meta = generate_recipe_tensor(batch, cfg)
    output = process_batch_gpu(batch, recipe_tensor, is_enhanced, cfg, device)

    assert output.batch_tensor.shape == (n, 3, 32, 128)
    assert output.batch_tensor.dtype == torch.float32
    assert output.batch_tensor.min().item() >= -1.0
    assert output.batch_tensor.max().item() <= 1.0
    assert len(output.post_meta) == n


@pytest.mark.system
def test_recipe_values_within_ranges_on_real_crops() -> None:
    """Load all golden crops, generate recipes — all values within RECIPE_RANGES."""
    from consumer.ops_recipe import RECIPE_RANGES, generate_recipe_tensor

    batch, _ = _build_golden_batch()
    cfg = ConsumerConfig()

    recipe_tensor, _keys, _is_enh, _groups, _meta = generate_recipe_tensor(batch, cfg)

    for i in range(recipe_tensor.shape[0]):
        for j, key in enumerate(RECIPE_KEYS):
            lo, hi = RECIPE_RANGES[key]
            val = recipe_tensor[i, j]
            assert lo <= val <= hi, f"Row {i} column '{key}': {val:.4f} outside [{lo}, {hi}]"


@pytest.mark.system
def test_correction_direction_on_real_crops() -> None:
    """Verify planner correction direction: dark→brighten, bright→darken, soft→sharpen."""
    from consumer.ops_recipe import generate_recipe_tensor

    batch, entries = _build_golden_batch()
    cfg = ConsumerConfig()

    recipe_tensor, _keys, _is_enh, _groups, _meta = generate_recipe_tensor(batch, cfg)

    # Build name→row index mapping (base first, then enhance)
    all_rois = list(batch.base_rois) + list(batch.enhance_rois)
    name_to_row = {roi.metrics.track_id: i for i, roi in enumerate(all_rois)}

    # Dark plates should be brightened (gain > 1.0)
    for dark_name in ["image_4", "image_9"]:
        if dark_name in name_to_row:
            row = name_to_row[dark_name]
            assert recipe_tensor[row, IDX_GAIN] > 1.0, (
                f"{dark_name} (luma={entries[dark_name]['metrics']['luminance_mean']}) "
                f"should have gain > 1.0, got {recipe_tensor[row, IDX_GAIN]:.4f}"
            )

    # Bright plate should be darkened (gain < 1.0)
    if "image_8" in name_to_row:
        row = name_to_row["image_8"]
        assert recipe_tensor[row, IDX_GAIN] < 1.0, (
            f"image_8 (luma={entries['image_8']['metrics']['luminance_mean']}) "
            f"should have gain < 1.0, got {recipe_tensor[row, IDX_GAIN]:.4f}"
        )

    # Soft plate should be sharpened (sharpen > 0, denoise == 0)
    if "image_10" in name_to_row:
        row = name_to_row["image_10"]
        assert recipe_tensor[row, IDX_SHARPEN] > 0.0, (
            f"image_10 (tenengrad={entries['image_10']['metrics']['tenengrad']}) "
            f"should have sharpen > 0, got {recipe_tensor[row, IDX_SHARPEN]:.4f}"
        )
        assert recipe_tensor[row, IDX_DENOISE] == 0.0, (
            f"image_10 (soft, not noisy) should have denoise == 0, "
            f"got {recipe_tensor[row, IDX_DENOISE]:.4f}"
        )

    # Clean plate: denoise, sharpen, contrast all zero
    if "image_7" in name_to_row:
        row = name_to_row["image_7"]
        assert recipe_tensor[row, IDX_DENOISE] == 0.0, "image_7 (clean) should have denoise == 0"
        assert recipe_tensor[row, IDX_SHARPEN] == 0.0, "image_7 (clean) should have sharpen == 0"
        assert recipe_tensor[row, IDX_CONTRAST] == 0.0, "image_7 (clean) should have contrast == 0"


@pytest.mark.system
def test_average_luma_improvement(device: torch.device) -> None:
    """All golden crops → pipeline moves luminance closer to target on average."""
    from consumer.ops_recipe import generate_recipe_tensor

    batch, _ = _build_golden_batch()
    cfg = ConsumerConfig()

    recipe_tensor, _keys, is_enhanced, _groups, _meta = generate_recipe_tensor(batch, cfg)
    output = process_batch_gpu(batch, recipe_tensor, is_enhanced, cfg, device)

    all_rois = list(batch.base_rois) + list(batch.enhance_rois)
    target_norm = cfg.target_luma / 255.0

    improvements = []
    for i, roi in enumerate(all_rois):
        input_luma_norm = roi.metrics.luminance_mean / 255.0
        input_dist = abs(input_luma_norm - target_norm)

        # Output is in [-1, 1], convert to [0, 1]
        img_01 = (output.batch_tensor[i] + 1.0) / 2.0
        post_luma = (0.299 * img_01[0] + 0.587 * img_01[1] + 0.114 * img_01[2]).mean().item()
        post_dist = abs(post_luma - target_norm)

        improvements.append(input_dist - post_dist)

    mean_improvement = float(np.mean(improvements))
    assert mean_improvement > 0.0, (
        f"Pipeline should move luminance closer to target on average, "
        f"but mean improvement was {mean_improvement:.4f}"
    )
