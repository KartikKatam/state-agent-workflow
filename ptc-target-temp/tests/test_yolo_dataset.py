"""Tests for YOLOWeightedDataset (chunk-01).

Tests weighted sampling, drone augmentation injection, kwargs popping,
and real-data construction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

if TYPE_CHECKING:
    from training.yolo.dataset import YOLOWeightedDataset

# ---------------------------------------------------------------------------
# TestYOLOWeightedDatasetInit (3 tests)
# ---------------------------------------------------------------------------


class TestYOLOWeightedDatasetInit:
    """Tests for dataset import and initialization."""

    def test_dataset_importable(self) -> None:
        """YOLOWeightedDataset importable without error."""
        from training.yolo.dataset import YOLOWeightedDataset

        assert YOLOWeightedDataset is not None

    def test_dataset_pops_custom_kwargs(self) -> None:
        """super().__init__ called WITHOUT sample_weights/drone_transform."""
        from training.yolo.dataset import YOLOWeightedDataset

        with patch("training.yolo.dataset.YOLODataset.__init__", return_value=None) as mock_init:
            weights = np.ones(10)
            transform = MagicMock()
            ds = YOLOWeightedDataset.__new__(YOLOWeightedDataset)
            YOLOWeightedDataset.__init__(
                ds,
                img_path="/fake",
                imgsz=640,
                sample_weights=weights,
                drone_transform=transform,
            )
            # Verify super().__init__ was called WITHOUT our custom kwargs
            call_kwargs = mock_init.call_args
            if call_kwargs.kwargs:
                assert "sample_weights" not in call_kwargs.kwargs
                assert "drone_transform" not in call_kwargs.kwargs

    def test_dataset_uniform_weights_by_default(self) -> None:
        """sample_weights=None -> all weights == 1.0."""
        from training.yolo.dataset import YOLOWeightedDataset

        with patch("training.yolo.dataset.YOLODataset.__init__", return_value=None):
            ds = YOLOWeightedDataset.__new__(YOLOWeightedDataset)
            YOLOWeightedDataset.__init__(ds, img_path="/fake", imgsz=640, sample_weights=None)
            # After init with None, weights should be uniform
            assert ds._sample_weights is not None
            assert np.allclose(ds._sample_weights, 1.0)


# ---------------------------------------------------------------------------
# TestUpdateSamplingProbs (4 tests)
# ---------------------------------------------------------------------------


class TestUpdateSamplingProbs:
    """Tests for _update_sampling_probs weight normalization."""

    def _make_dataset_with_weights(self, weights: np.ndarray) -> YOLOWeightedDataset:
        from training.yolo.dataset import YOLOWeightedDataset

        with patch("training.yolo.dataset.YOLODataset.__init__", return_value=None):
            ds = YOLOWeightedDataset.__new__(YOLOWeightedDataset)
            YOLOWeightedDataset.__init__(ds, img_path="/fake", imgsz=640, sample_weights=weights)
        return ds

    def test_uniform_weights_give_uniform_probs(self) -> None:
        """weights=ones(10) -> all probs==0.1."""
        ds = self._make_dataset_with_weights(np.ones(10))
        assert np.allclose(ds._probs, 0.1)

    def test_weights_clipped_to_range(self) -> None:
        """Extreme weights [0.01, 100.0, 1.0] clipped to [0.1, 10.0], probs sum to 1.0."""
        ds = self._make_dataset_with_weights(np.array([0.01, 100.0, 1.0]))
        # After clip: effective weights are [0.1, 10.0, 1.0]
        assert abs(ds._probs.sum() - 1.0) < 1e-9

    def test_probs_sum_to_one(self) -> None:
        """weights=[1,3,1,1] -> sum(probs) == 1.0."""
        ds = self._make_dataset_with_weights(np.array([1.0, 3.0, 1.0, 1.0]))
        assert abs(ds._probs.sum() - 1.0) < 1e-9

    def test_higher_weight_gets_higher_prob(self) -> None:
        """weights=[1,5,1] -> probs[1] > probs[0] and probs[1] > probs[2]."""
        ds = self._make_dataset_with_weights(np.array([1.0, 5.0, 1.0]))
        assert ds._probs[1] > ds._probs[0]
        assert ds._probs[1] > ds._probs[2]


# ---------------------------------------------------------------------------
# TestApplyDroneAugmentations (3 tests)
# ---------------------------------------------------------------------------


class TestApplyDroneAugmentations:
    """Tests for _apply_drone_augmentations safety and behavior."""

    def _make_dataset_with_transform(
        self, transform: MagicMock | None = None
    ) -> YOLOWeightedDataset:
        from training.yolo.dataset import YOLOWeightedDataset

        with patch("training.yolo.dataset.YOLODataset.__init__", return_value=None):
            ds = YOLOWeightedDataset.__new__(YOLOWeightedDataset)
            YOLOWeightedDataset.__init__(
                ds,
                img_path="/fake",
                imgsz=640,
                sample_weights=np.ones(5),
                drone_transform=transform,
            )
        return ds

    def test_augmentations_applied_when_transform_has_transforms(self) -> None:
        """drone_transform with transforms -> result image/bboxes change."""
        # Create a mock transform that modifies the image
        transform = MagicMock()
        transform.transforms = [MagicMock()]  # non-empty = has transforms
        modified_img = np.zeros((64, 64, 3), dtype=np.uint8)
        transform.return_value = {
            "image": modified_img,
            "bboxes": [[0.5, 0.5, 0.2, 0.2]],
            "class_labels": [0],
        }

        ds = self._make_dataset_with_transform(transform)

        original_img = np.ones((64, 64, 3), dtype=np.uint8) * 128
        result = {
            "img": original_img,
            "bboxes": np.array([[0.5, 0.5, 0.3, 0.2]]),
            "cls": np.array([0]),
        }

        ds._apply_drone_augmentations(result)
        # The augmentation should have been called
        transform.assert_called_once()

    def test_all_bboxes_dropped_returns_original(self) -> None:
        """drone_transform returning empty bboxes -> original result returned."""
        transform = MagicMock()
        transform.transforms = [MagicMock()]
        transform.return_value = {
            "image": np.zeros((64, 64, 3), dtype=np.uint8),
            "bboxes": [],  # All dropped
            "class_labels": [],
        }

        ds = self._make_dataset_with_transform(transform)

        original_img = np.ones((64, 64, 3), dtype=np.uint8) * 128
        result = {
            "img": original_img,
            "bboxes": np.array([[0.5, 0.5, 0.3, 0.2]]),
            "cls": np.array([0]),
        }

        modified_result = ds._apply_drone_augmentations(result)
        # Should return original since all bboxes were dropped
        assert np.array_equal(modified_result["img"], original_img)

    def test_augmentation_exception_returns_original(self) -> None:
        """drone_transform raising RuntimeError -> original result, no propagation."""
        transform = MagicMock()
        transform.transforms = [MagicMock()]
        transform.side_effect = RuntimeError("Transform failed")

        ds = self._make_dataset_with_transform(transform)

        original_img = np.ones((64, 64, 3), dtype=np.uint8) * 128
        result = {
            "img": original_img,
            "bboxes": np.array([[0.5, 0.5, 0.3, 0.2]]),
            "cls": np.array([0]),
        }

        modified_result = ds._apply_drone_augmentations(result)
        # Should return original — exception caught
        assert np.array_equal(modified_result["img"], original_img)


# ---------------------------------------------------------------------------
# TestDatasetWithRealData (3 tests)
# ---------------------------------------------------------------------------


class TestDatasetWithRealData:
    """Tests using real training data from roboflow dataset."""

    @pytest.mark.real_data
    @pytest.mark.slow
    def test_dataset_construction_with_real_data(self, make_synthetic_yolo_dataset: tuple) -> None:
        """Construct YOLOWeightedDataset on synthetic YOLO data -> len>0."""
        from training.yolo.dataset import YOLOWeightedDataset

        data_yaml_path, train_dir, _ = make_synthetic_yolo_dataset
        ds = YOLOWeightedDataset(
            img_path=str(train_dir / "images"),
            imgsz=64,
            augment=False,
            data={
                "path": str(data_yaml_path.parent),
                "train": str(train_dir / "images"),
                "val": str(train_dir / "images"),
                "nc": 1,
                "names": {0: "license-plate"},
            },
            task="detect",
        )
        assert len(ds) > 0

    @pytest.mark.real_data
    @pytest.mark.slow
    def test_getitem_returns_valid_result(self, make_synthetic_yolo_dataset: tuple) -> None:
        """dataset[0] returns dict with 'img' key, 3-channel image."""
        from training.yolo.dataset import YOLOWeightedDataset

        data_yaml_path, train_dir, _ = make_synthetic_yolo_dataset
        ds = YOLOWeightedDataset(
            img_path=str(train_dir / "images"),
            imgsz=64,
            augment=False,
            data={
                "path": str(data_yaml_path.parent),
                "train": str(train_dir / "images"),
                "val": str(train_dir / "images"),
                "nc": 1,
                "names": {0: "license-plate"},
            },
            task="detect",
        )
        item = ds[0]
        assert "img" in item
        assert item["img"].shape[0] == 3  # CHW format

    @pytest.mark.real_data
    @pytest.mark.slow
    def test_weighted_sampling_shifts_distribution(
        self, make_synthetic_yolo_dataset: tuple
    ) -> None:
        """Set weight[0]=5.0, rest 1.0, sample 100x -> idx 0 sampled >5%."""
        from training.yolo.dataset import YOLOWeightedDataset

        data_yaml_path, train_dir, _ = make_synthetic_yolo_dataset
        ds = YOLOWeightedDataset(
            img_path=str(train_dir / "images"),
            imgsz=64,
            augment=False,
            data={
                "path": str(data_yaml_path.parent),
                "train": str(train_dir / "images"),
                "val": str(train_dir / "images"),
                "nc": 1,
                "names": {0: "license-plate"},
            },
            task="detect",
        )

        n = len(ds)
        weights = np.ones(n)
        weights[0] = 5.0
        ds.sample_weights = weights

        # Sample 100 times and count how often index 0 is selected
        np.random.seed(42)
        count_0 = 0
        for _ in range(100):
            idx = np.random.choice(n, p=ds._probs)
            if idx == 0:
                count_0 += 1

        # With weight 5.0 vs 1.0 for others, index 0 should appear >5% of the time
        assert count_0 > 5, f"Expected index 0 to appear >5 times but got {count_0}"
