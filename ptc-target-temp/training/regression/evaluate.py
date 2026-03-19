"""Evaluation metrics for corner regression training.

Corner extraction from regression output, CPE, PCK, batch evaluation,
validation pass, and visualization helpers.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from typing import TYPE_CHECKING

import cv2
import numpy as np
import torch

if TYPE_CHECKING:
    from training.regression.model import CornerRegressionNet

logger = logging.getLogger(__name__)


# ── Corner extraction ─────────────────────────────────────────────────


def extract_corners_from_regression(
    output: torch.Tensor,
) -> tuple[list[tuple[float, float]], list[float]]:
    """Extract corners from regression output (trivial reshape).

    Args:
        output: (1, 8) or (N, 8) predicted coordinates.

    Returns:
        (corners, confidences) — 4 (x, y) and 4 placeholder confidence values.
    """
    coords = output[0]  # (8,)
    corners = [(float(coords[2 * i]), float(coords[2 * i + 1])) for i in range(4)]
    confidences = [1.0, 1.0, 1.0, 1.0]
    return corners, confidences


def extract_corners_with_confidence(
    coords: torch.Tensor,
    attention_maps: torch.Tensor,
) -> tuple[list[tuple[float, float]], list[float]]:
    """Extract corners + attention-based confidence.

    Args:
        coords: (1, 8) predicted coordinates.
        attention_maps: (1, 4, H, W) normalized attention weights.

    Returns:
        (corners, confidences) — 4 (x, y) and 4 peak attention values.
    """
    corners = [(float(coords[0, 2 * i]), float(coords[0, 2 * i + 1])) for i in range(4)]
    confidences = [float(attention_maps[0, i].max()) for i in range(4)]
    return corners, confidences


def extract_gt_corners_from_coords(
    coord_target: torch.Tensor,
) -> list[tuple[float, float]]:
    """Extract ground-truth corners from coordinate target.

    Args:
        coord_target: (8,) tensor [x0, y0, x1, y1, ...].

    Returns:
        4 (x, y) corners.
    """
    return [(float(coord_target[2 * i]), float(coord_target[2 * i + 1])) for i in range(4)]


# ── CPE (Corner Pixel Error) ─────────────────────────────────────────


def compute_cpe(
    pred_corners: list[tuple[float, float]],
    gt_corners: list[tuple[float, float]],
) -> list[float]:
    """Compute Euclidean distance per corner."""
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
    """Compute PCK at multiple pixel thresholds."""
    cpe = compute_cpe(pred_corners, gt_corners)
    pck: dict[int, float] = {}
    for t in thresholds:
        pck[t] = sum(1 for c in cpe if c < t) / 4.0
    return pck


# ── Batch evaluation ─────────────────────────────────────────────────


def evaluate_batch(
    model: CornerRegressionNet,
    images: torch.Tensor,
    coord_targets: torch.Tensor,
) -> dict[str, Any]:
    """Evaluate a batch and return aggregated metrics.

    Args:
        model: CornerRegressionNet.
        images: (N, 3, H, W) input images.
        coord_targets: (N, 8) ground truth coordinates.

    Returns:
        Dict with mean_cpe, pck_2, pck_4, pck_8, mean_confidence,
        min_confidence, per_corner_cpe, attention_entropy, attention_peak_mean,
        attention_peak_min.
    """
    n = images.shape[0]

    with torch.no_grad():
        coords, attention = model.forward_with_attention(images)

    all_cpe: list[list[float]] = []
    all_pck: list[dict[int, float]] = []
    all_conf: list[float] = []

    for i in range(n):
        pred_corners, _ = extract_corners_with_confidence(
            coords[i : i + 1], attention[i : i + 1]
        )
        gt_corners = extract_gt_corners_from_coords(coord_targets[i])

        cpe = compute_cpe(pred_corners, gt_corners)
        pck = compute_pck(pred_corners, gt_corners)

        all_cpe.append(cpe)
        all_pck.append(pck)
        # Confidence = peak attention per corner
        conf = [float(attention[i, c].max()) for c in range(4)]
        all_conf.extend(conf)

    # Aggregate
    flat_cpe = [c for row in all_cpe for c in row]
    per_corner_cpe = [sum(row[j] for row in all_cpe) / n for j in range(4)]

    # Attention metrics
    # Entropy: -sum(attn * log(attn)) per corner, averaged
    attn_flat = attention.reshape(n, 4, -1)  # (N, 4, H*W)
    log_attn = torch.log(attn_flat + 1e-10)
    entropy = -(attn_flat * log_attn).sum(dim=2)  # (N, 4)
    mean_entropy = float(entropy.mean())

    # Peak attention per corner
    peak_attn = attn_flat.max(dim=2).values  # (N, 4)
    mean_peak = float(peak_attn.mean())
    min_peak = float(peak_attn.min())

    return {
        "mean_cpe": sum(flat_cpe) / len(flat_cpe),
        "pck_2": sum(p[2] for p in all_pck) / len(all_pck),
        "pck_4": sum(p[4] for p in all_pck) / len(all_pck),
        "pck_8": sum(p[8] for p in all_pck) / len(all_pck),
        "mean_confidence": sum(all_conf) / len(all_conf) if all_conf else 0.0,
        "min_confidence": min(all_conf) if all_conf else 0.0,
        "per_corner_cpe": per_corner_cpe,
        "attention_entropy": mean_entropy,
        "attention_peak_mean": mean_peak,
        "attention_peak_min": min_peak,
    }


# ── Full validation pass ─────────────────────────────────────────────


def validate(
    model: CornerRegressionNet,
    val_loader: Any,
) -> dict[str, Any]:
    """Run full validation pass over a DataLoader.

    Args:
        model: CornerRegressionNet.
        val_loader: DataLoader yielding (images, coord_targets, idx).

    Returns:
        Aggregated metrics.
    """
    model.eval()
    device = next(model.parameters()).device

    all_metrics: list[dict[str, Any]] = []

    for batch in val_loader:
        images, coord_targets = batch[0].to(device), batch[1].to(device)
        metrics = evaluate_batch(model, images, coord_targets)
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
            "attention_entropy": 0.0,
            "attention_peak_mean": 0.0,
            "attention_peak_min": 0.0,
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
        "attention_entropy": sum(m["attention_entropy"] for m in all_metrics) / n_batches,
        "attention_peak_mean": sum(m["attention_peak_mean"] for m in all_metrics) / n_batches,
        "attention_peak_min": min(m["attention_peak_min"] for m in all_metrics),
    }


# ── Visualization helpers ────────────────────────────────────────────


def draw_corner_overlay(
    img: np.ndarray,
    pred_corners: list[tuple[float, float]],
    gt_corners: list[tuple[float, float]],
    pred_conf: list[float],
) -> np.ndarray:
    """Draw predicted and ground-truth corners on an image."""
    out = img.copy()

    # GT corners (red)
    gt_pts = [(int(round(x)), int(round(y))) for x, y in gt_corners]
    for pt in gt_pts:
        cv2.circle(out, pt, 3, (0, 0, 255), -1)
    if len(gt_pts) == 4:
        cv2.polylines(out, [np.array(gt_pts, dtype=np.int32)], True, (0, 0, 255), 1)

    # Predicted corners (green)
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


def render_attention_overlay(
    img: np.ndarray,
    attention_maps: np.ndarray,
    alpha: float = 0.4,
) -> np.ndarray:
    """Render attention maps as colormap overlay on image.

    Args:
        img: BGR uint8 image (H, W, 3).
        attention_maps: (4, grid_h, grid_w) attention weights.
        alpha: Blending alpha.

    Returns:
        BGR uint8 blended image.
    """
    combined = np.max(attention_maps, axis=0)  # (grid_h, grid_w)

    h, w = img.shape[:2]
    combined_resized = cv2.resize(combined, (w, h), interpolation=cv2.INTER_LINEAR)

    max_val = combined_resized.max()
    if max_val > 0:
        combined_uint8 = (combined_resized / max_val * 255).astype(np.uint8)
    else:
        combined_uint8 = np.zeros((h, w), dtype=np.uint8)

    heatmap_color = cv2.applyColorMap(combined_uint8, cv2.COLORMAP_JET)
    blended = cv2.addWeighted(img, 1.0 - alpha, heatmap_color, alpha, 0)

    return blended
