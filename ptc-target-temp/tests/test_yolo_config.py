"""Tests for YoloTrainingConfig, get_phase, get_phase_aug, generate_data_yaml, setup_checkpoint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# TestYoloTrainingConfigDefaults
# ---------------------------------------------------------------------------


class TestYoloTrainingConfigDefaults:
    """Config importable with sensible defaults."""

    def test_config_importable_with_defaults(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        c = YoloTrainingConfig()
        assert c.epochs == 100
        assert c.warm_end == 0.15
        assert c.refine_start == 0.75
        assert c.device == "cuda"

    def test_config_training_core_defaults(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        c = YoloTrainingConfig()
        assert c.optimizer == "AdamW"
        assert c.lr0 == 0.001
        assert c.lrf == 0.01
        assert c.cos_lr is True
        assert c.patience == 30

    def test_config_phase_aug_dicts_exist(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        c = YoloTrainingConfig()
        assert isinstance(c.warm_aug, dict)
        assert isinstance(c.ramp_aug, dict)
        assert isinstance(c.refine_aug, dict)
        assert "mosaic" in c.warm_aug
        assert "mosaic" in c.ramp_aug
        assert "mosaic" in c.refine_aug

    def test_config_drone_aug_defaults(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        c = YoloTrainingConfig()
        assert c.drone_aug_enabled is True
        assert c.drone_perspective_limit > 0
        assert 0 < c.drone_motion_blur_prob <= 1.0

    def test_config_mining_defaults(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        c = YoloTrainingConfig()
        assert c.mining_min_epoch == 15
        assert c.mining_interval == 5
        assert c.mining_top_fraction == 0.10
        assert c.mining_oversample_factor == 3.0

    def test_config_overrides_applied(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        c = YoloTrainingConfig(epochs=50, device="cpu")
        assert c.epochs == 50
        assert c.device == "cpu"
        # Other defaults unchanged
        assert c.optimizer == "AdamW"
        assert c.warm_end == 0.15


# ---------------------------------------------------------------------------
# TestGetPhase
# ---------------------------------------------------------------------------


class TestGetPhase:
    """Phase boundary logic for 100-epoch config."""

    def test_get_phase_warm_start(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        cfg = YoloTrainingConfig(epochs=100)
        assert cfg.get_phase(0) == "WARM"

    def test_get_phase_warm_last_epoch(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        cfg = YoloTrainingConfig(epochs=100)
        assert cfg.get_phase(14) == "WARM"  # 14/100=0.14 < 0.15

    def test_get_phase_ramp_first_epoch(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        cfg = YoloTrainingConfig(epochs=100)
        assert cfg.get_phase(15) == "RAMP"  # 15/100=0.15 >= warm_end

    def test_get_phase_ramp_last_epoch(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        cfg = YoloTrainingConfig(epochs=100)
        assert cfg.get_phase(74) == "RAMP"  # 74/100=0.74 < 0.75

    def test_get_phase_refine_first_epoch(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        cfg = YoloTrainingConfig(epochs=100)
        assert cfg.get_phase(75) == "REFINE"  # 75/100=0.75 >= refine_start

    def test_get_phase_refine_last_epoch(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        cfg = YoloTrainingConfig(epochs=100)
        assert cfg.get_phase(99) == "REFINE"


# ---------------------------------------------------------------------------
# TestGetPhaseAug
# ---------------------------------------------------------------------------


class TestGetPhaseAug:
    """get_phase_aug returns the correct aug dict for each phase."""

    def test_get_phase_aug_warm_returns_warm_dict(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        cfg = YoloTrainingConfig(epochs=100)
        assert cfg.get_phase_aug(0) == cfg.warm_aug

    def test_get_phase_aug_ramp_returns_ramp_dict(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        cfg = YoloTrainingConfig(epochs=100)
        assert cfg.get_phase_aug(50) == cfg.ramp_aug

    def test_get_phase_aug_refine_returns_refine_dict(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        cfg = YoloTrainingConfig(epochs=100)
        assert cfg.get_phase_aug(80) == cfg.refine_aug


# ---------------------------------------------------------------------------
# TestGetPhaseEdgeCases
# ---------------------------------------------------------------------------


class TestGetPhaseEdgeCases:
    """Edge cases for get_phase."""

    def test_get_phase_single_epoch(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        cfg = YoloTrainingConfig(epochs=1)
        phase = cfg.get_phase(0)
        assert phase in ("WARM", "RAMP", "REFINE")

    def test_get_phase_custom_boundaries(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        cfg = YoloTrainingConfig(epochs=20, warm_end=0.5, refine_start=0.8)
        assert cfg.get_phase(9) == "WARM"  # 9/20=0.45 < 0.5
        assert cfg.get_phase(10) == "RAMP"  # 10/20=0.50 >= 0.5
        assert cfg.get_phase(16) == "REFINE"  # 16/20=0.80 >= 0.8

    def test_warm_aug_mosaic_lower_than_ramp(self) -> None:
        from training.yolo.config import YoloTrainingConfig

        cfg = YoloTrainingConfig()
        assert cfg.warm_aug["mosaic"] < cfg.ramp_aug["mosaic"]


# ---------------------------------------------------------------------------
# TestGenerateDataYaml
# ---------------------------------------------------------------------------


class TestGenerateDataYaml:
    """generate_data_yaml creates valid YAML with correct structure."""

    def test_generate_data_yaml_creates_file(self, tmp_path: Path) -> None:
        import yaml

        from training.yolo.setup_checkpoint import generate_data_yaml

        # Create expected directory structure
        for split in ("train", "valid", "test"):
            (tmp_path / "data" / "roboflow_dataset" / split / "images").mkdir(
                parents=True, exist_ok=True
            )
            (tmp_path / "data" / "roboflow_dataset" / split / "labels").mkdir(
                parents=True, exist_ok=True
            )

        result = generate_data_yaml(tmp_path)
        assert isinstance(result, Path)
        assert result.exists()
        # Valid YAML
        d = yaml.safe_load(result.read_text())
        assert isinstance(d, dict)

    def test_generate_data_yaml_correct_class_name(self, tmp_path: Path) -> None:
        import yaml

        from training.yolo.setup_checkpoint import generate_data_yaml

        for split in ("train", "valid", "test"):
            (tmp_path / "data" / "roboflow_dataset" / split / "images").mkdir(
                parents=True, exist_ok=True
            )
            (tmp_path / "data" / "roboflow_dataset" / split / "labels").mkdir(
                parents=True, exist_ok=True
            )

        result = generate_data_yaml(tmp_path)
        d = yaml.safe_load(result.read_text())
        assert d["names"][0] == "License_Plate"
        assert d["nc"] == 1

    def test_generate_data_yaml_absolute_paths(self, tmp_path: Path) -> None:
        import yaml

        from training.yolo.setup_checkpoint import generate_data_yaml

        for split in ("train", "valid", "test"):
            (tmp_path / "data" / "roboflow_dataset" / split / "images").mkdir(
                parents=True, exist_ok=True
            )
            (tmp_path / "data" / "roboflow_dataset" / split / "labels").mkdir(
                parents=True, exist_ok=True
            )

        result = generate_data_yaml(tmp_path)
        d = yaml.safe_load(result.read_text())
        assert Path(d["train"]).is_absolute()
        assert Path(d["val"]).is_absolute()

    @pytest.mark.real_data
    def test_generate_data_yaml_real_dataset_paths(self) -> None:
        import yaml

        from training.yolo.setup_checkpoint import generate_data_yaml

        project_root = Path(__file__).resolve().parent.parent
        dataset_dir = project_root / "data" / "roboflow_dataset"
        if not dataset_dir.exists():
            pytest.skip("Training data not downloaded")

        result = generate_data_yaml(project_root)
        d = yaml.safe_load(result.read_text())
        assert Path(d["train"]).exists()
        assert Path(d["val"]).exists()


# ---------------------------------------------------------------------------
# TestSetupCheckpoint
# ---------------------------------------------------------------------------


class TestSetupCheckpoint:
    """setup_checkpoint functions with mocked HF / YOLO."""

    def test_download_checkpoint_calls_hf_hub(self, tmp_path: Path) -> None:
        from training.yolo.setup_checkpoint import REPO_ID, download_checkpoint

        local = tmp_path / "checkpoint.pt"
        with (
            patch("training.yolo.setup_checkpoint.LOCAL_PATH", local),
            patch("training.yolo.setup_checkpoint.hf_hub_download") as mock_dl,
        ):
            mock_dl.return_value = str(tmp_path / "model.pt")
            (tmp_path / "model.pt").touch()

            download_checkpoint()

            mock_dl.assert_called_once()
            assert REPO_ID in str(mock_dl.call_args)
            # dev-001: NO local_dir_use_symlinks kwarg
            assert "local_dir_use_symlinks" not in mock_dl.call_args.kwargs

    def test_download_checkpoint_no_symlinks_kwarg(self, tmp_path: Path) -> None:
        """dev-001 regression: local_dir_use_symlinks must NOT be in kwargs."""
        from training.yolo.setup_checkpoint import download_checkpoint

        local = tmp_path / "checkpoint.pt"
        with (
            patch("training.yolo.setup_checkpoint.LOCAL_PATH", local),
            patch("training.yolo.setup_checkpoint.hf_hub_download") as mock_dl,
        ):
            mock_dl.return_value = str(tmp_path / "model.pt")
            (tmp_path / "model.pt").touch()

            download_checkpoint()

            assert "local_dir_use_symlinks" not in mock_dl.call_args.kwargs

    def test_validate_checkpoint_rejects_multiclass(self, tmp_path: Path) -> None:
        from training.yolo.setup_checkpoint import validate_checkpoint

        mock_model = MagicMock()
        mock_model.names = {0: "license-plate", 1: "car"}

        with patch("training.yolo.setup_checkpoint.YOLO", return_value=mock_model):
            with pytest.raises(AssertionError):
                validate_checkpoint(tmp_path / "fake.pt")

    def test_validate_checkpoint_rejects_wrong_name(self, tmp_path: Path) -> None:
        from training.yolo.setup_checkpoint import validate_checkpoint

        mock_model = MagicMock()
        mock_model.names = {0: "cat"}

        with patch("training.yolo.setup_checkpoint.YOLO", return_value=mock_model):
            with pytest.raises(AssertionError):
                validate_checkpoint(tmp_path / "fake.pt")


# ---------------------------------------------------------------------------
# TestMakeYoloConfig
# ---------------------------------------------------------------------------


class TestMakeYoloConfig:
    """make_yolo_config factory in conftest_training.py."""

    def test_make_yolo_config_test_defaults(self) -> None:
        from tests.conftest_training import make_yolo_config

        c = make_yolo_config()
        assert c.epochs == 10
        assert c.device == "cpu"
        assert c.wandb_enabled is False
        assert c.mining_enabled is False
        assert c.imgsz == 64

    def test_make_yolo_config_with_overrides(self) -> None:
        from tests.conftest_training import make_yolo_config

        c = make_yolo_config(epochs=5, mining_enabled=True)
        assert c.epochs == 5
        assert c.mining_enabled is True
        assert c.device == "cpu"  # Default preserved

    def test_make_yolo_config_returns_correct_type(self) -> None:
        from tests.conftest_training import make_yolo_config
        from training.yolo.config import YoloTrainingConfig

        result = make_yolo_config()
        assert isinstance(result, YoloTrainingConfig)
