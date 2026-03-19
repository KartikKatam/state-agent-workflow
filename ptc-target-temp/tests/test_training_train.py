"""Tests for training/train.py — chunk-07: Training Infrastructure."""

from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import torch

from training.config import AugmentationPhase, TrainingConfig
from training.model import CornerHeatmapNet
from training.train import (
    build_hem_sampler,
    build_optimizer,
    build_scheduler,
    check_phase_advance,
    check_unfreeze,
    freeze_encoder,
    get_device,
    load_checkpoint,
    poll_config,
    save_checkpoint,
    unfreeze_encoder,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cfg() -> TrainingConfig:
    """Default config for tests — small/fast settings."""
    return TrainingConfig(device="cpu", total_epochs=50)


@pytest.fixture()
def model(cfg: TrainingConfig) -> CornerHeatmapNet:
    """Small model on CPU for testing."""
    return CornerHeatmapNet(cfg)


# ---------------------------------------------------------------------------
# get_device
# ---------------------------------------------------------------------------


class TestGetDevice:
    """Tests for get_device."""

    def test_get_device_cpu_explicit(self) -> None:
        """device='cpu' -> str(get_device(cfg))=='cpu'."""
        cfg = TrainingConfig(device="cpu")
        d = get_device(cfg)
        assert str(d) == "cpu"

    def test_get_device_cuda_unavailable_falls_back(self) -> None:
        """CUDA requested but unavailable -> falls back to CPU with warning."""
        cfg = TrainingConfig(device="cuda")
        with patch("torch.cuda.is_available", return_value=False):
            d = get_device(cfg)
        assert str(d) == "cpu"


# ---------------------------------------------------------------------------
# build_optimizer
# ---------------------------------------------------------------------------


class TestBuildOptimizer:
    """Tests for build_optimizer."""

    def test_optimizer_three_groups_correct_lr(
        self, model: CornerHeatmapNet, cfg: TrainingConfig
    ) -> None:
        """build_optimizer -> 3 groups, groups[0].lr==base_lr*encoder_lr_mult, groups[1].lr==base_lr."""
        opt = build_optimizer(model, cfg)
        assert len(opt.param_groups) == 3
        # Group 0: encoder at reduced LR
        assert opt.param_groups[0]["lr"] == pytest.approx(cfg.base_lr * cfg.encoder_lr_mult)
        # Group 1: decoder at base LR
        assert opt.param_groups[1]["lr"] == pytest.approx(cfg.base_lr)
        # Group 2: heads at base LR
        assert opt.param_groups[2]["lr"] == pytest.approx(cfg.base_lr)


# ---------------------------------------------------------------------------
# Freeze / unfreeze
# ---------------------------------------------------------------------------


class TestFreezeUnfreeze:
    """Tests for freeze_encoder and unfreeze_encoder."""

    def test_freeze_encoder_disables_grad(self, model: CornerHeatmapNet) -> None:
        """freeze_encoder -> no encoder param has requires_grad."""
        freeze_encoder(model)
        assert not any(p.requires_grad for p in model.encoder_params())

    def test_unfreeze_encoder_enables_grad(self, model: CornerHeatmapNet) -> None:
        """unfreeze_encoder after freeze -> all encoder params have requires_grad."""
        freeze_encoder(model)
        unfreeze_encoder(model)
        assert all(p.requires_grad for p in model.encoder_params())


# ---------------------------------------------------------------------------
# build_scheduler
# ---------------------------------------------------------------------------


class TestBuildScheduler:
    """Tests for build_scheduler — linear warmup + cosine decay."""

    def test_scheduler_warmup_lr(self, model: CornerHeatmapNet, cfg: TrainingConfig) -> None:
        """At epoch 0, LR multiplier approx warmup_start_lr/base_lr."""
        opt = build_optimizer(model, cfg)
        sched = build_scheduler(opt, cfg)
        # Before any step, get_last_lr() reflects epoch 0
        lr0 = sched.get_last_lr()[0]
        expected = cfg.base_lr * cfg.encoder_lr_mult * (cfg.warmup_start_lr / cfg.base_lr)
        assert lr0 == pytest.approx(expected, rel=0.05)

    def test_scheduler_post_warmup_lr(self, model: CornerHeatmapNet, cfg: TrainingConfig) -> None:
        """At epoch == warmup_epochs, LR multiplier approx 1.0 (full base_lr)."""
        opt = build_optimizer(model, cfg)
        sched = build_scheduler(opt, cfg)
        # Step through warmup
        for _ in range(cfg.warmup_epochs):
            sched.step()
        # After warmup, decoder LR should be approximately base_lr
        decoder_lr = sched.get_last_lr()[1]
        assert decoder_lr == pytest.approx(cfg.base_lr, rel=0.05)

    def test_scheduler_cosine_phase_monotonic_after_warmup(
        self, model: CornerHeatmapNet, cfg: TrainingConfig
    ) -> None:
        """After warmup_epochs, LR multiplier monotonically decreases (or stays flat)."""
        opt = build_optimizer(model, cfg)
        sched = build_scheduler(opt, cfg)
        # Step through warmup
        for _ in range(cfg.warmup_epochs):
            sched.step()
        prev_lr = sched.get_last_lr()[1]
        # Step through remaining epochs — LR should never increase
        for _ in range(cfg.warmup_epochs, cfg.total_epochs):
            sched.step()
            cur_lr = sched.get_last_lr()[1]
            assert cur_lr <= prev_lr + 1e-10  # small tolerance for float
            prev_lr = cur_lr


# ---------------------------------------------------------------------------
# check_unfreeze
# ---------------------------------------------------------------------------


class TestCheckUnfreeze:
    """Tests for check_unfreeze — stagnation detection."""

    def test_check_unfreeze_force_at_max(self) -> None:
        """epoch==freeze_max_epochs, frozen=True -> (True, 'force_unfreeze...')."""
        cfg = TrainingConfig(freeze_max_epochs=60)
        should, reason = check_unfreeze(cfg, 60, [1.0] * 60, True)
        assert should is True
        assert "force_unfreeze" in reason

    def test_check_unfreeze_stagnation(self) -> None:
        """Flat val_loss -> stagnation detected."""
        cfg = TrainingConfig(freeze_min_epochs=20, stagnation_window=5, stagnation_threshold=0.01)
        # Flat loss = zero improvement
        val_loss = [1.0] * 25
        should, reason = check_unfreeze(cfg, 24, val_loss, True)
        assert should is True
        assert "stagnant" in reason

    def test_check_unfreeze_no_trigger_when_improving(self) -> None:
        """Decreasing val_loss -> no unfreeze."""
        cfg = TrainingConfig(freeze_min_epochs=3, stagnation_window=5, stagnation_threshold=0.01)
        val_loss = [1.0, 0.9, 0.8, 0.7, 0.6]
        should, reason = check_unfreeze(cfg, 4, val_loss, True)
        assert should is False
        assert reason == ""

    def test_check_unfreeze_empty_history(self) -> None:
        """Empty val_loss -> (False, '') without crash."""
        cfg = TrainingConfig()
        should, reason = check_unfreeze(cfg, 0, [], True)
        assert should is False
        assert reason == ""

    def test_check_unfreeze_not_frozen(self) -> None:
        """Already unfrozen -> (False, '')."""
        cfg = TrainingConfig()
        should, reason = check_unfreeze(cfg, 100, [1.0] * 100, False)
        assert should is False
        assert reason == ""


# ---------------------------------------------------------------------------
# build_hem_sampler
# ---------------------------------------------------------------------------


class TestBuildHemSampler:
    """Tests for build_hem_sampler — hard example mining."""

    def test_hem_sampler_weights_clamped(self) -> None:
        """Losses [0.01,0.5,5.0,0.1] -> weights in [floor, ceiling]."""
        cfg = TrainingConfig()
        losses = np.array([0.01, 0.5, 5.0, 0.1])
        sampler = build_hem_sampler(losses, cfg)
        weights = list(sampler.weights)
        assert all(cfg.hem_weight_floor <= w <= cfg.hem_weight_ceiling for w in weights)

    def test_hem_sampler_handles_zero_mean_losses_safely(self) -> None:
        """All-zero losses -> no NaN/Inf, weights == floor."""
        cfg = TrainingConfig()
        losses = np.array([0.0, 0.0, 0.0, 0.0])
        sampler = build_hem_sampler(losses, cfg)
        weights = list(sampler.weights)
        assert all(not math.isnan(w) and not math.isinf(w) for w in weights)
        assert all(w == pytest.approx(cfg.hem_weight_floor) for w in weights)


# ---------------------------------------------------------------------------
# check_phase_advance
# ---------------------------------------------------------------------------


class TestCheckPhaseAdvance:
    """Tests for check_phase_advance — augmentation phase transitions."""

    def test_phase_advance_none_to_moderate(self) -> None:
        """encoder_frozen=False, current=NONE -> MODERATE."""
        cfg = TrainingConfig()
        result = check_phase_advance(cfg, [], AugmentationPhase.NONE, False)
        assert result == AugmentationPhase.MODERATE

    def test_phase_advance_moderate_to_full(self) -> None:
        """pck4_history all >= threshold for sustain_epochs -> FULL_DRONE."""
        cfg = TrainingConfig(phase3_pck_threshold=0.75, phase3_sustain_epochs=5)
        pck4_history = [0.8] * 10
        result = check_phase_advance(cfg, pck4_history, AugmentationPhase.MODERATE, False)
        assert result == AugmentationPhase.FULL_DRONE

    def test_phase_advance_no_premature_trigger(self) -> None:
        """pck4 with dip below threshold -> remains MODERATE."""
        cfg = TrainingConfig(phase3_pck_threshold=0.75, phase3_sustain_epochs=5)
        pck4_history = [0.8, 0.8, 0.7, 0.8, 0.8]
        result = check_phase_advance(cfg, pck4_history, AugmentationPhase.MODERATE, False)
        assert result == AugmentationPhase.MODERATE

    def test_phase_advance_stays_none_when_frozen(self) -> None:
        """encoder_frozen=True, current=NONE -> stays NONE."""
        cfg = TrainingConfig()
        result = check_phase_advance(cfg, [], AugmentationPhase.NONE, True)
        assert result == AugmentationPhase.NONE


# ---------------------------------------------------------------------------
# Checkpoint save/load
# ---------------------------------------------------------------------------


class TestCheckpoint:
    """Tests for save_checkpoint and load_checkpoint."""

    def test_checkpoint_roundtrip(self, cfg: TrainingConfig, tmp_path: Path) -> None:
        """save_checkpoint -> load_checkpoint: model weights, optimizer, scheduler all match."""
        model1 = CornerHeatmapNet(cfg)
        opt1 = build_optimizer(model1, cfg)
        sched1 = build_scheduler(opt1, cfg)
        # Step scheduler a few times to create non-trivial state
        for _ in range(3):
            sched1.step()

        ckpt_path = save_checkpoint(
            model1,
            opt1,
            sched1,
            epoch=3,
            metrics={"pck_4": 0.5},
            cfg=cfg,
            path=tmp_path / "test.pt",
        )
        assert ckpt_path.exists()

        # Load into fresh model
        model2 = CornerHeatmapNet(cfg)
        opt2 = build_optimizer(model2, cfg)
        sched2 = build_scheduler(opt2, cfg)
        ckpt = load_checkpoint(ckpt_path, model2, opt2, sched2)

        # Model weights match
        for p1, p2 in zip(model1.parameters(), model2.parameters(), strict=True):
            assert torch.allclose(p1, p2)

        # Epoch preserved
        assert ckpt["epoch"] == 3
        assert ckpt["metrics"]["pck_4"] == 0.5

    def test_checkpoint_load_model_only(self, cfg: TrainingConfig, tmp_path: Path) -> None:
        """load_checkpoint with optimizer=None, scheduler=None still restores model."""
        model1 = CornerHeatmapNet(cfg)
        opt1 = build_optimizer(model1, cfg)
        sched1 = build_scheduler(opt1, cfg)

        ckpt_path = save_checkpoint(
            model1,
            opt1,
            sched1,
            epoch=0,
            metrics={},
            cfg=cfg,
            path=tmp_path / "model_only.pt",
        )

        model2 = CornerHeatmapNet(cfg)
        load_checkpoint(ckpt_path, model2)

        for p1, p2 in zip(model1.parameters(), model2.parameters(), strict=True):
            assert torch.allclose(p1, p2)


# ---------------------------------------------------------------------------
# poll_config
# ---------------------------------------------------------------------------


class TestPollConfig:
    """Tests for poll_config — interactive configuration override."""

    def test_poll_config_handles_invalid_json_without_crash(
        self, cfg: TrainingConfig, tmp_path: Path
    ) -> None:
        """Malformed JSON -> returns original cfg unchanged."""
        override_path = tmp_path / "bad_config.json"
        override_path.write_text("{bad json!!!")
        original_lr = cfg.base_lr
        result = poll_config(cfg, override_path)
        assert result.base_lr == original_lr

    def test_poll_config_ignores_unknown_keys(self, cfg: TrainingConfig, tmp_path: Path) -> None:
        """Unknown keys are silently ignored."""
        override_path = tmp_path / "override.json"
        override_path.write_text(json.dumps({"base_lr": 5e-4, "bogus_field": 999}))
        result = poll_config(cfg, override_path)
        assert result.base_lr == pytest.approx(5e-4)
        assert not hasattr(result, "bogus_field")

    def test_poll_config_applies_valid_overrides(self, cfg: TrainingConfig, tmp_path: Path) -> None:
        """Valid overrides applied, others unchanged."""
        override_path = tmp_path / "override.json"
        override_path.write_text(json.dumps({"base_lr": 5e-4, "lambda_offset": 2.0}))
        original_total = cfg.total_epochs
        result = poll_config(cfg, override_path)
        assert result.base_lr == pytest.approx(5e-4)
        assert result.lambda_offset == pytest.approx(2.0)
        assert result.total_epochs == original_total

    def test_poll_config_missing_file_returns_unchanged(
        self, cfg: TrainingConfig, tmp_path: Path
    ) -> None:
        """Non-existent file -> returns original cfg unchanged."""
        override_path = tmp_path / "does_not_exist.json"
        result = poll_config(cfg, override_path)
        assert result.base_lr == cfg.base_lr
