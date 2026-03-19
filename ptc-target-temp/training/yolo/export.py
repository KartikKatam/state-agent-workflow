"""YOLO model export to ONNX and TensorRT formats.

Exports trained YOLO weights to:
- ONNX: FP32, fixed 640x640, opset 17, simplified
- TensorRT: FP16, GPU-specific engine (optional, requires tensorrt package)

Validates exported models with dummy inference.
"""

from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path

import numpy as np
from ultralytics import YOLO  # pyright: ignore[reportPrivateImportUsage]

from training.yolo.config import YoloTrainingConfig

logger = logging.getLogger(__name__)


def _check_tensorrt_available() -> bool:
    """Check if the tensorrt package is importable."""
    try:
        import tensorrt  # noqa: F401

        return True
    except (ImportError, TypeError):
        return False


def export_model(
    weights_path: Path,
    cfg: YoloTrainingConfig,
    *,
    models_dir: Path | None = None,
) -> dict[str, Path | None]:
    """Export trained YOLO model to ONNX and optionally TensorRT.

    Args:
        weights_path: Path to trained .pt weights file.
        cfg: Training config with export settings (imgsz, export_onnx,
            export_trt, export_half).
        models_dir: Directory for exported model copies. Defaults to models/.

    Returns:
        Dict with 'onnx' and 'trt' keys pointing to exported file paths
        (None if skipped).
    """
    if models_dir is None:
        models_dir = Path("models")
    models_dir.mkdir(parents=True, exist_ok=True)

    model = YOLO(str(weights_path))
    results: dict[str, Path | None] = {"onnx": None, "trt": None}

    # --- ONNX export ---
    if cfg.export_onnx:
        logger.info("Exporting to ONNX (FP32, %dx%d, opset 17)...", cfg.imgsz, cfg.imgsz)
        onnx_path_str = model.export(
            format="onnx",
            imgsz=cfg.imgsz,
            dynamic=False,
            simplify=True,
            half=False,
            opset=17,
        )
        onnx_path = Path(onnx_path_str)
        dest = models_dir / "yolov11m-lpr.onnx"
        shutil.copy2(str(onnx_path), str(dest))
        size_mb = onnx_path.stat().st_size / (1024 * 1024)
        logger.info("ONNX export complete: %s (%.1f MB)", dest, size_mb)
        results["onnx"] = dest

        # Validate ONNX with dummy inference
        _validate_exported_model(onnx_path_str, cfg.imgsz)

    # --- TensorRT export ---
    if cfg.export_trt:
        if not _check_tensorrt_available():
            logger.warning(
                "tensorrt package not installed — skipping TRT export. "
                "Install with: pip install tensorrt"
            )
        else:
            import torch

            logger.info("Exporting to TensorRT (FP16, %dx%d)...", cfg.imgsz, cfg.imgsz)
            engine_path_str = model.export(
                format="engine",
                imgsz=cfg.imgsz,
                half=cfg.export_half,
                device=0,
            )
            engine_path = Path(engine_path_str)
            dest = models_dir / "yolov11m-lpr.engine"
            shutil.copy2(str(engine_path), str(dest))
            size_mb = engine_path.stat().st_size / (1024 * 1024)
            logger.info("TRT export complete: %s (%.1f MB)", dest, size_mb)
            results["trt"] = dest

            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                logger.warning(
                    "TRT engine is tied to GPU architecture: %s. "
                    "Rebuild if deploying on a different GPU.",
                    gpu_name,
                )

            # Validate TRT with dummy inference
            _validate_exported_model(engine_path_str, cfg.imgsz)

    # Print deployment instructions
    print("\n--- Deployment ---")
    if results["onnx"]:
        print(f"  ONNX model: {results['onnx']}")
    if results["trt"]:
        print(f"  TRT engine: {results['trt']}")
    if not results["onnx"] and not results["trt"]:
        print("  No models exported.")
    print("  Update producer/config.py with the appropriate model path.")

    return results


def _validate_exported_model(model_path: str, imgsz: int) -> None:
    """Validate an exported model with dummy inference."""
    logger.info("Validating exported model: %s", model_path)
    validation_model = YOLO(model_path)
    dummy_image = np.random.randint(0, 255, (imgsz, imgsz, 3), dtype=np.uint8)
    validation_model(dummy_image, verbose=False)
    logger.info("Validation passed for %s", model_path)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for model export."""
    parser = argparse.ArgumentParser(
        description="Export trained YOLO model to ONNX and/or TensorRT."
    )
    parser.add_argument("--weights", type=str, required=True, help="Path to trained .pt weights")
    parser.add_argument("--imgsz", type=int, default=640, help="Export image size (default: 640)")
    parser.add_argument(
        "--half", action="store_true", default=True, help="Use FP16 for TRT (default: True)"
    )
    parser.add_argument("--no-trt", action="store_true", default=False, help="Skip TensorRT export")
    parser.add_argument("--no-onnx", action="store_true", default=False, help="Skip ONNX export")
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint for model export."""
    args = parse_args()

    cfg = YoloTrainingConfig(
        imgsz=args.imgsz,
        export_onnx=not args.no_onnx,
        export_trt=not args.no_trt,
        export_half=args.half,
    )

    weights_path = Path(args.weights)
    if not weights_path.exists():
        raise SystemExit(f"Weights file not found: {weights_path}")

    export_model(weights_path, cfg)


if __name__ == "__main__":
    main()
