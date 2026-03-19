"""Tests for stateful tracking components in ops_tracking.py.

Covers: estimate_camera_motion, predict_track_alpha_beta, check_velocity_consistency,
update_track_state_alpha_beta, initialize_new_track, cleanup_lost_tracks,
and PlateTracker class integration. Property-based tests for dimension clamping,
velocity consistency, track conservation, and age monotonicity.
"""

from __future__ import annotations

import copy
import uuid

import cv2
import numpy as np
import pytest
from hypothesis import given, settings, strategies as st

from producer.ops_tracking import (
    PlateTracker,
    bbox_to_position,
    check_velocity_consistency,
    cleanup_lost_tracks,
    estimate_camera_motion,
    initialize_new_track,
    predict_track_alpha_beta,
    track_detections,
    update_track_state_alpha_beta,
)

from .conftest_producer import make_config, make_detection, make_track, st_track

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gray_frame(h: int = 480, w: int = 640, seed: int = 42) -> np.ndarray:
    """Create a grayscale frame with textured corners (good for LK)."""
    rng = np.random.default_rng(seed)
    frame = rng.integers(0, 256, size=(h, w), dtype=np.uint8)
    # Add some structure so goodFeaturesToTrack finds corners
    for y in range(0, h, 40):
        for x in range(0, w, 40):
            frame[y : y + 5, x : x + 5] = 255
    return frame


def _shift_frame(frame: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Translate a frame by (dx, dy) pixels using affine transform."""
    M = np.array([[1, 0, dx], [0, 1, dy]], dtype=np.float64)
    return cv2.warpAffine(frame, M, (frame.shape[1], frame.shape[0]))


def _uniform_frame(h: int = 480, w: int = 640, value: int = 128) -> np.ndarray:
    """Create a uniform grayscale frame (no corners to detect)."""
    return np.full((h, w), value, dtype=np.uint8)


# ===== Camera Motion Estimation =====


class TestEstimateCameraMotion:
    """Tests for estimate_camera_motion."""

    def test_camera_motion_identical(self):
        """Identical frames → near (0,0) motion."""
        frame = _make_gray_frame()
        cfg = make_config(enable_lk_motion=True)
        dx, dy = estimate_camera_motion(frame, frame.copy(), cfg)
        assert abs(dx) < 1.0
        assert abs(dy) < 1.0

    def test_camera_motion_translation(self):
        """Synthetic translation → (dx,dy) ≈ offset."""
        frame = _make_gray_frame()
        shift_x, shift_y = 10, 5
        shifted = _shift_frame(frame, shift_x, shift_y)
        cfg = make_config(enable_lk_motion=True)
        dx, dy = estimate_camera_motion(frame, shifted, cfg)
        assert dx == pytest.approx(shift_x, abs=3.0)
        assert dy == pytest.approx(shift_y, abs=3.0)

    def test_camera_motion_uniform(self):
        """Uniform frame (<10 corners) → (0,0)."""
        frame = _uniform_frame()
        cfg = make_config(enable_lk_motion=True)
        dx, dy = estimate_camera_motion(frame, frame.copy(), cfg)
        assert dx == 0.0
        assert dy == 0.0

    def test_camera_motion_disabled(self):
        """enable_lk_motion=False → (0,0)."""
        frame = _make_gray_frame()
        cfg = make_config(enable_lk_motion=False)
        dx, dy = estimate_camera_motion(frame, frame.copy(), cfg)
        assert dx == 0.0
        assert dy == 0.0


# ===== Alpha-Beta Prediction =====


class TestPredictTrackAlphaBeta:
    """Tests for predict_track_alpha_beta."""

    def test_predict_zero_velocity_zero_camera(self):
        """Zero velocity + zero camera → prediction == position."""
        track = make_track(position=[100.0, 200.0, 50.0, 30.0], velocity=[0.0, 0.0, 0.0, 0.0])
        result = predict_track_alpha_beta(track, 0.0, 0.0)
        assert result == [100.0, 200.0, 50.0, 30.0]

    def test_predict_camera_additive(self):
        """Camera motion additive to cx/cy only (not w/h)."""
        track = make_track(position=[100.0, 200.0, 50.0, 30.0], velocity=[0.0, 0.0, 0.0, 0.0])
        result = predict_track_alpha_beta(track, 5.0, -3.0)
        assert result[0] == pytest.approx(105.0)  # cx + dx
        assert result[1] == pytest.approx(197.0)  # cy + dy
        assert result[2] == pytest.approx(50.0)  # w unchanged
        assert result[3] == pytest.approx(30.0)  # h unchanged

    def test_predict_positive_dimensions(self):
        """Positive dimensions w,h >= 1.0 enforced."""
        track = make_track(
            position=[100.0, 200.0, 5.0, 3.0],
            velocity=[0.0, 0.0, -10.0, -10.0],  # Shrinks below 0
        )
        result = predict_track_alpha_beta(track, 0.0, 0.0)
        assert result[2] >= 1.0  # w clamped
        assert result[3] >= 1.0  # h clamped

    def test_predict_negative_velocity_clamped(self):
        """Negative velocity on dimensions → clamped to 1.0."""
        track = make_track(
            position=[100.0, 200.0, 2.0, 2.0],
            velocity=[0.0, 0.0, -50.0, -50.0],
        )
        result = predict_track_alpha_beta(track, 0.0, 0.0)
        assert result[2] == 1.0
        assert result[3] == 1.0

    @given(track=st_track(), dx=st.floats(-100, 100), dy=st.floats(-100, 100))
    @settings(max_examples=50)
    def test_predict_dimensions_property(self, track, dx, dy):
        """Property: result[2] >= 1.0 and result[3] >= 1.0 always."""
        result = predict_track_alpha_beta(track, dx, dy)
        assert result[2] >= 1.0
        assert result[3] >= 1.0

    @given(track=st_track(), dx=st.floats(-100, 100), dy=st.floats(-100, 100))
    @settings(max_examples=50)
    def test_predict_camera_additivity_property(self, track, dx, dy):
        """Property: predict(track,dx,dy)[0] == predict(track,0,0)[0] + dx."""
        result_with = predict_track_alpha_beta(track, dx, dy)
        result_without = predict_track_alpha_beta(track, 0.0, 0.0)
        assert result_with[0] == pytest.approx(result_without[0] + dx)
        assert result_with[1] == pytest.approx(result_without[1] + dy)


# ===== Velocity Consistency =====


class TestCheckVelocityConsistency:
    """Tests for check_velocity_consistency."""

    def test_velocity_consistency_low_hits(self):
        """hits < 2 → always True (not enough history)."""
        track = make_track(hits=1, velocity=[0.0, 0.0, 0.0, 0.0])
        cfg = make_config()
        assert check_velocity_consistency(track, [999.0, 999.0, 0.0, 0.0], cfg)

    def test_velocity_consistency_first_check(self):
        """hits == 2 → first real check."""
        track = make_track(hits=2, velocity=[0.0, 0.0, 0.0, 0.0])
        cfg = make_config(max_velocity_change_abs=50.0)
        # Small change should pass
        assert check_velocity_consistency(track, [10.0, 10.0, 0.0, 0.0], cfg)

    def test_velocity_consistency_zero_change(self):
        """Zero velocity change → True."""
        track = make_track(hits=5, velocity=[10.0, 10.0, 0.0, 0.0])
        cfg = make_config()
        assert check_velocity_consistency(track, [10.0, 10.0, 0.0, 0.0], cfg)

    def test_velocity_consistency_stationary(self):
        """Stationary track (speed=0) → threshold = max_velocity_change_abs."""
        track = make_track(hits=5, velocity=[0.0, 0.0, 0.0, 0.0])
        cfg = make_config(max_velocity_change_abs=50.0, max_velocity_change_ratio=1.0)
        # Change of exactly 50 should pass (dv <= max_change)
        assert check_velocity_consistency(track, [50.0, 0.0, 0.0, 0.0], cfg)
        # Change of 51 should fail
        assert not check_velocity_consistency(track, [51.0, 0.0, 0.0, 0.0], cfg)

    def test_velocity_consistency_fast_track(self):
        """Fast track → threshold scales with speed."""
        track = make_track(hits=5, velocity=[100.0, 0.0, 0.0, 0.0])
        cfg = make_config(max_velocity_change_abs=50.0, max_velocity_change_ratio=1.0)
        # Current speed=100, so max_change = max(100*1.0, 50) = 100
        # Change of 90 should pass
        assert check_velocity_consistency(track, [190.0, 0.0, 0.0, 0.0], cfg)

    def test_velocity_consistency_excessive(self):
        """Excessive change → False."""
        track = make_track(hits=5, velocity=[10.0, 10.0, 0.0, 0.0])
        cfg = make_config(max_velocity_change_abs=50.0, max_velocity_change_ratio=1.0)
        # Current speed ≈ 14.14, max_change = max(14.14, 50) = 50
        # Change of ~282 should fail
        assert not check_velocity_consistency(track, [210.0, 210.0, 0.0, 0.0], cfg)

    @given(
        vel=st.lists(st.floats(-50, 50), min_size=4, max_size=4),
        new_vel=st.lists(st.floats(-50, 50), min_size=4, max_size=4),
    )
    @settings(max_examples=50)
    def test_velocity_consistency_property(self, vel, new_vel):
        """Property: hits < 2 → True for any velocity."""
        track = make_track(hits=1, velocity=vel)
        cfg = make_config()
        assert check_velocity_consistency(track, new_vel, cfg)


# ===== Track State Update =====


class TestUpdateTrackState:
    """Tests for update_track_state_alpha_beta."""

    def _setup(self, alpha=0.85, beta=0.05, **track_kw):
        """Helper: create track, detection, predicted position, and config."""
        track_defaults = {
            "position": [200.0, 300.0, 100.0, 80.0],
            "velocity": [5.0, 5.0, 0.0, 0.0],
            "hits": 3,
            "frames_lost": 2,
        }
        track_defaults.update(track_kw)
        track = make_track(**track_defaults)
        det = make_detection(bbox=[190.0, 290.0, 310.0, 390.0], frame_idx=10)
        predicted = [205.0, 305.0, 100.0, 80.0]
        cfg = make_config(
            tracking_alpha=alpha,
            tracking_beta=beta,
            max_velocity_change_abs=50.0,
            max_velocity_change_ratio=1.0,
        )
        return track, det, predicted, cfg

    def test_update_state_true_hits(self):
        """True return → hits incremented by 1."""
        track, det, predicted, cfg = self._setup()
        initial_hits = track.hits
        result = update_track_state_alpha_beta(track, det, predicted, cfg, 10)
        assert result is True
        assert track.hits == initial_hits + 1

    def test_update_state_true_frames_lost(self):
        """True return → frames_lost reset to 0."""
        track, det, predicted, cfg = self._setup(frames_lost=3)
        result = update_track_state_alpha_beta(track, det, predicted, cfg, 10)
        assert result is True
        assert track.frames_lost == 0

    def test_update_state_true_last_seen(self):
        """True return → last_seen_frame set to frame_idx."""
        track, det, predicted, cfg = self._setup()
        result = update_track_state_alpha_beta(track, det, predicted, cfg, 42)
        assert result is True
        assert track.last_seen_frame == 42

    def test_update_state_alpha_blending(self):
        """Alpha blending: new_pos ≈ (1-α)*predicted + α*measured."""
        track, det, predicted, cfg = self._setup(alpha=0.85)
        measured = bbox_to_position(det.bbox)
        update_track_state_alpha_beta(track, det, predicted, cfg, 10)
        for i in range(4):
            expected = predicted[i] + 0.85 * (measured[i] - predicted[i])
            assert track.position[i] == pytest.approx(expected, abs=0.01)

    def test_update_state_beta_blending(self):
        """Beta blending: new_vel ≈ old_vel + β*residual."""
        track, det, predicted, cfg = self._setup(beta=0.05)
        old_vel = track.velocity.copy()
        measured = bbox_to_position(det.bbox)
        residual = [measured[i] - predicted[i] for i in range(4)]
        update_track_state_alpha_beta(track, det, predicted, cfg, 10)
        for i in range(4):
            expected = old_vel[i] + 0.05 * residual[i]
            assert track.velocity[i] == pytest.approx(expected, abs=0.01)

    def test_update_state_false_no_mutation(self):
        """False → track COMPLETELY unchanged."""
        # Create a scenario where velocity consistency fails
        track = make_track(
            position=[200.0, 300.0, 100.0, 80.0],
            velocity=[5.0, 5.0, 0.0, 0.0],
            hits=5,
            frames_lost=2,
            age=10,
        )
        # Detection far away → large residual → large velocity change
        det = make_detection(bbox=[900.0, 900.0, 1100.0, 1000.0], frame_idx=10)
        predicted = [205.0, 305.0, 100.0, 80.0]
        cfg = make_config(
            tracking_alpha=0.85,
            tracking_beta=0.5,  # High beta → large velocity update
            max_velocity_change_abs=5.0,  # Very tight threshold
            max_velocity_change_ratio=0.1,
        )
        snapshot = copy.deepcopy(track)
        result = update_track_state_alpha_beta(track, det, predicted, cfg, 10)
        assert result is False
        assert track.position == snapshot.position
        assert track.velocity == snapshot.velocity
        assert track.hits == snapshot.hits
        assert track.frames_lost == snapshot.frames_lost

    def test_update_state_velocity_inconsistency(self):
        """Velocity inconsistency → no mutation + False."""
        track = make_track(
            position=[200.0, 300.0, 100.0, 80.0],
            velocity=[5.0, 5.0, 0.0, 0.0],
            hits=5,
        )
        # Large residual with high beta forces huge velocity change
        det = make_detection(bbox=[800.0, 800.0, 1000.0, 900.0], frame_idx=10)
        predicted = [205.0, 305.0, 100.0, 80.0]
        cfg = make_config(
            tracking_beta=1.0,
            max_velocity_change_abs=10.0,
            max_velocity_change_ratio=0.1,
        )
        result = update_track_state_alpha_beta(track, det, predicted, cfg, 10)
        assert result is False

    def test_update_state_alpha_zero(self):
        """alpha=0 → position = prediction (measurement ignored)."""
        track, det, predicted, cfg = self._setup(alpha=0.0)
        update_track_state_alpha_beta(track, det, predicted, cfg, 10)
        for i in range(4):
            assert track.position[i] == pytest.approx(predicted[i])

    def test_update_state_alpha_one(self):
        """alpha=1 → position = measurement."""
        track, det, predicted, cfg = self._setup(alpha=1.0)
        measured = bbox_to_position(det.bbox)
        update_track_state_alpha_beta(track, det, predicted, cfg, 10)
        for i in range(4):
            assert track.position[i] == pytest.approx(measured[i])


# ===== Track Initialization =====


class TestInitializeNewTrack:
    """Tests for initialize_new_track."""

    def test_init_track_uuid(self):
        """Track ID is valid UUID4 format."""
        det = make_detection()
        track = initialize_new_track(det, frame_idx=0)
        parsed = uuid.UUID(track.track_id, version=4)
        assert str(parsed) == track.track_id

    def test_init_track_distinct(self):
        """N calls → N distinct track_ids."""
        det = make_detection()
        ids = {initialize_new_track(det, frame_idx=0).track_id for _ in range(20)}
        assert len(ids) == 20

    def test_init_track_position(self):
        """Position from detection bbox via bbox_to_position."""
        det = make_detection(bbox=[100.0, 200.0, 300.0, 400.0])
        track = initialize_new_track(det, frame_idx=0)
        expected = bbox_to_position([100.0, 200.0, 300.0, 400.0])
        assert track.position == expected

    def test_init_track_defaults(self):
        """Default values: velocity=[0,0,0,0], age=1, hits=1, frames_lost=0."""
        det = make_detection()
        track = initialize_new_track(det, frame_idx=5)
        assert track.velocity == [0.0, 0.0, 0.0, 0.0]
        assert track.age == 1
        assert track.hits == 1
        assert track.frames_lost == 0
        assert track.last_seen_frame == 5

    def test_init_track_keypoints(self):
        """Keypoints copied from detection."""
        kps = [(10.0, 20.0), (30.0, 40.0)]
        scores = [0.9, 0.8]
        det = make_detection(keypoints=kps, keypoint_scores=scores)
        track = initialize_new_track(det, frame_idx=0)
        assert track.keypoints == kps
        assert track.keypoint_scores == scores


# ===== Cleanup Lost Tracks =====


class TestCleanupLostTracks:
    """Tests for cleanup_lost_tracks."""

    def test_cleanup_below_max(self):
        """frames_lost < max → retained."""
        cfg = make_config(max_frames_lost=5)
        tracks = [make_track(frames_lost=3)]
        result = cleanup_lost_tracks(tracks, cfg)
        assert len(result) == 1

    def test_cleanup_at_max(self):
        """frames_lost == max → removed (strict <)."""
        cfg = make_config(max_frames_lost=5)
        tracks = [make_track(frames_lost=5)]
        result = cleanup_lost_tracks(tracks, cfg)
        assert len(result) == 0

    def test_cleanup_one_below_max(self):
        """frames_lost == max-1 → retained."""
        cfg = make_config(max_frames_lost=5)
        tracks = [make_track(frames_lost=4)]
        result = cleanup_lost_tracks(tracks, cfg)
        assert len(result) == 1

    def test_cleanup_empty(self):
        """Empty input → empty output."""
        cfg = make_config(max_frames_lost=5)
        result = cleanup_lost_tracks([], cfg)
        assert result == []

    def test_cleanup_order(self):
        """Preserves order of retained tracks."""
        cfg = make_config(max_frames_lost=5)
        t1 = make_track(frames_lost=0)
        t2 = make_track(frames_lost=5)  # removed
        t3 = make_track(frames_lost=2)
        result = cleanup_lost_tracks([t1, t2, t3], cfg)
        assert len(result) == 2
        assert result[0] is t1
        assert result[1] is t3


# ===== PlateTracker Integration =====


class TestPlateTrackerIntegration:
    """Tests for PlateTracker class."""

    def test_tracker_init_state(self):
        """Init state: empty tracks, prev_frame=None, idx=-1, lk_coasting=False."""
        cfg = make_config(enable_lk_motion=False)
        tracker = PlateTracker(cfg)
        assert tracker.tracks == []
        assert tracker.prev_frame is None
        assert tracker.prev_frame_idx == -1
        assert tracker.lk_coasting is False

    def test_tracker_first_frame(self):
        """First frame with detections → new tracks."""
        cfg = make_config(enable_lk_motion=False)
        tracker = PlateTracker(cfg)
        dets = [
            make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=0),
            make_detection(bbox=[300.0, 300.0, 400.0, 400.0], frame_idx=0),
        ]
        frame = _uniform_frame()
        output = tracker.update(dets, frame, 0)
        assert len(tracker.tracks) == 2
        assert output.num_tracks_created == 2

    def test_tracker_repeated_detection(self):
        """Same detection → track matched, hits increase."""
        cfg = make_config(enable_lk_motion=False, min_hits=3)
        tracker = PlateTracker(cfg)
        det = [make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=0)]
        frame = _uniform_frame()

        # Frame 0: create track
        tracker.update(det, frame, 0)
        assert tracker.tracks[0].hits == 1

        # Frame 1: match → hits increases
        det1 = [make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=1)]
        tracker.update(det1, frame, 1)
        assert tracker.tracks[0].hits == 2

    def test_tracker_empty_detections(self):
        """Empty detections → existing tracks frames_lost++."""
        cfg = make_config(enable_lk_motion=False, max_frames_lost=10)
        tracker = PlateTracker(cfg)
        det = [make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=0)]
        frame = _uniform_frame()
        tracker.update(det, frame, 0)
        assert tracker.tracks[0].frames_lost == 0

        # Empty detections → frames_lost increases
        tracker.update([], frame, 1)
        assert tracker.tracks[0].frames_lost == 1

    def test_tracker_confirmation(self):
        """hits >= min_hits → in confirmed_tracks."""
        cfg = make_config(enable_lk_motion=False, min_hits=2)
        tracker = PlateTracker(cfg)
        frame = _uniform_frame()

        # Frame 0: hits=1, not confirmed
        output0 = tracker.update(
            [make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=0)], frame, 0
        )
        assert len(output0.confirmed_tracks) == 0

        # Frame 1: hits=2, confirmed
        output1 = tracker.update(
            [make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=1)], frame, 1
        )
        assert len(output1.confirmed_tracks) == 1

    def test_tracker_death(self):
        """frames_lost >= max → removed, ID in lost_track_ids."""
        cfg = make_config(enable_lk_motion=False, max_frames_lost=2)
        tracker = PlateTracker(cfg)
        det = [make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=0)]
        frame = _uniform_frame()

        tracker.update(det, frame, 0)
        track_id = tracker.tracks[0].track_id

        # 2 frames with no detections → frames_lost reaches max
        tracker.update([], frame, 1)
        output = tracker.update([], frame, 2)

        assert track_id in output.lost_track_ids
        assert all(t.track_id != track_id for t in tracker.tracks)

    def test_tracker_velocity_rejection(self):
        """Velocity inconsistency → match rejected, detection freed for new track."""
        cfg = make_config(
            enable_lk_motion=False,
            tracking_beta=1.0,
            max_velocity_change_abs=1.0,
            max_velocity_change_ratio=0.01,
            max_frames_lost=100,
        )
        tracker = PlateTracker(cfg)
        frame = _uniform_frame()

        # Frame 0: create track
        det0 = [make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=0)]
        tracker.update(det0, frame, 0)

        # Match once more to get hits=2 (velocity check starts at hits>=2)
        det1 = [make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=1)]
        tracker.update(det1, frame, 1)

        # Frame 2: detection far away → velocity inconsistency
        det2 = [make_detection(bbox=[900.0, 900.0, 1100.0, 1000.0], frame_idx=2)]
        tracker.update(det2, frame, 2)

        # Should have 2 tracks: original (unmatched) + new from freed detection
        # The original track was unmatched (velocity rejection), new track created
        assert len(tracker.tracks) >= 2

    def test_tracker_age_all_tracks(self):
        """Age incremented for ALL tracks (matched + unmatched) — ambiguity #22."""
        cfg = make_config(enable_lk_motion=False, max_frames_lost=10)
        tracker = PlateTracker(cfg)
        frame = _uniform_frame()

        # Create 2 tracks
        dets = [
            make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=0),
            make_detection(bbox=[500.0, 500.0, 600.0, 600.0], frame_idx=0),
        ]
        tracker.update(dets, frame, 0)
        ages_after_0 = [t.age for t in tracker.tracks]

        # Frame 1: only match first track
        dets1 = [make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=1)]
        tracker.update(dets1, frame, 1)

        # Both tracks should have age incremented
        for i, t in enumerate(tracker.tracks):
            assert t.age == ages_after_0[i] + 1

    def test_tracker_lk_coast(self):
        """Frame skip > threshold → coast mode."""
        cfg = make_config(
            enable_lk_motion=True,
            lk_max_frame_skip=2,
            lk_reenable_delay_frames=5,
        )
        tracker = PlateTracker(cfg)
        frame = _make_gray_frame()

        # Frame 0: init reference
        tracker.update([], frame, 0)
        assert tracker.lk_coasting is False

        # Frame 10: large skip → enter coast mode
        tracker.update([], frame, 10)
        assert tracker.lk_coasting is True

    def test_tracker_lk_reenable(self):
        """Delta back to normal → coast disabled — ambiguity #23."""
        cfg = make_config(
            enable_lk_motion=True,
            lk_max_frame_skip=2,
            lk_reenable_delay_frames=1,
        )
        tracker = PlateTracker(cfg)
        frame = _make_gray_frame()

        # Frame 0: init
        tracker.update([], frame, 0)

        # Frame 10: enter coast mode
        tracker.update([], frame, 10)
        assert tracker.lk_coasting is True

        # Frame 11: coast period over (delay=1) and delta=1 ≤ max_skip=2
        tracker.update([], frame, 11)
        assert tracker.lk_coasting is False

    def test_tracker_lost_count(self):
        """num_tracks_lost == len(lost_track_ids)."""
        cfg = make_config(enable_lk_motion=False, max_frames_lost=1)
        tracker = PlateTracker(cfg)
        frame = _uniform_frame()

        # Create tracks then lose them
        dets = [
            make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=0),
            make_detection(bbox=[500.0, 500.0, 600.0, 600.0], frame_idx=0),
        ]
        tracker.update(dets, frame, 0)

        # Frame 1: no detections → frames_lost=1 → cleaned up (>= max_frames_lost=1)
        output = tracker.update([], frame, 1)
        assert output.num_tracks_lost == len(output.lost_track_ids)

    def test_tracker_confirmed_subset(self):
        """confirmed_tracks ⊆ tracks."""
        cfg = make_config(enable_lk_motion=False, min_hits=2)
        tracker = PlateTracker(cfg)
        frame = _uniform_frame()

        dets = [
            make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=0),
            make_detection(bbox=[500.0, 500.0, 600.0, 600.0], frame_idx=0),
        ]
        tracker.update(dets, frame, 0)

        # Frame 1: match only first
        dets1 = [make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=1)]
        output = tracker.update(dets1, frame, 1)

        confirmed_ids = {t.track_id for t in output.confirmed_tracks}
        all_ids = {t.track_id for t in output.tracks}
        assert confirmed_ids.issubset(all_ids)

    def test_tracker_conservation_property(self):
        """Conservation: len(before) - lost + created == len(after)."""
        cfg = make_config(enable_lk_motion=False, max_frames_lost=3)
        tracker = PlateTracker(cfg)
        frame = _uniform_frame()

        # Seed with some tracks
        dets = [
            make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=0),
            make_detection(bbox=[500.0, 500.0, 600.0, 600.0], frame_idx=0),
        ]
        tracker.update(dets, frame, 0)

        # Run several updates and check conservation each time
        for i in range(1, 6):
            before_count = len(tracker.tracks)
            # Alternate: detections on even, empty on odd
            if i % 2 == 0:
                d = [make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=i)]
            else:
                d = []
            output = tracker.update(d, frame, i)
            after_count = len(tracker.tracks)
            assert after_count == before_count - output.num_tracks_lost + output.num_tracks_created

    def test_tracker_age_monotonicity_property(self):
        """Track.age never decreases across calls."""
        cfg = make_config(enable_lk_motion=False, max_frames_lost=20)
        tracker = PlateTracker(cfg)
        frame = _uniform_frame()

        det = [make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=0)]
        tracker.update(det, frame, 0)
        track_id = tracker.tracks[0].track_id

        prev_age = tracker.tracks[0].age
        for i in range(1, 10):
            # Alternate matched/unmatched
            if i % 2 == 0:
                d = [make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=i)]
            else:
                d = []
            tracker.update(d, frame, i)
            for t in tracker.tracks:
                if t.track_id == track_id:
                    assert t.age >= prev_age
                    prev_age = t.age

    def test_track_detections_wrapper(self):
        """track_detections wrapper produces identical result to tracker.update."""
        cfg = make_config(enable_lk_motion=False)
        tracker1 = PlateTracker(cfg)
        tracker2 = PlateTracker(cfg)
        frame = _uniform_frame()
        dets = [make_detection(bbox=[100.0, 100.0, 200.0, 200.0], frame_idx=0)]

        output1 = tracker1.update(dets, frame, 0)
        output2 = track_detections(dets, frame, 0, tracker2)

        assert output1.num_tracks_created == output2.num_tracks_created
        assert len(output1.tracks) == len(output2.tracks)
        assert output1.camera_motion == output2.camera_motion
