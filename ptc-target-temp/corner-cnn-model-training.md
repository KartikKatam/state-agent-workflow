# Corner Heatmap CNN — Architecture & Training Design

**Date**: 2026-02-17
**Status**: Complete (all 3 parts finalized)
**Author**: Kartik

---

## Purpose

Define the complete model architecture, training harness, and training process for a lightweight CNN that predicts the 4 corners of a license plate from cropped plate images. This model enables perspective correction via homography to maximize OCR accuracy on drone-captured plates at 200ft altitude.

This document is a companion to the existing design doc "Replace YOLO-Pose with YOLO + Corner Heatmap CNN" which defines the pipeline integration. This document defines the model itself and how to train it.

---

## Part 1: Model Architecture

### Overview

The model follows an encoder-decoder architecture with skip connections, producing per-corner heatmaps and sub-pixel offset maps at stride 4 from the input resolution. The design is directly informed by CenterNet (Objects as Points, Zhou et al. 2019) and SimpleBaseline (Xiao et al. 2018), adapted for the specific constraints of license plate corner localization from drone imagery.

### Input Specification

- **Input size**: 80 × 256 × 3 (H × W × C), BGR, float32 normalized (uint8 → float32 / 255.0, HWC → CHW)
- **Source**: Letterbox-resized plate crops from the producer pipeline. Original variable H×W crops are resized preserving aspect ratio via `scale = min(256/W, 80/H)`, then center-padded with gray (114, 114, 114) to exactly 80×256. The original crop is untouched — letterboxing operates on a copy.
- **Context**: Crops include a 15% buffer around the YOLO detection bbox, providing surrounding vehicle/background context that aids corner localization

### Encoder

**Architecture**: ResNet18, pretrained on ImageNet, truncated after layer3.

| Stage | Output Stride | Spatial Resolution | Channels | Notes |
|-------|--------------|-------------------|----------|-------|
| Stem (conv1 + bn + relu + maxpool) | 4 | 20 × 64 | 64 | Initial downsampling |
| Layer1 | 4 | 20 × 64 | 64 | **Skip connection source → Decoder Stage 2** |
| Layer2 | 8 | 10 × 32 | 128 | **Skip connection source → Decoder Stage 1** |
| Layer3 | 16 | 5 × 16 | 256 | Bottleneck — deepest features, input to decoder |

**Why layer3 (not layer2 or layer4)**:

- Layer2 (stride 8, 128ch) has a receptive field of ~25-30px — sufficient for detecting local edge junctions but insufficient for reasoning about plate geometry in the context of oblique drone viewing angles and the 15% surrounding context.
- Layer3 (stride 16, 256ch) has a receptive field of ~60-70px, covering most of the plate crop. This enables whole-plate geometric reasoning — understanding all four corners in relation to each other and the plate-to-background boundary. At 200ft altitude with skewed plates, this global context is necessary to disambiguate genuine corners from noise, shadow edges, and compression artifacts.
- Layer4 (stride 32, 512ch) produces a 4×8 feature map — too coarse for precise spatial recovery even with a strong decoder. Overkill for this task.

**ImageNet pretraining**: The encoder is initialized with ImageNet-pretrained weights. Early layers (edges, textures, gradients) transfer directly to the plate corner domain. This dramatically reduces the amount of plate-specific training data needed, since the encoder already understands low-level and mid-level visual features.

### Decoder

**Architecture**: Two-stage upsampling decoder with skip connections from the encoder (U-Net pattern), using transposed convolutions.

Each decoder stage performs:
1. Transposed convolution (4×4 kernel, stride 2) to upsample 2× and reduce channels
2. Concatenation with the corresponding encoder skip features
3. Convolutional block (conv + BatchNorm + ReLU) to fuse the concatenated features

| Decoder Stage | Input | Upsample To | Skip Source | Channels After Concat | Channels After Fusion Conv |
|--------------|-------|-------------|-------------|----------------------|---------------------------|
| Stage 1 | Layer3: 5×16, 256ch | 10×32 | Layer2: 10×32, 128ch | 128 + 128 = 256 | 128 |
| Stage 2 | Stage 1: 10×32, 128ch | 20×64 | Layer1: 20×64, 64ch | 64 + 64 = 128 | 64 |

**Transposed convolution specification**: 4×4 kernel, stride 2, padding 1. This configuration produces even overlap and minimizes checkerboard artifacts (same configuration validated by CenterNet and SimpleBaseline).

**Channel reduction pattern**: 256 → 128 → 64, matching CenterNet's approach of decreasing channels at each upsample stage. This naturally aligns with the skip connection channel widths (layer2 = 128ch, layer1 = 64ch).

**Why skip connections**: The decoder must recover precise spatial information that was lost during encoding. Without skips, the decoder must hallucinate spatial detail from the 8×16 bottleneck alone. With skips, the decoder receives:
- From layer2 (stride 8): local corner structure features — "L-shaped junctions," edge intersections
- From layer1 (stride 4): precise edge and gradient locations at the output resolution

The fusion convolutions learn to combine semantic understanding from the deep path ("this region contains a plate corner") with spatial precision from the skip path ("edges are at exactly these pixel locations").

**Why transposed convolutions (not bilinear + conv)**: Transposed convolutions combine upsampling and learned refinement in a single operation, reducing the total number of layers. This approach is the standard in CenterNet and SimpleBaseline for heatmap prediction and is well-validated. The 4×4 kernel at stride 2 minimizes checkerboard risk. If artifacts are observed during training, the fallback is straightforward: swap to bilinear upsample + 3×3 conv without retraining the rest of the network.

### Output Heads

Two parallel heads read from the final decoder feature map (20 × 64, 64ch):

**Heatmap Head**:
- 1×1 convolution: 64ch → 4 channels (one per corner: TL, TR, BR, BL)
- Sigmoid activation to produce values in [0, 1]
- Each channel is a spatial probability map; the peak indicates the predicted corner location
- Ground truth: Gaussian blobs (sigma=1.5) centered at each labeled corner

**Offset Head**:
- 1×1 convolution: 64ch → 8 channels (x, y offset per corner)
- No activation (raw regression values)
- Predicts the sub-pixel offset from the heatmap grid cell center to the true corner location
- Compensates for the stride-4 discretization: each heatmap pixel covers a 4×4 input region, and the offset encodes where within that 4×4 cell the corner actually falls

### Output Summary

| Output | Shape | Activation | Purpose |
|--------|-------|-----------|---------|
| Heatmaps | 20 × 64 × 4 | Sigmoid | Coarse corner localization + confidence |
| Offsets | 20 × 64 × 8 | None | Sub-pixel refinement within stride-4 cells |

### Inference Post-Processing (as implemented in ops_corner_cnn.py)

For each of the 4 heatmap channels:
1. Find the peak via argmax → grid position (gx, gy)
2. The peak value serves as the confidence score for that corner
3. Read the corresponding offset values at the peak location (dx, dy)
4. Compute final corner position in letterbox space: `x = gx*4 + dx, y = gy*4 + dy`
5. Denormalize from letterbox space back to original crop pixel coordinates: `x_crop = (x_letterbox - pad_x) / scale`

If any corner confidence is below the threshold (default 0.3), the entire prediction is discarded and the ROI receives `keypoints=None` (all 4 must pass for a valid homography).

### Model Size and Latency Estimates

- **Parameters**: ~12-14M (ResNet18 layers 1-3: ~11M, decoder + heads: ~1-3M)
- **VRAM**: ~50-100MB (FP16)
- **Inference latency**: ~0.3-0.5ms per crop (TensorRT, FP16, RTX 5070)
- **Frame budget impact**: 1-5 crops/frame × 0.5ms = 0.5-2.5ms, well within 33ms budget

### Architecture Diagram

```
Input: 80×256×3
        │
        ▼
┌─ ResNet18 Encoder (ImageNet pretrained) ──────────────────────┐
│                                                                │
│   Stem (conv7×7, bn, relu, maxpool)  →  20×64, 64ch           │
│       │                                                        │
│   Layer1 (2 residual blocks)         →  20×64, 64ch  ──skip──┐│
│       │                                                       ││
│   Layer2 (2 residual blocks)         →  10×32, 128ch ──skip─┐││
│       │                                                      │││
│   Layer3 (2 residual blocks)         →  5×16, 256ch          │││
│                                                              │││
└──────────────────────────────────────────────────────────────│││
        │                                                      │││
        ▼                                                      │││
┌─ Decoder ────────────────────────────────────────────────────│││
│                                                              │││
│   Deconv Stage 1: TransConv 4×4/s2   →  10×32, 128ch        │││
│       + Concat with Layer2 skip       →  10×32, 256ch ◄─────┘││
│       + Conv 3×3 + BN + ReLU          →  10×32, 128ch        ││
│                                                               ││
│   Deconv Stage 2: TransConv 4×4/s2   →  20×64, 64ch          ││
│       + Concat with Layer1 skip       →  20×64, 128ch ◄──────┘│
│       + Conv 3×3 + BN + ReLU          →  20×64, 64ch          │
│                                                                │
└────────────────────────────────────────────────────────────────┘
        │
        ├──→ Heatmap Head: Conv 1×1 → 20×64×4  (sigmoid)
        │
        └──→ Offset Head:  Conv 1×1 → 20×64×8  (no activation)
```

### Key Design References

- **CenterNet** (Zhou et al., 2019) — "Objects as Points": Established the heatmap + offset regression paradigm for keypoint prediction using ResNet encoders with transposed convolution decoders. Our architecture follows this pattern directly, adapted from object center detection to plate corner detection.
- **SimpleBaseline** (Xiao et al., 2018) — "Simple Baselines for Human Pose Estimation": Demonstrated that a ResNet encoder with transposed convolution layers (256 filters, 4×4 kernel, stride 2) produces competitive heatmap predictions without complex decoder designs. Validated the deconv approach over bilinear alternatives.
- **U-Net** (Ronneberger et al., 2015): Established skip connections between encoder and decoder at matching resolutions for precise spatial recovery in dense prediction tasks. Our skip connections from layer1 and layer2 follow this principle.

### Decisions Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Encoder backbone | ResNet18 (pretrained ImageNet) | Lightweight, well-understood, strong transfer learning. Sufficient capacity for plate corners without being overkill. |
| Encoder truncation | After layer3 (stride 16, 256ch) | Receptive field covers full plate crop including 15% context buffer. Needed for whole-plate geometric reasoning on skewed drone imagery. Layer2 too shallow for oblique angles, layer4 too coarse spatially. |
| Decoder type | Transposed convolutions + skip connections | Transposed convolutions validated by CenterNet/SimpleBaseline for heatmap prediction. Skip connections provide spatial precision from encoder layers at matching resolutions (U-Net pattern). Combined approach gives semantic + spatial quality. |
| Decoder channels | 256 → 128 → 64 (matching skip widths) | Follows CenterNet's decreasing channel pattern. Natural alignment with layer2 (128ch) and layer1 (64ch) skip connections. |
| Transposed conv kernel | 4×4, stride 2 | Even overlap minimizes checkerboard artifacts. Exact configuration used by CenterNet and SimpleBaseline. |
| Output stride | 4 (20×64 from 80×256 input) | Balances spatial precision with computational cost. Offset head compensates for stride-4 discretization to achieve sub-pixel accuracy. |
| Heatmap channels | 4 (one per corner: TL, TR, BR, BL) | Each corner gets independent spatial probability map with individual confidence score. Enables per-corner gating. |
| Offset channels | 8 (x, y per corner) | Sub-pixel refinement within each stride-4 cell. Standard in CenterNet-family architectures. |

---

## Part 2: Training Harness

### Overview

The training harness defines how the model learns: what the ground truth targets look like, how prediction errors are measured, and how the model's weights are updated. Every decision here is informed by the CenterNet heatmap detection paradigm, adapted for the specific constraints of license plate corner prediction from 20×64 stride-4 heatmaps.

### Ground Truth Heatmap Generation

For each training image, the 4 labeled corner positions (TL, TR, BR, BL) in letterbox pixel coordinates are converted into target heatmaps on the 20×64 output grid.

**Gaussian rendering**: For each corner, a 2D Gaussian blob is rendered onto its corresponding heatmap channel, centered at the corner's grid position (corner_x / 4, corner_y / 4).

**Sigma**: Fixed at 1.5 (in heatmap pixel units).

At sigma 1.5, the Gaussian has meaningful activation (>13% of peak) within ~3 heatmap pixels of center, which maps to ~12 input pixels. This produces a tight, precise target that:
- Keeps adjacent vertical corners (TL/BL or TR/BR) well-separated even on foreshortened plates, which is critical on the 20-pixel-tall heatmap where vertical corner spacing can be as small as 8-10 heatmap pixels
- Produces sharp peaks that yield unambiguous argmax localization during inference
- Is sparse enough that focal loss is necessary (and sufficient) to handle the extreme positive/negative imbalance — only ~2.2% of heatmap pixels carry meaningful gradient signal

A fixed sigma is appropriate because the letterbox preprocessing normalizes all crops to 80×256, and the 15% YOLO bbox buffer keeps plate size within the crop relatively consistent. Adaptive sigma (scaling with plate size) would only be needed if plate-to-crop ratio varied dramatically, which it does not in this pipeline.

**Pixel values**: The Gaussian peak is 1.0 at center, decaying according to `exp(-(dx² + dy²) / (2 * sigma²))`. Values below a floor threshold (e.g., 1e-4) are clamped to 0 to keep the target sparse.

### Heatmap Loss: Focal Loss (CenterNet variant)

**Choice**: Modified focal loss as defined in CornerNet/CenterNet, not standard MSE or BCE.

**Why not MSE**: MSE treats every heatmap pixel equally. With ~97.8% of pixels being background (near-zero target), the model can minimize MSE by predicting near-zero everywhere. The sparse corner peaks generate insufficient gradient to overcome the background signal. Training converges very slowly and the model is biased toward under-prediction.

**Why not BCE**: BCE is better than MSE for imbalanced targets because it penalizes confident wrong predictions more harshly. However, it still doesn't actively suppress the gradient from easy background pixels, so the imbalance problem remains.

**Why focal loss**: Focal loss was purpose-built for extreme class imbalance (originally for object detection in RetinaNet). It applies two key modifications:
1. **Down-weights easy predictions**: Background pixels where both prediction and target are near zero are "easy" — the model is already correct. Focal loss reduces their gradient contribution to near-zero, preventing them from drowning out the sparse corner signal.
2. **Gaussian-modulated background penalty**: Pixels near a corner (where the Gaussian target is say 0.3) receive a reduced background penalty compared to pixels far from any corner (target 0.0). This creates a soft transition zone around each corner rather than a hard boundary between "corner" and "not corner."

**Hyperparameters**:
- **Alpha = 2**: Controls how aggressively easy examples are down-weighted. At alpha=2, a pixel where the model predicts 0.1 and the target is 0.0 contributes (0.1)² = 1% of the gradient it would contribute under standard BCE. Standard value from CornerNet/CenterNet.
- **Beta = 4**: Controls how the Gaussian target values modulate the background penalty. At beta=4, a pixel near a corner (Gaussian value 0.5) has its background penalty reduced by (1 - 0.5)⁴ = 6.25% of the full penalty. Pixels far from corners (Gaussian value 0.0) receive the full penalty. Standard value from CornerNet/CenterNet.

These are well-validated defaults that are rarely changed in practice.

### Offset Loss: Smooth L1 (Huber Loss)

**Choice**: Smooth L1 loss, computed only at the 4 ground truth corner locations per image.

**What it does**: Smooth L1 behaves like L1 (linear penalty) for large errors and transitions to L2 (quadratic penalty) for small errors below a threshold (default 1.0). This combines the outlier robustness of L1 with the smooth gradients of L2 near zero, making optimization stable when the model is already producing accurate offsets.

**Why not L1**: Pure L1 has a gradient discontinuity at zero — the gradient magnitude is constant regardless of how close the prediction is to the target. This can cause oscillation when the model is near convergence.

**Why not L2**: L2 squares the error, so outliers (a few badly annotated corners, or extreme predictions early in training) produce disproportionately large gradients that can destabilize training.

**Sparse computation**: The offset loss is computed only at the 4 ground truth corner positions on the heatmap grid, not across the entire 20×64 spatial map. At all other positions, the offset prediction is irrelevant — there is no corner there, so the offset value doesn't matter. This means the offset loss is a sum over exactly 4 spatial locations × 2 values (dx, dy) per location = 8 scalar loss terms per image.

**Offset target computation**: For a ground truth corner at input pixel position (px, py), the corresponding heatmap grid position is (gx, gy) = (floor(px/4), floor(py/4)). The offset target is: `target_dx = px - gx*4`, `target_dy = py - gy*4`. These values are in the range [0, 4) by construction — the offset encodes where within the stride-4 cell the corner falls.

### Total Loss and Weighting

**Total loss**: `L_total = L_heatmap + λ × L_offset`

**Lambda (λ)**: Starting value 1.0.

This balances the heatmap focal loss (computed over all 20×64×4 = 5,120 heatmap pixels, but effectively dominated by ~112 positive-region pixels due to focal weighting) against the offset Smooth L1 loss (computed over exactly 8 values). The gradient magnitudes from both losses at λ=1.0 are in roughly the same range for CenterNet-style architectures.

**Tuning strategy**: Monitor both loss components on the validation set during training. If after 30-50 epochs the heatmap loss has converged but offset loss remains high, increase λ to 2.0-3.0. If offset converges quickly but heatmaps remain blurry, decrease λ to 0.5. Expect 2-3 training runs to finalize λ. At ~10-30 minutes per run, total tuning time is under 2 hours.

### Optimizer

**Choice**: AdamW

AdamW's adaptive per-parameter learning rates handle the different gradient scales between the pretrained encoder (small gradients, already near good features) and the randomly-initialized decoder/heads (large gradients, learning from scratch). The decoupled weight decay provides regularization without interfering with the adaptive learning rate — important for preventing overfitting on a small dataset (1000-2000 images).

**Configuration**:
- Learning rate: 1e-4 (standard for fine-tuning pretrained encoders)
- Weight decay: 1e-4 (light regularization)
- Betas: (0.9, 0.999) (PyTorch defaults, well-validated)

### Learning Rate Schedule

**Choice**: Cosine annealing with warmup.

**Warmup** (first 5-10 epochs): Linear ramp from 1e-6 to 1e-4. The decoder and heads are randomly initialized, so early gradients are noisy and large. Starting with a very low LR prevents destructive updates in the first few epochs before the decoder starts producing coherent features.

**Cosine decay** (remaining epochs): Smoothly decays the LR from 1e-4 toward ~1e-6 following a cosine curve. Unlike step decay (which drops LR abruptly at fixed epochs), cosine annealing provides continuous, gradual reduction that avoids sudden training dynamics shifts. Standard schedule for fine-tuning pretrained models.

**Total epochs**: 200-300. Heatmap models converge slower than classification models due to sparse supervision. At ~3-6 seconds per epoch (1000 images, batch size 16, RTX 5070), a full run is 10-30 minutes.

### Differential Learning Rates

**Encoder**: 0.1× the base learning rate (1e-5). The ImageNet-pretrained encoder already contains useful features. Aggressive updates would destroy them. A reduced LR allows the encoder to gently adapt its features toward the plate corner domain without losing its general-purpose edge and texture representations.

**Decoder + heads**: 1× the base learning rate (1e-4). These are initialized randomly and need full-speed learning.

### Batch Size

**Choice**: 16.

With 1000 images, this gives 62-63 batches per epoch. Large enough for stable gradient estimates (averaging over 16 images' worth of sparse corner signal), small enough to fit comfortably in GPU memory alongside the model. Larger batch sizes (32, 64) would be fine computationally but provide diminishing returns on gradient stability at this dataset size.

### Evaluation Metrics

During training, track the following on the validation set:

**Corner Pixel Error (CPE)**: The Euclidean distance in input pixels between the predicted corner (after argmax + offset) and the ground truth corner. Computed per-corner and averaged across all corners and images. This is the primary metric — it directly measures how accurate the homography will be.

**PCK (Percentage of Correct Keypoints)**: The fraction of predicted corners within a threshold distance of ground truth. Computed at multiple thresholds:
- PCK@2px: corners within 2 input pixels — excellent homography quality
- PCK@4px: corners within 4 input pixels — good, usable homography
- PCK@8px: corners within 8 input pixels — marginal, may introduce warp artifacts

**Target**: PCK@4px > 90% on the validation set. This means 90% of corner predictions are within 4 pixels of ground truth in the 80×256 letterbox space, which translates to sub-pixel accuracy in the original crop space after denormalization.

**Heatmap loss and offset loss (tracked separately)**: Used to diagnose whether λ needs adjustment. If one converges much faster than the other, rebalance λ.

**Mean confidence**: Average peak heatmap value across all corners. Consistently low values (<0.5) indicate model uncertainty. Very high values (>0.95) combined with high CPE indicate confident wrong predictions — a likely overfitting signal.

### Weight Initialization

**Encoder**: ImageNet-pretrained weights (from torchvision). Loaded at training start.

**Decoder transposed convolutions**: Kaiming (He) initialization — standard for conv layers with ReLU activation. Initializes weights such that the variance of activations is preserved through the layer, preventing vanishing or exploding signals in the randomly-initialized decoder.

**Decoder batch norm**: Weights initialized to 1.0, biases to 0.0 (standard).

**Heatmap head (1×1 conv)**: Bias initialized to -2.19 (which corresponds to sigmoid(-2.19) ≈ 0.1). This is the CenterNet initialization trick — it ensures the model initially predicts low confidence everywhere rather than random high activations, which prevents the focal loss from receiving huge gradients on the first forward pass that could destabilize early training.

**Offset head (1×1 conv)**: Zero-initialized weights and biases. The model initially predicts zero offset (center of each stride-4 cell), which is a reasonable default since corners are equally likely to be anywhere within a cell.

### Gradient Clipping

**Max gradient norm**: 10.0. Prevents rare large gradients (from unusual training samples or the sparse focal loss) from causing destructive weight updates. This is a safety net that should rarely activate during normal training.

### Regularization

**Weight decay**: 1e-4 (via AdamW). Provides L2 regularization on all parameters, discouraging large weights that would indicate overfitting.

**Data augmentation**: The primary regularization mechanism. Defined in Part 3. With only 1000-2000 images, augmentation is critical to prevent overfitting and is expected to contribute more to generalization than any explicit regularization technique.

**No dropout**: Dropout is not standard in fully convolutional architectures for dense prediction. The spatial structure of heatmaps means dropping random activations would create holes in the prediction that are inconsistent with the Gaussian target structure.

### Data Budget

**Minimum viable**: ~500 well-annotated, diverse plate crops with aggressive augmentation.

**Comfortable**: 1000-2000 images covering the deployment variation space (viewing angles, lighting, plate types, motion blur, compression artifacts).

**Diminishing returns**: 5000+ images for this specific task and model size.

**Bootstrap strategy**: Use the existing YOLO-Pose model to generate pseudo-labels on a large batch of crops, then manually verify and correct the worst predictions. This accelerates annotation significantly compared to labeling from scratch.

### Training/Validation Split

**Split**: 80/20 (800 train, 200 val for a 1000-image dataset). Stratify by viewing angle and lighting condition if metadata is available. The validation set must represent deployment conditions — it should not be easier or more uniform than what the model will encounter in production.

### Part 2 Decisions Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Gaussian sigma | Fixed 1.5 | Tight peaks for precise localization on the 20-pixel-tall heatmap. Prevents vertical corner overlap on foreshortened plates. Fixed is appropriate because letterbox normalizes crop sizes. Focal loss handles the resulting sparsity (~2.2% positive pixels). |
| Heatmap loss | Focal loss (alpha=2, beta=4) | Purpose-built for extreme class imbalance on heatmaps. Down-weights easy background, Gaussian-modulates penalty near corners. Standard from CornerNet/CenterNet. |
| Offset loss | Smooth L1 (Huber) | Outlier-robust (L1 for large errors), smooth gradients near zero (L2). Computed only at 4 GT corner locations per image. |
| Loss weight λ | 1.0 (starting, tuned over 2-3 runs) | Balances heatmap focal loss with offset Smooth L1. Adjusted based on per-component loss curves on validation set. |
| Optimizer | AdamW (LR=1e-4, WD=1e-4) | Adaptive per-parameter LR for pretrained encoder + random decoder. Decoupled weight decay. |
| LR schedule | Cosine annealing + 5-10 epoch warmup | Warmup protects random decoder. Cosine provides smooth decay. |
| Differential LR | Encoder 0.1×, decoder/heads 1× | Preserve pretrained features while decoder learns freely. |
| Batch size | 16 | Stable gradients, fits GPU memory. 62 batches/epoch at 1000 images. |
| Training duration | 200-300 epochs (~10-30 min total) | Heatmap models need more epochs. Fast enough for iterative λ tuning. |
| Heatmap head bias init | -2.19 (sigmoid ≈ 0.1) | CenterNet trick — low initial confidence prevents huge early focal loss gradients. |
| Offset head init | Zero weights and biases | Predicts cell center by default. Reasonable starting point. |
| Gradient clipping | Max norm 10.0 | Safety net against rare large gradients from sparse focal loss. |
| Primary metric | PCK@4px > 90% | Directly measures corner accuracy. 4px threshold ensures usable homography. |
| Data budget | 1000-2000 diverse images (min ~500) | Pretrained encoder reduces data needs. Augmentation extends effective dataset. Bootstrap with YOLO-Pose pseudo-labels. |
| Train/val split | 80/20, stratified | Validation must represent deployment conditions. |

---

## Part 3: Training Process & Data Pipeline

### Overview

This section defines the complete training process: how data is annotated and loaded, how augmentations are applied with correct keypoint tracking, how the training loop runs with interactive control (curriculum phases, encoder freeze/unfreeze, lambda tuning), and how monitoring via W&B drives decisions.

### Dataset Class

The PyTorch Dataset class handles:
1. Load crop image and its 4 corner coordinates from annotations.json
2. Apply augmentations (geometric + photometric), transforming both image and corners
3. Letterbox the augmented image to 80×256, transforming corners to letterbox space
4. Reorder corners into canonical TL, TR, BR, BL via centroid-angle sorting
5. Render ground truth Gaussian heatmaps (4 channels, sigma=1.5) from the letterbox-space corners
6. Compute offset targets at each corner's heatmap grid position
7. Normalize image (uint8 → float32 / 255.0, HWC → CHW)
8. Return: image tensor, heatmap targets, offset targets, offset mask (which grid positions have valid offset targets)

### Augmentation Pipeline

All geometric augmentations transform both the image and the corner coordinates simultaneously. Incorrect coordinate tracking produces wrong ground truth and the model will not converge — this is the most critical implementation requirement.

Augmentation parameters are calibrated from analysis of real drone footage at 200ft altitude, including daytime, nighttime, and oblique-angle captures from the Firefly deployment environment.

#### Phase 1: No Augmentation (Frozen Encoder, Epochs 0 to ~20-60)

No augmentation. Clean images only. The decoder learns basic corner structure from pristine data with fixed ImageNet encoder features. Only preprocessing is applied: letterbox to 80×256, normalize.

#### Phase 2: Moderate Augmentation (Post-Unfreeze)

Activated when the encoder is unfrozen. Simulates natural variation within the observed data distribution.

| Augmentation | Parameters | Probability | Keypoint Impact |
|-------------|-----------|------------|-----------------|
| Horizontal flip | — | 50% | x → width - x; remap TL↔TR, BL↔BR |
| Brightness jitter | ±30% | 80% | None (photometric) |
| Contrast jitter | ±20% | 80% | None (photometric) |
| Saturation jitter | ±20% | 50% | None (photometric) |
| Small rotation | ±5° around crop center | 50% | Rotate corner coords around same center |
| Scale jitter | 0.9× to 1.1× | 50% | Scale corner coords from center |
| Gaussian noise | sigma 5-10 | 30% | None (photometric) |

#### Phase 3: Full Drone-Realistic Augmentation

Activated when Phase 2 PCK@4px target (>75%) is met. Everything from Phase 2, plus harder augmentations that simulate deployment edge cases:

| Augmentation | Parameters | Probability | Keypoint Impact |
|-------------|-----------|------------|-----------------|
| Heavier rotation | ±15° | 50% | Rotate corner coords |
| Perspective jitter | Random quadrilateral warp, ±10-15° simulated view angle | 40% | Transform corners through same homography matrix |
| Motion blur | Kernel size 3-7px, random direction | 30% | None (photometric) |
| Gaussian blur | sigma 0.5-1.5 | 30% | None (photometric) |
| Heavy brightness + gamma | Brightness ±50%, gamma 0.5-1.5 | 40% | None (photometric) |
| JPEG compression | Quality 30-70 | 40% | None (photometric) |
| Heavy Gaussian noise | sigma 10-25 | 20% | None (photometric) |
| Random rectangular occlusion | 1-2 rectangles, 5-15% of crop area, gray fill | 20% | None (occlusion in buffer region) |
| Scale jitter widened | 0.8× to 1.2× | 50% | Scale corner coords |
| Night simulation (daytime images only) | Brightness ×0.2-0.4, gamma 1.5-2.0, noise sigma 15-30 | 15% | None (photometric) |

Night simulation supplements real night annotations to ensure the model sees enough low-light examples, matching the 30% night deployment ratio.

#### Excluded Augmentations

| Excluded | Reason |
|----------|--------|
| Vertical flip | Plates are never upside-down from a drone |
| Rotation > 20° | Unrealistic for drone viewing geometry |
| Color channel swap / grayscale | Deployment is always color; plate-to-vehicle color contrast is a useful feature |
| Cutout directly on the plate area | Creates impossible targets where corners are invisible but ground truth says they exist |
| Mixup / CutMix | Blending plate crops creates unrealistic images with overlapping corner targets |

#### Keypoint Coordinate Tracking

For each geometric augmentation, corners are transformed through the identical transformation applied to the image:

- **Horizontal flip**: `x_new = image_width - x_old`. Then remap corner identities: swap TL↔TR and BL↔BR in heatmap channel assignment.
- **Rotation**: 2D rotation matrix around crop center: `[x_new, y_new] = R @ [x - cx, y - cy] + [cx, cy]`
- **Scale**: From crop center: `x_new = cx + (x - cx) * scale`
- **Perspective warp**: Apply the same 3×3 homography matrix H to corners: `[x_new, y_new, w] = H @ [x, y, 1]`, normalize: `x_final = x_new/w, y_final = y_new/w`
- **Photometric augmentations**: No coordinate change needed.

**Boundary check**: After all geometric augmentations, verify all 4 corners remain within image bounds. If any corner falls outside, discard the augmented sample and re-augment (resample). This prevents training on targets where the plate is partially warped out of frame.

### Training Loop & Interactive Management

#### Control System

The training loop is governed by two mechanisms operating together:

**1. Automated callbacks** (default behavior, no intervention required):

| Condition | Action |
|-----------|--------|
| Epoch ≥ 20 AND val loss stagnant (< 1% improvement over 15 epochs) AND encoder frozen | Unfreeze encoder (0.1× LR), enable Phase 2 augmentation, enable HEM. Save pre-unfreeze checkpoint. W&B alert. |
| Epoch ≥ 60 AND encoder still frozen | Force unfreeze (same actions as above). Ceiling prevents indefinite frozen training. |
| Phase 2 active AND val PCK@4px > 75% for 5 consecutive epochs | Advance to Phase 3 augmentation. W&B alert. |
| New best validation PCK@4px achieved | Save checkpoint as `best.pt`. |
| Every 10 epochs | Save numbered checkpoint for rollback capability. |

**2. Manual override** (via `training_config.json`, polled at the start of every epoch):

```json
{
  "encoder_frozen": true,
  "augmentation_phase": 1,
  "hard_example_mining": false,
  "lambda_offset": 1.0,
  "learning_rate": 1e-4,
  "stop_after_epoch": 300,
  "pause": false
}
```

Any field can be changed while training runs — changes take effect at the next epoch boundary.

Setting `"pause": true` causes the loop to save a checkpoint, log metrics, then poll the config file every 30 seconds until `pause` is set back to `false`. This enables W&B inspection and parameter adjustment mid-run without losing any training state.

**Checkpoint-before-change**: Before any major state change (unfreeze, phase advance, lambda change — whether automated or manual), the system saves a checkpoint. This enables rollback if a change worsens performance: restore the pre-change checkpoint, adjust the config, and continue.

#### Hard Example Mining

Activated when encoder is unfrozen (by callback or manual override). Not active during frozen encoder phase — "hard" examples during frozen training are hard because the encoder isn't adapted, not because the images are genuinely difficult.

**Implementation**: After each epoch, record per-image total loss. Compute sampling weight per image: `weight = clamp((loss / mean_loss), 0.5, 3.0)`. Use WeightedRandomSampler for next epoch's DataLoader.

- **Floor (0.5×)**: Easy images still appear to prevent catastrophic forgetting
- **Ceiling (3×)**: Prevents a single badly-annotated outlier from dominating training

#### Epoch Flow

```
For each epoch:
  1. Read training_config.json for manual overrides
  2. If pause=true: save checkpoint, wait until resumed
  3. Check automated callback conditions
  4. If state change triggered: save pre-change checkpoint, apply, log to W&B
  5. Build DataLoader with current augmentation phase + HEM weights
  6. For each batch:
     a. Load images + corners from dataset
     b. Apply augmentations (current phase), transform corners
     c. Letterbox to 80×256, transform corners to letterbox space
     d. Reorder corners to canonical TL, TR, BR, BL
     e. Render Gaussian heatmap targets (sigma=1.5)
     f. Compute offset targets at corner grid positions
     g. Forward pass → predicted heatmaps + offsets
     h. Compute focal loss (heatmaps) + lambda × smooth L1 (offsets)
     i. Backward pass, gradient clipping (max norm 10), optimizer step
     j. Log batch loss to W&B
  7. Validation pass (no augmentation):
     a. Forward pass on all val images
     b. Extract corners via argmax + offset for each image
     c. Compute CPE, PCK@2/4/8, mean confidence, per-component losses
     d. Log all metrics to W&B
     e. If best PCK@4px: save best.pt
  8. Log image visualizations (every 10 epochs)
  9. Update HEM weights from this epoch's per-image losses
  10. Save periodic checkpoint (every 10 epochs)
```

### W&B Monitoring

**Setup**: `pip install wandb && wandb login`. In training script: `wandb.init(project="corner-cnn")` at start, `wandb.log({...})` for all metrics. Total setup: under 5 minutes.

**Scalars logged every epoch**:
- `train/heatmap_loss`, `train/offset_loss`, `train/total_loss`
- `val/heatmap_loss`, `val/offset_loss`, `val/total_loss`
- `val/cpe_mean` (mean corner pixel error)
- `val/pck_2px`, `val/pck_4px`, `val/pck_8px`
- `val/mean_confidence`
- `lr` (current learning rate)
- `phase` (current curriculum phase: 1, 2, or 3)
- `encoder_frozen` (boolean as 0/1)

**Image visualizations logged every 10 epochs**:
- **Corner overlay grid**: 8 val images with predicted corners (green dots) and GT corners (red dots). Immediately shows if predictions are accurate, systematically offset, or failing on specific types.
- **Heatmap renders**: 4 val images with predicted heatmap channels as colormap overlay. Reveals whether peaks are tight and localized or broad and mushy.
- **Worst-case images**: 4 val images with highest CPE, with corners overlaid. Shows what the model struggles with — informs whether to add augmentations, get more data, or accept failure and rely on confidence gate.

**W&B alerts** (optional email/Slack notification):
- Val loss stagnant for 20 epochs → "Consider unfreeze or lambda adjustment"
- PCK@4px crossed 75% → "Phase 2 target met, advancing to Phase 3"
- PCK@4px crossed 90% → "Production target reached"
- Mean confidence < 0.3 → "Model may be destabilized, check recent changes"

**Cross-run comparison**: Lambda tuning runs appear as separate lines in W&B. Dashboard overlays val/pck_4px across runs for direct comparison.

### Expected Training Timeline

| Phase | Epochs (approx) | Duration | What Happens |
|-------|---------|----------|-------------|
| Frozen encoder, no augmentation | 0-30 | ~3 min | Decoder learns basic corners from clean data. Val loss decreases then plateaus. |
| Unfreeze trigger | ~30 | — | Callback detects plateau → unfreeze encoder, enable Phase 2 + HEM. Checkpoint saved. |
| Phase 2, moderate augmentation | 30-80 | ~5 min | Encoder adapts features. Moderate augmentation. Loss drops again. PCK@4px climbs toward 75%. |
| Lambda check | ~80-100 | — | Review heatmap vs offset loss balance on W&B. Adjust lambda if needed (restart from unfreeze checkpoint, not from scratch). |
| Phase 3 trigger | ~80-100 | — | PCK@4px > 75% sustained → advance to full drone-realistic augmentation. |
| Phase 3, full augmentation | 100-250 | ~15 min | Temporary loss spike from harder augmentation. Model adapts. PCK@4px climbs toward 90%+. |
| Late training | 250-300 | ~5 min | LR very low (cosine decay). Fine-tuning. Marginal improvements. |
| **Total** | **300** | **~30 min** | **Best checkpoint = highest val PCK@4px across all 300 epochs.** |

### Convergence Criteria

**Training complete**: After 300 epochs. Best checkpoint (`best.pt`) selected by highest validation PCK@4px regardless of which epoch it occurred at.

**Production-ready**: `best.pt` achieves PCK@4px > 90% on validation set. If not met after 300 epochs with lambda tuning, the likely bottleneck is data diversity or annotation quality — not architecture or training procedure.

**Overfitting indicators**: Training loss decreasing while validation loss increases or plateaus. Mean confidence very high (>0.95) combined with high CPE. Worst-case visualizations showing memorization rather than generalization.

### Decision Checklist (Review Every 50 Epochs)

| Check | If Yes |
|-------|--------|
| Is validation loss still decreasing? | Keep training. |
| Val loss plateaued 50+ epochs, encoder frozen? | Unfreeze encoder. |
| Val loss plateaued 50+ epochs, encoder unfrozen? | Training is converged for current phase. |
| Heatmap loss converging much faster than offset loss? | Increase lambda, restart from unfreeze checkpoint. |
| Offset loss converging much faster than heatmap loss? | Decrease lambda, restart from unfreeze checkpoint. |
| Current phase PCK target met? | Advance to next augmentation phase. |
| Corner overlay shows systematic offset (all predictions shifted one direction)? | Annotation consistency issue — review and correct annotations. |
| High confidence + high CPE? | Overfitting — add augmentation or get more data. |
| Worst-case images all same type (e.g., all night shots)? | Need more training data of that specific type. |

### Part 3 Decisions Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Curriculum | 3 phases: none → moderate → full drone-realistic | Phased difficulty prevents hard augmentation from confusing untrained decoder. |
| Phase transitions | Automated callbacks + manual config override | Callbacks handle common case. Manual override for W&B-informed intervention. Pause/resume for inspection. |
| Encoder freeze | Min 20 epochs, max 60, unfreeze on val plateau | Protects pretrained features while decoder stabilizes. Plateau detection automates timing. |
| Hard example mining | Post-unfreeze only, loss-proportional (0.5×-3× weight) | Focuses on genuinely hard cases (not encoder-limitation cases). Floor/ceiling prevent degenerate sampling. |
| Horizontal flip | 50%, with corner identity remap | Doubles effective data. Highest-value single augmentation. |
| Night simulation | 15% in Phase 3, aggressive darkening + noise on daytime images | Supplements real night data for ≥30% low-light training. |
| Perspective jitter | ±10-15°, 40% in Phase 3 | Simulates oblique drone viewing angles observed in deployment. |
| Boundary check | Discard + re-augment if any corner exits image | Prevents invalid training targets. |
| Interactive control | Config file polled per epoch + pause/resume + checkpoint-before-change | Full mid-training adjustability. Rollback from bad decisions. |
| Monitoring | W&B: scalars, image visualizations, alerts, cross-run comparison | Complete visibility. Minimal setup (~5 min). Alerts reduce active monitoring. |
| Training duration | 300 epochs, ~30 min total | Full run avoids missing improvements after phase transitions. Best checkpoint selected. |
| Production target | PCK@4px > 90% | Corner accuracy sufficient for usable homography to improve OCR downstream. |
