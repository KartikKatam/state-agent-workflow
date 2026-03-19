# Handoff: Variance Prediction for Calibrated Confidence

**From**: regression-coder
**To**: variance prediction agent
**Date**: 2026-02-20
**Status**: Ready for implementation, training, and analysis

---

## Objective

Add a variance prediction head to the regression model so each corner prediction comes with a calibrated uncertainty estimate (predicted sigma in pixels). Train for 100 epochs with identical parameters to baseline, then generate a report comparing confidence quality between:

1. **Current approach**: attention peak as confidence proxy (uncalibrated)
2. **Variance prediction**: learned per-corner sigma (calibrated)

**Baseline to compare against**: PCK@4=0.890, Mean CPE=2.28px (epoch 98, 100 epochs)

---

## CRITICAL RULES

1. **Do NOT modify any existing files.** All new code goes in `training/regression/variance/`.
2. Files to create:
   - `training/regression/variance/__init__.py`
   - `training/regression/variance/model.py` — CornerRegressionNetWithVariance
   - `training/regression/variance/losses.py` — Gaussian NLL loss combining coordinate + variance
   - `training/regression/variance/train.py` — training loop adapted for variance model
   - `training/regression/variance/evaluate.py` — evaluation with confidence calibration analysis
   - `training/regression/variance/report.py` — generates the calibration comparison report

---

## Context Files To Read

| File | Why |
|------|-----|
| `training/regression/HANDOFF.md` | Full project context, architecture, design decisions |
| `training/regression/model.py` | **PRIMARY** — CornerRegressionNet to extend with variance head |
| `training/regression/losses.py` | Current wing_loss — variance model replaces this with Gaussian NLL |
| `training/regression/train.py` | Training loop to copy and adapt |
| `training/regression/evaluate.py` | Evaluation functions to extend with calibration metrics |
| `training/regression/config.py` | RegressionConfig — may need extra fields |
| `training/regression/results/baseline_100ep/best.pt` | Baseline checkpoint for comparison |

---

## How Variance Prediction Works

The model outputs both coordinates AND a learned uncertainty per coordinate:

```
Current model:
  Input → Encoder → Decoder → Attention Head → soft-argmax → (N, 8) coordinates

Variance model:
  Input → Encoder → Decoder → Attention Head → soft-argmax → (N, 8) coordinates
                             → Variance Head → softplus   → (N, 8) log-variances → exp → (N, 8) sigmas
```

The variance head is a separate `Conv2d(64, 8, 1)` (or `Conv2d(64, 4, 1)` if predicting one sigma per corner instead of per-coordinate). It operates on the same decoder features but produces uncertainty estimates instead of attention maps.

**Loss function**: Gaussian negative log-likelihood (NLL)

```
NLL = 0.5 * (pred - target)² / sigma² + log(sigma)
```

- First term: penalizes inaccurate predictions, scaled by uncertainty. If sigma is large, the penalty is reduced — the model is "admitting" it's uncertain.
- Second term: penalizes overconfidence. The model can't just predict infinite sigma to avoid the first term, because log(sigma) grows.
- At equilibrium: the model learns to predict sigma ≈ actual expected error magnitude for each corner.

---

## Implementation Details

### `training/regression/variance/model.py` — CornerRegressionNetWithVariance

Copy `training/regression/model.py` (CornerRegressionNet) and add:

1. **Variance head** after the decoder, parallel to attention head:
```python
self.variance_head = nn.Conv2d(64, 4, kernel_size=1)  # one sigma per corner
```

2. **Initialization**: zero weights, bias = 0.0 (initial sigma = softplus(0) ≈ 0.69, ~moderate uncertainty)
```python
nn.init.zeros_(self.variance_head.weight)
nn.init.constant_(self.variance_head.bias, 0.0)
```

3. **Forward pass** returns coordinates + sigmas:
```python
def forward(self, x: Tensor) -> tuple[Tensor, Tensor]:
    features = self._encode_decode(x)
    logits = self.attention_head(features)       # (N, 4, 20, 64)
    coords, _ = self._soft_argmax(logits)        # (N, 8)

    # Variance: global average pool the features, then predict per-corner sigma
    var_logits = self.variance_head(features)     # (N, 4, 20, 64)
    # Pool spatially — variance is a per-corner global property, not spatial
    var_pooled = var_logits.mean(dim=(2, 3))      # (N, 4)
    # Softplus ensures sigma > 0, scale to pixel units
    sigma = torch.nn.functional.softplus(var_pooled) * self.cfg.stride  # (N, 4)

    return coords, sigma
```

4. **forward_with_attention** returns all three:
```python
def forward_with_attention(self, x: Tensor) -> tuple[Tensor, Tensor, Tensor]:
    """Returns (coords, attention, sigma)."""
    features = self._encode_decode(x)
    logits = self.attention_head(features)
    coords, attention = self._soft_argmax(logits)
    var_logits = self.variance_head(features)
    var_pooled = var_logits.mean(dim=(2, 3))
    sigma = torch.nn.functional.softplus(var_pooled) * self.cfg.stride
    return coords, attention, sigma
```

5. **head_params()** must include both heads:
```python
def head_params(self):
    yield from self.attention_head.parameters()
    yield from self.variance_head.parameters()
```

### `training/regression/variance/losses.py`

```python
def gaussian_nll_loss(
    pred: Tensor,        # (N, 8) predicted pixel coordinates
    target: Tensor,      # (N, 8) target pixel coordinates
    sigma: Tensor,       # (N, 4) predicted sigma per corner (pixel units)
    input_w: int,
    input_h: int,
) -> Tensor:
    """Gaussian NLL loss with learned variance.

    Normalizes coordinates independently per axis (same as regression_loss),
    then computes NLL using the predicted sigma.
    """
    scale = torch.tensor([input_w, input_h] * 4, device=pred.device, dtype=pred.dtype)
    pred_norm = pred / scale
    target_norm = target / scale

    # Expand sigma from (N, 4) to (N, 8) — same sigma for x and y of each corner
    sigma_expanded = sigma.repeat_interleave(2, dim=1)  # (N, 8)
    # Normalize sigma the same way as coordinates
    sigma_norm = sigma_expanded / scale

    # Gaussian NLL: 0.5 * ((pred - target) / sigma)^2 + log(sigma)
    diff = pred_norm - target_norm
    nll = 0.5 * (diff / sigma_norm).pow(2) + torch.log(sigma_norm)

    return nll.mean()
```

**Important**: The sigma normalization must match the coordinate normalization. If x is normalized by 256 and y by 80, sigma_x is divided by 256 and sigma_y by 80. This ensures the model learns sigma in normalized units, consistent with the loss landscape.

### `training/regression/variance/train.py`

Copy `training/regression/train.py` with these changes:

1. Import `CornerRegressionNetWithVariance` instead of `CornerRegressionNet`
2. Import `gaussian_nll_loss` instead of `regression_loss`
3. In `train_one_epoch`, the forward pass returns `(coords, sigma)`:
```python
coords, sigma = model(images)
loss = gaussian_nll_loss(coords, coord_targets, sigma, cfg.input_w, cfg.input_h)
```
4. Per-image HEM loss also uses `gaussian_nll_loss`
5. Log `sigma.mean()` and `sigma.min()` as additional W&B metrics per epoch

### `training/regression/variance/evaluate.py`

Copy `training/regression/evaluate.py` with these changes:

1. `evaluate_batch` calls `model.forward_with_attention(images)` which now returns `(coords, attention, sigma)`
2. Add calibration metrics:
   - **Sigma-error correlation**: Pearson correlation between predicted sigma and actual CPE per corner. Should be positive (higher sigma → higher error). Values > 0.5 indicate good calibration.
   - **Calibration curve**: for sigma bins [0-1, 1-2, 2-3, 3-4, 4+], what fraction of corners have actual error < sigma? Should be ~68% if perfectly calibrated (1-sigma Gaussian).
   - **NLL score**: mean negative log-likelihood of the actual errors under the predicted Gaussian. Lower is better.

### `training/regression/variance/report.py`

After training, generate a comparison report. This script:

1. Loads the baseline model (`training/regression/results/baseline_100ep/best.pt`) and runs it on the validation set, collecting (attention_peak, actual_error) pairs for all corners.
2. Loads the variance model (`training/regression/results/variance_100ep/best.pt`) and runs it on the validation set, collecting (predicted_sigma, actual_error) pairs.
3. Computes and prints:

```
=== CONFIDENCE CALIBRATION COMPARISON ===

Baseline (attention peak as confidence):
  Correlation with error: {pearson_r}  (negative = higher confidence → lower error)
  Separation: mean_conf(error<4px) vs mean_conf(error>4px): {good_conf} vs {bad_conf}

Variance Prediction (learned sigma as confidence):
  Correlation with error: {pearson_r}  (positive = higher sigma → higher error)
  Calibration at 1-sigma: {pct}% of corners within predicted sigma (target: 68%)
  Calibration at 2-sigma: {pct}% of corners within 2*sigma (target: 95%)
  Mean predicted sigma for correct (<4px) corners: {sigma_good} px
  Mean predicted sigma for incorrect (>4px) corners: {sigma_bad} px
  Separation ratio: {sigma_bad/sigma_good}x (higher = better discrimination)

=== REJECTION ANALYSIS ===
If we reject corners where sigma > {threshold} px:
  Remaining: {pct}% of corners
  PCK@4 of remaining: {pck4}  (should be higher than 0.890)
  PCK@4 of rejected: {pck4_rejected}  (should be much lower)
```

This report directly answers: "Can the model reliably tell us when it's wrong?"

---

## Training Run

```python
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')

from training.regression.config import RegressionConfig
from training.regression.variance.train import train

cfg = RegressionConfig()
cfg.base_lr = 1e-4
cfg.encoder_lr_mult = 0.05
cfg.batch_size = 16
cfg.freeze_min_epochs = 15
cfg.freeze_max_epochs = 60
cfg.wing_w = 10.0           # Not used directly — gaussian_nll_loss replaces wing loss
cfg.wing_epsilon = 2.0      # Not used directly
cfg.attention_temperature = 1.0
cfg.total_epochs = 100
cfg.wandb_enabled = True
cfg.wandb_project = 'corner-regression'
cfg.checkpoint_dir = 'training/regression/results/variance_100ep'
cfg.checkpoint_interval = 10
cfg.vis_interval = 10

best_path = train(cfg)
print(f'\nBest checkpoint: {best_path}')
```

Log output to: `training/regression/results/variance_100ep.log`

Then run the report:
```python
from training.regression.variance.report import generate_report
generate_report(
    baseline_ckpt='training/regression/results/baseline_100ep/best.pt',
    variance_ckpt='training/regression/results/variance_100ep/best.pt',
)
```

---

## Key Gotchas

1. **Softplus, not exp, for sigma.** `exp()` can explode to infinity. `softplus(x) = log(1 + exp(x))` is numerically stable and always positive.
2. **sigma must be > 0.** If sigma becomes very small (< 1e-6), the `1/sigma²` term in NLL explodes. Clamp: `sigma = sigma.clamp(min=1e-4)`.
3. **The coordinate prediction should NOT degrade.** The variance head is a separate branch — it shouldn't hurt the attention head's coordinate accuracy. If PCK@4 drops significantly (>0.02), something is wrong with the loss balance.
4. **Global average pooling for variance.** Variance is a per-corner property (how confident am I about THIS corner?), not a spatial map. Pool the variance head's output spatially before predicting sigma. Don't try to do spatial variance — it overcomplicates things.
5. **Initial sigma matters.** With bias=0 and softplus, initial sigma ≈ 0.69 * stride = 2.76px. This is reasonable for early training. If you initialize too low, the model starts overconfident and the NLL loss explodes. If too high, the model starts too uncertain and coordinate learning is slow.
6. **NLL loss replaces wing loss entirely.** Don't add them together. The NLL already handles coordinate regression (through the `(pred-target)²/sigma²` term) and uncertainty estimation simultaneously.

---

## Expected Results

- **PCK@4**: should be within ±0.01 of baseline (0.880-0.900). The variance head shouldn't hurt coordinate accuracy.
- **Sigma values**: should converge to mean ~2-3px (matching actual mean CPE of 2.28px).
- **Calibration**: ~60-70% of corners within 1-sigma, ~90-95% within 2-sigma.
- **Discrimination**: sigma for bad corners (>4px error) should be 2-3x higher than sigma for good corners (<4px error).

Send the full report output to team-lead via SendMessage when done.
