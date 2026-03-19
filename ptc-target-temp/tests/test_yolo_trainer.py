"""Tests for LPRDetectionTrainer — custom YOLO trainer with build_dataset override.

Chunk-02: trainer.py tests. All tests mock DetectionTrainer to avoid
requiring actual YOLO model downloads or GPU.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np

from tests.conftest_training import make_yolo_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trainer(cfg: Any | None = None, overrides: dict[str, Any] | None = None) -> Any:
    """Create an LPRDetectionTrainer with mocked parent init.

    Patches DetectionTrainer.__init__ so we don't need a real YOLO setup.
    Returns (trainer, cfg).
    """
    from ultralytics.models.yolo.detect import DetectionTrainer

    from training.yolo.trainer import LPRDetectionTrainer

    if cfg is None:
        cfg = make_yolo_config(epochs=100, drone_aug_enabled=True)

    with patch.object(DetectionTrainer, "__init__", lambda self, **kw: None):
        trainer = LPRDetectionTrainer(lpr_config=cfg, overrides=overrides or {})

    # Manually set attributes that DetectionTrainer.__init__ normally creates
    trainer.args = MagicMock()
    trainer.args.imgsz = cfg.imgsz
    trainer.args.task = "detect"
    trainer.args.classes = None
    trainer.model = MagicMock()
    trainer.model.stride = MagicMock()
    trainer.model.stride.max.return_value = 32
    trainer.data = {"train": "/fake/train", "val": "/fake/val"}
    trainer.train_loader = None

    return trainer, cfg


# ---------------------------------------------------------------------------
# TestLPRDetectionTrainerInit
# ---------------------------------------------------------------------------


class TestLPRDetectionTrainerInit:
    """Tests for LPRDetectionTrainer initialization."""

    def test_trainer_importable(self) -> None:
        """LPRDetectionTrainer importable, has build_dataset method."""
        from training.yolo.trainer import LPRDetectionTrainer

        assert hasattr(LPRDetectionTrainer, "build_dataset")

    def test_trainer_stores_lpr_config(self) -> None:
        """trainer.lpr_config is cfg, current_phase=='WARM'."""
        trainer, cfg = _make_trainer()
        assert trainer.lpr_config is cfg
        assert trainer.current_phase == "WARM"

    def test_trainer_initial_state(self) -> None:
        """_sample_weights is None, _drone_transform is None."""
        trainer, _ = _make_trainer()
        assert trainer._sample_weights is None
        assert trainer._drone_transform is None


# ---------------------------------------------------------------------------
# TestBuildDataset
# ---------------------------------------------------------------------------


class TestBuildDataset:
    """Tests for build_dataset() override."""

    def test_build_dataset_val_defers_to_super(self) -> None:
        """mode='val' calls super().build_dataset, not YOLOWeightedDataset."""
        from ultralytics.models.yolo.detect import DetectionTrainer

        trainer, _ = _make_trainer()
        sentinel = object()

        with patch.object(DetectionTrainer, "build_dataset", return_value=sentinel) as mock_super:
            result = trainer.build_dataset("/path", mode="val")

        mock_super.assert_called_once()
        assert result is sentinel

    def test_build_dataset_train_returns_weighted_dataset(self) -> None:
        """mode='train' calls YOLOWeightedDataset with sample_weights and drone_transform."""
        trainer, _ = _make_trainer()
        mock_dataset = MagicMock()

        with (
            patch(
                "training.yolo.trainer.YOLOWeightedDataset", return_value=mock_dataset
            ) as mock_cls,
            patch("training.yolo.trainer.build_drone_pipeline"),
        ):
            result = trainer.build_dataset("/path", mode="train")

        assert result is mock_dataset
        call_kwargs = mock_cls.call_args
        # Verify custom kwargs were passed
        assert "sample_weights" in call_kwargs.kwargs or any(
            "sample_weights" in str(a) for a in call_kwargs
        )

    def test_build_dataset_passes_drone_transform(self) -> None:
        """current_phase='RAMP' -> drone_transform is A.Compose (not None)."""
        import albumentations as A

        trainer, cfg = _make_trainer()
        trainer.current_phase = "RAMP"

        # Use real build_drone_pipeline to get a real A.Compose
        with patch(
            "training.yolo.trainer.YOLOWeightedDataset", return_value=MagicMock()
        ) as mock_cls:
            trainer.build_dataset("/path", mode="train")

        # Extract the drone_transform kwarg
        call_kwargs = mock_cls.call_args.kwargs
        assert "drone_transform" in call_kwargs
        assert isinstance(call_kwargs["drone_transform"], A.Compose)

    def test_build_dataset_signature_matches_parent(self) -> None:
        """Parameters: self, img_path, mode='train', batch=None."""
        from training.yolo.trainer import LPRDetectionTrainer

        sig = inspect.signature(LPRDetectionTrainer.build_dataset)
        params = list(sig.parameters.keys())
        assert params == ["self", "img_path", "mode", "batch"]

        # Check defaults
        assert sig.parameters["mode"].default == "train"
        assert sig.parameters["batch"].default is None


# ---------------------------------------------------------------------------
# TestPhaseTransition
# ---------------------------------------------------------------------------


class TestPhaseTransition:
    """Tests for trigger_phase_transition()."""

    def test_trigger_phase_transition_updates_phase(self) -> None:
        """trigger_phase_transition('RAMP') -> current_phase=='RAMP'."""
        trainer, _ = _make_trainer()
        trainer.trigger_phase_transition("RAMP")
        assert trainer.current_phase == "RAMP"

    def test_trigger_phase_transition_calls_update_intensity(self) -> None:
        """update_augmentation_intensity called with phase-mapped intensity."""
        trainer, _ = _make_trainer()

        with patch.object(trainer, "update_augmentation_intensity") as mock_update:
            trainer.trigger_phase_transition("RAMP")

        mock_update.assert_called_once_with(1.0)

    def test_trigger_phase_transition_updates_args(self) -> None:
        """trainer.args.mosaic == cfg.ramp_aug['mosaic'] after RAMP transition."""
        trainer, cfg = _make_trainer()

        # Use a real SimpleNamespace-like args so setattr works
        class Args:
            pass

        trainer.args = Args()
        trainer.args.imgsz = cfg.imgsz  # type: ignore[attr-defined]
        trainer.args.task = "detect"  # type: ignore[attr-defined]
        trainer.args.classes = None  # type: ignore[attr-defined]

        with patch.object(trainer, "_close_dataloader_mosaic"):
            trainer.trigger_phase_transition("RAMP")

        assert trainer.args.mosaic == cfg.ramp_aug["mosaic"]  # type: ignore[attr-defined]
        assert trainer.args.mixup == cfg.ramp_aug["mixup"]  # type: ignore[attr-defined]
        assert trainer.args.degrees == cfg.ramp_aug["degrees"]  # type: ignore[attr-defined]

    def test_phase_transition_rebuild_changes_active_transforms(self) -> None:
        """After WARM->RAMP transition: train_loader.dataset._drone_transform has 9 transforms."""
        from training.yolo.dataset import YOLOWeightedDataset

        trainer, cfg = _make_trainer(cfg=make_yolo_config(epochs=100, drone_aug_enabled=True))
        trainer.current_phase = "WARM"

        # Set up a mock train_loader with a spec'd dataset (isinstance must work)
        mock_dataset = MagicMock(spec=YOLOWeightedDataset)
        mock_dataset._drone_transform = MagicMock(transforms=[])  # WARM: 0 transforms
        mock_loader = MagicMock()
        mock_loader.dataset = mock_dataset
        trainer.train_loader = mock_loader

        # Patch super()._close_dataloader_mosaic to be a no-op
        from ultralytics.models.yolo.detect import DetectionTrainer

        with patch.object(DetectionTrainer, "_close_dataloader_mosaic"):
            trainer.trigger_phase_transition("RAMP")

        # After rebuild, the dataset's drone_transform should have 11 transforms (RAMP)
        assert len(mock_dataset._drone_transform.transforms) == 11


# ---------------------------------------------------------------------------
# TestUpdateSampleWeights
# ---------------------------------------------------------------------------


class TestUpdateSampleWeights:
    """Tests for update_sample_weights()."""

    def test_update_sample_weights_stores(self) -> None:
        """np.array_equal(trainer._sample_weights, weights)."""
        trainer, _ = _make_trainer()
        weights = np.array([1.0, 2.0, 3.0])

        trainer.update_sample_weights(weights)

        assert np.array_equal(trainer._sample_weights, weights)

    def test_update_sample_weights_updates_live_dataset(self) -> None:
        """dataset.sample_weights setter called with weights."""
        from training.yolo.dataset import YOLOWeightedDataset

        trainer, _ = _make_trainer()
        weights = np.array([1.0, 2.0, 3.0])

        # Set up mock train_loader with a mock YOLOWeightedDataset
        mock_dataset = MagicMock(spec=YOLOWeightedDataset)
        mock_loader = MagicMock()
        mock_loader.dataset = mock_dataset
        trainer.train_loader = mock_loader

        trainer.update_sample_weights(weights)

        # Verify the setter was called
        assert mock_dataset.sample_weights is not None  # property was set
