#!/usr/bin/env python3
"""
Generate golden crops from COCO-annotated images for recipe-processor testing.

Runs the existing LPR pipeline (cropping, fast quality, rich quality) on
11 golden-example images to produce:
1. Per-image RichQualityMetrics summary
2. Saved crops as .npy files
3. JSON summary for chunk-coder test assertions
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from consumer.config import load_consumer_config
from consumer.models import RichQualityMetrics
from consumer.ops_quality_rich import analyze_roi_rich
from producer.config import load_producer_config
from producer.models import FastQualityMetrics, RoiFastQuality, RoiImage
from producer.ops_cropping import (
    adaptive_resize_crop,
    compute_padded_bbox,
    extract_crop_from_bbox,
)

# Paths
IMAGES_DIR = Path("/home/kartik/Downloads/golden-exmamples/train")
ANNOTATIONS_PATH = IMAGES_DIR / "_annotations.coco.json"
OUTPUT_CROPS_DIR = Path("tests/fixtures/data/golden_crops")
OUTPUT_SUMMARY_PATH = Path(".claude/temp/golden-metrics-summary.json")


def load_coco_annotations(path: Path) -> dict:
    """Load COCO annotations JSON."""
    with open(path) as f:
        return json.load(f)


def coco_bbox_to_xyxy(bbox: list[float]) -> list[float]:
    """Convert COCO bbox [x, y, w, h] to [x1, y1, x2, y2]."""
    x, y, w, h = bbox
    return [x, y, x + w, y + h]


def coco_keypoints_to_list(
    kp_flat: list[float],
) -> tuple[list[tuple[float, float]], list[float]] | tuple[None, None]:
    """
    Convert COCO keypoints [x1,y1,v1,x2,y2,v2,...] to (keypoints, scores).

    Args:
        kp_flat: Flat list [x1,y1,v1,x2,y2,v2,...]

    Returns:
        (keypoints, scores) where keypoints = [(x1,y1), (x2,y2), ...] and scores = [v1, v2, ...]
    """
    if not kp_flat or len(kp_flat) % 3 != 0:
        return None, None

    keypoints = []
    scores = []
    for i in range(0, len(kp_flat), 3):
        x, y, v = kp_flat[i], kp_flat[i + 1], kp_flat[i + 2]
        keypoints.append((x, y))
        scores.append(v / 2.0 if v == 2 else 0.0)  # v=2 means visible, normalize to [0,1]

    return keypoints, scores


def create_fake_roi(
    crop_img: np.ndarray,
    bbox: list[float],
    padded_bbox: list[float],
    frame_width: int,
    frame_height: int,
    keypoints: list[tuple[float, float]] | None,
    keypoint_scores: list[float] | None,
    crop_w: int,
    crop_h: int,
    was_resized: bool,
    scale_factor: float,
) -> RoiImage:
    """Create a fake RoiImage for pipeline testing."""
    return RoiImage(
        track_id="golden-crop",
        crop_img=crop_img,
        bbox=bbox,
        padded_bbox=padded_bbox,
        frame_idx=0,
        confidence=0.99,  # Fake confidence
        frame_width=frame_width,
        frame_height=frame_height,
        keypoints=keypoints,
        keypoint_scores=keypoint_scores,
        was_resized=was_resized,
        resize_scale=scale_factor,
        crop_width=crop_w,
        crop_height=crop_h,
    )


def create_fake_fast_quality(roi: RoiImage) -> RoiFastQuality:
    """Create a fake RoiFastQuality for rich quality analysis."""
    # Create dummy fast quality metrics (not used by rich quality analysis)
    metrics = FastQualityMetrics(
        focus_tenengrad=1.0,
        brightness_mean=0.5,
        contrast_std=0.1,
        over_exposed_frac=0.0,
        under_exposed_frac=0.0,
        band_edge_mean=1.0,
        gradient_histogram=np.zeros(25, dtype=np.float32),
    )

    thumb_gray = np.zeros((48, 160), dtype=np.uint8)

    return RoiFastQuality(
        roi=roi,
        metrics=metrics,
        quality_score=0.5,
        passes_min_quality=True,
        thumb_gray=thumb_gray,
    )


def metrics_to_dict(m: RichQualityMetrics) -> dict:
    """Convert RichQualityMetrics to JSON-serializable dict."""
    return {
        # Identification
        "track_id": m.track_id,
        "frame_idx": m.frame_idx,
        # Geometric
        "plate_width_px": m.plate_width_px,
        "plate_height_px": m.plate_height_px,
        "crop_clip_fraction": m.crop_clip_fraction,
        "detection_confidence": m.detection_confidence,
        # Keypoints
        "keypoint_confidence_min": m.keypoint_confidence_min,
        "keypoint_confidence_mean": m.keypoint_confidence_mean,
        # Pose
        "skew_degrees": m.skew_degrees,
        "perspective_score": m.perspective_score,
        "perspective_direction": m.perspective_direction,
        # Exposure
        "luminance_mean": m.luminance_mean,
        "luminance_p05": m.luminance_p05,
        "luminance_p95": m.luminance_p95,
        "black_clip_fraction": m.black_clip_fraction,
        "white_clip_fraction": m.white_clip_fraction,
        # Contrast
        "global_contrast": m.global_contrast,
        "local_contrast": m.local_contrast,
        # Sharpness
        "tenengrad": m.tenengrad,
        "tenengrad_horizontal": m.tenengrad_horizontal,
        "tenengrad_vertical": m.tenengrad_vertical,
        "blur_anisotropy": m.blur_anisotropy,
        # Noise
        "noise_std": m.noise_std,
        "flat_region_fraction": m.flat_region_fraction,
        # Flags
        "too_small": m.too_small,
        "clipped": m.clipped,
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
        # Diversity
        "bbox_center_x_normalized": m.bbox_center_x_normalized,
        "bbox_center_y_normalized": m.bbox_center_y_normalized,
        "diversity_signature": m.diversity_signature.tolist(),
        # Quad geometry
        "quad_area_px": m.quad_area_px,
        "edge_ratio": m.edge_ratio,
    }


def main():
    """Main entry point."""
    # Load configs
    producer_cfg = load_producer_config()
    consumer_cfg = load_consumer_config()

    # Load COCO annotations
    print(f"Loading COCO annotations from {ANNOTATIONS_PATH}")
    coco = load_coco_annotations(ANNOTATIONS_PATH)

    # Create output directories
    OUTPUT_CROPS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Build image_id -> filename map
    image_map = {img["id"]: img["file_name"] for img in coco["images"]}

    # Build image_id -> annotations map
    annotations_by_image = {}
    for ann in coco["annotations"]:
        img_id = ann["image_id"]
        if img_id not in annotations_by_image:
            annotations_by_image[img_id] = []
        annotations_by_image[img_id].append(ann)

    # Process each image
    all_metrics = []
    crop_idx = 0

    for img_id, filename in image_map.items():
        print(f"\n{'=' * 60}")
        print(f"Processing: {filename} (image_id={img_id})")
        print(f"{'=' * 60}")

        # Load image
        img_path = IMAGES_DIR / filename
        frame_img = cv2.imread(str(img_path))
        if frame_img is None:
            print(f"ERROR: Could not load {img_path}")
            continue

        frame_h, frame_w = frame_img.shape[:2]
        print(f"Image size: {frame_w}×{frame_h}")

        # Get annotations for this image
        anns = annotations_by_image.get(img_id, [])
        print(f"Found {len(anns)} annotations")

        for ann_idx, ann in enumerate(anns):
            print(f"\n--- Annotation {ann_idx + 1} ---")

            # Convert COCO bbox to [x1,y1,x2,y2]
            bbox_xyxy = coco_bbox_to_xyxy(ann["bbox"])
            print(f"Bbox (xyxy): {bbox_xyxy}")

            # Extract keypoints
            kp_flat = ann.get("keypoints", [])
            keypoints, keypoint_scores = coco_keypoints_to_list(kp_flat)
            if keypoints:
                print(f"Keypoints: {keypoints}")
                print(f"Keypoint scores: {keypoint_scores}")

            # Step 1: Compute padded bbox
            padded_bbox = compute_padded_bbox(bbox_xyxy, (frame_h, frame_w), producer_cfg)
            print(f"Padded bbox: {padded_bbox}")

            # Step 2: Extract crop
            crop_img = extract_crop_from_bbox(frame_img, padded_bbox)
            if crop_img is None:
                print("ERROR: Failed to extract crop")
                continue

            print(f"Crop size: {crop_img.shape[1]}×{crop_img.shape[0]}")

            # Step 3: Adaptive resize
            resized_crop, was_resized, scale_factor, crop_w, crop_h = adaptive_resize_crop(
                crop_img, producer_cfg.crop_adaptive_resize_threshold
            )
            if was_resized:
                print(f"Resized crop: {crop_w}×{crop_h} (scale={scale_factor:.3f})")

            # Translate keypoints to crop coordinates
            crop_keypoints = None
            crop_keypoint_scores = None
            if keypoints is not None:
                x0, y0, _, _ = padded_bbox
                crop_keypoints = [
                    ((xk - x0) * scale_factor, (yk - y0) * scale_factor) for (xk, yk) in keypoints
                ]
                crop_keypoint_scores = keypoint_scores

            # Step 4: Create fake RoiImage
            roi = create_fake_roi(
                resized_crop,
                bbox_xyxy,
                padded_bbox,
                frame_w,
                frame_h,
                crop_keypoints,
                crop_keypoint_scores,
                crop_w,
                crop_h,
                was_resized,
                scale_factor,
            )

            # Step 5: Create fake RoiFastQuality
            roi_fq = create_fake_fast_quality(roi)

            # Step 6: Run rich quality analysis
            roi_rq = analyze_roi_rich(roi_fq, consumer_cfg)
            metrics = roi_rq.metrics

            # Print key metrics
            print("\nRichQualityMetrics:")
            print(f"  Luminance mean: {metrics.luminance_mean:.1f}")
            print(f"  Global contrast: {metrics.global_contrast:.1f}")
            print(f"  Local contrast: {metrics.local_contrast:.1f}")
            print(f"  Tenengrad: {metrics.tenengrad:.1f}")
            print(f"  Tenengrad H: {metrics.tenengrad_horizontal:.1f}")
            print(f"  Tenengrad V: {metrics.tenengrad_vertical:.1f}")
            print(f"  Noise std: {metrics.noise_std:.2f}")
            print(f"  White clip: {metrics.white_clip_fraction:.3f}")
            print(f"  Black clip: {metrics.black_clip_fraction:.3f}")
            print(f"  top8_eligible: {metrics.top8_eligible}")
            print(f"  enhance_eligible: {metrics.enhance_eligible}")
            print(f"  homography_eligible: {metrics.homography_eligible}")
            print(f"  very_blurry: {metrics.very_blurry}")
            print(f"  mildly_soft: {metrics.mildly_soft}")
            print(f"  low_contrast: {metrics.low_contrast}")
            print(f"  noisy: {metrics.noisy}")
            print(f"  vertical_edges_weak: {metrics.vertical_edges_weak}")

            # Save crop as .npy
            crop_filename = f"crop_{crop_idx:03d}_{filename.replace('.jpg', '')}_ann{ann_idx}.npy"
            crop_path = OUTPUT_CROPS_DIR / crop_filename
            np.save(crop_path, resized_crop)
            print(f"Saved crop: {crop_path}")

            # Add to summary
            metrics_dict = metrics_to_dict(metrics)
            metrics_dict["image_filename"] = filename
            metrics_dict["annotation_idx"] = ann_idx
            metrics_dict["crop_filename"] = crop_filename
            all_metrics.append(metrics_dict)

            crop_idx += 1

    # Write summary JSON
    summary = {
        "num_images": len(image_map),
        "num_crops": crop_idx,
        "metrics": all_metrics,
    }

    with open(OUTPUT_SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n{'=' * 60}")
    print(f"Summary written to: {OUTPUT_SUMMARY_PATH}")
    print(f"Total crops saved: {crop_idx}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
