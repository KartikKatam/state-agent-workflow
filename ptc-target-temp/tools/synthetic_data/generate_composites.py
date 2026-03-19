"""Synthetic composite image generator for corner CNN training data.

Pastes real plate crops onto vehicle-rear backgrounds or random scene patches
to create synthetic training images with known corner annotations.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import cv2
import numpy as np
from tqdm import tqdm

# Add project root to path for imports
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from common.geometry import order_keypoints_by_angle  # noqa: E402

logger = logging.getLogger(__name__)

Corners = list[tuple[float, float]]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_source_plates(
    annotations_path: str | Path,
    split_path: str | Path,
    crops_dir: str | Path,
) -> list[tuple[str, Path, Corners]]:
    """Load train-split plate crops with valid corners.

    Returns list of (image_id, image_path, corners) tuples.
    Only images in split.json["train"] with non-null corners.
    Corners are canonicalized to TL, TR, BR, BL order.
    """
    with open(annotations_path) as f:
        annotations = json.load(f)
    with open(split_path) as f:
        splits = json.load(f)

    train_ids = set(splits["train"])
    crops_dir = Path(crops_dir)
    plates: list[tuple[str, Path, Corners]] = []

    for image_id in train_ids:
        if image_id not in annotations:
            continue
        entry = annotations[image_id]
        raw_corners = entry["corners"] if isinstance(entry, dict) else entry
        if raw_corners is None:
            continue
        corners: Corners = [(float(c[0]), float(c[1])) for c in raw_corners]
        corners = order_keypoints_by_angle(corners)
        img_path = crops_dir / image_id
        if img_path.exists():
            plates.append((image_id, img_path, corners))

    logger.info("Loaded %d source plates from train split", len(plates))
    return plates


def load_vehicle_rears(
    external_dirs: list[str | Path],
) -> list[tuple[Path, tuple[int, int, int, int]]]:
    """Load vehicle images and plate bboxes from YOLO-format datasets.

    Reads YOLO labels, finds the license-plate class bbox, and converts
    from YOLO normalized (cx, cy, w, h) to pixel (x1, y1, x2, y2).

    Returns list of (image_path, plate_bbox_xyxy) tuples.
    """
    # Known plate class names across datasets
    plate_class_names = {"license-plate", "license_plate", "regno", "number-plate"}

    results: list[tuple[Path, tuple[int, int, int, int]]] = []

    for ext_dir in external_dirs:
        ext_dir = Path(ext_dir)
        if not ext_dir.exists():
            logger.warning("External dir not found: %s", ext_dir)
            continue

        # Read data.yaml to find plate class index
        data_yaml_path = ext_dir / "data.yaml"
        plate_class_idx: int | None = None
        if data_yaml_path.exists():
            import yaml

            with open(data_yaml_path) as f:
                data_yaml = yaml.safe_load(f)
            names = data_yaml.get("names", {})
            if isinstance(names, dict):
                for idx, name in names.items():
                    if name.lower().replace(" ", "-") in plate_class_names:
                        plate_class_idx = int(idx)
                        break
            elif isinstance(names, list):
                for idx, name in enumerate(names):
                    if name.lower().replace(" ", "-") in plate_class_names:
                        plate_class_idx = idx
                        break

        if plate_class_idx is None:
            logger.warning("No plate class found in %s", ext_dir)
            continue

        # Scan all splits (train, valid, test)
        for split_name in ["train", "valid", "test"]:
            images_dir = ext_dir / split_name / "images"
            labels_dir = ext_dir / split_name / "labels"
            if not images_dir.exists() or not labels_dir.exists():
                continue

            for img_path in sorted(images_dir.iterdir()):
                if img_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                    continue
                label_path = labels_dir / (img_path.stem + ".txt")
                if not label_path.exists():
                    continue

                # Parse YOLO labels to find plate bbox
                plate_bbox = _parse_yolo_plate_bbox(label_path, img_path, plate_class_idx)
                if plate_bbox is not None:
                    results.append((img_path, plate_bbox))

    logger.info("Loaded %d vehicle rear images with plate bboxes", len(results))
    return results


def _parse_yolo_plate_bbox(
    label_path: Path, img_path: Path, plate_class_idx: int
) -> tuple[int, int, int, int] | None:
    """Parse YOLO label file and return plate bbox in pixel xyxy format."""
    img = cv2.imread(str(img_path))
    if img is None:
        return None
    img_h, img_w = img.shape[:2]

    with open(label_path) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) < 5:
                continue
            cls_id = int(parts[0])
            if cls_id != plate_class_idx:
                continue
            cx, cy, bw, bh = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
            x1 = int((cx - bw / 2) * img_w)
            y1 = int((cy - bh / 2) * img_h)
            x2 = int((cx + bw / 2) * img_w)
            y2 = int((cy + bh / 2) * img_h)
            return (x1, y1, x2, y2)
    return None


def load_scene_backgrounds(au_air_dir: str | Path) -> list[Path]:
    """Index AU-AIR frames for random background cropping.

    Returns list of image paths. Actual cropping happens lazily.
    """
    au_air_dir = Path(au_air_dir)
    paths: list[Path] = []
    if not au_air_dir.exists():
        logger.warning("AU-AIR directory not found: %s", au_air_dir)
        return paths

    images_dir = au_air_dir / "images"
    search_dir = images_dir if images_dir.exists() else au_air_dir

    for p in sorted(search_dir.iterdir()):
        if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
            paths.append(p)

    logger.info("Indexed %d scene background images", len(paths))
    return paths


# ---------------------------------------------------------------------------
# Compositing methods
# ---------------------------------------------------------------------------


def _create_feathered_mask(h: int, w: int, feather_px: int = 3) -> np.ndarray:
    """Create a Gaussian-feathered alpha mask for blending (1.0 inside, smooth falloff at edges).

    Uses cv2.GaussianBlur on a shrunken binary rectangle to produce a smooth
    gradient at the plate boundary instead of a hard linear ramp.
    """
    feather_px = max(2, feather_px)
    # Start with zeros, fill an inner rectangle with 1.0
    mask = np.zeros((h, w), dtype=np.float32)
    inner_top = feather_px
    inner_bot = max(feather_px + 1, h - feather_px)
    inner_left = feather_px
    inner_right = max(feather_px + 1, w - feather_px)
    mask[inner_top:inner_bot, inner_left:inner_right] = 1.0
    # Gaussian blur to create smooth falloff
    ksize = feather_px * 2 + 1
    sigma = feather_px / 2.0
    mask = cv2.GaussianBlur(mask, (ksize, ksize), sigma)
    # Normalize so center is 1.0
    max_val = mask.max()
    if max_val > 0:
        mask = mask / max_val
    return mask


def _match_lighting(plate: np.ndarray, bg_roi: np.ndarray, strength: float = 0.6) -> np.ndarray:
    """Adjust plate brightness to match background region using LAB L-channel.

    Shifts the plate's luminance toward the background's mean/std while
    preserving the plate's color (a*, b* channels) and text legibility.
    ``strength`` controls how aggressively to match (0 = no change, 1 = full match).
    """
    if plate.size == 0 or bg_roi.size == 0 or strength <= 0:
        return plate

    plate_lab = cv2.cvtColor(plate, cv2.COLOR_BGR2LAB).astype(np.float32)
    bg_lab = cv2.cvtColor(bg_roi, cv2.COLOR_BGR2LAB).astype(np.float32)

    p_mean = plate_lab[:, :, 0].mean()
    p_std = max(plate_lab[:, :, 0].std(), 1.0)
    b_mean = bg_lab[:, :, 0].mean()
    b_std = max(bg_lab[:, :, 0].std(), 1.0)

    # Blend toward background stats with damping
    target_mean = p_mean + (b_mean - p_mean) * strength
    target_std = p_std + (b_std - p_std) * strength

    plate_lab[:, :, 0] = (plate_lab[:, :, 0] - p_mean) * (target_std / p_std) + target_mean
    plate_lab[:, :, 0] = np.clip(plate_lab[:, :, 0], 0, 255)

    return cv2.cvtColor(plate_lab.astype(np.uint8), cv2.COLOR_LAB2BGR)


def composite_on_vehicle_rear(
    plate_img: np.ndarray,
    plate_corners: Corners,
    vehicle_img: np.ndarray,
    plate_bbox: tuple[int, int, int, int],
    rng: np.random.RandomState,
) -> tuple[np.ndarray, Corners]:
    """Paste plate crop onto a vehicle rear with existing plate masked out.

    Steps:
    1. Expand plate_bbox by 10-20% to get vehicle-rear context region
    2. Crop that region from vehicle_img
    3. Mask the original plate with surrounding mean color + blur
    4. Resize plate to fit, apply perspective warp, alpha-blend
    5. Compute new corners through transforms

    Returns: (composite_img, new_corners)
    """
    veh_h, veh_w = vehicle_img.shape[:2]
    x1, y1, x2, y2 = plate_bbox

    # 1. Expand bbox by 25-50% for more vehicle context
    expand = rng.uniform(0.25, 0.50)
    bw = x2 - x1
    bh = y2 - y1
    ex1 = max(0, int(x1 - bw * expand))
    ey1 = max(0, int(y1 - bh * expand))
    ex2 = min(veh_w, int(x2 + bw * expand))
    ey2 = min(veh_h, int(y2 + bh * expand))

    # 2. Crop context region
    context = vehicle_img[ey1:ey2, ex1:ex2].copy()
    ctx_h, ctx_w = context.shape[:2]
    if ctx_h < 10 or ctx_w < 10:
        # Fallback: too small, use scene-patch style
        return _composite_on_solid_patch(plate_img, plate_corners, rng)

    # 3. Mask original plate area within context crop
    local_x1 = x1 - ex1
    local_y1 = y1 - ey1
    local_x2 = x2 - ex1
    local_y2 = y2 - ey1
    local_x1 = max(0, min(local_x1, ctx_w))
    local_y1 = max(0, min(local_y1, ctx_h))
    local_x2 = max(0, min(local_x2, ctx_w))
    local_y2 = max(0, min(local_y2, ctx_h))

    # Inpaint the original plate region using surrounding texture
    inpaint_mask = np.zeros((ctx_h, ctx_w), dtype=np.uint8)
    inpaint_mask[local_y1:local_y2, local_x1:local_x2] = 255
    context = cv2.inpaint(context, inpaint_mask, inpaintRadius=5, flags=cv2.INPAINT_TELEA)

    # 4. Resize plate to fit masked region with scale jitter
    plate_h, plate_w = plate_img.shape[:2]
    target_w = local_x2 - local_x1
    target_h = local_y2 - local_y1
    if target_w < 4 or target_h < 4:
        return _composite_on_solid_patch(plate_img, plate_corners, rng)

    # Aspect-ratio-preserving resize with jitter (ensure plate covers masked area)
    scale_jitter = rng.uniform(0.95, 1.3)
    scale = min(target_w / plate_w, target_h / plate_h) * scale_jitter
    new_w = max(4, int(plate_w * scale))
    new_h = max(4, int(plate_h * scale))
    resized_plate = cv2.resize(plate_img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    # 5. Apply slight random perspective warp
    jitter_range = min(new_w, new_h) * 0.05
    H = _generate_perspective_H_local(new_w, new_h, jitter_range, rng)
    warped_plate = cv2.warpPerspective(
        resized_plate, H, (new_w, new_h), borderMode=cv2.BORDER_REFLECT_101
    )

    # 6. Compute paste location (center in masked region)
    paste_x = local_x1 + (target_w - new_w) // 2
    paste_y = local_y1 + (target_h - new_h) // 2
    paste_x = max(0, min(paste_x, ctx_w - new_w))
    paste_y = max(0, min(paste_y, ctx_h - new_h))

    # 6b. Match plate lighting to background region
    bg_roi = context[paste_y : paste_y + new_h, paste_x : paste_x + new_w]
    if bg_roi.shape[0] == new_h and bg_roi.shape[1] == new_w:
        warped_plate = _match_lighting(warped_plate, bg_roi)

    # Alpha-blend with Gaussian-feathered edge (scale with plate size)
    feather_px = max(5, int(min(new_w, new_h) * 0.08))
    mask = _create_feathered_mask(new_h, new_w, feather_px=feather_px)
    roi = context[paste_y : paste_y + new_h, paste_x : paste_x + new_w]
    if roi.shape[0] != new_h or roi.shape[1] != new_w:
        return _composite_on_solid_patch(plate_img, plate_corners, rng)
    mask_3ch = mask[:, :, np.newaxis]
    blended = warped_plate.astype(np.float32) * mask_3ch + roi.astype(np.float32) * (1 - mask_3ch)
    context[paste_y : paste_y + new_h, paste_x : paste_x + new_w] = blended.astype(np.uint8)

    # 7. Compute new corners
    new_corners = _transform_corners(plate_corners, H, scale, paste_x, paste_y)

    return context, new_corners


def composite_on_scene_patch(
    plate_img: np.ndarray,
    plate_corners: Corners,
    scene_img_path: Path,
    rng: np.random.RandomState,
) -> tuple[np.ndarray, Corners]:
    """Paste plate crop onto a random scene background patch.

    Steps:
    1. Load scene image, random-crop a patch ~1.5-2x plate size
    2. Apply random perspective warp + scale to plate
    3. Center plate on patch with random offset jitter
    4. Alpha-blend with feathered edge
    5. Compute new corners

    Returns: (composite_img, new_corners)
    """
    scene_img = cv2.imread(str(scene_img_path))
    if scene_img is None:
        # Fallback to solid color patch
        return _composite_on_solid_patch(plate_img, plate_corners, rng)

    plate_h, plate_w = plate_img.shape[:2]
    scene_h, scene_w = scene_img.shape[:2]

    # 1. Random-crop a patch ~1.5-2x the plate size
    patch_scale = rng.uniform(1.5, 2.0)
    patch_w = min(int(plate_w * patch_scale), scene_w)
    patch_h = min(int(plate_h * patch_scale), scene_h)

    crop_x = rng.randint(0, max(1, scene_w - patch_w))
    crop_y = rng.randint(0, max(1, scene_h - patch_h))
    patch = scene_img[crop_y : crop_y + patch_h, crop_x : crop_x + patch_w].copy()

    return _paste_plate_on_patch(plate_img, plate_corners, patch, rng)


def _composite_on_solid_patch(
    plate_img: np.ndarray,
    plate_corners: Corners,
    rng: np.random.RandomState,
) -> tuple[np.ndarray, Corners]:
    """Fallback: paste plate on a random solid-color patch."""
    plate_h, plate_w = plate_img.shape[:2]
    patch_scale = rng.uniform(1.5, 2.0)
    patch_w = int(plate_w * patch_scale)
    patch_h = int(plate_h * patch_scale)
    color = rng.randint(40, 200, 3).tolist()
    patch = np.full((patch_h, patch_w, 3), color, dtype=np.uint8)
    # Add some noise texture
    noise = rng.randint(0, 20, (patch_h, patch_w, 3), dtype=np.uint8)
    patch = np.clip(patch.astype(np.int16) + noise.astype(np.int16), 0, 255).astype(np.uint8)
    return _paste_plate_on_patch(plate_img, plate_corners, patch, rng)


def _paste_plate_on_patch(
    plate_img: np.ndarray,
    plate_corners: Corners,
    patch: np.ndarray,
    rng: np.random.RandomState,
) -> tuple[np.ndarray, Corners]:
    """Common logic: resize + warp plate, paste onto patch, compute corners."""
    plate_h, plate_w = plate_img.shape[:2]
    patch_h, patch_w = patch.shape[:2]

    # Scale plate to fit patch (0.7-1.3x of available space)
    scale_jitter = rng.uniform(0.7, 1.3)
    scale = min((patch_w * 0.8) / plate_w, (patch_h * 0.8) / plate_h) * scale_jitter
    scale = max(0.3, min(scale, 2.0))  # clamp to reasonable range
    new_w = max(8, int(plate_w * scale))
    new_h = max(4, int(plate_h * scale))

    # Ensure plate fits in patch
    new_w = min(new_w, patch_w - 2)
    new_h = min(new_h, patch_h - 2)

    resized_plate = cv2.resize(plate_img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    # Apply slight random perspective warp
    jitter_range = min(new_w, new_h) * 0.05
    H = _generate_perspective_H_local(new_w, new_h, jitter_range, rng)
    warped_plate = cv2.warpPerspective(
        resized_plate, H, (new_w, new_h), borderMode=cv2.BORDER_REFLECT_101
    )

    # Center with random offset jitter
    max_jitter_x = max(0, (patch_w - new_w) // 4)
    max_jitter_y = max(0, (patch_h - new_h) // 4)
    paste_x = (patch_w - new_w) // 2 + (
        rng.randint(-max_jitter_x, max_jitter_x + 1) if max_jitter_x > 0 else 0
    )
    paste_y = (patch_h - new_h) // 2 + (
        rng.randint(-max_jitter_y, max_jitter_y + 1) if max_jitter_y > 0 else 0
    )
    paste_x = max(0, min(paste_x, patch_w - new_w))
    paste_y = max(0, min(paste_y, patch_h - new_h))

    # Match plate lighting to background region
    bg_roi = patch[paste_y : paste_y + new_h, paste_x : paste_x + new_w]
    if bg_roi.shape[0] == new_h and bg_roi.shape[1] == new_w:
        warped_plate = _match_lighting(warped_plate, bg_roi)

    # Alpha-blend with Gaussian-feathered edge (scale with plate size)
    feather_px = max(5, int(min(new_w, new_h) * 0.08))
    mask = _create_feathered_mask(new_h, new_w, feather_px=feather_px)
    roi = patch[paste_y : paste_y + new_h, paste_x : paste_x + new_w]
    mask_3ch = mask[:, :, np.newaxis]
    blended = warped_plate.astype(np.float32) * mask_3ch + roi.astype(np.float32) * (1 - mask_3ch)
    patch[paste_y : paste_y + new_h, paste_x : paste_x + new_w] = blended.astype(np.uint8)

    # Compute new corners
    new_corners = _transform_corners(plate_corners, H, scale, paste_x, paste_y)

    return patch, new_corners


def _generate_perspective_H_local(
    img_w: int, img_h: int, jitter_range: float, rng: np.random.RandomState
) -> np.ndarray:
    """Generate a perspective transform matrix from jittered image corners."""
    src_pts = np.array(
        [[0, 0], [img_w - 1, 0], [img_w - 1, img_h - 1], [0, img_h - 1]],
        dtype=np.float32,
    )
    jitter = rng.uniform(-jitter_range, jitter_range, (4, 2)).astype(np.float32)
    dst_pts = src_pts + jitter
    H: np.ndarray = cv2.getPerspectiveTransform(src_pts, dst_pts)
    return H


def _transform_corners(
    original_corners: Corners,
    H: np.ndarray,
    scale: float,
    paste_x: int,
    paste_y: int,
) -> Corners:
    """Transform original plate corners through scale + warp + offset.

    For each corner:
    1. Scale: scaled = original * scale
    2. Apply perspective warp H: warped = H @ [scaled_x, scaled_y, 1]; divide by w'
    3. Offset: final = warped + (paste_x, paste_y)
    """
    new_corners: Corners = []
    for x, y in original_corners:
        # Scale
        sx = x * scale
        sy = y * scale
        # Perspective warp
        vec = np.array([sx, sy, 1.0])
        result = H @ vec
        w_prime = result[2]
        wx = float(result[0] / w_prime)
        wy = float(result[1] / w_prime)
        # Offset
        new_corners.append((wx + paste_x, wy + paste_y))
    return new_corners


# ---------------------------------------------------------------------------
# Post-composite realism
# ---------------------------------------------------------------------------


def _apply_post_composite_degradation(
    composite: np.ndarray, rng: np.random.RandomState
) -> np.ndarray:
    """Apply light uniform noise + JPEG compression to equalize plate and background.

    This makes the pasted plate share the same noise floor and compression
    artifacts as the surrounding background, preventing the plate from looking
    "too clean." Kept intentionally subtle so training-time augmentations
    still add meaningful diversity.
    """
    # Light Gaussian noise (sigma 2-5)
    sigma = rng.uniform(2.0, 5.0)
    noise = rng.randn(*composite.shape) * sigma
    out = np.clip(composite.astype(np.float32) + noise, 0, 255).astype(np.uint8)

    # JPEG round-trip (quality 75-90)
    quality = rng.randint(75, 91)
    _, encoded = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, quality])
    decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if decoded is None:
        return out
    return decoded


# ---------------------------------------------------------------------------
# Size normalization
# ---------------------------------------------------------------------------


def _normalize_composite_size(
    composite: np.ndarray,
    corners: Corners,
    min_w: int = 80,
    max_w: int = 300,
    min_h: int = 50,
    max_h: int = 180,
) -> tuple[np.ndarray, Corners] | None:
    """Resize composites to match real training crop size distribution.

    Downscales oversized composites and rejects undersized ones.
    Corner coordinates are scaled proportionally.
    Real data distribution: width p10=97 median=168 p90=227,
    height p10=74 median=100 p90=140.
    """
    h, w = composite.shape[:2]

    # Reject if too small (can't upscale without quality loss)
    if w < min_w or h < min_h:
        return None

    # Downscale if too large
    if w > max_w or h > max_h:
        scale = min(max_w / w, max_h / h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        resized = cv2.resize(composite, (new_w, new_h), interpolation=cv2.INTER_AREA)
        scaled_corners: Corners = [(x * scale, y * scale) for x, y in corners]
        return resized, scaled_corners

    return composite, corners


# ---------------------------------------------------------------------------
# Quality validation
# ---------------------------------------------------------------------------


def validate_composite(corners: Corners, img_w: int, img_h: int) -> bool:
    """Reject bad composites.

    Checks:
    1. All 4 corners within image bounds [0, w) x [0, h)
    2. Plate width (max_x - min_x) >= 8px
    3. Plate height (max_y - min_y) >= 4px
    4. Corners form convex quadrilateral (cross-product test)
    5. Corner ordering preserved (TL has smallest x+y)
    """
    if len(corners) != 4:
        return False

    # 1. Bounds check
    for x, y in corners:
        if x < 0 or x >= img_w or y < 0 or y >= img_h:
            return False

    # 2-3. Size check
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    if max(xs) - min(xs) < 8:
        return False
    if max(ys) - min(ys) < 4:
        return False

    # 4. Convex quadrilateral check (cross-product test)
    for i in range(4):
        p1 = corners[i]
        p2 = corners[(i + 1) % 4]
        p3 = corners[(i + 2) % 4]
        cross = (p2[0] - p1[0]) * (p3[1] - p2[1]) - (p2[1] - p1[1]) * (p3[0] - p2[0])
        if cross < 0:
            return False

    # 5. TL should have smallest x+y
    sums = [c[0] + c[1] for c in corners]
    if sums[0] != min(sums):
        return False

    return True


# ---------------------------------------------------------------------------
# Main generation loop
# ---------------------------------------------------------------------------


def generate_all(args: argparse.Namespace) -> None:
    """Main entry point for composite generation."""
    # Load source data
    plates = load_source_plates(args.annotations_path, args.split_path, args.crops_dir)
    if not plates:
        logger.error("No source plates found. Check paths.")
        return

    # Load vehicle rears (optional)
    vehicle_rears: list[tuple[Path, tuple[int, int, int, int]]] = []
    if args.vehicle_rear_dirs:
        vehicle_rears = load_vehicle_rears(args.vehicle_rear_dirs)

    # Load scene backgrounds (optional)
    scene_backgrounds: list[Path] = []
    if args.scene_bg_dir:
        scene_backgrounds = load_scene_backgrounds(args.scene_bg_dir)

    # Adjust vehicle_rear_ratio if no data available
    vehicle_rear_ratio = args.vehicle_rear_ratio
    if not vehicle_rears:
        logger.warning("No vehicle rear images found. Using scene-patch-only mode.")
        vehicle_rear_ratio = 0.0
    if not scene_backgrounds and vehicle_rear_ratio < 1.0:
        logger.warning("No scene backgrounds found. Using solid-color fallback for scene patches.")

    # Verify NO val images in source plates
    with open(args.split_path) as f:
        splits = json.load(f)
    val_set = set(splits["val"])
    for plate_id, _, _ in plates:
        if plate_id in val_set:
            raise ValueError(f"Data leakage: train plate '{plate_id}' found in val split!")

    # Output dir
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    annotations: dict[str, dict[str, list[list[float]]]] = {}
    manifest_entries: list[dict[str, object]] = []

    logger.info(
        "Generating %d composites (%.0f%% vehicle rear, %.0f%% scene patch)",
        args.num_images,
        vehicle_rear_ratio * 100,
        (1 - vehicle_rear_ratio) * 100,
    )

    for i in tqdm(range(args.num_images), desc="Generating composites"):
        seed = args.seed + i
        rng = np.random.RandomState(seed)

        synth_id = f"synth_{i:05d}.jpg"
        out_path = output_dir / synth_id

        # Skip existing (idempotent)
        if out_path.exists():
            continue

        # Pick random source plate
        plate_idx = rng.randint(0, len(plates))
        plate_id, plate_path, plate_corners = plates[plate_idx]
        plate_img = cv2.imread(str(plate_path))
        if plate_img is None:
            continue

        method = "scene_patch"
        composite = None
        new_corners = None

        for _attempt in range(5):
            if rng.random() < vehicle_rear_ratio and vehicle_rears:
                # Vehicle rear composite
                vr_idx = rng.randint(0, len(vehicle_rears))
                vr_path, vr_bbox = vehicle_rears[vr_idx]
                vr_img = cv2.imread(str(vr_path))
                if vr_img is None:
                    continue
                composite, new_corners = composite_on_vehicle_rear(
                    plate_img, plate_corners, vr_img, vr_bbox, rng
                )
                method = "vehicle_rear"
            elif scene_backgrounds:
                # Scene patch composite
                sc_idx = rng.randint(0, len(scene_backgrounds))
                composite, new_corners = composite_on_scene_patch(
                    plate_img, plate_corners, scene_backgrounds[sc_idx], rng
                )
                method = "scene_patch"
            else:
                # Solid color fallback
                composite, new_corners = _composite_on_solid_patch(plate_img, plate_corners, rng)
                method = "solid_patch"

            if composite is not None and new_corners is not None:
                ch, cw = composite.shape[:2]
                if validate_composite(new_corners, cw, ch):
                    break
                composite = None
                new_corners = None

        if composite is None or new_corners is None:
            continue

        # Normalize size to match real training crop distribution
        size_result = _normalize_composite_size(
            composite,
            new_corners,
            min_w=args.min_width,
            max_w=args.max_width,
            min_h=args.min_height,
            max_h=args.max_height,
        )
        if size_result is None:
            continue
        composite, new_corners = size_result

        # Re-validate after resize
        ch, cw = composite.shape[:2]
        if not validate_composite(new_corners, cw, ch):
            continue

        # Apply uniform noise + compression for plate/background consistency
        composite = _apply_post_composite_degradation(composite, rng)

        # Save composite
        cv2.imwrite(str(out_path), composite, [cv2.IMWRITE_JPEG_QUALITY, 85])

        # Record annotation (TL, TR, BR, BL order)
        annotations[synth_id] = {"corners": [[round(x, 4), round(y, 4)] for x, y in new_corners]}

        # Record manifest
        manifest_entries.append(
            {
                "synthetic_id": synth_id,
                "source_id": plate_id,
                "method": method,
                "seed": seed,
            }
        )

    # Write annotations
    annotations_out = Path(args.annotations_out)
    with open(annotations_out, "w") as f:
        json.dump(annotations, f, indent=2)

    # Write manifest
    manifest_out = Path(args.manifest_out)
    with open(manifest_out, "w") as f:
        for entry in manifest_entries:
            f.write(json.dumps(entry) + "\n")

    logger.info(
        "Generated %d composites. Annotations: %s, Manifest: %s",
        len(annotations),
        annotations_out,
        manifest_out,
    )

    # Print summary
    methods = {}
    for entry in manifest_entries:
        m = entry["method"]
        methods[m] = methods.get(m, 0) + 1
    print(f"\nSummary: {len(annotations)} composites generated")
    for m, count in sorted(methods.items()):
        print(f"  {m}: {count} ({count / len(annotations) * 100:.1f}%)")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Generate synthetic composite images")
    parser.add_argument("--num-images", type=int, default=12000)
    parser.add_argument("--vehicle-rear-ratio", type=float, default=0.7)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default="data/synthetic_crops")
    parser.add_argument("--annotations-out", default="data/synthetic_annotations.json")
    parser.add_argument("--manifest-out", default="data/synthetic_manifest.jsonl")
    parser.add_argument("--annotations-path", default="data/annotations.json")
    parser.add_argument("--split-path", default="data/split.json")
    parser.add_argument("--crops-dir", default="data/crops")
    parser.add_argument(
        "--vehicle-rear-dirs",
        nargs="*",
        default=["data/external/plat-kendaraan", "data/external/kongu-vehicle-plate"],
    )
    parser.add_argument("--scene-bg-dir", default="data/external/au-air")
    # Size normalization (match real training crop distribution)
    parser.add_argument("--min-width", type=int, default=80)
    parser.add_argument("--max-width", type=int, default=300)
    parser.add_argument("--min-height", type=int, default=50)
    parser.add_argument("--max-height", type=int, default=180)

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    generate_all(args)


if __name__ == "__main__":
    main()
