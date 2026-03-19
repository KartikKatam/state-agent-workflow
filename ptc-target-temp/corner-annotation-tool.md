# Corner Annotation Tool — Specification

**Date**: 2026-02-17
**Status**: Complete
**Author**: Kartik

---

### Data Annotation

**Target**: 1000 diverse plate crop images with 4 corner keypoints each.

**Diversity requirements**: At least 30% night/low-light images (matching the 70/30 day/night deployment split). Coverage across viewing angles (overhead, oblique), vehicle types (sedans, SUVs, trucks, vans), plate types (standard white California, dark/blue plates, specialty plates), and degradation levels (clean, blurry, compressed, noisy).

**Annotation tool**: Custom OpenCV click-to-annotate script. Specification:
- Displays each crop image at 2-3× zoom for pixel-precise clicking
- User clicks 4 points in any order (the training pipeline handles reordering via centroid-angle canonicalization)
- Each click renders a colored dot on the image for visual feedback (red, green, blue, yellow for clicks 1-4)
- Keyboard controls: Enter = confirm and save, R = redo current image (clear all points), Backspace = undo last click, S = skip image (mark as unusable), Left arrow = go back to previous image for correction
- Saves to JSON: `{"crop_00001.png": {"corners": [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]}, ...}`
- Coordinates are floating-point in original crop pixel space (sub-pixel precision by clicking on zoomed image)
- On startup, loads existing JSON and resumes from the first unannotated image
- Estimated annotation speed: 8-10 seconds per image, ~1000 images in 2.5-3 hours

**Annotation rules** (consistency guidelines):
- Click the point where the plate's physical edge meets the background — not where text starts, not at screws or bolts
- If a corner is occluded (by a hitch, another vehicle, bumper hardware), click where the corner would be by extrapolating the two visible plate edges. If truly impossible to estimate, skip the image.
- The plate's physical boundary, not the printed border or text area, defines the corners
- For plates with rounded corners, click the point where the tangent lines of the two edges would intersect (the "sharp corner" of the underlying rectangle)

### Data Format

**Directory structure**:
```
data/
  crops/              # 1000 plate crop images (PNG or JPG)
    crop_00001.png
    crop_00002.png
    ...
  annotations.json    # Corner keypoints for all images
  split.json          # Train/val assignment (80/20, fixed)
```

**split.json**: 80/20 train/val split, stratified by lighting condition (day/night) if metadata is available. Generated once and fixed — never re-randomized between runs, so validation results are comparable across experiments.

### Decisions Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Annotation tool | Custom OpenCV 4-click script | Fastest path to 1000 annotations. No tool setup overhead. ~3 hours total. |
| Corner ordering | Any click order, pipeline reorders via centroid-angle | Eliminates annotator ordering error. Same function as inference pipeline. |
| Data budget | 1000 images, ≥30% night | Matches 70/30 deployment split. Pretrained encoder + augmentation compensates for dataset size. |
