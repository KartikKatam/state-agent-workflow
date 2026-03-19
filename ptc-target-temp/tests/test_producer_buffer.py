"""Tests for producer/buffer.py — IdBufferManager.

Covers all 8 public methods: __init__, insert_roi (4-gate pipeline),
get_snapshot_if_updated, get_all_updated_bins, evict_tracks,
get_active_track_ids, get_stats, get_all_stats.

~53 tests organized by:
  - Init (2)
  - Gate 1-2: new track, frame ordering (3)
  - Gate 3: quality gating with bootstrap (7)
  - Insertion cases: novel/similar × full/not-full (7)
  - Post-insert invariants (6)
  - Property-based (5)
  - Snapshots (7)
  - Batch retrieval (5)
  - Eviction (6)
  - Query methods (6)
"""

from __future__ import annotations

import logging

import numpy as np
from hypothesis import given, settings, strategies as st

from producer.buffer import IdBufferManager
from producer.config import ProducerConfig
from producer.models import BufferStats, RoiFastQuality

from .conftest_producer import (
    make_config,
    make_fast_quality_metrics,
    make_roi_fast_quality,
    make_roi_image,
)

# ---------------------------------------------------------------------------
# Helper: create RoiFastQuality with controlled similarity
# ---------------------------------------------------------------------------


def _make_buffer_roi(
    *,
    track_id: str = "track-1",
    frame_idx: int = 1,
    quality_score: float = 0.75,
    band_edge_mean: float = 0.8,
    thumb_seed: int = 42,
) -> RoiFastQuality:
    """Create RoiFastQuality suitable for buffer insertion testing.

    Same thumb_seed → identical thumb + histogram → "similar" (SSIM≈1).
    Different thumb_seed → random thumb + histogram → "novel" (SSIM≈0).
    """
    rng = np.random.RandomState(thumb_seed)
    thumb = rng.randint(0, 256, (48, 160), dtype=np.uint8)
    hist_raw = rng.rand(25).astype(np.float32)
    hist = hist_raw / np.linalg.norm(hist_raw)

    return make_roi_fast_quality(
        roi=make_roi_image(track_id=track_id, frame_idx=frame_idx),
        metrics=make_fast_quality_metrics(
            band_edge_mean=band_edge_mean,
            gradient_histogram=hist,
        ),
        quality_score=quality_score,
        thumb_gray=thumb,
    )


def _default_cfg(**overrides) -> ProducerConfig:
    """ProducerConfig with buffer_band_edge_min=0.0 for simpler tests."""
    defaults = {"buffer_band_edge_min": 0.0}
    defaults.update(overrides)
    return make_config(**defaults)


# =========================================================================
# Init
# =========================================================================


class TestBufferInit:
    def test_buffer_init_empty(self):
        """bins=={}, stats=={}."""
        cfg = make_config()
        buf = IdBufferManager(cfg)
        assert buf.bins == {}
        assert buf.stats == {}

    def test_buffer_init_default_logger(self):
        """logger=None → default created (no crash)."""
        cfg = make_config()
        buf = IdBufferManager(cfg, logger=None)
        assert buf.logger is not None
        assert isinstance(buf.logger, logging.Logger)


# =========================================================================
# Gate 1 (New Track) + Gate 2 (Frame Order)
# =========================================================================


class TestBufferInsertGates:
    def test_insert_new_track(self):
        """New track → bin created, version=0→1, last_update=-1→frame_idx."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        roi = _make_buffer_roi(track_id="t1", frame_idx=5)

        assert buf.insert_roi(roi) is True
        assert "t1" in buf.bins
        assert buf.bins["t1"].version == 1
        assert buf.bins["t1"].last_update_frame_idx == 5
        assert len(buf.bins["t1"].entries) == 1

    def test_insert_ooo_drop(self):
        """frame_idx < last → drop, ooo_drops++."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        # Insert at frame 10
        buf.insert_roi(_make_buffer_roi(frame_idx=10, thumb_seed=1))
        # Try earlier frame
        result = buf.insert_roi(_make_buffer_roi(frame_idx=5, thumb_seed=2))

        assert result is False
        stats = buf.stats["track-1"]
        assert stats.out_of_order_drops == 1

    def test_insert_same_frame_drop(self):
        """Same frame_idx → drop (uses <=)."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(frame_idx=10, thumb_seed=1))
        result = buf.insert_roi(_make_buffer_roi(frame_idx=10, thumb_seed=2))

        assert result is False
        assert buf.stats["track-1"].out_of_order_drops == 1


# =========================================================================
# Gate 3 (Quality)
# =========================================================================


class TestBufferQualityGate:
    def test_insert_quality_below(self):
        """Quality below threshold → drop, quality_gate_drops++."""
        cfg = _default_cfg(buffer_min_quality=0.5, buffer_bootstrap_k_insertions=0)
        buf = IdBufferManager(cfg)
        roi = _make_buffer_roi(quality_score=0.3)

        assert buf.insert_roi(roi) is False
        assert buf.stats["track-1"].quality_gate_drops == 1

    def test_insert_quality_at_threshold(self):
        """Quality at threshold → pass (uses < for rejection, so >= passes).

        Also verifies anti-bias #1: NaN quality passes (IEEE 754 NaN<x=False).
        """
        cfg = _default_cfg(buffer_min_quality=0.5, buffer_bootstrap_k_insertions=0)
        buf = IdBufferManager(cfg)
        roi = _make_buffer_roi(quality_score=0.5)
        assert buf.insert_roi(roi) is True

        # Anti-bias #1: NaN quality_score passes quality gate
        buf2 = IdBufferManager(cfg)
        roi_nan = _make_buffer_roi(track_id="t-nan", quality_score=float("nan"), thumb_seed=99)
        assert buf2.insert_roi(roi_nan) is True

    def test_insert_bootstrap_discount(self):
        """Bootstrap phase → discounted threshold allows lower quality."""
        # Full threshold=0.5, bootstrap discount=0.5 → bootstrap threshold=0.25
        cfg = _default_cfg(
            buffer_min_quality=0.5,
            buffer_bootstrap_discount=0.5,
            buffer_bootstrap_k_insertions=3,
        )
        buf = IdBufferManager(cfg)
        # quality=0.3 passes 0.25 (bootstrap) but fails 0.5 (full)
        roi = _make_buffer_roi(quality_score=0.3)
        assert buf.insert_roi(roi) is True

    def test_insert_post_bootstrap(self):
        """Post-bootstrap → full threshold applied."""
        cfg = _default_cfg(
            buffer_min_quality=0.5,
            buffer_bootstrap_discount=0.5,
            buffer_bootstrap_k_insertions=2,
        )
        buf = IdBufferManager(cfg)
        # Exhaust bootstrap phase (2 attempts)
        buf.insert_roi(_make_buffer_roi(frame_idx=1, quality_score=0.8, thumb_seed=1))
        buf.insert_roi(_make_buffer_roi(frame_idx=2, quality_score=0.8, thumb_seed=2))
        # 3rd attempt: post-bootstrap, quality=0.3 fails full threshold=0.5
        roi = _make_buffer_roi(frame_idx=3, quality_score=0.3, thumb_seed=3)
        assert buf.insert_roi(roi) is False
        assert buf.stats["track-1"].quality_gate_drops == 1

    def test_insert_kth_full_threshold(self):
        """K-th attempt uses full threshold (increment BEFORE check)."""
        # bootstrap_k=3: 1st,2nd use discount; 3rd uses full
        cfg = _default_cfg(
            buffer_min_quality=0.5,
            buffer_bootstrap_discount=0.5,
            buffer_bootstrap_k_insertions=3,
        )
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(frame_idx=1, quality_score=0.8, thumb_seed=1))
        buf.insert_roi(_make_buffer_roi(frame_idx=2, quality_score=0.8, thumb_seed=2))
        # 3rd attempt: attempted goes 2→3, 3<3=False → full threshold=0.5
        roi = _make_buffer_roi(frame_idx=3, quality_score=0.3, thumb_seed=3)
        assert buf.insert_roi(roi) is False

    def test_insert_band_edge_min(self):
        """band_edge below minimum → drop even if quality passes."""
        cfg = _default_cfg(
            buffer_min_quality=0.1,
            buffer_band_edge_min=0.5,
            buffer_bootstrap_k_insertions=0,
        )
        buf = IdBufferManager(cfg)
        roi = _make_buffer_roi(quality_score=0.9, band_edge_mean=0.3)
        assert buf.insert_roi(roi) is False
        assert buf.stats["track-1"].quality_gate_drops == 1

    def test_insert_attempted_before_check(self):
        """attempted_insertions incremented before quality check."""
        cfg = _default_cfg(buffer_min_quality=0.99, buffer_bootstrap_k_insertions=0)
        buf = IdBufferManager(cfg)
        # Quality too low → fails gate, but attempted should still be 1
        roi = _make_buffer_roi(quality_score=0.1)
        buf.insert_roi(roi)
        assert buf.bins["track-1"].attempted_insertions == 1


# =========================================================================
# Insertion Cases (novel/similar × full/not-full)
# =========================================================================


class TestBufferInsertCases:
    def test_insert_novel_append(self):
        """Not full + novel → append, appends++."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(frame_idx=1, thumb_seed=1))
        # Different thumb → novel
        result = buf.insert_roi(_make_buffer_roi(frame_idx=2, thumb_seed=2))

        assert result is True
        assert len(buf.bins["track-1"].entries) == 2
        assert buf.stats["track-1"].appends == 2

    def test_insert_similar_replace(self):
        """Not full + similar + quality margin met → replace."""
        cfg = _default_cfg(buffer_quality_margin_similar=0.02)
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(frame_idx=1, quality_score=0.5, thumb_seed=10))
        # Same thumb → similar, higher quality → replace
        result = buf.insert_roi(_make_buffer_roi(frame_idx=2, quality_score=0.6, thumb_seed=10))
        assert result is True
        assert len(buf.bins["track-1"].entries) == 1  # Replaced, not appended
        assert buf.stats["track-1"].replaces == 1

    def test_insert_similar_drop(self):
        """Not full + similar + quality margin NOT met → similarity_drops++."""
        cfg = _default_cfg(buffer_quality_margin_similar=0.02)
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(frame_idx=1, quality_score=0.5, thumb_seed=10))
        # Same thumb → similar, same quality → margin not met → drop
        result = buf.insert_roi(_make_buffer_roi(frame_idx=2, quality_score=0.5, thumb_seed=10))
        assert result is False
        assert buf.stats["track-1"].similarity_drops == 1

    def test_insert_full_novel_replace(self):
        """Full + novel + quality > worst + margin → replace worst."""
        cap = 2
        cfg = _default_cfg(buffer_bin_capacity=cap, buffer_quality_margin_worst=0.05)
        buf = IdBufferManager(cfg)
        # Fill bin with 2 entries
        buf.insert_roi(_make_buffer_roi(frame_idx=1, quality_score=0.4, thumb_seed=1))
        buf.insert_roi(_make_buffer_roi(frame_idx=2, quality_score=0.5, thumb_seed=2))
        assert len(buf.bins["track-1"].entries) == cap
        # Novel, quality > worst(0.4) + margin(0.05)
        result = buf.insert_roi(_make_buffer_roi(frame_idx=3, quality_score=0.8, thumb_seed=3))
        assert result is True
        assert len(buf.bins["track-1"].entries) == cap
        assert buf.stats["track-1"].replaces >= 1

    def test_insert_full_novel_drop(self):
        """Full + novel + quality ≤ worst + margin → drop."""
        cap = 2
        cfg = _default_cfg(buffer_bin_capacity=cap, buffer_quality_margin_worst=0.05)
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(frame_idx=1, quality_score=0.7, thumb_seed=1))
        buf.insert_roi(_make_buffer_roi(frame_idx=2, quality_score=0.8, thumb_seed=2))
        # Novel but quality(0.5) ≤ worst(0.7) + 0.05 → drop
        result = buf.insert_roi(_make_buffer_roi(frame_idx=3, quality_score=0.5, thumb_seed=3))
        assert result is False

    def test_insert_full_similar_replace(self):
        """Full + similar + quality > similar + margin → replace."""
        cap = 2
        cfg = _default_cfg(buffer_bin_capacity=cap, buffer_quality_margin_similar=0.02)
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(frame_idx=1, quality_score=0.5, thumb_seed=1))
        buf.insert_roi(_make_buffer_roi(frame_idx=2, quality_score=0.5, thumb_seed=2))
        # Same thumb as seed=1 → similar to entry 0, higher quality → replace
        result = buf.insert_roi(_make_buffer_roi(frame_idx=3, quality_score=0.7, thumb_seed=1))
        assert result is True
        assert len(buf.bins["track-1"].entries) == cap
        assert buf.stats["track-1"].replaces >= 1

    def test_insert_full_similar_drop(self):
        """Full + similar + quality ≤ similar + margin → drop."""
        cap = 2
        cfg = _default_cfg(buffer_bin_capacity=cap, buffer_quality_margin_similar=0.02)
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(frame_idx=1, quality_score=0.5, thumb_seed=1))
        buf.insert_roi(_make_buffer_roi(frame_idx=2, quality_score=0.5, thumb_seed=2))
        # Same thumb as seed=1 → similar, same quality → margin not met
        result = buf.insert_roi(_make_buffer_roi(frame_idx=3, quality_score=0.5, thumb_seed=1))
        assert result is False
        assert buf.stats["track-1"].similarity_drops >= 1


# =========================================================================
# Post-Insert Invariants
# =========================================================================


class TestBufferPostInsert:
    def test_insert_read_only_arrays(self):
        """crop_img, gradient_histogram, thumb_gray read-only after insert."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(frame_idx=1))

        entry = buf.bins["track-1"].entries[0]
        assert entry.roi.crop_img.flags.writeable is False
        assert entry.metrics.gradient_histogram.flags.writeable is False
        assert entry.thumb_gray.flags.writeable is False

    def test_insert_version_increment(self):
        """Version increments by 1 on append/replace."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)

        buf.insert_roi(_make_buffer_roi(frame_idx=1, thumb_seed=1))
        assert buf.bins["track-1"].version == 1

        buf.insert_roi(_make_buffer_roi(frame_idx=2, thumb_seed=2))
        assert buf.bins["track-1"].version == 2

    def test_insert_version_no_increment_on_drop(self):
        """Version does NOT increment on drop."""
        cfg = _default_cfg(buffer_min_quality=0.5, buffer_bootstrap_k_insertions=0)
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(frame_idx=1, quality_score=0.8, thumb_seed=1))
        v_before = buf.bins["track-1"].version

        # Quality too low → drop
        buf.insert_roi(_make_buffer_roi(frame_idx=2, quality_score=0.1, thumb_seed=2))
        assert buf.bins["track-1"].version == v_before

    def test_insert_last_update_frame(self):
        """last_update_frame_idx updated on insert."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(frame_idx=7, thumb_seed=1))
        assert buf.bins["track-1"].last_update_frame_idx == 7

        buf.insert_roi(_make_buffer_roi(frame_idx=15, thumb_seed=2))
        assert buf.bins["track-1"].last_update_frame_idx == 15

    def test_insert_successful_insertions(self):
        """successful_insertions incremented on insert."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(frame_idx=1, thumb_seed=1))
        assert buf.bins["track-1"].successful_insertions == 1

        buf.insert_roi(_make_buffer_roi(frame_idx=2, thumb_seed=2))
        assert buf.bins["track-1"].successful_insertions == 2

    def test_insert_first_roi_novel(self):
        """First ROI for new track: empty bin → novel → append."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        result = buf.insert_roi(_make_buffer_roi(frame_idx=1))

        assert result is True
        assert len(buf.bins["track-1"].entries) == 1
        assert buf.stats["track-1"].appends == 1


# =========================================================================
# Property-Based Tests
# =========================================================================


class TestBufferProperties:
    @given(
        data=st.lists(
            st.tuples(
                st.integers(min_value=0, max_value=500),
                st.floats(min_value=0.0, max_value=1.0),
                st.integers(min_value=0, max_value=200),
            ),
            min_size=1,
            max_size=15,
        )
    )
    @settings(max_examples=50)
    def test_insert_stats_accounting_property(self, data):
        """total == quality_drops + ooo_drops + appends + replaces + sim_drops."""
        cfg = _default_cfg(buffer_bin_capacity=4)
        buf = IdBufferManager(cfg)
        for frame_idx, quality, seed in data:
            buf.insert_roi(
                _make_buffer_roi(frame_idx=frame_idx, quality_score=quality, thumb_seed=seed)
            )
        for stats in buf.stats.values():
            total = (
                stats.quality_gate_drops
                + stats.out_of_order_drops
                + stats.appends
                + stats.replaces
                + stats.similarity_drops
            )
            assert stats.total_attempts == total

    @given(
        data=st.lists(
            st.tuples(
                st.integers(min_value=0, max_value=500),
                st.floats(min_value=0.0, max_value=1.0),
                st.integers(min_value=0, max_value=200),
            ),
            min_size=1,
            max_size=15,
        )
    )
    @settings(max_examples=50)
    def test_insert_capacity_property(self, data):
        """len(entries) <= buffer_bin_capacity."""
        cap = 4
        cfg = _default_cfg(buffer_bin_capacity=cap)
        buf = IdBufferManager(cfg)
        for frame_idx, quality, seed in data:
            buf.insert_roi(
                _make_buffer_roi(frame_idx=frame_idx, quality_score=quality, thumb_seed=seed)
            )
        for b in buf.bins.values():
            assert len(b.entries) <= cap

    @given(
        data=st.lists(
            st.tuples(
                st.integers(min_value=0, max_value=500),
                st.floats(min_value=0.0, max_value=1.0),
                st.integers(min_value=0, max_value=200),
            ),
            min_size=1,
            max_size=15,
        )
    )
    @settings(max_examples=50)
    def test_insert_version_monotonicity_property(self, data):
        """Version is non-decreasing."""
        cfg = _default_cfg(buffer_bin_capacity=4)
        buf = IdBufferManager(cfg)
        prev_version = 0
        for frame_idx, quality, seed in data:
            buf.insert_roi(
                _make_buffer_roi(frame_idx=frame_idx, quality_score=quality, thumb_seed=seed)
            )
            v = buf.bins["track-1"].version
            assert v >= prev_version
            prev_version = v

    @given(
        data=st.lists(
            st.tuples(
                st.integers(min_value=1, max_value=500),
                st.floats(min_value=0.0, max_value=1.0),
                st.integers(min_value=0, max_value=200),
            ),
            min_size=1,
            max_size=15,
        )
    )
    @settings(max_examples=50)
    def test_insert_return_version_property(self, data):
        """Return True ↔ version increased."""
        cfg = _default_cfg(buffer_bin_capacity=4)
        buf = IdBufferManager(cfg)
        for frame_idx, quality, seed in data:
            v_before = buf.bins["track-1"].version if "track-1" in buf.bins else 0
            result = buf.insert_roi(
                _make_buffer_roi(frame_idx=frame_idx, quality_score=quality, thumb_seed=seed)
            )
            v_after = buf.bins["track-1"].version
            if result:
                assert v_after == v_before + 1
            else:
                assert v_after == v_before

    @given(
        data=st.lists(
            st.floats(min_value=0.0, max_value=1.0),
            min_size=1,
            max_size=15,
        )
    )
    @settings(max_examples=50)
    def test_insert_frame_monotonicity_property(self, data):
        """Strictly increasing frame_idx → no ooo_drops."""
        cfg = _default_cfg(buffer_bin_capacity=4)
        buf = IdBufferManager(cfg)
        for i, quality in enumerate(data):
            buf.insert_roi(_make_buffer_roi(frame_idx=i + 1, quality_score=quality, thumb_seed=i))
        for stats in buf.stats.values():
            assert stats.out_of_order_drops == 0


# =========================================================================
# Snapshots
# =========================================================================


class TestBufferSnapshot:
    def test_snapshot_newer_version(self):
        """version > last_seen → returns snapshot."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1))

        snap = buf.get_snapshot_if_updated("t1", 0)
        assert snap is not None
        assert snap.track_id == "t1"
        assert snap.version == 1

    def test_snapshot_same_version(self):
        """version == last_seen → None."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1))

        assert buf.get_snapshot_if_updated("t1", 1) is None

    def test_snapshot_older_version(self):
        """last_seen > version → None."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1))
        # version=1, last_seen=5
        assert buf.get_snapshot_if_updated("t1", 5) is None

    def test_snapshot_missing_track(self):
        """Missing track_id → None."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        assert buf.get_snapshot_if_updated("nonexistent", -1) is None

    def test_snapshot_entries_independent(self):
        """Snapshot entries is a new list — modifying it doesn't affect bin."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1))

        snap = buf.get_snapshot_if_updated("t1", 0)
        assert snap is not None
        original_len = len(buf.bins["t1"].entries)
        snap.entries.clear()
        assert len(buf.bins["t1"].entries) == original_len

    def test_snapshot_default_last_seen(self):
        """last_seen=-1 → always returns snapshot (any version > -1)."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1))

        snap = buf.get_snapshot_if_updated("t1", -1)
        assert snap is not None

    def test_snapshot_empty_bin(self):
        """Empty bin but version(0) > last_seen(-1) → snapshot with empty entries."""
        cfg = _default_cfg(buffer_min_quality=0.99, buffer_bootstrap_k_insertions=0)
        buf = IdBufferManager(cfg)
        # Quality too low → bin created but entry not appended
        buf.insert_roi(_make_buffer_roi(track_id="t1", quality_score=0.1))

        assert len(buf.bins["t1"].entries) == 0
        snap = buf.get_snapshot_if_updated("t1", -1)
        assert snap is not None
        assert snap.entries == []


# =========================================================================
# Batch Retrieval (get_all_updated_bins)
# =========================================================================


class TestBufferBatchRetrieval:
    def test_all_updated_empty(self):
        """No bins → []."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        assert buf.get_all_updated_bins({}) == []

    def test_all_updated_default_versions(self):
        """Empty last_seen → all bins returned (default version -1)."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1, thumb_seed=1))
        buf.insert_roi(_make_buffer_roi(track_id="t2", frame_idx=1, thumb_seed=2))

        snaps = buf.get_all_updated_bins({})
        assert len(snaps) == 2

    def test_all_updated_all_current(self):
        """All at current version → []."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1, thumb_seed=1))
        buf.insert_roi(_make_buffer_roi(track_id="t2", frame_idx=1, thumb_seed=2))

        versions = {"t1": 1, "t2": 1}
        assert buf.get_all_updated_bins(versions) == []

    def test_all_updated_extra_keys(self):
        """Extra keys in last_seen → ignored, no error."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1))

        snaps = buf.get_all_updated_bins({"t1": 0, "ghost": 99})
        assert len(snaps) == 1

    def test_all_updated_equivalence(self):
        """Equivalent to per-track get_snapshot_if_updated."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1, thumb_seed=1))
        buf.insert_roi(_make_buffer_roi(track_id="t2", frame_idx=1, thumb_seed=2))
        buf.insert_roi(_make_buffer_roi(track_id="t2", frame_idx=2, thumb_seed=3))

        versions = {"t1": 1, "t2": 0}
        bulk = buf.get_all_updated_bins(versions)
        per_track = []
        for tid in buf.bins:
            s = buf.get_snapshot_if_updated(tid, versions.get(tid, -1))
            if s is not None:
                per_track.append(s)

        bulk_ids = {s.track_id for s in bulk}
        per_ids = {s.track_id for s in per_track}
        assert bulk_ids == per_ids


# =========================================================================
# Eviction
# =========================================================================


class TestBufferEviction:
    def test_evict_both_deleted(self):
        """Both bin and stats deleted."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1))

        buf.evict_tracks(["t1"])
        assert "t1" not in buf.bins
        assert "t1" not in buf.stats

    def test_evict_unknown(self):
        """Unknown IDs → no-op, returns 0."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        assert buf.evict_tracks(["ghost"]) == 0

    def test_evict_count_accuracy(self):
        """Return count matches actual evictions."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1, thumb_seed=1))
        buf.insert_roi(_make_buffer_roi(track_id="t2", frame_idx=1, thumb_seed=2))

        removed = buf.evict_tracks(["t1", "t2", "ghost"])
        assert removed == 2

    def test_evict_idempotent(self):
        """Evict twice → second returns 0."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1))

        assert buf.evict_tracks(["t1"]) == 1
        assert buf.evict_tracks(["t1"]) == 0

    def test_evict_no_side_effects(self):
        """No side effects on other tracks."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1, thumb_seed=1))
        buf.insert_roi(_make_buffer_roi(track_id="t2", frame_idx=1, thumb_seed=2))

        v_before = buf.bins["t2"].version
        entries_before = len(buf.bins["t2"].entries)
        buf.evict_tracks(["t1"])

        assert buf.bins["t2"].version == v_before
        assert len(buf.bins["t2"].entries) == entries_before

    def test_evict_null_queries(self):
        """After eviction: get_stats → None, get_snapshot → None."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1))
        buf.evict_tracks(["t1"])

        assert buf.get_stats("t1") is None
        assert buf.get_snapshot_if_updated("t1", -1) is None


# =========================================================================
# Query Methods
# =========================================================================


class TestBufferQueries:
    def test_active_track_ids_empty(self):
        """Empty → []."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        assert buf.get_active_track_ids() == []

    def test_active_track_ids_nonempty_filter(self):
        """Only returns bins WITH entries (anti-bias #2)."""
        cfg = _default_cfg(buffer_min_quality=0.99, buffer_bootstrap_k_insertions=0)
        buf = IdBufferManager(cfg)
        # t1: quality too low → bin created, entries empty
        buf.insert_roi(_make_buffer_roi(track_id="t1", quality_score=0.1, thumb_seed=1))
        # t2: quality high enough → entry appended
        buf.insert_roi(_make_buffer_roi(track_id="t2", quality_score=1.0, thumb_seed=2))

        assert "t1" in buf.bins  # Bin exists
        assert len(buf.bins["t1"].entries) == 0  # But empty
        active = buf.get_active_track_ids()
        assert "t1" not in active
        assert "t2" in active

    def test_active_track_ids_after_eviction(self):
        """Evicted absent from result."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1, thumb_seed=1))
        buf.insert_roi(_make_buffer_roi(track_id="t2", frame_idx=1, thumb_seed=2))

        buf.evict_tracks(["t1"])
        active = buf.get_active_track_ids()
        assert "t1" not in active
        assert "t2" in active

    def test_get_stats_known(self):
        """Known track → BufferStats."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1))

        stats = buf.get_stats("t1")
        assert isinstance(stats, BufferStats)
        assert stats.total_attempts == 1

    def test_get_stats_unknown(self):
        """Unknown track → None."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        assert buf.get_stats("nonexistent") is None

    def test_get_all_stats_shallow_copy(self):
        """Dict is copy, values are shared references."""
        cfg = _default_cfg()
        buf = IdBufferManager(cfg)
        buf.insert_roi(_make_buffer_roi(track_id="t1", frame_idx=1))

        all_stats = buf.get_all_stats()
        assert all_stats is not buf.stats
        assert all_stats["t1"] is buf.stats["t1"]
