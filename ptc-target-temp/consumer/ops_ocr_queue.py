"""OCR ready queue — commit scoring, gate logic, and queue state."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

from producer.models import TrackId

from .config import ConsumerConfig
from .models import CommitRecord, InFlightRecord, OcrWorkItem, TrackMetrics, TrackState
from .ops_batch_select import compute_ocr_likelihood_score

if TYPE_CHECKING:
    from .models import EnhancedBatchSelection, ProcessorOutput

logger = logging.getLogger(__name__)


def _compute_commit_score(base_roi_scores: list[float]) -> float:
    """Compute multi-ROI commit score with smooth support ramp.

    Args:
        base_roi_scores: ROI OCR-likelihood scores, sorted descending.

    Returns:
        Composite score in [0, 1]. Anchor (top-2) retains >= 67% weight at
        any batch size. Support ramps linearly, capping at B=8.

    Algorithm:
        B=0 -> 0.0
        B=1 -> s1
        B>=2 -> anchor = 0.5*s1 + 0.5*s2
                support_count = B - 2
                support_weight = min(support_count / 6.0, 1.0) * 0.33
                if support_count == 0: return anchor
                support_mean = mean(s3..sB)
                S = (1 - support_weight) * anchor + support_weight * support_mean
    """
    b = len(base_roi_scores)

    if b == 0:
        return 0.0

    if not all(math.isfinite(s) for s in base_roi_scores):
        logger.warning(
            "non-finite score detected in commit score inputs: %s",
            base_roi_scores,
        )
        return 0.0

    if b == 1:
        return base_roi_scores[0]

    # B >= 2: anchor from top-2
    anchor = 0.5 * base_roi_scores[0] + 0.5 * base_roi_scores[1]

    support_count = b - 2
    if support_count == 0:
        return anchor

    support_weight = min(support_count / 6.0, 1.0) * 0.33
    support_mean = sum(base_roi_scores[2:]) / support_count

    return (1.0 - support_weight) * anchor + support_weight * support_mean


def _evaluate_commit_gate(
    track_state: TrackState,
    candidate_score: float,
    timestamp_ms: int,
    cfg: ConsumerConfig,
) -> tuple[bool, str]:
    """Evaluate 4-rule commit gate.

    Rules evaluated in order:
        1. done -> reject
        2. no prior commit -> accept (first_commit)
        3. improvement >= margin -> accept
        4. staleness backstop -> accept if qualifying
        5. fallthrough -> reject (below_margin)

    Args:
        track_state: Current track state (read-only, not mutated).
        candidate_score: Score from _compute_commit_score.
        timestamp_ms: Current timestamp in milliseconds.
        cfg: Consumer configuration with gate parameters.

    Returns:
        (accepted, reason) tuple.
    """
    # Rule 1: done track always rejects
    if track_state.done:
        return False, "track_done"

    # Rule 2: first commit always accepted
    if track_state.best_commit is None:
        return True, "first_commit"

    # Rule 3: improvement above margin
    # Subtraction form avoids FP accumulation error at boundary
    if candidate_score - track_state.best_commit.commit_score >= cfg.commit_margin:
        return True, "improvement"

    # Rule 4: staleness backstop
    if (
        cfg.enable_staleness
        and (timestamp_ms - track_state.best_commit.created_at_ms) >= cfg.max_staleness_ms
        and candidate_score >= cfg.stale_min_score
    ):
        return True, "stale_commit"

    # Rule 5: fallthrough
    return False, "below_margin"


class OcrReadyQueue:
    """Per-track OCR commit queue with gate-based acceptance.

    Manages track lifecycle: submit_candidate evaluates the commit gate
    and maintains per-track commit state with supersedence tracking.

    Not thread-safe. Intended for single-threaded consumer pipeline use.
    """

    def __init__(self, cfg: ConsumerConfig) -> None:
        self._cfg = cfg
        self._tracks: dict[TrackId, TrackState] = {}
        self._done_metrics: dict[TrackId, TrackMetrics] = {}

    def submit_candidate(
        self,
        candidate: EnhancedBatchSelection,
        processed: ProcessorOutput,
        src_bin_version: int,
        timestamp_ms: int,
    ) -> bool:
        """Evaluate commit gate and update track state if accepted.

        Args:
            candidate: Selected batch with base ROIs for scoring.
            processed: GPU-processed tensors from the batch.
            src_bin_version: Source bin version from the producer buffer.
            timestamp_ms: Current timestamp in milliseconds.

        Returns:
            True if committed, False if rejected.
        """
        track_id = candidate.track_id

        # Tombstone check: reject tracks already marked done
        if track_id in self._done_metrics:
            return False

        # Step 1: Capture per-ROI quality in base-rois order (unsorted — H9)
        per_roi_quality = [
            compute_ocr_likelihood_score(roi, candidate.base_rois, self._cfg)
            for roi in candidate.base_rois
        ]
        # Sorted descending copy for commit scoring
        scores = sorted(per_roi_quality, reverse=True)

        # Step 2: B=0 early exit
        b = len(scores)
        if b == 0:
            if track_id in self._tracks:
                self._tracks[track_id].metrics.total_candidates_received += 1
            return False

        # Step 3: Compute commit score
        commit_score = _compute_commit_score(scores)

        # Step 4: Get or create TrackState
        if track_id not in self._tracks:
            self._tracks[track_id] = TrackState(
                track_id=track_id,
                metrics=TrackMetrics(first_seen_ms=timestamp_ms),
            )
        track_state = self._tracks[track_id]

        # Step 5: Increment total_candidates_received
        track_state.metrics.total_candidates_received += 1

        # Step 6: Evaluate gate
        accept, reason = _evaluate_commit_gate(
            track_state,
            commit_score,
            timestamp_ms,
            self._cfg,
        )

        # Step 7: If not accepted, log and return False
        if not accept:
            s_best = (
                track_state.best_commit.commit_score
                if track_state.best_commit is not None
                else None
            )
            logger.info(
                "submit track_id=%s src_bin=%d S_new=%.4f S_best=%s decision=reject reason=%s B=%d",
                track_id,
                src_bin_version,
                commit_score,
                f"{s_best:.4f}" if s_best is not None else "none",
                reason,
                b,
            )
            return False

        # Step 8: Accepted — create new commit record
        # 8a: Check supersedence (old commit replaced before consumption)
        superseded = (
            track_state.best_commit is not None
            and track_state.best_commit.commit_version > track_state.consumed_commit_version
        )
        if superseded:
            track_state.metrics.commits_superseded_before_consumption += 1

        # 8b: Compute new commit_version
        if track_state.best_commit is not None:
            new_version = track_state.best_commit.commit_version + 1
        else:
            new_version = 1

        # 8c: Populate duplicate_groups (identity mapping if empty — no enhanced ROIs)
        groups = candidate.duplicate_groups
        if not groups:
            groups = {str(i): [i] for i in range(len(candidate.base_rois))}

        # 8d: Create CommitRecord
        new_record = CommitRecord(
            commit_version=new_version,
            source_bin_version=src_bin_version,
            commit_score=commit_score,
            batch_tensor=processed.batch_tensor,
            is_enhanced=processed.is_enhanced,
            base_roi_scores=scores,
            created_at_ms=timestamp_ms,
            duplicate_groups=groups,
            per_roi_quality=per_roi_quality,
        )

        # 8d-e: Update track state
        track_state.best_commit = new_record
        track_state.metrics.total_commits += 1

        decision = "stale_commit" if reason == "stale_commit" else "commit"
        logger.info(
            "submit track_id=%s src_bin=%d S_new=%.4f "
            "decision=%s reason=%s commit_version=%d B=%d superseded=%s",
            track_id,
            src_bin_version,
            commit_score,
            decision,
            reason,
            new_version,
            b,
            superseded,
        )

        # 8f: Return True
        return True

    def pull_next(self, worker_id: str, timestamp_ms: int) -> OcrWorkItem | None:
        """Dispatch highest-priority eligible work item.

        Eligibility: not done, has best_commit, no in_flight, and
        best_commit.commit_version > consumed_commit_version.

        Priority sort:
            1. P0 (never consumed) before P1 (previously consumed)
            2. Oldest best_commit.created_at_ms first
            3. Lexicographic track_id tiebreak

        Args:
            worker_id: Identifier for the OCR worker receiving the item.
            timestamp_ms: Current timestamp in milliseconds.

        Returns:
            OcrWorkItem if eligible work exists, None otherwise.
        """
        # In-flight timeout sweep — O(tracks), bounded by ocr_queue_capacity.
        # Note: timeout sweep and dispatch share the same timestamp_ms.
        timeout_ms = self._cfg.in_flight_timeout_ms
        for ts in self._tracks.values():
            if (
                ts.in_flight is not None
                and (timestamp_ms - ts.in_flight.dispatched_at_ms) > timeout_ms
            ):
                logger.warning(
                    "in-flight timeout track_id=%s commit_version=%d "
                    "dispatched_at_ms=%d age_ms=%d timeout_ms=%d",
                    ts.track_id,
                    ts.in_flight.commit_version,
                    ts.in_flight.dispatched_at_ms,
                    timestamp_ms - ts.in_flight.dispatched_at_ms,
                    timeout_ms,
                )
                ts.in_flight = None

        eligible = [
            ts
            for ts in self._tracks.values()
            if not ts.done
            and ts.best_commit is not None
            and ts.in_flight is None
            and ts.best_commit.commit_version > ts.consumed_commit_version
        ]

        if not eligible:
            logger.debug("no eligible work")
            return None

        eligible.sort(
            key=lambda ts: (
                0 if ts.consumed_commit_version == 0 else 1,  # P0 before P1
                ts.best_commit.created_at_ms,  # type: ignore[union-attr]  # filtered above
                ts.track_id,  # lexicographic tiebreak
            ),
        )

        winner = eligible[0]
        assert winner.best_commit is not None  # guaranteed by filter

        # Create in-flight record
        winner.in_flight = InFlightRecord(
            commit_version=winner.best_commit.commit_version,
            dispatched_at_ms=timestamp_ms,
            worker_id=worker_id,
        )

        priority_tier = "P0" if winner.consumed_commit_version == 0 else "P1"
        logger.info(
            "dispatch track_id=%s commit_version=%d worker_id=%s N=%d priority_tier=%s",
            winner.track_id,
            winner.best_commit.commit_version,
            worker_id,
            winner.best_commit.batch_tensor.shape[0],
            priority_tier,
        )

        return OcrWorkItem(
            track_id=winner.track_id,
            commit_version=winner.best_commit.commit_version,
            source_bin_version=winner.best_commit.source_bin_version,
            batch_tensor=winner.best_commit.batch_tensor,
            is_enhanced=winner.best_commit.is_enhanced,
            duplicate_groups=winner.best_commit.duplicate_groups,
            per_roi_quality=winner.best_commit.per_roi_quality,
        )

    def finish_commit(
        self,
        track_id: TrackId,
        commit_version: int,
        ocr_result: Any = None,
    ) -> bool:
        """Mark a dispatched commit as consumed.

        Args:
            track_id: Track whose commit was processed.
            commit_version: Version of the commit that was processed.
            ocr_result: OCR result (accepted for future use, not stored).

        Returns:
            True if successfully consumed, False on mismatch or not found.
        """
        track_state = self._tracks.get(track_id)
        if track_state is None:
            return False

        if track_state.in_flight is None:
            return False

        if track_state.in_flight.commit_version != commit_version:
            return False

        track_state.in_flight = None
        track_state.consumed_commit_version = commit_version
        track_state.metrics.total_consumed += 1

        log_msg = "consumed track_id=%s commit_version=%d"
        log_args: list[object] = [track_id, commit_version]
        if ocr_result is not None and hasattr(ocr_result, "raw_confidence"):
            log_msg += " raw_confidence=%.4f"
            log_args.append(ocr_result.raw_confidence)
        logger.info(log_msg, *log_args)

        return True

    def fail_commit(
        self,
        track_id: TrackId,
        commit_version: int,
        reason: str = "unknown",
    ) -> bool:
        """Clear in-flight state without recording OCR result.

        Track becomes re-eligible for pull_next. Does NOT update
        consumed_commit_version.

        Args:
            track_id: Track whose commit failed.
            commit_version: Version of the commit that failed.
            reason: Reason for failure (for logging).

        Returns:
            True if in-flight cleared, False on mismatch or not found.
        """
        track_state = self._tracks.get(track_id)
        if track_state is None:
            logger.warning(
                "fail_commit track_id=%s not found commit_version=%d reason=%s",
                track_id,
                commit_version,
                reason,
            )
            return False

        if track_state.in_flight is None:
            logger.warning(
                "fail_commit track_id=%s no in_flight record commit_version=%d reason=%s",
                track_id,
                commit_version,
                reason,
            )
            return False

        if track_state.in_flight.commit_version != commit_version:
            logger.warning(
                "fail_commit track_id=%s version mismatch: expected=%d got=%d reason=%s",
                track_id,
                track_state.in_flight.commit_version,
                commit_version,
                reason,
            )
            return False

        track_state.in_flight = None
        logger.info(
            "fail_commit track_id=%s commit_version=%d reason=%s",
            track_id,
            commit_version,
            reason,
        )
        return True

    def mark_done(
        self,
        track_id: TrackId,
        reason: str = "done",
        timestamp_ms: int | None = None,
    ) -> bool:
        """Externally mark a track as complete.

        Releases tensor references, force-evicts any in-flight commit,
        and retains metrics in _done_metrics for post-mortem queries.

        Args:
            track_id: Track to finalize.
            reason: Reason for completion (e.g. 'done', 'lost').
            timestamp_ms: When the track was marked done.

        Returns:
            True if track was found and finalized, False if unknown.
        """
        track_state = self._tracks.get(track_id)
        if track_state is None:
            return False

        track_state.done = True
        track_state.done_at_ms = timestamp_ms

        # Release tensor reference
        track_state.best_commit = None

        # Force-evict in-flight if active
        if track_state.in_flight is not None:
            logger.warning("force-evicting in-flight commit for track %s", track_id)
            track_state.in_flight = None

        # Retain metrics, then remove track
        m = track_state.metrics
        self._done_metrics[track_id] = m

        # Evict oldest done_metrics if over capacity
        if self._cfg.max_done_metrics > 0 and len(self._done_metrics) > self._cfg.max_done_metrics:
            evict_count = len(self._done_metrics) // 2
            sorted_ids = sorted(
                self._done_metrics,
                key=lambda tid: self._done_metrics[tid].first_seen_ms,
            )
            for tid in sorted_ids[:evict_count]:
                del self._done_metrics[tid]
            logger.info(
                "evicted %d done_metrics entries (cap=%d, was=%d)",
                evict_count,
                self._cfg.max_done_metrics,
                self._cfg.max_done_metrics + evict_count,
            )

        del self._tracks[track_id]

        logger.info(
            "done track_id=%s reason=%s first_seen_ms=%d "
            "total_candidates_received=%d total_commits=%d "
            "commits_superseded=%d total_consumed=%d",
            track_id,
            reason,
            m.first_seen_ms,
            m.total_candidates_received,
            m.total_commits,
            m.commits_superseded_before_consumption,
            m.total_consumed,
        )

        return True

    def get_metrics(self, track_id: TrackId) -> TrackMetrics | None:
        """Return metrics for an active or completed track.

        Args:
            track_id: Track to query.

        Returns:
            TrackMetrics if track is known (active or done), None otherwise.
        """
        if track_id in self._tracks:
            return self._tracks[track_id].metrics
        return self._done_metrics.get(track_id)
