"""Calibration comparison report: attention confidence vs learned variance.

Loads baseline and variance models, runs both on the validation set, and
prints a detailed comparison of confidence quality and calibration.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch.utils.data import DataLoader

from training.regression.config import RegressionConfig
from training.regression.dataset import PlateCornerRegressionDataset

logger = logging.getLogger(__name__)


def _collect_baseline_data(
    ckpt_path: str,
    cfg: RegressionConfig,
    val_loader: DataLoader,
    device: torch.device,
) -> dict[str, Any]:
    """Run baseline model on val set, collect (attention_peak, error) pairs."""
    from training.regression.model import CornerRegressionNet

    model = CornerRegressionNet(cfg).to(device)
    ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()

    all_errors: list[float] = []
    all_confs: list[float] = []

    for batch in val_loader:
        images, coord_targets = batch[0].to(device), batch[1].to(device)
        with torch.no_grad():
            coords, attention = model.forward_with_attention(images)

        n = images.shape[0]
        for i in range(n):
            for j in range(4):
                px = float(coords[i, 2 * j])
                py = float(coords[i, 2 * j + 1])
                gx = float(coord_targets[i, 2 * j])
                gy = float(coord_targets[i, 2 * j + 1])
                error = math.sqrt((px - gx) ** 2 + (py - gy) ** 2)
                conf = float(attention[i, j].max())
                all_errors.append(error)
                all_confs.append(conf)

    errors = np.array(all_errors)
    confs = np.array(all_confs)
    pck4 = float((errors < 4).mean())
    mean_cpe = float(errors.mean())

    # Correlation between confidence and error (negative = good)
    corr = float(np.corrcoef(confs, errors)[0, 1]) if confs.std() > 1e-8 else 0.0

    # Separation
    good_mask = errors < 4
    bad_mask = errors >= 4
    good_conf = float(confs[good_mask].mean()) if good_mask.any() else 0.0
    bad_conf = float(confs[bad_mask].mean()) if bad_mask.any() else 0.0

    return {
        "errors": errors,
        "confs": confs,
        "pck4": pck4,
        "mean_cpe": mean_cpe,
        "correlation": corr,
        "good_conf": good_conf,
        "bad_conf": bad_conf,
    }


def _collect_variance_data(
    ckpt_path: str,
    cfg: RegressionConfig,
    val_loader: DataLoader,
    device: torch.device,
) -> dict[str, Any]:
    """Run variance model on val set, collect (sigma, error) pairs."""
    from training.regression.variance.model import CornerRegressionNetWithVariance

    model = CornerRegressionNetWithVariance(cfg).to(device)
    ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()

    all_errors: list[float] = []
    all_sigmas: list[float] = []

    for batch in val_loader:
        images, coord_targets = batch[0].to(device), batch[1].to(device)
        with torch.no_grad():
            coords, _attention, sigma = model.forward_with_attention(images)

        n = images.shape[0]
        for i in range(n):
            for j in range(4):
                px = float(coords[i, 2 * j])
                py = float(coords[i, 2 * j + 1])
                gx = float(coord_targets[i, 2 * j])
                gy = float(coord_targets[i, 2 * j + 1])
                error = math.sqrt((px - gx) ** 2 + (py - gy) ** 2)
                s = float(sigma[i, j])
                all_errors.append(error)
                all_sigmas.append(s)

    errors = np.array(all_errors)
    sigmas = np.array(all_sigmas)
    pck4 = float((errors < 4).mean())
    mean_cpe = float(errors.mean())

    # Correlation between sigma and error (positive = good)
    corr = float(np.corrcoef(sigmas, errors)[0, 1]) if sigmas.std() > 1e-8 else 0.0

    # Calibration
    within_1sigma = float((errors < sigmas).mean())
    within_2sigma = float((errors < 2 * sigmas).mean())

    # Separation
    good_mask = errors < 4
    bad_mask = errors >= 4
    sigma_good = float(sigmas[good_mask].mean()) if good_mask.any() else 0.0
    sigma_bad = float(sigmas[bad_mask].mean()) if bad_mask.any() else 0.0
    separation_ratio = sigma_bad / max(sigma_good, 1e-4)

    # NLL
    sigmas_clamped = np.clip(sigmas, 1e-4, None)
    nll = 0.5 * (errors / sigmas_clamped) ** 2 + np.log(sigmas_clamped)
    nll_score = float(nll.mean())

    return {
        "errors": errors,
        "sigmas": sigmas,
        "pck4": pck4,
        "mean_cpe": mean_cpe,
        "correlation": corr,
        "within_1sigma": within_1sigma,
        "within_2sigma": within_2sigma,
        "sigma_good": sigma_good,
        "sigma_bad": sigma_bad,
        "separation_ratio": separation_ratio,
        "nll_score": nll_score,
        "sigma_mean": float(sigmas.mean()),
        "sigma_median": float(np.median(sigmas)),
    }


def _rejection_analysis(
    errors: np.ndarray,
    sigmas: np.ndarray,
    thresholds: list[float],
) -> list[dict[str, float]]:
    """Analyze PCK@4 when rejecting high-sigma corners."""
    results = []
    for thresh in thresholds:
        keep_mask = sigmas <= thresh
        reject_mask = sigmas > thresh
        n_total = len(errors)
        n_keep = int(keep_mask.sum())
        n_reject = int(reject_mask.sum())

        pck4_keep = float((errors[keep_mask] < 4).mean()) if n_keep > 0 else 0.0
        pck4_reject = float((errors[reject_mask] < 4).mean()) if n_reject > 0 else 0.0

        results.append({
            "threshold": thresh,
            "remaining_pct": 100.0 * n_keep / n_total,
            "pck4_keep": pck4_keep,
            "pck4_reject": pck4_reject,
        })
    return results


def generate_report(
    baseline_ckpt: str = "training/regression/results/baseline_100ep/best.pt",
    variance_ckpt: str = "training/regression/results/variance_100ep/best.pt",
) -> str:
    """Generate calibration comparison report. Returns the report text."""
    cfg = RegressionConfig()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    val_dataset = PlateCornerRegressionDataset(cfg, split="val")
    val_loader = DataLoader(
        val_dataset,
        batch_size=cfg.batch_size,
        shuffle=False,
        num_workers=0,
        pin_memory=False,
    )

    logger.info("Collecting baseline data from %s...", baseline_ckpt)
    baseline = _collect_baseline_data(baseline_ckpt, cfg, val_loader, device)

    logger.info("Collecting variance data from %s...", variance_ckpt)
    variance = _collect_variance_data(variance_ckpt, cfg, val_loader, device)

    # Build rejection analysis at multiple thresholds
    rejection = _rejection_analysis(
        variance["errors"],
        variance["sigmas"],
        [2.0, 3.0, 4.0, 5.0],
    )

    # Calibration bins
    sigma_bins = [(0, 1), (1, 2), (2, 3), (3, 4), (4, float("inf"))]
    bin_calibration = []
    for lo, hi in sigma_bins:
        mask = (variance["sigmas"] >= lo) & (variance["sigmas"] < hi)
        n = int(mask.sum())
        if n > 0:
            frac = float((variance["errors"][mask] < variance["sigmas"][mask]).mean())
        else:
            frac = 0.0
        bin_calibration.append((lo, hi, n, frac))

    lines = []
    lines.append("=" * 60)
    lines.append("   CONFIDENCE CALIBRATION COMPARISON")
    lines.append("=" * 60)
    lines.append("")
    lines.append("Baseline (attention peak as confidence):")
    lines.append(f"  PCK@4: {baseline['pck4']:.4f}")
    lines.append(f"  Mean CPE: {baseline['mean_cpe']:.2f} px")
    lines.append(f"  Correlation with error: {baseline['correlation']:.4f}  (negative = higher confidence -> lower error)")
    lines.append(f"  Separation: mean_conf(error<4px)={baseline['good_conf']:.4f} vs mean_conf(error>4px)={baseline['bad_conf']:.4f}")
    lines.append("")
    lines.append("Variance Prediction (learned sigma as confidence):")
    lines.append(f"  PCK@4: {variance['pck4']:.4f}")
    lines.append(f"  Mean CPE: {variance['mean_cpe']:.2f} px")
    lines.append(f"  Mean sigma: {variance['sigma_mean']:.2f} px  (median: {variance['sigma_median']:.2f} px)")
    lines.append(f"  Correlation with error: {variance['correlation']:.4f}  (positive = higher sigma -> higher error)")
    lines.append(f"  Calibration at 1-sigma: {variance['within_1sigma']*100:.1f}% of corners within predicted sigma (target: 68%)")
    lines.append(f"  Calibration at 2-sigma: {variance['within_2sigma']*100:.1f}% of corners within 2*sigma (target: 95%)")
    lines.append(f"  Mean sigma for correct (<4px) corners: {variance['sigma_good']:.2f} px")
    lines.append(f"  Mean sigma for incorrect (>4px) corners: {variance['sigma_bad']:.2f} px")
    lines.append(f"  Separation ratio: {variance['separation_ratio']:.2f}x (higher = better discrimination)")
    lines.append(f"  NLL score: {variance['nll_score']:.4f}")
    lines.append("")

    lines.append("Calibration by sigma bin:")
    lines.append(f"  {'Bin':>12}  {'Count':>6}  {'% within sigma':>14}")
    for lo, hi, n, frac in bin_calibration:
        hi_str = f"{hi:.0f}" if hi != float("inf") else "inf"
        lines.append(f"  {lo:.0f}-{hi_str:>3} px   {n:>6}  {frac*100:>13.1f}%")
    lines.append("")

    lines.append("=" * 60)
    lines.append("   REJECTION ANALYSIS")
    lines.append("=" * 60)
    lines.append("If we reject corners where sigma > threshold:")
    lines.append(f"  {'Threshold':>10}  {'Remaining':>10}  {'PCK@4 keep':>10}  {'PCK@4 reject':>12}")
    for r in rejection:
        lines.append(
            f"  {r['threshold']:>8.1f}px  {r['remaining_pct']:>9.1f}%  {r['pck4_keep']:>10.4f}  {r['pck4_reject']:>12.4f}"
        )
    lines.append("")

    report = "\n".join(lines)
    print(report)

    # Save report to file
    report_path = Path("training/regression/results/variance_100ep/calibration_report.txt")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report)
    logger.info("Report saved to %s", report_path)

    return report
