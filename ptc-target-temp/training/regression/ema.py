"""Exponential Moving Average for model weights."""

from __future__ import annotations

import copy

import torch
import torch.nn as nn


class ModelEMA:
    """Maintains an exponential moving average of model parameters.

    Usage:
        model = CornerRegressionNet(cfg)
        ema = ModelEMA(model, decay=0.999)

        # In training loop:
        loss.backward()
        optimizer.step()
        ema.update(model)

        # For evaluation:
        val_metrics = validate(ema.module, val_loader)
    """

    def __init__(self, model: nn.Module, decay: float = 0.999) -> None:
        self.module = copy.deepcopy(model)
        self.module.eval()
        self.module.requires_grad_(False)
        self.decay = decay

    @torch.no_grad()
    def update(self, model: nn.Module) -> None:
        for ema_p, model_p in zip(self.module.parameters(), model.parameters()):
            ema_p.data.mul_(self.decay).add_(model_p.data, alpha=1.0 - self.decay)
        # Also update buffers (e.g., BatchNorm running stats)
        for ema_b, model_b in zip(self.module.buffers(), model.buffers()):
            ema_b.data.copy_(model_b.data)
