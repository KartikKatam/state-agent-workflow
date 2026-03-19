"""Runner for Experiment 2: Progressive wing loss (w=10→3 over training)."""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("training/regression/results/wing_progressive.log"),
    ],
)

from training.regression.config import RegressionConfig
from training.regression.train_progressive import train_progressive

cfg = RegressionConfig()
cfg.base_lr = 1e-4
cfg.encoder_lr_mult = 0.05
cfg.batch_size = 16
cfg.freeze_min_epochs = 15
cfg.freeze_max_epochs = 60
cfg.wing_w = 10.0  # starting value
cfg.wing_epsilon = 2.0  # starting value (scales with w)
cfg.attention_temperature = 1.0
cfg.total_epochs = 100
cfg.wandb_enabled = True
cfg.wandb_project = "corner-regression"
cfg.checkpoint_dir = "training/regression/results/wing_progressive"
cfg.checkpoint_interval = 10
cfg.vis_interval = 10

best_path = train_progressive(cfg)
print(f"\nBest checkpoint: {best_path}")
