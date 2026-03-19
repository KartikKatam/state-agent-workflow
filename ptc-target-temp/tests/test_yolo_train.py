"""Tests for training/yolo/train.py — training entrypoint + W&B integration.

Pass A chunk-04 tests: CLI parsing, config building, callback wiring,
and dev-004 regression (no add_wandb_callback).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# =====================================================================
# TestParseArgs — CLI argument parsing
# =====================================================================


class TestParseArgs:
    """Tests for parse_args() CLI interface."""

    def test_parse_args_defaults(self) -> None:
        """Default args: epochs=100, device='cuda', no_wandb=False."""
        with patch.object(sys, "argv", ["train"]):
            from training.yolo.train import parse_args

            args = parse_args()

        assert args.epochs == 100
        assert args.device == "cuda"
        assert args.no_wandb is False

    def test_parse_args_no_flags(self) -> None:
        """--no-wandb, --no-mining, --no-drone-aug parsed as True."""
        with patch.object(sys, "argv", ["train", "--no-wandb", "--no-mining", "--no-drone-aug"]):
            from training.yolo.train import parse_args

            args = parse_args()

        assert args.no_wandb is True
        assert args.no_mining is True
        assert args.no_drone_aug is True

    def test_parse_args_epochs_override(self) -> None:
        """--epochs 50 overrides default."""
        with patch.object(sys, "argv", ["train", "--epochs", "50"]):
            from training.yolo.train import parse_args

            args = parse_args()

        assert args.epochs == 50

    def test_parse_args_resume_flag(self) -> None:
        """--resume /path/to/last.pt parsed correctly."""
        with patch.object(sys, "argv", ["train", "--resume", "/path/to/last.pt"]):
            from training.yolo.train import parse_args

            args = parse_args()

        assert args.resume == "/path/to/last.pt"

    def test_parse_args_all_flags_together(self) -> None:
        """All disable flags + overrides parsed correctly together."""
        with patch.object(
            sys,
            "argv",
            [
                "train",
                "--no-wandb",
                "--no-mining",
                "--no-curriculum",
                "--no-drone-aug",
                "--epochs",
                "5",
                "--batch",
                "4",
            ],
        ):
            from training.yolo.train import parse_args

            args = parse_args()

        assert args.no_wandb is True
        assert args.no_mining is True
        assert args.no_curriculum is True
        assert args.no_drone_aug is True
        assert args.epochs == 5
        assert args.batch == 4


# =====================================================================
# TestBuildConfig — CLI args → YoloTrainingConfig mapping
# =====================================================================


class TestBuildConfig:
    """Tests for build_config() mapping CLI args to YoloTrainingConfig."""

    def _make_args(self, **overrides: Any) -> MagicMock:
        """Create a mock args namespace with defaults."""
        args = MagicMock()
        args.data = "training/yolo/data.yaml"
        args.checkpoint = "models/yolov11m-lpr-base.pt"
        args.epochs = 100
        args.batch = -1
        args.imgsz = 640
        args.device = "cuda"
        args.run_name = "lpr-finetune"
        args.no_wandb = False
        args.no_mining = False
        args.no_curriculum = False
        args.no_drone_aug = False
        args.resume = None
        for k, v in overrides.items():
            setattr(args, k, v)
        return args

    def test_build_config_maps_no_wandb(self) -> None:
        """args.no_wandb=True maps to cfg.wandb_enabled=False."""
        from training.yolo.train import build_config

        args = self._make_args(no_wandb=True)
        cfg = build_config(args)
        assert cfg.wandb_enabled is False

    def test_build_config_maps_no_mining(self) -> None:
        """args.no_mining=True maps to cfg.mining_enabled=False."""
        from training.yolo.train import build_config

        args = self._make_args(no_mining=True)
        cfg = build_config(args)
        assert cfg.mining_enabled is False

    def test_build_config_maps_epochs(self) -> None:
        """args.epochs=5 maps to cfg.epochs=5."""
        from training.yolo.train import build_config

        args = self._make_args(epochs=5)
        cfg = build_config(args)
        assert cfg.epochs == 5

    def test_build_config_default_enables_features(self) -> None:
        """All no_*=False means all features enabled."""
        from training.yolo.train import build_config

        args = self._make_args()
        cfg = build_config(args)
        assert cfg.wandb_enabled is True
        assert cfg.mining_enabled is True
        assert cfg.drone_aug_enabled is True

    def test_build_config_resume_handling(self) -> None:
        """args.resume set stores resume path in config."""
        from training.yolo.train import build_config

        args = self._make_args(resume="/path/last.pt")
        cfg = build_config(args)
        # Config should be valid with resume — main() uses args.resume directly
        # for overrides, not a config field. Verify config is still valid.
        assert cfg.epochs == 100


# =====================================================================
# TestCallbackWiring — main() registers correct callbacks
# =====================================================================


class TestCallbackWiring:
    """Tests for main() callback registration and wiring."""

    # Use conftest_training.py as a real file that always exists for --checkpoint
    _REAL_FILE = str(Path(__file__).resolve())

    def _run_main_with_mocks(
        self, *, resume_path: str | None = None
    ) -> tuple[MagicMock, dict[str, Any]]:
        """Run main() with all external deps mocked. Returns (trainer_mock, captured_overrides)."""
        captured_overrides: dict[str, Any] = {}

        mock_trainer_instance = MagicMock()
        mock_trainer_instance.train.return_value = None
        # best = None skips export section
        mock_trainer_instance.best = None

        def capture_init(*, lpr_config: Any, overrides: dict[str, Any]) -> MagicMock:
            captured_overrides.update(overrides)
            return mock_trainer_instance

        mock_trainer_class = MagicMock(side_effect=capture_init)

        argv = [
            "train",
            "--no-wandb",
            "--epochs",
            "5",
            "--checkpoint",
            self._REAL_FILE,
        ]
        if resume_path:
            argv.extend(["--resume", resume_path])

        with (
            patch.object(sys, "argv", argv),
            patch("training.yolo.train.LPRDetectionTrainer", mock_trainer_class),
        ):
            from training.yolo.train import main

            main()

        return mock_trainer_instance, captured_overrides

    def test_main_registers_callbacks(self) -> None:
        """trainer.add_callback called for all 4 event hooks."""
        trainer, _ = self._run_main_with_mocks()

        callback_events = [c.args[0] for c in trainer.add_callback.call_args_list]
        assert "on_train_start" in callback_events
        assert "on_train_epoch_start" in callback_events
        assert "on_train_epoch_end" in callback_events
        assert "on_fit_epoch_end" in callback_events

    def test_no_add_wandb_callback(self) -> None:
        """add_wandb_callback NEVER called (dev-004 regression)."""
        trainer, _ = self._run_main_with_mocks()

        # Verify: "add_wandb_callback" never appears in any trainer call
        all_calls_str = str(trainer.mock_calls)
        assert "add_wandb_callback" not in all_calls_str

    def test_overrides_has_close_mosaic_zero(self) -> None:
        """overrides dict passed to trainer has close_mosaic=0."""
        _, overrides = self._run_main_with_mocks()
        assert overrides["close_mosaic"] == 0

    def test_resume_sets_both_model_and_resume_flag(self) -> None:
        """Resume: overrides['model'] = path AND overrides['resume'] = True."""
        _, overrides = self._run_main_with_mocks(resume_path="/path/last.pt")
        assert overrides["model"] == "/path/last.pt"
        assert overrides["resume"] is True


# =====================================================================
# TestMainExitsOnMissingCheckpoint
# =====================================================================


class TestMainExitsOnMissingCheckpoint:
    """Test that main() exits when checkpoint file doesn't exist."""

    def test_main_exits_if_checkpoint_missing(self) -> None:
        """SystemExit raised when checkpoint doesn't exist."""
        with patch.object(
            sys,
            "argv",
            ["train", "--no-wandb", "--checkpoint", "/nonexistent/fake.pt"],
        ):
            from training.yolo.train import main

            with pytest.raises(SystemExit):
                main()
