"""Run a grid of YOLO LPR training experiments sequentially.

Each experiment gets a unique W&B run name and output directory.
Runs sequentially (single GPU). Launch with nohup and walk away.

Usage:
    nohup python -m training.yolo.run_experiments > experiments.log 2>&1 &
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

EXPERIMENTS: list[dict] = [
    {
        # Normal curriculum: default augmentation ramp
        "name": "normal-curriculum-150ep",
        "epochs": 150,
        "imgsz": 640,
        "lr0": 0.001,
        "overrides": {},
    },
    {
        # Aggressive curriculum: harder drone geometry, faster ramp, lower refine floor
        "name": "aggressive-curriculum-150ep",
        "epochs": 150,
        "imgsz": 640,
        "lr0": 0.001,
        "overrides": {
            "warm_end": 0.10,               # shorter warmup (15 ep vs 22)
            "peak_start": 0.25,             # reach max intensity sooner (37 ep vs 60)
            "refine_floor": 0.3,            # harder refinement (30% vs 50%)
            "drone_affine_shear_x": (-20, 20),   # stronger oblique x-shear
            "drone_affine_shear_y": (-35, 35),   # stronger oblique y-shear
            "drone_affine_prob": 0.85,            # more frequent affine
            "drone_perspective_limit": 0.30,      # stronger keystone (0.30 vs 0.20)
            "drone_scale_limit": 0.50,            # more altitude variation (0.50 vs 0.40)
            "drone_occlusion_prob": 0.40,         # more occlusion (0.40 vs 0.25)
            "drone_motion_blur_limit": 11,        # heavier motion blur (11 vs 7)
            "drone_noise_var_limit": (10.0, 70.0),  # noisier sensor (70 vs 50)
        },
    },
]


def run_experiment(exp: dict) -> None:
    """Run a single training experiment."""
    from training.yolo.config import YoloTrainingConfig
    from training.yolo.trainer import LPRDetectionTrainer

    try:
        import wandb
    except ImportError:
        wandb = None  # type: ignore[assignment]

    name = exp["name"]
    logger.info("=" * 60)
    logger.info("STARTING EXPERIMENT: %s", name)
    logger.info("=" * 60)

    # Build config with experiment-specific overrides
    cfg = YoloTrainingConfig(
        epochs=exp["epochs"],
        imgsz=exp["imgsz"],
        lr0=exp["lr0"],
        run_name=name,
        **exp["overrides"],
    )

    # Verify checkpoint
    checkpoint = Path(cfg.checkpoint_path)
    if not checkpoint.exists():
        logger.error("Checkpoint not found: %s — skipping %s", checkpoint, name)
        return

    # Init W&B
    if cfg.wandb_enabled and wandb is not None:
        wandb.init(
            project=cfg.wandb_project,
            name=name,
            config={
                "epochs": cfg.epochs,
                "batch_size": cfg.batch_size,
                "imgsz": cfg.imgsz,
                "lr0": cfg.lr0,
                "warm_end": cfg.warm_end,
                "peak_start": cfg.peak_start,
                "refine_start": cfg.refine_start,
                "refine_floor": cfg.refine_floor,
                "mining_enabled": cfg.mining_enabled,
                "drone_aug_enabled": cfg.drone_aug_enabled,
                "freeze_backbone": cfg.freeze_backbone,
                "drone_affine_shear_y": cfg.drone_affine_shear_y,
                "drone_perspective_limit": cfg.drone_perspective_limit,
            },
            reinit=True,
        )

    # Build overrides
    overrides = {
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
        "name": name,
        "close_mosaic": 0,
    }
    for key, value in cfg.warm_aug.items():
        overrides[key] = value

    trainer = LPRDetectionTrainer(lpr_config=cfg, overrides=overrides)

    # Wire callbacks (same as train.py)
    _scheduler_holder: list = []
    _miner_holder: list = []

    def _on_train_start(trainer_arg):  # type: ignore[no-untyped-def]
        from training.yolo.curriculum import CurriculumScheduler
        from training.yolo.mining import HardNegativeMiner

        _scheduler_holder.append(CurriculumScheduler(cfg, trainer_arg))
        if cfg.mining_enabled:
            _miner_holder.append(HardNegativeMiner(cfg, trainer_arg))

    def _on_train_epoch_start(trainer_arg):  # type: ignore[no-untyped-def]
        if _scheduler_holder:
            _scheduler_holder[0].on_epoch_start(trainer_arg)

    def _on_train_epoch_end(trainer_arg):  # type: ignore[no-untyped-def]
        if _miner_holder:
            _miner_holder[0].on_epoch_end(trainer_arg)

    def _on_fit_epoch_end(trainer_arg):  # type: ignore[no-untyped-def]
        if cfg.wandb_enabled and wandb is not None and wandb.run is not None:
            metrics = getattr(trainer_arg, "metrics", None)
            if metrics:
                wandb.log(metrics)

    trainer.add_callback("on_train_start", _on_train_start)
    trainer.add_callback("on_train_epoch_start", _on_train_epoch_start)
    trainer.add_callback("on_train_epoch_end", _on_train_epoch_end)
    trainer.add_callback("on_fit_epoch_end", _on_fit_epoch_end)

    # Train
    try:
        trainer.train()
        logger.info("EXPERIMENT %s COMPLETE", name)
    except Exception:
        logger.exception("EXPERIMENT %s FAILED", name)
    finally:
        if cfg.wandb_enabled and wandb is not None and wandb.run is not None:
            wandb.finish()


def main() -> None:
    """Run all experiments sequentially."""
    logger.info("Starting %d experiments", len(EXPERIMENTS))
    for i, exp in enumerate(EXPERIMENTS, 1):
        logger.info("--- Experiment %d/%d: %s ---", i, len(EXPERIMENTS), exp["name"])
        run_experiment(exp)
    logger.info("ALL EXPERIMENTS COMPLETE")


if __name__ == "__main__":
    main()
