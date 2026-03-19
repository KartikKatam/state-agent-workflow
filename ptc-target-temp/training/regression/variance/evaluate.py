"""Evaluation metrics for variance-aware corner regression.

Extends baseline evaluation with calibration metrics: sigma-error correlation,
calibration curve, and NLL score.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from typing import TYPE_CHECKING

import numpy as np
import torch

if TYPE_CHECKING:
    from training.regression.variance.model import CornerRegressionNetWithVariance

logger = logging.getLogger(__name__)


# ── Corner extraction ─────────────────────────────────────────────────


def extract_corners_with_confidence(
    coords: torch.Tensor,
    attention_maps: torch.Tensor,
) -> tuple[list[tuple[float, float]], list[float]]:
    """Extract corners + attention-based confidence."""
    corners = [(float(coords[0, 2 * i]), float(coords[0, 2 * i + 1])) for i in range(4)]
    confidences = [float(attention_maps[0, i].max()) for i in range(4)]
    return corners, confidences


def extract_gt_corners_from_coords(
    coord_target: torch.Tensor,
) -> list[tuple[float, float]]:
    """Extract ground-truth corners from coordinate target."""
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


# ── Calibration metrics ──────────────────────────────────────────────


def compute_calibration_metrics(
    all_errors: list[float],
    all_sigmas: list[float],
) -> dict[str, float]:
    """Compute calibration metrics from per-corner errors and sigmas.

    Args:
        all_errors: Per-corner Euclidean errors in pixels.
        all_sigmas: Per-corner predicted sigma in pixels.

    Returns:
        Dict with sigma_error_correlation, calibration_1sigma, calibration_2sigma,
        nll_score, sigma_mean, sigma_min, sigma_max.
    """
    if not all_errors or not all_sigmas:
        return {
            "sigma_error_correlation": 0.0,
            "calibration_1sigma": 0.0,
            "calibration_2sigma": 0.0,
            "nll_score": 0.0,
            "sigma_mean": 0.0,
            "sigma_min": 0.0,
            "sigma_max": 0.0,
        }

    errors = np.array(all_errors)
    sigmas = np.array(all_sigmas)

    # Pearson correlation between sigma and error
    if errors.std() > 1e-8 and sigmas.std() > 1e-8:
        correlation = float(np.corrcoef(sigmas, errors)[0, 1])
    else:
        correlation = 0.0

    # Calibration: fraction within 1-sigma and 2-sigma
    within_1sigma = float((errors < sigmas).mean())
    within_2sigma = float((errors < 2 * sigmas).mean())

    # NLL score: mean of 0.5 * (error/sigma)^2 + log(sigma)
    sigmas_clamped = np.clip(sigmas, 1e-4, None)
    nll = 0.5 * (errors / sigmas_clamped) ** 2 + np.log(sigmas_clamped)
    nll_score = float(nll.mean())

    return {
        "sigma_error_correlation": correlation,
        "calibration_1sigma": within_1sigma,
        "calibration_2sigma": within_2sigma,
        "nll_score": nll_score,
        "sigma_mean": float(sigmas.mean()),
        "sigma_min": float(sigmas.min()),
        "sigma_max": float(sigmas.max()),
    }


# ── Batch evaluation ─────────────────────────────────────────────────


def evaluate_batch(
    model: CornerRegressionNetWithVariance,
    images: torch.Tensor,
    coord_targets: torch.Tensor,
) -> dict[str, Any]:
    """Evaluate a batch and return aggregated metrics including sigma calibration."""
    n = images.shape[0]

    with torch.no_grad():
        coords, attention, sigma = model.forward_with_attention(images)

    all_cpe: list[list[float]] = []
    all_pck: list[dict[int, float]] = []
    all_conf: list[float] = []
    all_errors_flat: list[float] = []
    all_sigmas_flat: list[float] = []

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

        # Collect per-corner errors and sigmas
        sigma_i = sigma[i]  # (4,)
        for j in range(4):
            all_errors_flat.append(cpe[j])
            all_sigmas_flat.append(float(sigma_i[j]))

    # Aggregate standard metrics
    flat_cpe = [c for row in all_cpe for c in row]
    per_corner_cpe = [sum(row[j] for row in all_cpe) / n for j in range(4)]

    # Attention metrics
    attn_flat = attention.reshape(n, 4, -1)
    log_attn = torch.log(attn_flat + 1e-10)
    entropy = -(attn_flat * log_attn).sum(dim=2)
    mean_entropy = float(entropy.mean())

    peak_attn = attn_flat.max(dim=2).values
    mean_peak = float(peak_attn.mean())
    min_peak = float(peak_attn.min())

    # Calibration metrics
    cal_metrics = compute_calibration_metrics(all_errors_flat, all_sigmas_flat)

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
        # Sigma / calibration
        "sigma_mean": cal_metrics["sigma_mean"],
        "sigma_min": cal_metrics["sigma_min"],
        "sigma_max": cal_metrics["sigma_max"],
        "sigma_error_correlation": cal_metrics["sigma_error_correlation"],
        "calibration_1sigma": cal_metrics["calibration_1sigma"],
        "calibration_2sigma": cal_metrics["calibration_2sigma"],
        "nll_score": cal_metrics["nll_score"],
        # Raw per-corner data for report aggregation
        "_errors": all_errors_flat,
        "_sigmas": all_sigmas_flat,
    }


# ── Full validation pass ─────────────────────────────────────────────


def validate(
    model: CornerRegressionNetWithVariance,
    val_loader: Any,
) -> dict[str, Any]:
    """Run full validation pass over a DataLoader."""
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
            "sigma_mean": 0.0,
            "sigma_min": 0.0,
            "sigma_error_correlation": 0.0,
            "calibration_1sigma": 0.0,
            "calibration_2sigma": 0.0,
            "nll_score": 0.0,
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
        "sigma_mean": sum(m["sigma_mean"] for m in all_metrics) / n_batches,
        "sigma_min": min(m["sigma_min"] for m in all_metrics),
        "sigma_error_correlation": sum(m["sigma_error_correlation"] for m in all_metrics) / n_batches,
        "calibration_1sigma": sum(m["calibration_1sigma"] for m in all_metrics) / n_batches,
        "calibration_2sigma": sum(m["calibration_2sigma"] for m in all_metrics) / n_batches,
        "nll_score": sum(m["nll_score"] for m in all_metrics) / n_batches,
    }
