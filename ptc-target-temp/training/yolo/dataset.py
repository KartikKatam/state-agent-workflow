"""YOLOWeightedDataset — weighted sampling + drone augmentation injection.

Subclasses Ultralytics YOLODataset to add per-image weighted sampling
and drone augmentation injection in __getitem__.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from ultralytics.data import YOLODataset

logger = logging.getLogger(__name__)


class YOLOWeightedDataset(YOLODataset):
    """YOLO dataset with per-image weighted sampling and drone augmentations.

    Custom kwargs (sample_weights, drone_transform) are popped before
    passing to YOLODataset.__init__. Weighted sampling resamples the index
    in __getitem__ when weights are non-uniform.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Pop custom kwargs before super().__init__
        sample_weights = kwargs.pop("sample_weights", None)
        drone_transform = kwargs.pop("drone_transform", None)

        super().__init__(*args, **kwargs)

        # Store drone transform
        self._drone_transform = drone_transform

        # Initialize weights
        n = len(self) if hasattr(self, "labels") else 0
        if sample_weights is not None:
            self._sample_weights = np.asarray(sample_weights, dtype=np.float64)
        else:
            self._sample_weights = np.ones(max(n, 1), dtype=np.float64)

        self._probs = np.ones_like(self._sample_weights) / max(len(self._sample_weights), 1)
        self._update_sampling_probs()

    @property
    def sample_weights(self) -> np.ndarray:
        """Current sample weights."""
        return self._sample_weights

    @sample_weights.setter
    def sample_weights(self, weights: np.ndarray) -> None:
        """Update sample weights and recompute probabilities."""
        self._sample_weights = np.asarray(weights, dtype=np.float64)
        self._update_sampling_probs()

    def _update_sampling_probs(self) -> None:
        """Clip weights to [0.1, 10.0], normalize to probabilities summing to 1."""
        clipped = np.clip(self._sample_weights, 0.1, 10.0)
        total = clipped.sum()
        if total > 0:
            self._probs = clipped / total
        else:
            self._probs = np.ones_like(clipped) / len(clipped)

    def __getitem__(self, index: int) -> dict[str, Any]:
        """Get item with optional weighted resampling and drone augmentation."""
        # Resample index when weights are non-uniform
        if not np.allclose(self._sample_weights, 1.0):
            index = int(np.random.choice(len(self._probs), p=self._probs))

        result = super().__getitem__(index)

        # Apply drone augmentations if available
        if self._drone_transform is not None and hasattr(self._drone_transform, "transforms"):
            if len(self._drone_transform.transforms) > 0:
                result = self._apply_drone_augmentations(result)

        return result

    def _apply_drone_augmentations(self, result: dict[str, Any]) -> dict[str, Any]:
        """Apply drone augmentations to the result dict.

        Returns original result if all bboxes are dropped or on exception.
        """
        try:
            img = result["img"]
            # Convert bboxes from xyxy/xywh to the format expected by albumentations
            bboxes = result.get("bboxes", np.empty((0, 4)))
            cls = result.get("cls", np.empty((0,)))

            if isinstance(bboxes, np.ndarray) and len(bboxes) > 0:
                bbox_list = bboxes.tolist()
            else:
                bbox_list = []

            if isinstance(cls, np.ndarray) and len(cls) > 0:
                class_labels = cls.flatten().astype(int).tolist()
            else:
                class_labels = []

            transformed = self._drone_transform(
                image=img,
                bboxes=bbox_list,
                class_labels=class_labels,
            )

            # If all bboxes were dropped, return original
            if len(bbox_list) > 0 and len(transformed["bboxes"]) == 0:
                logger.debug("all bboxes dropped by drone augmentation, keeping original")
                return result

            result["img"] = transformed["image"]
            if len(transformed["bboxes"]) > 0:
                result["bboxes"] = np.array(transformed["bboxes"], dtype=np.float32)
                result["cls"] = np.array(transformed["class_labels"], dtype=np.float32).reshape(
                    -1, 1
                )

        except Exception:
            logger.debug("drone augmentation failed, keeping original", exc_info=True)

        return result
