"""Production training runner — stride-4 (300 epochs)."""

from training.regression.production.config import ProductionConfig
from training.regression.production.train import train

if __name__ == "__main__":
    cfg = ProductionConfig()
    cfg.stride = 4
    cfg.total_epochs = 300
    cfg.ema_decay = 0.9999
    cfg.wandb_enabled = True
    cfg.wandb_project = "corner-regression-production"
    cfg.checkpoint_dir = "training/regression/results/production_s4_300ep"
    train(cfg)
