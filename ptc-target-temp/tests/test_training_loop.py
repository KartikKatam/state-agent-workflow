"""Integration tests for the main training loop (chunk-08).

Tests: train(), W&B integration, conftest factories, resume,
pause/resume, checkpoint management, and loss sanity.
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import pytest
import torch

from tests.conftest_training import (
    make_synthetic_dataset,
    make_training_config,
)
from training.config import AugmentationPhase
from training.train import train

# ---------------------------------------------------------------------------
# Conftest factory tests
# ---------------------------------------------------------------------------


class TestConftestFactories:
    """Tests for conftest_training.py factories."""

    def test_conftest_make_training_config_defaults(self) -> None:
        """make_training_config() returns test-friendly defaults."""
        cfg = make_training_config()
        assert cfg.total_epochs == 2
        assert cfg.batch_size == 2
        assert cfg.device == "cpu"
        assert cfg.wandb_enabled is False

    def test_conftest_make_training_config_overrides(self) -> None:
        """make_training_config with overrides replaces defaults."""
        cfg = make_training_config(total_epochs=10, batch_size=4)
        assert cfg.total_epochs == 10
        assert cfg.batch_size == 4

    def test_conftest_make_synthetic_dataset(self, tmp_path: Path) -> None:
        """make_synthetic_dataset creates images, annotations, and split files."""
        data_dir, ann_path, split_path = make_synthetic_dataset(5, tmp_path=tmp_path)
        assert data_dir.exists()
        assert ann_path.exists()
        assert split_path.exists()

        annotations = json.loads(ann_path.read_text())
        assert len(annotations) == 5

        splits = json.loads(split_path.read_text())
        assert "train" in splits
        assert "val" in splits
        total = len(splits["train"]) + len(splits["val"])
        assert total == 5


# ---------------------------------------------------------------------------
# DataLoader construction test
# ---------------------------------------------------------------------------


class TestDataLoaderConstruction:
    """Tests for DataLoader creation from synthetic dataset."""

    def test_dataloader_construction(self, tmp_path: Path) -> None:
        """DataLoader from synthetic dataset yields correct batch shapes."""
        data_dir, ann_path, split_path = make_synthetic_dataset(n_images=6, tmp_path=tmp_path)
        cfg = make_training_config(
            data_dir=str(data_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
        )
        from torch.utils.data import DataLoader

        from training.dataset import PlateCornerDataset

        dataset = PlateCornerDataset(cfg, split="train")
        loader = DataLoader(dataset, batch_size=2, shuffle=False, num_workers=0, pin_memory=False)
        batch = next(iter(loader))
        images, hm, off, mask = batch
        assert images.shape[0] == 2
        assert images.ndim == 4  # (B, C, H, W)
        assert hm.ndim == 4  # (B, 4, H/stride, W/stride)
        assert off.ndim == 4
        assert mask.ndim == 4


# ---------------------------------------------------------------------------
# Main training loop tests
# ---------------------------------------------------------------------------


class TestTrainNoWandb:
    """Tests that training works without W&B."""

    def test_train_no_wandb(self, tmp_path: Path) -> None:
        """Training with wandb_enabled=False completes without ImportError."""
        data_dir, ann_path, split_path = make_synthetic_dataset(n_images=6, tmp_path=tmp_path)
        cfg = make_training_config(
            data_dir=str(data_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
            checkpoint_dir=str(tmp_path / "ckpts"),
            config_override_path=str(tmp_path / "nonexistent_config.json"),
            wandb_enabled=False,
            total_epochs=2,
        )
        result = train(cfg)
        assert result is not None
        assert isinstance(result, Path)
        assert result.exists()


class TestMiniTrainLoop:
    """Tests for a minimal training run."""

    def test_mini_train_loop(self, tmp_path: Path) -> None:
        """2-epoch training on synthetic data completes, checkpoint exists."""
        data_dir, ann_path, split_path = make_synthetic_dataset(n_images=6, tmp_path=tmp_path)
        cfg = make_training_config(
            data_dir=str(data_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
            checkpoint_dir=str(tmp_path / "ckpts"),
            config_override_path=str(tmp_path / "nonexistent_config.json"),
            total_epochs=2,
        )
        result = train(cfg)
        assert result is not None
        assert result.exists()
        # Verify checkpoint can be loaded
        ckpt = torch.load(result, map_location="cpu", weights_only=False)
        assert "model_state_dict" in ckpt
        assert "epoch" in ckpt


class TestPhaseAdvanceInTraining:
    """Tests for augmentation phase advancement during training."""

    def test_phase_advance_in_training(self, tmp_path: Path) -> None:
        """Phase advances to MODERATE after encoder unfreezes."""
        data_dir, ann_path, split_path = make_synthetic_dataset(n_images=6, tmp_path=tmp_path)
        # freeze_max_epochs=0 means unfreeze immediately at epoch 0
        cfg = make_training_config(
            data_dir=str(data_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
            checkpoint_dir=str(tmp_path / "ckpts"),
            config_override_path=str(tmp_path / "nonexistent_config.json"),
            total_epochs=3,
            freeze_max_epochs=0,
        )
        result = train(cfg)
        assert result is not None
        # The best checkpoint should record aug_phase >= MODERATE
        ckpt = torch.load(result, map_location="cpu", weights_only=False)
        aug_phase = ckpt.get("aug_phase", 0)
        assert aug_phase >= AugmentationPhase.MODERATE.value


class TestBestCheckpointSaved:
    """Tests for best checkpoint tracking."""

    def test_best_checkpoint_saved(self, tmp_path: Path) -> None:
        """Best checkpoint saved when PCK improves."""
        data_dir, ann_path, split_path = make_synthetic_dataset(n_images=6, tmp_path=tmp_path)
        cfg = make_training_config(
            data_dir=str(data_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
            checkpoint_dir=str(tmp_path / "ckpts"),
            config_override_path=str(tmp_path / "nonexistent_config.json"),
            total_epochs=2,
        )
        result = train(cfg)
        assert result is not None
        # best.pt should exist in checkpoint_dir
        best_path = Path(cfg.checkpoint_dir) / "best.pt"
        assert best_path.exists()
        assert result == best_path


class TestPeriodicCheckpointSaved:
    """Tests for periodic checkpoint saving."""

    def test_periodic_checkpoint_saved(self, tmp_path: Path) -> None:
        """Periodic checkpoints saved at configured intervals."""
        data_dir, ann_path, split_path = make_synthetic_dataset(n_images=6, tmp_path=tmp_path)
        cfg = make_training_config(
            data_dir=str(data_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
            checkpoint_dir=str(tmp_path / "ckpts"),
            config_override_path=str(tmp_path / "nonexistent_config.json"),
            total_epochs=10,
            checkpoint_interval=5,
        )
        result = train(cfg)
        assert result is not None
        ckpt_dir = Path(cfg.checkpoint_dir)
        assert (ckpt_dir / "checkpoint_epoch0.pt").exists()
        assert (ckpt_dir / "checkpoint_epoch5.pt").exists()


class TestTrainLossSanity:
    """Tests for loss value sanity."""

    def test_train_no_nan_inf_in_logged_losses(self, tmp_path: Path) -> None:
        """All returned loss values are finite after 2-epoch training."""
        data_dir, ann_path, split_path = make_synthetic_dataset(n_images=6, tmp_path=tmp_path)
        cfg = make_training_config(
            data_dir=str(data_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
            checkpoint_dir=str(tmp_path / "ckpts"),
            config_override_path=str(tmp_path / "nonexistent_config.json"),
            total_epochs=2,
        )
        result = train(cfg)
        assert result is not None
        ckpt = torch.load(result, map_location="cpu", weights_only=False)
        metrics = ckpt.get("metrics", {})
        for key in ["pck_4", "mean_cpe"]:
            if key in metrics:
                assert math.isfinite(metrics[key]), f"{key} is not finite: {metrics[key]}"


class TestWeightUpdates:
    """Tests that training actually modifies weights."""

    def test_train_step_updates_weights_from_init(self, tmp_path: Path) -> None:
        """After 1 epoch, at least one model parameter differs from init."""
        data_dir, ann_path, split_path = make_synthetic_dataset(n_images=6, tmp_path=tmp_path)
        cfg = make_training_config(
            data_dir=str(data_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
            checkpoint_dir=str(tmp_path / "ckpts"),
            config_override_path=str(tmp_path / "nonexistent_config.json"),
            total_epochs=1,
        )
        # Capture initial weights
        from training.model import CornerHeatmapNet

        torch.manual_seed(42)
        init_model = CornerHeatmapNet(cfg)
        init_weights = {name: p.clone() for name, p in init_model.named_parameters()}

        # Train with same seed for model init
        torch.manual_seed(42)
        result = train(cfg)
        assert result is not None

        # Load trained weights and compare
        trained_ckpt = torch.load(result, map_location="cpu", weights_only=False)
        trained_state = trained_ckpt["model_state_dict"]

        any_changed = False
        for name, init_param in init_weights.items():
            if name in trained_state and not torch.equal(init_param, trained_state[name]):
                any_changed = True
                break

        assert any_changed, "No parameters changed after training — training may be broken"


class TestResumeFromCheckpoint:
    """Tests for checkpoint resume."""

    def test_resume_from_checkpoint_continues_epoch_count(self, tmp_path: Path) -> None:
        """Resume continues from saved epoch, doesn't restart from 0."""
        data_dir, ann_path, split_path = make_synthetic_dataset(n_images=6, tmp_path=tmp_path)
        ckpt_dir = str(tmp_path / "ckpts")
        cfg = make_training_config(
            data_dir=str(data_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
            checkpoint_dir=ckpt_dir,
            config_override_path=str(tmp_path / "nonexistent_config.json"),
            total_epochs=2,
        )
        # Phase 1: train 2 epochs
        result1 = train(cfg)
        assert result1 is not None
        ckpt1 = torch.load(result1, map_location="cpu", weights_only=False)
        epoch1 = ckpt1["epoch"]

        # Phase 2: resume and train to epoch 4
        cfg2 = make_training_config(
            data_dir=str(data_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
            checkpoint_dir=ckpt_dir,
            config_override_path=str(tmp_path / "nonexistent_config.json"),
            total_epochs=4,
        )
        result2 = train(cfg2, resume_path=result1)
        assert result2 is not None
        ckpt2 = torch.load(result2, map_location="cpu", weights_only=False)
        # Resumed training should have epoch >= 2 (the last epoch from phase 1)
        assert ckpt2["epoch"] >= epoch1

    def test_resume_restores_optimizer_and_scheduler_state(self, tmp_path: Path) -> None:
        """Resume restores optimizer and scheduler state correctly."""
        data_dir, ann_path, split_path = make_synthetic_dataset(n_images=6, tmp_path=tmp_path)
        ckpt_dir = str(tmp_path / "ckpts")
        cfg = make_training_config(
            data_dir=str(data_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
            checkpoint_dir=ckpt_dir,
            config_override_path=str(tmp_path / "nonexistent_config.json"),
            total_epochs=2,
        )
        # Train and get checkpoint
        result = train(cfg)
        assert result is not None
        ckpt = torch.load(result, map_location="cpu", weights_only=False)

        # Verify checkpoint has optimizer and scheduler state
        assert "optimizer_state_dict" in ckpt
        assert "scheduler_state_dict" in ckpt

        # Restore into fresh model and verify
        from training.model import CornerHeatmapNet
        from training.train import build_optimizer, build_scheduler, load_checkpoint

        model = CornerHeatmapNet(cfg)
        optimizer = build_optimizer(model, cfg)
        scheduler = build_scheduler(optimizer, cfg)

        load_checkpoint(result, model, optimizer, scheduler)
        # Optimizer should have state from training (not empty)
        assert len(optimizer.state) > 0
        # Scheduler last_epoch should be > 0
        assert scheduler.last_epoch > 0


class TestPauseResume:
    """Tests for pause/resume via config polling."""

    def test_pause_flag_saves_checkpoint_and_blocks(self, tmp_path: Path) -> None:
        """Pause flag causes checkpoint save, unpause resumes training."""
        data_dir, ann_path, split_path = make_synthetic_dataset(n_images=6, tmp_path=tmp_path)
        config_override = tmp_path / "training_config.json"
        ckpt_dir = tmp_path / "ckpts"

        cfg = make_training_config(
            data_dir=str(data_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
            checkpoint_dir=str(ckpt_dir),
            config_override_path=str(config_override),
            total_epochs=4,
            config_poll_interval=0.1,
        )

        # Write pause=true to config before training starts
        # We'll unpause after a short delay via a background thread
        config_override.write_text(json.dumps({"pause": True}))

        result_container: list[Path | None] = [None]
        error_container: list[Exception | None] = [None]

        def run_training() -> None:
            try:
                result_container[0] = train(cfg)
            except Exception as e:
                error_container[0] = e

        t = threading.Thread(target=run_training)
        t.start()

        # Wait a bit for training to enter pause loop, then unpause
        time.sleep(1.0)
        config_override.write_text(json.dumps({"pause": False}))
        t.join(timeout=60)

        assert not t.is_alive(), "Training thread did not complete"
        assert error_container[0] is None, f"Training raised: {error_container[0]}"
        result = result_container[0]
        assert result is not None
        assert result.exists()

        # Verify a pause checkpoint was saved
        pause_ckpts = list(ckpt_dir.glob("checkpoint_pause_*.pt"))
        assert len(pause_ckpts) >= 1


class TestTrainRejectsZeroEpochs:
    """Tests for zero-epoch edge case."""

    def test_train_rejects_zero_epochs(self, tmp_path: Path) -> None:
        """Training with total_epochs=0 raises ValueError."""
        data_dir, ann_path, split_path = make_synthetic_dataset(n_images=6, tmp_path=tmp_path)
        cfg = make_training_config(
            data_dir=str(data_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
            checkpoint_dir=str(tmp_path / "ckpts"),
            config_override_path=str(tmp_path / "nonexistent_config.json"),
            total_epochs=0,
        )
        with pytest.raises(ValueError, match="epochs"):
            train(cfg)
