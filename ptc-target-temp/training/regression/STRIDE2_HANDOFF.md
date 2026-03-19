# Handoff: Stride-2 Regression Model Variant

**From**: regression-coder (baseline implementation agent)
**To**: stride-2 implementation agent
**Date**: 2026-02-20
**Status**: Ready for implementation

---

## Objective

Create a **stride-2 variant** of the soft-argmax regression model and run a 100-epoch training experiment with identical parameters to the stride-4 baseline. The goal is to measure whether doubling the spatial resolution of the attention map improves sub-pixel corner localization.

**Baseline result to beat**: PCK@4 = 0.890 at epoch 98 (stride-4, 100 epochs)

---

## CRITICAL RULE

**Do NOT modify any existing files.** Create all new files in `training/regression/stride2/`. The stride-4 code in `training/regression/` must remain untouched.

---

## What To Build

A copy of the stride-4 regression model with a 3-stage decoder that outputs at stride-2 resolution (40×128 instead of 20×64).

### Files to create:

```
training/regression/stride2/
├── __init__.py          # package marker
├── model.py             # CornerRegressionNetS2 — 3-stage decoder + stride-2 attention
└── (nothing else — reuse config, losses, dataset, evaluate, train from parent)
```

You only need to create a new **model.py**. Everything else can be imported from `training/regression/`:
- `training.regression.config.RegressionConfig` — add `stride=2` at runtime
- `training.regression.losses` — unchanged (operates on coordinates, not spatial maps)
- `training.regression.dataset` — unchanged (returns coordinate targets, not heatmaps)
- `training.regression.evaluate` — unchanged (operates on coordinate outputs)
- `training.regression.train` — needs a small wrapper to import the stride-2 model instead

### Architecture change (stride-4 → stride-2):

Read `training/regression/model.py` (the stride-4 version) as your starting point. Then read `training/model.py` (the heatmap model) which already has stride-2 support — look at the `self._stride2` conditional blocks.

**Stride-4 (current)**:
```
Encoder: stem(conv+bn+relu+maxpool) → layer1(64ch, 20×64) → layer2(128ch, 10×32) → layer3(256ch, 5×16)
Decoder: up1(256→128, 10×32) + skip_layer2 → up2(128→64, 20×64) + skip_layer1
Attention: Conv2d(64, 4, 1×1) → softmax over 1280 positions → expected coordinates
Grid: 20×64, stride=4, x_coords=[0,4,8,...,252], y_coords=[0,4,8,...,76]
```

**Stride-2 (what you're building)**:
```
Encoder: stem_pre(conv+bn+relu) → stem_post(maxpool) → layer1(64ch, 20×64) → layer2(128ch, 10×32) → layer3(256ch, 5×16)
Decoder: up1(256→128, 10×32) + skip_layer2 → up2(128→64, 20×64) + skip_layer1 → up3(64→32, 40×128) + skip_stem_pre
Attention: Conv2d(32, 4, 1×1) → softmax over 5120 positions → expected coordinates
Grid: 40×128, stride=2, x_coords=[0,2,4,...,254], y_coords=[0,2,4,...,78]
```

Key differences:
1. **Split the stem** into `stem_pre` (conv1+bn1+relu, outputs 64ch at 40×128) and `stem_post` (maxpool)
2. **Add decoder stage 3**: `up3 = ConvTranspose2d(64, 32, kernel_size=4, stride=2, padding=1)` and `fuse3` that concatenates up3 output (32ch) with stem_pre skip (64ch) = 96ch input → `Conv2d(96, 32, 3, padding=1) + BN + ReLU`
3. **Attention head input changes**: `Conv2d(32, 4, 1×1)` instead of `Conv2d(64, 4, 1×1)`
4. **Coordinate grids are 40×128** with stride=2: `x_coords=[0,2,4,...,254]`, `y_coords=[0,2,4,...,78]`
5. **Softmax is over 5120 positions** (40×128) instead of 1280 (20×64)
6. **`encoder_params()`** must yield `stem_pre` and `stem_post` instead of `stem`
7. **`decoder_params()`** must also yield `up3` and `fuse3`

### Reference files to read:

| File | What to look at |
|------|-----------------|
| `training/regression/model.py` | **PRIMARY** — copy this, add stride-2 path |
| `training/model.py` lines 37-84 | `stem_pre`/`stem_post` split, `up3`/`fuse3` definitions, stride-2 forward path |
| `training/model.py` lines 144-184 | Forward pass with stride-2 conditionals |
| `training/model.py` lines 186-210 | `encoder_params()` / `decoder_params()` with stride-2 |
| `training/regression/config.py` | RegressionConfig — has `stride: int = 4` field |

### Weight initialization for new layers:

Copy the same pattern from `_init_weights()` in `training/regression/model.py`:
- `up3`: Kaiming normal weights, zero bias
- `fuse3`: Kaiming normal for Conv2d, ones/zeros for BatchNorm2d
- Attention head: Kaiming normal weights, **zero bias** (same as stride-4)

---

## Training Run

After building the model, run this exact experiment:

```python
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')

from training.regression.config import RegressionConfig
from training.regression.stride2.model import CornerRegressionNetS2
from training.regression.train import train

# Monkey-patch the model import in train.py
import training.regression.train as train_module

# Save original
_original_train = train_module.train

def train_stride2(cfg, resume_path=None):
    """Wrapper that uses stride-2 model."""
    if cfg.total_epochs <= 0:
        raise ValueError(f"total_epochs must be > 0, got {cfg.total_epochs}")

    from training.regression.dataset import PlateCornerRegressionDataset
    from training.regression.evaluate import validate
    from training.regression.stride2.model import CornerRegressionNetS2

    # Override the model class
    import training.regression.train as tm
    original_get_device = tm.get_device
    device = original_get_device(cfg)
    model = CornerRegressionNetS2(cfg).to(device)

    # ... this approach is fragile. Better approach below.

# BETTER APPROACH: Just write a thin train_stride2.py script. See below.
```

**Actually, the cleanest approach**: write a `training/regression/stride2/run.py` that copies the `train()` function from `training/regression/train.py` but imports `CornerRegressionNetS2` instead of `CornerRegressionNet`. Only the model instantiation line changes. Everything else (optimizer, scheduler, freeze/unfreeze, HEM, checkpointing, W&B) is identical.

### Exact parameters (must match baseline):

```python
cfg = RegressionConfig()
cfg.stride = 2                    # THE ONLY CHANGE
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
cfg.checkpoint_dir = 'training/regression/results/stride2_100ep'
cfg.checkpoint_interval = 10
cfg.vis_interval = 10
```

### Smoke test before training:

```bash
.venv/bin/python -c "
from training.regression.config import RegressionConfig
from training.regression.stride2.model import CornerRegressionNetS2
import torch

cfg = RegressionConfig()
cfg.stride = 2
model = CornerRegressionNetS2(cfg)
x = torch.randn(2, 3, 80, 256)
out = model(x)
print(f'Output shape: {out.shape}')  # must be (2, 8)

coords, attn = model.forward_with_attention(x)
print(f'Attention shape: {attn.shape}')  # must be (2, 4, 40, 128)
print(f'Attention sum: {attn[0].sum(dim=[1,2])}')  # must be [1, 1, 1, 1]
print(f'Params: {sum(p.numel() for p in model.parameters())}')
"
```

### Expected differences from stride-4:

- **Attention map**: 40×128 (5120 cells) vs 20×64 (1280 cells) — 4× more spatial positions
- **Parameter count**: ~3.85M vs ~3.81M (slightly more from up3+fuse3, slightly less from 32→4 head vs 64→4)
- **Training speed**: ~1.5-2× slower per epoch (4× larger attention maps, extra decoder stage)
- **Expected PCK@4**: +0.01-0.03 over stride-4 baseline (the model already interpolates well at stride-4)

### After training completes:

Extract results from the best checkpoint and send them to the team lead:

```python
import torch
ckpt = torch.load('training/regression/results/stride2_100ep/best.pt', map_location='cpu', weights_only=False)
print(f'Best epoch: {ckpt["epoch"]}')
metrics = ckpt['metrics']
for k, v in sorted(metrics.items()):
    if k not in ('val_loss_history', 'pck4_history'):
        print(f'{k}: {v}')
```

Report: best epoch, PCK@2/4/8, mean CPE, PCK@4/PCK@8 ratio, attention entropy, and comparison to stride-4 baseline (0.890 PCK@4, 2.28px CPE).
