# Handoff: Wing Loss Parameter Experiments

**From**: regression-coder
**To**: wing-loss sweep agent
**Date**: 2026-02-20
**Status**: Ready for implementation and training

---

## Objective

Run 2 training experiments in parallel to test whether tighter wing loss parameters improve sub-pixel precision. The baseline used paper defaults (w=10, eps=2) and achieved PCK@4=0.890 at 100 epochs. Our mean CPE is 2.28px — most errors are well below the w=10 threshold, so the amplified-gradient region may be too wide.

**Baseline to compare against**: PCK@4=0.890, Mean CPE=2.28px, PCK@4/PCK@8=0.902 (epoch 98, 100 epochs)

---

## CRITICAL RULES

1. **Do NOT modify any existing files.** New code goes in `training/regression/` only as new files.
2. The only files you may create are:
   - `training/regression/losses_progressive.py` — progressive wing loss implementation
   - `training/regression/train_progressive.py` — thin wrapper of train.py that uses progressive loss
   - Helper/runner scripts as needed
3. Experiment 1 (fixed w=5) needs NO code changes — just different config values passed to the existing `train()` function.

---

## Context Files To Read

| File | Why |
|------|-----|
| `training/regression/HANDOFF.md` | Full project context, design decisions, gotchas |
| `training/regression/losses.py` | Current wing_loss and regression_loss implementation — you'll base the progressive variant on this |
| `training/regression/config.py` | RegressionConfig dataclass — has wing_w and wing_epsilon fields |
| `training/regression/train.py` | Training loop — the progressive variant needs to modify loss computation per epoch |
| `training/regression/PLAN.md` Section 4.4 | Wing loss sweep rationale and parameter choices |

---

## Experiment 1: Fixed Tight Wing Loss (w=5, epsilon=1)

**Hypothesis**: Since mean CPE is 2.28px, concentrating the amplified-gradient region in 0-5px (instead of 0-10px) will give stronger gradient signal on the errors we actually have, improving PCK@4.

**No code changes needed.** Just run with different config:

```python
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')

from training.regression.config import RegressionConfig
from training.regression.train import train

cfg = RegressionConfig()
cfg.base_lr = 1e-4
cfg.encoder_lr_mult = 0.05
cfg.batch_size = 16
cfg.freeze_min_epochs = 15
cfg.freeze_max_epochs = 60
cfg.wing_w = 5.0          # CHANGED from 10.0
cfg.wing_epsilon = 1.0    # CHANGED from 2.0
cfg.attention_temperature = 1.0
cfg.total_epochs = 100
cfg.wandb_enabled = True
cfg.wandb_project = 'corner-regression'
cfg.checkpoint_dir = 'training/regression/results/wing_w5_eps1'
cfg.checkpoint_interval = 10
cfg.vis_interval = 10

best_path = train(cfg)
```

Log output to: `training/regression/results/wing_w5_eps1.log`

---

## Experiment 2: Progressive Wing Loss (w shrinks with training progress)

**Hypothesis**: Start with w=10 (wide gradient amplification for coarse learning) and linearly decay to w=3 (tight focus on sub-3px errors for fine-tuning). This mirrors the sigma curriculum from the heatmap approach — start easy, end precise.

**Requires new code.** Create `training/regression/losses_progressive.py`:

```python
def progressive_wing_w(epoch: int, total_epochs: int, w_start: float = 10.0, w_end: float = 3.0) -> float:
    """Linearly decay wing loss width from w_start to w_end over training."""
    progress = min(epoch / max(total_epochs - 1, 1), 1.0)
    return w_start + (w_end - w_start) * progress
```

Then create `training/regression/train_progressive.py` — a copy of `train()` from `training/regression/train.py` with one modification in `train_one_epoch`: instead of using `cfg.wing_w` directly, call `progressive_wing_w(epoch, cfg.total_epochs)` to get the current w value. The epsilon should scale proportionally: `eps = cfg.wing_epsilon * (current_w / cfg.wing_w)`.

**Important**: The progressive function must be called at the START of each epoch in the main training loop, and the current w value should be logged to W&B as `wing_w_current`.

Config for this run:
```python
cfg = RegressionConfig()
cfg.base_lr = 1e-4
cfg.encoder_lr_mult = 0.05
cfg.batch_size = 16
cfg.freeze_min_epochs = 15
cfg.freeze_max_epochs = 60
cfg.wing_w = 10.0              # starting value
cfg.wing_epsilon = 2.0         # starting value (scales with w)
cfg.attention_temperature = 1.0
cfg.total_epochs = 100
cfg.wandb_enabled = True
cfg.wandb_project = 'corner-regression'
cfg.checkpoint_dir = 'training/regression/results/wing_progressive'
cfg.checkpoint_interval = 10
cfg.vis_interval = 10
```

Log output to: `training/regression/results/wing_progressive.log`

---

## After Both Experiments Complete

Extract results from both best checkpoints and produce a comparison:

```python
import torch

for name, path in [
    ('Baseline (w=10, eps=2)', 'training/regression/results/baseline_100ep/best.pt'),
    ('Tight (w=5, eps=1)', 'training/regression/results/wing_w5_eps1/best.pt'),
    ('Progressive (w=10→3)', 'training/regression/results/wing_progressive/best.pt'),
]:
    ckpt = torch.load(path, map_location='cpu', weights_only=False)
    m = ckpt['metrics']
    pck4 = m['pck_4']
    pck8 = m['pck_8']
    print(f'{name}: PCK@4={pck4:.4f}, CPE={m["mean_cpe"]:.3f}px, PCK@4/8={pck4/max(pck8,.001):.3f}, epoch={ckpt["epoch"]}')
```

Send the comparison table to team-lead via SendMessage when done.

---

## Key Gotchas

1. **Wing loss math**: `C = w - w * ln(1 + w/epsilon)` must be recomputed whenever w or epsilon changes. The current `wing_loss()` function computes C inline, so this is automatic.
2. **Normalization**: `regression_loss()` normalizes w and epsilon by `input_w` (256). The progressive function should output w in pixel units (the normalization happens inside `regression_loss`).
3. **Run both experiments in parallel** — they're independent. Use background execution for at least one.
4. **Both runs are ~30 minutes** (100 epochs each on this hardware).
