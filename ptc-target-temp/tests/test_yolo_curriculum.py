"""Tests for CurriculumScheduler — three-phase curriculum with backbone freezing.

Chunk-02: curriculum.py tests. All tests use mock trainer/model objects.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import torch
import torch.nn as nn

from tests.conftest_training import make_yolo_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scheduler(
    cfg: Any | None = None,
    trainer: Any | None = None,
) -> Any:
    """Create a CurriculumScheduler with mock trainer."""
    from training.yolo.curriculum import CurriculumScheduler

    if cfg is None:
        cfg = make_yolo_config(epochs=100, freeze_backbone=True, freeze_epochs=15)
    if trainer is None:
        trainer = MagicMock()

    return CurriculumScheduler(cfg, trainer)


def _make_trainer_arg(epoch: int, model: Any | None = None) -> MagicMock:
    """Create a mock trainer_arg with .epoch and .model attributes."""
    trainer_arg = MagicMock()
    trainer_arg.epoch = epoch
    if model is not None:
        trainer_arg.model = model
    return trainer_arg


class _MockModelWithParams(nn.Module):
    """Mock model with N named parameters for freeze testing."""

    def __init__(self, n_params: int = 15) -> None:
        super().__init__()
        for i in range(n_params):
            self.register_parameter(f"param_{i}", nn.Parameter(torch.randn(1)))


class _MockModelWithBN(nn.Module):
    """Mock model with Conv + BatchNorm layers for freeze BN testing."""

    def __init__(self) -> None:
        super().__init__()
        # 2 params each (weight, bias)
        self.conv1 = nn.Conv2d(3, 16, 3, bias=True)  # params 0,1
        self.bn1 = nn.BatchNorm2d(16)  # params 2,3 (+ running_mean/var buffers)
        self.conv2 = nn.Conv2d(16, 32, 3, bias=True)  # params 4,5
        self.bn2 = nn.BatchNorm2d(32)  # params 6,7
        self.conv3 = nn.Conv2d(32, 64, 3, bias=True)  # params 8,9
        self.bn3 = nn.BatchNorm2d(64)  # params 10,11
        self.conv4 = nn.Conv2d(64, 64, 3, bias=True)  # params 12,13
        self.head = nn.Linear(64, 1)  # params 14,15


# ---------------------------------------------------------------------------
# TestCurriculumSchedulerInit
# ---------------------------------------------------------------------------


class TestCurriculumSchedulerInit:
    """Tests for CurriculumScheduler initialization."""

    def test_curriculum_importable(self) -> None:
        """CurriculumScheduler importable."""
        from training.yolo.curriculum import CurriculumScheduler

        assert hasattr(CurriculumScheduler, "on_epoch_start")

    def test_curriculum_stores_config_and_trainer(self) -> None:
        """sched.cfg is cfg, sched.trainer is trainer."""
        cfg = make_yolo_config(epochs=100)
        trainer = MagicMock()
        sched = _make_scheduler(cfg=cfg, trainer=trainer)

        assert sched.cfg is cfg
        assert sched.trainer is trainer


# ---------------------------------------------------------------------------
# TestOnEpochStart
# ---------------------------------------------------------------------------


class TestOnEpochStart:
    """Tests for on_epoch_start phase detection and freeze logic."""

    def test_intensity_zero_during_warm(self) -> None:
        """Epochs in WARM phase call update_augmentation_intensity(0.0)."""
        cfg = make_yolo_config(epochs=100, freeze_backbone=False)
        mock_trainer = MagicMock()
        sched = _make_scheduler(cfg=cfg, trainer=mock_trainer)

        sched.on_epoch_start(_make_trainer_arg(0))
        sched.on_epoch_start(_make_trainer_arg(5))

        # Both should be intensity 0.0
        calls = mock_trainer.update_augmentation_intensity.call_args_list
        assert len(calls) == 2
        assert calls[0].args[0] == pytest.approx(0.0)
        assert calls[1].args[0] == pytest.approx(0.0)

    def test_intensity_ramps_after_warm(self) -> None:
        """Epoch 15 has intensity > 0, and it increases by epoch 25."""
        cfg = make_yolo_config(epochs=100, freeze_backbone=False)
        mock_trainer = MagicMock()
        sched = _make_scheduler(cfg=cfg, trainer=mock_trainer)

        sched.on_epoch_start(_make_trainer_arg(15))
        sched.on_epoch_start(_make_trainer_arg(25))

        calls = mock_trainer.update_augmentation_intensity.call_args_list
        intensity_15 = calls[0].args[0]
        intensity_25 = calls[1].args[0]
        assert intensity_15 >= 0.0
        assert intensity_25 > intensity_15, "Intensity should increase during ramp"

    def test_intensity_peaks_at_one(self) -> None:
        """Mid-training intensity reaches 1.0."""
        cfg = make_yolo_config(epochs=100, freeze_backbone=False)
        mock_trainer = MagicMock()
        sched = _make_scheduler(cfg=cfg, trainer=mock_trainer)

        sched.on_epoch_start(_make_trainer_arg(50))

        calls = mock_trainer.update_augmentation_intensity.call_args_list
        assert calls[0].args[0] == pytest.approx(1.0)

    def test_intensity_decreases_in_refine(self) -> None:
        """Epoch 90 has lower intensity than epoch 50 (REFINE eases off)."""
        cfg = make_yolo_config(epochs=100, freeze_backbone=False)
        mock_trainer = MagicMock()
        sched = _make_scheduler(cfg=cfg, trainer=mock_trainer)
        sched._last_phase = "RAMP"

        sched.on_epoch_start(_make_trainer_arg(50))
        sched.on_epoch_start(_make_trainer_arg(90))

        calls = mock_trainer.update_augmentation_intensity.call_args_list
        assert calls[1].args[0] < calls[0].args[0], "REFINE should have lower intensity"

    def test_freeze_during_warm(self) -> None:
        """freeze_backbone=True, epoch<freeze_epochs -> _freeze_backbone called."""
        cfg = make_yolo_config(epochs=100, freeze_backbone=True, freeze_epochs=15)
        mock_trainer = MagicMock()
        sched = _make_scheduler(cfg=cfg, trainer=mock_trainer)

        with patch.object(sched, "_freeze_backbone") as mock_freeze:
            sched.on_epoch_start(_make_trainer_arg(5))

        mock_freeze.assert_called_once()

    def test_unfreeze_at_boundary(self) -> None:
        """epoch==freeze_epochs -> _unfreeze_backbone called once."""
        cfg = make_yolo_config(epochs=100, freeze_backbone=True, freeze_epochs=15)
        mock_trainer = MagicMock()
        sched = _make_scheduler(cfg=cfg, trainer=mock_trainer)

        with (
            patch.object(sched, "_freeze_backbone"),
            patch.object(sched, "_unfreeze_backbone") as mock_unfreeze,
        ):
            # Epoch 15 == freeze_epochs -> unfreeze
            sched.on_epoch_start(_make_trainer_arg(15))

        mock_unfreeze.assert_called_once()


# ---------------------------------------------------------------------------
# TestFreezeBackbone
# ---------------------------------------------------------------------------


class TestFreezeBackbone:
    """Tests for _freeze_backbone and _unfreeze_backbone mechanics."""

    def test_freeze_sets_requires_grad_false(self) -> None:
        """First freeze_layers params have requires_grad==False."""
        cfg = make_yolo_config(epochs=100, freeze_backbone=True, freeze_layers=10)
        sched = _make_scheduler(cfg=cfg)

        model = _MockModelWithParams(n_params=15)
        trainer_arg = _make_trainer_arg(0, model=model)

        sched._freeze_backbone(trainer_arg)

        params = list(model.parameters())
        # First 10 should be frozen
        for i in range(10):
            assert not params[i].requires_grad, f"param {i} should be frozen"
        # Last 5 should still be trainable
        for i in range(10, 15):
            assert params[i].requires_grad, f"param {i} should be trainable"

    def test_freeze_sets_bn_eval(self) -> None:
        """Frozen BatchNorm modules have training==False."""
        # freeze_layers=4 freezes conv1.weight, conv1.bias, bn1.weight, bn1.bias
        cfg = make_yolo_config(epochs=100, freeze_backbone=True, freeze_layers=4)
        sched = _make_scheduler(cfg=cfg)

        model = _MockModelWithBN()
        # Put model in train mode first
        model.train()
        trainer_arg = _make_trainer_arg(0, model=model)

        sched._freeze_backbone(trainer_arg)

        # bn1 should be in eval mode (its params are in the frozen range)
        assert not model.bn1.training, "bn1 should be eval (frozen)"
        # bn2 should still be in train mode (its params are NOT frozen)
        assert model.bn2.training, "bn2 should still be training"

    def test_unfreeze_sets_all_requires_grad_true(self) -> None:
        """All params have requires_grad==True after unfreeze."""
        cfg = make_yolo_config(epochs=100, freeze_backbone=True, freeze_layers=10)
        sched = _make_scheduler(cfg=cfg)

        model = _MockModelWithParams(n_params=15)
        trainer_arg = _make_trainer_arg(15, model=model)

        # First freeze
        sched._freeze_backbone(trainer_arg)
        # Then unfreeze
        sched._unfreeze_backbone(trainer_arg)

        for param in model.parameters():
            assert param.requires_grad, "all params should be trainable after unfreeze"


# ---------------------------------------------------------------------------
# TestCurriculumLogging
# ---------------------------------------------------------------------------


class TestCurriculumLogging:
    """Tests for phase transition logging."""

    def test_phase_transition_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """caplog contains phase transition message with old/new phase names."""
        cfg = make_yolo_config(epochs=100, freeze_backbone=False)
        mock_trainer = MagicMock()
        sched = _make_scheduler(cfg=cfg, trainer=mock_trainer)

        with caplog.at_level(logging.INFO, logger="training.yolo.curriculum"):
            # Force WARM -> RAMP transition
            sched.on_epoch_start(_make_trainer_arg(15))

        assert "WARM" in caplog.text
        assert "RAMP" in caplog.text

    def test_wandb_log_called_on_transition(self) -> None:
        """wandb.log called with dict containing 'curriculum/phase'."""
        cfg = make_yolo_config(epochs=100, freeze_backbone=False)
        mock_trainer = MagicMock()
        _make_scheduler(cfg=cfg, trainer=mock_trainer)

        mock_wandb = MagicMock()
        mock_wandb.run = MagicMock()  # wandb.run is not None -> log will fire

        with patch.dict("sys.modules", {"wandb": mock_wandb}):
            # Need to reload the module to pick up the patched wandb
            import importlib

            import training.yolo.curriculum

            importlib.reload(training.yolo.curriculum)

            sched2 = training.yolo.curriculum.CurriculumScheduler(cfg, mock_trainer)
            sched2.on_epoch_start(_make_trainer_arg(15))

        mock_wandb.log.assert_called_once()
        call_kwargs = mock_wandb.log.call_args[0][0]
        assert "curriculum/phase" in call_kwargs

        # Reload again to restore original state
        importlib.reload(training.yolo.curriculum)
