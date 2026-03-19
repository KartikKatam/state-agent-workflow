"""Test factories for corner CNN and YOLO training integration tests.

Provides: make_training_config, make_synthetic_corners,
make_synthetic_dataset, make_mock_wandb, make_yolo_config,
sample_training_images, sample_training_image, make_synthetic_yolo_dataset.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

from training.config import TrainingConfig
from training.yolo.config import YoloTrainingConfig


def make_training_config(**overrides: Any) -> TrainingConfig:
    """Create a TrainingConfig with test-friendly defaults.

    Defaults: total_epochs=2, batch_size=2, device='cpu', wandb_enabled=False,
    warmup_epochs=0, checkpoint_interval=1.
    """
    defaults: dict[str, Any] = {
        "total_epochs": 2,
        "batch_size": 2,
        "device": "cpu",
        "wandb_enabled": False,
        "warmup_epochs": 0,
        "checkpoint_interval": 1,
        "freeze_min_epochs": 0,
        "freeze_max_epochs": 0,
        "input_h": 80,
        "input_w": 256,
        "stride": 4,
    }
    defaults.update(overrides)
    return TrainingConfig(**defaults)


def make_synthetic_corners(n: int = 4, img_w: int = 200, img_h: int = 50) -> list[list[float]]:
    """Generate valid rectangular corners within image bounds.

    Returns n corners as [[x, y], ...] forming a rough rectangle.
    For n=4: top-left, top-right, bottom-right, bottom-left.
    """
    margin_x = img_w * 0.15
    margin_y = img_h * 0.15
    x0, x1 = margin_x, img_w - margin_x
    y0, y1 = margin_y, img_h - margin_y

    if n == 4:
        return [
            [float(x0), float(y0)],
            [float(x1), float(y0)],
            [float(x1), float(y1)],
            [float(x0), float(y1)],
        ]
    # For non-4 cases, distribute points around a rectangle
    corners: list[list[float]] = []
    for i in range(n):
        t = i / n
        if t < 0.25:
            x = x0 + (x1 - x0) * (t / 0.25)
            y = float(y0)
        elif t < 0.5:
            x = float(x1)
            y = y0 + (y1 - y0) * ((t - 0.25) / 0.25)
        elif t < 0.75:
            x = x1 - (x1 - x0) * ((t - 0.5) / 0.25)
            y = float(y1)
        else:
            x = float(x0)
            y = y1 - (y1 - y0) * ((t - 0.75) / 0.25)
        corners.append([x, y])
    return corners


def make_synthetic_dataset(
    n_images: int = 10,
    img_size: tuple[int, int] = (50, 200),
    tmp_path: Path | None = None,
) -> tuple[Path, Path, Path]:
    """Create a synthetic dataset for training tests.

    Creates temp directory with n_images synthetic BGR uint8 crops,
    annotations.json, and split.json with 80/20 train/val split.

    Args:
        n_images: Number of synthetic images to generate.
        img_size: (height, width) of each image.
        tmp_path: Directory to create the dataset in. Must be provided.

    Returns:
        (data_dir, annotations_path, split_path)
    """
    if tmp_path is None:
        msg = "tmp_path is required"
        raise ValueError(msg)

    data_dir = tmp_path / "crops"
    data_dir.mkdir(parents=True, exist_ok=True)

    annotations: dict[str, list[list[float]]] = {}
    train_ids: list[str] = []
    val_ids: list[str] = []

    h, w = img_size
    rng = np.random.RandomState(42)

    for i in range(n_images):
        filename = f"img_{i:04d}.png"
        img = rng.randint(0, 255, (h, w, 3), dtype=np.uint8)
        cv2.imwrite(str(data_dir / filename), img)
        corners = make_synthetic_corners(n=4, img_w=w, img_h=h)
        annotations[filename] = corners

        # 80/20 split
        if i < int(n_images * 0.8) or i == 0:
            train_ids.append(filename)
        else:
            val_ids.append(filename)

    # Ensure at least 1 val image
    if not val_ids:
        val_ids.append(train_ids.pop())

    annotations_path = tmp_path / "annotations.json"
    annotations_path.write_text(json.dumps(annotations))

    split_path = tmp_path / "split.json"
    split_path.write_text(json.dumps({"train": train_ids, "val": val_ids}))

    return data_dir, annotations_path, split_path


def make_yolo_config(**overrides: Any) -> YoloTrainingConfig:
    """Create a YoloTrainingConfig with test-friendly defaults.

    Defaults: epochs=10, batch_size=2, device='cpu', wandb_enabled=False,
    mining_enabled=False, drone_aug_enabled=False, freeze_backbone=False, imgsz=64.
    """
    defaults: dict[str, Any] = {
        "epochs": 10,
        "batch_size": 2,
        "device": "cpu",
        "wandb_enabled": False,
        "mining_enabled": False,
        "drone_aug_enabled": False,
        "freeze_backbone": False,
        "imgsz": 64,
    }
    defaults.update(overrides)
    return YoloTrainingConfig(**defaults)


@contextmanager
def make_mock_wandb() -> Generator[MagicMock, None, None]:
    """Context manager that patches wandb with a MagicMock.

    Yields the mock wandb.init return value (the mock run object).
    """
    mock_wandb = MagicMock()
    mock_run = MagicMock()
    mock_wandb.init.return_value = mock_run
    mock_wandb.Image = MagicMock

    with patch.dict("sys.modules", {"wandb": mock_wandb}):
        yield mock_run


# ---------------------------------------------------------------------------
# YOLO training fixtures (chunk-01+)
# ---------------------------------------------------------------------------

_ROBOFLOW_TRAIN = Path(__file__).resolve().parent.parent / "data" / "roboflow_dataset" / "train"


@pytest.fixture(scope="session")
def sample_training_images() -> list[tuple[Path, Path]]:
    """Return 5 real (image_path, label_path) tuples from roboflow dataset.

    Skips if data not downloaded.
    """
    images_dir = _ROBOFLOW_TRAIN / "images"
    labels_dir = _ROBOFLOW_TRAIN / "labels"
    if not images_dir.exists() or len(list(images_dir.glob("*.jpg"))) < 5:
        pytest.skip("Training data not downloaded. Run Roboflow CLI to download.")

    pairs: list[tuple[Path, Path]] = []
    for img_path in sorted(images_dir.glob("*.jpg"))[:5]:
        label_path = labels_dir / f"{img_path.stem}.txt"
        if label_path.exists():
            pairs.append((img_path, label_path))

    if len(pairs) < 5:
        pytest.skip("Not enough matching image/label pairs found.")
    return pairs


@pytest.fixture
def sample_training_image(
    sample_training_images: list[tuple[Path, Path]], tmp_path: Path
) -> tuple[Path, Path]:
    """Return single (image_path, label_path) copied to tmp_path for isolation."""
    src_img, src_label = sample_training_images[0]
    dst_img = tmp_path / src_img.name
    dst_label = tmp_path / src_label.name
    shutil.copy2(src_img, dst_img)
    shutil.copy2(src_label, dst_label)
    return dst_img, dst_label


@pytest.fixture
def make_synthetic_yolo_dataset(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a minimal YOLO-format dataset in tmp_path. NEVER skips.

    Returns (data_yaml_path, train_dir, valid_dir).
    """
    rng = np.random.RandomState(42)

    train_dir = tmp_path / "train"
    valid_dir = tmp_path / "valid"
    for split_dir in [train_dir, valid_dir]:
        (split_dir / "images").mkdir(parents=True)
        (split_dir / "labels").mkdir(parents=True)

    # 3 train images
    for i in range(3):
        img = rng.randint(0, 255, (64, 64, 3), dtype=np.uint8)
        cv2.imwrite(str(train_dir / "images" / f"img_{i:04d}.jpg"), img)
        (train_dir / "labels" / f"img_{i:04d}.txt").write_text("0 0.5 0.5 0.3 0.2\n")

    # 2 valid images
    for i in range(2):
        img = rng.randint(0, 255, (64, 64, 3), dtype=np.uint8)
        cv2.imwrite(str(valid_dir / "images" / f"img_{i:04d}.jpg"), img)
        (valid_dir / "labels" / f"img_{i:04d}.txt").write_text("0 0.5 0.5 0.3 0.2\n")

    # data.yaml
    import yaml

    data_yaml_path = tmp_path / "data.yaml"
    data = {
        "path": str(tmp_path),
        "train": str(train_dir / "images"),
        "val": str(valid_dir / "images"),
        "nc": 1,
        "names": {0: "license-plate"},
    }
    data_yaml_path.write_text(yaml.dump(data))

    return data_yaml_path, train_dir, valid_dir
