"""Validation and quality checks for generated synthetic composite data.

Checks data leakage, annotation format parity, corner validity,
duplicates, and generates visual inspection mosaics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Corner colors for visualization: TL=green, TR=red, BR=blue, BL=yellow
CORNER_COLORS = [
    (0, 255, 0),  # TL = green (BGR)
    (0, 0, 255),  # TR = red
    (255, 0, 0),  # BR = blue
    (0, 255, 255),  # BL = yellow
]


def check_no_val_leakage(manifest_path: str | Path, split_path: str | Path) -> bool:
    """Assert no source_id in manifest appears in split.json['val'].

    Returns True if clean, raises ValueError if leakage detected.
    """
    with open(split_path) as f:
        splits = json.load(f)
    val_set = set(splits["val"])

    with open(manifest_path) as f:
        for line in f:
            entry = json.loads(line.strip())
            source_id = entry.get("source_id", "")
            if source_id in val_set:
                raise ValueError(
                    f"Data leakage: source '{source_id}' used in synthetic data "
                    f"is in the validation split!"
                )

    logger.info("No validation leakage detected.")
    return True


def check_annotations_format(synthetic_ann_path: str | Path, real_ann_path: str | Path) -> bool:
    """Assert synthetic annotations match format of real annotations.

    Keys are strings, values have 'corners' with 4 [x,y] pairs.
    """
    with open(synthetic_ann_path) as f:
        synth_ann = json.load(f)
    with open(real_ann_path) as f:
        real_ann = json.load(f)

    # Check at least one real entry for format reference
    real_example = next(
        (v for v in real_ann.values() if isinstance(v, dict) and v.get("corners") is not None),
        None,
    )
    if real_example is None:
        logger.warning("No valid real annotations found for format comparison.")
        return True

    errors = 0
    for key, value in synth_ann.items():
        if not isinstance(key, str):
            logger.error("Non-string key: %s", key)
            errors += 1
            continue
        if not isinstance(value, dict):
            logger.error("Non-dict value for %s", key)
            errors += 1
            continue
        corners = value.get("corners")
        if corners is None:
            logger.error("Null corners for %s", key)
            errors += 1
            continue
        if len(corners) != 4:
            logger.error("Expected 4 corners, got %d for %s", len(corners), key)
            errors += 1
            continue
        for i, pt in enumerate(corners):
            if not isinstance(pt, list) or len(pt) != 2:
                logger.error("Bad corner[%d] for %s: %s", i, key, pt)
                errors += 1

    if errors > 0:
        raise ValueError(f"Annotation format check failed with {errors} errors.")

    logger.info("Annotation format check passed: %d entries.", len(synth_ann))
    return True


def check_corner_validity(
    synthetic_ann_path: str | Path, synthetic_dir: str | Path
) -> dict[str, int]:
    """For each annotation, verify corners are in-bounds, convex, non-degenerate.

    Returns dict of stats: total, valid, oob, degenerate, non_convex.
    """
    with open(synthetic_ann_path) as f:
        synth_ann = json.load(f)

    synthetic_dir = Path(synthetic_dir)
    stats = {"total": 0, "valid": 0, "oob": 0, "degenerate": 0, "non_convex": 0}

    for key, value in synth_ann.items():
        stats["total"] += 1
        corners = value.get("corners")
        if corners is None or len(corners) != 4:
            stats["degenerate"] += 1
            continue

        # Get image dimensions
        img_path = synthetic_dir / key
        if img_path.exists():
            img = cv2.imread(str(img_path))
            if img is not None:
                h, w = img.shape[:2]
            else:
                continue
        else:
            continue

        # Check bounds
        oob = False
        for pt in corners:
            if pt[0] < 0 or pt[0] >= w or pt[1] < 0 or pt[1] >= h:
                oob = True
                break
        if oob:
            stats["oob"] += 1
            continue

        # Check size
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        if max(xs) - min(xs) < 8 or max(ys) - min(ys) < 4:
            stats["degenerate"] += 1
            continue

        # Check convexity
        is_convex = True
        for i in range(4):
            p1 = corners[i]
            p2 = corners[(i + 1) % 4]
            p3 = corners[(i + 2) % 4]
            cross = (p2[0] - p1[0]) * (p3[1] - p2[1]) - (p2[1] - p1[1]) * (p3[0] - p2[0])
            if cross < 0:
                is_convex = False
                break
        if not is_convex:
            stats["non_convex"] += 1
            continue

        stats["valid"] += 1

    logger.info(
        "Corner validity: %d/%d valid, %d OOB, %d degenerate, %d non-convex",
        stats["valid"],
        stats["total"],
        stats["oob"],
        stats["degenerate"],
        stats["non_convex"],
    )
    return stats


def check_no_duplicates(synthetic_dir: str | Path, sample_size: int = 500) -> bool:
    """Check for duplicate images via file hash sampling."""
    synthetic_dir = Path(synthetic_dir)
    files = sorted(synthetic_dir.glob("*.jpg"))

    if len(files) <= sample_size:
        sample = files
    else:
        rng = np.random.RandomState(42)
        indices = rng.choice(len(files), sample_size, replace=False)
        sample = [files[i] for i in indices]

    hashes: dict[str, str] = {}
    duplicates = 0
    for f in sample:
        h = hashlib.md5(f.read_bytes()).hexdigest()
        if h in hashes:
            logger.warning("Duplicate: %s == %s", f.name, hashes[h])
            duplicates += 1
        else:
            hashes[h] = f.name

    if duplicates > 0:
        logger.warning("%d duplicates found in sample of %d.", duplicates, len(sample))
    else:
        logger.info("No duplicates found in sample of %d.", len(sample))
    return duplicates == 0


def draw_corners_on_image(
    img: np.ndarray,
    corners: list[list[float]],
    label: str = "",
) -> np.ndarray:
    """Draw corner dots and quad outline on image for visual inspection."""
    vis = img.copy()
    pts = [(int(round(c[0])), int(round(c[1]))) for c in corners]

    # Draw quad outline in white
    for i in range(4):
        cv2.line(vis, pts[i], pts[(i + 1) % 4], (255, 255, 255), 1, cv2.LINE_AA)

    # Draw corner dots
    for i, pt in enumerate(pts):
        cv2.circle(vis, pt, 3, CORNER_COLORS[i], -1, cv2.LINE_AA)

    # Add label
    if label:
        cv2.putText(
            vis,
            label,
            (2, 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

    return vis


def generate_mosaic(
    synthetic_dir: str | Path,
    synthetic_ann_path: str | Path,
    output_path: str | Path,
    grid: tuple[int, int] = (8, 8),
) -> None:
    """Generate visual inspection mosaic.

    Random-sample grid[0]*grid[1] images, draw corner annotations.
    Save as PNG.
    """
    with open(synthetic_ann_path) as f:
        synth_ann = json.load(f)

    synthetic_dir = Path(synthetic_dir)
    ids = list(synth_ann.keys())
    n_cells = grid[0] * grid[1]

    rng = np.random.RandomState(42)
    if len(ids) > n_cells:
        indices = rng.choice(len(ids), n_cells, replace=False)
        sample_ids = [ids[i] for i in indices]
    else:
        sample_ids = ids[:n_cells]

    # Load and annotate each image
    cell_h, cell_w = 120, 300  # fixed cell size for mosaic
    mosaic = np.full((cell_h * grid[0], cell_w * grid[1], 3), 128, dtype=np.uint8)

    for idx, sid in enumerate(sample_ids):
        row = idx // grid[1]
        col = idx % grid[1]

        img_path = synthetic_dir / sid
        img = cv2.imread(str(img_path))
        if img is None:
            continue

        corners = synth_ann[sid].get("corners")
        if corners is None:
            continue

        # Draw annotations
        vis = draw_corners_on_image(img, corners, label=sid[:20])

        # Resize to cell
        vis_resized = cv2.resize(vis, (cell_w, cell_h), interpolation=cv2.INTER_AREA)

        mosaic[
            row * cell_h : (row + 1) * cell_h,
            col * cell_w : (col + 1) * cell_w,
        ] = vis_resized

    cv2.imwrite(str(output_path), mosaic)
    logger.info("Mosaic saved: %s (%dx%d grid)", output_path, grid[0], grid[1])


def print_statistics(synthetic_ann_path: str | Path, manifest_path: str | Path) -> None:
    """Print summary statistics for synthetic data."""
    with open(synthetic_ann_path) as f:
        synth_ann = json.load(f)

    methods: dict[str, int] = {}
    source_usage: dict[str, int] = {}
    with open(manifest_path) as f:
        for line in f:
            entry = json.loads(line.strip())
            m = entry.get("method", "unknown")
            methods[m] = methods.get(m, 0) + 1
            src = entry.get("source_id", "unknown")
            source_usage[src] = source_usage.get(src, 0) + 1

    print("\n=== Synthetic Data Statistics ===")
    print(f"Total composites: {len(synth_ann)}")
    print("\nMethod distribution:")
    for m, count in sorted(methods.items()):
        pct = count / max(1, len(synth_ann)) * 100
        print(f"  {m}: {count} ({pct:.1f}%)")

    usage_values = list(source_usage.values())
    if usage_values:
        print("\nSource plate usage:")
        print(f"  Unique plates used: {len(source_usage)}")
        print(f"  Min usage: {min(usage_values)}")
        print(f"  Max usage: {max(usage_values)}")
        print(f"  Mean usage: {np.mean(usage_values):.1f}")

    # Corner position distributions
    all_xs: list[float] = []
    all_ys: list[float] = []
    for value in synth_ann.values():
        corners = value.get("corners")
        if corners:
            for pt in corners:
                all_xs.append(pt[0])
                all_ys.append(pt[1])

    if all_xs:
        print("\nCorner position ranges:")
        print(f"  X: [{min(all_xs):.1f}, {max(all_xs):.1f}] mean={np.mean(all_xs):.1f}")
        print(f"  Y: [{min(all_ys):.1f}, {max(all_ys):.1f}] mean={np.mean(all_ys):.1f}")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Validate synthetic composite data")
    parser.add_argument("--synthetic-dir", default="data/synthetic_crops")
    parser.add_argument("--synthetic-annotations", default="data/synthetic_annotations.json")
    parser.add_argument("--manifest", default="data/synthetic_manifest.jsonl")
    parser.add_argument("--real-annotations", default="data/annotations.json")
    parser.add_argument("--real-split", default="data/split.json")
    parser.add_argument("--show-mosaic", action="store_true")
    parser.add_argument("--mosaic-output", default="data/synthetic_mosaic.png")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Run all checks
    print("Checking for validation data leakage...")
    check_no_val_leakage(args.manifest, args.real_split)

    print("Checking annotation format parity...")
    check_annotations_format(args.synthetic_annotations, args.real_annotations)

    print("Checking corner validity...")
    check_corner_validity(args.synthetic_annotations, args.synthetic_dir)

    print("Checking for duplicates...")
    check_no_duplicates(args.synthetic_dir)

    print("Printing statistics...")
    print_statistics(args.synthetic_annotations, args.manifest)

    if args.show_mosaic:
        print("Generating mosaic...")
        generate_mosaic(
            args.synthetic_dir,
            args.synthetic_annotations,
            args.mosaic_output,
        )


if __name__ == "__main__":
    main()
