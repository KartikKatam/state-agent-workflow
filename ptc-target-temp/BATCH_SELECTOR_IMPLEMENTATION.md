# Batch Selector Implementation Plan
## Optimal Selection Logic for LPR Consumer Pipeline

**Date**: 2025-12-29
**Target**: Implement tight-filter, diversity-aware, enhancement-aware batch selection
**Spec**: Up to 8 base images + up to 4 enhanced duplicates from bins of up to 32 ROIs

---

## Design Philosophy

### Core Principles
1. **"Only signal through"** - Strict eligibility gating, reject noise early
2. **"Return fewer if needed"** - Quality over quantity, no filler
3. **"Avoid carbon copies"** - Enforce pose/viewpoint diversity
4. **"Enhancement only when upside is likely"** - Risk-aware duplicate selection

### Input Contract
- **Source**: BinSnapshot with up to 32 ROIs (from Producer buffer)
- **Each ROI has**:
  - Rich quality metrics (30+ photometric/geometric metrics)
  - Eligibility flags (`top8_eligible`, `enhance_eligible`, `homography_eligible`)
  - Pose signature (11D diversity vector: normalized quad + skew + persp + plate_h)
  - Gradient histogram (25D from fast quality)
  - Fast quality score (producer ranking)

### Output Contract
- **Base batch**: 1-8 ROIs (diverse, high OCR-likelihood)
- **Enhanced duplicates**: 0-4 ROIs (references to base batch with high enhancement upside)
- **Metadata**: Explicit duplicate relationships via `group_id = base_roi_id`

---

## Algorithm Specification

### Step 0: Strict Eligibility Gating

**Purpose**: Filter out hopeless candidates before any ranking/selection.

```python
def apply_eligibility_gate(
    candidates: List[RoiRichQuality],
    cfg: ConsumerConfig
) -> List[RoiRichQuality]:
    """
    Hard filter: only keep ROIs that pass all viability checks.

    Base eligibility gates:
    1. top8_eligible == True (coarse viability from rich analyzer)
    2. plate_height_px >= cfg.plate_height_min_px (current: 40px)
    3. Optional: additional signal floor checks if needed

    Behavior:
    - If ZERO candidates pass → return empty list (downstream handles gracefully)
    - If 1-2 candidates pass → return them (batch will be small, but valid)
    - No "degraded mode" filling with bad quality
    """
    eligible = [
        roi for roi in candidates
        if roi.metrics.top8_eligible
        and roi.metrics.plate_height_px >= cfg.plate_height_min_px
    ]

    # Optional: add signal floor for tenengrad if needed
    # if cfg.batch_tenengrad_floor > 0:
    #     eligible = [roi for roi in eligible
    #                 if roi.metrics.tenengrad >= cfg.batch_tenengrad_floor]

    return eligible
```

**Configuration Parameters**:
```python
# In ConsumerConfig
plate_height_min_px: int = 40              # Hard minimum (US plates typically 80-120px)
batch_tenengrad_floor: float = 0.0         # Optional signal floor (0 = disabled)
batch_enable_signal_floors: bool = False   # Master switch for additional floors
```

---

### Step 1: Compute OCR-Likelihood Score (q)

**Purpose**: Rank eligible candidates by predicted OCR success.

```python
def compute_ocr_likelihood_score(roi: RoiRichQuality, cfg: ConsumerConfig) -> float:
    """
    Compute composite quality score for OCR likelihood.

    High-weight factors (predict OCR success):
    - Sharpness (tenengrad): Higher = better
    - Plate size (plate_height_px): Larger = more detail for OCR
    - Contrast (global_contrast, local_contrast): Higher = clearer characters
    - Exposure sanity (luminance_mean, clip fractions): Balanced = readable
    - Low noise (noise_std): Lower noise = cleaner text

    Low-weight factors (tie-breakers):
    - Detection confidence: YOLO confidence (already gated in producer)
    - Recency (frame_idx): Prefer newer frames slightly

    Normalization:
    - Use robust percentiles (p10-p90) within current bin
    - Prevents outliers from dominating
    - Each bin is self-normalized (adaptive to conditions)
    """
    m = roi.metrics

    # Sharpness component (high weight)
    # Already normalized by rich analyzer to [0-1] range via tenengrad thresholds
    sharpness_score = normalize_metric(
        m.tenengrad,
        cfg.tenengrad_floor,
        cfg.tenengrad_good,
        clip=True
    )

    # Size component (high weight)
    # Larger plates → more pixels for OCR
    size_score = normalize_metric(
        m.plate_height_px,
        cfg.plate_height_min_px,
        cfg.plate_height_ideal,  # New param: 100px for US plates
        clip=True
    )

    # Contrast component (high weight)
    # Average global and local contrast
    contrast_score = 0.5 * (
        normalize_metric(m.global_contrast, cfg.global_contrast_min, 100.0, clip=True) +
        normalize_metric(m.local_contrast, cfg.local_contrast_min, 30.0, clip=True)
    )

    # Exposure component (high weight)
    # Penalize over/under exposure and clipping
    exposure_score = 1.0
    if m.luminance_mean < cfg.luminance_mean_min or m.luminance_mean > cfg.luminance_mean_max:
        exposure_score *= 0.7
    if m.black_clip_fraction > cfg.black_clip_fraction_max:
        exposure_score *= (1.0 - m.black_clip_fraction)
    if m.white_clip_fraction > cfg.white_clip_fraction_max:
        exposure_score *= (1.0 - m.white_clip_fraction)

    # Noise component (high weight)
    # Lower noise = better
    noise_score = 1.0 - normalize_metric(
        m.noise_std,
        0.0,
        cfg.noise_std_max,
        clip=True
    )

    # Weighted composite (high-weight factors)
    q = (
        cfg.ocr_score_weight_sharpness * sharpness_score +
        cfg.ocr_score_weight_size * size_score +
        cfg.ocr_score_weight_contrast * contrast_score +
        cfg.ocr_score_weight_exposure * exposure_score +
        cfg.ocr_score_weight_noise * noise_score
    )

    # Tie-breakers (low weight, small contribution)
    detection_bonus = m.detection_confidence * cfg.ocr_score_weight_detection
    # Recency bonus: normalize frame_idx within bin (prefer recent slightly)
    # Note: frame_idx handled via bin-level normalization

    q_final = q + detection_bonus

    return float(np.clip(q_final, 0.0, 1.0))
```

**Configuration Parameters**:
```python
# In ConsumerConfig

# OCR likelihood score weights (must sum to ~1.0 for main components)
ocr_score_weight_sharpness: float = 0.35   # Tenengrad (critical for OCR)
ocr_score_weight_size: float = 0.25        # Plate height (more pixels = better)
ocr_score_weight_contrast: float = 0.20    # Character clarity
ocr_score_weight_exposure: float = 0.15    # Balanced lighting
ocr_score_weight_noise: float = 0.05       # Noise penalty
ocr_score_weight_detection: float = 0.02   # Tie-breaker (YOLO confidence)

# Ideal values for normalization
plate_height_ideal: int = 100              # Ideal plate height (US plates)
tenengrad_ideal: float = 5000.0            # Ideal sharpness (empirical)
```

---

### Step 2: Select Anchors (Quality First)

**Purpose**: Start with the top 2 highest-quality candidates as diversity anchors.

```python
def select_anchors(
    eligible: List[RoiRichQuality],
    cfg: ConsumerConfig
) -> List[RoiRichQuality]:
    """
    Select top-2 candidates by OCR-likelihood score as initial anchors.

    Anchors:
    - Guaranteed to be in final batch
    - Serve as diversity reference points
    - Ensure at least some high-quality images included

    Behavior:
    - If 0 eligible → return empty
    - If 1 eligible → return [best]
    - If 2+ eligible → return top-2 by q score
    """
    if len(eligible) == 0:
        return []

    # Sort by OCR-likelihood score (descending)
    ranked = sorted(
        eligible,
        key=lambda roi: compute_ocr_likelihood_score(roi, cfg),
        reverse=True
    )

    # Take top-2 (or fewer if bin is small)
    num_anchors = min(2, len(ranked))
    return ranked[:num_anchors]
```

---

### Step 3: Diversity Fill with Novelty Constraint

**Purpose**: Fill remaining batch slots (up to 8 total) with novel viewpoints.

```python
def compute_pose_distance(
    roi_a: RoiRichQuality,
    roi_b: RoiRichQuality,
    cfg: ConsumerConfig
) -> float:
    """
    Compute weighted pose distance for diversity enforcement.

    Distance components (scenario-driven weights):
    1. Skew (rotation): HIGH weight - rotation changes OCR difficulty significantly
    2. Quad coordinates: MEDIUM weight - but clamped to ignore tiny jitter
    3. Perspective: LOW weight - minor factor for diversity
    4. Scale (plate_height): LOW weight - already in quality score

    Uses pose_signature (11D vector):
    - [0:8]: normalized quad coords (x,y for TL,TR,BR,BL)
    - [8]: skew_degrees
    - [9]: perspective_score
    - [10]: plate_height_px

    Returns:
    - Distance in [0, inf), higher = more different
    - Apply minimum novelty threshold d_min to filter carbon copies
    """
    sig_a = roi_a.metrics.pose_signature
    sig_b = roi_b.metrics.pose_signature

    # Extract components
    quad_a = sig_a[:8]  # 8 normalized coords
    quad_b = sig_b[:8]
    skew_a = sig_a[8]
    skew_b = sig_b[8]
    persp_a = sig_a[9]
    persp_b = sig_b[9]
    height_a = sig_a[10]
    height_b = sig_b[10]

    # 1. Skew distance (HIGH weight)
    # Angular difference in degrees, normalized to [0, 1] (assume max 45° skew)
    skew_dist = abs(skew_a - skew_b) / 45.0

    # 2. Quad coordinate distance (MEDIUM weight, clamped for jitter)
    # L2 distance between normalized quad coords
    # Clamp small differences to 0 (ignore camera jitter < threshold)
    quad_l2 = np.linalg.norm(quad_a - quad_b)
    quad_dist = max(0.0, quad_l2 - cfg.diversity_quad_jitter_threshold)

    # 3. Perspective distance (LOW weight)
    # Difference in perspective severity
    persp_dist = abs(persp_a - persp_b) / 3.0  # Normalize (typical range 1.0-3.0)

    # 4. Scale distance (LOW weight)
    # Relative height difference (already in quality score, minor for diversity)
    scale_dist = abs(height_a - height_b) / max(height_a, height_b, 1.0)

    # Weighted distance
    d = (
        cfg.diversity_weight_skew * skew_dist +
        cfg.diversity_weight_quad * quad_dist +
        cfg.diversity_weight_persp * persp_dist +
        cfg.diversity_weight_scale * scale_dist
    )

    return float(d)


def diversity_fill(
    anchors: List[RoiRichQuality],
    remaining: List[RoiRichQuality],
    cfg: ConsumerConfig
) -> List[RoiRichQuality]:
    """
    Greedy fill to max_batch_size (8) with novelty constraint.

    Algorithm:
    1. Start with anchors as selected set
    2. Sort remaining candidates by OCR-likelihood (descending)
    3. For each candidate:
       a. Compute min distance to all selected ROIs
       b. If min_dist >= d_min → novel, add to batch
       c. If min_dist < d_min → carbon copy, skip
    4. Stop when batch size = 8 OR no more novel candidates

    Behavior:
    - Returns fewer than 8 if no novel candidates remain
    - No filling with non-novel images (strict diversity)
    """
    selected = list(anchors)  # Start with anchors

    # Sort remaining by quality (greedy: try best candidates first)
    ranked_remaining = sorted(
        remaining,
        key=lambda roi: compute_ocr_likelihood_score(roi, cfg),
        reverse=True
    )

    for candidate in ranked_remaining:
        # Check batch size limit
        if len(selected) >= cfg.max_batch_size:
            break

        # Compute minimum distance to all selected ROIs
        distances = [
            compute_pose_distance(candidate, sel, cfg)
            for sel in selected
        ]
        min_dist = min(distances) if distances else float('inf')

        # Novelty check: add only if sufficiently different
        if min_dist >= cfg.diversity_min_distance:
            selected.append(candidate)

    return selected
```

**Configuration Parameters**:
```python
# In ConsumerConfig

# Diversity distance weights (scenario-driven)
diversity_weight_skew: float = 0.50        # HIGH: rotation changes OCR difficulty
diversity_weight_quad: float = 0.30        # MEDIUM: viewpoint variation
diversity_weight_persp: float = 0.10       # LOW: perspective distortion
diversity_weight_scale: float = 0.10       # LOW: scale already in quality

# Diversity thresholds
diversity_min_distance: float = 0.15       # Minimum novelty to avoid carbon copies
diversity_quad_jitter_threshold: float = 0.05  # Ignore tiny quad coord jitter (normalized)
```

---

### Step 4: Enhancement Selection (Upside-Only Duplicates)

**Purpose**: Select up to 4 base batch ROIs for risky photometric enhancement.

```python
def compute_enhancement_upside_score(roi: RoiRichQuality, cfg: ConsumerConfig) -> float:
    """
    Compute enhancement upside score for duplicate selection.

    High upside when:
    1. Low contrast → CLAHE likely helps
    2. Mild under/over exposure → gamma adjustment helps
    3. Mild softness (not blur) → unsharp masking helps
    4. Moderate noise (not blur-dominated) → denoising helps

    Guardrails (prevent enhancement on bad candidates):
    - Must be enhance_eligible (viable + fixable + motivated)
    - Don't duplicate "already excellent" images (waste of compute)
    - Don't enhance severely blurred images (amplifies noise)
    - Don't enhance blown highlights (CLAHE creates halos)

    Returns:
    - Score in [0, 1], higher = more upside from enhancement
    """
    m = roi.metrics

    # Gate: must be enhance_eligible (pre-computed flag)
    if not m.enhance_eligible:
        return 0.0

    upside = 0.0

    # 1. Low contrast upside (CLAHE benefit)
    if m.low_contrast:
        # More severe contrast deficit → higher upside
        contrast_deficit = 1.0 - normalize_metric(
            m.global_contrast,
            0.0,
            cfg.global_contrast_min,
            clip=True
        )
        upside += cfg.enhance_upside_weight_contrast * contrast_deficit

    # 2. Exposure upside (gamma/histogram adjustment benefit)
    if m.exposure_bad:
        # Penalize extreme exposure (can't fix blown pixels)
        # Reward mild exposure issues
        if m.luminance_mean < cfg.luminance_mean_min:
            # Underexposed
            exposure_deficit = (cfg.luminance_mean_min - m.luminance_mean) / 50.0
            upside += cfg.enhance_upside_weight_exposure * min(exposure_deficit, 1.0)
        elif m.luminance_mean > cfg.luminance_mean_max:
            # Overexposed (only if not blown highlights)
            if m.white_clip_fraction <= cfg.white_clip_enhance_max:
                exposure_excess = (m.luminance_mean - cfg.luminance_mean_max) / 50.0
                upside += cfg.enhance_upside_weight_exposure * min(exposure_excess, 1.0)

    # 3. Mild softness upside (sharpening benefit)
    if m.mildly_soft and not m.very_blurry:
        # Sharpening helps mild softness, not severe blur
        soft_deficit = 1.0 - normalize_metric(
            m.tenengrad,
            cfg.tenengrad_floor,
            cfg.tenengrad_good,
            clip=True
        )
        upside += cfg.enhance_upside_weight_sharpness * soft_deficit

    # 4. Noise upside (denoising benefit)
    if m.noisy:
        # Only if not blur-dominated (denoising can blur further)
        if m.tenengrad >= cfg.tenengrad_floor:
            noise_level = normalize_metric(
                m.noise_std,
                0.0,
                cfg.noise_std_max,
                clip=True
            )
            upside += cfg.enhance_upside_weight_noise * noise_level

    # Guardrail: penalize "already excellent" images
    # If OCR-likelihood is very high, enhancement upside is low (not needed)
    q_score = compute_ocr_likelihood_score(roi, cfg)
    if q_score > cfg.enhance_excellence_threshold:
        excellence_penalty = (q_score - cfg.enhance_excellence_threshold) / 0.2
        upside *= (1.0 - min(excellence_penalty, 0.8))  # Max 80% penalty

    return float(np.clip(upside, 0.0, 1.0))


def select_enhancement_duplicates(
    base_batch: List[RoiRichQuality],
    cfg: ConsumerConfig
) -> List[RoiRichQuality]:
    """
    Select up to 4 ROIs from base batch for enhanced duplicates.

    Selection:
    1. Compute enhancement upside score for each base batch ROI
    2. Rank by upside (descending)
    3. Take top-4 with upside > 0

    Behavior:
    - If no ROIs have upside > 0 → return empty (no duplicates)
    - If only 2 ROIs have upside → return 2 duplicates (not 4)
    - Max 4 duplicates even if more have upside (compute budget)

    Output metadata:
    - Each duplicate has group_id = base_roi.roi_fq.roi.track_id + frame_idx
    - Allows OCR aggregator to link duplicates to base
    """
    if len(base_batch) == 0:
        return []

    # Compute upside scores
    candidates_with_upside = [
        (roi, compute_enhancement_upside_score(roi, cfg))
        for roi in base_batch
    ]

    # Filter: only keep ROIs with positive upside
    viable = [(roi, score) for roi, score in candidates_with_upside if score > 0.0]

    if len(viable) == 0:
        return []

    # Sort by upside (descending)
    viable.sort(key=lambda x: x[1], reverse=True)

    # Take top-4 (or fewer)
    num_duplicates = min(cfg.max_enhance_duplicates, len(viable))
    enhance_rois = [roi for roi, score in viable[:num_duplicates]]

    return enhance_rois
```

**Configuration Parameters**:
```python
# In ConsumerConfig

# Enhancement duplicate limits
max_enhance_duplicates: int = 4            # Max duplicates for full photometric

# Enhancement upside weights
enhance_upside_weight_contrast: float = 0.40    # CLAHE upside
enhance_upside_weight_exposure: float = 0.25    # Gamma adjustment upside
enhance_upside_weight_sharpness: float = 0.20   # Unsharp masking upside
enhance_upside_weight_noise: float = 0.15       # Denoising upside

# Enhancement guardrails
enhance_excellence_threshold: float = 0.85      # Don't enhance "already excellent" (OCR score)
```

---

### Step 5: Output Contract to OCR Block

**Purpose**: Package base batch + duplicates with explicit metadata for OCR aggregation.

```python
@dataclass
class EnhancedBatchSelection:
    """
    Extended batch selection with enhancement duplicates.

    Replaces current BatchSelection for enhanced pipeline.
    """
    track_id: TrackId
    version: int

    # Base batch: up to 8 ROIs (minimal-risk processing)
    base_rois: List[RoiRichQuality]

    # Enhancement duplicates: up to 4 ROIs (risky photometric processing)
    # These are references to ROIs in base_rois (not separate images)
    enhance_rois: List[RoiRichQuality]

    # Metadata for OCR aggregation
    duplicate_groups: Dict[str, List[int]]  # group_id → [base_idx, enhance_idx]

    def total_images_for_ocr(self) -> int:
        """Total images to send to OCR (base + duplicates)."""
        return len(self.base_rois) + len(self.enhance_rois)


def package_batch_output(
    track_id: TrackId,
    version: int,
    base_batch: List[RoiRichQuality],
    enhance_duplicates: List[RoiRichQuality],
) -> EnhancedBatchSelection:
    """
    Package base batch + duplicates with explicit duplicate relationships.

    Duplicate groups:
    - Key: group_id = f"{track_id}_{frame_idx}"
    - Value: [base_index, enhance_index] (indices into combined OCR batch)

    OCR batch order: [base_rois] + [enhance_rois]
    Indices: [0..N-1] for base, [N..N+M-1] for enhanced
    """
    duplicate_groups = {}

    # Build mapping: frame_idx → base_batch index
    frame_to_base_idx = {
        roi.metrics.frame_idx: idx
        for idx, roi in enumerate(base_batch)
    }

    # For each enhanced duplicate, link to base ROI
    for enhance_idx, enhance_roi in enumerate(enhance_duplicates):
        frame_idx = enhance_roi.metrics.frame_idx

        if frame_idx in frame_to_base_idx:
            base_idx = frame_to_base_idx[frame_idx]
            group_id = f"{track_id}_{frame_idx}"

            # OCR batch indices: base @ base_idx, enhanced @ len(base)+enhance_idx
            ocr_base_idx = base_idx
            ocr_enhance_idx = len(base_batch) + enhance_idx

            duplicate_groups[group_id] = [ocr_base_idx, ocr_enhance_idx]

    return EnhancedBatchSelection(
        track_id=track_id,
        version=version,
        base_rois=base_batch,
        enhance_rois=enhance_duplicates,
        duplicate_groups=duplicate_groups,
    )
```

---

### Step 6: Logging Hooks (Tuning Visibility)

**Purpose**: Log metrics for offline tuning of thresholds and weights.

```python
def log_batch_selection_metrics(
    track_id: TrackId,
    version: int,
    total_candidates: int,
    eligible_count: int,
    base_batch: List[RoiRichQuality],
    enhance_duplicates: List[RoiRichQuality],
    cfg: ConsumerConfig,
) -> None:
    """
    Log batch selection stats for tuning/debugging.

    Metrics to log:
    1. Eligibility funnel: total → eligible → selected
    2. Diversity enforcement: pairwise min distances among selected
    3. Enhancement selection: upside scores, which ROIs got duplicates + why
    4. Quality distribution: OCR-likelihood scores for selected vs rejected
    """
    # Eligibility funnel
    logger.info(
        f"Batch selection for {track_id} v{version}: "
        f"{total_candidates} total → {eligible_count} eligible → "
        f"{len(base_batch)} base + {len(enhance_duplicates)} enhanced"
    )

    # Diversity verification: compute pairwise distances in final batch
    if len(base_batch) > 1:
        distances = []
        for i, roi_a in enumerate(base_batch):
            for j, roi_b in enumerate(base_batch):
                if i < j:
                    d = compute_pose_distance(roi_a, roi_b, cfg)
                    distances.append(d)

        min_dist = min(distances) if distances else 0.0
        mean_dist = np.mean(distances) if distances else 0.0

        logger.debug(
            f"Diversity check: min_dist={min_dist:.3f}, mean_dist={mean_dist:.3f} "
            f"(threshold: {cfg.diversity_min_distance:.3f})"
        )

    # Enhancement selection reasons
    for roi in enhance_duplicates:
        upside = compute_enhancement_upside_score(roi, cfg)
        m = roi.metrics
        reasons = []
        if m.low_contrast:
            reasons.append("low_contrast")
        if m.exposure_bad:
            reasons.append("exposure_bad")
        if m.mildly_soft:
            reasons.append("mildly_soft")
        if m.noisy:
            reasons.append("noisy")

        logger.debug(
            f"Enhanced duplicate: frame {m.frame_idx}, upside={upside:.3f}, "
            f"reasons={reasons}"
        )
```

---

## Main Entry Point

```python
def select_batch_with_enhancement(
    track_id: TrackId,
    version: int,
    candidates: List[RoiRichQuality],
    cfg: ConsumerConfig,
) -> EnhancedBatchSelection:
    """
    Main batch selector: tight filter, diversity-aware, enhancement-aware.

    Pipeline:
    1. Strict eligibility gating (only signal through)
    2. Compute OCR-likelihood scores
    3. Select top-2 anchors (quality first)
    4. Diversity fill to 8 max (novelty constraint)
    5. Enhancement selection (upside-only duplicates, max 4)
    6. Package output with duplicate metadata
    7. Log metrics for tuning

    Returns:
    - EnhancedBatchSelection with base_rois + enhance_rois
    - Empty batches handled gracefully (OCR block skips)
    """
    total_candidates = len(candidates)

    # Step 0: Eligibility gating
    eligible = apply_eligibility_gate(candidates, cfg)
    eligible_count = len(eligible)

    if eligible_count == 0:
        # No viable candidates → return empty batch
        logger.warning(f"Track {track_id}: No eligible candidates (total: {total_candidates})")
        return EnhancedBatchSelection(
            track_id=track_id,
            version=version,
            base_rois=[],
            enhance_rois=[],
            duplicate_groups={},
        )

    # Step 1 & 2: Select anchors (top-2 by quality)
    anchors = select_anchors(eligible, cfg)

    # Step 3: Diversity fill (greedy with novelty constraint)
    remaining = [roi for roi in eligible if roi not in anchors]
    base_batch = diversity_fill(anchors, remaining, cfg)

    # Step 4: Enhancement selection (upside-only, max 4)
    enhance_duplicates = select_enhancement_duplicates(base_batch, cfg)

    # Step 5: Package output
    output = package_batch_output(track_id, version, base_batch, enhance_duplicates)

    # Step 6: Logging
    if cfg.enable_batch_selection_logging:
        log_batch_selection_metrics(
            track_id, version, total_candidates, eligible_count,
            base_batch, enhance_duplicates, cfg
        )

    return output
```

---

## Configuration Summary

**New ConsumerConfig parameters (all with defaults)**:

```python
@dataclass
class ConsumerConfig:
    # ... existing params ...

    # ===== Batch selection (eligibility) =====
    batch_tenengrad_floor: float = 0.0              # Optional signal floor
    batch_enable_signal_floors: bool = False        # Master switch

    # ===== OCR-likelihood scoring =====
    ocr_score_weight_sharpness: float = 0.35        # Tenengrad weight
    ocr_score_weight_size: float = 0.25             # Plate height weight
    ocr_score_weight_contrast: float = 0.20         # Contrast weight
    ocr_score_weight_exposure: float = 0.15         # Exposure weight
    ocr_score_weight_noise: float = 0.05            # Noise penalty weight
    ocr_score_weight_detection: float = 0.02        # Detection confidence tie-breaker

    plate_height_ideal: int = 100                   # Ideal plate height (normalization)
    tenengrad_ideal: float = 5000.0                 # Ideal sharpness (normalization)

    # ===== Diversity enforcement =====
    diversity_weight_skew: float = 0.50             # Rotation weight (HIGH)
    diversity_weight_quad: float = 0.30             # Quad coords weight (MEDIUM)
    diversity_weight_persp: float = 0.10            # Perspective weight (LOW)
    diversity_weight_scale: float = 0.10            # Scale weight (LOW)

    diversity_min_distance: float = 0.15            # Minimum novelty threshold
    diversity_quad_jitter_threshold: float = 0.05   # Jitter tolerance

    # ===== Enhancement duplicate selection =====
    max_enhance_duplicates: int = 4                 # Max enhanced duplicates

    enhance_upside_weight_contrast: float = 0.40    # CLAHE upside
    enhance_upside_weight_exposure: float = 0.25    # Gamma upside
    enhance_upside_weight_sharpness: float = 0.20   # Sharpening upside
    enhance_upside_weight_noise: float = 0.15       # Denoising upside

    enhance_excellence_threshold: float = 0.85      # Don't enhance "excellent"

    # ===== Logging =====
    enable_batch_selection_logging: bool = True     # Enable detailed logs
```

---

## Testing & Tuning Strategy

### Phase 1: Eligibility Gating Validation
1. Log eligibility funnel for 100+ tracks
2. Verify rejection reasons align with visual inspection
3. Adjust `plate_height_min_px` if too aggressive/lenient

### Phase 2: OCR-Likelihood Score Calibration
1. Compare selected batches to manual ground truth (best 8 from bin)
2. Tune weights via ablation study (vary one at a time)
3. Validate score distribution (should span [0.3, 1.0] for good bins)

### Phase 3: Diversity Enforcement Tuning
1. Compute pairwise distances in selected batches
2. Visualize selected images side-by-side
3. Adjust `diversity_min_distance` if carbon copies persist
4. Tune `diversity_quad_jitter_threshold` to ignore camera shake

### Phase 4: Enhancement Selection Optimization
1. Track OCR confidence improvement: enhanced vs base
2. Measure cases where enhanced version < base (enhancement backfired)
3. Adjust upside weights to maximize enhancement success rate
4. Consider lowering `max_enhance_duplicates` if compute-bound

### Phase 5: End-to-End OCR Performance
1. A/B test: old batch selector vs new (same OCR/aggregation)
2. Metrics: final OCR accuracy, confidence, batch size distribution
3. Iterate on thresholds based on production performance

---

## Implementation Checklist

- [ ] Add new config parameters to `ConsumerConfig`
- [ ] Implement `apply_eligibility_gate()`
- [ ] Implement `compute_ocr_likelihood_score()`
- [ ] Implement `select_anchors()`
- [ ] Implement `compute_pose_distance()`
- [ ] Implement `diversity_fill()`
- [ ] Implement `compute_enhancement_upside_score()`
- [ ] Implement `select_enhancement_duplicates()`
- [ ] Define `EnhancedBatchSelection` dataclass in `consumer/models.py`
- [ ] Implement `package_batch_output()`
- [ ] Implement `log_batch_selection_metrics()`
- [ ] Implement `select_batch_with_enhancement()` main entry point
- [ ] Write unit tests for each step
- [ ] Integration test with real Producer snapshots
- [ ] Update `consumer/ops_batch_select.py` to use new logic
- [ ] Update downstream (recipe, preprocessor) to handle enhanced duplicates

---

## References

- **[Plan_02.txt](Plan_02.txt)**: Lines 96-100 (batch + duplicate system design)
- **[consumer/ops_quality_rich.py](consumer/ops_quality_rich.py)**: RichQualityMetrics specification
- **[consumer/models.py](consumer/models.py)**: Current BatchSelection (to be extended)
- **[consumer/config.py](consumer/config.py)**: Existing thresholds and params
