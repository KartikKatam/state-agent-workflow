# Handoff: Soft-Argmax + Wing Loss Implementation

**From**: softargmax-impl (research/planning agent)
**To**: Next implementation agent
**Date**: 2026-02-20
**Status**: Plan approved, ready for implementation

---

## What's Done

1. Full codebase analysis of all 6 training files
2. Review of 14-experiment heatmap sweep results
3. Design decisions made with user approval
4. Implementation plan written and approved

## What Needs To Be Done

Implement all files in `training/regression/` per the plan. **No existing files should be modified.** Everything is a new, isolated copy.

---

## Files To Read Before Starting

### Plan and Design
- **`training/regression/PLAN.md`** — Full implementation plan with file specs, parameter decisions, experiment plan. THIS IS YOUR PRIMARY REFERENCE.
- **`models/corner-cnn/experiments/approaches/arch-regression.md`** — Research design doc with architecture rationale, tradeoff analysis, risk assessment.

### Source Files To Copy From (read-only, do not modify)
- **`training/model.py`** — Copy encoder + decoder verbatim into `training/regression/model.py`. Replace dual heads with attention head.
- **`training/losses.py`** — Reference for loss interface. Regression losses are entirely new (wing loss).
- **`training/dataset.py`** — Copy letterbox/augmentation logic. Remove heatmap/offset target generation. Return `(img, coord_target, idx)`.
- **`training/evaluate.py`** — Copy `compute_cpe`, `compute_pck`, `draw_corner_overlay`. Replace extraction functions.
- **`training/train.py`** — Copy training loop structure. Replace loss calls, target unpacking, W&B logging. Remove sigma scheduling.
- **`training/config.py`** — Copy shared fields. Remove heatmap-specific fields. Add `wing_w`, `wing_epsilon`, `attention_temperature`.

### Experiment Data (for parameter defaults)
- **`models/corner-cnn/experiments/results/training_experiments.json`** — All 14 experiments with detailed results.
- **`models/corner-cnn/experiments/results/SWEEP-ANALYSIS.md`** — Analysis proving PCK@4/PCK@8 ≈ 0.50 ceiling, optimal params.
- **`models/corner-cnn/experiments/sweep.py`** — Sweep runner pattern to mirror for `training/regression/sweep.py`.

### Shared Dependencies (import, don't copy)
- `training.augmentations.apply_augmentation` — Augmentation pipeline. BUT NOTE: it imports `training.config.AugmentationPhase` and `training.config.TrainingConfig`. The regression dataset must pass a config object compatible with the augmentation function's field reads. The plan says to copy `letterbox_crop` and `corners_to_letterbox` locally for full isolation. For augmentations, either import from `training.augmentations` directly (it only reads fields, doesn't typecheck), or copy the augmentation code too.
- `common.geometry.order_keypoints_by_angle` — Corner canonicalization. Import directly.

---

## Key Design Decisions (Already Made)

1. **No shared base class** — Copy encoder/decoder code into `CornerRegressionNet`. Don't refactor existing `CornerHeatmapNet`. Full isolation.
2. **Temperature is config-only** — Not learnable. Set before training, constant during run.
3. **Dataset returns 3 items** — `(img_tensor, coord_target, idx)`. New dataset class, not a flag on existing one.
4. **Confidence via `forward_with_attention()`** — `forward()` returns `(N, 8)` for training speed. `forward_with_attention()` returns `((N, 8), (N, 4, 20, 64))` for eval/viz.
5. **Independent x/y normalization** — x/256 and y/80 separately in loss. 1px error counts equally regardless of axis.
6. **isinstance not needed** — Entirely separate eval/train files. No dispatch logic.
7. **Augmentation compatibility** — The `apply_augmentation` function reads fields from the config object by attribute access. `RegressionConfig` must include all augmentation fields (copy them from `TrainingConfig`). The function also takes `AugmentationPhase` enum — import that from `training.config`.

---

## Implementation Order (from PLAN.md Section 7)

```
Step 1: config.py          (~30 lines)
Step 2: losses.py          (~50 lines)
Step 3: model.py           (~120 lines)
Step 4: dataset.py         (~100 lines)
Step 5: evaluate.py        (~120 lines)
Step 6: train.py           (~250 lines)
Step 7: sweep.py           (~120 lines)
Step 8: __init__.py        (~5 lines)
```

Total: ~800 lines of new code.

---

## Gotchas / Things To Watch For

1. **Augmentation imports**: `apply_augmentation` is in `training.augmentations` which imports `from training.config import AugmentationPhase, TrainingConfig`. Your `RegressionConfig` is NOT a `TrainingConfig` subclass. The augmentation function doesn't typecheck though — it just reads `cfg.aug_hflip_prob` etc by attribute. So it works as long as `RegressionConfig` has all the same augmentation fields. Make sure to copy ALL of them (there are ~30 augmentation fields in `TrainingConfig`).

2. **Coordinate grid buffers**: `x_grid` and `y_grid` must be registered as buffers (`self.register_buffer`), not parameters. They should NOT be in any optimizer param group. They auto-move to GPU with `model.to(device)`.

3. **Softmax dim**: The spatial softmax must be over the flattened spatial dimension (1280 = 20×64), NOT over the channel dimension (4). Flatten to `(N, 4, 1280)`, softmax over `dim=2`, reshape back to `(N, 4, 20, 64)`.

4. **Coordinate interleaving**: Model output is `(N, 8)` as `[x0, y0, x1, y1, x2, y2, x3, y3]`. The `x_coords` from the expected value computation gives `(N, 4)` x-values and `y_coords` gives `(N, 4)` y-values. Stack and interleave: `torch.stack([x_coords, y_coords], dim=2).reshape(N, 8)`.

5. **Wing loss continuity**: The constant `C = w - w * ln(1 + w/epsilon)` ensures the piecewise function is continuous at |x| = w. Double-check the math.

6. **HEM per-image loss**: In `train_one_epoch`, the per-image loss for HEM must call `regression_loss` on single samples `pred[i:i+1]`, `target[i:i+1]`. Same pattern as existing code but with different loss function.

7. **Checkpoint format**: Save `model_type: "regression"` in checkpoint dict so it's distinguishable from heatmap checkpoints. The config is also saved (which has `wing_w` etc), but an explicit marker is cleaner.

8. **No sigma anywhere**: Don't copy `set_sigma()`, `get_scheduled_sigma()`, or any sigma schedule logic. This is the biggest simplification vs the heatmap code.

---

## Smoke Test After Implementation

```bash
cd /home/kartik/work/firefly/LPR-SingleDrone
.venv/bin/python -c "
from training.regression.config import RegressionConfig
from training.regression.model import CornerRegressionNet
from training.regression.losses import wing_loss, regression_loss
import torch

cfg = RegressionConfig()
model = CornerRegressionNet(cfg)
x = torch.randn(2, 3, 80, 256)
out = model(x)
print(f'Output shape: {out.shape}')  # should be (2, 8)
print(f'Output range: [{out.min():.1f}, {out.max():.1f}]')  # should be within [0, 256] x [0, 80]

coords, attn = model.forward_with_attention(x)
print(f'Coords shape: {coords.shape}')  # (2, 8)
print(f'Attention shape: {attn.shape}')  # (2, 4, 20, 64)
print(f'Attention sum per corner: {attn[0].sum(dim=[1,2])}')  # should be [1, 1, 1, 1]

target = torch.rand(2, 8) * torch.tensor([256, 80, 256, 80, 256, 80, 256, 80])
loss = regression_loss(out, target, 256, 80, 10.0, 2.0)
print(f'Loss: {loss.item():.4f}')  # should be finite
loss.backward()
print('Backward pass OK')
"
```

Then run the sanity check experiment (Phase 0 from PLAN.md):
```bash
.venv/bin/python -c "
from training.regression.config import RegressionConfig
from training.regression.train import train
cfg = RegressionConfig()
cfg.total_epochs = 5
cfg.wandb_enabled = False
cfg.checkpoint_dir = 'training/regression/results/sanity'
train(cfg)
"
```
