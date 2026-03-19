"""
Handles YOLOv11m-Pose TensorRT FP16 loading and detection on a time-based sampling cadence.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
from ultralytics import YOLO  # pyright: ignore[reportPrivateImportUsage]

from .config import ProducerConfig
from .models import Detection, DetectionTimings, DetectorOutput

# Pose keypoint remap (index order). Adjust this list to change order globally.
# Current model order: Top-Right, Top-Left, Bottom-Right, Bottom-Left.
KEYPOINT_REMAP = [0, 1, 2, 3]


class FrameSampler:
    """Time-based sampler to pull the latest frame at ~15 FPS cadence."""

    def __init__(self, cfg: ProducerConfig) -> None:
        self.interval_ms = cfg.frame_sample_interval_ms
        self._last_sample_time = 0.0

    def should_sample(self, timestamp_ms: int | None = None) -> bool:
        now = float(timestamp_ms) if timestamp_ms is not None else time.monotonic() * 1000.0
        if now - self._last_sample_time < self.interval_ms:
            return False
        self._last_sample_time = now
        return True


class YoloDetector:
    """
    Loads YOLOv11m with TensorRT FP16 optimization for 3x faster inference.

    Expects a pre-built .engine file. To create:
        python scripts/build_tensorrt_engine.py --model models/best.pt --imgsz 960 544

    Uses post-training quantization (FP32 training → FP16 inference).
    No changes to training pipeline required.
    """

    def __init__(
        self,
        cfg: ProducerConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self.model_path = cfg.yolo_model_path
        self.plate_class_id = cfg.plate_class_id
        self.input_size = cfg.input_size
        self.conf_threshold = cfg.conf_threshold
        self.iou_threshold = cfg.iou_threshold
        self.device = cfg.device
        self.max_detections = cfg.max_detections
        self.warmup_runs = cfg.warmup_runs
        self.logger = logger or logging.getLogger(__name__)
        self.init_timings_ms: dict[str, float] = {}

        # Validate CUDA availability for TensorRT
        if not self.device.startswith("cuda") or not torch.cuda.is_available():
            raise RuntimeError(
                "TensorRT FP16 requires CUDA. "
                f"Current device: {self.device}, CUDA available: {torch.cuda.is_available()}"
            )

        start_load = time.monotonic()
        self.model = self._load_model()
        self.init_timings_ms["load_model_ms"] = (time.monotonic() - start_load) * 1000

        start_warmup = time.monotonic()
        self._warmup()
        self.init_timings_ms["warmup_ms"] = (time.monotonic() - start_warmup) * 1000

    def _load_model(self) -> YOLO:
        """
        Load pre-built TensorRT FP16 engine.

        The .engine file contains optimized CUDA kernels with:
        - FP16 precision (2x memory reduction, 3x speedup)
        - Fused layers (Conv+BatchNorm+ReLU → single kernel)
        - Auto-tuned kernels (fastest implementation selected)
        - Optimized memory layout (minimal allocations)
        """
        # Derive engine path from model path (.pt → .engine)
        model_path_obj = Path(self.model_path)
        engine_path = model_path_obj.with_suffix(".engine")

        if not engine_path.exists():
            raise FileNotFoundError(
                f"TensorRT engine not found: {engine_path}\n\n"
                f"Please build the engine first:\n"
                f"  python scripts/build_tensorrt_engine.py --model {self.model_path} "
                f"--imgsz {self.input_size[0]} {self.input_size[1]}\n\n"
                f"This is a one-time optimization step that takes 2-5 minutes."
            )

        self.logger.info(f"Loading TensorRT FP16 engine: {engine_path}")

        try:
            model = YOLO(str(engine_path), task="pose")
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load TensorRT engine from {engine_path}. "
                f"The engine may be corrupted or incompatible. "
                f"Try rebuilding with build_tensorrt_engine.py. Error: {exc}"
            ) from exc

        # Log engine info
        engine_size_mb = engine_path.stat().st_size / (1024**2)
        self.logger.info(f"TensorRT engine loaded successfully ({engine_size_mb:.1f} MB)")

        return model

    def _warmup(self) -> None:
        """
        Run warmup inferences to initialize CUDA kernels and allocate GPU memory.
        TensorRT engines benefit from warmup to optimize runtime graph execution.
        """
        if self.warmup_runs <= 0:
            return

        w, h = self.input_size  # Config is (width, height)
        # Create dummy input (TensorRT handles precision internally)
        dummy = np.zeros((h, w, 3), dtype=np.uint8)

        try:
            for _ in range(self.warmup_runs):
                _ = self.model(dummy, verbose=False)
            self.logger.debug(f"Warmup completed ({self.warmup_runs} runs)")
        except Exception as exc:
            # Warmup is opportunistic; do not fail pipeline.
            self.logger.warning(f"Warmup failed: {exc}", exc_info=False)

    def run(
        self, frame_img: np.ndarray, frame_idx: int
    ) -> tuple[list[Detection], DetectionTimings]:
        """
        Run TensorRT FP16 inference on a single frame.

        TensorRT handles all precision and optimization internally.
        No need to manually convert to FP16 or manage device placement.

        Returns:
            (detections, timings) tuple - raw YOLO detections before filtering
        """
        timings = DetectionTimings()
        run_start_time = time.monotonic()

        prep_start_time = time.monotonic()
        tensor, scale, pad = self._prepare_input(frame_img)
        timings.prepare_ms = (time.monotonic() - prep_start_time) * 1000

        infer_start_time = time.monotonic()
        with torch.inference_mode():
            target_w, target_h = self.input_size
            results = self.model(
                tensor,
                conf=self.conf_threshold,
                iou=self.iou_threshold,
                imgsz=(target_h, target_w),
                verbose=False,
            )
        timings.inference_ms = (time.monotonic() - infer_start_time) * 1000

        if not results:
            timings.total_ms = (time.monotonic() - run_start_time) * 1000
            return [], timings

        post_start_time = time.monotonic()
        detections = self._postprocess(
            results[0].boxes,
            results[0].keypoints if hasattr(results[0], "keypoints") else None,
            frame_img.shape[:2],
            scale,
            pad,
            frame_idx,
        )
        timings.postprocess_ms = (time.monotonic() - post_start_time) * 1000

        timings.total_ms = (time.monotonic() - run_start_time) * 1000
        return detections, timings

    def _prepare_input(
        self, frame_img: np.ndarray
    ) -> tuple[torch.Tensor, tuple[float, float], tuple[float, float]]:
        """
        Letterbox resize and convert to tensor for TensorRT inference.
        """
        h, w = frame_img.shape[:2]
        target_w, target_h = self.input_size  # Config is (width, height)
        scale = min(target_w / w, target_h / h)
        new_w, new_h = int(round(w * scale)), int(round(h * scale))

        resized = cv2.resize(frame_img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        dw, dh = target_w - new_w, target_h - new_h
        dw /= 2
        dh /= 2
        top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
        left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
        padded = cv2.copyMakeBorder(
            resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114)
        )

        img = padded[:, :, ::-1].transpose(2, 0, 1)  # BGR -> RGB, HWC -> CHW
        img = np.ascontiguousarray(img, dtype=np.float32) / 255.0
        tensor = torch.from_numpy(img).unsqueeze(0).to(self.device)
        return tensor, (scale, scale), (left, top)

    def _postprocess(
        self,
        boxes: Any,
        keypoints: Any,
        orig_shape: Sequence[int],
        scale: tuple[float, float],
        pad: tuple[float, float],
        frame_idx: int,
    ) -> list[Detection]:
        """Convert YOLO boxes back to original frame coords with filtering."""
        orig_h, orig_w = orig_shape
        detections: list[Detection] = []
        pad_x, pad_y = pad
        scale_x, scale_y = scale
        keypoints_xy = None
        keypoints_conf = None

        if keypoints is not None and hasattr(keypoints, "xy"):
            keypoints_xy = keypoints.xy.cpu().numpy()
            if hasattr(keypoints, "conf") and keypoints.conf is not None:
                keypoints_conf = keypoints.conf.cpu().numpy()

        for idx, box in enumerate(boxes):
            cls_id = int(box.cls[0].item())
            if cls_id != self.plate_class_id:
                continue

            conf = float(box.conf[0].item())
            x1, y1, x2, y2 = box.xyxy[0].tolist()

            # Reverse letterbox transform.
            x1 = (x1 - pad_x) / scale_x
            y1 = (y1 - pad_y) / scale_y
            x2 = (x2 - pad_x) / scale_x
            y2 = (y2 - pad_y) / scale_y

            x1 = float(np.clip(x1, 0, orig_w - 1))
            y1 = float(np.clip(y1, 0, orig_h - 1))
            x2 = float(np.clip(x2, 0, orig_w - 1))
            y2 = float(np.clip(y2, 0, orig_h - 1))

            det_keypoints = None
            det_keypoint_scores = None
            if keypoints_xy is not None and idx < len(keypoints_xy):
                raw_points = keypoints_xy[idx]
                remapped_points = [raw_points[i] for i in KEYPOINT_REMAP]
                det_keypoints = []
                for xk, yk in remapped_points:
                    xk = (xk - pad_x) / scale_x
                    yk = (yk - pad_y) / scale_y
                    xk = float(np.clip(xk, 0, orig_w - 1))
                    yk = float(np.clip(yk, 0, orig_h - 1))
                    det_keypoints.append((xk, yk))

                if keypoints_conf is not None:
                    raw_scores = keypoints_conf[idx]
                    det_keypoint_scores = [float(raw_scores[i]) for i in KEYPOINT_REMAP]

            detections.append(
                Detection(
                    bbox=[x1, y1, x2, y2],
                    confidence=conf,
                    class_id=cls_id,
                    frame_idx=frame_idx,
                    keypoints=det_keypoints,
                    keypoint_scores=det_keypoint_scores,
                )
            )

            if len(detections) >= self.max_detections:
                break

        return detections


def _filter_detections_geometric(
    detections: list[Detection],
    cfg: ProducerConfig,
) -> tuple[list[Detection], dict[int, str]]:
    """
    Filter detections using geometric constraints (aspect ratio, size).

    Args:
        detections: Raw YOLO detections
        cfg: Configuration with filter thresholds

    Returns:
        (filtered_detections, filter_reasons)
        - filtered_detections: Detections passing all geometric checks
        - filter_reasons: Map of detection_idx → rejection reason (for tuning logs)

    Performance: <0.1ms (trivial per-detection checks)
    """
    filtered = []
    reasons = {}

    for idx, det in enumerate(detections):
        bbox = det.bbox
        x1, y1, x2, y2 = bbox
        w = x2 - x1
        h = y2 - y1

        # Size check
        if w < cfg.min_bbox_width:
            reasons[idx] = f"width_too_small_{w:.1f}<{cfg.min_bbox_width}"
            continue
        if h < cfg.min_bbox_height:
            reasons[idx] = f"height_too_small_{h:.1f}<{cfg.min_bbox_height}"
            continue

        # Aspect ratio check
        aspect = w / h if h > 0 else 0.0
        if aspect < cfg.min_aspect_ratio:
            reasons[idx] = f"aspect_too_narrow_{aspect:.2f}<{cfg.min_aspect_ratio}"
            continue
        if aspect > cfg.max_aspect_ratio:
            reasons[idx] = f"aspect_too_wide_{aspect:.2f}>{cfg.max_aspect_ratio}"
            continue

        filtered.append(det)

    return filtered, reasons


def detect_plate_rois(
    frame_img: np.ndarray,
    frame_idx: int,
    detector: YoloDetector,
    sampler: FrameSampler,
    cfg: ProducerConfig,
    timestamp_ms: int | None = None,
) -> DetectorOutput:
    """
    Public entrypoint: samples on time cadence, runs detection, applies geometric filtering.

    Args:
        frame_img: BGR frame from WHEP client (H×W×3 uint8)
        frame_idx: Sequential frame number
        detector: YoloDetector instance
        sampler: FrameSampler instance
        cfg: ProducerConfig (needed for geometric filtering)
        timestamp_ms: Optional frame timestamp

    Returns:
        DetectorOutput with raw_detections, filtered_detections, filter_reasons, and sampled flag
    """
    if not sampler.should_sample(timestamp_ms=timestamp_ms):
        return DetectorOutput(
            raw_detections=[],
            filtered_detections=[],
            filter_reasons={},
            sampled=False,
        )

    # Run YOLO inference
    raw_detections, timings = detector.run(frame_img, frame_idx)

    # Apply geometric filtering
    filtered_detections, filter_reasons = _filter_detections_geometric(raw_detections, cfg)

    return DetectorOutput(
        raw_detections=raw_detections,
        filtered_detections=filtered_detections,
        filter_reasons=filter_reasons,
        timings=timings,
        sampled=True,
    )
