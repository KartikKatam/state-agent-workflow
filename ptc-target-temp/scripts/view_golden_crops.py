#!/usr/bin/env python3
"""
Visualize golden crop fixtures: unprocessed vs processed side-by-side.

Shows each golden crop before and after the full recipe pipeline
(planner → processor) for visual inspection.

Usage:
    python scripts/view_golden_crops.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import torch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from consumer.config import ConsumerConfig
from consumer.ops_preprocess_gpu import process_batch_gpu
from consumer.ops_recipe import (
    IDX_DENOISE,
    IDX_GAIN,
    IDX_GAMMA,
    IDX_SHARPEN,
    generate_recipe_tensor,
)
from tests.test_preprocess import _build_golden_batch, _load_golden_manifest

GOLDEN_CROPS_DIR = PROJECT_ROOT / "tests" / "fixtures" / "data" / "golden_crops"
OUTPUT_DIR = PROJECT_ROOT / "tests" / "fixtures" / "data" / "golden_crops" / "visual_comparison"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest = _load_golden_manifest()
    batch, entries = _build_golden_batch()
    cfg = ConsumerConfig()
    device = torch.device("cpu")

    recipe_tensor, _keys, is_enhanced, _groups, _meta = generate_recipe_tensor(batch, cfg)
    output = process_batch_gpu(batch, recipe_tensor, is_enhanced, cfg, device)

    all_rois = list(batch.base_rois) + list(batch.enhance_rois)

    print(f"Generating visual comparison for {len(all_rois)} golden crops...\n")

    grid_rows = []

    for i, roi in enumerate(all_rois):
        name = roi.metrics.track_id
        entry = entries[name]
        scenario = entry.get("scenario", "unknown")
        pm = output.post_meta[i]

        # Load original unprocessed crop
        original_crop = np.load(GOLDEN_CROPS_DIR / entry["file"])  # BGR uint8 HWC

        # Get processed output: [-1, 1] → [0, 255] BGR uint8
        processed_01 = (output.batch_tensor[i] + 1.0) / 2.0  # [0, 1] (3, 32, 128)
        processed_np = (processed_01.permute(1, 2, 0).numpy() * 255).clip(0, 255).astype(np.uint8)
        # Channel order: the pipeline preserves BGR, so processed_np is BGR

        # Resize original to same height as processed (32px) for side-by-side
        target_h = 32
        scale = target_h / original_crop.shape[0]
        target_w = int(original_crop.shape[1] * scale)
        original_resized = cv2.resize(
            original_crop, (target_w, target_h), interpolation=cv2.INTER_AREA
        )

        # Pad or resize original to 128px wide to match processed
        if target_w < 128:
            pad_left = (128 - target_w) // 2
            pad_right = 128 - target_w - pad_left
            original_padded = cv2.copyMakeBorder(
                original_resized, 0, 0, pad_left, pad_right, cv2.BORDER_CONSTANT, value=(0, 0, 0)
            )
        elif target_w > 128:
            original_padded = cv2.resize(original_resized, (128, 32), interpolation=cv2.INTER_AREA)
        else:
            original_padded = original_resized

        # Create label bar
        label_h = 20
        label_bar = np.zeros((label_h, 128 * 2 + 10, 3), dtype=np.uint8)
        gain = recipe_tensor[i, IDX_GAIN]
        gamma = recipe_tensor[i, IDX_GAMMA]
        sharpen = recipe_tensor[i, IDX_SHARPEN]
        denoise = recipe_tensor[i, IDX_DENOISE]
        label = f"{name} [{scenario}] g={gain:.2f} gm={gamma:.2f} s={sharpen:.2f} d={denoise:.2f}"
        cv2.putText(label_bar, label, (5, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)

        # Combine: [original | gap | processed]
        gap = np.zeros((32, 10, 3), dtype=np.uint8)
        pair = np.hstack([original_padded, gap, processed_np])

        # Stack label + pair
        combined = np.vstack([label_bar, pair])
        grid_rows.append(combined)

        # Also save individual comparison
        individual_path = OUTPUT_DIR / f"{name}_{scenario}.png"
        cv2.imwrite(str(individual_path), combined)

    # Build full grid image
    row_gap = np.zeros((5, grid_rows[0].shape[1], 3), dtype=np.uint8)
    grid_parts = []
    for row in grid_rows:
        grid_parts.append(row)
        grid_parts.append(row_gap)

    grid = np.vstack(grid_parts[:-1])  # Remove last gap
    grid_path = OUTPUT_DIR / "all_comparisons.png"
    cv2.imwrite(str(grid_path), grid)

    print(f"Grid image: {grid_path}")
    print(f"Individual images: {OUTPUT_DIR}/")
    print("\nLeft = unprocessed crop (resized to 32px height)")
    print("Right = processed output (32x128, PARSeq-ready)")

    # Print summary
    print(
        f"\n{'Name':>15} {'Scenario':<20} {'InLuma':>7} {'PostLuma':>8} {'Delta':>7} {'Gain':>6} {'Gamma':>6}"
    )
    print("-" * 80)
    for i, roi in enumerate(all_rois):
        name = roi.metrics.track_id
        scenario = entries[name].get("scenario", "?")
        pm = output.post_meta[i]
        print(
            f"{name:>15} {scenario:<20} {roi.metrics.luminance_mean:>7.1f} "
            f"{pm.post_luma_mean:>8.4f} {pm.luma_delta:>7.4f} "
            f"{recipe_tensor[i, IDX_GAIN]:>6.3f} {recipe_tensor[i, IDX_GAMMA]:>6.3f}"
        )


if __name__ == "__main__":
    main()
