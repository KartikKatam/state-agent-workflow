"""Corner CNN predictor: heatmap-based plate corner localization from crops.

CNN Interface Contract
─────────────────────
Architecture : ResNet-18 + UNet-lite decoder
Input        : (1, 3, 80, 256) NCHW float32 BGR, letterbox-resized crop
Output       : (1, 12, 20, 64) float32
  - Channels 0–3  : per-corner heatmaps (TL, TR, BR, BL)
  - Channels 4–11 : per-corner substride offsets (dx0, dy0, dx1, dy1, …)

Corner extraction (per corner i):
  1. argmax(heatmap[i]) → (gy, gx) on the 20×64 grid
  2. offset = output[4+2*i : 6+2*i, gy, gx] → (dx, dy)
  3. corner_letterbox = (gx * stride + dx, gy * stride + dy)   where stride = 4
  4. confidence = heatmap[i, gy, gx]

Confidence gating:
  If min(confidences) < threshold → return None for that crop.

Corner order: TL(0), TR(1), BR(2), BL(3) — natural reading order.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .config import ProducerConfig
from .models import RoiImage

logger = logging.getLogger(__name__)

_STRIDE = 4  # Spatial stride: input 80×256 → output 20×64
_PAD_VALUE = (114, 114, 114)  # Gray padding for letterbox


@dataclass
class CornerCnnOutput:
    """CNN prediction result for a single crop.

    Attributes:
        corners: 4 corner coordinates in crop-pixel space [TL, TR, BR, BL],
                 or None when confidence is below threshold.
        corner_confidences: Per-corner confidence scores [0, 1], or None.
    """

    corners: list[tuple[float, float]] | None
    corner_confidences: list[float] | None


class CornerCnnPredictor:
    """TensorRT FP16 corner CNN with letterbox preprocessing.

    Predicts 4 plate corners from cropped images via heatmap regression.
    Operates on a disposable letterboxed copy — the original crop is never modified.
    """

    def __init__(
        self,
        cfg: ProducerConfig,
        log: logging.Logger | None = None,
    ) -> None:
        self.cfg = cfg
        self.logger = log or logger
        self._confidence_threshold = cfg.corner_cnn_confidence_threshold
        self._input_h = cfg.corner_cnn_input_h
        self._input_w = cfg.corner_cnn_input_w
        self._model: Any = self._load_model()
        self._warmup()

    def _load_model(self) -> Any:
        """Load TensorRT FP16 engine.

        Follows ops_detection.py pattern: derive engine path from config,
        FileNotFoundError if missing, RuntimeError on load failure, log engine size.
        """
        engine_path = Path(self.cfg.corner_cnn_model_path)

        if not engine_path.exists():
            raise FileNotFoundError(
                f"Corner CNN engine not found: {engine_path}\n\n"
                f"Please build the engine first or set corner_cnn_enabled=False."
            )

        self.logger.info("Loading corner CNN engine: %s", engine_path)

        try:
            import tensorrt as trt

            trt_logger = trt.Logger(trt.Logger.WARNING)
            with open(engine_path, "rb") as f:
                runtime = trt.Runtime(trt_logger)
                engine = runtime.deserialize_cuda_engine(f.read())
            context = engine.create_execution_context()
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load corner CNN engine from {engine_path}. Error: {exc}"
            ) from exc

        engine_size_mb = engine_path.stat().st_size / (1024**2)
        self.logger.info("Corner CNN engine loaded (%.1f MB)", engine_size_mb)

        return context

    def _warmup(self) -> None:
        """Run warmup inference to initialize CUDA kernels.

        Follows ops_detection.py warmup pattern: dummy input, opportunistic failure.
        """
        dummy = np.zeros((self._input_h, self._input_w, 3), dtype=np.uint8)
        blob = self._preprocess(dummy)
        try:
            self._infer(blob)
            self.logger.debug("Corner CNN warmup completed")
        except Exception as exc:
            self.logger.warning("Corner CNN warmup failed: %s", exc)

    # ─── Preprocessing ───────────────────────────────────────────

    def _letterbox_crop(self, crop: np.ndarray) -> tuple[np.ndarray, float, float, float]:
        """Letterbox-resize crop preserving aspect ratio with gray padding.

        Args:
            crop: BGR uint8 image (H, W, 3).

        Returns:
            (letterboxed, scale, pad_x, pad_y) where letterboxed is (input_h, input_w, 3)
            and pad_x/pad_y are the float center-padding offsets.
        """
        h, w = crop.shape[:2]
        target_h, target_w = self._input_h, self._input_w

        scale = min(target_w / w, target_h / h)
        new_w = int(round(w * scale))
        new_h = int(round(h * scale))

        resized = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        pad_x = (target_w - new_w) / 2.0
        pad_y = (target_h - new_h) / 2.0

        top = int(round(pad_y - 0.1))
        bottom = int(round(pad_y + 0.1))
        left = int(round(pad_x - 0.1))
        right = int(round(pad_x + 0.1))

        letterboxed = cv2.copyMakeBorder(
            resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=_PAD_VALUE
        )

        return letterboxed, scale, pad_x, pad_y

    @staticmethod
    def _preprocess(img: np.ndarray) -> np.ndarray:
        """Convert HWC uint8 BGR → NCHW float32 [0, 1]."""
        blob = img.astype(np.float32) / 255.0
        blob = blob.transpose(2, 0, 1)  # HWC → CHW
        return blob[np.newaxis, ...]  # (1, 3, H, W)

    # ─── Postprocessing ──────────────────────────────────────────

    def _extract_corners_from_output(
        self, raw_output: np.ndarray
    ) -> tuple[list[tuple[float, float]], list[float]]:
        """Extract 4 corners from raw CNN output via argmax + substride offset.

        Args:
            raw_output: Shape (1, 12, H_out, W_out).
                Channels 0–3: heatmaps (TL, TR, BR, BL).
                Channels 4–11: per-corner substride offsets (dx_i, dy_i).

        Returns:
            (corners_letterbox, confidences) — 4 (x, y) in letterbox pixel coords
            and 4 confidence values.
        """
        corners: list[tuple[float, float]] = []
        confidences: list[float] = []
        w_out = raw_output.shape[3]

        for i in range(4):
            heatmap = raw_output[0, i]  # (H_out, W_out)
            flat_idx = int(np.argmax(heatmap))
            gy, gx = divmod(flat_idx, w_out)

            dx = float(raw_output[0, 4 + 2 * i, gy, gx])
            dy = float(raw_output[0, 4 + 2 * i + 1, gy, gx])

            x = gx * _STRIDE + dx
            y = gy * _STRIDE + dy
            conf = float(heatmap[gy, gx])

            corners.append((x, y))
            confidences.append(conf)

        return corners, confidences

    def _denormalize_corners(
        self,
        corners_lb: list[tuple[float, float]],
        scale: float,
        pad_x: float,
        pad_y: float,
        crop_h: int,
        crop_w: int,
    ) -> list[tuple[float, float]]:
        """Convert corners from letterbox space to original crop pixel coords.

        Inverse of the letterbox transform: subtract padding, divide by scale,
        clip to crop bounds.
        """
        result: list[tuple[float, float]] = []
        max_x = float(crop_w - 1)
        max_y = float(crop_h - 1)

        for x_lb, y_lb in corners_lb:
            x_crop = (x_lb - pad_x) / scale
            y_crop = (y_lb - pad_y) / scale
            x_crop = float(np.clip(x_crop, 0.0, max_x))
            y_crop = float(np.clip(y_crop, 0.0, max_y))
            result.append((x_crop, y_crop))

        return result

    # ─── Inference ───────────────────────────────────────────────

    def _infer(self, blob: np.ndarray) -> np.ndarray:
        """Run TRT inference on preprocessed input.

        Args:
            blob: NCHW float32 input, shape (1, 3, input_h, input_w).

        Returns:
            Raw CNN output, shape (1, 12, H_out, W_out).
        """
        import torch

        device = self.cfg.corner_cnn_device
        input_tensor = torch.from_numpy(blob).to(device)

        output_h = self._input_h // _STRIDE
        output_w = self._input_w // _STRIDE
        output_tensor = torch.empty((1, 12, output_h, output_w), dtype=torch.float32, device=device)

        self._model.set_tensor_address(
            self._model.engine.get_tensor_name(0), input_tensor.data_ptr()
        )
        self._model.set_tensor_address(
            self._model.engine.get_tensor_name(1), output_tensor.data_ptr()
        )
        self._model.execute_async_v3(torch.cuda.current_stream().cuda_stream)
        torch.cuda.synchronize()

        return output_tensor.cpu().numpy()

    # ─── Public API ──────────────────────────────────────────────

    def predict(self, crops: list[np.ndarray]) -> list[CornerCnnOutput]:
        """Predict plate corners for a list of crop images.

        Sequential loop over crops (batch=1):
        letterbox → normalize → TRT inference → extract corners →
        confidence gate → denormalize → CornerCnnOutput.

        Args:
            crops: List of BGR uint8 images (variable H×W×3).

        Returns:
            List of CornerCnnOutput, one per crop. Corners are in crop-pixel
            coords [TL, TR, BR, BL] or None if confidence too low.
        """
        results: list[CornerCnnOutput] = []

        for crop in crops:
            letterboxed, scale, pad_x, pad_y = self._letterbox_crop(crop)
            blob = self._preprocess(letterboxed)
            raw_output = self._infer(blob)

            corners_lb, confidences = self._extract_corners_from_output(raw_output)

            if min(confidences) < self._confidence_threshold:
                logger.debug(
                    "Corner CNN: min confidence %.3f < threshold %.3f, returning None",
                    min(confidences),
                    self._confidence_threshold,
                )
                results.append(CornerCnnOutput(corners=None, corner_confidences=None))
                continue

            crop_h, crop_w = crop.shape[:2]
            corners_crop = self._denormalize_corners(
                corners_lb, scale, pad_x, pad_y, crop_h, crop_w
            )

            results.append(CornerCnnOutput(corners=corners_crop, corner_confidences=confidences))

        return results


# ─── Public Entrypoint ──────────────────────────────────────────────


def predict_corners(
    rois: list[RoiImage],
    predictor: CornerCnnPredictor,
    cfg: ProducerConfig,
) -> list[RoiImage]:
    """Predict plate corners for ROIs and write keypoints in-place.

    Calls predictor.predict on crop images, writes corners to roi.keypoints
    and confidences to roi.keypoint_scores. On any exception, logs WARNING
    and returns rois unchanged (all keypoints remain None).

    Args:
        rois: List of RoiImage with crop_img populated.
        predictor: Initialized CornerCnnPredictor.
        cfg: Producer config (unused currently, reserved for future tuning).

    Returns:
        The same list of RoiImage, with keypoints/keypoint_scores populated
        where CNN succeeded.
    """
    if not rois:
        return rois

    try:
        crops = [roi.crop_img for roi in rois]
        outputs = predictor.predict(crops)

        for roi, output in zip(rois, outputs, strict=True):
            roi.keypoints = output.corners
            roi.keypoint_scores = output.corner_confidences

    except Exception:
        logger.warning("predict_corners failed: %s", _exc_summary(), exc_info=False)

    return rois


def _exc_summary() -> str:
    """One-line summary of the current exception (for log messages)."""
    import sys

    exc = sys.exc_info()[1]
    return f"{type(exc).__name__}: {exc}" if exc else "unknown"
