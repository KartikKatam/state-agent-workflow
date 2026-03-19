"""
Orchestrates Producer workflow: frame sampling → detection → tracking → cropping → quality → buffer.

Stateful: Maintains detector, tracker, and buffer across frames.
Single-threaded: Not thread-safe (designed for single producer).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import queue
import time

import numpy as np

from .buffer import IdBufferManager
from .config import ProducerConfig
from .models import (
    BinSnapshot,
    BufferStats,
    FrameResult,
    LoggingMode,
    PipelineStats,
    TrackId,
)
from .ops_cropping import crop_tracks
from .ops_detection import FrameSampler, YoloDetector, detect_plate_rois
from .ops_quality_fast import fast_quality_analyze_rois
from .ops_tracking import PlateTracker, track_detections


class NonBlockingQueueHandler(logging.handlers.QueueHandler):
    """
    QueueHandler that drops oldest log when queue is full (no blocking).

    Ensures logging never blocks the main pipeline, even under I/O pressure.
    Drops oldest log records first to preserve recent context.
    """

    def enqueue(self, record: logging.LogRecord) -> None:
        """
        Put log record on queue, drop oldest if full.

        Args:
            record: Log record to enqueue
        """
        try:
            # Fast path: queue not full
            self.queue.put_nowait(record)
        except queue.Full:
            # Queue full - drop oldest record (from front) to make room
            try:
                self.queue.get_nowait()  # type: ignore[reportAttributeAccessIssue]  # Remove oldest
                self.queue.put_nowait(record)  # Add new
            except Exception:
                # Race condition or queue closed, drop this log
                # (Acceptable: logging is best-effort under extreme load)
                pass


class JSONLFormatter(logging.Formatter):
    """
    Formats log records as single-line JSON objects (JSONL format).

    Produces structured logs suitable for ML analysis, pandas DataFrames,
    and query tools like jq. All fields are flattened (no nested objects).
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record as compact JSON line.

        Args:
            record: Log record to format

        Returns:
            Single-line JSON string
        """
        # Build flat JSON object
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add structured metrics if present (from pipeline logging)
        metrics = getattr(record, "metrics", None)
        if metrics is not None:
            log_entry.update(metrics)

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Compact JSON (no whitespace)
        return json.dumps(log_entry, separators=(",", ":"))


def setup_async_logging(
    cfg: ProducerConfig, logger_name: str = "producer"
) -> tuple[logging.Logger, logging.handlers.QueueListener | None]:
    """
    Setup non-blocking logging with QueueHandler.

    All modes write structured JSONL to file for ML analysis.
    Console output is human-readable (PRODUCTION/DEBUG only show errors/debug).

    Args:
        cfg: Producer configuration with logging mode
        logger_name: Logger name (default: "producer")

    Returns:
        (logger, listener) tuple. listener must be stopped on shutdown.
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)  # Capture all, filter at handler level

    # Remove existing handlers (prevent duplicates on re-init)
    logger.handlers.clear()

    # Create queue for async logging (bounded to prevent OOM)
    log_queue = queue.Queue(maxsize=cfg.logging_queue_size)

    # Create handlers based on mode
    handlers = []

    # Map mode to log file path
    log_path_map = {
        LoggingMode.PRODUCTION: cfg.production_log_path,
        LoggingMode.DEBUG: cfg.debug_log_path,
        LoggingMode.TUNING: cfg.tuning_log_path,
    }

    if cfg.pipeline_logging_mode == LoggingMode.PRODUCTION:
        # JSONL file for structured metrics (sparse - every 300 frames)
        file_handler = logging.FileHandler(log_path_map[LoggingMode.PRODUCTION], mode="a")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(JSONLFormatter())
        handlers.append(file_handler)

        # Console only for errors
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.ERROR)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        handlers.append(console_handler)

    elif cfg.pipeline_logging_mode == LoggingMode.DEBUG:
        # JSONL file for structured metrics (every frame)
        file_handler = logging.FileHandler(log_path_map[LoggingMode.DEBUG], mode="a")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(JSONLFormatter())
        handlers.append(file_handler)

        # Console with DEBUG level (human-readable)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        handlers.append(console_handler)

    elif cfg.pipeline_logging_mode == LoggingMode.TUNING:
        # JSONL file for structured metrics (every frame)
        file_handler = logging.FileHandler(log_path_map[LoggingMode.TUNING], mode="a")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(JSONLFormatter())
        handlers.append(file_handler)

        # Console only for errors
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.ERROR)
        console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        handlers.append(console_handler)

    # Create queue listener (background thread)
    listener = logging.handlers.QueueListener(log_queue, *handlers, respect_handler_level=True)
    listener.start()

    # Add non-blocking queue handler to logger
    queue_handler = NonBlockingQueueHandler(log_queue)
    logger.addHandler(queue_handler)

    return logger, listener


def build_frame_metrics(result: FrameResult, stats: PipelineStats, cfg: ProducerConfig) -> dict:
    """
    Build flat metrics dict from FrameResult and PipelineStats.

    Produces structured data for JSONL logging in all modes.
    Flat schema enables easy ML analysis with pandas/sklearn.

    Args:
        result: Single frame result
        stats: Cumulative stats (for averages/context)
        cfg: Producer configuration (for config snapshot)

    Returns:
        Flat dict with all metrics
    """
    return {
        # Frame identity
        "frame_idx": result.frame_idx,
        "timestamp_ms": result.timestamp_ms,
        "sampled": result.sampled,
        "error": result.error,
        # Detection counts
        "num_raw_detections": result.num_raw_detections,
        "num_filtered_detections": result.num_filtered_detections,
        "num_rejected_detections": result.num_rejected_detections,
        # Tracking counts
        "num_active_tracks": result.num_active_tracks,
        "num_confirmed_tracks": result.num_confirmed_tracks,
        # Cropping/Quality/Buffer counts
        "num_crops": result.num_crops,
        "num_quality_passed": result.num_quality_passed,
        "buffer_updates": result.buffer_updates,
        # Per-frame timing (milliseconds)
        "detection_ms": result.detection_ms,
        "tracking_ms": result.tracking_ms,
        "cropping_ms": result.cropping_ms,
        "corner_cnn_ms": result.corner_cnn_ms,
        "quality_ms": result.quality_ms,
        "buffer_ms": result.buffer_ms,
        "total_ms": result.total_ms,
        # Cumulative stats (for context - useful for ML features)
        "cum_frames_received": stats.total_frames_received,
        "cum_frames_sampled": stats.total_frames_sampled,
        "cum_raw_detections": stats.total_raw_detections,
        "cum_valid_detections": stats.total_valid_detections,
        "cum_tracks_created": stats.total_tracks_created,
        "cum_tracks_lost": stats.total_tracks_lost,
        "cum_quality_passed": stats.total_quality_passed,
        "cum_buffer_insertions": stats.total_buffer_insertions,
        "cum_frames_over_budget": stats.frames_over_budget,
        "cum_errors": stats.total_errors,
        # Average timing (for comparison)
        "avg_frame_ms": stats.avg_frame_ms,
        "avg_detection_ms": stats.avg_detection_ms,
        "avg_tracking_ms": stats.avg_tracking_ms,
        "avg_cropping_ms": stats.avg_cropping_ms,
        "avg_quality_ms": stats.avg_quality_ms,
        "avg_buffer_ms": stats.avg_buffer_ms,
        # Config snapshot (key thresholds for ML analysis)
        "cfg_frame_sample_interval_ms": cfg.frame_sample_interval_ms,
        "cfg_conf_threshold": cfg.conf_threshold,
        "cfg_iou_threshold": cfg.iou_threshold,
        "cfg_min_bbox_width": cfg.min_bbox_width,
        "cfg_min_bbox_height": cfg.min_bbox_height,
        "cfg_min_aspect_ratio": cfg.min_aspect_ratio,
        "cfg_max_aspect_ratio": cfg.max_aspect_ratio,
        "cfg_min_hits": cfg.min_hits,
        "cfg_max_frames_lost": cfg.max_frames_lost,
        "cfg_iou_threshold_confirmed": cfg.iou_threshold_confirmed,
        "cfg_iou_threshold_tentative": cfg.iou_threshold_tentative,
        "cfg_buffer_min_quality": cfg.buffer_min_quality,
        "cfg_buffer_similarity_threshold": cfg.buffer_similarity_threshold,
    }


class ProducerPipeline:
    """
    Orchestrates detection → tracking → cropping → quality → buffer.

    Maintains stateful components (detector, tracker, buffer) across frames.
    Provides unified interface for frame processing and Consumer access.
    """

    def __init__(self, cfg: ProducerConfig, logger: logging.Logger | None = None):
        """
        Initialize pipeline with all components.

        Args:
            cfg: Producer configuration
            logger: Optional logger instance (if None, creates async logger)
                    Use cases for custom logger:
                    - Unit testing (capture logs in test assertions)
                    - External logging systems (Datadog, ELK)
                    - Benchmarking (NullHandler for zero overhead)

        Raises:
            RuntimeError: If TensorRT engine not found or GPU unavailable
        """
        self.cfg = cfg

        # Setup async logging if no logger provided
        if logger is None:
            self.logger, self.log_listener = setup_async_logging(cfg)
            self._owns_logger = True
        else:
            self.logger = logger
            self.log_listener = None
            self._owns_logger = False

        # Initialize cumulative stats with configured percentile window size
        self.stats = PipelineStats()
        self.stats.set_window_size(cfg.pipeline_percentile_window_frames)

        # Initialize components (fail-fast if config invalid)
        self.logger.info("Initializing Producer pipeline...")

        self.sampler = FrameSampler(cfg)
        self.detector = YoloDetector(cfg, logger=self.logger)
        self.tracker = PlateTracker(cfg, logger=self.logger)
        self.buffer = IdBufferManager(cfg, logger=self.logger)

        # Corner CNN (optional Stage 3.5)
        if cfg.corner_cnn_enabled:
            from .ops_corner_cnn import CornerCnnPredictor

            self.corner_cnn: CornerCnnPredictor | None = CornerCnnPredictor(cfg, log=self.logger)
            self.logger.info("Corner CNN predictor initialized")
        else:
            self.corner_cnn = None

        self.logger.info(
            f"Producer pipeline initialized successfully. "
            f"Logging mode: {cfg.pipeline_logging_mode.value}, "
            f"TensorRT engine: {cfg.yolo_model_path}, "
            f"Target budget: {cfg.pipeline_frame_budget_ms:.1f}ms"
        )

    def process_frame(
        self,
        frame_img: np.ndarray,
        frame_idx: int,
        timestamp_ms: int | None = None,
    ) -> FrameResult:
        """
        Process single frame through full pipeline.

        Args:
            frame_img: BGR frame from video stream (H×W×3 uint8)
            frame_idx: Sequential frame number
            timestamp_ms: Optional frame timestamp

        Returns:
            FrameResult with counts, timing, and status

        Note:
            Frame is NOT mutated. All crops/buffers are internal.
        """
        start_time = time.monotonic()
        self.stats.total_frames_received += 1

        try:
            # ===== STAGE 1: DETECTION =====
            detector_output = detect_plate_rois(
                frame_img, frame_idx, self.detector, self.sampler, self.cfg, timestamp_ms
            )

            # Handle non-sampled frames
            if not detector_output.sampled:
                self.stats.total_frames_skipped += 1
                result = self._build_frame_result(
                    frame_idx=frame_idx,
                    timestamp_ms=timestamp_ms,
                    sampled=False,
                    detection_ms=detector_output.timings.total_ms,
                    total_ms=(time.monotonic() - start_time) * 1000,
                )
                self._maybe_log_frame_result(result)
                return result

            # Frame was sampled
            self.stats.total_frames_sampled += 1
            num_raw = len(detector_output.raw_detections)
            num_filtered = len(detector_output.filtered_detections)
            num_rejected = len(detector_output.filter_reasons)

            self.stats.total_raw_detections += num_raw
            self.stats.total_valid_detections += num_filtered
            self.stats.total_filtered_detections += num_rejected

            # ===== STAGE 2: TRACKING =====
            tracker_output = track_detections(
                detector_output.filtered_detections, frame_img, frame_idx, self.tracker
            )

            if tracker_output.lost_track_ids:
                self.buffer.evict_tracks(tracker_output.lost_track_ids)

            num_active = len(tracker_output.tracks)
            num_confirmed = len(tracker_output.confirmed_tracks)

            # Update tracking stats
            self.stats.total_tracks_created += tracker_output.num_tracks_created
            self.stats.total_tracks_confirmed += tracker_output.num_tracks_confirmed
            self.stats.total_tracks_lost += tracker_output.num_tracks_lost

            # Early exit: no filtered detections (tracking only)
            if num_filtered == 0:
                total_ms = (time.monotonic() - start_time) * 1000
                result = self._build_frame_result(
                    frame_idx=frame_idx,
                    timestamp_ms=timestamp_ms,
                    sampled=True,
                    num_raw_detections=num_raw,
                    num_filtered_detections=0,
                    num_rejected_detections=num_rejected,
                    num_active_tracks=num_active,
                    num_confirmed_tracks=num_confirmed,
                    detection_ms=detector_output.timings.total_ms,
                    tracking_ms=tracker_output.timings.total_ms,
                    total_ms=total_ms,
                )
                self._update_stats(
                    total_ms,
                    detector_output.timings.total_ms,
                    tracker_output.timings.total_ms,
                    0,
                    0,
                    0,
                )
                self._maybe_log_frame_result(result)
                self._check_over_budget(result)
                return result

            # Early exit: no confirmed tracks
            if num_confirmed == 0 and self.cfg.pipeline_skip_on_no_confirmed:
                total_ms = (time.monotonic() - start_time) * 1000
                result = self._build_frame_result(
                    frame_idx=frame_idx,
                    timestamp_ms=timestamp_ms,
                    sampled=True,
                    num_raw_detections=num_raw,
                    num_filtered_detections=num_filtered,
                    num_rejected_detections=num_rejected,
                    num_active_tracks=num_active,
                    num_confirmed_tracks=0,
                    detection_ms=detector_output.timings.total_ms,
                    tracking_ms=tracker_output.timings.total_ms,
                    total_ms=total_ms,
                )
                self._update_stats(
                    total_ms,
                    detector_output.timings.total_ms,
                    tracker_output.timings.total_ms,
                    0,
                    0,
                    0,
                )
                self._maybe_log_frame_result(result)
                self._check_over_budget(result)
                return result

            # ===== STAGE 3: CROPPING =====
            crop_start = time.monotonic()
            rois = crop_tracks(frame_img, tracker_output.confirmed_tracks, frame_idx, self.cfg)
            crop_ms = (time.monotonic() - crop_start) * 1000

            # Update crop stats
            self.stats.total_crop_attempts += num_confirmed
            self.stats.total_crop_successes += len(rois)
            self.stats.total_crop_failures += num_confirmed - len(rois)

            # Early exit: no successful crops
            if len(rois) == 0:
                total_ms = (time.monotonic() - start_time) * 1000
                result = self._build_frame_result(
                    frame_idx=frame_idx,
                    timestamp_ms=timestamp_ms,
                    sampled=True,
                    num_raw_detections=num_raw,
                    num_filtered_detections=num_filtered,
                    num_rejected_detections=num_rejected,
                    num_active_tracks=num_active,
                    num_confirmed_tracks=num_confirmed,
                    num_crops=0,
                    detection_ms=detector_output.timings.total_ms,
                    tracking_ms=tracker_output.timings.total_ms,
                    cropping_ms=crop_ms,
                    total_ms=total_ms,
                )
                self._update_stats(
                    total_ms,
                    detector_output.timings.total_ms,
                    tracker_output.timings.total_ms,
                    crop_ms,
                    0,
                    0,
                )
                self._maybe_log_frame_result(result)
                self._check_over_budget(result)
                return result

            # ===== STAGE 3.5: CORNER CNN (optional) =====
            corner_cnn_ms = 0.0
            if self.corner_cnn is not None:
                from .ops_corner_cnn import predict_corners

                corner_cnn_start = time.monotonic()
                rois = predict_corners(rois, self.corner_cnn, self.cfg)
                corner_cnn_ms = (time.monotonic() - corner_cnn_start) * 1000
                self.stats.total_corner_cnn_analyzed += len(rois)

            # ===== STAGE 4: QUALITY ANALYSIS =====
            quality_start = time.monotonic()
            roi_fast_qualities = fast_quality_analyze_rois(rois, self.cfg)
            quality_ms = (time.monotonic() - quality_start) * 1000

            # Update quality stats (count passes_min_quality=True)
            total_quality = len(roi_fast_qualities)
            self.stats.total_quality_analyzed += total_quality
            num_quality_passed = sum(1 for rfq in roi_fast_qualities if rfq.passes_min_quality)
            self.stats.total_quality_passed += num_quality_passed
            self.stats.total_quality_failed += total_quality - num_quality_passed

            # Hard gate: skip buffer work for ROIs that failed fast QA
            roi_fast_qualities = [rfq for rfq in roi_fast_qualities if rfq.passes_min_quality]

            # ===== STAGE 5: BUFFER INSERTION =====
            buffer_start = time.monotonic()
            buffer_updates = 0
            for roi_fq in roi_fast_qualities:
                inserted = self.buffer.insert_roi(roi_fq)
                if inserted:
                    buffer_updates += 1
                    self.stats.total_buffer_insertions += 1
                else:
                    self.stats.total_buffer_drops += 1
            buffer_ms = (time.monotonic() - buffer_start) * 1000

            # ===== FINALIZATION =====
            total_ms = (time.monotonic() - start_time) * 1000

            result = self._build_frame_result(
                frame_idx=frame_idx,
                timestamp_ms=timestamp_ms,
                sampled=True,
                num_raw_detections=num_raw,
                num_filtered_detections=num_filtered,
                num_rejected_detections=num_rejected,
                num_active_tracks=num_active,
                num_confirmed_tracks=num_confirmed,
                num_crops=len(rois),
                num_quality_passed=num_quality_passed,
                buffer_updates=buffer_updates,
                detection_ms=detector_output.timings.total_ms,
                tracking_ms=tracker_output.timings.total_ms,
                cropping_ms=crop_ms,
                quality_ms=quality_ms,
                buffer_ms=buffer_ms,
                total_ms=total_ms,
                corner_cnn_ms=corner_cnn_ms,
            )

            self._update_stats(
                total_ms,
                detector_output.timings.total_ms,
                tracker_output.timings.total_ms,
                crop_ms,
                quality_ms,
                buffer_ms,
                corner_cnn_ms,
            )

            # Logging (mode-aware)
            self._maybe_log_frame_result(result)
            self._maybe_log_pipeline_stats(frame_idx)
            self._check_over_budget(result)

            return result

        except Exception as exc:
            # Catch all exceptions, log with full context, return error result
            import traceback

            error_msg = f"{type(exc).__name__}: {str(exc)}\n{traceback.format_exc()}"

            self.logger.error(f"[Frame {frame_idx}] Pipeline exception", exc_info=True)

            self.stats.total_errors += 1
            self.stats.last_error = error_msg
            self.stats.last_error_frame = frame_idx

            total_ms = (time.monotonic() - start_time) * 1000

            result = self._build_frame_result(
                frame_idx=frame_idx,
                timestamp_ms=timestamp_ms,
                sampled=False,
                total_ms=total_ms,
                error=error_msg,
            )

            self._maybe_log_frame_result(result)
            return result

    def _build_frame_result(
        self,
        frame_idx: int,
        timestamp_ms: int | None,
        sampled: bool,
        num_raw_detections: int = 0,
        num_filtered_detections: int = 0,
        num_rejected_detections: int = 0,
        num_active_tracks: int = 0,
        num_confirmed_tracks: int = 0,
        num_crops: int = 0,
        num_quality_passed: int = 0,
        buffer_updates: int = 0,
        detection_ms: float = 0.0,
        tracking_ms: float = 0.0,
        cropping_ms: float = 0.0,
        quality_ms: float = 0.0,
        buffer_ms: float = 0.0,
        total_ms: float = 0.0,
        corner_cnn_ms: float = 0.0,
        error: str | None = None,
    ) -> FrameResult:
        """
        Single source of truth for FrameResult construction.

        Avoids duplication across early exit paths and normal completion.
        """
        return FrameResult(
            frame_idx=frame_idx,
            timestamp_ms=timestamp_ms,
            num_raw_detections=num_raw_detections,
            num_filtered_detections=num_filtered_detections,
            num_rejected_detections=num_rejected_detections,
            num_active_tracks=num_active_tracks,
            num_confirmed_tracks=num_confirmed_tracks,
            num_crops=num_crops,
            num_quality_passed=num_quality_passed,
            buffer_updates=buffer_updates,
            detection_ms=detection_ms,
            tracking_ms=tracking_ms,
            cropping_ms=cropping_ms,
            quality_ms=quality_ms,
            buffer_ms=buffer_ms,
            total_ms=total_ms,
            sampled=sampled,
            corner_cnn_ms=corner_cnn_ms,
            error=error,
        )

    def _update_stats(
        self,
        total_ms: float,
        detection_ms: float,
        tracking_ms: float,
        cropping_ms: float,
        quality_ms: float,
        buffer_ms: float,
        corner_cnn_ms: float = 0.0,
    ) -> None:
        """
        Update cumulative statistics with latest frame timing.

        Updates both running averages and sliding window percentiles.
        Called for ALL sampled frames, including early exits.
        """
        n = self.stats.total_frames_sampled

        # ===== Running averages (all-time) =====
        self.stats.avg_frame_ms = (self.stats.avg_frame_ms * (n - 1) + total_ms) / n
        self.stats.avg_detection_ms = (self.stats.avg_detection_ms * (n - 1) + detection_ms) / n
        self.stats.avg_tracking_ms = (self.stats.avg_tracking_ms * (n - 1) + tracking_ms) / n
        self.stats.avg_cropping_ms = (self.stats.avg_cropping_ms * (n - 1) + cropping_ms) / n
        self.stats.avg_quality_ms = (self.stats.avg_quality_ms * (n - 1) + quality_ms) / n
        self.stats.avg_buffer_ms = (self.stats.avg_buffer_ms * (n - 1) + buffer_ms) / n
        self.stats.avg_corner_cnn_ms = (self.stats.avg_corner_cnn_ms * (n - 1) + corner_cnn_ms) / n

        # ===== Sliding window updates (recent percentiles) =====
        self.stats._windows["frame_ms"].append(total_ms)
        self.stats._windows["detection_ms"].append(detection_ms)
        self.stats._windows["tracking_ms"].append(tracking_ms)
        self.stats._windows["cropping_ms"].append(cropping_ms)
        self.stats._windows["quality_ms"].append(quality_ms)
        self.stats._windows["buffer_ms"].append(buffer_ms)
        self.stats._windows["corner_cnn_ms"].append(corner_cnn_ms)

        # Min/max tracking
        if n == 1:
            self.stats.min_frame_ms = total_ms
        else:
            self.stats.min_frame_ms = min(self.stats.min_frame_ms, total_ms)

        self.stats.max_frame_ms = max(self.stats.max_frame_ms, total_ms)

    def _check_over_budget(self, result: FrameResult) -> None:
        """
        Check and track over-budget frames.

        PRODUCTION: Silent (only stats tracking)
        DEBUG: Log warning for every over-budget frame
        TUNING: Silent (data in JSONL)
        """
        if result.total_ms > self.cfg.pipeline_frame_budget_ms:
            self.stats.frames_over_budget += 1

            # Only log warnings in DEBUG mode
            if self.cfg.pipeline_logging_mode == LoggingMode.DEBUG:
                self.logger.warning(
                    f"[Frame {result.frame_idx}] Over budget: "
                    f"{result.total_ms:.1f}ms > {self.cfg.pipeline_frame_budget_ms:.1f}ms "
                    f"(det={result.detection_ms:.1f}, trk={result.tracking_ms:.1f}, "
                    f"crop={result.cropping_ms:.1f}, cnn={result.corner_cnn_ms:.1f}, "
                    f"qual={result.quality_ms:.1f}, buf={result.buffer_ms:.1f})"
                )

    def _maybe_log_frame_result(self, result: FrameResult) -> None:
        """
        Log frame result based on current logging mode.

        All modes write structured JSONL to file for ML analysis.
        Console shows errors (all modes) or debug info (DEBUG mode only).

        Args:
            result: Already-constructed FrameResult (no duplication)
        """
        mode = self.cfg.pipeline_logging_mode

        # Determine logging interval based on mode
        frame_interval_map = {
            LoggingMode.PRODUCTION: self.cfg.production_frame_interval,
            LoggingMode.DEBUG: self.cfg.debug_frame_interval,
            LoggingMode.TUNING: self.cfg.tuning_frame_interval,
        }
        frame_interval = frame_interval_map[mode]

        # Check if we should log this frame (based on interval)
        should_log_frame = result.frame_idx % frame_interval == 0

        # Always log errors to both JSONL and console (regardless of interval)
        if result.error:
            metrics = build_frame_metrics(result, self.stats, self.cfg)
            record = self.logger.makeRecord(
                name=self.logger.name,
                level=logging.ERROR,
                fn="",
                lno=0,
                msg=f"Frame {result.frame_idx} failed: {result.error[:200]}",
                args=(),
                exc_info=None,
            )
            record.metrics = metrics  # Attach for JSONL
            self.logger.handle(record)
            return

        # Log regular frames at configured interval
        if should_log_frame:
            metrics = build_frame_metrics(result, self.stats, self.cfg)

            # Choose log level and message based on mode
            if mode == LoggingMode.DEBUG:
                # DEBUG mode: detailed console output + JSONL
                msg = (
                    f"[Frame {result.frame_idx}] "
                    f"dets={result.num_raw_detections}/{result.num_filtered_detections} "
                    f"(rejected={result.num_rejected_detections}), "
                    f"tracks={result.num_active_tracks}/{result.num_confirmed_tracks}, "
                    f"crops={result.num_crops}, qual_pass={result.num_quality_passed}, "
                    f"buf_updates={result.buffer_updates}, "
                    f"time={result.total_ms:.1f}ms"
                )
                level = logging.DEBUG
            else:
                # PRODUCTION/TUNING: minimal console, full JSONL
                msg = f"Frame {result.frame_idx} processed"
                level = logging.INFO

            record = self.logger.makeRecord(
                name=self.logger.name, level=level, fn="", lno=0, msg=msg, args=(), exc_info=None
            )
            record.metrics = metrics  # Attach for JSONL
            self.logger.handle(record)

    def _maybe_log_pipeline_stats(self, frame_idx: int) -> None:
        """Log periodic cumulative pipeline statistics."""
        mode = self.cfg.pipeline_logging_mode

        # Determine interval based on mode
        if mode == LoggingMode.PRODUCTION:
            interval = self.cfg.production_stats_interval_frames
        elif mode == LoggingMode.DEBUG:
            interval = self.cfg.debug_stats_interval_frames
        else:
            # TUNING mode uses JSONL, not human-readable stats
            return

        if interval <= 0 or frame_idx % interval != 0:
            return

        s = self.stats
        self.logger.info(
            f"\n========== Pipeline Stats (Frame {frame_idx}) ==========\n"
            f"Frames: {s.total_frames_received} received, "
            f"{s.total_frames_sampled} sampled, {s.total_frames_skipped} skipped\n"
            f"Detections: {s.total_raw_detections} raw, "
            f"{s.total_valid_detections} valid, {s.total_filtered_detections} filtered\n"
            f"Tracks: {s.total_tracks_created} created, "
            f"{s.total_tracks_confirmed} confirmed, {s.total_tracks_lost} lost\n"
            f"Crops: {s.total_crop_attempts} attempts, "
            f"{s.total_crop_successes} success, {s.total_crop_failures} failed\n"
            f"Quality: {s.total_quality_analyzed} analyzed, "
            f"{s.total_quality_passed} passed, {s.total_quality_failed} failed\n"
            f"Corner CNN: {s.total_corner_cnn_analyzed} crops analyzed\n"
            f"Buffer: {s.total_buffer_insertions} inserted, {s.total_buffer_drops} dropped\n"
            # All-time averages
            f"Timing (all-time avg): {s.avg_frame_ms:.1f}ms total "
            f"(det={s.avg_detection_ms:.1f}, trk={s.avg_tracking_ms:.1f}, "
            f"crop={s.avg_cropping_ms:.1f}, cnn={s.avg_corner_cnn_ms:.1f}, "
            f"qual={s.avg_quality_ms:.1f}, buf={s.avg_buffer_ms:.1f})\n"
            # Recent percentiles (sliding window)
            f"Timing (p50/p95/p99): {s.p50_frame_ms:.1f}/{s.p95_frame_ms:.1f}/{s.p99_frame_ms:.1f}ms total\n"
            f"  Detection:  {s.p50_detection_ms:.1f}/{s.p95_detection_ms:.1f}/{s.p99_detection_ms:.1f}ms\n"
            f"  Tracking:   {s.p50_tracking_ms:.1f}/{s.p95_tracking_ms:.1f}/{s.p99_tracking_ms:.1f}ms\n"
            f"  Cropping:   {s.p50_cropping_ms:.1f}/{s.p95_cropping_ms:.1f}/{s.p99_cropping_ms:.1f}ms\n"
            f"  Corner CNN: {s.p50_corner_cnn_ms:.1f}/{s.p95_corner_cnn_ms:.1f}/{s.p99_corner_cnn_ms:.1f}ms\n"
            f"  Quality:    {s.p50_quality_ms:.1f}/{s.p95_quality_ms:.1f}/{s.p99_quality_ms:.1f}ms\n"
            f"  Buffer:     {s.p50_buffer_ms:.1f}/{s.p95_buffer_ms:.1f}/{s.p99_buffer_ms:.1f}ms\n"
            # Min/max
            f"Timing (range): {s.min_frame_ms:.1f}ms min, {s.max_frame_ms:.1f}ms max\n"
            f"Performance: {s.frames_over_budget} frames over budget "
            f"({100.0 * s.frames_over_budget / max(s.total_frames_sampled, 1):.1f}%)\n"
            f"Errors: {s.total_errors} exceptions"
            f"{f', last: {s.last_error[:100]}' if s.last_error else ''}\n"
            f"======================================================"
        )

    def get_updated_bins(self, last_seen_versions: dict[TrackId, int]) -> list[BinSnapshot]:
        """
        Get buffer snapshots for bins with version updates (Consumer API).

        Args:
            last_seen_versions: Map of track_id → last known version

        Returns:
            List of BinSnapshot with deep-copied entries
        """
        return self.buffer.get_all_updated_bins(last_seen_versions)

    def get_buffer_stats(self) -> dict[TrackId, BufferStats]:
        """Get buffer insertion statistics for all track IDs."""
        return self.buffer.get_all_stats()

    def get_pipeline_stats(self) -> PipelineStats:
        """Get cumulative pipeline statistics."""
        return self.stats

    def shutdown(self) -> None:
        """Clean up resources (GPU memory, logging)."""
        self.logger.info("Shutting down Producer pipeline...")

        # Stop queue listener if we own the logger
        if self._owns_logger and self.log_listener:
            self.log_listener.stop()

        # Future: Release GPU memory explicitly if needed
        self.logger.info("Producer pipeline shutdown complete.")
