"""
Fast GPU-accelerated quality analysis for Producer ROI crops.

Uses Tenengrad (Sobel-based gradient magnitude) for focus estimation,
plus simple brightness, contrast, and exposure metrics to gate low-quality
plates before buffer insertion.

GPU Acceleration Strategy:
    - Resize all ROIs to fixed size (48×160) on CPU using cv2.resize
    - Batch all resized BGR images as uint8 and transfer to GPU
      (4x smaller transfer vs float32 - critical for real-time)
    - Convert uint8→float32 on GPU (essentially free)
    - Convert BGR→grayscale on GPU using tensor operations
    - Compute gradients using F.conv2d with Sobel kernels (GPU)
    - All metrics computed as GPU tensor operations
    - Generate thumbnails on GPU
    - Single CPU transfer at the end for results

Performance:
    ~0.5-1.5ms for 2-3 ROIs on RTX 4090 (vs ~5-10ms CPU equivalent)

Each ROI also gets a small grayscale thumbnail for later similarity checks
in the buffer system.
"""

from __future__ import annotations

import cv2
import numpy as np
import torch
import torch.nn.functional as F

from .config import ProducerConfig
from .models import FastQualityMetrics, RoiFastQuality, RoiImage

# Module-level Sobel kernel cache (created once per device)
_SOBEL_X: torch.Tensor | None = None
_SOBEL_Y: torch.Tensor | None = None


def _get_sobel_kernels(device: torch.device) -> tuple[torch.Tensor, torch.Tensor]:
    """
    Get or create cached Sobel kernels for gradient computation.

    Args:
        device: Target GPU device for kernels

    Returns:
        (sobel_x, sobel_y) each shape [1, 1, 3, 3] on GPU
    """
    global _SOBEL_X, _SOBEL_Y

    if _SOBEL_X is None or _SOBEL_X.device != device:
        kx = torch.tensor([[1, 0, -1], [2, 0, -2], [1, 0, -1]], dtype=torch.float32, device=device)
        ky = torch.tensor([[1, 2, 1], [0, 0, 0], [-1, -2, -1]], dtype=torch.float32, device=device)
        _SOBEL_X = kx.view(1, 1, 3, 3)
        _SOBEL_Y = ky.view(1, 1, 3, 3)

    assert _SOBEL_X is not None and _SOBEL_Y is not None
    return _SOBEL_X, _SOBEL_Y


def _bgr_to_gray_gpu(bgr_batch: torch.Tensor) -> torch.Tensor:
    """
    Convert BGR batch to grayscale on GPU.

    Args:
        bgr_batch: [N, 3, H, W] float32 tensor on GPU with BGR channels

    Returns:
        [N, 1, H, W] float32 tensor on GPU with grayscale values

    Note:
        Uses OpenCV-style BGR→gray formula: 0.114*B + 0.587*G + 0.299*R
    """
    b = bgr_batch[:, 0:1, :, :]  # [N, 1, H, W]
    g = bgr_batch[:, 1:2, :, :]  # [N, 1, H, W]
    r = bgr_batch[:, 2:3, :, :]  # [N, 1, H, W]
    gray = 0.114 * b + 0.587 * g + 0.299 * r  # [N, 1, H, W]
    return gray


def _clamp01(x: float) -> float:
    """Clamp value to [0, 1] range."""
    return max(0.0, min(1.0, x))


def fast_quality_analyze_rois(
    rois: list[RoiImage],
    cfg: ProducerConfig,
    device: torch.device | None = None,
) -> list[RoiFastQuality]:
    """
    Run fast GPU-based quality analysis on a list of ROI crops.

    Pipeline:
        1. Resize all ROIs to fixed size (cfg.fast_quality_*_metrics) on CPU
        2. Batch all resized BGR images as uint8
        3. Single GPU transfer (uint8 = 4x smaller than float32)
        4. Convert uint8→float32 on GPU (essentially free)
        5. Convert BGR→grayscale on GPU
        6. Compute Sobel gradients via F.conv2d (Tenengrad focus + band edge)
        7. Compute brightness, contrast, exposure metrics (GPU tensor ops)
        8. Generate thumbnails for buffer similarity (cfg.fast_quality_*_thumb)
        9. Transfer metrics to CPU and build result objects

    Args:
        rois: List of RoiImage objects (cropped on CPU, BGR uint8)
        cfg: ProducerConfig with fast quality parameters
        device: Target GPU device (defaults to cuda if available)

    Returns:
        List of RoiFastQuality (same order as input rois)

    Performance:
        GPU (RTX 4090): ~0.5-1.5ms for 2-3 ROIs (batch processing)
        CPU equivalent: ~5-10ms (sequential processing)

    GPU Memory:
        ~2-5MB peak for typical batch (2-3 ROIs @ cfg.fast_quality_*_metrics)

    PCIe Transfer:
        uint8: 3 * Hq * Wq * N (small, scales with cfg.fast_quality_*_metrics)
        float32: 12 * Hq * Wq * N (4x larger)

    Note:
        Thumb size is decoupled from metrics size so similarity cost can be tuned
        independently; defaults match to avoid extra resize work.
        Does not perform any buffer logic or filtering across IDs.
        Each ROI is analyzed independently in parallel on GPU.
    """
    # Early return for empty input
    if not rois:
        return []

    # Default to CUDA if available
    if device is None:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Target sizes
    Hq = cfg.fast_quality_h_metrics
    Wq = cfg.fast_quality_w_metrics
    N = len(rois)

    # ===== STEP 1: RESIZE ALL ROIS TO FIXED SIZE ON CPU =====
    # cv2.resize is highly optimized and runs on CPU very fast
    # INTER_AREA is best for downsampling (anti-aliased)
    resized_bgr = np.zeros((N, Hq, Wq, 3), dtype=np.uint8)

    for i, roi in enumerate(rois):
        img = roi.crop_img  # H×W×3 BGR uint8
        # Resize to target size (stays uint8)
        resized = cv2.resize(img, (Wq, Hq), interpolation=cv2.INTER_AREA)
        resized_bgr[i] = resized

    # ===== STEP 2: BATCH AND TRANSFER TO GPU (uint8 = 4x smaller) =====
    # Convert to torch tensor: [N, H, W, 3] → [N, 3, H, W]
    bgr_batch_u8 = torch.from_numpy(resized_bgr).permute(0, 3, 1, 2)  # [N, 3, Hq, Wq] uint8
    # Single GPU transfer (only 1 byte per pixel)
    bgr_batch_u8 = bgr_batch_u8.to(device)

    # ===== STEP 3: CONVERT uint8 → float32 ON GPU (essentially free) =====
    bgr_batch = bgr_batch_u8.float() / 255.0  # [N, 3, Hq, Wq] float32, [0, 1]
    del bgr_batch_u8  # Free GPU memory

    # ===== STEP 4: BGR → GRAYSCALE ON GPU =====
    gray_batch = _bgr_to_gray_gpu(bgr_batch)  # [N, 1, Hq, Wq] on GPU
    del bgr_batch  # Free GPU memory

    # ===== STEP 5: COMPUTE SOBEL GRADIENTS (GPU CONVOLUTION) =====
    sobel_x, sobel_y = _get_sobel_kernels(device)
    Gx = F.conv2d(gray_batch, sobel_x, padding=1)  # [N, 1, Hq, Wq]
    Gy = F.conv2d(gray_batch, sobel_y, padding=1)  # [N, 1, Hq, Wq]
    Gmag = torch.sqrt(Gx * Gx + Gy * Gy + 1e-6)  # Gradient magnitude (Tenengrad)

    # ===== STEP 5.5: COMPUTE GRADIENT HISTOGRAM FEATURE VECTOR (GPU) =====
    # Gradient orientations (atan2 on GPU, unsigned angles for symmetry)
    angles = torch.atan2(Gy, Gx)  # [N, 1, Hq, Wq], range [-π, π]
    angles = torch.abs(angles)  # [0, π] for unsigned (plate edges symmetric)
    angles_deg = angles * 180.0 / np.pi  # Convert to degrees [0, 180]
    del Gx, Gy  # Free GPU memory

    # Compute weighted histogram per image (bin by angle, weight by magnitude)
    num_bins = cfg.fast_quality_gradient_histogram_bins
    bin_edges = torch.linspace(0.0, 180.0, steps=num_bins + 1, device=device)
    histograms = []
    for i in range(N):
        angle_flat = angles_deg[i].flatten()  # Flatten spatial dims
        mag_flat = Gmag[i].flatten()  # Gradient magnitudes as weights

        # Weighted histogram on GPU (torch.histc does not support weights)
        bin_idx = torch.bucketize(angle_flat, bin_edges, right=False) - 1
        bin_idx = bin_idx.clamp(0, num_bins - 1).long()
        hist = torch.zeros(num_bins, device=device)
        hist.scatter_add_(0, bin_idx, mag_flat)
        hist = hist / (hist.sum() + 1e-6)  # Normalize to probability distribution
        histograms.append(hist)

    gradient_hist_batch = torch.stack(histograms)  # [N, 16]
    del angles, angles_deg  # Free GPU memory

    # Spatial quadrant sharpness (3×3 grid for character band localization)
    # MC (middle-center) quadrant captures character band sharpness directly
    third_h = Hq // 3
    third_w = Wq // 3

    # Define 3×3 grid boundaries (row-major order: TL,TC,TR,ML,MC,MR,BL,BC,BR)
    quad_slices = [
        (0, third_h, 0, third_w),  # TL: Top-Left
        (0, third_h, third_w, 2 * third_w),  # TC: Top-Center
        (0, third_h, 2 * third_w, Wq),  # TR: Top-Right
        (third_h, 2 * third_h, 0, third_w),  # ML: Middle-Left
        (third_h, 2 * third_h, third_w, 2 * third_w),  # MC: Middle-Center (character band)
        (third_h, 2 * third_h, 2 * third_w, Wq),  # MR: Middle-Right
        (2 * third_h, Hq, 0, third_w),  # BL: Bottom-Left
        (2 * third_h, Hq, third_w, 2 * third_w),  # BC: Bottom-Center
        (2 * third_h, Hq, 2 * third_w, Wq),  # BR: Bottom-Right
    ]

    # Compute mean gradient magnitude per quadrant
    spatial_stats = []
    for y1, y2, x1, x2 in quad_slices:
        quad_mean = Gmag[:, :, y1:y2, x1:x2].mean(dim=(1, 2, 3))  # [N]
        spatial_stats.append(quad_mean)

    spatial_stats_batch = torch.stack(spatial_stats, dim=1)  # [N, 9]

    # Combine into feature vector [16 hist bins, 9 spatial stats] = [N, 25]
    feature_vectors_batch = torch.cat([gradient_hist_batch, spatial_stats_batch], dim=1)

    # L2 normalize to unit vectors (enables cosine similarity via dot product)
    feature_norms = torch.norm(feature_vectors_batch, p=2, dim=1, keepdim=True)  # [N, 1]
    feature_vectors_batch = feature_vectors_batch / (feature_norms + 1e-6)  # [N, 25]

    # ===== STEP 6: COMPUTE METRICS (GPU TENSOR OPS) =====

    # Brightness and contrast
    brightness_mean = gray_batch.mean(dim=(1, 2, 3))  # [N]
    contrast_std = gray_batch.std(dim=(1, 2, 3))  # [N]

    # Exposure fractions
    over_mask = gray_batch > cfg.fast_quality_over_threshold
    under_mask = gray_batch < cfg.fast_quality_under_threshold
    over_exposed_frac = over_mask.float().mean(dim=(1, 2, 3))  # [N]
    under_exposed_frac = under_mask.float().mean(dim=(1, 2, 3))  # [N]
    del over_mask, under_mask  # Free GPU memory

    # Focus: mean Tenengrad over entire image
    focus_tenengrad = Gmag.mean(dim=(1, 2, 3))  # [N]

    # Band edge: mean gradient in central character band
    # This captures sharpness where it matters most (character region)
    top = int(Hq * cfg.fast_quality_band_top_frac)
    bot = int(Hq * cfg.fast_quality_band_bottom_frac)
    band = Gmag[:, :, top:bot, :]  # [N, 1, band_h, Wq]
    band_edge_mean = band.mean(dim=(1, 2, 3))  # [N]
    del band, Gmag  # Free GPU memory

    # ===== STEP 7: GPU → CPU TRANSFER (single batched sync) =====
    # Transfer metrics + feature vectors + grayscale thumbnails

    brightness_mean_np = brightness_mean.detach().cpu().numpy()
    contrast_std_np = contrast_std.detach().cpu().numpy()
    over_exposed_np = over_exposed_frac.detach().cpu().numpy()
    under_exposed_np = under_exposed_frac.detach().cpu().numpy()
    focus_np = focus_tenengrad.detach().cpu().numpy()
    band_edge_np = band_edge_mean.detach().cpu().numpy()
    feature_vectors_np = feature_vectors_batch.detach().cpu().numpy()  # [N, 25] float32

    # Resize grayscale to thumbnail size for buffer similarity checks.
    # Defaults match metrics size to avoid extra work, but remain decoupled
    # so similarity cost can be tuned independently.
    Ht = cfg.fast_quality_h_thumb
    Wt = cfg.fast_quality_w_thumb
    if Ht == Hq and Wt == Wq:
        thumb_batch = gray_batch
    else:
        thumb_batch = F.interpolate(gray_batch, size=(Ht, Wt), mode="area")
    thumb_np = (
        (thumb_batch.squeeze(1) * 255.0).clamp(0, 255).byte().cpu().numpy()
    )  # [N, Ht, Wt] uint8

    if thumb_batch is not gray_batch:
        del thumb_batch
    del gray_batch, feature_vectors_batch  # Free GPU memory

    # ===== STEP 8: BUILD RESULT OBJECTS (CPU) =====

    results = []
    for i in range(N):
        # Extract raw metric values
        focus = float(focus_np[i])
        bright = float(brightness_mean_np[i])
        contrast = float(contrast_std_np[i])
        over = float(over_exposed_np[i])
        under = float(under_exposed_np[i])
        band = float(band_edge_np[i])
        thumb = thumb_np[i]  # Hq×Wq uint8 (48×160)
        feature_vec = feature_vectors_np[i]  # (25,) float32 - native GPU precision

        # Hard gate: reject if any threshold violated
        passes_min_quality = (
            (focus >= cfg.fast_quality_focus_min)
            and (contrast >= cfg.fast_quality_contrast_min)
            and (band >= cfg.fast_quality_band_edge_min)
            and (cfg.fast_quality_bright_min <= bright <= cfg.fast_quality_bright_max)
            and (over <= cfg.fast_quality_over_exposed_max)
            and (under <= cfg.fast_quality_under_exposed_max)
        )

        # Normalized components for scalar quality_score
        s_focus = _clamp01(focus / max(cfg.fast_quality_focus_good, 1e-6))
        s_contrast = _clamp01(contrast / max(cfg.fast_quality_contrast_good, 1e-6))
        s_band = _clamp01(band / max(cfg.fast_quality_band_edge_good, 1e-6))

        # Brightness score: penalize deviation from target
        bright_err = abs(bright - cfg.fast_quality_bright_target) / max(
            cfg.fast_quality_bright_tol, 1e-6
        )
        s_bright = 1.0 - _clamp01(bright_err)

        # Exposure penalty
        exp_penalty = over * cfg.fast_quality_over_weight + under * cfg.fast_quality_under_weight
        s_exposure = 1.0 - _clamp01(exp_penalty)

        # Weighted scalar quality_score
        quality_score = (
            cfg.fast_quality_w_focus * s_focus
            + cfg.fast_quality_w_contrast * s_contrast
            + cfg.fast_quality_w_band * s_band
            + cfg.fast_quality_w_bright * s_bright
            + cfg.fast_quality_w_exposure * s_exposure
        )

        # Build metrics object
        metrics_obj = FastQualityMetrics(
            focus_tenengrad=focus,
            brightness_mean=bright,
            contrast_std=contrast,
            over_exposed_frac=over,
            under_exposed_frac=under,
            band_edge_mean=band,
            gradient_histogram=feature_vec,
        )

        # Build RoiFastQuality result
        result = RoiFastQuality(
            roi=rois[i],
            metrics=metrics_obj,
            quality_score=quality_score,
            passes_min_quality=passes_min_quality,
            thumb_gray=thumb,  # Ht×Wt uint8 for buffer similarity checks
        )

        results.append(result)

    return results
