from __future__ import annotations

import logging
import math

import numpy as np

from .config import ConsumerConfig
from .models import (
    EnhancedBatchSelection,
    RecipeRow,
    RichQualityMetrics,
    RoiRichQuality,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Recipe tensor schema constants (12-D, shared by planner + processor)
# ---------------------------------------------------------------------------

RECIPE_KEYS: list[str] = [
    "warp_enable",  # 0
    "tight_crop_enable",  # 1
    "reserved",  # 2  — was rotation; cut because partial rotation without
    #      perspective correction can introduce new distortions.
    #      Slot kept for schema stability. See design doc.
    "warp_margin",  # 3
    "gain",  # 4
    "gamma",  # 5
    "contrast",  # 6
    "denoise",  # 7
    "sharpen",  # 8
    "clahe",  # 9
    "clahe_clip",  # 10
    "clahe_tiles",  # 11
]

# Index constants for named access
IDX_WARP_ENABLE: int = 0
IDX_TIGHT_CROP: int = 1
IDX_RESERVED: int = 2
IDX_WARP_MARGIN: int = 3
IDX_GAIN: int = 4
IDX_GAMMA: int = 5
IDX_CONTRAST: int = 6
IDX_DENOISE: int = 7
IDX_SHARPEN: int = 8
IDX_CLAHE: int = 9
IDX_CLAHE_CLIP: int = 10
IDX_CLAHE_TILES: int = 11

# Identity values: applying these produces no visual change
RECIPE_IDENTITY: dict[str, float] = {
    "warp_enable": 0.0,
    "tight_crop_enable": 0.0,
    "reserved": 0.0,
    "warp_margin": 0.0,
    "gain": 1.0,
    "gamma": 1.0,
    "contrast": 0.0,
    "denoise": 0.0,
    "sharpen": 0.0,
    "clahe": 0.0,
    "clahe_clip": 2.0,
    "clahe_tiles": 8.0,
}

# Valid ranges per recipe column: (min, max)
RECIPE_RANGES: dict[str, tuple[float, float]] = {
    "warp_enable": (0.0, 1.0),
    "tight_crop_enable": (0.0, 1.0),
    "reserved": (0.0, 0.0),
    "warp_margin": (0.0, 0.10),
    "gain": (0.75, 1.25),
    "gamma": (0.70, 1.50),
    "contrast": (0.0, 0.40),
    "denoise": (0.0, 0.40),
    "sharpen": (0.0, 0.35),
    "clahe": (0.0, 1.0),
    "clahe_clip": (1.5, 4.0),
    "clahe_tiles": (6.0, 12.0),
}


# ---------------------------------------------------------------------------
# Sub-planner functions (chunk-02): pure metrics → recipe values
# ---------------------------------------------------------------------------


def plan_geometry(metrics: RichQualityMetrics, cfg: ConsumerConfig) -> tuple[float, float, float]:
    """Plan geometry correction: warp_enable, tight_crop_enable, warp_margin.

    Two paths: homography-eligible -> (1, 1, margin), else identity (0, 0, 0).
    """
    if metrics.homography_eligible:
        logger.debug("plan_geometry: homography eligible, warp_margin=%.3f", cfg.warp_margin)
        return (1.0, 1.0, cfg.warp_margin)
    logger.debug("plan_geometry: not homography eligible, identity")
    return (0.0, 0.0, 0.0)


def plan_exposure(metrics: RichQualityMetrics, cfg: ConsumerConfig) -> tuple[float, float]:
    """Plan exposure correction: gain, gamma.

    Formula: delta = (target - luma_mean) / luma_range
             gain  = clamp(1 + k_gain * delta,  gain_min,  gain_max)
             gamma = clamp(exp(-k_gamma * delta), gamma_min, gamma_max)

    Clipping guards: if brightening but white_clip too high, or
    darkening but black_clip too high, return identity.
    """
    delta = (cfg.target_luma - metrics.luminance_mean) / cfg.luma_range

    # Clipping guards
    if delta > 0 and metrics.white_clip_fraction >= cfg.white_clip_fraction_max:
        logger.debug(
            "plan_exposure: white clip guard, white_clip=%.3f >= %.3f",
            metrics.white_clip_fraction,
            cfg.white_clip_fraction_max,
        )
        return (1.0, 1.0)
    if delta < 0 and metrics.black_clip_fraction >= cfg.black_clip_fraction_max:
        logger.debug(
            "plan_exposure: black clip guard, black_clip=%.3f >= %.3f",
            metrics.black_clip_fraction,
            cfg.black_clip_fraction_max,
        )
        return (1.0, 1.0)

    raw_gain = 1.0 + cfg.k_gain * delta
    gain = max(cfg.gain_min, min(cfg.gain_max, raw_gain))

    raw_gamma = math.exp(-cfg.k_gamma * delta)
    gamma = max(cfg.gamma_min, min(cfg.gamma_max, raw_gamma))

    logger.debug("plan_exposure: delta=%.4f, gain=%.3f, gamma=%.3f", delta, gain, gamma)
    return (gain, gamma)


def plan_contrast(metrics: RichQualityMetrics, cfg: ConsumerConfig) -> float:
    """Plan contrast correction blend strength.

    If low_contrast: deficit = (target - actual) / target, proportional blend
    clamped to [0, contrast_max]. Else 0.
    """
    if not metrics.low_contrast:
        return 0.0

    deficit = (cfg.target_contrast - metrics.global_contrast) / cfg.target_contrast
    deficit = max(deficit, 0.0)
    result = min(cfg.k_contrast * deficit, cfg.contrast_max)
    logger.debug("plan_contrast: deficit=%.3f, result=%.3f", deficit, result)
    return result


def plan_denoise_sharpen(metrics: RichQualityMetrics, cfg: ConsumerConfig) -> tuple[float, float]:
    """Plan denoise/sharpen — mutually exclusive cascade.

    Priority: very_blurry -> (0,0), noisy -> (denoise,0),
              mildly_soft -> (0,sharpen), else (0,0).
    """
    if metrics.very_blurry:
        logger.debug("plan_denoise_sharpen: very_blurry, both zero")
        return (0.0, 0.0)

    if metrics.noisy:
        excess = (metrics.noise_std - cfg.target_noise_max) / cfg.target_noise_max
        excess = max(excess, 0.0)
        denoise = min(cfg.k_denoise * excess, cfg.denoise_max)
        logger.debug("plan_denoise_sharpen: noisy, excess=%.3f, denoise=%.3f", excess, denoise)
        return (denoise, 0.0)

    if metrics.mildly_soft:
        deficit = (cfg.target_sharpness - metrics.tenengrad) / cfg.target_sharpness
        deficit = max(deficit, 0.0)
        sharpen = min(cfg.k_sharpen * deficit, cfg.sharpen_max)
        logger.debug(
            "plan_denoise_sharpen: mildly_soft, deficit=%.3f, sharpen=%.3f",
            deficit,
            sharpen,
        )
        return (0.0, sharpen)

    return (0.0, 0.0)


def plan_clahe(
    metrics: RichQualityMetrics, is_enhanced: bool, cfg: ConsumerConfig
) -> tuple[float, float, int]:
    """Plan CLAHE parameters with 7-gate check.

    All gates must pass: is_enhanced, enhance_eligible, low_contrast,
    white_clip < threshold, not noisy, not very_blurry, adequate sharpness.
    Returns (clahe_blend, clahe_clip, clahe_tiles) — identity (0, clip_min, tiles_min)
    if any gate fails.
    """
    gates_passed: list[str] = []
    gates_failed: list[str] = []

    # Gate 1: is_enhanced
    (gates_passed if is_enhanced else gates_failed).append("is_enhanced")
    # Gate 2: enhance_eligible
    (gates_passed if metrics.enhance_eligible else gates_failed).append("enhance_eligible")
    # Gate 3: low_contrast
    (gates_passed if metrics.low_contrast else gates_failed).append("low_contrast")
    # Gate 4: white_clip below threshold
    wc_ok = metrics.white_clip_fraction < cfg.clahe_max_white_clip
    (gates_passed if wc_ok else gates_failed).append("white_clip")
    # Gate 5: not noisy
    (gates_passed if not metrics.noisy else gates_failed).append("not_noisy")
    # Gate 6: not very_blurry
    (gates_passed if not metrics.very_blurry else gates_failed).append("not_very_blurry")
    # Gate 7: adequate sharpness
    sharp_ok = metrics.tenengrad >= cfg.clahe_min_sharpness
    (gates_passed if sharp_ok else gates_failed).append("adequate_sharpness")

    if gates_failed:
        logger.debug("plan_clahe: gates failed: %s", ", ".join(gates_failed))
        return (0.0, cfg.clahe_clip_min, cfg.clahe_tiles_min)

    # All gates pass — compute proportional params
    deficit = (cfg.target_contrast - metrics.global_contrast) / cfg.target_contrast
    deficit = max(deficit, 0.0)

    clahe = min(deficit * cfg.clahe_blend_max, cfg.clahe_blend_max)
    clip = cfg.clahe_clip_min + deficit * (cfg.clahe_clip_max - cfg.clahe_clip_min)
    tiles = round(cfg.clahe_tiles_min + deficit * (cfg.clahe_tiles_max - cfg.clahe_tiles_min))

    logger.debug(
        "plan_clahe: all gates pass, deficit=%.3f, clahe=%.3f, clip=%.2f, tiles=%d",
        deficit,
        clahe,
        clip,
        tiles,
    )
    return (clahe, clip, tiles)


# ---------------------------------------------------------------------------
# Entry point: recipe tensor assembly (chunk-03)
# ---------------------------------------------------------------------------


def _determine_primary_reason(active_ops: list[str]) -> str:
    """Pick the single most important correction reason from active ops."""
    # Priority order: geometry > exposure > contrast > denoise > sharpen > clahe
    priority = [
        ("warp_enable", "perspective"),
        ("gain", "underexposed"),
        ("gamma", "underexposed"),
        ("contrast", "low_contrast"),
        ("denoise", "noisy"),
        ("sharpen", "soft"),
        ("clahe", "low_contrast_enhanced"),
    ]
    for op, reason in priority:
        if op in active_ops:
            return reason
    return "none"


def generate_recipe_tensor(
    batch: EnhancedBatchSelection,
    cfg: ConsumerConfig,
) -> tuple[np.ndarray, list[str], np.ndarray, dict[str, list[int]], list[RecipeRow]]:
    """Generate (N, 12) recipe tensor from an EnhancedBatchSelection.

    Concatenates base + enhanced ROIs (base first), dispatches sub-planners
    per ROI, assembles tensor with clamping, and emits RecipeRow metadata.

    Returns:
        recipe_tensor: (N, 12) float32 — per-row recipe values clamped to RECIPE_RANGES
        recipe_keys: list[str] length 12 — column names
        is_enhanced: (N,) bool ndarray — True for enhanced ROIs
        group_ids: dict — passthrough of batch.duplicate_groups
        metadata: list[RecipeRow] length N — per-row diagnostics
    """
    # Concatenate base + enhanced ROIs (base first)
    all_rois: list[RoiRichQuality] = list(batch.base_rois) + list(batch.enhance_rois)
    n = len(all_rois)

    # Build is_enhanced array
    n_base = len(batch.base_rois)
    is_enhanced = np.array([False] * n_base + [True] * len(batch.enhance_rois), dtype=bool)

    # Handle empty batch
    if n == 0:
        recipe_tensor = np.zeros((0, 12), dtype=np.float32)
        logger.debug("generate_recipe_tensor: empty batch, returning (0,12) tensor")
        return recipe_tensor, RECIPE_KEYS, is_enhanced, batch.duplicate_groups, []

    # Allocate tensor
    recipe_tensor = np.zeros((n, 12), dtype=np.float32)
    metadata: list[RecipeRow] = []

    for i, roi in enumerate(all_rois):
        m = roi.metrics
        enh = bool(is_enhanced[i])

        # Dispatch sub-planners
        warp_enable, tight_crop, warp_margin = plan_geometry(m, cfg)
        gain, gamma = plan_exposure(m, cfg)
        contrast = plan_contrast(m, cfg)
        denoise, sharpen = plan_denoise_sharpen(m, cfg)
        clahe, clahe_clip, clahe_tiles = plan_clahe(m, enh, cfg)

        # Fill row: [warp_enable, tight_crop, reserved, warp_margin,
        #            gain, gamma, contrast, denoise, sharpen,
        #            clahe, clahe_clip, clahe_tiles]
        row = [
            warp_enable,
            tight_crop,
            0.0,  # reserved — always 0
            warp_margin,
            gain,
            gamma,
            contrast,
            denoise,
            sharpen,
            clahe,
            clahe_clip,
            float(clahe_tiles),
        ]

        # Clamp to RECIPE_RANGES
        for j, key in enumerate(RECIPE_KEYS):
            lo, hi = RECIPE_RANGES[key]
            row[j] = max(lo, min(hi, row[j]))

        recipe_tensor[i] = row

        # Compute metadata
        active_ops = [key for j, key in enumerate(RECIPE_KEYS) if row[j] != RECIPE_IDENTITY[key]]
        primary_reason = _determine_primary_reason(active_ops)

        luma_deficit = (cfg.target_luma - m.luminance_mean) / cfg.luma_range
        contrast_deficit = (
            (cfg.target_contrast - m.global_contrast) / cfg.target_contrast
            if cfg.target_contrast > 0
            else 0.0
        )
        sharpness_deficit = (
            (cfg.target_sharpness - m.tenengrad) / cfg.target_sharpness
            if cfg.target_sharpness > 0
            else 0.0
        )
        noise_excess = (
            (m.noise_std - cfg.target_noise_max) / cfg.target_noise_max
            if cfg.target_noise_max > 0
            else 0.0
        )

        # Collect gate info from CLAHE (most complex gating)
        gates_passed: list[str] = []
        gates_failed: list[str] = []
        gate_checks = [
            ("is_enhanced", enh),
            ("enhance_eligible", m.enhance_eligible),
            ("low_contrast", m.low_contrast),
            ("white_clip", m.white_clip_fraction < cfg.clahe_max_white_clip),
            ("not_noisy", not m.noisy),
            ("not_very_blurry", not m.very_blurry),
            ("adequate_sharpness", m.tenengrad >= cfg.clahe_min_sharpness),
        ]
        for gate_name, passed in gate_checks:
            (gates_passed if passed else gates_failed).append(gate_name)

        metadata.append(
            RecipeRow(
                luma_deficit=luma_deficit,
                contrast_deficit=contrast_deficit,
                sharpness_deficit=sharpness_deficit,
                noise_excess=noise_excess,
                gates_passed=gates_passed,
                gates_failed=gates_failed,
                active_ops=active_ops,
                primary_reason=primary_reason,
            )
        )

    # Batch summary logging
    active_distribution: dict[str, int] = {}
    for row_meta in metadata:
        for op in row_meta.active_ops:
            active_distribution[op] = active_distribution.get(op, 0) + 1
    logger.debug(
        "generate_recipe_tensor: %d images (%d base, %d enhanced), active ops: %s",
        n,
        n_base,
        n - n_base,
        active_distribution,
    )

    return recipe_tensor, RECIPE_KEYS, is_enhanced, batch.duplicate_groups, metadata
