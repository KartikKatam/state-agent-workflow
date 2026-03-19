"""Create a combined dataset (real + synthetic) for training.

Symlinks all real crop images and synthetic crop images into a single
flat directory, merges annotations, and creates a new split.json with
synthetic IDs added to the train split only. Val/test remain real-only.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


def main() -> None:
    real_crops_dir = Path("data/crops")
    real_annotations_path = Path("data/annotations.json")
    real_split_path = Path("data/split.json")

    synth_crops_dir = Path("data/synthetic_crops")
    synth_annotations_path = Path("data/synthetic_annotations.json")

    combined_dir = Path("data/crops_combined")
    combined_annotations_path = Path("data/annotations_combined.json")
    combined_split_path = Path("data/split_combined.json")

    # --- Validate inputs ---
    assert real_crops_dir.exists(), f"Real crops not found: {real_crops_dir}"
    assert real_annotations_path.exists(), f"Real annotations not found: {real_annotations_path}"
    assert real_split_path.exists(), f"Real split not found: {real_split_path}"
    assert synth_crops_dir.exists(), f"Synthetic crops not found: {synth_crops_dir}"
    assert synth_annotations_path.exists(), (
        f"Synthetic annotations not found: {synth_annotations_path}"
    )

    # --- Load real data ---
    with open(real_annotations_path) as f:
        real_annotations = json.load(f)
    with open(real_split_path) as f:
        real_split = json.load(f)

    # --- Load synthetic data ---
    with open(synth_annotations_path) as f:
        synth_annotations = json.load(f)

    # --- Create combined directory ---
    combined_dir.mkdir(parents=True, exist_ok=True)

    # --- Symlink real crops ---
    real_count = 0
    real_abs = real_crops_dir.resolve()
    for img_file in sorted(real_crops_dir.iterdir()):
        if img_file.suffix.lower() in (".jpg", ".jpeg", ".png"):
            link_path = combined_dir / img_file.name
            if not link_path.exists():
                os.symlink(real_abs / img_file.name, link_path)
            real_count += 1

    print(f"Symlinked {real_count} real crops")

    # --- Symlink synthetic crops ---
    synth_count = 0
    synth_abs = synth_crops_dir.resolve()
    for img_file in sorted(synth_crops_dir.iterdir()):
        if img_file.suffix.lower() in (".jpg", ".jpeg", ".png"):
            link_path = combined_dir / img_file.name
            if not link_path.exists():
                os.symlink(synth_abs / img_file.name, link_path)
            synth_count += 1

    print(f"Symlinked {synth_count} synthetic crops")

    # --- Check for naming collisions ---
    real_ids = set(real_annotations.keys())
    synth_ids = set(synth_annotations.keys())
    collisions = real_ids & synth_ids
    if collisions:
        print(
            f"WARNING: {len(collisions)} naming collisions — synthetic IDs overlap with real IDs!"
        )
        print(f"  First 5: {list(collisions)[:5]}")
        print("  Aborting to prevent data corruption.")
        sys.exit(1)

    # --- Merge annotations ---
    combined_annotations = {}
    combined_annotations.update(real_annotations)
    combined_annotations.update(synth_annotations)

    with open(combined_annotations_path, "w") as f:
        json.dump(combined_annotations, f, indent=2)

    print(
        f"Merged annotations: {len(real_annotations)} real + {len(synth_annotations)} synthetic = {len(combined_annotations)} total"
    )

    # --- Create combined split (synthetic added to train ONLY) ---
    synth_train_ids = sorted(synth_annotations.keys())

    combined_split = {
        "train": real_split["train"] + synth_train_ids,
        "val": real_split["val"],
    }
    # Preserve test split if it exists
    if "test" in real_split:
        combined_split["test"] = real_split["test"]

    with open(combined_split_path, "w") as f:
        json.dump(combined_split, f, indent=2)

    print("\nCombined split:")
    print(
        f"  train: {len(real_split['train'])} real + {len(synth_train_ids)} synthetic = {len(combined_split['train'])} total"
    )
    print(f"  val:   {len(real_split['val'])} real (unchanged)")
    if "test" in combined_split:
        print(f"  test:  {len(combined_split['test'])} real (unchanged)")

    # --- Summary ---
    print("\nOutput files:")
    print(f"  {combined_dir}/           — all images (symlinked)")
    print(f"  {combined_annotations_path}  — merged annotations")
    print(f"  {combined_split_path}        — combined split")
    print("\nTo use in training, set:")
    print(f"  data_dir = '{combined_dir}'")
    print(f"  annotations_path = '{combined_annotations_path}'")
    print(f"  split_path = '{combined_split_path}'")


if __name__ == "__main__":
    main()
