"""
Implements IOU-based greedy tracking with alpha/beta Kalman filter for position/size/velocity,
with Lucas-Kanade global motion compensation for gimbal movement robustness.
"""

from __future__ import annotations

import logging
import time
import uuid

import cv2
import numpy as np

from .config import ProducerConfig
from .models import Detection, Track, TrackerOutput, TrackingTimings

# ===== Helper Utilities =====


def bbox_to_position(bbox: list[float]) -> list[float]:
    """
    Convert bbox [x1, y1, x2, y2] to position [cx, cy, w, h].

    Returns:
        [center_x, center_y, width, height] - always length 4
    """
    x1, y1, x2, y2 = bbox
    w = x2 - x1
    h = y2 - y1
    cx = x1 + w / 2
    cy = y1 + h / 2
    return [cx, cy, w, h]


def position_to_bbox(position: list[float]) -> list[float]:
    """
    Convert position [cx, cy, w, h] to bbox [x1, y1, x2, y2].

    Returns:
        [x1, y1, x2, y2] - always length 4
    """
    cx, cy, w, h = position
    x1 = cx - w / 2
    y1 = cy - h / 2
    x2 = cx + w / 2
    y2 = cy + h / 2
    return [x1, y1, x2, y2]


def compute_iou(bbox1: list[float], bbox2: list[float]) -> float:
    """
    Compute IOU (Intersection over Union) between two bboxes.

    Args:
        bbox1, bbox2: Bboxes in [x1, y1, x2, y2] format

    Returns:
        IOU value in [0, 1]
    """
    x1_1, y1_1, x2_1, y2_1 = bbox1
    x1_2, y1_2, x2_2, y2_2 = bbox2

    # Intersection
    x1_i = max(x1_1, x1_2)
    y1_i = max(y1_1, y1_2)
    x2_i = min(x2_1, x2_2)
    y2_i = min(y2_1, y2_2)

    if x2_i < x1_i or y2_i < y1_i:
        return 0.0

    intersection = (x2_i - x1_i) * (y2_i - y1_i)

    # Union
    area1 = (x2_1 - x1_1) * (y2_1 - y1_1)
    area2 = (x2_2 - x1_2) * (y2_2 - y1_2)
    union = area1 + area2 - intersection

    if union <= 0:
        return 0.0

    return intersection / union


def compute_aspect_ratio(bbox: list[float]) -> float:
    """Compute width/height aspect ratio."""
    x1, y1, x2, y2 = bbox
    w = x2 - x1
    h = y2 - y1
    if h <= 0:
        return 0.0
    return w / h


# ===== Core Tracking Functions =====


def estimate_camera_motion(
    prev_frame: np.ndarray,
    curr_frame: np.ndarray,
    cfg: ProducerConfig,
) -> tuple[float, float]:
    """
    Estimate global camera motion using Lucas-Kanade optical flow.

    Args:
        prev_frame: Previous grayscale frame (HxW uint8)
        curr_frame: Current grayscale frame (HxW uint8)
        cfg: Configuration with LK parameters

    Returns:
        (dx, dy): Median motion in pixels (robust to outliers)

    Performance: ~2-3ms for 100 corners

    Note:
        Expects grayscale input. If called with BGR, results will be incorrect.
        Use PlateTracker.update() which handles conversion automatically.
    """
    if not cfg.enable_lk_motion:
        return (0.0, 0.0)

    # Detect sparse corner features
    corners = cv2.goodFeaturesToTrack(
        prev_frame,
        maxCorners=cfg.lk_max_corners,
        qualityLevel=cfg.lk_quality_level,
        minDistance=cfg.lk_min_distance,
    )

    if corners is None or len(corners) < 10:
        # Not enough features, skip LK
        return (0.0, 0.0)

    # Track features using pyramidal LK
    new_corners, status, _ = cv2.calcOpticalFlowPyrLK(
        prev_frame,
        curr_frame,
        corners,
        None,  # type: ignore[arg-type]  # OpenCV accepts None for nextPts
        winSize=cfg.lk_win_size,
        maxLevel=cfg.lk_max_level,
    )

    # Filter valid tracks
    valid_mask = status.flatten() == 1
    if valid_mask.sum() < 5:
        # Too few valid tracks
        return (0.0, 0.0)

    valid_old = corners[valid_mask]
    valid_new = new_corners[valid_mask]

    # Compute flow vectors
    flow = valid_new - valid_old

    # Use median for robustness (outliers from moving objects)
    median_dx = float(np.median(flow[:, 0, 0]))
    median_dy = float(np.median(flow[:, 0, 1]))

    return (median_dx, median_dy)


def predict_track_alpha_beta(
    track: Track,
    camera_dx: float,
    camera_dy: float,
) -> list[float]:
    """
    Predict next position for a single track using alpha-beta filter.

    Args:
        track: Track to predict
        camera_dx, camera_dy: Camera motion correction from LK

    Returns:
        Predicted position [cx, cy, w, h]
    """
    # Predict: position' = position + velocity
    cx, cy, w, h = track.position
    vx, vy, vw, vh = track.velocity

    predicted_cx = cx + vx + camera_dx
    predicted_cy = cy + vy + camera_dy
    predicted_w = w + vw
    predicted_h = h + vh

    # Ensure positive dimensions
    predicted_w = max(predicted_w, 1.0)
    predicted_h = max(predicted_h, 1.0)

    return [predicted_cx, predicted_cy, predicted_w, predicted_h]


def filter_detections_geometric(
    detections: list[Detection],
    cfg: ProducerConfig,
) -> list[Detection]:
    """
    Filter detections using geometric constraints (aspect ratio, size).

    Args:
        detections: Raw YOLO detections
        cfg: Configuration with filter thresholds

    Returns:
        Filtered detections passing all geometric checks

    Performance: <0.1ms (trivial per-detection checks)
    """
    valid_detections = []

    for det in detections:
        bbox = det.bbox
        x1, y1, x2, y2 = bbox

        # Size check
        w = x2 - x1
        h = y2 - y1
        if w < cfg.min_bbox_width or h < cfg.min_bbox_height:
            continue

        # Aspect ratio check
        aspect_ratio = compute_aspect_ratio(bbox)
        if aspect_ratio < cfg.min_aspect_ratio or aspect_ratio > cfg.max_aspect_ratio:
            continue

        valid_detections.append(det)

    return valid_detections


def compute_iou_matrix(
    predicted_bboxes: list[list[float]],
    detection_bboxes: list[list[float]],
) -> np.ndarray:
    """
    Compute IOU matrix between predictions and detections.

    Args:
        predicted_bboxes: N predicted bboxes [x1, y1, x2, y2]
        detection_bboxes: M detection bboxes [x1, y1, x2, y2]

    Returns:
        NxM matrix where element [i,j] = IOU(prediction_i, detection_j)

    Performance: ~1ms for 10 tracks × 10 detections
    """
    if len(predicted_bboxes) == 0 or len(detection_bboxes) == 0:
        return np.zeros((len(predicted_bboxes), len(detection_bboxes)))

    iou_matrix = np.zeros((len(predicted_bboxes), len(detection_bboxes)))

    for i, pred_bbox in enumerate(predicted_bboxes):
        for j, det_bbox in enumerate(detection_bboxes):
            iou_matrix[i, j] = compute_iou(pred_bbox, det_bbox)

    return iou_matrix


def match_tracks_to_detections(
    tracks: list[Track],
    detections: list[Detection],
    iou_matrix: np.ndarray,
    cfg: ProducerConfig,
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """
    Greedy IOU-based matching with two-tier thresholds.

    Args:
        tracks: Active tracks
        detections: Current detections
        iou_matrix: Precomputed IOU matrix (tracks × detections)
        cfg: Configuration with IOU thresholds

    Returns:
        matches: List of (track_idx, detection_idx) pairs
        unmatched_tracks: Indices of tracks without matches
        unmatched_detections: Indices of detections without matches

    Algorithm:
        Greedy matching: highest IOU first, with adaptive thresholds
        for confirmed vs tentative tracks.

    Performance: ~1ms for typical counts
    """
    num_tracks = len(tracks)
    num_detections = len(detections)

    if num_tracks == 0 or num_detections == 0:
        return [], list(range(num_tracks)), list(range(num_detections))

    # Track which indices are already matched
    matched_track_indices = set()
    matched_detection_indices = set()
    matches = []

    # Greedy matching: highest IOU first
    while True:
        # Mask out already matched rows/columns
        masked_iou = iou_matrix.copy()
        for i in matched_track_indices:
            masked_iou[i, :] = 0.0
        for j in matched_detection_indices:
            masked_iou[:, j] = 0.0

        # Find highest IOU
        max_iou = masked_iou.max()
        if max_iou == 0.0:
            break

        max_idx = np.unravel_index(masked_iou.argmax(), masked_iou.shape)
        track_idx, det_idx = max_idx

        # Check threshold (two-tier: confirmed vs tentative)
        track = tracks[track_idx]
        is_confirmed = track.is_confirmed(cfg.min_hits)

        threshold = cfg.iou_threshold_confirmed if is_confirmed else cfg.iou_threshold_tentative

        if max_iou < threshold:
            # Remaining IOUs are too low
            break

        # Valid match
        matches.append((track_idx, det_idx))
        matched_track_indices.add(track_idx)
        matched_detection_indices.add(det_idx)

    # Compute unmatched indices
    unmatched_tracks = [i for i in range(num_tracks) if i not in matched_track_indices]
    unmatched_detections = [i for i in range(num_detections) if i not in matched_detection_indices]

    return matches, unmatched_tracks, unmatched_detections


def check_velocity_consistency(
    track: Track,
    new_velocity: list[float],
    cfg: ProducerConfig,
) -> bool:
    """
    Check if velocity change is reasonable (adaptive threshold).

    Args:
        track: Track with current velocity
        new_velocity: Proposed new velocity after update
        cfg: Configuration with velocity change thresholds

    Returns:
        True if velocity change is consistent, False if suspicious

    Why adaptive: Allows fast-moving objects to change velocity more
    than slow-moving objects, while still catching teleportation.
    """
    if track.hits < 2:
        # Not enough history to judge
        return True

    # Compute velocity change magnitude
    vx_old, vy_old = track.velocity[:2]
    vx_new, vy_new = new_velocity[:2]

    dv = np.sqrt((vx_new - vx_old) ** 2 + (vy_new - vy_old) ** 2)

    # Current speed
    current_speed = np.sqrt(vx_old**2 + vy_old**2)

    # Adaptive threshold: allow larger changes for fast-moving objects
    max_change = max(
        current_speed * cfg.max_velocity_change_ratio,
        cfg.max_velocity_change_abs,
    )

    return dv <= max_change


def update_track_state_alpha_beta(
    track: Track,
    detection: Detection,
    predicted_position: list[float],
    cfg: ProducerConfig,
    frame_idx: int,
) -> bool:
    """
    Update track state using alpha-beta filter equations.

    Args:
        track: Track to update (mutated in-place)
        detection: Matched detection
        predicted_position: Predicted position before update
        cfg: Configuration with alpha/beta parameters
        frame_idx: Current frame index

    Updates in-place:
        - position using alpha (measurement smoothing)
        - velocity using beta (velocity smoothing)
        - confidence, hits, frames_lost, last_seen_frame

    Alpha-Beta filter equations:
        residual = measurement - prediction
        position' = prediction + alpha * residual
        velocity' = velocity + beta * residual
    """
    # Convert detection bbox to position
    measured_position = bbox_to_position(detection.bbox)

    # Compute residual
    residual = [measured_position[i] - predicted_position[i] for i in range(4)]

    # Update position with alpha
    alpha = cfg.tracking_alpha
    new_position = [predicted_position[i] + alpha * residual[i] for i in range(4)]

    # Update velocity with beta
    beta = cfg.tracking_beta
    new_velocity = [track.velocity[i] + beta * residual[i] for i in range(4)]

    # Validate velocity consistency
    if not check_velocity_consistency(track, new_velocity, cfg):
        # Suspicious velocity change, reject this update
        # (track will be marked as unmatched by caller)
        return False

    # Apply updates
    track.position = new_position
    track.velocity = new_velocity
    track.confidence = detection.confidence
    track.keypoints = detection.keypoints
    track.keypoint_scores = detection.keypoint_scores
    track.hits += 1
    track.frames_lost = 0  # Reset lost counter
    track.last_seen_frame = frame_idx
    return True


def initialize_new_track(
    detection: Detection,
    frame_idx: int,
) -> Track:
    """
    Create a new tentative track from an unmatched detection.

    Args:
        detection: Unmatched detection to track
        frame_idx: Current frame index

    Returns:
        New Track with initialized state
    """
    track_id = str(uuid.uuid4())
    position = bbox_to_position(detection.bbox)
    velocity = [0.0, 0.0, 0.0, 0.0]  # No motion assumed initially

    return Track(
        track_id=track_id,
        confidence=detection.confidence,
        keypoints=detection.keypoints,
        keypoint_scores=detection.keypoint_scores,
        position=position,
        velocity=velocity,
        age=1,
        hits=1,
        frames_lost=0,
        last_seen_frame=frame_idx,
    )


def cleanup_lost_tracks(
    tracks: list[Track],
    cfg: ProducerConfig,
) -> list[Track]:
    """
    Remove tracks that have been lost for too many frames.

    Args:
        tracks: All active tracks
        cfg: Configuration with max_frames_lost threshold

    Returns:
        Filtered list with lost tracks removed

    Performance: <0.1ms (simple filter)
    """
    return [t for t in tracks if t.frames_lost < cfg.max_frames_lost]


# ===== Main Tracker Class =====


class PlateTracker:
    """
    Stateful tracker managing active tracks across frames.

    Implements IOU + alpha-beta Kalman filtering with Lucas-Kanade motion compensation.
    Follows BoT-SORT two-tier matching strategy for robustness.
    """

    def __init__(self, cfg: ProducerConfig, logger: logging.Logger | None = None):
        """
        Initialize tracker with configuration.

        Args:
            cfg: Producer configuration
            logger: Optional logger instance
        """
        self.cfg = cfg
        self.logger = logger or logging.getLogger(__name__)
        self.tracks: list[Track] = []
        self.prev_frame: np.ndarray | None = None

        # LK frame-skip tracking
        self.prev_frame_idx: int = -1  # Last frame where LK ran successfully
        self.lk_coasting: bool = False  # True when LK disabled due to frame skip
        self.lk_coast_until_frame: int = -1  # Absolute frame_idx to re-enable LK

    def update(
        self,
        detections: list[Detection],
        curr_frame: np.ndarray,
        frame_idx: int,
    ) -> TrackerOutput:
        """
        Update tracks with new detections.

        Args:
            detections: YOLO detections from current frame
            curr_frame: Current frame (BGR or grayscale)
            frame_idx: Current frame index

        Returns:
            TrackerOutput with all tracks, confirmed tracks, and timings
        """
        timings = TrackingTimings()
        start_total = time.monotonic()
        curr_frame_gray: np.ndarray | None = None

        def _get_curr_gray() -> np.ndarray:
            nonlocal curr_frame_gray
            if curr_frame_gray is None:
                if len(curr_frame.shape) == 3:
                    curr_frame_gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)
                else:
                    curr_frame_gray = curr_frame
            assert curr_frame_gray is not None  # Type narrowing for pyright
            return curr_frame_gray

        # ===== STEP 1: ESTIMATE CAMERA MOTION (with frame-skip protection) =====
        start_lk = time.monotonic()
        camera_dx, camera_dy = 0.0, 0.0

        if self.cfg.enable_lk_motion and self.prev_frame is not None:
            # Calculate frame skip (delta between current and previous SAMPLED frame)
            frame_delta = frame_idx - self.prev_frame_idx

            # CASE 1: Currently coasting - check if delay period has elapsed
            if self.lk_coasting:
                if frame_idx >= self.lk_coast_until_frame:
                    # Coast period over - check if frame skip is now acceptable
                    if frame_delta <= self.cfg.lk_max_frame_skip:
                        # Exit coast mode - re-enable LK
                        self.lk_coasting = False
                        self.logger.info(
                            f"[Frame {frame_idx}] LK re-enabled after coasting. "
                            f"Frame delta: {frame_delta} frames"
                        )
                        # Fall through to run LK below
                    else:
                        # Still too large a skip - extend coast period
                        self.lk_coast_until_frame = frame_idx + self.cfg.lk_reenable_delay_frames
                        self.logger.debug(
                            f"[Frame {frame_idx}] LK coast extended (delta={frame_delta} > "
                            f"{self.cfg.lk_max_frame_skip}). Re-enable at frame {self.lk_coast_until_frame}"
                        )
                        # Skip LK, use pure AB filter
                        timings.lk_motion_ms = (time.monotonic() - start_lk) * 1000
                else:
                    # Still within coast delay period
                    if frame_idx % 30 == 0:  # Log every ~2 seconds @ 15fps
                        self.logger.debug(
                            f"[Frame {frame_idx}] LK coasting (re-enable at frame {self.lk_coast_until_frame})"
                        )
                    timings.lk_motion_ms = (time.monotonic() - start_lk) * 1000
                # Keep reference frame fresh while coasting
                if self.lk_coasting:
                    self.prev_frame = _get_curr_gray().copy()
                    self.prev_frame_idx = frame_idx

            # CASE 2: Not coasting - check if frame skip exceeds threshold
            elif frame_delta > self.cfg.lk_max_frame_skip:
                # Enter coast mode
                self.lk_coasting = True
                self.lk_coast_until_frame = frame_idx + self.cfg.lk_reenable_delay_frames

                self.logger.warning(
                    f"[Frame {frame_idx}] LK frame skip detected: {frame_delta} frames "
                    f"exceeds threshold ({self.cfg.lk_max_frame_skip}). "
                    f"Entering coast mode (pure AB filter). Re-enable at frame {self.lk_coast_until_frame}."
                )
                timings.lk_motion_ms = (time.monotonic() - start_lk) * 1000
                self.prev_frame = _get_curr_gray().copy()
                self.prev_frame_idx = frame_idx

            # CASE 3: Normal operation - frame skip within threshold, run LK
            if not self.lk_coasting and frame_delta <= self.cfg.lk_max_frame_skip:
                # Convert to grayscale for LK if needed
                curr_frame_gray = _get_curr_gray()
                assert curr_frame_gray is not None  # Type narrowing for pyright

                # Run Lucas-Kanade optical flow
                camera_dx, camera_dy = estimate_camera_motion(
                    self.prev_frame, curr_frame_gray, self.cfg
                )

                # Store grayscale for next frame
                self.prev_frame = curr_frame_gray.copy()
                self.prev_frame_idx = frame_idx
                timings.lk_motion_ms = (time.monotonic() - start_lk) * 1000

        elif self.cfg.enable_lk_motion:
            # First frame with LK enabled - initialize reference frame
            if len(curr_frame.shape) == 3:
                self.prev_frame = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)
            else:
                self.prev_frame = curr_frame.copy()
            self.prev_frame_idx = frame_idx
            timings.lk_motion_ms = (time.monotonic() - start_lk) * 1000

        # Step 2: Predict all tracks
        start_pred = time.monotonic()
        predicted_positions = []
        predicted_bboxes = []
        for track in self.tracks:
            pred_pos = predict_track_alpha_beta(track, camera_dx, camera_dy)
            predicted_positions.append(pred_pos)
            predicted_bboxes.append(position_to_bbox(pred_pos))
        timings.prediction_ms = (time.monotonic() - start_pred) * 1000

        # Step 3: Compute IOU matrix and match (detections already filtered by detect_plate_rois)
        start_match = time.monotonic()
        detection_bboxes = [det.bbox for det in detections]
        iou_matrix = compute_iou_matrix(predicted_bboxes, detection_bboxes)
        matches, unmatched_tracks, unmatched_detections = match_tracks_to_detections(
            self.tracks, detections, iou_matrix, self.cfg
        )
        timings.matching_ms = (time.monotonic() - start_match) * 1000

        # Step 4: Update matched tracks
        start_update = time.monotonic()
        successful_matches: list[tuple[int, int]] = []
        failed_track_indices: list[int] = []
        failed_det_indices: list[int] = []
        for track_idx, det_idx in matches:
            track = self.tracks[track_idx]
            detection = detections[det_idx]
            predicted_position = predicted_positions[track_idx]

            updated = update_track_state_alpha_beta(
                track, detection, predicted_position, self.cfg, frame_idx
            )
            if updated:
                successful_matches.append((track_idx, det_idx))
            else:
                failed_track_indices.append(track_idx)
                failed_det_indices.append(det_idx)

        if failed_track_indices:
            unmatched_tracks = list(set(unmatched_tracks).union(failed_track_indices))
        if failed_det_indices:
            unmatched_detections = list(set(unmatched_detections).union(failed_det_indices))

        # Step 5: Handle unmatched tracks (increment age, frames_lost)
        for track_idx in unmatched_tracks:
            track = self.tracks[track_idx]
            track.age += 1
            track.frames_lost += 1

        # Step 6: Initialize new tracks from unmatched detections
        num_tracks_created = 0
        for det_idx in unmatched_detections:
            detection = detections[det_idx]
            new_track = initialize_new_track(detection, frame_idx)
            self.tracks.append(new_track)
            num_tracks_created += 1

        # Also increment age for matched tracks
        for track_idx, _ in successful_matches:
            self.tracks[track_idx].age += 1

        timings.update_ms = (time.monotonic() - start_update) * 1000

        # Step 7: Cleanup lost tracks
        start_cleanup = time.monotonic()
        lost_track_ids = [
            track.track_id for track in self.tracks if track.frames_lost >= self.cfg.max_frames_lost
        ]
        self.tracks = cleanup_lost_tracks(self.tracks, self.cfg)
        num_tracks_lost = len(lost_track_ids)
        timings.cleanup_ms = (time.monotonic() - start_cleanup) * 1000

        # Step 8: Extract confirmed tracks
        confirmed_tracks = [t for t in self.tracks if t.is_confirmed(self.cfg.min_hits)]
        num_confirmed_tracks = len(confirmed_tracks)

        timings.total_ms = (time.monotonic() - start_total) * 1000

        return TrackerOutput(
            tracks=self.tracks,
            confirmed_tracks=confirmed_tracks,
            camera_motion=(camera_dx, camera_dy),
            timings=timings,
            lost_track_ids=lost_track_ids,
            num_tracks_created=num_tracks_created,
            num_tracks_confirmed=num_confirmed_tracks,
            num_tracks_lost=num_tracks_lost,
        )


# ===== Public API =====


def track_detections(
    detections: list[Detection],
    curr_frame: np.ndarray,
    frame_idx: int,
    tracker: PlateTracker,
) -> TrackerOutput:
    """
    Primary entrypoint for tracking detections across frames.

    Args:
        detections: Geometrically-filtered detections from detect_plate_rois()
        curr_frame: Current frame (BGR or grayscale) - auto-converted as needed
        frame_idx: Current frame index
        tracker: Stateful tracker object (maintains track history)

    Returns:
        TrackerOutput containing all tracks, confirmed tracks, lifecycle stats, and timings

    Note:
        - Detections are already filtered by geometric constraints (size, aspect ratio)
        - If LK motion compensation is enabled and curr_frame is BGR, it will be
          converted to grayscale internally (~0.3-1ms). The original BGR frame
          can still be used for cropping and quality analysis.
    """
    return tracker.update(detections, curr_frame, frame_idx)
