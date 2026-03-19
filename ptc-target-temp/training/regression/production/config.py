"""ProductionConfig — extends RegressionConfig with EMA and synthetic data fields."""

from __future__ import annotations

from dataclasses import dataclass

from training.regression.config import RegressionConfig


@dataclass
class ProductionConfig(RegressionConfig):
    """Production training config combining all proven improvements.

    Extends RegressionConfig with:
    - EMA (Exponential Moving Average)
    - Synthetic data mixing
    """

    # --- EMA ---
    ema_enabled: bool = True
    ema_decay: float = 0.9999

    # --- Synthetic data ---
    synthetic_data_dir: str = "data/synthetic_crops"
    synthetic_annotations_path: str = "data/synthetic_annotations.json"
    synthetic_ratio: float = 0.5
