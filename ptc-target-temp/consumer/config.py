from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ConsumerConfig:
    """
    Tunable parameters for the Consumer block (analysis, batch selection, recipes, preprocessing, OCR queue).
    """

    # ===== Batch selection =====
    max_batch_size: int = 8

    # ===== OCR-likelihood scoring =====
    ocr_score_weight_sharpness: float = 0.35
    ocr_score_weight_size: float = 0.25
    ocr_score_weight_contrast: float = 0.20
    ocr_score_weight_exposure: float = 0.15
    ocr_score_weight_noise: float = 0.05
    ocr_score_weight_detection: float = 0.02
    plate_height_ideal: int = 100
    tenengrad_ideal: float = 5000.0
    batch_tenengrad_floor: float = 0.0
    batch_enable_signal_floors: bool = False

    # ===== Enhancement duplicates =====
    max_enhance_duplicates: int = 4
    enhance_upside_weight_contrast: float = 0.40
    enhance_upside_weight_exposure: float = 0.25
    enhance_upside_weight_sharpness: float = 0.20
    enhance_upside_weight_noise: float = 0.15
    enhance_excellence_threshold: float = 0.85

    # ===== Selection logging =====
    enable_batch_selection_logging: bool = True

    # ===== OCR commit gate =====
    commit_margin: float = 0.04  # Min score improvement to supersede
    enable_staleness: bool = True  # Enable staleness backstop
    max_staleness_ms: int = 1200  # ms before commit is considered stale
    stale_min_score: float = 0.55  # Min score for stale backstop acceptance

    # ===== Diversity (pose-distance) =====
    diversity_weight_skew: float = 0.50
    diversity_weight_quad: float = 0.30
    diversity_weight_persp: float = 0.10
    diversity_weight_scale: float = 0.10
    diversity_min_distance: float = 0.15
    diversity_quad_jitter_threshold: float = 0.05
    anchor_min_pose_distance: float = 0.07

    # ===== Rich quality analysis =====
    rich_quality_enabled: bool = True

    # Canonical crop size for photometric metrics (letterboxed to preserve AR)
    # IMPORTANT: Width must be divisible by 8, height by 4 (for 4×8 local contrast grid)
    canonical_crop_width: int = 256  # Must be multiple of 8
    canonical_crop_height: int = 128  # Must be multiple of 4

    # ===== Hard viability checks =====

    # Plate size constraints (pixels)
    plate_height_min_px: int = (
        40  # Below this, OCR extremely unlikely (US plates: ~100px recommended)
    )
    crop_clip_fraction_max: float = 0.25  # Max fraction of padded bbox allowed to be clipped

    # ===== Keypoint ordering and homography sanity =====

    # Keypoint confidence thresholds
    keypoint_confidence_min_threshold: float = (
        0.5  # Minimum per-keypoint confidence for homography eligibility
    )

    # Quad geometry sanity checks
    quad_area_min_px: float = 1000.0  # Minimum quad area (prevents degenerate quads)
    edge_ratio_max: float = 6.0  # max(edges)/min(edges) - catches keypoint collapse/mis-detection

    # Aspect ratio plausibility (US plates ~2:1, allow broad range)
    aspect_ratio_min: float = 1.3  # Minimum width/height ratio (accounts for perspective)
    aspect_ratio_max: float = 4.0  # Maximum width/height ratio (catches extreme distortion)

    # ===== Photometric quality thresholds =====

    # Exposure (LAB L channel, range [0-255])
    luminance_mean_min: float = 40.0  # Below this: severely underexposed
    luminance_mean_max: float = 220.0  # Above this: severely overexposed
    black_clip_fraction_max: float = 0.15  # Max fraction of near-black pixels
    white_clip_fraction_max: float = 0.15  # Max fraction of near-white pixels (exposure_bad gate)
    white_clip_hard_max: float = 0.30  # Absolute max for top8_eligible (blown highlights)
    white_clip_enhance_max: float = (
        0.10  # Max for enhance_eligible (enhancement won't help blown pixels)
    )

    # Luminance clipping thresholds (L channel values)
    luminance_black_threshold: int = 5  # Pixels <= this are "black clipped"
    luminance_white_threshold: int = 250  # Pixels >= this are "white clipped"

    # Contrast thresholds
    global_contrast_min: float = 30.0  # p90-p10 of luminance (low contrast flag)
    local_contrast_min: float = 8.0  # Mean tile stddev over 4×8 grid (low contrast flag)
    local_contrast_min_pixels_per_tile: int = 10  # Min real pixels per tile to compute std

    # Sharpness (Tenengrad - Sobel energy in raw space)
    # Raw tenengrad = mean(∇²) over pixels (excluding padding boundary)
    # Scale depends on canonical size, interpolation method, and plate content
    # IMPORTANT: Tune these thresholds from observed distributions in your dataset
    # Starting values below are rough estimates - expect to adjust based on real data
    tenengrad_floor: float = 500.0  # Below this: very_blurry (hopeless for sharpening)
    tenengrad_good: float = (
        3000.0  # Above this: sharp enough (mildly_soft if between floor and good)
    )

    # Noise threshold (std of high-frequency residual)
    noise_std_max: float = 12.0  # Above this: noisy flag set

    # ===== Directional sharpness thresholds (motion blur detection) =====

    # Horizontal sharpness floor (detects vertical edges - critical for OCR)
    # Destroyed by horizontal motion blur, which is worst case for character discrimination (B/8, D/0, 1/I)
    # IMPORTANT: Tune from observed distributions - this is a strict gate
    tenengrad_h_floor: float = (
        500.0  # Below this: vertical_edges_weak (gate out horizontal motion blur)
    )

    # Vertical sharpness floor (detects horizontal edges - less critical for OCR)
    # Set arbitrarily low (effectively disabled) because vertical motion blur preserves vertical edges
    # Kept as tunable parameter for future experimentation
    # NOTE: Horizontal edges are less critical for character discrimination
    tenengrad_v_floor: float = 50.0  # Below this: horizontal_edges_weak (logged only, not gated)

    # ===== Robust noise estimation =====

    # Flat region detection for noise estimation
    # Flat regions are identified by low local variance (minimal texture/edges)
    flat_region_var_threshold: float = 100.0  # Local variance threshold for flat detection
    flat_region_min_pixels: int = 100  # Minimum pixels required in flat regions for MAD estimate

    # ===== Recipe planner: target parameters =====
    target_luma: float = 128.0  # Target luminance mean [0-255]
    target_contrast: float = 60.0  # Target global contrast (p90-p10)
    target_sharpness: float = 3000.0  # Target tenengrad value
    target_noise_max: float = 12.0  # Max acceptable noise_std
    luma_range: float = 255.0  # Deterministic luma range constant

    # ===== Recipe planner: mapping gain coefficients =====
    k_gain: float = 1.0  # Exposure gain sensitivity
    k_gamma: float = 1.0  # Gamma correction sensitivity
    k_contrast: float = 1.0  # Contrast blend sensitivity
    k_denoise: float = 1.0  # Denoise strength sensitivity
    k_sharpen: float = 1.0  # Sharpen strength sensitivity

    # ===== Recipe planner: clamp ranges =====
    gain_min: float = 0.75  # Min gain multiplier
    gain_max: float = 1.25  # Max gain multiplier
    gamma_min: float = 0.70  # Min gamma exponent
    gamma_max: float = 1.50  # Max gamma exponent
    contrast_max: float = 0.40  # Max contrast blend strength
    denoise_max: float = 0.40  # Max denoise blend strength
    sharpen_max: float = 0.35  # Max sharpen blend strength

    # ===== Recipe planner: CLAHE parameters =====
    clahe_blend_max: float = 1.0  # Max CLAHE blend strength
    clahe_clip_min: float = 1.5  # Min CLAHE clip limit
    clahe_clip_max: float = 4.0  # Max CLAHE clip limit
    clahe_tiles_min: int = 6  # Min CLAHE grid tiles
    clahe_tiles_max: int = 12  # Max CLAHE grid tiles
    clahe_max_white_clip: float = 0.10  # Max white_clip_fraction for CLAHE gate
    clahe_min_sharpness: float = 500.0  # Min tenengrad for CLAHE gate

    # ===== Recipe planner: geometry =====
    warp_margin: float = 0.04  # Fractional margin for homography warp
    recipe_output_width: int = 128  # PARSeq input width
    recipe_output_height: int = 32  # PARSeq input height

    # ===== Recipe planner: photometric =====
    contrast_steepness: float = 10.0  # Sigmoid steepness for contrast S-curve

    # ===== OCR aggregation: quality weights =====

    # Center of the quality weighting curve.
    # Range [0, 1]. Quality scores below q0 get weight < 1, above get > 1.
    # Increase → shift the "neutral quality" point higher, penalizing more ROIs.
    # Decrease → shift it lower, boosting more ROIs.
    # Default 0.5: symmetric around the midpoint of the quality score range.
    agg_quality_q0: float = 0.5

    # Steepness of the tanh weighting curve.
    # Range [0.5, 10]. Controls how sharply weights change around q0.
    # Increase → sharper transition (more binary high/low weighting).
    # Decrease → gentler curve (weights closer to uniform).
    # Default 2.0: moderate sensitivity, keeps weights smooth.
    agg_quality_k: float = 2.0

    # Amplitude of the quality effect on raw weights.
    # Range [0, 0.5]. Raw weights span approximately [1 - beta, 1 + beta].
    # Increase → quality matters more (wider weight spread).
    # Decrease → quality matters less (weights closer to 1.0).
    # Default 0.2: ±20% max quality effect before normalization.
    agg_quality_beta: float = 0.2

    # Maximum allowed normalized weight per unit.
    # Range [1.0, 2.0]. Prevents any single unit from dominating aggregation.
    # Increase → allow higher-quality units more influence.
    # Decrease → force weights closer to uniform.
    # Default 1.3: allows 30% above mean, balanced single-unit influence.
    agg_quality_w_cap: float = 1.3

    # ===== OCR aggregation: length resolution =====

    # Epsilon floor for log-space computations to prevent log(0).
    # Range (0, 1e-6]. Used in EOS hazard and weighted log aggregation.
    # Increase → more numerical damping but slight probability distortion.
    # Decrease → closer to true log-prob but risk of numerical instability.
    # Default 1e-10: small enough to not affect results, large enough for float64 safety.
    agg_eps: float = 1e-10

    # Weight of the length log-probability in the composite candidate score.
    # Range [0, 1]. score = char_log_prob + lambda_len * len_log_prob.
    # Increase → length agreement matters more in candidate ranking.
    # Decrease → character-level confidence dominates scoring.
    # Default 0.25: mild length influence, characters dominate per design doc.
    agg_lambda_len: float = 0.25

    # Maximum position index for length distribution (must match PARSeq output length).
    # Range [1, 50]. Defines the support of the length distribution P(L=t).
    # Increase → allow longer strings (must match OCR model capacity).
    # Decrease → truncate length support (may miss longer strings).
    # Default 9: 8 character slots + 1 EOS slot.
    agg_max_positions: int = 9

    # ===== OCR aggregation: character + duplicate override =====

    # Maximum margin (p1 - p2) for duplicate agreement override to consider.
    # Range [0, 0.2]. Only near-tie positions (margin <= delta) are candidates.
    # Increase → override fires on wider margins (more aggressive correction).
    # Decrease → override only fires on extremely close ties (more conservative).
    # Default 0.06: narrow band catches O/0, I/1, B/8 confusion without overriding clear decisions.
    agg_delta_agree_override: float = 0.06

    # Minimum top-1 probability in BOTH base and enhanced ROIs for override.
    # Range [0.3, 0.9]. Filters noisy agreement — both models must be confident.
    # Increase → require higher confidence from both models (fewer overrides).
    # Decrease → allow less confident agreement (more overrides but riskier).
    # Default 0.60: moderate confidence gate filters noise while allowing genuine agreement.
    agg_pair_agree_min_p1: float = 0.60

    # Allowed character set for decoded output (EOS excluded automatically).
    # Standard US license plate charset: 26 uppercase letters + 10 digits = 36 chars.
    # Change only if supporting non-US plates or special characters.
    # Default: uppercase A-Z + digits 0-9 (36 characters).
    agg_allowed_chars: str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

    # ===== OCR aggregation: EMA + hypothesis bank =====

    # Character EMA learning rate for hypothesis bank distribution updates.
    # Range [0.05, 0.5]. Controls how fast character distributions converge.
    # Increase → faster convergence but more noise sensitivity.
    # Decrease → smoother convergence but slower adaptation.
    # Default 0.15: moderate rate, MUST be < agg_alpha_len per design (length converges faster).
    agg_alpha_char: float = 0.15

    # Length EMA learning rate for hypothesis bank length distribution updates.
    # Range [0.05, 0.5]. Must be > agg_alpha_char so length converges faster.
    # Increase → faster length lock-in, reduces length oscillation.
    # Decrease → slower length convergence, more passes needed.
    # Default 0.20: slightly faster than char EMA, per design requirement.
    agg_alpha_len: float = 0.20

    # Number of unseen passes before a hypothesis is considered stale.
    # Range [1, 10]. Hypothesis not updated for > stale_passes is replaceable.
    # Increase → keep old hypotheses longer (conservative).
    # Decrease → replace stale hypotheses faster (aggressive).
    # Default 3: allows brief absence without premature replacement.
    agg_stale_passes: int = 3

    # Minimum log-score advantage needed to replace a non-stale H2 hypothesis.
    # Range [0.1, 2.0]. candidate.score - h2.score must exceed this threshold.
    # Increase → harder to replace (more stable H2).
    # Decrease → easier to replace (more responsive to better candidates).
    # Default 0.5: requires meaningful improvement over existing hypothesis.
    agg_replace_margin: float = 0.5

    # ===== OCR aggregation: confusion pairs =====

    # Core confusion pairs for license plate OCR.
    # Range: pairs of visually similar characters. Each pair is checked during
    # confidence bucketing and DONE evaluation.
    # Increase → more pairs flagged (more conservative HIGH gating).
    # Decrease → fewer pairs checked (faster HIGH but riskier).
    # Default: 8 pairs covering the most common US plate confusions.
    agg_core_confusion_pairs: tuple[tuple[str, str], ...] = (
        ("O", "0"),
        ("Q", "0"),
        ("D", "0"),
        ("B", "8"),
        ("Z", "7"),
        ("I", "1"),
        ("S", "5"),
        ("G", "6"),
    )

    # Extended confusion pairs for CA regex tiebreak dominance gate.
    # Range: additional pairs beyond core set. Used only in tiebreak ambiguity check.
    # Increase → broader confusion detection (tiebreak fires on more scenarios).
    # Decrease → narrower (tiebreak requires stronger confusion signal).
    # Default: 9 secondary pairs from empirical plate confusion analysis.
    agg_extended_confusion_pairs: tuple[tuple[str, str], ...] = (
        ("2", "Z"),
        ("T", "7"),
        ("J", "1"),
        ("5", "2"),
        ("E", "F"),
        ("L", "T"),
        ("A", "4"),
        ("V", "Y"),
        ("M", "W"),
    )

    # Maximum margin (p1 - p2) for a confusion pair to block HIGH confidence.
    # Range [0.05, 0.50]. If a position has decoded/runner-up chars in a core
    # confusion set AND margin < this threshold, confusion_blocked = True.
    # Increase → harder to block HIGH (more lenient on confusion).
    # Decrease → easier to block HIGH (more conservative).
    # Default 0.20: positions with thin margin between confused chars block HIGH.
    agg_confusion_margin_high: float = 0.20

    # ===== OCR aggregation: confidence bucketing =====

    # Minimum mean top-1 probability across all positions for HIGH confidence.
    # Range [0.70, 0.99]. Checked after confusion veto.
    # Increase → harder to achieve HIGH (more conservative).
    # Decrease → easier HIGH (more false positives).
    # Default 0.90: requires strong average character confidence.
    agg_high_mean_p1: float = 0.90

    # Minimum per-position margin (p1 - p2) for HIGH confidence.
    # Range [0.01, 0.50]. The weakest position must still have this margin.
    # Increase → stricter (any ambiguous position blocks HIGH).
    # Decrease → more lenient (allows some close calls).
    # Default 0.10: every position must have clear winner.
    agg_high_min_margin: float = 0.10

    # Minimum mean top-1 probability for MED confidence.
    # Range [0.50, 0.90]. Below this threshold → LOW.
    # Increase → fewer MED results (pushed to LOW).
    # Decrease → more results reach MED.
    # Default 0.75: moderate average character confidence.
    agg_med_mean_p1: float = 0.75

    # Minimum mean margin (p1 - p2) for MED confidence.
    # Range [0.01, 0.20]. Average margin across positions.
    # Increase → harder to reach MED.
    # Decrease → easier MED (more noise tolerance).
    # Default 0.05: modest average separation required.
    agg_med_mean_margin: float = 0.05

    # ===== OCR aggregation: DONE evaluation =====

    # Minimum length probability for one-pass DONE (Path A).
    # Range [0.50, 0.95]. P(L=candidate_length) must exceed this.
    # Increase → stricter length agreement required.
    # Decrease → allows weaker length consensus.
    # Default 0.70: strong but not extreme length confidence.
    agg_onepass_len_prob: float = 0.70

    # Minimum per-position top-1 probability for one-pass DONE.
    # Range [0.70, 0.99]. Every position must exceed this.
    # Increase → stricter (requires near-certain characters).
    # Decrease → allows weaker character confidence.
    # Default 0.90: high per-position certainty required.
    agg_onepass_min_p1: float = 0.90

    # Minimum per-position margin for one-pass DONE.
    # Range [0.05, 0.30]. Every position must exceed this.
    # Increase → requires wider separation between top-2 chars.
    # Decrease → allows closer calls.
    # Default 0.12: clear character separation at every position.
    agg_onepass_min_margin: float = 0.12

    # Minimum margin for confusion positions in one-pass DONE.
    # Range [0.10, 0.50]. Confusion positions must meet this stricter threshold.
    # Increase → harder to pass one-pass with confusion.
    # Decrease → more lenient on confused positions.
    # Default 0.25: much stricter than general margin for confused positions.
    agg_onepass_confusion_margin: float = 0.25

    # Minimum win count for multi-pass DONE (Path B).
    # Range [2, 10]. H1 must have been updated at least this many times.
    # Increase → more passes required before DONE.
    # Decrease → fewer passes needed (faster DONE, less confidence).
    # Default 2: at least 2 confirming passes.
    agg_multipass_min_wins: int = 2

    # Minimum score gap between H1 and H2 for Path B DONE.
    # Range [0.1, 2.0]. abs(H1.score - H2.score) must exceed this.
    # Increase → H2 must be clearly weaker.
    # Decrease → allows closer competitors.
    # Default 0.5: meaningful score separation required.
    agg_hyp_gap_done: float = 0.5

    # ===== OCR aggregation: CA tiebreak =====

    # Maximum score difference for CA tiebreak to consider.
    # Range [0.05, 1.0]. abs(c1.score - c2.score) must be below this.
    # Increase → tiebreak considers wider score gaps.
    # Decrease → only near-exact ties trigger tiebreak.
    # Default 0.25: only close calls eligible for regex tiebreak.
    agg_tie_score: float = 0.25

    # ===== OCR aggregation: debug payload =====

    # Whether to populate the debug payload in AggregationDecision.
    # Range: bool. When False, debug_payload is None (saves memory).
    # Increase (True) → full diagnostics for tuning and debugging.
    # Decrease (False) → no payload, production mode.
    # Default False: disabled in production for performance.
    agg_enable_debug_payload: bool = False

    # ===== OCR queue =====
    ocr_queue_capacity: int = 64
    max_done_metrics: int = 10_000  # Max done-track metrics retained; 0 = unlimited
    in_flight_timeout_ms: int = 30_000  # Max time a dispatched commit can stay in-flight

    # ===== PARSeq OCR engine =====
    parseq_model_path: str = "models/parseq.pt"
    parseq_warmup_runs: int = 3
    parseq_max_batch_size: int = 12
    parseq_device: str = "cuda:0"
    parseq_allowed_chars: str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    parseq_vocab_path: str = "models/parseq_vocab.txt"
    parseq_temperature: float = 1.2  # M1: using 1.2 per calibration strategy
    parseq_normalize_input: bool = True
    parseq_norm_mean: tuple[float, float, float] = (0.485, 0.456, 0.406)  # ImageNet RGB
    parseq_norm_std: tuple[float, float, float] = (0.229, 0.224, 0.225)  # ImageNet RGB
    parseq_max_plate_chars: int = 8

    def __post_init__(self):
        """Validate config parameters after initialization."""
        # Canonical crop size validation (required for local contrast grid)
        if self.canonical_crop_width % 8 != 0:
            raise ValueError(
                f"canonical_crop_width must be divisible by 8 (for 4×8 local contrast grid), "
                f"got {self.canonical_crop_width}"
            )
        if self.canonical_crop_height % 4 != 0:
            raise ValueError(
                f"canonical_crop_height must be divisible by 4 (for 4×8 local contrast grid), "
                f"got {self.canonical_crop_height}"
            )

        # Threshold validation (logical bounds)
        if self.flat_region_var_threshold < 0.0:
            raise ValueError(
                f"flat_region_var_threshold must be >= 0.0, got {self.flat_region_var_threshold}"
            )

        if self.flat_region_min_pixels < 0:
            raise ValueError(
                f"flat_region_min_pixels must be >= 0, got {self.flat_region_min_pixels}"
            )

        if not (0 <= self.luminance_black_threshold < self.luminance_white_threshold <= 255):
            raise ValueError(
                f"luminance thresholds must satisfy 0 <= black < white <= 255, "
                f"got black={self.luminance_black_threshold}, white={self.luminance_white_threshold}"
            )

        if not (self.tenengrad_floor <= self.tenengrad_good):
            raise ValueError(
                f"tenengrad_floor must be <= tenengrad_good, "
                f"got floor={self.tenengrad_floor}, good={self.tenengrad_good}"
            )

        # OCR-likelihood scoring validation
        for weight_name in [
            "ocr_score_weight_sharpness",
            "ocr_score_weight_size",
            "ocr_score_weight_contrast",
            "ocr_score_weight_exposure",
            "ocr_score_weight_noise",
            "ocr_score_weight_detection",
        ]:
            if getattr(self, weight_name) < 0:
                raise ValueError(f"{weight_name} must be >= 0, got {getattr(self, weight_name)}")

        if self.plate_height_ideal <= 0:
            raise ValueError(f"plate_height_ideal must be > 0, got {self.plate_height_ideal}")

        if self.tenengrad_ideal <= 0:
            raise ValueError(f"tenengrad_ideal must be > 0, got {self.tenengrad_ideal}")

        # OCR commit gate validation
        if self.commit_margin < 0:
            raise ValueError(f"commit_margin must be >= 0, got {self.commit_margin}")
        if self.max_staleness_ms <= 0:
            raise ValueError(f"max_staleness_ms must be > 0, got {self.max_staleness_ms}")
        if not (0.0 <= self.stale_min_score <= 1.0):
            raise ValueError(f"stale_min_score must be in [0.0, 1.0], got {self.stale_min_score}")
        if self.in_flight_timeout_ms <= 0:
            raise ValueError(f"in_flight_timeout_ms must be > 0, got {self.in_flight_timeout_ms}")

        # PARSeq validation
        if self.parseq_temperature <= 0:
            raise ValueError(f"parseq_temperature must be > 0, got {self.parseq_temperature}")
        if self.parseq_warmup_runs < 0:
            raise ValueError(f"parseq_warmup_runs must be >= 0, got {self.parseq_warmup_runs}")
        if self.parseq_max_batch_size < 1:
            raise ValueError(
                f"parseq_max_batch_size must be >= 1, got {self.parseq_max_batch_size}"
            )
        if not self.parseq_allowed_chars:
            raise ValueError("parseq_allowed_chars must be non-empty")
        if not self.parseq_vocab_path:
            raise ValueError("parseq_vocab_path must be non-empty")
        if len(self.parseq_norm_mean) != 3:
            raise ValueError(
                f"parseq_norm_mean must have 3 elements, got {len(self.parseq_norm_mean)}"
            )
        if len(self.parseq_norm_std) != 3:
            raise ValueError(
                f"parseq_norm_std must have 3 elements, got {len(self.parseq_norm_std)}"
            )
        if any(s <= 0 for s in self.parseq_norm_std):
            raise ValueError(f"parseq_norm_std elements must be > 0, got {self.parseq_norm_std}")
        if self.parseq_max_plate_chars < 1:
            raise ValueError(
                f"parseq_max_plate_chars must be >= 1, got {self.parseq_max_plate_chars}"
            )
        # H6: cross-validate parseq_max_plate_chars + 1 == agg_max_positions
        if self.parseq_max_plate_chars + 1 != self.agg_max_positions:
            raise ValueError(
                f"parseq_max_plate_chars + 1 must equal agg_max_positions: "
                f"{self.parseq_max_plate_chars} + 1 = {self.parseq_max_plate_chars + 1} "
                f"!= {self.agg_max_positions}"
            )

        # Warn if parseq and aggregator character sets diverge
        if self.parseq_allowed_chars != self.agg_allowed_chars:
            import warnings

            warnings.warn(
                f"parseq_allowed_chars ({self.parseq_allowed_chars!r}) does not match "
                f"agg_allowed_chars ({self.agg_allowed_chars!r}). "
                "This may cause distribution/aggregator character set mismatch.",
                UserWarning,
                stacklevel=2,
            )

        # Enhancement validation
        if self.max_enhance_duplicates < 0:
            raise ValueError(
                f"max_enhance_duplicates must be >= 0, got {self.max_enhance_duplicates}"
            )
        for weight_name in [
            "enhance_upside_weight_contrast",
            "enhance_upside_weight_exposure",
            "enhance_upside_weight_sharpness",
            "enhance_upside_weight_noise",
        ]:
            if getattr(self, weight_name) < 0:
                raise ValueError(f"{weight_name} must be >= 0, got {getattr(self, weight_name)}")
        if not (0 <= self.enhance_excellence_threshold <= 1):
            raise ValueError(
                f"enhance_excellence_threshold must be in [0, 1], got {self.enhance_excellence_threshold}"
            )

        # Diversity validation
        diversity_weight_sum = (
            self.diversity_weight_skew
            + self.diversity_weight_quad
            + self.diversity_weight_persp
            + self.diversity_weight_scale
        )
        if diversity_weight_sum <= 0:
            raise ValueError(f"Sum of diversity weights must be > 0, got {diversity_weight_sum}")
        if self.diversity_min_distance < 0:
            raise ValueError(
                f"diversity_min_distance must be >= 0, got {self.diversity_min_distance}"
            )
        if self.diversity_quad_jitter_threshold < 0:
            raise ValueError(
                f"diversity_quad_jitter_threshold must be >= 0, got {self.diversity_quad_jitter_threshold}"
            )
        if self.anchor_min_pose_distance < 0:
            raise ValueError(
                f"anchor_min_pose_distance must be >= 0, got {self.anchor_min_pose_distance}"
            )

        # Recipe planner validation
        if not (0.0 <= self.target_luma <= 255.0):
            raise ValueError(f"target_luma must be in [0, 255], got {self.target_luma}")
        if self.target_contrast < 0.0:
            raise ValueError(f"target_contrast must be >= 0, got {self.target_contrast}")
        if self.target_sharpness < 0.0:
            raise ValueError(f"target_sharpness must be >= 0, got {self.target_sharpness}")
        if self.target_noise_max < 0.0:
            raise ValueError(f"target_noise_max must be >= 0, got {self.target_noise_max}")
        if self.luma_range <= 0.0:
            raise ValueError(f"luma_range must be > 0, got {self.luma_range}")

        # Mapping gain coefficients must be non-negative
        for gain_name in ["k_gain", "k_gamma", "k_contrast", "k_denoise", "k_sharpen"]:
            if getattr(self, gain_name) < 0.0:
                raise ValueError(f"{gain_name} must be >= 0, got {getattr(self, gain_name)}")

        # Clamp range ordering
        if self.gain_min > self.gain_max:
            raise ValueError(f"gain_min must be <= gain_max, got {self.gain_min} > {self.gain_max}")
        if self.gamma_min > self.gamma_max:
            raise ValueError(
                f"gamma_min must be <= gamma_max, got {self.gamma_min} > {self.gamma_max}"
            )

        # Max blend/strength must be non-negative
        if self.contrast_max < 0.0:
            raise ValueError(f"contrast_max must be >= 0, got {self.contrast_max}")
        if self.denoise_max < 0.0:
            raise ValueError(f"denoise_max must be >= 0, got {self.denoise_max}")
        if self.sharpen_max < 0.0:
            raise ValueError(f"sharpen_max must be >= 0, got {self.sharpen_max}")

        # CLAHE range ordering
        if self.clahe_clip_min > self.clahe_clip_max:
            raise ValueError(
                f"clahe_clip_min must be <= clahe_clip_max, "
                f"got {self.clahe_clip_min} > {self.clahe_clip_max}"
            )
        if self.clahe_tiles_min > self.clahe_tiles_max:
            raise ValueError(
                f"clahe_tiles_min must be <= clahe_tiles_max, "
                f"got {self.clahe_tiles_min} > {self.clahe_tiles_max}"
            )
        if self.clahe_blend_max < 0.0:
            raise ValueError(f"clahe_blend_max must be >= 0, got {self.clahe_blend_max}")

        # Geometry validation
        if not (0.0 <= self.warp_margin <= 0.5):
            raise ValueError(f"warp_margin must be in [0.0, 0.5], got {self.warp_margin}")
        if self.recipe_output_width <= 0:
            raise ValueError(f"recipe_output_width must be > 0, got {self.recipe_output_width}")
        if self.recipe_output_height <= 0:
            raise ValueError(f"recipe_output_height must be > 0, got {self.recipe_output_height}")

        # Photometric
        if self.contrast_steepness <= 0.0:
            raise ValueError(f"contrast_steepness must be > 0, got {self.contrast_steepness}")


def load_consumer_config(**overrides) -> ConsumerConfig:
    """
    Create ConsumerConfig with optional runtime overrides.

    Args:
        **overrides: Config fields to override

    Returns:
        ConsumerConfig with defaults + overrides applied
    """
    cfg = ConsumerConfig()

    for key, value in overrides.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
        else:
            raise ValueError(f"Unknown config parameter: {key}")

    # Re-validate after applying overrides (since __post_init__ only ran on defaults)
    if overrides:
        cfg.__post_init__()

    return cfg
