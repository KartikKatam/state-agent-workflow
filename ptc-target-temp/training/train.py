"""Training infrastructure for corner CNN — chunks 07+08.

Provides: optimizer with differential LR, cosine-warmup scheduler,
freeze/unfreeze with stagnation detection, HEM sampler, checkpoint
save/load, interactive config polling, device selection,
train_one_epoch, W&B integration, and the main train() loop.
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

from training.config import AugmentationPhase, TrainingConfig
from training.losses import combined_loss

if TYPE_CHECKING:
    from training.model import CornerHeatmapNet

# Optional W&B import
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


def get_device(cfg: TrainingConfig) -> torch.device:
    """Select compute device based on config and hardware availability."""
    if cfg.device == "cuda" and torch.cuda.is_available():
        return torch.device("cuda")
    if cfg.device == "cuda":
        logger.warning("CUDA requested but unavailable, falling back to CPU")
    return torch.device("cpu")


# ---------------------------------------------------------------------------
# Optimizer — 3 param groups with differential LR
# ---------------------------------------------------------------------------


def build_optimizer(model: CornerHeatmapNet, cfg: TrainingConfig) -> AdamW:
    """Build AdamW with 3 param groups: encoder (reduced LR), decoder, heads."""
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


def build_scheduler(optimizer: AdamW, cfg: TrainingConfig) -> LambdaLR:
    """Build LR scheduler with linear warmup then cosine decay.

    The lr_lambda returns a multiplier applied to each param group's base LR.
    During warmup: interpolates from warmup_start_lr/base_lr to 1.0.
    After warmup: cosine decays from 1.0 toward warmup_start_lr/base_lr.
    """
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


def freeze_encoder(model: CornerHeatmapNet) -> None:
    """Freeze all encoder parameters (requires_grad=False)."""
    for p in model.encoder_params():
        p.requires_grad_(False)
    logger.info("Encoder frozen")


def unfreeze_encoder(model: CornerHeatmapNet) -> None:
    """Unfreeze all encoder parameters (requires_grad=True)."""
    for p in model.encoder_params():
        p.requires_grad_(True)
    logger.info("Encoder unfrozen")


def check_unfreeze(
    cfg: TrainingConfig,
    epoch: int,
    val_loss_history: list[float],
    encoder_frozen: bool,
) -> tuple[bool, str]:
    """Check whether encoder should be unfrozen based on stagnation or max epoch.

    Returns (should_unfreeze, reason_string).
    """
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


def build_hem_sampler(per_image_losses: np.ndarray, cfg: TrainingConfig) -> WeightedRandomSampler:
    """Build WeightedRandomSampler for hard example mining.

    Weights = clip(losses / (mean + eps), floor, ceiling).
    """
    mean_loss = per_image_losses.mean()
    weights = np.clip(
        per_image_losses / (mean_loss + 1e-8),
        cfg.hem_weight_floor,
        cfg.hem_weight_ceiling,
    )
    logger.debug(
        "HEM sampler: mean_loss=%.4f, weight range=[%.2f, %.2f]",
        mean_loss,
        weights.min(),
        weights.max(),
    )
    return WeightedRandomSampler(
        weights=weights.tolist(), num_samples=len(weights), replacement=True
    )


# ---------------------------------------------------------------------------
# Sigma schedule
# ---------------------------------------------------------------------------


def get_scheduled_sigma(epoch: int, schedule: list[tuple[int, float]]) -> float:
    """Return the sigma for a given epoch based on a step schedule.

    The schedule is a list of (epoch, sigma) sorted by epoch.
    Returns the sigma of the last entry whose epoch <= current epoch.
    """
    current_sigma = schedule[0][1]
    for sched_epoch, sigma in schedule:
        if epoch >= sched_epoch:
            current_sigma = sigma
        else:
            break
    return current_sigma


# ---------------------------------------------------------------------------
# Augmentation phase advance
# ---------------------------------------------------------------------------


def check_phase_advance(
    cfg: TrainingConfig,
    pck4_history: list[float],
    current_phase: AugmentationPhase,
    encoder_frozen: bool,
) -> AugmentationPhase:
    """Check whether augmentation phase should advance.

    NONE -> MODERATE when encoder is unfrozen.
    MODERATE -> FULL_DRONE when last phase3_sustain_epochs pck4 all >= threshold.
    """
    if current_phase == AugmentationPhase.NONE and not encoder_frozen:
        logger.info("Augmentation phase: NONE -> MODERATE (encoder unfrozen)")
        return AugmentationPhase.MODERATE
    if current_phase == AugmentationPhase.MODERATE:
        if len(pck4_history) >= cfg.phase3_sustain_epochs:
            recent = pck4_history[-cfg.phase3_sustain_epochs :]
            if all(p >= cfg.phase3_pck_threshold for p in recent):
                logger.info(
                    "Augmentation phase: MODERATE -> FULL_DRONE (pck4 sustained >= %.2f for %d epochs)",
                    cfg.phase3_pck_threshold,
                    cfg.phase3_sustain_epochs,
                )
                return AugmentationPhase.FULL_DRONE
    return current_phase


# ---------------------------------------------------------------------------
# Checkpoint save / load
# ---------------------------------------------------------------------------


def save_checkpoint(
    model: CornerHeatmapNet,
    optimizer: AdamW,
    scheduler: LambdaLR,
    epoch: int,
    metrics: dict,
    cfg: TrainingConfig,
    path: Path,
    encoder_frozen: bool = True,
    aug_phase: AugmentationPhase = AugmentationPhase.NONE,
    unfreeze_epoch: int | None = None,
) -> Path:
    """Save training checkpoint. Creates parent directories if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "epoch": epoch,
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
    model: CornerHeatmapNet,
    optimizer: AdamW | None = None,
    scheduler: LambdaLR | None = None,
) -> dict:
    """Load training checkpoint. Restores model (required), optimizer and scheduler (optional)."""
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


def poll_config(cfg: TrainingConfig, config_path: Path) -> TrainingConfig:
    """Poll config override file for interactive control.

    If the file exists and contains valid JSON, apply known keys to cfg.
    Unknown keys are silently ignored. Malformed JSON returns cfg unchanged.
    """
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
            logger.debug("Config override: %s = %s", key, value)
    return cfg


# ---------------------------------------------------------------------------
# train_one_epoch
# ---------------------------------------------------------------------------


def train_one_epoch(
    model: CornerHeatmapNet,
    loader: DataLoader,
    optimizer: AdamW,
    cfg: TrainingConfig,
    device: torch.device,
    per_image_losses: np.ndarray | None = None,
) -> dict[str, float]:
    """Train for one epoch. Returns dict with train_loss, train_hm_loss, train_off_loss."""
    model.train()
    total_loss = 0.0
    total_hm = 0.0
    total_off = 0.0
    n_batches = 0

    for batch_idx, batch in enumerate(loader):
        images, hm_target, off_target, off_mask = [t.to(device) for t in batch[:4]]
        dataset_indices = batch[4] if len(batch) > 4 else None

        output = model(images)
        loss_total, loss_hm, loss_off = combined_loss(
            output, hm_target, off_target, off_mask, cfg.lambda_offset
        )

        optimizer.zero_grad()
        loss_total.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip_max_norm)
        optimizer.step()

        # Per-image loss tracking for HEM (uses actual dataset indices)
        if per_image_losses is not None and dataset_indices is not None:
            with torch.no_grad():
                batch_size = images.shape[0]
                for i in range(batch_size):
                    img_loss, _, _ = combined_loss(
                        output[i : i + 1],
                        hm_target[i : i + 1],
                        off_target[i : i + 1],
                        off_mask[i : i + 1],
                        cfg.lambda_offset,
                    )
                    ds_idx = int(dataset_indices[i])
                    if ds_idx < len(per_image_losses):
                        per_image_losses[ds_idx] = img_loss.item()

        total_loss += loss_total.item()
        total_hm += loss_hm.item()
        total_off += loss_off.item()
        n_batches += 1

    if n_batches == 0:
        return {"train_loss": 0.0, "train_hm_loss": 0.0, "train_off_loss": 0.0}

    return {
        "train_loss": total_loss / n_batches,
        "train_hm_loss": total_hm / n_batches,
        "train_off_loss": total_off / n_batches,
    }


# ---------------------------------------------------------------------------
# W&B integration (optional)
# ---------------------------------------------------------------------------


def init_wandb(cfg: TrainingConfig) -> Any:
    """Initialize W&B run if enabled and available.

    Returns the wandb run object or None.
    """
    if not cfg.wandb_enabled or not HAS_WANDB:
        return None
    return wandb.init(project=cfg.wandb_project, config=dataclasses.asdict(cfg))  # type: ignore[union-attr]


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
    run.log(
        {
            "epoch": epoch,
            "train/loss": train_metrics.get("train_loss", 0),
            "train/hm_loss": train_metrics.get("train_hm_loss", 0),
            "train/off_loss": train_metrics.get("train_off_loss", 0),
            "val/mean_cpe": val_metrics.get("mean_cpe", 0),
            "val/pck_2": val_metrics.get("pck_2", 0),
            "val/pck_4": val_metrics.get("pck_4", 0),
            "val/pck_8": val_metrics.get("pck_8", 0),
            "val/mean_confidence": val_metrics.get("mean_confidence", 0),
            "lr": lr,
            "encoder_frozen": int(encoder_frozen),
            "aug_phase": aug_phase.value,
        },
        step=epoch,
    )


def log_visualizations(
    run: Any,
    epoch: int,
    model: CornerHeatmapNet,
    val_loader: DataLoader,
    cfg: TrainingConfig,
    device: torch.device,
) -> None:
    """Log visualization grids to W&B at vis_interval epochs."""
    if run is None or epoch % cfg.vis_interval != 0:
        return
    if not HAS_WANDB:
        return

    from training.evaluate import (
        draw_corner_overlay,
        extract_corners_from_heatmap,
        render_heatmap_overlay,
    )

    model.eval()
    images_logged = 0
    max_images = 8

    for batch in val_loader:
        imgs_tensor = batch[0].to(device)
        hm_targets = batch[1]

        with torch.no_grad():
            output = model(imgs_tensor)

        for i in range(min(imgs_tensor.shape[0], max_images - images_logged)):
            # Denormalize image for visualization
            img_np = imgs_tensor[i].cpu().permute(1, 2, 0).numpy()
            img_np = (img_np * 255).clip(0, 255).astype(np.uint8)

            # extract_corners_from_heatmap expects (1, 12, H, W) output
            pred_corners, pred_conf = extract_corners_from_heatmap(output[i : i + 1], cfg.stride)
            gt_corners_raw = hm_targets[i].numpy()
            # GT corners from heatmap peaks (approximate)
            gt_corners: list[tuple[float, float]] = []
            for c in range(4):
                hy, hx = np.unravel_index(gt_corners_raw[c].argmax(), gt_corners_raw[c].shape)
                gt_corners.append((float(hx * cfg.stride), float(hy * cfg.stride)))

            overlay = draw_corner_overlay(img_np, pred_corners, gt_corners, pred_conf)
            heatmap_vis = render_heatmap_overlay(img_np, output[i, :4].cpu().numpy())

            run.log(
                {
                    f"vis/corner_overlay_{images_logged}": wandb.Image(overlay),  # type: ignore[union-attr]
                    f"vis/heatmap_{images_logged}": wandb.Image(heatmap_vis),  # type: ignore[union-attr]
                },
                step=epoch,
            )
            images_logged += 1
            if images_logged >= max_images:
                break

        if images_logged >= max_images:
            break

    model.train()


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


def train(cfg: TrainingConfig, resume_path: Path | None = None) -> Path:
    """Run complete training loop. Returns path to best checkpoint.

    Orchestrates: model init, freeze/unfreeze, augmentation phase advancement,
    HEM activation, config polling, pause/resume, best/periodic checkpoints,
    W&B logging, and validation.
    """
    if cfg.total_epochs <= 0:
        msg = f"total_epochs must be > 0, got {cfg.total_epochs} epochs"
        raise ValueError(msg)

    from training.dataset import PlateCornerDataset
    from training.evaluate import validate
    from training.model import CornerHeatmapNet

    # --- SETUP ---
    device = get_device(cfg)
    model = CornerHeatmapNet(cfg).to(device)
    freeze_encoder(model)
    optimizer = build_optimizer(model, cfg)
    scheduler = build_scheduler(optimizer, cfg)

    train_dataset = PlateCornerDataset(cfg, split="train")
    val_dataset = PlateCornerDataset(cfg, split="val")
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
        # Restore metrics history if available
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

        # 1b. Sigma curriculum schedule
        if cfg.sigma_schedule is not None:
            new_sigma = get_scheduled_sigma(epoch, cfg.sigma_schedule)
            if epoch == start_epoch or new_sigma != get_scheduled_sigma(max(0, epoch - 1), cfg.sigma_schedule):
                train_dataset.set_sigma(new_sigma)
                val_dataset.set_sigma(new_sigma)
                logger.info("Sigma schedule: epoch %d -> sigma=%.2f", epoch, new_sigma)

        # 2. Check unfreeze (SAVE checkpoint BEFORE unfreezing)
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

        # 3. Check aug phase advance (SAVE checkpoint BEFORE phase change)
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

        # 3b. Compute augmentation ramp factor
        aug_ramp_factor = 1.0
        if unfreeze_epoch is not None and cfg.aug_ramp_epochs > 0:
            epochs_since_unfreeze = epoch - unfreeze_epoch
            if epochs_since_unfreeze < cfg.aug_delay_after_unfreeze:
                aug_ramp_factor = 0.0  # still in delay period
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

        # 6. Scheduler step (AFTER training)
        scheduler.step()

        # 7. Validate
        val_metrics = validate(model, val_loader, cfg)
        val_loss_history.append(val_metrics["mean_cpe"])
        pck4_history.append(val_metrics["pck_4"])

        # 8. Log metrics and visualizations
        current_lr = optimizer.param_groups[1]["lr"]
        log_epoch(run, epoch, train_metrics, val_metrics, current_lr, encoder_frozen, aug_phase)
        log_visualizations(run, epoch, model, val_loader, cfg, device)

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

        # 9b. Early stopping check
        if cfg.early_stopping_patience > 0 and epochs_since_improvement >= cfg.early_stopping_patience:
            logger.info(
                "Early stopping at epoch %d: no PCK@4 improvement for %d epochs (best=%.4f at epoch %d)",
                epoch,
                cfg.early_stopping_patience,
                best_pck4,
                epoch - epochs_since_improvement,
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
            "Epoch %d/%d: train_loss=%.4f, pck_4=%.4f, lr=%.2e, frozen=%s, aug=%s",
            epoch + 1,
            cfg.total_epochs,
            train_metrics["train_loss"],
            val_metrics["pck_4"],
            current_lr,
            encoder_frozen,
            aug_phase.name,
        )

    if run is not None:
        run.finish()

    if best_checkpoint_path is None:
        # Save final checkpoint if no improvement was recorded
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
