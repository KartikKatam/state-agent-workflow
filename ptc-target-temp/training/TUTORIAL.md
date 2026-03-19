# Corner CNN Training — Practical Guide

## File Organization

```
training/
  config.py          — TrainingConfig dataclass + AugmentationPhase enum
  model.py           — CornerHeatmapNet (ResNet18 encoder + transposed conv decoder + heatmap/offset heads)
  losses.py          — Focal heatmap loss + smooth L1 offset loss -> combined_loss()
  dataset.py         — PlateCornerDataset (loads crops + annotations, generates heatmap/offset targets)
  augmentations.py   — 3-phase curriculum augmentation with keypoint tracking
  evaluate.py        — validate(), extract_corners_from_heatmap(), CPE/PCK metrics, visualization helpers
  train.py           — build_optimizer, build_scheduler, freeze/unfreeze, HEM, checkpoints,
                       train_one_epoch, W&B integration, train() main loop

tests/
  conftest_training.py         — Test factories (make_training_config, make_synthetic_dataset, etc.)
  test_training_config.py      — Config validation tests
  test_training_model.py       — Model architecture tests
  test_training_losses.py      — Loss function tests
  test_training_dataset.py     — Dataset loading tests
  test_training_augmentations.py — Augmentation pipeline tests
  test_training_evaluate.py    — Evaluation metric tests
  test_training_train.py       — Training infrastructure tests (optimizer, scheduler, freeze, checkpoint)
  test_training_loop.py        — Integration tests (full train loop, resume, pause, W&B)
```

## Prerequisites

### Data Directory Structure

```
data/
  corner_crops/          — Cropped plate images (PNG/JPG, any size — letterboxed to 256x80)
    img_0001.png
    img_0002.png
    ...
  annotations.json       — Corner coordinates per image
  split.json             — Train/val split
```

### annotations.json format

Maps filename to 4 corners `[[x1,y1], [x2,y2], [x3,y3], [x4,y4]]` in order TL, TR, BR, BL:

```json
{
  "img_0001.png": [[12.5, 8.0], [185.3, 7.2], [186.1, 42.8], [11.9, 43.5]],
  "img_0002.png": [[25.0, 10.0], [210.0, 9.5], [211.0, 55.0], [24.5, 55.5]]
}
```

Coordinates are in the **original crop pixel space** (before letterboxing).

### split.json format

```json
{
  "train": ["img_0001.png", "img_0002.png", "img_0003.png"],
  "val": ["img_0004.png", "img_0005.png"]
}
```

### Optional: lighting_labels.json

For night simulation gating (skips night-sim augmentation on already-dark images):

```json
{
  "img_0001.png": "day",
  "img_0004.png": "night"
}
```

Set `lighting_labels_path` in config. If empty string (default), night sim applies to all images.

### W&B (optional)

```bash
pip install wandb
wandb login
```

Set `wandb_enabled=True` and `wandb_project="your-project"` in config. Training works fine without W&B installed — it falls back silently.

## How to Start Training

### Minimal Example

```python
from training.config import TrainingConfig
from training.train import train

cfg = TrainingConfig(
    data_dir="data/corner_crops",
    annotations_path="data/annotations.json",
    split_path="data/split.json",
    device="cuda",
    total_epochs=300,
    batch_size=16,
)

best_checkpoint = train(cfg)
print(f"Best checkpoint: {best_checkpoint}")
```

### As a Script

```python
# train_corners.py
from pathlib import Path
from training.config import TrainingConfig
from training.train import train

if __name__ == "__main__":
    cfg = TrainingConfig(
        data_dir="data/corner_crops",
        annotations_path="data/annotations.json",
        split_path="data/split.json",
        checkpoint_dir="checkpoints/run_01",
        wandb_project="corner-cnn-v1",
        wandb_enabled=True,
    )
    best = train(cfg)
    print(f"Done. Best checkpoint: {best}")
```

```bash
python train_corners.py
```

### Overriding Defaults

Every field in `TrainingConfig` has a default. Override only what you need:

```python
cfg = TrainingConfig(
    base_lr=2e-4,              # Higher LR
    lambda_offset=0.5,         # Less offset weight
    freeze_max_epochs=40,      # Unfreeze encoder earlier
    total_epochs=200,          # Shorter training
    checkpoint_interval=20,    # Less frequent checkpoints
)
```

## Checkpoint System

All checkpoints are saved to `checkpoint_dir` (default: `checkpoints/`).

### What Gets Saved

Each checkpoint `.pt` file contains:

| Key | Value |
|-----|-------|
| `model_state_dict` | Full model weights |
| `optimizer_state_dict` | AdamW state (moments, step counts) |
| `scheduler_state_dict` | LR scheduler state |
| `epoch` | Epoch number when saved |
| `metrics` | Validation metrics + loss history |
| `config` | Full TrainingConfig as dict |
| `encoder_frozen` | Whether encoder was frozen at save time |
| `aug_phase` | Augmentation phase (0=NONE, 1=MODERATE, 2=FULL_DRONE) |

### Three Checkpoint Types

**`best.pt`** — The best model by validation PCK@4px. Updated whenever val PCK@4px exceeds the previous best. This is your deployment checkpoint.

**`checkpoint_epochN.pt`** — Periodic snapshot every `checkpoint_interval` epochs. For inspecting training progress or rolling back.

**`pre_unfreeze_epochN.pt` / `pre_phaseN_epochN.pt`** — Saved automatically before major state changes (encoder unfreeze, augmentation phase advance). If the transition causes instability, load this to roll back.

## How the Best Checkpoint is Picked

The best checkpoint tracks **validation PCK@4px** — the percentage of predicted corners within 4 pixels of ground truth on the **held-out validation set**.

This is deliberately NOT training loss. Training loss measures how well the model fits the training data; PCK@4px on validation measures how well it generalizes to unseen images.

**What to watch for:**
- Val PCK@4px climbing = model is learning to generalize. `best.pt` updates.
- Val PCK@4px plateaus but training loss keeps dropping = overfitting. `best.pt` stops updating. This is normal — the checkpoint system protects you from deploying an overfit model.
- Val PCK@4px drops after unfreeze = temporary instability. Wait 5-10 epochs. If it doesn't recover, load `pre_unfreeze_epochN.pt`.

## Resuming Training

Pass `resume_path` to continue from any checkpoint:

```python
from pathlib import Path
from training.config import TrainingConfig
from training.train import train

cfg = TrainingConfig(
    data_dir="data/corner_crops",
    annotations_path="data/annotations.json",
    split_path="data/split.json",
    checkpoint_dir="checkpoints/run_01",
    total_epochs=400,  # Extend training
)

# Resume from epoch 200 checkpoint
best = train(cfg, resume_path=Path("checkpoints/run_01/checkpoint_epoch200.pt"))
```

Resume restores: model weights, optimizer state (momentum), scheduler state (LR position), epoch counter, encoder frozen/unfrozen state, augmentation phase. Training continues from `epoch + 1`.

## Interactive Control

While training runs, you can modify behavior without stopping by writing to the config override file (`training_config.json` by default, set via `config_override_path`).

The file is polled every epoch. Write valid JSON with any `TrainingConfig` field name:

### Pause Training

```bash
echo '{"pause": true}' > training_config.json
```

A checkpoint is saved automatically. Training blocks until you unpause:

```bash
echo '{"pause": false}' > training_config.json
```

### Change Hyperparameters Mid-Training

```bash
# Reduce learning rate
echo '{"base_lr": 5e-5}' > training_config.json

# Change loss balance
echo '{"lambda_offset": 2.0}' > training_config.json

# Multiple changes at once
echo '{"base_lr": 5e-5, "lambda_offset": 0.5, "batch_size": 8}' > training_config.json
```

Only recognized `TrainingConfig` field names are applied. Unknown keys are silently ignored. Malformed JSON is ignored.

## W&B Visualization

### What Gets Logged

**Every epoch (scalars):**
- `train/loss`, `train/hm_loss`, `train/off_loss`
- `val/mean_cpe`, `val/pck_2`, `val/pck_4`, `val/pck_8`, `val/mean_confidence`
- `lr`, `encoder_frozen`, `aug_phase`

**Every `vis_interval` epochs (images):**
- Corner overlay grid — validation images with predicted (green) and GT (red) corners, confidence labels
- Heatmap renders — JET colormap overlay showing where the model "looks"

### Viewing

```bash
wandb login
# Training logs appear at https://wandb.ai/<your-entity>/corner-cnn-training
```

If W&B is not installed or `wandb_enabled=False`, training proceeds normally with no errors — all W&B calls become no-ops.

## Training Phases Explained

Training uses a 3-phase curriculum that automatically advances:

### Phase 1: Frozen Encoder (epochs 0 to ~20-60)

- **What**: ResNet18 encoder weights are frozen. Only decoder + heads train.
- **Why**: Prevents destroying pretrained features before the heads learn to produce meaningful heatmaps.
- **Augmentation**: NONE — clean images only, so the heads learn the basic mapping.
- **Watch for**: Training loss should drop steadily. Val PCK should reach ~0.3-0.5.

**Unfreeze trigger**: Either `freeze_max_epochs` reached (forced at epoch 60 by default), or validation loss stagnates for `stagnation_window` epochs (15) after `freeze_min_epochs` (20).

### Phase 2: Moderate Augmentation (after unfreeze)

- **What**: Encoder unfrozen with reduced LR (`base_lr * encoder_lr_mult = 1e-5`). HEM sampler activates.
- **Augmentation**: MODERATE — horizontal flip, mild rotation (5 deg), mild scale (0.9-1.1x), basic photometric (brightness, contrast, noise).
- **Watch for**: Temporary PCK dip after unfreeze is normal. Should recover within 5-10 epochs. HEM focuses training on hard examples.
- **A pre-unfreeze checkpoint is saved automatically** so you can roll back if needed.

### Phase 3: Full Drone Augmentation

- **Trigger**: Val PCK@4px stays above `phase3_pck_threshold` (0.75) for `phase3_sustain_epochs` (5) consecutive epochs.
- **Augmentation**: FULL_DRONE — adds perspective warp, motion blur, JPEG compression, occlusion, heavy noise, night simulation, stronger rotation (15 deg) and scale (0.8-1.2x).
- **Watch for**: Another temporary PCK dip is normal. This phase hardens the model for real-world drone footage.
- **A pre-phase checkpoint is saved automatically**.

## After Training

### Loading for Inference

```python
import torch
from training.config import TrainingConfig
from training.model import CornerHeatmapNet

cfg = TrainingConfig()  # Must match training config geometry
model = CornerHeatmapNet(cfg)

ckpt = torch.load("checkpoints/run_01/best.pt", map_location="cpu", weights_only=False)
model.load_state_dict(ckpt["model_state_dict"])
model.eval()

# Now use model(input_tensor) for inference
# Input: (1, 3, 80, 256) float32 tensor
# Output: (1, 12, 20, 64) — 4 heatmaps + 8 offset channels
```

### Export to TensorRT

The trained model is a standard PyTorch module. Export via the existing producer pipeline's TensorRT export path, or via ONNX:

```python
dummy = torch.randn(1, 3, 80, 256)
torch.onnx.export(model, dummy, "corner_cnn.onnx", opset_version=17)
# Then use trtexec to convert ONNX -> TensorRT engine
```

### Inspecting a Checkpoint

```python
ckpt = torch.load("checkpoints/run_01/best.pt", map_location="cpu", weights_only=False)
print(f"Epoch: {ckpt['epoch']}")
print(f"Val PCK@4: {ckpt['metrics'].get('pck_4', 'N/A')}")
print(f"Encoder frozen: {ckpt['encoder_frozen']}")
print(f"Aug phase: {ckpt['aug_phase']}")
```
