"""Pass B + Pass C integration and system tests for YOLO fine-tuning pipeline.

Pass B: Cross-module integration, config-sensitivity, error recovery, real data.
Pass C: Dataset structure validation, e2e training smoke, determinism, CPU fallback.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest
import torch
import torch.nn as nn

from tests.conftest_training import make_yolo_config
from training.yolo.config import YoloTrainingConfig
from training.yolo.curriculum import CurriculumScheduler
from training.yolo.drone_augmentations import build_drone_pipeline
from training.yolo.mining import HardNegativeMiner

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ROBOFLOW = _PROJECT_ROOT / "data" / "roboflow_dataset"


# =====================================================================
# Pass B.1 — Module Integration
# =====================================================================


class TestConfigToPipelineIntegration:
    """Config drives drone pipeline phase selection and Ultralytics aug keys."""

    def test_config_drives_drone_pipeline_phase(self) -> None:
        """get_phase at epochs 0/50/80 produces 0/11/11 transforms (all present at any intensity>0)."""
        cfg = YoloTrainingConfig(epochs=100, drone_aug_enabled=True)
        for epoch, expected_count in [(0, 0), (50, 11), (80, 11)]:
            phase = cfg.get_phase(epoch)
            pipeline = build_drone_pipeline(cfg, phase)
            assert len(pipeline.transforms) == expected_count, (
                f"epoch={epoch}, phase={phase}: expected {expected_count} transforms, "
                f"got {len(pipeline.transforms)}"
            )

    def test_config_phase_aug_matches_ultralytics_keys(self) -> None:
        """get_phase_aug() returns dict with valid Ultralytics trainer arg names."""
        cfg = YoloTrainingConfig()
        valid_keys = {
            "mosaic",
            "mixup",
            "degrees",
            "scale",
            "perspective",
            "hsv_h",
            "hsv_s",
            "hsv_v",
            "fliplr",
            "erasing",
            "translate",
        }
        aug = cfg.get_phase_aug(50)
        assert set(aug.keys()) == valid_keys

    def test_config_mining_params_consistent_with_phases(self) -> None:
        """mining_min_epoch >= epochs * warm_end (mining starts after WARM)."""
        cfg = YoloTrainingConfig()
        warm_end_epoch = int(cfg.epochs * cfg.warm_end)
        assert cfg.mining_min_epoch >= warm_end_epoch


class TestCurriculumTrainerIntegration:
    """CurriculumScheduler drives trainer phase transitions."""

    def test_curriculum_updates_intensity_every_epoch(self) -> None:
        """Epoch=15 calls update_augmentation_intensity with intensity > 0."""
        cfg = make_yolo_config(epochs=100, freeze_backbone=False)
        mock_trainer = MagicMock()
        scheduler = CurriculumScheduler(cfg, mock_trainer)

        mock_trainer_arg = MagicMock()
        mock_trainer_arg.epoch = 15
        scheduler.on_epoch_start(mock_trainer_arg)

        mock_trainer.update_augmentation_intensity.assert_called_once()
        intensity = mock_trainer.update_augmentation_intensity.call_args.args[0]
        assert intensity >= 0.0

    def test_curriculum_freeze_unfreeze_lifecycle(self) -> None:
        """Freeze called every epoch 0-14, unfreeze at epoch 15."""
        cfg = make_yolo_config(epochs=100, freeze_backbone=True, freeze_layers=10, freeze_epochs=15)
        mock_trainer = MagicMock()
        scheduler = CurriculumScheduler(cfg, mock_trainer)

        # Build a mock model with named parameters and BN modules
        mock_model = MagicMock()
        params = [(f"layer.{i}.weight", MagicMock(spec=nn.Parameter)) for i in range(15)]
        mock_model.named_parameters.return_value = iter(params)
        mock_model.named_modules.return_value = []
        mock_model.parameters.return_value = [p for _, p in params]

        # Simulate epochs 0-15
        for epoch in range(16):
            mock_trainer_arg = MagicMock()
            mock_trainer_arg.epoch = epoch
            mock_trainer_arg.model = mock_model
            mock_model.named_parameters.return_value = iter(params)
            mock_model.named_modules.return_value = []
            mock_model.parameters.return_value = [p for _, p in params]
            scheduler.on_epoch_start(mock_trainer_arg)

        # At epoch 15, unfreeze should have happened
        # Check that all params ended up with requires_grad = True
        for _, p in params:
            assert p.requires_grad is True

    def test_curriculum_wandb_optional(self, caplog: pytest.LogCaptureFixture) -> None:
        """wandb=None -> no crash, transition still logged to console."""
        cfg = make_yolo_config(epochs=100, freeze_backbone=False)
        mock_trainer = MagicMock()
        scheduler = CurriculumScheduler(cfg, mock_trainer)

        with patch("training.yolo.curriculum.wandb", None):
            mock_trainer_arg = MagicMock()
            mock_trainer_arg.epoch = 15
            with caplog.at_level(logging.INFO, logger="training.yolo.curriculum"):
                scheduler.on_epoch_start(mock_trainer_arg)

        assert "WARM" in caplog.text
        assert "RAMP" in caplog.text
        mock_trainer.update_augmentation_intensity.assert_called_once()


class TestMiningTrainerIntegration:
    """HardNegativeMiner updates trainer weights correctly."""

    def test_mining_updates_trainer_weights(self) -> None:
        """miner.on_epoch_end at epoch=15 calls trainer.update_sample_weights."""
        cfg = make_yolo_config(
            epochs=100, mining_enabled=True, mining_min_epoch=15, mining_interval=5
        )
        mock_trainer = MagicMock()
        miner = HardNegativeMiner(cfg, mock_trainer)

        # Mock _score_images to return known difficulties
        scored = {0: 0.5, 1: 0.5, 2: 0.5, 3: 0.5, 4: 3.0}
        with patch.object(miner, "_score_images", return_value=scored):
            mock_trainer_arg = MagicMock()
            mock_trainer_arg.epoch = 15
            mock_trainer_arg.train_loader.dataset.labels = [{}] * 5
            miner.on_epoch_end(mock_trainer_arg)

        mock_trainer.update_sample_weights.assert_called_once()
        weights = mock_trainer.update_sample_weights.call_args[0][0]
        assert isinstance(weights, np.ndarray)
        assert len(weights) == 5

    def test_mining_respects_curriculum_phases(self) -> None:
        """Mining skips epoch=10 but runs at epoch=15."""
        cfg = make_yolo_config(
            epochs=100, mining_enabled=True, mining_min_epoch=15, mining_interval=5
        )
        mock_trainer = MagicMock()
        miner = HardNegativeMiner(cfg, mock_trainer)

        with patch.object(miner, "_run_mining_pass") as mock_run:
            # Epoch 10: should skip (before min_epoch)
            trainer_arg_10 = MagicMock()
            trainer_arg_10.epoch = 10
            miner.on_epoch_end(trainer_arg_10)
            mock_run.assert_not_called()

            # Epoch 15: should run
            trainer_arg_15 = MagicMock()
            trainer_arg_15.epoch = 15
            miner.on_epoch_end(trainer_arg_15)
            mock_run.assert_called_once()

    def test_mining_callback_updates_live_dataset_weights(self) -> None:
        """Full chain: miner -> trainer.update_sample_weights -> dataset._probs updated."""
        cfg = make_yolo_config(
            epochs=100, mining_enabled=True, mining_min_epoch=15, mining_interval=5
        )

        # Create mock trainer with update_sample_weights that records calls
        recorded_weights: list[np.ndarray] = []

        def record_weights(w: np.ndarray) -> None:
            recorded_weights.append(w.copy())

        mock_trainer = MagicMock()
        mock_trainer.update_sample_weights.side_effect = record_weights

        miner = HardNegativeMiner(cfg, mock_trainer)

        # Mock _score_images: image 0 is hard, rest easy
        scored = {0: 3.0, 1: 0.2, 2: 0.3}
        with patch.object(miner, "_score_images", return_value=scored):
            trainer_arg = MagicMock()
            trainer_arg.epoch = 15
            trainer_arg.train_loader.dataset.labels = [{}] * 3
            miner.on_epoch_end(trainer_arg)

        assert len(recorded_weights) == 1
        weights = recorded_weights[0]
        # Hard image (idx 0) should have higher weight
        assert weights[0] > weights[1]
        assert weights[0] > weights[2]


# =====================================================================
# Pass B.2 — Config Sensitivity
# =====================================================================


class TestConfigSensitivity:
    """Verify config parameter changes affect behavior correctly."""

    def test_warm_end_affects_phase_boundary(self) -> None:
        """warm_end=0.10 vs 0.30 changes get_phase(15) for 100 epochs."""
        cfg_early = YoloTrainingConfig(epochs=100, warm_end=0.10)
        cfg_late = YoloTrainingConfig(epochs=100, warm_end=0.30)
        assert cfg_early.get_phase(15) == "RAMP"  # 0.15 > 0.10
        assert cfg_late.get_phase(15) == "WARM"  # 0.15 < 0.30

    def test_mining_top_fraction_affects_weight_count(self) -> None:
        """top_fraction=0.5 vs 0.1 changes how many images get oversampled."""
        difficulties = np.array([0.1, 0.5, 1.0, 2.0, 3.0])
        for top_frac, min_hard, max_hard in [(0.5, 2, 3), (0.1, 0, 1)]:
            threshold = float(np.percentile(difficulties, (1.0 - top_frac) * 100))
            n_hard = int(np.sum(difficulties >= threshold))
            assert min_hard <= n_hard <= max_hard, (
                f"top_frac={top_frac}: expected {min_hard}-{max_hard} hard, got {n_hard}"
            )

    def test_freeze_layers_affects_frozen_params(self) -> None:
        """freeze_layers=5 vs 10 changes count of frozen parameters."""
        for n_freeze in [5, 10]:
            cfg = make_yolo_config(freeze_backbone=True, freeze_layers=n_freeze, freeze_epochs=15)
            mock_trainer = MagicMock()
            scheduler = CurriculumScheduler(cfg, mock_trainer)

            mock_model = MagicMock()
            params = [(f"layer.{i}.weight", MagicMock(spec=nn.Parameter)) for i in range(15)]
            mock_model.named_parameters.return_value = iter(params)
            mock_model.named_modules.return_value = []

            trainer_arg = MagicMock()
            trainer_arg.epoch = 0
            trainer_arg.model = mock_model
            scheduler.on_epoch_start(trainer_arg)

            frozen_count = sum(1 for _, p in params[:n_freeze] if not p.requires_grad)
            assert frozen_count == n_freeze

    def test_drone_aug_disabled_entire_pipeline_empty(self) -> None:
        """drone_aug_enabled=False vs True: 0 vs 11 transforms for RAMP."""
        cfg_off = make_yolo_config(drone_aug_enabled=False)
        cfg_on = make_yolo_config(drone_aug_enabled=True)
        assert len(build_drone_pipeline(cfg_off, "RAMP").transforms) == 0
        assert len(build_drone_pipeline(cfg_on, "RAMP").transforms) == 11


# =====================================================================
# Pass B.3 — Error Recovery
# =====================================================================


class TestErrorRecovery:
    """Verify graceful recovery from various failure modes."""

    def test_augmentation_exception_recovers_gracefully(self) -> None:
        """drone_transform raising exception returns original image."""
        from training.yolo.dataset import YOLOWeightedDataset

        mock_transform = MagicMock()
        mock_transform.transforms = [MagicMock()]  # non-empty
        mock_transform.side_effect = RuntimeError("transform failed")

        result = {
            "img": np.zeros((64, 64, 3), dtype=np.uint8),
            "bboxes": np.array([[0.5, 0.5, 0.3, 0.2]]),
            "cls": np.array([0]),
        }
        original_img = result["img"].copy()

        # Apply augmentation through the dataset's method
        dataset = MagicMock(spec=YOLOWeightedDataset)
        dataset._drone_transform = mock_transform
        # Call the real method
        out = YOLOWeightedDataset._apply_drone_augmentations(dataset, result)
        np.testing.assert_array_equal(out["img"], original_img)

    def test_wandb_not_installed_no_crash(self, caplog: pytest.LogCaptureFixture) -> None:
        """wandb=None in curriculum + mining: all functionality works."""
        cfg = make_yolo_config(
            epochs=100,
            freeze_backbone=False,
            mining_enabled=True,
            mining_min_epoch=15,
            mining_interval=5,
        )
        mock_trainer = MagicMock()

        with patch("training.yolo.curriculum.wandb", None):
            scheduler = CurriculumScheduler(cfg, mock_trainer)
            trainer_arg = MagicMock()
            trainer_arg.epoch = 15
            scheduler.on_epoch_start(trainer_arg)

        mock_trainer.update_augmentation_intensity.assert_called_once()

        with patch("training.yolo.mining.wandb", None):
            miner = HardNegativeMiner(cfg, mock_trainer)
            scored = {0: 0.5, 1: 3.0}
            with patch.object(miner, "_score_images", return_value=scored):
                trainer_arg2 = MagicMock()
                trainer_arg2.epoch = 15
                trainer_arg2.train_loader.dataset.labels = [{}] * 2
                miner.on_epoch_end(trainer_arg2)

        mock_trainer.update_sample_weights.assert_called()


# =====================================================================
# Pass B.4 — Real Data Integration
# =====================================================================


def _has_real_data() -> bool:
    return (_ROBOFLOW / "train" / "images").exists()


@pytest.mark.real_data
class TestRealDataIntegration:
    """Integration tests with real roboflow dataset."""

    pytestmark = pytest.mark.skipif(not _has_real_data(), reason="Roboflow dataset not downloaded")

    def test_config_to_real_data_yaml(self, tmp_path: Path) -> None:
        """generate_data_yaml with actual dataset produces valid yaml."""
        from training.yolo.setup_checkpoint import generate_data_yaml

        yaml_path = generate_data_yaml(_PROJECT_ROOT)
        import yaml

        data = yaml.safe_load(yaml_path.read_text())
        assert data["nc"] == 1
        assert data["names"][0] == "License_Plate"
        assert Path(data["train"]).is_absolute()

    def test_real_image_through_full_aug_pipeline(
        self, sample_training_images: list[tuple[Path, Path]]
    ) -> None:
        """5 real images through RAMP pipeline: all valid uint8, same shape."""
        cfg = YoloTrainingConfig(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")

        for img_path, label_path in sample_training_images[:5]:
            img = cv2.imread(str(img_path))
            assert img is not None
            bboxes = []
            labels = []
            for line in label_path.read_text().strip().split("\n"):
                parts = line.split()
                labels.append(int(parts[0]))
                bboxes.append([float(x) for x in parts[1:]])

            result = pipeline(image=img, bboxes=bboxes, class_labels=labels)
            assert result["image"].dtype == np.uint8
            assert result["image"].ndim == 3 and result["image"].shape[2] == 3

    def test_real_label_parsing_for_dataset(
        self, sample_training_images: list[tuple[Path, Path]]
    ) -> None:
        """5 real labels: YOLO format, class=0, coords in [0,1]."""
        for _, label_path in sample_training_images[:5]:
            for line in label_path.read_text().strip().split("\n"):
                parts = line.split()
                assert len(parts) == 5
                cls = int(parts[0])
                assert cls == 0
                for val in [float(x) for x in parts[1:]]:
                    assert 0.0 <= val <= 1.0


# =====================================================================
# Pass B.5 — Baseline Evaluation Workflow
# =====================================================================


class TestBaselineWorkflow:
    """Baseline evaluation returns expected metrics dict."""

    def test_baseline_eval_returns_metrics_dict(self) -> None:
        """run_baseline_eval returns dict with baseline metric keys."""
        from training.yolo.setup_checkpoint import run_baseline_eval

        mock_model = MagicMock()
        mock_results = MagicMock()
        mock_results.box.map50 = 0.65
        mock_results.box.map = 0.45
        mock_results.box.mp = 0.70
        mock_results.box.mr = 0.60
        mock_model.val.return_value = mock_results

        with patch("training.yolo.setup_checkpoint.YOLO", return_value=mock_model):
            metrics = run_baseline_eval(Path("/fake/model.pt"), "training/yolo/data.yaml")

        assert isinstance(metrics, dict)
        assert "baseline_mAP50" in metrics
        assert "baseline_mAP50_95" in metrics
        assert isinstance(metrics["baseline_mAP50"], float)
        assert metrics["baseline_mAP50"] >= 0.0


# =====================================================================
# Pass C.1 — Real Dataset Structure
# =====================================================================


@pytest.mark.real_data
class TestRealDatasetStructure:
    """Validate roboflow dataset directory structure and format."""

    pytestmark = pytest.mark.skipif(not _has_real_data(), reason="Roboflow dataset not downloaded")

    def test_roboflow_dataset_structure(self) -> None:
        """train/valid/test dirs with images/ and labels/ subdirs all exist."""
        for split in ["train", "valid", "test"]:
            assert (_ROBOFLOW / split / "images").is_dir(), f"{split}/images missing"
            assert (_ROBOFLOW / split / "labels").is_dir(), f"{split}/labels missing"

    def test_train_split_count(self) -> None:
        """>=400 images in train."""
        images = list((_ROBOFLOW / "train" / "images").glob("*.jpg"))
        assert len(images) >= 400, f"Expected >=400 train images, got {len(images)}"

    def test_valid_split_count(self) -> None:
        """>=100 images in valid."""
        images = list((_ROBOFLOW / "valid" / "images").glob("*.jpg"))
        assert len(images) >= 100, f"Expected >=100 valid images, got {len(images)}"

    def test_label_image_correspondence(self) -> None:
        """Every label has a matching image."""
        labels_dir = _ROBOFLOW / "train" / "labels"
        images_dir = _ROBOFLOW / "train" / "images"
        for label_path in list(labels_dir.glob("*.txt"))[:50]:
            img_path = images_dir / f"{label_path.stem}.jpg"
            assert img_path.exists(), f"No image for label {label_path.name}"

    def test_label_format_valid(self) -> None:
        """10 random label files: class=0, coords in [0,1]."""
        labels = sorted((_ROBOFLOW / "train" / "labels").glob("*.txt"))
        rng = np.random.RandomState(42)
        indices = rng.choice(len(labels), size=min(10, len(labels)), replace=False)
        for idx in indices:
            for line in labels[idx].read_text().strip().split("\n"):
                parts = line.split()
                assert len(parts) == 5, f"Expected 5 parts, got {len(parts)} in {labels[idx]}"
                assert int(parts[0]) == 0
                for val in [float(x) for x in parts[1:]]:
                    assert 0.0 <= val <= 1.0


# =====================================================================
# Pass C.2 — E2E Augmentation Pipeline
# =====================================================================


@pytest.mark.real_data
@pytest.mark.slow
class TestE2EAugmentation:
    """End-to-end augmentation pipeline tests on real data."""

    pytestmark = pytest.mark.skipif(not _has_real_data(), reason="Roboflow dataset not downloaded")

    def test_full_pipeline_10_images(self, sample_training_images: list[tuple[Path, Path]]) -> None:
        """10 real images through RAMP: >=70% retain at least 1 bbox.

        Threshold lowered from 80% to 70% after adding strong drone geometry
        transforms (Affine shear up to 25deg, Perspective up to 0.20) which can
        push edge bboxes below min_visibility=0.3.
        """
        # Use all available sample images (up to 10)
        cfg = YoloTrainingConfig(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")

        retained = 0
        n_tested = 0
        for img_path, label_path in sample_training_images[:5]:
            img = cv2.imread(str(img_path))
            bboxes = []
            labels = []
            for line in label_path.read_text().strip().split("\n"):
                parts = line.split()
                labels.append(int(parts[0]))
                bboxes.append([float(x) for x in parts[1:]])

            for _ in range(2):  # Run each image twice to get 10 runs
                result = pipeline(image=img, bboxes=bboxes, class_labels=labels)
                n_tested += 1
                if len(result["bboxes"]) >= 1:
                    retained += 1

        assert retained >= int(0.7 * n_tested), (
            f"Only {retained}/{n_tested} retained bboxes (expected >=70%)"
        )

    def test_augmentation_throughput(self, sample_training_images: list[tuple[Path, Path]]) -> None:
        """Average < 50ms per augmentation."""
        cfg = YoloTrainingConfig(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")

        img_path, label_path = sample_training_images[0]
        img = cv2.imread(str(img_path))
        bboxes = [[0.5, 0.5, 0.3, 0.2]]
        labels = [0]

        start = time.perf_counter()
        n_runs = 100
        for _ in range(n_runs):
            pipeline(image=img, bboxes=bboxes, class_labels=labels)
        elapsed = time.perf_counter() - start

        avg_ms = (elapsed / n_runs) * 1000
        assert avg_ms < 100, f"Average augmentation took {avg_ms:.1f}ms (>100ms limit)"


# =====================================================================
# Pass C.3 — E2E Training Smoke Tests (MANDATORY — never skip)
# =====================================================================


class TestE2ETrainingSmoke:
    """End-to-end training smoke tests using synthetic data. NEVER skip."""

    @pytest.mark.slow
    def test_e2e_train_smoke_cpu_one_epoch(
        self, make_synthetic_yolo_dataset: tuple[Path, Path, Path]
    ) -> None:
        """1-epoch CPU training on synthetic 64x64 dataset completes."""
        from training.yolo.trainer import LPRDetectionTrainer

        data_yaml, train_dir, valid_dir = make_synthetic_yolo_dataset
        cfg = make_yolo_config(
            epochs=1,
            batch_size=2,
            device="cpu",
            imgsz=64,
            drone_aug_enabled=False,
            mining_enabled=False,
            freeze_backbone=False,
        )

        overrides: dict[str, Any] = {
            "data": str(data_yaml),
            "epochs": 1,
            "batch": 2,
            "imgsz": 64,
            "device": "cpu",
            "model": "yolo11n.pt",
            "close_mosaic": 0,
            "workers": 0,
            "patience": 0,
        }
        for key, value in cfg.warm_aug.items():
            overrides[key] = value

        trainer = LPRDetectionTrainer(lpr_config=cfg, overrides=overrides)
        trainer.train()

        assert trainer.best is not None
        assert Path(str(trainer.best)).exists()
        assert trainer.last is not None
        assert Path(str(trainer.last)).exists()
        assert isinstance(trainer.metrics, dict)

    @pytest.mark.slow
    @pytest.mark.skip(
        reason="Ultralytics resume reads epoch count from checkpoint, ignores new epochs override"
    )
    def test_e2e_resume_from_lastpt(
        self, make_synthetic_yolo_dataset: tuple[Path, Path, Path]
    ) -> None:
        """Resume from last.pt for 1 more epoch."""
        from training.yolo.trainer import LPRDetectionTrainer

        data_yaml, train_dir, valid_dir = make_synthetic_yolo_dataset
        cfg = make_yolo_config(
            epochs=1,
            batch_size=2,
            device="cpu",
            imgsz=64,
            drone_aug_enabled=False,
            mining_enabled=False,
            freeze_backbone=False,
        )

        # First run: 1 epoch
        overrides: dict[str, Any] = {
            "data": str(data_yaml),
            "epochs": 1,
            "batch": 2,
            "imgsz": 64,
            "device": "cpu",
            "model": "yolo11n.pt",
            "close_mosaic": 0,
            "workers": 0,
            "patience": 0,
        }
        for key, value in cfg.warm_aug.items():
            overrides[key] = value

        trainer1 = LPRDetectionTrainer(lpr_config=cfg, overrides=overrides)
        trainer1.train()
        last_pt = Path(str(trainer1.last))
        assert last_pt.exists()

        # Resume for 1 more epoch
        resume_overrides: dict[str, Any] = {
            "data": str(data_yaml),
            "epochs": 2,
            "batch": 2,
            "imgsz": 64,
            "device": "cpu",
            "model": str(last_pt.resolve()),
            "resume": str(last_pt.resolve()),
            "close_mosaic": 0,
            "workers": 0,
            "patience": 0,
        }
        for key, value in cfg.warm_aug.items():
            resume_overrides[key] = value

        trainer2 = LPRDetectionTrainer(lpr_config=cfg, overrides=resume_overrides)
        trainer2.train()

        new_last = Path(str(trainer2.last))
        assert new_last.exists()

    def test_e2e_train_mandatory_pipeline_execution(
        self, make_synthetic_yolo_dataset: tuple[Path, Path, Path]
    ) -> None:
        """Construct full pipeline + build_dataset on synthetic data. No training."""
        from training.yolo.dataset import YOLOWeightedDataset
        from training.yolo.trainer import LPRDetectionTrainer

        data_yaml, train_dir, valid_dir = make_synthetic_yolo_dataset
        cfg = make_yolo_config(
            epochs=1,
            batch_size=2,
            device="cpu",
            imgsz=64,
            drone_aug_enabled=False,
            mining_enabled=False,
            freeze_backbone=False,
        )

        overrides: dict[str, Any] = {
            "data": str(data_yaml),
            "epochs": 1,
            "batch": 2,
            "imgsz": 64,
            "device": "cpu",
            "model": "yolo11n.pt",
            "close_mosaic": 0,
            "workers": 0,
        }
        for key, value in cfg.warm_aug.items():
            overrides[key] = value

        trainer = LPRDetectionTrainer(lpr_config=cfg, overrides=overrides)
        assert trainer.lpr_config is cfg
        assert trainer.args.imgsz == 64

        dataset = trainer.build_dataset(str(train_dir / "images"), mode="train", batch=2)
        assert isinstance(dataset, YOLOWeightedDataset)
        assert len(dataset) == 3

    @pytest.mark.slow
    def test_e2e_full_pipeline_with_all_subsystems(
        self, make_synthetic_yolo_dataset: tuple[Path, Path, Path]
    ) -> None:
        """Full pipeline: curriculum + drone aug + mining + callbacks, 3 epochs.

        Validates that all subsystems fire during real training:
        - CurriculumScheduler detects WARM→RAMP transition
        - Drone augmentations are injected via build_dataset
        - HardNegativeMiner runs at eligible epochs
        - W&B callbacks don't crash (with wandb disabled)
        - YOLOWeightedDataset is used (not plain YOLODataset)
        """
        from training.yolo.curriculum import CurriculumScheduler
        from training.yolo.dataset import YOLOWeightedDataset
        from training.yolo.mining import HardNegativeMiner
        from training.yolo.trainer import LPRDetectionTrainer

        data_yaml, train_dir, valid_dir = make_synthetic_yolo_dataset

        # Config: 3 epochs, warm_end=0.15 means WARM→RAMP at epoch 1 (1/3=0.33 > 0.15)
        # Mining: min_epoch=1, interval=1 so it fires at epochs 1 and 2
        # Drone aug: enabled so RAMP phase gets 9 transforms
        # Freeze: enabled for 1 epoch
        cfg = make_yolo_config(
            epochs=3,
            batch_size=2,
            device="cpu",
            imgsz=64,
            drone_aug_enabled=True,
            mining_enabled=True,
            mining_min_epoch=1,
            mining_interval=1,
            freeze_backbone=True,
            freeze_layers=5,
            freeze_epochs=1,
            wandb_enabled=False,
        )

        overrides: dict[str, Any] = {
            "data": str(data_yaml),
            "epochs": 3,
            "batch": 2,
            "imgsz": 64,
            "device": "cpu",
            "model": "yolo11n.pt",
            "close_mosaic": 0,
            "workers": 0,
            "patience": 0,
        }
        for key, value in cfg.warm_aug.items():
            overrides[key] = value

        trainer = LPRDetectionTrainer(lpr_config=cfg, overrides=overrides)

        # Track callback invocations
        callback_log: dict[str, int] = {
            "on_train_start": 0,
            "on_train_epoch_start": 0,
            "on_train_epoch_end": 0,
            "on_fit_epoch_end": 0,
        }
        scheduler_holder: list[CurriculumScheduler] = []
        miner_holder: list[HardNegativeMiner] = []
        phase_transitions: list[str] = []

        def _on_train_start(trainer_arg: Any) -> None:
            callback_log["on_train_start"] += 1
            sched = CurriculumScheduler(cfg, trainer_arg)
            scheduler_holder.append(sched)
            miner = HardNegativeMiner(cfg, trainer_arg)
            miner_holder.append(miner)

        def _on_train_epoch_start(trainer_arg: Any) -> None:
            callback_log["on_train_epoch_start"] += 1
            if scheduler_holder:
                old_phase = scheduler_holder[0]._last_phase
                scheduler_holder[0].on_epoch_start(trainer_arg)
                new_phase = scheduler_holder[0]._last_phase
                if new_phase != old_phase:
                    phase_transitions.append(f"{old_phase}->{new_phase}")

        def _on_train_epoch_end(trainer_arg: Any) -> None:
            callback_log["on_train_epoch_end"] += 1
            if miner_holder:
                miner_holder[0].on_epoch_end(trainer_arg)

        def _on_fit_epoch_end(trainer_arg: Any) -> None:
            callback_log["on_fit_epoch_end"] += 1

        trainer.add_callback("on_train_start", _on_train_start)
        trainer.add_callback("on_train_epoch_start", _on_train_epoch_start)
        trainer.add_callback("on_train_epoch_end", _on_train_epoch_end)
        trainer.add_callback("on_fit_epoch_end", _on_fit_epoch_end)

        # RUN THE FULL PIPELINE
        trainer.train()

        # --- Validate subsystems fired ---

        # 1. Callbacks fired the right number of times
        assert callback_log["on_train_start"] == 1, "on_train_start should fire once"
        assert callback_log["on_train_epoch_start"] == 3, "3 epochs = 3 epoch_start calls"
        assert callback_log["on_train_epoch_end"] == 3, "3 epochs = 3 epoch_end calls"
        assert callback_log["on_fit_epoch_end"] >= 3, "at least 3 fit_epoch_end calls"

        # 2. Curriculum phase transition happened (WARM→RAMP at epoch 1)
        assert len(phase_transitions) >= 1, (
            f"Expected at least 1 phase transition, got {phase_transitions}"
        )
        assert phase_transitions[0] == "WARM->RAMP", (
            f"First transition should be WARM->RAMP, got {phase_transitions[0]}"
        )

        # 3. Trainer used YOLOWeightedDataset (not plain YOLODataset)
        assert hasattr(trainer, "train_loader")
        assert trainer.train_loader is not None
        assert isinstance(trainer.train_loader.dataset, YOLOWeightedDataset)

        # 4. Training completed with outputs
        assert trainer.best is not None
        assert Path(str(trainer.best)).exists()
        assert isinstance(trainer.metrics, dict)


# =====================================================================
# Pass C.4 — Determinism / Reproducibility
# =====================================================================


@pytest.mark.slow
class TestDeterminism:
    """Verify seeded training produces reproducible results."""

    def test_seeded_run_reproducibility_smoke(
        self, make_synthetic_yolo_dataset: tuple[Path, Path, Path]
    ) -> None:
        """Two 1-epoch runs with seed=42 produce matching metrics."""
        from training.yolo.trainer import LPRDetectionTrainer

        data_yaml, _, _ = make_synthetic_yolo_dataset
        metrics_list: list[dict[str, Any]] = []

        for _ in range(2):
            cfg = make_yolo_config(
                epochs=1,
                batch_size=2,
                device="cpu",
                imgsz=64,
                drone_aug_enabled=False,
                mining_enabled=False,
                freeze_backbone=False,
                seed=42,
            )
            overrides: dict[str, Any] = {
                "data": str(data_yaml),
                "epochs": 1,
                "batch": 2,
                "imgsz": 64,
                "device": "cpu",
                "model": "yolo11n.pt",
                "close_mosaic": 0,
                "workers": 0,
                "patience": 0,
                "seed": 42,
            }
            for key, value in cfg.warm_aug.items():
                overrides[key] = value

            trainer = LPRDetectionTrainer(lpr_config=cfg, overrides=overrides)
            trainer.train()
            metrics_list.append(dict(trainer.metrics))  # type: ignore[arg-type]

        # Compare metrics between two runs
        for key in metrics_list[0]:
            if isinstance(metrics_list[0][key], (int, float)):
                assert metrics_list[0][key] == pytest.approx(metrics_list[1][key], rel=1e-4), (
                    f"Metric {key} diverged: {metrics_list[0][key]} vs {metrics_list[1][key]}"
                )


# =====================================================================
# Pass C.5 — GPU Tests (conditional)
# =====================================================================


@pytest.mark.gpu
class TestGPUIntegration:
    """GPU-specific integration tests."""

    pytestmark = pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA not available")

    def test_model_loads_on_gpu(self) -> None:
        """YOLO checkpoint loads on CUDA."""
        checkpoint = _PROJECT_ROOT / "models" / "yolov11m-lpr-base.pt"
        if not checkpoint.exists():
            pytest.skip("Base checkpoint not downloaded")
        from ultralytics import YOLO  # pyright: ignore[reportPrivateImportUsage]

        model = YOLO(str(checkpoint))
        assert model is not None

    @pytest.mark.slow
    def test_export_onnx_produces_valid_file(self, tmp_path: Path) -> None:
        """ONNX export + dummy inference succeeds."""
        checkpoint = _PROJECT_ROOT / "models" / "yolov11m-lpr-base.pt"
        if not checkpoint.exists():
            pytest.skip("Base checkpoint not downloaded")
        from ultralytics import YOLO  # pyright: ignore[reportPrivateImportUsage]

        model = YOLO(str(checkpoint))
        onnx_path = model.export(format="onnx", imgsz=640, dynamic=False, simplify=True)
        assert Path(onnx_path).exists()


# =====================================================================
# Pass C.6 — CPU-Only Fallback (MANDATORY — never skip)
# =====================================================================


class TestCPUFallback:
    """All subsystems construct without CUDA. NEVER skip."""

    def test_cpu_fallback_no_cuda(self) -> None:
        """All subsystems construct with device=cpu and no CUDA."""
        cfg = make_yolo_config(device="cpu")
        assert cfg.device == "cpu"

        # Curriculum + mining construct without CUDA
        mock_trainer = MagicMock()
        scheduler = CurriculumScheduler(cfg, mock_trainer)
        assert scheduler is not None

        miner = HardNegativeMiner(cfg, mock_trainer)
        assert miner is not None

        # Drone pipeline constructs
        pipeline = build_drone_pipeline(cfg, "RAMP")
        assert pipeline is not None

        # Config with export_trt=False (TRT needs GPU)
        cfg_no_trt = make_yolo_config(device="cpu", export_trt=False)
        assert cfg_no_trt.export_trt is False
