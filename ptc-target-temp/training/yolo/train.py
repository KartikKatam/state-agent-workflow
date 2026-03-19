"""Training entrypoint — wires config, trainer, curriculum, mining, and W&B.

CLI interface for YOLO LPR fine-tuning with deferred callback initialization
and manual W&B metric logging (dev-004 fix: no add_wandb_callback).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from training.yolo.config import YoloTrainingConfig
from training.yolo.trainer import LPRDetectionTrainer

logger = logging.getLogger(__name__)

try:
    import wandb
except ImportError:
    wandb = None  # type: ignore[assignment]


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for YOLO LPR training."""
    parser = argparse.ArgumentParser(description="Fine-tune YOLOv11m for drone-based LPR.")
    parser.add_argument(
        "--data",
        type=str,
        default="training/yolo/data.yaml",
        help="Path to data.yaml (default: training/yolo/data.yaml)",
    )
    parser.add_argument(
        "--checkpoint",
        type=str,
        default="models/yolov11m-lpr-base.pt",
        help="Path to base checkpoint (default: models/yolov11m-lpr-base.pt)",
    )
    parser.add_argument("--epochs", type=int, default=100, help="Number of training epochs")
    parser.add_argument("--batch", type=int, default=-1, help="Batch size (-1 for auto)")
    parser.add_argument("--imgsz", type=int, default=640, help="Image size")
    parser.add_argument("--device", type=str, default="cuda", help="Device (cuda or cpu)")
    parser.add_argument("--run-name", type=str, default="lpr-finetune", help="W&B run name")
    parser.add_argument("--no-wandb", action="store_true", default=False, help="Disable W&B")
    parser.add_argument("--no-mining", action="store_true", default=False, help="Disable mining")
    parser.add_argument(
        "--no-curriculum", action="store_true", default=False, help="Disable curriculum"
    )
    parser.add_argument(
        "--no-drone-aug", action="store_true", default=False, help="Disable drone augmentations"
    )
    parser.add_argument(
        "--resume", type=str, default=None, help="Path to last.pt for resume training"
    )
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> YoloTrainingConfig:
    """Map CLI args to YoloTrainingConfig."""
    return YoloTrainingConfig(
        data_yaml=args.data,
        checkpoint_path=args.checkpoint,
        epochs=args.epochs,
        batch_size=args.batch,
        imgsz=args.imgsz,
        device=args.device,
        run_name=args.run_name,
        wandb_enabled=not args.no_wandb,
        mining_enabled=not args.no_mining,
        drone_aug_enabled=not args.no_drone_aug,
    )


def main() -> None:
    """Training entrypoint: parse args, build config, wire callbacks, train."""
    args = parse_args()
    cfg = build_config(args)

    # Verify checkpoint exists
    checkpoint = Path(cfg.checkpoint_path)
    if not checkpoint.exists():
        logger.error("Checkpoint not found: %s", checkpoint)
        print(
            f"Checkpoint not found: {checkpoint}\n"
            "Download with: python -m training.yolo.setup_checkpoint",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Init W&B (manual — NO add_wandb_callback, dev-004 fix)
    if cfg.wandb_enabled and wandb is not None:
        wandb.init(
            project=cfg.wandb_project,
            name=cfg.run_name,
            config={
                "epochs": cfg.epochs,
                "batch_size": cfg.batch_size,
                "imgsz": cfg.imgsz,
                "device": cfg.device,
                "optimizer": cfg.optimizer,
                "lr0": cfg.lr0,
                "warm_end": cfg.warm_end,
                "refine_start": cfg.refine_start,
                "mining_enabled": cfg.mining_enabled,
                "drone_aug_enabled": cfg.drone_aug_enabled,
                "freeze_backbone": cfg.freeze_backbone,
            },
        )
        logger.info("W&B initialized: project=%s, run=%s", cfg.wandb_project, cfg.run_name)

    # Build overrides dict with WARM phase initial augs
    overrides: dict[str, Any] = {
        "model": cfg.checkpoint_path,
        "data": cfg.data_yaml,
        "epochs": cfg.epochs,
        "batch": cfg.batch_size,
        "imgsz": cfg.imgsz,
        "device": cfg.device,
        "optimizer": cfg.optimizer,
        "lr0": cfg.lr0,
        "lrf": cfg.lrf,
        "warmup_epochs": cfg.warmup_epochs,
        "patience": cfg.patience,
        "weight_decay": cfg.weight_decay,
        "cos_lr": cfg.cos_lr,
        "seed": cfg.seed,
        "workers": cfg.workers,
        "project": cfg.project_dir,
        "name": cfg.run_name,
        "close_mosaic": 0,
    }

    # Apply WARM phase initial aug params
    for key, value in cfg.warm_aug.items():
        overrides[key] = value

    # Resume handling: BOTH model path and resume flag required
    if args.resume:
        overrides["model"] = args.resume
        overrides["resume"] = True

    # Create trainer
    trainer = LPRDetectionTrainer(lpr_config=cfg, overrides=overrides)

    # --- Register callbacks (deferred init pattern) ---
    # CurriculumScheduler and HardNegativeMiner are created inside
    # on_train_start because the trainer isn't fully initialized until then.

    _scheduler_holder: list[Any] = []
    _miner_holder: list[Any] = []

    def _on_train_start(trainer_arg: Any) -> None:
        """Deferred init of CurriculumScheduler and HardNegativeMiner."""
        if not args.no_curriculum:
            from training.yolo.curriculum import CurriculumScheduler

            scheduler = CurriculumScheduler(cfg, trainer_arg)
            _scheduler_holder.append(scheduler)
            logger.info("CurriculumScheduler initialized")

        if cfg.mining_enabled:
            from training.yolo.mining import HardNegativeMiner

            miner = HardNegativeMiner(cfg, trainer_arg)
            _miner_holder.append(miner)
            logger.info("HardNegativeMiner initialized")

    def _on_train_epoch_start(trainer_arg: Any) -> None:
        """Dispatch to curriculum scheduler."""
        if _scheduler_holder:
            _scheduler_holder[0].on_epoch_start(trainer_arg)

    def _on_train_epoch_end(trainer_arg: Any) -> None:
        """Dispatch to hard-negative miner."""
        if _miner_holder:
            _miner_holder[0].on_epoch_end(trainer_arg)

    def _on_fit_epoch_end(trainer_arg: Any) -> None:
        """Manual W&B core metrics logging (dev-004 fix)."""
        if cfg.wandb_enabled and wandb is not None and wandb.run is not None:
            metrics = getattr(trainer_arg, "metrics", None)
            if metrics:
                wandb.log(metrics)

    trainer.add_callback("on_train_start", _on_train_start)
    trainer.add_callback("on_train_epoch_start", _on_train_epoch_start)
    trainer.add_callback("on_train_epoch_end", _on_train_epoch_end)
    trainer.add_callback("on_fit_epoch_end", _on_fit_epoch_end)

    # Train
    logger.info("Starting training: epochs=%d, device=%s", cfg.epochs, cfg.device)
    trainer.train()

    # Optional export
    if cfg.export_onnx or cfg.export_trt:
        from training.yolo.export import export_model

        best_weights = trainer.best
        if best_weights and Path(str(best_weights)).exists():
            logger.info("Exporting model from %s", best_weights)
            export_model(Path(str(best_weights)), cfg)

    # Finish W&B
    if cfg.wandb_enabled and wandb is not None and wandb.run is not None:
        wandb.finish()
        logger.info("W&B run finished")


if __name__ == "__main__":
    main()
