"""Evaluation metrics for corner CNN training (chunk-06).

Corner extraction from heatmaps (mirroring inference at ops_corner_cnn.py:167-200),
CPE computation, PCK@2/4/8px, batch evaluation, validation pass, and visualization helpers.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import cv2
import numpy as np
import torch

from training.config import TrainingConfig

logger = logging.getLogger(__name__)


# ── Corner extraction (mirrors inference) ────────────────────────────


def extract_corners_from_heatmap(
    output: np.ndarray | torch.Tensor,
    stride: int = 4,
) -> tuple[list[tuple[float, float]], list[float]]:
    """Extract 4 corners from raw CNN output via argmax + substride offset.

    Mirrors ops_corner_cnn.py:167-200 EXACTLY.

    Args:
        output: Shape (1, 12, H_out, W_out).
            Channels 0-3: heatmaps (TL, TR, BR, BL).
            Channels 4-11: per-corner substride offsets (dx_i, dy_i).
        stride: Spatial downsampling stride.

    Returns:
        (corners, confidences) — 4 (x, y) in letterbox pixel coords
        and 4 confidence values.
    """
    corners: list[tuple[float, float]] = []
    confidences: list[float] = []
    w_out = output.shape[3]

    for i in range(4):
        heatmap = output[0, i]  # (H_out, W_out)

        if isinstance(heatmap, torch.Tensor):
            flat_idx = int(torch.argmax(heatmap).item())
        else:
            flat_idx = int(np.argmax(heatmap))

        gy, gx = divmod(flat_idx, w_out)

        dx = float(output[0, 4 + 2 * i, gy, gx])
        dy = float(output[0, 4 + 2 * i + 1, gy, gx])

        x = gx * stride + dx
        y = gy * stride + dy
        conf = float(heatmap[gy, gx])

        corners.append((x, y))
        confidences.append(conf)

    return corners, confidences


# ── GT corner extraction (reverses offset encoding from chunk-04) ────


def extract_gt_corners_from_targets(
    hm_target: torch.Tensor,  # kept for API symmetry
    off_target: torch.Tensor,
    off_mask: torch.Tensor,
    stride: int,
) -> list[tuple[float, float]]:
    """Extract ground-truth corners from encoded targets.

    Reverses the offset encoding from generate_offset_target (chunk-04).

    Args:
        hm_target: (4, grid_h, grid_w) heatmap targets (unused, kept for API symmetry).
        off_target: (8, grid_h, grid_w) offset targets.
        off_mask: (4, grid_h, grid_w) binary mask indicating active cells.
        stride: Spatial stride.

    Returns:
        4 (x, y) corners in letterbox pixel coordinates.
    """
    corners: list[tuple[float, float]] = []

    for i in range(4):
        mask_i = off_mask[i]  # (grid_h, grid_w)

        if isinstance(mask_i, torch.Tensor):
            nonzero = torch.nonzero(mask_i, as_tuple=False)
            gy = int(nonzero[0, 0].item())
            gx = int(nonzero[0, 1].item())
        else:
            ys, xs = np.nonzero(mask_i)
            gy = int(ys[0])
            gx = int(xs[0])

        dx = float(off_target[2 * i, gy, gx])
        dy = float(off_target[2 * i + 1, gy, gx])

        x = gx * stride + dx
        y = gy * stride + dy
        corners.append((x, y))

    return corners


# ── CPE (Corner Pixel Error) ─────────────────────────────────────────


def compute_cpe(
    pred_corners: list[tuple[float, float]],
    gt_corners: list[tuple[float, float]],
) -> list[float]:
    """Compute Euclidean distance per corner.

    Args:
        pred_corners: 4 predicted (x, y) coordinates.
        gt_corners: 4 ground-truth (x, y) coordinates.

    Returns:
        4 CPE values (Euclidean distance per corner).
    """
    cpe: list[float] = []
    for (px, py), (gx, gy) in zip(pred_corners, gt_corners, strict=True):
        dist = math.sqrt((px - gx) ** 2 + (py - gy) ** 2)
        cpe.append(dist)
    return cpe


# ── PCK (Percentage of Correct Keypoints) ────────────────────────────


def compute_pck(
    pred_corners: list[tuple[float, float]],
    gt_corners: list[tuple[float, float]],
    thresholds: tuple[int, ...] = (2, 4, 8),
) -> dict[int, float]:
    """Compute PCK at multiple pixel thresholds.

    Args:
        pred_corners: 4 predicted (x, y) coordinates.
        gt_corners: 4 ground-truth (x, y) coordinates.
        thresholds: Pixel distance thresholds.

    Returns:
        Dict mapping threshold -> fraction of corners within threshold.
    """
    cpe = compute_cpe(pred_corners, gt_corners)
    pck: dict[int, float] = {}
    for t in thresholds:
        pck[t] = sum(1 for c in cpe if c < t) / 4.0
    return pck


# ── Batch evaluation ─────────────────────────────────────────────────


def evaluate_batch(
    model: torch.nn.Module,
    images: torch.Tensor,
    hm_targets: torch.Tensor,
    off_targets: torch.Tensor,
    off_masks: torch.Tensor,
    cfg: TrainingConfig,
) -> dict[str, Any]:
    """Evaluate a batch of images and return aggregated metrics.

    Args:
        model: The corner CNN model.
        images: (N, 3, H, W) input images.
        hm_targets: (N, 4, grid_h, grid_w) heatmap targets.
        off_targets: (N, 8, grid_h, grid_w) offset targets.
        off_masks: (N, 4, grid_h, grid_w) offset masks.
        cfg: Training configuration.

    Returns:
        Dict with mean_cpe, pck_2, pck_4, pck_8, mean_confidence,
        min_confidence, per_corner_cpe.
    """
    n = images.shape[0]

    with torch.no_grad():
        output = model(images)  # (N, 12, grid_h, grid_w)

    all_cpe: list[list[float]] = []
    all_pck: list[dict[int, float]] = []
    all_conf: list[float] = []

    for i in range(n):
        pred_corners, pred_conf = extract_corners_from_heatmap(output[i : i + 1], stride=cfg.stride)
        gt_corners = extract_gt_corners_from_targets(
            hm_targets[i], off_targets[i], off_masks[i], cfg.stride
        )

        cpe = compute_cpe(pred_corners, gt_corners)
        pck = compute_pck(pred_corners, gt_corners)

        all_cpe.append(cpe)
        all_pck.append(pck)
        all_conf.extend(pred_conf)

    # Aggregate
    flat_cpe = [c for row in all_cpe for c in row]
    per_corner_cpe = [sum(row[j] for row in all_cpe) / n for j in range(4)]

    logger.debug(
        "evaluate_batch: n=%d mean_cpe=%.2f pck@4=%.3f",
        n,
        sum(flat_cpe) / len(flat_cpe),
        sum(p[4] for p in all_pck) / len(all_pck),
    )

    return {
        "mean_cpe": sum(flat_cpe) / len(flat_cpe),
        "pck_2": sum(p[2] for p in all_pck) / len(all_pck),
        "pck_4": sum(p[4] for p in all_pck) / len(all_pck),
        "pck_8": sum(p[8] for p in all_pck) / len(all_pck),
        "mean_confidence": sum(all_conf) / len(all_conf) if all_conf else 0.0,
        "min_confidence": min(all_conf) if all_conf else 0.0,
        "per_corner_cpe": per_corner_cpe,
    }


# ── Full validation pass ─────────────────────────────────────────────


def validate(
    model: torch.nn.Module,
    val_loader: Any,
    cfg: TrainingConfig,
) -> dict[str, Any]:
    """Run full validation pass over a DataLoader.

    Args:
        model: The corner CNN model.
        val_loader: DataLoader yielding (images, hm_targets, off_targets, off_masks).
        cfg: Training configuration.

    Returns:
        Aggregated metrics: mean_cpe, pck_2, pck_4, pck_8, confidence stats.
    """
    model.eval()
    device = next(model.parameters()).device

    all_metrics: list[dict[str, Any]] = []

    for batch in val_loader:
        images, hm_targets, off_targets, off_masks = batch[:4]
        images = images.to(device)
        hm_targets = hm_targets.to(device)
        off_targets = off_targets.to(device)
        off_masks = off_masks.to(device)
        metrics = evaluate_batch(model, images, hm_targets, off_targets, off_masks, cfg)
        all_metrics.append(metrics)

    if not all_metrics:
        return {
            "mean_cpe": 0.0,
            "pck_2": 0.0,
            "pck_4": 0.0,
            "pck_8": 0.0,
            "mean_confidence": 0.0,
            "min_confidence": 0.0,
            "per_corner_cpe": [0.0, 0.0, 0.0, 0.0],
        }

    n_batches = len(all_metrics)

    logger.info("validate: %d batches processed", n_batches)

    return {
        "mean_cpe": sum(m["mean_cpe"] for m in all_metrics) / n_batches,
        "pck_2": sum(m["pck_2"] for m in all_metrics) / n_batches,
        "pck_4": sum(m["pck_4"] for m in all_metrics) / n_batches,
        "pck_8": sum(m["pck_8"] for m in all_metrics) / n_batches,
        "mean_confidence": sum(m["mean_confidence"] for m in all_metrics) / n_batches,
        "min_confidence": min(m["min_confidence"] for m in all_metrics),
        "per_corner_cpe": [
            sum(m["per_corner_cpe"][j] for m in all_metrics) / n_batches for j in range(4)
        ],
    }


# ── Visualization helpers ────────────────────────────────────────────


def draw_corner_overlay(
    img: np.ndarray,
    pred_corners: list[tuple[float, float]],
    gt_corners: list[tuple[float, float]],
    pred_conf: list[float],
) -> np.ndarray:
    """Draw predicted and ground-truth corners on an image.

    Green circles/polygon for predictions, red for ground truth.
    Confidence labels next to each predicted corner.

    Args:
        img: BGR uint8 image (H, W, 3).
        pred_corners: 4 predicted (x, y) coordinates.
        gt_corners: 4 ground-truth (x, y) coordinates.
        pred_conf: 4 confidence values.

    Returns:
        BGR uint8 image with overlays.
    """
    out = img.copy()

    # Draw GT corners and polygon (red)
    gt_pts = [(int(round(x)), int(round(y))) for x, y in gt_corners]
    for pt in gt_pts:
        cv2.circle(out, pt, 3, (0, 0, 255), -1)
    if len(gt_pts) == 4:
        cv2.polylines(out, [np.array(gt_pts, dtype=np.int32)], True, (0, 0, 255), 1)

    # Draw predicted corners and polygon (green)
    pred_pts = [(int(round(x)), int(round(y))) for x, y in pred_corners]
    for idx, pt in enumerate(pred_pts):
        cv2.circle(out, pt, 3, (0, 255, 0), -1)
        label = f"{pred_conf[idx]:.2f}"
        cv2.putText(
            out, label, (pt[0] + 4, pt[1] - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (0, 255, 0), 1
        )
    if len(pred_pts) == 4:
        cv2.polylines(out, [np.array(pred_pts, dtype=np.int32)], True, (0, 255, 0), 1)

    return out


def render_heatmap_overlay(
    img: np.ndarray,
    heatmaps: np.ndarray,
    alpha: float = 0.4,
) -> np.ndarray:
    """Render heatmap channels as colormap overlay on image.

    Takes the max across all 4 heatmap channels, applies COLORMAP_JET,
    and blends with the input image.

    Args:
        img: BGR uint8 image (H, W, 3).
        heatmaps: (4, grid_h, grid_w) float32 heatmaps.
        alpha: Blending alpha for the heatmap overlay.

    Returns:
        BGR uint8 blended image.
    """
    # Max across channels
    combined = np.max(heatmaps, axis=0)  # (grid_h, grid_w)

    # Resize to image dimensions
    h, w = img.shape[:2]
    combined_resized = cv2.resize(combined, (w, h), interpolation=cv2.INTER_LINEAR)

    # Normalize to [0, 255] for colormap
    max_val = combined_resized.max()
    if max_val > 0:
        combined_uint8 = (combined_resized / max_val * 255).astype(np.uint8)
    else:
        combined_uint8 = np.zeros((h, w), dtype=np.uint8)

    # Apply colormap
    heatmap_color = cv2.applyColorMap(combined_uint8, cv2.COLORMAP_JET)

    # Blend
    blended = cv2.addWeighted(img, 1.0 - alpha, heatmap_color, alpha, 0)

    return blended
