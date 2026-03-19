"""Tests for corner CNN predictor module.

chunk-01: config fields, CornerCnnOutput type, letterbox preprocessing,
corner extraction from heatmaps, denormalization, and confidence gating.
chunk-02: predict_corners() entrypoint, FrameResult/PipelineStats fields,
graceful degradation on failure, keypoint/score population.
chunk-04: hardening — image integrity, failure modes, disabled flow,
zero-crop edge case, letterbox round-trip accuracy, end-to-end keypoint
population, keypoint ordering round-trip.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import numpy as np
import pytest
from pytest import approx

from producer.config import ProducerConfig
from producer.models import FrameResult, PipelineStats, RoiImage
from producer.ops_corner_cnn import CornerCnnOutput, CornerCnnPredictor, predict_corners

# ─── Fixtures & Helpers ──────────────────────────────────────────────


@pytest.fixture
def cfg() -> ProducerConfig:
    """Default config with corner CNN enabled."""
    return ProducerConfig(
        corner_cnn_enabled=True,
        corner_cnn_model_path="models/corner_cnn.engine",
        corner_cnn_input_h=80,
        corner_cnn_input_w=256,
        corner_cnn_confidence_threshold=0.3,
        corner_cnn_device="cuda",
    )


@pytest.fixture
def predictor(cfg: ProducerConfig) -> CornerCnnPredictor:
    """CornerCnnPredictor with mocked TRT engine (no real GPU needed)."""
    with (
        patch.object(CornerCnnPredictor, "_load_model", return_value=None),
        patch.object(CornerCnnPredictor, "_warmup"),
    ):
        p = CornerCnnPredictor(cfg)
    return p


def make_roi_image(
    crop_h: int = 40,
    crop_w: int = 200,
    track_id: str = "test-track-001",
    frame_idx: int = 0,
) -> RoiImage:
    """Create a minimal RoiImage for testing predict_corners."""
    crop = np.random.randint(0, 255, (crop_h, crop_w, 3), dtype=np.uint8)
    return RoiImage(
        track_id=track_id,
        crop_img=crop,
        bbox=[100.0, 200.0, 300.0, 240.0],
        padded_bbox=[90.0, 190.0, 310.0, 250.0],
        frame_idx=frame_idx,
        confidence=0.85,
        frame_width=1920,
        frame_height=1080,
    )


def make_synthetic_output(
    peaks: list[tuple[int, int]],
    confs: list[float],
    offsets: list[tuple[float, float]] | None = None,
    shape: tuple[int, int, int, int] = (1, 12, 20, 64),
) -> np.ndarray:
    """Create synthetic CNN output tensor with specified heatmap peaks.

    Args:
        peaks: List of 4 (gy, gx) grid positions for TL, TR, BR, BL.
        confs: List of 4 confidence values (heatmap peak values).
        offsets: Optional list of 4 (dx, dy) substride offsets.
        shape: Output tensor shape, default (1, 12, 20, 64).
    """
    output = np.zeros(shape, dtype=np.float32)
    for i, ((gy, gx), conf) in enumerate(zip(peaks, confs, strict=True)):
        output[0, i, gy, gx] = conf
        if offsets is not None and i < len(offsets):
            dx, dy = offsets[i]
            output[0, 4 + 2 * i, gy, gx] = dx
            output[0, 4 + 2 * i + 1, gy, gx] = dy
    return output


# ─── Config Tests ────────────────────────────────────────────────────


class TestConfigCornerCnn:
    """Tests for corner CNN config fields on ProducerConfig."""

    def test_config_has_all_corner_cnn_fields(self) -> None:
        """All 6 config fields exist with correct defaults."""
        # Arrange / Act
        cfg = ProducerConfig()

        # Assert
        assert cfg.corner_cnn_enabled is False
        assert cfg.corner_cnn_model_path == "models/corner_cnn.engine"
        assert cfg.corner_cnn_input_h == 80
        assert cfg.corner_cnn_input_w == 256
        assert cfg.corner_cnn_confidence_threshold == 0.3
        assert cfg.corner_cnn_device == "cuda"


# ─── Type Tests ──────────────────────────────────────────────────────


class TestCornerCnnOutput:
    """Tests for CornerCnnOutput dataclass."""

    def test_corner_cnn_output_importable(self) -> None:
        """CornerCnnOutput importable with corners and corner_confidences attrs."""
        # Arrange / Act
        out = CornerCnnOutput(
            corners=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
            corner_confidences=[0.9, 0.9, 0.9, 0.9],
        )

        # Assert
        assert out.corners is not None
        assert len(out.corners) == 4
        assert out.corner_confidences is not None
        assert len(out.corner_confidences) == 4


# ─── Letterbox Tests ─────────────────────────────────────────────────


class TestLetterbox:
    """Tests for _letterbox_crop preprocessing."""

    def test_letterbox_wide_crop(self, predictor: CornerCnnPredictor) -> None:
        """Wide crop (40,200,3) → shape==(80,256,3), correct scale and padding."""
        # Arrange
        crop = np.zeros((40, 200, 3), dtype=np.uint8)

        # Act
        letterboxed, scale, pad_x, pad_y = predictor._letterbox_crop(crop)

        # Assert
        assert letterboxed.shape == (80, 256, 3)
        assert scale == approx(1.28, abs=1e-6)
        assert pad_x == approx(0.0, abs=0.5)
        assert pad_y == approx(14.5, abs=0.5)

    def test_letterbox_square_crop(self, predictor: CornerCnnPredictor) -> None:
        """Square crop (100,100,3) → shape==(80,256,3), correct scale and padding."""
        # Arrange
        crop = np.zeros((100, 100, 3), dtype=np.uint8)

        # Act
        letterboxed, scale, pad_x, pad_y = predictor._letterbox_crop(crop)

        # Assert
        assert letterboxed.shape == (80, 256, 3)
        assert scale == approx(0.8, abs=1e-6)
        assert pad_x == approx(88.0, abs=0.5)
        assert pad_y == approx(0.0, abs=0.5)

    def test_letterbox_tall_crop(self, predictor: CornerCnnPredictor) -> None:
        """Tall crop (120,40,3) → shape==(80,256,3), correct scale and padding."""
        # Arrange
        crop = np.zeros((120, 40, 3), dtype=np.uint8)

        # Act
        letterboxed, scale, pad_x, pad_y = predictor._letterbox_crop(crop)

        # Assert
        assert letterboxed.shape == (80, 256, 3)
        assert scale == approx(0.667, abs=0.01)
        assert pad_x == approx(115.0, abs=1.0)
        assert pad_y == approx(0.0, abs=0.5)

    def test_letterbox_output_shape_always_target(self, predictor: CornerCnnPredictor) -> None:
        """5 crops of varying sizes all produce output shape (80, 256, 3)."""
        # Arrange
        sizes = [(40, 200), (100, 100), (120, 40), (30, 300), (80, 256)]

        for h, w in sizes:
            crop = np.zeros((h, w, 3), dtype=np.uint8)

            # Act
            letterboxed, _, _, _ = predictor._letterbox_crop(crop)

            # Assert
            assert letterboxed.shape == (80, 256, 3), f"Failed for input ({h}, {w})"

    def test_letterbox_preserves_aspect_ratio(self, predictor: CornerCnnPredictor) -> None:
        """Content region aspect ratio new_w/new_h == approx(W/H, rel=0.02)."""
        # Arrange
        crop = np.zeros((40, 200, 3), dtype=np.uint8)
        original_ratio = 200 / 40  # 5.0

        # Act
        _, scale, _, _ = predictor._letterbox_crop(crop)
        new_w = round(200 * scale)
        new_h = round(40 * scale)

        # Assert
        assert new_w / new_h == approx(original_ratio, rel=0.02)

    def test_letterbox_padding_is_gray(self, predictor: CornerCnnPredictor) -> None:
        """Padding region pixels == (114, 114, 114) when crop is filled with 255."""
        # Arrange — square crop guarantees horizontal padding
        crop = np.full((100, 100, 3), 255, dtype=np.uint8)

        # Act
        letterboxed, _, pad_x, _ = predictor._letterbox_crop(crop)

        # Assert — first and last columns are in the padding region
        assert pad_x > 1  # sanity: there IS horizontal padding
        np.testing.assert_array_equal(letterboxed[0, 0], [114, 114, 114])
        np.testing.assert_array_equal(letterboxed[0, -1], [114, 114, 114])


# ─── Corner Extraction Tests ────────────────────────────────────────


class TestExtractCorners:
    """Tests for _extract_corners_from_output heatmap postprocessing."""

    def test_extract_corners_golden(self, predictor: CornerCnnPredictor) -> None:
        """Synthetic tensor with known peaks → exact coordinates and confidences."""
        # Arrange
        peaks = [(2, 5), (3, 58), (17, 60), (18, 3)]
        confs = [0.95, 0.88, 0.91, 0.87]
        offsets = [(0.3, -0.1), (-0.2, 0.15), (0.4, 0.25), (-0.15, 0.35)]
        raw_output = make_synthetic_output(peaks, confs, offsets)

        # Act
        corners, confidences = predictor._extract_corners_from_output(raw_output)

        # Assert — TL
        assert corners[0] == approx((20.3, 7.9), abs=1e-4)
        # TR
        assert corners[1] == approx((231.8, 12.15), abs=1e-4)
        # BR
        assert corners[2] == approx((240.4, 68.25), abs=1e-4)
        # BL
        assert corners[3] == approx((11.85, 72.35), abs=1e-4)
        assert confidences == approx([0.95, 0.88, 0.91, 0.87], abs=1e-4)

    def test_extract_corners_zero_offset(self, predictor: CornerCnnPredictor) -> None:
        """TL peak at (gy=10,gx=32) zero offset → corner == (128.0, 40.0) exactly."""
        # Arrange
        peaks = [(10, 32), (10, 32), (10, 32), (10, 32)]
        confs = [0.9, 0.9, 0.9, 0.9]
        raw_output = make_synthetic_output(peaks, confs)

        # Act
        corners, _ = predictor._extract_corners_from_output(raw_output)

        # Assert — stride is 4: x = 32*4 = 128, y = 10*4 = 40
        assert corners[0] == (128.0, 40.0)

    def test_extract_corner_order_tl_tr_br_bl(self, predictor: CornerCnnPredictor) -> None:
        """corners[0] is TL, [1] TR, [2] BR, [3] BL matching CNN Interface Contract."""
        # Arrange — place peaks in distinct quadrants
        peaks = [
            (2, 5),  # TL: top-left of heatmap
            (2, 58),  # TR: top-right
            (17, 58),  # BR: bottom-right
            (17, 5),  # BL: bottom-left
        ]
        confs = [0.9, 0.9, 0.9, 0.9]
        raw_output = make_synthetic_output(peaks, confs)

        # Act
        corners, _ = predictor._extract_corners_from_output(raw_output)

        # Assert — TL should be top-left quadrant (small x, small y)
        assert corners[0][0] < corners[1][0]  # TL.x < TR.x
        assert corners[0][1] < corners[3][1]  # TL.y < BL.y
        assert corners[1][0] > corners[3][0]  # TR.x > BL.x
        assert corners[2][1] > corners[0][1]  # BR.y > TL.y

    def test_extract_confidences_match_heatmap_peaks(self, predictor: CornerCnnPredictor) -> None:
        """4 peaks with values [0.95, 0.70, 0.85, 0.60] → confidences == same."""
        # Arrange
        peaks = [(5, 10), (5, 50), (15, 50), (15, 10)]
        confs = [0.95, 0.70, 0.85, 0.60]
        raw_output = make_synthetic_output(peaks, confs)

        # Act
        _, confidences = predictor._extract_corners_from_output(raw_output)

        # Assert
        assert confidences == approx(confs, abs=1e-6)


# ─── Denormalization Tests ───────────────────────────────────────────


class TestDenormalize:
    """Tests for _denormalize_corners inverse letterbox transform."""

    def test_denormalize_golden_wide_crop(self, predictor: CornerCnnPredictor) -> None:
        """Known letterbox params for wide crop → correct crop-pixel coordinates."""
        # Arrange
        corners_lb = [
            (10.0, 16.0),
            (240.0, 17.0),
            (238.0, 60.0),
            (12.0, 58.0),
        ]
        scale = 1.28
        pad_x, pad_y = 0.0, 14.5
        crop_h, crop_w = 40, 200

        # Act
        corners_crop = predictor._denormalize_corners(
            corners_lb, scale, pad_x, pad_y, crop_h, crop_w
        )

        # Assert
        assert corners_crop[0] == approx((7.81, 1.17), abs=0.1)  # TL
        assert corners_crop[1] == approx((187.5, 1.95), abs=0.1)  # TR
        assert corners_crop[2] == approx((185.94, 35.55), abs=0.1)  # BR
        assert corners_crop[3] == approx((9.38, 33.98), abs=0.1)  # BL

    def test_denormalize_clips_to_crop_bounds(self, predictor: CornerCnnPredictor) -> None:
        """Corners with negative letterbox coords → clipped to 0; overflow → clipped."""
        # Arrange — corners that would map outside crop bounds
        corners_lb = [
            (-5.0, -3.0),  # Would be negative after denorm
            (300.0, -3.0),  # x overflow
            (300.0, 100.0),  # Both overflow
            (-5.0, 100.0),  # x negative, y overflow
        ]
        scale = 1.0
        pad_x, pad_y = 0.0, 0.0
        crop_h, crop_w = 40, 200

        # Act
        corners_crop = predictor._denormalize_corners(
            corners_lb, scale, pad_x, pad_y, crop_h, crop_w
        )

        # Assert — all clipped to valid crop pixel range
        for x, y in corners_crop:
            assert 0.0 <= x <= crop_w - 1
            assert 0.0 <= y <= crop_h - 1

    def test_denormalize_identity_no_pad_no_scale(self, predictor: CornerCnnPredictor) -> None:
        """scale=1.0, pad=(0,0) → corners_crop == corners_lb (identity transform)."""
        # Arrange
        corners_lb = [(10.0, 5.0), (50.0, 5.0), (50.0, 30.0), (10.0, 30.0)]
        scale = 1.0
        pad_x, pad_y = 0.0, 0.0
        crop_h, crop_w = 80, 256

        # Act
        corners_crop = predictor._denormalize_corners(
            corners_lb, scale, pad_x, pad_y, crop_h, crop_w
        )

        # Assert
        assert corners_crop == approx(corners_lb, abs=1e-6)


# ─── Confidence Gate Tests ───────────────────────────────────────────


class TestConfidenceGate:
    """Tests for predict() confidence gating behavior."""

    def test_confidence_all_above_returns_corners(self, predictor: CornerCnnPredictor) -> None:
        """Mock TRT all 4 confs=0.8 (> 0.3 threshold) → result.corners is not None."""
        # Arrange
        crop = np.random.randint(0, 255, (50, 150, 3), dtype=np.uint8)
        peaks = [(2, 5), (2, 58), (17, 58), (17, 5)]
        confs = [0.8, 0.8, 0.8, 0.8]
        synthetic = make_synthetic_output(peaks, confs)

        # Act
        with patch.object(predictor, "_infer", return_value=synthetic):
            results = predictor.predict([crop])

        # Assert
        assert len(results) == 1
        assert results[0].corners is not None
        assert len(results[0].corners) == 4

    def test_confidence_one_below_returns_none(self, predictor: CornerCnnPredictor) -> None:
        """Mock TRT confs [0.8,0.8,0.8,0.2] (min < 0.3) → result.corners is None."""
        # Arrange
        crop = np.random.randint(0, 255, (50, 150, 3), dtype=np.uint8)
        peaks = [(2, 5), (2, 58), (17, 58), (17, 5)]
        confs = [0.8, 0.8, 0.8, 0.2]
        synthetic = make_synthetic_output(peaks, confs)

        # Act
        with patch.object(predictor, "_infer", return_value=synthetic):
            results = predictor.predict([crop])

        # Assert
        assert len(results) == 1
        assert results[0].corners is None

    def test_confidence_at_threshold_returns_corners(self, predictor: CornerCnnPredictor) -> None:
        """Mock TRT all 4 confs=0.3 (== threshold, not strictly less) → not None."""
        # Arrange
        crop = np.random.randint(0, 255, (50, 150, 3), dtype=np.uint8)
        peaks = [(2, 5), (2, 58), (17, 58), (17, 5)]
        confs = [0.3, 0.3, 0.3, 0.3]
        synthetic = make_synthetic_output(peaks, confs)

        # Act
        with patch.object(predictor, "_infer", return_value=synthetic):
            results = predictor.predict([crop])

        # Assert
        assert len(results) == 1
        assert results[0].corners is not None


# ─── Chunk-02: predict_corners() Entrypoint Tests ───────────────────


class TestPredictCorners:
    """Tests for predict_corners() public entrypoint with graceful degradation."""

    def test_predict_corners_writes_keypoints(
        self, predictor: CornerCnnPredictor, cfg: ProducerConfig
    ) -> None:
        """1 RoiImage + mock predictor returning valid corners → roi.keypoints set."""
        # Arrange
        roi = make_roi_image()
        corners = [(5.0, 5.0), (195.0, 5.0), (195.0, 35.0), (5.0, 35.0)]
        confs = [0.9, 0.9, 0.9, 0.9]
        mock_output = CornerCnnOutput(corners=corners, corner_confidences=confs)

        # Act
        with patch.object(predictor, "predict", return_value=[mock_output]):
            result = predict_corners([roi], predictor, cfg)

        # Assert
        assert len(result) == 1
        assert result[0].keypoints == corners

    def test_predict_corners_writes_scores(
        self, predictor: CornerCnnPredictor, cfg: ProducerConfig
    ) -> None:
        """Mock predictor returning confs → roi.keypoint_scores set."""
        # Arrange
        roi = make_roi_image()
        corners = [(5.0, 5.0), (195.0, 5.0), (195.0, 35.0), (5.0, 35.0)]
        confs = [0.9, 0.9, 0.9, 0.9]
        mock_output = CornerCnnOutput(corners=corners, corner_confidences=confs)

        # Act
        with patch.object(predictor, "predict", return_value=[mock_output]):
            result = predict_corners([roi], predictor, cfg)

        # Assert
        assert result[0].keypoint_scores == [0.9, 0.9, 0.9, 0.9]

    def test_predict_corners_none_on_low_confidence(
        self, predictor: CornerCnnPredictor, cfg: ProducerConfig
    ) -> None:
        """Mock predictor returning None corners → keypoints stays None."""
        # Arrange
        roi = make_roi_image()
        mock_output = CornerCnnOutput(corners=None, corner_confidences=None)

        # Act
        with patch.object(predictor, "predict", return_value=[mock_output]):
            result = predict_corners([roi], predictor, cfg)

        # Assert
        assert result[0].keypoints is None
        assert result[0].keypoint_scores is None

    def test_predict_corners_empty_rois(
        self, predictor: CornerCnnPredictor, cfg: ProducerConfig
    ) -> None:
        """predict_corners([], predictor, cfg) → returns empty list."""
        # Arrange / Act
        result = predict_corners([], predictor, cfg)

        # Assert
        assert result == []

    def test_predict_corners_partial_success(
        self, predictor: CornerCnnPredictor, cfg: ProducerConfig
    ) -> None:
        """3 ROIs, mock returns [valid, None, valid] → mixed keypoints."""
        # Arrange
        rois = [make_roi_image(track_id=f"track-{i}") for i in range(3)]
        corners_valid = [(5.0, 5.0), (195.0, 5.0), (195.0, 35.0), (5.0, 35.0)]
        confs_valid = [0.9, 0.9, 0.9, 0.9]
        mock_outputs = [
            CornerCnnOutput(corners=corners_valid, corner_confidences=confs_valid),
            CornerCnnOutput(corners=None, corner_confidences=None),
            CornerCnnOutput(corners=corners_valid, corner_confidences=confs_valid),
        ]

        # Act
        with patch.object(predictor, "predict", return_value=mock_outputs):
            result = predict_corners(rois, predictor, cfg)

        # Assert
        assert result[0].keypoints is not None
        assert result[1].keypoints is None
        assert result[2].keypoints is not None

    def test_predict_corners_failure_returns_unchanged(
        self, predictor: CornerCnnPredictor, cfg: ProducerConfig
    ) -> None:
        """Mock predictor raises RuntimeError → no exception, all keypoints None."""
        # Arrange
        rois = [make_roi_image()]

        # Act
        with patch.object(predictor, "predict", side_effect=RuntimeError("CUDA OOM")):
            result = predict_corners(rois, predictor, cfg)

        # Assert — no exception propagated, rois returned with keypoints unchanged
        assert len(result) == 1
        assert result[0].keypoints is None

    def test_predict_corners_failure_logs_warning(
        self,
        predictor: CornerCnnPredictor,
        cfg: ProducerConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Mock predictor raises RuntimeError('CUDA OOM') → WARNING logged."""
        # Arrange
        rois = [make_roi_image()]

        # Act
        with caplog.at_level(logging.WARNING):
            with patch.object(predictor, "predict", side_effect=RuntimeError("CUDA OOM")):
                predict_corners(rois, predictor, cfg)

        # Assert
        assert any("CUDA OOM" in r.message for r in caplog.records)
        assert any(r.levelname == "WARNING" for r in caplog.records)


# ─── Chunk-02: Model Field Tests ────────────────────────────────────


class TestModelFields:
    """Tests for FrameResult and PipelineStats new fields (chunk-02)."""

    def test_frame_result_has_corner_cnn_ms(self) -> None:
        """corner_cnn_ms in FrameResult.__dataclass_fields__; default == 0.0."""
        # Assert
        assert "corner_cnn_ms" in FrameResult.__dataclass_fields__
        assert FrameResult.__dataclass_fields__["corner_cnn_ms"].default == 0.0

    def test_pipeline_stats_has_counter(self) -> None:
        """total_corner_cnn_analyzed in PipelineStats; default == 0."""
        # Assert
        assert "total_corner_cnn_analyzed" in PipelineStats.__dataclass_fields__
        stats = PipelineStats()
        assert stats.total_corner_cnn_analyzed == 0

    def test_cnn_disabled_pipeline_config_accepted(self) -> None:
        """ProducerConfig(corner_cnn_enabled=False) constructs without error."""
        # Act
        cfg = ProducerConfig(corner_cnn_enabled=False)

        # Assert
        assert cfg.corner_cnn_enabled is False


# ─── Chunk-04: Hardening Tests ──────────────────────────────────────


class TestHardeningImageIntegrity:
    """Hardening: verify crop images are never mutated by CNN prediction."""

    def test_image_integrity_crop_unchanged(self, predictor: CornerCnnPredictor) -> None:
        """40x200 crop value=42; after predictor.predict → np.array_equal."""
        # Arrange
        crop = np.full((40, 200, 3), 42, dtype=np.uint8)
        original = crop.copy()
        peaks = [(4, 5), (4, 58), (15, 58), (15, 5)]
        confs = [0.8, 0.8, 0.8, 0.8]
        synthetic = make_synthetic_output(peaks, confs)

        # Act
        with patch.object(predictor, "_infer", return_value=synthetic):
            predictor.predict([crop])

        # Assert — crop must be bitwise identical to original
        np.testing.assert_array_equal(crop, original)


class TestHardeningCnnFailure:
    """Hardening: CNN failure/OOM returns None + logs WARNING."""

    def test_cnn_failure_returns_none_keypoints(
        self, predictor: CornerCnnPredictor, cfg: ProducerConfig
    ) -> None:
        """Mock predictor raises RuntimeError('CUDA OOM') → all keypoints None."""
        # Arrange
        rois = [make_roi_image(), make_roi_image(track_id="track-002")]

        # Act
        with patch.object(predictor, "predict", side_effect=RuntimeError("CUDA OOM")):
            result = predict_corners(rois, predictor, cfg)

        # Assert — no exception, all keypoints None
        assert len(result) == 2
        for roi in result:
            assert roi.keypoints is None

    def test_cnn_failure_logs_warning_with_reason(
        self,
        predictor: CornerCnnPredictor,
        cfg: ProducerConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Same failure mock + caplog → WARNING containing exception message."""
        # Arrange
        rois = [make_roi_image()]

        # Act
        with caplog.at_level(logging.WARNING):
            with patch.object(predictor, "predict", side_effect=RuntimeError("CUDA OOM")):
                predict_corners(rois, predictor, cfg)

        # Assert
        warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warning_records) >= 1
        assert any("CUDA OOM" in r.message for r in warning_records)


class TestHardeningCnnDisabled:
    """Hardening: corner_cnn_enabled=False flow."""

    def test_cnn_disabled_no_predictor_created(self) -> None:
        """corner_cnn_enabled=False → CornerCnnPredictor.__init__ never called."""
        # Arrange
        disabled_cfg = ProducerConfig(corner_cnn_enabled=False)

        # Act — simulate pipeline conditional init pattern
        with patch.object(CornerCnnPredictor, "__init__", return_value=None) as mock_init:
            predictor = None
            if disabled_cfg.corner_cnn_enabled:
                predictor = CornerCnnPredictor(disabled_cfg)

        # Assert
        mock_init.assert_not_called()
        assert predictor is None

    def test_cnn_disabled_all_keypoints_none(self) -> None:
        """3 RoiImages with CNN disabled → all keypoints/scores remain None."""
        # Arrange — ROIs created without CNN processing
        rois = [make_roi_image(track_id=f"track-{i}") for i in range(3)]

        # Assert — without CNN, keypoints stay at default None
        for roi in rois:
            assert roi.keypoints is None
            assert roi.keypoint_scores is None


class TestHardeningZeroCrops:
    """Hardening: empty crop list edge case."""

    def test_zero_crops_no_op(self, predictor: CornerCnnPredictor, cfg: ProducerConfig) -> None:
        """predict_corners([], predictor, cfg) → []; predictor.predict NOT called."""
        # Act
        with patch.object(predictor, "predict") as mock_predict:
            result = predict_corners([], predictor, cfg)

        # Assert
        assert result == []
        mock_predict.assert_not_called()


class TestHardeningLetterboxRoundTrip:
    """Hardening: letterbox → denormalize round-trip accuracy."""

    def test_letterbox_round_trip_wide(self, predictor: CornerCnnPredictor) -> None:
        """Crop (40,200): crop coords → letterbox → denormalize → recovered."""
        # Arrange
        crop = np.zeros((40, 200, 3), dtype=np.uint8)
        _, scale, pad_x, pad_y = predictor._letterbox_crop(crop)

        # Preconditions
        assert scale == approx(1.28, abs=0.01)
        assert pad_y > 0

        # Known crop-pixel coordinates
        original = [(10.0, 5.0), (190.0, 5.0), (190.0, 35.0), (10.0, 35.0)]

        # Forward transform: crop → letterbox
        lb_coords = [(x * scale + pad_x, y * scale + pad_y) for x, y in original]

        # Act — inverse transform: letterbox → crop
        recovered = predictor._denormalize_corners(lb_coords, scale, pad_x, pad_y, 40, 200)

        # Assert — compare each point individually
        for rec, orig in zip(recovered, original, strict=True):
            assert rec == approx(orig, abs=0.5)

    def test_letterbox_round_trip_square(self, predictor: CornerCnnPredictor) -> None:
        """Crop (100,100): same round-trip chain."""
        # Arrange
        crop = np.zeros((100, 100, 3), dtype=np.uint8)
        _, scale, pad_x, pad_y = predictor._letterbox_crop(crop)

        # Preconditions
        assert pad_x > 0

        original = [(10.0, 10.0), (90.0, 10.0), (90.0, 90.0), (10.0, 90.0)]

        lb_coords = [(x * scale + pad_x, y * scale + pad_y) for x, y in original]

        # Act
        recovered = predictor._denormalize_corners(lb_coords, scale, pad_x, pad_y, 100, 100)

        # Assert — compare each point individually
        for rec, orig in zip(recovered, original, strict=True):
            assert rec == approx(orig, abs=0.5)

    def test_letterbox_round_trip_tall(self, predictor: CornerCnnPredictor) -> None:
        """Crop (120,40): same round-trip chain."""
        # Arrange
        crop = np.zeros((120, 40, 3), dtype=np.uint8)
        _, scale, pad_x, pad_y = predictor._letterbox_crop(crop)

        # Preconditions
        assert pad_x > 0

        original = [(5.0, 10.0), (35.0, 10.0), (35.0, 110.0), (5.0, 110.0)]

        lb_coords = [(x * scale + pad_x, y * scale + pad_y) for x, y in original]

        # Act
        recovered = predictor._denormalize_corners(lb_coords, scale, pad_x, pad_y, 120, 40)

        # Assert — compare each point individually
        for rec, orig in zip(recovered, original, strict=True):
            assert rec == approx(orig, abs=0.5)


class TestHardeningE2E:
    """Hardening: end-to-end keypoint population and ordering."""

    def test_e2e_keypoints_populated_4_tuples(
        self, predictor: CornerCnnPredictor, cfg: ProducerConfig
    ) -> None:
        """Mock TRT with clear peaks → roi.keypoints is list of 4 (float,float)."""
        # Arrange
        roi = make_roi_image()
        peaks = [(4, 5), (4, 58), (15, 58), (15, 5)]
        confs = [0.8, 0.85, 0.9, 0.75]
        synthetic = make_synthetic_output(peaks, confs)

        # Act
        with patch.object(predictor, "_infer", return_value=synthetic):
            result = predict_corners([roi], predictor, cfg)

        # Assert
        kps = result[0].keypoints
        assert kps is not None
        assert len(kps) == 4
        for pt in kps:
            assert isinstance(pt, tuple)
            assert len(pt) == 2
            assert isinstance(pt[0], float)
            assert isinstance(pt[1], float)

    def test_e2e_keypoint_scores_populated_4_floats(
        self, predictor: CornerCnnPredictor, cfg: ProducerConfig
    ) -> None:
        """Mock TRT → roi.keypoint_scores is list of 4 floats in [0, 1]."""
        # Arrange
        roi = make_roi_image()
        peaks = [(4, 5), (4, 58), (15, 58), (15, 5)]
        confs = [0.8, 0.85, 0.9, 0.75]
        synthetic = make_synthetic_output(peaks, confs)

        # Act
        with patch.object(predictor, "_infer", return_value=synthetic):
            result = predict_corners([roi], predictor, cfg)

        # Assert
        scores = result[0].keypoint_scores
        assert scores is not None
        assert len(scores) == 4
        for s in scores:
            assert isinstance(s, float)
            assert 0.0 <= s <= 1.0

    def test_e2e_keypoint_order_tl_tr_br_bl(
        self, predictor: CornerCnnPredictor, cfg: ProducerConfig
    ) -> None:
        """Mock TRT peaks in quadrants → roi.keypoints in TL,TR,BR,BL order."""
        # Arrange — peaks placed in distinct quadrants of 20×64 grid
        roi = make_roi_image()
        peaks = [
            (4, 5),  # TL: top-left of heatmap
            (4, 58),  # TR: top-right
            (15, 58),  # BR: bottom-right
            (15, 5),  # BL: bottom-left
        ]
        confs = [0.8, 0.8, 0.8, 0.8]
        synthetic = make_synthetic_output(peaks, confs)

        # Act
        with patch.object(predictor, "_infer", return_value=synthetic):
            result = predict_corners([roi], predictor, cfg)

        # Assert — spatial ordering
        kps = result[0].keypoints
        assert kps is not None
        assert kps[0][0] < kps[1][0]  # TL.x < TR.x
        assert kps[0][1] < kps[3][1]  # TL.y < BL.y
        assert kps[1][0] > kps[3][0]  # TR.x > BL.x
        assert kps[2][1] > kps[0][1]  # BR.y > TL.y

    def test_keypoint_ordering_round_trip(self) -> None:
        """CNN TL,TR,BR,BL corners → order_keypoints_by_angle → same order."""
        # Arrange — valid convex quadrilateral in TL,TR,BR,BL order
        from consumer.ops_quality_rich import order_keypoints_by_angle

        corners = [
            (10.0, 5.0),  # TL
            (190.0, 5.0),  # TR
            (190.0, 35.0),  # BR
            (10.0, 35.0),  # BL
        ]

        # Act
        ordered = order_keypoints_by_angle(corners)

        # Assert — canonical output matches CNN output order
        assert ordered == approx(corners, abs=1e-6)
