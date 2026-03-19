"""
Extracts ROI crops from detected license plates with adaptive padding and resizing.

Implements a two-stage strategy:
1. Apply 15% dynamic padding (min 12px) to capture preprocessing context
2. Adaptively resize oversized crops (>400px) to prevent buffer bloat

Padding scales proportionally during resize, maintaining relative context for
rotation estimation (Hough lines) and illumination correction (CLAHE).
"""

from __future__ import annotations

import cv2
import numpy as np

from .config import ProducerConfig
from .models import RoiImage, Track


def compute_padded_bbox(
    bbox: list[float],
    frame_shape: tuple[int, int],
    cfg: ProducerConfig,
) -> list[float]:
    """
    Compute padded bounding box with dynamic percentage-based padding.

    Args:
        bbox: Original tight bbox [x1, y1, x2, y2] in absolute pixels
        frame_shape: (height, width) of source frame
        cfg: Producer configuration with padding parameters

    Returns:
        Padded bbox [x1, y1, x2, y2] clamped to frame boundaries

    Padding logic:
        - 15% of bbox dimensions (scales with plate quality)
        - Minimum 12px (ensures edge context at marginal sizes)
        - Clamped to frame edges (no artificial borders)
    """
    x1, y1, x2, y2 = bbox
    frame_h, frame_w = frame_shape

    # Compute bbox dimensions
    bbox_w = x2 - x1
    bbox_h = y2 - y1

    # Dynamic padding: 15% with minimum floor
    pad_w = max(int(bbox_w * cfg.crop_padding_ratio), cfg.crop_padding_min_px)
    pad_h = max(int(bbox_h * cfg.crop_padding_ratio), cfg.crop_padding_min_px)

    # Apply padding
    x1_pad = x1 - pad_w
    y1_pad = y1 - pad_h
    x2_pad = x2 + pad_w
    y2_pad = y2 + pad_h

    # Clamp to frame boundaries
    x1_pad = max(0.0, x1_pad)
    y1_pad = max(0.0, y1_pad)
    x2_pad = min(float(frame_w), x2_pad)
    y2_pad = min(float(frame_h), y2_pad)

    return [x1_pad, y1_pad, x2_pad, y2_pad]


def extract_crop_from_bbox(
    frame_img: np.ndarray,
    padded_bbox: list[float],
) -> np.ndarray | None:
    """
    Extract crop from frame using padded bbox coordinates.

    Args:
        frame_img: Full frame BGR image (H×W×3 uint8)
        padded_bbox: Padded bbox [x1, y1, x2, y2] in absolute pixels

    Returns:
        Cropped BGR image, or None if bbox is invalid

    Note:
        Assumes padded_bbox is already clamped to frame boundaries.
    """
    x1, y1, x2, y2 = padded_bbox

    # Convert to integer pixel coordinates
    x1_int = int(round(x1))
    y1_int = int(round(y1))
    x2_int = int(round(x2))
    y2_int = int(round(y2))

    # Validate bbox
    if x2_int <= x1_int or y2_int <= y1_int:
        return None

    # Extract crop via numpy slicing (fast, zero-copy view)
    crop = frame_img[y1_int:y2_int, x1_int:x2_int]

    # Validate crop is not empty
    if crop.size == 0:
        return None

    # Make a copy to own the data (original frame will be discarded)
    return crop.copy()


def adaptive_resize_crop(
    crop_img: np.ndarray,
    threshold: int,
) -> tuple[np.ndarray, bool, float, int, int]:
    """
    Adaptively resize oversized crops to prevent buffer bloat.

    Args:
        crop_img: Cropped BGR image (H×W×3 uint8)
        threshold: Width threshold for triggering resize (also target width)

    Returns:
        (resized_crop, was_resized, scale_factor, new_w, new_h)
        - resized_crop: Resized image or original if no resize needed
        - was_resized: True if resize was applied
        - scale_factor: Ratio of new/old dimensions (1.0 if not resized)
        - new_w, new_h: Width/height of the returned crop

    Strategy:
        If crop width > threshold, resize to threshold width maintaining aspect ratio.
        Uses INTER_AREA (best for downsampling - anti-aliased area interpolation).
        Padding scales proportionally with image, preserving relative context.

    Performance:
        ~1ms for 800×400 → 400×200 resize on CPU
    """
    h, w = crop_img.shape[:2]

    # Check if resize disabled or not needed
    # threshold=0 means disabled (preserve full quality)
    if threshold <= 0 or w <= threshold:
        return crop_img, False, 1.0, w, h

    # Compute aspect-preserving scale
    scale = threshold / w
    new_w = threshold
    new_h = int(round(h * scale))

    # Resize using INTER_AREA (optimal for downsampling)
    resized = cv2.resize(crop_img, (new_w, new_h), interpolation=cv2.INTER_AREA)

    return resized, True, scale, new_w, new_h


def validate_crop_dimensions(
    crop_img: np.ndarray,
    cfg: ProducerConfig,
) -> bool:
    """
    Validate crop meets minimum dimension requirements.

    Args:
        crop_img: Cropped BGR image (H×W×3 uint8)
        cfg: Producer configuration with dimension constraints

    Returns:
        True if crop is valid, False if too small
    """
    h, w = crop_img.shape[:2]
    return w >= cfg.crop_min_width and h >= cfg.crop_min_height


def crop_track_roi(
    frame_img: np.ndarray,
    track: Track,
    frame_idx: int,
    cfg: ProducerConfig,
) -> RoiImage | None:
    """
    Extract and process ROI crop for a single track.

    Pipeline:
        1. Compute padded bbox (15% dynamic padding, min 12px)
        2. Extract crop from frame
        3. Adaptive resize if oversized (>400px → 400px)
        4. Validate dimensions
        5. Return RoiImage with metadata

    Args:
        frame_img: Full frame BGR image (H×W×3 uint8)
        track: Track object with bbox and metadata
        frame_idx: Current frame index
        cfg: Producer configuration

    Returns:
        RoiImage if successful, None if crop failed validation

    Performance:
        - Typical (100-400px): ~0.1-0.3ms per track
        - Oversized (>400px): ~1.5ms per track (includes resize)
    """
    # Step 1: Get tight bbox from track
    bbox = track.get_bbox()

    # Step 2: Compute padded bbox
    frame_h, frame_w = frame_img.shape[:2]
    padded_bbox = compute_padded_bbox(bbox, (frame_h, frame_w), cfg)

    # Step 3: Extract crop
    crop_img = extract_crop_from_bbox(frame_img, padded_bbox)
    if crop_img is None:
        return None

    # Step 4: Adaptive resize if needed
    resized_crop, was_resized, scale_factor, crop_w, crop_h = adaptive_resize_crop(
        crop_img, cfg.crop_adaptive_resize_threshold
    )

    # Step 5: Validate dimensions
    if not validate_crop_dimensions(resized_crop, cfg):
        return None

    # Step 5.5: Translate keypoints to crop coordinates (if from current frame)
    crop_keypoints = None
    crop_keypoint_scores = None
    if track.keypoints is not None and track.last_seen_frame == frame_idx:
        x0, y0, _, _ = padded_bbox
        crop_keypoints = [
            ((xk - x0) * scale_factor, (yk - y0) * scale_factor) for (xk, yk) in track.keypoints
        ]
        crop_keypoint_scores = track.keypoint_scores

    # Step 6: Build RoiImage
    return RoiImage(
        track_id=track.track_id,
        crop_img=resized_crop,
        bbox=bbox,
        padded_bbox=padded_bbox,
        frame_idx=frame_idx,
        confidence=track.confidence,
        frame_width=frame_w,
        frame_height=frame_h,
        keypoints=crop_keypoints,
        keypoint_scores=crop_keypoint_scores,
        was_resized=was_resized,
        resize_scale=scale_factor,
        crop_width=crop_w,
        crop_height=crop_h,
    )


def crop_tracks(
    frame_img: np.ndarray,
    tracks: list[Track],
    frame_idx: int,
    cfg: ProducerConfig,
) -> list[RoiImage]:
    """
    Extract ROI crops for multiple tracks (public API).

    Args:
        frame_img: Full frame BGR image (H×W×3 uint8) from WHEP stream
        tracks: List of confirmed tracks to crop (typically 2-5 per frame)
        frame_idx: Current frame index
        cfg: Producer configuration

    Returns:
        List of RoiImage objects (excludes failed crops)

    Usage:
        Called by Producer pipeline after tracking to prepare crops for
        quality scoring and buffer management. Original frame is discarded
        after this operation - only RoiImages travel downstream.

    Performance:
        ~0.3-1.5ms per track depending on size and resize needs
    """
    rois = []

    for track in tracks:
        roi = crop_track_roi(frame_img, track, frame_idx, cfg)
        if roi is not None:
            rois.append(roi)

    return rois
