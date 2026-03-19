"""Generate a single comparison grid image for all golden crops.

Columns (left to right):
  1. Original crop (direct resize)
  2. GT keypoints + homography
  3. Model keypoints + homography
  4. Model keypoints + homography + image processing

Each panel has OCR readout + confidence below it.
One row per crop, all in one image.
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
from consumer.models import EnhancedBatchSelection, RichQualityMetrics, RoiRichQuality
from consumer.ops_parseq import ParseqEngine
from consumer.ops_preprocess_gpu import (
    _apply_clahe_gpu,
    _apply_geometry,
    _apply_photometric_ops,
    _safe_ingest,
)
from consumer.ops_recipe import (
    IDX_WARP_ENABLE,
    IDX_TIGHT_CROP,
    IDX_WARP_MARGIN,
    RECIPE_IDENTITY,
    RECIPE_KEYS,
    generate_recipe_tensor,
)
from producer.models import FastQualityMetrics, RoiFastQuality, RoiImage
from training.regression.production.config import ProductionConfig
from training.regression.production.model import ProductionCornerNet

GOLDEN_DIR = Path("tests/fixtures/data/golden_crops")
MANIFEST_PATH = GOLDEN_DIR / "manifest.json"
CHECKPOINT_PATH = Path("training/regression/results/production_s2_300ep/best.pt")
OUTPUT_DIR = Path("results/ocr_pipeline_output")


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
    return [(float((coords[i * 2] - pad_x) / scale), float((coords[i * 2 + 1] - pad_y) / scale)) for i in range(4)]


def make_roi_rq(entry: dict, crop_img: np.ndarray, kps: list[tuple[float, float]], force_enhance: bool = False) -> RoiRichQuality:
    m = entry["metrics"]
    name = entry["name"]
    h, w = crop_img.shape[:2]
    roi_img = RoiImage(
        track_id=name, crop_img=crop_img,
        bbox=[0.0, 0.0, float(m["plate_width_px"]), float(m["plate_height_px"])],
        padded_bbox=[0.0, 0.0, float(w), float(h)],
        frame_idx=0, confidence=0.9, frame_width=1920, frame_height=1080,
        keypoints=kps, keypoint_scores=[0.9] * 4, crop_width=w, crop_height=h,
    )
    fast_m = FastQualityMetrics(
        focus_tenengrad=float(m["tenengrad"]), brightness_mean=float(m["luminance_mean"]) / 255.0,
        contrast_std=float(m["global_contrast"]) / 255.0, over_exposed_frac=float(m["white_clip_fraction"]),
        under_exposed_frac=float(m["black_clip_fraction"]), band_edge_mean=100.0,
        gradient_histogram=np.zeros(25, dtype=np.float32),
    )
    roi_fq = RoiFastQuality(roi=roi_img, metrics=fast_m, quality_score=0.5,
                            passes_min_quality=True, thumb_gray=np.zeros((16, 32), dtype=np.uint8))
    enhance_eligible = force_enhance or bool(m["enhance_eligible"])
    rich = RichQualityMetrics(
        track_id=name, frame_idx=0, canonical_width=256, canonical_height=128,
        plate_width_px=float(m["plate_width_px"]), plate_height_px=float(m["plate_height_px"]),
        crop_clip_fraction=0.0, detection_confidence=0.9,
        keypoints=kps, keypoint_scores=[0.9] * 4,
        keypoint_confidence_min=0.9, keypoint_confidence_mean=0.9,
        skew_degrees=m.get("skew_degrees"), perspective_score=m.get("perspective_score"),
        perspective_direction=m.get("perspective_direction"),
        luminance_mean=float(m["luminance_mean"]), luminance_p05=float(m["luminance_p05"]),
        luminance_p95=float(m["luminance_p95"]),
        black_clip_fraction=float(m["black_clip_fraction"]), white_clip_fraction=float(m["white_clip_fraction"]),
        global_contrast=float(m["global_contrast"]), local_contrast=float(m["local_contrast"]),
        tenengrad=float(m["tenengrad"]), tenengrad_horizontal=float(m["tenengrad_horizontal"]),
        tenengrad_vertical=float(m["tenengrad_vertical"]),
        blur_anisotropy=float(m["blur_anisotropy"]), noise_std=float(m["noise_std"]),
        flat_region_fraction=float(m["flat_region_fraction"]),
        too_small=bool(m["too_small"]), clipped=False,
        very_blurry=bool(m["very_blurry"]), mildly_soft=bool(m["mildly_soft"]),
        exposure_bad=bool(m["exposure_bad"]), low_contrast=bool(m["low_contrast"]),
        noisy=bool(m["noisy"]), vertical_edges_weak=bool(m["vertical_edges_weak"]),
        horizontal_edges_weak=bool(m["horizontal_edges_weak"]),
        top8_eligible=bool(m["top8_eligible"]), enhance_eligible=enhance_eligible,
        homography_eligible=bool(m["homography_eligible"]),
        bbox_center_x_normalized=0.5, bbox_center_y_normalized=0.5,
        diversity_signature=np.array([0.0, 0.4, 0.5, 0.0], dtype=np.float32),
        quad_area_px=None, edge_ratio=None,
    )
    return RoiRichQuality(roi_fq=roi_fq, metrics=rich)


def greedy_decode(distributions: np.ndarray, vocab_chars: tuple[str, ...], eos_index: int) -> tuple[str, float]:
    text = []
    log_conf_sum = 0.0
    for t in range(distributions.shape[0]):
        best_idx = int(np.argmax(distributions[t]))
        if best_idx == eos_index:
            break
        text.append(vocab_chars[best_idx])
        log_conf_sum += np.log(max(float(distributions[t, best_idx]), 1e-12))
    if not text:
        return ("", 0.0)
    return ("".join(text), float(np.exp(log_conf_sum / len(text))))


def build_geom_recipe(cfg: ConsumerConfig) -> np.ndarray:
    r = np.array([[RECIPE_IDENTITY[k] for k in RECIPE_KEYS]], dtype=np.float32)
    r[0, IDX_WARP_ENABLE] = 1.0
    r[0, IDX_TIGHT_CROP] = 1.0
    r[0, IDX_WARP_MARGIN] = cfg.warp_margin
    return r


def make_original_tensor(crop: np.ndarray, device: torch.device) -> torch.Tensor:
    resized = cv2.resize(crop, (128, 32), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    t = torch.from_numpy(rgb).float().permute(2, 0, 1) / 255.0
    return (t * 2 - 1).unsqueeze(0).to(device)


def make_geom_tensor(crop: np.ndarray, kps: list[tuple[float, float]], cfg: ConsumerConfig, device: torch.device) -> torch.Tensor:
    images = _safe_ingest([crop], device)
    recipe = build_geom_recipe(cfg)
    recipe_t = torch.tensor(recipe, dtype=torch.float32, device=device)
    result = _apply_geometry(images, recipe_t, [kps], cfg)
    return result * 2 - 1


def make_full_pipeline_tensor(entry: dict, crop: np.ndarray, kps: list[tuple[float, float]], cfg: ConsumerConfig, device: torch.device) -> torch.Tensor:
    roi_rq = make_roi_rq(entry, crop, kps, force_enhance=True)
    batch = EnhancedBatchSelection(track_id="f", version=1, base_rois=[], enhance_rois=[roi_rq], duplicate_groups={})
    recipe, _, is_enh, _, _ = generate_recipe_tensor(batch, cfg)
    images = _safe_ingest([crop], device)
    recipe_t = torch.tensor(recipe, dtype=torch.float32, device=device)
    is_enh_t = torch.tensor(is_enh, dtype=torch.bool, device=device)
    result = _apply_geometry(images, recipe_t, [kps], cfg)
    result = _apply_photometric_ops(result, recipe_t, cfg)
    result = _apply_clahe_gpu(result, recipe_t, is_enh_t, cfg)
    return result * 2 - 1


def parseq_to_bgr(t: torch.Tensor) -> np.ndarray:
    """(3,H,W) in [-1,1] -> uint8 BGR."""
    img = ((t.cpu().float() + 1.0) / 2.0).clamp(0, 1)
    img = (img.permute(1, 2, 0).numpy() * 255).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


# --- Layout constants ---
PANEL_W = 128       # OCR output width
PANEL_H = 32        # OCR output height
TEXT_H = 28          # space for OCR text below each panel
COL_GAP = 8         # gap between columns
ROW_GAP = 6         # gap between rows
LABEL_COL_W = 130   # left label column width
HEADER_H = 30       # column header height
SCALE = 2           # scale everything up for readability


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda")

    # Load corner model
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
    print("Corner model loaded")

    # Load PARSeq
    consumer_cfg = ConsumerConfig()
    consumer_cfg.parseq_model_path = "models/weights/parseq.pt"
    consumer_cfg.parseq_vocab_path = "models/weights/parseq_vocab.txt"
    parseq = ParseqEngine(consumer_cfg)
    print("PARSeq loaded")

    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    n_crops = len(manifest["crops"])
    n_cols = 4
    col_labels = [
        "Original (resize)",
        "GT Keypoints + Homography",
        "Model KPs + Homography",
        "Model KPs + Homography\n+ Image Processing",
    ]

    # Compute canvas size (at 1x, then scale up)
    cell_w = PANEL_W
    cell_h = PANEL_H + TEXT_H
    grid_w = LABEL_COL_W + n_cols * cell_w + (n_cols - 1) * COL_GAP
    grid_h = HEADER_H + n_crops * cell_h + (n_crops - 1) * ROW_GAP

    canvas = np.full((grid_h, grid_w, 3), 255, dtype=np.uint8)

    # Draw column headers
    for c in range(n_cols):
        x = LABEL_COL_W + c * (cell_w + COL_GAP)
        lines = col_labels[c].split("\n")
        for li, line in enumerate(lines):
            cv2.putText(canvas, line, (x, 10 + li * 11), cv2.FONT_HERSHEY_SIMPLEX, 0.30, (0, 0, 0), 1)

    print(f"\nProcessing {n_crops} crops...")

    for row, entry in enumerate(manifest["crops"]):
        crop_img = np.load(GOLDEN_DIR / entry["file"])
        name = entry["name"]
        scenario = entry["scenario"]
        gt_kps = entry.get("keypoints_in_crop")
        gt_kps_tuples = [(kp[0], kp[1]) for kp in gt_kps] if gt_kps else None
        model_kps = predict_keypoints(corner_model, crop_img, device)

        # Build 4 tensors
        t1 = make_original_tensor(crop_img, device)                                       # col 0: original resize
        t2 = make_geom_tensor(crop_img, gt_kps_tuples, consumer_cfg, device) if gt_kps_tuples else t1.clone()  # col 1: GT homography
        t3 = make_geom_tensor(crop_img, model_kps, consumer_cfg, device)                  # col 2: model homography
        t4 = make_full_pipeline_tensor(entry, crop_img, model_kps, consumer_cfg, device)   # col 3: full pipeline

        # Run PARSeq on all 4
        batch = torch.cat([t1, t2, t3, t4], dim=0)
        result = parseq.run(batch)
        assert result is not None

        texts = []
        confs = []
        for i in range(4):
            text, conf = greedy_decode(result.distributions[i], result.vocab_chars, result.eos_index)
            texts.append(text)
            confs.append(conf)

        # Row label
        y_row = HEADER_H + row * (cell_h + ROW_GAP)
        cv2.putText(canvas, name, (2, y_row + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 0, 0), 1)
        cv2.putText(canvas, f"({scenario})", (2, y_row + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.25, (100, 100, 100), 1)

        # Place panels
        panel_images = [parseq_to_bgr(t1[0]), parseq_to_bgr(t2[0]), parseq_to_bgr(t3[0]), parseq_to_bgr(t4[0])]
        for c in range(n_cols):
            x = LABEL_COL_W + c * (cell_w + COL_GAP)
            y = y_row

            # Place the 32x128 image
            canvas[y : y + PANEL_H, x : x + PANEL_W] = panel_images[c]

            # Draw thin border
            cv2.rectangle(canvas, (x - 1, y - 1), (x + PANEL_W, y + PANEL_H), (180, 180, 180), 1)

            # OCR text + confidence below
            text_str = texts[c] if texts[c] else "---"
            conf_str = f"{confs[c]:.0%}"
            label = f"{text_str}  {conf_str}"

            # Color confidence: green if >85%, yellow 70-85%, red <70%
            if confs[c] >= 0.85:
                color = (0, 130, 0)
            elif confs[c] >= 0.70:
                color = (0, 130, 180)
            else:
                color = (0, 0, 200)

            cv2.putText(canvas, label, (x, y + PANEL_H + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.33, color, 1)

        print(f"  {name:16s} | {texts[0]:>8s} {confs[0]:5.0%} | {texts[1]:>8s} {confs[1]:5.0%} | {texts[2]:>8s} {confs[2]:5.0%} | {texts[3]:>8s} {confs[3]:5.0%}")

    # Scale up for readability
    scaled = cv2.resize(canvas, (grid_w * SCALE, grid_h * SCALE), interpolation=cv2.INTER_NEAREST)

    out_path = OUTPUT_DIR / "golden_crops_comparison_grid.png"
    cv2.imwrite(str(out_path), scaled)
    print(f"\nSaved: {out_path}")
    print(f"Size: {scaled.shape[1]}x{scaled.shape[0]}")


if __name__ == "__main__":
    main()
