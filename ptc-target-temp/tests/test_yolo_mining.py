"""Tests for hard-negative mining (chunk-03)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tests.conftest_training import make_yolo_config
from training.yolo.mining import HardNegativeMiner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ROBOFLOW_LABELS = Path("data/roboflow_dataset/train/labels")


def _make_miner(
    *,
    mining_enabled: bool = True,
    mining_min_epoch: int = 15,
    mining_interval: int = 5,
    mining_top_fraction: float = 0.10,
    mining_oversample_factor: float = 3.0,
) -> tuple[HardNegativeMiner, MagicMock]:
    """Return (miner, mock_trainer) with a test-friendly config."""
    cfg = make_yolo_config(
        mining_enabled=mining_enabled,
        mining_min_epoch=mining_min_epoch,
        mining_interval=mining_interval,
        mining_top_fraction=mining_top_fraction,
        mining_oversample_factor=mining_oversample_factor,
    )
    mock_trainer = MagicMock()
    return HardNegativeMiner(cfg, mock_trainer), mock_trainer


def _make_trainer_arg(epoch: int = 15) -> MagicMock:
    """Build a mock trainer_arg that on_epoch_end receives."""
    trainer_arg = MagicMock()
    trainer_arg.epoch = epoch
    return trainer_arg


# ---------------------------------------------------------------------------
# TestComputeDifficulty — golden formula verification
# ---------------------------------------------------------------------------


class TestComputeDifficulty:
    """Verify the static difficulty scoring formula."""

    def test_no_detections_max_difficulty(self) -> None:
        """No detections on 5 GT plates → max difficulty 3.0."""
        assert HardNegativeMiner.compute_difficulty(5, 0, 0.0) == 3.0

    def test_perfect_detection_zero_difficulty(self) -> None:
        """All detected with perfect confidence → ~0.0."""
        assert HardNegativeMiner.compute_difficulty(5, 5, 1.0) < 0.001

    def test_partial_miss_correct_formula(self) -> None:
        """4 GT, 2 detected, avg_conf=0.8 → (1-0.8)+2*(2/4) = 1.2."""
        assert HardNegativeMiner.compute_difficulty(4, 2, 0.8) == pytest.approx(1.2)

    def test_all_detected_low_confidence(self) -> None:
        """3 GT, 3 detected, avg_conf=0.4 → (1-0.4)+0 = 0.6."""
        assert HardNegativeMiner.compute_difficulty(3, 3, 0.4) == pytest.approx(0.6)

    def test_over_detection_no_negative_miss_rate(self) -> None:
        """More detections than GT → miss_rate clamped to 0."""
        # (1-0.9) + 2*max(0, 2-5)/2 = 0.1 + 0 = 0.1
        assert HardNegativeMiner.compute_difficulty(2, 5, 0.9) == pytest.approx(0.1)

    def test_single_gt_single_det(self) -> None:
        """1 GT, 1 det, avg_conf=0.7 → (1-0.7)+0 = 0.3."""
        assert HardNegativeMiner.compute_difficulty(1, 1, 0.7) == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# TestMiningGuardConditions — when mining runs vs skips
# ---------------------------------------------------------------------------


class TestMiningGuardConditions:
    """Verify guard logic in on_epoch_end."""

    def test_mining_disabled_skips(self) -> None:
        """mining_enabled=False → _run_mining_pass never called."""
        miner, _trainer = _make_miner(mining_enabled=False)
        with patch.object(miner, "_run_mining_pass") as mock_run:
            miner.on_epoch_end(_make_trainer_arg(epoch=15))
            mock_run.assert_not_called()

    def test_mining_before_min_epoch_skips(self) -> None:
        """epoch=10 < mining_min_epoch=15 → skip."""
        miner, _trainer = _make_miner(mining_min_epoch=15)
        with patch.object(miner, "_run_mining_pass") as mock_run:
            miner.on_epoch_end(_make_trainer_arg(epoch=10))
            mock_run.assert_not_called()

    def test_mining_wrong_interval_skips(self) -> None:
        """epoch=17, interval=5 → 17%5 != 0 → skip."""
        miner, _trainer = _make_miner(mining_interval=5, mining_min_epoch=15)
        with patch.object(miner, "_run_mining_pass") as mock_run:
            miner.on_epoch_end(_make_trainer_arg(epoch=17))
            mock_run.assert_not_called()

    def test_mining_runs_at_correct_epoch(self) -> None:
        """epoch=15, enabled, interval=5 → _run_mining_pass called."""
        miner, _trainer = _make_miner(mining_min_epoch=15, mining_interval=5)
        with patch.object(miner, "_run_mining_pass") as mock_run:
            miner.on_epoch_end(_make_trainer_arg(epoch=15))
            mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# TestMiningWeightAssignment — weights after a mining pass
# ---------------------------------------------------------------------------


def _setup_mining_pass(
    difficulties: list[float],
    n_background: int = 0,
    top_fraction: float = 0.10,
    oversample_factor: float = 3.0,
) -> tuple[HardNegativeMiner, MagicMock, MagicMock]:
    """Wire up a miner with a mock dataset that yields controlled difficulties.

    Returns (miner, mock_trainer, trainer_arg).
    Each non-background image is given a label dict with `n_gt = 1` so
    compute_difficulty is driven by the mock model predictions.
    """
    cfg = make_yolo_config(
        mining_enabled=True,
        mining_min_epoch=0,
        mining_interval=1,
        mining_top_fraction=top_fraction,
        mining_oversample_factor=oversample_factor,
    )

    mock_trainer = MagicMock()
    miner = HardNegativeMiner(cfg, mock_trainer)

    # Build trainer_arg with mock dataset
    trainer_arg = MagicMock()
    trainer_arg.epoch = 0

    # Labels: background images first, then scored images
    labels: list[dict] = []
    im_files: list[str] = []
    for i in range(n_background):
        labels.append({"bboxes": np.zeros((0, 4)), "cls": np.array([])})
        im_files.append(f"/fake/bg_{i}.jpg")
    for i in range(len(difficulties)):
        labels.append({"bboxes": np.array([[0.5, 0.5, 0.3, 0.2]]), "cls": np.array([0])})
        im_files.append(f"/fake/img_{i}.jpg")

    trainer_arg.train_loader.dataset.labels = labels
    trainer_arg.train_loader.dataset.im_files = im_files

    # Mock EMA model: returns predictions that produce the desired difficulties.
    # For each scored image (n_gt=1), we set n_det and avg_conf to match.
    # Since compute_difficulty(1, n_det, avg_conf) = (1-avg_conf) + 2*max(0,1-n_det)/1
    # For difficulty d: set n_det=1, avg_conf=(1-d) → d = (1-(1-d)) + 0 = d ✓
    # But only works for d <= 1.0. For d > 1.0: set n_det=0, avg_conf=0 → d=3.0
    # General: set n_det=1, avg_conf = max(0, 1-d)
    mock_model = MagicMock()
    mock_model.parameters.return_value = iter([MagicMock(device=MagicMock(type="cpu"))])
    trainer_arg.ema.ema = mock_model

    return miner, mock_trainer, trainer_arg


class TestMiningWeightAssignment:
    """Verify weight assignment after mining pass."""

    def test_hard_images_get_oversample_weight(self) -> None:
        """Hardest image gets weight == mining_oversample_factor."""
        # 5 images, difficulties: 4 easy (0.5) + 1 hard (3.0)
        # top_fraction=0.10 → top 10% = threshold near 3.0
        # With 5 scored images, top 10% ≈ at least the hardest 1
        miner, mock_trainer, trainer_arg = _setup_mining_pass(
            difficulties=[0.5, 0.5, 0.5, 0.5, 3.0],
            top_fraction=0.50,  # top 50% so threshold catches the hard one
            oversample_factor=3.0,
        )

        # Patch _score_images to return controlled results
        scored = {4: 3.0, 0: 0.5, 1: 0.5, 2: 0.5, 3: 0.5}
        with patch.object(miner, "_score_images", return_value=scored):
            miner._run_mining_pass(trainer_arg)

        # Verify trainer.update_sample_weights was called
        mock_trainer.update_sample_weights.assert_called_once()
        weights = mock_trainer.update_sample_weights.call_args[0][0]
        # The hardest image (index 4) should get oversample weight
        assert weights[4] == 3.0

    def test_easy_images_keep_uniform_weight(self) -> None:
        """Non-hard images retain weight == 1.0."""
        miner, mock_trainer, trainer_arg = _setup_mining_pass(
            difficulties=[0.1, 0.1, 0.1, 0.1, 3.0],
            top_fraction=0.20,
            oversample_factor=3.0,
        )

        scored = {0: 0.1, 1: 0.1, 2: 0.1, 3: 0.1, 4: 3.0}
        with patch.object(miner, "_score_images", return_value=scored):
            miner._run_mining_pass(trainer_arg)

        weights = mock_trainer.update_sample_weights.call_args[0][0]
        # Easy images (indices 0-3) should keep weight 1.0
        for idx in range(4):
            assert weights[idx] == 1.0

    def test_background_images_excluded(self) -> None:
        """Background images (n_gt=0) keep weight=1.0, excluded from scoring."""
        miner, mock_trainer, trainer_arg = _setup_mining_pass(
            difficulties=[3.0, 0.1],
            n_background=2,  # 2 bg images at indices 0,1
            top_fraction=0.50,
            oversample_factor=3.0,
        )

        # scored dict uses original dataset indices — bg images not in it
        scored = {2: 3.0, 3: 0.1}
        with patch.object(miner, "_score_images", return_value=scored):
            miner._run_mining_pass(trainer_arg)

        weights = mock_trainer.update_sample_weights.call_args[0][0]
        # Background images (indices 0, 1) should stay 1.0
        assert weights[0] == 1.0
        assert weights[1] == 1.0

    def test_mining_calls_trainer_update(self) -> None:
        """trainer.update_sample_weights called once with numpy array."""
        miner, mock_trainer, trainer_arg = _setup_mining_pass(
            difficulties=[0.5, 1.0, 2.0],
            top_fraction=0.50,
            oversample_factor=3.0,
        )

        scored = {0: 0.5, 1: 1.0, 2: 2.0}
        with patch.object(miner, "_score_images", return_value=scored):
            miner._run_mining_pass(trainer_arg)

        mock_trainer.update_sample_weights.assert_called_once()
        weights = mock_trainer.update_sample_weights.call_args[0][0]
        assert isinstance(weights, np.ndarray)


# ---------------------------------------------------------------------------
# TestMiningWithRealData — real label file parsing
# ---------------------------------------------------------------------------


class TestMiningWithRealData:
    """Tests using real roboflow dataset labels."""

    @pytest.mark.real_data
    def test_difficulty_scoring_on_real_labels(self) -> None:
        """Load 5 real label files, verify difficulty scoring."""
        if not ROBOFLOW_LABELS.is_dir():
            pytest.skip("Roboflow training data not downloaded")

        label_files = sorted(ROBOFLOW_LABELS.glob("*.txt"))[:5]
        assert len(label_files) >= 5, "Need at least 5 label files"

        for lf in label_files:
            lines = lf.read_text().strip().splitlines()
            n_gt = len(lines)
            assert n_gt >= 1, f"Expected non-empty label: {lf.name}"

            # No detections → max difficulty
            d = HardNegativeMiner.compute_difficulty(n_gt, 0, 0.0)
            assert d == 3.0

            # All detected with avg_conf=0.8 → difficulty = 0.2
            d2 = HardNegativeMiner.compute_difficulty(n_gt, n_gt, 0.8)
            assert d2 == pytest.approx(0.2)

    @pytest.mark.real_data
    def test_label_parsing_for_mining(self) -> None:
        """Parse 5 real YOLO labels: class=0, coords in [0,1]."""
        if not ROBOFLOW_LABELS.is_dir():
            pytest.skip("Roboflow training data not downloaded")

        label_files = sorted(ROBOFLOW_LABELS.glob("*.txt"))[:5]
        assert len(label_files) >= 5, "Need at least 5 label files"

        for lf in label_files:
            lines = lf.read_text().strip().splitlines()
            assert len(lines) >= 1, f"Empty label file: {lf.name}"

            for line in lines:
                parts = line.strip().split()
                assert len(parts) == 5, f"Expected 5 values per line, got {len(parts)}: {lf.name}"

                cls_id = int(parts[0])
                assert cls_id == 0, f"Expected class 0, got {cls_id}: {lf.name}"

                cx, cy, w, h = (float(x) for x in parts[1:])
                for val, name in [(cx, "cx"), (cy, "cy"), (w, "w"), (h, "h")]:
                    assert 0.0 <= val <= 1.0, f"{name}={val} out of range [0,1]: {lf.name}"
