"""Training loop variant with progressive wing loss — w shrinks over training.

Thin wrapper around train.py that overrides wing_w and wing_epsilon per epoch.
Only the epoch loop and train_one_epoch are modified; all other functions are
imported from train.py.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch.utils.data import DataLoader

from training.config import AugmentationPhase
from training.regression.config import RegressionConfig
from training.regression.losses import regression_loss
from training.regression.losses_progressive import progressive_wing_w
from training.regression.train import (
    build_hem_sampler,
    build_optimizer,
    build_scheduler,
    check_phase_advance,
    check_unfreeze,
    freeze_encoder,
    get_device,
    init_wandb,
    load_checkpoint,
    log_visualizations,
    poll_config,
    save_checkpoint,
    unfreeze_encoder,
)

# Optional W&B import
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Modified train_one_epoch — accepts explicit w/epsilon overrides
# ---------------------------------------------------------------------------


def train_one_epoch_progressive(
    model: Any,
    loader: DataLoader,
    optimizer: Any,
    cfg: RegressionConfig,
    device: torch.device,
    wing_w_override: float,
    wing_eps_override: float,
    per_image_losses: np.ndarray | None = None,
) -> dict[str, float]:
    """Train for one epoch with explicit wing loss parameters.

    Identical to train.train_one_epoch except wing_w and wing_epsilon
    come from arguments rather than cfg.
    """
    model.train()
    total_loss = 0.0
    n_batches = 0

    for batch in loader:
        images, coord_targets = batch[0].to(device), batch[1].to(device)
        dataset_indices = batch[2] if len(batch) > 2 else None

        pred = model(images)  # (N, 8)
        loss = regression_loss(
            pred, coord_targets, cfg.input_w, cfg.input_h, wing_w_override, wing_eps_override
        )

        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip_max_norm)
        optimizer.step()

        # Per-image loss tracking for HEM
        if per_image_losses is not None and dataset_indices is not None:
            with torch.no_grad():
                batch_size = images.shape[0]
                for i in range(batch_size):
                    img_loss = regression_loss(
                        pred[i : i + 1],
                        coord_targets[i : i + 1],
                        cfg.input_w,
                        cfg.input_h,
                        wing_w_override,
                        wing_eps_override,
                    )
                    ds_idx = int(dataset_indices[i])
                    if ds_idx < len(per_image_losses):
                        per_image_losses[ds_idx] = img_loss.item()

        total_loss += loss.item()
        n_batches += 1

    if n_batches == 0:
        return {"train_loss": 0.0, "train_reg_loss": 0.0}

    avg_loss = total_loss / n_batches
    return {"train_loss": avg_loss, "train_reg_loss": avg_loss}


# ---------------------------------------------------------------------------
# Modified log_epoch — adds wing_w_current metric
# ---------------------------------------------------------------------------


def log_epoch_progressive(
    run: Any,
    epoch: int,
    train_metrics: dict[str, float],
    val_metrics: dict[str, Any],
    lr: float,
    encoder_frozen: bool,
    aug_phase: AugmentationPhase,
    wing_w_current: float,
) -> None:
    """Log per-epoch metrics to W&B, including current wing_w."""
    if run is None:
        return
    pck4 = val_metrics.get("pck_4", 0)
    pck8 = val_metrics.get("pck_8", 0)
    pck4_pck8_ratio = pck4 / max(pck8, 0.001)
    run.log(
        {
            "epoch": epoch,
            "train/reg_loss": train_metrics.get("train_reg_loss", 0),
            "val/mean_cpe": val_metrics.get("mean_cpe", 0),
            "val/pck_2": val_metrics.get("pck_2", 0),
            "val/pck_4": pck4,
            "val/pck_8": pck8,
            "val/pck4_pck8_ratio": pck4_pck8_ratio,
            "val/attention_entropy": val_metrics.get("attention_entropy", 0),
            "val/attention_peak_mean": val_metrics.get("attention_peak_mean", 0),
            "val/attention_peak_min": val_metrics.get("attention_peak_min", 0),
            "val/mean_confidence": val_metrics.get("mean_confidence", 0),
            "lr": lr,
            "encoder_frozen": int(encoder_frozen),
            "aug_phase": aug_phase.value,
            "wing_w_current": wing_w_current,
        },
        step=epoch,
    )


# ---------------------------------------------------------------------------
# _check_pause (imported pattern from train.py)
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
# Main training loop with progressive wing loss
# ---------------------------------------------------------------------------


def train_progressive(cfg: RegressionConfig, resume_path: Path | None = None) -> Path:
    """Run training with progressive wing loss. Returns path to best checkpoint.

    Wing width decays linearly from cfg.wing_w to w_end=3.0 over training.
    Epsilon scales proportionally: eps = cfg.wing_epsilon * (current_w / cfg.wing_w).
    """
    if cfg.total_epochs <= 0:
        msg = f"total_epochs must be > 0, got {cfg.total_epochs} epochs"
        raise ValueError(msg)

    from training.regression.dataset import PlateCornerRegressionDataset
    from training.regression.evaluate import validate
    from training.regression.model import CornerRegressionNet

    # Progressive parameters
    w_start = cfg.wing_w  # 10.0
    w_end = 3.0
    eps_start = cfg.wing_epsilon  # 2.0

    # --- SETUP ---
    device = get_device(cfg)
    model = CornerRegressionNet(cfg).to(device)
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
        # 0. Compute progressive wing loss parameters
        current_w = progressive_wing_w(epoch, cfg.total_epochs, w_start, w_end)
        current_eps = eps_start * (current_w / w_start)
        logger.info(
            "Epoch %d: progressive wing_w=%.2f, wing_eps=%.3f",
            epoch,
            current_w,
            current_eps,
        )

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

        # 5. Train one epoch with progressive wing loss
        train_metrics = train_one_epoch_progressive(
            model,
            train_loader,
            optimizer,
            cfg,
            device,
            wing_w_override=current_w,
            wing_eps_override=current_eps,
            per_image_losses=per_image_losses,
        )

        # 6. Scheduler step
        scheduler.step()

        # 7. Validate
        val_metrics = validate(model, val_loader)
        val_loss_history.append(val_metrics["mean_cpe"])
        pck4_history.append(val_metrics["pck_4"])

        # 8. Log metrics and visualizations
        current_lr = optimizer.param_groups[1]["lr"]
        log_epoch_progressive(
            run, epoch, train_metrics, val_metrics, current_lr, encoder_frozen, aug_phase, current_w
        )
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
                {
                    **val_metrics,
                    "val_loss_history": val_loss_history,
                    "pck4_history": pck4_history,
                },
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
                {
                    **val_metrics,
                    "val_loss_history": val_loss_history,
                    "pck4_history": pck4_history,
                },
                cfg,
                ckpt_dir / f"checkpoint_epoch{epoch}.pt",
                encoder_frozen,
                aug_phase,
                unfreeze_epoch=unfreeze_epoch,
            )

        logger.info(
            "Epoch %d/%d: loss=%.4f, pck_4=%.4f, lr=%.2e, frozen=%s, aug=%s, wing_w=%.2f",
            epoch + 1,
            cfg.total_epochs,
            train_metrics["train_loss"],
            val_metrics["pck_4"],
            current_lr,
            encoder_frozen,
            aug_phase.name,
            current_w,
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
