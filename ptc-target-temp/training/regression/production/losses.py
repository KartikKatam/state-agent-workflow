"""Production loss: Gaussian NLL with wing-loss-shaped coordinate penalty."""

from __future__ import annotations

import math

import torch
from torch import Tensor


def production_loss(
    pred_coords: Tensor,
    pred_log_sigma: Tensor,
    target_coords: Tensor,
    img_w: int,
    img_h: int,
    wing_w: float = 10.0,
    wing_eps: float = 2.0,
) -> Tensor:
    """Gaussian NLL loss with wing-loss-shaped coordinate penalty.

    Normalizes coordinates independently per axis, computes wing loss
    element-wise, then wraps in Gaussian NLL using predicted log_sigma.

    NLL = 2*log_sigma_norm + wing_error * exp(-2*log_sigma_norm)

    Args:
        pred_coords: (N, 8) predicted pixel coords [x0, y0, ...].
        pred_log_sigma: (N, 8) predicted log(sigma) per coordinate.
        target_coords: (N, 8) target pixel coords.
        img_w: Image width (256).
        img_h: Image height (80).
        wing_w: Wing loss width in pixels.
        wing_eps: Wing loss curvature in pixels.

    Returns:
        Scalar mean loss.
    """
    scale = torch.tensor(
        [img_w, img_h] * 4,
        device=pred_coords.device,
        dtype=pred_coords.dtype,
    )

    # Normalize coordinates independently per axis
    pred_norm = pred_coords / scale
    target_norm = target_coords / scale

    # Wing loss parameters in normalized space (using img_w as reference)
    w_norm = wing_w / img_w
    eps_norm = wing_eps / img_w
    C = w_norm - w_norm * math.log(1.0 + w_norm / eps_norm)

    # Element-wise wing error in normalized space
    diff = (pred_norm - target_norm).abs()
    wing_error = torch.where(
        diff < w_norm,
        w_norm * torch.log(1.0 + diff / eps_norm),
        diff - C,
    )

    # Normalize log_sigma to match coordinate normalization
    log_sigma_norm = pred_log_sigma - torch.log(scale)

    # Gaussian NLL: log(sigma^2) + wing_error / sigma^2
    # = 2*log_sigma_norm + wing_error * exp(-2*log_sigma_norm)
    nll = 2.0 * log_sigma_norm + wing_error * torch.exp(-2.0 * log_sigma_norm)

    return nll.mean()
