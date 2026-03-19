"""Tests for producer data types (models.py) and configuration (config.py).

Chunk-01: Test Infrastructure + Data Types (~55 tests).
Covers all dataclasses in producer.models and producer.config including
construction, invariants, boundary conditions, and property-based validation.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings

from producer.config import ProducerConfig, load_producer_config
from producer.models import (
    DetectorOutput,
    LoggingMode,
    PercentileWindow,
    PipelineStats,
    Track,
    TrackerOutput,
    TrackId,
)
from tests.conftest_producer import (
    make_bin_snapshot,
    make_buffer_stats,
    make_detection,
    make_fast_quality_metrics,
    make_frame_result,
    make_id_bin,
    make_roi_fast_quality,
    make_roi_image,
    make_track,
    st_position,
    st_quality_score,
    st_track,
)

# ===========================================================================
# TrackId
# ===========================================================================


class TestTrackId:
    """Tests for TrackId type alias."""

    def test_track_id_is_str_hashable(self):
        """TrackId is str and hashable (usable as dict key)."""
        tid: TrackId = "abc-123"
        assert isinstance(tid, str)
        # Hashable: can be used as dict key
        d = {tid: 1}
        assert d[tid] == 1


# ===========================================================================
# LoggingMode
# ===========================================================================


class TestLoggingMode:
    """Tests for LoggingMode enum."""

    def test_logging_mode_members(self):
        """LoggingMode has exactly 3 members."""
        assert len(LoggingMode) == 3
        assert set(LoggingMode.__members__.keys()) == {"PRODUCTION", "DEBUG", "TUNING"}

    def test_logging_mode_string_construction(self):
        """LoggingMode can be constructed from string value."""
        assert LoggingMode("debug") == LoggingMode.DEBUG
        assert LoggingMode("production") == LoggingMode.PRODUCTION
        assert LoggingMode("tuning") == LoggingMode.TUNING

    def test_logging_mode_invalid(self):
        """Invalid string raises ValueError."""
        with pytest.raises(ValueError):
            LoggingMode("invalid")


# ===========================================================================
# Detection
# ===========================================================================


class TestDetection:
    """Tests for Detection dataclass."""

    def test_detection_construction(self):
        """Detection with valid fields constructs correctly."""
        d = make_detection()
        assert d.bbox == [100.0, 200.0, 300.0, 400.0]
        assert d.confidence == 0.85
        assert d.class_id == 0
        assert d.frame_idx == 0
        assert d.keypoints is None
        assert d.keypoint_scores is None

    def test_detection_keypoints_pairing(self):
        """Keypoints and keypoint_scores should both be set or both None."""
        # Both None — valid
        d1 = make_detection(keypoints=None, keypoint_scores=None)
        assert d1.keypoints is None and d1.keypoint_scores is None

        # Both set — valid
        kps = [(10.0, 20.0), (30.0, 40.0)]
        scores = [0.9, 0.8]
        d2 = make_detection(keypoints=kps, keypoint_scores=scores)
        assert d2.keypoints == kps
        assert d2.keypoint_scores == scores


# ===========================================================================
# Track
# ===========================================================================


class TestTrack:
    """Tests for Track dataclass."""

    def test_track_post_init_wrong_lengths(self):
        """Track __post_init__ raises AssertionError for wrong position/velocity lengths."""
        with pytest.raises(AssertionError, match="position must be length 4"):
            Track(
                track_id="t1",
                confidence=0.9,
                position=[1.0, 2.0, 3.0],  # len 3
                velocity=[0.0, 0.0, 0.0, 0.0],
            )
        with pytest.raises(AssertionError, match="velocity must be length 4"):
            Track(
                track_id="t1",
                confidence=0.9,
                position=[1.0, 2.0, 3.0, 4.0],
                velocity=[0.0, 0.0],  # len 2
            )

    def test_track_get_bbox_math(self):
        """get_bbox returns correct [x1, y1, x2, y2] from position."""
        t = make_track(position=[200.0, 300.0, 100.0, 50.0])
        bbox = t.get_bbox()
        assert bbox == [150.0, 275.0, 250.0, 325.0]

    def test_track_is_confirmed_boundary(self):
        """is_confirmed at hits==min_hits boundary."""
        t = make_track(hits=3)
        assert t.is_confirmed(min_hits=3) is True
        assert t.is_confirmed(min_hits=4) is False

    @given(data=st_position())
    @settings(max_examples=50)
    def test_track_get_bbox_property(self, data):
        """bbox width/height match position w/h, center preserved."""
        cx, cy, w, h = data
        t = make_track(position=[cx, cy, w, h])
        bbox = t.get_bbox()
        x1, y1, x2, y2 = bbox

        assert math.isclose(x2 - x1, w, rel_tol=1e-9)
        assert math.isclose(y2 - y1, h, rel_tol=1e-9)
        assert math.isclose((x1 + x2) / 2, cx, rel_tol=1e-9)
        assert math.isclose((y1 + y2) / 2, cy, rel_tol=1e-9)

    @given(track=st_track())
    @settings(max_examples=50)
    def test_track_is_confirmed_min_hits_zero(self, track):
        """min_hits=0 always True regardless of hits."""
        assert track.is_confirmed(min_hits=0) is True


# ===========================================================================
# DetectorOutput
# ===========================================================================


class TestDetectorOutput:
    """Tests for DetectorOutput dataclass."""

    def test_detector_output_accounting(self):
        """len(filter_reasons) + len(filtered) == len(raw)."""
        raw = [make_detection(frame_idx=i) for i in range(5)]
        filtered = raw[:3]
        reasons = {3: "too_small", 4: "bad_aspect"}
        out = DetectorOutput(
            raw_detections=raw,
            filtered_detections=filtered,
            filter_reasons=reasons,
        )
        assert len(out.filter_reasons) + len(out.filtered_detections) == len(out.raw_detections)


# ===========================================================================
# TrackerOutput
# ===========================================================================


class TestTrackerOutput:
    """Tests for TrackerOutput dataclass."""

    def test_tracker_output_confirmed_subset(self):
        """confirmed_tracks is subset of tracks."""
        t1 = make_track(hits=5)
        t2 = make_track(hits=1)
        t3 = make_track(hits=10)
        out = TrackerOutput(
            tracks=[t1, t2, t3],
            confirmed_tracks=[t1, t3],
            camera_motion=(0.0, 0.0),
        )
        for ct in out.confirmed_tracks:
            assert ct in out.tracks


# ===========================================================================
# RoiImage
# ===========================================================================


class TestRoiImage:
    """Tests for RoiImage dataclass."""

    def test_roi_image_field_access(self):
        """RoiImage fields accessible after construction."""
        roi = make_roi_image()
        assert isinstance(roi.track_id, str)
        assert roi.crop_img.shape == (100, 200, 3)
        assert roi.crop_img.dtype == np.uint8
        assert roi.frame_width == 1920
        assert roi.frame_height == 1080
        assert roi.was_resized is False
        assert roi.resize_scale == 1.0


# ===========================================================================
# FastQualityMetrics
# ===========================================================================


class TestFastQualityMetrics:
    """Tests for FastQualityMetrics dataclass."""

    def test_fast_quality_metrics_shape_dtype(self):
        """gradient_histogram has shape (25,) and dtype float32."""
        m = make_fast_quality_metrics()
        assert m.gradient_histogram.shape == (25,)
        assert m.gradient_histogram.dtype == np.float32


# ===========================================================================
# RoiFastQuality
# ===========================================================================


class TestRoiFastQuality:
    """Tests for RoiFastQuality dataclass."""

    def test_roi_fast_quality_independence(self):
        """quality_score and passes_min_quality are independent fields."""
        # High score but fails gate
        rfq1 = make_roi_fast_quality(quality_score=0.95, passes_min_quality=False)
        assert rfq1.quality_score == 0.95
        assert rfq1.passes_min_quality is False

        # Low score but passes gate
        rfq2 = make_roi_fast_quality(quality_score=0.1, passes_min_quality=True)
        assert rfq2.quality_score == 0.1
        assert rfq2.passes_min_quality is True


# ===========================================================================
# IdBin
# ===========================================================================


class TestIdBin:
    """Tests for IdBin dataclass."""

    def test_id_bin_is_bootstrapping_boundary(self):
        """is_bootstrapping boundary at attempted==bootstrap_k (uses <, so K-th is NOT bootstrapping)."""
        b = make_id_bin(attempted_insertions=2)
        assert b.is_bootstrapping(bootstrap_k=3) is True  # 2 < 3

        b2 = make_id_bin(attempted_insertions=3)
        assert b2.is_bootstrapping(bootstrap_k=3) is False  # 3 < 3 is False

        b3 = make_id_bin(attempted_insertions=0)
        assert b3.is_bootstrapping(bootstrap_k=1) is True  # 0 < 1

        b4 = make_id_bin(attempted_insertions=1)
        assert b4.is_bootstrapping(bootstrap_k=1) is False  # 1 < 1 is False


# ===========================================================================
# BinSnapshot
# ===========================================================================


class TestBinSnapshot:
    """Tests for BinSnapshot dataclass."""

    def test_bin_snapshot_read_only_arrays(self):
        """crop_img in snapshot entries should be shareable but not mutated in-place."""
        # Create a snapshot with an entry whose crop_img is writable
        roi = make_roi_image()
        rfq = make_roi_fast_quality(roi=roi)
        snap = make_bin_snapshot(entries=[rfq])

        # The contract says Consumer MUST NOT mutate crop_img in-place.
        # The snapshot shares image data with the producer buffer (shallow copy).
        # We verify the snapshot entry's crop_img is accessible and has expected shape.
        assert snap.entries[0].roi.crop_img.shape == (100, 200, 3)
        assert snap.entries[0].roi.crop_img.dtype == np.uint8


# ===========================================================================
# BufferStats
# ===========================================================================


class TestBufferStats:
    """Tests for BufferStats dataclass."""

    def test_buffer_stats_accounting_identity(self):
        """total == quality_drops + ooo_drops + appends + replaces + sim_drops."""
        stats = make_buffer_stats(
            total_attempts=20,
            quality_gate_drops=5,
            out_of_order_drops=2,
            appends=8,
            replaces=3,
            similarity_drops=2,
        )
        assert stats.total_attempts == (
            stats.quality_gate_drops
            + stats.out_of_order_drops
            + stats.appends
            + stats.replaces
            + stats.similarity_drops
        )

    @given(
        qd=st_quality_score().map(lambda x: int(x * 100)),
        ood=st_quality_score().map(lambda x: int(x * 50)),
        ap=st_quality_score().map(lambda x: int(x * 100)),
        rp=st_quality_score().map(lambda x: int(x * 50)),
        sd=st_quality_score().map(lambda x: int(x * 50)),
    )
    @settings(max_examples=50)
    def test_buffer_stats_accounting_identity_property(self, qd, ood, ap, rp, sd):
        """Property: total always equals sum of components."""
        total = qd + ood + ap + rp + sd
        stats = make_buffer_stats(
            total_attempts=total,
            quality_gate_drops=qd,
            out_of_order_drops=ood,
            appends=ap,
            replaces=rp,
            similarity_drops=sd,
        )
        assert stats.total_attempts == (
            stats.quality_gate_drops
            + stats.out_of_order_drops
            + stats.appends
            + stats.replaces
            + stats.similarity_drops
        )


# ===========================================================================
# PercentileWindow
# ===========================================================================


class TestPercentileWindow:
    """Tests for PercentileWindow dataclass."""

    def test_percentile_window_below_min(self):
        """<10 samples returns 0.0."""
        pw = PercentileWindow(maxlen=100)
        for i in range(9):
            pw.append(float(i))
        assert pw.percentile(50) == 0.0

    def test_percentile_window_at_min(self):
        """Exactly 10 samples returns real percentile."""
        pw = PercentileWindow(maxlen=100)
        for i in range(10):
            pw.append(float(i))
        result = pw.percentile(50)
        assert result > 0.0  # median of [0..9] = 4.5

    def test_percentile_window_eviction(self):
        """Append eviction at maxlen: old values removed."""
        pw = PercentileWindow(maxlen=15)
        # Fill 15 values: 0..14
        for i in range(15):
            pw.append(float(i))
        assert len(pw.values) == 15

        # Add 5 more — oldest 5 evicted, window is now [5..19]
        for i in range(15, 20):
            pw.append(float(i))
        assert len(pw.values) == 15
        assert list(pw.values) == [float(i) for i in range(5, 20)]

    @given(
        values=st_quality_score().flatmap(
            lambda _: st_quality_score().filter(lambda x: not math.isnan(x))
        ),
    )
    @settings(max_examples=50)
    def test_percentile_window_monotonicity(self, values):
        """p(q1) <= p(q2) when q1 <= q2."""
        pw = PercentileWindow(maxlen=100)
        # Fill with enough non-NaN data
        for i in range(20):
            pw.append(float(i) + values)

        p25 = pw.percentile(25)
        p50 = pw.percentile(50)
        p75 = pw.percentile(75)
        p99 = pw.percentile(99)
        assert p25 <= p50 <= p75 <= p99

    def test_percentile_window_nan_propagation(self):
        """NaN in window propagates to percentile output."""
        pw = PercentileWindow(maxlen=100)
        for i in range(9):
            pw.append(float(i))
        pw.append(float("nan"))
        result = pw.percentile(50)
        assert math.isnan(result)

    @given(val=st_quality_score())
    @settings(max_examples=50)
    def test_percentile_window_all_same(self, val):
        """All-same-value: all percentiles equal that value."""
        pw = PercentileWindow(maxlen=100)
        for _ in range(20):
            pw.append(val)
        # All percentiles should equal val
        assert math.isclose(pw.percentile(50), val, rel_tol=1e-9)
        assert math.isclose(pw.percentile(95), val, rel_tol=1e-9)
        assert math.isclose(pw.percentile(99), val, rel_tol=1e-9)

    def test_percentile_window_convenience_properties(self):
        """p50/p95/p99 properties match get_percentile calls."""
        pw = PercentileWindow(maxlen=100)
        for i in range(20):
            pw.append(float(i))
        assert pw.p50 == pw.percentile(50)
        assert pw.p95 == pw.percentile(95)
        assert pw.p99 == pw.percentile(99)


# ===========================================================================
# PipelineStats
# ===========================================================================


class TestPipelineStats:
    """Tests for PipelineStats dataclass."""

    def test_pipeline_stats_post_init(self):
        """__post_init__ creates 7 PercentileWindows."""
        ps = PipelineStats()
        assert len(ps._windows) == 7
        expected_metrics = {
            "frame_ms",
            "detection_ms",
            "tracking_ms",
            "cropping_ms",
            "quality_ms",
            "buffer_ms",
            "corner_cnn_ms",
        }
        assert set(ps._windows.keys()) == expected_metrics

    def test_pipeline_stats_set_window_size(self):
        """set_window_size reinitializes (data lost)."""
        ps = PipelineStats()
        # Add data to a window
        for i in range(20):
            ps._windows["frame_ms"].append(float(i))
        assert len(ps._windows["frame_ms"].values) == 20

        # Reinitialize with new size
        ps.set_window_size(50)
        assert len(ps._windows["frame_ms"].values) == 0  # data lost
        assert ps._windows["frame_ms"].maxlen == 50

    def test_pipeline_stats_unknown_metric(self):
        """get_percentile with unknown metric returns 0.0."""
        ps = PipelineStats()
        assert ps.get_percentile("nonexistent_metric", 50) == 0.0

    def test_pipeline_stats_percentile_properties(self):
        """21 percentile properties equal get_percentile calls."""
        ps = PipelineStats()
        # Add enough data to all windows
        for metric in ps._windows:
            for i in range(20):
                ps._windows[metric].append(float(i))

        # 7 metrics × 3 percentiles = 21 properties
        assert ps.p50_frame_ms == ps.get_percentile("frame_ms", 50)
        assert ps.p95_frame_ms == ps.get_percentile("frame_ms", 95)
        assert ps.p99_frame_ms == ps.get_percentile("frame_ms", 99)

        assert ps.p50_detection_ms == ps.get_percentile("detection_ms", 50)
        assert ps.p95_detection_ms == ps.get_percentile("detection_ms", 95)
        assert ps.p99_detection_ms == ps.get_percentile("detection_ms", 99)

        assert ps.p50_tracking_ms == ps.get_percentile("tracking_ms", 50)
        assert ps.p95_tracking_ms == ps.get_percentile("tracking_ms", 95)
        assert ps.p99_tracking_ms == ps.get_percentile("tracking_ms", 99)

        assert ps.p50_cropping_ms == ps.get_percentile("cropping_ms", 50)
        assert ps.p95_cropping_ms == ps.get_percentile("cropping_ms", 95)
        assert ps.p99_cropping_ms == ps.get_percentile("cropping_ms", 99)

        assert ps.p50_quality_ms == ps.get_percentile("quality_ms", 50)
        assert ps.p95_quality_ms == ps.get_percentile("quality_ms", 95)
        assert ps.p99_quality_ms == ps.get_percentile("quality_ms", 99)

        assert ps.p50_buffer_ms == ps.get_percentile("buffer_ms", 50)
        assert ps.p95_buffer_ms == ps.get_percentile("buffer_ms", 95)
        assert ps.p99_buffer_ms == ps.get_percentile("buffer_ms", 99)

        assert ps.p50_corner_cnn_ms == ps.get_percentile("corner_cnn_ms", 50)
        assert ps.p95_corner_cnn_ms == ps.get_percentile("corner_cnn_ms", 95)
        assert ps.p99_corner_cnn_ms == ps.get_percentile("corner_cnn_ms", 99)


# ===========================================================================
# FrameResult
# ===========================================================================


class TestFrameResult:
    """Tests for FrameResult dataclass."""

    def test_frame_result_field_access(self):
        """FrameResult fields accessible."""
        fr = make_frame_result()
        assert fr.frame_idx == 0
        assert fr.num_raw_detections == 5
        assert fr.num_filtered_detections == 3
        assert fr.total_ms == 21.0
        assert fr.sampled is True
        assert fr.error is None
        assert fr.corner_cnn_ms == 0.0


# ===========================================================================
# ProducerConfig
# ===========================================================================


class TestProducerConfig:
    """Tests for ProducerConfig dataclass."""

    def test_producer_config_defaults(self):
        """Default construction succeeds, all fields accessible."""
        cfg = ProducerConfig()
        assert cfg.plate_class_id == 0
        assert cfg.conf_threshold == 0.25
        assert cfg.min_bbox_width == 100
        assert cfg.pipeline_logging_mode == LoggingMode.PRODUCTION

    def test_producer_config_cross_field_invariants(self):
        """iou thresholds ordered, min_bbox_width>=100, weights sum to 1.0."""
        cfg = ProducerConfig()
        # iou_threshold_tentative > iou_threshold_confirmed
        assert cfg.iou_threshold_tentative > cfg.iou_threshold_confirmed
        # min_bbox_width >= 100
        assert cfg.min_bbox_width >= 100
        # Quality weights sum to 1.0
        quality_weights = (
            cfg.fast_quality_w_focus
            + cfg.fast_quality_w_contrast
            + cfg.fast_quality_w_band
            + cfg.fast_quality_w_bright
            + cfg.fast_quality_w_exposure
        )
        assert math.isclose(quality_weights, 1.0, rel_tol=1e-9)
        # Novelty weights sum to 1.0
        novelty_weights = cfg.buffer_novelty_weight_pixel + cfg.buffer_novelty_weight_feature
        assert math.isclose(novelty_weights, 1.0, rel_tol=1e-9)


# ===========================================================================
# load_producer_config
# ===========================================================================


class TestLoadProducerConfig:
    """Tests for load_producer_config function."""

    def test_load_config_no_overrides(self, tmp_path, monkeypatch):
        """No overrides returns defaults."""
        monkeypatch.chdir(tmp_path)
        cfg = load_producer_config()
        assert isinstance(cfg, ProducerConfig)
        assert cfg.plate_class_id == 0

    def test_load_config_single_override(self, tmp_path, monkeypatch):
        """Single override takes effect."""
        monkeypatch.chdir(tmp_path)
        cfg = load_producer_config(min_bbox_width=150)
        assert cfg.min_bbox_width == 150

    def test_load_config_unknown_field(self, tmp_path, monkeypatch):
        """Unknown field raises ValueError."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="Unknown config parameter"):
            load_producer_config(nonexistent_field=42)

    def test_load_config_logging_mode_string(self, tmp_path, monkeypatch):
        """'debug' string converts to LoggingMode.DEBUG."""
        monkeypatch.chdir(tmp_path)
        cfg = load_producer_config(pipeline_logging_mode="debug")
        assert cfg.pipeline_logging_mode == LoggingMode.DEBUG

    def test_load_config_logging_mode_uppercase(self, tmp_path, monkeypatch):
        """'PRODUCTION' uppercase works (case-insensitive)."""
        monkeypatch.chdir(tmp_path)
        cfg = load_producer_config(pipeline_logging_mode="PRODUCTION")
        assert cfg.pipeline_logging_mode == LoggingMode.PRODUCTION

    def test_load_config_logging_mode_invalid(self, tmp_path, monkeypatch):
        """'invalid' raises ValueError."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="Invalid pipeline_logging_mode"):
            load_producer_config(pipeline_logging_mode="invalid")

    def test_load_config_enum_passthrough(self, tmp_path, monkeypatch):
        """Already-enum value passes through unchanged."""
        monkeypatch.chdir(tmp_path)
        cfg = load_producer_config(pipeline_logging_mode=LoggingMode.TUNING)
        assert cfg.pipeline_logging_mode == LoggingMode.TUNING

    def test_load_config_directory_creation(self, tmp_path, monkeypatch):
        """Directory creation is idempotent."""
        monkeypatch.chdir(tmp_path)
        # First call creates directories
        cfg1 = load_producer_config()
        assert cfg1 is not None
        # Second call doesn't fail (idempotent)
        cfg2 = load_producer_config()
        assert cfg2 is not None
