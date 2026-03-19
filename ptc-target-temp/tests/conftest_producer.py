"""Shared test factories and Hypothesis strategies for producer tests.

Provides 10 factory functions following the make_roi_rq() pattern
(keyword-only args with **overrides) and 6 Hypothesis strategies
for property-based testing of producer data types.
"""

from __future__ import annotations

import uuid

import numpy as np
from hypothesis import strategies as st

from producer.config import ProducerConfig
from producer.models import (
    BinSnapshot,
    BufferStats,
    Detection,
    FastQualityMetrics,
    FrameResult,
    IdBin,
    RoiFastQuality,
    RoiImage,
    Track,
)

# ---------------------------------------------------------------------------
# Factory functions (module-level, NOT fixtures)
# ---------------------------------------------------------------------------


def make_detection(**overrides) -> Detection:
    """Factory for Detection with sensible defaults."""
    defaults = {
        "bbox": [100.0, 200.0, 300.0, 400.0],
        "confidence": 0.85,
        "class_id": 0,
        "frame_idx": 0,
    }
    defaults.update(overrides)
    return Detection(**defaults)


def make_track(**overrides) -> Track:
    """Factory for Track with valid position/velocity passing __post_init__."""
    defaults = {
        "track_id": str(uuid.uuid4()),
        "confidence": 0.9,
        "position": [200.0, 300.0, 200.0, 200.0],  # [cx, cy, w, h]
        "velocity": [0.0, 0.0, 0.0, 0.0],  # [vx, vy, vw, vh]
        "hits": 3,
        "age": 5,
    }
    defaults.update(overrides)
    return Track(**defaults)


def make_roi_image(**overrides) -> RoiImage:
    """Factory for RoiImage with minimal valid crop."""
    defaults = {
        "track_id": str(uuid.uuid4()),
        "crop_img": np.zeros((100, 200, 3), dtype=np.uint8),
        "bbox": [100.0, 200.0, 300.0, 400.0],
        "padded_bbox": [90.0, 190.0, 310.0, 410.0],
        "frame_idx": 0,
        "confidence": 0.85,
        "frame_width": 1920,
        "frame_height": 1080,
    }
    defaults.update(overrides)
    return RoiImage(**defaults)


def make_fast_quality_metrics(**overrides) -> FastQualityMetrics:
    """Factory for FastQualityMetrics with valid gradient_histogram."""
    hist = np.full(25, 1.0 / np.sqrt(25.0), dtype=np.float32)
    defaults = {
        "focus_tenengrad": 1.5,
        "brightness_mean": 0.5,
        "contrast_std": 0.1,
        "over_exposed_frac": 0.01,
        "under_exposed_frac": 0.02,
        "band_edge_mean": 0.8,
        "gradient_histogram": hist,
    }
    defaults.update(overrides)
    return FastQualityMetrics(**defaults)


def make_roi_fast_quality(**overrides) -> RoiFastQuality:
    """Factory for RoiFastQuality with valid thumb_gray."""
    defaults = {
        "roi": make_roi_image(),
        "metrics": make_fast_quality_metrics(),
        "quality_score": 0.75,
        "passes_min_quality": True,
        "thumb_gray": np.zeros((48, 160), dtype=np.uint8),
    }
    defaults.update(overrides)
    return RoiFastQuality(**defaults)


def make_config(**overrides) -> ProducerConfig:
    """Factory for ProducerConfig with defaults."""
    cfg = ProducerConfig()
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def make_frame_result(**overrides) -> FrameResult:
    """Factory for FrameResult with sensible defaults."""
    defaults = {
        "frame_idx": 0,
        "timestamp_ms": 0,
        "num_raw_detections": 5,
        "num_filtered_detections": 3,
        "num_rejected_detections": 2,
        "num_active_tracks": 4,
        "num_confirmed_tracks": 2,
        "num_crops": 2,
        "num_quality_passed": 1,
        "buffer_updates": 1,
        "detection_ms": 10.0,
        "tracking_ms": 5.0,
        "cropping_ms": 2.0,
        "quality_ms": 3.0,
        "buffer_ms": 1.0,
        "total_ms": 21.0,
        "sampled": True,
    }
    defaults.update(overrides)
    return FrameResult(**defaults)


def make_id_bin(**overrides) -> IdBin:
    """Factory for IdBin with empty entries."""
    defaults = {
        "track_id": str(uuid.uuid4()),
        "entries": [],
        "version": 0,
        "last_update_frame_idx": 0,
    }
    defaults.update(overrides)
    return IdBin(**defaults)


def make_bin_snapshot(**overrides) -> BinSnapshot:
    """Factory for BinSnapshot."""
    defaults = {
        "track_id": str(uuid.uuid4()),
        "version": 1,
        "entries": [],
        "last_update_frame_idx": 0,
    }
    defaults.update(overrides)
    return BinSnapshot(**defaults)


def make_buffer_stats(**overrides) -> BufferStats:
    """Factory for BufferStats with zero counters."""
    defaults = {
        "track_id": str(uuid.uuid4()),
        "total_attempts": 0,
        "quality_gate_drops": 0,
        "out_of_order_drops": 0,
        "appends": 0,
        "replaces": 0,
        "similarity_drops": 0,
        "current_bin_size": 0,
        "current_version": 0,
    }
    defaults.update(overrides)
    return BufferStats(**defaults)


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------


@st.composite
def st_bbox(draw):
    """Valid bbox [x1, y1, x2, y2] with x2>x1, y2>y1."""
    x1 = draw(st.floats(min_value=0.0, max_value=1800.0))
    y1 = draw(st.floats(min_value=0.0, max_value=900.0))
    w = draw(st.floats(min_value=1.0, max_value=500.0))
    h = draw(st.floats(min_value=1.0, max_value=300.0))
    return [x1, y1, x1 + w, y1 + h]


@st.composite
def st_position(draw):
    """Valid position [cx, cy, w, h] with w>0, h>0."""
    cx = draw(st.floats(min_value=50.0, max_value=1870.0))
    cy = draw(st.floats(min_value=50.0, max_value=1030.0))
    w = draw(st.floats(min_value=1.0, max_value=500.0))
    h = draw(st.floats(min_value=1.0, max_value=300.0))
    return [cx, cy, w, h]


@st.composite
def st_detection(draw):
    """Detection with valid fields."""
    bbox = draw(st_bbox())
    confidence = draw(st.floats(min_value=0.0, max_value=1.0))
    class_id = draw(st.integers(min_value=0, max_value=10))
    frame_idx = draw(st.integers(min_value=0, max_value=100000))
    return Detection(bbox=bbox, confidence=confidence, class_id=class_id, frame_idx=frame_idx)


@st.composite
def st_track(draw):
    """Track with valid position/velocity passing __post_init__."""
    pos = draw(st_position())
    vel = [
        draw(st.floats(min_value=-50.0, max_value=50.0)),
        draw(st.floats(min_value=-50.0, max_value=50.0)),
        draw(st.floats(min_value=-10.0, max_value=10.0)),
        draw(st.floats(min_value=-10.0, max_value=10.0)),
    ]
    hits = draw(st.integers(min_value=0, max_value=100))
    return Track(
        track_id=str(uuid.uuid4()),
        confidence=draw(st.floats(min_value=0.0, max_value=1.0)),
        position=pos,
        velocity=vel,
        hits=hits,
    )


@st.composite
def st_roi_image(draw):
    """RoiImage with minimal crop."""
    bbox = draw(st_bbox())
    return RoiImage(
        track_id=str(uuid.uuid4()),
        crop_img=np.zeros((10, 20, 3), dtype=np.uint8),
        bbox=bbox,
        padded_bbox=bbox,
        frame_idx=draw(st.integers(min_value=0, max_value=100000)),
        confidence=draw(st.floats(min_value=0.0, max_value=1.0)),
        frame_width=1920,
        frame_height=1080,
    )


def st_quality_score():
    """Quality score in [0.0, 1.0]."""
    return st.floats(min_value=0.0, max_value=1.0)
