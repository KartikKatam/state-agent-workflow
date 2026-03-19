#!/usr/bin/env python3
"""
Build golden crop fixtures from COCO-annotated images.

Runs the full LPR pipeline (crop → fast quality → rich quality) on each
annotated plate and saves:
  - .npy crop files in tests/fixtures/data/golden_crops/
  - manifest.json with SHA256, metrics, and shape per crop
  - A summary table of key metrics for scenario labeling

Usage:
    python scripts/build_golden_fixtures.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import torch

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from consumer.config import ConsumerConfig
from consumer.ops_quality_rich import analyze_roi_rich
from producer.config import ProducerConfig
from producer.models import RoiImage
from producer.ops_cropping import (
    adaptive_resize_crop,
    compute_padded_bbox,
    extract_crop_from_bbox,
)
from producer.ops_quality_fast import fast_quality_analyze_rois

# === Paths ===
ANNOTATIONS_DIR = Path("/home/kartik/Downloads/golden-exmamples/train")
ANNOTATIONS_FILE = ANNOTATIONS_DIR / "_annotations.coco.json"
OUTPUT_DIR = PROJECT_ROOT / "tests" / "fixtures" / "data" / "golden_crops"


def load_coco_annotations(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def coco_bbox_to_xyxy(bbox: list) -> list[float]:
    """Convert COCO [x, y, w, h] to [x1, y1, x2, y2]."""
    x, y, w, h = [float(v) for v in bbox]
    return [x, y, x + w, y + h]


def parse_keypoints(kp_flat: list[float]) -> tuple[list[tuple[float, float]], list[float]]:
    """Parse COCO keypoints [x1,y1,v1, x2,y2,v2, ...] into points and scores."""
    points = []
    scores = []
    for i in range(0, len(kp_flat), 3):
        x, y, v = kp_flat[i], kp_flat[i + 1], kp_flat[i + 2]
        points.append((float(x), float(y)))
        scores.append(float(v) / 2.0)  # v=2 (visible) -> score=1.0
    return points, scores


def build_roi_from_annotation(
    frame_img: np.ndarray,
    bbox_xyxy: list[float],
    keypoints: list[tuple[float, float]],
    keypoint_scores: list[float],
    track_id: str,
    producer_cfg: ProducerConfig,
) -> RoiImage | None:
    """Build RoiImage using the existing producer pipeline functions."""
    frame_h, frame_w = frame_img.shape[:2]

    # Compute padded bbox
    padded_bbox = compute_padded_bbox(bbox_xyxy, (frame_h, frame_w), producer_cfg)

    # Extract crop
    crop_img = extract_crop_from_bbox(frame_img, padded_bbox)
    if crop_img is None:
        print(f"  WARNING: crop extraction failed for {track_id}")
        return None

    # Adaptive resize
    resized_crop, was_resized, scale_factor, crop_w, crop_h = adaptive_resize_crop(
        crop_img, producer_cfg.crop_adaptive_resize_threshold
    )

    # Translate keypoints from frame coords to crop coords
    x0, y0 = padded_bbox[0], padded_bbox[1]
    crop_keypoints = [
        ((xk - x0) * scale_factor, (yk - y0) * scale_factor) for (xk, yk) in keypoints
    ]

    return RoiImage(
        track_id=track_id,
        crop_img=resized_crop,
        bbox=bbox_xyxy,
        padded_bbox=padded_bbox,
        frame_idx=0,
        confidence=1.0,
        frame_width=frame_w,
        frame_height=frame_h,
        keypoints=crop_keypoints,
        keypoint_scores=keypoint_scores,
        was_resized=was_resized,
        resize_scale=scale_factor,
        crop_width=crop_w,
        crop_height=crop_h,
    )


def sha256_of_array(arr: np.ndarray) -> str:
    return hashlib.sha256(arr.tobytes()).hexdigest()


def main() -> None:
    print("Loading COCO annotations...")
    coco = load_coco_annotations(ANNOTATIONS_FILE)

    # Build image id -> image info lookup
    images_by_id = {img["id"]: img for img in coco["images"]}

    # Configs with defaults
    producer_cfg = ProducerConfig()
    consumer_cfg = ConsumerConfig()

    # Determine device
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    # Prepare output dir
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest_crops = []
    summary_rows = []

    for ann in coco["annotations"]:
        img_info = images_by_id[ann["image_id"]]
        img_file = ANNOTATIONS_DIR / img_info["file_name"]
        original_name = img_info.get("extra", {}).get("name", img_info["file_name"])
        crop_name = Path(original_name).stem.replace(" ", "_").replace("(", "").replace(")", "")

        print(f"\nProcessing: {original_name} -> {crop_name}")

        # Load frame
        frame = cv2.imread(str(img_file))
        if frame is None:
            print(f"  ERROR: Could not load {img_file}")
            continue

        # Parse annotation
        bbox_xyxy = coco_bbox_to_xyxy(ann["bbox"])
        keypoints, kp_scores = parse_keypoints(ann["keypoints"])

        # Build RoiImage via producer pipeline
        roi = build_roi_from_annotation(
            frame, bbox_xyxy, keypoints, kp_scores, crop_name, producer_cfg
        )
        if roi is None:
            continue

        print(f"  Crop shape: {roi.crop_img.shape}, resized: {roi.was_resized}")

        # Run fast quality (GPU)
        roi_fqs = fast_quality_analyze_rois([roi], producer_cfg, device)
        if not roi_fqs:
            print(f"  ERROR: fast quality failed for {crop_name}")
            continue
        roi_fq = roi_fqs[0]

        # Run rich quality
        roi_rq = analyze_roi_rich(roi_fq, consumer_cfg)
        m = roi_rq.metrics

        # Save crop as .npy
        npy_path = OUTPUT_DIR / f"{crop_name}.npy"
        np.save(npy_path, roi.crop_img)
        sha = sha256_of_array(roi.crop_img)

        print(f"  Saved: {npy_path}")
        print(
            f"  luminance_mean={m.luminance_mean:.1f}, global_contrast={m.global_contrast:.1f}, "
            f"tenengrad={m.tenengrad:.1f}, noise_std={m.noise_std:.1f}"
        )
        print(
            f"  white_clip={m.white_clip_fraction:.3f}, enhance_eligible={m.enhance_eligible}, "
            f"homography_eligible={m.homography_eligible}"
        )

        # Build manifest entry
        manifest_entry = {
            "name": crop_name,
            "file": f"{crop_name}.npy",
            "sha256": sha,
            "shape": list(roi.crop_img.shape),
            "original_image": original_name,
            "keypoints_in_crop": roi.keypoints,
            "metrics": {
                "luminance_mean": round(m.luminance_mean, 2),
                "luminance_p05": round(m.luminance_p05, 2),
                "luminance_p95": round(m.luminance_p95, 2),
                "global_contrast": round(m.global_contrast, 2),
                "local_contrast": round(m.local_contrast, 2),
                "tenengrad": round(m.tenengrad, 2),
                "tenengrad_horizontal": round(m.tenengrad_horizontal, 2),
                "tenengrad_vertical": round(m.tenengrad_vertical, 2),
                "blur_anisotropy": round(m.blur_anisotropy, 2),
                "noise_std": round(m.noise_std, 2),
                "flat_region_fraction": round(m.flat_region_fraction, 2),
                "black_clip_fraction": round(m.black_clip_fraction, 2),
                "white_clip_fraction": round(m.white_clip_fraction, 2),
                "plate_width_px": round(m.plate_width_px, 2),
                "plate_height_px": round(m.plate_height_px, 2),
                "skew_degrees": round(m.skew_degrees, 2) if m.skew_degrees is not None else None,
                "perspective_score": round(m.perspective_score, 2)
                if m.perspective_score is not None
                else None,
                "perspective_direction": round(m.perspective_direction, 2)
                if m.perspective_direction is not None
                else None,
                "too_small": m.too_small,
                "very_blurry": m.very_blurry,
                "mildly_soft": m.mildly_soft,
                "exposure_bad": m.exposure_bad,
                "low_contrast": m.low_contrast,
                "noisy": m.noisy,
                "vertical_edges_weak": m.vertical_edges_weak,
                "horizontal_edges_weak": m.horizontal_edges_weak,
                "top8_eligible": m.top8_eligible,
                "enhance_eligible": m.enhance_eligible,
                "homography_eligible": m.homography_eligible,
            },
        }
        manifest_crops.append(manifest_entry)

        # Summary row for quick viewing
        summary_rows.append(
            {
                "name": crop_name,
                "luma": round(m.luminance_mean, 1),
                "contrast": round(m.global_contrast, 1),
                "tenengrad": round(m.tenengrad, 1),
                "noise": round(m.noise_std, 1),
                "white_clip": round(m.white_clip_fraction, 3),
                "enhance": m.enhance_eligible,
                "homography": m.homography_eligible,
                "flags": [
                    f
                    for f, v in [
                        ("too_small", m.too_small),
                        ("very_blurry", m.very_blurry),
                        ("soft", m.mildly_soft),
                        ("exp_bad", m.exposure_bad),
                        ("low_contrast", m.low_contrast),
                        ("noisy", m.noisy),
                        ("vert_weak", m.vertical_edges_weak),
                        ("horiz_weak", m.horizontal_edges_weak),
                    ]
                    if v
                ],
            }
        )

    # Write manifest
    manifest = {"crops": manifest_crops}
    manifest_path = OUTPUT_DIR / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\nManifest written to: {manifest_path}")

    # Print summary table
    print("\n" + "=" * 100)
    print("SUMMARY — use these metrics to assign scenario labels")
    print("=" * 100)
    print(
        f"{'Name':<20} {'Luma':>6} {'Contr':>7} {'Tenen':>8} {'Noise':>6} {'WClip':>6} {'Enh':>5} {'Homo':>5} {'Flags'}"
    )
    print("-" * 100)
    for row in summary_rows:
        flags_str = ", ".join(row["flags"]) if row["flags"] else "clean"
        print(
            f"{row['name']:<20} {row['luma']:>6.1f} {row['contrast']:>7.1f} {row['tenengrad']:>8.1f} "
            f"{row['noise']:>6.1f} {row['white_clip']:>6.3f} {str(row['enhance']):>5} {str(row['homography']):>5} {flags_str}"
        )

    print(f"\n{len(manifest_crops)} crops processed. Fixtures in: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
