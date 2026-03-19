"""Root conftest.py — imports fixtures from domain-specific conftest modules."""

from __future__ import annotations

# Re-export fixtures so pytest discovers them
from tests.conftest_training import (  # noqa: F401
    make_synthetic_yolo_dataset,
    sample_training_image,
    sample_training_images,
)
