"""OCR aggregation operations: evidence formation, quality weighting, length resolution,
character aggregation, duplicate override, scoring, and hypothesis bank.

Chunk-01: Evidence Unit Formation + Quality Weights
- form_evidence_units: fuse base/enhanced ROI pairs into EvidenceUnit instances
- compute_quality_weights: tanh-bounded symmetric quality weighting

Chunk-02: EOS Hazard Length Resolution
- compute_eos_hazard: per-unit P(L=t) via hazard/survival decomposition in log-space
- aggregate_length_distributions: weighted log aggregation across units → (P_pass_len, L1, L2)

Chunk-03: Character Aggregation + Duplicate Override + Scoring
- aggregate_characters: weighted log-space character distribution fusion with EOS removal
- apply_duplicate_agreement_override: narrow-band duplicate agreement override (3-condition gate)
- score_candidate: composite scoring from character and length log-probabilities

Chunk-04: Hypothesis Bank + EMA
- OcrAggregator: stateful two-hypothesis bank per track with EMA distribution updates
- update_hypotheses: 7-branch deterministic candidate-to-hypothesis matching
- get_hypotheses / clear_track: query and lifecycle management
"""

from __future__ import annotations

import logging
import re

import numpy as np

from consumer.config import ConsumerConfig
from consumer.models import (
    AggregationDebugPayload,
    AggregationDecision,
    CandidateResult,
    ConfidenceBucket,
    EvidenceUnit,
    HypothesisState,
    ReasonCode,
)
from producer.models import TrackId

logger = logging.getLogger(__name__)


def form_evidence_units(
    distributions: np.ndarray,
    duplicate_groups: dict[str, list[int]],
    quality_scores: list[float],
) -> list[EvidenceUnit]:
    """Fuse base/enhanced ROI pairs into evidence units.

    For each duplicate group:
    - Base ROI is always the first index in the group.
    - If an enhanced ROI exists (second index), fuse distributions by
      normalizing (base + enhanced) and compute per-position agreement signals.
    - If base-only, the distribution passes through unchanged.

    Args:
        distributions: (N, max_T, vocab_size) softmax distributions for all ROIs.
        duplicate_groups: Maps group_id to list of ROI indices [base, enhanced?].
        quality_scores: Per-ROI quality scores (indexed same as distributions).

    Returns:
        One EvidenceUnit per group, preserving group insertion order.
    """
    if not duplicate_groups:
        return []

    units: list[EvidenceUnit] = []
    max_t = distributions.shape[1]

    for group_id, indices in duplicate_groups.items():
        base_idx = indices[0]
        base_dist = distributions[base_idx]  # (max_T, vocab_size)
        quality = quality_scores[base_idx]

        if len(indices) >= 2:
            # Enhanced ROI present — fuse distributions
            enhanced_idx = indices[1]
            enhanced_dist = distributions[enhanced_idx]

            fused_raw = base_dist + enhanced_dist
            fused = fused_raw / fused_raw.sum(axis=-1, keepdims=True)

            # Per-position agreement signals
            base_argmax = base_dist.argmax(axis=-1)  # (max_T,)
            enhanced_argmax = enhanced_dist.argmax(axis=-1)  # (max_T,)
            agree_mask = base_argmax == enhanced_argmax  # (max_T,) bool

            agree_char_indices = np.where(agree_mask, base_argmax, -1)

            base_top1 = base_dist.max(axis=-1)  # (max_T,)
            enhanced_top1 = enhanced_dist.max(axis=-1)  # (max_T,)
            pair_min_p1 = np.minimum(base_top1, enhanced_top1)

            has_enhanced = True

            logger.debug(
                "group %s: fused base_idx=%d + enhanced_idx=%d, agree_count=%d/%d",
                group_id,
                base_idx,
                enhanced_idx,
                int(agree_mask.sum()),
                max_t,
            )
        else:
            # Base-only — pass through
            fused = base_dist
            agree_mask = np.zeros(max_t, dtype=bool)
            agree_char_indices = np.full(max_t, -1, dtype=np.intp)
            pair_min_p1 = np.zeros(max_t, dtype=np.float64)
            has_enhanced = False

            logger.debug("group %s: base-only idx=%d", group_id, base_idx)

        units.append(
            EvidenceUnit(
                distribution=fused,
                quality=quality,
                agree_mask=agree_mask,
                agree_char_indices=agree_char_indices,
                pair_min_p1=pair_min_p1,
                has_enhanced=has_enhanced,
                group_id=group_id,
            )
        )

    logger.info("formed %d evidence units from %d groups", len(units), len(duplicate_groups))
    return units


def compute_quality_weights(
    units: list[EvidenceUnit],
    cfg: ConsumerConfig,
) -> np.ndarray:
    """Compute tanh-bounded symmetric quality weights for evidence units.

    Algorithm:
    1. raw_w[i] = 1.0 + beta * tanh(k * (quality_i - q0))
    2. mean_w = mean(raw_w)
    3. w_norm = raw_w / mean_w  (mean becomes 1.0)
    4. w_final = min(w_cap, w_norm) per element

    Args:
        units: Evidence units with quality scores.
        cfg: Config with agg_quality_q0, agg_quality_k, agg_quality_beta, agg_quality_w_cap.

    Returns:
        (len(units),) array of normalized, capped weights.
    """
    n = len(units)
    qualities = np.array([u.quality for u in units], dtype=np.float64)

    # Step 1: Raw tanh-bounded weights
    raw_w = 1.0 + cfg.agg_quality_beta * np.tanh(
        cfg.agg_quality_k * (qualities - cfg.agg_quality_q0)
    )

    # Step 2-3: Normalize to mean 1.0
    mean_w = raw_w.mean()
    if mean_w > 0:
        w_norm = raw_w / mean_w
    else:
        w_norm = np.ones(n, dtype=np.float64)

    # Step 4: Cap
    w_final = np.minimum(w_norm, cfg.agg_quality_w_cap)

    logger.debug(
        "quality weights: n=%d, raw_range=[%.4f,%.4f], norm_range=[%.4f,%.4f], capped=%d",
        n,
        raw_w.min(),
        raw_w.max(),
        w_final.min(),
        w_final.max(),
        int((w_norm > cfg.agg_quality_w_cap).sum()),
    )

    return w_final


# ---------------------------------------------------------------------------
# Chunk-02: EOS Hazard Length Resolution
# ---------------------------------------------------------------------------


def compute_eos_hazard(
    distribution: np.ndarray,
    eos_index: int,
    max_T: int,
    eps: float,
) -> np.ndarray:
    """Compute length distribution P(L=t) via EOS hazard/survival in log-space.

    For each candidate length t (1-indexed):
        logP(L=t) = log(eps + EOS[t-1]) + sum_{k=0}^{t-2} log(eps + (1 - EOS[k]))

    where EOS[k] = distribution[k, eos_index] is the probability of end-of-sequence
    at position k. The result is softmax-normalized to sum to 1.0.

    Args:
        distribution: (max_T, vocab_size) softmax distribution for one evidence unit.
        eos_index: Index of the EOS token in the vocabulary.
        max_T: Number of positions (length of output).
        eps: Epsilon floor to prevent log(0).

    Returns:
        (max_T,) array of P(L=t) values summing to ~1.0.
    """
    eos_probs = distribution[:max_T, eos_index]  # (max_T,)

    # Compute log-hazard and cumulative log-survival
    log_hazard = np.log(eps + eos_probs)  # (max_T,)
    log_survival = np.log(eps + (1.0 - eos_probs))  # (max_T,)

    # Cumulative log-survival: for t, we need sum of log_survival[0..t-1]
    cum_log_survival = np.cumsum(log_survival)  # (max_T,)

    # logP(L=t) = log_hazard[t] + cum_log_survival[t-1]  (where t is 0-indexed)
    # For t=0: logP(L=1) = log_hazard[0] (no survival term)
    # For t>0: logP(L=t+1) = log_hazard[t] + cum_log_survival[t-1]
    log_p = np.empty(max_T, dtype=np.float64)
    log_p[0] = log_hazard[0]
    log_p[1:] = log_hazard[1:] + cum_log_survival[:-1]

    # Softmax normalization for numerical stability
    log_p -= log_p.max()
    exp_p = np.exp(log_p)
    result = exp_p / exp_p.sum()

    logger.debug(
        "eos_hazard: max_T=%d, peak_length=%d, peak_prob=%.4f",
        max_T,
        int(np.argmax(result)) + 1,
        result.max(),
    )

    return result


def aggregate_length_distributions(
    units: list[EvidenceUnit],
    weights: np.ndarray,
    eos_index: int,
    cfg: ConsumerConfig,
) -> tuple[np.ndarray, int, int]:
    """Aggregate per-unit length distributions into a pass-level distribution.

    Algorithm:
    1. Compute per-unit hazard distributions via compute_eos_hazard.
    2. Weighted log aggregation: log_P_agg(t) = sum_u weights[u] * log(eps + hazard_u(t)).
    3. Softmax normalize to get P_pass_len.
    4. L1 = argmax + 1 (1-indexed), L2 = second argmax + 1.

    Args:
        units: Evidence units from form_evidence_units.
        weights: (len(units),) quality weights from compute_quality_weights.
        eos_index: Index of the EOS token in the vocabulary.
        cfg: Config with agg_eps and agg_max_positions.

    Returns:
        (P_pass_len, L1, L2) where P_pass_len is (agg_max_positions,) distribution,
        L1 is the most likely length (1-indexed), L2 is the second most likely.
    """
    max_t = cfg.agg_max_positions
    eps = cfg.agg_eps

    # Step 1: Per-unit hazard distributions
    log_p_agg = np.zeros(max_t, dtype=np.float64)
    for i, unit in enumerate(units):
        hazard = compute_eos_hazard(unit.distribution, eos_index, max_t, eps)
        log_p_agg += weights[i] * np.log(eps + hazard)

    # Step 2: Softmax normalization
    log_p_agg -= log_p_agg.max()
    exp_p = np.exp(log_p_agg)
    p_pass_len = exp_p / exp_p.sum()

    # Step 3: Top-2 candidate lengths (1-indexed)
    l1_idx = int(np.argmax(p_pass_len))
    l1 = l1_idx + 1

    # Second argmax: mask the top-1 position
    temp = p_pass_len.copy()
    temp[l1_idx] = -1.0
    l2_idx = int(np.argmax(temp))
    l2 = l2_idx + 1

    logger.info(
        "length_aggregation: n_units=%d, L1=%d (p=%.4f), L2=%d (p=%.4f)",
        len(units),
        l1,
        p_pass_len[l1_idx],
        l2,
        p_pass_len[l2_idx],
    )

    return p_pass_len, l1, l2


# ---------------------------------------------------------------------------
# Chunk-03: Character Aggregation + Duplicate Override + Scoring
# ---------------------------------------------------------------------------


def aggregate_characters(
    units: list[EvidenceUnit],
    weights: np.ndarray,
    candidate_length: int,
    eos_index: int,
    allowed_chars: str,
    eps: float,
) -> tuple[np.ndarray, list[int]]:
    """Per-position weighted log-space character distribution fusion with EOS removal.

    For each position t in 0..candidate_length-1:
    1. Extract allowed character probabilities (exclude EOS), renormalize per unit.
    2. Weighted log aggregation: log_P(t,c) = sum_u weights[u] * log(eps + p_u(t,c)).
    3. Softmax normalize to get P_pass_char(t,:).
    4. decoded[t] = argmax(P_pass_char(t,:)).

    Args:
        units: Evidence units from form_evidence_units.
        weights: (len(units),) quality weights.
        candidate_length: Number of character positions to decode.
        eos_index: Index of the EOS token in the full vocabulary.
        allowed_chars: String of allowed characters (e.g. "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789").
        eps: Epsilon floor for log-space.

    Returns:
        (char_dists, decoded) where char_dists is (candidate_length, n_allowed_chars)
        and decoded is list of allowed-char indices.
    """
    vocab_size = units[0].distribution.shape[1]
    n_chars = len(allowed_chars)

    # Build allowed_indices: vocab indices of allowed chars excluding EOS.
    # Assumes allowed chars map to vocab indices 1..n_chars (EOS at index 0).
    allowed_indices = []
    for i in range(vocab_size):
        if i != eos_index and len(allowed_indices) < n_chars:
            allowed_indices.append(i)
    allowed_indices = np.array(allowed_indices, dtype=np.intp)

    char_dists = np.zeros((candidate_length, n_chars), dtype=np.float64)
    decoded: list[int] = []

    for t in range(candidate_length):
        log_agg = np.zeros(n_chars, dtype=np.float64)
        for i, unit in enumerate(units):
            p_u = unit.distribution[t, allowed_indices].astype(np.float64)
            p_sum = p_u.sum()
            if p_sum > 0:
                p_u = p_u / p_sum
            log_agg += weights[i] * np.log(eps + p_u)

        # Softmax normalization
        log_agg -= log_agg.max()
        exp_agg = np.exp(log_agg)
        char_dists[t] = exp_agg / exp_agg.sum()

        decoded.append(int(np.argmax(char_dists[t])))

    logger.debug(
        "aggregate_characters: L=%d, n_units=%d, decoded=%s",
        candidate_length,
        len(units),
        decoded,
    )

    return char_dists, decoded


def apply_duplicate_agreement_override(
    char_dists: np.ndarray,
    decoded_chars: list[int],
    units: list[EvidenceUnit],
    eos_index: int,
    allowed_chars: str,
    cfg: ConsumerConfig,
) -> tuple[list[int], list[int]]:
    """Narrow-band duplicate agreement override for ambiguous positions.

    For each position t where the margin (p1 - p2) is within delta_agree_override:
    1. Check margin <= delta_agree_override.
    2. Identify runner-up character (second argmax in char_dists[t]).
    3. Map runner-up to vocab index.
    4. For each unit with has_enhanced: check all 3 conditions:
       a) agree_mask[t] == True
       b) agree_char_indices[t] == runner_up_vocab_idx
       c) pair_min_p1[t] >= agg_pair_agree_min_p1
    5. If all met: override decoded[t] to runner-up, record position.

    Args:
        char_dists: (L, n_allowed_chars) per-position character distributions.
        decoded_chars: List of allowed-char indices from aggregate_characters.
        units: Evidence units (for raw agreement signals).
        eos_index: EOS token vocab index.
        allowed_chars: String of allowed characters.
        cfg: Config with agg_delta_agree_override, agg_pair_agree_min_p1.

    Returns:
        (new_decoded, overridden_positions) where new_decoded is the updated list
        and overridden_positions lists the positions where override fired.
    """
    L = char_dists.shape[0]
    n_chars = len(allowed_chars)
    new_decoded = list(decoded_chars)
    overridden: list[int] = []

    # Build allowed_indices: vocab index for each allowed char position
    vocab_size = units[0].distribution.shape[1] if units else n_chars + 1
    allowed_indices = []
    for i in range(vocab_size):
        if i != eos_index and len(allowed_indices) < n_chars:
            allowed_indices.append(i)

    for t in range(L):
        probs = char_dists[t]
        top1_idx = new_decoded[t]
        p1 = probs[top1_idx]

        # Find runner-up (second highest)
        temp = probs.copy()
        temp[top1_idx] = -1.0
        runner_up_idx = int(np.argmax(temp))
        p2 = probs[runner_up_idx]

        margin = p1 - p2
        if margin > cfg.agg_delta_agree_override:
            continue

        # Map runner-up allowed index to vocab index
        runner_up_vocab_idx = allowed_indices[runner_up_idx]

        # Check units with enhanced ROIs
        fired = False
        for unit in units:
            if not unit.has_enhanced:
                continue
            if t >= len(unit.agree_mask):
                continue
            if (
                unit.agree_mask[t]
                and unit.agree_char_indices[t] == runner_up_vocab_idx
                and unit.pair_min_p1[t] >= cfg.agg_pair_agree_min_p1
            ):
                new_decoded[t] = runner_up_idx
                overridden.append(t)
                fired = True
                break

        if fired:
            logger.debug(
                "override at pos %d: '%s' -> '%s' (margin=%.4f, vocab_idx=%d)",
                t,
                allowed_chars[top1_idx] if top1_idx < len(allowed_chars) else "?",
                allowed_chars[runner_up_idx] if runner_up_idx < len(allowed_chars) else "?",
                margin,
                runner_up_vocab_idx,
            )

    if overridden:
        logger.info(
            "duplicate_override: %d positions overridden at %s",
            len(overridden),
            overridden,
        )

    return new_decoded, overridden


def score_candidate(
    char_dists: np.ndarray,
    decoded_chars: list[int],
    len_dist: np.ndarray,
    candidate_length: int,
    lambda_len: float,
    eps: float,
) -> float:
    """Composite scoring combining character and length log-probabilities.

    score = sum_{t=0}^{L-1} log(eps + char_dists[t, decoded[t]])
          + lambda_len * log(eps + len_dist[candidate_length - 1])

    Args:
        char_dists: (L, n_allowed_chars) per-position character distributions.
        decoded_chars: List of allowed-char indices.
        len_dist: (max_T,) pass-level length distribution.
        candidate_length: Number of characters (L).
        lambda_len: Weight of length term in composite score.
        eps: Epsilon floor for log.

    Returns:
        Composite log-probability score (negative, closer to 0 = more confident).
    """
    char_score = 0.0
    for t in range(candidate_length):
        char_score += np.log(eps + char_dists[t, decoded_chars[t]])

    len_score = lambda_len * np.log(eps + len_dist[candidate_length - 1])

    total = float(char_score + len_score)

    logger.debug(
        "score_candidate: L=%d, char_score=%.4f, len_score=%.4f, total=%.4f",
        candidate_length,
        char_score,
        len_score,
        total,
    )

    return total


# ---------------------------------------------------------------------------
# Chunk-04: Hypothesis Bank + EMA
# ---------------------------------------------------------------------------


class OcrAggregator:
    """Stateful two-hypothesis bank per track with EMA distribution updates.

    Maintains up to two hypotheses (H1 strongest, H2 alternative) per track.
    Each call to update_hypotheses processes candidates through a 7-branch
    deterministic matching algorithm:
    (a) bootstrap H1, (b) match H1, (c) match H2, (d) fill H2,
    (e) stale replace H2, (f) weak replace H2, (g) discard.

    After processing, H1 and H2 are swapped if H2.score > H1.score, and
    both are re-decoded from their EMA distributions.
    """

    def __init__(self, cfg: ConsumerConfig) -> None:
        self._cfg = cfg
        self._hypotheses: dict[TrackId, list[HypothesisState | None]] = {}
        self._pass_counts: dict[TrackId, int] = {}

    def _init_hypothesis(self, candidate: CandidateResult, pass_num: int) -> HypothesisState:
        """Create a new hypothesis initialized from a candidate."""
        return HypothesisState(
            string=candidate.string,
            ema_char_dists=candidate.char_dists.copy(),
            ema_len_dist=candidate.len_dist.copy(),
            win_count=1,
            last_seen_pass=pass_num,
            score=candidate.score,
            candidate_length=candidate.candidate_length,
        )

    def _update_ema(self, hypothesis: HypothesisState, candidate: CandidateResult) -> None:
        """EMA update of hypothesis distributions from candidate (mutates in place).

        Does NOT re-decode — re-decode happens in update_hypotheses after all
        candidates are processed.
        """
        alpha_char = self._cfg.agg_alpha_char
        alpha_len = self._cfg.agg_alpha_len

        # Pad/truncate candidate char_dists to match hypothesis shape
        h_len, h_chars = hypothesis.ema_char_dists.shape
        c_len = candidate.char_dists.shape[0]
        if c_len < h_len:
            padded = np.zeros((h_len, h_chars), dtype=np.float64)
            padded[:c_len] = candidate.char_dists
            c_char = padded
        elif c_len > h_len:
            c_char = candidate.char_dists[:h_len]
        else:
            c_char = candidate.char_dists

        hypothesis.ema_char_dists = (
            1.0 - alpha_char
        ) * hypothesis.ema_char_dists + alpha_char * c_char

        # Length distribution EMA (same shape guaranteed by config max_positions)
        h_max = len(hypothesis.ema_len_dist)
        c_max = len(candidate.len_dist)
        if c_max < h_max:
            padded_len = np.zeros(h_max, dtype=np.float64)
            padded_len[:c_max] = candidate.len_dist
            c_len_dist = padded_len
        elif c_max > h_max:
            c_len_dist = candidate.len_dist[:h_max]
        else:
            c_len_dist = candidate.len_dist

        hypothesis.ema_len_dist = (
            1.0 - alpha_len
        ) * hypothesis.ema_len_dist + alpha_len * c_len_dist

        hypothesis.score = candidate.score

    def _re_decode(self, hypothesis: HypothesisState) -> None:
        """Re-decode string and candidate_length from EMA distributions."""
        # Length from argmax of EMA length distribution
        hypothesis.candidate_length = int(np.argmax(hypothesis.ema_len_dist)) + 1

        # String from argmax of EMA char distributions
        length = min(hypothesis.candidate_length, hypothesis.ema_char_dists.shape[0])
        allowed_chars = self._cfg.agg_allowed_chars
        decoded = []
        for t in range(length):
            idx = int(np.argmax(hypothesis.ema_char_dists[t]))
            if idx < len(allowed_chars):
                decoded.append(allowed_chars[idx])
            else:
                decoded.append("?")
        hypothesis.string = "".join(decoded)

        # Re-score from EMA distributions
        eps = self._cfg.agg_eps
        char_score = 0.0
        for t in range(length):
            idx = int(np.argmax(hypothesis.ema_char_dists[t]))
            char_score += float(np.log(eps + hypothesis.ema_char_dists[t, idx]))
        len_score = self._cfg.agg_lambda_len * float(
            np.log(eps + hypothesis.ema_len_dist[hypothesis.candidate_length - 1])
        )
        hypothesis.score = char_score + len_score

    def update_hypotheses(
        self,
        track_id: TrackId,
        candidates: list[CandidateResult],
        pass_num: int,
    ) -> tuple[HypothesisState | None, HypothesisState | None]:
        """Process candidates through 7-branch hypothesis matching.

        Candidates are processed sequentially (C1 then C2). After processing:
        - H1/H2 are swapped if H2.score > H1.score (H1 dominance).
        - Both hypotheses are re-decoded from their EMA distributions.

        Args:
            track_id: Track identifier.
            candidates: List of CandidateResult from this pass (typically 1-2).
            pass_num: Current pass number (monotonically increasing).

        Returns:
            (H1, H2) where either may be None.
        """
        # Initialize track state if new
        if track_id not in self._hypotheses:
            self._hypotheses[track_id] = [None, None]
        self._pass_counts[track_id] = pass_num

        h1, h2 = self._hypotheses[track_id]

        for candidate in candidates:
            if h1 is None:
                # (a) Bootstrap H1
                h1 = self._init_hypothesis(candidate, pass_num)
                logger.debug(
                    "hypothesis bootstrap H1: track=%s, string=%s, score=%.4f",
                    track_id,
                    candidate.string,
                    candidate.score,
                )
            elif candidate.string == h1.string:
                # (b) Match H1 — EMA update
                self._update_ema(h1, candidate)
                h1.win_count += 1
                h1.last_seen_pass = pass_num
                logger.debug(
                    "hypothesis match H1: track=%s, string=%s, win_count=%d",
                    track_id,
                    h1.string,
                    h1.win_count,
                )
            elif h2 is not None and candidate.string == h2.string:
                # (c) Match H2 — EMA update
                self._update_ema(h2, candidate)
                h2.win_count += 1
                h2.last_seen_pass = pass_num
                logger.debug(
                    "hypothesis match H2: track=%s, string=%s, win_count=%d",
                    track_id,
                    h2.string,
                    h2.win_count,
                )
            elif h2 is None:
                # (d) Fill H2
                h2 = self._init_hypothesis(candidate, pass_num)
                logger.debug(
                    "hypothesis fill H2: track=%s, string=%s, score=%.4f",
                    track_id,
                    candidate.string,
                    candidate.score,
                )
            elif pass_num - h2.last_seen_pass > self._cfg.agg_stale_passes:
                # (e) Stale replace H2
                logger.info(
                    "hypothesis stale replace H2: track=%s, old=%s (last_seen=%d), new=%s, pass=%d",
                    track_id,
                    h2.string,
                    h2.last_seen_pass,
                    candidate.string,
                    pass_num,
                )
                h2 = self._init_hypothesis(candidate, pass_num)
            elif candidate.score - h2.score > self._cfg.agg_replace_margin:
                # (f) Weak replace H2
                logger.info(
                    "hypothesis weak replace H2: track=%s, old=%s (score=%.4f), new=%s (score=%.4f), margin=%.4f",
                    track_id,
                    h2.string,
                    h2.score,
                    candidate.string,
                    candidate.score,
                    candidate.score - h2.score,
                )
                h2 = self._init_hypothesis(candidate, pass_num)
            else:
                # (g) Discard
                logger.debug(
                    "hypothesis discard: track=%s, candidate=%s (score=%.4f), h2=%s (score=%.4f)",
                    track_id,
                    candidate.string,
                    candidate.score,
                    h2.string,
                    h2.score,
                )

        # Re-decode from EMA distributions BEFORE swap so both scores are
        # on the same scale (EMA-derived). Without this, a hypothesis updated
        # this pass has candidate.score while one not updated has its previous
        # re-decoded score — incomparable scales that cause spurious swaps.
        if h1 is not None:
            self._re_decode(h1)
        if h2 is not None:
            self._re_decode(h2)

        # H1 dominance: swap if H2 is stronger (both now have EMA-derived scores)
        if h1 is not None and h2 is not None and h2.score > h1.score:
            logger.info(
                "hypothesis swap: track=%s, H1=%s (%.4f) <-> H2=%s (%.4f)",
                track_id,
                h1.string,
                h1.score,
                h2.string,
                h2.score,
            )
            h1, h2 = h2, h1

        self._hypotheses[track_id] = [h1, h2]
        return h1, h2

    def get_hypotheses(
        self, track_id: TrackId
    ) -> tuple[HypothesisState | None, HypothesisState | None]:
        """Return current (H1, H2) for a track, (None, None) if unknown."""
        if track_id not in self._hypotheses:
            return None, None
        h1, h2 = self._hypotheses[track_id]
        return h1, h2

    def clear_track(self, track_id: TrackId) -> None:
        """Remove all state for a track."""
        self._hypotheses.pop(track_id, None)
        self._pass_counts.pop(track_id, None)
        logger.debug("hypothesis clear_track: %s", track_id)

    def aggregate_pass(
        self,
        track_id: TrackId,
        distributions: np.ndarray,
        duplicate_groups: dict[str, list[int]],
        quality_scores: list[float],
        eos_index: int,
    ) -> AggregationDecision:
        """Full aggregation pipeline entry point.

        Orchestrates the complete pass: evidence formation → length resolution →
        character aggregation → duplicate override → scoring → hypothesis update →
        confidence → DONE evaluation → CA tiebreak → AggregationDecision.

        Args:
            track_id: Track identifier.
            distributions: (N, max_T, vocab_size) softmax distributions.
            duplicate_groups: Maps group_id to ROI indices.
            quality_scores: Per-ROI quality scores.
            eos_index: EOS token index in vocabulary.

        Returns:
            AggregationDecision with best string, confidence, done status, and reason.
        """
        cfg = self._cfg
        pass_num = self._pass_counts.get(track_id, 0) + 1
        self._pass_counts[track_id] = pass_num

        # Handle empty input
        if distributions.shape[0] == 0 or not duplicate_groups:
            h1, h2 = self.get_hypotheses(track_id)
            best_string = h1.string if h1 is not None else ""
            h1_string = best_string
            h2_string = h2.string if h2 is not None else None
            return AggregationDecision(
                best_string=best_string,
                confidence_bucket=ConfidenceBucket.LOW,
                done=False,
                reason_code=ReasonCode.INTERIM_AMBIGUOUS,
                h1_string=h1_string,
                h2_string=h2_string,
                pass_num=pass_num,
                debug_payload=None,
            )

        # Step 1: Form evidence units
        units = form_evidence_units(distributions, duplicate_groups, quality_scores)

        # Step 2: Compute quality weights
        weights = compute_quality_weights(units, cfg)

        # Steps 3-4: Aggregate length distributions
        p_pass_len, l1, l2 = aggregate_length_distributions(units, weights, eos_index, cfg)

        # Steps 5-8: For each candidate length, produce CandidateResult
        allowed_chars = cfg.agg_allowed_chars
        candidates: list[CandidateResult] = []
        dup_agree_fired = False
        all_overridden: list[int] = []

        for candidate_length in [l1, l2]:
            char_dists, decoded = aggregate_characters(
                units, weights, candidate_length, eos_index, allowed_chars, cfg.agg_eps
            )
            new_decoded, overridden = apply_duplicate_agreement_override(
                char_dists, decoded, units, eos_index, allowed_chars, cfg
            )
            if overridden:
                dup_agree_fired = True
                all_overridden.extend(overridden)

            score = score_candidate(
                char_dists,
                new_decoded,
                p_pass_len,
                candidate_length,
                cfg.agg_lambda_len,
                cfg.agg_eps,
            )

            string = "".join(
                allowed_chars[idx] if idx < len(allowed_chars) else "?" for idx in new_decoded
            )

            candidates.append(
                CandidateResult(
                    string=string,
                    score=score,
                    char_dists=char_dists,
                    len_dist=p_pass_len,
                    candidate_length=candidate_length,
                    overridden_positions=tuple(overridden),
                )
            )

        # Step 9: Update hypotheses
        h1, h2 = self.update_hypotheses(track_id, candidates, pass_num)

        # Build confusion sets for downstream use
        core_confusion_sets = [frozenset(pair) for pair in cfg.agg_core_confusion_pairs]

        # Step 10: Confidence on H1 (using C1's char_dists)
        c1 = candidates[0]
        c1_decoded = [int(np.argmax(c1.char_dists[t])) for t in range(c1.candidate_length)]
        h1_bucket = compute_confidence_bucket(
            c1.char_dists, c1_decoded, allowed_chars, core_confusion_sets, cfg
        )

        # Step 11: Evaluate DONE
        done, reason = evaluate_done(
            h1, h2, c1, core_confusion_sets, allowed_chars, p_pass_len, h1_bucket, pass_num, cfg
        )

        # Step 12: CA tiebreak (only if not done and 2 candidates)
        ca_tiebreak_fired = False
        best_candidate_idx = 0
        if not done and len(candidates) >= 2:
            ca_choice, ca_fired = apply_ca_tiebreak(
                candidates[0],
                candidates[1],
                c1.char_dists,
                p_pass_len,
                allowed_chars,
                core_confusion_sets,
                cfg,
            )
            ca_tiebreak_fired = ca_fired
            best_candidate_idx = ca_choice

        # Reason-code precedence (LOCKED): FINAL_* > TIEBREAK_CA_BIAS > TIEBREAK_DUP_AGREE > INTERIM_*
        if done:
            # reason already set to FINAL_* by evaluate_done
            pass
        elif ca_tiebreak_fired:
            reason = ReasonCode.TIEBREAK_CA_BIAS
        elif dup_agree_fired:
            reason = ReasonCode.TIEBREAK_DUP_AGREE
        # else: reason stays as INTERIM_* from evaluate_done

        best_string = (
            candidates[best_candidate_idx].string
            if not done
            else (h1.string if h1 is not None else candidates[0].string)
        )
        h1_string = h1.string if h1 is not None else ""
        h2_string = h2.string if h2 is not None else None

        # Step 13: Build debug payload if enabled
        debug_payload = None
        if cfg.agg_enable_debug_payload:
            debug_payload = _build_debug_payload(
                units,
                weights,
                candidates,
                p_pass_len,
                l1,
                l2,
                all_overridden,
                dup_agree_fired,
                ca_tiebreak_fired,
                h1,
                h2,
                allowed_chars,
                eos_index,
            )

        logger.info(
            "aggregate_pass: track=%s, pass=%d, best=%s, bucket=%s, done=%s, reason=%s",
            track_id,
            pass_num,
            best_string,
            h1_bucket.value,
            done,
            reason.value,
        )

        return AggregationDecision(
            best_string=best_string,
            confidence_bucket=h1_bucket,
            done=done,
            reason_code=reason,
            h1_string=h1_string,
            h2_string=h2_string,
            pass_num=pass_num,
            debug_payload=debug_payload,
        )


# ---------------------------------------------------------------------------
# Chunk-05: Confidence + DONE + Tiebreak
# ---------------------------------------------------------------------------

# CA regex patterns for US license plates
_CA_PATTERN_1 = re.compile(r"^[0-9][A-Z]{3}[0-9]{3}$")
_CA_PATTERN_2 = re.compile(r"^[0-9]{4}[A-Z]{3}$")


def compute_confidence_bucket(
    char_dists: np.ndarray,
    decoded_chars: list[int],
    allowed_chars: str,
    core_confusion_sets: list[frozenset[str]],
    cfg: ConsumerConfig,
) -> ConfidenceBucket:
    """Compute confidence bucket from aggregated character distributions.

    Per position: extract top-1 prob, top-2 prob, margin, and decoded/runner-up chars.
    Confusion check: if {decoded_char, runner_up_char} is a subset of any core confusion
    set AND margin < agg_confusion_margin_high → confusion_blocked.

    HIGH if mean_p1 >= threshold AND min_margin >= threshold AND NOT confusion_blocked.
    MED if mean_p1 >= threshold AND mean_margin >= threshold.
    Else LOW.

    Args:
        char_dists: (L, n_allowed_chars) per-position distributions.
        decoded_chars: List of allowed-char indices (argmax per position).
        allowed_chars: Character set string.
        core_confusion_sets: List of frozenset pairs for confusion detection.
        cfg: Config with confidence thresholds.

    Returns:
        ConfidenceBucket (HIGH, MED, or LOW).
    """
    L = len(decoded_chars)
    if L == 0:
        return ConfidenceBucket.LOW

    p1_values: list[float] = []
    margins: list[float] = []
    confusion_blocked = False

    for t in range(L):
        probs = char_dists[t]
        top1_idx = decoded_chars[t]
        p1 = float(probs[top1_idx])

        # Find runner-up
        temp = probs.copy()
        temp[top1_idx] = -1.0
        top2_idx = int(np.argmax(temp))
        p2 = float(probs[top2_idx])
        margin = p1 - p2

        p1_values.append(p1)
        margins.append(margin)

        # Confusion check
        if top1_idx < len(allowed_chars) and top2_idx < len(allowed_chars):
            char1 = allowed_chars[top1_idx]
            char2 = allowed_chars[top2_idx]
            pair = frozenset((char1, char2))
            if pair in core_confusion_sets and margin < cfg.agg_confusion_margin_high:
                confusion_blocked = True
                logger.debug(
                    "confusion at pos %d: %s/%s margin=%.4f < %.4f",
                    t,
                    char1,
                    char2,
                    margin,
                    cfg.agg_confusion_margin_high,
                )

    mean_p1 = sum(p1_values) / L
    min_margin = min(margins)
    mean_margin = sum(margins) / L

    # HIGH gate
    if (
        mean_p1 >= cfg.agg_high_mean_p1
        and min_margin >= cfg.agg_high_min_margin
        and not confusion_blocked
    ):
        return ConfidenceBucket.HIGH

    # MED gate
    if mean_p1 >= cfg.agg_med_mean_p1 and mean_margin >= cfg.agg_med_mean_margin:
        return ConfidenceBucket.MED

    return ConfidenceBucket.LOW


def evaluate_done(
    h1: HypothesisState | None,
    h2: HypothesisState | None,
    pass_c1: CandidateResult,
    core_confusion_sets: list[frozenset[str]],
    allowed_chars: str,
    pass_len_dist: np.ndarray,
    h1_bucket: ConfidenceBucket,
    pass_num: int,
    cfg: ConsumerConfig,
) -> tuple[bool, ReasonCode]:
    """Two-pathway DONE evaluation.

    Path A (one-pass): Uses pass_c1 (NOT EMA). Requires strong length agreement,
    all positions with p1 >= threshold and margin >= threshold, confusion positions
    with margin >= stricter threshold.

    Path B (multi-pass): Uses H1 EMA state. Requires HIGH bucket, sufficient wins,
    and H2 non-competitive (None/stale/lower-bucket/score-gap > threshold).

    Args:
        h1: Current H1 hypothesis (may be None).
        h2: Current H2 hypothesis (may be None).
        pass_c1: First candidate from this pass (for Path A).
        core_confusion_sets: Confusion pair sets.
        allowed_chars: Character set string.
        pass_len_dist: Pass-level length distribution.
        h1_bucket: Confidence bucket for H1.
        pass_num: Current pass number.
        cfg: Config with DONE thresholds.

    Returns:
        (done, reason_code) tuple.
    """
    # Path A: one-pass strong agreement
    path_a_pass = _check_path_a(pass_c1, core_confusion_sets, allowed_chars, pass_len_dist, cfg)
    if path_a_pass:
        return True, ReasonCode.FINAL_ONEPASS_AGREE

    # Path B: multi-pass stabilization
    if h1 is not None:
        path_b_pass = _check_path_b(h1, h2, h1_bucket, cfg)
        if path_b_pass:
            return True, ReasonCode.FINAL_MULTIPASS_STABLE

    # Neither path passed — determine interim reason
    has_confusion = _has_confusion_below_threshold(pass_c1, core_confusion_sets, allowed_chars, cfg)
    if has_confusion:
        return False, ReasonCode.INTERIM_CONFUSION

    return False, ReasonCode.INTERIM_AMBIGUOUS


def _check_path_a(
    pass_c1: CandidateResult,
    core_confusion_sets: list[frozenset[str]],
    allowed_chars: str,
    pass_len_dist: np.ndarray,
    cfg: ConsumerConfig,
) -> bool:
    """Check Path A one-pass DONE conditions."""
    L = pass_c1.candidate_length

    # Length probability gate
    if L - 1 >= len(pass_len_dist):
        return False
    len_prob = float(pass_len_dist[L - 1])
    if len_prob < cfg.agg_onepass_len_prob:
        return False

    # Per-position checks
    for t in range(L):
        probs = pass_c1.char_dists[t]
        top1_idx = int(np.argmax(probs))
        p1 = float(probs[top1_idx])

        if p1 < cfg.agg_onepass_min_p1:
            return False

        # Margin check
        temp = probs.copy()
        temp[top1_idx] = -1.0
        top2_idx = int(np.argmax(temp))
        p2 = float(probs[top2_idx])
        margin = p1 - p2

        if margin < cfg.agg_onepass_min_margin:
            return False

        # Confusion position: stricter margin
        if top1_idx < len(allowed_chars) and top2_idx < len(allowed_chars):
            char1 = allowed_chars[top1_idx]
            char2 = allowed_chars[top2_idx]
            pair = frozenset((char1, char2))
            if pair in core_confusion_sets:
                if margin < cfg.agg_onepass_confusion_margin:
                    return False

    return True


def _check_path_b(
    h1: HypothesisState,
    h2: HypothesisState | None,
    h1_bucket: ConfidenceBucket,
    cfg: ConsumerConfig,
) -> bool:
    """Check Path B multi-pass DONE conditions."""
    # Require HIGH confidence
    if h1_bucket != ConfidenceBucket.HIGH:
        return False

    # Require minimum wins
    if h1.win_count < cfg.agg_multipass_min_wins:
        return False

    # H2 must be non-competitive
    if h2 is not None:
        score_gap = abs(h1.score - h2.score)
        if score_gap < cfg.agg_hyp_gap_done:
            return False

    return True


def _has_confusion_below_threshold(
    pass_c1: CandidateResult,
    core_confusion_sets: list[frozenset[str]],
    allowed_chars: str,
    cfg: ConsumerConfig,
) -> bool:
    """Check if any position has a confusion pair below the confusion margin."""
    L = pass_c1.candidate_length
    for t in range(L):
        probs = pass_c1.char_dists[t]
        top1_idx = int(np.argmax(probs))
        temp = probs.copy()
        temp[top1_idx] = -1.0
        top2_idx = int(np.argmax(temp))

        if top1_idx < len(allowed_chars) and top2_idx < len(allowed_chars):
            char1 = allowed_chars[top1_idx]
            char2 = allowed_chars[top2_idx]
            pair = frozenset((char1, char2))
            if pair in core_confusion_sets:
                margin = float(probs[top1_idx] - probs[top2_idx])
                if margin < cfg.agg_confusion_margin_high:
                    return True
    return False


def apply_ca_tiebreak(
    c1: CandidateResult,
    c2: CandidateResult,
    c1_char_dists: np.ndarray,
    pass_len_dist: np.ndarray,
    allowed_chars: str,
    core_confusion_sets: list[frozenset[str]],
    cfg: ConsumerConfig,
) -> tuple[int, bool]:
    """Confusion-aware regex tiebreak with ambiguity dominance gate.

    1. Score gate: if score difference >= tie_score → no tiebreak.
    2. Ambiguity dominance gate: >=50% confusion positions OR length_ambiguity >= 0.30.
    3. CA regex match: check both candidates against US plate patterns.

    Args:
        c1: First candidate (typically H1's best).
        c2: Second candidate.
        c1_char_dists: Character distributions from C1 for confusion analysis.
        pass_len_dist: Pass-level length distribution.
        allowed_chars: Character set string.
        core_confusion_sets: Core confusion pairs.
        cfg: Config with tiebreak thresholds.

    Returns:
        (choice, fired) where choice is 0 (C1) or 1 (C2), fired is True if
        tiebreak logic was applied.
    """
    # Step 1: Score gate
    score_diff = abs(c1.score - c2.score)
    if score_diff >= cfg.agg_tie_score:
        return 0, False

    # Step 2: Ambiguity dominance gate
    # Build all confusion sets (core + extended)
    all_confusion_sets = list(core_confusion_sets)
    for pair in cfg.agg_extended_confusion_pairs:
        all_confusion_sets.append(frozenset(pair))

    L = c1_char_dists.shape[0]
    confusion_count = 0
    for t in range(L):
        probs = c1_char_dists[t]
        top1_idx = int(np.argmax(probs))
        temp = probs.copy()
        temp[top1_idx] = -1.0
        top2_idx = int(np.argmax(temp))

        if top1_idx < len(allowed_chars) and top2_idx < len(allowed_chars):
            char1 = allowed_chars[top1_idx]
            char2 = allowed_chars[top2_idx]
            pair = frozenset((char1, char2))
            if pair in all_confusion_sets:
                confusion_count += 1

    confusion_fraction = confusion_count / L if L > 0 else 0.0
    length_ambiguity = 1.0 - float(np.max(pass_len_dist))

    confusion_dominated = confusion_fraction >= 0.5 or length_ambiguity >= 0.30
    if not confusion_dominated:
        return 0, False

    # Step 3: CA regex match
    c1_matches = _matches_ca_pattern(c1.string)
    c2_matches = _matches_ca_pattern(c2.string)

    if c2_matches and not c1_matches:
        logger.info(
            "ca_tiebreak: C2 '%s' matches CA, C1 '%s' doesn't → swap",
            c2.string,
            c1.string,
        )
        return 1, True
    elif c1_matches and not c2_matches:
        logger.info(
            "ca_tiebreak: C1 '%s' matches CA, C2 '%s' doesn't → keep C1, mark fired",
            c1.string,
            c2.string,
        )
        return 0, True

    # Both match or neither matches → no swap
    return 0, False


def _matches_ca_pattern(string: str) -> bool:
    """Check if string matches California license plate patterns."""
    return bool(_CA_PATTERN_1.match(string) or _CA_PATTERN_2.match(string))


def _build_debug_payload(
    units: list[EvidenceUnit],
    weights: np.ndarray,
    candidates: list[CandidateResult],
    p_pass_len: np.ndarray,
    l1: int,
    l2: int,
    overridden_positions: list[int],
    dup_agree_fired: bool,
    ca_tiebreak_fired: bool,
    h1: HypothesisState | None,
    h2: HypothesisState | None,
    allowed_chars: str,
    eos_index: int,
) -> AggregationDebugPayload:
    """Build the 16-field debug payload."""
    c1 = candidates[0]
    L = c1.candidate_length

    # Per-ROI info
    per_roi_decoded = [u.group_id for u in units]
    per_roi_quality = [u.quality for u in units]
    per_roi_is_enhanced = [u.has_enhanced for u in units]
    per_roi_group_id = [u.group_id for u in units]

    # Per-position info from C1
    per_position_top1: list[float] = []
    per_position_top2: list[float] = []
    per_position_margin: list[float] = []
    per_position_top_k_chars: list[list[str]] = []

    for t in range(L):
        probs = c1.char_dists[t]
        sorted_indices = np.argsort(probs)[::-1]
        p1 = float(probs[sorted_indices[0]])
        p2 = float(probs[sorted_indices[1]]) if len(sorted_indices) > 1 else 0.0
        per_position_top1.append(p1)
        per_position_top2.append(p2)
        per_position_margin.append(p1 - p2)

        top_k = []
        for idx in sorted_indices[:3]:
            if int(idx) < len(allowed_chars):
                top_k.append(allowed_chars[int(idx)])
        per_position_top_k_chars.append(top_k)

    return AggregationDebugPayload(
        per_roi_decoded=per_roi_decoded,
        per_roi_quality=per_roi_quality,
        per_roi_is_enhanced=per_roi_is_enhanced,
        per_roi_group_id=per_roi_group_id,
        per_position_top1=per_position_top1,
        per_position_top2=per_position_top2,
        per_position_margin=per_position_margin,
        per_position_top_k_chars=per_position_top_k_chars,
        length_top2_lengths=(l1, l2),
        length_top2_probs=(float(p_pass_len[l1 - 1]), float(p_pass_len[l2 - 1])),
        quality_weights=[float(w) for w in weights],
        overridden_positions=overridden_positions,
        dup_agree_fired=dup_agree_fired,
        ca_tiebreak_fired=ca_tiebreak_fired,
        h1_win_count=h1.win_count if h1 is not None else 0,
        h2_win_count=h2.win_count if h2 is not None else None,
    )
