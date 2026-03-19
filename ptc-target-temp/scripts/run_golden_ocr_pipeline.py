"""Run the full GPU preprocessing pipeline on golden crops using MODEL-predicted keypoints.

4-way comparison for every image:
  1. Original crop
  2. Homography warp only (geometry, no photometric)
  3. Base processing (geometry + photometric, no CLAHE enhance)
  4. Enhanced processing (geometry + photometric + CLAHE)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import torch

# --- Project imports ---
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from consumer.config import ConsumerConfig
from consumer.models import (
    EnhancedBatchSelection,
    RichQualityMetrics,
    RoiRichQuality,
)
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

# --- Paths ---
GOLDEN_DIR = Path("tests/fixtures/data/golden_crops")
MANIFEST_PATH = GOLDEN_DIR / "manifest.json"
CHECKPOINT_PATH = Path("training/regression/results/production_s2_300ep/best.pt")
OUTPUT_DIR = Path("results/ocr_pipeline_output")


def letterbox_crop(
    img: np.ndarray, target_h: int = 80, target_w: int = 256
) -> tuple[np.ndarray, float, int, int]:
    """Letterbox image to target size with gray padding."""
    h, w = img.shape[:2]
    scale = min(target_w / w, target_h / h)
    new_w, new_h = int(w * scale), int(h * scale)
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((target_h, target_w, 3), 128, dtype=np.uint8)
    pad_x = (target_w - new_w) // 2
    pad_y = (target_h - new_h) // 2
    canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized
    return canvas, scale, pad_x, pad_y


def predict_keypoints(
    model: ProductionCornerNet, crop: np.ndarray, device: torch.device
) -> list[tuple[float, float]]:
    """Run model inference and return 4 keypoints in original crop coordinates."""
    lb, scale, pad_x, pad_y = letterbox_crop(crop)
    tensor = (
        torch.from_numpy(lb[:, :, ::-1].copy())
        .float()
        .permute(2, 0, 1)
        .unsqueeze(0)
        / 255.0
    )
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


def make_roi_rq(
    entry: dict,
    crop_img: np.ndarray,
    model_kps: list[tuple[float, float]],
    force_enhance_eligible: bool = False,
) -> RoiRichQuality:
    """Build RoiRichQuality using MODEL keypoints but real quality metrics."""
    m = entry["metrics"]
    name = entry["name"]
    h, w = crop_img.shape[:2]

    roi_img = RoiImage(
        track_id=name,
        crop_img=crop_img,
        bbox=[0.0, 0.0, float(m["plate_width_px"]), float(m["plate_height_px"])],
        padded_bbox=[0.0, 0.0, float(w), float(h)],
        frame_idx=0,
        confidence=0.9,
        frame_width=1920,
        frame_height=1080,
        keypoints=model_kps,
        keypoint_scores=[0.9] * 4,
        crop_width=w,
        crop_height=h,
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
        roi=roi_img,
        metrics=fast_metrics,
        quality_score=0.5,
        passes_min_quality=True,
        thumb_gray=np.zeros((16, 32), dtype=np.uint8),
    )

    enhance_eligible = force_enhance_eligible or bool(m["enhance_eligible"])

    rich_metrics = RichQualityMetrics(
        track_id=name,
        frame_idx=0,
        canonical_width=256,
        canonical_height=128,
        plate_width_px=float(m["plate_width_px"]),
        plate_height_px=float(m["plate_height_px"]),
        crop_clip_fraction=0.0,
        detection_confidence=0.9,
        keypoints=model_kps,
        keypoint_scores=[0.9] * 4,
        keypoint_confidence_min=0.9,
        keypoint_confidence_mean=0.9,
        skew_degrees=m.get("skew_degrees"),
        perspective_score=m.get("perspective_score"),
        perspective_direction=m.get("perspective_direction"),
        luminance_mean=float(m["luminance_mean"]),
        luminance_p05=float(m["luminance_p05"]),
        luminance_p95=float(m["luminance_p95"]),
        black_clip_fraction=float(m["black_clip_fraction"]),
        white_clip_fraction=float(m["white_clip_fraction"]),
        global_contrast=float(m["global_contrast"]),
        local_contrast=float(m["local_contrast"]),
        tenengrad=float(m["tenengrad"]),
        tenengrad_horizontal=float(m["tenengrad_horizontal"]),
        tenengrad_vertical=float(m["tenengrad_vertical"]),
        blur_anisotropy=float(m["blur_anisotropy"]),
        noise_std=float(m["noise_std"]),
        flat_region_fraction=float(m["flat_region_fraction"]),
        too_small=bool(m["too_small"]),
        clipped=False,
        very_blurry=bool(m["very_blurry"]),
        mildly_soft=bool(m["mildly_soft"]),
        exposure_bad=bool(m["exposure_bad"]),
        low_contrast=bool(m["low_contrast"]),
        noisy=bool(m["noisy"]),
        vertical_edges_weak=bool(m["vertical_edges_weak"]),
        horizontal_edges_weak=bool(m["horizontal_edges_weak"]),
        top8_eligible=bool(m["top8_eligible"]),
        enhance_eligible=enhance_eligible,
        homography_eligible=bool(m["homography_eligible"]),
        bbox_center_x_normalized=0.5,
        bbox_center_y_normalized=0.5,
        diversity_signature=np.array([0.0, 0.4, 0.5, 0.0], dtype=np.float32),
        quad_area_px=None,
        edge_ratio=None,
    )

    return RoiRichQuality(roi_fq=roi_fq, metrics=rich_metrics)


def gpu_tensor_to_bgr(t: torch.Tensor) -> np.ndarray:
    """Convert (3,H,W) float32 tensor in [0,1] to uint8 BGR."""
    img = t.cpu().clamp(0, 1)
    img = (img.permute(1, 2, 0).numpy() * 255).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


def parseq_tensor_to_bgr(t: torch.Tensor) -> np.ndarray:
    """Convert (3,H,W) tensor in [-1,1] (PARSeq range) to uint8 BGR."""
    img = ((t.cpu().float() + 1.0) / 2.0).clamp(0, 1)
    img = (img.permute(1, 2, 0).numpy() * 255).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


def run_geometry_only(
    crop: np.ndarray,
    kps: list[tuple[float, float]],
    cfg: ConsumerConfig,
    device: torch.device,
) -> np.ndarray:
    """Run ONLY the geometry (homography) stage, return BGR uint8 image."""
    images = _safe_ingest([crop], device)
    # Build an identity recipe but with warp enabled
    identity = np.array(
        [[RECIPE_IDENTITY[k] for k in RECIPE_KEYS]], dtype=np.float32
    )
    identity[0, 0] = 1.0  # warp_enable
    identity[0, 1] = 1.0  # tight_crop_enable
    identity[0, 3] = cfg.warp_margin  # warp_margin
    recipe_t = torch.tensor(identity, dtype=torch.float32, device=device)
    result = _apply_geometry(images, recipe_t, [kps], cfg)
    return gpu_tensor_to_bgr(result[0])


def run_base_pipeline(
    entry: dict,
    crop: np.ndarray,
    kps: list[tuple[float, float]],
    cfg: ConsumerConfig,
    device: torch.device,
) -> np.ndarray:
    """Run full base pipeline (geometry + photometric, no CLAHE enhance)."""
    roi_rq = make_roi_rq(entry, crop, kps, force_enhance_eligible=False)
    batch = EnhancedBatchSelection(
        track_id="base-run",
        version=1,
        base_rois=[roi_rq],
        enhance_rois=[],
        duplicate_groups={},
    )
    recipe, _, is_enh, _, _ = generate_recipe_tensor(batch, cfg)

    # Run stages manually: ingest → geometry → photometric → normalize
    images = _safe_ingest([crop], device)
    quads: list[list[tuple[float, float]] | None] = [kps]
    recipe_t = torch.tensor(recipe, dtype=torch.float32, device=device)
    result = _apply_geometry(images, recipe_t, quads, cfg)
    result = _apply_photometric_ops(result, recipe_t, cfg)
    # No CLAHE for base
    result = result * 2 - 1  # PARSeq normalize
    return parseq_tensor_to_bgr(result[0])


def run_enhanced_pipeline(
    entry: dict,
    crop: np.ndarray,
    kps: list[tuple[float, float]],
    cfg: ConsumerConfig,
    device: torch.device,
) -> np.ndarray:
    """Run full enhanced pipeline (geometry + photometric + CLAHE)."""
    roi_rq = make_roi_rq(entry, crop, kps, force_enhance_eligible=True)
    batch = EnhancedBatchSelection(
        track_id="enh-run",
        version=1,
        base_rois=[],
        enhance_rois=[roi_rq],
        duplicate_groups={},
    )
    recipe, _, is_enh, _, _ = generate_recipe_tensor(batch, cfg)

    # Run all stages including CLAHE
    images = _safe_ingest([crop], device)
    quads: list[list[tuple[float, float]] | None] = [kps]
    recipe_t = torch.tensor(recipe, dtype=torch.float32, device=device)
    is_enh_t = torch.tensor(is_enh, dtype=torch.bool, device=device)
    result = _apply_geometry(images, recipe_t, quads, cfg)
    result = _apply_photometric_ops(result, recipe_t, cfg)
    result = _apply_clahe_gpu(result, recipe_t, is_enh_t, cfg)
    result = result * 2 - 1
    return parseq_tensor_to_bgr(result[0])


def save_4way(
    original: np.ndarray,
    geom_only: np.ndarray,
    base_proc: np.ndarray,
    enh_proc: np.ndarray,
    name: str,
    scenario: str,
    out_dir: Path,
) -> None:
    """Save 4-way: original | homography only | base pipeline | enhanced pipeline."""
    proc_h, proc_w = base_proc.shape[:2]  # 32x128
    orig_h, orig_w = original.shape[:2]
    scale = proc_h / orig_h
    orig_resized = cv2.resize(
        original, (int(orig_w * scale), proc_h), interpolation=cv2.INTER_LINEAR
    )

    label_h = 22
    gap = 10
    panels = [orig_resized, geom_only, base_proc, enh_proc]
    labels = ["Original Crop", "Homography Only", "Base Pipeline", "Enhanced Pipeline"]
    total_w = sum(p.shape[1] for p in panels) + gap * (len(panels) - 1)
    canvas_h = proc_h + label_h + 8
    canvas = np.full((canvas_h, total_w, 3), 255, dtype=np.uint8)

    y_off = label_h + 4
    x = 0
    for panel, label in zip(panels, labels):
        pw = panel.shape[1]
        canvas[y_off : y_off + proc_h, x : x + pw] = panel
        cv2.putText(
            canvas, label, (x + 2, label_h - 5),
            cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 0, 0), 1,
        )
        x += pw + gap

    out_path = out_dir / f"{name}_{scenario}_4way.png"
    cv2.imwrite(str(out_path), canvas)
    print(f"  Saved: {out_path}")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    # --- Load model ---
    print("\n=== Loading stride-2 model ===")
    cfg = ProductionConfig()
    cfg.stride = 2
    model = ProductionCornerNet(cfg)
    ckpt = torch.load(str(CHECKPOINT_PATH), map_location=device, weights_only=True)
    if "ema_state_dict" in ckpt:
        model.load_state_dict(ckpt["ema_state_dict"])
        print("Loaded EMA weights")
    else:
        model.load_state_dict(ckpt["model_state_dict"])
        print("Loaded model weights")
    model.to(device)
    model.eval()

    # --- Load manifest & predict ---
    print("\n=== Predicting keypoints ===")
    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    consumer_cfg = ConsumerConfig()

    for entry in manifest["crops"]:
        crop_img = np.load(GOLDEN_DIR / entry["file"])
        name = entry["name"]
        scenario = entry["scenario"]
        model_kps = predict_keypoints(model, crop_img, device)

        print(f"\n--- {name} ({scenario}) — {crop_img.shape[1]}x{crop_img.shape[0]} ---")
        print(f"  Model KPs: {[(round(x, 1), round(y, 1)) for x, y in model_kps]}")

        # Stage 1: Homography only
        geom_img = run_geometry_only(crop_img, model_kps, consumer_cfg, device)

        # Stage 2: Base pipeline (geometry + photometric, no CLAHE)
        base_img = run_base_pipeline(entry, crop_img, model_kps, consumer_cfg, device)

        # Stage 3: Enhanced pipeline (geometry + photometric + CLAHE)
        enh_img = run_enhanced_pipeline(entry, crop_img, model_kps, consumer_cfg, device)

        # Save 4-way comparison
        save_4way(crop_img, geom_img, base_img, enh_img, name, scenario, OUTPUT_DIR)

    print(f"\n=== Done! All 4-way comparisons saved to: {OUTPUT_DIR.resolve()} ===")


if __name__ == "__main__":
    main()
