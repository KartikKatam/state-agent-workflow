"""Tests for producer pipeline integration: FrameSampler, detect_plate_rois, ProducerPipeline.

Chunk-07: ~30 tests covering detection, pipeline orchestration, corner CNN path,
error recovery, and lifecycle behavior. YoloDetector is always mocked (TensorRT).
"""

from __future__ import annotations

import logging
import logging.handlers
import queue
from unittest.mock import MagicMock, patch

import numpy as np

from producer.config import ProducerConfig
from producer.models import (
    Detection,
    DetectionTimings,
    DetectorOutput,
    FrameResult,
    LoggingMode,
    Track,
    TrackerOutput,
    TrackingTimings,
)
from producer.ops_detection import FrameSampler, detect_plate_rois
from producer.pipeline import NonBlockingQueueHandler, ProducerPipeline, setup_async_logging

from .conftest_producer import make_config, make_detection, make_roi_fast_quality, make_roi_image

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_frame(h: int = 1080, w: int = 1920) -> np.ndarray:
    """Create a dummy BGR frame."""
    return np.zeros((h, w, 3), dtype=np.uint8)


def _make_detector_output(
    *,
    sampled: bool = True,
    raw: list[Detection] | None = None,
    filtered: list[Detection] | None = None,
    reasons: dict[int, str] | None = None,
) -> DetectorOutput:
    """Factory for DetectorOutput."""
    return DetectorOutput(
        raw_detections=raw or [],
        filtered_detections=filtered or [],
        filter_reasons=reasons or {},
        timings=DetectionTimings(total_ms=1.0),
        sampled=sampled,
    )


def _make_tracker_output(
    *,
    tracks: list[Track] | None = None,
    confirmed: list[Track] | None = None,
    lost_ids: list[str] | None = None,
) -> TrackerOutput:
    """Factory for TrackerOutput."""
    return TrackerOutput(
        tracks=tracks or [],
        confirmed_tracks=confirmed or [],
        camera_motion=(0.0, 0.0),
        timings=TrackingTimings(total_ms=1.0),
        lost_track_ids=lost_ids or [],
    )


def _build_pipeline(cfg: ProducerConfig | None = None) -> ProducerPipeline:
    """Build a ProducerPipeline with all heavy components mocked out.

    Patches YoloDetector, PlateTracker, IdBufferManager, and FrameSampler
    so no GPU or real resources are needed.
    """
    cfg = cfg or make_config()
    logger = logging.getLogger("test_pipeline")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)

    with (
        patch("producer.pipeline.YoloDetector"),
        patch("producer.pipeline.PlateTracker") as mock_tracker_cls,
        patch("producer.pipeline.IdBufferManager") as mock_buffer_cls,
    ):
        pipeline = ProducerPipeline(cfg, logger=logger)

    # Replace the real detector/sampler with mocks for process_frame calls
    pipeline.detector = MagicMock()
    pipeline.sampler = MagicMock()
    pipeline.tracker = mock_tracker_cls.return_value
    pipeline.buffer = mock_buffer_cls.return_value
    pipeline.corner_cnn = None

    return pipeline


# ===========================================================================
# FrameSampler Tests (7)
# ===========================================================================


class TestFrameSampler:
    """Tests for FrameSampler timing logic."""

    def test_frame_sampler_init(self):
        """__init__: interval_ms stored, _last_sample_time=0.0."""
        cfg = make_config(frame_sample_interval_ms=100.0)
        sampler = FrameSampler(cfg)

        assert sampler.interval_ms == 100.0
        assert sampler._last_sample_time == 0.0

    def test_frame_sampler_first_call(self):
        """First call returns True when timestamp >= interval (0.0 init).

        With _last_sample_time=0.0, any timestamp >= interval_ms will pass.
        In production, monotonic clock provides large timestamps making first call always True.
        """
        cfg = make_config(frame_sample_interval_ms=1000.0)
        sampler = FrameSampler(cfg)

        # 1000 - 0.0 = 1000.0, NOT < 1000.0 -> True
        assert sampler.should_sample(timestamp_ms=1000) is True

    def test_frame_sampler_rapid_calls(self):
        """Rapid calls within interval return False."""
        cfg = make_config(frame_sample_interval_ms=100.0)
        sampler = FrameSampler(cfg)

        assert sampler.should_sample(timestamp_ms=1000) is True
        assert sampler.should_sample(timestamp_ms=1050) is False
        assert sampler.should_sample(timestamp_ms=1099) is False

    def test_frame_sampler_exact_boundary(self):
        """Exact boundary: now - last == interval -> True (uses < comparison)."""
        cfg = make_config(frame_sample_interval_ms=100.0)
        sampler = FrameSampler(cfg)

        # First call at 1000ms
        assert sampler.should_sample(timestamp_ms=1000) is True
        # Exactly at boundary: 1100 - 1000 = 100 = interval -> NOT < interval -> True
        assert sampler.should_sample(timestamp_ms=1100) is True

    def test_frame_sampler_below_boundary(self):
        """Just below boundary: interval - epsilon -> False."""
        cfg = make_config(frame_sample_interval_ms=100.0)
        sampler = FrameSampler(cfg)

        assert sampler.should_sample(timestamp_ms=1000) is True
        # 1099 - 1000 = 99 < 100 -> False
        assert sampler.should_sample(timestamp_ms=1099) is False

    def test_frame_sampler_none_timestamp(self):
        """timestamp_ms=None uses monotonic clock."""
        cfg = make_config(frame_sample_interval_ms=0.0)  # Always sample
        sampler = FrameSampler(cfg)

        # With interval=0.0, any monotonic value will pass
        result = sampler.should_sample(timestamp_ms=None)
        assert result is True

    def test_frame_sampler_state_on_true(self):
        """State (_last_sample_time) only updated on True return."""
        cfg = make_config(frame_sample_interval_ms=100.0)
        sampler = FrameSampler(cfg)

        # First call: True, state updated to 1000.0
        assert sampler.should_sample(timestamp_ms=1000) is True
        assert sampler._last_sample_time == 1000.0

        # Second call: False, state NOT updated (still 1000.0)
        assert sampler.should_sample(timestamp_ms=1050) is False
        assert sampler._last_sample_time == 1000.0

        # Third call: True, state updated to 1100.0
        assert sampler.should_sample(timestamp_ms=1100) is True
        assert sampler._last_sample_time == 1100.0


# ===========================================================================
# NonBlockingQueueHandler Tests (2)
# ===========================================================================


class TestNonBlockingQueueHandler:
    """Tests for NonBlockingQueueHandler."""

    def test_queue_handler_normal(self):
        """Normal enqueue when queue has space."""
        q: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=10)
        handler = NonBlockingQueueHandler(q)

        record = logging.LogRecord("test", logging.INFO, "", 0, "hello", (), None)
        handler.enqueue(record)

        assert q.qsize() == 1
        assert q.get_nowait().getMessage() == "hello"

    def test_queue_handler_full(self):
        """Queue full -> drops oldest, enqueues new."""
        q: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=2)
        handler = NonBlockingQueueHandler(q)

        r1 = logging.LogRecord("test", logging.INFO, "", 0, "first", (), None)
        r2 = logging.LogRecord("test", logging.INFO, "", 0, "second", (), None)
        r3 = logging.LogRecord("test", logging.INFO, "", 0, "third", (), None)

        handler.enqueue(r1)
        handler.enqueue(r2)
        assert q.qsize() == 2

        # This should drop "first" and add "third"
        handler.enqueue(r3)
        assert q.qsize() == 2

        messages = [q.get_nowait().getMessage(), q.get_nowait().getMessage()]
        assert "second" in messages
        assert "third" in messages


# ===========================================================================
# detect_plate_rois Tests (4)
# ===========================================================================


class TestDetectPlateRois:
    """Tests for detect_plate_rois with mocked detector."""

    def test_detect_not_sampled(self):
        """Not sampled -> DetectorOutput(sampled=False, empty lists)."""
        cfg = make_config(frame_sample_interval_ms=1000.0)
        sampler = FrameSampler(cfg)
        detector = MagicMock()
        frame = _make_frame()

        # First call samples, second call within interval does not
        sampler.should_sample(timestamp_ms=1000)
        result = detect_plate_rois(frame, 1, detector, sampler, cfg, timestamp_ms=1050)

        assert result.sampled is False
        assert result.raw_detections == []
        assert result.filtered_detections == []
        assert result.filter_reasons == {}
        detector.run.assert_not_called()

    def test_detect_sampled_no_detections(self):
        """Sampled, no detections -> sampled=True, raw=[], filtered=[]."""
        cfg = make_config()
        sampler = FrameSampler(cfg)
        detector = MagicMock()
        detector.run.return_value = ([], DetectionTimings(total_ms=5.0))
        frame = _make_frame()

        # Use large timestamp so sampler passes (timestamp - 0.0 >= interval)
        result = detect_plate_rois(frame, 0, detector, sampler, cfg, timestamp_ms=100_000)

        assert result.sampled is True
        assert result.raw_detections == []
        assert result.filtered_detections == []

    def test_detect_sampled_with_detections(self):
        """Sampled with detections -> filtered + reasons populated."""
        cfg = make_config(min_bbox_width=50.0, min_bbox_height=20.0)
        sampler = FrameSampler(cfg)
        detector = MagicMock()

        good_det = make_detection(bbox=[100.0, 200.0, 200.0, 260.0])  # w=100, h=60
        small_det = make_detection(bbox=[100.0, 200.0, 110.0, 210.0])  # w=10, h=10

        detector.run.return_value = (
            [good_det, small_det],
            DetectionTimings(total_ms=5.0),
        )
        frame = _make_frame()

        result = detect_plate_rois(frame, 0, detector, sampler, cfg, timestamp_ms=100_000)

        assert result.sampled is True
        assert len(result.raw_detections) == 2
        assert len(result.filtered_detections) == 1
        assert result.filtered_detections[0] is good_det
        assert len(result.filter_reasons) == 1

    def test_detect_accounting(self):
        """len(filtered) + len(reasons) == len(raw)."""
        cfg = make_config(min_bbox_width=50.0, min_bbox_height=20.0)
        sampler = FrameSampler(cfg)
        detector = MagicMock()

        dets = [
            make_detection(bbox=[100.0, 200.0, 200.0, 260.0]),  # good: w=100, h=60
            make_detection(bbox=[100.0, 200.0, 110.0, 210.0]),  # bad: w=10
            make_detection(bbox=[100.0, 200.0, 300.0, 400.0]),  # good: w=200, h=200
        ]
        detector.run.return_value = (dets, DetectionTimings(total_ms=5.0))
        frame = _make_frame()

        result = detect_plate_rois(frame, 0, detector, sampler, cfg, timestamp_ms=100_000)

        assert len(result.filtered_detections) + len(result.filter_reasons) == len(
            result.raw_detections
        )


# ===========================================================================
# ProducerPipeline Core Path Tests (7)
# ===========================================================================


class TestProducerPipeline:
    """Tests for ProducerPipeline with mocked detector."""

    def test_pipeline_never_raises(self):
        """process_frame catch-all: never raises even if detector throws."""
        pipeline = _build_pipeline()

        # Make detect_plate_rois raise
        with patch("producer.pipeline.detect_plate_rois", side_effect=RuntimeError("boom")):
            result = pipeline.process_frame(_make_frame(), frame_idx=0)

        assert isinstance(result, FrameResult)
        assert result.error is not None
        assert "boom" in result.error

    def test_pipeline_not_sampled(self):
        """Not sampled -> FrameResult(sampled=False), total_frames_skipped++."""
        pipeline = _build_pipeline()

        not_sampled = _make_detector_output(sampled=False)
        with patch("producer.pipeline.detect_plate_rois", return_value=not_sampled):
            result = pipeline.process_frame(_make_frame(), frame_idx=0)

        assert result.sampled is False
        assert pipeline.stats.total_frames_skipped == 1
        assert pipeline.stats.total_frames_received == 1

    def test_pipeline_frames_received(self):
        """total_frames_received increments by 1 per call."""
        pipeline = _build_pipeline()

        not_sampled = _make_detector_output(sampled=False)
        with patch("producer.pipeline.detect_plate_rois", return_value=not_sampled):
            pipeline.process_frame(_make_frame(), frame_idx=0)
            pipeline.process_frame(_make_frame(), frame_idx=1)
            pipeline.process_frame(_make_frame(), frame_idx=2)

        assert pipeline.stats.total_frames_received == 3

    def test_pipeline_no_filtered_detections(self):
        """No filtered detections -> tracking still runs (for lifecycle maintenance)."""
        pipeline = _build_pipeline()

        det_output = _make_detector_output(
            sampled=True,
            raw=[make_detection()],
            reasons={0: "too_small"},
        )
        tracker_output = _make_tracker_output()

        with (
            patch("producer.pipeline.detect_plate_rois", return_value=det_output),
            patch("producer.pipeline.track_detections", return_value=tracker_output),
        ):
            result = pipeline.process_frame(_make_frame(), frame_idx=0)

        assert result.sampled is True
        assert result.num_filtered_detections == 0
        assert result.num_raw_detections == 1

    def test_pipeline_no_confirmed_tracks(self):
        """No confirmed tracks with skip_on_no_confirmed=True -> early exit."""
        cfg = make_config(pipeline_skip_on_no_confirmed=True)
        pipeline = _build_pipeline(cfg)

        det_output = _make_detector_output(
            sampled=True,
            filtered=[make_detection()],
        )
        tracker_output = _make_tracker_output(
            tracks=[MagicMock()],
            confirmed=[],  # No confirmed tracks
        )

        with (
            patch("producer.pipeline.detect_plate_rois", return_value=det_output),
            patch("producer.pipeline.track_detections", return_value=tracker_output),
        ):
            result = pipeline.process_frame(_make_frame(), frame_idx=0)

        assert result.sampled is True
        assert result.num_confirmed_tracks == 0
        assert result.num_crops == 0

    def test_pipeline_no_successful_crops(self):
        """No successful crops -> early exit before quality."""
        pipeline = _build_pipeline()

        det_output = _make_detector_output(
            sampled=True,
            filtered=[make_detection()],
        )
        tracker_output = _make_tracker_output(
            tracks=[MagicMock()],
            confirmed=[MagicMock()],
        )

        with (
            patch("producer.pipeline.detect_plate_rois", return_value=det_output),
            patch("producer.pipeline.track_detections", return_value=tracker_output),
            patch("producer.pipeline.crop_tracks", return_value=[]),  # No crops
        ):
            result = pipeline.process_frame(_make_frame(), frame_idx=0)

        assert result.sampled is True
        assert result.num_crops == 0
        assert result.num_quality_passed == 0

    def test_pipeline_full_path(self):
        """Full path: detections -> tracking -> cropping -> quality -> buffer."""
        pipeline = _build_pipeline()

        det_output = _make_detector_output(
            sampled=True,
            raw=[make_detection(), make_detection()],
            filtered=[make_detection()],
        )
        tracker_output = _make_tracker_output(
            tracks=[MagicMock()],
            confirmed=[MagicMock()],
        )
        roi = make_roi_image()
        rfq = make_roi_fast_quality(passes_min_quality=True)
        pipeline.buffer.insert_roi.return_value = True

        with (
            patch("producer.pipeline.detect_plate_rois", return_value=det_output),
            patch("producer.pipeline.track_detections", return_value=tracker_output),
            patch("producer.pipeline.crop_tracks", return_value=[roi]),
            patch("producer.pipeline.fast_quality_analyze_rois", return_value=[rfq]),
        ):
            result = pipeline.process_frame(_make_frame(), frame_idx=0)

        assert result.sampled is True
        assert result.num_raw_detections == 2
        assert result.num_filtered_detections == 1
        assert result.num_crops == 1
        assert result.num_quality_passed == 1
        assert result.buffer_updates == 1
        pipeline.buffer.insert_roi.assert_called_once()


# ===========================================================================
# Corner CNN Path Tests (2)
# ===========================================================================


class TestCornerCnnPath:
    """Tests for corner CNN integration in pipeline."""

    def test_pipeline_corner_cnn_disabled(self):
        """corner_cnn=None -> corner_cnn_ms=0.0, rois unchanged."""
        pipeline = _build_pipeline()
        assert pipeline.corner_cnn is None

        det_output = _make_detector_output(
            sampled=True,
            filtered=[make_detection()],
        )
        tracker_output = _make_tracker_output(
            tracks=[MagicMock()],
            confirmed=[MagicMock()],
        )
        roi = make_roi_image()
        rfq = make_roi_fast_quality(passes_min_quality=True)
        pipeline.buffer.insert_roi.return_value = True

        with (
            patch("producer.pipeline.detect_plate_rois", return_value=det_output),
            patch("producer.pipeline.track_detections", return_value=tracker_output),
            patch("producer.pipeline.crop_tracks", return_value=[roi]),
            patch("producer.pipeline.fast_quality_analyze_rois", return_value=[rfq]),
        ):
            result = pipeline.process_frame(_make_frame(), frame_idx=0)

        assert result.corner_cnn_ms == 0.0

    def test_pipeline_corner_cnn_enabled(self):
        """Mock CornerCnnPredictor -> Stage 3.5 exercised, stats updated."""
        pipeline = _build_pipeline()

        # Attach a mock corner_cnn predictor
        mock_corner_cnn = MagicMock()
        pipeline.corner_cnn = mock_corner_cnn

        det_output = _make_detector_output(
            sampled=True,
            filtered=[make_detection()],
        )
        tracker_output = _make_tracker_output(
            tracks=[MagicMock()],
            confirmed=[MagicMock()],
        )
        roi = make_roi_image()
        roi2 = make_roi_image()
        rfq = make_roi_fast_quality(passes_min_quality=True)
        pipeline.buffer.insert_roi.return_value = True

        # predict_corners is imported locally inside process_frame:
        #   from .ops_corner_cnn import predict_corners
        # So we patch at the module level where it's imported from.
        with (
            patch("producer.pipeline.detect_plate_rois", return_value=det_output),
            patch("producer.pipeline.track_detections", return_value=tracker_output),
            patch("producer.pipeline.crop_tracks", return_value=[roi]),
            patch("producer.ops_corner_cnn.predict_corners", return_value=[roi2]) as mock_predict,
            patch("producer.pipeline.fast_quality_analyze_rois", return_value=[rfq]),
        ):
            result = pipeline.process_frame(_make_frame(), frame_idx=0)

        # Stage 3.5 was exercised
        mock_predict.assert_called_once()
        # Stats updated
        assert pipeline.stats.total_corner_cnn_analyzed == 1
        assert result.corner_cnn_ms >= 0.0


# ===========================================================================
# Error Recovery + Lifecycle Tests (8)
# ===========================================================================


class TestErrorAndLifecycle:
    """Tests for error recovery, frame immutability, shutdown, and stats access."""

    def test_pipeline_error_recovery(self):
        """Inject exception -> error result, next frame OK."""
        pipeline = _build_pipeline()

        not_sampled = _make_detector_output(sampled=False)

        # First frame: exception
        with patch("producer.pipeline.detect_plate_rois", side_effect=ValueError("test error")):
            r1 = pipeline.process_frame(_make_frame(), frame_idx=0)

        assert r1.error is not None
        assert "test error" in r1.error

        # Second frame: normal
        with patch("producer.pipeline.detect_plate_rois", return_value=not_sampled):
            r2 = pipeline.process_frame(_make_frame(), frame_idx=1)

        assert r2.error is None
        assert r2.sampled is False

    def test_pipeline_error_sampled_flag(self):
        """Error sets sampled=False even if frame was actually sampled (anti-bias #3).

        pipeline.py:587 always sets sampled=False in the error handler.
        But total_frames_sampled may already have been incremented at line 371
        if the error occurred after the sampling check.
        """
        pipeline = _build_pipeline()

        # Create a detector output that IS sampled, but cause an error later
        sampled_output = _make_detector_output(sampled=True, filtered=[make_detection()])
        # track_detections will raise
        with (
            patch("producer.pipeline.detect_plate_rois", return_value=sampled_output),
            patch("producer.pipeline.track_detections", side_effect=RuntimeError("tracking crash")),
        ):
            result = pipeline.process_frame(_make_frame(), frame_idx=0)

        # Error result always has sampled=False (line 587)
        assert result.sampled is False
        assert result.error is not None
        # But total_frames_sampled was incremented before the crash (line 371)
        assert pipeline.stats.total_frames_sampled == 1

    def test_pipeline_frame_immutability(self):
        """Frame array unchanged after process_frame."""
        pipeline = _build_pipeline()

        frame = _make_frame()
        frame_copy = frame.copy()

        not_sampled = _make_detector_output(sampled=False)
        with patch("producer.pipeline.detect_plate_rois", return_value=not_sampled):
            pipeline.process_frame(frame, frame_idx=0)

        np.testing.assert_array_equal(frame, frame_copy)

    def test_pipeline_shutdown_idempotent(self):
        """Call shutdown twice, no error."""
        pipeline = _build_pipeline()

        pipeline.shutdown()
        pipeline.shutdown()  # Should not raise

    def test_pipeline_shutdown_no_listener(self):
        """_owns_logger=False -> does NOT stop listener."""
        pipeline = _build_pipeline()
        assert pipeline._owns_logger is False  # Built with external logger
        assert pipeline.log_listener is None

        # Mock listener to verify it's NOT called
        mock_listener = MagicMock()
        pipeline.log_listener = mock_listener

        pipeline.shutdown()
        mock_listener.stop.assert_not_called()

    def test_pipeline_get_updated_bins(self):
        """get_updated_bins delegates to buffer."""
        pipeline = _build_pipeline()

        pipeline.buffer.get_all_updated_bins.return_value = ["snapshot1"]
        result = pipeline.get_updated_bins({"track-1": 0})

        pipeline.buffer.get_all_updated_bins.assert_called_once_with({"track-1": 0})
        assert result == ["snapshot1"]

    def test_pipeline_get_stats_identity(self):
        """get_pipeline_stats returns self.stats (identity, not copy)."""
        pipeline = _build_pipeline()

        stats = pipeline.get_pipeline_stats()
        assert stats is pipeline.stats

    def test_setup_async_logging_modes(self, tmp_path):
        """All 3 logging modes create appropriate handlers."""
        for mode in [LoggingMode.PRODUCTION, LoggingMode.DEBUG, LoggingMode.TUNING]:
            cfg = make_config(
                pipeline_logging_mode=mode,
                production_log_path=str(tmp_path / "prod.jsonl"),
                debug_log_path=str(tmp_path / "debug.jsonl"),
                tuning_log_path=str(tmp_path / "tuning.jsonl"),
            )
            logger, listener = setup_async_logging(cfg, logger_name=f"test_{mode.value}")

            assert logger is not None
            assert listener is not None
            try:
                # Logger should have exactly 1 handler (QueueHandler)
                assert len(logger.handlers) == 1
                assert isinstance(logger.handlers[0], NonBlockingQueueHandler)
            finally:
                listener.stop()
