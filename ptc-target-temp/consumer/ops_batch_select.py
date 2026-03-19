"""Batch selection pipeline: eligibility gating, OCR-likelihood scoring, and selection.

Chunk-01: normalize_metric, apply_eligibility_gate, compute_ocr_likelihood_score.
Chunk-02: select_anchors, compute_pose_distance, diversity_fill.
Chunk-03: compute_enhancement_upside_score, select_enhancement_duplicates.
Chunk-04: package_batch_output.
Chunk-05: log_batch_selection_metrics.
Chunk-06: select_batch_with_enhancement (entry point).
"""

from __future__ import annotations

import logging

from producer.models import TrackId

from .config import ConsumerConfig
from .models import EnhancedBatchSelection, RoiRichQuality

logger = logging.getLogger(__name__)


def normalize_metric(value: float, low: float, high: float) -> float:
    """Normalize *value* into [0, 1] using the range [low, high].

    Returns 0.5 when *high* <= *low* (degenerate / inverted range).
    Result is clamped to [0.0, 1.0].
    """
    if high <= low:
        return 0.5
    raw = (value - low) / (high - low)
    return max(0.0, min(1.0, raw))


def apply_eligibility_gate(
    candidates: list[RoiRichQuality],
    cfg: ConsumerConfig,
) -> list[RoiRichQuality]:
    """Filter candidates by hard eligibility criteria.

    A candidate passes only if ALL conditions hold:
    1. ``top8_eligible`` is True
    2. ``plate_height_px >= cfg.plate_height_min_px``
    3. If signal floors enabled and tenengrad floor > 0:
       ``tenengrad >= cfg.batch_tenengrad_floor``

    Returns an empty list when no candidates pass.
    """
    eligible: list[RoiRichQuality] = []
    for c in candidates:
        m = c.metrics
        if not m.top8_eligible:
            continue
        if m.plate_height_px < cfg.plate_height_min_px:
            continue
        if (
            cfg.batch_enable_signal_floors
            and cfg.batch_tenengrad_floor > 0
            and m.tenengrad < cfg.batch_tenengrad_floor
        ):
            continue
        eligible.append(c)

    logger.debug(
        "eligibility gate: %d / %d candidates passed",
        len(eligible),
        len(candidates),
    )
    return eligible


def compute_ocr_likelihood_score(
    roi: RoiRichQuality,
    eligible_list: list[RoiRichQuality],
    cfg: ConsumerConfig,
) -> float:
    """Compute weighted OCR-likelihood score for *roi* in [0, 1].

    Normalization strategy:
    - Sharpness (tenengrad) and size (plate_height_px): per-bin percentile
      (p10/p90 of the eligible list). Falls back to config ideals when
      ``len(eligible_list) < 2``.
    - Contrast, exposure, noise: fixed thresholds.
    - Detection confidence: raw value clamped [0, 1].
    """
    m = roi.metrics

    # --- Per-bin percentile bounds (sharpness and size) ---
    if len(eligible_list) >= 2:
        tenengr_vals = [r.metrics.tenengrad for r in eligible_list]
        tenengr_vals_sorted = sorted(tenengr_vals)
        n = len(tenengr_vals_sorted)
        p10_sharp = tenengr_vals_sorted[max(0, int(n * 0.1))]
        p90_sharp = tenengr_vals_sorted[min(n - 1, int(n * 0.9))]

        height_vals = [r.metrics.plate_height_px for r in eligible_list]
        height_vals_sorted = sorted(height_vals)
        p10_size = height_vals_sorted[max(0, int(n * 0.1))]
        p90_size = height_vals_sorted[min(n - 1, int(n * 0.9))]
    else:
        # Fallback to config ideals when too few items for percentiles
        p10_sharp = 0.0
        p90_sharp = cfg.tenengrad_ideal
        p10_size = float(cfg.plate_height_min_px)
        p90_size = float(cfg.plate_height_ideal)

    # --- Normalize 6 components ---
    sharpness_norm = normalize_metric(m.tenengrad, p10_sharp, p90_sharp)
    size_norm = normalize_metric(m.plate_height_px, p10_size, p90_size)
    contrast_norm = normalize_metric(m.global_contrast, 0, 100)
    exposure_norm = 1.0 - normalize_metric(abs(m.luminance_mean - 128), 0, 128)
    noise_norm = 1.0 - normalize_metric(m.noise_std, 0, cfg.noise_std_max)
    detection_norm = max(0.0, min(1.0, m.detection_confidence))

    # --- Weighted sum ---
    score = (
        cfg.ocr_score_weight_sharpness * sharpness_norm
        + cfg.ocr_score_weight_size * size_norm
        + cfg.ocr_score_weight_contrast * contrast_norm
        + cfg.ocr_score_weight_exposure * exposure_norm
        + cfg.ocr_score_weight_noise * noise_norm
        + cfg.ocr_score_weight_detection * detection_norm
    )

    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Chunk-02: Anchor selection, pose distance, diversity fill
# ---------------------------------------------------------------------------


def select_anchors(
    eligible: list[RoiRichQuality],
    ocr_scores: dict[int, float],
    cfg: ConsumerConfig,
) -> list[RoiRichQuality]:
    """Select top-2 anchor ROIs by OCR-likelihood score with pose leniency.

    *ocr_scores* maps ``id(roi)`` to its score.  Anchor-1 is always the
    highest scorer.  Anchor-2 is the first remaining candidate (in score
    order) whose pose distance from anchor-1 is
    ``>= cfg.anchor_min_pose_distance``.  Falls back to the next-highest
    scorer when no candidate meets the threshold.

    Returns at most 2 anchors (1 if only 1 eligible, empty if none).
    """
    if not eligible:
        return []
    ranked = sorted(eligible, key=lambda r: ocr_scores.get(id(r), 0.0), reverse=True)
    anchor1 = ranked[0]
    if len(ranked) < 2:
        return [anchor1]

    # Scan for a diverse anchor-2
    for candidate in ranked[1:]:
        if compute_pose_distance(anchor1, candidate, cfg) >= cfg.anchor_min_pose_distance:
            return [anchor1, candidate]

    # Fallback: next-highest score
    logger.debug(
        "anchor2 fallback: no candidate meets anchor_min_pose_distance=%.2f",
        cfg.anchor_min_pose_distance,
    )
    return [anchor1, ranked[1]]


def compute_pose_distance(
    a: RoiRichQuality,
    b: RoiRichQuality,
    cfg: ConsumerConfig,
) -> float:
    """Weighted pose distance between two ROIs in [0, 1].

    Four components, each clamped [0, 1]:
    * **skew** (default 50 %): ``abs(skew_a - skew_b) / 90``
    * **quad** (default 30 %): mean L2 of normalised corners with jitter
      clamp, or ``abs(bbox_center_x)`` fallback when keypoints are ``None``
    * **perspective** (default 10 %): ``abs(persp_dir_a - persp_dir_b) / 2``
    * **scale** (default 10 %): ``abs(height_a - height_b) / plate_height_ideal``
    """
    m_a, m_b = a.metrics, b.metrics

    # --- Skew component ---
    skew_a = m_a.skew_degrees if m_a.skew_degrees is not None else 0.0
    skew_b = m_b.skew_degrees if m_b.skew_degrees is not None else 0.0
    skew_diff = min(1.0, abs(skew_a - skew_b) / 90.0)

    # --- Quad component (keypoints or bbox fallback) ---
    if m_a.keypoints is not None and m_b.keypoints is not None:
        total_l2 = 0.0
        for (ax, ay), (bx, by) in zip(m_a.keypoints, m_b.keypoints, strict=True):
            nx_a = ax / m_a.plate_width_px if m_a.plate_width_px else 0.0
            ny_a = ay / m_a.plate_height_px if m_a.plate_height_px else 0.0
            nx_b = bx / m_b.plate_width_px if m_b.plate_width_px else 0.0
            ny_b = by / m_b.plate_height_px if m_b.plate_height_px else 0.0
            total_l2 += ((nx_a - nx_b) ** 2 + (ny_a - ny_b) ** 2) ** 0.5
        quad_raw = total_l2 / len(m_a.keypoints)
        if quad_raw < cfg.diversity_quad_jitter_threshold:
            quad_raw = 0.0
        quad_diff = min(1.0, quad_raw)
    else:
        quad_diff = abs(m_a.bbox_center_x_normalized - m_b.bbox_center_x_normalized)

    # --- Perspective component ---
    persp_a = m_a.perspective_direction if m_a.perspective_direction is not None else 0.0
    persp_b = m_b.perspective_direction if m_b.perspective_direction is not None else 0.0
    persp_diff = min(1.0, abs(persp_a - persp_b) / 2.0)

    # --- Scale component ---
    scale_diff = min(
        1.0,
        abs(m_a.plate_height_px - m_b.plate_height_px) / cfg.plate_height_ideal,
    )

    # --- Weighted sum ---
    distance = (
        cfg.diversity_weight_skew * skew_diff
        + cfg.diversity_weight_quad * quad_diff
        + cfg.diversity_weight_persp * persp_diff
        + cfg.diversity_weight_scale * scale_diff
    )

    logger.debug(
        "pose_distance: skew=%.3f quad=%.3f persp=%.3f scale=%.3f -> %.3f",
        skew_diff,
        quad_diff,
        persp_diff,
        scale_diff,
        distance,
    )
    return distance


def diversity_fill(
    anchors: list[RoiRichQuality],
    remaining: list[RoiRichQuality],
    ocr_scores: dict[int, float],
    cfg: ConsumerConfig,
) -> list[RoiRichQuality]:
    """Greedily fill a diverse batch starting from *anchors*.

    *remaining* candidates are tried in descending OCR-score order.
    A candidate is added only when its minimum pose distance to every
    already-selected ROI is ``>= cfg.diversity_min_distance``.
    Stops at ``cfg.max_batch_size``.
    """
    selected = list(anchors)
    if len(selected) >= cfg.max_batch_size:
        return selected[: cfg.max_batch_size]

    ranked = sorted(remaining, key=lambda r: ocr_scores.get(id(r), 0.0), reverse=True)

    for candidate in ranked:
        if len(selected) >= cfg.max_batch_size:
            break
        # Check min distance to all already-selected
        min_dist = (
            min(compute_pose_distance(candidate, s, cfg) for s in selected)
            if selected
            else float("inf")
        )
        if min_dist >= cfg.diversity_min_distance:
            selected.append(candidate)
            logger.debug(
                "diversity_fill: added candidate (min_dist=%.3f, batch=%d/%d)",
                min_dist,
                len(selected),
                cfg.max_batch_size,
            )

    logger.debug(
        "diversity_fill: %d/%d selected (%d anchors + %d filled)",
        len(selected),
        cfg.max_batch_size,
        len(anchors),
        len(selected) - len(anchors),
    )
    return selected


# ---------------------------------------------------------------------------
# Chunk-03: Enhancement duplicate selection
# ---------------------------------------------------------------------------


def compute_enhancement_upside_score(
    roi: RoiRichQuality,
    ocr_score: float,
    cfg: ConsumerConfig,
) -> float:
    """Compute enhancement upside score for *roi* in [0, 1].

    Returns 0.0 when guardrails trigger (excellent OCR, very blurry,
    or blown highlights). Otherwise sums deficiency weights for
    fixable issues: low contrast, bad exposure, mildly soft, noisy.
    """
    m = roi.metrics

    # Guardrails — enhancement won't help these cases
    if ocr_score > cfg.enhance_excellence_threshold:
        logger.debug(
            "upside=0: ocr_score=%.2f > threshold=%.2f", ocr_score, cfg.enhance_excellence_threshold
        )
        return 0.0
    if m.very_blurry:
        logger.debug("upside=0: very_blurry")
        return 0.0
    if m.white_clip_fraction > cfg.white_clip_hard_max:
        logger.debug(
            "upside=0: white_clip=%.2f > hard_max=%.2f",
            m.white_clip_fraction,
            cfg.white_clip_hard_max,
        )
        return 0.0

    # Sum triggered deficiency weights
    upside = 0.0
    if m.low_contrast:
        upside += cfg.enhance_upside_weight_contrast
    if m.exposure_bad:
        upside += cfg.enhance_upside_weight_exposure
    if m.mildly_soft:
        upside += cfg.enhance_upside_weight_sharpness
    if m.noisy:
        upside += cfg.enhance_upside_weight_noise

    logger.debug(
        "upside=%.2f: low_contrast=%s exposure_bad=%s mildly_soft=%s noisy=%s",
        upside,
        m.low_contrast,
        m.exposure_bad,
        m.mildly_soft,
        m.noisy,
    )
    return upside


def select_enhancement_duplicates(
    base_batch: list[RoiRichQuality],
    ocr_scores: dict[int, float],
    cfg: ConsumerConfig,
) -> list[RoiRichQuality]:
    """Select up to *max_enhance_duplicates* ROIs from *base_batch* for enhancement.

    Only ROIs with ``enhance_eligible=True`` and positive upside score
    are considered. Results are sorted by upside score descending.
    """
    candidates: list[tuple[float, RoiRichQuality]] = []
    for roi in base_batch:
        if not roi.metrics.enhance_eligible:
            continue
        upside = compute_enhancement_upside_score(roi, ocr_scores.get(id(roi), 0.0), cfg)
        if upside > 0:
            candidates.append((upside, roi))

    # Sort by upside descending
    candidates.sort(key=lambda x: x[0], reverse=True)

    selected = [roi for _, roi in candidates[: cfg.max_enhance_duplicates]]

    logger.debug(
        "enhancement: %d eligible, %d with positive upside, %d selected (max=%d)",
        sum(1 for r in base_batch if r.metrics.enhance_eligible),
        len(candidates),
        len(selected),
        cfg.max_enhance_duplicates,
    )
    return selected


# ---------------------------------------------------------------------------
# Chunk-04: Output packaging with duplicate groups
# ---------------------------------------------------------------------------


def package_batch_output(
    track_id: TrackId,
    version: int,
    base_rois: list[RoiRichQuality],
    enhance_rois: list[RoiRichQuality],
) -> EnhancedBatchSelection:
    """Package base and enhanced ROIs into :class:`EnhancedBatchSelection`.

    For each enhanced ROI, finds the matching base ROI by identity (``id()``)
    and creates a duplicate group linking ``[base_index, enhance_index]``.
    Enhance indices start at ``len(base_rois)``.
    """
    base_id_to_idx = {id(roi): idx for idx, roi in enumerate(base_rois)}

    duplicate_groups: dict[str, list[int]] = {}
    for i, enh_roi in enumerate(enhance_rois):
        base_idx = base_id_to_idx.get(id(enh_roi))
        if base_idx is not None:
            enh_idx = len(base_rois) + i
            duplicate_groups[f"group-{i}"] = [base_idx, enh_idx]
        else:
            logger.warning("enhance ROI %d has no matching base ROI — orphan skipped", i)

    logger.debug(
        "packaging: %d base, %d enhanced, %d groups",
        len(base_rois),
        len(enhance_rois),
        len(duplicate_groups),
    )

    return EnhancedBatchSelection(
        track_id=track_id,
        version=version,
        base_rois=base_rois,
        enhance_rois=enhance_rois,
        duplicate_groups=duplicate_groups,
    )


# ---------------------------------------------------------------------------
# Chunk-05: Selection logging
# ---------------------------------------------------------------------------


def log_batch_selection_metrics(
    *,
    total_count: int,
    eligible_count: int,
    base_rois: list[RoiRichQuality],
    enhance_rois: list[RoiRichQuality],
    diversity_distances: list[float],
    enhancement_upsides: dict[int, float],
    track_id: TrackId,
    version: int,
    cfg: ConsumerConfig,
) -> None:
    """Log selection funnel, diversity stats, and enhancement decisions.

    No-op when ``cfg.enable_batch_selection_logging`` is False.
    Uses stdlib logging at INFO level with lazy formatting.
    """
    if not cfg.enable_batch_selection_logging:
        return

    logger.info(
        "batch_select [%s v%d] funnel: %d total -> %d eligible -> %d base, %d enhanced",
        track_id,
        version,
        total_count,
        eligible_count,
        len(base_rois),
        len(enhance_rois),
    )

    if diversity_distances:
        d_min = min(diversity_distances)
        d_max = max(diversity_distances)
        d_mean = sum(diversity_distances) / len(diversity_distances)
        logger.info(
            "batch_select [%s v%d] diversity: min=%.3f mean=%.3f max=%.3f (n=%d)",
            track_id,
            version,
            d_min,
            d_mean,
            d_max,
            len(diversity_distances),
        )

    if enhance_rois:
        for roi in enhance_rois:
            upside = enhancement_upsides.get(id(roi), 0.0)
            logger.info(
                "batch_select [%s v%d] enhancement: roi frame=%d upside=%.2f",
                track_id,
                version,
                roi.metrics.frame_idx,
                upside,
            )


# ---------------------------------------------------------------------------
# Chunk-06: Entry point
# ---------------------------------------------------------------------------


def select_batch_with_enhancement(
    track_id: TrackId,
    version: int,
    candidates: list[RoiRichQuality],
    cfg: ConsumerConfig,
) -> EnhancedBatchSelection:
    """Select a diverse batch of ROIs with optional enhancement duplicates.

    Orchestrates the full pipeline:
    gate → score → anchor → diversity → enhance → package → log.

    Returns an empty :class:`EnhancedBatchSelection` when no candidates
    pass the eligibility gate.
    """
    # 1. Eligibility gate
    eligible = apply_eligibility_gate(candidates, cfg)

    # 2. Early return if nothing eligible
    if not eligible:
        logger.info(
            "batch_select [%s v%d] no eligible candidates (%d total)",
            track_id,
            version,
            len(candidates),
        )
        return EnhancedBatchSelection(
            track_id=track_id,
            version=version,
            base_rois=[],
            enhance_rois=[],
            duplicate_groups={},
        )

    # 3. OCR-likelihood scoring
    ocr_scores = {id(roi): compute_ocr_likelihood_score(roi, eligible, cfg) for roi in eligible}

    # 4. Anchor selection
    anchors = select_anchors(eligible, ocr_scores, cfg)

    # 5. Diversity fill
    anchor_ids = {id(a) for a in anchors}
    remaining = [r for r in eligible if id(r) not in anchor_ids]
    base_batch = diversity_fill(anchors, remaining, ocr_scores, cfg)

    # 6. Enhancement duplicate selection
    enhance_dupes = select_enhancement_duplicates(base_batch, ocr_scores, cfg)

    # 7. Package output
    result = package_batch_output(track_id, version, base_batch, enhance_dupes)

    # 8. Compute logging metrics and log
    diversity_distances: list[float] = []
    for roi in base_batch:
        if id(roi) in anchor_ids:
            continue
        min_dist = min(
            compute_pose_distance(roi, other, cfg) for other in base_batch if other is not roi
        )
        diversity_distances.append(min_dist)

    enhancement_upsides = {
        id(roi): compute_enhancement_upside_score(roi, ocr_scores.get(id(roi), 0.0), cfg)
        for roi in enhance_dupes
    }

    log_batch_selection_metrics(
        total_count=len(candidates),
        eligible_count=len(eligible),
        base_rois=base_batch,
        enhance_rois=enhance_dupes,
        diversity_distances=diversity_distances,
        enhancement_upsides=enhancement_upsides,
        track_id=track_id,
        version=version,
        cfg=cfg,
    )

    return result
