"""Sweep photometric + CLAHE parameters to find PARSeq confidence ceiling.

For each golden crop, tests:
  1. Current planner recipe (baseline)
  2. Aggressive gain/gamma sweep (exposure tuning)
  3. Contrast sweep
  4. Sharpen sweep
  5. CLAHE sweep (clip_limit, blend strength)
  6. Best combo found

Compares against homography-only baseline to quantify the actual headroom.
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
from consumer.models import EnhancedBatchSelection, RoiRichQuality
from consumer.ops_parseq import ParseqEngine
from consumer.ops_preprocess_gpu import (
    _apply_clahe_gpu,
    _apply_geometry,
    _apply_photometric_ops,
    _safe_ingest,
)
from consumer.ops_recipe import (
    IDX_CLAHE,
    IDX_CLAHE_CLIP,
    IDX_CLAHE_TILES,
    IDX_CONTRAST,
    IDX_GAIN,
    IDX_GAMMA,
    IDX_SHARPEN,
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
    return [(float((coords[i*2] - pad_x) / scale), float((coords[i*2+1] - pad_y) / scale)) for i in range(4)]


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
    """Identity recipe with warp enabled."""
    r = np.array([[RECIPE_IDENTITY[k] for k in RECIPE_KEYS]], dtype=np.float32)
    r[0, IDX_WARP_ENABLE] = 1.0
    r[0, IDX_TIGHT_CROP] = 1.0
    r[0, IDX_WARP_MARGIN] = cfg.warp_margin
    return r


def run_with_recipe(
    crop: np.ndarray,
    kps: list[tuple[float, float]],
    recipe: np.ndarray,
    is_enhanced: bool,
    cfg: ConsumerConfig,
    device: torch.device,
) -> torch.Tensor:
    """Run GPU pipeline with a custom recipe. Returns (1,3,32,128) tensor in [-1,1]."""
    images = _safe_ingest([crop], device)
    recipe_t = torch.tensor(recipe, dtype=torch.float32, device=device)
    result = _apply_geometry(images, recipe_t, [kps], cfg)
    result = _apply_photometric_ops(result, recipe_t, cfg)
    if is_enhanced:
        is_enh_t = torch.tensor([True], dtype=torch.bool, device=device)
        result = _apply_clahe_gpu(result, recipe_t, is_enh_t, cfg)
    return result * 2 - 1


def main() -> None:
    device = torch.device("cuda")

    # Load models
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

    consumer_cfg = ConsumerConfig()
    consumer_cfg.parseq_model_path = "models/weights/parseq.pt"
    consumer_cfg.parseq_vocab_path = "models/weights/parseq_vocab.txt"
    parseq = ParseqEngine(consumer_cfg)

    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    # Define sweep grid
    gains = [0.75, 0.85, 0.95, 1.0, 1.05, 1.15, 1.25]
    gammas = [0.70, 0.80, 0.90, 1.0, 1.10, 1.20, 1.50]
    contrasts = [0.0, 0.10, 0.20, 0.30, 0.40]
    sharpens = [0.0, 0.10, 0.20, 0.30, 0.35]
    clahe_blends = [0.0, 0.3, 0.5, 0.7, 1.0]
    clahe_clips = [1.5, 2.5, 4.0]

    print("=" * 130)
    print(f"{'Image':<16} {'Scenario':<18} {'Geom-Only':>10} {'Planner':>10} {'Best-Photo':>11} {'Best-Enh':>10} {'Best Recipe':<40} {'Text':<10}")
    print("=" * 130)

    total_geom = 0.0
    total_planner = 0.0
    total_best_photo = 0.0
    total_best_enh = 0.0
    n_images = 0

    for entry in manifest["crops"]:
        crop_img = np.load(GOLDEN_DIR / entry["file"])
        name = entry["name"]
        scenario = entry["scenario"]
        kps = predict_keypoints(corner_model, crop_img, device)

        # --- Geom-only baseline ---
        geom_recipe = build_geom_recipe(consumer_cfg)
        geom_tensor = run_with_recipe(crop_img, kps, geom_recipe, False, consumer_cfg, device)
        geom_result = parseq.run(geom_tensor)
        assert geom_result is not None
        geom_text, geom_conf = greedy_decode(geom_result.distributions[0], geom_result.vocab_chars, geom_result.eos_index)

        # --- Current planner baseline ---
        # Build a quick ROI for the planner
        m = entry["metrics"]
        h, w = crop_img.shape[:2]
        roi_img = RoiImage(
            track_id=name, crop_img=crop_img,
            bbox=[0.0, 0.0, float(m["plate_width_px"]), float(m["plate_height_px"])],
            padded_bbox=[0.0, 0.0, float(w), float(h)],
            frame_idx=0, confidence=0.9, frame_width=1920, frame_height=1080,
            keypoints=kps, keypoint_scores=[0.9]*4, crop_width=w, crop_height=h,
        )
        from consumer.models import RichQualityMetrics
        fast_m = FastQualityMetrics(
            focus_tenengrad=float(m["tenengrad"]), brightness_mean=float(m["luminance_mean"])/255.0,
            contrast_std=float(m["global_contrast"])/255.0, over_exposed_frac=float(m["white_clip_fraction"]),
            under_exposed_frac=float(m["black_clip_fraction"]), band_edge_mean=100.0,
            gradient_histogram=np.zeros(25, dtype=np.float32),
        )
        roi_fq = RoiFastQuality(roi=roi_img, metrics=fast_m, quality_score=0.5,
                                passes_min_quality=True, thumb_gray=np.zeros((16,32), dtype=np.uint8))
        rich = RichQualityMetrics(
            track_id=name, frame_idx=0, canonical_width=256, canonical_height=128,
            plate_width_px=float(m["plate_width_px"]), plate_height_px=float(m["plate_height_px"]),
            crop_clip_fraction=0.0, detection_confidence=0.9,
            keypoints=kps, keypoint_scores=[0.9]*4,
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
            top8_eligible=bool(m["top8_eligible"]), enhance_eligible=bool(m["enhance_eligible"]),
            homography_eligible=bool(m["homography_eligible"]),
            bbox_center_x_normalized=0.5, bbox_center_y_normalized=0.5,
            diversity_signature=np.array([0.0, 0.4, 0.5, 0.0], dtype=np.float32),
            quad_area_px=None, edge_ratio=None,
        )
        roi_rq = RoiRichQuality(roi_fq=roi_fq, metrics=rich)
        planner_batch = EnhancedBatchSelection(track_id="p", version=1, base_rois=[roi_rq], enhance_rois=[], duplicate_groups={})
        planner_recipe, _, planner_is_enh, _, _ = generate_recipe_tensor(planner_batch, consumer_cfg)
        planner_tensor = run_with_recipe(crop_img, kps, planner_recipe, False, consumer_cfg, device)
        planner_result = parseq.run(planner_tensor)
        assert planner_result is not None
        planner_text, planner_conf = greedy_decode(planner_result.distributions[0], planner_result.vocab_chars, planner_result.eos_index)

        # --- Sweep photometric (no CLAHE) ---
        best_photo_conf = geom_conf
        best_photo_text = geom_text
        best_photo_recipe_str = "identity"
        best_photo_recipe = geom_recipe.copy()

        # Batch the gain/gamma sweep for speed
        sweep_recipes = []
        sweep_labels = []
        for g in gains:
            for gm in gammas:
                r = geom_recipe.copy()
                r[0, IDX_GAIN] = g
                r[0, IDX_GAMMA] = gm
                sweep_recipes.append(r)
                sweep_labels.append(f"g={g:.2f},gm={gm:.2f}")

        # Add contrast sweep on top of planner gain/gamma
        pg = planner_recipe[0, IDX_GAIN]
        pgm = planner_recipe[0, IDX_GAMMA]
        for c in contrasts:
            r = geom_recipe.copy()
            r[0, IDX_GAIN] = pg
            r[0, IDX_GAMMA] = pgm
            r[0, IDX_CONTRAST] = c
            sweep_recipes.append(r)
            sweep_labels.append(f"g={pg:.2f},gm={pgm:.2f},c={c:.2f}")

        # Add sharpen sweep
        for s in sharpens:
            r = geom_recipe.copy()
            r[0, IDX_GAIN] = pg
            r[0, IDX_GAMMA] = pgm
            r[0, IDX_SHARPEN] = s
            sweep_recipes.append(r)
            sweep_labels.append(f"g={pg:.2f},gm={pgm:.2f},s={s:.2f}")

        # Run sweep in batches of 12 (parseq max batch)
        for batch_start in range(0, len(sweep_recipes), 12):
            batch_end = min(batch_start + 12, len(sweep_recipes))
            tensors = []
            for idx in range(batch_start, batch_end):
                t = run_with_recipe(crop_img, kps, sweep_recipes[idx], False, consumer_cfg, device)
                tensors.append(t)
            batch_t = torch.cat(tensors, dim=0)
            result = parseq.run(batch_t)
            if result is None:
                continue
            for j in range(batch_end - batch_start):
                text, conf = greedy_decode(result.distributions[j], result.vocab_chars, result.eos_index)
                if conf > best_photo_conf:
                    best_photo_conf = conf
                    best_photo_text = text
                    best_photo_recipe_str = sweep_labels[batch_start + j]
                    best_photo_recipe = sweep_recipes[batch_start + j].copy()

        # --- Sweep CLAHE on top of best photometric ---
        best_enh_conf = best_photo_conf
        best_enh_text = best_photo_text
        best_enh_recipe_str = best_photo_recipe_str

        clahe_recipes = []
        clahe_labels = []
        for blend in clahe_blends:
            if blend == 0:
                continue
            for clip in clahe_clips:
                for tiles in [6, 8, 12]:
                    r = best_photo_recipe.copy()
                    r[0, IDX_CLAHE] = blend
                    r[0, IDX_CLAHE_CLIP] = clip
                    r[0, IDX_CLAHE_TILES] = float(tiles)
                    clahe_recipes.append(r)
                    clahe_labels.append(f"{best_photo_recipe_str}+clahe={blend:.1f},clip={clip:.1f},t={tiles}")

        for batch_start in range(0, len(clahe_recipes), 12):
            batch_end = min(batch_start + 12, len(clahe_recipes))
            tensors = []
            for idx in range(batch_start, batch_end):
                t = run_with_recipe(crop_img, kps, clahe_recipes[idx], True, consumer_cfg, device)
                tensors.append(t)
            batch_t = torch.cat(tensors, dim=0)
            result = parseq.run(batch_t)
            if result is None:
                continue
            for j in range(batch_end - batch_start):
                text, conf = greedy_decode(result.distributions[j], result.vocab_chars, result.eos_index)
                if conf > best_enh_conf:
                    best_enh_conf = conf
                    best_enh_text = text
                    best_enh_recipe_str = clahe_labels[batch_start + j]

        # Deltas
        photo_delta = best_photo_conf - geom_conf
        enh_delta = best_enh_conf - best_photo_conf

        print(f"{name:<16} {scenario:<18} {geom_conf:>9.1%} {planner_conf:>9.1%} {best_photo_conf:>9.1%} ({photo_delta:+.1%}) {best_enh_conf:>9.1%} ({enh_delta:+.1%})  {best_enh_recipe_str:<40} {best_enh_text:<10}")

        total_geom += geom_conf
        total_planner += planner_conf
        total_best_photo += best_photo_conf
        total_best_enh += best_enh_conf
        n_images += 1

    print("=" * 130)
    print(f"{'AVERAGE':<16} {'':18} {total_geom/n_images:>9.1%} {total_planner/n_images:>9.1%} {total_best_photo/n_images:>9.1%}        {total_best_enh/n_images:>9.1%}")
    print()
    print("Column key:")
    print("  Geom-Only  = homography warp, no photometric corrections")
    print("  Planner    = current recipe planner output")
    print("  Best-Photo = best gain/gamma/contrast/sharpen found via sweep")
    print("  Best-Enh   = best CLAHE added on top of best photometric")


if __name__ == "__main__":
    main()
