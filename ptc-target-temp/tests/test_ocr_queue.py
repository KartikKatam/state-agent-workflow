"""Tests for OCR ready queue."""

from __future__ import annotations

import copy
import logging
from dataclasses import fields
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import torch
from hypothesis import given, settings, strategies as st

from consumer.config import ConsumerConfig
from consumer.models import (
    CommitRecord,
    EnhancedBatchSelection,
    InFlightRecord,
    OcrWorkItem,
    ProcessorOutput,
    RoiRichQuality,
    TrackMetrics,
    TrackState,
)
from consumer.ops_ocr_queue import (
    OcrReadyQueue,
    _compute_commit_score,
    _evaluate_commit_gate,
)

# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_cfg(**overrides: object) -> ConsumerConfig:
    """Build ConsumerConfig with optional overrides."""
    return ConsumerConfig(**overrides)  # type: ignore[arg-type]


def make_metrics(first_seen_ms: int = 0) -> TrackMetrics:
    """Build TrackMetrics with sensible defaults."""
    return TrackMetrics(first_seen_ms=first_seen_ms)


def make_track_state(
    track_id: str = "t1",
    best_commit: CommitRecord | None = None,
    in_flight: InFlightRecord | None = None,
    consumed_commit_version: int = 0,
    done: bool = False,
    done_at_ms: int | None = None,
    first_seen_ms: int = 0,
) -> TrackState:
    """Build TrackState with sensible defaults."""
    return TrackState(
        track_id=track_id,
        best_commit=best_commit,
        in_flight=in_flight,
        consumed_commit_version=consumed_commit_version,
        done=done,
        done_at_ms=done_at_ms,
        metrics=make_metrics(first_seen_ms),
    )


def make_commit(
    commit_score: float = 0.80,
    commit_version: int = 1,
    created_at_ms: int = 0,
) -> CommitRecord:
    """Build a minimal CommitRecord for gate tests.

    Uses None for tensor/array fields since gate logic never inspects them.
    """
    return CommitRecord(
        commit_version=commit_version,
        source_bin_version=1,
        commit_score=commit_score,
        batch_tensor=None,  # type: ignore[arg-type]
        is_enhanced=None,  # type: ignore[arg-type]
        base_roi_scores=[commit_score],
        created_at_ms=created_at_ms,
    )


# ===========================================================================
# _compute_commit_score tests
# ===========================================================================


class TestComputeCommitScore:
    """Tests for multi-ROI commit score computation."""

    def test_b0_returns_zero(self) -> None:
        """Empty list returns 0.0."""
        assert _compute_commit_score([]) == 0.0

    def test_b1_returns_input(self) -> None:
        """Single score [0.9] returns 0.9; B=1 bypasses anchor/support."""
        assert _compute_commit_score([0.9]) == 0.9

    def test_b2_pure_anchor(self) -> None:
        """[0.9, 0.8] == 0.85 exact; pure 50/50 anchor, zero support weight."""
        assert _compute_commit_score([0.9, 0.8]) == pytest.approx(0.85)

    def test_b3_moderate_support(self) -> None:
        """[0.9, 0.8, 0.7]: support_weight = min(1/6, 1) * 0.33 ≈ 0.055.

        anchor = 0.5*0.9 + 0.5*0.8 = 0.85
        support_mean = 0.7
        S = (1 - 0.055) * 0.85 + 0.055 * 0.7 ≈ 0.84175
        """
        result = _compute_commit_score([0.9, 0.8, 0.7])
        assert result == pytest.approx(0.84175)

    def test_b3_weak_support_minimal_damage(self) -> None:
        """[0.9, 0.8, 0.0]: weak s3 drops score by at most ~0.055*anchor.

        anchor = 0.85
        support_mean = 0.0
        support_weight = 0.055
        S = 0.945 * 0.85 + 0.055 * 0.0 = 0.80325
        """
        result = _compute_commit_score([0.9, 0.8, 0.0])
        assert result == pytest.approx(0.80325)

    def test_b8_full_support_split(self) -> None:
        """[0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]: full 67/33 split at B=8.

        anchor = 0.85
        support_count = 6, support_weight = min(6/6, 1) * 0.33 = 0.33
        support_mean = mean(0.7, 0.6, 0.5, 0.4, 0.3, 0.2) = 0.45
        S = 0.67 * 0.85 + 0.33 * 0.45 = 0.5695 + 0.1485 = 0.718
        """
        scores = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]
        result = _compute_commit_score(scores)
        assert result == pytest.approx(0.718, abs=1e-6)

    def test_b8_all_identical(self) -> None:
        """[0.75]*8 == 0.75; anchor=support=0.75, weights irrelevant for uniform."""
        assert _compute_commit_score([0.75] * 8) == pytest.approx(0.75)

    def test_support_weight_capped_above_b8(self) -> None:
        """[0.8]*10 == 0.8; support_weight still 0.33, capped via min(count/6, 1)."""
        assert _compute_commit_score([0.8] * 10) == pytest.approx(0.8)

    def test_b_fluctuation_continuity(self) -> None:
        """abs(cs([0.9,0.8]) - cs([0.9,0.8,0.0])) < 0.06.

        No discontinuous jump across B boundary.
        """
        s2 = _compute_commit_score([0.9, 0.8])
        s3 = _compute_commit_score([0.9, 0.8, 0.0])
        assert abs(s2 - s3) < 0.06

    @given(
        scores=st.lists(
            st.floats(min_value=0.0, max_value=1.0),
            min_size=0,
            max_size=10,
        ).map(lambda xs: sorted(xs, reverse=True)),
    )
    @settings(max_examples=200)
    def test_scores_in_01_output_in_01(self, scores: list[float]) -> None:
        """Property: scores in [0,1] sorted desc -> 0.0 <= result <= 1.0."""
        result = _compute_commit_score(scores)
        assert 0.0 <= result <= 1.0

    @given(
        x=st.floats(min_value=0.0, max_value=1.0),
        n=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=200)
    def test_identical_scores_returns_same(self, x: float, n: int) -> None:
        """Property: [x]*n -> pytest.approx(x) for any x in [0,1], n in [1,10]."""
        result = _compute_commit_score([x] * n)
        assert result == pytest.approx(x)


# ===========================================================================
# _evaluate_commit_gate tests
# ===========================================================================


class TestEvaluateCommitGate:
    """Tests for 4-rule commit gate evaluation."""

    def test_done_track_always_rejects(self) -> None:
        """Rule 1: done track -> (False, 'track_done') regardless of score."""
        ts = make_track_state(done=True)
        ok, reason = _evaluate_commit_gate(ts, 1.0, 100, make_cfg())
        assert ok is False
        assert reason == "track_done"

    def test_first_commit_accepted(self) -> None:
        """Rule 2: no prior commit -> (True, 'first_commit')."""
        ts = make_track_state(best_commit=None)
        ok, reason = _evaluate_commit_gate(ts, 0.8, 100, make_cfg())
        assert ok is True
        assert reason == "first_commit"

    def test_improvement_above_margin(self) -> None:
        """Rule 3: 0.85 vs 0.80 existing, margin=0.04 -> (True, 'improvement').

        Delta 0.05 >= margin 0.04.
        """
        ts = make_track_state(best_commit=make_commit(commit_score=0.80))
        ok, reason = _evaluate_commit_gate(ts, 0.85, 100, make_cfg())
        assert ok is True
        assert reason == "improvement"

    def test_below_margin_rejected(self) -> None:
        """0.83 vs 0.80 existing, margin=0.04 -> (False, 'below_margin').

        Delta 0.03 < margin 0.04.
        """
        ts = make_track_state(best_commit=make_commit(commit_score=0.80))
        ok, reason = _evaluate_commit_gate(ts, 0.83, 100, make_cfg())
        assert ok is False
        assert reason == "below_margin"

    def test_exactly_at_margin_accepted(self) -> None:
        """0.54 vs 0.50 existing, margin=0.04 -> (True, 'improvement').

        Delta 0.04 >= margin 0.04 (boundary). Uses 0.50/0.54 because
        0.54 - 0.50 is exactly representable as >= 0.04 in IEEE 754,
        while 0.84 - 0.80 is not.
        """
        ts = make_track_state(best_commit=make_commit(commit_score=0.50))
        ok, reason = _evaluate_commit_gate(ts, 0.54, 100, make_cfg())
        assert ok is True
        assert reason == "improvement"

    def test_staleness_fires(self) -> None:
        """Rule 4: stale (1300 >= 1200) AND score >= 0.55 -> (True, 'stale_commit')."""
        ts = make_track_state(
            best_commit=make_commit(commit_score=0.80, created_at_ms=0),
        )
        ok, reason = _evaluate_commit_gate(ts, 0.60, 1300, make_cfg())
        assert ok is True
        assert reason == "stale_commit"

    def test_staleness_blocked_score_too_low(self) -> None:
        """Stale but 0.50 < stale_min_score 0.55 -> (False, 'below_margin')."""
        ts = make_track_state(
            best_commit=make_commit(commit_score=0.80, created_at_ms=0),
        )
        ok, reason = _evaluate_commit_gate(ts, 0.50, 2000, make_cfg())
        assert ok is False
        assert reason == "below_margin"

    def test_staleness_blocked_not_stale_yet(self) -> None:
        """Not stale: 100ms elapsed < 1200ms threshold -> (False, 'below_margin')."""
        ts = make_track_state(
            best_commit=make_commit(commit_score=0.80, created_at_ms=900),
        )
        ok, reason = _evaluate_commit_gate(ts, 0.60, 1000, make_cfg())
        assert ok is False
        assert reason == "below_margin"

    def test_staleness_disabled_never_fires(self) -> None:
        """enable_staleness=False: stale + qualifying -> still (False, 'below_margin')."""
        cfg = make_cfg(enable_staleness=False)
        ts = make_track_state(
            best_commit=make_commit(commit_score=0.80, created_at_ms=0),
        )
        ok, reason = _evaluate_commit_gate(ts, 0.60, 5000, cfg)
        assert ok is False
        assert reason == "below_margin"

    def test_gate_does_not_mutate_track_state(self) -> None:
        """All TrackState fields unchanged after gate call; pure decision function."""
        ts = make_track_state(
            best_commit=make_commit(commit_score=0.80, created_at_ms=0),
        )
        ts_before = copy.deepcopy(ts)

        _evaluate_commit_gate(ts, 0.85, 100, make_cfg())

        # Compare all dataclass fields
        for f in fields(ts):
            assert getattr(ts, f.name) == getattr(ts_before, f.name), (
                f"Field '{f.name}' was mutated by gate"
            )


# ---------------------------------------------------------------------------
# Factory helpers — chunk-02 (submit_candidate)
# ---------------------------------------------------------------------------


def make_roi_rq() -> RoiRichQuality:
    """Minimal RoiRichQuality stub for submit_candidate tests.

    Actual content doesn't matter since compute_ocr_likelihood_score
    is mocked in these tests.
    """
    return RoiRichQuality(
        roi_fq=None,  # type: ignore[arg-type]
        metrics=None,  # type: ignore[arg-type]
    )


def make_candidate(
    track_id: str = "t1",
    n_rois: int = 4,
    version: int = 1,
) -> EnhancedBatchSelection:
    """Minimal EnhancedBatchSelection for submit_candidate tests."""
    return EnhancedBatchSelection(
        track_id=track_id,
        version=version,
        base_rois=[make_roi_rq() for _ in range(n_rois)],
        enhance_rois=[],
        duplicate_groups={},
    )


def make_processed(n_rois: int = 4) -> ProcessorOutput:
    """Minimal ProcessorOutput with correct tensor shapes."""
    return ProcessorOutput(
        batch_tensor=torch.zeros(n_rois, 3, 32, 128),
        is_enhanced=np.zeros(n_rois, dtype=bool),
        post_meta=[],
    )


# ===========================================================================
# submit_candidate tests — chunk-02
# ===========================================================================

_MOCK_SCORE = "consumer.ops_ocr_queue.compute_ocr_likelihood_score"


class TestSubmitCandidate:
    """Tests for OcrReadyQueue.submit_candidate — chunk-02."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_first_candidate_committed(self, mock_score: MagicMock) -> None:
        """New track returns True; commit_version==1, commit_score==0.80."""
        queue = OcrReadyQueue(make_cfg())
        result = queue.submit_candidate(
            make_candidate(track_id="t1", n_rois=4),
            make_processed(n_rois=4),
            src_bin_version=1,
            timestamp_ms=100,
        )

        assert result is True
        ts = queue._tracks["t1"]
        assert ts.best_commit is not None
        assert ts.best_commit.commit_version == 1
        assert ts.best_commit.commit_score == pytest.approx(0.80)
        assert ts.best_commit.source_bin_version == 1
        assert ts.best_commit.created_at_ms == 100

    @patch(_MOCK_SCORE)
    def test_improvement_accepted(self, mock_score: MagicMock) -> None:
        """Existing score 0.80, new 0.90 -> True; commit_version==2."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        mock_score.return_value = 0.80
        queue.submit_candidate(make_candidate(), proc, src_bin_version=1, timestamp_ms=100)

        mock_score.return_value = 0.90
        result = queue.submit_candidate(
            make_candidate(),
            proc,
            src_bin_version=2,
            timestamp_ms=200,
        )

        assert result is True
        ts = queue._tracks["t1"]
        assert ts.best_commit is not None
        assert ts.best_commit.commit_version == 2
        assert ts.best_commit.commit_score == pytest.approx(0.90)

    @patch(_MOCK_SCORE)
    def test_below_margin_rejected(self, mock_score: MagicMock) -> None:
        """Existing score 0.85, new 0.87 (delta=0.02 < 0.04) -> False."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        mock_score.return_value = 0.85
        queue.submit_candidate(make_candidate(), proc, src_bin_version=1, timestamp_ms=100)

        mock_score.return_value = 0.87
        result = queue.submit_candidate(
            make_candidate(),
            proc,
            src_bin_version=2,
            timestamp_ms=200,
        )

        assert result is False
        # best_commit unchanged
        ts = queue._tracks["t1"]
        assert ts.best_commit is not None
        assert ts.best_commit.commit_version == 1
        assert ts.best_commit.commit_score == pytest.approx(0.85)

    @patch(_MOCK_SCORE)
    def test_supersedence_counter_increments(self, mock_score: MagicMock) -> None:
        """Unconsumed v1 replaced by better v2 -> superseded==1."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        mock_score.return_value = 0.70
        queue.submit_candidate(make_candidate(), proc, src_bin_version=1, timestamp_ms=100)

        mock_score.return_value = 0.80
        queue.submit_candidate(make_candidate(), proc, src_bin_version=2, timestamp_ms=200)

        ts = queue._tracks["t1"]
        assert ts.metrics.commits_superseded_before_consumption == 1

    @patch(_MOCK_SCORE)
    def test_no_supersedence_when_consumed(self, mock_score: MagicMock) -> None:
        """Consumed v1 then improvement -> superseded==0."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        mock_score.return_value = 0.70
        queue.submit_candidate(make_candidate(), proc, src_bin_version=1, timestamp_ms=100)

        # Simulate consumption of v1 (finish_commit not yet implemented)
        queue._tracks["t1"].consumed_commit_version = 1

        mock_score.return_value = 0.80
        queue.submit_candidate(make_candidate(), proc, src_bin_version=2, timestamp_ms=200)

        ts = queue._tracks["t1"]
        assert ts.metrics.commits_superseded_before_consumption == 0

    @patch(_MOCK_SCORE)
    def test_b0_rejected_immediately(self, mock_score: MagicMock) -> None:
        """Candidate with 0 base_rois -> False; no score computed."""
        queue = OcrReadyQueue(make_cfg())
        result = queue.submit_candidate(
            make_candidate(n_rois=0),
            make_processed(n_rois=0),
            src_bin_version=1,
            timestamp_ms=100,
        )

        assert result is False
        mock_score.assert_not_called()

    @patch(_MOCK_SCORE)
    def test_metrics_candidates_received(self, mock_score: MagicMock) -> None:
        """3 submits (2 accepted, 1 rejected) -> total_candidates_received==3."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        mock_score.return_value = 0.70
        queue.submit_candidate(make_candidate(), proc, src_bin_version=1, timestamp_ms=100)

        mock_score.return_value = 0.72  # delta=0.02 < 0.04 -> rejected
        queue.submit_candidate(make_candidate(), proc, src_bin_version=2, timestamp_ms=200)

        mock_score.return_value = 0.80  # delta=0.10 >= 0.04 -> accepted
        queue.submit_candidate(make_candidate(), proc, src_bin_version=3, timestamp_ms=300)

        ts = queue._tracks["t1"]
        assert ts.metrics.total_candidates_received == 3

    @patch(_MOCK_SCORE)
    def test_metrics_total_commits(self, mock_score: MagicMock) -> None:
        """3 submits (2 accepted, 1 rejected) -> total_commits==2."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        mock_score.return_value = 0.70
        queue.submit_candidate(make_candidate(), proc, src_bin_version=1, timestamp_ms=100)

        mock_score.return_value = 0.72  # rejected
        queue.submit_candidate(make_candidate(), proc, src_bin_version=2, timestamp_ms=200)

        mock_score.return_value = 0.80  # accepted
        queue.submit_candidate(make_candidate(), proc, src_bin_version=3, timestamp_ms=300)

        ts = queue._tracks["t1"]
        assert ts.metrics.total_commits == 2

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_commit_version_starts_at_1(self, mock_score: MagicMock) -> None:
        """First commit -> commit_version==1 (not 0)."""
        queue = OcrReadyQueue(make_cfg())
        queue.submit_candidate(
            make_candidate(),
            make_processed(),
            src_bin_version=1,
            timestamp_ms=100,
        )

        ts = queue._tracks["t1"]
        assert ts.best_commit is not None
        assert ts.best_commit.commit_version == 1

    @patch(_MOCK_SCORE)
    def test_commit_version_increments(self, mock_score: MagicMock) -> None:
        """Two accepted commits -> commit_version==2."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed()

        mock_score.return_value = 0.70
        queue.submit_candidate(make_candidate(), proc, src_bin_version=1, timestamp_ms=100)

        mock_score.return_value = 0.80
        queue.submit_candidate(make_candidate(), proc, src_bin_version=2, timestamp_ms=200)

        ts = queue._tracks["t1"]
        assert ts.best_commit is not None
        assert ts.best_commit.commit_version == 2


# ===========================================================================
# pull_next tests — chunk-03
# ===========================================================================


class TestPullNext:
    """Tests for OcrReadyQueue.pull_next — chunk-03."""

    def test_pull_empty_queue(self) -> None:
        """Empty queue -> pull_next returns None."""
        queue = OcrReadyQueue(make_cfg())
        assert queue.pull_next("w1", 100) is None

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_pull_returns_work_item(self, mock_score: MagicMock) -> None:
        """1 committed track -> OcrWorkItem with correct fields."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)
        queue.submit_candidate(
            make_candidate(track_id="t1"),
            proc,
            src_bin_version=5,
            timestamp_ms=100,
        )

        item = queue.pull_next("w1", 200)

        assert item is not None
        assert isinstance(item, OcrWorkItem)
        assert item.track_id == "t1"
        assert item.commit_version == 1
        assert item.source_bin_version == 5
        assert item.batch_tensor.shape == (4, 3, 32, 128)
        assert isinstance(item.is_enhanced, np.ndarray)

    @patch(_MOCK_SCORE)
    def test_pull_p0_before_p1(self, mock_score: MagicMock) -> None:
        """P0 (never consumed) dispatched before P1 (previously consumed + new commit)."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        # Create P1 track: submit -> pull -> finish -> submit improvement
        mock_score.return_value = 0.70
        queue.submit_candidate(
            make_candidate(track_id="p1"),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )
        pulled = queue.pull_next("w1", 150)
        assert pulled is not None
        queue.finish_commit("p1", 1)

        mock_score.return_value = 0.80  # improvement over 0.70
        queue.submit_candidate(
            make_candidate(track_id="p1"),
            proc,
            src_bin_version=2,
            timestamp_ms=200,
        )

        # Create P0 track (never consumed)
        mock_score.return_value = 0.75
        queue.submit_candidate(
            make_candidate(track_id="p0"),
            proc,
            src_bin_version=3,
            timestamp_ms=300,
        )

        # Pull should return P0 first (priority tier 0 < 1)
        item = queue.pull_next("w1", 400)
        assert item is not None
        assert item.track_id == "p0"

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_pull_oldest_first_within_tier(self, mock_score: MagicMock) -> None:
        """Two P0 tracks -> older created_at_ms dispatched first."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="older"),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )
        queue.submit_candidate(
            make_candidate(track_id="newer"),
            proc,
            src_bin_version=2,
            timestamp_ms=200,
        )

        item = queue.pull_next("w1", 300)
        assert item is not None
        assert item.track_id == "older"

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_pull_track_id_tiebreak(self, mock_score: MagicMock) -> None:
        """Same tier same created_at_ms -> lexicographic smaller track_id wins."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="bravo"),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )
        queue.submit_candidate(
            make_candidate(track_id="alpha"),
            proc,
            src_bin_version=2,
            timestamp_ms=100,
        )

        item = queue.pull_next("w1", 200)
        assert item is not None
        assert item.track_id == "alpha"

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_pull_in_flight_blocks_redispatch(self, mock_score: MagicMock) -> None:
        """Pull track A then pull again (only A) -> None; in_flight blocks."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )

        first = queue.pull_next("w1", 200)
        assert first is not None

        second = queue.pull_next("w2", 300)
        assert second is None

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_pull_skips_done_tracks(self, mock_score: MagicMock) -> None:
        """Done track skipped; committed non-done track dispatched instead."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="done_track"),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )
        queue.submit_candidate(
            make_candidate(track_id="live_track"),
            proc,
            src_bin_version=2,
            timestamp_ms=200,
        )

        queue.mark_done("done_track", timestamp_ms=250)

        item = queue.pull_next("w1", 300)
        assert item is not None
        assert item.track_id == "live_track"


# ===========================================================================
# finish_commit tests — chunk-03
# ===========================================================================


class TestFinishCommit:
    """Tests for OcrReadyQueue.finish_commit — chunk-03."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_finish_matching_version(self, mock_score: MagicMock) -> None:
        """Dispatch v1, finish(v1) -> True; in_flight cleared, consumed==1."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )
        queue.pull_next("w1", 200)

        result = queue.finish_commit("t1", 1)

        assert result is True
        ts = queue._tracks["t1"]
        assert ts.in_flight is None
        assert ts.consumed_commit_version == 1
        assert ts.metrics.total_consumed == 1

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_finish_version_mismatch(self, mock_score: MagicMock) -> None:
        """In_flight v1, finish(v2) -> False; in_flight still present."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )
        queue.pull_next("w1", 200)

        result = queue.finish_commit("t1", 2)

        assert result is False
        ts = queue._tracks["t1"]
        assert ts.in_flight is not None
        assert ts.in_flight.commit_version == 1

    def test_finish_unknown_track(self) -> None:
        """finish_commit('unknown', 1) -> False."""
        queue = OcrReadyQueue(make_cfg())
        assert queue.finish_commit("unknown", 1) is False

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_finish_no_in_flight(self, mock_score: MagicMock) -> None:
        """Track committed but not dispatched -> finish_commit returns False."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )

        result = queue.finish_commit("t1", 1)
        assert result is False

    @patch(_MOCK_SCORE)
    def test_finish_increments_total_consumed(self, mock_score: MagicMock) -> None:
        """Two dispatch-finish cycles -> metrics.total_consumed==2."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        # Cycle 1
        mock_score.return_value = 0.70
        queue.submit_candidate(
            make_candidate(track_id="t1"),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )
        queue.pull_next("w1", 150)
        queue.finish_commit("t1", 1)

        # Cycle 2 (improvement)
        mock_score.return_value = 0.80
        queue.submit_candidate(
            make_candidate(track_id="t1"),
            proc,
            src_bin_version=2,
            timestamp_ms=200,
        )
        queue.pull_next("w1", 250)
        queue.finish_commit("t1", 2)

        ts = queue._tracks["t1"]
        assert ts.metrics.total_consumed == 2


# ===========================================================================
# mark_done tests — chunk-03
# ===========================================================================


class TestMarkDone:
    """Tests for OcrReadyQueue.mark_done — chunk-03."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_mark_done_success(self, mock_score: MagicMock) -> None:
        """Active track -> mark_done returns True; track removed from _tracks."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )

        result = queue.mark_done("t1", timestamp_ms=200)
        assert result is True
        assert "t1" not in queue._tracks

    def test_mark_done_unknown_track(self) -> None:
        """mark_done('unknown') -> False."""
        queue = OcrReadyQueue(make_cfg())
        assert queue.mark_done("unknown", timestamp_ms=100) is False

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_mark_done_force_evicts_in_flight(self, mock_score: MagicMock) -> None:
        """Track with active dispatch -> mark_done returns True; in_flight cleared."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )
        queue.pull_next("w1", 200)

        result = queue.mark_done("t1", timestamp_ms=300)
        assert result is True
        assert "t1" not in queue._tracks
        assert "t1" in queue._done_metrics

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_mark_done_retains_metrics(self, mock_score: MagicMock) -> None:
        """After mark_done, metrics preserved in _done_metrics with correct values."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )

        queue.mark_done("t1", timestamp_ms=200)

        metrics = queue._done_metrics["t1"]
        assert metrics.first_seen_ms == 100
        assert metrics.total_candidates_received == 1
        assert metrics.total_commits == 1
        assert metrics.total_consumed == 0
        assert metrics.commits_superseded_before_consumption == 0

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_done_track_rejects_future_submits(self, mock_score: MagicMock) -> None:
        """submit -> mark_done -> submit same track_id -> False (tombstone)."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )
        queue.mark_done("t1", timestamp_ms=200)

        result = queue.submit_candidate(
            make_candidate(track_id="t1"),
            proc,
            src_bin_version=2,
            timestamp_ms=300,
        )
        assert result is False


# ===========================================================================
# Lifecycle tests — chunk-03
# ===========================================================================


class TestLifecycle:
    """End-to-end lifecycle tests — chunk-03."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_full_lifecycle(self, mock_score: MagicMock) -> None:
        """submit->pull->finish->mark_done; final metrics correct."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        # submit
        assert queue.submit_candidate(
            make_candidate(track_id="t1"),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )

        # pull
        item = queue.pull_next("w1", 200)
        assert item is not None
        assert item.track_id == "t1"

        # finish
        assert queue.finish_commit("t1", 1)

        # mark_done
        assert queue.mark_done("t1", timestamp_ms=300)

        # Verify final metrics
        metrics = queue._done_metrics["t1"]
        assert metrics.total_candidates_received == 1
        assert metrics.total_commits == 1
        assert metrics.total_consumed == 1
        assert metrics.commits_superseded_before_consumption == 0

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_at_most_once_consumption(self, mock_score: MagicMock) -> None:
        """submit, pull, finish, pull again -> None (consumed == committed)."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )

        item = queue.pull_next("w1", 200)
        assert item is not None
        queue.finish_commit("t1", 1)

        # Second pull -> None (commit_version == consumed_commit_version)
        item2 = queue.pull_next("w2", 300)
        assert item2 is None


# ===========================================================================
# Logging tests — chunk-04
# ===========================================================================


class TestLogging:
    """Chunk-04: structured logging assertions using caplog."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_log_commit_decision(
        self, mock_score: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Accepted submit logs INFO: track_id, S_new, decision=commit, reason, commit_version, B."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        with caplog.at_level(logging.INFO, logger="consumer.ops_ocr_queue"):
            queue.submit_candidate(
                make_candidate(track_id="t1", n_rois=4),
                proc,
                src_bin_version=5,
                timestamp_ms=100,
            )

        assert any(
            "track_id=t1" in r.message
            and "decision=commit" in r.message
            and "reason=first_commit" in r.message
            and "commit_version=1" in r.message
            and "B=4" in r.message
            for r in caplog.records
        )

    @patch(_MOCK_SCORE)
    def test_log_reject_decision(
        self, mock_score: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Rejected submit logs INFO: track_id, S_new, S_best, decision=reject, reason=below_margin."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        mock_score.return_value = 0.85
        queue.submit_candidate(make_candidate(), proc, src_bin_version=1, timestamp_ms=100)

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="consumer.ops_ocr_queue"):
            mock_score.return_value = 0.87
            queue.submit_candidate(make_candidate(), proc, src_bin_version=2, timestamp_ms=200)

        assert any(
            "decision=reject" in r.message
            and "reason=below_margin" in r.message
            and "S_best=" in r.message
            for r in caplog.records
        )

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_log_dispatch(self, mock_score: MagicMock, caplog: pytest.LogCaptureFixture) -> None:
        """pull_next logs INFO: track_id, commit_version, worker_id, N, priority_tier."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)
        queue.submit_candidate(
            make_candidate(track_id="t1", n_rois=4),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )

        with caplog.at_level(logging.INFO, logger="consumer.ops_ocr_queue"):
            queue.pull_next("w1", 200)

        assert any(
            "track_id=t1" in r.message
            and "commit_version=1" in r.message
            and "worker_id=w1" in r.message
            and "N=4" in r.message
            and "priority_tier=P0" in r.message
            for r in caplog.records
        )

    def test_log_no_eligible_work(self, caplog: pytest.LogCaptureFixture) -> None:
        """Empty pull logs DEBUG: 'no eligible work'."""
        queue = OcrReadyQueue(make_cfg())

        with caplog.at_level(logging.DEBUG, logger="consumer.ops_ocr_queue"):
            queue.pull_next("w1", 100)

        assert any("no eligible work" in r.message for r in caplog.records)

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_log_consumption(self, mock_score: MagicMock, caplog: pytest.LogCaptureFixture) -> None:
        """finish_commit logs INFO: track_id, commit_version."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)
        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )
        queue.pull_next("w1", 200)

        with caplog.at_level(logging.INFO, logger="consumer.ops_ocr_queue"):
            queue.finish_commit("t1", 1)

        assert any(
            "track_id=t1" in r.message and "commit_version=1" in r.message for r in caplog.records
        )

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_log_done_with_full_metrics(
        self, mock_score: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """mark_done logs INFO: track_id, reason, all 5 TrackMetrics fields."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)
        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )
        queue.pull_next("w1", 200)
        queue.finish_commit("t1", 1)

        with caplog.at_level(logging.INFO, logger="consumer.ops_ocr_queue"):
            queue.mark_done("t1", reason="done", timestamp_ms=300)

        log_text = caplog.text
        assert "track_id=t1" in log_text
        assert "reason=done" in log_text
        assert "first_seen_ms=100" in log_text
        assert "total_candidates_received=1" in log_text
        assert "total_commits=1" in log_text
        assert "commits_superseded=0" in log_text
        assert "total_consumed=1" in log_text

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_log_force_evict_warning(
        self, mock_score: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """mark_done with active in_flight logs WARNING: 'force-evicting in-flight'."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)
        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )
        queue.pull_next("w1", 200)

        with caplog.at_level(logging.WARNING, logger="consumer.ops_ocr_queue"):
            queue.mark_done("t1", timestamp_ms=300)

        assert any(
            "force-evicting in-flight" in r.message
            and "t1" in r.message
            and r.levelno == logging.WARNING
            for r in caplog.records
        )


# ===========================================================================
# get_metrics tests — chunk-04
# ===========================================================================


class TestGetMetrics:
    """Chunk-04: get_metrics accessor."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_get_metrics_active_track(self, mock_score: MagicMock) -> None:
        """Active track -> get_metrics returns correct TrackMetrics."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )

        m = queue.get_metrics("t1")
        assert m is not None
        assert m.first_seen_ms == 100
        assert m.total_candidates_received == 1
        assert m.total_commits == 1

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_get_metrics_done_track(self, mock_score: MagicMock) -> None:
        """Done track -> get_metrics returns TrackMetrics from _done_metrics."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )
        queue.mark_done("t1", timestamp_ms=200)

        m = queue.get_metrics("t1")
        assert m is not None
        assert m.first_seen_ms == 100
        assert m.total_commits == 1

    def test_get_metrics_unknown_returns_none(self) -> None:
        """Unknown track_id -> get_metrics returns None."""
        queue = OcrReadyQueue(make_cfg())
        assert queue.get_metrics("unknown") is None


# ===========================================================================
# Edge case tests — chunk-04
# ===========================================================================


class TestEdgeCasesChunk04:
    """Chunk-04: edge case hardening."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_all_tracks_done_pull_returns_none(self, mock_score: MagicMock) -> None:
        """All tracks mark_done'd -> pull_next returns None."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )
        queue.submit_candidate(
            make_candidate(track_id="t2"), proc, src_bin_version=2, timestamp_ms=200
        )
        queue.mark_done("t1", timestamp_ms=300)
        queue.mark_done("t2", timestamp_ms=300)

        assert queue.pull_next("w1", 400) is None

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_all_tracks_in_flight_pull_returns_none(self, mock_score: MagicMock) -> None:
        """All tracks dispatched (in_flight active) -> pull_next returns None."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )
        queue.submit_candidate(
            make_candidate(track_id="t2"), proc, src_bin_version=2, timestamp_ms=200
        )
        queue.pull_next("w1", 300)
        queue.pull_next("w2", 300)

        assert queue.pull_next("w3", 400) is None

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_double_submit_same_ms(self, mock_score: MagicMock) -> None:
        """Two submits same track same ms: first accepted, second rejected by margin."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        r1 = queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )
        r2 = queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=2, timestamp_ms=100
        )

        assert r1 is True
        assert r2 is False  # 0.80 vs 0.80, delta=0 < margin=0.04

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_submit_after_mark_done_rejects(self, mock_score: MagicMock) -> None:
        """submit -> mark_done -> submit same track_id -> False (tombstone)."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )
        queue.mark_done("t1", timestamp_ms=200)

        result = queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=2, timestamp_ms=300
        )
        assert result is False


# ===========================================================================
# Metrics integrity tests — chunk-04
# ===========================================================================


class TestMetricsIntegrity:
    """Chunk-04: metrics invariant verification."""

    @patch(_MOCK_SCORE)
    def test_metrics_consumed_leq_commits(self, mock_score: MagicMock) -> None:
        """After complex multi-track sequences, total_consumed <= total_commits."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        for track_id in ["t1", "t2", "t3"]:
            mock_score.return_value = 0.70
            queue.submit_candidate(
                make_candidate(track_id=track_id),
                proc,
                src_bin_version=1,
                timestamp_ms=100,
            )
            item = queue.pull_next("w1", 200)
            if item:
                queue.finish_commit(track_id, item.commit_version)

            mock_score.return_value = 0.80
            queue.submit_candidate(
                make_candidate(track_id=track_id),
                proc,
                src_bin_version=2,
                timestamp_ms=300,
            )

        for track_id in ["t1", "t2", "t3"]:
            m = queue.get_metrics(track_id)
            assert m is not None
            assert m.total_consumed <= m.total_commits

    @patch(_MOCK_SCORE)
    def test_metrics_superseded_leq_uncommitted(self, mock_score: MagicMock) -> None:
        """superseded <= commits - consumed for every track."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        # Multiple supersedences without consumption
        mock_score.return_value = 0.60
        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )

        mock_score.return_value = 0.70
        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=2, timestamp_ms=200
        )

        mock_score.return_value = 0.80
        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=3, timestamp_ms=300
        )

        m = queue.get_metrics("t1")
        assert m is not None
        assert m.commits_superseded_before_consumption <= m.total_commits - m.total_consumed

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_metrics_done_at_ms_implies_done(self, mock_score: MagicMock) -> None:
        """Track with done_at_ms set is in _done_metrics, not _tracks."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )
        queue.submit_candidate(
            make_candidate(track_id="t2"), proc, src_bin_version=2, timestamp_ms=200
        )

        queue.mark_done("t1", timestamp_ms=300)

        # t1: done, in _done_metrics
        assert "t1" in queue._done_metrics
        assert "t1" not in queue._tracks

        # t2: active, done_at_ms is None
        assert queue._tracks["t2"].done_at_ms is None

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_metrics_first_seen_ms_on_first_submit(self, mock_score: MagicMock) -> None:
        """first_seen_ms matches timestamp of first submit_candidate call."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )
        queue.submit_candidate(
            make_candidate(track_id="t2"), proc, src_bin_version=2, timestamp_ms=200
        )
        queue.submit_candidate(
            make_candidate(track_id="t3"), proc, src_bin_version=3, timestamp_ms=300
        )

        assert queue.get_metrics("t1") is not None
        assert queue.get_metrics("t1").first_seen_ms == 100  # type: ignore[union-attr]
        assert queue.get_metrics("t2") is not None
        assert queue.get_metrics("t2").first_seen_ms == 200  # type: ignore[union-attr]
        assert queue.get_metrics("t3") is not None
        assert queue.get_metrics("t3").first_seen_ms == 300  # type: ignore[union-attr]


# ===========================================================================
# Multi-track integration tests — chunk-04 (Pass B)
# ===========================================================================


class TestMultiTrackIntegration:
    """Chunk-04: Pass B multi-track integration scenarios."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_multi_track_priority_dispatch_order(self, mock_score: MagicMock) -> None:
        """3 tracks (2 P0, 1 P1) dispatched in exact priority order."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        # alpha: P0, created at t=200
        queue.submit_candidate(
            make_candidate(track_id="alpha"),
            proc,
            src_bin_version=1,
            timestamp_ms=200,
        )

        # beta: P0, created at t=100 (older)
        queue.submit_candidate(
            make_candidate(track_id="beta"),
            proc,
            src_bin_version=2,
            timestamp_ms=100,
        )

        # gamma: P1 via submit+pull+finish+new submit
        mock_score.return_value = 0.70
        queue.submit_candidate(
            make_candidate(track_id="gamma"),
            proc,
            src_bin_version=3,
            timestamp_ms=50,
        )
        item = queue.pull_next("w0", 60)
        assert item is not None
        queue.finish_commit("gamma", 1)

        mock_score.return_value = 0.80
        queue.submit_candidate(
            make_candidate(track_id="gamma"),
            proc,
            src_bin_version=4,
            timestamp_ms=300,
        )

        # Dispatch order: beta (P0, oldest), alpha (P0, newer), gamma (P1)
        item1 = queue.pull_next("w1", 400)
        assert item1 is not None
        assert item1.track_id == "beta"

        item2 = queue.pull_next("w2", 400)
        assert item2 is not None
        assert item2.track_id == "alpha"

        item3 = queue.pull_next("w3", 400)
        assert item3 is not None
        assert item3.track_id == "gamma"

    @patch(_MOCK_SCORE)
    def test_inflight_supersedence(self, mock_score: MagicMock) -> None:
        """Dispatch v1, submit better v2, verify state, finish v1, pull v2."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        # Submit + dispatch v1
        mock_score.return_value = 0.70
        queue.submit_candidate(
            make_candidate(track_id="A"), proc, src_bin_version=1, timestamp_ms=100
        )
        item = queue.pull_next("w1", 200)
        assert item is not None
        assert item.commit_version == 1

        # Submit better v2 while v1 is in-flight
        mock_score.return_value = 0.80
        queue.submit_candidate(
            make_candidate(track_id="A"), proc, src_bin_version=2, timestamp_ms=300
        )

        ts = queue._tracks["A"]
        assert ts.best_commit is not None
        assert ts.best_commit.commit_version == 2
        assert ts.in_flight is not None
        assert ts.in_flight.commit_version == 1
        assert ts.metrics.commits_superseded_before_consumption == 1

        # Finish v1
        assert queue.finish_commit("A", 1) is True

        # Now v2 is eligible for dispatch
        item2 = queue.pull_next("w2", 400)
        assert item2 is not None
        assert item2.commit_version == 2

    def test_score_continuity_across_b_fluctuation(self) -> None:
        """B=2, B=3, B=2 with same top-2: small continuous score changes."""
        s1 = _compute_commit_score([0.9, 0.8])  # B=2: 0.85
        s2 = _compute_commit_score([0.9, 0.8, 0.0])  # B=3: ~0.803
        s3 = _compute_commit_score([0.9, 0.8])  # B=2: 0.85

        assert abs(s1 - s2) < 0.06
        assert abs(s2 - s3) < 0.06
        assert abs(s1 - s3) < 0.001  # identical inputs -> identical output

    @patch(_MOCK_SCORE)
    def test_staleness_backstop_full_scenario(self, mock_score: MagicMock) -> None:
        """Full staleness timing scenario: accept, reject (not stale), accept (stale), reject (below floor)."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        # Initial commit at t=0 with score 0.80
        mock_score.return_value = 0.80
        assert queue.submit_candidate(make_candidate(), proc, src_bin_version=1, timestamp_ms=0)

        # t=1000: submit 0.60 (not stale yet, below margin) -> rejected
        mock_score.return_value = 0.60
        assert (
            queue.submit_candidate(make_candidate(), proc, src_bin_version=2, timestamp_ms=1000)
            is False
        )

        # t=1300: submit 0.60 (stale: 1300 >= 1200, and 0.60 >= 0.55) -> accepted
        mock_score.return_value = 0.60
        assert queue.submit_candidate(make_candidate(), proc, src_bin_version=3, timestamp_ms=1300)

        # t=2500: submit 0.50 (stale, but below stale_min_score 0.55) -> rejected
        mock_score.return_value = 0.50
        assert (
            queue.submit_candidate(make_candidate(), proc, src_bin_version=4, timestamp_ms=2500)
            is False
        )


# ===========================================================================
# Config sensitivity tests — chunk-04 (Pass B)
# ===========================================================================


class TestConfigSensitivity:
    """Chunk-04: config sensitivity and breaking config tests."""

    @pytest.mark.parametrize(
        "margin,expected",
        [
            (0.0, True),  # 0.0 accepts any improvement
            (0.04, True),  # 0.04 default: delta=0.05 >= 0.04
            (0.20, False),  # 0.20 rejects: delta=0.05 < 0.20
        ],
    )
    @patch(_MOCK_SCORE)
    def test_commit_margin_sensitivity(
        self, mock_score: MagicMock, margin: float, expected: bool
    ) -> None:
        """commit_margin affects acceptance of improvement candidates."""
        queue = OcrReadyQueue(make_cfg(commit_margin=margin))
        proc = make_processed(n_rois=4)

        mock_score.return_value = 0.70
        queue.submit_candidate(make_candidate(), proc, src_bin_version=1, timestamp_ms=100)

        mock_score.return_value = 0.75
        result = queue.submit_candidate(make_candidate(), proc, src_bin_version=2, timestamp_ms=200)
        assert result is expected

    @pytest.mark.parametrize(
        "floor,expected",
        [
            (0.0, True),  # 0.0 accepts any stale candidate
            (0.55, True),  # 0.55 default: 0.60 >= 0.55
            (1.0, False),  # 1.0 effectively disables: 0.60 < 1.0
        ],
    )
    @patch(_MOCK_SCORE)
    def test_stale_min_score_sensitivity(
        self, mock_score: MagicMock, floor: float, expected: bool
    ) -> None:
        """stale_min_score affects staleness backstop acceptance."""
        queue = OcrReadyQueue(make_cfg(stale_min_score=floor))
        proc = make_processed(n_rois=4)

        mock_score.return_value = 0.80
        queue.submit_candidate(make_candidate(), proc, src_bin_version=1, timestamp_ms=0)

        mock_score.return_value = 0.60
        result = queue.submit_candidate(
            make_candidate(), proc, src_bin_version=2, timestamp_ms=1300
        )
        assert result is expected

    @pytest.mark.parametrize(
        "max_stale,ts_ms,expected",
        [
            (100, 200, True),  # 100ms: stale at t=200
            (1200, 1300, True),  # 1200ms: stale at t=1300
            (10000, 1300, False),  # 10000ms: not stale at t=1300
        ],
    )
    @patch(_MOCK_SCORE)
    def test_max_staleness_ms_sensitivity(
        self, mock_score: MagicMock, max_stale: int, ts_ms: int, expected: bool
    ) -> None:
        """max_staleness_ms controls when staleness backstop activates."""
        queue = OcrReadyQueue(make_cfg(max_staleness_ms=max_stale))
        proc = make_processed(n_rois=4)

        mock_score.return_value = 0.80
        queue.submit_candidate(make_candidate(), proc, src_bin_version=1, timestamp_ms=0)

        mock_score.return_value = 0.60
        result = queue.submit_candidate(
            make_candidate(), proc, src_bin_version=2, timestamp_ms=ts_ms
        )
        assert result is expected

    def test_breaking_negative_commit_margin(self) -> None:
        """commit_margin = -0.01 -> ValueError in __post_init__."""
        with pytest.raises(ValueError):
            make_cfg(commit_margin=-0.01)

    def test_breaking_zero_max_staleness_ms(self) -> None:
        """max_staleness_ms = 0 -> ValueError in __post_init__."""
        with pytest.raises(ValueError):
            make_cfg(max_staleness_ms=0)

    def test_breaking_stale_min_score_out_of_range(self) -> None:
        """stale_min_score = 1.5 -> ValueError in __post_init__."""
        with pytest.raises(ValueError):
            make_cfg(stale_min_score=1.5)

    @patch(_MOCK_SCORE)
    def test_interaction_staleness_false_ignores_timing(self, mock_score: MagicMock) -> None:
        """enable_staleness=False + any max_staleness_ms -> staleness never fires."""
        queue = OcrReadyQueue(make_cfg(enable_staleness=False, max_staleness_ms=1))
        proc = make_processed(n_rois=4)

        mock_score.return_value = 0.80
        queue.submit_candidate(make_candidate(), proc, src_bin_version=1, timestamp_ms=0)

        mock_score.return_value = 0.60
        result = queue.submit_candidate(
            make_candidate(), proc, src_bin_version=2, timestamp_ms=100000
        )
        assert result is False

    @patch(_MOCK_SCORE)
    def test_interaction_margin_zero_improvement_fires_first(self, mock_score: MagicMock) -> None:
        """commit_margin=0.0 -> improvement rule fires before staleness."""
        queue = OcrReadyQueue(make_cfg(commit_margin=0.0))
        proc = make_processed(n_rois=4)

        mock_score.return_value = 0.80
        queue.submit_candidate(make_candidate(), proc, src_bin_version=1, timestamp_ms=0)

        # Even tiny improvement accepted via Rule 3 before Rule 4
        mock_score.return_value = 0.800001
        result = queue.submit_candidate(make_candidate(), proc, src_bin_version=2, timestamp_ms=0)
        assert result is True


# ===========================================================================
# Error recovery tests — chunk-04 (Pass B)
# ===========================================================================


class TestErrorRecovery:
    """Chunk-04: error recovery scenarios."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_finish_after_mark_done(self, mock_score: MagicMock) -> None:
        """Worker calls finish_commit after track was mark_done'd -> False."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )
        queue.pull_next("w1", 200)
        queue.mark_done("t1", timestamp_ms=300)

        result = queue.finish_commit("t1", 1)
        assert result is False

    @patch(_MOCK_SCORE)
    def test_finish_stale_version_after_redispatch(self, mock_score: MagicMock) -> None:
        """Worker finishes stale version after supersedence + re-dispatch -> False."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        # Submit + dispatch v1
        mock_score.return_value = 0.70
        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )
        queue.pull_next("w1", 200)

        # Submit better v2 (while v1 in-flight)
        mock_score.return_value = 0.80
        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=2, timestamp_ms=300
        )

        # Finish v1, clearing in_flight
        queue.finish_commit("t1", 1)

        # Pull v2
        queue.pull_next("w2", 400)

        # Try to finish with stale version v1 (in_flight is v2 now)
        result = queue.finish_commit("t1", 1)
        assert result is False  # version mismatch: in_flight is v2


# ===========================================================================
# Golden examples — chunk-04 (Pass B)
# ===========================================================================


class TestGoldenExamples:
    """Chunk-04: golden path scenarios."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_happy_path_single_track(self, mock_score: MagicMock) -> None:
        """B=4: submit, pull, finish, mark_done with full metrics audit."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        # submit
        result = queue.submit_candidate(
            make_candidate(track_id="t1", n_rois=4),
            proc,
            src_bin_version=1,
            timestamp_ms=100,
        )
        assert result is True

        # pull
        item = queue.pull_next("w1", 200)
        assert item is not None
        assert item.batch_tensor.shape == (4, 3, 32, 128)

        # finish
        assert queue.finish_commit("t1", 1) is True

        # mark_done
        assert queue.mark_done("t1", timestamp_ms=300) is True

        # Final metrics via get_metrics
        m = queue.get_metrics("t1")
        assert m is not None
        assert m.total_candidates_received == 1
        assert m.total_commits == 1
        assert m.total_consumed == 1
        assert m.commits_superseded_before_consumption == 0

    @patch(_MOCK_SCORE)
    def test_multi_track_lifecycle(self, mock_score: MagicMock) -> None:
        """3 tracks, multiple submits, dispatches, finishes, mark_dones."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        # Track A: 2 submits (commit, improvement), dispatch, finish, mark_done
        mock_score.return_value = 0.60
        queue.submit_candidate(
            make_candidate(track_id="A"), proc, src_bin_version=1, timestamp_ms=100
        )
        mock_score.return_value = 0.70
        queue.submit_candidate(
            make_candidate(track_id="A"), proc, src_bin_version=2, timestamp_ms=200
        )

        item_a = queue.pull_next("w1", 300)
        assert item_a is not None
        assert item_a.track_id == "A"
        assert item_a.commit_version == 2
        queue.finish_commit("A", 2)
        queue.mark_done("A", timestamp_ms=400)

        # Track B: 3 submits (commit, reject, improvement), dispatch, no finish
        mock_score.return_value = 0.70
        queue.submit_candidate(
            make_candidate(track_id="B"), proc, src_bin_version=3, timestamp_ms=150
        )
        mock_score.return_value = 0.72  # rejected (below margin)
        queue.submit_candidate(
            make_candidate(track_id="B"), proc, src_bin_version=4, timestamp_ms=250
        )
        mock_score.return_value = 0.80
        queue.submit_candidate(
            make_candidate(track_id="B"), proc, src_bin_version=5, timestamp_ms=350
        )

        item_b = queue.pull_next("w2", 450)
        assert item_b is not None
        assert item_b.track_id == "B"
        assert item_b.commit_version == 2

        # Track C: 1 submit, dispatch, finish, mark_done
        mock_score.return_value = 0.90
        queue.submit_candidate(
            make_candidate(track_id="C"), proc, src_bin_version=6, timestamp_ms=500
        )

        item_c = queue.pull_next("w3", 550)
        assert item_c is not None
        assert item_c.track_id == "C"
        queue.finish_commit("C", 1)
        queue.mark_done("C", timestamp_ms=600)

        # Metrics audit — Track A (done)
        m_a = queue.get_metrics("A")
        assert m_a is not None
        assert m_a.total_candidates_received == 2
        assert m_a.total_commits == 2
        assert m_a.total_consumed == 1
        assert m_a.commits_superseded_before_consumption == 1

        # Metrics audit — Track B (active, dispatched)
        m_b = queue.get_metrics("B")
        assert m_b is not None
        assert m_b.total_candidates_received == 3
        assert m_b.total_commits == 2
        assert m_b.total_consumed == 0
        assert m_b.commits_superseded_before_consumption == 1

        # Metrics audit — Track C (done)
        m_c = queue.get_metrics("C")
        assert m_c is not None
        assert m_c.total_candidates_received == 1
        assert m_c.total_commits == 1
        assert m_c.total_consumed == 1
        assert m_c.commits_superseded_before_consumption == 0


# ===========================================================================
# NaN/Inf guard tests — chunk-05 (Pass A)
# ===========================================================================


class TestNanInfGuard:
    """Chunk-05: NaN/Inf guard in _compute_commit_score."""

    def test_nan_single_score_returns_zero(self) -> None:
        """Single NaN score returns 0.0 instead of propagating NaN."""
        assert _compute_commit_score([float("nan")]) == 0.0

    def test_nan_in_anchor_position_returns_zero(self) -> None:
        """NaN in s1 (anchor position) returns 0.0."""
        assert _compute_commit_score([float("nan"), 0.8]) == 0.0

    def test_nan_in_support_position_returns_zero(self) -> None:
        """NaN in s3 (support position) returns 0.0."""
        assert _compute_commit_score([0.9, 0.8, float("nan")]) == 0.0

    def test_inf_returns_zero(self) -> None:
        """+Inf returns 0.0 instead of producing Inf commit score."""
        assert _compute_commit_score([float("inf"), 0.8]) == 0.0

    def test_neg_inf_returns_zero(self) -> None:
        """-Inf also triggers the guard."""
        assert _compute_commit_score([float("-inf"), 0.8]) == 0.0

    def test_mixed_nan_and_valid_returns_zero(self) -> None:
        """One NaN among valid scores returns 0.0."""
        assert _compute_commit_score([0.9, 0.8, float("nan"), 0.6, 0.5]) == 0.0

    def test_all_nan_returns_zero(self) -> None:
        """All-NaN input returns 0.0."""
        assert _compute_commit_score([float("nan")] * 4) == 0.0

    def test_nan_guard_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Guard logs WARNING 'non-finite score' when NaN input is given."""
        with caplog.at_level(logging.WARNING, logger="consumer.ops_ocr_queue"):
            _compute_commit_score([float("nan"), 0.8])

        assert any(
            "non-finite score" in r.message and r.levelno == logging.WARNING for r in caplog.records
        )

    def test_valid_scores_unaffected_by_guard(self) -> None:
        """Normal valid scores produce the same result as before the guard."""
        result = _compute_commit_score([0.9, 0.8, 0.7])
        assert result == pytest.approx(0.84175)


# ===========================================================================
# Eviction mechanics tests — chunk-05 (Pass A)
# ===========================================================================


class TestEvictionMechanics:
    """Chunk-05: _done_metrics eviction in mark_done."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_done_metrics_eviction_at_capacity(self, mock_score: MagicMock) -> None:
        """max_done_metrics=10, mark_done 12 tracks -> len(_done_metrics) <= 10."""
        queue = OcrReadyQueue(make_cfg(max_done_metrics=10))
        proc = make_processed(n_rois=4)

        for i in range(12):
            tid = f"t{i:03d}"
            queue.submit_candidate(
                make_candidate(track_id=tid), proc, src_bin_version=i, timestamp_ms=i * 100
            )
            queue.mark_done(tid, timestamp_ms=i * 100 + 50)

        assert len(queue._done_metrics) <= 10

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_done_metrics_eviction_removes_oldest(self, mock_score: MagicMock) -> None:
        """max_done_metrics=4, mark_done 6 tracks -> oldest evicted, newest kept."""
        queue = OcrReadyQueue(make_cfg(max_done_metrics=4))
        proc = make_processed(n_rois=4)

        # Submit and mark_done 6 tracks with ascending first_seen_ms
        for i in range(6):
            tid = f"t{i}"
            queue.submit_candidate(
                make_candidate(track_id=tid),
                proc,
                src_bin_version=i,
                timestamp_ms=(i + 1) * 100,  # first_seen_ms: 100, 200, 300, 400, 500, 600
            )
            queue.mark_done(tid, timestamp_ms=(i + 1) * 100 + 50)

        # After eviction, the retained entries should have the highest first_seen_ms
        # Eviction fires after 5th mark_done (len=5 > cap=4): evicts 5//2=2 oldest (100,200)
        # Leaving 300,400,500. 6th mark_done brings len to 4 (== cap), no second eviction.
        # Final: first_seen_ms in {300, 400, 500, 600}
        assert len(queue._done_metrics) <= 4
        # The 2 oldest tracks (t0=100, t1=200) were evicted
        for m in queue._done_metrics.values():
            assert m.first_seen_ms >= 300

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_done_metrics_eviction_preserves_latest_entry(self, mock_score: MagicMock) -> None:
        """Track that triggered eviction is always retained."""
        queue = OcrReadyQueue(make_cfg(max_done_metrics=4))
        proc = make_processed(n_rois=4)

        for i in range(6):
            tid = f"t{i}"
            queue.submit_candidate(
                make_candidate(track_id=tid),
                proc,
                src_bin_version=i,
                timestamp_ms=(i + 1) * 100,
            )
            queue.mark_done(tid, timestamp_ms=(i + 1) * 100 + 50)

        # The last track (t5, first_seen_ms=600) triggered eviction and must be retained
        assert "t5" in queue._done_metrics

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_done_metrics_eviction_logs_info(
        self, mock_score: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Eviction logs INFO with 'evicted' and count."""
        queue = OcrReadyQueue(make_cfg(max_done_metrics=4))
        proc = make_processed(n_rois=4)

        for i in range(4):
            tid = f"t{i}"
            queue.submit_candidate(
                make_candidate(track_id=tid),
                proc,
                src_bin_version=i,
                timestamp_ms=(i + 1) * 100,
            )
            queue.mark_done(tid, timestamp_ms=(i + 1) * 100 + 50)

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="consumer.ops_ocr_queue"):
            # 5th track triggers eviction
            queue.submit_candidate(
                make_candidate(track_id="trigger"),
                proc,
                src_bin_version=10,
                timestamp_ms=600,
            )
            queue.mark_done("trigger", timestamp_ms=650)

        assert any("evicted" in r.message and r.levelno == logging.INFO for r in caplog.records)

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_done_metrics_no_eviction_below_capacity(self, mock_score: MagicMock) -> None:
        """max_done_metrics=10, mark_done 5 tracks -> no eviction, len==5."""
        queue = OcrReadyQueue(make_cfg(max_done_metrics=10))
        proc = make_processed(n_rois=4)

        for i in range(5):
            tid = f"t{i}"
            queue.submit_candidate(
                make_candidate(track_id=tid), proc, src_bin_version=i, timestamp_ms=i * 100
            )
            queue.mark_done(tid, timestamp_ms=i * 100 + 50)

        assert len(queue._done_metrics) == 5

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_done_metrics_no_eviction_when_unlimited(self, mock_score: MagicMock) -> None:
        """max_done_metrics=0 (unlimited), mark_done 20 tracks -> all retained."""
        queue = OcrReadyQueue(make_cfg(max_done_metrics=0))
        proc = make_processed(n_rois=4)

        for i in range(20):
            tid = f"t{i:03d}"
            queue.submit_candidate(
                make_candidate(track_id=tid), proc, src_bin_version=i, timestamp_ms=i * 100
            )
            queue.mark_done(tid, timestamp_ms=i * 100 + 50)

        assert len(queue._done_metrics) == 20

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_get_metrics_after_eviction(self, mock_score: MagicMock) -> None:
        """After eviction, get_metrics returns None for evicted, valid for retained."""
        queue = OcrReadyQueue(make_cfg(max_done_metrics=4))
        proc = make_processed(n_rois=4)

        for i in range(6):
            tid = f"t{i}"
            queue.submit_candidate(
                make_candidate(track_id=tid),
                proc,
                src_bin_version=i,
                timestamp_ms=(i + 1) * 100,
            )
            queue.mark_done(tid, timestamp_ms=(i + 1) * 100 + 50)

        # Retained tracks should have valid metrics
        retained_count = 0
        evicted_count = 0
        for i in range(6):
            tid = f"t{i}"
            m = queue.get_metrics(tid)
            if m is not None:
                retained_count += 1
                assert m.total_commits == 1
            else:
                evicted_count += 1

        assert retained_count > 0
        assert evicted_count > 0


# ===========================================================================
# Idempotency tests — chunk-05 (Pass A)
# ===========================================================================


class TestIdempotency:
    """Chunk-05: idempotency verification for mark_done and finish_commit."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_mark_done_idempotent(self, mock_score: MagicMock) -> None:
        """First mark_done returns True, second returns False."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )

        assert queue.mark_done("t1", timestamp_ms=200) is True
        assert queue.mark_done("t1", timestamp_ms=300) is False

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_finish_commit_idempotent(self, mock_score: MagicMock) -> None:
        """First finish_commit returns True, second returns False."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )
        queue.pull_next("w1", 200)

        assert queue.finish_commit("t1", 1) is True
        assert queue.finish_commit("t1", 1) is False


# ===========================================================================
# Docstring test — chunk-05 (Pass A)
# ===========================================================================


class TestDocstring:
    """Chunk-05: thread-safety docstring verification."""

    def test_thread_safety_docstring_present(self) -> None:
        """'Not thread-safe' in OcrReadyQueue.__doc__."""
        assert OcrReadyQueue.__doc__ is not None
        assert "Not thread-safe" in OcrReadyQueue.__doc__


# ===========================================================================
# NaN lifecycle integration — chunk-05 (Pass B)
# ===========================================================================


class TestNanLifecycleIntegration:
    """Chunk-05: Pass B — NaN mid-lifecycle integration with chunks 01-04."""

    @patch(_MOCK_SCORE)
    def test_nan_mid_lifecycle_track_recovers(self, mock_score: MagicMock) -> None:
        """NaN mid-stream: valid(0.80) -> NaN(rejected) -> valid(0.90) accepted."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        # Step 1: valid submit (0.80) -> accepted (first_commit)
        mock_score.return_value = 0.80
        assert queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=1, timestamp_ms=100
        )

        # Step 2: NaN submit -> _compute_commit_score returns 0.0, below_margin
        mock_score.return_value = float("nan")
        result = queue.submit_candidate(
            make_candidate(track_id="t1", n_rois=1), proc, src_bin_version=2, timestamp_ms=200
        )
        # NaN input to _compute_commit_score returns 0.0, which is below margin (0.0 - 0.80 < 0.04)
        assert result is False

        # Step 3: valid submit (0.90) -> accepted (improvement, 0.90 - 0.80 >= 0.04)
        mock_score.return_value = 0.90
        assert queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=3, timestamp_ms=300
        )

        ts = queue._tracks["t1"]
        assert ts.best_commit is not None
        assert ts.best_commit.commit_score == pytest.approx(0.90)
        assert ts.metrics.total_candidates_received == 3
        assert ts.metrics.total_commits == 2

    @patch(_MOCK_SCORE)
    def test_nan_on_first_submit_track_accepts_next_valid(self, mock_score: MagicMock) -> None:
        """NaN on first submit -> score=0.0 committed, then valid improvement accepted."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        # First submit: NaN -> _compute_commit_score returns 0.0, accepted as first_commit
        mock_score.return_value = float("nan")
        result = queue.submit_candidate(
            make_candidate(track_id="t1", n_rois=1), proc, src_bin_version=1, timestamp_ms=100
        )
        assert result is True  # first_commit always accepted
        ts = queue._tracks["t1"]
        assert ts.best_commit is not None
        assert ts.best_commit.commit_score == 0.0

        # Second submit: valid (0.80) -> accepted (0.80 - 0.0 >= 0.04)
        mock_score.return_value = 0.80
        assert queue.submit_candidate(
            make_candidate(track_id="t1"), proc, src_bin_version=2, timestamp_ms=200
        )

        ts = queue._tracks["t1"]
        assert ts.best_commit is not None
        assert ts.best_commit.commit_score == pytest.approx(0.80)
        assert ts.metrics.total_commits == 2

    @patch(_MOCK_SCORE)
    def test_nan_guard_warning_logged_in_submit_context(
        self, mock_score: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """NaN submit produces both WARNING 'non-finite' and INFO gate decision."""
        queue = OcrReadyQueue(make_cfg())
        proc = make_processed(n_rois=4)

        with caplog.at_level(logging.DEBUG, logger="consumer.ops_ocr_queue"):
            mock_score.return_value = float("nan")
            queue.submit_candidate(
                make_candidate(track_id="t1", n_rois=1), proc, src_bin_version=1, timestamp_ms=100
            )

        # Guard warning
        assert any(
            "non-finite score" in r.message and r.levelno == logging.WARNING for r in caplog.records
        )
        # Gate decision (first_commit, so it's accepted with score 0.0)
        assert any("decision=" in r.message and r.levelno == logging.INFO for r in caplog.records)


# ===========================================================================
# Eviction during active lifecycle — chunk-05 (Pass B)
# ===========================================================================


class TestEvictionDuringLifecycle:
    """Chunk-05: Pass B — eviction during multi-track active lifecycle."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_eviction_during_active_multi_track_dispatch(self, mock_score: MagicMock) -> None:
        """Eviction fires while tracks are actively being submitted and dispatched."""
        queue = OcrReadyQueue(make_cfg(max_done_metrics=4))
        proc = make_processed(n_rois=4)

        # Submit + full lifecycle for t1..t5 (first_seen_ms 100..500)
        for i in range(5):
            tid = f"t{i + 1}"
            queue.submit_candidate(
                make_candidate(track_id=tid),
                proc,
                src_bin_version=i,
                timestamp_ms=(i + 1) * 100,
            )
            item = queue.pull_next("w1", (i + 1) * 100 + 10)
            assert item is not None
            queue.finish_commit(tid, item.commit_version)
            queue.mark_done(tid, timestamp_ms=(i + 1) * 100 + 20)

        # After 5 done tracks with cap=4, eviction should have fired
        assert len(queue._done_metrics) <= 4

        # New track t6: full lifecycle still works
        queue.submit_candidate(
            make_candidate(track_id="t6"), proc, src_bin_version=10, timestamp_ms=700
        )
        item = queue.pull_next("w1", 710)
        assert item is not None
        assert item.track_id == "t6"
        queue.finish_commit("t6", 1)
        queue.mark_done("t6", timestamp_ms=720)

        assert len(queue._done_metrics) <= 4

        # New track t7: active, not done
        queue.submit_candidate(
            make_candidate(track_id="t7"), proc, src_bin_version=11, timestamp_ms=800
        )

        # Active track unaffected by done_metrics eviction
        assert "t7" in queue._tracks
        item = queue.pull_next("w1", 810)
        assert item is not None
        assert item.track_id == "t7"

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_eviction_preserves_active_track_metrics(self, mock_score: MagicMock) -> None:
        """Eviction of _done_metrics never affects _tracks (active tracks)."""
        queue = OcrReadyQueue(make_cfg(max_done_metrics=3))
        proc = make_processed(n_rois=4)

        # Create 2 active tracks
        queue.submit_candidate(
            make_candidate(track_id="active1"), proc, src_bin_version=1, timestamp_ms=100
        )
        queue.submit_candidate(
            make_candidate(track_id="active2"), proc, src_bin_version=2, timestamp_ms=200
        )

        # Mark done 4 tracks (triggers eviction at done count > 3)
        for i in range(4):
            tid = f"done{i}"
            queue.submit_candidate(
                make_candidate(track_id=tid),
                proc,
                src_bin_version=10 + i,
                timestamp_ms=300 + i * 100,
            )
            queue.mark_done(tid, timestamp_ms=300 + i * 100 + 50)

        # Active tracks still intact
        assert "active1" in queue._tracks
        assert "active2" in queue._tracks
        assert queue.get_metrics("active1") is not None
        assert queue.get_metrics("active2") is not None

        # Active tracks dispatchable
        item = queue.pull_next("w1", 800)
        assert item is not None
        assert item.track_id in ("active1", "active2")


# ===========================================================================
# Combined full-feature integration — chunk-05 (Pass B)
# ===========================================================================


class TestFullIntegrationChunk05:
    """Chunk-05: Pass B — comprehensive integration exercising ALL chunk-05 features."""

    @patch(_MOCK_SCORE)
    def test_full_integration_scoring_gate_queue_dispatch_nan_eviction(
        self, mock_score: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """5-track scenario exercising NaN guard, eviction, force-evict, and metrics."""
        queue = OcrReadyQueue(make_cfg(max_done_metrics=3))
        proc = make_processed(n_rois=4)

        with caplog.at_level(logging.DEBUG, logger="consumer.ops_ocr_queue"):
            # Track A: normal lifecycle
            mock_score.return_value = 0.80
            queue.submit_candidate(
                make_candidate(track_id="A"), proc, src_bin_version=1, timestamp_ms=100
            )
            item = queue.pull_next("w1", 110)
            assert item is not None
            queue.finish_commit("A", 1)
            queue.mark_done("A", reason="done", timestamp_ms=120)

            # Track B: valid -> NaN (rejected) -> valid improvement
            mock_score.return_value = 0.70
            queue.submit_candidate(
                make_candidate(track_id="B"), proc, src_bin_version=2, timestamp_ms=200
            )
            mock_score.return_value = float("nan")
            queue.submit_candidate(
                make_candidate(track_id="B", n_rois=1), proc, src_bin_version=3, timestamp_ms=250
            )
            mock_score.return_value = 0.85
            queue.submit_candidate(
                make_candidate(track_id="B"), proc, src_bin_version=4, timestamp_ms=300
            )
            item = queue.pull_next("w1", 310)
            assert item is not None
            queue.finish_commit("B", item.commit_version)
            queue.mark_done("B", reason="done", timestamp_ms=320)

            # Track C: valid -> pull (in_flight) -> mark_done (force-evict)
            mock_score.return_value = 0.90
            queue.submit_candidate(
                make_candidate(track_id="C"), proc, src_bin_version=5, timestamp_ms=400
            )
            queue.pull_next("w1", 410)
            queue.mark_done("C", reason="done", timestamp_ms=420)

            # Track D: normal lifecycle
            mock_score.return_value = 0.75
            queue.submit_candidate(
                make_candidate(track_id="D"), proc, src_bin_version=6, timestamp_ms=500
            )
            item = queue.pull_next("w1", 510)
            assert item is not None
            queue.finish_commit("D", 1)
            queue.mark_done("D", reason="done", timestamp_ms=520)

        # Track E: still active
        mock_score.return_value = 0.80
        queue.submit_candidate(
            make_candidate(track_id="E"), proc, src_bin_version=7, timestamp_ms=600
        )

        # (a) Track E is still active
        item = queue.pull_next("w1", 610)
        assert item is not None
        assert item.track_id == "E"

        # (b) done_metrics capped
        assert len(queue._done_metrics) <= 3

        # (c) Track B metrics
        m_b = queue.get_metrics("B")
        if m_b is not None:  # may have been evicted
            assert m_b.total_candidates_received == 3
            assert m_b.total_commits == 2

        # (d/e) Some tracks evicted, some retained
        done_tracks = ["A", "B", "C", "D"]
        retained = sum(1 for t in done_tracks if queue.get_metrics(t) is not None)
        evicted = sum(1 for t in done_tracks if queue.get_metrics(t) is None)
        assert retained <= 3
        assert evicted >= 1

        # (f) Log assertions
        log_text = caplog.text
        assert "non-finite score" in log_text  # Track B NaN
        assert "force-evicting in-flight" in log_text  # Track C
        assert "evicted" in log_text  # done_metrics eviction


# ===========================================================================
# Config sensitivity — chunk-05 (Pass B)
# ===========================================================================


class TestMaxDoneMetricsSensitivity:
    """Chunk-05: Pass B — max_done_metrics config sensitivity."""

    @pytest.mark.parametrize(
        "max_done,n_tracks,expected_max_len",
        [
            (0, 8, 8),  # unlimited -> all 8 retained
            (3, 8, 3),  # cap=3 -> at most 3
            (10, 8, 8),  # cap=10 -> all 8 retained (below cap)
        ],
    )
    @patch(_MOCK_SCORE, return_value=0.80)
    def test_max_done_metrics_config_sensitivity(
        self, mock_score: MagicMock, max_done: int, n_tracks: int, expected_max_len: int
    ) -> None:
        """Parametrized: max_done_metrics controls retention."""
        queue = OcrReadyQueue(make_cfg(max_done_metrics=max_done))
        proc = make_processed(n_rois=4)

        for i in range(n_tracks):
            tid = f"t{i:03d}"
            queue.submit_candidate(
                make_candidate(track_id=tid), proc, src_bin_version=i, timestamp_ms=i * 100
            )
            queue.mark_done(tid, timestamp_ms=i * 100 + 50)

        assert len(queue._done_metrics) <= expected_max_len


# ===========================================================================
# Regression: existing behavior preserved — chunk-05 (Pass B)
# ===========================================================================


class TestRegressionChunk05:
    """Chunk-05: Pass B — regression check that NaN guard doesn't alter valid scoring."""

    def test_existing_chunk01_scoring_unaffected(self) -> None:
        """All chunk-01 golden scoring examples still produce exact same results."""
        # B=0 -> 0.0
        assert _compute_commit_score([]) == 0.0
        # B=1 -> 0.9
        assert _compute_commit_score([0.9]) == 0.9
        # B=2 -> 0.85
        assert _compute_commit_score([0.9, 0.8]) == pytest.approx(0.85)
        # B=3 -> 0.84175
        assert _compute_commit_score([0.9, 0.8, 0.7]) == pytest.approx(0.84175)
        # B=8 -> 0.718
        scores = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]
        assert _compute_commit_score(scores) == pytest.approx(0.718, abs=1e-6)


# ===========================================================================
# Stress test — chunk-05 (Pass C)
# ===========================================================================


class TestStressChunk05:
    """Chunk-05: Pass C — stress tests."""

    @patch(_MOCK_SCORE)
    def test_stress_many_tracks_full_lifecycle_with_eviction(
        self, mock_score: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """50 tracks full lifecycle, max_done_metrics=10, every 5th gets NaN submit."""
        queue = OcrReadyQueue(make_cfg(max_done_metrics=10))
        proc = make_processed(n_rois=4)
        nan_count = 0

        with caplog.at_level(logging.DEBUG, logger="consumer.ops_ocr_queue"):
            for i in range(50):
                tid = f"t{i:03d}"
                ts_base = i * 100

                # Every 5th track: submit NaN first
                if i % 5 == 0:
                    mock_score.return_value = float("nan")
                    queue.submit_candidate(
                        make_candidate(track_id=tid, n_rois=1),
                        proc,
                        src_bin_version=i * 10,
                        timestamp_ms=ts_base,
                    )
                    nan_count += 1

                # Valid submit
                mock_score.return_value = 0.80
                queue.submit_candidate(
                    make_candidate(track_id=tid),
                    proc,
                    src_bin_version=i * 10 + 1,
                    timestamp_ms=ts_base + 10,
                )

                # Pull + finish + mark_done
                item = queue.pull_next("w1", ts_base + 20)
                assert item is not None
                queue.finish_commit(tid, item.commit_version)
                queue.mark_done(tid, timestamp_ms=ts_base + 30)

        # (a) done_metrics capped
        assert len(queue._done_metrics) <= 10

        # (b) metrics invariants for retained entries
        for m in queue._done_metrics.values():
            assert m.total_consumed <= m.total_commits

        # (c) no tracks remain active
        assert len(queue._tracks) == 0

        # (d) log assertions
        nan_warnings = sum(1 for r in caplog.records if "non-finite score" in r.message)
        assert nan_warnings == nan_count  # 10 NaN submits
        assert any("evicted" in r.message for r in caplog.records)


# ===========================================================================
# Boundary conditions — chunk-05 (Pass C)
# ===========================================================================


class TestBoundaryConditionsChunk05:
    """Chunk-05: Pass C — boundary conditions for eviction."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_eviction_exactly_at_capacity_no_eviction(self, mock_score: MagicMock) -> None:
        """max_done_metrics=5, mark_done exactly 5 -> no eviction (len == cap, not >)."""
        queue = OcrReadyQueue(make_cfg(max_done_metrics=5))
        proc = make_processed(n_rois=4)

        for i in range(5):
            tid = f"t{i}"
            queue.submit_candidate(
                make_candidate(track_id=tid), proc, src_bin_version=i, timestamp_ms=i * 100
            )
            queue.mark_done(tid, timestamp_ms=i * 100 + 50)

        assert len(queue._done_metrics) == 5

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_eviction_one_over_capacity_triggers(self, mock_score: MagicMock) -> None:
        """max_done_metrics=5, mark_done 6 -> eviction triggers, len <= 5."""
        queue = OcrReadyQueue(make_cfg(max_done_metrics=5))
        proc = make_processed(n_rois=4)

        for i in range(6):
            tid = f"t{i}"
            queue.submit_candidate(
                make_candidate(track_id=tid),
                proc,
                src_bin_version=i,
                timestamp_ms=(i + 1) * 100,
            )
            queue.mark_done(tid, timestamp_ms=(i + 1) * 100 + 50)

        assert len(queue._done_metrics) <= 5
        # 50% of 6 = 3 evicted -> 3 remaining, all with highest first_seen_ms
        remaining_first_seen = [m.first_seen_ms for m in queue._done_metrics.values()]
        for fs in remaining_first_seen:
            assert fs >= 400  # t3=400, t4=500, t5=600


# ===========================================================================
# Property-based NaN guard — chunk-05 (Pass C)
# ===========================================================================


# ===========================================================================
# Data model extensions — chunk-03
# ===========================================================================


class TestCommitRecordExtensions:
    """Chunk-03: CommitRecord duplicate_groups + per_roi_quality fields."""

    def test_commit_record_accepts_duplicate_groups(self) -> None:
        """CommitRecord stores duplicate_groups correctly."""
        cr = CommitRecord(
            commit_version=1,
            source_bin_version=1,
            commit_score=0.80,
            batch_tensor=torch.zeros(2, 3, 32, 128),
            is_enhanced=np.zeros(2, dtype=bool),
            base_roi_scores=[0.8, 0.7],
            created_at_ms=100,
            duplicate_groups={"g-0": [0, 1]},
        )
        assert cr.duplicate_groups == {"g-0": [0, 1]}

    def test_commit_record_default_empty_dict(self) -> None:
        """CommitRecord without duplicate_groups defaults to {}."""
        cr = CommitRecord(
            commit_version=1,
            source_bin_version=1,
            commit_score=0.80,
            batch_tensor=torch.zeros(1, 3, 32, 128),
            is_enhanced=np.zeros(1, dtype=bool),
            base_roi_scores=[0.8],
            created_at_ms=100,
        )
        assert cr.duplicate_groups == {}

    def test_commit_record_stores_per_roi_quality(self) -> None:
        """CommitRecord stores per_roi_quality (H9: base-rois-aligned, unsorted)."""
        cr = CommitRecord(
            commit_version=1,
            source_bin_version=1,
            commit_score=0.80,
            batch_tensor=torch.zeros(3, 3, 32, 128),
            is_enhanced=np.zeros(3, dtype=bool),
            base_roi_scores=[0.9, 0.8, 0.7],
            created_at_ms=100,
            per_roi_quality=[0.8, 0.9, 0.7],
        )
        assert cr.per_roi_quality == [0.8, 0.9, 0.7]

    def test_commit_record_default_empty_list(self) -> None:
        """CommitRecord without per_roi_quality defaults to []."""
        cr = CommitRecord(
            commit_version=1,
            source_bin_version=1,
            commit_score=0.80,
            batch_tensor=torch.zeros(1, 3, 32, 128),
            is_enhanced=np.zeros(1, dtype=bool),
            base_roi_scores=[0.8],
            created_at_ms=100,
        )
        assert cr.per_roi_quality == []


class TestOcrWorkItemExtensions:
    """Chunk-03: OcrWorkItem duplicate_groups + per_roi_quality fields."""

    def test_work_item_accepts_duplicate_groups(self) -> None:
        """OcrWorkItem stores duplicate_groups correctly."""
        wi = OcrWorkItem(
            track_id="t1",
            commit_version=1,
            source_bin_version=1,
            batch_tensor=torch.zeros(2, 3, 32, 128),
            is_enhanced=np.zeros(2, dtype=bool),
            duplicate_groups={"g-0": [0, 1]},
        )
        assert wi.duplicate_groups == {"g-0": [0, 1]}

    def test_work_item_default_empty_dict(self) -> None:
        """OcrWorkItem without duplicate_groups defaults to {}."""
        wi = OcrWorkItem(
            track_id="t1",
            commit_version=1,
            source_bin_version=1,
            batch_tensor=torch.zeros(1, 3, 32, 128),
            is_enhanced=np.zeros(1, dtype=bool),
        )
        assert wi.duplicate_groups == {}

    def test_work_item_stores_per_roi_quality(self) -> None:
        """OcrWorkItem stores per_roi_quality (H9: base-rois-aligned, unsorted)."""
        wi = OcrWorkItem(
            track_id="t1",
            commit_version=1,
            source_bin_version=1,
            batch_tensor=torch.zeros(2, 3, 32, 128),
            is_enhanced=np.zeros(2, dtype=bool),
            per_roi_quality=[0.8, 0.9],
        )
        assert wi.per_roi_quality == [0.8, 0.9]

    def test_work_item_default_empty_list(self) -> None:
        """OcrWorkItem without per_roi_quality defaults to []."""
        wi = OcrWorkItem(
            track_id="t1",
            commit_version=1,
            source_bin_version=1,
            batch_tensor=torch.zeros(1, 3, 32, 128),
            is_enhanced=np.zeros(1, dtype=bool),
        )
        assert wi.per_roi_quality == []


# ===========================================================================
# submit + pull pipeline flow — chunk-03
# ===========================================================================


class TestDuplicateGroupsFlow:
    """Chunk-03: duplicate_groups flows through submit -> pull pipeline."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_submit_stores_duplicate_groups(self, mock_score: MagicMock) -> None:
        """EnhancedBatchSelection with groups -> best_commit.duplicate_groups matches."""
        queue = OcrReadyQueue(make_cfg())
        candidate = EnhancedBatchSelection(
            track_id="t1",
            version=1,
            base_rois=[make_roi_rq() for _ in range(2)],
            enhance_rois=[],
            duplicate_groups={"g-0": [0, 4]},
        )
        proc = make_processed(n_rois=2)
        queue.submit_candidate(candidate, proc, src_bin_version=1, timestamp_ms=100)

        ts = queue._tracks["t1"]
        assert ts.best_commit is not None
        assert ts.best_commit.duplicate_groups == {"g-0": [0, 4]}

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_pull_next_includes_duplicate_groups(self, mock_score: MagicMock) -> None:
        """submit with groups -> pull_next -> work_item.duplicate_groups matches."""
        queue = OcrReadyQueue(make_cfg())
        candidate = EnhancedBatchSelection(
            track_id="t1",
            version=1,
            base_rois=[make_roi_rq() for _ in range(2)],
            enhance_rois=[],
            duplicate_groups={"g-0": [0, 4]},
        )
        proc = make_processed(n_rois=2)
        queue.submit_candidate(candidate, proc, src_bin_version=1, timestamp_ms=100)

        wi = queue.pull_next(worker_id="w1", timestamp_ms=200)
        assert wi is not None
        assert wi.duplicate_groups == {"g-0": [0, 4]}

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_submit_base_only_identity_mapping(self, mock_score: MagicMock) -> None:
        """Empty duplicate_groups={} -> identity mapping auto-populated."""
        queue = OcrReadyQueue(make_cfg())
        candidate = EnhancedBatchSelection(
            track_id="t1",
            version=1,
            base_rois=[make_roi_rq() for _ in range(3)],
            enhance_rois=[],
            duplicate_groups={},  # empty = no enhanced ROIs
        )
        proc = make_processed(n_rois=3)
        queue.submit_candidate(candidate, proc, src_bin_version=1, timestamp_ms=100)

        ts = queue._tracks["t1"]
        assert ts.best_commit is not None
        assert ts.best_commit.duplicate_groups == {"0": [0], "1": [1], "2": [2]}


class TestPerRoiQualityFlow:
    """Chunk-03: per_roi_quality flows through submit -> pull."""

    def test_submit_captures_unsorted_scores(self) -> None:
        """per_roi_quality matches original unsorted order (H9: base-rois-aligned)."""
        queue = OcrReadyQueue(make_cfg())
        candidate = make_candidate(track_id="t1", n_rois=4)
        proc = make_processed(n_rois=4)

        # Mock compute_ocr_likelihood_score to return different scores per ROI
        scores_per_call = [0.5, 0.9, 0.3, 0.7]
        with patch(_MOCK_SCORE, side_effect=scores_per_call):
            queue.submit_candidate(candidate, proc, src_bin_version=1, timestamp_ms=100)

        ts = queue._tracks["t1"]
        assert ts.best_commit is not None
        # per_roi_quality should be unsorted (original order)
        assert ts.best_commit.per_roi_quality == [0.5, 0.9, 0.3, 0.7]
        # base_roi_scores should be sorted descending
        assert ts.best_commit.base_roi_scores == [0.9, 0.7, 0.5, 0.3]

    def test_pull_next_includes_per_roi_quality(self) -> None:
        """submit -> pull_next -> work_item.per_roi_quality preserved."""
        queue = OcrReadyQueue(make_cfg())
        candidate = make_candidate(track_id="t1", n_rois=3)
        proc = make_processed(n_rois=3)

        scores_per_call = [0.6, 0.8, 0.4]
        with patch(_MOCK_SCORE, side_effect=scores_per_call):
            queue.submit_candidate(candidate, proc, src_bin_version=1, timestamp_ms=100)

        wi = queue.pull_next(worker_id="w1", timestamp_ms=200)
        assert wi is not None
        assert wi.per_roi_quality == [0.6, 0.8, 0.4]

    def test_per_roi_quality_round_trip(self) -> None:
        """Exact round-trip: [0.3, 0.9, 0.5, 0.7] preserved (M9)."""
        queue = OcrReadyQueue(make_cfg())
        candidate = make_candidate(track_id="t1", n_rois=4)
        proc = make_processed(n_rois=4)

        scores_per_call = [0.3, 0.9, 0.5, 0.7]
        with patch(_MOCK_SCORE, side_effect=scores_per_call):
            queue.submit_candidate(candidate, proc, src_bin_version=1, timestamp_ms=100)

        wi = queue.pull_next(worker_id="w1", timestamp_ms=200)
        assert wi is not None
        assert wi.per_roi_quality == [0.3, 0.9, 0.5, 0.7]


# ===========================================================================
# fail_commit — chunk-03
# ===========================================================================


class TestFailCommit:
    """Chunk-03: fail_commit clears in-flight without recording OCR result."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_fail_commit_clears_inflight(self, mock_score: MagicMock) -> None:
        """submit + pull + fail_commit -> in_flight None, track re-eligible."""
        queue = OcrReadyQueue(make_cfg())
        queue.submit_candidate(
            make_candidate(), make_processed(), src_bin_version=1, timestamp_ms=100
        )
        wi = queue.pull_next(worker_id="w1", timestamp_ms=200)
        assert wi is not None

        result = queue.fail_commit(wi.track_id, wi.commit_version, reason="inference_failed")
        assert result is True

        ts = queue._tracks["t1"]
        assert ts.in_flight is None
        # Track should be re-eligible (can be pulled again)
        wi2 = queue.pull_next(worker_id="w2", timestamp_ms=300)
        assert wi2 is not None
        assert wi2.track_id == "t1"

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_fail_commit_version_mismatch(self, mock_score: MagicMock) -> None:
        """Wrong version -> returns False."""
        queue = OcrReadyQueue(make_cfg())
        queue.submit_candidate(
            make_candidate(), make_processed(), src_bin_version=1, timestamp_ms=100
        )
        queue.pull_next(worker_id="w1", timestamp_ms=200)

        result = queue.fail_commit("t1", commit_version=999, reason="test")
        assert result is False

    def test_fail_commit_track_not_found(self) -> None:
        """Nonexistent track -> returns False."""
        queue = OcrReadyQueue(make_cfg())
        result = queue.fail_commit("nonexistent", commit_version=1, reason="test")
        assert result is False

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_fail_commit_preserves_consumed_version(self, mock_score: MagicMock) -> None:
        """fail_commit does NOT update consumed_commit_version."""
        queue = OcrReadyQueue(make_cfg())
        # Submit + pull + finish v1 (normal consumption)
        queue.submit_candidate(
            make_candidate(), make_processed(), src_bin_version=1, timestamp_ms=100
        )
        wi1 = queue.pull_next(worker_id="w1", timestamp_ms=200)
        assert wi1 is not None
        queue.finish_commit("t1", wi1.commit_version)

        assert queue._tracks["t1"].consumed_commit_version == 1

        # Submit v2 + pull + fail_commit
        mock_score.return_value = 0.90
        queue.submit_candidate(
            make_candidate(), make_processed(), src_bin_version=2, timestamp_ms=300
        )
        wi2 = queue.pull_next(worker_id="w1", timestamp_ms=400)
        assert wi2 is not None
        queue.fail_commit("t1", wi2.commit_version, reason="inference_failed")

        # consumed_commit_version should still be 1 (NOT updated by fail_commit)
        assert queue._tracks["t1"].consumed_commit_version == 1

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_fail_commit_logs_info(
        self, mock_score: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """INFO log with track_id, commit_version, reason."""
        queue = OcrReadyQueue(make_cfg())
        queue.submit_candidate(
            make_candidate(), make_processed(), src_bin_version=1, timestamp_ms=100
        )
        queue.pull_next(worker_id="w1", timestamp_ms=200)

        with caplog.at_level(logging.INFO, logger="consumer.ops_ocr_queue"):
            queue.fail_commit("t1", commit_version=1, reason="parseq_inference_returned_none")

        assert any(
            "t1" in r.message and "1" in r.message and "parseq_inference_returned_none" in r.message
            for r in caplog.records
            if r.levelno == logging.INFO
        )

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_fail_commit_warning_on_mismatch(
        self, mock_score: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """WARNING log on mismatch (M17: race condition visibility)."""
        queue = OcrReadyQueue(make_cfg())
        queue.submit_candidate(
            make_candidate(), make_processed(), src_bin_version=1, timestamp_ms=100
        )
        queue.pull_next(worker_id="w1", timestamp_ms=200)

        with caplog.at_level(logging.WARNING, logger="consumer.ops_ocr_queue"):
            queue.fail_commit("t1", commit_version=999, reason="test")

        assert any(r.levelno == logging.WARNING for r in caplog.records)


# ===========================================================================
# In-flight timeout — chunk-03
# ===========================================================================


class TestInFlightTimeout:
    """Chunk-03: in-flight timeout expiry in pull_next."""

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_timeout_expires_stale_record(
        self, mock_score: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """pull at t=0, pull_next at t=31000 -> in_flight cleared, WARNING logged."""
        cfg = make_cfg(in_flight_timeout_ms=30_000)
        queue = OcrReadyQueue(cfg)
        queue.submit_candidate(
            make_candidate(track_id="t1"), make_processed(), src_bin_version=1, timestamp_ms=0
        )

        # Pull t1 at t=0
        wi1 = queue.pull_next(worker_id="w1", timestamp_ms=0)
        assert wi1 is not None
        assert wi1.track_id == "t1"
        assert queue._tracks["t1"].in_flight is not None
        assert queue._tracks["t1"].in_flight.dispatched_at_ms == 0

        # At t=31000, t1's in-flight should expire during the timeout sweep.
        # After clearing, t1 becomes re-eligible and gets re-dispatched.
        with caplog.at_level(logging.WARNING, logger="consumer.ops_ocr_queue"):
            wi2 = queue.pull_next(worker_id="w2", timestamp_ms=31_000)

        # WARNING should have been logged for the timeout expiry
        assert any(r.levelno == logging.WARNING and "timeout" in r.message for r in caplog.records)
        # t1 was cleared then re-dispatched with the new timestamp
        assert wi2 is not None
        assert wi2.track_id == "t1"
        assert queue._tracks["t1"].in_flight is not None
        assert queue._tracks["t1"].in_flight.dispatched_at_ms == 31_000
        assert queue._tracks["t1"].in_flight.worker_id == "w2"

    @patch(_MOCK_SCORE, return_value=0.80)
    def test_timeout_preserves_fresh_record(self, mock_score: MagicMock) -> None:
        """pull at t=0, pull_next at t=5000 -> still in_flight."""
        cfg = make_cfg(in_flight_timeout_ms=30_000)
        queue = OcrReadyQueue(cfg)
        queue.submit_candidate(
            make_candidate(track_id="t1"), make_processed(), src_bin_version=1, timestamp_ms=0
        )
        queue.submit_candidate(
            make_candidate(track_id="t2"), make_processed(), src_bin_version=1, timestamp_ms=0
        )

        # Pull t1 at t=0
        wi1 = queue.pull_next(worker_id="w1", timestamp_ms=0)
        assert wi1 is not None
        assert wi1.track_id == "t1"

        # At t=5000 (well within timeout), t1's in-flight should NOT expire
        wi2 = queue.pull_next(worker_id="w2", timestamp_ms=5_000)
        assert wi2 is not None
        assert wi2.track_id == "t2"  # t2 gets pulled, t1 still in-flight

        # Verify t1 still has in_flight
        assert queue._tracks["t1"].in_flight is not None

    def test_timeout_config_validation(self) -> None:
        """in_flight_timeout_ms=0 raises ValueError."""
        with pytest.raises(ValueError):
            make_cfg(in_flight_timeout_ms=0)


class TestNanGuardProperty:
    """Chunk-05: Pass C — property-based NaN/Inf guard test."""

    @given(
        scores=st.lists(
            st.floats(min_value=0.0, max_value=1.0),
            min_size=1,
            max_size=10,
        ),
        insert_pos=st.integers(min_value=0, max_value=9),
        bad_value=st.sampled_from([float("nan"), float("inf"), float("-inf")]),
    )
    @settings(max_examples=200)
    def test_nan_guard_property_any_nonfinite_returns_zero(
        self, scores: list[float], insert_pos: int, bad_value: float
    ) -> None:
        """Any list with at least one non-finite element returns exactly 0.0."""
        # Insert a non-finite value at a valid position
        pos = insert_pos % (len(scores) + 1)
        scores_with_bad = scores[:pos] + [bad_value] + scores[pos:]
        # Sort descending (as _compute_commit_score expects)
        # NaN breaks sorting, but the guard should catch it regardless
        result = _compute_commit_score(scores_with_bad)
        assert result == 0.0
