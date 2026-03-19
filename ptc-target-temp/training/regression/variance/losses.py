"""Gaussian NLL loss for variance-aware corner regression."""

from __future__ import annotations

import torch
from torch import Tensor


def gaussian_nll_loss(
    pred: Tensor,
    target: Tensor,
    sigma: Tensor,
    input_w: int,
    input_h: int,
) -> Tensor:
    """Gaussian NLL loss with learned variance.

    Normalizes coordinates independently per axis (same as regression_loss),
    then computes NLL using the predicted sigma.

    NLL = 0.5 * ((pred - target) / sigma)^2 + log(sigma)

    Args:
        pred: (N, 8) predicted pixel coordinates [x0, y0, x1, y1, ...].
        target: (N, 8) target pixel coordinates.
        sigma: (N, 4) predicted sigma per corner (pixel units).
        input_w: Image width (256).
        input_h: Image height (80).

    Returns:
        Scalar mean loss.
    """
    scale = torch.tensor(
        [input_w, input_h] * 4,
        device=pred.device,
        dtype=pred.dtype,
    )
    pred_norm = pred / scale
    target_norm = target / scale

    # Expand sigma from (N, 4) to (N, 8) — same sigma for x and y of each corner
    sigma_expanded = sigma.repeat_interleave(2, dim=1)  # (N, 8)
    # Normalize sigma the same way as coordinates
    sigma_norm = sigma_expanded / scale
    # Clamp to prevent numerical instability
    sigma_norm = sigma_norm.clamp(min=1e-6)

    # Gaussian NLL: 0.5 * ((pred - target) / sigma)^2 + log(sigma)
    diff = pred_norm - target_norm
    nll = 0.5 * (diff / sigma_norm).pow(2) + torch.log(sigma_norm)

    return nll.mean()
