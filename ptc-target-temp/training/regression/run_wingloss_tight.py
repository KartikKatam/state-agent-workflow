"""Runner for Experiment 1: Fixed tight wing loss (w=5, epsilon=1)."""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("training/regression/results/wing_w5_eps1.log"),
    ],
)

from training.regression.config import RegressionConfig
from training.regression.train import train

cfg = RegressionConfig()
cfg.base_lr = 1e-4
cfg.encoder_lr_mult = 0.05
cfg.batch_size = 16
cfg.freeze_min_epochs = 15
cfg.freeze_max_epochs = 60
cfg.wing_w = 5.0  # CHANGED from 10.0
cfg.wing_epsilon = 1.0  # CHANGED from 2.0
cfg.attention_temperature = 1.0
cfg.total_epochs = 100
cfg.wandb_enabled = True
cfg.wandb_project = "corner-regression"
cfg.checkpoint_dir = "training/regression/results/wing_w5_eps1"
cfg.checkpoint_interval = 10
cfg.vis_interval = 10

best_path = train(cfg)
print(f"\nBest checkpoint: {best_path}")
