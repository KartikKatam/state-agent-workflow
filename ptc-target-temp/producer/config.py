from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import LoggingMode


@dataclass
class ProducerConfig:
    """
    Tunable parameters for the Producer block (detection, sampling, tracking, quality, buffer).

    Detection-specific params are consumed by ops_detection.YoloDetector and FrameSampler.
    """

    # Detection
    yolo_model_path: str = "models/yolov11m.pt"  # Path to .engine (preferred) or .pt
    plate_class_id: int = 0
    input_size: tuple[int, int] = (960, 544)  # (width, height)
    conf_threshold: float = 0.25
    iou_threshold: float = 0.45
    device: str = "cuda"
    max_detections: int = 200
    warmup_runs: int = 1

    # ===== Corner CNN Configuration =====
    corner_cnn_enabled: bool = False  # Master toggle (disabled until model trained)
    corner_cnn_model_path: str = "models/corner_cnn.engine"  # TensorRT engine file
    corner_cnn_input_h: int = 80  # CNN input height (letterbox target)
    corner_cnn_input_w: int = 256  # CNN input width (letterbox target)
    corner_cnn_confidence_threshold: float = 0.3  # Below this → keypoints=None
    corner_cnn_device: str = "cuda"  # CUDA device for inference

    # Sampling cadence (ms between samples ~ 1/15s)
    frame_sample_interval_ms: float = 1000.0 / 15.0

    # ===== Tracking Configuration =====

    # Alpha-Beta filter parameters (configurable for tuning)
    tracking_alpha: float = 0.85  # Position smoothing (0-1, higher = trust measurement more)
    tracking_beta: float = 0.05  # Velocity smoothing (0-1, lower = smoother velocity)

    # IOU matching thresholds (two-tier system from BoT-SORT)
    iou_threshold_confirmed: float = 0.25  # For confirmed tracks (hits >= min_hits)
    iou_threshold_tentative: float = (
        0.40  # For tentative tracks (stricter to prevent false positives)
    )

    # Track lifecycle management
    min_hits: int = 3  # Minimum successful matches before track is confirmed
    max_frames_lost: int = 5  # Consecutive missed detections before track deletion

    # False positive geometric filters
    # Matrice 3D tele (162mm equiv) at 200ft/1x zoom: plate ~43px wide (too small for OCR)
    # 100px threshold requires ~2.5x zoom, enabling early detection vs. waiting for 4x (170px)
    # Provides ~20px/char - marginal but workable for TrOCR. Buffer prioritization upgrades quality.
    min_bbox_width: int = 100  # Minimum plate width in pixels (requires 2.5x+ zoom at 200ft)
    min_bbox_height: int = 40  # Minimum plate height (accounts for perspective distortion)
    min_aspect_ratio: float = 1.5  # Minimum width/height ratio (plates are wider than tall)
    max_aspect_ratio: float = 5.0  # Maximum width/height ratio

    # Velocity consistency check (adaptive thresholds)
    max_velocity_change_ratio: float = 1.0  # Allow 100% velocity change relative to current speed
    max_velocity_change_abs: float = 50.0  # OR 50 pixels absolute change (whichever is larger)

    # Lucas-Kanade global motion compensation
    enable_lk_motion: bool = True  # Enable LK-based camera motion estimation
    lk_max_corners: int = 100  # Number of feature points to track
    lk_quality_level: float = 0.01  # Corner detection quality (0-1)
    lk_min_distance: int = 10  # Minimum distance between corners in pixels
    lk_win_size: tuple[int, int] = (21, 21)  # LK window size
    lk_max_level: int = 2  # Pyramid levels for large motions

    # Lucas-Kanade frame-skip protection (coasting logic)
    # Maximum allowed frame gap for LK optical flow (in absolute frame indices)
    # This represents the temporal distance between sampled frames:
    #   - At 30fps video sampled @ 15fps: normal delta = 2 (66ms), threshold = 2
    #   - At 15fps video sampled @ 15fps: normal delta = 1 (66ms), threshold = 2
    #   - Delta > threshold indicates frame drops/stutter (>133ms temporal gap)
    # LK optical flow degrades rapidly with gaps >100-150ms due to motion magnitude
    lk_max_frame_skip: int = 2

    # Re-enable delay: frames to wait before re-enabling LK after a skip event
    # Prevents thrashing if frames continue to stutter
    # Value = 1 means wait for one stable sampled frame before re-attempting LK
    lk_reenable_delay_frames: int = 1

    # ===== ROI Cropping Configuration =====

    # Dynamic padding: 15% scales naturally with plate quality, minimum 12px floor for marginal sizes
    # At 100px (marginal): 15% = 15px → clamped to 12px min → 124×52px crop
    # At 170px (good): 15% = 25px → 220×90px crop (optimal for CLAHE/rotation estimation)
    # At 800px (oversized): 15% = 120px → 1040px crop → resized to 400px (46px final padding)
    crop_padding_ratio: float = 0.15  # Percentage padding (scales naturally with bbox size)
    crop_padding_min_px: int = 12  # Minimum padding (ensures edge context at marginal quality)

    # Adaptive resizing: prevents buffer bloat from extreme zoom while preserving relative context
    # Crops wider than threshold are resized down, padding scales proportionally
    # Example: 1200px crop with 180px padding → 800px crop with 120px padding (same 15% ratio)
    #
    # TUNED FOR PREPROCESSING QUALITY: Set to 800px as optimal balance.
    # Consumer preprocessing (CLAHE, rotation estimation) benefits from larger images:
    # - 800px crop: 100×41 pixel CLAHE tiles, ±0.7° rotation accuracy (97% quality)
    # - After preprocessing, consumer crops tight to plate and resizes to 384×384 for TrOCR
    # - Only resizes extreme 5x+ zoom cases (minimal impact on typical 2-4x zoom)
    # - Saves 55% memory vs unlimited (288MB → 128MB buffer typical)
    crop_adaptive_resize_threshold: int = 800  # Optimal for preprocessing quality + memory

    # Crop dimension constraints
    crop_min_width: int = 100  # Minimum crop width (matches min_bbox_width)
    crop_min_height: int = 40  # Minimum crop height (matches min_bbox_height)

    # ===== Fast Quality Analysis (Producer) =====

    # Toggle
    fast_quality_enabled: bool = True

    # Feature vector for pose/appearance difference detection
    fast_quality_gradient_histogram_bins: int = 16  # Orientation bins (0-180°, unsigned)
    fast_quality_spatial_quadrants: int = (
        9  # Spatial sharpness stats (3×3 grid: TL,TC,TR,ML,MC,MR,BL,BC,BR)
    )

    # Grayscale QA downsample size for metrics (Tenengrad, brightness, etc.)
    fast_quality_h_metrics: int = 48  # Vertical pixels
    fast_quality_w_metrics: int = 160  # Horizontal pixels (approx plate aspect)

    # Thumbnail size for buffer similarity checks
    # Thumb defaults match metrics size to avoid resize overhead; tweak if you
    # want a cheaper similarity check than the QA resolution.
    fast_quality_h_thumb: int = 48
    fast_quality_w_thumb: int = 160

    # Hard gate thresholds (tune later with data)
    fast_quality_focus_min: float = 0.0  # Tenengrad minimum
    fast_quality_contrast_min: float = 0.0
    fast_quality_band_edge_min: float = 0.0
    fast_quality_bright_min: float = 0.0  # [0..1] mean intensity
    fast_quality_bright_max: float = 1.0
    fast_quality_over_exposed_max: float = 1.0  # Fraction of pixels near white
    fast_quality_under_exposed_max: float = 1.0  # Fraction near black

    # Brightness / exposure tuning
    fast_quality_bright_target: float = 0.5  # Ideal mid-gray
    fast_quality_bright_tol: float = 0.3  # How far from target is acceptable
    fast_quality_over_threshold: float = 0.94  # > this is "overexposed"
    fast_quality_under_threshold: float = 0.06  # < this is "underexposed"

    # Central band (character band) vertical fraction
    fast_quality_band_top_frac: float = 0.30  # Top of band = 30% of H
    fast_quality_band_bottom_frac: float = 0.70  # Bottom of band = 70% of H

    # "Good" values for normalization (rough, tune later)
    fast_quality_focus_good: float = 1.0
    fast_quality_contrast_good: float = 0.1
    fast_quality_band_edge_good: float = 1.0

    # Weights for scalar quality_score
    fast_quality_w_focus: float = 0.40
    fast_quality_w_contrast: float = 0.25
    fast_quality_w_band: float = 0.15
    fast_quality_w_bright: float = 0.15
    fast_quality_w_exposure: float = 0.05

    # Exposure penalty weights
    fast_quality_over_weight: float = 1.0
    fast_quality_under_weight: float = 0.5

    # ===== ID Buffer Management =====

    # Capacity & lifecycle
    buffer_bin_capacity: int = 32  # Max ROIs per track_id
    buffer_bootstrap_k_insertions: int = 3  # Relaxed threshold for first K quality-gate attempts

    # Quality gating
    buffer_min_quality: float = 0.25  # Base quality_score threshold (0-1)
    buffer_bootstrap_discount: float = 0.75  # Multiplier for bootstrap phase (0.25 × 0.75 = 0.1875)

    # Similarity & diversity (hybrid: pixel SSIM + gradient histogram cosine similarity)
    buffer_similarity_threshold: float = (
        0.80  # Combined similarity threshold for "too similar" (0-1)
    )
    buffer_novelty_weight_pixel: float = 0.6  # Weight for pixel similarity (SSIM on thumbnail)
    buffer_novelty_weight_feature: float = 0.4  # Weight for feature similarity (gradient histogram)

    # Replacement margins (prevent flapping)
    buffer_quality_margin_similar: float = (
        0.02  # Quality improvement needed to replace similar entry
    )
    buffer_quality_margin_worst: float = (
        0.05  # Quality improvement needed to replace worst entry (novel case)
    )

    # Band edge validation (character presence check)
    buffer_band_edge_min: float = 0.5  # Minimum band_edge_mean for valid character region

    # ===== Pipeline Performance =====

    pipeline_frame_budget_ms: float = 66.0  # Target frame budget (~15 FPS)
    pipeline_skip_on_no_detections: bool = True  # Skip downstream if no filtered detections
    pipeline_skip_on_no_confirmed: bool = True  # Skip crop/quality if no confirmed tracks

    # ===== Logging Configuration =====

    pipeline_logging_mode: LoggingMode = LoggingMode.PRODUCTION
    logging_queue_size: int = 1000  # Max log records buffered in memory (non-blocking)

    # Sliding window size for percentile metrics (p50, p95, p99)
    # At 15fps: 150 frames = 10 seconds of recent data
    # Increase if percentiles are too noisy, decrease for faster response to issues
    pipeline_percentile_window_frames: int = 150

    # Structured JSONL output paths (all modes write structured data for ML analysis)
    production_log_path: str = "logs/production_metrics.jsonl"
    debug_log_path: str = "logs/debug_metrics.jsonl"
    tuning_log_path: str = "logs/tuning_metrics.jsonl"

    # Production mode (minimal logging - errors + periodic stats)
    production_stats_interval_frames: int = 4500  # Every 5 min @ 15fps
    production_frame_interval: int = 300  # Log every 300 frames (~20 sec @ 15fps)

    # Debug mode (verbose per-frame logging)
    debug_frame_interval: int = 1  # Log every frame
    debug_stats_interval_frames: int = 150  # Every 10 sec
    debug_log_detections: bool = True
    debug_log_tracks: bool = True
    debug_log_quality: bool = True

    # Tuning mode (full structured JSONL for ML)
    tuning_frame_interval: int = 1  # Log every frame
    tuning_stats_interval_frames: int = 300  # Every 20 sec


def load_producer_config(**overrides) -> ProducerConfig:
    """
    Create ProducerConfig with optional runtime overrides.

    Args:
        **overrides: Config fields to override

    Returns:
        ProducerConfig with defaults + overrides applied

    Example:
        # Use all defaults
        cfg = load_producer_config()

        # Override specific params
        cfg = load_producer_config(
            min_bbox_width=120,
            pipeline_logging_mode=LoggingMode.TUNING
        )

        # From CLI args
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--log-mode", default="production")
        args = parser.parse_args()

        cfg = load_producer_config(
            pipeline_logging_mode=LoggingMode(args.log_mode)
        )
    """
    cfg = ProducerConfig()

    # Apply overrides
    for key, value in overrides.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
        else:
            raise ValueError(f"Unknown config parameter: {key}")

    # Validate logging mode is enum (handle string if passed)
    if isinstance(cfg.pipeline_logging_mode, str):
        try:
            cfg.pipeline_logging_mode = LoggingMode(cfg.pipeline_logging_mode.lower())
        except ValueError:
            raise ValueError(
                f"Invalid pipeline_logging_mode: '{cfg.pipeline_logging_mode}'. "
                f"Must be one of: {[m.value for m in LoggingMode]}"
            ) from None

    # Ensure log output directories exist for all modes
    log_path_map = {
        LoggingMode.PRODUCTION: cfg.production_log_path,
        LoggingMode.DEBUG: cfg.debug_log_path,
        LoggingMode.TUNING: cfg.tuning_log_path,
    }
    log_path = log_path_map.get(cfg.pipeline_logging_mode)
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    return cfg
