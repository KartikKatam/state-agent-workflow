from __future__ import annotations

import cv2
import numpy as np

from common.geometry import order_keypoints_by_angle as order_keypoints_by_angle
from producer.models import BinSnapshot, RoiFastQuality, RoiImage

from .config import ConsumerConfig
from .models import RichQualityMetrics, RoiRichQuality

# =============================================================================
# 0) Canonical Crop Creation
# =============================================================================


def create_canonical_crop(
    crop_bgr: np.ndarray, target_w: int, target_h: int
) -> tuple[np.ndarray, np.ndarray]:
    """
    Resize crop to canonical size with letterboxing to preserve aspect ratio.

    Args:
        crop_bgr: Original BGR crop (variable size)
        target_w: Target width (e.g., 256)
        target_h: Target height (e.g., 128)

    Returns:
        (letterboxed_crop, pad_mask)
        - letterboxed_crop: (target_h, target_w, 3) uint8 BGR
        - pad_mask: (target_h, target_w) bool, True = real pixels, False = padding
    """
    h, w = crop_bgr.shape[:2]

    # Compute scale to fit inside target while preserving AR
    scale = min(target_w / w, target_h / h)
    new_w = int(w * scale)
    new_h = int(h * scale)

    # Resize
    resized = cv2.resize(crop_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    # Create black canvas and paste resized image centered
    canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    y_offset = (target_h - new_h) // 2
    x_offset = (target_w - new_w) // 2
    canvas[y_offset : y_offset + new_h, x_offset : x_offset + new_w] = resized

    # Create padding mask (True = real pixels, False = padding)
    pad_mask = np.zeros((target_h, target_w), dtype=bool)
    pad_mask[y_offset : y_offset + new_h, x_offset : x_offset + new_w] = True

    return canvas, pad_mask


def convert_to_lab_l(crop_bgr: np.ndarray) -> np.ndarray:
    """
    Convert BGR crop to LAB color space and extract L channel.

    Args:
        crop_bgr: BGR image (H, W, 3) uint8

    Returns:
        L channel (H, W) uint8, range [0, 255]
    """
    lab = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2LAB)
    return lab[:, :, 0]  # L channel


# =============================================================================
# 1) Hard Viability Checks (Geometric - Original Crop)
# =============================================================================


def compute_plate_size_and_clip(roi: RoiImage, cfg: ConsumerConfig) -> tuple[float, float, float]:
    """
    Compute plate dimensions and crop clipping fraction.

    Args:
        roi: RoiImage with bbox and padded_bbox
        cfg: ConsumerConfig (unused here, for consistency)

    Returns:
        (plate_width_px, plate_height_px, crop_clip_fraction)

    Note:
        crop_clip_fraction is currently always 0.0 because Producer already
        clamps padded_bbox to frame boundaries during cropping. To compute
        actual clipping, Producer would need to pass pre-clamp bbox metadata.
    """
    # Plate size from original bbox
    x1, y1, x2, y2 = roi.bbox
    plate_w = x2 - x1
    plate_h = y2 - y1

    # Clip fraction: always 0 for now (producer already handles clipping during crop)
    # Future: if producer passes frame_width/frame_height, compute actual clipping:
    #   clipped_area = compute_bbox_clipping(roi.padded_bbox, frame_width, frame_height)
    #   total_area = (padded_bbox[2] - padded_bbox[0]) * (padded_bbox[3] - padded_bbox[1])
    #   clip_frac = clipped_area / total_area
    clip_frac = 0.0

    return plate_w, plate_h, clip_frac


# =============================================================================
# 2) Keypoint Ordering + Homography Sanity Gate
# =============================================================================


def is_quad_convex(quad: list[tuple[float, float]]) -> bool:
    """
    Check if quad is convex using cross-product sign consistency.

    Args:
        quad: [TL, TR, BR, BL] ordered points

    Returns:
        True if convex, False otherwise
    """

    def cross_product_sign(p1, p2, p3):
        """Sign of cross product (p2-p1) × (p3-p2)"""
        v1 = (p2[0] - p1[0], p2[1] - p1[1])
        v2 = (p3[0] - p2[0], p3[1] - p2[1])
        return v1[0] * v2[1] - v1[1] * v2[0]

    # Check all 4 edges (cyclic)
    signs = []
    for i in range(4):
        p1 = quad[i]
        p2 = quad[(i + 1) % 4]
        p3 = quad[(i + 2) % 4]
        signs.append(cross_product_sign(p1, p2, p3))

    # All signs must be same (all positive or all negative)
    return all(s > 0 for s in signs) or all(s < 0 for s in signs)


def compute_quad_area(quad: list[tuple[float, float]]) -> float:
    """
    Compute area of quadrilateral using shoelace formula.

    Args:
        quad: [TL, TR, BR, BL] ordered points

    Returns:
        Area in pixels^2
    """
    x = [pt[0] for pt in quad]
    y = [pt[1] for pt in quad]

    area = 0.5 * abs(
        x[0] * y[1]
        - x[1] * y[0]
        + x[1] * y[2]
        - x[2] * y[1]
        + x[2] * y[3]
        - x[3] * y[2]
        + x[3] * y[0]
        - x[0] * y[3]
    )
    return area


def compute_edge_lengths(quad: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """
    Compute lengths of all 4 edges.

    Args:
        quad: [TL, TR, BR, BL] ordered points

    Returns:
        (top_len, right_len, bottom_len, left_len)
    """
    tl, tr, br, bl = quad

    top_len = np.hypot(tr[0] - tl[0], tr[1] - tl[1])
    right_len = np.hypot(br[0] - tr[0], br[1] - tr[1])
    bottom_len = np.hypot(br[0] - bl[0], br[1] - bl[1])
    left_len = np.hypot(bl[0] - tl[0], bl[1] - tl[1])

    return top_len, right_len, bottom_len, left_len


def compute_edge_ratio(edge_lengths: tuple[float, float, float, float]) -> float:
    """
    Compute max/min edge ratio to detect degenerate quads.

    Catches keypoint collapse, mis-detection, and extreme skew that would
    destabilize homography computation.

    Args:
        edge_lengths: (top, right, bottom, left)

    Returns:
        max(edges) / min(edges)
    """
    max_edge = max(edge_lengths)
    min_edge = min(edge_lengths)

    if min_edge < 1e-6:
        return float("inf")

    return max_edge / min_edge


def compute_aspect_ratio(edge_lengths: tuple[float, float, float, float]) -> float:
    """
    Estimate aspect ratio from quad edge lengths.

    AR = mean(top, bottom) / mean(left, right)
    US plates: ~2:1 (width:height)

    Args:
        edge_lengths: (top, right, bottom, left)

    Returns:
        Estimated aspect ratio
    """
    top, right, bottom, left = edge_lengths

    mean_horiz = (top + bottom) / 2.0
    mean_vert = (left + right) / 2.0

    if mean_vert < 1e-6:
        return float("inf")

    return mean_horiz / mean_vert


def compute_skew_degrees(
    edge_lengths: tuple[float, float, float, float], quad: list[tuple[float, float]]
) -> float:
    """
    Compute skew angle (rotation from horizontal) using top/bottom edge angles.

    Args:
        edge_lengths: (top, right, bottom, left) - unused but kept for consistency
        quad: [TL, TR, BR, BL] ordered points

    Returns:
        Median angle in degrees (range typically -45 to +45)
    """
    tl, tr, br, bl = quad

    # Top edge angle
    top_angle = np.degrees(np.arctan2(tr[1] - tl[1], tr[0] - tl[0]))

    # Bottom edge angle
    bottom_angle = np.degrees(np.arctan2(br[1] - bl[1], br[0] - bl[0]))

    # Median of two
    skew = float(np.median([top_angle, bottom_angle]))

    return skew


def compute_perspective_score(edge_lengths: tuple[float, float, float, float]) -> float:
    """
    Compute perspective severity as max distortion ratio.

    Higher score = more perspective distortion (one edge much longer than opposite).

    Args:
        edge_lengths: (top, right, bottom, left)

    Returns:
        max(persp_lr, persp_tb) where persp = max/min of opposite edges
    """
    top, right, bottom, left = edge_lengths

    # Left/right perspective
    if min(left, right) < 1e-6:
        persp_lr = float("inf")
    else:
        persp_lr = max(left, right) / min(left, right)

    # Top/bottom perspective
    if min(top, bottom) < 1e-6:
        persp_tb = float("inf")
    else:
        persp_tb = max(top, bottom) / min(top, bottom)

    return max(persp_lr, persp_tb)


def compute_perspective_direction(edge_lengths: tuple[float, float, float, float]) -> float:
    """
    Compute signed perspective direction from YOLO-Pose quad edges.

    Knowing direction enables diversity—two images with opposite perspective
    show different character profiles (left vs right viewing angle).

    Args:
        edge_lengths: (top, right, bottom, left) from YOLO-Pose keypoints

    Returns:
        perspective_direction in [-1, 1]:
        - Negative: left edge longer (viewing from right)
        - Positive: right edge longer (viewing from left)
        - Zero: symmetric (head-on)
    """
    top, right, bottom, left = edge_lengths

    # Horizontal imbalance (primary for license plates)
    # Right longer → viewing from left (+)
    # Left longer → viewing from right (-)
    horiz_imbalance = (right - left) / (right + left + 1e-6)

    # Clip to [-1, 1] and scale by 2 for better dynamic range
    return float(np.clip(horiz_imbalance * 2, -1.0, 1.0))


def validate_quad_geometry(
    quad: list[tuple[float, float]], cfg: ConsumerConfig
) -> tuple[bool, float, float]:
    """
    Run all geometric sanity checks on quad.

    Args:
        quad: [TL, TR, BR, BL] ordered points
        cfg: ConsumerConfig with thresholds

    Returns:
        (geometry_valid, quad_area, edge_ratio)
    """
    # 1. Convexity check
    if not is_quad_convex(quad):
        return False, 0.0, float("inf")

    # 2. Area check
    area = compute_quad_area(quad)
    if area < cfg.quad_area_min_px:
        return False, area, float("inf")

    # 3. Edge lengths and ratio
    edge_lengths = compute_edge_lengths(quad)
    edge_ratio = compute_edge_ratio(edge_lengths)
    if edge_ratio > cfg.edge_ratio_max:
        return False, area, edge_ratio

    # 4. Aspect ratio plausibility (soft check, not hard fail)
    aspect_ratio = compute_aspect_ratio(edge_lengths)
    if not (cfg.aspect_ratio_min <= aspect_ratio <= cfg.aspect_ratio_max):
        # Treat as geometry invalid (prevents bad homography)
        return False, area, edge_ratio

    return True, area, edge_ratio


# =============================================================================
# 3) Pose Metrics (Geometric - Original Crop)
# =============================================================================


def compute_pose_metrics(
    keypoints: list[tuple[float, float]] | None,
    keypoint_scores: list[float] | None,
    roi: RoiImage,
    cfg: ConsumerConfig,
) -> tuple[
    list[tuple[float, float]] | None,  # ordered_quad or None
    float | None,  # kp_conf_min
    float | None,  # kp_conf_mean
    float | None,  # skew_deg
    float | None,  # persp_score
    float | None,  # persp_direction
    float | None,  # quad_area
    float | None,  # edge_ratio
    bool,  # homography_eligible
]:
    """
    Compute pose/viewpoint metrics from YOLO-Pose keypoints.

    Args:
        keypoints: 4 corners or None
        keypoint_scores: per-kp confidence or None
        roi: RoiImage (for bbox size)
        cfg: ConsumerConfig

    Returns:
        (ordered_quad, kp_conf_min, kp_conf_mean, skew_deg, persp_score,
         persp_direction, quad_area, edge_ratio, homography_eligible)

    Note:
        If keypoints exist but geometry validation fails:
        - Returns kp_conf_min/mean (keypoints were detected)
        - Returns None for skew/persp/persp_direction (geometry invalid, can't compute)
        - Sets homography_eligible = False
    """
    # No keypoints → all None
    if keypoints is None or len(keypoints) != 4:
        return None, None, None, None, None, None, None, None, False

    if keypoint_scores is None or len(keypoint_scores) != 4:
        return None, None, None, None, None, None, None, None, False

    # Keypoint confidence summary (always compute if keypoints exist)
    kp_conf_min = float(min(keypoint_scores))
    kp_conf_mean = float(np.mean(keypoint_scores))

    # Order keypoints
    try:
        ordered_quad = order_keypoints_by_angle(keypoints)
    except ValueError:
        # Keypoint ordering failed (shouldn't happen with 4 points, but defensive)
        return None, kp_conf_min, kp_conf_mean, None, None, None, None, None, False

    # Validate geometry
    geometry_valid, quad_area, edge_ratio = validate_quad_geometry(ordered_quad, cfg)

    if not geometry_valid:
        # Keypoints exist but geometry failed → return kp_conf, but no pose metrics
        return (
            ordered_quad,
            kp_conf_min,
            kp_conf_mean,
            None,  # skew_deg = None (invalid geometry)
            None,  # persp_score = None (invalid geometry)
            None,  # persp_direction = None (invalid geometry)
            quad_area,
            edge_ratio,
            False,  # homography_eligible = False
        )

    # Compute pose metrics (geometry valid)
    edge_lengths = compute_edge_lengths(ordered_quad)
    skew_deg = compute_skew_degrees(edge_lengths, ordered_quad)
    persp_score = compute_perspective_score(edge_lengths)
    persp_direction = compute_perspective_direction(edge_lengths)

    # Homography eligibility: kp_conf_min >= threshold AND geometry valid
    homography_eligible = kp_conf_min >= cfg.keypoint_confidence_min_threshold and geometry_valid

    return (
        ordered_quad,
        kp_conf_min,
        kp_conf_mean,
        skew_deg,
        persp_score,
        persp_direction,
        quad_area,
        edge_ratio,
        homography_eligible,
    )


# =============================================================================
# 4) Photometric Metrics (Canonical Resize)
# =============================================================================


def compute_exposure_metrics(
    luminance: np.ndarray, pad_mask: np.ndarray, cfg: ConsumerConfig
) -> tuple[float, float, float, float, float]:
    """
    Compute exposure metrics from L channel (only on real pixels, not padding).

    Args:
        luminance: L channel (H, W) uint8 [0-255]
        pad_mask: (H, W) bool, True = real pixels, False = padding
        cfg: ConsumerConfig

    Returns:
        (mean_L, p05_L, p95_L, black_clip_frac, white_clip_frac)
    """
    # Extract only real pixels (ignore letterbox padding)
    L_real = luminance[pad_mask]
    total_pixels = float(L_real.size)

    if total_pixels == 0:
        # Degenerate case: no real pixels (shouldn't happen)
        return 0.0, 0.0, 0.0, 0.0, 0.0

    mean_L = float(np.mean(L_real))
    p05_L = float(np.percentile(L_real, 5))
    p95_L = float(np.percentile(L_real, 95))

    black_clip_frac = float(np.sum(L_real <= cfg.luminance_black_threshold) / total_pixels)
    white_clip_frac = float(np.sum(L_real >= cfg.luminance_white_threshold) / total_pixels)

    return mean_L, p05_L, p95_L, black_clip_frac, white_clip_frac


def compute_contrast_metrics(
    luminance: np.ndarray, pad_mask: np.ndarray, cfg: ConsumerConfig
) -> tuple[float, float]:
    """
    Compute global and local contrast metrics (mask-aware).

    Global: p90 - p10 of luminance (only real pixels)
    Local: mean tile stddev over 4×8 grid (only real pixels per tile)

    Args:
        luminance: L channel (H, W) uint8 [0-255]
        pad_mask: (H, W) bool, True = real pixels
        cfg: ConsumerConfig (canonical sizes must be divisible: W%8==0, H%4==0)

    Returns:
        (global_contrast, local_contrast)
    """
    # Extract real pixels for global contrast
    L_real = luminance[pad_mask]

    if L_real.size == 0:
        return 0.0, 0.0

    # Global contrast: p90 - p10
    p90 = float(np.percentile(L_real, 90))
    p10 = float(np.percentile(L_real, 10))
    global_contrast = p90 - p10

    # Local contrast: mean tile stddev over 4×8 grid (mask-aware)
    h, w = luminance.shape
    tile_h = h // 4
    tile_w = w // 8

    if tile_h < 1 or tile_w < 1:
        # Image too small for tiling, use global std as fallback
        local_contrast = float(np.std(L_real))
    else:
        # Vectorized reshape for both luminance and mask
        # Config enforces W%8==0 and H%4==0, so no cropping needed
        # (H, W) → (4, tile_h, 8, tile_w) → (4, 8, tile_h, tile_w) → (32, tile_h, tile_w)
        L_tiles = luminance.reshape(4, tile_h, 8, tile_w).swapaxes(1, 2).reshape(32, tile_h, tile_w)
        M_tiles = pad_mask.reshape(4, tile_h, 8, tile_w).swapaxes(1, 2).reshape(32, tile_h, tile_w)

        # Compute std for each tile using only real pixels
        tile_stds = []
        for i in range(32):
            L_tile = L_tiles[i]
            M_tile = M_tiles[i]

            # Extract real pixels in this tile
            L_vals = L_tile[M_tile]

            # Only compute std if enough real pixels (avoid noise from tiny samples)
            if L_vals.size >= cfg.local_contrast_min_pixels_per_tile:
                tile_stds.append(np.std(L_vals))

        # Average valid tile stds, or fallback to global std
        if len(tile_stds) > 0:
            local_contrast = float(np.mean(tile_stds))
        else:
            # No valid tiles (all padding or too few pixels) → use global std
            local_contrast = float(np.std(L_real))

    return global_contrast, local_contrast


def compute_sharpness_metrics(
    luminance: np.ndarray, pad_mask: np.ndarray
) -> tuple[float, float, float, float]:
    """
    Compute all sharpness metrics in single Sobel pass.

    Computes directional and combined Tenengrad metrics to detect both
    isotropic blur (uniform degradation) and anisotropic blur (motion blur).

    Args:
        luminance: L channel (H, W) uint8 [0-255]
        pad_mask: (H, W) bool, True = real pixels

    Returns:
        (tenengrad, tenengrad_h, tenengrad_v, blur_anisotropy)
        - tenengrad: Combined Sobel energy (gx² + gy²)
        - tenengrad_h: Horizontal sharpness (gx² only, detects vertical edges)
        - tenengrad_v: Vertical sharpness (gy² only, detects horizontal edges)
        - blur_anisotropy: max(h,v) / min(h,v)
          - ~1.0: isotropic (uniform sharp or uniform blur)
          - >2.0: anisotropic (directional motion blur)

    Note:
        All metrics computed from single Sobel operation (no additional cost vs original).
        Erosion removes 1-pixel boundary ring to prevent padding contamination.
    """
    # Convert to float for gradient computation
    L_float = luminance.astype(np.float32)

    # Sobel gradients (ksize=3: industry standard for Tenengrad)
    grad_x = cv2.Sobel(L_float, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(L_float, cv2.CV_32F, 0, 1, ksize=3)

    # Erode mask to exclude contaminated boundary gradients
    # Sobel sees black padding near border → artificial strong edges
    # Erosion with k=3 removes 1-pixel boundary ring (safe for Sobel ksize=3)
    kernel = np.ones((3, 3), dtype=np.uint8)
    mask_eroded = cv2.erode(pad_mask.astype(np.uint8), kernel, iterations=1).astype(bool)

    # Compute separate horizontal and vertical energy
    grad_x_sq = grad_x**2
    grad_y_sq = grad_y**2

    if mask_eroded.sum() == 0:
        # Degenerate case: no valid pixels after erosion
        return 0.0, 0.0, 0.0, 1.0

    # Directional sharpness (separate axes)
    tenengrad_h = float(np.mean(grad_x_sq[mask_eroded]))
    tenengrad_v = float(np.mean(grad_y_sq[mask_eroded]))

    # Combined sharpness (traditional Tenengrad)
    tenengrad = float(np.mean((grad_x_sq + grad_y_sq)[mask_eroded]))

    # Anisotropy ratio (motion blur indicator)
    min_t = min(tenengrad_h, tenengrad_v)
    max_t = max(tenengrad_h, tenengrad_v)
    blur_anisotropy = max_t / (min_t + 1e-6)

    return tenengrad, tenengrad_h, tenengrad_v, blur_anisotropy


def compute_noise_std(
    luminance: np.ndarray, pad_mask: np.ndarray, cfg: ConsumerConfig
) -> tuple[float, float]:
    """
    Robust noise estimation using MAD in flat regions.

    Measures noise only in areas with low local variance (minimal texture/edges).
    This avoids conflating edge structure with sensor noise, providing accurate
    estimates for true noise level without edge contamination.

    Args:
        luminance: L channel (H, W) uint8 [0-255]
        pad_mask: (H, W) bool, True = real pixels
        cfg: ConsumerConfig with flat region parameters

    Returns:
        (noise_std, flat_region_fraction)
        - noise_std: MAD-based noise estimate in flat regions
        - flat_region_fraction: Fraction of image classified as flat (useful metadata for ML)

    Note:
        Falls back to global method if insufficient flat regions detected.
        Uses MAD (Median Absolute Deviation) for outlier robustness.
    """
    L_float = luminance.astype(np.float32)

    # Detect flat regions (low local variance)
    local_mean = cv2.blur(L_float, (5, 5))
    local_sq_mean = cv2.blur(L_float**2, (5, 5))
    local_var = local_sq_mean - local_mean**2

    # Flat mask: low variance AND valid pixels (not padding)
    flat_mask = (local_var < cfg.flat_region_var_threshold) & pad_mask
    total_valid = float(pad_mask.sum())
    flat_region_fraction = float(flat_mask.sum()) / (total_valid + 1e-6)

    # High-frequency residual (Gaussian blur to extract noise)
    L_smooth = cv2.GaussianBlur(L_float, (3, 3), 0)
    residual = L_float - L_smooth

    # Check if enough flat pixels for robust estimate
    if flat_mask.sum() < cfg.flat_region_min_pixels:
        # Not enough flat regions, fall back to global method
        # (still better than original since we're measuring residual, just not flat-only)
        residual_real = residual[pad_mask]
        if residual_real.size == 0:
            return 0.0, flat_region_fraction
        return float(np.std(residual_real)), flat_region_fraction

    # Extract residual in flat regions only
    flat_residual = residual[flat_mask]

    # MAD-based estimate (robust to outliers)
    median_residual = np.median(flat_residual)
    mad = np.median(np.abs(flat_residual - median_residual))
    noise_std = 1.4826 * mad  # Convert MAD to std for Gaussian assumption

    return float(noise_std), flat_region_fraction


# =============================================================================
# 5) Eligibility Flags
# =============================================================================


def compute_eligibility_flags(
    plate_h: float,
    clip_frac: float,
    tenengrad: float,
    tenengrad_h: float,
    tenengrad_v: float,
    white_clip_frac: float,
    mean_L: float,
    black_clip_frac: float,
    global_contrast: float,
    local_contrast: float,
    noise_std: float,
    cfg: ConsumerConfig,
) -> tuple[bool, bool, bool, bool, bool, bool, bool, bool, bool, bool, bool]:
    """
    Compute all eligibility flags including directional edge strength.

    Flags are soft gates for batch selector:
    - top8_eligible: Coarse viability (not hopeless)
    - enhance_eligible: Worth risky photometric enhancement (CLAHE, denoise, sharpen)
    - Individual quality flags: too_small, clipped, very_blurry, vertical_edges_weak, etc.

    Args:
        plate_h: Plate height in pixels
        clip_frac: Crop clipping fraction
        tenengrad: Combined sharpness metric (raw space)
        tenengrad_h: Horizontal sharpness (gx² only, detects vertical edges)
        tenengrad_v: Vertical sharpness (gy² only, detects horizontal edges)
        white_clip_frac: White clipping fraction
        mean_L: Mean luminance
        black_clip_frac: Black clipping fraction
        global_contrast: Global contrast metric
        local_contrast: Local contrast metric
        noise_std: Noise estimate
        cfg: ConsumerConfig

    Returns:
        (too_small, clipped, very_blurry, mildly_soft, exposure_bad,
         low_contrast, noisy, vertical_edges_weak, horizontal_edges_weak,
         top8_eligible, enhance_eligible)

    Note:
        vertical_edges_weak is CRITICAL - horizontal motion blur destroys vertical edges
        which are essential for character discrimination (B/8, D/0, 1/I).
        horizontal_edges_weak is logged for ML but NOT gated - vertical motion blur
        preserves vertical edges which are sufficient for OCR.
    """
    # ===== Hard viability checks =====
    too_small = plate_h < cfg.plate_height_min_px
    clipped = clip_frac > cfg.crop_clip_fraction_max

    # ===== Photometric quality flags =====
    very_blurry = tenengrad < cfg.tenengrad_floor
    mildly_soft = cfg.tenengrad_floor <= tenengrad < cfg.tenengrad_good

    exposure_bad = (
        mean_L < cfg.luminance_mean_min
        or mean_L > cfg.luminance_mean_max
        or black_clip_frac > cfg.black_clip_fraction_max
        or white_clip_frac > cfg.white_clip_fraction_max
    )

    low_contrast = (
        global_contrast < cfg.global_contrast_min or local_contrast < cfg.local_contrast_min
    )

    noisy = noise_std > cfg.noise_std_max

    # Directional edge strength (motion blur detection)
    # Vertical edges (tenengrad_h) are CRITICAL for OCR - destroyed by horizontal motion blur
    # Horizontal edges (tenengrad_v) are less critical - vertical motion blur is tolerable
    vertical_edges_weak = tenengrad_h < cfg.tenengrad_h_floor
    horizontal_edges_weak = tenengrad_v < cfg.tenengrad_v_floor  # Logged for ML, not gated

    # ===== Composite eligibility flags =====

    # 1) top8_eligible: Coarse viability (not hopeless)
    # Gates: size, clipping, defocus blur, horizontal motion blur (vertical edge loss), saturation
    # Does NOT gate on horizontal_edges_weak (vertical motion blur is tolerable)
    top8_eligible = (
        not too_small
        and not clipped
        and not very_blurry  # Catches defocus blur
        and not vertical_edges_weak  # Catches horizontal motion blur (worst case for OCR)
        and white_clip_frac <= cfg.white_clip_hard_max
    )

    # 2) enhance_eligible: Worth photometric enhancement
    # Three gates: viable AND fixable AND motivated

    # A) Viable: passes basic viability checks
    viable = top8_eligible

    # B) Fixable: enhancement won't make it worse
    # - Not severely blurred (sharpening would amplify noise)
    # - Vertical edges not weak (can't fix horizontal motion blur with enhancement)
    # - Not blown highlights (CLAHE on white creates ugly halos)
    fixable = (
        tenengrad >= cfg.tenengrad_floor
        and not vertical_edges_weak
        and white_clip_frac <= cfg.white_clip_enhance_max
    )

    # C) Motivated: at least one issue that enhancement can fix
    # - Low contrast → CLAHE helps
    # - Bad exposure → gamma/histogram adjustment helps
    # - Noisy → denoising applicable
    # - Mildly soft → gentle sharpening beneficial
    # If none of these → image is already good, enhancement is unnecessary risk
    motivated = low_contrast or exposure_bad or noisy or mildly_soft

    enhance_eligible = viable and fixable and motivated

    return (
        too_small,
        clipped,
        very_blurry,
        mildly_soft,
        exposure_bad,
        low_contrast,
        noisy,
        vertical_edges_weak,
        horizontal_edges_weak,
        top8_eligible,
        enhance_eligible,
    )


# =============================================================================
# 6) Pose Signature Vector
# =============================================================================


def compute_diversity_signature(
    skew_deg: float | None,
    plate_h: float,
    bbox_center_x_norm: float,
    persp_direction: float | None,
) -> np.ndarray:
    """
    Generate compact 4D diversity signature with orthogonal dimensions.

    Designed for batch selection with cleaner diversity measurement compared
    to the previous 11D pose signature (which had correlated dimensions).

    Args:
        skew_deg: Rotation angle in degrees or None
        plate_h: Plate height in pixels
        bbox_center_x_norm: Horizontal center position [0, 1] (already normalized)
        persp_direction: Signed perspective direction [-1, 1] or None

    Returns:
        Diversity signature (4,) float32:
        [0] rotation: skew in [-1, 1], maps [-45°, +45°]
        [1] scale: plate height in [0, 1], maps [40px, 140px]
        [2] position_x: horizontal center in [0, 1]
        [3] perspective_dir: signed perspective in [-1, 1]

    Note:
        Each dimension captures independent variation.
        Pre-normalized to similar ranges for meaningful L2 distance.
    """
    sig = np.zeros(4, dtype=np.float32)

    # [0] Rotation: map [-45°, +45°] to [-1, 1]
    if skew_deg is not None:
        sig[0] = np.clip(skew_deg / 45.0, -1.0, 1.0)

    # [1] Scale: map [40px, 140px] to [0, 1]
    sig[1] = np.clip((plate_h - 40.0) / 100.0, 0.0, 1.0)

    # [2] Position: already normalized [0, 1]
    sig[2] = bbox_center_x_norm

    # [3] Perspective direction: already [-1, 1] or 0 if None
    if persp_direction is not None:
        sig[3] = persp_direction

    return sig


# =============================================================================
# Helper: Tenengrad Normalization (Debugging/Visualization Only)
# =============================================================================


def normalize_tenengrad_for_display(
    tenengrad_raw: float, p10: float = 100.0, p90: float = 5000.0
) -> float:
    """
    Normalize raw tenengrad to [0-1] for visualization/debugging.

    NOT used in production logic - only for logging/display purposes.
    Default p10/p90 are empirical estimates; update based on offline calibration.

    Args:
        tenengrad_raw: Raw tenengrad value
        p10: 10th percentile from calibration dataset (default: 100)
        p90: 90th percentile from calibration dataset (default: 5000)

    Returns:
        Normalized value in [0-1]
    """
    if p90 <= p10:
        return 0.0

    normalized = (tenengrad_raw - p10) / (p90 - p10)
    return float(np.clip(normalized, 0.0, 1.0))


# =============================================================================
# Main Analysis Function
# =============================================================================


def analyze_roi_rich(roi_fq: RoiFastQuality, cfg: ConsumerConfig) -> RoiRichQuality:
    """
    Perform comprehensive V1 quality analysis on a single ROI.

    Computes geometric metrics on original crop, photometric on canonical resize.

    Args:
        roi_fq: RoiFastQuality (contains RoiImage + fast metrics)
        cfg: ConsumerConfig

    Returns:
        RoiRichQuality with full V1 metrics
    """
    roi = roi_fq.roi

    # Extract identification
    track_id = roi.track_id
    frame_idx = roi.frame_idx

    # ===== Step 0: Create canonical crop for photometric metrics =====
    canonical_bgr, pad_mask = create_canonical_crop(
        roi.crop_img, cfg.canonical_crop_width, cfg.canonical_crop_height
    )
    canonical_L = convert_to_lab_l(canonical_bgr)

    # ===== Step 1: Hard viability checks (geometric - original crop) =====
    plate_w, plate_h, clip_frac = compute_plate_size_and_clip(roi, cfg)

    # ===== Step 2 & 3: Pose metrics (geometric - original crop) =====
    (
        ordered_quad,
        kp_conf_min,
        kp_conf_mean,
        skew_deg,
        persp_score,
        persp_direction,
        quad_area,
        edge_ratio,
        homography_eligible,
    ) = compute_pose_metrics(roi.keypoints, roi.keypoint_scores, roi, cfg)

    # ===== Step 4: Photometric metrics (canonical resize) =====
    mean_L, p05_L, p95_L, black_clip_frac, white_clip_frac = compute_exposure_metrics(
        canonical_L, pad_mask, cfg
    )
    global_contrast, local_contrast = compute_contrast_metrics(canonical_L, pad_mask, cfg)

    # Sharpness metrics (directional + combined, single Sobel pass)
    tenengrad, tenengrad_h, tenengrad_v, blur_anisotropy = compute_sharpness_metrics(
        canonical_L, pad_mask
    )

    # Robust noise estimation
    noise_std, flat_region_fraction = compute_noise_std(canonical_L, pad_mask, cfg)

    # ===== Step 5: Frame position (normalized bbox center) =====
    bbox = roi.bbox
    bbox_cx = (bbox[0] + bbox[2]) / 2.0
    bbox_cy = (bbox[1] + bbox[3]) / 2.0
    bbox_center_x_normalized = bbox_cx / roi.frame_width
    bbox_center_y_normalized = bbox_cy / roi.frame_height

    # ===== Step 6: Eligibility flags =====
    (
        too_small,
        clipped,
        very_blurry,
        mildly_soft,
        exposure_bad,
        low_contrast,
        noisy,
        vertical_edges_weak,
        horizontal_edges_weak,
        top8_eligible,
        enhance_eligible,
    ) = compute_eligibility_flags(
        plate_h,
        clip_frac,
        tenengrad,
        tenengrad_h,
        tenengrad_v,
        white_clip_frac,
        mean_L,
        black_clip_frac,
        global_contrast,
        local_contrast,
        noise_std,
        cfg,
    )

    # ===== Step 7: Diversity signature (4D orthogonal representation) =====
    diversity_signature = compute_diversity_signature(
        skew_deg, plate_h, bbox_center_x_normalized, persp_direction
    )

    # ===== Assemble RichQualityMetrics =====
    metrics = RichQualityMetrics(
        # Identification
        track_id=track_id,
        frame_idx=frame_idx,
        # Canonical crop metadata
        canonical_width=cfg.canonical_crop_width,
        canonical_height=cfg.canonical_crop_height,
        # Geometric metrics
        plate_width_px=plate_w,
        plate_height_px=plate_h,
        crop_clip_fraction=clip_frac,
        detection_confidence=roi.confidence,
        # Keypoint data (always include if keypoints exist, even if geometry failed)
        keypoints=roi.keypoints,
        keypoint_scores=roi.keypoint_scores,
        keypoint_confidence_min=kp_conf_min,
        keypoint_confidence_mean=kp_conf_mean,
        # Pose metrics (None if geometry invalid)
        skew_degrees=skew_deg,
        perspective_score=persp_score,
        perspective_direction=persp_direction,
        # Photometric metrics - Exposure
        luminance_mean=mean_L,
        luminance_p05=p05_L,
        luminance_p95=p95_L,
        black_clip_fraction=black_clip_frac,
        white_clip_fraction=white_clip_frac,
        # Photometric metrics - Contrast
        global_contrast=global_contrast,
        local_contrast=local_contrast,
        # Photometric metrics - Sharpness (directional + combined)
        tenengrad=tenengrad,
        tenengrad_horizontal=tenengrad_h,
        tenengrad_vertical=tenengrad_v,
        blur_anisotropy=blur_anisotropy,
        # Photometric metrics - Noise (robust)
        noise_std=noise_std,
        flat_region_fraction=flat_region_fraction,
        # Flags
        too_small=too_small,
        clipped=clipped,
        very_blurry=very_blurry,
        mildly_soft=mildly_soft,
        exposure_bad=exposure_bad,
        low_contrast=low_contrast,
        noisy=noisy,
        vertical_edges_weak=vertical_edges_weak,
        horizontal_edges_weak=horizontal_edges_weak,
        top8_eligible=top8_eligible,
        enhance_eligible=enhance_eligible,
        homography_eligible=homography_eligible,
        # Frame position (for diversity)
        bbox_center_x_normalized=bbox_center_x_normalized,
        bbox_center_y_normalized=bbox_center_y_normalized,
        # Diversity signature (4D orthogonal representation)
        diversity_signature=diversity_signature,
        # Quad geometry metrics (for inspection/debugging)
        quad_area_px=quad_area,
        edge_ratio=edge_ratio,
    )

    return RoiRichQuality(roi_fq=roi_fq, metrics=metrics)


def rich_quality_analyze_snapshot(
    snapshot: BinSnapshot,
    cfg: ConsumerConfig,
) -> list[RoiRichQuality]:
    """
    Compute rich quality metrics for each ROI in a snapshot.

    Main entry point for Consumer pipeline.

    Args:
        snapshot: BinSnapshot with list of RoiFastQuality entries
        cfg: ConsumerConfig

    Returns:
        List of RoiRichQuality with V1 metrics
    """
    if not cfg.rich_quality_enabled:
        # Return empty list if disabled (batch selector should handle this gracefully)
        return []

    return [analyze_roi_rich(roi_fq, cfg) for roi_fq in snapshot.entries]
