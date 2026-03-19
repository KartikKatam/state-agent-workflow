# Production Training Runs — Agent Prompt

**Date:** 2026-02-21
**Goal:** Build combined training script, validate everything with tests, then run 2 production training runs.

---

## Overview

Build a production training pipeline that combines ALL proven improvements into one training script, validate it thoroughly with tests, then launch 2 parallel 300-epoch runs (stride-4 and stride-2).

**Working directory:** `/home/kartik/work/firefly/LPR-SingleDrone/`
**Python:** `.venv/bin/python`

**CRITICAL RULE:** Do NOT modify any existing files in `training/regression/` or `training/`. Create all new files in `training/regression/production/`.

---

## Step 1: Read Context Files

Read ALL of these before writing any code:

### Architecture components to combine
| File | What it provides |
|------|-----------------|
| `training/regression/model.py` | `CornerRegressionNet` — stride-4 soft-argmax model |
| `training/regression/stride2/model.py` | `CornerRegressionNetS2` — stride-2 variant |
| `training/regression/variance/model.py` | `CornerRegressionNetWithVariance` — adds variance head |
| `training/regression/variance/losses.py` | `gaussian_nll_loss` — Gaussian NLL loss function |
| `training/regression/ema.py` | `ModelEMA` — exponential moving average |
| `training/regression/config.py` | `RegressionConfig` — all config fields |
| `training/regression/losses.py` | `wing_loss`, `regression_loss` — base loss functions |

### Training loop reference
| File | What it provides |
|------|-----------------|
| `training/regression/train.py` | Base training loop (~250 lines) — copy and extend |
| `training/regression/train_ema.py` | EMA integration pattern |
| `training/regression/variance/train.py` | Variance head training pattern |

### Data pipeline
| File | What it provides |
|------|-----------------|
| `training/regression/dataset.py` | `PlateCornerRegressionDataset` — image loading, coord targets |
| `training/dataset.py` | `PlateCornerDataset` — has `build_synthetic_sampler()`, synthetic data loading |
| `training/augmentations.py` | Full augmentation pipeline with 7 new augs + ramp_factor |
| `training/config.py` | `TrainingConfig` — has synthetic data config fields |

### Experiment results (understand what works)
| File | What it provides |
|------|-----------------|
| `models/corner-cnn/experiments/results/SWEEP-ANALYSIS.md` | All experiment results and conclusions |

---

## Step 2: Build the Combined Training Script

Create these files in `training/regression/production/`:

### 2a. `__init__.py`
Package marker.

### 2b. `model.py` — Combined model with variance head + configurable stride

Merge `CornerRegressionNet` + variance head into one model class `ProductionCornerNet`:
- Takes `cfg.stride` (2 or 4) to determine architecture
- Stride-4: 2-stage decoder, 20x64 attention maps (1280 positions), 64ch heads
- Stride-2: 3-stage decoder with stem_pre/stem_post split, 40x128 attention maps (5120 positions), 32ch heads
- Variance output: `forward()` returns `(coords_8, log_sigma_8)` tuple
- `forward_with_attention()` returns `(coords_8, log_sigma_8, attention_maps)`
- `encoder_params()`, `decoder_params()`, `head_params()` — stride-aware param groups
- Coordinate grid buffers registered for the appropriate stride

### 2c. `losses.py` — Combined loss

Gaussian NLL loss that uses wing loss for the coordinate term:
```python
def production_loss(pred_coords, pred_log_sigma, target_coords, img_w, img_h, wing_w=10.0, wing_eps=2.0):
    """Gaussian NLL with wing-loss-shaped coordinate penalty."""
    # Normalize coordinates independently: x/img_w, y/img_h
    # Wing loss on normalized coordinate errors
    # Gaussian NLL: log(sigma^2) + wing_error / sigma^2
    # Return mean over batch
```

### 2d. `train.py` — Combined training loop

Copy from `training/regression/train.py` and integrate:
1. **Model**: Import `ProductionCornerNet` instead of `CornerRegressionNet`
2. **EMA**: Initialize `ModelEMA(model, decay=cfg.ema_decay)` after model creation. Update EMA after each optimizer step. Validate with EMA model. Save EMA state dict in checkpoint.
3. **Loss**: Use `production_loss` (Gaussian NLL + wing) instead of `regression_loss`
4. **Synthetic data**: Wire `build_synthetic_sampler()` when `cfg.synthetic_data_dir` is set. Use `num_samples=len(real_images)` to keep epoch size consistent.
5. **Checkpoint**: Save `model_state_dict`, `ema_state_dict`, `optimizer`, `scheduler`, `epoch`, `metrics`, `config`, `model_type: "production"`
6. **W&B logging**: Log sigma stats (mean, median, min, max) alongside existing metrics. Log `ema/pck4`, `ema/mean_cpe` separately.

### 2e. `config.py` — Production config

Extend `RegressionConfig` with:
```python
# EMA
ema_enabled: bool = True
ema_decay: float = 0.9999

# Synthetic data (copy from TrainingConfig)
synthetic_data_dir: str = "data/synthetic_crops"
synthetic_annotations_path: str = "data/synthetic_annotations.json"
synthetic_ratio: float = 0.5

# All augmentation fields from TrainingConfig that the regression dataset needs
# (the 7 new aug fields + the existing ones)
```

Ensure ALL augmentation fields from `TrainingConfig` are present (the `apply_augmentation` function reads them by attribute access).

### 2f. `run_stride4.py` — Stride-4 production runner
```python
cfg = ProductionConfig()
cfg.stride = 4
cfg.total_epochs = 300
cfg.ema_decay = 0.9999
cfg.wandb_enabled = True
cfg.wandb_project = 'corner-regression-production'
cfg.checkpoint_dir = 'training/regression/results/production_s4_300ep'
train(cfg)
```

### 2g. `run_stride2.py` — Stride-2 production runner
```python
# Same as stride-4 but:
cfg.stride = 2
cfg.checkpoint_dir = 'training/regression/results/production_s2_300ep'
```

---

## Step 3: Write Tests BEFORE Running Training

Create `tests/test_production_training.py` with these test groups:

### Group 1: Model tests (both strides)
- `test_stride4_output_shape` — input (2,3,80,256) → coords (2,8) + log_sigma (2,8)
- `test_stride2_output_shape` — same input → same coord shape, attention (2,4,40,128)
- `test_stride4_attention_shape` — forward_with_attention → (2,4,20,64)
- `test_stride2_attention_shape` — forward_with_attention → (2,4,40,128)
- `test_attention_sums_to_one` — per-corner attention sums to 1.0 (both strides)
- `test_sigma_is_positive` — exp(log_sigma) > 0 always
- `test_param_groups_cover_all` — encoder + decoder + head params == total params (both strides)
- `test_encoder_params_frozen` — after freeze, encoder grads are disabled
- `test_encoder_params_unfrozen` — after unfreeze, encoder grads are enabled

### Group 2: Loss tests
- `test_gaussian_nll_finite` — loss is finite for random inputs
- `test_gaussian_nll_gradient_flows` — loss.backward() works, model params have gradients
- `test_low_error_low_sigma_beats_high_sigma` — loss is lower when sigma matches actual error
- `test_wing_component_shape` — wing loss operates correctly on normalized coordinates

### Group 3: EMA tests
- `test_ema_initialization` — EMA model has same weights as source initially
- `test_ema_update_changes_weights` — after update, EMA weights differ from source
- `test_ema_decay_rate` — higher decay = slower weight change
- `test_ema_state_dict_roundtrip` — save/load EMA preserves weights

### Group 4: Data pipeline tests
- `test_synthetic_ratio_approximately_correct` — sample 10 epochs worth of batches, verify ~50% synthetic
- `test_no_exact_repeat_within_epoch` — with replacement=True, statistical test that indices aren't identical across consecutive epochs (draw 2 epochs, verify different ordering)
- `test_real_images_are_train_only` — no val images appear in training
- `test_synthetic_images_are_train_only` — synthetic annotations don't include val images
- `test_epoch_size_matches_real_count` — `num_samples == len(real_images)` ≈ 2228
- `test_dataset_returns_correct_tuple` — (img_tensor, coord_target, idx) with correct shapes

### Group 5: Augmentation integration tests
- `test_full_drone_augs_all_active` — in FULL_DRONE phase with ramp=1.0, verify all 7 new augs can fire (run 100 iterations, check each aug modifies at least 1 image)
- `test_ramp_factor_zero_disables_augs` — with ramp=0.0, image should be unchanged
- `test_augmentation_preserves_corners_in_bounds` — after augmentation, all 4 corners are within image bounds
- `test_composed_augmentations_dont_crash` — run 50 random augmentation compositions, no exceptions
- `test_night_sim_with_new_augs` — night sim + new augs don't conflict

### Group 6: End-to-end smoke test
- `test_one_epoch_stride4_no_crash` — run 1 epoch of training (tiny dataset, wandb off), verify loss is finite
- `test_one_epoch_stride2_no_crash` — same for stride-2
- `test_checkpoint_save_load_roundtrip` — save checkpoint with EMA, reload, verify weights match
- `test_ema_validation_runs` — validate with EMA model, get metrics dict back

### Group 7: Sampler verification
- `test_weighted_sampler_num_samples` — sampler length == len(real_images)
- `test_weighted_sampler_ratio` — draw full epoch, count real vs synthetic, verify within 10% of target ratio
- `test_all_synthetic_images_seen_over_50_epochs` — draw 50 epochs, verify >95% of synthetic pool was sampled at least once

---

## Step 4: Run Tests

```bash
# Run all production tests
.venv/bin/pytest tests/test_production_training.py -v

# Run quality gate
ruff format training/regression/production/ tests/test_production_training.py
ruff check training/regression/production/ tests/test_production_training.py --fix

# Verify no regressions
.venv/bin/pytest tests/test_synthetic_data.py tests/test_training_augmentations.py -v
```

ALL tests must pass before launching training runs.

---

## Step 5: Generate Synthetic Data (if not already done)

Check if `data/synthetic_crops/` exists and has images. If not:

```bash
.venv/bin/python tools/synthetic_data/generate_composites.py \
    --num-images 12000 \
    --vehicle-rear-ratio 0.7 \
    --seed 42 \
    --workers 4

.venv/bin/python tools/synthetic_data/validate_synthetic.py
```

Verify validation passes (no leakage, all corners valid, format parity).

---

## Step 6: Launch Training Runs (in parallel)

After ALL tests pass and synthetic data is validated:

### Run A — Stride-4
```bash
.venv/bin/python -m training.regression.production.run_stride4
```

### Run B — Stride-2
```bash
.venv/bin/python -m training.regression.production.run_stride2
```

Both runs should:
- Log to W&B under project `corner-regression-production`
- Save checkpoints to their respective dirs every 10 epochs
- Save best model (by EMA PCK@4) and last model
- Print epoch summaries with PCK@4, PCK@2, mean CPE, mean sigma, EMA metrics

---

## Step 7: After Training Completes

Extract results from both runs and send comparison to team-lead:

| Metric | Stride-4 | Stride-2 |
|--------|----------|----------|
| PCK@2 | ? | ? |
| PCK@4 | ? | ? |
| PCK@8 | ? | ? |
| Mean CPE | ? | ? |
| Mean sigma | ? | ? |
| Sigma correlation with error | ? | ? |
| Rejection @ sigma<2px: coverage | ? | ? |
| Rejection @ sigma<2px: PCK@4 | ? | ? |
| Best epoch | ? | ? |
| EMA vs non-EMA PCK@4 delta | ? | ? |

Also update `models/corner-cnn/experiments/results/training_experiments.json` with both experiments.

---

## Configuration Summary

| Parameter | Value |
|-----------|-------|
| Encoder | ResNet18 (ImageNet pretrained) |
| Decoder | Stride-2 (Run B) / Stride-4 (Run A) |
| Head | Soft-argmax + variance (Gaussian NLL) |
| Temperature | 1.0 |
| Loss | Gaussian NLL (wing w=10, eps=2 for coord term) |
| Optimizer | AdamW, betas=(0.9, 0.999) |
| Base LR | 1e-4 |
| Encoder LR | 5e-6 (0.05x base) |
| Weight decay | 1e-4 |
| Warmup | 5 epochs from 1e-6 |
| Schedule | Cosine annealing (resets on unfreeze) |
| Batch size | 16 |
| EMA | decay=0.9999 |
| Synthetic ratio | 0.5 |
| Sampler | WeightedRandomSampler, num_samples=len(real) |
| Freeze | min=15, max=60 epochs |
| FULL_DRONE threshold | PCK@4 >= 0.30 |
| Aug ramp | 0→1 within FULL_DRONE |
| HEM | Enabled |
| Epochs | 300 |
| Checkpoint | best (EMA PCK@4) + last + every 10ep |
