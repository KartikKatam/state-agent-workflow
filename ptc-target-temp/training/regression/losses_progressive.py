"""Progressive wing loss — width decays over training for coarse-to-fine learning."""

from __future__ import annotations


def progressive_wing_w(
    epoch: int,
    total_epochs: int,
    w_start: float = 10.0,
    w_end: float = 3.0,
) -> float:
    """Linearly decay wing loss width from w_start to w_end over training.

    Args:
        epoch: Current epoch (0-indexed).
        total_epochs: Total number of epochs.
        w_start: Starting wing width in pixels.
        w_end: Final wing width in pixels.

    Returns:
        Current wing width for this epoch.
    """
    progress = min(epoch / max(total_epochs - 1, 1), 1.0)
    return w_start + (w_end - w_start) * progress
