# Handoff: EMA (Exponential Moving Average) Training Experiment

**From**: regression-coder
**To**: EMA implementation agent
**Date**: 2026-02-20
**Status**: Ready for implementation and training

---

## Objective

Implement Exponential Moving Average (EMA) of model weights and run a 100-epoch training experiment with identical parameters to the baseline. The goal is to measure the exact gain from EMA — expected +0.005-0.015 PCK@4.

**Baseline to compare against**: PCK@4=0.890, Mean CPE=2.28px, PCK@4/PCK@8=0.902 (epoch 98, 100 epochs)

---

## CRITICAL RULES

1. **Do NOT modify any existing files.** All new code goes in `training/regression/` as new files only.
2. Files to create:
   - `training/regression/ema.py` — EMA helper class
   - `training/regression/train_ema.py` — copy of train.py that integrates EMA

---

## Context Files To Read

| File | Why |
|------|-----|
| `training/regression/HANDOFF.md` | Full project context, architecture decisions |
| `training/regression/model.py` | CornerRegressionNet — the model being EMA'd |
| `training/regression/train.py` | **PRIMARY** — the training loop you're copying and modifying |
| `training/regression/evaluate.py` | validate() function — needs to run on EMA model |
| `training/regression/config.py` | RegressionConfig dataclass |
| `training/regression/losses.py` | Loss functions (unchanged, just for reference) |

---

## What EMA Does

Instead of evaluating with the current model weights (which bounce around due to SGD noise), maintain a smoothed copy that's the exponential moving average of all past weights:

```
ema_weights = decay * ema_weights + (1 - decay) * current_weights
```

After each training step, update the EMA model. Evaluate and checkpoint using the EMA model, not the training model. The training model keeps its regular noisy weights for gradient computation.

---

## Implementation: `training/regression/ema.py`

```python
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
```

**Key details:**
- `decay = 0.999` is standard. This means the EMA has an effective window of ~1000 gradient steps. With batch_size=16 and 2228 images, that's ~7 epochs. No sweep needed.
- The EMA model is always in `.eval()` mode — BatchNorm uses running stats, not batch stats.
- Buffers (BatchNorm running mean/var, coordinate grids) are copied directly, not averaged.
- `requires_grad_(False)` ensures no accidental gradient computation on the EMA copy.

---

## Implementation: `training/regression/train_ema.py`

Copy `training/regression/train.py` entirely, then make these specific changes:

### 1. Import the EMA class
```python
from training.regression.ema import ModelEMA
```

### 2. In `train()`, after model creation and before the epoch loop:
```python
ema = ModelEMA(model, decay=0.999)
```

### 3. In `train_one_epoch()`, after `optimizer.step()`:
The EMA update must happen INSIDE the batch loop, after each optimizer step. This means `train_one_epoch` needs access to the EMA object. Add it as a parameter:
```python
def train_one_epoch(model, loader, optimizer, cfg, device, per_image_losses=None, ema=None):
    ...
    optimizer.step()
    if ema is not None:
        ema.update(model)
    ...
```

### 4. In the epoch loop, validate with EMA model instead of training model:
```python
# CHANGE THIS:
val_metrics = validate(model, val_loader)
# TO THIS:
val_metrics = validate(ema.module, val_loader)
```

### 5. Log visualizations with EMA model:
```python
log_visualizations(run, epoch, ema.module, val_loader, cfg, device)
```

### 6. Save BOTH models in checkpoint:
```python
torch.save({
    ...
    "model_state_dict": model.state_dict(),          # training model
    "ema_state_dict": ema.module.state_dict(),        # EMA model
    ...
}, path)
```

### 7. In `load_checkpoint()`, restore EMA:
```python
if "ema_state_dict" in checkpoint and ema is not None:
    ema.module.load_state_dict(checkpoint["ema_state_dict"])
```

### 8. The `train()` function signature adds the `train_ema` name to distinguish:
The function should be called `train` (same name) but live in `train_ema.py`.

---

## Training Run

Run with **exactly the same parameters** as baseline:

```python
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')

from training.regression.config import RegressionConfig
from training.regression.train_ema import train

cfg = RegressionConfig()
cfg.base_lr = 1e-4
cfg.encoder_lr_mult = 0.05
cfg.batch_size = 16
cfg.freeze_min_epochs = 15
cfg.freeze_max_epochs = 60
cfg.wing_w = 10.0
cfg.wing_epsilon = 2.0
cfg.attention_temperature = 1.0
cfg.total_epochs = 100
cfg.wandb_enabled = True
cfg.wandb_project = 'corner-regression'
cfg.checkpoint_dir = 'training/regression/results/ema_100ep'
cfg.checkpoint_interval = 10
cfg.vis_interval = 10

best_path = train(cfg)
print(f'\nBest checkpoint: {best_path}')
```

Log output to: `training/regression/results/ema_100ep.log`

---

## After Training Completes

Extract results and compare to baseline:

```python
import torch

for name, path in [
    ('Baseline (no EMA)', 'training/regression/results/baseline_100ep/best.pt'),
    ('With EMA (decay=0.999)', 'training/regression/results/ema_100ep/best.pt'),
]:
    ckpt = torch.load(path, map_location='cpu', weights_only=False)
    m = ckpt['metrics']
    pck4 = m['pck_4']
    pck8 = m['pck_8']
    print(f'{name}: PCK@4={pck4:.4f}, CPE={m["mean_cpe"]:.3f}px, PCK@4/8={pck4/max(pck8,.001):.3f}, epoch={ckpt["epoch"]}')
```

Also compare the epoch-by-epoch PCK@4 trajectories — EMA should show smoother progression with fewer epoch-to-epoch fluctuations.

Send the comparison to team-lead via SendMessage when done.

---

## Key Gotchas

1. **EMA update must happen after EVERY optimizer.step()**, not once per epoch. Per-epoch EMA averaging is too coarse and won't help.
2. **Validate with `ema.module`, not `model`**. The whole point is that the averaged weights generalize better.
3. **Keep training with `model`, not `ema.module`**. Gradients are computed on the training model. EMA is read-only.
4. **Freeze/unfreeze affects the training model only.** The EMA model automatically reflects the frozen/unfrozen state through its parameter updates.
5. **When rebuilding optimizer after unfreeze**, the EMA object doesn't need to change — it just keeps averaging whatever the training model produces.
6. **Expected training time**: ~30-35 minutes (same as baseline, EMA adds negligible overhead — just parameter copies).
