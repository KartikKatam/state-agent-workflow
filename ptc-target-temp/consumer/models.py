from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import torch

from producer.models import RoiFastQuality, TrackId


@dataclass
class RichQualityMetrics:
    """
    Comprehensive quality analysis metrics for license plate ROI (V1 spec).

    Combines geometric, photometric, and pose metrics to support:
    - Top-8 selection with pose diversity
    - Top-4 duplicate selection for full photometric enhancement
    - Homography/deskew eligibility gating
    - Rejection of hopeless (noisy/blurred/too-small) frames

    Geometric metrics computed on original crop, photometric on canonical resize.
    """

    # ===== Identification =====
    track_id: TrackId  # UUID linking to track (for debugging/logging)
    frame_idx: int  # Frame number where ROI was extracted (for debugging/logging)

    # ===== Canonical Crop Metadata =====
    canonical_width: int  # Width of canonical resize for photometric metrics
    canonical_height: int  # Height of canonical resize for photometric metrics

    # ===== Core Numeric Metrics =====

    # Plate size and crop integrity (geometric, original crop)
    plate_width_px: float  # Plate width in pixels (from bbox)
    plate_height_px: float  # Plate height in pixels (from bbox)
    crop_clip_fraction: float  # Fraction of padded bbox clipped by image boundaries [0-1]

    # Detection confidence
    detection_confidence: float  # YOLO detection confidence [0-1]

    # Raw keypoint data (geometric, original crop coordinates)
    keypoints: (
        list[tuple[float, float]] | None
    )  # 4 corners in crop coords (TL, TR, BR, BL order), or None
    keypoint_scores: list[float] | None  # Per-keypoint confidence scores, or None
    keypoint_confidence_min: float | None  # Minimum keypoint confidence, or None if no keypoints
    keypoint_confidence_mean: float | None  # Mean keypoint confidence, or None if no keypoints

    # Pose / viewpoint metrics (geometric, original crop)
    skew_degrees: (
        float | None
    )  # Rotation angle from horizontal (median of top/bottom edges), None if no quad
    perspective_score: (
        float | None
    )  # Max perspective distortion (max of lr/tb ratios), None if no quad
    perspective_direction: (
        float | None
    )  # Signed perspective direction [-1, 1]: negative=viewing from right, positive=viewing from left, None if no quad

    # Photometric metrics - Exposure (canonical resize, LAB L channel)
    luminance_mean: float  # Mean L value [0-255]
    luminance_p05: float  # 5th percentile of L
    luminance_p95: float  # 95th percentile of L
    black_clip_fraction: float  # Fraction of pixels <= 5 [0-1]
    white_clip_fraction: float  # Fraction of pixels >= 250 [0-1]

    # Photometric metrics - Contrast (canonical resize)
    global_contrast: float  # p90 - p10 of luminance
    local_contrast: float  # Mean tile stddev over 8×4 grid

    # Photometric metrics - Sharpness (canonical resize)
    tenengrad: float  # Sobel energy (combined gx² + gy²)
    tenengrad_horizontal: float  # Horizontal sharpness (gx² only, detects vertical edges)
    tenengrad_vertical: float  # Vertical sharpness (gy² only, detects horizontal edges)
    blur_anisotropy: float  # max(h,v) / min(h,v) - detects directional motion blur

    # Photometric metrics - Noise (canonical resize)
    noise_std: float  # MAD-based noise estimate in flat regions (robust to edge contamination)
    flat_region_fraction: (
        float  # Fraction of image classified as flat (metadata for noise estimate)
    )

    # ===== Eligibility Flags =====

    # Hard viability checks
    too_small: bool  # plate_height_px < threshold
    clipped: bool  # crop_clip_fraction > threshold

    # Photometric quality flags
    very_blurry: bool  # tenengrad < SHARP_FLOOR (hopeless for sharpening)
    mildly_soft: bool  # SHARP_FLOOR <= tenengrad < SHARP_GOOD
    exposure_bad: bool  # Mean out of range OR extreme clipping
    low_contrast: bool  # Global or local contrast below threshold
    noisy: bool  # noise_std > threshold
    vertical_edges_weak: bool  # tenengrad_h < h_floor (horizontal motion blur destroys vertical edges - critical for OCR)
    horizontal_edges_weak: (
        bool  # tenengrad_v < v_floor (vertical motion blur - logged for ML, not gated)
    )

    # Composite eligibility flags (soft gates for batch selection)
    top8_eligible: bool  # Coarse viability: not too_small, not clipped, not very_blurry, not extreme saturation
    enhance_eligible: bool  # Worth photometric enhancement: viable + fixable + motivated
    homography_eligible: bool  # Quad geometry valid for homography

    # ===== Frame Position (for diversity) =====

    bbox_center_x_normalized: float  # Horizontal center [0, 1] normalized to frame width
    bbox_center_y_normalized: float  # Vertical center [0, 1] normalized to frame height

    # ===== Diversity Signature Vector =====

    # Compact 4D diversity signature with orthogonal dimensions for batch selection
    # Shape: (4,) float32
    # [0] rotation: skew in [-1, 1], maps [-45°, +45°]
    # [1] scale: plate height in [0, 1], maps [40px, 140px]
    # [2] position_x: horizontal center in [0, 1]
    # [3] perspective_dir: signed perspective in [-1, 1]
    diversity_signature: np.ndarray

    # Quad geometry metrics (for inspection/debugging)
    quad_area_px: float | None  # Quad area in pixels, None if no quad
    edge_ratio: float | None  # max(edges) / min(edges), None if no quad


@dataclass
class RoiRichQuality:
    """
    ROI annotated with rich quality metrics.
    """

    roi_fq: RoiFastQuality
    metrics: RichQualityMetrics


@dataclass
class EnhancedBatchSelection:
    """Selected batch with explicit duplicate groups for OCR aggregation.

    Each enhanced ROI is linked to its base ROI via ``duplicate_groups``:
    a dict mapping group_id to ``[base_index, enhance_index]`` where indices
    reference positions in the concatenated ``base_rois + enhance_rois`` list.
    """

    track_id: TrackId
    version: int
    base_rois: list[RoiRichQuality]
    enhance_rois: list[RoiRichQuality]
    duplicate_groups: dict[str, list[int]]


@dataclass
class RecipeRow:
    """Per-image recipe metadata for logging and debugging."""

    luma_deficit: float
    contrast_deficit: float
    sharpness_deficit: float
    noise_excess: float
    gates_passed: list[str]
    gates_failed: list[str]
    active_ops: list[str]
    primary_reason: str


@dataclass
class PostProcessingMeta:
    """Post-processing quality metrics (computed in [0,1] before normalization)."""

    post_luma_mean: float
    post_global_contrast: float
    luma_delta: float
    contrast_delta: float


@dataclass
class ProcessorOutput:
    """GPU processor output bundle."""

    batch_tensor: torch.Tensor  # (N,3,H,W) float32
    is_enhanced: np.ndarray  # (N,) bool
    post_meta: list[PostProcessingMeta]


@dataclass
class TrackMetrics:
    """Per-track lifecycle counters.

    Initialized when a track first appears in submit_candidate.
    """

    first_seen_ms: int
    total_candidates_received: int = 0
    total_commits: int = 0
    commits_superseded_before_consumption: int = 0
    total_consumed: int = 0


@dataclass
class CommitRecord:
    """Snapshot of a committed batch.

    Immutable after creation (but not frozen — torch.Tensor is mutable).
    """

    commit_version: int
    source_bin_version: int
    commit_score: float
    batch_tensor: torch.Tensor  # (N,3,H,W) float32
    is_enhanced: np.ndarray  # (N,) bool
    base_roi_scores: list[float]  # sorted descending
    created_at_ms: int
    duplicate_groups: dict[str, list[int]] = field(default_factory=dict)
    per_roi_quality: list[float] = field(default_factory=list)  # H9: base-rois-aligned (unsorted)


@dataclass
class InFlightRecord:
    """Tracks a dispatched-but-not-finished commit.

    One per track at most.
    """

    commit_version: int
    dispatched_at_ms: int
    worker_id: str


@dataclass
class TrackState:
    """Full per-track lifecycle state.

    Keyed by track_id in the queue's internal dict.
    """

    track_id: TrackId
    metrics: TrackMetrics
    best_commit: CommitRecord | None = None
    in_flight: InFlightRecord | None = None
    consumed_commit_version: int = 0
    done: bool = False
    done_at_ms: int | None = None


@dataclass(frozen=True)
class OcrWorkItem:
    """Immutable work item returned by pull_next.

    frozen=True enforces field immutability (Tensor contents are still
    mutable at the numpy level, but the dataclass fields cannot be reassigned).
    """

    track_id: TrackId
    commit_version: int
    source_bin_version: int
    batch_tensor: torch.Tensor  # (N,3,H,W) float32
    is_enhanced: np.ndarray  # (N,) bool
    duplicate_groups: dict[str, list[int]] = field(default_factory=dict)
    per_roi_quality: list[float] = field(default_factory=list)  # H9: base-rois-aligned (unsorted)


@dataclass(frozen=True)
class CandidateResult:
    """Decoded candidate string with composite score from aggregated evidence.

    Produced by aggregate_characters + apply_duplicate_agreement_override + score_candidate.
    Immutable — downstream consumers (hypothesis bank) compare and store these.
    """

    string: str  # Decoded plate string (e.g. "ABC1234")
    score: float  # Composite score = char_log_prob + lambda_len * len_log_prob
    char_dists: np.ndarray  # (candidate_length, n_allowed_chars) per-position distributions
    len_dist: np.ndarray  # (max_T,) pass-level length distribution
    candidate_length: int  # Number of characters in the decoded string
    overridden_positions: tuple[int, ...]  # Positions where duplicate agreement override fired


@dataclass
class HypothesisState:
    """Mutable hypothesis state for OCR aggregation hypothesis bank.

    NOT frozen — mutated during EMA updates. Internal to OcrAggregator;
    callers never see this directly.
    """

    string: str  # Current decoded plate string from EMA distributions
    ema_char_dists: np.ndarray  # (max_T, n_allowed_chars) EMA character distributions
    ema_len_dist: np.ndarray  # (max_T,) EMA length distribution
    win_count: int  # Number of passes this hypothesis was updated
    last_seen_pass: int  # Last pass number where this hypothesis was seen
    score: float  # Latest composite score
    candidate_length: int  # From argmax(ema_len_dist) + 1


@dataclass(frozen=True)
class EvidenceUnit:
    """Fused base/enhanced ROI evidence unit for OCR aggregation.

    Stores the per-position softmax distribution (fused when enhanced is present)
    plus raw per-position signals needed by downstream duplicate override:
    - agree_char_indices: agreed character identity per position
    - pair_min_p1: min(base_top1, enhanced_top1) per position
    """

    distribution: np.ndarray  # (max_T, vocab_size) fused prob dist
    quality: float  # [0,1] from base ROI
    agree_mask: np.ndarray  # (max_T,) bool per-position base/enhanced argmax agreement
    agree_char_indices: np.ndarray  # (max_T,) int — agreed char index, -1 where no agreement
    pair_min_p1: np.ndarray  # (max_T,) float — min(base_top1, enhanced_top1), 0.0 if no enhanced
    has_enhanced: bool
    group_id: str


@dataclass
class OcrQueueItem:
    """
    Item enqueued for OCR processing.
    """

    track_id: TrackId
    version: int
    batch_tensor: np.ndarray
    selected: list[RoiRichQuality]
    recipes: np.ndarray


class ConfidenceBucket(enum.Enum):
    """Confidence level for aggregation decision."""

    LOW = "LOW"
    MED = "MED"
    HIGH = "HIGH"


class ReasonCode(enum.Enum):
    """Reason for aggregation decision.

    Precedence (LOCKED): FINAL_* > TIEBREAK_CA_BIAS > TIEBREAK_DUP_AGREE > INTERIM_*.
    """

    INTERIM_AMBIGUOUS = "INTERIM_AMBIGUOUS"
    INTERIM_CONFUSION = "INTERIM_CONFUSION"
    FINAL_ONEPASS_AGREE = "FINAL_ONEPASS_AGREE"
    FINAL_MULTIPASS_STABLE = "FINAL_MULTIPASS_STABLE"
    TIEBREAK_DUP_AGREE = "TIEBREAK_DUP_AGREE"
    TIEBREAK_CA_BIAS = "TIEBREAK_CA_BIAS"


@dataclass(frozen=True)
class AggregationDebugPayload:
    """Debug payload for aggregation decision — 16 fields.

    Provides per-ROI and per-position diagnostics for tuning.
    """

    per_roi_decoded: list[str]
    per_roi_quality: list[float]
    per_roi_is_enhanced: list[bool]
    per_roi_group_id: list[str]
    per_position_top1: list[float]
    per_position_top2: list[float]
    per_position_margin: list[float]
    per_position_top_k_chars: list[list[str]]
    length_top2_lengths: tuple[int, int]
    length_top2_probs: tuple[float, float]
    quality_weights: list[float]
    overridden_positions: list[int]
    dup_agree_fired: bool
    ca_tiebreak_fired: bool
    h1_win_count: int
    h2_win_count: int | None


@dataclass(frozen=True)
class AggregationDecision:
    """Frozen output of the aggregation pipeline.

    Produced by OcrAggregator.aggregate_pass().
    """

    best_string: str
    confidence_bucket: ConfidenceBucket
    done: bool
    reason_code: ReasonCode
    h1_string: str
    h2_string: str | None
    pass_num: int
    debug_payload: AggregationDebugPayload | None
