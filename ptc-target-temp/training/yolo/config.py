"""YoloTrainingConfig — all training hyperparameters for YOLO fine-tuning."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _default_warm_aug() -> dict[str, float]:
    """WARM phase: gentle augmentation."""
    return {
        "mosaic": 0.3,
        "mixup": 0.0,
        "degrees": 0.0,
        "scale": 0.2,
        "perspective": 0.0,
        "hsv_h": 0.01,
        "hsv_s": 0.3,
        "hsv_v": 0.3,
        "fliplr": 0.5,
        "erasing": 0.0,
        "translate": 0.1,
    }


def _default_ramp_aug() -> dict[str, float]:
    """RAMP phase: aggressive augmentation."""
    return {
        "mosaic": 1.0,
        "mixup": 0.1,
        "degrees": 10.0,
        "scale": 0.5,
        "perspective": 0.001,
        "hsv_h": 0.015,
        "hsv_s": 0.7,
        "hsv_v": 0.4,
        "fliplr": 0.5,
        "erasing": 0.3,
        "translate": 0.2,
    }


def _default_refine_aug() -> dict[str, float]:
    """REFINE phase: moderate augmentation for fine-tuning."""
    return {
        "mosaic": 0.5,
        "mixup": 0.0,
        "degrees": 5.0,
        "scale": 0.3,
        "perspective": 0.0005,
        "hsv_h": 0.01,
        "hsv_s": 0.4,
        "hsv_v": 0.3,
        "fliplr": 0.5,
        "erasing": 0.1,
        "translate": 0.1,
    }


@dataclass
class YoloTrainingConfig:
    """All training hyperparameters for YOLO LPR fine-tuning."""

    # --- Paths ---
    data_yaml: str = "training/yolo/data.yaml"
    checkpoint_path: str = "models/yolov11m-lpr-base.pt"
    project_dir: str = "runs/yolo-lpr"
    run_name: str = "lpr-finetune"

    # --- Training core ---
    epochs: int = 100
    batch_size: int = -1
    imgsz: int = 640
    device: str = "cuda"
    optimizer: str = "AdamW"
    lr0: float = 0.001
    lrf: float = 0.01
    warmup_epochs: float = 5.0
    patience: int = 30
    weight_decay: float = 0.0005
    cos_lr: bool = True
    seed: int = 42
    workers: int = 8

    # --- Curriculum phase boundaries ---
    warm_end: float = 0.15
    peak_start: float = 0.40
    refine_start: float = 0.75
    refine_floor: float = 0.5

    # --- Phase augmentation dicts ---
    warm_aug: dict[str, float] = field(default_factory=_default_warm_aug)
    ramp_aug: dict[str, float] = field(default_factory=_default_ramp_aug)
    refine_aug: dict[str, float] = field(default_factory=_default_refine_aug)

    # --- Drone augmentation params ---
    drone_aug_enabled: bool = True
    # Geometry: oblique viewing angle (Affine shear + rotate)
    drone_affine_shear_x: tuple[int, int] = (-15, 15)
    drone_affine_shear_y: tuple[int, int] = (-25, 25)
    drone_affine_rotate: tuple[int, int] = (-15, 15)
    drone_affine_prob: float = 0.7
    # Geometry: keystone distortion (Perspective)
    drone_perspective_limit: float = 0.20
    drone_perspective_prob: float = 0.5
    # Geometry: altitude variation (RandomScale)
    drone_scale_limit: float = 0.40
    drone_scale_prob: float = 0.4
    # Geometry: roof/pillar occlusion (CoarseDropout)
    drone_occlusion_max_holes: int = 2
    drone_occlusion_max_h_frac: float = 0.15
    drone_occlusion_max_w_frac: float = 0.25
    drone_occlusion_prob: float = 0.25
    drone_distortion_limit: float = 0.3
    drone_distortion_prob: float = 0.3
    drone_motion_blur_limit: int = 7
    drone_motion_blur_prob: float = 0.3
    drone_defocus_radius: tuple[int, int] = (3, 7)
    drone_defocus_prob: float = 0.2
    drone_noise_var_limit: tuple[float, float] = (10.0, 50.0)
    drone_noise_prob: float = 0.3
    drone_compression_quality: tuple[int, int] = (40, 85)
    drone_compression_prob: float = 0.4
    drone_fog_coef: tuple[float, float] = (0.1, 0.3)
    drone_fog_prob: float = 0.15
    drone_brightness_limit: float = 0.3
    drone_brightness_prob: float = 0.4

    # --- Mining params ---
    mining_enabled: bool = True
    mining_min_epoch: int = 15
    mining_interval: int = 5
    mining_top_fraction: float = 0.10
    mining_oversample_factor: float = 3.0

    # --- Freeze params ---
    freeze_backbone: bool = True
    freeze_layers: int = 10
    freeze_epochs: int = 15

    # --- W&B ---
    wandb_enabled: bool = True
    wandb_project: str = "yolo-lpr-training"

    # --- Export ---
    export_onnx: bool = True
    export_trt: bool = True
    export_half: bool = True

    def get_phase(self, epoch: int) -> str:
        """Return curriculum phase name for the given epoch.

        Phase boundaries are fractional: epoch/epochs compared to warm_end and refine_start.
        """
        progress = epoch / self.epochs
        if progress < self.warm_end:
            return "WARM"
        if progress < self.refine_start:
            return "RAMP"
        return "REFINE"

    def get_intensity(self, epoch: int) -> float:
        """Continuous augmentation intensity [0.0, 1.0] for the given epoch.

        Piecewise-linear curve:
        - [0, warm_end): 0.0 (backbone frozen, clean data)
        - [warm_end, peak_start): linear ramp 0.0 → 1.0
        - [peak_start, refine_start): 1.0 (peak difficulty)
        - [refine_start, 1.0]: linear ramp 1.0 → refine_floor
        """
        progress = epoch / self.epochs if self.epochs > 0 else 0.0
        if progress < self.warm_end:
            return 0.0
        if progress < self.peak_start:
            ramp_len = self.peak_start - self.warm_end
            return (progress - self.warm_end) / ramp_len if ramp_len > 0 else 1.0
        if progress < self.refine_start:
            return 1.0
        tail_len = 1.0 - self.refine_start
        if tail_len <= 0:
            return self.refine_floor
        t = (progress - self.refine_start) / tail_len
        return 1.0 - (1.0 - self.refine_floor) * t

    def get_interpolated_aug(self, intensity: float) -> dict[str, float]:
        """Interpolate Ultralytics aug params between warm_aug and ramp_aug.

        Args:
            intensity: 0.0 = pure warm_aug, 1.0 = pure ramp_aug.
        """
        result: dict[str, float] = {}
        for key in self.warm_aug:
            warm_val = self.warm_aug[key]
            ramp_val = self.ramp_aug[key]
            result[key] = warm_val + intensity * (ramp_val - warm_val)
        return result

    def get_phase_aug(self, epoch: int) -> dict[str, float]:
        """Return the Ultralytics augmentation dict for the given epoch's phase."""
        phase = self.get_phase(epoch)
        if phase == "WARM":
            return self.warm_aug
        if phase == "RAMP":
            return self.ramp_aug
        return self.refine_aug
