"""3-phase curriculum augmentation pipeline for corner CNN training.

Phase NONE: passthrough (no augmentation).
Phase MODERATE: horizontal flip, mild rotation/scale, basic photometric.
Phase FULL_DRONE: adds perspective, motion blur, JPEG, occlusion, night sim.

All geometric augmentations track keypoint coordinates.
Horizontal flip remaps corner identity (TL<->TR, BL<->BR).
Out-of-bounds corners trigger retry up to max_retries.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from training.config import AugmentationPhase, TrainingConfig

logger = logging.getLogger(__name__)

# Module-level RNG for testability (tests can patch this)
_rng = np.random.RandomState()

Corners = list[tuple[float, float]]


# ---------------------------------------------------------------------------
# Geometric augmentations (track keypoints)
# ---------------------------------------------------------------------------


def _aug_hflip(
    img: np.ndarray,
    corners: Corners,
) -> tuple[np.ndarray, Corners]:
    """Horizontal flip with corner identity remapping (TL<->TR, BL<->BR)."""
    w = img.shape[1]
    flipped = cv2.flip(img, 1)

    # Mirror x coordinates
    mirrored = [(float(w - 1 - x), float(y)) for x, y in corners]

    # Remap: [TL,TR,BR,BL] -> [old_TR_flipped, old_TL_flipped, old_BL_flipped, old_BR_flipped]
    remapped: Corners = [mirrored[1], mirrored[0], mirrored[3], mirrored[2]]
    return flipped, remapped


def _aug_rotate(
    img: np.ndarray,
    corners: Corners,
    angle: float,
    cfg: TrainingConfig,
) -> tuple[np.ndarray, Corners]:
    """Rotate image and corners by angle degrees around center."""
    h, w = img.shape[:2]
    center = (w / 2.0, h / 2.0)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)  # type: ignore[arg-type]

    rotated = cv2.warpAffine(
        img,
        M,
        (w, h),
        borderMode=cv2.BORDER_REFLECT_101,
    )

    new_corners: Corners = []
    for x, y in corners:
        vec = np.array([x, y, 1.0])
        result = M @ vec
        new_corners.append((float(result[0]), float(result[1])))

    return rotated, new_corners


def _aug_scale(
    img: np.ndarray,
    corners: Corners,
    scale_factor: float,
    cfg: TrainingConfig,
) -> tuple[np.ndarray, Corners]:
    """Scale image and corners by scale_factor around center."""
    h, w = img.shape[:2]
    center = (w / 2.0, h / 2.0)
    M = cv2.getRotationMatrix2D(center, 0, scale_factor)  # type: ignore[arg-type]

    scaled = cv2.warpAffine(
        img,
        M,
        (w, h),
        borderMode=cv2.BORDER_REFLECT_101,
    )

    new_corners: Corners = []
    for x, y in corners:
        vec = np.array([x, y, 1.0])
        result = M @ vec
        new_corners.append((float(result[0]), float(result[1])))

    return scaled, new_corners


def _aug_perspective(
    img: np.ndarray,
    corners: Corners,
    H: np.ndarray,
    cfg: TrainingConfig,
) -> tuple[np.ndarray, Corners]:
    """Perspective warp with explicit w-divide for corner tracking."""
    h, w = img.shape[:2]
    warped = cv2.warpPerspective(
        img,
        H,
        (w, h),
        borderMode=cv2.BORDER_REFLECT_101,
    )

    new_corners: Corners = []
    for x, y in corners:
        vec = np.array([x, y, 1.0])
        result = H @ vec
        # CRITICAL: perspective divide by w'
        w_prime = result[2]
        new_corners.append((float(result[0] / w_prime), float(result[1] / w_prime)))

    return warped, new_corners


def _generate_perspective_H(
    img_w: int, img_h: int, jitter_range: float, rng: np.random.RandomState
) -> np.ndarray:
    """Generate a perspective transform matrix from jittered image corners."""
    src_pts = np.array(
        [[0, 0], [img_w - 1, 0], [img_w - 1, img_h - 1], [0, img_h - 1]],
        dtype=np.float32,
    )
    jitter = rng.uniform(-jitter_range, jitter_range, (4, 2)).astype(np.float32)
    dst_pts = src_pts + jitter
    H: np.ndarray = cv2.getPerspectiveTransform(src_pts, dst_pts)  # type: ignore[assignment]
    return H


# ---------------------------------------------------------------------------
# Photometric augmentations (no keypoint tracking)
# ---------------------------------------------------------------------------


def _aug_brightness(img: np.ndarray, delta: float) -> np.ndarray:
    """Adjust brightness by additive delta in [-range, +range]."""
    return np.clip(img.astype(np.float32) + delta * 255.0, 0, 255).astype(np.uint8)


def _aug_contrast(img: np.ndarray, factor: float) -> np.ndarray:
    """Adjust contrast by multiplicative factor around mean."""
    mean = img.mean()
    return np.clip((img.astype(np.float32) - mean) * factor + mean, 0, 255).astype(np.uint8)


def _aug_saturation(img: np.ndarray, factor: float) -> np.ndarray:
    """Adjust saturation in HSV space."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * factor, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _aug_noise(img: np.ndarray, sigma: float) -> np.ndarray:
    """Add Gaussian noise with given sigma."""
    noise = np.random.randn(*img.shape) * sigma
    return np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def _aug_motion_blur(img: np.ndarray, kernel_size: int) -> np.ndarray:
    """Apply horizontal motion blur."""
    kernel = np.zeros((kernel_size, kernel_size), dtype=np.float32)
    kernel[kernel_size // 2, :] = 1.0 / kernel_size
    return cv2.filter2D(img, -1, kernel)


def _aug_gaussian_blur(img: np.ndarray, sigma: float) -> np.ndarray:
    """Apply Gaussian blur."""
    ksize = int(2 * round(3 * sigma) + 1)
    ksize = max(ksize, 3)
    return cv2.GaussianBlur(img, (ksize, ksize), sigma)


def _aug_jpeg(img: np.ndarray, quality: int) -> np.ndarray:
    """Apply JPEG compression artifact simulation."""
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    _, encoded = cv2.imencode(".jpg", img, encode_param)
    decoded: np.ndarray = cv2.imdecode(encoded, cv2.IMREAD_COLOR)  # type: ignore[assignment]
    return decoded


def _aug_occlusion(img: np.ndarray, area_frac: float, rng: np.random.RandomState) -> np.ndarray:
    """Apply random rectangular occlusion covering area_frac of image."""
    h, w = img.shape[:2]
    target_area = h * w * area_frac
    aspect = rng.uniform(0.5, 2.0)
    oh = int(np.sqrt(target_area / aspect))
    ow = int(oh * aspect)
    oh = min(oh, h)
    ow = min(ow, w)
    y0 = rng.randint(0, h - oh + 1) if h > oh else 0
    x0 = rng.randint(0, w - ow + 1) if w > ow else 0
    out = img.copy()
    # Fill with mean color of the image region
    out[y0 : y0 + oh, x0 : x0 + ow] = int(img.mean())
    return out


def _aug_shadow_overlay(
    img: np.ndarray, rng: np.random.RandomState, alpha_range: tuple[float, float]
) -> np.ndarray:
    """Cast random polygon shadow across the image.

    Generates a random convex polygon (3-6 vertices) covering 15-40% of image
    with a semi-transparent dark overlay. No corner transform.
    """
    h, w = img.shape[:2]
    n_vertices = rng.randint(3, 7)

    # Generate random polygon covering 15-40% of image
    cx = rng.uniform(0.2 * w, 0.8 * w)
    cy = rng.uniform(0.2 * h, 0.8 * h)
    coverage = rng.uniform(0.15, 0.40)
    radius = np.sqrt(h * w * coverage / np.pi)

    angles = np.sort(rng.uniform(0, 2 * np.pi, n_vertices))
    pts = []
    for a in angles:
        r = radius * rng.uniform(0.6, 1.0)
        px = int(np.clip(cx + r * np.cos(a), 0, w - 1))
        py = int(np.clip(cy + r * np.sin(a), 0, h - 1))
        pts.append([px, py])
    pts_arr = np.array(pts, dtype=np.int32)

    # Create shadow mask
    mask = np.zeros((h, w), dtype=np.float32)
    cv2.fillConvexPoly(mask, pts_arr, 1.0)

    # Blur shadow edge for realism
    blur_ksize = rng.randint(2, 5) * 2 + 1
    mask = cv2.GaussianBlur(mask, (blur_ksize, blur_ksize), 0)

    alpha = rng.uniform(alpha_range[0], alpha_range[1])
    shadow = img.astype(np.float32) * (1.0 - mask[:, :, np.newaxis] * alpha)
    return np.clip(shadow, 0, 255).astype(np.uint8)


def _aug_glare_specular(
    img: np.ndarray, rng: np.random.RandomState, alpha_range: tuple[float, float]
) -> np.ndarray:
    """Add bright elliptical highlight simulating sun glare on metallic plate.

    No corner transform — purely photometric.
    """
    h, w = img.shape[:2]
    # Random ellipse center and axes
    cx = rng.randint(w // 4, 3 * w // 4)
    cy = rng.randint(h // 4, 3 * h // 4)
    ax = int(rng.uniform(0.10, 0.40) * w)
    ay = int(rng.uniform(0.10, 0.40) * h)

    # Create highlight mask
    mask = np.zeros((h, w), dtype=np.float32)
    cv2.ellipse(mask, (cx, cy), (ax, ay), 0, 0, 360, 1.0, -1)

    # Radial gradient falloff
    y_grid, x_grid = np.ogrid[:h, :w]
    dist = np.sqrt(((x_grid - cx) / max(ax, 1)) ** 2 + ((y_grid - cy) / max(ay, 1)) ** 2)
    gradient = np.clip(1.0 - dist, 0, 1).astype(np.float32)
    mask = mask * gradient

    # Create highlight color (white to yellow-white)
    highlight_r = rng.randint(200, 256)
    highlight_g = rng.randint(200, 256)
    highlight_b = rng.randint(180, 256)
    highlight = np.array([highlight_b, highlight_g, highlight_r], dtype=np.float32)

    alpha = rng.uniform(alpha_range[0], alpha_range[1])
    out = img.astype(np.float32)
    out = out + mask[:, :, np.newaxis] * highlight * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def _aug_color_temperature(
    img: np.ndarray, rng: np.random.RandomState, a_range: float, b_range: float
) -> np.ndarray:
    """Shift white balance warm (sunrise) <-> cool (overcast).

    Converts BGR -> LAB, shifts a* and b* channels, converts back.
    No corner transform.
    """
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    a_shift = rng.uniform(-a_range, a_range)
    b_shift = rng.uniform(-b_range, b_range)
    lab[:, :, 1] = np.clip(lab[:, :, 1] + a_shift, 0, 255)
    lab[:, :, 2] = np.clip(lab[:, :, 2] + b_shift, 0, 255)
    return cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2BGR)


def _aug_directional_motion_blur(
    img: np.ndarray, rng: np.random.RandomState, kernel_range: tuple[int, int]
) -> np.ndarray:
    """Apply motion blur at arbitrary angle.

    Samples angle from [0, 45, 90, 135] degrees. Creates rotated line kernel.
    No corner transform.
    """
    ksize = rng.randint(kernel_range[0], kernel_range[1] + 1)
    ksize = max(3, ksize) | 1  # ensure odd and >= 3
    angle_deg = rng.choice([0, 45, 90, 135])

    # Create motion blur kernel
    kernel = np.zeros((ksize, ksize), dtype=np.float32)
    center = ksize // 2
    angle_rad = np.deg2rad(angle_deg)

    for i in range(ksize):
        offset = i - center
        px = int(round(center + offset * np.cos(angle_rad)))
        py = int(round(center + offset * np.sin(angle_rad)))
        if 0 <= px < ksize and 0 <= py < ksize:
            kernel[py, px] = 1.0

    kernel_sum = kernel.sum()
    if kernel_sum > 0:
        kernel /= kernel_sum

    return cv2.filter2D(img, -1, kernel)


def _aug_poisson_noise(
    img: np.ndarray, rng: np.random.RandomState, peak_range: tuple[float, float]
) -> np.ndarray:
    """Add Poisson (shot) noise simulating low-light photon noise.

    Scales image to [0, peak], applies Poisson sampling, scales back.
    No corner transform.
    """
    peak = rng.uniform(peak_range[0], peak_range[1])
    img_f = img.astype(np.float64) / 255.0 * peak
    # Poisson noise
    noisy = rng.poisson(np.clip(img_f, 0, None)).astype(np.float64)
    noisy = noisy / peak * 255.0
    return np.clip(noisy, 0, 255).astype(np.uint8)


def _aug_sensor_banding(
    img: np.ndarray, rng: np.random.RandomState, intensity_range: tuple[float, float]
) -> np.ndarray:
    """Add horizontal banding simulating CMOS sensor readout noise.

    Generates random horizontal stripe pattern with per-band intensity offset.
    No corner transform.
    """
    h, w = img.shape[:2]
    band_height = rng.randint(2, 9)

    out = img.astype(np.float32)
    for y_start in range(0, h, band_height):
        y_end = min(y_start + band_height, h)
        offset = rng.uniform(-intensity_range[1], intensity_range[1])
        out[y_start:y_end] += offset

    return np.clip(out, 0, 255).astype(np.uint8)


def _aug_defocus_blur(img: np.ndarray, sigma: float, rng: np.random.RandomState) -> np.ndarray:
    """Apply defocus blur at higher sigma than existing Gaussian blur.

    Sigma range: 1.0-3.0 (existing Gaussian blur is 0.5-1.5).
    No corner transform.
    """
    ksize = int(2 * round(3 * sigma) + 1)
    ksize = max(ksize, 3)
    return cv2.GaussianBlur(img, (ksize, ksize), sigma)


def _aug_night_sim(img: np.ndarray, cfg: TrainingConfig, rng: np.random.RandomState) -> np.ndarray:
    """Simulate night conditions: reduce brightness, apply gamma, add noise."""
    # Brightness reduction
    brightness_factor = rng.uniform(cfg.aug_night_brightness[0], cfg.aug_night_brightness[1])
    out = img.astype(np.float32) * brightness_factor

    # Gamma correction
    gamma = rng.uniform(cfg.aug_night_gamma[0], cfg.aug_night_gamma[1])
    out = np.clip(out / 255.0, 0, 1)
    out = np.power(out, gamma) * 255.0

    # Night noise
    noise_sigma = rng.uniform(cfg.aug_night_noise_sigma[0], cfg.aug_night_noise_sigma[1])
    noise = rng.randn(*out.shape) * noise_sigma
    out = np.clip(out + noise, 0, 255).astype(np.uint8)

    return out


# ---------------------------------------------------------------------------
# Bounds checking
# ---------------------------------------------------------------------------


def _corners_in_bounds(corners: Corners, w: int, h: int) -> bool:
    """Check all corners are within image bounds with tolerance.

    Uses generous tolerance (20%) because plate crops are typically wide and
    short — even small rotations push corners several pixels beyond the short
    axis. BORDER_REFLECT_101 fills edge regions with valid content, and corners
    are clamped to valid range afterward.
    """
    tol_x = w * 0.20
    tol_y = h * 0.20
    return all(-tol_x <= x < w + tol_x and -tol_y <= y < h + tol_y for x, y in corners)


def _clamp_corners(corners: Corners, w: int, h: int) -> Corners:
    """Clamp corners to valid image bounds [0, w-1] x [0, h-1]."""
    return [
        (float(max(0.0, min(x, w - 1.0))), float(max(0.0, min(y, h - 1.0)))) for x, y in corners
    ]


# ---------------------------------------------------------------------------
# Pipeline composition
# ---------------------------------------------------------------------------


def _apply_pipeline(
    img: np.ndarray,
    corners: Corners,
    phase: AugmentationPhase,
    cfg: TrainingConfig,
    is_night: bool,
    rng: np.random.RandomState,
    aug_ramp_factor: float = 1.0,
) -> tuple[np.ndarray, Corners]:
    """Apply phase-appropriate augmentation pipeline."""
    if phase == AugmentationPhase.NONE:
        return img, corners

    # --- Geometric augmentations ---
    if rng.random() < cfg.aug_hflip_prob * aug_ramp_factor:
        img, corners = _aug_hflip(img, corners)

    if phase == AugmentationPhase.MODERATE:
        rot_range = cfg.aug_rotation_range_p2
        rot_prob = cfg.aug_rotation_prob_p2
        scale_range = cfg.aug_scale_range_p2
        scale_prob = cfg.aug_scale_prob_p2
    else:  # FULL_DRONE
        rot_range = cfg.aug_rotation_range_p3
        rot_prob = cfg.aug_rotation_prob_p3
        scale_range = cfg.aug_scale_range_p3
        scale_prob = cfg.aug_scale_prob_p3

    if rng.random() < rot_prob * aug_ramp_factor:
        angle = rng.uniform(-rot_range, rot_range)
        img, corners = _aug_rotate(img, corners, angle, cfg)

    if rng.random() < scale_prob * aug_ramp_factor:
        factor = rng.uniform(scale_range[0], scale_range[1])
        img, corners = _aug_scale(img, corners, factor, cfg)

    if (
        phase == AugmentationPhase.FULL_DRONE
        and rng.random() < cfg.aug_perspective_prob * aug_ramp_factor
    ):
        H = _generate_perspective_H(img.shape[1], img.shape[0], cfg.aug_perspective_range, rng)
        img, corners = _aug_perspective(img, corners, H, cfg)

    # --- Photometric augmentations (no keypoint tracking) ---
    if rng.random() < cfg.aug_brightness_prob * aug_ramp_factor:
        delta = rng.uniform(-cfg.aug_brightness_range, cfg.aug_brightness_range)
        img = _aug_brightness(img, delta)

    if rng.random() < cfg.aug_contrast_prob * aug_ramp_factor:
        factor = 1.0 + rng.uniform(-cfg.aug_contrast_range, cfg.aug_contrast_range)
        img = _aug_contrast(img, factor)

    if rng.random() < cfg.aug_saturation_prob * aug_ramp_factor:
        factor = 1.0 + rng.uniform(-cfg.aug_saturation_range, cfg.aug_saturation_range)
        img = _aug_saturation(img, factor)

    if phase == AugmentationPhase.MODERATE:
        noise_sigma_range = cfg.aug_noise_sigma_p2
        noise_prob = cfg.aug_noise_prob_p2
    else:
        noise_sigma_range = cfg.aug_heavy_noise_sigma
        noise_prob = cfg.aug_heavy_noise_prob

    if rng.random() < noise_prob * aug_ramp_factor:
        sigma = rng.uniform(noise_sigma_range[0], noise_sigma_range[1])
        img = _aug_noise(img, sigma)

    # --- FULL_DRONE only photometric ---
    if phase == AugmentationPhase.FULL_DRONE:
        if rng.random() < cfg.aug_heavy_brightness_prob * aug_ramp_factor:
            gamma = rng.uniform(cfg.aug_gamma_range[0], cfg.aug_gamma_range[1])
            img_f = np.clip(img.astype(np.float32) / 255.0, 0, 1)
            img = (np.power(img_f, gamma) * 255.0).astype(np.uint8)

        if rng.random() < cfg.aug_motion_blur_prob * aug_ramp_factor:
            ksize = rng.randint(cfg.aug_motion_blur_range[0], cfg.aug_motion_blur_range[1] + 1)
            img = _aug_motion_blur(img, ksize)

        if rng.random() < cfg.aug_gaussian_blur_prob * aug_ramp_factor:
            sigma = rng.uniform(cfg.aug_gaussian_blur_sigma[0], cfg.aug_gaussian_blur_sigma[1])
            img = _aug_gaussian_blur(img, sigma)

        if rng.random() < cfg.aug_jpeg_prob * aug_ramp_factor:
            quality = rng.randint(cfg.aug_jpeg_quality_range[0], cfg.aug_jpeg_quality_range[1] + 1)
            img = _aug_jpeg(img, quality)

        if rng.random() < cfg.aug_occlusion_prob * aug_ramp_factor:
            area_frac = rng.uniform(
                cfg.aug_occlusion_area_range[0], cfg.aug_occlusion_area_range[1]
            )
            img = _aug_occlusion(img, area_frac, rng)

        # Night sim: skip when is_night=True (already a night image)
        if not is_night and rng.random() < cfg.aug_night_prob * aug_ramp_factor:
            img = _aug_night_sim(img, cfg, rng)

        # --- New photometric augmentations (Phase 2 synthetic data pipeline) ---
        if rng.random() < cfg.aug_shadow_prob * aug_ramp_factor:
            img = _aug_shadow_overlay(img, rng, cfg.aug_shadow_alpha_range)

        if rng.random() < cfg.aug_glare_prob * aug_ramp_factor:
            img = _aug_glare_specular(img, rng, cfg.aug_glare_alpha_range)

        if rng.random() < cfg.aug_color_temp_prob * aug_ramp_factor:
            img = _aug_color_temperature(
                img, rng, cfg.aug_color_temp_a_range, cfg.aug_color_temp_b_range
            )

        if rng.random() < cfg.aug_directional_blur_prob * aug_ramp_factor:
            img = _aug_directional_motion_blur(img, rng, cfg.aug_directional_blur_kernel)

        if rng.random() < cfg.aug_poisson_prob * aug_ramp_factor:
            img = _aug_poisson_noise(img, rng, cfg.aug_poisson_peak_range)

        if rng.random() < cfg.aug_sensor_banding_prob * aug_ramp_factor:
            img = _aug_sensor_banding(img, rng, cfg.aug_sensor_banding_intensity)

        if rng.random() < cfg.aug_defocus_prob * aug_ramp_factor:
            sigma = rng.uniform(cfg.aug_defocus_sigma[0], cfg.aug_defocus_sigma[1])
            img = _aug_defocus_blur(img, sigma, rng)

    return img, corners


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def apply_augmentation(
    img: np.ndarray,
    corners: Corners,
    phase: AugmentationPhase,
    cfg: TrainingConfig,
    *,
    is_night: bool = False,
    max_retries: int = 10,
    aug_ramp_factor: float = 1.0,
) -> tuple[np.ndarray, Corners] | None:
    """Apply augmentation with OOB retry.

    Returns (augmented_img, augmented_corners) or None if all retries fail.
    """
    if phase == AugmentationPhase.NONE:
        return img, corners

    h, w = img.shape[:2]

    for attempt in range(max_retries):
        aug_img, aug_corners = _apply_pipeline(
            img.copy(),
            list(corners),
            phase,
            cfg,
            is_night,
            _rng,
            aug_ramp_factor=aug_ramp_factor,
        )
        if _corners_in_bounds(aug_corners, w, h):
            # Clamp slightly-OOB corners (within tolerance) to valid range
            aug_corners = _clamp_corners(aug_corners, w, h)
            logger.debug(
                "augmentation succeeded on attempt %d/%d, phase=%s",
                attempt + 1,
                max_retries,
                phase.name,
            )
            return aug_img, aug_corners
        logger.debug(
            "augmentation OOB retry %d/%d, phase=%s",
            attempt + 1,
            max_retries,
            phase.name,
        )

    logger.warning("augmentation failed after %d retries, phase=%s", max_retries, phase.name)
    return None
