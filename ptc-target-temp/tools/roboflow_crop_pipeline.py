"""Download a Roboflow dataset and crop all annotated license plate bounding boxes.

Saves padded crops to a flat directory for corner keypoint annotation.

Usage:
    python tools/roboflow_crop_pipeline.py --api-key YOUR_KEY --output data/crops
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
from roboflow import Roboflow

# ─── Defaults ────────────────────────────────────────────────────────

WORKSPACE = "license-plate-recognition-rfxwe"
PROJECT = "lpr-yolobbox"
VERSION = 2
EXPORT_FORMAT = "yolov11"

PADDING_RATIO = 0.15
PADDING_MIN_PX = 12
SPLITS = ["train", "valid", "test"]


# ─── Padding (mirrors ops_cropping.py logic) ────────────────────────


def compute_padded_bbox(
    bbox: tuple[float, float, float, float],
    frame_shape: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Apply 15% padding (min 12px) clamped to frame boundaries."""
    x1, y1, x2, y2 = bbox
    h, w = frame_shape

    bbox_w = x2 - x1
    bbox_h = y2 - y1

    pad_w = max(int(bbox_w * PADDING_RATIO), PADDING_MIN_PX)
    pad_h = max(int(bbox_h * PADDING_RATIO), PADDING_MIN_PX)

    x1_pad = max(0, int(x1 - pad_w))
    y1_pad = max(0, int(y1 - pad_h))
    x2_pad = min(w, int(x2 + pad_w))
    y2_pad = min(h, int(y2 + pad_h))

    return x1_pad, y1_pad, x2_pad, y2_pad


# ─── YOLO label parsing ─────────────────────────────────────────────


def parse_yolo_labels(
    label_path: Path, img_w: int, img_h: int
) -> list[tuple[float, float, float, float]]:
    """Parse YOLO format labels into absolute pixel bboxes (x1, y1, x2, y2)."""
    bboxes = []
    if not label_path.exists():
        return bboxes

    for line in label_path.read_text().strip().splitlines():
        parts = line.strip().split()
        if len(parts) < 5:
            continue
        # class_id, cx, cy, w, h (normalized)
        cx, cy, bw, bh = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])

        x1 = (cx - bw / 2) * img_w
        y1 = (cy - bh / 2) * img_h
        x2 = (cx + bw / 2) * img_w
        y2 = (cy + bh / 2) * img_h

        bboxes.append((x1, y1, x2, y2))

    return bboxes


# ─── Main pipeline ───────────────────────────────────────────────────


def download_dataset(api_key: str, download_dir: Path) -> Path:
    """Download dataset from Roboflow and return the dataset location."""
    rf = Roboflow(api_key=api_key)
    project = rf.workspace(WORKSPACE).project(PROJECT)
    version = project.version(VERSION)

    dataset = version.download(EXPORT_FORMAT, location=str(download_dir))
    return Path(dataset.location)


def crop_dataset(dataset_dir: Path, output_dir: Path) -> int:
    """Crop all annotated bboxes from the dataset. Returns total crops saved."""
    output_dir.mkdir(parents=True, exist_ok=True)
    total_crops = 0
    total_images = 0

    for split in SPLITS:
        images_dir = dataset_dir / split / "images"
        labels_dir = dataset_dir / split / "labels"

        if not images_dir.exists():
            print(f"  Skipping split '{split}' — no images dir")
            continue

        image_files = sorted(
            p
            for p in images_dir.iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tiff"}
        )

        print(f"  Processing {split}: {len(image_files)} images")

        for img_path in image_files:
            img = cv2.imread(str(img_path))
            if img is None:
                print(f"    Warning: could not read {img_path.name}, skipping")
                continue

            total_images += 1
            img_h, img_w = img.shape[:2]

            label_path = labels_dir / (img_path.stem + ".txt")
            bboxes = parse_yolo_labels(label_path, img_w, img_h)

            for idx, bbox in enumerate(bboxes):
                x1, y1, x2, y2 = compute_padded_bbox(bbox, (img_h, img_w))

                if x2 <= x1 or y2 <= y1:
                    continue

                crop = img[y1:y2, x1:x2]
                if crop.size == 0:
                    continue

                crop_name = f"{img_path.stem}_{idx}.jpg"
                cv2.imwrite(str(output_dir / crop_name), crop)
                total_crops += 1

    print(f"\nDone: {total_crops} crops from {total_images} images → {output_dir}")
    return total_crops


# ─── CLI ─────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download Roboflow dataset and crop license plates"
    )
    parser.add_argument("--api-key", required=True, help="Roboflow API key")
    parser.add_argument(
        "--output", type=Path, default=Path("data/crops"), help="Output directory for crops"
    )
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=None,
        help="Where to download the dataset (default: temp dir next to output)",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip download, use existing dataset at --download-dir",
    )
    args = parser.parse_args()

    if args.download_dir is None:
        args.download_dir = args.output.parent / "roboflow_dataset"

    if not args.skip_download:
        print(f"Downloading from Roboflow: {WORKSPACE}/{PROJECT} v{VERSION}...")
        dataset_dir = download_dataset(args.api_key, args.download_dir)
        print(f"Downloaded to: {dataset_dir}\n")
    else:
        dataset_dir = args.download_dir
        print(f"Using existing dataset at: {dataset_dir}\n")

    print("Cropping license plates...")
    crop_dataset(dataset_dir, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
