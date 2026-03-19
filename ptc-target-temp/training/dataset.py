"""PlateCornerDataset — loads crops, generates heatmap + offset targets."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import cv2
import numpy as np
import torch
from torch.utils.data import Dataset, WeightedRandomSampler

from common.geometry import order_keypoints_by_angle
from training.config import AugmentationPhase, TrainingConfig

logger = logging.getLogger(__name__)

# Conditional import for augmentation (chunk-05 provides the real implementation)
try:
    from training.augmentations import apply_augmentation
except ImportError:

    def apply_augmentation(  # type: ignore[misc]
        img: np.ndarray,
        corners: list[tuple[float, float]],
        phase: AugmentationPhase,
        cfg: TrainingConfig,
        *,
        is_night: bool = False,
        max_retries: int = 10,
        aug_ramp_factor: float = 1.0,
    ) -> tuple[np.ndarray, list[tuple[float, float]]] | None:
        """No-op stub until chunk-05 provides real augmentation."""
        return img, corners


# ── Letterbox (matching inference exactly) ───────────────────────────


def letterbox_crop(
    crop: np.ndarray,
    target_h: int,
    target_w: int,
    pad_color: tuple[int, int, int],
) -> tuple[np.ndarray, float, float, float]:
    """Letterbox-resize crop preserving aspect ratio with padding.

    MUST match producer/ops_corner_cnn.py:125-156 EXACTLY for
    training/inference parity.

    Args:
        crop: BGR uint8 image (H, W, 3).
        target_h: Target height (e.g. 80).
        target_w: Target width (e.g. 256).
        pad_color: Border padding color.

    Returns:
        (letterboxed, scale, pad_x, pad_y)
    """
    h, w = crop.shape[:2]

    scale = min(target_w / w, target_h / h)
    new_w = int(round(w * scale))
    new_h = int(round(h * scale))

    resized = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    pad_x = (target_w - new_w) / 2.0
    pad_y = (target_h - new_h) / 2.0

    # +-0.1 rounding trick — CRITICAL for inference parity
    top = int(round(pad_y - 0.1))
    bottom = int(round(pad_y + 0.1))
    left = int(round(pad_x - 0.1))
    right = int(round(pad_x + 0.1))

    letterboxed = cv2.copyMakeBorder(
        resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=pad_color
    )

    return letterboxed, scale, pad_x, pad_y


# ── Corner coordinate conversion ────────────────────────────────────


def corners_to_letterbox(
    corners: list[tuple[float, float]],
    scale: float,
    pad_x: float,
    pad_y: float,
) -> list[tuple[float, float]]:
    """Convert crop-space corners to letterbox-space coordinates.

    Args:
        corners: List of (x, y) in original crop coordinates.
        scale: Letterbox scale factor.
        pad_x: Horizontal padding offset.
        pad_y: Vertical padding offset.

    Returns:
        List of (x_lb, y_lb) in letterbox coordinates.
    """
    return [(x * scale + pad_x, y * scale + pad_y) for x, y in corners]


# ── Heatmap target generation ────────────────────────────────────────


def generate_heatmap_target(
    corners_lb: list[tuple[float, float]],
    grid_h: int,
    grid_w: int,
    stride: int,
    sigma: float,
    floor: float,
) -> torch.Tensor:
    """Generate 4-channel Gaussian heatmap targets.

    Each channel has a Gaussian peak at the corresponding corner's
    grid position. Uses max (not addition) when Gaussians overlap.

    Args:
        corners_lb: 4 corners in letterbox pixel coordinates.
        grid_h: Heatmap height (input_h // stride).
        grid_w: Heatmap width (input_w // stride).
        stride: Downsampling stride.
        sigma: Gaussian sigma in heatmap-pixel units.
        floor: Minimum value threshold (clamp below to 0).

    Returns:
        Tensor of shape (4, grid_h, grid_w).

    Raises:
        ValueError: If sigma <= 0.
    """
    if sigma <= 0:
        raise ValueError(f"sigma must be positive, got {sigma}")

    heatmap = torch.zeros(4, grid_h, grid_w)

    # Optimization: only iterate within ~3*sigma radius
    radius = int(math.ceil(3.0 * sigma))

    for i, (x_lb, y_lb) in enumerate(corners_lb):
        gx = x_lb / stride  # float grid position
        gy = y_lb / stride

        # Compute integer center and clamp iteration bounds
        gx_center = int(round(gx))
        gy_center = int(round(gy))

        y_min = max(0, gy_center - radius)
        y_max = min(grid_h, gy_center + radius + 1)
        x_min = max(0, gx_center - radius)
        x_max = min(grid_w, gx_center + radius + 1)

        two_sigma_sq = 2.0 * sigma * sigma

        for gy_idx in range(y_min, y_max):
            for gx_idx in range(x_min, x_max):
                dx = gx_idx - gx
                dy = gy_idx - gy
                val = math.exp(-(dx * dx + dy * dy) / two_sigma_sq)
                if val >= floor:
                    heatmap[i, gy_idx, gx_idx] = max(heatmap[i, gy_idx, gx_idx].item(), val)

    return heatmap


# ── Offset target generation ─────────────────────────────────────────


def generate_offset_target(
    corners_lb: list[tuple[float, float]],
    grid_h: int,
    grid_w: int,
    stride: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Generate 8-channel offset targets and 4-channel binary masks.

    Channel layout: [dx0, dy0, dx1, dy1, dx2, dy2, dx3, dy3]
    for corners [TL, TR, BR, BL].

    Args:
        corners_lb: 4 corners in letterbox pixel coordinates.
        grid_h: Heatmap height.
        grid_w: Heatmap width.
        stride: Downsampling stride.

    Returns:
        (offset_target, offset_mask) both Tensors.
        offset_target: (8, grid_h, grid_w)
        offset_mask: (4, grid_h, grid_w)
    """
    offset_target = torch.zeros(8, grid_h, grid_w)
    offset_mask = torch.zeros(4, grid_h, grid_w)

    for i, (x_lb, y_lb) in enumerate(corners_lb):
        gx_int = int(math.floor(x_lb / stride))
        gy_int = int(math.floor(y_lb / stride))

        # Clamp to grid bounds
        gx_int = max(0, min(gx_int, grid_w - 1))
        gy_int = max(0, min(gy_int, grid_h - 1))

        # Sub-stride offset in [0, stride)
        dx = x_lb - gx_int * stride
        dy = y_lb - gy_int * stride

        offset_target[2 * i, gy_int, gx_int] = dx
        offset_target[2 * i + 1, gy_int, gx_int] = dy
        offset_mask[i, gy_int, gx_int] = 1.0

    return offset_target, offset_mask


# ── Dataset ──────────────────────────────────────────────────────────


class PlateCornerDataset(Dataset):  # type: ignore[type-arg]
    """PyTorch dataset for plate corner heatmap regression training.

    Loads crop images + JSON annotations, canonicalizes corner order,
    applies letterbox matching inference, generates heatmap + offset targets.

    Returns:
        (image, heatmap, offset_target, offset_mask) tensors.
    """

    def __init__(self, cfg: TrainingConfig, split: str = "train") -> None:
        self._cfg = cfg
        self._split = split
        self._aug_phase = AugmentationPhase.NONE
        self._aug_ramp_factor: float = 1.0
        self._current_sigma: float = cfg.gaussian_sigma

        # Load annotations
        with open(cfg.annotations_path) as f:
            self._annotations: dict[str, list[list[float]]] = json.load(f)

        # Load split and filter
        with open(cfg.split_path) as f:
            splits: dict[str, list[str]] = json.load(f)

        self._image_ids: list[str] = splits[split]

        # Load synthetic data if configured (train split only)
        self._synthetic_ids: list[str] = []
        self._synthetic_annotations: dict[str, dict[str, list[list[float]]]] = {}
        if split == "train" and cfg.synthetic_data_dir and cfg.synthetic_annotations_path:
            synth_dir = Path(cfg.synthetic_data_dir)
            synth_ann_path = Path(cfg.synthetic_annotations_path)
            if synth_dir.exists() and synth_ann_path.exists():
                with open(synth_ann_path) as f:
                    self._synthetic_annotations = json.load(f)
                self._synthetic_ids = [
                    sid
                    for sid in self._synthetic_annotations
                    if self._synthetic_annotations[sid].get("corners") is not None
                ]
                logger.info("Synthetic data loaded: %d images", len(self._synthetic_ids))

        # Optional lighting labels for night sim gating
        self._lighting_labels: dict[str, str] = {}
        if cfg.lighting_labels_path and Path(cfg.lighting_labels_path).exists():
            with open(cfg.lighting_labels_path) as f:
                self._lighting_labels = json.load(f)

        logger.info(
            "PlateCornerDataset loaded: split=%s, real=%d, synthetic=%d",
            split,
            len(self._image_ids),
            len(self._synthetic_ids),
        )

    def __len__(self) -> int:
        return len(self._image_ids) + len(self._synthetic_ids)

    def set_augmentation_phase(self, phase: AugmentationPhase) -> None:
        """Set the current augmentation phase."""
        self._aug_phase = phase
        logger.info("augmentation phase set to %s", phase.name)

    def set_aug_ramp_factor(self, factor: float) -> None:
        """Set the augmentation ramp factor (0.0 = no aug, 1.0 = full aug)."""
        self._aug_ramp_factor = factor

    def set_sigma(self, sigma: float) -> None:
        """Set the current Gaussian sigma for heatmap target generation."""
        self._current_sigma = sigma

    def is_night(self, filename: str) -> bool:
        """Check if image is a night image based on lighting labels."""
        return self._lighting_labels.get(filename, "") == "night"

    def __getitem__(
        self, idx: int
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, int]:
        cfg = self._cfg
        n_real = len(self._image_ids)

        # Determine if this index is real or synthetic
        if idx < n_real:
            image_id = self._image_ids[idx]
            img_path = Path(cfg.data_dir) / image_id
            entry = self._annotations[image_id]
        else:
            synth_idx = idx - n_real
            image_id = self._synthetic_ids[synth_idx]
            img_path = Path(cfg.synthetic_data_dir) / image_id
            entry = self._synthetic_annotations[image_id]

        # Load image
        if not img_path.exists():
            raise FileNotFoundError(f"Image not found: {img_path}")

        img = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"Failed to read image: {img_path}")

        # Load and canonicalize corners
        raw_corners = entry["corners"] if isinstance(entry, dict) else entry
        corners: list[tuple[float, float]] = [(float(c[0]), float(c[1])) for c in raw_corners]
        corners = order_keypoints_by_angle(corners)

        # Augmentation stub (chunk-05 provides real implementation)
        aug_result = apply_augmentation(
            img,
            corners,
            self._aug_phase,
            cfg,
            is_night=self.is_night(image_id),
            aug_ramp_factor=self._aug_ramp_factor,
        )
        if aug_result is not None:
            img, corners = aug_result

        # Letterbox (matching inference exactly)
        letterboxed, scale, pad_x, pad_y = letterbox_crop(
            img, cfg.input_h, cfg.input_w, cfg.pad_color
        )

        # Convert corners to letterbox space
        corners_lb = corners_to_letterbox(corners, scale, pad_x, pad_y)

        # Generate targets
        grid_h = cfg.input_h // cfg.stride
        grid_w = cfg.input_w // cfg.stride

        heatmap = generate_heatmap_target(
            corners_lb, grid_h, grid_w, cfg.stride, self._current_sigma, cfg.gaussian_floor
        )
        offset_target, offset_mask = generate_offset_target(corners_lb, grid_h, grid_w, cfg.stride)

        # Normalize image: uint8 -> float32/255, HWC -> CHW
        img_tensor = torch.from_numpy(letterboxed.astype(np.float32) / 255.0).permute(2, 0, 1)

        return img_tensor, heatmap, offset_target, offset_mask, idx


# ── Synthetic data sampler ─────────────────────────────────────────


def build_synthetic_sampler(
    dataset: PlateCornerDataset, cfg: TrainingConfig
) -> WeightedRandomSampler:
    """Build WeightedRandomSampler that enforces synthetic_ratio.

    Real images get weight (1 - synthetic_ratio), synthetic get synthetic_ratio.
    This ensures each batch has approximately the configured mix.
    """
    n_real = len(dataset._image_ids)
    n_synth = len(dataset._synthetic_ids)
    total = n_real + n_synth

    weights: list[float] = []
    for i in range(total):
        if i < n_real:
            weights.append(1.0 - cfg.synthetic_ratio)
        else:
            weights.append(cfg.synthetic_ratio)

    return WeightedRandomSampler(weights, num_samples=total, replacement=True)
