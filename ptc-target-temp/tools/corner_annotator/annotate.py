"""Corner annotation tool for license plate crop images.

Displays each crop at 2-3x zoom for pixel-precise clicking of 4 corner keypoints.
Saves annotations to JSON with sub-pixel precision in original crop pixel space.

Controls:
    Left click  — Place corner point (up to 4)
    Enter       — Confirm and save (requires exactly 4 points)
    R           — Redo current image (clear all points)
    Backspace   — Undo last click
    S           — Skip image (mark as unusable)
    Left arrow  — Go back to previous image for correction
    Q / Escape  — Quit and save progress

Usage:
    python -m tools.corner_annotator annotate data/crops data/annotations.json
    python -m tools.corner_annotator split data/annotations.json data/split.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

# ─── Constants ──────────────────────────────────────────────────────

MAX_CORNERS = 4
ZOOM_FACTOR = 3
DOT_RADIUS = 4
DOT_COLORS_BGR = [
    (0, 0, 255),  # Red
    (0, 255, 0),  # Green
    (255, 0, 0),  # Blue
    (0, 255, 255),  # Yellow
]
WINDOW_NAME = "Corner Annotation"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"}

# OpenCV key codes
KEY_ENTER = 13
KEY_BACKSPACE = 8
KEY_ESCAPE = 27
KEY_R = ord("r")
KEY_S = ord("s")
KEY_Q = ord("q")
KEY_LEFT_ARROW = 81  # Linux/GTK


# ─── Data I/O ───────────────────────────────────────────────────────


def load_annotations(path: Path) -> dict[str, Any]:
    """Load annotations from JSON file, returning empty dict if missing."""
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def save_annotations(annotations: dict[str, Any], path: Path) -> None:
    """Save annotations to JSON with consistent formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(annotations, f, indent=2)
        f.write("\n")


def list_images(crops_dir: Path) -> list[str]:
    """List image filenames in the crops directory, sorted alphabetically."""
    files = []
    for p in sorted(crops_dir.iterdir()):
        if p.suffix.lower() in IMAGE_EXTENSIONS and p.is_file():
            files.append(p.name)
    return files


def find_resume_index(images: list[str], annotations: dict[str, Any]) -> int:
    """Find the index of the first unannotated image."""
    for i, name in enumerate(images):
        if name not in annotations:
            return i
    return len(images)


# ─── Rendering ──────────────────────────────────────────────────────


def render_image(
    original: np.ndarray,
    corners: list[list[float]],
    zoom: int,
) -> np.ndarray:
    """Render the image at zoom with corner dots overlaid."""
    h, w = original.shape[:2]
    zoomed = cv2.resize(original, (w * zoom, h * zoom), interpolation=cv2.INTER_NEAREST)

    for i, (cx, cy) in enumerate(corners):
        zx = int(cx * zoom + zoom / 2)
        zy = int(cy * zoom + zoom / 2)
        color = DOT_COLORS_BGR[i % len(DOT_COLORS_BGR)]
        cv2.circle(zoomed, (zx, zy), DOT_RADIUS, color, -1)

    return zoomed


def render_status_bar(
    image: np.ndarray,
    filename: str,
    index: int,
    total: int,
    num_corners: int,
) -> np.ndarray:
    """Add a status bar at the bottom of the image."""
    bar_height = 30
    _h, w = image.shape[:2]
    bar = np.zeros((bar_height, w, 3), dtype=np.uint8)

    text = f"{filename}  [{index + 1}/{total}]  Corners: {num_corners}/{MAX_CORNERS}"
    cv2.putText(bar, text, (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    controls = "Enter=save  R=redo  Backspace=undo  S=skip  Left=back  Q=quit"
    cv2.putText(bar, controls, (w - 520, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1)

    return np.vstack([image, bar])


# ─── Click handler ──────────────────────────────────────────────────


class AnnotationState:
    """Mutable state for the current image being annotated."""

    def __init__(self, zoom: int = ZOOM_FACTOR) -> None:
        self.corners: list[list[float]] = []
        self.zoom = zoom
        self.dirty = True

    def add_corner(self, screen_x: int, screen_y: int) -> bool:
        """Convert screen coords to original pixel coords and add. Returns True if added."""
        if len(self.corners) >= MAX_CORNERS:
            return False
        # Simplify: the center of a zoomed pixel at original (px, py) is at
        # screen (px * zoom + zoom/2, py * zoom + zoom/2).
        # Inverting: orig = (screen - zoom/2) / zoom = screen/zoom - 0.5
        orig_x = screen_x / self.zoom - 0.5
        orig_y = screen_y / self.zoom - 0.5
        self.corners.append([round(orig_x, 4), round(orig_y, 4)])
        self.dirty = True
        return True

    def undo(self) -> bool:
        """Remove last corner. Returns True if removed."""
        if not self.corners:
            return False
        self.corners.pop()
        self.dirty = True
        return True

    def reset(self) -> None:
        """Clear all corners."""
        self.corners.clear()
        self.dirty = True

    @property
    def complete(self) -> bool:
        return len(self.corners) == MAX_CORNERS


def mouse_callback(event: int, x: int, y: int, _flags: int, param: Any) -> None:
    """OpenCV mouse callback for corner clicks."""
    if event == cv2.EVENT_LBUTTONDOWN:
        state: AnnotationState = param
        state.add_corner(x, y)


# ─── Main annotation loop ──────────────────────────────────────────


def annotate(crops_dir: Path, output_path: Path) -> None:
    """Run the interactive annotation loop."""
    annotations = load_annotations(output_path)
    images = list_images(crops_dir)

    if not images:
        print(f"No images found in {crops_dir}")
        return

    index = find_resume_index(images, annotations)
    if index >= len(images):
        print(f"All {len(images)} images are already annotated.")
        return

    print(f"Found {len(images)} images, {len(annotations)} already annotated.")
    print(f"Resuming from image {index + 1}: {images[index]}")
    print("Controls: Enter=save, R=redo, Backspace=undo, S=skip, Left=back, Q=quit")

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_GUI_NORMAL | cv2.WINDOW_AUTOSIZE)
    state = AnnotationState(zoom=ZOOM_FACTOR)

    # Pre-load existing corners if going back to an annotated image
    def load_state_for(idx: int) -> None:
        state.reset()
        name = images[idx]
        if name in annotations and annotations[name].get("corners"):
            for corner in annotations[name]["corners"]:
                state.corners.append(list(corner))

    load_state_for(index)

    try:
        while 0 <= index < len(images):
            filename = images[index]
            img_path = crops_dir / filename
            original = cv2.imread(str(img_path))

            if original is None:
                print(f"Warning: Could not read {img_path}, skipping.")
                index += 1
                load_state_for(min(index, len(images) - 1))
                continue

            cv2.setMouseCallback(WINDOW_NAME, mouse_callback, state)

            while True:
                if state.dirty:
                    display = render_image(original, state.corners, state.zoom)
                    display = render_status_bar(
                        display, filename, index, len(images), len(state.corners)
                    )
                    cv2.imshow(WINDOW_NAME, display)
                    state.dirty = False

                key = cv2.waitKey(50) & 0xFF

                if key == KEY_ENTER:
                    if state.complete:
                        annotations[filename] = {"corners": [list(c) for c in state.corners]}
                        save_annotations(annotations, output_path)
                        index += 1
                        state.reset()
                        if index < len(images):
                            load_state_for(index)
                        break
                elif key == KEY_R:
                    state.reset()
                elif key == KEY_BACKSPACE:
                    state.undo()
                elif key == KEY_S:
                    annotations[filename] = {"corners": None, "skipped": True}
                    save_annotations(annotations, output_path)
                    index += 1
                    state.reset()
                    if index < len(images):
                        load_state_for(index)
                    break
                elif key == KEY_LEFT_ARROW:
                    if index > 0:
                        index -= 1
                        load_state_for(index)
                        break
                elif key in (KEY_Q, KEY_ESCAPE):
                    save_annotations(annotations, output_path)
                    print(f"\nSaved. {len(annotations)} annotations total.")
                    return

        print(f"\nDone! {len(annotations)} annotations saved to {output_path}")

    finally:
        cv2.destroyAllWindows()


# ─── Split generation ──────────────────────────────────────────────


def generate_split(
    annotations_path: Path,
    split_path: Path,
    train_ratio: float = 0.8,
    seed: int = 42,
) -> dict[str, list[str]]:
    """Generate deterministic train/val split from annotations.

    Stratifies by 'night' metadata flag if present in annotations,
    otherwise performs a random split with fixed seed.

    Returns the split dict {"train": [...], "val": [...]}.
    """
    annotations = load_annotations(annotations_path)

    # Filter to images with actual corners (skip skipped images)
    valid_images = [
        name
        for name, data in annotations.items()
        if isinstance(data, dict) and data.get("corners") is not None
    ]
    valid_images.sort()

    rng = np.random.default_rng(seed)

    # Check if night metadata is available
    night_images = [
        name
        for name in valid_images
        if annotations[name].get("night") is True
        or annotations[name].get("metadata", {}).get("night") is True
    ]
    day_images = [name for name in valid_images if name not in night_images]

    if night_images:
        # Stratified split
        train: list[str] = []
        val: list[str] = []
        for group in [day_images, night_images]:
            shuffled = list(group)
            rng.shuffle(shuffled)
            n_train = int(len(shuffled) * train_ratio)
            train.extend(shuffled[:n_train])
            val.extend(shuffled[n_train:])
        train.sort()
        val.sort()
    else:
        # Simple random split
        shuffled = list(valid_images)
        rng.shuffle(shuffled)
        n_train = int(len(shuffled) * train_ratio)
        train = sorted(shuffled[:n_train])
        val = sorted(shuffled[n_train:])

    split = {"train": train, "val": val}

    split_path.parent.mkdir(parents=True, exist_ok=True)
    with open(split_path, "w") as f:
        json.dump(split, f, indent=2)
        f.write("\n")

    print(f"Split generated: {len(train)} train, {len(val)} val → {split_path}")
    return split


# ─── CLI ────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Corner annotation tool for license plate crop images.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Annotate corners interactively
  python -m tools.corner_annotator annotate data/crops data/annotations.json

  # Generate train/val split from existing annotations
  python -m tools.corner_annotator split data/annotations.json data/split.json

Controls (annotation mode):
  Left click    Place corner point (up to 4)
  Enter         Confirm and save (requires 4 points)
  R             Redo current image (clear all points)
  Backspace     Undo last click
  S             Skip image (mark as unusable)
  Left arrow    Go back to previous image
  Q / Escape    Quit and save progress
""",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Annotate subcommand
    annotate_parser = subparsers.add_parser(
        "annotate",
        help="Annotate corner keypoints on crop images",
    )
    annotate_parser.add_argument(
        "crops_dir",
        type=Path,
        help="Directory containing crop images",
    )
    annotate_parser.add_argument(
        "output",
        type=Path,
        help="Output JSON file for annotations",
    )
    annotate_parser.add_argument(
        "--zoom",
        type=int,
        default=ZOOM_FACTOR,
        help=f"Zoom factor for display (default: {ZOOM_FACTOR})",
    )

    # Split subcommand
    split_parser = subparsers.add_parser(
        "split",
        help="Generate train/val split from annotations",
    )
    split_parser.add_argument(
        "annotations",
        type=Path,
        help="Input annotations JSON file",
    )
    split_parser.add_argument(
        "output",
        type=Path,
        help="Output split JSON file",
    )
    split_parser.add_argument(
        "--ratio",
        type=float,
        default=0.8,
        help="Train ratio (default: 0.8)",
    )
    split_parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for deterministic split (default: 42)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "annotate":
        if not args.crops_dir.is_dir():
            print(f"Error: {args.crops_dir} is not a directory", file=sys.stderr)
            return 1
        annotate(args.crops_dir, args.output)
        return 0

    if args.command == "split":
        if not args.annotations.exists():
            print(f"Error: {args.annotations} not found", file=sys.stderr)
            return 1
        generate_split(args.annotations, args.output, args.ratio, args.seed)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
