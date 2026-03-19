"""Training entry point for stride-2 regression experiment.

Runs CornerRegressionNetS2 with identical hyperparameters to the stride-4
baseline, except stride=2 and total_epochs=125. Saves comparison checkpoints
at the stride-4 best epoch (98) and every 5 epochs from epoch 100 onward.

Usage:
    .venv/bin/python -m training.regression.stride2.run
"""

from __future__ import annotations

import dataclasses
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch

from training.config import AugmentationPhase
from training.regression.config import RegressionConfig
from training.regression.stride2.model import CornerRegressionNetS2
from training.regression.train import (
    _check_pause,
    build_hem_sampler,
    build_optimizer,
    build_scheduler,
    check_phase_advance,
    check_unfreeze,
    freeze_encoder,
    get_device,
    load_checkpoint,
    log_epoch,
    log_visualizations,
    poll_config,
    save_checkpoint,
    train_one_epoch,
    unfreeze_encoder,
)

try:
    import wandb

    HAS_WANDB = True
except ImportError:
    wandb = None  # type: ignore[assignment]
    HAS_WANDB = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stride-4 baseline reference (for comparison logging)
# ---------------------------------------------------------------------------
STRIDE4_BEST_EPOCH = 98
STRIDE4_BEST_PCK4 = 0.890
STRIDE4_BEST_CPE = 2.28
STRIDE4_TOTAL_EPOCHS = 100
POST_BASELINE_CKPT_INTERVAL = 5


# ---------------------------------------------------------------------------
# Main training loop (adapted from training.regression.train.train)
# ---------------------------------------------------------------------------


def train_stride2(cfg: RegressionConfig, resume_path: Path | None = None) -> Path:
    """Run stride-2 training with comparison tracking against stride-4 baseline.

    Identical to training.regression.train.train() except:
    1. Uses CornerRegressionNetS2 model
    2. Saves a comparison checkpoint at stride-4 best epoch (98)
    3. Checkpoints every 5 epochs after stride-4 total epochs (100)
    4. Logs stride-4 vs stride-2 deltas at comparison points
    """
    if cfg.total_epochs <= 0:
        msg = f"total_epochs must be > 0, got {cfg.total_epochs} epochs"
        raise ValueError(msg)

    from training.regression.dataset import PlateCornerRegressionDataset
    from training.regression.evaluate import validate

    # --- SETUP ---
    device = get_device(cfg)
    model = CornerRegressionNetS2(cfg).to(device)
    freeze_encoder(model)  # type: ignore[arg-type]
    optimizer = build_optimizer(model, cfg)  # type: ignore[arg-type]
    scheduler = build_scheduler(optimizer, cfg)

    train_dataset = PlateCornerRegressionDataset(cfg, split="train")
    val_dataset = PlateCornerRegressionDataset(cfg, split="val")
    train_loader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=cfg.batch_size,
        shuffle=True,
        num_workers=0,
        pin_memory=False,
    )
    val_loader = torch.utils.data.DataLoader(
        val_dataset,
        batch_size=cfg.batch_size,
        shuffle=False,
        num_workers=0,
        pin_memory=False,
    )

    # W&B init with explicit run name for easy dashboard identification
    run: Any = None
    if cfg.wandb_enabled and HAS_WANDB:
        run = wandb.init(  # type: ignore[union-attr]
            project=cfg.wandb_project,
            config=dataclasses.asdict(cfg),
            name=f"stride2-{cfg.total_epochs}ep",
        )

    config_path = Path(cfg.config_override_path)
    ckpt_dir = Path(cfg.checkpoint_dir)

    encoder_frozen = True
    aug_phase = AugmentationPhase.NONE
    best_pck4 = 0.0
    best_epoch = -1
    best_checkpoint_path: Path | None = None
    val_loss_history: list[float] = []
    pck4_history: list[float] = []
    per_image_losses: np.ndarray | None = None
    unfreeze_epoch: int | None = None
    epochs_since_improvement = 0

    # --- RESUME ---
    start_epoch = 0
    if resume_path is not None:
        ckpt = load_checkpoint(resume_path, model, optimizer, scheduler)  # type: ignore[arg-type]
        start_epoch = ckpt["epoch"] + 1
        encoder_frozen = ckpt.get("encoder_frozen", True)
        aug_phase = AugmentationPhase(ckpt.get("aug_phase", 0))
        if not encoder_frozen:
            unfreeze_encoder(model)  # type: ignore[arg-type]
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
                model,  # type: ignore[arg-type]
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
                model,  # type: ignore[arg-type]
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
            unfreeze_encoder(model)  # type: ignore[arg-type]
            encoder_frozen = False
            unfreeze_epoch = epoch
            optimizer = build_optimizer(model, cfg)  # type: ignore[arg-type]
            scheduler = build_scheduler(optimizer, cfg)
            per_image_losses = np.zeros(len(train_dataset))
            logger.info("Encoder unfrozen at epoch %d: %s", epoch, reason)

        # 3. Check aug phase advance
        new_phase = check_phase_advance(cfg, pck4_history, aug_phase, encoder_frozen)
        if new_phase != aug_phase:
            save_checkpoint(
                model,  # type: ignore[arg-type]
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
            train_loader = torch.utils.data.DataLoader(
                train_dataset,
                batch_size=cfg.batch_size,
                sampler=sampler,
                num_workers=0,
                pin_memory=False,
            )

        # 5. Train one epoch
        train_metrics = train_one_epoch(
            model, train_loader, optimizer, cfg, device, per_image_losses  # type: ignore[arg-type]
        )

        # 6. Scheduler step
        scheduler.step()

        # 7. Validate
        val_metrics = validate(model, val_loader)
        val_loss_history.append(val_metrics["mean_cpe"])
        pck4_history.append(val_metrics["pck_4"])

        # 8. Log metrics and visualizations
        current_lr = optimizer.param_groups[1]["lr"]
        log_epoch(run, epoch, train_metrics, val_metrics, current_lr, encoder_frozen, aug_phase)
        log_visualizations(run, epoch, model, val_loader, cfg, device)  # type: ignore[arg-type]

        # 8b. Log stride-4 comparison deltas to W&B (from stride-4 best epoch onward)
        if run is not None and epoch >= STRIDE4_BEST_EPOCH:
            run.log(
                {
                    "comparison/pck4_delta_vs_s4": val_metrics["pck_4"] - STRIDE4_BEST_PCK4,
                    "comparison/cpe_delta_vs_s4": val_metrics["mean_cpe"] - STRIDE4_BEST_CPE,
                },
                step=epoch,
            )

        # ---------------------------------------------------------------
        # STRIDE-4 COMPARISON POINT (epoch 98)
        # ---------------------------------------------------------------
        if epoch == STRIDE4_BEST_EPOCH:
            delta_pck4 = val_metrics["pck_4"] - STRIDE4_BEST_PCK4
            delta_cpe = val_metrics["mean_cpe"] - STRIDE4_BEST_CPE
            logger.info("=" * 60)
            logger.info("STRIDE-4 COMPARISON POINT (epoch %d)", epoch)
            logger.info(
                "  Stride-4 baseline: PCK@4=%.3f, CPE=%.2fpx", STRIDE4_BEST_PCK4, STRIDE4_BEST_CPE
            )
            logger.info(
                "  Stride-2 current:  PCK@4=%.3f, CPE=%.2fpx",
                val_metrics["pck_4"],
                val_metrics["mean_cpe"],
            )
            logger.info("  Delta: PCK@4 %+.4f, CPE %+.2fpx", delta_pck4, delta_cpe)
            logger.info("=" * 60)
            comparison_metrics = {
                **val_metrics,
                "val_loss_history": val_loss_history,
                "pck4_history": pck4_history,
                "stride4_comparison": {
                    "stride4_pck4": STRIDE4_BEST_PCK4,
                    "stride4_cpe": STRIDE4_BEST_CPE,
                    "pck4_delta": delta_pck4,
                    "cpe_delta": delta_cpe,
                },
            }
            save_checkpoint(
                model,  # type: ignore[arg-type]
                optimizer,
                scheduler,
                epoch,
                comparison_metrics,
                cfg,
                ckpt_dir / "comparison_epoch98.pt",
                encoder_frozen,
                aug_phase,
                unfreeze_epoch=unfreeze_epoch,
            )

        # ---------------------------------------------------------------
        # POST-BASELINE TRACKING (every 5 epochs after stride-4 total)
        # ---------------------------------------------------------------
        if epoch >= STRIDE4_TOTAL_EPOCHS:
            extra = epoch - STRIDE4_TOTAL_EPOCHS
            if extra % POST_BASELINE_CKPT_INTERVAL == 0:
                logger.info(
                    "[POST-BASELINE +%d] epoch %d: PCK@4=%.4f (vs s4: %+.4f), "
                    "CPE=%.2fpx (vs s4: %+.2fpx), entropy=%.2f",
                    extra,
                    epoch,
                    val_metrics["pck_4"],
                    val_metrics["pck_4"] - STRIDE4_BEST_PCK4,
                    val_metrics["mean_cpe"],
                    val_metrics["mean_cpe"] - STRIDE4_BEST_CPE,
                    val_metrics.get("attention_entropy", 0),
                )

        # 9. Best checkpoint by pck_4
        if val_metrics["pck_4"] > best_pck4:
            best_pck4 = val_metrics["pck_4"]
            best_epoch = epoch
            epochs_since_improvement = 0
            best_checkpoint_path = save_checkpoint(
                model,  # type: ignore[arg-type]
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

        # 10. Periodic checkpoint — every 5 after baseline, every 10 before
        if epoch >= STRIDE4_TOTAL_EPOCHS:
            should_ckpt = (epoch - STRIDE4_TOTAL_EPOCHS) % POST_BASELINE_CKPT_INTERVAL == 0
        else:
            should_ckpt = epoch % cfg.checkpoint_interval == 0
        if should_ckpt:
            save_checkpoint(
                model,  # type: ignore[arg-type]
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
            "Epoch %d/%d: loss=%.4f, pck_4=%.4f, lr=%.2e, frozen=%s, aug=%s",
            epoch + 1,
            cfg.total_epochs,
            train_metrics["train_loss"],
            val_metrics["pck_4"],
            current_lr,
            encoder_frozen,
            aug_phase.name,
        )

    # --- FINAL SUMMARY ---
    logger.info("=" * 60)
    logger.info("STRIDE-2 EXPERIMENT COMPLETE")
    logger.info("  Total epochs: %d", cfg.total_epochs)
    logger.info("  Best PCK@4: %.4f at epoch %d", best_pck4, best_epoch)
    logger.info(
        "  Stride-4 baseline: PCK@4=%.3f at epoch %d", STRIDE4_BEST_PCK4, STRIDE4_BEST_EPOCH
    )
    logger.info("  Final delta: PCK@4 %+.4f", best_pck4 - STRIDE4_BEST_PCK4)
    logger.info("=" * 60)

    if run is not None:
        run.finish()

    if best_checkpoint_path is None:
        best_checkpoint_path = save_checkpoint(
            model,  # type: ignore[arg-type]
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    cfg = RegressionConfig()
    cfg.stride = 2
    cfg.base_lr = 1e-4
    cfg.encoder_lr_mult = 0.05
    cfg.batch_size = 16
    cfg.freeze_min_epochs = 15
    cfg.freeze_max_epochs = 60
    cfg.wing_w = 10.0
    cfg.wing_epsilon = 2.0
    cfg.attention_temperature = 1.0
    cfg.total_epochs = 125
    cfg.wandb_enabled = True
    cfg.wandb_project = "corner-regression"
    cfg.checkpoint_dir = "training/regression/results/stride2_125ep"
    cfg.checkpoint_interval = 10
    cfg.vis_interval = 10

    best = train_stride2(cfg)
    logger.info("Best checkpoint saved to: %s", best)
