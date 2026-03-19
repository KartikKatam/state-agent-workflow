#!/usr/bin/env python3
"""Export PARSeq model to TensorRT FP16 engine with vocabulary extraction.

Usage:
    python scripts/export_parseq_trt.py --checkpoint models/parseq.pt

Prerequisites:
    bash scripts/setup_parseq.sh  (one-time PARSeq install + weight download)

No hub downloads — all loading via local strhub package.
"""

from __future__ import annotations

import argparse
import logging
import warnings
from pathlib import Path

import numpy as np
import torch

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def load_parseq_model(checkpoint_path: str) -> torch.nn.Module:
    """Load PARSeq from local strhub install with local weights.

    Sets NAR mode (C1): decode_ar=False, refine_iters=0 — single forward
    pass, no autoregressive decode loop (which would break ONNX tracing).

    No hub downloads.

    Raises:
        FileNotFoundError: Checkpoint file missing.
        ImportError: strhub package not installed.
    """
    checkpoint = Path(checkpoint_path)
    if not checkpoint.exists():
        raise FileNotFoundError(
            f"Checkpoint not found: {checkpoint_path}\nRun: bash scripts/setup_parseq.sh"
        )

    # Create PARSeq model via hubconf config + direct constructor (no remote downloads).
    try:
        import sys

        vendor_dir = str(Path(__file__).resolve().parent.parent / "vendor" / "parseq")
        if vendor_dir not in sys.path:
            sys.path.insert(0, vendor_dir)
        from hubconf import parseq as _parseq_factory
    except ImportError:
        raise ImportError("strhub package not found.\nRun: bash scripts/setup_parseq.sh") from None

    # Architecture only — no pretrained download
    model = _parseq_factory(pretrained=False)

    # Load weights: try weights_only=True first (PyTorch 2.6+ safe),
    # fall back to weights_only=False for older checkpoint formats (H2).
    try:
        state_dict = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    except Exception:
        warnings.warn(
            "Loading with weights_only=False (file is SHA-verified).",
            stacklevel=2,
        )
        state_dict = torch.load(checkpoint_path, map_location="cpu", weights_only=False)

    # Handle Lightning checkpoint format (dict with 'state_dict' key)
    if isinstance(state_dict, dict) and "state_dict" in state_dict:
        state_dict = state_dict["state_dict"]

    model.load_state_dict(state_dict)

    # NAR mode (C1): single forward pass, no AR decode loop
    model.decode_ar = False
    model.refine_iters = 0

    model.eval()
    model.cuda()

    param_count = sum(p.numel() for p in model.parameters())
    logger.info("PARSeq loaded: %s params, NAR mode, device=cuda", f"{param_count:,}")
    return model


# ---------------------------------------------------------------------------
# Vocabulary extraction
# ---------------------------------------------------------------------------


def extract_vocab(model: torch.nn.Module, output_path: str) -> list[str]:
    """Extract frozen vocabulary from model tokenizer.

    Writes the first ``num_tokens - 2`` entries of ``_itos`` (C2).
    BOS (index 95) and PAD (index 96) are excluded — they are never in
    the model's output space.

    File format: one token per line.
      Line 0: EOS token ``[E]``
      Lines 1-94: charset characters in model vocabulary order.

    Returns:
        List of 95 token strings.
    """
    # PARSeq tokenizer has _itos: list of token strings
    # Layout: [EOS, *charset, BOS, PAD] — we take the first num_tokens-2
    tokenizer = model.tokenizer  # type: ignore[union-attr]
    full_itos: list[str] = list(tokenizer._itos)  # type: ignore[union-attr]

    # Exclude BOS and PAD (last 2 special tokens)
    num_output_classes = len(full_itos) - 2
    vocab = full_itos[:num_output_classes]

    # Sanity checks
    if vocab[0] not in ("[E]", "<eos>"):
        raise ValueError(
            f"Expected EOS token '[E]' or '<eos>' at index 0, got '{vocab[0]}'. "
            "Tokenizer layout may have changed."
        )
    if len(vocab) != 95:
        raise ValueError(
            f"Expected 95 vocab entries (charset + EOS), got {len(vocab)}. "
            f"Full _itos length: {len(full_itos)}"
        )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with open(output, "w") as f:
        for token in vocab:
            f.write(token + "\n")

    logger.info("Vocab extracted: %d entries -> %s", len(vocab), output_path)
    return vocab


# ---------------------------------------------------------------------------
# ONNX export
# ---------------------------------------------------------------------------


def export_to_onnx(
    model: torch.nn.Module,
    onnx_path: str,
    img_h: int,
    img_w: int,
) -> None:
    """Export PARSeq to ONNX with dynamic batch axis.

    C1: NAR mode (decode_ar=False, refine_iters=0) must be set before calling.
    Uses opset 17 for native LayerNormalization support — required for TRT FP16
    (opset 14 decomposes LayerNorm into Reduce/Pow which overflow in FP16).
    """
    dummy_input = torch.randn(1, 3, img_h, img_w, device="cuda")

    output = Path(onnx_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    torch.onnx.export(
        model,
        (dummy_input,),
        onnx_path,
        input_names=["images"],
        output_names=["logits"],
        dynamic_axes={
            "images": {0: "batch"},
            "logits": {0: "batch"},
        },
        opset_version=17,
        do_constant_folding=True,
    )

    onnx_size_mb = output.stat().st_size / (1024 * 1024)
    logger.info("ONNX exported: %.1f MB -> %s", onnx_size_mb, onnx_path)


# ---------------------------------------------------------------------------
# TensorRT engine build
# ---------------------------------------------------------------------------


def build_trt_engine(
    onnx_path: str,
    engine_path: str,
    img_h: int,
    img_w: int,
    min_batch: int,
    opt_batch: int,
    max_batch: int,
    fp16: bool,
) -> None:
    """Build TensorRT engine from ONNX with dynamic batch profile.

    Uses the tensorrt Python API (not trtexec CLI).
    Default profile: min=1, opt=8, max=12.
    """
    import tensorrt as trt

    trt_logger = trt.Logger(trt.Logger.WARNING)
    builder = trt.Builder(trt_logger)
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
    parser = trt.OnnxParser(network, trt_logger)

    # Parse ONNX model
    with open(onnx_path, "rb") as f:
        if not parser.parse(f.read()):
            for i in range(parser.num_errors):
                logger.error("ONNX parse error: %s", parser.get_error(i))
            raise RuntimeError(f"Failed to parse ONNX model: {onnx_path}")

    # Builder config
    config = builder.create_builder_config()
    if fp16:
        config.set_flag(trt.BuilderFlag.FP16)
        logger.info("FP16 enabled")

    # Dynamic batch optimization profile
    profile = builder.create_optimization_profile()
    profile.set_shape(
        "images",
        (min_batch, 3, img_h, img_w),  # min
        (opt_batch, 3, img_h, img_w),  # opt
        (max_batch, 3, img_h, img_w),  # max
    )
    config.add_optimization_profile(profile)

    logger.info(
        "Building TRT engine: batch min=%d opt=%d max=%d, img=%dx%d%s",
        min_batch,
        opt_batch,
        max_batch,
        img_h,
        img_w,
        " FP16" if fp16 else "",
    )

    # Build serialized engine
    serialized = builder.build_serialized_network(network, config)
    if serialized is None:
        raise RuntimeError("TRT engine build failed (build_serialized_network returned None)")

    output = Path(engine_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with open(engine_path, "wb") as f:
        f.write(serialized)

    engine_size_mb = output.stat().st_size / (1024 * 1024)
    logger.info("TRT engine built: %.1f MB -> %s", engine_size_mb, engine_path)


# ---------------------------------------------------------------------------
# Engine verification
# ---------------------------------------------------------------------------


def verify_engine(
    engine_path: str,
    vocab_path: str,
    img_h: int,
    img_w: int,
) -> None:
    """Verify TRT engine produces valid, input-sensitive output.

    Checks:
        - Output shape is (batch, max_T, vocab_size)
        - vocab_size matches vocab file line count
        - No NaN/Inf in output
        - Both batch=1 and batch=12 (max profile) work
        - Different inputs produce different outputs (not constant-folded)
    """
    import tensorrt as trt

    trt_logger = trt.Logger(trt.Logger.WARNING)
    runtime = trt.Runtime(trt_logger)

    with open(engine_path, "rb") as f:
        engine = runtime.deserialize_cuda_engine(f.read())
    if engine is None:
        raise RuntimeError(f"Failed to deserialize TRT engine: {engine_path}")

    context = engine.create_execution_context()

    # Read vocab for cross-validation
    with open(vocab_path) as f:
        vocab_lines = [line.strip() for line in f if line.strip()]
    vocab_size = len(vocab_lines)

    def _run_inference(input_tensor: torch.Tensor) -> np.ndarray:
        """Run inference on a given input tensor and return output as numpy."""
        input_shape = tuple(input_tensor.shape)

        # Set dynamic input shape and get resulting output shape
        context.set_input_shape("images", input_shape)
        output_shape = tuple(context.get_tensor_shape("logits"))
        output_tensor = torch.empty(output_shape, device="cuda", dtype=torch.float32)

        # Bind tensor addresses
        context.set_tensor_address("images", input_tensor.data_ptr())
        context.set_tensor_address("logits", output_tensor.data_ptr())

        # Execute
        stream = torch.cuda.current_stream()
        context.execute_async_v3(stream_handle=stream.cuda_stream)
        stream.synchronize()

        return output_tensor.cpu().numpy()

    # --- Verify batch=1 ---
    zeros_input = torch.zeros(1, 3, img_h, img_w, device="cuda", dtype=torch.float32)
    output_1 = _run_inference(zeros_input)
    _, max_t, out_vocab = output_1.shape

    logger.info("Engine output shape (batch=1): %s", output_1.shape)
    logger.info("  max_T=%d, vocab_size=%d", max_t, out_vocab)

    # Cross-validate vocab_size with vocab file
    if out_vocab != vocab_size:
        raise ValueError(
            f"Vocab size mismatch: engine outputs {out_vocab} classes, "
            f"vocab file has {vocab_size} entries"
        )
    logger.info("  Vocab size match: %d == %d ✓", out_vocab, vocab_size)

    # NaN/Inf check
    if np.isnan(output_1).any():
        raise ValueError("Engine output contains NaN (batch=1)")
    if np.isinf(output_1).any():
        raise ValueError("Engine output contains Inf (batch=1)")
    logger.info("  No NaN/Inf (batch=1) ✓")

    # --- Verify batch=12 (max profile) ---
    max_input = torch.zeros(12, 3, img_h, img_w, device="cuda", dtype=torch.float32)
    output_12 = _run_inference(max_input)
    logger.info("Engine output shape (batch=12): %s", output_12.shape)

    if np.isnan(output_12).any():
        raise ValueError("Engine output contains NaN (batch=12)")
    if np.isinf(output_12).any():
        raise ValueError("Engine output contains Inf (batch=12)")
    logger.info("  No NaN/Inf (batch=12) ✓")

    # --- Verify input sensitivity (different inputs -> different outputs) ---
    random_input = torch.randn(1, 3, img_h, img_w, device="cuda", dtype=torch.float32)
    output_rand = _run_inference(random_input)
    max_diff = float(np.abs(output_1 - output_rand).max())

    if max_diff < 1e-4:
        raise ValueError(
            f"Engine appears to ignore input: zeros vs random max_diff={max_diff:.2e}. "
            "This usually means FP16 LayerNorm overflow — re-export with opset >= 17."
        )
    logger.info("  Input sensitivity verified: zeros vs random max_diff=%.3f ✓", max_diff)

    logger.info("Engine verification passed!")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Export PARSeq to TensorRT FP16 engine with vocab extraction.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python scripts/export_parseq_trt.py --checkpoint models/parseq.pt\n"
            "\n"
            "Prerequisites:\n"
            "  bash scripts/setup_parseq.sh  (one-time PARSeq install)\n"
        ),
    )
    parser.add_argument(
        "--checkpoint",
        required=True,
        help="Path to local PARSeq .pt checkpoint (REQUIRED)",
    )
    parser.add_argument(
        "--output-dir",
        default="models",
        help="Output directory for engine and vocab (default: models)",
    )
    parser.add_argument("--img-height", type=int, default=32, help="Image height (default: 32)")
    parser.add_argument("--img-width", type=int, default=128, help="Image width (default: 128)")
    parser.add_argument("--min-batch", type=int, default=1, help="Min batch size (default: 1)")
    parser.add_argument("--opt-batch", type=int, default=8, help="Optimal batch size (default: 8)")
    parser.add_argument("--max-batch", type=int, default=12, help="Max batch size (default: 12)")
    parser.add_argument("--no-fp16", action="store_true", help="Disable FP16 precision")
    parser.add_argument("--no-verify", action="store_true", help="Skip engine verification")
    return parser.parse_args()


def main() -> None:
    """Orchestrate the full export pipeline."""
    args = parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    output_dir = Path(args.output_dir)
    onnx_path = str(output_dir / "parseq.onnx")
    engine_path = str(output_dir / "parseq.engine")
    vocab_path = str(output_dir / "parseq_vocab.txt")

    # Step 1: Load model
    logger.info("=== Step 1/5: Load PARSeq ===")
    model = load_parseq_model(args.checkpoint)

    # Step 2: Extract vocab
    logger.info("=== Step 2/5: Extract vocabulary ===")
    extract_vocab(model, vocab_path)

    # Step 3: Export to ONNX
    logger.info("=== Step 3/5: Export to ONNX ===")
    export_to_onnx(model, onnx_path, args.img_height, args.img_width)

    # Step 4: Build TRT engine
    logger.info("=== Step 4/5: Build TRT engine ===")
    build_trt_engine(
        onnx_path,
        engine_path,
        args.img_height,
        args.img_width,
        args.min_batch,
        args.opt_batch,
        args.max_batch,
        fp16=not args.no_fp16,
    )

    # Step 5: Verify engine
    if not args.no_verify:
        logger.info("=== Step 5/5: Verify engine ===")
        verify_engine(engine_path, vocab_path, args.img_height, args.img_width)
    else:
        logger.info("=== Step 5/5: Verification skipped (--no-verify) ===")

    logger.info("")
    logger.info("=== Export complete ===")
    logger.info("  Engine: %s", engine_path)
    logger.info("  Vocab:  %s", vocab_path)
    logger.info("  ONNX:   %s (intermediate, can be deleted)", onnx_path)


if __name__ == "__main__":
    main()
