"""Wing loss and regression loss for soft-argmax corner detection."""

from __future__ import annotations

import math

import torch
from torch import Tensor


def wing_loss(pred: Tensor, target: Tensor, w: float, epsilon: float) -> Tensor:
    """Element-wise Wing loss (Feng et al., 2018).

    Args:
        pred: (N, 8) predicted normalized coordinates.
        target: (N, 8) target normalized coordinates.
        w: Wing loss width (in normalized units).
        epsilon: Wing loss curvature (in normalized units).

    Returns:
        Scalar mean loss.
    """
    diff = (pred - target).abs()
    C = w - w * math.log(1.0 + w / epsilon)
    loss = torch.where(
        diff < w,
        w * torch.log(1.0 + diff / epsilon),
        diff - C,
    )
    return loss.mean()


def regression_loss(
    pred: Tensor,
    target: Tensor,
    input_w: int,
    input_h: int,
    w_pixels: float,
    epsilon_pixels: float,
) -> Tensor:
    """Normalize coordinates independently per axis and compute wing loss.

    Args:
        pred: (N, 8) pixel coords [x0, y0, x1, y1, ...].
        target: (N, 8) pixel coords.
        input_w: Image width (256).
        input_h: Image height (80).
        w_pixels: Wing loss width in pixels.
        epsilon_pixels: Wing loss curvature in pixels.

    Returns:
        Scalar loss.
    """
    scale = torch.tensor(
        [input_w, input_h] * 4,
        device=pred.device,
        dtype=pred.dtype,
    )
    pred_norm = pred / scale
    target_norm = target / scale
    w_norm = w_pixels / input_w
    eps_norm = epsilon_pixels / input_w
    return wing_loss(pred_norm, target_norm, w_norm, eps_norm)
