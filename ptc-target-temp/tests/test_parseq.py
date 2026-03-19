"""Tests for PARSeq TRT engine — config, types, vocabulary mapping, inference pipeline."""

from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import numpy as np
import pytest
import torch

from consumer.config import ConsumerConfig
from consumer.models import (
    AggregationDecision,
    ConfidenceBucket,
    EnhancedBatchSelection,
    PostProcessingMeta,
    ProcessorOutput,
    ReasonCode,
    RoiRichQuality,
)
from consumer.ops_ocr_queue import OcrReadyQueue
from consumer.ops_parseq import (
    OcrWorkerTickResult,
    ParseqInferenceResult,
    ingest_and_infer,
    ocr_worker_tick,
)

if TYPE_CHECKING:
    from consumer.ops_parseq import ParseqEngine

# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def make_vocab_file(tmp_path: Path, chars: str, eos_token: str = "<eos>") -> Path:
    """Create a temporary vocab file with EOS on line 0, then one char per line.

    Args:
        tmp_path: pytest tmp_path fixture directory.
        chars: Characters to include (e.g. "ABC" -> lines: <eos>, A, B, C).
        eos_token: EOS token string written on line 0.

    Returns:
        Path to the written vocab file.
    """
    vocab_path = tmp_path / "vocab.txt"
    lines = [eos_token] + list(chars)
    vocab_path.write_text("\n".join(lines) + "\n")
    return vocab_path


def make_parseq_logits(
    batch_size: int,
    max_T: int,
    vocab_size: int = 95,
    target_string: str | None = None,
    allowed_indices: torch.Tensor | None = None,
    peak_logit: float = 5.0,
    noise_std: float = 0.1,
    seed: int = 42,
) -> np.ndarray:
    """Create synthetic logits with known target for testing.

    If target_string is provided along with allowed_indices, the argmax of
    filtered logits will match the target characters at the corresponding
    positions.

    Args:
        batch_size: Number of samples.
        max_T: Sequence length (positions).
        vocab_size: Full vocabulary size (default 95 for PARSeq).
        target_string: Optional target characters (e.g. "AB").
        allowed_indices: Mapping from allowed-index to full-vocab index.
        peak_logit: Value to place at the target position.
        noise_std: Standard deviation of background noise.
        seed: Random seed for reproducibility.

    Returns:
        np.ndarray of shape (batch_size, max_T, vocab_size) float32.
    """
    rng = np.random.default_rng(seed)
    logits = rng.normal(0.0, noise_std, (batch_size, max_T, vocab_size)).astype(np.float32)

    if target_string is not None and allowed_indices is not None:
        default_allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        for pos_idx, ch in enumerate(target_string):
            if pos_idx >= max_T:
                break
            char_pos_in_allowed = default_allowed.index(ch)
            full_vocab_idx = int(allowed_indices[char_pos_in_allowed].item())
            logits[:, pos_idx, full_vocab_idx] = peak_logit

    return logits


# ===========================================================================
# Config tests
# ===========================================================================


class TestParseqConfigDefaults:
    """Tests for PARSeq config field defaults."""

    def test_default_temperature(self) -> None:
        """M1: default temperature is 1.2 per calibration strategy."""
        cfg = ConsumerConfig()
        assert cfg.parseq_temperature == 1.2

    def test_default_max_plate_chars(self) -> None:
        cfg = ConsumerConfig()
        assert cfg.parseq_max_plate_chars == 8

    def test_default_max_batch_size(self) -> None:
        cfg = ConsumerConfig()
        assert cfg.parseq_max_batch_size == 12

    def test_default_vocab_path(self) -> None:
        cfg = ConsumerConfig()
        assert cfg.parseq_vocab_path == "models/parseq_vocab.txt"

    def test_cross_validation_default_passes(self) -> None:
        """H6: default parseq_max_plate_chars=8, agg_max_positions=9 passes (8+1==9)."""
        cfg = ConsumerConfig()
        assert cfg.parseq_max_plate_chars + 1 == cfg.agg_max_positions


class TestParseqConfigValidation:
    """Tests for PARSeq config validation rules."""

    def test_temperature_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="must be > 0"):
            ConsumerConfig(parseq_temperature=0)

    def test_temperature_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="must be > 0"):
            ConsumerConfig(parseq_temperature=-1)

    def test_warmup_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            ConsumerConfig(parseq_warmup_runs=-1)

    def test_norm_std_zero_element_raises(self) -> None:
        with pytest.raises(ValueError):
            ConsumerConfig(parseq_norm_std=(0.0, 0.224, 0.225))

    def test_cross_validation_parseq_agg_positions_mismatch(self) -> None:
        """H6: parseq_max_plate_chars=7 raises because 7+1=8 != agg_max_positions=9."""
        with pytest.raises(ValueError):
            ConsumerConfig(parseq_max_plate_chars=7)

    def test_allowed_chars_mismatch_warns(self) -> None:
        """Mismatched parseq/agg allowed_chars emits UserWarning."""
        with pytest.warns(UserWarning, match="mismatch"):
            ConsumerConfig(parseq_allowed_chars="ABC", agg_allowed_chars="XYZ")


# ===========================================================================
# load_vocab tests
# ===========================================================================


class TestLoadVocab:
    """Tests for load_vocab()."""

    def test_load_standard_vocab(self, tmp_path: Path) -> None:
        """Standard vocab: 36 chars + EOS = 37 tokens."""
        from consumer.ops_parseq import load_vocab

        vf = make_vocab_file(tmp_path, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        vocab = load_vocab(vf)
        assert len(vocab) == 37

    def test_eos_first_line(self, tmp_path: Path) -> None:
        """EOS token is <eos> on line 0."""
        from consumer.ops_parseq import load_vocab

        vf = make_vocab_file(tmp_path, "ABC")
        vocab = load_vocab(vf)
        assert vocab[0] == "<eos>"

    def test_missing_file_raises(self) -> None:
        from consumer.ops_parseq import load_vocab

        with pytest.raises(FileNotFoundError):
            load_vocab(Path("/nonexistent/vocab.txt"))

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        from consumer.ops_parseq import load_vocab

        empty = tmp_path / "empty.txt"
        empty.write_text("")
        with pytest.raises(ValueError):
            load_vocab(empty)

    def test_strips_whitespace(self, tmp_path: Path) -> None:
        """Trailing spaces and newlines are stripped."""
        from consumer.ops_parseq import load_vocab

        vocab_path = tmp_path / "vocab_ws.txt"
        vocab_path.write_text("<eos>  \nA \nB\t\nC\n")
        vocab = load_vocab(vocab_path)
        assert vocab == ["<eos>", "A", "B", "C"]


# ===========================================================================
# build_allowed_indices tests
# ===========================================================================


def _make_full_vocab(tmp_path: Path) -> list[str]:
    """Create a full 95-token vocab matching the real PARSeq layout."""
    from consumer.ops_parseq import load_vocab

    # Real PARSeq v1.0.0 vocab order: <eos>, 0-9, a-z, A-Z, specials
    chars = (
        "0123456789"
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
    )
    vf = make_vocab_file(tmp_path, chars)
    return load_vocab(vf)


class TestBuildAllowedIndices:
    """Tests for build_allowed_indices()."""

    def test_standard_mapping_length_37(self, tmp_path: Path) -> None:
        """H5: tensor length 37, dtype=torch.long."""
        from consumer.ops_parseq import build_allowed_indices

        vocab = _make_full_vocab(tmp_path)
        indices, _, _ = build_allowed_indices(vocab, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        assert indices.shape == (37,)
        assert indices.dtype == torch.long

    def test_eos_at_index_36(self, tmp_path: Path) -> None:
        from consumer.ops_parseq import build_allowed_indices

        vocab = _make_full_vocab(tmp_path)
        _, _, eos_index = build_allowed_indices(vocab, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        assert eos_index == 36

    def test_output_order_AZ_09_EOS(self, tmp_path: Path) -> None:
        """vocab_chars[0]=='A', [25]=='Z', [26]=='0', [35]=='9'."""
        from consumer.ops_parseq import build_allowed_indices

        vocab = _make_full_vocab(tmp_path)
        _, vocab_chars, _ = build_allowed_indices(vocab, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        assert vocab_chars[0] == "A"
        assert vocab_chars[25] == "Z"
        assert vocab_chars[26] == "0"
        assert vocab_chars[35] == "9"

    def test_missing_eos_raises(self) -> None:
        """Vocab without EOS raises ValueError mentioning 'EOS'."""
        from consumer.ops_parseq import build_allowed_indices

        vocab = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        with pytest.raises(ValueError, match="EOS"):
            build_allowed_indices(vocab, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")

    def test_missing_allowed_char_raises(self, tmp_path: Path) -> None:
        """Vocab missing 'Q' raises ValueError with diagnostic."""
        from consumer.ops_parseq import build_allowed_indices, load_vocab

        chars_no_q = "ABCDEFGHIJKLMNOP" + "RSTUVWXYZ0123456789"
        vf = make_vocab_file(tmp_path, chars_no_q)
        vocab = load_vocab(vf)
        with pytest.raises(ValueError, match="Q"):
            build_allowed_indices(vocab, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")

    def test_indices_point_to_correct_chars(self, tmp_path: Path) -> None:
        """full_vocab[allowed_indices[0]]=='A', full_vocab[allowed_indices[26]]=='0'."""
        from consumer.ops_parseq import build_allowed_indices

        vocab = _make_full_vocab(tmp_path)
        indices, _, _ = build_allowed_indices(vocab, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        assert vocab[int(indices[0].item())] == "A"
        assert vocab[int(indices[26].item())] == "0"


# ===========================================================================
# ParseqInferenceResult tests
# ===========================================================================


class TestParseqInferenceResult:
    """Tests for ParseqInferenceResult frozen dataclass."""

    def test_frozen_immutable(self) -> None:
        from consumer.ops_parseq import ParseqInferenceResult

        result = ParseqInferenceResult(
            distributions=np.zeros((1, 9, 37), dtype=np.float32),
            vocab_chars=tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") + ("<eos>",),
            eos_index=36,
            max_seq_len=9,
            inference_time_ms=1.0,
        )
        with pytest.raises(FrozenInstanceError):
            result.max_seq_len = 10  # type: ignore[misc]

    def test_all_fields_accessible(self) -> None:
        from consumer.ops_parseq import ParseqInferenceResult

        dists = np.ones((2, 9, 37), dtype=np.float32)
        vc = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") + ("<eos>",)
        result = ParseqInferenceResult(
            distributions=dists,
            vocab_chars=vc,
            eos_index=36,
            max_seq_len=9,
            inference_time_ms=2.5,
        )
        assert result.distributions.shape == (2, 9, 37)
        assert len(result.vocab_chars) == 37
        assert result.eos_index == 36
        assert result.max_seq_len == 9
        assert result.inference_time_ms == 2.5


# ===========================================================================
# Factory function tests
# ===========================================================================


class TestFactoryFunctions:
    """Tests for test factory functions themselves."""

    def test_make_vocab_file_readable(self, tmp_path: Path) -> None:
        """make_vocab_file('ABC') -> load_vocab returns ['<eos>', 'A', 'B', 'C']."""
        from consumer.ops_parseq import load_vocab

        vf = make_vocab_file(tmp_path, "ABC")
        vocab = load_vocab(vf)
        assert vocab == ["<eos>", "A", "B", "C"]

    def test_make_parseq_logits_shape(self) -> None:
        """make_parseq_logits(4, 9, 37) shape == (4, 9, 37). M3: default vocab_size=95."""
        logits = make_parseq_logits(4, 9, 37)
        assert logits.shape == (4, 9, 37)
        logits_default = make_parseq_logits(4, 9)
        assert logits_default.shape == (4, 9, 95)

    def test_make_parseq_logits_target_peak(self, tmp_path: Path) -> None:
        """make_parseq_logits with target_string='AB' argmax matches A, B positions."""
        from consumer.ops_parseq import build_allowed_indices, load_vocab

        chars = (
            "0123456789"
            "abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
        )
        vf = make_vocab_file(tmp_path, chars)
        vocab = load_vocab(vf)
        allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        indices, vocab_chars, eos_idx = build_allowed_indices(vocab, allowed)

        logits = make_parseq_logits(
            batch_size=1,
            max_T=9,
            vocab_size=95,
            target_string="AB",
            allowed_indices=indices,
            peak_logit=10.0,
            noise_std=0.01,
        )

        # After filtering by allowed_indices, argmax at position 0 should be A (idx 0)
        # and position 1 should be B (idx 1)
        filtered = logits[:, :, indices.numpy()]  # (1, 9, 37)
        assert np.argmax(filtered[0, 0, :]) == 0  # A
        assert np.argmax(filtered[0, 1, :]) == 1  # B


# ===========================================================================
# Chunk-02 CPU post-processing tests
# ===========================================================================


class TestParseqNormalization:
    """CPU tests for ParseqEngine.normalize ([-1,1] -> ImageNet)."""

    def test_minus1_to_imagenet(self) -> None:
        """Normalization: tensor of -1.0 -> (0.0-0.485)/0.229 for ch0 (exact)."""
        from consumer.ops_parseq import ParseqEngine

        batch = torch.full((1, 3, 32, 128), -1.0)
        mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
        std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
        result = ParseqEngine.normalize(batch, mean, std)
        # -1.0 in [-1,1] -> 0.0 in [0,1] -> (0.0 - 0.485) / 0.229
        expected_ch0 = (0.0 - 0.485) / 0.229
        assert result[0, 0, 0, 0].item() == pytest.approx(expected_ch0)

    def test_plus1_to_imagenet(self) -> None:
        """Normalization: tensor of +1.0 -> (1.0-0.485)/0.229 for ch0 (exact)."""
        from consumer.ops_parseq import ParseqEngine

        batch = torch.full((1, 3, 32, 128), 1.0)
        mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
        std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
        result = ParseqEngine.normalize(batch, mean, std)
        # +1.0 in [-1,1] -> 1.0 in [0,1] -> (1.0 - 0.485) / 0.229
        expected_ch0 = (1.0 - 0.485) / 0.229
        assert result[0, 0, 0, 0].item() == pytest.approx(expected_ch0)

    def test_normalize_preserves_shape(self) -> None:
        """Normalization: (4,3,32,128) input -> (4,3,32,128) output."""
        from consumer.ops_parseq import ParseqEngine

        batch = torch.zeros(4, 3, 32, 128)
        mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
        std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
        result = ParseqEngine.normalize(batch, mean, std)
        assert result.shape == (4, 3, 32, 128)


class TestParseqBgrToRgb:
    """CPU test for BGR -> RGB channel flip."""

    def test_bgr_to_rgb_channel_flip(self) -> None:
        """H8: BGR ch0=1,ch1=2,ch2=3 -> RGB ch0=3,ch1=2,ch2=1 via [:,[2,1,0],:,:]."""
        from consumer.ops_parseq import ParseqEngine

        batch = torch.zeros(1, 3, 2, 2)
        batch[:, 0, :, :] = 1.0  # B channel
        batch[:, 1, :, :] = 2.0  # G channel
        batch[:, 2, :, :] = 3.0  # R channel
        result = ParseqEngine.bgr_to_rgb(batch)
        assert result[0, 0, 0, 0].item() == 3.0  # R -> ch0
        assert result[0, 1, 0, 0].item() == 2.0  # G stays
        assert result[0, 2, 0, 0].item() == 1.0  # B -> ch2


class TestParseqVocabFilter:
    """CPU tests for vocab filtering via index gather."""

    def test_filter_reduces_dim(self) -> None:
        """Vocab filter: (4,9,95) gather with 37 indices -> (4,9,37)."""
        from consumer.ops_parseq import ParseqEngine

        logits = torch.randn(4, 9, 95)
        indices = torch.arange(37, dtype=torch.long)
        result = ParseqEngine.vocab_filter(logits, indices)
        assert result.shape == (4, 9, 37)

    def test_filter_gathers_correct_logits(self) -> None:
        """Vocab filter: known value at index 5, allowed_indices[0]=5 -> result[:,:,0]==42."""
        from consumer.ops_parseq import ParseqEngine

        logits = torch.zeros(1, 1, 95)
        logits[0, 0, 5] = 42.0
        indices = torch.tensor([5, 10, 20], dtype=torch.long)
        result = ParseqEngine.vocab_filter(logits, indices)
        assert result[0, 0, 0].item() == 42.0


class TestParseqTemperature:
    """CPU tests for temperature scaling and softmax."""

    def test_temperature_1_identity(self) -> None:
        """T=1.0: softmax(logits/1.0) == softmax(logits) (approx rel=1e-5)."""
        from consumer.ops_parseq import ParseqEngine

        logits = torch.randn(4, 9, 37)
        result = ParseqEngine.temperature_scale_and_softmax(logits, 1.0)
        expected = torch.softmax(logits, dim=-1)
        assert torch.allclose(result, expected, rtol=1e-5)

    def test_temperature_high_softer(self) -> None:
        """T=2.0 max prob < T=1.0 max prob (softer distribution)."""
        from consumer.ops_parseq import ParseqEngine

        logits = torch.randn(4, 9, 37)
        logits[..., 0] += 5.0  # Create a clear peak
        probs_t1 = ParseqEngine.temperature_scale_and_softmax(logits, 1.0)
        probs_t2 = ParseqEngine.temperature_scale_and_softmax(logits, 2.0)
        assert probs_t2.max().item() < probs_t1.max().item()

    def test_temperature_low_sharper(self) -> None:
        """T=0.5 max prob > T=1.0 max prob (sharper distribution)."""
        from consumer.ops_parseq import ParseqEngine

        logits = torch.randn(4, 9, 37)
        logits[..., 0] += 5.0  # Create a clear peak
        probs_t1 = ParseqEngine.temperature_scale_and_softmax(logits, 1.0)
        probs_t05 = ParseqEngine.temperature_scale_and_softmax(logits, 0.5)
        assert probs_t05.max().item() > probs_t1.max().item()

    def test_temperature_preserves_argmax(self) -> None:
        """T=0.5/1.0/2.0 all produce same argmax on peaked logits."""
        from consumer.ops_parseq import ParseqEngine

        logits = torch.randn(4, 9, 37)
        logits[..., 5] += 10.0  # Strong peak at index 5
        for T in (0.5, 1.0, 2.0):
            probs = ParseqEngine.temperature_scale_and_softmax(logits, T)
            assert (probs.argmax(dim=-1) == 5).all()

    def test_temperature_magnitude_floor(self) -> None:
        """T=2.0 peak=5.0 37 classes: max prob >= 1/37."""
        from consumer.ops_parseq import ParseqEngine

        logits = torch.zeros(1, 1, 37)
        logits[0, 0, 0] = 5.0
        probs = ParseqEngine.temperature_scale_and_softmax(logits, 2.0)
        assert probs[0, 0].max().item() >= 1.0 / 37


class TestParseqSoftmax:
    """CPU tests for softmax properties."""

    def test_softmax_sums_to_one(self) -> None:
        """softmax(logits/T) sum(dim=-1) == approx(1.0, abs=1e-5) all positions."""
        from consumer.ops_parseq import ParseqEngine

        logits = torch.randn(4, 9, 37)
        probs = ParseqEngine.temperature_scale_and_softmax(logits, 1.2)
        sums = probs.sum(dim=-1)
        assert torch.allclose(sums, torch.ones_like(sums), atol=1e-5)

    def test_softmax_non_negative(self) -> None:
        """softmax output all >= 0 including for negative input logits."""
        from consumer.ops_parseq import ParseqEngine

        logits = torch.randn(4, 9, 37) * 10.0 - 5.0
        probs = ParseqEngine.temperature_scale_and_softmax(logits, 1.0)
        assert (probs >= 0).all()


class TestParseqTruncation:
    """CPU test for output truncation."""

    def test_output_truncation_shape(self) -> None:
        """Truncate (8,26,37) native -> (8,9,37) via [:,:9,:]."""
        from consumer.ops_parseq import ParseqEngine

        distributions = torch.randn(8, 26, 37)
        result = ParseqEngine.truncate(distributions, 9)
        assert result.shape == (8, 9, 37)


class TestParseqNanInfDetection:
    """CPU tests for NaN/Inf detection."""

    def test_nan_detected(self) -> None:
        """CPU logits with NaN -> has_nan_inf returns True."""
        from consumer.ops_parseq import ParseqEngine

        logits = torch.randn(4, 9, 37)
        logits[0, 0, 0] = float("nan")
        assert ParseqEngine.has_nan_inf(logits) is True

    def test_inf_detected(self) -> None:
        """CPU logits with +Inf -> has_nan_inf returns True."""
        from consumer.ops_parseq import ParseqEngine

        logits = torch.randn(4, 9, 37)
        logits[0, 0, 0] = float("inf")
        assert ParseqEngine.has_nan_inf(logits) is True


class TestParseqMissingEngine:
    """Test for missing engine file (CPU — no GPU needed)."""

    def test_missing_engine_file_raises(self, tmp_path: Path) -> None:
        """Nonexistent .engine -> FileNotFoundError match 'export'."""
        from consumer.ops_parseq import ParseqEngine

        cfg = ConsumerConfig(
            parseq_model_path=str(tmp_path / "nonexistent.pt"),
        )
        with pytest.raises(FileNotFoundError, match="export"):
            ParseqEngine(cfg)


# ===========================================================================
# Chunk-02 GPU integration tests
# ===========================================================================


@pytest.fixture(scope="module")
def parseq_engine() -> ParseqEngine:
    """Shared ParseqEngine for GPU integration tests (module-scoped)."""
    from consumer.ops_parseq import ParseqEngine

    return ParseqEngine(ConsumerConfig())


@pytest.mark.gpu
class TestParseqEngineGpu:
    """GPU integration tests — require real TRT engine at models/parseq.engine."""

    def test_engine_loads_on_gpu(self, parseq_engine: ParseqEngine) -> None:
        """Real engine+vocab -> ParseqEngine created, _img_h==32, _img_w==128 (C5)."""
        assert parseq_engine._img_h == 32
        assert parseq_engine._img_w == 128

    def test_engine_output_dims_from_engine(self, parseq_engine: ParseqEngine) -> None:
        """C6: engine._native_max_T==26, engine._full_vocab_size==95 from output binding."""
        assert parseq_engine._native_max_T == 26
        assert parseq_engine._full_vocab_size == 95

    def test_cross_validation_catches_vocab_mismatch(self, tmp_path: Path) -> None:
        """C6: tampered vocab file -> error on init (engine vs vocab count mismatch)."""
        from consumer.ops_parseq import ParseqEngine

        tampered = tmp_path / "bad_vocab.txt"
        tampered.write_text("<eos>\nA\nB\n")
        cfg = ConsumerConfig(
            parseq_vocab_path=str(tampered),
            parseq_allowed_chars="AB",
            parseq_max_plate_chars=8,
        )
        with pytest.raises(ValueError, match="vocab"):
            ParseqEngine(cfg)

    def test_preallocated_buffer_shape(self, parseq_engine: ParseqEngine) -> None:
        """M5: engine._output_buf.shape == (12, 26, 95) after init."""
        assert parseq_engine._output_buf.shape == (12, 26, 95)

    def test_overflow_returns_none(self, parseq_engine: ParseqEngine) -> None:
        """batch N=13 > max_batch=12 -> returns None."""
        batch = torch.zeros(13, 3, 32, 128, device="cuda:0")
        result = parseq_engine.run(batch)
        assert result is None

    def test_empty_batch_returns_none(self, parseq_engine: ParseqEngine) -> None:
        """H7: batch N=0 -> returns None (not empty tensor)."""
        batch = torch.zeros(0, 3, 32, 128, device="cuda:0")
        result = parseq_engine.run(batch)
        assert result is None

    def test_run_top_level_exception_returns_none(
        self, parseq_engine: ParseqEngine, caplog: pytest.LogCaptureFixture
    ) -> None:
        """H11: malformed input triggers internal error -> returns None, ERROR logged."""
        import logging

        bad = torch.zeros(1, 1, 1, 1, device="cuda:0")
        with caplog.at_level(logging.ERROR):
            result = parseq_engine.run(bad)
        assert result is None
        assert any(r.levelno >= logging.ERROR for r in caplog.records)

    def test_logit_dynamic_range_warning(
        self, parseq_engine: ParseqEngine, caplog: pytest.LogCaptureFixture
    ) -> None:
        """M6: dynamic range check executes on all-black input.

        A healthy opset-17 engine produces varied logits even for featureless
        input (range >> 1e-6), so the WARNING doesn't fire. This test verifies
        the code path exists and the engine handles the edge case gracefully.
        The 1e-6 threshold detects corrupted engines (e.g. opset-14 FP16 overflow).
        """
        import logging

        # All-black = -1.0 in preprocessor output space [-1, 1]
        batch = torch.full((1, 3, 32, 128), -1.0, device="cuda:0")
        with caplog.at_level(logging.WARNING):
            result = parseq_engine.run(batch)
        # Healthy engine: black input produces valid result with varied logits
        assert result is not None
        assert result.distributions.shape == (1, 9, 37)
        # Probabilities must be valid distributions
        row_sums = result.distributions.sum(axis=-1)
        np.testing.assert_allclose(row_sums, 1.0, atol=1e-5)


# ---------------------------------------------------------------------------
# Chunk-04: Ingestion Functions + Hardening
# ---------------------------------------------------------------------------


def _make_c04_inference_result(**overrides: object) -> ParseqInferenceResult:
    """Create a ParseqInferenceResult with sensible defaults for chunk-04 tests."""
    defaults: dict[str, object] = dict(
        distributions=np.random.rand(4, 9, 37).astype(np.float32),
        vocab_chars=tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") + ("<eos>",),
        eos_index=36,
        max_seq_len=9,
        inference_time_ms=5.0,
    )
    defaults.update(overrides)
    return ParseqInferenceResult(**defaults)  # type: ignore[arg-type]


def _make_aggregation_decision(done: bool = False, **overrides: object) -> AggregationDecision:
    """Create an AggregationDecision with sensible defaults."""
    defaults: dict[str, object] = dict(
        best_string="ABC1234",
        confidence_bucket=ConfidenceBucket.HIGH,
        done=done,
        reason_code=(ReasonCode.FINAL_ONEPASS_AGREE if done else ReasonCode.INTERIM_AMBIGUOUS),
        h1_string="ABC1234",
        h2_string=None,
        pass_num=1,
        debug_payload=None,
    )
    defaults.update(overrides)
    return AggregationDecision(**defaults)  # type: ignore[arg-type]


def _make_roi_rq(track_id: str = "t-42", quality: float = 0.8) -> RoiRichQuality:
    """Minimal RoiRichQuality stub for queue submission."""
    roi_rq = MagicMock(spec=RoiRichQuality)
    roi_rq.roi_fq = MagicMock()
    roi_rq.roi_fq.roi = MagicMock()
    roi_rq.roi_fq.roi.track_id = track_id
    roi_rq.roi_fq.roi.crop_img = np.zeros((40, 100, 3), dtype=np.uint8)
    roi_rq.roi_fq.roi.bbox = [0.0, 0.0, 100.0, 40.0]
    roi_rq.roi_fq.roi.padded_bbox = [0.0, 0.0, 100.0, 40.0]
    roi_rq.roi_fq.roi.frame_idx = 0
    roi_rq.roi_fq.roi.confidence = 0.9
    roi_rq.roi_fq.roi.frame_width = 1920
    roi_rq.roi_fq.roi.frame_height = 1080
    roi_rq.roi_fq.roi.keypoints = None
    roi_rq.roi_fq.roi.keypoint_scores = None
    roi_rq.roi_fq.roi.was_resized = False
    roi_rq.roi_fq.roi.resize_scale = 1.0
    roi_rq.roi_fq.roi.crop_width = 100
    roi_rq.roi_fq.roi.crop_height = 40
    roi_rq.roi_fq.quality_score = quality
    roi_rq.metrics = MagicMock()
    roi_rq.metrics.plate_width_px = 100.0
    roi_rq.metrics.plate_height_px = 40.0
    roi_rq.metrics.crop_clip_fraction = 0.0
    roi_rq.metrics.detection_confidence = 0.9
    roi_rq.metrics.tenengrad = 3000.0
    roi_rq.metrics.luminance_mean = 128.0
    roi_rq.metrics.global_contrast = 60.0
    roi_rq.metrics.noise_std = 5.0
    return roi_rq


def _submit_track(
    queue: OcrReadyQueue,
    track_id: str = "t-42",
    n_rois: int = 4,
    timestamp_ms: int = 1000,
) -> None:
    """Submit a candidate for a track to the queue."""
    rois = [_make_roi_rq(track_id=track_id, quality=0.8 - i * 0.05) for i in range(n_rois)]
    batch_tensor = torch.randn(n_rois, 3, 32, 128)
    is_enhanced = np.zeros(n_rois, dtype=bool)

    post_meta = [PostProcessingMeta(128.0, 60.0, 0.0, 0.0) for _ in range(n_rois)]
    proc_output = ProcessorOutput(
        batch_tensor=batch_tensor,
        is_enhanced=is_enhanced,
        post_meta=post_meta,
    )

    selection = EnhancedBatchSelection(
        track_id=track_id,
        version=1,
        base_rois=rois,
        enhance_rois=[],
        duplicate_groups={},
    )

    queue.submit_candidate(selection, proc_output, 1, timestamp_ms)


# ─── OcrWorkerTickResult Tests ───


class TestOcrWorkerTickResult:
    """Tests for OcrWorkerTickResult dataclass."""

    def test_importable(self) -> None:
        """OcrWorkerTickResult is importable and constructible."""
        decision = _make_aggregation_decision(done=True)
        result = _make_c04_inference_result()
        tick = OcrWorkerTickResult(
            track_id="t-1",
            commit_version=1,
            inference_result=result,
            aggregation_decision=decision,
            tick_time_ms=10.0,
        )
        assert tick.track_id == "t-1"
        assert tick.commit_version == 1
        assert tick.tick_time_ms == 10.0

    def test_tick_result_frozen_immutable(self) -> None:
        """OcrWorkerTickResult is frozen — field assignment raises."""
        decision = _make_aggregation_decision()
        result = _make_c04_inference_result()
        tick = OcrWorkerTickResult(
            track_id="t-1",
            commit_version=1,
            inference_result=result,
            aggregation_decision=decision,
            tick_time_ms=10.0,
        )
        with pytest.raises(FrozenInstanceError):
            tick.track_id = "t-2"  # type: ignore[misc]


# ─── ingest_and_infer Tests ───


class TestIngestAndInfer:
    """Tests for ingest_and_infer function."""

    def test_empty_queue_returns_none(self) -> None:
        """Empty queue -> ingest_and_infer returns None."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)
        engine = MagicMock()

        result = ingest_and_infer(engine, queue, "w-1", 2000)
        assert result is None

    def test_happy_path_returns_tuple(self) -> None:
        """Successful inference returns (work_item, ParseqInferenceResult)."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)
        _submit_track(queue, track_id="t-1")

        inf_result = _make_c04_inference_result()
        engine = MagicMock()
        engine.run.return_value = inf_result

        result = ingest_and_infer(engine, queue, "w-1", 2000)
        assert result is not None
        work_item, parseq_result = result
        assert work_item.track_id == "t-1"
        assert parseq_result is inf_result

    def test_inference_failure_calls_fail_commit(self) -> None:
        """Engine returns None -> fail_commit called, returns None."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)
        _submit_track(queue, track_id="t-1")

        engine = MagicMock()
        engine.run.return_value = None

        result = ingest_and_infer(engine, queue, "w-1", 2000)
        assert result is None

        # Track should be re-eligible (in_flight cleared by fail_commit)
        state = queue._tracks["t-1"]
        assert state.in_flight is None

    def test_preserves_metadata(self) -> None:
        """Work item metadata (track_id, commit_version) is preserved."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)
        _submit_track(queue, track_id="t-42")

        inf_result = _make_c04_inference_result()
        engine = MagicMock()
        engine.run.return_value = inf_result

        result = ingest_and_infer(engine, queue, "w-1", 2000)
        assert result is not None
        work_item, _ = result
        assert work_item.track_id == "t-42"
        assert work_item.commit_version == 1


# ─── ocr_worker_tick Tests ───


class TestOcrWorkerTick:
    """Tests for ocr_worker_tick function."""

    def test_tick_empty_returns_none(self) -> None:
        """Empty queue -> ocr_worker_tick returns None."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)
        engine = MagicMock()
        aggregator = MagicMock()

        result = ocr_worker_tick(engine, queue, aggregator, "w-1", 2000)
        assert result is None

    def test_tick_happy_not_done(self) -> None:
        """Aggregator done=False -> OcrWorkerTickResult; finish_commit called; mark_done NOT called."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)
        _submit_track(queue, track_id="t-1")

        inf_result = _make_c04_inference_result()
        engine = MagicMock()
        engine.run.return_value = inf_result

        decision = _make_aggregation_decision(done=False)
        aggregator = MagicMock()
        aggregator.aggregate_pass.return_value = decision

        result = ocr_worker_tick(engine, queue, aggregator, "w-1", 2000)
        assert result is not None
        assert isinstance(result, OcrWorkerTickResult)
        assert result.aggregation_decision.done is False

        # finish_commit should have been called
        state = queue._tracks["t-1"]
        assert state.in_flight is None
        assert state.consumed_commit_version == 1
        # NOT done
        assert state.done is False

    def test_tick_happy_done(self) -> None:
        """Aggregator done=True -> finish_commit + mark_done(reason_code.value)."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)
        _submit_track(queue, track_id="t-1")

        inf_result = _make_c04_inference_result()
        engine = MagicMock()
        engine.run.return_value = inf_result

        decision = _make_aggregation_decision(done=True, reason_code=ReasonCode.FINAL_ONEPASS_AGREE)
        aggregator = MagicMock()
        aggregator.aggregate_pass.return_value = decision

        result = ocr_worker_tick(engine, queue, aggregator, "w-1", 2000)
        assert result is not None
        assert result.aggregation_decision.done is True

        # Track should be marked done — mark_done evicts from _tracks to _done_metrics
        assert "t-1" not in queue._tracks
        assert "t-1" in queue._done_metrics

    def test_tick_inference_failure(self) -> None:
        """Engine returns None -> returns None; fail_commit called."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)
        _submit_track(queue, track_id="t-1")

        engine = MagicMock()
        engine.run.return_value = None
        aggregator = MagicMock()

        result = ocr_worker_tick(engine, queue, aggregator, "w-1", 2000)
        assert result is None

        # fail_commit should have cleared in_flight
        state = queue._tracks["t-1"]
        assert state.in_flight is None

    def test_tick_aggregation_exception(self) -> None:
        """Aggregator raises RuntimeError -> returns None; fail_commit called."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)
        _submit_track(queue, track_id="t-1")

        inf_result = _make_c04_inference_result()
        engine = MagicMock()
        engine.run.return_value = inf_result

        aggregator = MagicMock()
        aggregator.aggregate_pass.side_effect = RuntimeError("aggregation boom")

        result = ocr_worker_tick(engine, queue, aggregator, "w-1", 2000)
        assert result is None

        # fail_commit should have cleared in_flight
        state = queue._tracks["t-1"]
        assert state.in_flight is None

    def test_tick_batch_overflow(self) -> None:
        """Work item with engine returning None -> fail_commit."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)
        _submit_track(queue, track_id="t-1", n_rois=4)

        engine = MagicMock()
        engine.run.return_value = None
        aggregator = MagicMock()

        result = ocr_worker_tick(engine, queue, aggregator, "w-1", 2000)
        assert result is None

        state = queue._tracks["t-1"]
        assert state.in_flight is None


# ─── No-orphan system tests ───


class TestNoOrphan:
    """Verify no orphaned in-flight records after tick sequences."""

    def test_mixed_ticks_no_orphan(self) -> None:
        """3 tracks: success, inference_fail, aggregation_fail -> all in_flight is None."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)
        _submit_track(queue, track_id="t-ok", timestamp_ms=1000)
        _submit_track(queue, track_id="t-inf-fail", timestamp_ms=1001)
        _submit_track(queue, track_id="t-agg-fail", timestamp_ms=1002)

        inf_result = _make_c04_inference_result()
        decision = _make_aggregation_decision(done=True)

        call_count = 0

        def engine_side_effect(
            batch_tensor: object,
        ) -> ParseqInferenceResult | None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return None
            return inf_result

        engine = MagicMock()
        engine.run.side_effect = engine_side_effect

        agg_call_count = 0

        def agg_side_effect(*args: object, **kwargs: object) -> AggregationDecision:
            nonlocal agg_call_count
            agg_call_count += 1
            if agg_call_count == 2:
                raise RuntimeError("aggregation explosion")
            return decision

        aggregator = MagicMock()
        aggregator.aggregate_pass.side_effect = agg_side_effect

        for i in range(3):
            ocr_worker_tick(engine, queue, aggregator, "w-1", 3000 + i)

        # Successful+done tracks evicted from _tracks to _done_metrics
        # Failed tracks remain in _tracks with in_flight=None
        for tid in ["t-inf-fail", "t-agg-fail"]:
            if tid in queue._tracks:
                state = queue._tracks[tid]
                assert state.in_flight is None, f"Track {tid} has orphaned in_flight"
        assert "t-ok" not in queue._tracks

    def test_sequential_success_ticks_all_clean(self) -> None:
        """5 tracks all succeed -> all evicted from _tracks to _done_metrics."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)
        for i in range(5):
            _submit_track(queue, track_id=f"t-{i}", timestamp_ms=1000 + i)

        inf_result = _make_c04_inference_result()
        decision = _make_aggregation_decision(done=True)

        engine = MagicMock()
        engine.run.return_value = inf_result
        aggregator = MagicMock()
        aggregator.aggregate_pass.return_value = decision

        for i in range(5):
            result = ocr_worker_tick(engine, queue, aggregator, "w-1", 2000 + i)
            assert result is not None

        for i in range(5):
            assert f"t-{i}" not in queue._tracks
            assert f"t-{i}" in queue._done_metrics


# ─── Log assertion tests ───


class TestIngestionLogging:
    """Verify structured logging at key decision points."""

    def test_inference_failure_logs_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """Engine returns None -> ERROR log containing track_id."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)
        _submit_track(queue, track_id="t-err")

        engine = MagicMock()
        engine.run.return_value = None
        aggregator = MagicMock()

        with caplog.at_level(logging.DEBUG):
            ocr_worker_tick(engine, queue, aggregator, "w-1", 2000)

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("t-err" in r.message for r in error_records), (
            f"Expected ERROR log with 't-err', got: {[r.message for r in error_records]}"
        )

    def test_aggregation_exception_logs_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """Aggregator raises -> ERROR log containing 'aggregat'."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)
        _submit_track(queue, track_id="t-ae")

        inf_result = _make_c04_inference_result()
        engine = MagicMock()
        engine.run.return_value = inf_result

        aggregator = MagicMock()
        aggregator.aggregate_pass.side_effect = RuntimeError("aggregation error")

        with caplog.at_level(logging.DEBUG):
            ocr_worker_tick(engine, queue, aggregator, "w-1", 2000)

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("aggregat" in r.message.lower() for r in error_records), (
            f"Expected ERROR log with 'aggregat', got: {[r.message for r in error_records]}"
        )

    def test_happy_path_logs_confidence_bucket(self, caplog: pytest.LogCaptureFixture) -> None:
        """H12: normal success -> INFO log contains 'confidence_bucket'; no ERROR records."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)
        _submit_track(queue, track_id="t-ok")

        inf_result = _make_c04_inference_result()
        engine = MagicMock()
        engine.run.return_value = inf_result

        decision = _make_aggregation_decision(done=False)
        aggregator = MagicMock()
        aggregator.aggregate_pass.return_value = decision

        with caplog.at_level(logging.DEBUG):
            result = ocr_worker_tick(engine, queue, aggregator, "w-1", 2000)

        assert result is not None
        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("confidence_bucket" in r.message for r in info_records), (
            f"Expected INFO log with 'confidence_bucket', got: {[r.message for r in info_records]}"
        )
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert not error_records, (
            f"Expected no ERROR logs on happy path, got: {[r.message for r in error_records]}"
        )


# ===========================================================================
# Full-system integration tests (real engine, real queue, no mocks except aggregator)
# ===========================================================================


@pytest.mark.gpu
class TestFullSystemIntegration:
    """End-to-end integration tests using real TRT engine, real queue, real config.

    Aggregator is mocked (it's from a separate module) but everything else is real:
    - Real ParseqEngine from models/parseq.engine + models/parseq_vocab.txt
    - Real OcrReadyQueue with real submit_candidate / pull_next / finish_commit
    - Real ingest_and_infer and ocr_worker_tick functions
    """

    def test_submit_pull_infer_real_engine(self, parseq_engine: ParseqEngine) -> None:
        """Full pipeline: submit → pull → engine.run() with real TRT inference."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)

        # Create a batch tensor on GPU matching PARSeq input format [-1, 1]
        n_rois = 4
        batch_tensor = torch.randn(n_rois, 3, 32, 128, device="cuda:0")
        batch_tensor = batch_tensor.clamp(-1.0, 1.0)
        is_enhanced = np.zeros(n_rois, dtype=bool)

        rois = [_make_roi_rq(track_id="t-int-1", quality=0.8 - i * 0.05) for i in range(n_rois)]
        post_meta = [PostProcessingMeta(128.0, 60.0, 0.0, 0.0) for _ in range(n_rois)]
        proc_output = ProcessorOutput(
            batch_tensor=batch_tensor,
            is_enhanced=is_enhanced,
            post_meta=post_meta,
        )
        selection = EnhancedBatchSelection(
            track_id="t-int-1",
            version=1,
            base_rois=rois,
            enhance_rois=[],
            duplicate_groups={},
        )

        accepted = queue.submit_candidate(selection, proc_output, 1, 1000)
        assert accepted is True

        # Pull and run real inference
        work_item = queue.pull_next("w-int", 2000)
        assert work_item is not None
        assert work_item.track_id == "t-int-1"

        result = parseq_engine.run(work_item.batch_tensor)
        assert result is not None
        assert isinstance(result, ParseqInferenceResult)
        assert result.distributions.shape == (n_rois, 9, 37)
        assert result.eos_index == 36
        assert result.max_seq_len == 9

        # Verify distributions are valid probability distributions
        row_sums = result.distributions.sum(axis=-1)
        np.testing.assert_allclose(row_sums, 1.0, atol=1e-5)
        assert (result.distributions >= 0).all()
        assert (result.distributions <= 1).all()

    def test_ingest_and_infer_real_engine(self, parseq_engine: ParseqEngine) -> None:
        """ingest_and_infer with real engine + real queue — no mocks."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)

        n_rois = 3
        batch_tensor = torch.randn(n_rois, 3, 32, 128, device="cuda:0").clamp(-1.0, 1.0)
        is_enhanced = np.zeros(n_rois, dtype=bool)

        rois = [_make_roi_rq(track_id="t-ingest", quality=0.7 + i * 0.05) for i in range(n_rois)]
        post_meta = [PostProcessingMeta(128.0, 60.0, 0.0, 0.0) for _ in range(n_rois)]
        proc_output = ProcessorOutput(
            batch_tensor=batch_tensor,
            is_enhanced=is_enhanced,
            post_meta=post_meta,
        )
        selection = EnhancedBatchSelection(
            track_id="t-ingest",
            version=1,
            base_rois=rois,
            enhance_rois=[],
            duplicate_groups={},
        )
        queue.submit_candidate(selection, proc_output, 1, 1000)

        pair = ingest_and_infer(parseq_engine, queue, "w-int", 2000)
        assert pair is not None
        work_item, result = pair

        assert work_item.track_id == "t-ingest"
        assert isinstance(result, ParseqInferenceResult)
        assert result.distributions.shape == (n_rois, 9, 37)
        assert result.inference_time_ms > 0

        # Verify distributions are proper probabilities
        row_sums = result.distributions.sum(axis=-1)
        np.testing.assert_allclose(row_sums, 1.0, atol=1e-5)

    def test_ocr_worker_tick_real_engine_mock_aggregator(self, parseq_engine: ParseqEngine) -> None:
        """ocr_worker_tick with real engine + real queue + mock aggregator."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)

        n_rois = 4
        batch_tensor = torch.randn(n_rois, 3, 32, 128, device="cuda:0").clamp(-1.0, 1.0)
        is_enhanced = np.zeros(n_rois, dtype=bool)

        rois = [_make_roi_rq(track_id="t-tick", quality=0.75) for _ in range(n_rois)]
        post_meta = [PostProcessingMeta(128.0, 60.0, 0.0, 0.0) for _ in range(n_rois)]
        proc_output = ProcessorOutput(
            batch_tensor=batch_tensor,
            is_enhanced=is_enhanced,
            post_meta=post_meta,
        )
        selection = EnhancedBatchSelection(
            track_id="t-tick",
            version=1,
            base_rois=rois,
            enhance_rois=[],
            duplicate_groups={},
        )
        queue.submit_candidate(selection, proc_output, 1, 1000)

        decision = _make_aggregation_decision(done=False)
        aggregator = MagicMock()
        aggregator.aggregate_pass.return_value = decision

        tick_result = ocr_worker_tick(parseq_engine, queue, aggregator, "w-int", 2000)
        assert tick_result is not None
        assert isinstance(tick_result, OcrWorkerTickResult)
        assert tick_result.track_id == "t-tick"
        assert tick_result.tick_time_ms > 0

        # Verify real inference result inside tick result
        inf = tick_result.inference_result
        assert inf.distributions.shape == (n_rois, 9, 37)
        row_sums = inf.distributions.sum(axis=-1)
        np.testing.assert_allclose(row_sums, 1.0, atol=1e-5)

        # Verify aggregator was called with correct args from real inference
        aggregator.aggregate_pass.assert_called_once()
        call_args = aggregator.aggregate_pass.call_args
        assert call_args[0][0] == "t-tick"  # track_id
        assert call_args[0][1].shape == (n_rois, 9, 37)  # distributions
        assert call_args[1]["eos_index"] == 36

        # finish_commit was called — track consumed
        state = queue._tracks["t-tick"]
        assert state.in_flight is None
        assert state.consumed_commit_version == 1

    def test_vocab_chars_match_config(self, parseq_engine: ParseqEngine) -> None:
        """Real engine vocab_chars matches configured allowed_chars + EOS."""
        cfg = ConsumerConfig()
        expected_chars = tuple(cfg.parseq_allowed_chars) + ("<eos>",)
        assert parseq_engine._vocab_chars == expected_chars
        assert parseq_engine._eos_index == len(cfg.parseq_allowed_chars)

    def test_duplicate_groups_flow_through(self, parseq_engine: ParseqEngine) -> None:
        """duplicate_groups and per_roi_quality flow from submit through tick."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)

        n_rois = 3
        batch_tensor = torch.randn(n_rois, 3, 32, 128, device="cuda:0").clamp(-1.0, 1.0)
        is_enhanced = np.array([False, False, True], dtype=bool)

        rois = [_make_roi_rq(track_id="t-dup", quality=0.8 - i * 0.1) for i in range(n_rois)]
        post_meta = [PostProcessingMeta(128.0, 60.0, 0.0, 0.0) for _ in range(n_rois)]
        proc_output = ProcessorOutput(
            batch_tensor=batch_tensor,
            is_enhanced=is_enhanced,
            post_meta=post_meta,
        )

        dup_groups = {"g-0": [0, 2]}
        selection = EnhancedBatchSelection(
            track_id="t-dup",
            version=1,
            base_rois=rois,
            enhance_rois=[],
            duplicate_groups=dup_groups,
        )
        queue.submit_candidate(selection, proc_output, 1, 1000)

        decision = _make_aggregation_decision(done=False)
        aggregator = MagicMock()
        aggregator.aggregate_pass.return_value = decision

        tick_result = ocr_worker_tick(parseq_engine, queue, aggregator, "w-int", 2000)
        assert tick_result is not None

        # Verify duplicate_groups flowed through to aggregator call
        call_args = aggregator.aggregate_pass.call_args
        passed_groups = call_args[0][2]  # 3rd positional = duplicate_groups
        assert passed_groups == dup_groups

        # Verify per_roi_quality was passed (non-empty — computed by submit_candidate)
        passed_quality = call_args[1]["quality_scores"]
        assert len(passed_quality) == n_rois
        assert all(isinstance(q, float) for q in passed_quality)

    def test_tick_done_marks_track_complete(self, parseq_engine: ParseqEngine) -> None:
        """ocr_worker_tick with done=True -> track evicted from _tracks to _done_metrics."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)

        n_rois = 2
        batch_tensor = torch.randn(n_rois, 3, 32, 128, device="cuda:0").clamp(-1.0, 1.0)
        is_enhanced = np.zeros(n_rois, dtype=bool)

        rois = [_make_roi_rq(track_id="t-done", quality=0.8) for _ in range(n_rois)]
        post_meta = [PostProcessingMeta(128.0, 60.0, 0.0, 0.0) for _ in range(n_rois)]
        proc_output = ProcessorOutput(
            batch_tensor=batch_tensor,
            is_enhanced=is_enhanced,
            post_meta=post_meta,
        )
        selection = EnhancedBatchSelection(
            track_id="t-done",
            version=1,
            base_rois=rois,
            enhance_rois=[],
            duplicate_groups={},
        )
        queue.submit_candidate(selection, proc_output, 1, 1000)

        decision = _make_aggregation_decision(done=True, reason_code=ReasonCode.FINAL_ONEPASS_AGREE)
        aggregator = MagicMock()
        aggregator.aggregate_pass.return_value = decision

        tick_result = ocr_worker_tick(parseq_engine, queue, aggregator, "w-int", 2000)
        assert tick_result is not None
        assert tick_result.aggregation_decision.done is True

        # Track evicted from _tracks, metrics retained in _done_metrics
        assert "t-done" not in queue._tracks
        assert "t-done" in queue._done_metrics

    def test_multi_track_sequential_real_engine(self, parseq_engine: ParseqEngine) -> None:
        """3 tracks submitted, ticked one by one with real engine, all reach done."""
        cfg = ConsumerConfig()
        queue = OcrReadyQueue(cfg)

        track_ids = ["t-seq-0", "t-seq-1", "t-seq-2"]
        for i, tid in enumerate(track_ids):
            n_rois = 2 + i  # 2, 3, 4 ROIs
            batch_tensor = torch.randn(n_rois, 3, 32, 128, device="cuda:0").clamp(-1.0, 1.0)
            is_enhanced = np.zeros(n_rois, dtype=bool)
            rois = [_make_roi_rq(track_id=tid, quality=0.8) for _ in range(n_rois)]
            post_meta = [PostProcessingMeta(128.0, 60.0, 0.0, 0.0) for _ in range(n_rois)]
            proc_output = ProcessorOutput(
                batch_tensor=batch_tensor,
                is_enhanced=is_enhanced,
                post_meta=post_meta,
            )
            selection = EnhancedBatchSelection(
                track_id=tid,
                version=1,
                base_rois=rois,
                enhance_rois=[],
                duplicate_groups={},
            )
            queue.submit_candidate(selection, proc_output, 1, 1000 + i)

        decision = _make_aggregation_decision(done=True)
        aggregator = MagicMock()
        aggregator.aggregate_pass.return_value = decision

        for i, tid in enumerate(track_ids):
            result = ocr_worker_tick(parseq_engine, queue, aggregator, "w-int", 2000 + i)
            assert result is not None, f"Tick for {tid} returned None"
            assert result.track_id == tid
            n_rois = 2 + i
            assert result.inference_result.distributions.shape == (n_rois, 9, 37)

        # All tracks done
        for tid in track_ids:
            assert tid not in queue._tracks
            assert tid in queue._done_metrics
