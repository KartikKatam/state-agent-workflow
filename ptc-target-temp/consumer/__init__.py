"""Consumer module for rich analysis, batching, preprocessing, and OCR queue."""

# Configuration
from .config import ConsumerConfig, load_consumer_config

# Core models
from .models import (
    EnhancedBatchSelection,
    OcrQueueItem,
    PostProcessingMeta,
    ProcessorOutput,
    RecipeRow,
    RichQualityMetrics,
    RoiRichQuality,
)
from .ops_batch_select import (
    apply_eligibility_gate,
    compute_ocr_likelihood_score,
    normalize_metric,
    select_batch_with_enhancement,
)
from .ops_preprocess_gpu import process_batch_gpu

# Operations
from .ops_quality_rich import (
    analyze_roi_rich,
    compute_contrast_metrics,
    compute_exposure_metrics,
    compute_plate_size_and_clip,
    compute_pose_metrics,
    compute_sharpness_metrics,
    create_canonical_crop,
    rich_quality_analyze_snapshot,
)
from .ops_recipe import RECIPE_KEYS, generate_recipe_tensor
from .ops_scheduler import choose_highest_priority_bin, fetch_updated_snapshots, get_next_snapshot
from .queue import OcrQueue

__all__ = [
    # Config
    "ConsumerConfig",
    "load_consumer_config",
    # Models
    "RichQualityMetrics",
    "RoiRichQuality",
    "EnhancedBatchSelection",
    "RecipeRow",
    "PostProcessingMeta",
    "ProcessorOutput",
    "OcrQueueItem",
    # Operations - Rich Quality
    "analyze_roi_rich",
    "rich_quality_analyze_snapshot",
    "create_canonical_crop",
    "compute_plate_size_and_clip",
    "compute_pose_metrics",
    "compute_exposure_metrics",
    "compute_contrast_metrics",
    "compute_sharpness_metrics",
    # Operations - Batching & Processing
    "select_batch_with_enhancement",
    "apply_eligibility_gate",
    "compute_ocr_likelihood_score",
    "normalize_metric",
    "generate_recipe_tensor",
    "process_batch_gpu",
    "RECIPE_KEYS",
    # Operations - Scheduling
    "get_next_snapshot",
    "fetch_updated_snapshots",
    "choose_highest_priority_bin",
    # Queue
    "OcrQueue",
]
