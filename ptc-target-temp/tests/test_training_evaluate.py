"""Tests for training/evaluate.py — Evaluation Metrics (chunk-06).

TDD red phase: all tests written before implementation.
"""

from __future__ import annotations

import numpy as np
import pytest
import torch

from training.config import TrainingConfig
from training.dataset import generate_heatmap_target, generate_offset_target
from training.evaluate import (
    compute_cpe,
    compute_pck,
    draw_corner_overlay,
    evaluate_batch,
    extract_corners_from_heatmap,
    extract_gt_corners_from_targets,
    render_heatmap_overlay,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _make_synthetic_output(
    corners_lb: list[tuple[float, float]],
    stride: int = 4,
    grid_h: int = 20,
    grid_w: int = 64,
) -> np.ndarray:
    """Build a (1, 12, H, W) synthetic CNN output with known peaks and offsets.

    For each corner i, places a clear peak at the grid cell and encodes
    the sub-stride offset in the appropriate channels.
    """
    output = np.zeros((1, 12, grid_h, grid_w), dtype=np.float32)

    for i, (x_lb, y_lb) in enumerate(corners_lb):
        gx = int(x_lb) // stride
        gy = int(y_lb) // stride
        gx = max(0, min(gx, grid_w - 1))
        gy = max(0, min(gy, grid_h - 1))

        dx = x_lb - gx * stride
        dy = y_lb - gy * stride

        # Heatmap peak
        output[0, i, gy, gx] = 0.95

        # Offset channels: dx at 4+2*i, dy at 4+2*i+1
        output[0, 4 + 2 * i, gy, gx] = dx
        output[0, 4 + 2 * i + 1, gy, gx] = dy

    return output


# ── CPE Tests ────────────────────────────────────────────────────────


class TestComputeCPE:
    """Tests for compute_cpe (Corner Pixel Error)."""

    def test_cpe_zero_perfect_prediction(self) -> None:
        """gt == pred -> all CPE values are 0.0."""
        gt = [(10.0, 10.0), (50.0, 10.0), (50.0, 40.0), (10.0, 40.0)]
        cpe = compute_cpe(gt, gt)
        assert len(cpe) == 4
        assert all(c == 0.0 for c in cpe)

    def test_cpe_known_distance(self) -> None:
        """pred[0] offset by (1,0) from gt -> cpe[0]==1.0, rest==0."""
        gt = [(10.0, 10.0), (50.0, 10.0), (50.0, 40.0), (10.0, 40.0)]
        pred = [(11.0, 10.0), (50.0, 10.0), (50.0, 40.0), (10.0, 40.0)]
        cpe = compute_cpe(pred, gt)
        assert cpe[0] == pytest.approx(1.0)
        assert cpe[1] == pytest.approx(0.0)
        assert cpe[2] == pytest.approx(0.0)
        assert cpe[3] == pytest.approx(0.0)

    def test_cpe_diagonal_distance(self) -> None:
        """Diagonal offset of (3,4) -> distance 5.0."""
        gt = [(10.0, 10.0), (50.0, 10.0), (50.0, 40.0), (10.0, 40.0)]
        pred = [(13.0, 14.0), (50.0, 10.0), (50.0, 40.0), (10.0, 40.0)]
        cpe = compute_cpe(pred, gt)
        assert cpe[0] == pytest.approx(5.0)


# ── PCK Tests ────────────────────────────────────────────────────────


class TestComputePCK:
    """Tests for compute_pck (Percentage of Correct Keypoints)."""

    def test_pck_all_within_threshold(self) -> None:
        """pred offset by 0.5px -> pck@2,4,8 all 1.0."""
        gt = [(10.0, 10.0), (50.0, 10.0), (50.0, 40.0), (10.0, 40.0)]
        pred = [(10.5, 10.5), (50.5, 10.5), (50.5, 40.5), (10.5, 40.5)]
        pck = compute_pck(pred, gt)
        assert pck[2] == pytest.approx(1.0)
        assert pck[4] == pytest.approx(1.0)
        assert pck[8] == pytest.approx(1.0)

    def test_pck_partial_threshold(self) -> None:
        """Corners at distances [1,3,5,7] -> pck[2]==0.25, pck[4]==0.5, pck[8]==1.0."""
        gt = [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0), (0.0, 0.0)]
        pred = [(1.0, 0.0), (3.0, 0.0), (5.0, 0.0), (7.0, 0.0)]
        pck = compute_pck(pred, gt)
        assert pck[2] == pytest.approx(0.25)
        assert pck[4] == pytest.approx(0.5)
        assert pck[8] == pytest.approx(1.0)


# ── Corner Extraction Tests ──────────────────────────────────────────


class TestExtractCornersFromHeatmap:
    """Tests for extract_corners_from_heatmap."""

    def test_extraction_matches_inference(self) -> None:
        """Synthetic output with known peaks -> exact positions match x=gx*4+dx, y=gy*4+dy."""
        corners_lb = [(10.5, 8.3), (200.7, 8.3), (200.7, 60.1), (10.5, 60.1)]
        output = _make_synthetic_output(corners_lb, stride=4)

        pred_corners, pred_conf = extract_corners_from_heatmap(output, stride=4)

        assert len(pred_corners) == 4
        assert len(pred_conf) == 4

        for i, (px, py) in enumerate(pred_corners):
            # Reconstruct expected from grid cell + offset
            gx = int(corners_lb[i][0]) // 4
            gy = int(corners_lb[i][1]) // 4
            gx = max(0, min(gx, 63))
            gy = max(0, min(gy, 19))
            dx = corners_lb[i][0] - gx * 4
            dy = corners_lb[i][1] - gy * 4
            expected_x = gx * 4 + dx
            expected_y = gy * 4 + dy
            assert px == pytest.approx(expected_x, abs=1e-4)
            assert py == pytest.approx(expected_y, abs=1e-4)

    def test_extraction_channel_indexing(self) -> None:
        """Corner 0 uses [4,5], corner 1 uses [6,7], corner 2 uses [8,9], corner 3 uses [10,11]."""
        grid_h, grid_w, stride = 20, 64, 4

        # Place each corner at a distinct grid cell with unique offsets
        output = np.zeros((1, 12, grid_h, grid_w), dtype=np.float32)

        # Corner 0 at grid (5, 5), offset (1.1, 0.5)
        output[0, 0, 5, 5] = 0.9
        output[0, 4, 5, 5] = 1.1
        output[0, 5, 5, 5] = 0.5

        # Corner 1 at grid (3, 50), offset (2.2, 1.3)
        output[0, 1, 3, 50] = 0.9
        output[0, 6, 3, 50] = 2.2
        output[0, 7, 3, 50] = 1.3

        # Corner 2 at grid (15, 50), offset (3.0, 2.0)
        output[0, 2, 15, 50] = 0.9
        output[0, 8, 15, 50] = 3.0
        output[0, 9, 15, 50] = 2.0

        # Corner 3 at grid (15, 5), offset (0.5, 3.5)
        output[0, 3, 15, 5] = 0.9
        output[0, 10, 15, 5] = 0.5
        output[0, 11, 15, 5] = 3.5

        corners, _confs = extract_corners_from_heatmap(output, stride=stride)

        assert corners[0] == pytest.approx((5 * 4 + 1.1, 5 * 4 + 0.5), abs=1e-4)
        assert corners[1] == pytest.approx((50 * 4 + 2.2, 3 * 4 + 1.3), abs=1e-4)
        assert corners[2] == pytest.approx((50 * 4 + 3.0, 15 * 4 + 2.0), abs=1e-4)
        assert corners[3] == pytest.approx((5 * 4 + 0.5, 15 * 4 + 3.5), abs=1e-4)

    def test_extraction_with_torch_tensor(self) -> None:
        """extract_corners_from_heatmap works with torch tensors too."""
        corners_lb = [(20.0, 10.0), (100.0, 10.0), (100.0, 50.0), (20.0, 50.0)]
        output_np = _make_synthetic_output(corners_lb)
        output_torch = torch.from_numpy(output_np)

        corners_np, _ = extract_corners_from_heatmap(output_np, stride=4)
        corners_torch, _ = extract_corners_from_heatmap(output_torch, stride=4)

        for i in range(4):
            assert corners_np[i][0] == pytest.approx(corners_torch[i][0], abs=1e-4)
            assert corners_np[i][1] == pytest.approx(corners_torch[i][1], abs=1e-4)


# ── GT Corner Extraction Roundtrip ───────────────────────────────────


class TestExtractGTCornersRoundtrip:
    """Tests for extract_gt_corners_from_targets."""

    def test_extract_gt_corners_roundtrip(self) -> None:
        """Known corners -> generate targets -> extract_gt_corners -> match."""
        corners_lb = [(10.5, 8.3), (200.7, 8.3), (200.7, 60.1), (10.5, 60.1)]
        stride = 4
        grid_h, grid_w = 20, 64

        hm = generate_heatmap_target(corners_lb, grid_h, grid_w, stride, sigma=1.5, floor=1e-4)
        off, mask = generate_offset_target(corners_lb, grid_h, grid_w, stride)

        recovered = extract_gt_corners_from_targets(hm, off, mask, stride)

        assert len(recovered) == 4
        for i in range(4):
            assert recovered[i][0] == pytest.approx(corners_lb[i][0], abs=1e-4)
            assert recovered[i][1] == pytest.approx(corners_lb[i][1], abs=1e-4)


# ── Batch Evaluation Tests ───────────────────────────────────────────


class TestEvaluateBatch:
    """Tests for evaluate_batch."""

    def test_evaluate_batch_returns_all_metrics(self) -> None:
        """evaluate_batch returns dict with all required metric keys."""
        cfg = TrainingConfig()

        # Create a minimal dummy model
        class DummyModel(torch.nn.Module):
            def forward(self, x: torch.Tensor) -> torch.Tensor:
                n = x.shape[0]
                return torch.zeros(n, 12, 20, 64)

        model = DummyModel()

        # Single image batch
        images = torch.zeros(1, 3, 80, 256)
        corners_lb = [(10.0, 8.0), (200.0, 8.0), (200.0, 60.0), (10.0, 60.0)]

        grid_h = cfg.input_h // cfg.stride
        grid_w = cfg.input_w // cfg.stride

        hm_targets = generate_heatmap_target(
            corners_lb, grid_h, grid_w, cfg.stride, cfg.gaussian_sigma, cfg.gaussian_floor
        ).unsqueeze(0)
        off_targets, off_masks = generate_offset_target(corners_lb, grid_h, grid_w, cfg.stride)
        off_targets = off_targets.unsqueeze(0)
        off_masks = off_masks.unsqueeze(0)

        metrics = evaluate_batch(model, images, hm_targets, off_targets, off_masks, cfg)

        required_keys = {
            "mean_cpe",
            "pck_2",
            "pck_4",
            "pck_8",
            "mean_confidence",
            "min_confidence",
            "per_corner_cpe",
        }
        assert required_keys.issubset(metrics.keys())
        assert len(metrics["per_corner_cpe"]) == 4
        assert isinstance(metrics["mean_cpe"], float)
        assert isinstance(metrics["pck_2"], float)


# ── Visualization Tests ──────────────────────────────────────────────


class TestVisualization:
    """Tests for visualization helpers."""

    def test_visualization_corner_overlay_valid(self) -> None:
        """draw_corner_overlay returns (80, 256, 3) uint8 BGR image."""
        img = np.zeros((80, 256, 3), dtype=np.uint8)
        pred = [(10.0, 10.0), (200.0, 10.0), (200.0, 60.0), (10.0, 60.0)]
        gt = [(12.0, 12.0), (198.0, 12.0), (198.0, 58.0), (12.0, 58.0)]
        conf = [0.9, 0.85, 0.92, 0.88]

        out = draw_corner_overlay(img, pred, gt, conf)

        assert out.shape == (80, 256, 3)
        assert out.dtype == np.uint8
        # Should have drawn something (not all black)
        assert out.sum() > 0

    def test_visualization_heatmap_overlay_valid(self) -> None:
        """render_heatmap_overlay returns valid BGR uint8 output image."""
        img = np.full((80, 256, 3), 128, dtype=np.uint8)
        heatmaps = np.zeros((4, 20, 64), dtype=np.float32)
        # Put a peak in channel 0
        heatmaps[0, 10, 32] = 1.0

        out = render_heatmap_overlay(img, heatmaps)

        assert out.shape == (80, 256, 3)
        assert out.dtype == np.uint8
