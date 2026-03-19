"""Tests for production training pipeline — 7 groups, 35 tests.

All tests use synthetic (randomly generated) data fixtures to be self-contained.
No dependency on real training data.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
import torch

from training.config import AugmentationPhase
from training.regression.production.config import ProductionConfig
from training.regression.production.losses import production_loss
from training.regression.production.model import ProductionCornerNet
from training.regression.production.train import ModelEMA as ProductionEMA

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cfg_s4() -> ProductionConfig:
    cfg = ProductionConfig()
    cfg.stride = 4
    cfg.device = "cpu"
    cfg.wandb_enabled = False
    return cfg


@pytest.fixture
def cfg_s2() -> ProductionConfig:
    cfg = ProductionConfig()
    cfg.stride = 2
    cfg.device = "cpu"
    cfg.wandb_enabled = False
    return cfg


@pytest.fixture
def model_s4(cfg_s4: ProductionConfig) -> ProductionCornerNet:
    return ProductionCornerNet(cfg_s4)


@pytest.fixture
def model_s2(cfg_s2: ProductionConfig) -> ProductionCornerNet:
    return ProductionCornerNet(cfg_s2)


@pytest.fixture
def tiny_dataset(tmp_path: Path) -> Path:
    """Create a tiny fake dataset for smoke tests."""
    crops_dir = tmp_path / "crops"
    crops_dir.mkdir()

    annotations: dict[str, dict] = {}
    train_ids: list[str] = []
    val_ids: list[str] = []

    rng = np.random.RandomState(42)
    for i in range(20):
        fname = f"img_{i:04d}.jpg"
        img = rng.randint(0, 256, (100, 300, 3), dtype=np.uint8)
        cv2.imwrite(str(crops_dir / fname), img)

        corners = [
            [30 + rng.uniform(-5, 5), 20 + rng.uniform(-5, 5)],
            [270 + rng.uniform(-5, 5), 20 + rng.uniform(-5, 5)],
            [270 + rng.uniform(-5, 5), 80 + rng.uniform(-5, 5)],
            [30 + rng.uniform(-5, 5), 80 + rng.uniform(-5, 5)],
        ]
        annotations[fname] = {"corners": corners}

        if i < 16:
            train_ids.append(fname)
        else:
            val_ids.append(fname)

    with open(tmp_path / "annotations.json", "w") as f:
        json.dump(annotations, f)
    with open(tmp_path / "split.json", "w") as f:
        json.dump({"train": train_ids, "val": val_ids}, f)

    return tmp_path


@pytest.fixture
def tiny_dataset_with_synthetic(tmp_path: Path) -> Path:
    """Create a tiny dataset with both real and synthetic images."""
    crops_dir = tmp_path / "crops"
    crops_dir.mkdir()
    synth_dir = tmp_path / "synthetic_crops"
    synth_dir.mkdir()

    annotations: dict[str, dict] = {}
    synthetic_annotations: dict[str, dict] = {}
    train_ids: list[str] = []
    val_ids: list[str] = []

    rng = np.random.RandomState(42)

    # 16 real train + 4 val
    for i in range(20):
        fname = f"img_{i:04d}.jpg"
        img = rng.randint(0, 256, (100, 300, 3), dtype=np.uint8)
        cv2.imwrite(str(crops_dir / fname), img)
        corners = [
            [30 + rng.uniform(-5, 5), 20 + rng.uniform(-5, 5)],
            [270 + rng.uniform(-5, 5), 20 + rng.uniform(-5, 5)],
            [270 + rng.uniform(-5, 5), 80 + rng.uniform(-5, 5)],
            [30 + rng.uniform(-5, 5), 80 + rng.uniform(-5, 5)],
        ]
        annotations[fname] = {"corners": corners}
        if i < 16:
            train_ids.append(fname)
        else:
            val_ids.append(fname)

    # 16 synthetic images
    for i in range(16):
        fname = f"synth_{i:04d}.jpg"
        img = rng.randint(0, 256, (100, 300, 3), dtype=np.uint8)
        cv2.imwrite(str(synth_dir / fname), img)
        corners = [
            [30 + rng.uniform(-5, 5), 20 + rng.uniform(-5, 5)],
            [270 + rng.uniform(-5, 5), 20 + rng.uniform(-5, 5)],
            [270 + rng.uniform(-5, 5), 80 + rng.uniform(-5, 5)],
            [30 + rng.uniform(-5, 5), 80 + rng.uniform(-5, 5)],
        ]
        synthetic_annotations[fname] = {"corners": corners}

    with open(tmp_path / "annotations.json", "w") as f:
        json.dump(annotations, f)
    with open(tmp_path / "split.json", "w") as f:
        json.dump({"train": train_ids, "val": val_ids}, f)
    with open(tmp_path / "synthetic_annotations.json", "w") as f:
        json.dump(synthetic_annotations, f)

    return tmp_path


def _make_cfg(tmp_path: Path, stride: int = 4, with_synthetic: bool = False) -> ProductionConfig:
    """Create a config pointing to test data."""
    cfg = ProductionConfig()
    cfg.stride = stride
    cfg.device = "cpu"
    cfg.wandb_enabled = False
    cfg.total_epochs = 1
    cfg.data_dir = str(tmp_path / "crops")
    cfg.annotations_path = str(tmp_path / "annotations.json")
    cfg.split_path = str(tmp_path / "split.json")
    cfg.checkpoint_dir = str(tmp_path / "checkpoints")
    cfg.checkpoint_interval = 1
    if with_synthetic:
        cfg.synthetic_data_dir = str(tmp_path / "synthetic_crops")
        cfg.synthetic_annotations_path = str(tmp_path / "synthetic_annotations.json")
    else:
        cfg.synthetic_data_dir = ""
        cfg.synthetic_annotations_path = ""
    return cfg


# ===========================================================================
# Group 1: Model tests (both strides)
# ===========================================================================


class TestModelOutputs:
    def test_stride4_output_shape(self, model_s4: ProductionCornerNet) -> None:
        x = torch.randn(2, 3, 80, 256)
        coords, log_sigma = model_s4(x)
        assert coords.shape == (2, 8)
        assert log_sigma.shape == (2, 8)

    def test_stride2_output_shape(self, model_s2: ProductionCornerNet) -> None:
        x = torch.randn(2, 3, 80, 256)
        coords, log_sigma = model_s2(x)
        assert coords.shape == (2, 8)
        assert log_sigma.shape == (2, 8)

    def test_stride4_attention_shape(self, model_s4: ProductionCornerNet) -> None:
        x = torch.randn(2, 3, 80, 256)
        _coords, _log_sigma, attention = model_s4.forward_with_attention(x)
        assert attention.shape == (2, 4, 20, 64)

    def test_stride2_attention_shape(self, model_s2: ProductionCornerNet) -> None:
        x = torch.randn(2, 3, 80, 256)
        _coords, _log_sigma, attention = model_s2.forward_with_attention(x)
        assert attention.shape == (2, 4, 40, 128)

    @pytest.mark.parametrize("stride", [2, 4])
    def test_attention_sums_to_one(self, stride: int) -> None:
        cfg = ProductionConfig()
        cfg.stride = stride
        cfg.device = "cpu"
        model = ProductionCornerNet(cfg)
        x = torch.randn(2, 3, 80, 256)
        _, _, attention = model.forward_with_attention(x)
        # Each corner's attention should sum to 1.0
        sums = attention.sum(dim=(2, 3))  # (2, 4)
        assert torch.allclose(sums, torch.ones_like(sums), atol=1e-5)

    def test_sigma_is_positive(self, model_s4: ProductionCornerNet) -> None:
        x = torch.randn(2, 3, 80, 256)
        _, log_sigma = model_s4(x)
        sigma = torch.exp(log_sigma)
        assert (sigma > 0).all()

    @pytest.mark.parametrize("stride", [2, 4])
    def test_param_groups_cover_all(self, stride: int) -> None:
        cfg = ProductionConfig()
        cfg.stride = stride
        cfg.device = "cpu"
        model = ProductionCornerNet(cfg)

        encoder_ids = {id(p) for p in model.encoder_params()}
        decoder_ids = {id(p) for p in model.decoder_params()}
        head_ids = {id(p) for p in model.head_params()}
        all_ids = {id(p) for p in model.parameters()}

        covered = encoder_ids | decoder_ids | head_ids
        assert covered == all_ids, f"Uncovered: {all_ids - covered}"

    def test_encoder_params_frozen(self, model_s4: ProductionCornerNet) -> None:
        for p in model_s4.encoder_params():
            p.requires_grad_(False)
        for p in model_s4.encoder_params():
            assert not p.requires_grad

    def test_encoder_params_unfrozen(self, model_s4: ProductionCornerNet) -> None:
        for p in model_s4.encoder_params():
            p.requires_grad_(False)
        for p in model_s4.encoder_params():
            p.requires_grad_(True)
        for p in model_s4.encoder_params():
            assert p.requires_grad


# ===========================================================================
# Group 2: Loss tests
# ===========================================================================


class TestProductionLoss:
    def test_gaussian_nll_finite(self) -> None:
        pred_coords = torch.randn(4, 8) * 50 + 128
        pred_log_sigma = torch.randn(4, 8)
        target_coords = torch.randn(4, 8) * 50 + 128
        loss = production_loss(pred_coords, pred_log_sigma, target_coords, 256, 80)
        assert torch.isfinite(loss)

    def test_gaussian_nll_gradient_flows(self) -> None:
        cfg = ProductionConfig()
        cfg.stride = 4
        cfg.device = "cpu"
        model = ProductionCornerNet(cfg)
        x = torch.randn(2, 3, 80, 256)
        target = torch.randn(2, 8) * 50 + 128

        coords, log_sigma = model(x)
        loss = production_loss(coords, log_sigma, target, 256, 80)
        loss.backward()

        has_grad = any(p.grad is not None and p.grad.abs().sum() > 0 for p in model.parameters())
        assert has_grad, "No gradients flowed to model parameters"

    def test_low_error_low_sigma_beats_high_sigma(self) -> None:
        import math

        target = torch.tensor([[128.0, 40.0] * 4])
        pred = target + 1.0  # small error (1px)

        # Sigma near optimal for 1px error (~30px matches wing error scale)
        log_sigma_near = torch.full((1, 8), math.log(30.0))
        loss_near = production_loss(pred, log_sigma_near, target, 256, 80)

        # Sigma way too large (500px) — log(sigma²) penalty dominates
        log_sigma_huge = torch.full((1, 8), math.log(500.0))
        loss_huge = production_loss(pred, log_sigma_huge, target, 256, 80)

        # Near-optimal sigma should give lower loss than overly large sigma
        assert loss_near < loss_huge, (
            f"near-optimal loss {loss_near} should be < huge-sigma loss {loss_huge}"
        )

    def test_wing_component_shape(self) -> None:
        """Wing loss operates correctly on normalized coordinates."""
        pred = torch.randn(4, 8) * 50 + 128
        target = pred + 5.0  # offset by 5 pixels
        log_sigma = torch.zeros(4, 8)
        loss = production_loss(pred, log_sigma, target, 256, 80)
        assert loss.dim() == 0  # scalar
        assert torch.isfinite(loss)


# ===========================================================================
# Group 3: EMA tests
# ===========================================================================


class TestEMA:
    def test_ema_initialization(self) -> None:
        cfg = ProductionConfig()
        cfg.stride = 4
        cfg.device = "cpu"
        model = ProductionCornerNet(cfg)
        ema = ProductionEMA(model, decay=0.999)

        for p_model, p_ema in zip(model.parameters(), ema.module.parameters(), strict=True):
            assert torch.allclose(p_model, p_ema)

    def test_ema_update_changes_weights(self) -> None:
        cfg = ProductionConfig()
        cfg.stride = 4
        cfg.device = "cpu"
        model = ProductionCornerNet(cfg)
        ema = ProductionEMA(model, decay=0.999)

        # Simulate a training step
        x = torch.randn(1, 3, 80, 256)
        coords, log_sigma = model(x)
        loss = coords.sum() + log_sigma.sum()
        loss.backward()
        with torch.no_grad():
            for p in model.parameters():
                if p.grad is not None:
                    p -= 0.01 * p.grad

        ema.update(model)

        # EMA weights should now differ from model
        all_same = all(
            torch.allclose(p_m, p_e)
            for p_m, p_e in zip(model.parameters(), ema.module.parameters(), strict=True)
        )
        assert not all_same, "EMA weights should differ after update"

    def test_ema_decay_rate(self) -> None:
        cfg = ProductionConfig()
        cfg.stride = 4
        cfg.device = "cpu"
        model = ProductionCornerNet(cfg)

        ema_fast = ProductionEMA(model, decay=0.9)
        ema_slow = ProductionEMA(model, decay=0.9999)

        # Perturb model
        with torch.no_grad():
            for p in model.parameters():
                p.add_(torch.randn_like(p) * 0.1)

        ema_fast.update(model)
        ema_slow.update(model)

        # Fast EMA should change more than slow EMA
        fast_diff = sum(
            (p_m - p_e).abs().sum().item()
            for p_m, p_e in zip(model.parameters(), ema_fast.module.parameters(), strict=True)
        )
        slow_diff = sum(
            (p_m - p_e).abs().sum().item()
            for p_m, p_e in zip(model.parameters(), ema_slow.module.parameters(), strict=True)
        )
        assert fast_diff < slow_diff, "Fast EMA (0.9) should be closer to model than slow (0.9999)"

    def test_ema_state_dict_roundtrip(self, tmp_path: Path) -> None:
        cfg = ProductionConfig()
        cfg.stride = 4
        cfg.device = "cpu"
        model = ProductionCornerNet(cfg)
        ema = ProductionEMA(model, decay=0.999)

        # Perturb and update
        with torch.no_grad():
            for p in model.parameters():
                p.add_(torch.randn_like(p) * 0.1)
        ema.update(model)

        # Save
        state_dict = ema.module.state_dict()
        torch.save(state_dict, tmp_path / "ema.pt")

        # Load into new EMA
        model2 = ProductionCornerNet(cfg)
        ema2 = ProductionEMA(model2, decay=0.999)
        loaded = torch.load(tmp_path / "ema.pt", weights_only=False)
        ema2.module.load_state_dict(loaded)

        for p1, p2 in zip(ema.module.parameters(), ema2.module.parameters(), strict=True):
            assert torch.allclose(p1, p2)


# ===========================================================================
# Group 4: Data pipeline tests
# ===========================================================================


class TestDataPipeline:
    def test_synthetic_ratio_approximately_correct(self, tiny_dataset_with_synthetic: Path) -> None:
        from training.regression.production.train import (
            ProductionDataset,
            build_production_sampler,
        )

        cfg = _make_cfg(tiny_dataset_with_synthetic, with_synthetic=True)
        dataset = ProductionDataset(cfg, split="train")
        sampler = build_production_sampler(dataset, cfg)

        real_count = 0
        synth_count = 0
        n_epochs = 10
        for _ in range(n_epochs):
            for idx in sampler:
                if idx < dataset.n_real:
                    real_count += 1
                else:
                    synth_count += 1

        total = real_count + synth_count
        actual_ratio = synth_count / total
        # Should be approximately 0.5 (within 15% tolerance)
        assert abs(actual_ratio - cfg.synthetic_ratio) < 0.15, (
            f"Synthetic ratio {actual_ratio:.3f} too far from target {cfg.synthetic_ratio}"
        )

    def test_no_exact_repeat_within_epoch(self, tiny_dataset_with_synthetic: Path) -> None:
        from training.regression.production.train import (
            ProductionDataset,
            build_production_sampler,
        )

        cfg = _make_cfg(tiny_dataset_with_synthetic, with_synthetic=True)
        dataset = ProductionDataset(cfg, split="train")
        sampler = build_production_sampler(dataset, cfg)

        epoch1 = list(sampler)
        epoch2 = list(sampler)
        # With replacement=True and random sampling, consecutive epochs
        # should produce different orderings
        assert epoch1 != epoch2, "Two consecutive epochs should not produce identical orderings"

    def test_real_images_are_train_only(self, tiny_dataset_with_synthetic: Path) -> None:
        cfg = _make_cfg(tiny_dataset_with_synthetic, with_synthetic=True)

        with open(cfg.split_path) as f:
            splits = json.load(f)

        train_set = set(splits["train"])
        val_set = set(splits["val"])
        assert train_set.isdisjoint(val_set), "Train and val should not overlap"

    def test_synthetic_images_are_train_only(self, tiny_dataset_with_synthetic: Path) -> None:
        cfg = _make_cfg(tiny_dataset_with_synthetic, with_synthetic=True)

        with open(cfg.split_path) as f:
            splits = json.load(f)
        with open(str(tiny_dataset_with_synthetic / "synthetic_annotations.json")) as f:
            synth_annotations = json.load(f)

        val_set = set(splits["val"])
        synth_set = set(synth_annotations.keys())
        assert synth_set.isdisjoint(val_set), "Synthetic images should not include val images"

    def test_epoch_size_matches_real_count(self, tiny_dataset_with_synthetic: Path) -> None:
        from training.regression.production.train import (
            ProductionDataset,
            build_production_sampler,
        )

        cfg = _make_cfg(tiny_dataset_with_synthetic, with_synthetic=True)
        dataset = ProductionDataset(cfg, split="train")
        sampler = build_production_sampler(dataset, cfg)

        assert sampler.num_samples == dataset.n_real

    def test_dataset_returns_correct_tuple(self, tiny_dataset: Path) -> None:
        from training.regression.production.train import ProductionDataset

        cfg = _make_cfg(tiny_dataset)
        dataset = ProductionDataset(cfg, split="train")

        img_tensor, coord_target, idx = dataset[0]
        assert img_tensor.shape == (3, 80, 256)
        assert coord_target.shape == (8,)
        assert isinstance(idx, int)


# ===========================================================================
# Group 5: Augmentation integration tests
# ===========================================================================


class TestAugmentationIntegration:
    def test_full_drone_augs_all_active(self) -> None:
        """In FULL_DRONE phase with ramp=1.0, augmentations modify images."""
        from training.augmentations import apply_augmentation

        cfg = ProductionConfig()
        img = np.random.RandomState(42).randint(0, 256, (100, 300, 3), dtype=np.uint8)
        corners: list[tuple[float, float]] = [
            (30.0, 20.0),
            (270.0, 20.0),
            (270.0, 80.0),
            (30.0, 80.0),
        ]

        modified_count = 0
        n_iterations = 100
        for _ in range(n_iterations):
            result = apply_augmentation(
                img, corners, AugmentationPhase.FULL_DRONE, cfg, aug_ramp_factor=1.0
            )
            if result is not None:
                aug_img, _ = result
                if not np.array_equal(img, aug_img):
                    modified_count += 1

        # With FULL_DRONE and ramp=1.0, most iterations should produce changes
        assert modified_count > 50, (
            f"Only {modified_count}/{n_iterations} iterations modified the image"
        )

    def test_ramp_factor_zero_disables_augs(self) -> None:
        from training.augmentations import apply_augmentation

        cfg = ProductionConfig()
        img = np.random.RandomState(42).randint(0, 256, (100, 300, 3), dtype=np.uint8)
        corners: list[tuple[float, float]] = [
            (30.0, 20.0),
            (270.0, 20.0),
            (270.0, 80.0),
            (30.0, 80.0),
        ]

        for _ in range(20):
            result = apply_augmentation(
                img, corners, AugmentationPhase.FULL_DRONE, cfg, aug_ramp_factor=0.0
            )
            assert result is not None
            aug_img, _ = result
            assert np.array_equal(img, aug_img), "Image should be unmodified with ramp=0.0"

    def test_augmentation_preserves_corners_in_bounds(self) -> None:
        from training.augmentations import apply_augmentation

        cfg = ProductionConfig()
        img = np.random.RandomState(42).randint(0, 256, (100, 300, 3), dtype=np.uint8)
        corners: list[tuple[float, float]] = [
            (30.0, 20.0),
            (270.0, 20.0),
            (270.0, 80.0),
            (30.0, 80.0),
        ]
        h, w = img.shape[:2]

        for _ in range(50):
            result = apply_augmentation(
                img, corners, AugmentationPhase.FULL_DRONE, cfg, aug_ramp_factor=1.0
            )
            if result is not None:
                _, aug_corners = result
                for x, y in aug_corners:
                    assert 0 <= x < w, f"Corner x={x} out of bounds [0, {w})"
                    assert 0 <= y < h, f"Corner y={y} out of bounds [0, {h})"

    def test_composed_augmentations_dont_crash(self) -> None:
        from training.augmentations import apply_augmentation

        cfg = ProductionConfig()

        for seed in range(50):
            rng = np.random.RandomState(seed)
            img = rng.randint(0, 256, (100, 300, 3), dtype=np.uint8)
            corners: list[tuple[float, float]] = [
                (30.0, 20.0),
                (270.0, 20.0),
                (270.0, 80.0),
                (30.0, 80.0),
            ]
            # Should not raise
            apply_augmentation(img, corners, AugmentationPhase.FULL_DRONE, cfg, aug_ramp_factor=1.0)

    def test_night_sim_with_new_augs(self) -> None:
        from training.augmentations import apply_augmentation

        cfg = ProductionConfig()
        img = np.random.RandomState(42).randint(0, 256, (100, 300, 3), dtype=np.uint8)
        corners: list[tuple[float, float]] = [
            (30.0, 20.0),
            (270.0, 20.0),
            (270.0, 80.0),
            (30.0, 80.0),
        ]

        for _ in range(50):
            result = apply_augmentation(
                img,
                corners,
                AugmentationPhase.FULL_DRONE,
                cfg,
                is_night=True,
                aug_ramp_factor=1.0,
            )
            # Should not crash; result may be None (OOB retries) or a tuple
            if result is not None:
                assert result[0].shape == img.shape


# ===========================================================================
# Group 6: End-to-end smoke tests
# ===========================================================================


class TestEndToEnd:
    def test_one_epoch_stride4_no_crash(self, tiny_dataset: Path) -> None:
        from training.regression.production.train import train

        cfg = _make_cfg(tiny_dataset, stride=4)
        cfg.ema_decay = 0.999
        best_path = train(cfg)
        assert best_path.exists()

    def test_one_epoch_stride2_no_crash(self, tiny_dataset: Path) -> None:
        from training.regression.production.train import train

        cfg = _make_cfg(tiny_dataset, stride=2)
        cfg.ema_decay = 0.999
        best_path = train(cfg)
        assert best_path.exists()

    def test_checkpoint_save_load_roundtrip(self, tiny_dataset: Path) -> None:
        from training.regression.production.train import load_checkpoint, save_checkpoint

        cfg = _make_cfg(tiny_dataset, stride=4)
        model = ProductionCornerNet(cfg)
        ema = ProductionEMA(model, decay=0.999)

        # Perturb model
        with torch.no_grad():
            for p in model.parameters():
                p.add_(torch.randn_like(p) * 0.1)
        ema.update(model)

        from torch.optim import AdamW
        from torch.optim.lr_scheduler import LambdaLR

        optimizer = AdamW(model.parameters(), lr=1e-4)
        scheduler = LambdaLR(optimizer, lambda _ep: 1.0)

        # Save
        ckpt_path = Path(tiny_dataset / "checkpoints" / "test.pt")
        save_checkpoint(model, optimizer, scheduler, 5, {"pck_4": 0.5}, cfg, ckpt_path, ema=ema)

        # Load into new model
        model2 = ProductionCornerNet(cfg)
        ema2 = ProductionEMA(model2, decay=0.999)
        ckpt = load_checkpoint(ckpt_path, model2, ema=ema2)

        assert ckpt["epoch"] == 5
        assert ckpt["model_type"] == "production"

        # Verify model weights match
        for p1, p2 in zip(model.parameters(), model2.parameters(), strict=True):
            assert torch.allclose(p1, p2)

        # Verify EMA weights match
        for p1, p2 in zip(ema.module.parameters(), ema2.module.parameters(), strict=True):
            assert torch.allclose(p1, p2)

    def test_ema_validation_runs(self, tiny_dataset: Path) -> None:
        from torch.utils.data import DataLoader

        from training.regression.production.train import (
            ProductionDataset,
            production_validate,
        )

        cfg = _make_cfg(tiny_dataset, stride=4)
        model = ProductionCornerNet(cfg)
        ema = ProductionEMA(model, decay=0.999)

        val_dataset = ProductionDataset(cfg, split="val")
        val_loader = DataLoader(val_dataset, batch_size=4, shuffle=False, num_workers=0)

        # Validate with EMA model
        from typing import cast

        ema_model = cast(ProductionCornerNet, ema.module)
        metrics = production_validate(ema_model, val_loader)

        assert "pck_4" in metrics
        assert "mean_cpe" in metrics
        assert "sigma_mean" in metrics
        assert "sigma_error_correlation" in metrics
        assert isinstance(metrics["pck_4"], float)


# ===========================================================================
# Group 7: Sampler verification
# ===========================================================================


class TestSamplerVerification:
    def test_weighted_sampler_num_samples(self, tiny_dataset_with_synthetic: Path) -> None:
        from training.regression.production.train import (
            ProductionDataset,
            build_production_sampler,
        )

        cfg = _make_cfg(tiny_dataset_with_synthetic, with_synthetic=True)
        dataset = ProductionDataset(cfg, split="train")
        sampler = build_production_sampler(dataset, cfg)

        assert sampler.num_samples == dataset.n_real

    def test_weighted_sampler_ratio(self, tiny_dataset_with_synthetic: Path) -> None:
        from training.regression.production.train import (
            ProductionDataset,
            build_production_sampler,
        )

        cfg = _make_cfg(tiny_dataset_with_synthetic, with_synthetic=True)
        dataset = ProductionDataset(cfg, split="train")
        sampler = build_production_sampler(dataset, cfg)

        real_count = 0
        synth_count = 0
        # Draw multiple epochs to reduce variance (small dataset = high per-epoch variance)
        n_epochs = 10
        for _ in range(n_epochs):
            for idx in sampler:
                if idx < dataset.n_real:
                    real_count += 1
                else:
                    synth_count += 1

        total = real_count + synth_count
        actual_ratio = synth_count / total
        # Within 15% of target ratio
        assert abs(actual_ratio - cfg.synthetic_ratio) < 0.15, (
            f"Actual ratio {actual_ratio:.3f} too far from {cfg.synthetic_ratio}"
        )

    def test_all_synthetic_images_seen_over_50_epochs(
        self, tiny_dataset_with_synthetic: Path
    ) -> None:
        from training.regression.production.train import (
            ProductionDataset,
            build_production_sampler,
        )

        cfg = _make_cfg(tiny_dataset_with_synthetic, with_synthetic=True)
        dataset = ProductionDataset(cfg, split="train")
        sampler = build_production_sampler(dataset, cfg)

        synth_seen: set[int] = set()
        n_synth = dataset.n_synthetic

        for _ in range(50):
            for idx in sampler:
                if idx >= dataset.n_real:
                    synth_seen.add(idx - dataset.n_real)

        coverage = len(synth_seen) / n_synth
        assert coverage > 0.95, (
            f"Only {coverage:.1%} synthetic images seen in 50 epochs (need >95%)"
        )
