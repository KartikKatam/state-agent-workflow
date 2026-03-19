"""Tests for training/losses.py — CenterNet-variant focal loss, sparse offset loss, combined loss."""

from __future__ import annotations

import torch
from hypothesis import given, settings, strategies as st

from training.losses import combined_loss, focal_loss_heatmap, offset_loss

# ---------------------------------------------------------------------------
# Focal loss heatmap tests
# ---------------------------------------------------------------------------


class TestFocalLossHeatmap:
    """Tests for focal_loss_heatmap — CenterNet-variant focal loss."""

    def test_focal_loss_near_zero_perfect_match(self) -> None:
        """Target with single peak, pred=target.clone() -> loss < 1e-4."""
        target = torch.zeros(1, 4, 20, 64)
        target[0, 0, 5, 10] = 1.0
        pred = target.clone()

        loss = focal_loss_heatmap(pred, target)

        assert loss.item() < 1e-4, f"Expected loss < 1e-4 for perfect match, got {loss.item()}"

    def test_focal_loss_high_for_wrong_prediction(self) -> None:
        """Single-peak target, pred=0.5 everywhere -> loss > 0.1."""
        target = torch.zeros(1, 4, 20, 64)
        target[0, 0, 5, 10] = 1.0
        pred = torch.full_like(target, 0.5)

        loss = focal_loss_heatmap(pred, target)

        assert loss.item() > 0.1, f"Expected loss > 0.1 for wrong prediction, got {loss.item()}"

    @given(
        alpha=st.floats(min_value=1.0, max_value=4.0),
        beta=st.floats(min_value=1.0, max_value=6.0),
    )
    @settings(max_examples=20, deadline=None)
    def test_focal_loss_always_nonnegative(self, alpha: float, beta: float) -> None:
        """Random pred/target in [0,1] -> loss >= 0 (property-based)."""
        pred = torch.rand(2, 4, 10, 16)
        target = torch.rand(2, 4, 10, 16)
        # Make some pixels exactly 1.0 (positive)
        target[0, 0, 3, 5] = 1.0
        target[1, 2, 7, 10] = 1.0

        loss = focal_loss_heatmap(pred, target, alpha=alpha, beta=beta)

        assert loss.item() >= 0, f"Expected non-negative loss, got {loss.item()}"
        assert torch.isfinite(loss), f"Expected finite loss, got {loss.item()}"

    def test_focal_loss_all_zero_targets(self) -> None:
        """All-zero targets, small pred -> no NaN, loss >= 0."""
        target = torch.zeros(1, 4, 20, 64)
        pred = torch.full_like(target, 0.01)

        loss = focal_loss_heatmap(pred, target)

        assert not torch.isnan(loss), "Loss should not be NaN for all-zero targets"
        assert loss.item() >= 0, f"Expected non-negative loss, got {loss.item()}"


# ---------------------------------------------------------------------------
# Offset loss tests
# ---------------------------------------------------------------------------


class TestOffsetLoss:
    """Tests for offset_loss — sparse Smooth L1 at GT positions."""

    def test_offset_loss_zero_perfect_match(self) -> None:
        """pred==target at masked position -> loss < 1e-6."""
        target = torch.rand(1, 8, 20, 64)
        mask = torch.zeros(1, 4, 20, 64)
        mask[0, 0, 5, 10] = 1.0

        loss = offset_loss(target, target, mask)

        assert loss.item() < 1e-6, f"Expected loss < 1e-6 for perfect match, got {loss.item()}"

    def test_offset_loss_nonzero_mismatch(self) -> None:
        """pred=zeros, target=ones at mask -> loss > 0."""
        pred = torch.zeros(1, 8, 20, 64)
        target = torch.ones(1, 8, 20, 64)
        mask = torch.zeros(1, 4, 20, 64)
        mask[0, 0, 5, 10] = 1.0
        mask[0, 2, 10, 30] = 1.0

        loss = offset_loss(pred, target, mask)

        assert loss.item() > 0, f"Expected positive loss for mismatch, got {loss.item()}"


# ---------------------------------------------------------------------------
# Combined loss tests
# ---------------------------------------------------------------------------


class TestCombinedLoss:
    """Tests for combined_loss — returns (total, heatmap, offset) tuple."""

    def test_combined_loss_returns_three_components(self) -> None:
        """combined_loss returns (total, hm, off), all scalar tensors,
        total == hm + lambda_offset * off."""
        pred = torch.rand(1, 12, 20, 64)
        heatmap_target = torch.zeros(1, 4, 20, 64)
        heatmap_target[0, 0, 5, 10] = 1.0
        offset_target = torch.zeros(1, 8, 20, 64)
        offset_mask = torch.zeros(1, 4, 20, 64)
        offset_mask[0, 0, 5, 10] = 1.0

        lambda_off = 1.5
        total, hm, off = combined_loss(
            pred, heatmap_target, offset_target, offset_mask, lambda_offset=lambda_off
        )

        # All scalar tensors
        assert total.dim() == 0, f"total should be scalar, got dim={total.dim()}"
        assert hm.dim() == 0, f"hm should be scalar, got dim={hm.dim()}"
        assert off.dim() == 0, f"off should be scalar, got dim={off.dim()}"

        # Relationship: total = hm + lambda * off
        expected = hm + lambda_off * off
        assert torch.allclose(total, expected, atol=1e-6), (
            f"total ({total.item()}) != hm ({hm.item()}) + {lambda_off} * off ({off.item()}) = {expected.item()}"
        )

    def test_combined_loss_gradient_flow(self) -> None:
        """Predictions with requires_grad -> combined_loss -> backward -> grad is not None."""
        pred = torch.rand(1, 12, 20, 64, requires_grad=True)
        heatmap_target = torch.zeros(1, 4, 20, 64)
        heatmap_target[0, 0, 5, 10] = 1.0
        offset_target = torch.zeros(1, 8, 20, 64)
        offset_mask = torch.zeros(1, 4, 20, 64)
        offset_mask[0, 0, 5, 10] = 1.0

        total, _, _ = combined_loss(pred, heatmap_target, offset_target, offset_mask)
        total.backward()

        assert pred.grad is not None, "Gradient should flow through combined_loss"
        assert pred.grad.shape == pred.shape, "Gradient shape should match input"
        assert torch.isfinite(pred.grad).all(), "Gradients should be finite"


# ---------------------------------------------------------------------------
# Edge-case / numerical stability
# ---------------------------------------------------------------------------


class TestNumericalStability:
    """Tests for numerical stability under extreme inputs."""

    def test_loss_outputs_finite_under_extreme_logits(self) -> None:
        """pred with values near 0 and near 1 (1e-7, 1-1e-7) -> loss is finite."""
        target = torch.zeros(1, 4, 20, 64)
        target[0, 0, 5, 10] = 1.0

        # Extreme predictions near boundaries
        pred = torch.full_like(target, 1e-7)
        pred[0, 0, 5, 10] = 1.0 - 1e-7

        loss = focal_loss_heatmap(pred, target)

        assert torch.isfinite(loss), f"Expected finite loss, got {loss.item()}"
        assert not torch.isnan(loss), f"Expected non-NaN loss, got {loss.item()}"
