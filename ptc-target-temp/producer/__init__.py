"""Producer module for license plate detection, tracking, and fast quality analysis."""

# Main pipeline
# Buffer management
from .buffer import IdBufferManager

# Configuration
from .config import ProducerConfig, load_producer_config

# Core models
from .models import (
    BinSnapshot,
    BufferStats,
    Detection,
    DetectionTimings,
    DetectorOutput,
    FastQualityMetrics,
    FrameResult,
    IdBin,
    LoggingMode,
    PipelineStats,
    RoiFastQuality,
    RoiImage,
    Track,
    TrackerOutput,
    TrackId,
    TrackingTimings,
)
from .ops_cropping import crop_track_roi, crop_tracks

# Operations
from .ops_detection import FrameSampler, YoloDetector, detect_plate_rois
from .ops_quality_fast import fast_quality_analyze_rois
from .ops_tracking import PlateTracker, track_detections
from .pipeline import ProducerPipeline

__all__ = [
    # Pipeline
    "ProducerPipeline",
    # Config
    "ProducerConfig",
    "load_producer_config",
    # Models
    "Detection",
    "DetectionTimings",
    "DetectorOutput",
    "Track",
    "TrackingTimings",
    "TrackerOutput",
    "RoiImage",
    "FastQualityMetrics",
    "RoiFastQuality",
    "FrameResult",
    "PipelineStats",
    "LoggingMode",
    "TrackId",
    # Buffer
    "IdBufferManager",
    "IdBin",
    "BinSnapshot",
    "BufferStats",
    # Operations
    "YoloDetector",
    "FrameSampler",
    "detect_plate_rois",
    "PlateTracker",
    "track_detections",
    "crop_tracks",
    "crop_track_roi",
    "fast_quality_analyze_rois",
]
