"""Production training loop — combines EMA, variance head, synthetic data, and all augmentations.

Imports shared utilities from the base training module and adds:
- ProductionCornerNet model with variance head
- Gaussian NLL + wing loss
- EMA-based validation with sigma stats
- Synthetic data mixing via WeightedRandomSampler
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
import torch
from torch.optim import AdamW
from torch.optim.lr_scheduler import LambdaLR
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler

from common.geometry import order_keypoints_by_angle
from training.config import AugmentationPhase
from training.regression.dataset import corners_to_letterbox, letterbox_crop
from training.regression.evaluate import compute_cpe, compute_pck
from training.regression.production.config import ProductionConfig
from training.regression.production.losses import production_loss
from training.regression.production.model import ProductionCornerNet
from training.regression.train import (
    build_hem_sampler,
    build_optimizer,
    build_scheduler,
    check_phase_advance,
    check_unfreeze,
    freeze_encoder,
    get_device,
    poll_config,
    unfreeze_encoder,
)

# Conditional import for augmentation
try:
    from training.augmentations import apply_augmentation
except ImportError:
    from training.regression.dataset import apply_augmentation  # type: ignore[assignment]


# Optional W&B import
try:
    import wandb

    HAS_WANDB = True
except ImportError:
    wandb = None  # type: ignore[assignment]
    HAS_WANDB = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Production EMA (fixes buffer copy issue with expanded tensors)
# ---------------------------------------------------------------------------


class ModelEMA:
    """EMA that handles expanded BatchNorm buffers safely."""

    def __init__(self, model: torch.nn.Module, decay: float = 0.999) -> None:
        import copy

        self.module = copy.deepcopy(model)
        self.module.eval()
        self.module.requires_grad_(False)
        self.decay = decay

    @torch.no_grad()
    def update(self, model: torch.nn.Module) -> None:
        for ema_p, model_p in zip(self.module.parameters(), model.parameters(), strict=True):
            ema_p.data.mul_(self.decay).add_(model_p.data, alpha=1.0 - self.decay)
        for ema_b, model_b in zip(self.module.buffers(), model.buffers(), strict=True):
            ema_b.data = model_b.data.clone()


# ---------------------------------------------------------------------------
# Production Dataset (real + synthetic)
# ---------------------------------------------------------------------------


class ProductionDataset(Dataset):  # type: ignore[type-arg]
    """Combined real + synthetic dataset for production training.

    Returns (image_tensor, coord_target, idx) where coord_target is
    (8,) tensor [x0, y0, x1, y1, x2, y2, x3, y3] in letterbox pixel coords.
    """

    def __init__(self, cfg: ProductionConfig, split: str = "train") -> None:
        self._cfg = cfg
        self._split = split
        self._aug_phase = AugmentationPhase.NONE
        self._aug_ramp_factor: float = 1.0

        # Load annotations
        with open(cfg.annotations_path) as f:
            self._annotations: dict[str, Any] = json.load(f)

        # Load split and filter
        with open(cfg.split_path) as f:
            splits: dict[str, list[str]] = json.load(f)

        self._image_ids: list[str] = splits[split]

        # Load synthetic data (train split only)
        self._synthetic_ids: list[str] = []
        self._synthetic_annotations: dict[str, Any] = {}
        if split == "train" and cfg.synthetic_data_dir and cfg.synthetic_annotations_path:
            synth_dir = Path(cfg.synthetic_data_dir)
            synth_ann_path = Path(cfg.synthetic_annotations_path)
            if synth_dir.exists() and synth_ann_path.exists():
                with open(synth_ann_path) as f:
                    self._synthetic_annotations = json.load(f)
                self._synthetic_ids = [
                    sid
                    for sid in self._synthetic_annotations
                    if isinstance(self._synthetic_annotations[sid], dict)
                    and self._synthetic_annotations[sid].get("corners") is not None
                ]
                logger.info("Synthetic data loaded: %d images", len(self._synthetic_ids))

        # Optional lighting labels for night sim gating
        self._lighting_labels: dict[str, str] = {}
        if cfg.lighting_labels_path and Path(cfg.lighting_labels_path).exists():
            with open(cfg.lighting_labels_path) as f:
                self._lighting_labels = json.load(f)

        logger.info(
            "ProductionDataset loaded: split=%s, real=%d, synthetic=%d",
            split,
            len(self._image_ids),
            len(self._synthetic_ids),
        )

    def __len__(self) -> int:
        return len(self._image_ids) + len(self._synthetic_ids)

    @property
    def n_real(self) -> int:
        return len(self._image_ids)

    @property
    def n_synthetic(self) -> int:
        return len(self._synthetic_ids)

    def set_augmentation_phase(self, phase: AugmentationPhase) -> None:
        self._aug_phase = phase

    def set_aug_ramp_factor(self, factor: float) -> None:
        self._aug_ramp_factor = factor

    def is_night(self, filename: str) -> bool:
        return self._lighting_labels.get(filename, "") == "night"

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor, int]:
        cfg = self._cfg
        n_real = len(self._image_ids)

        if idx < n_real:
            image_id = self._image_ids[idx]
            img_path = Path(cfg.data_dir) / image_id
            entry = self._annotations[image_id]
        else:
            synth_idx = idx - n_real
            image_id = self._synthetic_ids[synth_idx]
            img_path = Path(cfg.synthetic_data_dir) / image_id
            entry = self._synthetic_annotations[image_id]

        img = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"Failed to read image: {img_path}")

        raw_corners = entry["corners"] if isinstance(entry, dict) else entry
        corners: list[tuple[float, float]] = [(float(c[0]), float(c[1])) for c in raw_corners]
        corners = order_keypoints_by_angle(corners)

        aug_result = apply_augmentation(
            img,
            corners,
            self._aug_phase,
            cfg,
            is_night=self.is_night(image_id),
            aug_ramp_factor=self._aug_ramp_factor,
        )
        if aug_result is not None:
            img, corners = aug_result

        letterboxed, scale, pad_x, pad_y = letterbox_crop(
            img, cfg.input_h, cfg.input_w, cfg.pad_color
        )
        corners_lb = corners_to_letterbox(corners, scale, pad_x, pad_y)

        coord_target = torch.tensor(
            [c for xy in corners_lb for c in xy],
            dtype=torch.float32,
        )
        img_tensor = torch.from_numpy(letterboxed.astype(np.float32) / 255.0).permute(2, 0, 1)

        return img_tensor, coord_target, idx


# ---------------------------------------------------------------------------
# Synthetic data sampler
# ---------------------------------------------------------------------------


def build_production_sampler(
    dataset: ProductionDataset,
    cfg: ProductionConfig,
    per_image_losses: np.ndarray | None = None,
) -> WeightedRandomSampler:
    """Build sampler that enforces synthetic_ratio with optional HEM.

    When per_image_losses is provided, HEM weights apply to real images.
    Synthetic images always get cfg.synthetic_ratio weight.
    num_samples = n_real to keep epoch size consistent.
    """
    n_real = dataset.n_real
    n_synth = dataset.n_synthetic
    total = n_real + n_synth

    if per_image_losses is not None and n_real > 0:
        mean_loss = per_image_losses[:n_real].mean()
        hem_weights = np.clip(
            per_image_losses[:n_real] / (mean_loss + 1e-8),
            cfg.hem_weight_floor,
            cfg.hem_weight_ceiling,
        )
    else:
        hem_weights = np.ones(n_real)

    weights: list[float] = []
    for i in range(total):
        if i < n_real:
            weights.append(float(hem_weights[i]) * (1.0 - cfg.synthetic_ratio))
        else:
            weights.append(cfg.synthetic_ratio)

    return WeightedRandomSampler(weights, num_samples=n_real, replacement=True)


# ---------------------------------------------------------------------------
# Production validation (handles 3-return forward_with_attention)
# ---------------------------------------------------------------------------


def production_validate(
    model: ProductionCornerNet,
    val_loader: DataLoader,
    device: torch.device | None = None,
) -> dict[str, Any]:
    """Validate production model with sigma stats."""
    model.eval()
    if device is None:
        device = next(model.parameters()).device

    batch_metrics: list[dict[str, Any]] = []
    all_mean_cpe_per_image: list[float] = []
    all_mean_sigma_per_image: list[float] = []

    for batch in val_loader:
        images, coord_targets = batch[0].to(device), batch[1].to(device)
        n = images.shape[0]

        with torch.no_grad():
            coords, log_sigma, attention = model.forward_with_attention(images)

        sigma = torch.exp(log_sigma)  # (N, 8)

        batch_cpe_flat: list[float] = []
        batch_pck: list[dict[int, float]] = []
        batch_conf: list[float] = []

        for i in range(n):
            pred_corners = [
                (float(coords[i, 2 * j]), float(coords[i, 2 * j + 1])) for j in range(4)
            ]
            gt_corners = [
                (float(coord_targets[i, 2 * j]), float(coord_targets[i, 2 * j + 1]))
                for j in range(4)
            ]
            cpe = compute_cpe(pred_corners, gt_corners)
            pck = compute_pck(pred_corners, gt_corners)
            conf = [float(attention[i, c].max()) for c in range(4)]

            batch_cpe_flat.extend(cpe)
            batch_pck.append(pck)
            batch_conf.extend(conf)

            all_mean_cpe_per_image.append(float(np.mean(cpe)))
            all_mean_sigma_per_image.append(float(sigma[i].mean()))

        # Attention stats
        attn_flat = attention.reshape(n, 4, -1)
        log_attn = torch.log(attn_flat + 1e-10)
        entropy = -(attn_flat * log_attn).sum(dim=2)

        batch_metrics.append(
            {
                "mean_cpe": sum(batch_cpe_flat) / len(batch_cpe_flat),
                "pck_2": sum(p[2] for p in batch_pck) / len(batch_pck),
                "pck_4": sum(p[4] for p in batch_pck) / len(batch_pck),
                "pck_8": sum(p[8] for p in batch_pck) / len(batch_pck),
                "mean_confidence": sum(batch_conf) / len(batch_conf),
                "attention_entropy": float(entropy.mean()),
                "attention_peak_mean": float(attn_flat.max(dim=2).values.mean()),
                "attention_peak_min": float(attn_flat.max(dim=2).values.min()),
                "sigma_mean": float(sigma.mean()),
                "sigma_median": float(sigma.median()),
                "sigma_min": float(sigma.min()),
                "sigma_max": float(sigma.max()),
            }
        )

    if not batch_metrics:
        return {
            "mean_cpe": 0.0,
            "pck_2": 0.0,
            "pck_4": 0.0,
            "pck_8": 0.0,
            "mean_confidence": 0.0,
            "attention_entropy": 0.0,
            "attention_peak_mean": 0.0,
            "attention_peak_min": 0.0,
            "sigma_mean": 0.0,
            "sigma_median": 0.0,
            "sigma_min": 0.0,
            "sigma_max": 0.0,
            "sigma_error_correlation": 0.0,
        }

    nb = len(batch_metrics)

    # Sigma-error correlation
    sigma_error_corr = 0.0
    if len(all_mean_cpe_per_image) > 2:
        corr_matrix = np.corrcoef(all_mean_sigma_per_image, all_mean_cpe_per_image)
        if not np.isnan(corr_matrix[0, 1]):
            sigma_error_corr = float(corr_matrix[0, 1])

    return {
        "mean_cpe": sum(m["mean_cpe"] for m in batch_metrics) / nb,
        "pck_2": sum(m["pck_2"] for m in batch_metrics) / nb,
        "pck_4": sum(m["pck_4"] for m in batch_metrics) / nb,
        "pck_8": sum(m["pck_8"] for m in batch_metrics) / nb,
        "mean_confidence": sum(m["mean_confidence"] for m in batch_metrics) / nb,
        "attention_entropy": sum(m["attention_entropy"] for m in batch_metrics) / nb,
        "attention_peak_mean": sum(m["attention_peak_mean"] for m in batch_metrics) / nb,
        "attention_peak_min": min(m["attention_peak_min"] for m in batch_metrics),
        "sigma_mean": sum(m["sigma_mean"] for m in batch_metrics) / nb,
        "sigma_median": sum(m["sigma_median"] for m in batch_metrics) / nb,
        "sigma_min": min(m["sigma_min"] for m in batch_metrics),
        "sigma_max": max(m["sigma_max"] for m in batch_metrics),
        "sigma_error_correlation": sigma_error_corr,
    }


# ---------------------------------------------------------------------------
# Checkpoint save / load (production-specific with EMA)
# ---------------------------------------------------------------------------


def save_checkpoint(
    model: ProductionCornerNet,
    optimizer: AdamW,
    scheduler: LambdaLR,
    epoch: int,
    metrics: dict,
    cfg: ProductionConfig,
    path: Path,
    encoder_frozen: bool = True,
    aug_phase: AugmentationPhase = AugmentationPhase.NONE,
    unfreeze_epoch: int | None = None,
    ema: ModelEMA | None = None,
) -> Path:
    """Save production training checkpoint with EMA state."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_dict: dict[str, Any] = {
        "epoch": epoch,
        "model_type": "production",
        "model_state_dict": model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "scheduler_state_dict": scheduler.state_dict(),
        "metrics": metrics,
        "config": dataclasses.asdict(cfg),
        "encoder_frozen": encoder_frozen,
        "aug_phase": aug_phase.value if isinstance(aug_phase, AugmentationPhase) else aug_phase,
        "unfreeze_epoch": unfreeze_epoch,
    }
    if ema is not None:
        save_dict["ema_state_dict"] = ema.module.state_dict()
    torch.save(save_dict, path)
    logger.info("Checkpoint saved: %s (epoch %d)", path, epoch)
    return path


def load_checkpoint(
    path: Path,
    model: ProductionCornerNet,
    optimizer: AdamW | None = None,
    scheduler: LambdaLR | None = None,
    ema: ModelEMA | None = None,
) -> dict:
    """Load production training checkpoint."""
    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    if optimizer is not None and "optimizer_state_dict" in checkpoint:
        optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
    if scheduler is not None and "scheduler_state_dict" in checkpoint:
        scheduler.load_state_dict(checkpoint["scheduler_state_dict"])
    if ema is not None and "ema_state_dict" in checkpoint:
        ema.module.load_state_dict(checkpoint["ema_state_dict"])
    logger.info("Checkpoint loaded: %s (epoch %d)", path, checkpoint.get("epoch", -1))
    return checkpoint


# ---------------------------------------------------------------------------
# train_one_epoch (production: variance head + EMA)
# ---------------------------------------------------------------------------


def train_one_epoch(
    model: ProductionCornerNet,
    loader: DataLoader,
    optimizer: AdamW,
    cfg: ProductionConfig,
    device: torch.device,
    per_image_losses: np.ndarray | None = None,
    ema: ModelEMA | None = None,
) -> dict[str, float]:
    """Train for one epoch with production loss and EMA update."""
    model.train()
    total_loss = 0.0
    total_sigma_mean = 0.0
    total_sigma_median = 0.0
    total_sigma_min = 0.0
    total_sigma_max = 0.0
    n_batches = 0

    for batch in loader:
        images, coord_targets = batch[0].to(device), batch[1].to(device)
        dataset_indices = batch[2] if len(batch) > 2 else None

        coords, log_sigma = model(images)
        loss = production_loss(
            coords,
            log_sigma,
            coord_targets,
            cfg.input_w,
            cfg.input_h,
            cfg.wing_w,
            cfg.wing_epsilon,
        )

        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip_max_norm)
        optimizer.step()

        # EMA update after every optimizer step
        if ema is not None:
            ema.update(model)

        # Per-image loss tracking for HEM
        if per_image_losses is not None and dataset_indices is not None:
            with torch.no_grad():
                batch_size = images.shape[0]
                for i in range(batch_size):
                    img_coords, img_log_sigma = model(images[i : i + 1])
                    img_loss = production_loss(
                        img_coords,
                        img_log_sigma,
                        coord_targets[i : i + 1],
                        cfg.input_w,
                        cfg.input_h,
                        cfg.wing_w,
                        cfg.wing_epsilon,
                    )
                    ds_idx = int(dataset_indices[i])
                    if ds_idx < len(per_image_losses):
                        per_image_losses[ds_idx] = img_loss.item()

        with torch.no_grad():
            sigma = torch.exp(log_sigma)
            total_sigma_mean += float(sigma.mean())
            total_sigma_median += float(sigma.median())
            total_sigma_min += float(sigma.min())
            total_sigma_max += float(sigma.max())

        total_loss += loss.item()
        n_batches += 1

    if n_batches == 0:
        return {
            "train_loss": 0.0,
            "train_reg_loss": 0.0,
            "sigma_mean": 0.0,
            "sigma_median": 0.0,
            "sigma_min": 0.0,
            "sigma_max": 0.0,
        }

    avg_loss = total_loss / n_batches
    return {
        "train_loss": avg_loss,
        "train_reg_loss": avg_loss,
        "sigma_mean": total_sigma_mean / n_batches,
        "sigma_median": total_sigma_median / n_batches,
        "sigma_min": total_sigma_min / n_batches,
        "sigma_max": total_sigma_max / n_batches,
    }


# ---------------------------------------------------------------------------
# W&B integration
# ---------------------------------------------------------------------------


def init_wandb(cfg: ProductionConfig) -> Any:
    """Initialize W&B run if enabled and available."""
    if not cfg.wandb_enabled or not HAS_WANDB:
        return None
    return wandb.init(  # type: ignore[union-attr]
        project=cfg.wandb_project,
        config=dataclasses.asdict(cfg),
        tags=["production", f"stride-{cfg.stride}"],
    )


def log_epoch(
    run: Any,
    epoch: int,
    train_metrics: dict[str, float],
    val_metrics: dict[str, Any],
    ema_val_metrics: dict[str, Any],
    lr: float,
    encoder_frozen: bool,
    aug_phase: AugmentationPhase,
) -> None:
    """Log per-epoch metrics to W&B with sigma stats and EMA metrics."""
    if run is None:
        return
    pck4 = val_metrics.get("pck_4", 0)
    pck8 = val_metrics.get("pck_8", 0)
    pck4_pck8_ratio = pck4 / max(pck8, 0.001)
    run.log(
        {
            "epoch": epoch,
            "train/reg_loss": train_metrics.get("train_reg_loss", 0),
            "train/sigma_mean": train_metrics.get("sigma_mean", 0),
            "train/sigma_median": train_metrics.get("sigma_median", 0),
            "train/sigma_min": train_metrics.get("sigma_min", 0),
            "train/sigma_max": train_metrics.get("sigma_max", 0),
            "val/mean_cpe": val_metrics.get("mean_cpe", 0),
            "val/pck_2": val_metrics.get("pck_2", 0),
            "val/pck_4": pck4,
            "val/pck_8": pck8,
            "val/pck4_pck8_ratio": pck4_pck8_ratio,
            "val/sigma_mean": val_metrics.get("sigma_mean", 0),
            "val/sigma_error_corr": val_metrics.get("sigma_error_correlation", 0),
            "ema/pck_4": ema_val_metrics.get("pck_4", 0),
            "ema/mean_cpe": ema_val_metrics.get("mean_cpe", 0),
            "ema/sigma_mean": ema_val_metrics.get("sigma_mean", 0),
            "lr": lr,
            "encoder_frozen": int(encoder_frozen),
            "aug_phase": aug_phase.value,
        },
        step=epoch,
    )


# ---------------------------------------------------------------------------
# Pause check
# ---------------------------------------------------------------------------


def _check_pause(config_path: Path) -> bool:
    """Check if config override file has pause=true."""
    config_path = Path(config_path)
    if not config_path.exists():
        return False
    try:
        data = json.loads(config_path.read_text())
        return bool(data.get("pause", False))
    except (json.JSONDecodeError, OSError):
        return False


# ---------------------------------------------------------------------------
# Main training loop
# ---------------------------------------------------------------------------


def train(cfg: ProductionConfig, resume_path: Path | None = None) -> Path:
    """Run complete production training loop. Returns path to best checkpoint."""
    if cfg.total_epochs <= 0:
        msg = f"total_epochs must be > 0, got {cfg.total_epochs} epochs"
        raise ValueError(msg)

    # --- SETUP ---
    device = get_device(cfg)
    model = ProductionCornerNet(cfg).to(device)
    freeze_encoder(model)
    optimizer = build_optimizer(model, cfg)
    scheduler = build_scheduler(optimizer, cfg)

    # EMA
    ema = ModelEMA(model, decay=cfg.ema_decay) if cfg.ema_enabled else None

    # Datasets
    train_dataset = ProductionDataset(cfg, split="train")
    val_dataset = ProductionDataset(cfg, split="val")

    has_synthetic = train_dataset.n_synthetic > 0

    # Initial DataLoader
    if has_synthetic:
        sampler = build_production_sampler(train_dataset, cfg)
        train_loader = DataLoader(
            train_dataset,
            batch_size=cfg.batch_size,
            sampler=sampler,
            num_workers=0,
            pin_memory=False,
        )
    else:
        train_loader = DataLoader(
            train_dataset,
            batch_size=cfg.batch_size,
            shuffle=True,
            num_workers=0,
            pin_memory=False,
        )
    val_loader = DataLoader(
        val_dataset,
        batch_size=cfg.batch_size,
        shuffle=False,
        num_workers=0,
        pin_memory=False,
    )

    run = init_wandb(cfg)
    config_path = Path(cfg.config_override_path)
    ckpt_dir = Path(cfg.checkpoint_dir)

    encoder_frozen = True
    aug_phase = AugmentationPhase.NONE
    best_pck4 = 0.0
    best_checkpoint_path: Path | None = None
    val_loss_history: list[float] = []
    pck4_history: list[float] = []
    per_image_losses: np.ndarray | None = None
    unfreeze_epoch: int | None = None
    epochs_since_improvement = 0

    # --- RESUME ---
    start_epoch = 0
    if resume_path is not None:
        ckpt = load_checkpoint(resume_path, model, optimizer, scheduler, ema)
        start_epoch = ckpt["epoch"] + 1
        encoder_frozen = ckpt.get("encoder_frozen", True)
        aug_phase = AugmentationPhase(ckpt.get("aug_phase", 0))
        if not encoder_frozen:
            unfreeze_encoder(model)
            per_image_losses = np.zeros(len(train_dataset))
        unfreeze_epoch = ckpt.get("unfreeze_epoch", None)
        train_dataset.set_augmentation_phase(aug_phase)
        saved_metrics = ckpt.get("metrics", {})
        if "val_loss_history" in saved_metrics:
            val_loss_history = saved_metrics["val_loss_history"]
        if "pck4_history" in saved_metrics:
            pck4_history = saved_metrics["pck4_history"]
        logger.info(
            "Resumed from epoch %d, encoder_frozen=%s, aug_phase=%s",
            start_epoch,
            encoder_frozen,
            aug_phase.name,
        )

    # --- EPOCH LOOP ---
    for epoch in range(start_epoch, cfg.total_epochs):
        # 1. Interactive config polling (pause support)
        cfg = poll_config(cfg, config_path)  # type: ignore[assignment]
        if _check_pause(config_path):
            save_checkpoint(
                model,
                optimizer,
                scheduler,
                epoch,
                {"val_loss_history": val_loss_history, "pck4_history": pck4_history},
                cfg,
                ckpt_dir / f"checkpoint_pause_epoch{epoch}.pt",
                encoder_frozen,
                aug_phase,
                unfreeze_epoch=unfreeze_epoch,
                ema=ema,
            )
            logger.info("Training paused at epoch %d", epoch)
            while _check_pause(config_path):
                time.sleep(cfg.config_poll_interval)
                cfg = poll_config(cfg, config_path)  # type: ignore[assignment]
            logger.info("Training resumed at epoch %d", epoch)

        # 2. Check unfreeze
        should_unfreeze, reason = check_unfreeze(cfg, epoch, val_loss_history, encoder_frozen)
        if should_unfreeze:
            save_checkpoint(
                model,
                optimizer,
                scheduler,
                epoch,
                {"val_loss_history": val_loss_history, "pck4_history": pck4_history},
                cfg,
                ckpt_dir / f"pre_unfreeze_epoch{epoch}.pt",
                encoder_frozen,
                aug_phase,
                unfreeze_epoch=unfreeze_epoch,
                ema=ema,
            )
            unfreeze_encoder(model)
            encoder_frozen = False
            unfreeze_epoch = epoch
            optimizer = build_optimizer(model, cfg)
            scheduler = build_scheduler(optimizer, cfg)
            per_image_losses = np.zeros(len(train_dataset))
            logger.info("Encoder unfrozen at epoch %d: %s", epoch, reason)

        # 3. Check aug phase advance
        new_phase = check_phase_advance(cfg, pck4_history, aug_phase, encoder_frozen)
        if new_phase != aug_phase:
            save_checkpoint(
                model,
                optimizer,
                scheduler,
                epoch,
                {"val_loss_history": val_loss_history, "pck4_history": pck4_history},
                cfg,
                ckpt_dir / f"pre_phase{new_phase.value}_epoch{epoch}.pt",
                encoder_frozen,
                aug_phase,
                unfreeze_epoch=unfreeze_epoch,
                ema=ema,
            )
            aug_phase = new_phase
            train_dataset.set_augmentation_phase(aug_phase)
            logger.info("Augmentation phase -> %s at epoch %d", aug_phase.name, epoch)

        # 3b. Augmentation ramp factor
        aug_ramp_factor = 1.0
        if unfreeze_epoch is not None and cfg.aug_ramp_epochs > 0:
            epochs_since_unfreeze = epoch - unfreeze_epoch
            if epochs_since_unfreeze < cfg.aug_delay_after_unfreeze:
                aug_ramp_factor = 0.0
            else:
                ramp_progress = epochs_since_unfreeze - cfg.aug_delay_after_unfreeze
                aug_ramp_factor = min(1.0, ramp_progress / cfg.aug_ramp_epochs)
        train_dataset.set_aug_ramp_factor(aug_ramp_factor)

        # 4. Rebuild DataLoader with HEM / synthetic sampler
        if not encoder_frozen:
            if has_synthetic:
                sampler = build_production_sampler(train_dataset, cfg, per_image_losses)
            elif per_image_losses is not None:
                sampler = build_hem_sampler(per_image_losses, cfg)
            else:
                sampler = None
            if sampler is not None:
                train_loader = DataLoader(
                    train_dataset,
                    batch_size=cfg.batch_size,
                    sampler=sampler,
                    num_workers=0,
                    pin_memory=False,
                )

        # 5. Train one epoch (with EMA update per step)
        train_metrics = train_one_epoch(
            model, train_loader, optimizer, cfg, device, per_image_losses, ema=ema
        )

        # 6. Scheduler step
        scheduler.step()

        # 7. Validate with both raw model and EMA model
        val_metrics = production_validate(model, val_loader, device)
        if ema is not None:
            ema_model = cast("ProductionCornerNet", ema.module)
            ema_val_metrics = production_validate(ema_model, val_loader, device)
        else:
            ema_val_metrics = val_metrics

        # Use EMA PCK@4 for best model tracking (if EMA enabled)
        tracking_pck4 = ema_val_metrics["pck_4"]
        val_loss_history.append(ema_val_metrics["mean_cpe"])
        pck4_history.append(tracking_pck4)

        # 8. Log metrics
        current_lr = optimizer.param_groups[1]["lr"]
        log_epoch(
            run,
            epoch,
            train_metrics,
            val_metrics,
            ema_val_metrics,
            current_lr,
            encoder_frozen,
            aug_phase,
        )

        # 9. Best checkpoint by EMA PCK@4
        if tracking_pck4 > best_pck4:
            best_pck4 = tracking_pck4
            epochs_since_improvement = 0
            best_checkpoint_path = save_checkpoint(
                model,
                optimizer,
                scheduler,
                epoch,
                {
                    **ema_val_metrics,
                    "val_loss_history": val_loss_history,
                    "pck4_history": pck4_history,
                },
                cfg,
                ckpt_dir / "best.pt",
                encoder_frozen,
                aug_phase,
                unfreeze_epoch=unfreeze_epoch,
                ema=ema,
            )
        else:
            epochs_since_improvement += 1

        # 9b. Early stopping
        if (
            cfg.early_stopping_patience > 0
            and epochs_since_improvement >= cfg.early_stopping_patience
        ):
            logger.info(
                "Early stopping at epoch %d: no PCK@4 improvement for %d epochs (best=%.4f)",
                epoch,
                cfg.early_stopping_patience,
                best_pck4,
            )
            break

        # 10. Periodic checkpoint
        if epoch % cfg.checkpoint_interval == 0:
            save_checkpoint(
                model,
                optimizer,
                scheduler,
                epoch,
                {
                    **ema_val_metrics,
                    "val_loss_history": val_loss_history,
                    "pck4_history": pck4_history,
                },
                cfg,
                ckpt_dir / f"checkpoint_epoch{epoch}.pt",
                encoder_frozen,
                aug_phase,
                unfreeze_epoch=unfreeze_epoch,
                ema=ema,
            )

        # 11. Print epoch summary
        logger.info(
            "Epoch %d/%d: loss=%.4f, pck4=%.4f, ema_pck4=%.4f, sigma=%.2f, lr=%.2e, frozen=%s, aug=%s",
            epoch + 1,
            cfg.total_epochs,
            train_metrics["train_loss"],
            val_metrics["pck_4"],
            ema_val_metrics["pck_4"],
            train_metrics["sigma_mean"],
            current_lr,
            encoder_frozen,
            aug_phase.name,
        )

    # Finish W&B
    if run is not None:
        run.finish()

    # Save last checkpoint if no best found
    if best_checkpoint_path is None:
        best_checkpoint_path = save_checkpoint(
            model,
            optimizer,
            scheduler,
            cfg.total_epochs - 1,
            {"val_loss_history": val_loss_history, "pck4_history": pck4_history},
            cfg,
            ckpt_dir / "best.pt",
            encoder_frozen,
            aug_phase,
            unfreeze_epoch=unfreeze_epoch,
            ema=ema,
        )

    return best_checkpoint_path
