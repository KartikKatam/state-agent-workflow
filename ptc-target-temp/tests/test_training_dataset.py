"""Tests for training/dataset.py — PlateCornerDataset and ground truth generation."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
import torch

from training.dataset import (
    PlateCornerDataset,
    corners_to_letterbox,
    generate_heatmap_target,
    generate_offset_target,
    letterbox_crop,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _make_synthetic_dataset(
    tmp_path: Path,
    *,
    num_train: int = 3,
    num_val: int = 1,
    img_h: int = 50,
    img_w: int = 200,
) -> tuple[str, str, str]:
    """Create a minimal synthetic dataset for testing.

    Returns (data_dir, annotations_path, split_path).
    """
    data_dir = tmp_path / "corner_crops"
    data_dir.mkdir(parents=True)

    annotations: dict[str, list[list[float]]] = {}
    train_ids: list[str] = []
    val_ids: list[str] = []

    for i in range(num_train + num_val):
        fname = f"crop_{i:04d}.png"
        img = np.random.randint(0, 255, (img_h, img_w, 3), dtype=np.uint8)
        cv2.imwrite(str(data_dir / fname), img)

        # Corners roughly matching a plate in the crop
        corners = [
            [10.0, 5.0],
            [190.0, 5.0],
            [190.0, 45.0],
            [10.0, 45.0],
        ]
        annotations[fname] = corners

        if i < num_train:
            train_ids.append(fname)
        else:
            val_ids.append(fname)

    ann_path = tmp_path / "annotations.json"
    ann_path.write_text(json.dumps(annotations))

    split_path = tmp_path / "split.json"
    split_path.write_text(json.dumps({"train": train_ids, "val": val_ids}))

    return str(data_dir), str(ann_path), str(split_path)


# ── Letterbox Tests ──────────────────────────────────────────────────


class TestLetterboxCrop:
    """Tests for letterbox_crop matching inference exactly."""

    def test_letterbox_output_dimensions(self) -> None:
        """img (50,200,3) -> letterbox_crop -> shape (80,256,3)."""
        img = np.zeros((50, 200, 3), dtype=np.uint8)
        lb, _s, _px, _py = letterbox_crop(img, 80, 256, (114, 114, 114))
        assert lb.shape == (80, 256, 3)

    def test_letterbox_exact_values_wide_crop(self) -> None:
        """img (50,200,3) -> scale==1.28, pad_x==0, pad_y==8.0."""
        img = np.zeros((50, 200, 3), dtype=np.uint8)
        lb, scale, pad_x, pad_y = letterbox_crop(img, 80, 256, (114, 114, 114))

        assert scale == pytest.approx(1.28, abs=1e-6)
        assert pad_x == pytest.approx(0.0, abs=1e-6)
        assert pad_y == pytest.approx(8.0, abs=1e-6)

    def test_letterbox_exact_values_tall_crop(self) -> None:
        """img (100,100,3) -> scale==0.8, new_h=80, new_w=80, pad_x=88.0, pad_y=0."""
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        lb, scale, pad_x, pad_y = letterbox_crop(img, 80, 256, (114, 114, 114))

        assert scale == pytest.approx(0.8, abs=1e-6)
        assert pad_x == pytest.approx(88.0, abs=1e-6)
        assert pad_y == pytest.approx(0.0, abs=1e-6)
        assert lb.shape == (80, 256, 3)

    def test_letterbox_exact_values_square_crop(self) -> None:
        """img (80,256,3) -> scale==1.0, pad_x==0, pad_y==0."""
        img = np.zeros((80, 256, 3), dtype=np.uint8)
        lb, scale, pad_x, pad_y = letterbox_crop(img, 80, 256, (114, 114, 114))

        assert scale == pytest.approx(1.0, abs=1e-6)
        assert pad_x == pytest.approx(0.0, abs=1e-6)
        assert pad_y == pytest.approx(0.0, abs=1e-6)
        assert lb.shape == (80, 256, 3)

    def test_letterbox_padding_uses_rounding_trick(self) -> None:
        """Verify +-0.1 rounding: top+new_h+bottom==80."""
        img = np.zeros((50, 200, 3), dtype=np.uint8)
        lb, _scale, _pad_x, _pad_y = letterbox_crop(img, 80, 256, (114, 114, 114))

        # scale = min(256/200, 80/50) = min(1.28, 1.6) = 1.28
        # new_w = round(200*1.28) = 256, new_h = round(50*1.28) = 64
        # pad_y = (80 - 64)/2 = 8.0
        # top = round(8.0 - 0.1) = round(7.9) = 8
        # bottom = round(8.0 + 0.1) = round(8.1) = 8
        # top + new_h + bottom = 8 + 64 + 8 = 80
        assert lb.shape[0] == 80
        assert lb.shape[1] == 256


# ── Corner Conversion Tests ──────────────────────────────────────────


class TestCornersToLetterbox:
    """Tests for corners_to_letterbox coordinate mapping."""

    def test_corners_to_letterbox_conversion(self) -> None:
        """corners + scale/pad -> letterbox coordinates."""
        corners = [(10.0, 5.0), (190.0, 5.0), (190.0, 45.0), (10.0, 45.0)]
        scale = 1.28
        pad_x = 0.0
        pad_y = 8.0

        result = corners_to_letterbox(corners, scale, pad_x, pad_y)

        expected = [
            (12.8, 14.4),
            (243.2, 14.4),
            (243.2, 65.6),
            (12.8, 65.6),
        ]
        for (rx, ry), (ex, ey) in zip(result, expected, strict=True):
            assert rx == pytest.approx(ex, abs=1e-4)
            assert ry == pytest.approx(ey, abs=1e-4)


# ── Heatmap Tests ────────────────────────────────────────────────────


class TestGenerateHeatmapTarget:
    """Tests for generate_heatmap_target Gaussian peak generation."""

    def test_heatmap_peak_at_correct_position(self) -> None:
        """corners_lb at grid-aligned positions -> peak 1.0 at exact grid cell."""
        corners_lb = [
            (40.0, 20.0),
            (200.0, 20.0),
            (200.0, 60.0),
            (40.0, 60.0),
        ]
        hm = generate_heatmap_target(corners_lb, 20, 64, 4, 1.5, 1e-4)

        # Corner 0 at (40,20): gx=40/4=10, gy=20/4=5
        assert hm[0, 5, 10].item() == pytest.approx(1.0)
        # Corner 1 at (200,20): gx=200/4=50, gy=20/4=5
        assert hm[1, 5, 50].item() == pytest.approx(1.0)
        # Corner 2 at (200,60): gx=200/4=50, gy=60/4=15
        assert hm[2, 15, 50].item() == pytest.approx(1.0)
        # Corner 3 at (40,60): gx=40/4=10, gy=60/4=15
        assert hm[3, 15, 10].item() == pytest.approx(1.0)

    def test_heatmap_values_in_zero_one(self) -> None:
        """Heatmap values bounded in [0, 1]."""
        corners_lb = [
            (30.0, 15.0),
            (210.0, 15.0),
            (210.0, 55.0),
            (30.0, 55.0),
        ]
        hm = generate_heatmap_target(corners_lb, 20, 64, 4, 1.5, 1e-4)

        assert hm.min().item() >= 0.0
        assert hm.max().item() <= 1.0

    def test_heatmap_uses_max_not_addition(self) -> None:
        """Two close corners: no heatmap value exceeds 1.0."""
        # Two corners very close together
        corners_lb = [
            (40.0, 20.0),
            (44.0, 20.0),  # 1 stride apart from first
            (200.0, 60.0),
            (40.0, 60.0),
        ]
        hm = generate_heatmap_target(corners_lb, 20, 64, 4, 1.5, 1e-4)

        assert hm.max().item() <= 1.0

    def test_heatmap_shape(self) -> None:
        """Heatmap output shape is (4, grid_h, grid_w)."""
        corners_lb = [
            (40.0, 20.0),
            (200.0, 20.0),
            (200.0, 60.0),
            (40.0, 60.0),
        ]
        hm = generate_heatmap_target(corners_lb, 20, 64, 4, 1.5, 1e-4)
        assert hm.shape == (4, 20, 64)

    def test_heatmap_handles_sigma_zero_gracefully(self) -> None:
        """sigma=0 -> ValueError with clear message, not ZeroDivisionError."""
        corners_lb = [
            (40.0, 20.0),
            (200.0, 20.0),
            (200.0, 60.0),
            (40.0, 60.0),
        ]
        with pytest.raises(ValueError, match="sigma"):
            generate_heatmap_target(corners_lb, 20, 64, 4, 0.0, 1e-4)


# ── Offset Tests ─────────────────────────────────────────────────────


class TestGenerateOffsetTarget:
    """Tests for generate_offset_target sub-stride offset computation."""

    def test_offset_target_exact_values(self) -> None:
        """corners_lb=[(41.5,21.5),...] -> exact dx,dy at grid cell."""
        corners_lb = [
            (41.5, 21.5),
            (201.0, 21.0),
            (200.5, 60.5),
            (40.5, 60.0),
        ]
        ot, om = generate_offset_target(corners_lb, 20, 64, 4)

        # Corner 0: (41.5, 21.5) -> gx_int=floor(41.5/4)=10, gy_int=floor(21.5/4)=5
        # dx = 41.5 - 10*4 = 1.5, dy = 21.5 - 5*4 = 1.5
        assert ot[0, 5, 10].item() == pytest.approx(1.5)
        assert ot[1, 5, 10].item() == pytest.approx(1.5)
        assert om[0, 5, 10].item() == pytest.approx(1.0)

        # Corner 1: (201.0, 21.0) -> gx_int=floor(201/4)=50, gy_int=floor(21/4)=5
        # dx = 201.0 - 50*4 = 1.0, dy = 21.0 - 5*4 = 1.0
        assert ot[2, 5, 50].item() == pytest.approx(1.0)
        assert ot[3, 5, 50].item() == pytest.approx(1.0)
        assert om[1, 5, 50].item() == pytest.approx(1.0)

        # Corner 2: (200.5, 60.5) -> gx_int=floor(200.5/4)=50, gy_int=floor(60.5/4)=15
        # dx = 200.5 - 50*4 = 0.5, dy = 60.5 - 15*4 = 0.5
        assert ot[4, 15, 50].item() == pytest.approx(0.5)
        assert ot[5, 15, 50].item() == pytest.approx(0.5)
        assert om[2, 15, 50].item() == pytest.approx(1.0)

        # Corner 3: (40.5, 60.0) -> gx_int=floor(40.5/4)=10, gy_int=floor(60/4)=15
        # dx = 40.5 - 10*4 = 0.5, dy = 60.0 - 15*4 = 0.0
        assert ot[6, 15, 10].item() == pytest.approx(0.5)
        assert ot[7, 15, 10].item() == pytest.approx(0.0)
        assert om[3, 15, 10].item() == pytest.approx(1.0)

    def test_offset_target_range_constraint(self) -> None:
        """All masked offsets in [0, stride=4)."""
        corners_lb = [
            (41.5, 21.5),
            (201.0, 21.0),
            (200.5, 60.5),
            (40.5, 60.0),
        ]
        ot, om = generate_offset_target(corners_lb, 20, 64, 4)

        # Expand mask to 8 channels to match offset target
        mask_expanded = om.repeat_interleave(2, dim=0)
        masked = ot[mask_expanded > 0]
        assert masked.min().item() >= 0.0
        assert masked.max().item() < 4.0

    def test_offset_mask_has_exactly_4_positives(self) -> None:
        """Single plate -> offset_mask.sum() == 4."""
        corners_lb = [
            (41.5, 21.5),
            (201.0, 21.0),
            (200.5, 60.5),
            (40.5, 60.0),
        ]
        _ot, om = generate_offset_target(corners_lb, 20, 64, 4)
        assert om.sum().item() == pytest.approx(4.0)

    def test_offset_target_shape(self) -> None:
        """Offset target (8, grid_h, grid_w) and mask (4, grid_h, grid_w)."""
        corners_lb = [
            (41.5, 21.5),
            (201.0, 21.0),
            (200.5, 60.5),
            (40.5, 60.0),
        ]
        ot, om = generate_offset_target(corners_lb, 20, 64, 4)
        assert ot.shape == (8, 20, 64)
        assert om.shape == (4, 20, 64)


# ── Dataset Integration Tests ────────────────────────────────────────


class TestPlateCornerDataset:
    """Tests for the full PlateCornerDataset class."""

    def test_dataset_returns_correct_shapes(self, tmp_path: Path) -> None:
        """dataset[0] -> img (3,80,256), hm (4,20,64), ot (8,20,64), om (4,20,64)."""
        data_dir, ann_path, split_path = _make_synthetic_dataset(tmp_path)
        from training.config import TrainingConfig

        cfg = TrainingConfig(
            data_dir=data_dir,
            annotations_path=ann_path,
            split_path=split_path,
        )
        ds = PlateCornerDataset(cfg, split="train")
        img, hm, ot, om = ds[0]

        assert img.shape == (3, 80, 256)
        assert hm.shape == (4, 20, 64)
        assert ot.shape == (8, 20, 64)
        assert om.shape == (4, 20, 64)

    def test_dataset_image_normalization_range_0_1(self, tmp_path: Path) -> None:
        """Image tensor values in [0.0, 1.0]."""
        data_dir, ann_path, split_path = _make_synthetic_dataset(tmp_path)
        from training.config import TrainingConfig

        cfg = TrainingConfig(
            data_dir=data_dir,
            annotations_path=ann_path,
            split_path=split_path,
        )
        ds = PlateCornerDataset(cfg, split="train")
        img, _, _, _ = ds[0]

        assert img.min().item() >= 0.0
        assert img.max().item() <= 1.0
        assert img.dtype == torch.float32

    def test_offset_mask_has_exactly_4_positives_per_image(self, tmp_path: Path) -> None:
        """Each image has exactly 4 positive mask values (one per corner)."""
        data_dir, ann_path, split_path = _make_synthetic_dataset(tmp_path)
        from training.config import TrainingConfig

        cfg = TrainingConfig(
            data_dir=data_dir,
            annotations_path=ann_path,
            split_path=split_path,
        )
        ds = PlateCornerDataset(cfg, split="train")
        _, _, _, om = ds[0]

        assert om.sum().item() == pytest.approx(4.0)

    def test_dataset_split_filtering_train_vs_val(self, tmp_path: Path) -> None:
        """train split has 3 images, val split has 1."""
        data_dir, ann_path, split_path = _make_synthetic_dataset(tmp_path, num_train=3, num_val=1)
        from training.config import TrainingConfig

        cfg = TrainingConfig(
            data_dir=data_dir,
            annotations_path=ann_path,
            split_path=split_path,
        )
        ds_train = PlateCornerDataset(cfg, split="train")
        ds_val = PlateCornerDataset(cfg, split="val")

        assert len(ds_train) == 3
        assert len(ds_val) == 1

    def test_dataset_missing_image_raises_clear_error(self, tmp_path: Path) -> None:
        """Annotation references nonexistent image -> FileNotFoundError."""
        data_dir = tmp_path / "corner_crops"
        data_dir.mkdir(parents=True)

        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps({"ghost.png": [[10, 5], [190, 5], [190, 45], [10, 45]]}))

        split_path = tmp_path / "split.json"
        split_path.write_text(json.dumps({"train": ["ghost.png"], "val": []}))

        from training.config import TrainingConfig

        cfg = TrainingConfig(
            data_dir=str(data_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
        )
        ds = PlateCornerDataset(cfg, split="train")

        with pytest.raises(FileNotFoundError, match="ghost.png"):
            ds[0]

    def test_dataset_len(self, tmp_path: Path) -> None:
        """__len__ returns correct count."""
        data_dir, ann_path, split_path = _make_synthetic_dataset(tmp_path, num_train=5, num_val=2)
        from training.config import TrainingConfig

        cfg = TrainingConfig(
            data_dir=data_dir,
            annotations_path=ann_path,
            split_path=split_path,
        )
        ds = PlateCornerDataset(cfg, split="train")
        assert len(ds) == 5
