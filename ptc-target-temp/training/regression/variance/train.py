"""Training loop for variance-aware corner regression.

Adapts the baseline training loop to use CornerRegressionNetWithVariance
and gaussian_nll_loss instead of wing loss.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import torch
from torch.optim import AdamW
from torch.optim.lr_scheduler import LambdaLR
from torch.utils.data import DataLoader, WeightedRandomSampler

from training.config import AugmentationPhase
from training.regression.config import RegressionConfig
from training.regression.variance.losses import gaussian_nll_loss

if TYPE_CHECKING:
    from training.regression.variance.model import CornerRegressionNetWithVariance

try:
    import wandb

    HAS_WANDB = True
except ImportError:
    wandb = None  # type: ignore[assignment]
    HAS_WANDB = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Device selection
# ---------------------------------------------------------------------------


def get_device(cfg: RegressionConfig) -> torch.device:
    """Select compute device based on config and hardware availability."""
    if cfg.device == "cuda" and torch.cuda.is_available():
        return torch.device("cuda")
    if cfg.device == "cuda":
        logger.warning("CUDA requested but unavailable, falling back to CPU")
    return torch.device("cpu")


# ---------------------------------------------------------------------------
# Optimizer — 3 param groups with differential LR
# ---------------------------------------------------------------------------


def build_optimizer(model: CornerRegressionNetWithVariance, cfg: RegressionConfig) -> AdamW:
    """Build AdamW with 3 param groups: encoder (reduced LR), decoder, head."""
    param_groups = [
        {
            "params": list(model.encoder_params()),
            "lr": cfg.base_lr * cfg.encoder_lr_mult,
        },
        {
            "params": list(model.decoder_params()),
            "lr": cfg.base_lr,
        },
        {
            "params": list(model.head_params()),
            "lr": cfg.base_lr,
        },
    ]
    return AdamW(param_groups, weight_decay=cfg.weight_decay, betas=(0.9, 0.999))


# ---------------------------------------------------------------------------
# Scheduler — linear warmup + cosine decay
# ---------------------------------------------------------------------------


def build_scheduler(optimizer: AdamW, cfg: RegressionConfig) -> LambdaLR:
    """Build LR scheduler with linear warmup then cosine decay."""
    warmup_ratio = cfg.warmup_start_lr / cfg.base_lr

    def lr_lambda(epoch: int) -> float:
        if epoch < cfg.warmup_epochs:
            return warmup_ratio + (1.0 - warmup_ratio) * (epoch / cfg.warmup_epochs)
        progress = (epoch - cfg.warmup_epochs) / max(cfg.total_epochs - cfg.warmup_epochs, 1)
        return warmup_ratio + (1.0 - warmup_ratio) * 0.5 * (1.0 + math.cos(math.pi * progress))

    return LambdaLR(optimizer, lr_lambda)


# ---------------------------------------------------------------------------
# Freeze / unfreeze
# ---------------------------------------------------------------------------


def freeze_encoder(model: CornerRegressionNetWithVariance) -> None:
    """Freeze all encoder parameters."""
    for p in model.encoder_params():
        p.requires_grad_(False)
    logger.info("Encoder frozen")


def unfreeze_encoder(model: CornerRegressionNetWithVariance) -> None:
    """Unfreeze all encoder parameters."""
    for p in model.encoder_params():
        p.requires_grad_(True)
    logger.info("Encoder unfrozen")


def check_unfreeze(
    cfg: RegressionConfig,
    epoch: int,
    val_loss_history: list[float],
    encoder_frozen: bool,
) -> tuple[bool, str]:
    """Check whether encoder should be unfrozen."""
    if not encoder_frozen:
        return (False, "")
    if epoch >= cfg.freeze_max_epochs:
        return (True, f"force_unfreeze at epoch {epoch} >= {cfg.freeze_max_epochs}")
    if epoch >= cfg.freeze_min_epochs and len(val_loss_history) >= cfg.stagnation_window:
        recent = val_loss_history[-cfg.stagnation_window :]
        improvement = (recent[0] - recent[-1]) / (abs(recent[0]) + 1e-8)
        if improvement < cfg.stagnation_threshold:
            return (
                True,
                f"val_loss stagnant: {improvement:.4f} < {cfg.stagnation_threshold}",
            )
    return (False, "")


# ---------------------------------------------------------------------------
# HEM sampler
# ---------------------------------------------------------------------------


def build_hem_sampler(per_image_losses: np.ndarray, cfg: RegressionConfig) -> WeightedRandomSampler:
    """Build WeightedRandomSampler for hard example mining."""
    mean_loss = per_image_losses.mean()
    weights = np.clip(
        per_image_losses / (mean_loss + 1e-8),
        cfg.hem_weight_floor,
        cfg.hem_weight_ceiling,
    )
    return WeightedRandomSampler(
        weights=weights.tolist(), num_samples=len(weights), replacement=True
    )


# ---------------------------------------------------------------------------
# Augmentation phase advance
# ---------------------------------------------------------------------------


def check_phase_advance(
    cfg: RegressionConfig,
    pck4_history: list[float],
    current_phase: AugmentationPhase,
    encoder_frozen: bool,
) -> AugmentationPhase:
    """Check whether augmentation phase should advance."""
    if current_phase == AugmentationPhase.NONE and not encoder_frozen:
        logger.info("Augmentation phase: NONE -> MODERATE (encoder unfrozen)")
        return AugmentationPhase.MODERATE
    if current_phase == AugmentationPhase.MODERATE:
        if len(pck4_history) >= cfg.phase3_sustain_epochs:
            recent = pck4_history[-cfg.phase3_sustain_epochs :]
            if all(p >= cfg.phase3_pck_threshold for p in recent):
                logger.info(
                    "Augmentation phase: MODERATE -> FULL_DRONE (pck4 sustained >= %.2f)",
                    cfg.phase3_pck_threshold,
                )
                return AugmentationPhase.FULL_DRONE
    return current_phase


# ---------------------------------------------------------------------------
# Checkpoint save / load
# ---------------------------------------------------------------------------


def save_checkpoint(
    model: CornerRegressionNetWithVariance,
    optimizer: AdamW,
    scheduler: LambdaLR,
    epoch: int,
    metrics: dict,
    cfg: RegressionConfig,
    path: Path,
    encoder_frozen: bool = True,
    aug_phase: AugmentationPhase = AugmentationPhase.NONE,
    unfreeze_epoch: int | None = None,
) -> Path:
    """Save training checkpoint."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "epoch": epoch,
            "model_type": "regression_variance",
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "scheduler_state_dict": scheduler.state_dict(),
            "metrics": metrics,
            "config": dataclasses.asdict(cfg),
            "encoder_frozen": encoder_frozen,
            "aug_phase": aug_phase.value if isinstance(aug_phase, AugmentationPhase) else aug_phase,
            "unfreeze_epoch": unfreeze_epoch,
        },
        path,
    )
    logger.info("Checkpoint saved: %s (epoch %d)", path, epoch)
    return path


def load_checkpoint(
    path: Path,
    model: CornerRegressionNetWithVariance,
    optimizer: AdamW | None = None,
    scheduler: LambdaLR | None = None,
) -> dict:
    """Load training checkpoint."""
    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    if optimizer is not None and "optimizer_state_dict" in checkpoint:
        optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
    if scheduler is not None and "scheduler_state_dict" in checkpoint:
        scheduler.load_state_dict(checkpoint["scheduler_state_dict"])
    logger.info("Checkpoint loaded: %s (epoch %d)", path, checkpoint.get("epoch", -1))
    return checkpoint


# ---------------------------------------------------------------------------
# Interactive config polling
# ---------------------------------------------------------------------------


def poll_config(cfg: RegressionConfig, config_path: Path) -> RegressionConfig:
    """Poll config override file for interactive control."""
    config_path = Path(config_path)
    if not config_path.exists():
        return cfg
    try:
        data = json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("Failed to parse config override at %s, ignoring", config_path)
        return cfg
    valid_fields = {f.name for f in dataclasses.fields(cfg)}
    for key, value in data.items():
        if key in valid_fields:
            setattr(cfg, key, value)
    return cfg


# ---------------------------------------------------------------------------
# train_one_epoch
# ---------------------------------------------------------------------------


def train_one_epoch(
    model: CornerRegressionNetWithVariance,
    loader: DataLoader,
    optimizer: AdamW,
    cfg: RegressionConfig,
    device: torch.device,
    per_image_losses: np.ndarray | None = None,
) -> dict[str, float]:
    """Train for one epoch. Returns dict with train_loss, sigma stats."""
    model.train()
    total_loss = 0.0
    total_sigma_mean = 0.0
    total_sigma_min = 0.0
    n_batches = 0

    for batch in loader:
        images, coord_targets = batch[0].to(device), batch[1].to(device)
        dataset_indices = batch[2] if len(batch) > 2 else None

        coords, sigma = model(images)
        loss = gaussian_nll_loss(coords, coord_targets, sigma, cfg.input_w, cfg.input_h)

        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip_max_norm)
        optimizer.step()

        # Per-image loss tracking for HEM
        if per_image_losses is not None and dataset_indices is not None:
            with torch.no_grad():
                batch_size = images.shape[0]
                for i in range(batch_size):
                    img_coords, img_sigma = model(images[i : i + 1])
                    img_loss = gaussian_nll_loss(
                        img_coords,
                        coord_targets[i : i + 1],
                        img_sigma,
                        cfg.input_w,
                        cfg.input_h,
                    )
                    ds_idx = int(dataset_indices[i])
                    if ds_idx < len(per_image_losses):
                        per_image_losses[ds_idx] = img_loss.item()

        total_loss += loss.item()
        with torch.no_grad():
            total_sigma_mean += sigma.mean().item()
            total_sigma_min += sigma.min().item()
        n_batches += 1

    if n_batches == 0:
        return {"train_loss": 0.0, "train_reg_loss": 0.0, "sigma_mean": 0.0, "sigma_min": 0.0}

    avg_loss = total_loss / n_batches
    return {
        "train_loss": avg_loss,
        "train_reg_loss": avg_loss,
        "sigma_mean": total_sigma_mean / n_batches,
        "sigma_min": total_sigma_min / n_batches,
    }


# ---------------------------------------------------------------------------
# W&B integration
# ---------------------------------------------------------------------------


def init_wandb(cfg: RegressionConfig) -> Any:
    """Initialize W&B run if enabled and available."""
    if not cfg.wandb_enabled or not HAS_WANDB:
        return None
    return wandb.init(  # type: ignore[union-attr]
        project=cfg.wandb_project,
        config=dataclasses.asdict(cfg),
        tags=["variance"],
    )


def log_epoch(
    run: Any,
    epoch: int,
    train_metrics: dict[str, float],
    val_metrics: dict[str, Any],
    lr: float,
    encoder_frozen: bool,
    aug_phase: AugmentationPhase,
) -> None:
    """Log per-epoch metrics to W&B."""
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
            "train/sigma_min": train_metrics.get("sigma_min", 0),
            "val/mean_cpe": val_metrics.get("mean_cpe", 0),
            "val/pck_2": val_metrics.get("pck_2", 0),
            "val/pck_4": pck4,
            "val/pck_8": pck8,
            "val/pck4_pck8_ratio": pck4_pck8_ratio,
            "val/attention_entropy": val_metrics.get("attention_entropy", 0),
            "val/attention_peak_mean": val_metrics.get("attention_peak_mean", 0),
            "val/attention_peak_min": val_metrics.get("attention_peak_min", 0),
            "val/mean_confidence": val_metrics.get("mean_confidence", 0),
            "val/sigma_mean": val_metrics.get("sigma_mean", 0),
            "val/sigma_min": val_metrics.get("sigma_min", 0),
            "val/sigma_error_corr": val_metrics.get("sigma_error_correlation", 0),
            "val/calibration_1sigma": val_metrics.get("calibration_1sigma", 0),
            "lr": lr,
            "encoder_frozen": int(encoder_frozen),
            "aug_phase": aug_phase.value,
        },
        step=epoch,
    )


# ---------------------------------------------------------------------------
# Main training loop
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


def train(cfg: RegressionConfig, resume_path: Path | None = None) -> Path:
    """Run complete training loop. Returns path to best checkpoint."""
    if cfg.total_epochs <= 0:
        msg = f"total_epochs must be > 0, got {cfg.total_epochs} epochs"
        raise ValueError(msg)

    from training.regression.dataset import PlateCornerRegressionDataset
    from training.regression.variance.evaluate import validate
    from training.regression.variance.model import CornerRegressionNetWithVariance

    # --- SETUP ---
    device = get_device(cfg)
    model = CornerRegressionNetWithVariance(cfg).to(device)
    freeze_encoder(model)
    optimizer = build_optimizer(model, cfg)
    scheduler = build_scheduler(optimizer, cfg)

    train_dataset = PlateCornerRegressionDataset(cfg, split="train")
    val_dataset = PlateCornerRegressionDataset(cfg, split="val")
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
        ckpt = load_checkpoint(resume_path, model, optimizer, scheduler)
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
        cfg = poll_config(cfg, config_path)
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
            )
            logger.info("Training paused at epoch %d", epoch)
            while _check_pause(config_path):
                time.sleep(cfg.config_poll_interval)
                cfg = poll_config(cfg, config_path)
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

        # 4. Rebuild DataLoader with HEM sampler if active
        if per_image_losses is not None and not encoder_frozen:
            sampler = build_hem_sampler(per_image_losses, cfg)
            train_loader = DataLoader(
                train_dataset,
                batch_size=cfg.batch_size,
                sampler=sampler,
                num_workers=0,
                pin_memory=False,
            )

        # 5. Train one epoch
        train_metrics = train_one_epoch(
            model, train_loader, optimizer, cfg, device, per_image_losses
        )

        # 6. Scheduler step
        scheduler.step()

        # 7. Validate
        val_metrics = validate(model, val_loader)
        val_loss_history.append(val_metrics["mean_cpe"])
        pck4_history.append(val_metrics["pck_4"])

        # 8. Log metrics
        current_lr = optimizer.param_groups[1]["lr"]
        log_epoch(run, epoch, train_metrics, val_metrics, current_lr, encoder_frozen, aug_phase)

        # 9. Best checkpoint by pck_4
        if val_metrics["pck_4"] > best_pck4:
            best_pck4 = val_metrics["pck_4"]
            epochs_since_improvement = 0
            best_checkpoint_path = save_checkpoint(
                model,
                optimizer,
                scheduler,
                epoch,
                {**val_metrics, "val_loss_history": val_loss_history, "pck4_history": pck4_history},
                cfg,
                ckpt_dir / "best.pt",
                encoder_frozen,
                aug_phase,
                unfreeze_epoch=unfreeze_epoch,
            )
        else:
            epochs_since_improvement += 1

        # 9b. Early stopping
        if cfg.early_stopping_patience > 0 and epochs_since_improvement >= cfg.early_stopping_patience:
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
                {**val_metrics, "val_loss_history": val_loss_history, "pck4_history": pck4_history},
                cfg,
                ckpt_dir / f"checkpoint_epoch{epoch}.pt",
                encoder_frozen,
                aug_phase,
                unfreeze_epoch=unfreeze_epoch,
            )

        logger.info(
            "Epoch %d/%d: loss=%.4f, pck_4=%.4f, sigma_mean=%.2f, sigma_min=%.2f, lr=%.2e, frozen=%s, aug=%s",
            epoch + 1,
            cfg.total_epochs,
            train_metrics["train_loss"],
            val_metrics["pck_4"],
            train_metrics["sigma_mean"],
            train_metrics["sigma_min"],
            current_lr,
            encoder_frozen,
            aug_phase.name,
        )

    if run is not None:
        run.finish()

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
        )

    return best_checkpoint_path
