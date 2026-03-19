"""LPRDetectionTrainer — custom YOLO trainer with weighted dataset + drone augmentations.

Subclasses Ultralytics DetectionTrainer to inject YOLOWeightedDataset
with drone augmentations via build_dataset() override. Supports dataloader
rebuild at curriculum phase transitions and live sample weight updates.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np
from ultralytics.models.yolo.detect import (
    DetectionTrainer,  # pyright: ignore[reportPrivateImportUsage]
)

from training.yolo.dataset import YOLOWeightedDataset
from training.yolo.drone_augmentations import build_drone_pipeline, build_drone_pipeline_scaled

if TYPE_CHECKING:
    from training.yolo.config import YoloTrainingConfig

logger = logging.getLogger(__name__)


class LPRDetectionTrainer(DetectionTrainer):
    """Detection trainer with per-image weighted sampling and drone augmentations.

    Overrides ``build_dataset`` to return :class:`YOLOWeightedDataset` for the
    training split, injects the phase-appropriate drone augmentation pipeline,
    and provides hooks for curriculum-driven phase transitions and mining weight
    updates.
    """

    def __init__(
        self,
        lpr_config: YoloTrainingConfig,
        overrides: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.lpr_config = lpr_config
        self.current_phase: str = "WARM"
        self._sample_weights: np.ndarray | None = None
        self._drone_transform: Any | None = None
        super().__init__(overrides=overrides or {}, **kwargs)

    # ------------------------------------------------------------------
    # Dataset override
    # ------------------------------------------------------------------

    def build_dataset(self, img_path: str, mode: str = "train", batch: int | None = None) -> Any:
        """Build dataset — returns YOLOWeightedDataset for train, default for val."""
        if mode != "train":
            return super().build_dataset(img_path, mode=mode, batch=batch)

        # Build drone pipeline for current phase
        self._drone_transform = build_drone_pipeline(self.lpr_config, self.current_phase)

        # Grid size from model stride (model may be None or a string path during early init)
        gs = max(int(self.model.stride.max() if hasattr(self.model, "stride") else 0), 32)  # type: ignore[union-attr]

        logger.debug(
            "build_dataset: phase=%s, gs=%d, img_path=%s",
            self.current_phase,
            gs,
            img_path,
        )

        return YOLOWeightedDataset(
            img_path=img_path,
            imgsz=self.args.imgsz,
            batch_size=batch,
            augment=True,
            hyp=self.args,
            rect=False,
            stride=gs,
            pad=0.5,
            prefix="",
            task=self.args.task,
            classes=self.args.classes,
            data=self.data,
            sample_weights=self._sample_weights,
            drone_transform=self._drone_transform,
        )

    # ------------------------------------------------------------------
    # Dataloader rebuild
    # ------------------------------------------------------------------

    def _close_dataloader_mosaic(self) -> None:
        """Re-inject custom augmentations after Ultralytics rebuilds the dataloader."""
        if not hasattr(self, "train_loader") or self.train_loader is None:
            return
        super()._close_dataloader_mosaic()

        if hasattr(self, "train_loader") and self.train_loader is not None:
            dataset = self.train_loader.dataset
            if isinstance(dataset, YOLOWeightedDataset):
                self._drone_transform = build_drone_pipeline(self.lpr_config, self.current_phase)
                dataset._drone_transform = self._drone_transform
                if self._sample_weights is not None:
                    dataset.sample_weights = self._sample_weights
                logger.debug(
                    "re-injected drone_transform (%d transforms) after rebuild",
                    len(self._drone_transform.transforms) if self._drone_transform else 0,
                )

    # ------------------------------------------------------------------
    # Augmentation intensity + phase transition API
    # ------------------------------------------------------------------

    def update_augmentation_intensity(self, intensity: float) -> None:
        """Update Ultralytics aug params and drone pipeline for the given intensity.

        Called every epoch by CurriculumScheduler. Interpolates between warm_aug
        and ramp_aug for Ultralytics params, and scales drone pipeline probabilities.
        """
        # Interpolate Ultralytics aug dict
        aug_dict = self.lpr_config.get_interpolated_aug(intensity)
        for key, value in aug_dict.items():
            setattr(self.args, key, value)

        # Rebuild drone pipeline with scaled probabilities
        self._drone_transform = build_drone_pipeline_scaled(self.lpr_config, intensity)

        # Propagate to live dataset if it exists
        if hasattr(self, "train_loader") and self.train_loader is not None:
            dataset = self.train_loader.dataset
            if isinstance(dataset, YOLOWeightedDataset):
                dataset._drone_transform = self._drone_transform

        logger.debug(
            "augmentation intensity=%.2f, drone transforms=%d",
            intensity,
            len(self._drone_transform.transforms) if self._drone_transform else 0,
        )

    def trigger_phase_transition(self, new_phase: str) -> None:
        """Backward-compatible: update for a discrete phase name.

        Delegates to :meth:`update_augmentation_intensity` with the phase's
        canonical intensity.
        """
        logger.info("phase transition: %s -> %s", self.current_phase, new_phase)
        self.current_phase = new_phase
        intensity = {"WARM": 0.0, "RAMP": 1.0, "REFINE": 0.5}.get(new_phase, 0.0)
        self.update_augmentation_intensity(intensity)

    def update_sample_weights(self, weights: np.ndarray) -> None:
        """Store new sample weights and propagate to the live dataset."""
        self._sample_weights = weights
        if hasattr(self, "train_loader") and self.train_loader is not None:
            dataset = self.train_loader.dataset
            if isinstance(dataset, YOLOWeightedDataset):
                dataset.sample_weights = weights
                logger.debug("updated sample weights on live dataset (%d images)", len(weights))
