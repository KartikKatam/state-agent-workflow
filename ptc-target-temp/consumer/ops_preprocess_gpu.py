"""GPU preprocessing pipeline for license plate image normalization.

Provides spatial normalization (homography warp or direct resize) and
photometric correction (gain, gamma, contrast, denoise, sharpen, CLAHE).
Policy/execution separation: this module ONLY executes recipe instructions
— it never makes correction decisions.

GPU Pipeline Order:
    ingest → geometry → photometric → CLAHE → post-metrics → normalize
"""

from __future__ import annotations

import logging

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from kornia.enhance import equalize_clahe

from .config import ConsumerConfig
from .models import (
    EnhancedBatchSelection,
    PostProcessingMeta,
    ProcessorOutput,
    RichQualityMetrics,
)
from .ops_recipe import (
    IDX_CLAHE,
    IDX_CLAHE_CLIP,
    IDX_CLAHE_TILES,
    IDX_CONTRAST,
    IDX_DENOISE,
    IDX_GAIN,
    IDX_GAMMA,
    IDX_SHARPEN,
    IDX_TIGHT_CROP,
    IDX_WARP_ENABLE,
    IDX_WARP_MARGIN,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GPU pipeline: safe ingest
# ---------------------------------------------------------------------------


def _safe_ingest(
    crops: list[np.ndarray],
    device: torch.device,
) -> list[torch.Tensor]:
    """Convert uint8 HWC crop arrays to float32 CHW tensors on device.

    Each crop is copied before conversion to avoid aliasing the input arrays.

    Args:
        crops: List of (H_i, W_i, 3) uint8 numpy arrays.
        device: Target torch device.

    Returns:
        List of (3, H_i, W_i) float32 tensors on device, values in [0, 1].
    """
    logger.debug("safe_ingest: %d crops, device=%s", len(crops), device)
    result: list[torch.Tensor] = []
    for crop in crops:
        arr = crop.copy().astype(np.float32) / 255.0
        tensor = torch.from_numpy(arr.transpose(2, 0, 1)).to(device)
        result.append(tensor)
    return result


# ---------------------------------------------------------------------------
# GPU pipeline: geometry
# ---------------------------------------------------------------------------


def _perspective_grid(
    H: torch.Tensor,
    dst_h: int,
    dst_w: int,
    src_h: int,
    src_w: int,
    device: torch.device,
) -> torch.Tensor:
    """Create a grid_sample-compatible grid from a perspective transform matrix.

    Args:
        H: 3x3 perspective matrix mapping source → destination pixel coords.
        dst_h: Destination grid height.
        dst_w: Destination grid width.
        src_h: Source image height (for normalization).
        src_w: Source image width (for normalization).
        device: Torch device.

    Returns:
        (1, dst_h, dst_w, 2) sampling grid normalized to [-1, 1].
    """
    H_inv = torch.linalg.inv(H)

    ys = torch.arange(dst_h, dtype=torch.float32, device=device)
    xs = torch.arange(dst_w, dtype=torch.float32, device=device)
    grid_y, grid_x = torch.meshgrid(ys, xs, indexing="ij")
    ones = torch.ones_like(grid_x)

    # (dst_h, dst_w, 3) homogeneous coordinates
    dst_coords = torch.stack([grid_x, grid_y, ones], dim=-1)

    # Map destination → source via H^(-1)
    src_coords = torch.einsum("ij,...j->...i", H_inv, dst_coords)
    src_coords = src_coords[..., :2] / src_coords[..., 2:3].clamp(min=1e-8)

    # Normalize to [-1, 1] for grid_sample (align_corners=True)
    src_coords[..., 0] = 2.0 * src_coords[..., 0] / max(src_w - 1, 1) - 1.0
    src_coords[..., 1] = 2.0 * src_coords[..., 1] / max(src_h - 1, 1) - 1.0

    return src_coords.unsqueeze(0)


def _warp_perspective(
    img: torch.Tensor,
    quad: list[tuple[float, float]],
    margin: float,
    tight_crop: bool,
    h_out: int,
    w_out: int,
) -> torch.Tensor:
    """Warp image via perspective transform and crop to output size.

    Args:
        img: (3, H, W) float32 tensor.
        quad: 4 keypoints in (TL, TR, BR, BL) order, pixel coords.
        margin: Fractional warp margin relative to output size.
        tight_crop: Whether to remove margin after warp.
        h_out: Final output height.
        w_out: Final output width.

    Returns:
        (3, h_out, w_out) float32 tensor.
    """
    _, src_h, src_w = img.shape
    device = img.device

    margin_x = int(round(margin * w_out))
    margin_y = int(round(margin * h_out))

    int_w = w_out + 2 * margin_x
    int_h = h_out + 2 * margin_y

    # Source points: quad is already (TL, TR, BR, BL) — canonical order
    src_pts = np.array(quad, dtype=np.float32)

    # Destination rectangle (plate maps to inner region, margin around)
    dst_pts = np.array(
        [
            [margin_x, margin_y],
            [int_w - 1 - margin_x, margin_y],
            [int_w - 1 - margin_x, int_h - 1 - margin_y],
            [margin_x, int_h - 1 - margin_y],
        ],
        dtype=np.float32,
    )

    # Compute perspective matrix (CPU, one-time per image)
    H = cv2.getPerspectiveTransform(src_pts, dst_pts)  # type: ignore[arg-type]
    H_t = torch.tensor(H, dtype=torch.float32, device=device)

    grid = _perspective_grid(H_t, int_h, int_w, src_h, src_w, device)

    warped = F.grid_sample(
        img.unsqueeze(0),
        grid,
        mode="bilinear",
        padding_mode="zeros",
        align_corners=True,
    )

    # Tight crop: remove margin pixels
    if tight_crop and (margin_x > 0 or margin_y > 0):
        warped = warped[:, :, margin_y : margin_y + h_out, margin_x : margin_x + w_out]
    elif warped.shape[2] != h_out or warped.shape[3] != w_out:
        warped = F.interpolate(warped, size=(h_out, w_out), mode="bilinear", align_corners=False)

    return warped.squeeze(0).clamp(0.0, 1.0)


def _apply_geometry(
    images: list[torch.Tensor],
    recipe: torch.Tensor,
    quads: list[list[tuple[float, float]] | None],
    cfg: ConsumerConfig,
) -> torch.Tensor:
    """Apply geometry normalization: homography warp or direct resize.

    Two paths per image based on recipe values:
      - Path A (warp_enable=1, valid quad): perspective warp + tight crop + resize
      - Path B (warp_enable=0 or no quad): direct bilinear resize

    Args:
        images: List of (3, H_i, W_i) float32 tensors on device.
        recipe: (N, 12) recipe tensor on device.
        quads: Per-image quad keypoints (TL, TR, BR, BL) or None.
        cfg: Consumer configuration with output size.

    Returns:
        (N, 3, H_out, W_out) stacked tensor, values in [0, 1].
    """
    h_out = cfg.recipe_output_height
    w_out = cfg.recipe_output_width

    results: list[torch.Tensor] = []
    for i, img in enumerate(images):
        warp_enable = recipe[i, IDX_WARP_ENABLE].item()
        quad = quads[i]

        if warp_enable > 0.5 and quad is not None:
            margin = recipe[i, IDX_WARP_MARGIN].item()
            tight_crop = recipe[i, IDX_TIGHT_CROP].item() > 0.5
            result = _warp_perspective(img, quad, margin, tight_crop, h_out, w_out)
            logger.debug(
                "geometry[%d]: homography warp, margin=%.3f, tight_crop=%s",
                i,
                margin,
                tight_crop,
            )
        else:
            result = F.interpolate(
                img.unsqueeze(0),
                size=(h_out, w_out),
                mode="bilinear",
                align_corners=False,
            ).squeeze(0)
            logger.debug("geometry[%d]: direct resize to %dx%d", i, w_out, h_out)

        results.append(result)

    return torch.stack(results)


# ---------------------------------------------------------------------------
# GPU pipeline: photometric ops
# ---------------------------------------------------------------------------


def _apply_photometric_ops(
    batch: torch.Tensor,
    recipe: torch.Tensor,
    cfg: ConsumerConfig,
) -> torch.Tensor:
    """Apply photometric corrections in fixed order: gain → gamma → contrast → denoise → sharpen.

    All ops use identity-value-means-no-op pattern (no branching).
    Clamps output to [0, 1] after all ops.

    Args:
        batch: (N, 3, H, W) float32 tensor in [0, 1].
        recipe: (N, 12) recipe tensor on same device.
        cfg: Consumer configuration (for contrast_steepness).

    Returns:
        (N, 3, H, W) float32 tensor, clamped to [0, 1].
    """
    n = batch.shape[0]
    out = batch.clone()

    # Per-row recipe values as (N, 1, 1, 1) for broadcasting
    gain = recipe[:, IDX_GAIN].view(n, 1, 1, 1)
    gamma = recipe[:, IDX_GAMMA].view(n, 1, 1, 1)
    contrast = recipe[:, IDX_CONTRAST].view(n, 1, 1, 1)
    denoise = recipe[:, IDX_DENOISE].view(n, 1, 1, 1)
    sharpen = recipe[:, IDX_SHARPEN].view(n, 1, 1, 1)

    # 1. Gain: multiply
    out = out * gain

    # 2. Gamma: power (clamp to avoid NaN with fractional powers of negatives)
    out = out.clamp(min=0.0).pow(gamma)

    # 3. Contrast: sigmoid S-curve blend
    steepness = cfg.contrast_steepness
    s_curve = torch.sigmoid(steepness * (out - 0.5))
    out = out + contrast * (s_curve - out)

    # 4. Denoise: box blur blend (kernel_size=5)
    if denoise.max().item() > 0:
        blurred = F.avg_pool2d(
            F.pad(out, (2, 2, 2, 2), mode="reflect"),
            kernel_size=5,
            stride=1,
            padding=0,
        )
        out = out + denoise * (blurred - out)

    # 5. Sharpen: unsharp mask blend (kernel_size=5)
    if sharpen.max().item() > 0:
        blurred_s = F.avg_pool2d(
            F.pad(out, (2, 2, 2, 2), mode="reflect"),
            kernel_size=5,
            stride=1,
            padding=0,
        )
        unsharp = out + (out - blurred_s)
        out = out + sharpen * (unsharp - out)

    out = out.clamp(0.0, 1.0)
    logger.debug("photometric_ops: %d images, gain range=[%.2f,%.2f]", n, gain.min(), gain.max())
    return out


# ---------------------------------------------------------------------------
# GPU pipeline: CLAHE
# ---------------------------------------------------------------------------


def _apply_clahe_gpu(
    batch: torch.Tensor,
    recipe: torch.Tensor,
    is_enhanced: torch.Tensor,
    cfg: ConsumerConfig,
) -> torch.Tensor:
    """Apply CLAHE to enhanced rows where clahe > 0 using Kornia.

    For each qualifying row: extract luminance, apply equalize_clahe,
    blend via luminance ratio for color preservation.

    Args:
        batch: (N, 3, H, W) float32 tensor in [0, 1].
        recipe: (N, 12) recipe tensor on same device.
        is_enhanced: (N,) bool tensor marking enhanced rows.
        cfg: Consumer configuration.

    Returns:
        (N, 3, H, W) float32 tensor with CLAHE applied to qualifying rows.
    """
    out = batch.clone()
    n = batch.shape[0]

    for i in range(n):
        clahe_blend = recipe[i, IDX_CLAHE].item()
        if not is_enhanced[i].item() or clahe_blend <= 0:
            continue

        clip_limit = recipe[i, IDX_CLAHE_CLIP].item()
        grid_size = int(recipe[i, IDX_CLAHE_TILES].item())

        # Extract single image and compute luminance
        img = out[i : i + 1]  # (1, 3, H, W)
        luma = 0.299 * img[:, 0:1] + 0.587 * img[:, 1:2] + 0.114 * img[:, 2:3]

        # Apply CLAHE to luminance
        luma_eq = equalize_clahe(
            luma,
            clip_limit=clip_limit,
            grid_size=(grid_size, grid_size),
        )

        # Luminance ratio for color preservation
        ratio = (luma_eq + 1e-6) / (luma + 1e-6)

        # Blend: scale ratio by blend factor
        blended_ratio = 1.0 + clahe_blend * (ratio - 1.0)
        out[i : i + 1] = (img * blended_ratio).clamp(0.0, 1.0)

        logger.debug(
            "clahe[%d]: blend=%.2f, clip=%.1f, tiles=%d",
            i,
            clahe_blend,
            clip_limit,
            grid_size,
        )

    return out


# ---------------------------------------------------------------------------
# GPU pipeline: post-processing metrics
# ---------------------------------------------------------------------------


def _compute_post_metrics(
    batch: torch.Tensor,
    input_metrics: list[RichQualityMetrics],
    target_luma: float,
) -> list[PostProcessingMeta]:
    """Compute post-processing quality metrics while pixels are in [0, 1].

    Metrics are non-gating in V1 (logging only).

    Args:
        batch: (N, 3, H, W) float32 tensor in [0, 1].
        input_metrics: Per-row input quality metrics (for delta computation).
        target_luma: Target luminance in [0, 255] scale.

    Returns:
        List of PostProcessingMeta, one per row.
    """
    n = batch.shape[0]
    target_01 = target_luma / 255.0
    results: list[PostProcessingMeta] = []

    for i in range(n):
        img = batch[i]  # (3, H, W)
        luma = 0.299 * img[0] + 0.587 * img[1] + 0.114 * img[2]

        post_luma_mean = luma.mean().item()

        # Global contrast: p90 - p10 of luminance
        p10 = torch.quantile(luma, 0.1).item()
        p90 = torch.quantile(luma, 0.9).item()
        post_global_contrast = p90 - p10

        # Deltas relative to input
        input_luma_01 = input_metrics[i].luminance_mean / 255.0
        input_contrast_01 = input_metrics[i].global_contrast / 255.0

        luma_delta = abs(post_luma_mean - target_01) - abs(input_luma_01 - target_01)
        contrast_delta = post_global_contrast - input_contrast_01

        results.append(
            PostProcessingMeta(
                post_luma_mean=post_luma_mean,
                post_global_contrast=post_global_contrast,
                luma_delta=luma_delta,
                contrast_delta=contrast_delta,
            )
        )

    logger.debug("post_metrics: %d rows computed", n)
    return results


# ---------------------------------------------------------------------------
# GPU pipeline: entry point
# ---------------------------------------------------------------------------


def process_batch_gpu(
    batch: EnhancedBatchSelection,
    recipe_tensor: np.ndarray,
    is_enhanced: np.ndarray,
    cfg: ConsumerConfig,
    device: torch.device,
) -> ProcessorOutput:
    """GPU processing pipeline: ingest → geometry → photometric → CLAHE → post-metrics → normalize.

    Args:
        batch: Selected batch with base and enhancement ROIs.
        recipe_tensor: (N, 12) float32 recipe values.
        is_enhanced: (N,) bool array marking enhanced rows.
        cfg: Consumer configuration.
        device: Target torch device.

    Returns:
        ProcessorOutput with PARSeq-ready tensor in [-1, 1].
    """
    all_rois = batch.base_rois + batch.enhance_rois
    n = len(all_rois)
    h_out = cfg.recipe_output_height
    w_out = cfg.recipe_output_width

    # Empty batch guard
    if n == 0:
        logger.debug("process_batch_gpu: empty batch, returning zeros")
        return ProcessorOutput(
            batch_tensor=torch.empty(0, 3, h_out, w_out, dtype=torch.float32, device=device),
            is_enhanced=is_enhanced,
            post_meta=[],
        )

    # Extract crops and quads from batch
    crops = [roi.roi_fq.roi.crop_img for roi in all_rois]
    quads: list[list[tuple[float, float]] | None] = [roi.metrics.keypoints for roi in all_rois]

    # Safe ingest: uint8 HWC → float32 CHW on device
    images = _safe_ingest(crops, device)

    # Convert recipe to torch
    recipe_t = torch.tensor(recipe_tensor, dtype=torch.float32, device=device)

    # Apply geometry normalization
    batch_tensor = _apply_geometry(images, recipe_t, quads, cfg)
    logger.debug("process_batch_gpu: geometry done, %d images, shape=%s", n, batch_tensor.shape)

    # Apply photometric corrections
    batch_tensor = _apply_photometric_ops(batch_tensor, recipe_t, cfg)

    # Apply CLAHE to enhanced rows
    is_enhanced_t = torch.tensor(is_enhanced, dtype=torch.bool, device=device)
    batch_tensor = _apply_clahe_gpu(batch_tensor, recipe_t, is_enhanced_t, cfg)

    # Compute post-processing metrics (while still in [0, 1])
    input_metrics = [roi.metrics for roi in all_rois]
    post_meta = _compute_post_metrics(batch_tensor, input_metrics, cfg.target_luma)

    # PARSeq normalization: [0, 1] → [-1, 1]
    batch_tensor = batch_tensor * 2 - 1
    logger.debug(
        "process_batch_gpu: complete, %d images, shape=%s, range=[%.2f, %.2f]",
        n,
        batch_tensor.shape,
        batch_tensor.min().item(),
        batch_tensor.max().item(),
    )

    return ProcessorOutput(
        batch_tensor=batch_tensor.contiguous(),
        is_enhanced=is_enhanced,
        post_meta=post_meta,
    )
