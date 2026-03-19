"""GPU quality analysis tests for ops_quality_fast.py (~35 tests).

Tests all functions in ops_quality_fast.py on actual GPU hardware.
Covers per-metric validation (focus, brightness, contrast, exposure,
band_edge), gradient histogram properties, composite quality_score,
hard-gate passes_min_quality, and thumbnail generation.

All tests require CUDA and are marked @pytest.mark.gpu.
"""

from __future__ import annotations

import numpy as np
import pytest
import torch

from producer.ops_quality_fast import fast_quality_analyze_rois
from tests.conftest_producer import make_config, make_roi_image

# Skip entire module if CUDA is not available
pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA not available"),
]

DEVICE = torch.device("cuda:0") if torch.cuda.is_available() else torch.device("cpu")


# ---------------------------------------------------------------------------
# Synthetic image helpers
# ---------------------------------------------------------------------------


def _uniform_roi(value: int = 128, h: int = 100, w: int = 200):
    """ROI with uniform color across all channels."""
    img = np.full((h, w, 3), value, dtype=np.uint8)
    return make_roi_image(crop_img=img)


def _black_roi(h: int = 100, w: int = 200):
    """All-black ROI (pixel value 0)."""
    return _uniform_roi(0, h, w)


def _white_roi(h: int = 100, w: int = 200):
    """All-white ROI (pixel value 255)."""
    return _uniform_roi(255, h, w)


def _sharp_edge_roi(h: int = 100, w: int = 200):
    """Left half black, right half white — sharp vertical edge."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, w // 2 :, :] = 255
    return make_roi_image(crop_img=img)


def _vertical_stripes_roi(h: int = 100, w: int = 200, stripe_w: int = 10):
    """Alternating black/white vertical stripes."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    for x in range(0, w, stripe_w * 2):
        img[:, x : x + stripe_w, :] = 255
    return make_roi_image(crop_img=img)


def _horizontal_stripes_roi(h: int = 100, w: int = 200, stripe_w: int = 10):
    """Alternating black/white horizontal stripes."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(0, h, stripe_w * 2):
        img[y : y + stripe_w, :, :] = 255
    return make_roi_image(crop_img=img)


def _analyze_one(roi, cfg=None):
    """Analyze a single ROI and return its RoiFastQuality result."""
    if cfg is None:
        cfg = make_config()
    results = fast_quality_analyze_rois([roi], cfg, device=DEVICE)
    assert len(results) == 1
    return results[0]


# ===========================================================================
# Batch behavior tests
# ===========================================================================


class TestAnalyzeRoisBatch:
    """Tests for batch processing behavior of fast_quality_analyze_rois."""

    def test_analyze_rois_empty(self):
        """Empty input returns empty output with no GPU work."""
        result = fast_quality_analyze_rois([], make_config(), device=DEVICE)
        assert result == []

    def test_analyze_rois_single(self):
        """Single ROI produces one complete result."""
        roi = _uniform_roi()
        results = fast_quality_analyze_rois([roi], make_config(), device=DEVICE)
        assert len(results) == 1
        r = results[0]
        assert r.roi is roi
        assert r.metrics is not None
        assert isinstance(r.quality_score, float)
        assert isinstance(r.passes_min_quality, bool)
        assert r.thumb_gray is not None

    def test_analyze_rois_batch_order(self):
        """Batch of 3 returns 3 results preserving input order."""
        rois = [_black_roi(), _uniform_roi(), _white_roi()]
        results = fast_quality_analyze_rois(rois, make_config(), device=DEVICE)
        assert len(results) == 3
        # Brightness must increase: black < gray < white
        assert results[0].metrics.brightness_mean < results[1].metrics.brightness_mean
        assert results[1].metrics.brightness_mean < results[2].metrics.brightness_mean

    def test_analyze_rois_reference_preserved(self):
        """result[i].roi is the same object as rois[i] (identity, not copy)."""
        rois = [_uniform_roi(), _sharp_edge_roi()]
        results = fast_quality_analyze_rois(rois, make_config(), device=DEVICE)
        for i, r in enumerate(results):
            assert r.roi is rois[i]

    def test_output_shape_dtype(self):
        """thumb_gray has correct shape/dtype, gradient_histogram is (25,) float32."""
        cfg = make_config()
        r = _analyze_one(_uniform_roi(), cfg)
        # Thumbnail
        assert r.thumb_gray.shape == (cfg.fast_quality_h_thumb, cfg.fast_quality_w_thumb)
        assert r.thumb_gray.dtype == np.uint8
        # Gradient histogram
        assert r.metrics.gradient_histogram.shape == (25,)
        assert r.metrics.gradient_histogram.dtype == np.float32


# ===========================================================================
# Focus metric tests
# ===========================================================================


class TestFocusMetric:
    """Tests for focus_tenengrad (Sobel gradient magnitude)."""

    def test_focus_uniform(self):
        """Uniform image has near-zero focus (border padding effects only)."""
        r = _analyze_one(_uniform_roi())
        # Not exactly 0 due to F.conv2d zero-padding edges + sqrt(1e-6) epsilon
        assert r.metrics.focus_tenengrad < 0.15

    def test_focus_sharp_edge(self):
        """Sharp edge image has significantly higher focus than uniform."""
        r_sharp = _analyze_one(_sharp_edge_roi())
        r_uniform = _analyze_one(_uniform_roi())
        assert r_sharp.metrics.focus_tenengrad > r_uniform.metrics.focus_tenengrad
        assert r_sharp.metrics.focus_tenengrad > 0.15

    def test_focus_non_negative(self):
        """Focus is non-negative for any input image."""
        for roi_fn in [_black_roi, _white_roi, _uniform_roi, _sharp_edge_roi]:
            r = _analyze_one(roi_fn())
            assert r.metrics.focus_tenengrad >= 0.0


# ===========================================================================
# Brightness metric tests
# ===========================================================================


class TestBrightnessMetric:
    """Tests for brightness_mean (mean grayscale intensity)."""

    def test_brightness_black(self):
        """All-black image has brightness approximately 0.0."""
        r = _analyze_one(_black_roi())
        assert r.metrics.brightness_mean == pytest.approx(0.0, abs=0.01)

    def test_brightness_white(self):
        """All-white image has brightness approximately 1.0."""
        r = _analyze_one(_white_roi())
        assert r.metrics.brightness_mean == pytest.approx(1.0, abs=0.01)

    def test_brightness_range(self):
        """Brightness is always in [0, 1]."""
        for roi_fn in [_black_roi, _white_roi, _uniform_roi, _sharp_edge_roi]:
            r = _analyze_one(roi_fn())
            assert 0.0 <= r.metrics.brightness_mean <= 1.0


# ===========================================================================
# Contrast metric tests
# ===========================================================================


class TestContrastMetric:
    """Tests for contrast_std (standard deviation of grayscale pixels)."""

    def test_contrast_uniform(self):
        """Uniform image has near-zero contrast."""
        r = _analyze_one(_uniform_roi())
        assert r.metrics.contrast_std == pytest.approx(0.0, abs=0.01)

    def test_contrast_range(self):
        """Contrast is in [0, 0.5] for any input."""
        for roi_fn in [_black_roi, _white_roi, _uniform_roi, _sharp_edge_roi]:
            r = _analyze_one(roi_fn())
            assert 0.0 <= r.metrics.contrast_std <= 0.5 + 1e-3


# ===========================================================================
# Over-exposed metric tests
# ===========================================================================


class TestOverExposedMetric:
    """Tests for over_exposed_frac (fraction of pixels > over_threshold)."""

    def test_over_exposed_black(self):
        """All-black image: no pixels above over_threshold (0.94)."""
        r = _analyze_one(_black_roi())
        assert r.metrics.over_exposed_frac == pytest.approx(0.0, abs=1e-6)

    def test_over_exposed_white(self):
        """All-white image: all pixels (1.0) above over_threshold (0.94)."""
        r = _analyze_one(_white_roi())
        assert r.metrics.over_exposed_frac == pytest.approx(1.0, abs=0.01)

    def test_over_exposed_range(self):
        """Over-exposed fraction is in [0, 1]."""
        for roi_fn in [_black_roi, _white_roi, _uniform_roi]:
            r = _analyze_one(roi_fn())
            assert 0.0 <= r.metrics.over_exposed_frac <= 1.0


# ===========================================================================
# Under-exposed metric tests
# ===========================================================================


class TestUnderExposedMetric:
    """Tests for under_exposed_frac (fraction of pixels < under_threshold)."""

    def test_under_exposed_black(self):
        """All-black image: all pixels (0.0) below under_threshold (0.06)."""
        r = _analyze_one(_black_roi())
        assert r.metrics.under_exposed_frac == pytest.approx(1.0, abs=0.01)

    def test_under_exposed_white(self):
        """All-white image: no pixels below under_threshold (0.06)."""
        r = _analyze_one(_white_roi())
        assert r.metrics.under_exposed_frac == pytest.approx(0.0, abs=1e-6)


# ===========================================================================
# Band edge metric tests
# ===========================================================================


class TestBandEdgeMetric:
    """Tests for band_edge_mean (gradient magnitude in character band)."""

    def test_band_edge_uniform(self):
        """Uniform image has near-zero band edge (no gradients in band)."""
        r = _analyze_one(_uniform_roi())
        assert r.metrics.band_edge_mean < 0.15

    def test_band_edge_non_negative(self):
        """Band edge is non-negative for any input."""
        for roi_fn in [_black_roi, _white_roi, _uniform_roi, _sharp_edge_roi]:
            r = _analyze_one(roi_fn())
            assert r.metrics.band_edge_mean >= 0.0


# ===========================================================================
# Gradient histogram tests
# ===========================================================================


class TestGradientHistogram:
    """Tests for the 25D L2-normalized gradient histogram feature vector."""

    def test_gradient_histogram_shape(self):
        """Histogram is always (25,) — 16 orientation bins + 9 spatial quadrants."""
        r = _analyze_one(_uniform_roi())
        assert r.metrics.gradient_histogram.shape == (25,)

    def test_gradient_histogram_dtype(self):
        """Histogram dtype is float32 (native GPU precision)."""
        r = _analyze_one(_uniform_roi())
        assert r.metrics.gradient_histogram.dtype == np.float32

    def test_gradient_histogram_l2_norm(self):
        """L2 norm is approximately 1.0 (unit vector) for non-degenerate input."""
        r = _analyze_one(_sharp_edge_roi())
        norm = np.linalg.norm(r.metrics.gradient_histogram)
        assert norm == pytest.approx(1.0, abs=1e-4)

    def test_gradient_histogram_self_similarity(self):
        """Self dot product equals 1.0 (cosine similarity with itself)."""
        r = _analyze_one(_sharp_edge_roi())
        h = r.metrics.gradient_histogram
        assert np.dot(h, h) == pytest.approx(1.0, abs=1e-4)

    def test_gradient_histogram_determinism(self):
        """Identical images produce identical histograms across runs."""
        roi = _sharp_edge_roi()
        cfg = make_config()
        r1 = fast_quality_analyze_rois([roi], cfg, device=DEVICE)[0]
        r2 = fast_quality_analyze_rois([roi], cfg, device=DEVICE)[0]
        np.testing.assert_allclose(
            r1.metrics.gradient_histogram,
            r2.metrics.gradient_histogram,
            rtol=1e-5,
            atol=1e-6,
        )

    def test_gradient_histogram_dissimilar(self):
        """Vertical vs horizontal stripes produce dissimilar histograms."""
        r_v = _analyze_one(_vertical_stripes_roi())
        r_h = _analyze_one(_horizontal_stripes_roi())
        similarity = np.dot(
            r_v.metrics.gradient_histogram,
            r_h.metrics.gradient_histogram,
        )
        assert similarity < 0.98


# ===========================================================================
# Composite quality score tests
# ===========================================================================


class TestQualityScore:
    """Tests for the weighted composite quality_score."""

    def test_quality_score_range(self):
        """Quality score is in [0, 1] for any input."""
        for roi_fn in [_black_roi, _white_roi, _uniform_roi, _sharp_edge_roi]:
            r = _analyze_one(roi_fn())
            assert 0.0 <= r.quality_score <= 1.0

    def test_quality_score_monotonicity(self):
        """Higher focus (sharp edge vs uniform) produces higher quality score."""
        r_uniform = _analyze_one(_uniform_roi())
        r_sharp = _analyze_one(_sharp_edge_roi())
        # Sharp edge has higher focus and band_edge
        assert r_sharp.metrics.focus_tenengrad > r_uniform.metrics.focus_tenengrad
        assert r_sharp.quality_score > r_uniform.quality_score

    def test_default_weights_sum(self):
        """Default scoring weights sum to 1.0."""
        cfg = make_config()
        total = (
            cfg.fast_quality_w_focus
            + cfg.fast_quality_w_contrast
            + cfg.fast_quality_w_band
            + cfg.fast_quality_w_bright
            + cfg.fast_quality_w_exposure
        )
        assert total == pytest.approx(1.0)

    def test_brightness_at_target(self):
        """Mid-gray (brightness at target) scores higher than dark image."""
        r_mid = _analyze_one(_uniform_roi(128))  # brightness ≈ 0.5 = target
        r_dark = _analyze_one(_uniform_roi(50))  # brightness ≈ 0.2, far from target
        # Both have near-zero focus/contrast/band, so difference is brightness + exposure
        assert r_mid.quality_score > r_dark.quality_score


# ===========================================================================
# Hard gate tests
# ===========================================================================


class TestHardGate:
    """Tests for passes_min_quality (AND of threshold checks)."""

    def test_hard_gate_all_at_threshold(self):
        """All metrics exactly at threshold boundaries pass (inclusive >=, <=)."""
        roi = _sharp_edge_roi()
        # Discover actual metric values
        r = fast_quality_analyze_rois([roi], make_config(), device=DEVICE)[0]
        m = r.metrics
        # Set thresholds to exactly match discovered metrics
        cfg = make_config(
            fast_quality_focus_min=m.focus_tenengrad,
            fast_quality_contrast_min=m.contrast_std,
            fast_quality_band_edge_min=m.band_edge_mean,
            fast_quality_bright_min=m.brightness_mean,
            fast_quality_bright_max=m.brightness_mean,
            fast_quality_over_exposed_max=m.over_exposed_frac,
            fast_quality_under_exposed_max=m.under_exposed_frac,
        )
        r2 = fast_quality_analyze_rois([roi], cfg, device=DEVICE)[0]
        assert r2.passes_min_quality is True

    def test_hard_gate_one_below(self):
        """One metric below threshold causes gate failure (AND logic)."""
        roi = _sharp_edge_roi()
        r = fast_quality_analyze_rois([roi], make_config(), device=DEVICE)[0]
        m = r.metrics
        # All permissive except focus_min slightly above actual value
        cfg = make_config(
            fast_quality_focus_min=m.focus_tenengrad + 0.01,
            fast_quality_contrast_min=0.0,
            fast_quality_band_edge_min=0.0,
            fast_quality_bright_min=0.0,
            fast_quality_bright_max=1.0,
            fast_quality_over_exposed_max=1.0,
            fast_quality_under_exposed_max=1.0,
        )
        r2 = fast_quality_analyze_rois([roi], cfg, device=DEVICE)[0]
        assert r2.passes_min_quality is False

    def test_hard_gate_independent_score(self):
        """Gate result is independent of quality_score (separate check)."""
        roi = _sharp_edge_roi()
        # Permissive gate → passes
        cfg_pass = make_config(
            fast_quality_focus_min=0.0,
            fast_quality_contrast_min=0.0,
            fast_quality_band_edge_min=0.0,
            fast_quality_bright_min=0.0,
            fast_quality_bright_max=1.0,
            fast_quality_over_exposed_max=1.0,
            fast_quality_under_exposed_max=1.0,
        )
        # Impossible gate → fails (focus_min unreachable)
        cfg_fail = make_config(
            fast_quality_focus_min=999.0,
            fast_quality_contrast_min=0.0,
            fast_quality_band_edge_min=0.0,
            fast_quality_bright_min=0.0,
            fast_quality_bright_max=1.0,
            fast_quality_over_exposed_max=1.0,
            fast_quality_under_exposed_max=1.0,
        )
        r_pass = fast_quality_analyze_rois([roi], cfg_pass, device=DEVICE)[0]
        r_fail = fast_quality_analyze_rois([roi], cfg_fail, device=DEVICE)[0]
        # Scoring params unchanged → same quality_score
        assert r_pass.quality_score == pytest.approx(r_fail.quality_score, abs=1e-6)
        # But gate results differ
        assert r_pass.passes_min_quality is True
        assert r_fail.passes_min_quality is False

    def test_hard_gate_all_black(self):
        """All-black image fails gate when bright_min > 0."""
        roi = _black_roi()
        cfg = make_config(fast_quality_bright_min=0.25)
        r = fast_quality_analyze_rois([roi], cfg, device=DEVICE)[0]
        assert r.metrics.brightness_mean < 0.25
        assert r.passes_min_quality is False


# ===========================================================================
# Thumbnail tests
# ===========================================================================


class TestThumbnail:
    """Tests for thumb_gray generation."""

    def test_thumb_gray_shape_dtype(self):
        """Thumbnail has configured shape, uint8 dtype, values in [0, 255]."""
        cfg = make_config()
        r = _analyze_one(_uniform_roi(), cfg)
        assert r.thumb_gray.shape == (cfg.fast_quality_h_thumb, cfg.fast_quality_w_thumb)
        assert r.thumb_gray.dtype == np.uint8
        assert r.thumb_gray.min() >= 0
        assert r.thumb_gray.max() <= 255
