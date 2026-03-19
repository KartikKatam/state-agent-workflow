"""Run PARSeq OCR on all 4 pipeline stages for each golden crop.

For each of the 11 golden crops, runs PARSeq on:
  1. Original crop (direct resize to 32x128)
  2. Homography warp only
  3. Base pipeline (geometry + photometric)
  4. Enhanced pipeline (geometry + photometric + CLAHE)

Reports: plate text + confidence for each.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from consumer.config import ConsumerConfig
from consumer.models import (
    EnhancedBatchSelection,
    RichQualityMetrics,
    RoiRichQuality,
)
from consumer.ops_parseq import ParseqEngine, load_vocab, build_allowed_indices
from consumer.ops_preprocess_gpu import (
    _apply_clahe_gpu,
    _apply_geometry,
    _apply_photometric_ops,
    _safe_ingest,
)
from consumer.ops_recipe import RECIPE_IDENTITY, RECIPE_KEYS, generate_recipe_tensor
from producer.models import FastQualityMetrics, RoiFastQuality, RoiImage
from training.regression.production.config import ProductionConfig
from training.regression.production.model import ProductionCornerNet

GOLDEN_DIR = Path("tests/fixtures/data/golden_crops")
MANIFEST_PATH = GOLDEN_DIR / "manifest.json"
CHECKPOINT_PATH = Path("training/regression/results/production_s2_300ep/best.pt")


def letterbox_crop(img: np.ndarray, target_h: int = 80, target_w: int = 256) -> tuple[np.ndarray, float, int, int]:
    h, w = img.shape[:2]
    scale = min(target_w / w, target_h / h)
    new_w, new_h = int(w * scale), int(h * scale)
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((target_h, target_w, 3), 128, dtype=np.uint8)
    pad_x = (target_w - new_w) // 2
    pad_y = (target_h - new_h) // 2
    canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized
    return canvas, scale, pad_x, pad_y


def predict_keypoints(model: ProductionCornerNet, crop: np.ndarray, device: torch.device) -> list[tuple[float, float]]:
    lb, scale, pad_x, pad_y = letterbox_crop(crop)
    tensor = torch.from_numpy(lb[:, :, ::-1].copy()).float().permute(2, 0, 1).unsqueeze(0) / 255.0
    tensor = tensor.to(device)
    with torch.no_grad():
        coords, _ = model(tensor)
    coords = coords.cpu().numpy()[0]
    keypoints = []
    for i in range(4):
        px = (coords[i * 2] - pad_x) / scale
        py = (coords[i * 2 + 1] - pad_y) / scale
        keypoints.append((float(px), float(py)))
    return keypoints


def make_roi_rq(entry: dict, crop_img: np.ndarray, model_kps: list[tuple[float, float]], force_enhance: bool = False) -> RoiRichQuality:
    m = entry["metrics"]
    name = entry["name"]
    h, w = crop_img.shape[:2]
    roi_img = RoiImage(
        track_id=name, crop_img=crop_img,
        bbox=[0.0, 0.0, float(m["plate_width_px"]), float(m["plate_height_px"])],
        padded_bbox=[0.0, 0.0, float(w), float(h)],
        frame_idx=0, confidence=0.9, frame_width=1920, frame_height=1080,
        keypoints=model_kps, keypoint_scores=[0.9] * 4, crop_width=w, crop_height=h,
    )
    fast_metrics = FastQualityMetrics(
        focus_tenengrad=float(m["tenengrad"]),
        brightness_mean=float(m["luminance_mean"]) / 255.0,
        contrast_std=float(m["global_contrast"]) / 255.0,
        over_exposed_frac=float(m["white_clip_fraction"]),
        under_exposed_frac=float(m["black_clip_fraction"]),
        band_edge_mean=100.0,
        gradient_histogram=np.zeros(25, dtype=np.float32),
    )
    roi_fq = RoiFastQuality(
        roi=roi_img, metrics=fast_metrics, quality_score=0.5,
        passes_min_quality=True, thumb_gray=np.zeros((16, 32), dtype=np.uint8),
    )
    enhance_eligible = force_enhance or bool(m["enhance_eligible"])
    rich_metrics = RichQualityMetrics(
        track_id=name, frame_idx=0, canonical_width=256, canonical_height=128,
        plate_width_px=float(m["plate_width_px"]), plate_height_px=float(m["plate_height_px"]),
        crop_clip_fraction=0.0, detection_confidence=0.9,
        keypoints=model_kps, keypoint_scores=[0.9] * 4,
        keypoint_confidence_min=0.9, keypoint_confidence_mean=0.9,
        skew_degrees=m.get("skew_degrees"), perspective_score=m.get("perspective_score"),
        perspective_direction=m.get("perspective_direction"),
        luminance_mean=float(m["luminance_mean"]), luminance_p05=float(m["luminance_p05"]),
        luminance_p95=float(m["luminance_p95"]),
        black_clip_fraction=float(m["black_clip_fraction"]),
        white_clip_fraction=float(m["white_clip_fraction"]),
        global_contrast=float(m["global_contrast"]), local_contrast=float(m["local_contrast"]),
        tenengrad=float(m["tenengrad"]), tenengrad_horizontal=float(m["tenengrad_horizontal"]),
        tenengrad_vertical=float(m["tenengrad_vertical"]),
        blur_anisotropy=float(m["blur_anisotropy"]), noise_std=float(m["noise_std"]),
        flat_region_fraction=float(m["flat_region_fraction"]),
        too_small=bool(m["too_small"]), clipped=False,
        very_blurry=bool(m["very_blurry"]), mildly_soft=bool(m["mildly_soft"]),
        exposure_bad=bool(m["exposure_bad"]), low_contrast=bool(m["low_contrast"]),
        noisy=bool(m["noisy"]),
        vertical_edges_weak=bool(m["vertical_edges_weak"]),
        horizontal_edges_weak=bool(m["horizontal_edges_weak"]),
        top8_eligible=bool(m["top8_eligible"]), enhance_eligible=enhance_eligible,
        homography_eligible=bool(m["homography_eligible"]),
        bbox_center_x_normalized=0.5, bbox_center_y_normalized=0.5,
        diversity_signature=np.array([0.0, 0.4, 0.5, 0.0], dtype=np.float32),
        quad_area_px=None, edge_ratio=None,
    )
    return RoiRichQuality(roi_fq=roi_fq, metrics=rich_metrics)


def greedy_decode(distributions: np.ndarray, vocab_chars: tuple[str, ...], eos_index: int) -> tuple[str, float]:
    """Greedy decode a single (max_T, vocab_size) distribution.

    Returns (plate_text, confidence) where confidence is the geometric mean
    of per-position max probabilities up to EOS.
    """
    text = []
    log_conf_sum = 0.0
    for t in range(distributions.shape[0]):
        best_idx = int(np.argmax(distributions[t]))
        if best_idx == eos_index:
            break
        prob = float(distributions[t, best_idx])
        text.append(vocab_chars[best_idx])
        log_conf_sum += np.log(max(prob, 1e-12))

    n = len(text)
    if n == 0:
        return ("", 0.0)
    confidence = float(np.exp(log_conf_sum / n))
    return ("".join(text), confidence)


def make_original_tensor(crop: np.ndarray, device: torch.device) -> torch.Tensor:
    """Direct resize of original crop to 32x128, then to PARSeq [-1,1] tensor."""
    resized = cv2.resize(crop, (128, 32), interpolation=cv2.INTER_LINEAR)
    # BGR -> RGB, HWC -> CHW, [0,255] -> [0,1] -> [-1,1]
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    t = torch.from_numpy(rgb).float().permute(2, 0, 1) / 255.0
    t = t * 2 - 1  # PARSeq normalization
    return t.unsqueeze(0).to(device)


def make_geom_only_tensor(crop: np.ndarray, kps: list[tuple[float, float]], cfg: ConsumerConfig, device: torch.device) -> torch.Tensor:
    """Geometry only: homography warp, no photometric. Returns [-1,1] tensor."""
    images = _safe_ingest([crop], device)
    identity = np.array([[RECIPE_IDENTITY[k] for k in RECIPE_KEYS]], dtype=np.float32)
    identity[0, 0] = 1.0  # warp_enable
    identity[0, 1] = 1.0  # tight_crop
    identity[0, 3] = cfg.warp_margin
    recipe_t = torch.tensor(identity, dtype=torch.float32, device=device)
    result = _apply_geometry(images, recipe_t, [kps], cfg)
    return (result * 2 - 1)  # [0,1] -> [-1,1]


def make_base_tensor(entry: dict, crop: np.ndarray, kps: list[tuple[float, float]], cfg: ConsumerConfig, device: torch.device) -> torch.Tensor:
    """Base pipeline: geometry + photometric, no CLAHE. Returns [-1,1] tensor."""
    roi_rq = make_roi_rq(entry, crop, kps, force_enhance=False)
    batch = EnhancedBatchSelection(track_id="b", version=1, base_rois=[roi_rq], enhance_rois=[], duplicate_groups={})
    recipe, _, is_enh, _, _ = generate_recipe_tensor(batch, cfg)
    images = _safe_ingest([crop], device)
    recipe_t = torch.tensor(recipe, dtype=torch.float32, device=device)
    result = _apply_geometry(images, recipe_t, [kps], cfg)
    result = _apply_photometric_ops(result, recipe_t, cfg)
    return (result * 2 - 1)


def make_enhanced_tensor(entry: dict, crop: np.ndarray, kps: list[tuple[float, float]], cfg: ConsumerConfig, device: torch.device) -> torch.Tensor:
    """Enhanced pipeline: geometry + photometric + CLAHE. Returns [-1,1] tensor."""
    roi_rq = make_roi_rq(entry, crop, kps, force_enhance=True)
    batch = EnhancedBatchSelection(track_id="e", version=1, base_rois=[], enhance_rois=[roi_rq], duplicate_groups={})
    recipe, _, is_enh, _, _ = generate_recipe_tensor(batch, cfg)
    images = _safe_ingest([crop], device)
    recipe_t = torch.tensor(recipe, dtype=torch.float32, device=device)
    is_enh_t = torch.tensor(is_enh, dtype=torch.bool, device=device)
    result = _apply_geometry(images, recipe_t, [kps], cfg)
    result = _apply_photometric_ops(result, recipe_t, cfg)
    result = _apply_clahe_gpu(result, recipe_t, is_enh_t, cfg)
    return (result * 2 - 1)


def main() -> None:
    device = torch.device("cuda")
    print("=== Loading models ===")

    # Load corner CNN
    corner_cfg = ProductionConfig()
    corner_cfg.stride = 2
    corner_model = ProductionCornerNet(corner_cfg)
    ckpt = torch.load(str(CHECKPOINT_PATH), map_location=device, weights_only=True)
    if "ema_state_dict" in ckpt:
        corner_model.load_state_dict(ckpt["ema_state_dict"])
    else:
        corner_model.load_state_dict(ckpt["model_state_dict"])
    corner_model.to(device)
    corner_model.eval()
    print("Corner CNN loaded (stride-2 EMA)")

    # Load PARSeq TRT engine
    consumer_cfg = ConsumerConfig()
    consumer_cfg.parseq_model_path = "models/weights/parseq.pt"
    consumer_cfg.parseq_vocab_path = "models/weights/parseq_vocab.txt"
    parseq = ParseqEngine(consumer_cfg)
    print("PARSeq TRT engine loaded")

    # Load manifest
    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    # Header
    print("\n" + "=" * 120)
    print(f"{'Image':<16} {'Scenario':<20} {'Stage':<22} {'Text':<12} {'Confidence':>10}")
    print("=" * 120)

    for entry in manifest["crops"]:
        crop_img = np.load(GOLDEN_DIR / entry["file"])
        name = entry["name"]
        scenario = entry["scenario"]
        model_kps = predict_keypoints(corner_model, crop_img, device)

        # Build 4 tensors
        t_original = make_original_tensor(crop_img, device)
        t_geom = make_geom_only_tensor(crop_img, model_kps, consumer_cfg, device)
        t_base = make_base_tensor(entry, crop_img, model_kps, consumer_cfg, device)
        t_enhanced = make_enhanced_tensor(entry, crop_img, model_kps, consumer_cfg, device)

        # Stack all 4 into one batch for efficiency
        batch = torch.cat([t_original, t_geom, t_base, t_enhanced], dim=0)
        result = parseq.run(batch)

        if result is None:
            print(f"{name:<16} {scenario:<20} ** PARSeq inference failed **")
            continue

        stage_names = ["Original (resize)", "Homography Only", "Base Pipeline", "Enhanced Pipeline"]
        for i, stage in enumerate(stage_names):
            text, conf = greedy_decode(result.distributions[i], result.vocab_chars, result.eos_index)
            marker = " <--" if i == 0 else ""
            print(f"{name:<16} {scenario:<20} {stage:<22} {text:<12} {conf:>9.1%}{marker}")
        print("-" * 120)

    print("\nDone.")


if __name__ == "__main__":
    main()
