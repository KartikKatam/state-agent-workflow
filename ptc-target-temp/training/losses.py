"""CenterNet-variant loss functions for corner heatmap regression.

- focal_loss_heatmap: per-pixel focal loss for heatmap channels
- offset_loss: sparse Smooth L1 at ground truth positions
- combined_loss: weighted sum returning individual components
"""

from __future__ import annotations

import logging

import torch
import torch.nn.functional as F
from torch import Tensor

logger = logging.getLogger(__name__)


def focal_loss_heatmap(
    pred: Tensor,
    target: Tensor,
    alpha: float = 2.0,
    beta: float = 4.0,
) -> Tensor:
    """CenterNet-variant focal loss for heatmap channels.

    Positive pixels (target == 1.0):
        loss = -(1 - pred)^alpha * log(pred)
    Negative pixels (target < 1.0):
        loss = -(1 - target)^beta * pred^alpha * log(1 - pred)

    Normalization: sum of all losses / max(num_positive_pixels, 1).

    Args:
        pred: Predicted heatmaps, shape (N, 4, H, W), values in [0, 1].
        target: Ground truth heatmaps, shape (N, 4, H, W).
        alpha: Focusing exponent for easy/hard weighting.
        beta: Penalty reduction near ground truth peaks.

    Returns:
        Scalar loss tensor.
    """
    # Clamp predictions to avoid log(0)
    pred = pred.clamp(min=1e-6, max=1.0 - 1e-6)

    pos_mask = target.eq(1.0)
    neg_mask = ~pos_mask

    # Positive loss: -(1 - pred)^alpha * log(pred)
    pos_loss = -(1.0 - pred).pow(alpha) * pred.log()
    pos_loss = pos_loss[pos_mask].sum()

    # Negative loss: -(1 - target)^beta * pred^alpha * log(1 - pred)
    neg_loss = -(1.0 - target).pow(beta) * pred.pow(alpha) * (1.0 - pred).log()
    neg_loss = neg_loss[neg_mask].sum()

    num_pos = pos_mask.float().sum().clamp(min=1.0)
    loss = (pos_loss + neg_loss) / num_pos

    logger.debug(
        "focal_loss_heatmap: num_pos=%d, pos_loss=%.4f, neg_loss=%.4f, total=%.4f",
        int(num_pos.item()),
        pos_loss.item(),
        neg_loss.item(),
        loss.item(),
    )

    return loss


def offset_loss(pred: Tensor, target: Tensor, mask: Tensor) -> Tensor:
    """Sparse Smooth L1 loss at ground truth positions only.

    For each corner i (0-3):
        dx_loss = smooth_l1(pred[:, 2*i] * mask[:, i], target[:, 2*i] * mask[:, i])
        dy_loss = smooth_l1(pred[:, 2*i+1] * mask[:, i], target[:, 2*i+1] * mask[:, i])

    Args:
        pred: Predicted offsets, shape (N, 8, H, W).
        target: Ground truth offsets, shape (N, 8, H, W).
        mask: Binary mask, shape (N, 4, H, W). 1.0 at GT corner positions.

    Returns:
        Scalar loss tensor: sum / (N * 4).
    """
    n = pred.shape[0]
    total = torch.tensor(0.0, device=pred.device, dtype=pred.dtype)

    for i in range(4):
        m = mask[:, i : i + 1]  # (N, 1, H, W)
        dx_loss = F.smooth_l1_loss(
            pred[:, 2 * i : 2 * i + 1] * m,
            target[:, 2 * i : 2 * i + 1] * m,
            reduction="sum",
        )
        dy_loss = F.smooth_l1_loss(
            pred[:, 2 * i + 1 : 2 * i + 2] * m,
            target[:, 2 * i + 1 : 2 * i + 2] * m,
            reduction="sum",
        )
        total = total + dx_loss + dy_loss

    loss = total / (n * 4)

    logger.debug("offset_loss: n=%d, raw_sum=%.4f, normalized=%.4f", n, total.item(), loss.item())

    return loss


def combined_loss(
    pred: Tensor,
    heatmap_target: Tensor,
    offset_target: Tensor,
    offset_mask: Tensor,
    lambda_offset: float = 1.0,
) -> tuple[Tensor, Tensor, Tensor]:
    """Combined heatmap focal loss + offset loss.

    Splits the model prediction into heatmap (channels 0:4) and offset (channels 4:12)
    components, computes each loss, and returns individual components for logging.

    Args:
        pred: Model output, shape (N, 12, H, W).
        heatmap_target: Ground truth heatmaps, shape (N, 4, H, W).
        offset_target: Ground truth offsets, shape (N, 8, H, W).
        offset_mask: Binary mask for offset positions, shape (N, 4, H, W).
        lambda_offset: Weight for offset loss term.

    Returns:
        Tuple of (total_loss, heatmap_loss, offset_loss) — all scalar tensors.
    """
    heatmaps = pred[:, 0:4]
    offsets = pred[:, 4:12]

    l_heatmap = focal_loss_heatmap(heatmaps, heatmap_target)
    l_offset = offset_loss(offsets, offset_target, offset_mask)
    l_total = l_heatmap + lambda_offset * l_offset

    logger.debug(
        "combined_loss: total=%.4f, heatmap=%.4f, offset=%.4f, lambda=%.2f",
        l_total.item(),
        l_heatmap.item(),
        l_offset.item(),
        lambda_offset,
    )

    return l_total, l_heatmap, l_offset
