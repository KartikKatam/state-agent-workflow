# Corner Annotation Tool — Usage Guide

## Quick Start

Run all commands from the project root (`LPR-SingleDrone/`):

```bash
# Annotate corners on crop images
python -m tools.corner_annotator annotate <crops_folder> <output_json>

# Generate train/val split
python -m tools.corner_annotator split <annotations_json> <output_split_json>

# Help
python -m tools.corner_annotator --help
```

### Example

```bash
python -m tools.corner_annotator annotate data/crops data/annotations.json
python -m tools.corner_annotator split data/annotations.json data/split.json
```

## Annotation Controls

| Key         | Action                                      |
|-------------|---------------------------------------------|
| Left click  | Place corner point (up to 4)                |
| Enter       | Confirm and save (requires exactly 4 points)|
| Backspace   | Undo last click                             |
| R           | Redo current image (clear all points)       |
| S           | Skip image (mark as unusable)               |
| Left arrow  | Go back to previous image for correction    |
| Q / Escape  | Quit and save progress                      |

Clicks are shown as colored dots: red (1st), green (2nd), blue (3rd), yellow (4th).

Images display at 3x zoom by default. Use `--zoom 2` for less zoom on large images.

## Annotation Rules

- Click the point where the plate's **physical edge** meets the background — not where text starts, not at screws or bolts.
- If a corner is occluded, click where the corner **would be** by extrapolating the two visible plate edges. If truly impossible to estimate, press S to skip.
- The plate's physical boundary, not the printed border or text area, defines the corners.
- For rounded corners, click where the tangent lines of the two edges would intersect (the "sharp corner" of the underlying rectangle).
- Click corners in **any order** — the training pipeline reorders via centroid-angle canonicalization.

## Resume Behavior

The tool auto-saves after every image. If you quit and re-run the same command, it loads the existing JSON and resumes from the first unannotated image. Already-annotated images are skipped.

## Output Format

### annotations.json

```json
{
  "crop_00001.png": {
    "corners": [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
  },
  "crop_00002.png": {
    "corners": null,
    "skipped": true
  }
}
```

- Coordinates are **floating-point** in original image pixel space (sub-pixel precision from clicking on the zoomed view).
- Skipped images have `"corners": null` and `"skipped": true`.

### split.json

```json
{
  "train": ["crop_00001.png", "crop_00003.png", ...],
  "val": ["crop_00002.png", ...]
}
```

- 80/20 train/val split (configurable with `--ratio`).
- Deterministic — same seed always produces the same split (default seed: 42).
- Stratifies by day/night if annotations contain a `"night": true` flag.
- Skipped images are excluded from the split.

## Using Annotations for Training

### Expected directory layout

```
data/
  crops/              # plate crop images (PNG or JPG)
    crop_00001.png
    crop_00002.png
    ...
  annotations.json    # output from the annotate command
  split.json          # output from the split command
```

### Loading in a training script

```python
import json
from pathlib import Path

data_dir = Path("data")

# Load annotations and split
with open(data_dir / "annotations.json") as f:
    annotations = json.load(f)

with open(data_dir / "split.json") as f:
    split = json.load(f)

# Build dataset
for image_name in split["train"]:
    corners = annotations[image_name]["corners"]  # [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
    image_path = data_dir / "crops" / image_name

    # corners are in original image pixel coordinates (float)
    # normalize to [0, 1] by dividing by image width/height if needed
    # reorder corners via centroid-angle canonicalization before training
```

### Corner ordering for training

Corners are saved in **click order** (arbitrary). Before training, canonicalize the order:

1. Compute the centroid of the 4 points.
2. Compute the angle of each point relative to the centroid.
3. Sort by angle to get a consistent winding order (e.g., top-left first, clockwise).

This is the same canonicalization used in the inference pipeline, so train and inference use identical ordering.

### Coordinate normalization

Coordinates are in absolute pixel space. To normalize to `[0, 1]`:

```python
import cv2

img = cv2.imread(str(image_path))
h, w = img.shape[:2]
normalized = [[x / w, y / h] for x, y in corners]
```
