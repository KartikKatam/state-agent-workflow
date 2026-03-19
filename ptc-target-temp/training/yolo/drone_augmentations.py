"""Drone-perspective augmentation pipeline for YOLO fine-tuning.

Provides intensity-scaled Albumentations pipelines simulating drone flight conditions.
All 11 transforms (4 geometry + 7 degradation) are always present; their probabilities
and parameter strengths scale linearly with the ``intensity`` argument (0.0–1.0).

Backward-compatible ``build_drone_pipeline(cfg, phase)`` maps WARM/RAMP/REFINE to
intensity 0.0/1.0/0.5.  The continuous ``build_drone_pipeline_scaled(cfg, intensity)``
is used by the per-epoch curriculum scheduler.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import albumentations as A
import cv2

if TYPE_CHECKING:
    from training.yolo.config import YoloTrainingConfig

logger = logging.getLogger(__name__)


def build_drone_pipeline(cfg: YoloTrainingConfig, phase: str) -> A.Compose:
    """Build a phase-appropriate drone augmentation pipeline.

    Backward-compatible wrapper around :func:`build_drone_pipeline_scaled`.
    Maps discrete phase names to intensity values.

    Args:
        cfg: Training config with drone augmentation parameters.
        phase: One of "WARM", "RAMP", "REFINE".

    Returns:
        A.Compose with BboxParams configured for YOLO format.
    """
    intensity = {"WARM": 0.0, "RAMP": 1.0, "REFINE": 0.5}.get(phase, 0.0)
    return build_drone_pipeline_scaled(cfg, intensity)


def build_drone_pipeline_scaled(cfg: YoloTrainingConfig, intensity: float) -> A.Compose:
    """Build drone pipeline with probabilities and strengths scaled by intensity.

    Args:
        cfg: Training config with drone augmentation parameters.
        intensity: 0.0 = empty pipeline, 1.0 = full RAMP pipeline.
            Intermediate values scale both probabilities and geometric strengths.

    Returns:
        A.Compose with BboxParams configured for YOLO format.
    """
    bbox_params = A.BboxParams(
        format="yolo",
        label_fields=["class_labels"],
        min_visibility=0.3,
    )

    if not cfg.drone_aug_enabled or intensity <= 0.0:
        logger.debug(
            "drone augmentation off (enabled=%s, intensity=%.2f)", cfg.drone_aug_enabled, intensity
        )
        return A.Compose([], bbox_params=bbox_params)

    t = min(intensity, 1.0)
    transforms = _build_scaled_transforms(cfg, t)

    logger.debug("built scaled pipeline (intensity=%.2f) with %d transforms", t, len(transforms))
    return A.Compose(transforms, bbox_params=bbox_params)  # type: ignore[arg-type]


def _build_scaled_transforms(cfg: YoloTrainingConfig, t: float) -> list[A.BasicTransform]:
    """Build 11-transform pipeline with all probabilities and strengths scaled by *t*.

    At t=1.0 this produces the full RAMP pipeline.  At t=0.5 every probability
    and geometric range is halved, producing medium-difficulty augmentation.

    Args:
        cfg: Training config.
        t: Intensity in (0.0, 1.0].
    """
    return [
        # --- Drone Geometry ---
        # 1. Affine — oblique viewing angle (pitch via y-shear, yaw via x-shear, roll)
        A.Affine(
            shear={
                "x": _scale_range(cfg.drone_affine_shear_x, t),
                "y": _scale_range(cfg.drone_affine_shear_y, t),
            },
            rotate=_scale_range(cfg.drone_affine_rotate, t),
            fit_output=False,
            keep_ratio=True,
            border_mode=cv2.BORDER_CONSTANT,
            fill=0,
            p=cfg.drone_affine_prob * t,
        ),
        # 2. RandomScale — altitude variation
        A.RandomScale(
            scale_limit=cfg.drone_scale_limit * t,
            p=cfg.drone_scale_prob * t,
        ),
        # 3. Perspective — keystone distortion (dev-002: border_mode=BORDER_CONSTANT)
        A.Perspective(
            scale=(0.05 * t, max(0.01, cfg.drone_perspective_limit * t)),
            keep_size=True,
            border_mode=cv2.BORDER_CONSTANT,
            fill=0,
            p=cfg.drone_perspective_prob * t,
        ),
        # 4. CoarseDropout — roof/pillar occlusion
        A.CoarseDropout(
            max_holes=max(1, int(cfg.drone_occlusion_max_holes * t)),
            max_height=max(0.03, cfg.drone_occlusion_max_h_frac * t),
            max_width=max(0.05, cfg.drone_occlusion_max_w_frac * t),
            min_holes=1,
            min_height=0.03,
            min_width=0.05,
            fill=0,
            p=cfg.drone_occlusion_prob * t,
        ),
        # --- Sensor / Atmosphere Degradation ---
        # 5. OpticalDistortion — lens distortion (dev-003: try/except shift_limit)
        _make_optical_distortion(
            distort_limit=cfg.drone_distortion_limit * t,
            p=cfg.drone_distortion_prob * t,
        ),
        # 6. MotionBlur — flight motion
        A.MotionBlur(
            blur_limit=max(3, int(cfg.drone_motion_blur_limit * t)),
            p=cfg.drone_motion_blur_prob * t,
        ),
        # 7. Defocus — focus imperfections
        A.Defocus(
            radius=(
                cfg.drone_defocus_radius[0],
                max(cfg.drone_defocus_radius[0] + 1, int(cfg.drone_defocus_radius[1] * t)),
            ),
            p=cfg.drone_defocus_prob * t,
        ),
        # 8. GaussNoise — sensor noise
        A.GaussNoise(
            var_limit=(
                cfg.drone_noise_var_limit[0],
                max(cfg.drone_noise_var_limit[0] + 1, cfg.drone_noise_var_limit[1] * t),
            ),
            p=cfg.drone_noise_prob * t,
        ),
        # 9. ImageCompression — transmission compression (lower quality = harder)
        A.ImageCompression(
            quality_lower=int(
                cfg.drone_compression_quality[0]
                + (1.0 - t) * (cfg.drone_compression_quality[1] - cfg.drone_compression_quality[0])
            ),
            quality_upper=cfg.drone_compression_quality[1],
            p=cfg.drone_compression_prob * t,
        ),
        # 10. RandomFog — atmospheric haze
        A.RandomFog(
            fog_coef_lower=cfg.drone_fog_coef[0],
            fog_coef_upper=max(cfg.drone_fog_coef[0] + 0.01, cfg.drone_fog_coef[1] * t),
            alpha_coef=0.08,
            p=cfg.drone_fog_prob * t,
        ),
        # 11. RandomBrightnessContrast — lighting variation
        A.RandomBrightnessContrast(
            brightness_limit=max(0.05, cfg.drone_brightness_limit * t),
            p=cfg.drone_brightness_prob * t,
        ),
    ]


def _scale_range(range_tuple: tuple[int, int], t: float) -> tuple[int, int]:
    """Scale a symmetric range by intensity: (-25, 25) at t=0.5 → (-12, 12)."""
    lo, hi = range_tuple
    scaled_lo = int(lo * t)
    scaled_hi = int(hi * t)
    # Ensure at least ±1 range when t > 0
    if scaled_lo == 0 and lo != 0:
        scaled_lo = -1 if lo < 0 else 1
    if scaled_hi == 0 and hi != 0:
        scaled_hi = 1 if hi > 0 else -1
    return (scaled_lo, scaled_hi)


def _make_optical_distortion(distort_limit: float, p: float) -> A.OpticalDistortion:
    """Create OpticalDistortion with shift_limit guard (dev-003).

    Some albumentations versions removed shift_limit. Try with it first,
    fall back to without.
    """
    try:
        return A.OpticalDistortion(
            distort_limit=distort_limit,
            shift_limit=0.05,
            p=p,
        )
    except TypeError:
        logger.info("shift_limit not supported in this albumentations version, omitting")
        return A.OpticalDistortion(
            distort_limit=distort_limit,
            p=p,
        )
