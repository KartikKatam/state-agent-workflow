"""Generate sample composites + real crops side by side for visual inspection."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from common.geometry import order_keypoints_by_angle  # noqa: E402
from tools.synthetic_data.generate_composites import (  # noqa: E402
    _apply_post_composite_degradation,
    _normalize_composite_size,
    composite_on_vehicle_rear,
    load_source_plates,
    load_vehicle_rears,
    validate_composite,
)

# Corner colors: TL=green, TR=red, BR=blue, BL=yellow
CORNER_COLORS = [
    (0, 255, 0),  # TL green
    (0, 0, 255),  # TR red
    (255, 0, 0),  # BR blue
    (0, 255, 255),  # BL yellow
]
CORNER_LABELS = ["TL", "TR", "BR", "BL"]


def draw_annotations(
    img: np.ndarray,
    corners: list[tuple[float, float] | list[float]],
    label: str = "",
) -> np.ndarray:
    """Draw corner dots, labels, and quad outline on image."""
    vis = img.copy()
    pts = [(int(round(c[0])), int(round(c[1]))) for c in corners]

    # Quad outline in white
    for i in range(4):
        cv2.line(vis, pts[i], pts[(i + 1) % 4], (255, 255, 255), 1, cv2.LINE_AA)

    # Corner dots + per-corner labels
    for i, pt in enumerate(pts):
        cv2.circle(vis, pt, 4, CORNER_COLORS[i], -1, cv2.LINE_AA)
        cv2.circle(vis, pt, 4, (0, 0, 0), 1, cv2.LINE_AA)  # black outline
        # Label offset to avoid overlap with dot
        lx = pt[0] + 6
        ly = pt[1] - 4 if i < 2 else pt[1] + 12  # above for top, below for bottom
        cv2.putText(
            vis,
            CORNER_LABELS[i],
            (lx, ly),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            CORNER_COLORS[i],
            1,
            cv2.LINE_AA,
        )

    # Image label at top
    if label:
        cv2.putText(
            vis,
            label,
            (3, 14),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

    # Dimensions at bottom-right
    h, w = vis.shape[:2]
    dim_text = f"{w}x{h}"
    cv2.putText(
        vis,
        dim_text,
        (w - 60, h - 5),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.3,
        (200, 200, 200),
        1,
        cv2.LINE_AA,
    )

    return vis


def main() -> None:
    output_dir = Path("data/sample_composites_v2")
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Load data ---
    plates = load_source_plates("data/annotations.json", "data/split.json", "data/crops")
    veh_rears = load_vehicle_rears(
        [
            "data/external/plat-kendaraan",
            "data/external/kongu-vehicle-plate",
        ]
    )

    print(f"Source plates: {len(plates)}")
    print(f"Vehicle rears: {len(veh_rears)}")

    # --- Generate 15 composites ---
    generated = 0
    attempts = 0

    while generated < 15 and attempts < 200:
        seed = 5000 + attempts
        local_rng = np.random.RandomState(seed)
        attempts += 1

        # Pick random source plate
        plate_id, plate_path, plate_corners = plates[local_rng.randint(0, len(plates))]
        plate_img = cv2.imread(str(plate_path))
        if plate_img is None:
            continue

        # Pick random vehicle rear
        vr_idx = local_rng.randint(0, len(veh_rears))
        vr_path, vr_bbox = veh_rears[vr_idx]
        vr_img = cv2.imread(str(vr_path))
        if vr_img is None:
            continue

        composite, corners = composite_on_vehicle_rear(
            plate_img, plate_corners, vr_img, vr_bbox, local_rng
        )

        ch, cw = composite.shape[:2]
        if not validate_composite(corners, cw, ch):
            continue

        # Size normalization
        size_result = _normalize_composite_size(composite, corners)
        if size_result is None:
            continue
        composite, corners = size_result

        # Re-validate after resize
        ch, cw = composite.shape[:2]
        if not validate_composite(corners, cw, ch):
            continue

        # Post-composite degradation
        composite = _apply_post_composite_degradation(composite, local_rng)

        # Draw annotations and save
        label = f"COMPOSITE #{generated + 1:02d} (src: {plate_id[:25]})"
        vis = draw_annotations(composite, corners, label=label)
        fname = f"composite_{generated + 1:02d}.png"
        cv2.imwrite(str(output_dir / fname), vis)
        print(f"  {fname}: {cw}x{ch}, src={plate_id[:30]}")
        generated += 1

    print(f"\nGenerated {generated} composites from {attempts} attempts")

    # --- Copy real crops with annotations for comparison ---
    with open("data/annotations.json") as f:
        annotations = json.load(f)
    with open("data/split.json") as f:
        splits = json.load(f)

    # Pick 10 diverse real crops from train split
    train_ids = splits["train"]
    real_rng = np.random.RandomState(42)
    indices = real_rng.choice(len(train_ids), min(50, len(train_ids)), replace=False)

    real_count = 0
    for idx in indices:
        if real_count >= 10:
            break
        img_id = train_ids[idx]
        entry = annotations.get(img_id)
        if entry is None:
            continue
        raw_corners = entry["corners"] if isinstance(entry, dict) else entry
        if raw_corners is None:
            continue

        img_path = Path("data/crops") / img_id
        img = cv2.imread(str(img_path))
        if img is None:
            continue

        corners_tuples = [(float(c[0]), float(c[1])) for c in raw_corners]
        corners_ordered = order_keypoints_by_angle(corners_tuples)

        label = f"REAL #{real_count + 1:02d} ({img_id[:25]})"
        vis = draw_annotations(img, corners_ordered, label=label)
        fname = f"real_{real_count + 1:02d}.png"
        cv2.imwrite(str(output_dir / fname), vis)

        rh, rw = img.shape[:2]
        print(f"  {fname}: {rw}x{rh}, id={img_id[:30]}")
        real_count += 1

    print(f"\nSaved {real_count} real crops for comparison")
    print(f"\nAll samples in: {output_dir.resolve()}")
    print("  composite_*.png = synthetic composites with keypoints (v2 — improved)")
    print("  real_*.png       = real crops with keypoints")


if __name__ == "__main__":
    main()
