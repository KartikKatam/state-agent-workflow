# Soft-Argmax + Wing Loss Regression Approach — Implementation Plan

**Date**: 2026-02-20
**Status**: Draft — awaiting user approval
**Design doc**: `models/corner-cnn/experiments/approaches/arch-regression.md`
**Baseline reference**: 14-experiment heatmap sweep (best PCK@4 = 0.361, structural ceiling PCK@4/PCK@8 ≈ 0.50)

---

## 1. Folder Structure

All code lives in `training/regression/`. Nothing in `training/*.py` is modified.

```
training/regression/
├── __init__.py              # package marker
├── PLAN.md                  # this file
├── config.py                # RegressionConfig dataclass
├── model.py                 # CornerRegressionNet (soft-argmax)
├── losses.py                # wing_loss, regression_loss
├── dataset.py               # PlateCornerRegressionDataset
├── evaluate.py              # corner extraction, CPE, PCK, validate
├── train.py                 # training loop, optimizer, scheduler, HEM
├── sweep.py                 # experiment runner (mirrors existing sweep.py)
└── results/                 # experiment logs and checkpoints
    └── (created at runtime)
```

**Shared imports (read-only, not modified):**
- `training.augmentations.apply_augmentation` — augmentation pipeline
- `training.config.AugmentationPhase` — enum for aug phase gating
- `common.geometry.order_keypoints_by_angle` — corner canonicalization

The regression config will contain its own copy of all augmentation fields so
that `apply_augmentation` can be called with a compatible object. The augmentation
function only reads fields from the config, it doesn't require a specific type.

---

## 2. File-by-File Specification

### 2.1 `config.py` — `RegressionConfig`

A standalone dataclass. Copies all shared fields (input dims, optimizer, freeze/unfreeze,
augmentation, checkpoint, W&B) from `training.config.TrainingConfig`.

**Removed fields** (heatmap-specific, no longer needed):
- `gaussian_sigma`, `gaussian_floor`, `sigma_schedule`
- `focal_alpha`, `focal_beta`
- `lambda_offset`

**New fields:**
```python
# --- Regression head ---
wing_w: float = 10.0              # wing loss width (pixels, before normalization)
wing_epsilon: float = 2.0         # wing loss curvature (pixels, before normalization)
attention_temperature: float = 1.0 # softmax temperature; <1 sharpens attention
```

**Rationale for defaults:**
- `wing_w=10.0`: Standard value from Feng et al. 2018. Errors below 10px get
  amplified gradient. Since our PCK@4 threshold is 4px, this covers the entire
  range we care about.
- `wing_epsilon=2.0`: Controls curvature steepness. At |error|=epsilon, gradient
  is w/(2*epsilon) = 2.5× L1. Standard value.
- `attention_temperature=1.0`: Neutral start. Reduce to 0.5 if attention doesn't
  concentrate by epoch 50.

### 2.2 `model.py` — `CornerRegressionNet`

**Architecture:**
```
Input:  (N, 3, 80, 256)
  ↓
Encoder: ResNet18 stem → layer1 → layer2 → layer3
  ↓  (N, 256, 5, 16)  stride=16 bottleneck
Decoder stage 1: ConvTranspose2d(256,128) + skip from layer2 → fuse
  ↓  (N, 128, 10, 32)
Decoder stage 2: ConvTranspose2d(128,64) + skip from layer1 → fuse
  ↓  (N, 64, 20, 64)   stride=4 feature map
Attention head: Conv2d(64, 4, kernel_size=1)
  ↓  (N, 4, 20, 64)    raw attention logits
Temperature scaling: logits / τ
  ↓
Spatial softmax: softmax over flattened 1280 positions per channel
  ↓  (N, 4, 20, 64)    attention weights (sum to 1.0 per channel)
Expected coordinates:
  x_i = sum(attention_i * x_grid) for each corner i
  y_i = sum(attention_i * y_grid) for each corner i
  ↓
Output: (N, 8)  [x0, y0, x1, y1, x2, y2, x3, y3] in pixel coords
```

**Key implementation details:**

- Encoder + decoder code is **copied verbatim** from `training/model.py:CornerHeatmapNet`.
  Same layer definitions, same weight init for decoder, same pretrained ResNet18.
- `attention_head`: Kaiming normal weights, **zero bias** (flat initial attention →
  uniform softmax → predicts image center initially).
- `x_grid` and `y_grid` registered as **buffers** (non-trainable, auto-move to GPU):
  ```python
  x_grid = torch.arange(grid_w).float() * stride  # [0, 4, 8, ..., 252]
  y_grid = torch.arange(grid_h).float() * stride   # [0, 4, 8, ..., 76]
  # Broadcast to (1, 1, grid_h, grid_w) for batch multiplication
  ```
- `forward(x) -> Tensor`:  Returns (N, 8) coordinates only. Used in training loop.
- `forward_with_attention(x) -> tuple[Tensor, Tensor]`: Returns ((N, 8), (N, 4, 20, 64)).
  Used in evaluation/visualization. Avoids double forward pass — computes attention
  once, returns both coordinates and maps.
- `encoder_params()`, `decoder_params()`, `head_params()`: Same interface as
  `CornerHeatmapNet` for compatibility with the 3-group optimizer.

**Parameter count:** ~7.7M total (identical to heatmap model; head changes from
580 params to 260 params, negligible difference).

### 2.3 `losses.py` — Wing Loss

Two functions:

**`wing_loss(pred, target, w, epsilon)`**
```python
def wing_loss(pred: Tensor, target: Tensor, w: float, epsilon: float) -> Tensor:
    """Element-wise Wing loss (Feng et al., 2018).

    Args:
        pred: (N, 8) predicted normalized coordinates [0, 1].
        target: (N, 8) target normalized coordinates [0, 1].
        w: Wing loss width (in normalized units).
        epsilon: Wing loss curvature (in normalized units).

    Returns:
        Scalar mean loss.
    """
    diff = (pred - target).abs()
    C = w - w * math.log(1.0 + w / epsilon)
    # Piecewise: log region for small errors, linear for large
    loss = torch.where(
        diff < w,
        w * torch.log(1.0 + diff / epsilon),
        diff - C,
    )
    return loss.mean()
```

**`regression_loss(pred, target, input_w, input_h, w_pixels, epsilon_pixels)`**
```python
def regression_loss(
    pred: Tensor,     # (N, 8) pixel coords
    target: Tensor,   # (N, 8) pixel coords
    input_w: int,     # 256
    input_h: int,     # 80
    w_pixels: float,  # 10.0
    epsilon_pixels: float,  # 2.0
) -> Tensor:
    """Normalize coordinates and compute wing loss."""
    # Scale factors: [w, h, w, h, w, h, w, h]
    scale = torch.tensor(
        [input_w, input_h] * 4,
        device=pred.device, dtype=pred.dtype,
    )
    pred_norm = pred / scale
    target_norm = target / scale
    w_norm = w_pixels / input_w  # ≈ 0.039
    eps_norm = epsilon_pixels / input_w  # ≈ 0.0078
    return wing_loss(pred_norm, target_norm, w_norm, eps_norm)
```

**Why normalize independently (x/256, y/80)?**
A 1px error should count the same regardless of axis. Without normalization, the
256-wide x-axis would dominate the 80-tall y-axis by 3.2×. Independent normalization
balances them. This matches how PCK measures Euclidean distance in pixel space.

### 2.4 `dataset.py` — `PlateCornerRegressionDataset`

**Copied from** `training/dataset.py:PlateCornerDataset` with these changes:

- `__getitem__` returns `(img_tensor, coord_target, idx)` — 3 items.
- `coord_target = torch.tensor([x0, y0, x1, y1, x2, y2, x3, y3], dtype=torch.float32)`
  in letterbox pixel coordinates.
- **No** `generate_heatmap_target()` call.
- **No** `generate_offset_target()` call.
- **No** `set_sigma()` method (no heatmaps → no sigma).
- Keeps: letterbox, augmentation, corner canonicalization, lighting labels.

**Shared functions imported** (not copied):
- `letterbox_crop` from `training.dataset` — or copy locally to avoid import.
  Decision: **copy locally** to maintain full isolation. It's 20 lines.
- `corners_to_letterbox` from `training.dataset` — same, copy locally (5 lines).

### 2.5 `evaluate.py` — Evaluation

**New functions:**

```python
def extract_corners_from_regression(
    output: Tensor,  # (1, 8) or (N, 8)
) -> tuple[list[tuple[float, float]], list[float]]:
    """Extract corners from regression output. Trivial reshape."""
    coords = output[0]  # (8,)
    corners = [(float(coords[2*i]), float(coords[2*i+1])) for i in range(4)]
    confidences = [1.0, 1.0, 1.0, 1.0]  # placeholder
    return corners, confidences

def extract_corners_with_confidence(
    coords: Tensor,        # (1, 8)
    attention_maps: Tensor, # (1, 4, H, W)
) -> tuple[list[tuple[float, float]], list[float]]:
    """Extract corners + attention-based confidence."""
    corners = [(float(coords[0, 2*i]), float(coords[0, 2*i+1])) for i in range(4)]
    confidences = [float(attention_maps[0, i].max()) for i in range(4)]
    return corners, confidences
```

**Reused functions** (copied from `training/evaluate.py`):
- `compute_cpe()` — unchanged, operates on corner lists
- `compute_pck()` — unchanged
- `draw_corner_overlay()` — unchanged

**Modified functions:**
- `evaluate_batch()` — takes model, images, coord_targets; calls
  `model.forward_with_attention()`, extracts corners + confidence.
  GT corners come directly from `coord_target` (no offset decoding needed).
- `validate()` — same structure, unpacks 3-item tuples from dataloader.

**New visualization:**
- `render_attention_overlay()` — replaces `render_heatmap_overlay()`.
  Takes attention maps (N, 4, H, W), max across channels, apply COLORMAP_JET.
  Same logic, different input source.

### 2.6 `train.py` — Training Loop

**Copied structure from** `training/train.py` with these changes:

**Same:**
- `get_device()` — identical
- `build_optimizer()` — identical 3-group structure (encoder 0.1×, decoder, head)
- `build_scheduler()` — identical linear warmup + cosine decay
- `freeze_encoder()` / `unfreeze_encoder()` / `check_unfreeze()` — identical
- `build_hem_sampler()` — identical (weights based on per-image loss)
- `check_phase_advance()` — identical logic
- `save_checkpoint()` / `load_checkpoint()` — identical structure
- `poll_config()` — identical
- W&B integration — same pattern

**Changed:**
- `train_one_epoch()`:
  - Unpacks `(images, coord_target, _idx)` instead of 5-item tuples
  - Calls `regression_loss(pred, coord_target, ...)` instead of `combined_loss()`
  - Returns `{"train_loss": ..., "train_reg_loss": ...}` (single loss, no hm/off split)
  - Per-image HEM loss: `regression_loss(pred[i:i+1], target[i:i+1], ...)`
- `log_epoch()`:
  - Logs `train/reg_loss` instead of `train/hm_loss` + `train/off_loss`
  - Logs `val/attention_entropy` (new metric for monitoring attention concentration)
- `log_visualizations()`:
  - Uses `forward_with_attention()` and `render_attention_overlay()`
- `train()`:
  - Instantiates `CornerRegressionNet` and `PlateCornerRegressionDataset`
  - No sigma scheduling logic (removed entirely)
  - Otherwise identical epoch loop structure

**Removed:**
- `get_scheduled_sigma()` — no sigma in regression approach
- All sigma schedule handling in epoch loop

### 2.7 `sweep.py` — Experiment Runner

Mirrors `models/corner-cnn/experiments/sweep.py` structure but imports from
`training.regression.*`. Contains the experiment configurations for the
regression approach (see Section 4).

---

## 3. Parameter Decisions (Informed by 14-Experiment Heatmap Sweep)

### 3.1 What transfers directly from heatmap experiments

These parameters were optimized across 14 experiments and their optimal values
are architecture-independent (they control the encoder, decoder, and optimizer,
which are identical in both approaches):

| Parameter | Optimal Value | Evidence |
|-----------|--------------|----------|
| `base_lr` | 1e-4 | 3e-4 overshoots (exp 13: 0.159), 2e-4 no benefit (exp 12) |
| `encoder_lr_mult` | 0.05 | Best with σ=3.0 (exp 2: 0.361). 0.10 caps at ~0.20 (exps 9,10,12) |
| `batch_size` | 16 | bs=32 halves gradient updates, slows convergence (exp 11) |
| `weight_decay` | 1e-4 | Default, never varied (not a bottleneck) |
| `freeze_min_epochs` | 15 | Standard across all experiments |
| `freeze_max_epochs` | 60 | Standard, allows stagnation detection |
| `grad_clip_max_norm` | 10.0 | Standard, never varied |
| `warmup_epochs` | 5 | Standard |
| `warmup_start_lr` | 1e-6 | Standard |

### 3.2 What changes (heatmap-specific → regression-specific)

| Heatmap Parameter | Value | Regression Replacement | Value | Rationale |
|-------------------|-------|----------------------|-------|-----------|
| `gaussian_sigma` | 3.0 | N/A (removed) | — | No heatmaps |
| `focal_alpha` | 2.0 | N/A (removed) | — | No focal loss |
| `focal_beta` | 4.0 | N/A (removed) | — | No focal loss |
| `lambda_offset` | 1.0 | N/A (removed) | — | Single loss, no balancing |
| `sigma_schedule` | various | N/A (removed) | — | No sigma |
| N/A | — | `wing_w` | 10.0 | Standard, covers 0-10px |
| N/A | — | `wing_epsilon` | 2.0 | Standard curvature |
| N/A | — | `attention_temperature` | 1.0 | Neutral start |

### 3.3 What we expect to be different

**Convergence speed:** The heatmap model gets dense gradient signal (focal loss
over 1280 grid cells per corner). The regression model gets point gradient signal
(8 scalars per sample). However, wing loss amplifies small-error gradient by 1.7-3.3×
compared to L1, partially compensating. We expect **slightly slower early convergence**
but **no precision ceiling** — the model can keep improving past where heatmap plateaus.

**Run-to-run variance:** The 80% relative variance (exp 2 vs exp 14) was driven by
frozen-phase heatmap learning. Regression targets are simpler (direct coordinates vs
Gaussian heatmaps), which may reduce variance. But the encoder/decoder init is still
random, so substantial variance is expected. **3-seed runs are mandatory.**

**Post-unfreeze behavior:** The heatmap model's post-unfreeze dip was encoder-LR-driven,
not architecture-dependent. We expect similar dip patterns. enc_lr_mult=0.05 eliminated
the dip in heatmap experiments (exp 2: 0% dip), so it should work here too.

---

## 4. Experiment Plan

### 4.1 Phase 0: Sanity Check (1 run, ~10 min)

**Purpose:** Verify the code works, loss decreases, attention concentrates.

```python
{
    "name": "sanity_check",
    "epochs": 20,
    "params": {
        "base_lr": 1e-4,
        "encoder_lr_mult": 0.05,
        "batch_size": 16,
        "wing_w": 10.0,
        "wing_epsilon": 2.0,
        "attention_temperature": 1.0,
        "freeze_min_epochs": 15,
    },
}
```

**Success criteria:**
- Loss decreases monotonically after epoch 2
- `max(attention)` per corner rises from ~0.001 (uniform=1/1280) to >0.01 by epoch 20
- PCK@8 > 0.05 (model is learning something)
- No NaN/Inf in loss or coordinates

**If sanity fails:** Check attention temperature, gradient magnitudes, coordinate
scale. Log attention entropy per epoch.

### 4.2 Phase 1: Baseline Comparison (3 seeds × 150 epochs, ~4.5 hours)

**Purpose:** Establish regression baseline with proven heatmap-optimal parameters.
Compare to heatmap best (0.361 at 50 epochs, projected 0.38-0.50 at 300 epochs).

```python
# Run 3 times with different random seeds
{
    "name": "regression_baseline_seed{0,1,2}",
    "epochs": 150,
    "params": {
        "base_lr": 1e-4,
        "encoder_lr_mult": 0.05,
        "batch_size": 16,
        "wing_w": 10.0,
        "wing_epsilon": 2.0,
        "attention_temperature": 1.0,
        "freeze_min_epochs": 15,
        "freeze_max_epochs": 60,
        "wandb_enabled": True,
    },
}
```

**Key metrics to track:**
- PCK@4, PCK@2, PCK@8 per epoch
- **PCK@4/PCK@8 ratio** — does it break the 0.50 ceiling?
- Mean CPE
- `max(attention)` per corner (attention concentration)
- Attention entropy (should decrease over training)
- Mean confidence (attention peak)
- Post-unfreeze dip %
- Per-corner CPE breakdown

**Success criteria:**
- Mean PCK@4 across 3 seeds > 0.25 at 150 epochs (comparable to heatmap at 75 epochs)
- PCK@4/PCK@8 ratio > 0.55 for at least 1 seed (breaks the 0.50 ceiling)
- Attention maps show clear corner localization (single dominant peak per channel)

**Decision point:** If mean PCK@4 < 0.15 at epoch 50, abort and investigate
attention concentration. May need temperature reduction (Phase 2 alt).

### 4.3 Phase 2: Temperature Sweep (3 runs × 150 epochs, ~4.5 hours)

**Purpose:** If Phase 1 attention stays diffuse (max attention < 0.05 at epoch 50),
test temperature scaling to sharpen it.

Only run if Phase 1 shows diffuse attention. Otherwise skip.

```python
TEMPERATURE_SWEEP = [
    {
        "name": "temp_0.5",
        "params": {
            # ... same as baseline ...
            "attention_temperature": 0.5,
        },
    },
    {
        "name": "temp_0.25",
        "params": {
            "attention_temperature": 0.25,
        },
    },
    {
        "name": "temp_2.0",
        "params": {
            "attention_temperature": 2.0,  # softer, for comparison
        },
    },
]
```

**Rationale:** Temperature < 1.0 divides logits before softmax, making the
distribution peakier. This concentrates attention on fewer cells, giving
more precise expected coordinates but potentially losing the interpolation
benefit. Temperature > 1.0 (softer) tests if more interpolation helps.

### 4.4 Phase 3: Wing Loss Parameter Sweep (4 runs × 150 epochs, ~6 hours)

**Purpose:** Explore whether wing loss parameters significantly affect convergence.

Only run after Phase 1 establishes that the approach works. Can run in parallel
with Phase 2 if attention is fine.

```python
WING_SWEEP = [
    {
        "name": "wing_w5_eps1",
        "params": {
            "wing_w": 5.0,     # tighter log region (0-5px)
            "wing_epsilon": 1.0,
        },
    },
    {
        "name": "wing_w20_eps4",
        "params": {
            "wing_w": 20.0,    # wider log region (0-20px)
            "wing_epsilon": 4.0,
        },
    },
    {
        "name": "smoothl1_comparison",
        "params": {
            "loss_type": "smooth_l1",  # direct comparison to current offset loss
        },
    },
    {
        "name": "l1_comparison",
        "params": {
            "loss_type": "l1",  # simplest baseline
        },
    },
]
```

**Rationale:**
- `w=5, eps=1`: Concentrates amplified gradient in the 0-5px range (directly
  targeting PCK@4). More aggressive than default but risks instability.
- `w=20, eps=4`: Wider log region. More gradual transition to L1 tail.
- Smooth L1 and L1 comparisons tell us how much wing loss specifically
  contributes vs just switching to regression.

### 4.5 Phase 4: Extended Run (best config × 3 seeds × 300 epochs, ~9 hours)

**Purpose:** Full-length comparison to heatmap 300-epoch projections.

Take the best config from Phases 1-3 and run to 300 epochs with 3 seeds.

```python
{
    "name": "best_config_300ep_seed{0,1,2}",
    "epochs": 300,
    "params": {
        # ... best from Phase 1-3 ...
        "wandb_enabled": True,
        "early_stopping_patience": 50,  # stop if no improvement for 50 epochs
    },
}
```

**Success criteria for promotion to production candidate:**
- Mean PCK@4 > 0.50 across 3 seeds (beats projected heatmap ceiling)
- PCK@4/PCK@8 ratio > 0.60 (confirmed structural improvement)
- PCK@8 ≥ 0.60 (no regression in coarse localization)
- Stable training (no divergence, no loss spikes)

### 4.6 Experiment Summary

| Phase | Runs | Epochs | GPU Hours | Prerequisite |
|-------|------|--------|-----------|-------------|
| 0: Sanity | 1 | 20 | ~0.2h | Code complete |
| 1: Baseline 3-seed | 3 | 150 | ~4.5h | Phase 0 passes |
| 2: Temperature sweep | 3 | 150 | ~4.5h | Only if Phase 1 attention diffuse |
| 3: Wing loss sweep | 4 | 150 | ~6h | Phase 1 shows approach works |
| 4: Extended best config | 3 | 300 | ~9h | Best config identified |

**Total estimated GPU time:** 10-24 hours depending on which phases are needed.

---

## 5. Metrics and Monitoring

### 5.1 New Metrics (not in heatmap experiments)

| Metric | Formula | Purpose |
|--------|---------|---------|
| `attention_entropy` | `-sum(attn * log(attn))` per corner, mean over batch | Monitor attention concentration. Should decrease from ln(1280)≈7.15 toward 0 |
| `attention_peak` | `max(softmax(attention))` per corner | Confidence proxy. Should rise from 1/1280≈0.001 toward >0.05 |
| `coord_mae` | Mean absolute error in pixel space | Intuitive error metric alongside CPE |
| `pck4_pck8_ratio` | `PCK@4 / max(PCK@8, 0.001)` | THE key metric — must exceed 0.50 to confirm approach works |

### 5.2 W&B Dashboard

Each run logs:
```
train/reg_loss          — per-epoch training loss
val/mean_cpe            — validation corner pixel error
val/pck_2, pck_4, pck_8 — PCK at thresholds
val/pck4_pck8_ratio     — precision ratio (MOST IMPORTANT)
val/attention_entropy    — attention concentration
val/attention_peak_mean  — mean peak attention
val/attention_peak_min   — min peak attention (worst corner)
val/mean_confidence      — attention-based confidence
lr                       — current learning rate
encoder_frozen           — freeze state
aug_phase                — augmentation phase
```

---

## 6. Risk Mitigation

### Risk 1: Attention stays diffuse (probability: 20-30%)

**Detection:** `attention_peak < 0.01` and `attention_entropy > 6.0` at epoch 50.

**Mitigation:**
1. Reduce temperature to 0.5 → 0.25 (Phase 2)
2. Add entropy regularization: `loss = wing_loss + λ_entropy * attention_entropy`
   (encourages the model to concentrate attention)
3. Initialize attention head bias to non-zero values that create initial peaks
   near image center/corners

### Risk 2: Slower convergence than heatmap (probability: 40-50%)

**Detection:** PCK@8 at epoch 50 < 0.20 (heatmap baseline achieves ~0.37-0.62).

**Mitigation:**
1. This is expected — regression gets 8 scalar signals per sample vs 5120 dense
   heatmap signals. Wing loss partially compensates.
2. If PCK@8 is growing but slowly, just train longer (Phase 4).
3. If PCK@8 is truly stalled, check gradient magnitudes through the model.

### Risk 3: Run-to-run variance remains high (probability: 60-70%)

**Detection:** 3-seed std/mean > 0.30 for PCK@4 at 150 epochs.

**Mitigation:**
1. Always report mean ± std across 3+ seeds. Never draw conclusions from
   single runs.
2. Consider SWA (Stochastic Weight Averaging) for the last 30% of training.
3. Use deterministic data ordering for first 10 epochs (warmup).

### Risk 4: Model predicts out-of-bound coordinates (probability: 10%)

**Detection:** Predicted coordinates outside [0, 256] × [0, 80].

**Mitigation:**
1. Coordinates from spatial expectation are inherently bounded by the grid
   coordinates: x ∈ [0, 252], y ∈ [0, 76] (grid cell centers × stride).
   This is a structural guarantee of the soft-argmax — no clamping needed.
2. This is actually an advantage over direct FC regression, which has no
   inherent bounds.

---

## 7. Implementation Order

```
Step 1: config.py          (~30 lines)   — RegressionConfig dataclass
Step 2: losses.py          (~50 lines)   — wing_loss + regression_loss
Step 3: model.py           (~120 lines)  — CornerRegressionNet
Step 4: dataset.py         (~100 lines)  — PlateCornerRegressionDataset
Step 5: evaluate.py        (~120 lines)  — extraction, metrics, validate
Step 6: train.py           (~250 lines)  — full training loop
Step 7: sweep.py           (~120 lines)  — experiment runner
Step 8: __init__.py        (~5 lines)    — package marker
```

**Total: ~800 lines of new code.** No existing files modified.

Each step can be tested independently:
- Step 2: Unit test wing_loss gradient properties
- Step 3: Forward pass shape test with random input
- Step 4: Load one sample, verify coord_target shape
- Step 5: Verify CPE/PCK with known inputs
- Step 6: Run 2-epoch smoke test

---

## 8. What We're Testing (Hypotheses)

**Primary hypothesis:** Replacing hard argmax + sparse offset with soft-argmax
breaks the PCK@4/PCK@8 ≈ 0.50 structural ceiling.

**Secondary hypothesis:** Wing loss provides better convergence in the sub-10px
error range compared to smooth L1 on sparse offsets.

**Null hypothesis (what failure looks like):** PCK@4/PCK@8 ratio remains ≈ 0.50,
and/or PCK@4 at 300 epochs is below heatmap projection (0.38-0.50). This would
indicate the precision ceiling is not caused by the hard argmax bottleneck but
by the encoder/decoder spatial resolution itself.

If the null hypothesis holds, the next investigation should be stride-2 decoder
(higher output resolution) rather than further head architecture changes.
