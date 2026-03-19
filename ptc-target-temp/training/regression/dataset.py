"""PlateCornerRegressionDataset — loads crops, returns coordinate targets."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import cv2
import numpy as np
import torch
from torch.utils.data import Dataset

from common.geometry import order_keypoints_by_angle
from training.config import AugmentationPhase

logger = logging.getLogger(__name__)

# Conditional import for augmentation
try:
    from training.augmentations import apply_augmentation
except ImportError:

    def apply_augmentation(  # type: ignore[misc]
        img: np.ndarray,
        corners: list[tuple[float, float]],
        phase: AugmentationPhase,
        cfg: object,
        *,
        is_night: bool = False,
        max_retries: int = 10,
        aug_ramp_factor: float = 1.0,
    ) -> tuple[np.ndarray, list[tuple[float, float]]] | None:
        """No-op stub until augmentation module is available."""
        return img, corners


# ── Letterbox (copied locally for full isolation) ─────────────────────


def letterbox_crop(
    crop: np.ndarray,
    target_h: int,
    target_w: int,
    pad_color: tuple[int, int, int],
) -> tuple[np.ndarray, float, float, float]:
    """Letterbox-resize crop preserving aspect ratio with padding."""
    h, w = crop.shape[:2]
    scale = min(target_w / w, target_h / h)
    new_w = int(round(w * scale))
    new_h = int(round(h * scale))

    resized = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    pad_x = (target_w - new_w) / 2.0
    pad_y = (target_h - new_h) / 2.0

    top = int(round(pad_y - 0.1))
    bottom = int(round(pad_y + 0.1))
    left = int(round(pad_x - 0.1))
    right = int(round(pad_x + 0.1))

    letterboxed = cv2.copyMakeBorder(
        resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=pad_color
    )

    return letterboxed, scale, pad_x, pad_y


def corners_to_letterbox(
    corners: list[tuple[float, float]],
    scale: float,
    pad_x: float,
    pad_y: float,
) -> list[tuple[float, float]]:
    """Convert crop-space corners to letterbox-space coordinates."""
    return [(x * scale + pad_x, y * scale + pad_y) for x, y in corners]


# ── Dataset ──────────────────────────────────────────────────────────


class PlateCornerRegressionDataset(Dataset):  # type: ignore[type-arg]
    """PyTorch dataset for plate corner regression training.

    Returns (image_tensor, coord_target, idx) where coord_target is
    (8,) tensor [x0, y0, x1, y1, x2, y2, x3, y3] in letterbox pixel coords.
    """

    def __init__(self, cfg: object, split: str = "train") -> None:
        self._cfg = cfg
        self._split = split
        self._aug_phase = AugmentationPhase.NONE
        self._aug_ramp_factor: float = 1.0

        # Load annotations
        with open(cfg.annotations_path) as f:  # type: ignore[attr-defined]
            self._annotations: dict[str, list[list[float]]] = json.load(f)

        # Load split and filter
        with open(cfg.split_path) as f:  # type: ignore[attr-defined]
            splits: dict[str, list[str]] = json.load(f)

        self._image_ids: list[str] = splits[split]

        # Optional lighting labels for night sim gating
        self._lighting_labels: dict[str, str] = {}
        if cfg.lighting_labels_path and Path(cfg.lighting_labels_path).exists():  # type: ignore[attr-defined]
            with open(cfg.lighting_labels_path) as f:  # type: ignore[attr-defined]
                self._lighting_labels = json.load(f)

        logger.info(
            "PlateCornerRegressionDataset loaded: split=%s, images=%d",
            split,
            len(self._image_ids),
        )

    def __len__(self) -> int:
        return len(self._image_ids)

    def set_augmentation_phase(self, phase: AugmentationPhase) -> None:
        """Set the current augmentation phase."""
        self._aug_phase = phase
        logger.info("augmentation phase set to %s", phase.name)

    def set_aug_ramp_factor(self, factor: float) -> None:
        """Set the augmentation ramp factor (0.0 = no aug, 1.0 = full aug)."""
        self._aug_ramp_factor = factor

    def is_night(self, filename: str) -> bool:
        """Check if image is a night image based on lighting labels."""
        return self._lighting_labels.get(filename, "") == "night"

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor, int]:
        cfg = self._cfg
        image_id = self._image_ids[idx]

        # Load image
        img_path = Path(cfg.data_dir) / image_id  # type: ignore[attr-defined]
        if not img_path.exists():
            raise FileNotFoundError(f"Image not found: {img_path}")

        img = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"Failed to read image: {img_path}")

        # Load and canonicalize corners
        entry = self._annotations[image_id]
        raw_corners = entry["corners"] if isinstance(entry, dict) else entry
        corners: list[tuple[float, float]] = [(float(c[0]), float(c[1])) for c in raw_corners]
        corners = order_keypoints_by_angle(corners)

        # Augmentation
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
            img, cfg.input_h, cfg.input_w, cfg.pad_color  # type: ignore[attr-defined]
        )

        # Convert corners to letterbox space
        corners_lb = corners_to_letterbox(corners, scale, pad_x, pad_y)

        # Build coordinate target: [x0, y0, x1, y1, x2, y2, x3, y3]
        coord_target = torch.tensor(
            [c for xy in corners_lb for c in xy],
            dtype=torch.float32,
        )

        # Normalize image: uint8 -> float32/255, HWC -> CHW
        img_tensor = torch.from_numpy(letterboxed.astype(np.float32) / 255.0).permute(2, 0, 1)

        return img_tensor, coord_target, idx
