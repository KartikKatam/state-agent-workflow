from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class AugmentationPhase(IntEnum):
    """Curriculum augmentation phase for training."""

    NONE = 0
    MODERATE = 1
    FULL_DRONE = 2


@dataclass
class TrainingConfig:
    """All training hyperparameters for corner CNN training (EfficientNet-B3 backbone)."""

    # --- Input / model geometry ---
    input_h: int = 80
    input_w: int = 256
    pad_color: tuple[int, int, int] = (114, 114, 114)
    stride: int = 4

    # --- Heatmap target generation ---
    gaussian_sigma: float = 1.5
    gaussian_floor: float = 1e-4
    sigma_schedule: list[tuple[int, float]] | None = (
        None  # [(epoch, sigma), ...] e.g. [(0, 3.0), (100, 2.0), (200, 1.5)]
    )

    # --- Loss ---
    focal_alpha: float = 2.0
    focal_beta: float = 4.0
    lambda_offset: float = 1.0

    # --- Optimizer ---
    base_lr: float = 1e-4
    encoder_lr_mult: float = 0.05
    weight_decay: float = 5e-4
    warmup_epochs: int = 5
    warmup_start_lr: float = 1e-6

    # --- Schedule ---
    batch_size: int = 16
    total_epochs: int = 300
    grad_clip_max_norm: float = 10.0
    device: str = "cuda"

    # --- Freeze / unfreeze ---
    freeze_min_epochs: int = 20
    freeze_max_epochs: int = 80
    stagnation_window: int = 15
    stagnation_threshold: float = 0.01

    # --- Early stopping ---
    early_stopping_patience: int = 0  # 0 = disabled; stop if val PCK@4 doesn't improve for N epochs

    # --- Phase transitions ---
    phase3_pck_threshold: float = 0.30
    phase3_sustain_epochs: int = 5

    # --- HEM ---
    hem_weight_floor: float = 0.5
    hem_weight_ceiling: float = 3.0

    # --- Confidence ---
    confidence_threshold: float = 0.3

    # --- Checkpoint ---
    checkpoint_interval: int = 10
    checkpoint_dir: str = "checkpoints"

    # --- Vis / logging ---
    vis_interval: int = 10
    config_poll_interval: int = 30
    config_override_path: str = "training_config.json"

    # --- Data ---
    data_dir: str = "data/crops"
    annotations_path: str = "data/annotations.json"
    split_path: str = "data/split.json"
    lighting_labels_path: str = ""

    # --- W&B ---
    wandb_project: str = "corner-cnn-training"
    wandb_enabled: bool = True

    # --- Augmentation Phase 2 (MODERATE) ---
    aug_hflip_prob: float = 0.5
    aug_brightness_range: float = 0.3
    aug_brightness_prob: float = 0.8
    aug_contrast_range: float = 0.2
    aug_contrast_prob: float = 0.8
    aug_saturation_range: float = 0.2
    aug_saturation_prob: float = 0.5
    aug_rotation_range_p2: float = 5.0
    aug_rotation_prob_p2: float = 0.5
    aug_scale_range_p2: tuple[float, float] = (0.9, 1.1)
    aug_scale_prob_p2: float = 0.5
    aug_noise_sigma_p2: tuple[float, float] = (5.0, 10.0)
    aug_noise_prob_p2: float = 0.3

    # --- Augmentation Phase 3 (FULL_DRONE) ---
    aug_rotation_range_p3: float = 15.0
    aug_rotation_prob_p3: float = 0.5
    aug_perspective_range: float = 15.0
    aug_perspective_prob: float = 0.4
    aug_motion_blur_range: tuple[int, int] = (3, 7)
    aug_motion_blur_prob: float = 0.3
    aug_gaussian_blur_sigma: tuple[float, float] = (0.5, 1.5)
    aug_gaussian_blur_prob: float = 0.3
    aug_heavy_brightness_range: float = 0.5
    aug_heavy_brightness_prob: float = 0.4
    aug_gamma_range: tuple[float, float] = (0.5, 1.5)
    aug_jpeg_quality_range: tuple[int, int] = (30, 70)
    aug_jpeg_prob: float = 0.4
    aug_heavy_noise_sigma: tuple[float, float] = (10.0, 25.0)
    aug_heavy_noise_prob: float = 0.2
    aug_occlusion_area_range: tuple[float, float] = (0.05, 0.15)
    aug_occlusion_prob: float = 0.2
    aug_scale_range_p3: tuple[float, float] = (0.8, 1.2)
    aug_scale_prob_p3: float = 0.5
    aug_night_brightness: tuple[float, float] = (0.2, 0.4)
    aug_night_gamma: tuple[float, float] = (1.5, 2.0)
    aug_night_noise_sigma: tuple[float, float] = (15.0, 30.0)
    aug_night_prob: float = 0.15

    # --- Augmentation ramp-up ---
    aug_delay_after_unfreeze: int = 0  # epochs of NONE augmentation to keep after encoder unfreeze
    aug_ramp_epochs: int = 0  # epochs to ramp augmentation probabilities from 0→1 after delay
