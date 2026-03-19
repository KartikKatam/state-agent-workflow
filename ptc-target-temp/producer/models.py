from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

# Canonical type alias from name-bank
TrackId = str  # UUID for tracked license plate object


class LoggingMode(Enum):
    """
    Pipeline logging verbosity modes.

    PRODUCTION: Minimal logging - errors and critical warnings only
    DEBUG: Full per-frame logging for development and troubleshooting
    TUNING: Parameter optimization focus - structured metrics for ML models
    """

    PRODUCTION = "production"
    DEBUG = "debug"
    TUNING = "tuning"


@dataclass
class Detection:
    """YOLO plate detection result."""

    bbox: list[float]  # [x1, y1, x2, y2] absolute pixels
    confidence: float
    class_id: int
    frame_idx: int
    keypoints: list[tuple[float, float]] | None = None  # Pose keypoints in frame coords
    keypoint_scores: list[float] | None = None  # Per-keypoint confidence scores


@dataclass
class DetectionTimings:
    """Performance metrics from a single detection run (milliseconds)."""

    prepare_ms: float = 0.0
    inference_ms: float = 0.0
    postprocess_ms: float = 0.0
    total_ms: float = 0.0


@dataclass
class DetectorOutput:
    """
    Bundle of detections and performance timings.

    After geometric filtering is applied in detection stage.
    """

    raw_detections: list[Detection]  # All YOLO detections (before geometric filter)
    filtered_detections: list[Detection]  # After geometric filtering (passed to tracker)
    filter_reasons: dict[int, str]  # Map of detection_idx → rejection reason (for tuning)
    timings: DetectionTimings = field(default_factory=DetectionTimings)
    sampled: bool = False  # Explicit flag indicating if frame was sampled


@dataclass
class Track:
    """
    Represents a tracked license plate across frames.

    State is stored as position [cx, cy, w, h] and velocity [vx, vy, vw, vh].
    bbox is derived from position when needed for output/matching to prevent drift.
    """

    track_id: TrackId
    confidence: float  # Latest detection confidence

    # Primary state (alpha-beta filter)
    position: list[float]  # [cx, cy, w, h] - always length 4
    velocity: list[float]  # [vx, vy, vw, vh] - always length 4

    # Track lifecycle counters
    keypoints: list[tuple[float, float]] | None = None  # Latest keypoints in frame coords
    keypoint_scores: list[float] | None = None  # Per-keypoint confidence scores
    age: int = 0  # Total frames since creation (incremented every frame)
    hits: int = 0  # Successful matches with detections (incremented only when matched)
    frames_lost: int = 0  # Consecutive frames without match (reset to 0 when matched)
    last_seen_frame: int = 0  # Frame index of last successful match

    def __post_init__(self):
        """Validate state vector lengths."""
        assert len(self.position) == 4, f"position must be length 4, got {len(self.position)}"
        assert len(self.velocity) == 4, f"velocity must be length 4, got {len(self.velocity)}"

    def get_bbox(self) -> list[float]:
        """Derive bbox [x1, y1, x2, y2] from current position."""
        cx, cy, w, h = self.position
        x1 = cx - w / 2
        y1 = cy - h / 2
        x2 = cx + w / 2
        y2 = cy + h / 2
        return [x1, y1, x2, y2]

    def is_confirmed(self, min_hits: int) -> bool:
        """Check if track has enough hits to be considered confirmed."""
        return self.hits >= min_hits


@dataclass
class TrackingTimings:
    """Performance metrics from tracking operation (milliseconds)."""

    lk_motion_ms: float = 0.0  # Lucas-Kanade global motion estimation
    prediction_ms: float = 0.0  # Alpha-beta prediction for all tracks
    matching_ms: float = 0.0  # IOU computation and assignment
    update_ms: float = 0.0  # Track state updates
    cleanup_ms: float = 0.0  # Removing lost tracks
    total_ms: float = 0.0


@dataclass
class TrackerOutput:
    """Bundle of active tracks and performance timings."""

    tracks: list[Track]  # All active tracks (confirmed + tentative)
    confirmed_tracks: list[Track]  # Only tracks ready for cropping (hits >= min_hits)
    camera_motion: tuple[float, float]  # (dx, dy) estimated camera motion
    timings: TrackingTimings = field(default_factory=TrackingTimings)
    lost_track_ids: list[TrackId] = field(default_factory=list)  # Track IDs removed this frame

    # Lifecycle stats for this frame
    num_tracks_created: int = 0  # New tracks initialized this frame
    num_tracks_confirmed: int = 0  # All tracks that are confirmed (hits >= min_hits)
    num_tracks_lost: int = 0  # Tracks deleted this frame (frames_lost >= max)


@dataclass
class RoiImage:
    """
    Cropped region of interest containing a license plate candidate.

    Canonical data structure from name-bank for passing plate crops through the pipeline.
    After cropping, the original frame is discarded - only this ROI travels downstream.
    """

    track_id: TrackId  # UUID linking this ROI to its track
    crop_img: np.ndarray  # Cropped BGR image (H×W×3 uint8), may be adaptively resized
    bbox: list[float]  # Original detection bbox [x1, y1, x2, y2] in frame coordinates
    padded_bbox: list[float]  # Padded bbox [x1, y1, x2, y2] before any resize (frame coordinates)
    frame_idx: int  # Frame number where this ROI was extracted
    confidence: float  # Detection confidence from YOLO
    frame_width: int  # Original frame width (pixels) for position normalization
    frame_height: int  # Original frame height (pixels) for position normalization
    keypoints: list[tuple[float, float]] | None = None  # Pose keypoints in crop coords
    keypoint_scores: list[float] | None = None  # Per-keypoint confidence scores
    was_resized: bool = False  # True if adaptive resize was applied (>400px → 400px)
    resize_scale: float = 1.0  # Scale factor applied during resize (1.0 = no resize)
    crop_width: int = 0  # Final crop width after resizing (pixels)
    crop_height: int = 0  # Final crop height after resizing (pixels)


@dataclass
class FastQualityMetrics:
    """Cheap GPU-computed metrics for Producer fast quality gating."""

    focus_tenengrad: float  # Mean gradient magnitude (Sobel-based)
    brightness_mean: float  # Mean gray value [0..1]
    contrast_std: float  # Std of gray values
    over_exposed_frac: float  # Fraction of pixels > over threshold
    under_exposed_frac: float  # Fraction of pixels < under threshold
    band_edge_mean: float  # Mean gradient magnitude in central character band
    gradient_histogram: np.ndarray  # L2-normalized feature vector (25,) float32
    # [16 orientation bins (0-180°), 9 spatial quadrant sharpness (3×3 grid)]
    # Enables pose/rotation difference detection for buffer diversity


@dataclass
class RoiFastQuality:
    """
    ROI plus fast quality metrics.

    Used as the hand-off object between Producer fast QA and the ROI buffer.
    """

    roi: RoiImage  # Original cropped ROI (CPU BGR image)
    metrics: FastQualityMetrics  # Raw metrics (for logging / tuning)
    quality_score: float  # Scalar for ranking, derived from metrics
    passes_min_quality: bool  # Hard gate result
    thumb_gray: np.ndarray  # Small H_thumb x W_thumb uint8 thumbnail


@dataclass
class IdBin:
    """
    Per-ID buffer storing diverse, high-quality ROI candidates.

    Maintains version counter for Consumer snapshot coordination.
    Max capacity enforces diversity via similarity-based replacement.
    """

    track_id: TrackId  # UUID for this license plate
    entries: list[RoiFastQuality]  # Current ROI pool (max capacity per config)
    version: int  # Increments on append/replace (Consumer sync)
    last_update_frame_idx: int  # Most recent frame_idx inserted
    successful_insertions: int = 0  # Successful appends/replacements (for stats)
    attempted_insertions: int = 0  # Quality-gate attempts (for bootstrap logic)

    def is_bootstrapping(self, bootstrap_k: int) -> bool:
        """Check if bin is still in bootstrap phase (relaxed quality threshold)."""
        return self.attempted_insertions < bootstrap_k


@dataclass
class BinSnapshot:
    """
    Immutable snapshot of an IdBin for Consumer processing.

    Shallow-copied from IdBin to allow async Consumer analysis while
    Producer continues updating the original bin. Uses shallow copy for
    efficiency - shares image data with producer buffer.

    IMPORTANT: Consumer preprocessing MUST use functional style (create new arrays)
    and NOT mutate crop_img in-place to maintain isolation.
    """

    track_id: TrackId
    version: int  # Snapshot version (matches IdBin.version at copy time)
    entries: list[RoiFastQuality]  # Shallow copy of ROI entries (shares image data with buffer)
    last_update_frame_idx: int  # Frame index of most recent entry


@dataclass
class BufferStats:
    """
    Per-ID buffer insertion statistics for monitoring/debugging.

    Can be queried by external logging systems without impacting hot path performance.
    Persists even after bin eviction for post-mortem analysis.
    """

    track_id: TrackId
    total_attempts: int  # Total ROIs received for this ID (includes all drops)
    quality_gate_drops: int  # Dropped at quality gate (failed min_quality threshold)
    out_of_order_drops: int  # Dropped due to frame_idx monotonicity violation
    appends: int  # Successful appends (bin not full, novel pose)
    replaces: int  # Successful replacements (similar or worst eviction)
    similarity_drops: int  # Dropped due to similarity (not good enough to replace)
    current_bin_size: int  # Current number of entries in bin
    current_version: int  # Current version number


@dataclass
class PercentileWindow:
    """
    Sliding window for percentile calculations (p50, p95, p99).

    Uses deque with maxlen for O(1) append and automatic eviction.
    Percentiles computed on-demand using numpy (O(N log N) sort).
    """

    maxlen: int
    values: deque[float] = field(default_factory=deque)

    def __post_init__(self):
        """Reset deque with configured maxlen."""
        self.values = deque(maxlen=self.maxlen)

    def append(self, value: float) -> None:
        """Add value to window (oldest evicted if full)."""
        self.values.append(value)

    def percentile(self, p: float) -> float:
        """
        Compute percentile (0-100) over current window.

        Args:
            p: Percentile value (e.g., 50 for median, 99 for p99)

        Returns:
            Percentile value, or 0.0 if insufficient data (<10 samples)
        """
        if len(self.values) < 10:
            return 0.0
        return float(np.percentile(list(self.values), p))

    @property
    def p50(self) -> float:
        """Median (50th percentile)."""
        return self.percentile(50)

    @property
    def p95(self) -> float:
        """95th percentile."""
        return self.percentile(95)

    @property
    def p99(self) -> float:
        """99th percentile."""
        return self.percentile(99)


@dataclass
class FrameResult:
    """
    Lightweight summary of pipeline processing for a single frame.

    Does NOT contain full data (crops, buffers) - only counts and timing.
    Full data lives in the buffer and is accessed via get_updated_bins().
    """

    frame_idx: int
    timestamp_ms: int | None

    # Stage outputs (counts only, not full data)
    num_raw_detections: int  # YOLO detections before geometric filter
    num_filtered_detections: int  # After geometric filtering (passed to tracker)
    num_rejected_detections: int  # Rejected by geometric filter
    num_active_tracks: int  # Total tracks (confirmed + tentative)
    num_confirmed_tracks: int  # Tracks eligible for cropping
    num_crops: int  # Successful ROI extractions
    num_quality_passed: int  # ROIs passing hard quality gate
    buffer_updates: int  # Successful buffer insertions

    # Performance metrics (milliseconds)
    detection_ms: float
    tracking_ms: float
    cropping_ms: float
    quality_ms: float
    buffer_ms: float
    total_ms: float

    # Status flags
    sampled: bool
    corner_cnn_ms: float = 0.0
    error: str | None = None


@dataclass
class PipelineStats:
    """
    Cumulative statistics across all frames processed by pipeline.

    Used for monitoring, debugging, and config parameter optimization.
    """

    # Frame-level counts
    total_frames_received: int = 0
    total_frames_sampled: int = 0
    total_frames_skipped: int = 0

    # Detection stats (geometric filtering in detection stage)
    total_raw_detections: int = 0  # YOLO detections before geometric filter
    total_valid_detections: int = 0  # After geometric filter (passed to tracker)
    total_filtered_detections: int = 0  # Rejected by geometric filter

    # Tracking stats
    total_tracks_created: int = 0
    total_tracks_confirmed: int = 0
    total_tracks_lost: int = 0

    # Cropping stats
    total_crop_attempts: int = 0
    total_crop_successes: int = 0
    total_crop_failures: int = 0

    # Quality stats
    total_quality_analyzed: int = 0
    total_quality_passed: int = 0
    total_quality_failed: int = 0

    # Buffer stats
    total_buffer_insertions: int = 0
    total_buffer_drops: int = 0

    # Performance tracking (milliseconds)
    avg_frame_ms: float = 0.0
    max_frame_ms: float = 0.0
    min_frame_ms: float = 0.0
    frames_over_budget: int = 0

    # Stage-specific timing averages
    avg_detection_ms: float = 0.0
    avg_tracking_ms: float = 0.0
    avg_cropping_ms: float = 0.0
    avg_quality_ms: float = 0.0
    avg_buffer_ms: float = 0.0
    avg_corner_cnn_ms: float = 0.0

    # Corner CNN stats
    total_corner_cnn_analyzed: int = 0

    # Error tracking
    total_errors: int = 0
    last_error: str | None = None
    last_error_frame: int = -1

    # ===== NEW: Sliding window percentiles =====
    # Private fields - use get_percentile() method or convenience properties

    # Stores recent timing measurements for each stage (last N frames)
    # Enables p50/p95/p99 calculation over recent window instead of all-time
    _windows: dict[str, PercentileWindow] = field(default_factory=dict, init=False, repr=False)
    _window_size: int = field(default=150, init=False, repr=False)

    def __post_init__(self):
        """
        Initialize percentile windows for all timing metrics.

        Called automatically after dataclass __init__.
        Creates a sliding window for each timing metric to track recent performance.
        """
        # Create windows for all 7 timing metrics
        for metric in [
            "frame_ms",
            "detection_ms",
            "tracking_ms",
            "cropping_ms",
            "quality_ms",
            "buffer_ms",
            "corner_cnn_ms",
        ]:
            self._windows[metric] = PercentileWindow(maxlen=self._window_size)

    def set_window_size(self, size: int) -> None:
        """
        Set window size for percentile calculations.

        Should be called immediately after construction, before any updates.

        Args:
            size: Number of recent frames to include in percentile window
        """
        self._window_size = size
        self.__post_init__()  # Reinitialize windows with new size

    def get_percentile(self, metric: str, percentile: float) -> float:
        """
        Get percentile value for a specific metric.

        Args:
            metric: Timing metric name (e.g., 'frame_ms', 'detection_ms')
            percentile: Percentile to compute (50, 95, or 99)

        Returns:
            Percentile value in milliseconds, or 0.0 if insufficient data

        Example:
            stats.get_percentile('frame_ms', 95)  # p95 of recent frame times
        """
        if metric not in self._windows:
            return 0.0
        return self._windows[metric].percentile(percentile)

    # Convenience properties for common percentiles (cleaner than manual get_percentile calls)
    # Pattern: p{percentile}_{metric} (e.g., p95_frame_ms)

    @property
    def p50_frame_ms(self) -> float:
        """Median (p50) of recent frame times."""
        return self.get_percentile("frame_ms", 50)

    @property
    def p95_frame_ms(self) -> float:
        """95th percentile of recent frame times."""
        return self.get_percentile("frame_ms", 95)

    @property
    def p99_frame_ms(self) -> float:
        """99th percentile of recent frame times."""
        return self.get_percentile("frame_ms", 99)

    @property
    def p50_detection_ms(self) -> float:
        """Median (p50) of recent detection times."""
        return self.get_percentile("detection_ms", 50)

    @property
    def p95_detection_ms(self) -> float:
        """95th percentile of recent detection times."""
        return self.get_percentile("detection_ms", 95)

    @property
    def p99_detection_ms(self) -> float:
        """99th percentile of recent detection times."""
        return self.get_percentile("detection_ms", 99)

    @property
    def p50_tracking_ms(self) -> float:
        """Median (p50) of recent tracking times."""
        return self.get_percentile("tracking_ms", 50)

    @property
    def p95_tracking_ms(self) -> float:
        """95th percentile of recent tracking times."""
        return self.get_percentile("tracking_ms", 95)

    @property
    def p99_tracking_ms(self) -> float:
        """99th percentile of recent tracking times."""
        return self.get_percentile("tracking_ms", 99)

    @property
    def p50_cropping_ms(self) -> float:
        """Median (p50) of recent cropping times."""
        return self.get_percentile("cropping_ms", 50)

    @property
    def p95_cropping_ms(self) -> float:
        """95th percentile of recent cropping times."""
        return self.get_percentile("cropping_ms", 95)

    @property
    def p99_cropping_ms(self) -> float:
        """99th percentile of recent cropping times."""
        return self.get_percentile("cropping_ms", 99)

    @property
    def p50_quality_ms(self) -> float:
        """Median (p50) of recent quality analysis times."""
        return self.get_percentile("quality_ms", 50)

    @property
    def p95_quality_ms(self) -> float:
        """95th percentile of recent quality analysis times."""
        return self.get_percentile("quality_ms", 95)

    @property
    def p99_quality_ms(self) -> float:
        """99th percentile of recent quality analysis times."""
        return self.get_percentile("quality_ms", 99)

    @property
    def p50_buffer_ms(self) -> float:
        """Median (p50) of recent buffer insertion times."""
        return self.get_percentile("buffer_ms", 50)

    @property
    def p95_buffer_ms(self) -> float:
        """95th percentile of recent buffer insertion times."""
        return self.get_percentile("buffer_ms", 95)

    @property
    def p99_buffer_ms(self) -> float:
        """99th percentile of recent buffer insertion times."""
        return self.get_percentile("buffer_ms", 99)

    @property
    def p50_corner_cnn_ms(self) -> float:
        """Median (p50) of recent corner CNN times."""
        return self.get_percentile("corner_cnn_ms", 50)

    @property
    def p95_corner_cnn_ms(self) -> float:
        """95th percentile of recent corner CNN times."""
        return self.get_percentile("corner_cnn_ms", 95)

    @property
    def p99_corner_cnn_ms(self) -> float:
        """99th percentile of recent corner CNN times."""
        return self.get_percentile("corner_cnn_ms", 99)
