"""Tests for drone augmentation pipeline (chunk-01).

Tests build_drone_pipeline() — phase-aware A.Compose with BboxParams,
dev-002/003 regression tests, real image validation, synthetic fallbacks.
"""

from __future__ import annotations

import albumentations as A
import cv2
import numpy as np
import pytest

from tests.conftest_training import make_yolo_config

# ---------------------------------------------------------------------------
# TestBuildDronePipeline (7 tests)
# ---------------------------------------------------------------------------


class TestBuildDronePipeline:
    """Tests for build_drone_pipeline phase dispatch and structure."""

    def test_warm_phase_empty_pipeline(self) -> None:
        """WARM phase returns A.Compose with 0 transforms."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "WARM")
        assert isinstance(pipeline, A.Compose)
        assert len(pipeline.transforms) == 0

    def test_ramp_phase_eleven_transforms(self) -> None:
        """RAMP phase returns pipeline with exactly 11 transforms (4 geometry + 7 degradation)."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")
        assert len(pipeline.transforms) == 11

    def test_refine_phase_eleven_transforms(self) -> None:
        """REFINE phase returns pipeline with 11 transforms (all present, scaled to half intensity)."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "REFINE")
        assert len(pipeline.transforms) == 11

    def test_drone_aug_disabled_returns_empty(self) -> None:
        """drone_aug_enabled=False returns empty pipeline for any phase."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        cfg = make_yolo_config(drone_aug_enabled=False)
        pipeline = build_drone_pipeline(cfg, "RAMP")
        assert len(pipeline.transforms) == 0

    def test_pipeline_has_bbox_params(self) -> None:
        """Pipeline has BboxParams configured (processors not None)."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")
        assert pipeline.processors is not None

    def test_unknown_phase_returns_empty(self) -> None:
        """Unknown phase string returns empty pipeline."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "UNKNOWN")
        assert len(pipeline.transforms) == 0

    def test_min_visibility_configured(self) -> None:
        """BboxParams has min_visibility=0.3."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")
        # BboxParams are stored in processors; check the params object
        for proc in pipeline.processors.values():
            params = proc.params
            if hasattr(params, "min_visibility"):
                assert params.min_visibility == pytest.approx(0.3)
                return
        pytest.fail("No BboxParams with min_visibility found")


# ---------------------------------------------------------------------------
# TestDroneGeometryTransforms (5 tests)
# ---------------------------------------------------------------------------


class TestDroneGeometryTransforms:
    """Tests for drone geometry transforms added for oblique viewing angles."""

    def test_ramp_has_affine_transform(self) -> None:
        """RAMP pipeline contains an A.Affine transform for oblique angle simulation."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")
        affine_transforms = [t for t in pipeline.transforms if isinstance(t, A.Affine)]
        assert len(affine_transforms) == 1, "Expected exactly 1 Affine transform in RAMP"

    def test_ramp_has_coarse_dropout(self) -> None:
        """RAMP pipeline contains A.CoarseDropout for occlusion simulation."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")
        dropout_transforms = [t for t in pipeline.transforms if isinstance(t, A.CoarseDropout)]
        assert len(dropout_transforms) == 1, "Expected exactly 1 CoarseDropout in RAMP"

    def test_perspective_scale_upgraded(self) -> None:
        """Perspective scale upper bound >= 0.15 (was 0.08, now 0.20)."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")
        perspective_transforms = [t for t in pipeline.transforms if isinstance(t, A.Perspective)]
        assert len(perspective_transforms) >= 1
        # Perspective stores scale as a tuple (min, max)
        scale = perspective_transforms[0].scale
        assert scale[1] >= 0.15, f"Perspective scale upper {scale[1]} should be >= 0.15"

    def test_geometry_before_degradation(self) -> None:
        """Affine (geometry) appears before MotionBlur (degradation) in transform list."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")
        affine_idx = next(i for i, t in enumerate(pipeline.transforms) if isinstance(t, A.Affine))
        motionblur_idx = next(
            i for i, t in enumerate(pipeline.transforms) if isinstance(t, A.MotionBlur)
        )
        assert affine_idx < motionblur_idx, (
            f"Affine (idx={affine_idx}) should come before MotionBlur (idx={motionblur_idx})"
        )

    def test_refine_has_geometry(self) -> None:
        """REFINE phase includes Affine and CoarseDropout (lighter versions)."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "REFINE")
        affine = [t for t in pipeline.transforms if isinstance(t, A.Affine)]
        dropout = [t for t in pipeline.transforms if isinstance(t, A.CoarseDropout)]
        assert len(affine) == 1, "REFINE should have 1 Affine"
        assert len(dropout) == 1, "REFINE should have 1 CoarseDropout"


# ---------------------------------------------------------------------------
# TestDroneAugDevFixes (2 tests)
# ---------------------------------------------------------------------------


class TestDroneAugDevFixes:
    """Regression tests for dev-002 and dev-003 fixes."""

    def test_perspective_uses_border_constant(self) -> None:
        """A.Perspective has border_mode==cv2.BORDER_CONSTANT (dev-002)."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")
        perspective_transforms = [t for t in pipeline.transforms if isinstance(t, A.Perspective)]
        assert len(perspective_transforms) >= 1, "No Perspective transform found"
        for t in perspective_transforms:
            assert t.border_mode == cv2.BORDER_CONSTANT

    def test_optical_distortion_shift_limit_fallback(self) -> None:
        """Pipeline builds without error, OpticalDistortion present (dev-003)."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        cfg = make_yolo_config(drone_aug_enabled=True)
        # Should not raise
        pipeline = build_drone_pipeline(cfg, "RAMP")
        distortion_transforms = [
            t for t in pipeline.transforms if isinstance(t, A.OpticalDistortion)
        ]
        assert len(distortion_transforms) >= 1, "No OpticalDistortion transform found"


# ---------------------------------------------------------------------------
# TestDroneAugOnRealImage (4 tests)
# ---------------------------------------------------------------------------


class TestDroneAugOnRealImage:
    """Tests using real training images from roboflow dataset."""

    @pytest.mark.real_data
    def test_ramp_pipeline_on_real_image(self, sample_training_image: tuple[str, str]) -> None:
        """RAMP pipeline on real image: output valid 3-ch image, bboxes valid YOLO format."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        img_path, label_path = sample_training_image
        img = cv2.imread(str(img_path))
        assert img is not None, f"Failed to load image: {img_path}"
        bboxes, class_labels = _parse_yolo_labels(str(label_path))

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")
        result = pipeline(image=img, bboxes=bboxes, class_labels=class_labels)

        out = result["image"]
        assert len(out.shape) == 3 and out.shape[2] == 3
        assert out.shape[0] > 0 and out.shape[1] > 0
        for bbox in result["bboxes"]:
            cx, cy, w, h = bbox
            assert 0 <= cx <= 1 and 0 <= cy <= 1
            assert 0 <= w <= 1 and 0 <= h <= 1

    @pytest.mark.real_data
    def test_refine_pipeline_on_real_image(self, sample_training_image: tuple[str, str]) -> None:
        """REFINE pipeline on real image: output valid, bboxes valid."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        img_path, label_path = sample_training_image
        img = cv2.imread(str(img_path))
        assert img is not None
        bboxes, class_labels = _parse_yolo_labels(str(label_path))

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "REFINE")
        result = pipeline(image=img, bboxes=bboxes, class_labels=class_labels)

        assert result["image"].shape[:2] == img.shape[:2]
        for bbox in result["bboxes"]:
            cx, cy, w, h = bbox
            assert 0 <= cx <= 1 and 0 <= cy <= 1

    @pytest.mark.real_data
    def test_augmentation_preserves_image_shape(
        self, sample_training_image: tuple[str, str]
    ) -> None:
        """Output image is 3-channel with valid dimensions after RAMP pipeline.

        Note: RandomScale may change HxW. We verify the output is still a
        valid 3-channel image (not corrupted or collapsed).
        """
        from training.yolo.drone_augmentations import build_drone_pipeline

        img_path, label_path = sample_training_image
        img = cv2.imread(str(img_path))
        assert img is not None
        bboxes, class_labels = _parse_yolo_labels(str(label_path))

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")
        result = pipeline(image=img, bboxes=bboxes, class_labels=class_labels)
        out_img = result["image"]
        assert len(out_img.shape) == 3
        assert out_img.shape[2] == 3
        assert out_img.shape[0] > 0 and out_img.shape[1] > 0

    @pytest.mark.real_data
    def test_augmentation_drops_small_bboxes(self, sample_training_image: tuple[str, str]) -> None:
        """min_visibility=0.3 filter drops small bboxes near edge (run 20x)."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        img_path, _ = sample_training_image
        img = cv2.imread(str(img_path))
        assert img is not None

        # Small bbox near image edge — vulnerable to being dropped by perspective
        bboxes = [[0.02, 0.02, 0.03, 0.03]]
        class_labels = [0]

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")

        dropped_at_least_once = False
        for _ in range(20):
            result = pipeline(image=img, bboxes=bboxes, class_labels=class_labels)
            if len(result["bboxes"]) < len(bboxes):
                dropped_at_least_once = True
                break

        assert dropped_at_least_once, (
            "Expected min_visibility=0.3 to drop a tiny bbox near edge in at least one of 20 runs"
        )


# ---------------------------------------------------------------------------
# TestDroneAugOnSyntheticImage (3 tests)
# ---------------------------------------------------------------------------


class TestDroneAugOnSyntheticImage:
    """Fallback tests using synthetic images (no real data needed)."""

    def test_ramp_pipeline_on_synthetic_640x640(self) -> None:
        """Synthetic 640x640 image through RAMP: no exception, valid 3-ch output."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        img = np.random.randint(0, 255, (640, 640, 3), dtype=np.uint8)
        bboxes = [[0.5, 0.5, 0.3, 0.2]]
        class_labels = [0]

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")
        result = pipeline(image=img, bboxes=bboxes, class_labels=class_labels)
        out = result["image"]
        assert len(out.shape) == 3 and out.shape[2] == 3
        assert out.shape[0] > 0 and out.shape[1] > 0

    def test_empty_bboxes_no_crash(self) -> None:
        """Empty bbox list through RAMP pipeline: no exception."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        img = np.random.randint(0, 255, (640, 640, 3), dtype=np.uint8)

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")
        result = pipeline(image=img, bboxes=[], class_labels=[])
        assert result["image"] is not None

    def test_pipeline_idempotent_type(self) -> None:
        """Output image dtype == np.uint8 after pipeline."""
        from training.yolo.drone_augmentations import build_drone_pipeline

        img = np.random.randint(0, 255, (640, 640, 3), dtype=np.uint8)
        bboxes = [[0.5, 0.5, 0.3, 0.2]]
        class_labels = [0]

        cfg = make_yolo_config(drone_aug_enabled=True)
        pipeline = build_drone_pipeline(cfg, "RAMP")
        result = pipeline(image=img, bboxes=bboxes, class_labels=class_labels)
        assert result["image"].dtype == np.uint8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_yolo_labels(label_path: str) -> tuple[list[list[float]], list[int]]:
    """Parse a YOLO-format label file into bboxes and class_labels."""
    bboxes: list[list[float]] = []
    class_labels: list[int] = []
    with open(label_path) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 5:
                cls = int(parts[0])
                cx, cy, w, h = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
                bboxes.append([cx, cy, w, h])
                class_labels.append(cls)
    return bboxes, class_labels
