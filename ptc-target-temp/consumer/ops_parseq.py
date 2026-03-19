"""PARSeq TRT OCR engine — config types, vocabulary mapping, and inference pipeline.

Chunk-01: ParseqInferenceResult, load_vocab, build_allowed_indices.
Chunk-04: OcrWorkerTickResult, ingest_and_infer, ocr_worker_tick.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import torch

if TYPE_CHECKING:
    from consumer.config import ConsumerConfig
    from consumer.models import AggregationDecision, OcrWorkItem
    from consumer.ops_ocr_aggregator import OcrAggregator
    from consumer.ops_ocr_queue import OcrReadyQueue

from producer.models import TrackId

logger = logging.getLogger(__name__)

# EOS tokens accepted by this module (backward compat: PARSeq v1.0.0 uses <eos>)
_EOS_TOKENS = ("<eos>", "[E]")


# ─── Chunk-01: Types ───


@dataclass(frozen=True)
class ParseqInferenceResult:
    """Immutable result from PARSeq TRT inference.

    Attributes:
        distributions: Softmax probability distributions, shape (N, max_seq_len, vocab_size).
        vocab_chars: Tuple of character labels in allowed-chars order with EOS appended.
        eos_index: Index of the EOS token in vocab_chars (last position).
        max_seq_len: Number of sequence positions (parseq_max_plate_chars + 1).
        inference_time_ms: Wall-clock time for the inference call.
    """

    distributions: np.ndarray  # (N, max_seq_len, vocab_size) float32
    vocab_chars: tuple[str, ...]  # length = vocab_size
    eos_index: int
    max_seq_len: int
    inference_time_ms: float


# ─── Chunk-01: Vocabulary loading ───


def load_vocab(vocab_path: str | Path) -> list[str]:
    """Load vocabulary file. One token per line, line 0 is EOS.

    Args:
        vocab_path: Path to the vocab text file.

    Returns:
        List of token strings in model order.

    Raises:
        FileNotFoundError: If the vocab file does not exist.
        ValueError: If the vocab file is empty after stripping.
    """
    path = Path(vocab_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Vocab file not found: {path}. Run scripts/export_parseq_trt.py to generate it."
        )

    tokens: list[str] = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped:
            tokens.append(stripped)

    if not tokens:
        raise ValueError(f"Vocab file is empty: {path}")

    logger.debug("loaded vocab with %d tokens from %s", len(tokens), path)
    return tokens


def build_allowed_indices(
    full_vocab: list[str],
    allowed_chars: str,
) -> tuple[torch.Tensor, tuple[str, ...], int]:
    """Build the allowed-character index mapping from full vocab to filtered subset.

    Maps each character in ``allowed_chars`` (in order) to its index in
    ``full_vocab``, then appends the EOS index.

    Args:
        full_vocab: Complete vocabulary list from :func:`load_vocab`.
        allowed_chars: Characters to allow (e.g. "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789").

    Returns:
        Tuple of:
            - ``allowed_indices``: ``torch.Tensor`` of shape ``(len(allowed_chars) + 1,)``,
              dtype ``torch.long``. Maps position -> full-vocab index.
            - ``vocab_chars``: Tuple of character labels in allowed order + EOS.
            - ``eos_index``: Position of EOS in the output (= ``len(allowed_chars)``).

    Raises:
        ValueError: If EOS token not found in vocab or a required char is missing.
    """
    # Build reverse map: token string -> index in full_vocab
    token_to_idx: dict[str, int] = {tok: i for i, tok in enumerate(full_vocab)}

    # Find EOS index — accept both <eos> and [E] for backward compat
    eos_vocab_idx: int | None = None
    eos_token_found: str | None = None
    for eos_candidate in _EOS_TOKENS:
        if eos_candidate in token_to_idx:
            eos_vocab_idx = token_to_idx[eos_candidate]
            eos_token_found = eos_candidate
            break

    if eos_vocab_idx is None:
        raise ValueError(
            f"EOS token not found in vocabulary. "
            f"Expected one of {_EOS_TOKENS}, got tokens: {full_vocab[:5]}..."
        )

    # Map each allowed char to its full-vocab index
    indices: list[int] = []
    chars_out: list[str] = []
    for ch in allowed_chars:
        if ch not in token_to_idx:
            raise ValueError(
                f"Character '{ch}' not found in vocabulary. "
                f"Vocab has {len(full_vocab)} tokens. "
                f"First 10: {full_vocab[:10]}"
            )
        indices.append(token_to_idx[ch])
        chars_out.append(ch)

    # Append EOS at the end
    indices.append(eos_vocab_idx)
    chars_out.append(eos_token_found)  # type: ignore[arg-type]

    eos_index = len(allowed_chars)  # last position

    logger.debug(
        "built allowed indices: %d chars + EOS (token=%s, vocab_idx=%d)",
        len(allowed_chars),
        eos_token_found,
        eos_vocab_idx,
    )

    return (
        torch.tensor(indices, dtype=torch.long),
        tuple(chars_out),
        eos_index,
    )


# ─── Chunk-02: ParseqEngine ───


class ParseqEngine:
    """TensorRT PARSeq OCR inference engine.

    Loads a TRT engine, warms it up, and runs batched inference producing
    calibrated probability distributions with vocabulary filtering, temperature
    scaling, and output truncation.
    """

    # --- Static helper methods (testable on CPU without engine) ---

    @staticmethod
    def normalize(
        batch: torch.Tensor,
        mean: torch.Tensor,
        std: torch.Tensor,
    ) -> torch.Tensor:
        """Remap [-1,1] preprocessor output to ImageNet-normalized [0,1] space.

        Steps: [-1,1] -> [0,1] via (x+1)/2, then (x-mean)/std.
        """
        x = (batch + 1.0) / 2.0
        return (x - mean) / std

    @staticmethod
    def bgr_to_rgb(batch: torch.Tensor) -> torch.Tensor:
        """Flip BGR channels to RGB via [:, [2,1,0], :, :]."""
        return batch[:, [2, 1, 0], :, :]

    @staticmethod
    def vocab_filter(
        logits: torch.Tensor,
        allowed_indices: torch.Tensor,
    ) -> torch.Tensor:
        """Gather logits at allowed vocabulary indices."""
        return logits[:, :, allowed_indices]

    @staticmethod
    def temperature_scale_and_softmax(
        logits: torch.Tensor,
        temperature: float,
    ) -> torch.Tensor:
        """Apply temperature scaling then softmax along vocab dimension."""
        return torch.softmax(logits / temperature, dim=-1)

    @staticmethod
    def truncate(distributions: torch.Tensor, max_seq_len: int) -> torch.Tensor:
        """Truncate to max_seq_len positions via [:, :max_seq_len, :]."""
        return distributions[:, :max_seq_len, :]

    @staticmethod
    def has_nan_inf(tensor: torch.Tensor) -> bool:
        """Check if tensor contains NaN or Inf values."""
        return bool(torch.isnan(tensor).any() or torch.isinf(tensor).any())

    # --- Instance methods ---

    def __init__(self, cfg: ConsumerConfig) -> None:
        self.logger = logging.getLogger(f"{__name__}.ParseqEngine")

        # Validate CUDA
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is not available — ParseqEngine requires GPU")

        self._device = torch.device(cfg.parseq_device)

        # Derive .engine path from parseq_model_path
        model_path = Path(cfg.parseq_model_path)
        engine_path = model_path.with_suffix(".engine")

        if not engine_path.exists():
            raise FileNotFoundError(
                f"TRT engine not found: {engine_path}\n\n"
                f"Run scripts/export_parseq_trt.py to generate it."
            )

        # Load TRT engine (follows producer/ops_corner_cnn.py pattern)
        import tensorrt as trt

        trt_logger = trt.Logger(trt.Logger.WARNING)
        with open(engine_path, "rb") as f:
            runtime = trt.Runtime(trt_logger)
            self._engine = runtime.deserialize_cuda_engine(f.read())
        self._context = self._engine.create_execution_context()

        engine_size_mb = engine_path.stat().st_size / (1024**2)
        self.logger.info("PARSeq TRT engine loaded (%.1f MB) from %s", engine_size_mb, engine_path)

        # Load vocab and build index map
        full_vocab = load_vocab(cfg.parseq_vocab_path)
        self._allowed_indices, self._vocab_chars, self._eos_index = build_allowed_indices(
            full_vocab, cfg.parseq_allowed_chars
        )
        self._allowed_indices_gpu = self._allowed_indices.to(self._device)

        # Query engine output shape for cross-validation (C6)
        for i in range(self._engine.num_io_tensors):
            name = self._engine.get_tensor_name(i)
            if self._engine.get_tensor_mode(name) == trt.TensorIOMode.OUTPUT:
                out_shape = self._engine.get_tensor_shape(name)
                # Dynamic batch = -1, e.g. (-1, 26, 95)
                self._native_max_T: int = out_shape[1]
                self._full_vocab_size: int = out_shape[2]
                break

        # C6: Cross-validate vocab size from engine vs vocab file
        if len(full_vocab) != self._full_vocab_size:
            raise ValueError(
                f"Vocab size mismatch: vocab file has {len(full_vocab)} tokens "
                f"but TRT engine output has {self._full_vocab_size} classes. "
                f"Re-export the engine or fix the vocab file."
            )

        # Pre-compute normalization tensors
        self._do_normalize = cfg.parseq_normalize_input
        self._norm_mean = torch.tensor(cfg.parseq_norm_mean, device=self._device).view(1, 3, 1, 1)
        self._norm_std = torch.tensor(cfg.parseq_norm_std, device=self._device).view(1, 3, 1, 1)

        # Fixed image dimensions (C5: hardcoded by PARSeq architecture)
        self._img_h: int = 32
        self._img_w: int = 128

        # Store config values
        self._temperature = cfg.parseq_temperature
        self._max_batch_size = cfg.parseq_max_batch_size
        self._max_seq_len = cfg.parseq_max_plate_chars + 1  # +1 for EOS position

        # Pre-allocate output buffer at max batch size (M5)
        self._output_buf = torch.zeros(
            (self._max_batch_size, self._native_max_T, self._full_vocab_size),
            dtype=torch.float32,
            device=self._device,
        )

        # Warmup
        self._warmup(cfg.parseq_warmup_runs)

    def _warmup(self, warmup_runs: int) -> None:
        """Opportunistic warmup: run inference with zeros tensor. Log warning on failure."""
        dummy = torch.zeros(
            (1, 3, self._img_h, self._img_w),
            dtype=torch.float32,
            device=self._device,
        )
        for i in range(warmup_runs):
            try:
                self.run(dummy)
            except Exception:
                self.logger.warning("warmup run %d/%d failed", i + 1, warmup_runs, exc_info=True)
        self.logger.info("PARSeq warmup complete (%d runs)", warmup_runs)

    def run(self, batch_tensor: torch.Tensor) -> ParseqInferenceResult | None:
        """Run batched inference. Returns None on any exception (H11)."""
        try:
            return self._run_impl(batch_tensor)
        except Exception:
            self.logger.error("ParseqEngine.run failed", exc_info=True)
            return None

    def _run_impl(self, batch_tensor: torch.Tensor) -> ParseqInferenceResult | None:
        """12-step inference pipeline."""
        t0 = time.perf_counter()

        # Step 1: Validate input
        N = batch_tensor.shape[0]
        if N == 0:
            return None  # H7: empty batch

        if N > self._max_batch_size:
            self.logger.warning("batch size %d exceeds max %d", N, self._max_batch_size)
            return None

        if batch_tensor.ndim != 4:
            raise ValueError(f"Expected 4D tensor, got {batch_tensor.ndim}D")
        if (
            batch_tensor.shape[1] != 3
            or batch_tensor.shape[2] != self._img_h
            or batch_tensor.shape[3] != self._img_w
        ):
            raise ValueError(
                f"Expected shape (N, 3, {self._img_h}, {self._img_w}), got {tuple(batch_tensor.shape)}"
            )
        if not batch_tensor.is_cuda:
            raise ValueError("Input tensor must be on CUDA device")

        # Step 2: BGR -> RGB channel flip (H8)
        x = self.bgr_to_rgb(batch_tensor)

        # Step 3: Re-normalize if enabled ([-1,1] -> [0,1] -> ImageNet)
        if self._do_normalize:
            x = self.normalize(x, self._norm_mean, self._norm_std)

        # Ensure contiguous memory layout for TRT (chunk-00 discovery)
        x = x.contiguous()

        # Step 4: TRT forward pass with fresh CUDA stream (M4)
        stream = torch.cuda.current_stream(self._device)
        output_slice = self._output_buf[:N]  # M5: use pre-allocated buffer slice

        self._context.set_input_shape("images", (N, 3, self._img_h, self._img_w))
        self._context.set_tensor_address("images", x.data_ptr())
        self._context.set_tensor_address("logits", output_slice.data_ptr())
        self._context.execute_async_v3(stream.cuda_stream)
        stream.synchronize()

        raw_logits = output_slice.clone()  # Clone so we don't hold reference to shared buffer

        # Step 5: NaN/Inf check
        if self.has_nan_inf(raw_logits):
            self.logger.error("NaN or Inf detected in TRT output")
            return None

        # Step 6: Logit dynamic range check (M6)
        logit_range = raw_logits.max() - raw_logits.min()
        if logit_range < 1e-6:
            self.logger.warning(
                "logit dynamic range %.2e < 1e-6 — possible corrupted engine or black input",
                logit_range.item(),
            )

        # Step 7: Vocab filter
        filtered = self.vocab_filter(raw_logits, self._allowed_indices_gpu)

        # Step 8 + 9: Temperature scale + softmax
        probs = self.temperature_scale_and_softmax(filtered, self._temperature)

        # Step 10: Truncate to max_seq_len positions
        truncated = self.truncate(probs, self._max_seq_len)

        # Step 11: CPU transfer
        distributions = truncated.cpu().numpy()

        inference_ms = (time.perf_counter() - t0) * 1000.0

        # Step 12: Package result
        return ParseqInferenceResult(
            distributions=distributions,
            vocab_chars=self._vocab_chars,
            eos_index=self._eos_index,
            max_seq_len=self._max_seq_len,
            inference_time_ms=inference_ms,
        )


# ─── Chunk-04 types ───


@dataclass(frozen=True)
class OcrWorkerTickResult:
    """Result of a single ocr_worker_tick cycle.

    Carries the inference result and aggregation decision for a single track.
    """

    track_id: TrackId
    commit_version: int
    inference_result: ParseqInferenceResult
    aggregation_decision: AggregationDecision
    tick_time_ms: float


# ─── Chunk-04 functions ───


def ingest_and_infer(
    engine: Any,
    queue: OcrReadyQueue,
    worker_id: str,
    timestamp_ms: int,
) -> tuple[OcrWorkItem, ParseqInferenceResult] | None:
    """Pull next work item and run inference.

    Returns (work_item, result) on success, None on empty queue or failure.
    On inference failure, calls queue.fail_commit with reason
    'parseq_inference_returned_none'.
    """
    work_item = queue.pull_next(worker_id, timestamp_ms)
    if work_item is None:
        return None

    result = engine.run(work_item.batch_tensor)
    if result is None:
        logger.error(
            "inference failed for track_id=%s commit_version=%d",
            work_item.track_id,
            work_item.commit_version,
        )
        queue.fail_commit(
            work_item.track_id,
            work_item.commit_version,
            reason="parseq_inference_returned_none",
        )
        return None

    return work_item, result


def ocr_worker_tick(
    engine: Any,
    queue: OcrReadyQueue,
    aggregator: OcrAggregator,
    worker_id: str,
    timestamp_ms: int,
) -> OcrWorkerTickResult | None:
    """Full OCR worker cycle: pull -> infer -> aggregate -> commit -> done.

    Every code path calls finish_commit, fail_commit, or returns None.
    No orphaned in-flight records.
    """
    t0 = time.perf_counter()

    # Step 1: Pull and infer
    pair = ingest_and_infer(engine, queue, worker_id, timestamp_ms)
    if pair is None:
        return None

    work_item, result = pair

    # Step 2: Aggregate — wrapped in try/except for error recovery
    try:
        decision = aggregator.aggregate_pass(
            work_item.track_id,
            result.distributions,
            work_item.duplicate_groups,
            quality_scores=work_item.per_roi_quality,
            eos_index=result.eos_index,
        )
    except Exception:
        logger.error(
            "aggregation failed for track_id=%s commit_version=%d",
            work_item.track_id,
            work_item.commit_version,
            exc_info=True,
        )
        queue.fail_commit(
            work_item.track_id,
            work_item.commit_version,
            reason="aggregation_exception",
        )
        return None

    # Step 3: Finish commit — always called on successful aggregation
    queue.finish_commit(work_item.track_id, work_item.commit_version)

    # Step 4: Mark done if aggregation says so
    if decision.done:
        queue.mark_done(
            work_item.track_id,
            reason=decision.reason_code.value,
            timestamp_ms=timestamp_ms,
        )

    tick_ms = (time.perf_counter() - t0) * 1000.0

    # H12: Log confidence_bucket (not raw_confidence)
    logger.info(
        "tick complete track_id=%s commit_version=%d done=%s "
        "confidence_bucket=%s reason_code=%s tick_ms=%.1f",
        work_item.track_id,
        work_item.commit_version,
        decision.done,
        decision.confidence_bucket.value,
        decision.reason_code.value,
        tick_ms,
    )

    return OcrWorkerTickResult(
        track_id=work_item.track_id,
        commit_version=work_item.commit_version,
        inference_result=result,
        aggregation_decision=decision,
        tick_time_ms=tick_ms,
    )
