"""Compare two YOLO models on drone test images, save annotated side-by-side results."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO


def run_comparison(
    image_paths: list[Path],
    model_a_path: Path,
    model_b_path: Path,
    label_a: str,
    label_b: str,
    output_dir: Path,
) -> None:
    """Run both models on each image, save annotated results."""
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading model A: {label_a} from {model_a_path}")
    model_a = YOLO(str(model_a_path))
    print(f"Loading model B: {label_b} from {model_b_path}")
    model_b = YOLO(str(model_b_path))

    for img_path in image_paths:
        img = cv2.imread(str(img_path))
        if img is None:
            print(f"  SKIP (unreadable): {img_path.name}")
            continue

        # Run inference
        results_a = model_a(img, verbose=False, conf=0.25)
        results_b = model_b(img, verbose=False, conf=0.25)

        # Get annotated frames
        ann_a = results_a[0].plot()
        ann_b = results_b[0].plot()

        # Resize to same height for side-by-side
        h = max(ann_a.shape[0], ann_b.shape[0])
        if ann_a.shape[0] != h:
            scale = h / ann_a.shape[0]
            ann_a = cv2.resize(ann_a, (int(ann_a.shape[1] * scale), h))
        if ann_b.shape[0] != h:
            scale = h / ann_b.shape[0]
            ann_b = cv2.resize(ann_b, (int(ann_b.shape[1] * scale), h))

        # Add labels
        font = cv2.FONT_HERSHEY_SIMPLEX
        label_h = 40
        banner_a = np.zeros((label_h, ann_a.shape[1], 3), dtype=np.uint8)
        banner_b = np.zeros((label_h, ann_b.shape[1], 3), dtype=np.uint8)

        # Count detections and get max confidence
        n_a = len(results_a[0].boxes)
        n_b = len(results_b[0].boxes)
        conf_a = f"{results_a[0].boxes.conf.max().item():.2f}" if n_a > 0 else "N/A"
        conf_b = f"{results_b[0].boxes.conf.max().item():.2f}" if n_b > 0 else "N/A"

        cv2.putText(banner_a, f"{label_a} | {n_a} det, conf={conf_a}", (10, 28), font, 0.7, (255, 255, 255), 2)
        cv2.putText(banner_b, f"{label_b} | {n_b} det, conf={conf_b}", (10, 28), font, 0.7, (255, 255, 255), 2)

        col_a = np.vstack([banner_a, ann_a])
        col_b = np.vstack([banner_b, ann_b])

        # Add divider
        divider = np.ones((col_a.shape[0], 4, 3), dtype=np.uint8) * 128
        combined = np.hstack([col_a, divider, col_b])

        out_path = output_dir / f"compare_{img_path.stem}.png"
        cv2.imwrite(str(out_path), combined)
        print(f"  {img_path.name}: normal={n_a} det (conf={conf_a}), aggressive={n_b} det (conf={conf_b}) -> {out_path.name}")

        # Also save individual results
        cv2.imwrite(str(output_dir / f"normal_{img_path.stem}.png"), ann_a)
        cv2.imwrite(str(output_dir / f"aggressive_{img_path.stem}.png"), ann_b)

    print(f"\nResults saved to {output_dir}")


if __name__ == "__main__":
    downloads = Path("/home/kartik/Downloads")
    image_files = sorted([
        downloads / "image.png",
        *[downloads / f"image ({i}).png" for i in range(1, 13)],
    ])
    # Filter to only existing files
    image_files = [p for p in image_files if p.exists()]
    print(f"Found {len(image_files)} test images")

    model_normal = Path("/home/kartik/work/firefly/runs/detect/runs/yolo-lpr/normal-curriculum-150ep/weights/best.pt")
    model_aggressive = Path("/home/kartik/work/firefly/runs/detect/runs/yolo-lpr/aggressive-curriculum-150ep/weights/best.pt")
    output = Path("/home/kartik/work/firefly/LPR-SingleDrone/results/drone-comparison")

    run_comparison(
        image_paths=image_files,
        model_a_path=model_normal,
        model_b_path=model_aggressive,
        label_a="Normal Curriculum",
        label_b="Aggressive Curriculum",
        output_dir=output,
    )
