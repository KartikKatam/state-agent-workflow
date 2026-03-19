"""Tests for synthetic data generation pipeline and new augmentations."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from training.augmentations import (
    _aug_color_temperature,
    _aug_defocus_blur,
    _aug_directional_motion_blur,
    _aug_glare_specular,
    _aug_poisson_noise,
    _aug_sensor_banding,
    _aug_shadow_overlay,
    apply_augmentation,
)
from training.config import AugmentationPhase, TrainingConfig

# ===================================================================
# Fixtures
# ===================================================================


def make_plate_image(h: int = 80, w: int = 256) -> np.ndarray:
    """Create a synthetic plate image for testing."""
    rng = np.random.RandomState(42)
    return rng.randint(50, 200, (h, w, 3), dtype=np.uint8)


def make_plate_corners(
    w: int = 256, h: int = 80, margin: float = 0.15
) -> list[tuple[float, float]]:
    """Create canonical TL, TR, BR, BL plate corners well inside bounds."""
    mx = w * margin
    my = h * margin
    return [
        (mx, my),  # TL
        (w - 1 - mx, my),  # TR
        (w - 1 - mx, h - 1 - my),  # BR
        (mx, h - 1 - my),  # BL
    ]


@pytest.fixture
def sample_plate_crop() -> tuple[np.ndarray, list[tuple[float, float]]]:
    """A realistic synthetic plate crop (200x60) with known corners."""
    img = make_plate_image(h=60, w=200)
    corners = make_plate_corners(w=200, h=60)
    return img, corners


@pytest.fixture
def sample_vehicle_rear() -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """A synthetic vehicle-rear image (400x300) with a known plate bbox."""
    rng = np.random.RandomState(123)
    img = rng.randint(30, 220, (300, 400, 3), dtype=np.uint8)
    # Place a "plate region" at a realistic location
    plate_bbox = (120, 180, 280, 230)  # x1, y1, x2, y2
    return img, plate_bbox


@pytest.fixture
def sample_scene_background(tmp_path: Path) -> Path:
    """A synthetic scene background image (640x480), saved to file."""
    rng = np.random.RandomState(456)
    img = rng.randint(20, 240, (480, 640, 3), dtype=np.uint8)
    path = tmp_path / "scene_bg.jpg"
    cv2.imwrite(str(path), img)
    return path


@pytest.fixture
def source_plates_dir(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a temp directory with 5 synthetic plate crops + annotations + split."""
    crops_dir = tmp_path / "crops"
    crops_dir.mkdir()

    rng = np.random.RandomState(42)
    annotations: dict[str, dict[str, list[list[float]]]] = {}
    train_ids: list[str] = []
    val_ids: list[str] = []

    for i in range(5):
        fname = f"plate_{i:04d}.jpg"
        img = rng.randint(40, 200, (50, 200, 3), dtype=np.uint8)
        cv2.imwrite(str(crops_dir / fname), img)

        # Corners well inside bounds
        corners = [
            [30.0, 7.5],
            [170.0, 7.5],
            [170.0, 42.5],
            [30.0, 42.5],
        ]
        annotations[fname] = {"corners": corners}

        if i < 4:
            train_ids.append(fname)
        else:
            val_ids.append(fname)

    # Add a skipped entry
    annotations["plate_skip.jpg"] = {"corners": None, "skipped": True}

    ann_path = tmp_path / "annotations.json"
    ann_path.write_text(json.dumps(annotations))

    split_path = tmp_path / "split.json"
    split_path.write_text(json.dumps({"train": train_ids, "val": val_ids}))

    return crops_dir, ann_path, split_path


@pytest.fixture
def vehicle_rears_dir(tmp_path: Path) -> Path:
    """Create a temp directory mimicking Plat Kendaraan YOLO format."""
    import yaml

    dataset_dir = tmp_path / "plat-kendaraan"
    train_images = dataset_dir / "train" / "images"
    train_labels = dataset_dir / "train" / "labels"
    train_images.mkdir(parents=True)
    train_labels.mkdir(parents=True)

    rng = np.random.RandomState(789)
    for i in range(5):
        img = rng.randint(20, 230, (300, 400, 3), dtype=np.uint8)
        cv2.imwrite(str(train_images / f"veh_{i:04d}.jpg"), img)
        # YOLO label: class=2 (license-plate), cx cy w h normalized
        label = "2 0.5 0.7 0.3 0.12\n"
        (train_labels / f"veh_{i:04d}.txt").write_text(label)

    # data.yaml
    data_yaml = {
        "names": {0: "bus", 1: "car", 2: "license-plate", 3: "motorcycle", 4: "truck"},
    }
    (dataset_dir / "data.yaml").write_text(yaml.dump(data_yaml))

    return dataset_dir


@pytest.fixture
def scene_backgrounds_dir(tmp_path: Path) -> Path:
    """Create a temp directory with 3 synthetic scene background images."""
    scene_dir = tmp_path / "au-air" / "images"
    scene_dir.mkdir(parents=True)

    rng = np.random.RandomState(321)
    for i in range(3):
        img = rng.randint(10, 250, (480, 640, 3), dtype=np.uint8)
        cv2.imwrite(str(scene_dir / f"scene_{i:04d}.jpg"), img)

    return tmp_path / "au-air"


# ===================================================================
# Test Group 1: New augmentation functions
# ===================================================================


class TestShadowOverlay:
    def test_shadow_changes_image(self) -> None:
        """Shadow overlay modifies pixel values."""
        img = make_plate_image()
        rng = np.random.RandomState(42)
        result = _aug_shadow_overlay(img, rng, (0.3, 0.6))
        assert not np.array_equal(result, img)

    def test_shadow_preserves_shape(self) -> None:
        """Output shape matches input shape."""
        img = make_plate_image()
        rng = np.random.RandomState(42)
        result = _aug_shadow_overlay(img, rng, (0.3, 0.6))
        assert result.shape == img.shape

    def test_shadow_deterministic_with_seed(self) -> None:
        """Same RNG seed produces same shadow."""
        img = make_plate_image()
        r1 = _aug_shadow_overlay(img.copy(), np.random.RandomState(42), (0.3, 0.6))
        r2 = _aug_shadow_overlay(img.copy(), np.random.RandomState(42), (0.3, 0.6))
        np.testing.assert_array_equal(r1, r2)

    def test_shadow_output_range(self) -> None:
        """Output stays in [0, 255]."""
        img = make_plate_image()
        rng = np.random.RandomState(42)
        result = _aug_shadow_overlay(img, rng, (0.3, 0.6))
        assert result.min() >= 0
        assert result.max() <= 255


class TestGlareSpecular:
    def test_glare_adds_bright_region(self) -> None:
        """Glare increases brightness in some region."""
        img = np.full((80, 256, 3), 100, dtype=np.uint8)
        rng = np.random.RandomState(42)
        result = _aug_glare_specular(img, rng, (0.3, 0.6))
        # Glare should increase some pixel values above 100
        assert result.max() > 100

    def test_glare_preserves_shape(self) -> None:
        img = make_plate_image()
        rng = np.random.RandomState(42)
        result = _aug_glare_specular(img, rng, (0.3, 0.6))
        assert result.shape == img.shape

    def test_glare_output_range(self) -> None:
        img = make_plate_image()
        rng = np.random.RandomState(42)
        result = _aug_glare_specular(img, rng, (0.3, 0.6))
        assert result.min() >= 0
        assert result.max() <= 255


class TestColorTemperature:
    def test_warm_shift_effect(self) -> None:
        """Color temperature shift changes pixel values."""
        img = np.full((80, 256, 3), 128, dtype=np.uint8)
        rng = np.random.RandomState(42)
        result = _aug_color_temperature(img, rng, 10.0, 15.0)
        assert not np.array_equal(result, img)

    def test_color_temp_preserves_shape(self) -> None:
        img = make_plate_image()
        rng = np.random.RandomState(42)
        result = _aug_color_temperature(img, rng, 10.0, 15.0)
        assert result.shape == img.shape

    def test_color_temp_deterministic(self) -> None:
        img = make_plate_image()
        r1 = _aug_color_temperature(img.copy(), np.random.RandomState(42), 10.0, 15.0)
        r2 = _aug_color_temperature(img.copy(), np.random.RandomState(42), 10.0, 15.0)
        np.testing.assert_array_equal(r1, r2)


class TestDirectionalMotionBlur:
    def test_directional_blur_changes_image(self) -> None:
        """Directional motion blur changes pixel values."""
        img = make_plate_image()
        rng = np.random.RandomState(42)
        result = _aug_directional_motion_blur(img, rng, (5, 11))
        assert not np.array_equal(result, img)

    def test_directional_blur_preserves_shape(self) -> None:
        img = make_plate_image()
        rng = np.random.RandomState(42)
        result = _aug_directional_motion_blur(img, rng, (5, 11))
        assert result.shape == img.shape

    def test_directional_blur_deterministic(self) -> None:
        img = make_plate_image()
        r1 = _aug_directional_motion_blur(img.copy(), np.random.RandomState(42), (5, 11))
        r2 = _aug_directional_motion_blur(img.copy(), np.random.RandomState(42), (5, 11))
        np.testing.assert_array_equal(r1, r2)


class TestPoissonNoise:
    def test_poisson_adds_noise(self) -> None:
        """Poisson noise changes pixel values."""
        img = np.full((80, 256, 3), 128, dtype=np.uint8)
        rng = np.random.RandomState(42)
        result = _aug_poisson_noise(img, rng, (30.0, 60.0))
        assert not np.array_equal(result, img)

    def test_poisson_output_range(self) -> None:
        """Output stays in [0, 255]."""
        img = make_plate_image()
        rng = np.random.RandomState(42)
        result = _aug_poisson_noise(img, rng, (30.0, 60.0))
        assert result.min() >= 0
        assert result.max() <= 255

    def test_poisson_preserves_shape(self) -> None:
        img = make_plate_image()
        rng = np.random.RandomState(42)
        result = _aug_poisson_noise(img, rng, (30.0, 60.0))
        assert result.shape == img.shape


class TestSensorBanding:
    def test_banding_changes_image(self) -> None:
        """Banding noise modifies pixel values."""
        img = np.full((80, 256, 3), 128, dtype=np.uint8)
        rng = np.random.RandomState(42)
        result = _aug_sensor_banding(img, rng, (5.0, 15.0))
        assert not np.array_equal(result, img)

    def test_banding_preserves_shape(self) -> None:
        img = make_plate_image()
        rng = np.random.RandomState(42)
        result = _aug_sensor_banding(img, rng, (5.0, 15.0))
        assert result.shape == img.shape

    def test_banding_creates_horizontal_pattern(self) -> None:
        """Banding noise varies by row — rows within a band should be uniform."""
        img = np.full((80, 256, 3), 128, dtype=np.uint8)
        rng = np.random.RandomState(42)
        result = _aug_sensor_banding(img, rng, (5.0, 15.0))
        # Rows within the same band should be identical
        # (for uniform input, the output for adjacent rows in same band are identical)
        diff = result.astype(np.int16) - 128
        # At least some rows should have non-zero offset
        row_means = diff.mean(axis=(1, 2))
        assert not np.all(row_means == 0)


class TestDefocusBlur:
    def test_defocus_blurs_image(self) -> None:
        """Defocus reduces high-frequency content."""
        img = make_plate_image()
        rng = np.random.RandomState(42)
        result = _aug_defocus_blur(img, 2.0, rng)
        # Blurred image should have lower variance in high-frequency
        # Simple check: result differs from input
        assert not np.array_equal(result, img)

    def test_defocus_preserves_shape(self) -> None:
        img = make_plate_image()
        rng = np.random.RandomState(42)
        result = _aug_defocus_blur(img, 2.0, rng)
        assert result.shape == img.shape


# ===================================================================
# Test Group 2: Augmentation pipeline integration
# ===================================================================


class TestNewAugmentationsInPipeline:
    def _make_cfg_with_new_augs(self, prob: float = 1.0) -> TrainingConfig:
        """Create config with all new augmentation probs set to given value."""
        return TrainingConfig(
            aug_shadow_prob=prob,
            aug_glare_prob=prob,
            aug_color_temp_prob=prob,
            aug_directional_blur_prob=prob,
            aug_poisson_prob=prob,
            aug_sensor_banding_prob=prob,
            aug_defocus_prob=prob,
            # Disable geometric to isolate photometric
            aug_hflip_prob=0.0,
            aug_rotation_prob_p3=0.0,
            aug_scale_prob_p3=0.0,
            aug_perspective_prob=0.0,
            # Disable other photometric to isolate new ones
            aug_brightness_prob=0.0,
            aug_contrast_prob=0.0,
            aug_saturation_prob=0.0,
            aug_heavy_noise_prob=0.0,
            aug_heavy_brightness_prob=0.0,
            aug_motion_blur_prob=0.0,
            aug_gaussian_blur_prob=0.0,
            aug_jpeg_prob=0.0,
            aug_occlusion_prob=0.0,
            aug_night_prob=0.0,
        )

    def test_full_drone_with_new_augmentations(self) -> None:
        """FULL_DRONE phase can apply all new augmentations without error."""
        cfg = self._make_cfg_with_new_augs(prob=1.0)
        img = make_plate_image()
        corners = make_plate_corners()

        rng = np.random.RandomState(42)
        with patch("training.augmentations._rng", rng):
            result = apply_augmentation(img, corners, AugmentationPhase.FULL_DRONE, cfg)
        assert result is not None

    def test_new_augmentations_respect_ramp_factor(self) -> None:
        """With aug_ramp_factor=0, no new augmentations apply."""
        cfg = self._make_cfg_with_new_augs(prob=1.0)
        img = np.full((80, 256, 3), 128, dtype=np.uint8)
        corners = make_plate_corners()

        rng = np.random.RandomState(42)
        with patch("training.augmentations._rng", rng):
            result = apply_augmentation(
                img,
                corners,
                AugmentationPhase.FULL_DRONE,
                cfg,
                aug_ramp_factor=0.0,
            )
        assert result is not None
        out_img, _ = result
        # With all probs * 0.0 = 0, image should be unchanged
        np.testing.assert_array_equal(out_img, img)

    def test_corners_unchanged_by_photometric(self) -> None:
        """All 7 new augmentations are photometric — corners must not change."""
        cfg = self._make_cfg_with_new_augs(prob=1.0)
        img = make_plate_image()
        corners = make_plate_corners()

        for seed in range(10):
            rng = np.random.RandomState(seed)
            with patch("training.augmentations._rng", rng):
                result = apply_augmentation(
                    img.copy(),
                    list(corners),
                    AugmentationPhase.FULL_DRONE,
                    cfg,
                )
            if result is not None:
                _, out_corners = result
                for i, (orig, out) in enumerate(zip(corners, out_corners, strict=True)):
                    assert orig[0] == pytest.approx(out[0], abs=0.01), (
                        f"seed={seed}, corner[{i}] x changed: {orig[0]} -> {out[0]}"
                    )
                    assert orig[1] == pytest.approx(out[1], abs=0.01), (
                        f"seed={seed}, corner[{i}] y changed: {orig[1]} -> {out[1]}"
                    )


# ===================================================================
# Test Group 3: Compositing pipeline
# ===================================================================


class TestCompositeOnVehicleRear:
    def test_composite_produces_valid_image(
        self,
        sample_plate_crop: tuple[np.ndarray, list[tuple[float, float]]],
        sample_vehicle_rear: tuple[np.ndarray, tuple[int, int, int, int]],
    ) -> None:
        """Composite output is a valid BGR uint8 image."""
        from tools.synthetic_data.generate_composites import composite_on_vehicle_rear

        plate_img, plate_corners = sample_plate_crop
        vehicle_img, plate_bbox = sample_vehicle_rear
        rng = np.random.RandomState(42)

        composite, corners = composite_on_vehicle_rear(
            plate_img, plate_corners, vehicle_img, plate_bbox, rng
        )
        assert composite.dtype == np.uint8
        assert composite.ndim == 3
        assert composite.shape[2] == 3

    def test_corners_inside_bounds(
        self,
        sample_plate_crop: tuple[np.ndarray, list[tuple[float, float]]],
        sample_vehicle_rear: tuple[np.ndarray, tuple[int, int, int, int]],
    ) -> None:
        """All 4 output corners are within composite image bounds."""
        from tools.synthetic_data.generate_composites import composite_on_vehicle_rear

        plate_img, plate_corners = sample_plate_crop
        vehicle_img, plate_bbox = sample_vehicle_rear
        rng = np.random.RandomState(42)

        composite, corners = composite_on_vehicle_rear(
            plate_img, plate_corners, vehicle_img, plate_bbox, rng
        )
        h, w = composite.shape[:2]
        for i, (cx, cy) in enumerate(corners):
            assert 0 <= cx < w, f"corner[{i}] x={cx} out of [0,{w})"
            assert 0 <= cy < h, f"corner[{i}] y={cy} out of [0,{h})"

    def test_corners_form_convex_quad(
        self,
        sample_plate_crop: tuple[np.ndarray, list[tuple[float, float]]],
        sample_vehicle_rear: tuple[np.ndarray, tuple[int, int, int, int]],
    ) -> None:
        """Output corners form a convex quadrilateral."""
        from tools.synthetic_data.generate_composites import (
            composite_on_vehicle_rear,
            validate_composite,
        )

        plate_img, plate_corners = sample_plate_crop
        vehicle_img, plate_bbox = sample_vehicle_rear
        rng = np.random.RandomState(42)

        composite, corners = composite_on_vehicle_rear(
            plate_img, plate_corners, vehicle_img, plate_bbox, rng
        )
        h, w = composite.shape[:2]
        assert validate_composite(corners, w, h)

    def test_deterministic_with_seed(
        self,
        sample_plate_crop: tuple[np.ndarray, list[tuple[float, float]]],
        sample_vehicle_rear: tuple[np.ndarray, tuple[int, int, int, int]],
    ) -> None:
        """Same seed produces identical composite + corners."""
        from tools.synthetic_data.generate_composites import composite_on_vehicle_rear

        plate_img, plate_corners = sample_plate_crop
        vehicle_img, plate_bbox = sample_vehicle_rear

        c1, corners1 = composite_on_vehicle_rear(
            plate_img,
            plate_corners,
            vehicle_img,
            plate_bbox,
            np.random.RandomState(42),
        )
        c2, corners2 = composite_on_vehicle_rear(
            plate_img,
            plate_corners,
            vehicle_img,
            plate_bbox,
            np.random.RandomState(42),
        )
        np.testing.assert_array_equal(c1, c2)
        assert corners1 == corners2


class TestCompositeOnScenePatch:
    def test_composite_produces_valid_image(
        self,
        sample_plate_crop: tuple[np.ndarray, list[tuple[float, float]]],
        sample_scene_background: Path,
    ) -> None:
        from tools.synthetic_data.generate_composites import composite_on_scene_patch

        plate_img, plate_corners = sample_plate_crop
        rng = np.random.RandomState(42)

        composite, corners = composite_on_scene_patch(
            plate_img, plate_corners, sample_scene_background, rng
        )
        assert composite.dtype == np.uint8
        assert composite.ndim == 3
        assert composite.shape[2] == 3

    def test_corners_inside_bounds(
        self,
        sample_plate_crop: tuple[np.ndarray, list[tuple[float, float]]],
        sample_scene_background: Path,
    ) -> None:
        from tools.synthetic_data.generate_composites import composite_on_scene_patch

        plate_img, plate_corners = sample_plate_crop
        rng = np.random.RandomState(42)

        composite, corners = composite_on_scene_patch(
            plate_img, plate_corners, sample_scene_background, rng
        )
        h, w = composite.shape[:2]
        for i, (cx, cy) in enumerate(corners):
            assert 0 <= cx < w, f"corner[{i}] x={cx} out of [0,{w})"
            assert 0 <= cy < h, f"corner[{i}] y={cy} out of [0,{h})"

    def test_deterministic_with_seed(
        self,
        sample_plate_crop: tuple[np.ndarray, list[tuple[float, float]]],
        sample_scene_background: Path,
    ) -> None:
        from tools.synthetic_data.generate_composites import composite_on_scene_patch

        plate_img, plate_corners = sample_plate_crop

        c1, corners1 = composite_on_scene_patch(
            plate_img,
            plate_corners,
            sample_scene_background,
            np.random.RandomState(42),
        )
        c2, corners2 = composite_on_scene_patch(
            plate_img,
            plate_corners,
            sample_scene_background,
            np.random.RandomState(42),
        )
        np.testing.assert_array_equal(c1, c2)
        assert corners1 == corners2


class TestValidateComposite:
    def test_accepts_valid_corners(self) -> None:
        from tools.synthetic_data.generate_composites import validate_composite

        corners: list[tuple[float, float]] = [
            (30.0, 10.0),
            (170.0, 10.0),
            (170.0, 50.0),
            (30.0, 50.0),
        ]
        assert validate_composite(corners, 200, 60)

    def test_rejects_oob_corners(self) -> None:
        from tools.synthetic_data.generate_composites import validate_composite

        corners: list[tuple[float, float]] = [
            (-5.0, 10.0),  # OOB
            (170.0, 10.0),
            (170.0, 50.0),
            (30.0, 50.0),
        ]
        assert not validate_composite(corners, 200, 60)

    def test_rejects_degenerate_quad(self) -> None:
        """Reject when plate width < 8px or height < 4px."""
        from tools.synthetic_data.generate_composites import validate_composite

        corners: list[tuple[float, float]] = [
            (100.0, 30.0),
            (105.0, 30.0),  # width = 5 < 8
            (105.0, 35.0),
            (100.0, 35.0),
        ]
        assert not validate_composite(corners, 200, 60)

    def test_rejects_non_convex(self) -> None:
        from tools.synthetic_data.generate_composites import validate_composite

        # Create a non-convex quadrilateral (bowtie)
        corners: list[tuple[float, float]] = [
            (30.0, 10.0),
            (170.0, 50.0),  # crossed
            (170.0, 10.0),
            (30.0, 50.0),
        ]
        assert not validate_composite(corners, 200, 60)


# ===================================================================
# Test Group 4: Data leakage
# ===================================================================


class TestDataLeakage:
    def test_only_train_images_used(self, source_plates_dir: tuple[Path, Path, Path]) -> None:
        """Verify load_source_plates only returns train-split images."""
        from tools.synthetic_data.generate_composites import load_source_plates

        crops_dir, ann_path, split_path = source_plates_dir

        with open(split_path) as f:
            splits = json.load(f)
        val_set = set(splits["val"])

        plates = load_source_plates(ann_path, split_path, crops_dir)
        for plate_id, _, _ in plates:
            assert plate_id not in val_set, f"{plate_id} is in val split!"

    def test_skipped_images_excluded(self, source_plates_dir: tuple[Path, Path, Path]) -> None:
        """Images with corners=null are excluded from source plates."""
        from tools.synthetic_data.generate_composites import load_source_plates

        crops_dir, ann_path, split_path = source_plates_dir
        plates = load_source_plates(ann_path, split_path, crops_dir)
        plate_ids = {pid for pid, _, _ in plates}
        assert "plate_skip.jpg" not in plate_ids

    def test_val_leakage_check(self, tmp_path: Path) -> None:
        """validate_synthetic detects val image in manifest and raises."""
        from tools.synthetic_data.validate_synthetic import check_no_val_leakage

        split_path = tmp_path / "split.json"
        split_path.write_text(
            json.dumps(
                {
                    "train": ["train_001.jpg"],
                    "val": ["val_001.jpg"],
                }
            )
        )

        manifest_path = tmp_path / "manifest.jsonl"
        # Source uses a val image — should be caught
        manifest_path.write_text(
            json.dumps({"synthetic_id": "synth_001.jpg", "source_id": "val_001.jpg"}) + "\n"
        )

        with pytest.raises(ValueError, match="Data leakage"):
            check_no_val_leakage(manifest_path, split_path)


# ===================================================================
# Test Group 5: Annotation format parity
# ===================================================================


class TestAnnotationFormat:
    def test_synthetic_matches_real_format(
        self, source_plates_dir: tuple[Path, Path, Path], tmp_path: Path
    ) -> None:
        """synthetic_annotations.json keys and structure match annotations.json."""
        from tools.synthetic_data.validate_synthetic import check_annotations_format

        _crops_dir, ann_path, _split_path = source_plates_dir

        # Create synthetic annotations in same format
        synth_ann = {
            "synth_00001.jpg": {
                "corners": [[30.0, 10.0], [170.0, 10.0], [170.0, 50.0], [30.0, 50.0]]
            },
            "synth_00002.jpg": {
                "corners": [[25.0, 8.0], [175.0, 8.0], [175.0, 52.0], [25.0, 52.0]]
            },
        }
        synth_ann_path = tmp_path / "synthetic_annotations.json"
        synth_ann_path.write_text(json.dumps(synth_ann))

        assert check_annotations_format(synth_ann_path, ann_path)

    def test_dataset_loads_synthetic(
        self, source_plates_dir: tuple[Path, Path, Path], tmp_path: Path
    ) -> None:
        """PlateCornerDataset can load synthetic data alongside real data."""
        from tests.conftest_training import make_training_config
        from training.dataset import PlateCornerDataset

        crops_dir, ann_path, split_path = source_plates_dir

        # Create synthetic data
        synth_dir = tmp_path / "synth_crops"
        synth_dir.mkdir()
        rng = np.random.RandomState(42)

        synth_ann: dict[str, dict[str, list[list[float]]]] = {}
        for i in range(3):
            fname = f"synth_{i:04d}.jpg"
            img = rng.randint(40, 200, (50, 200, 3), dtype=np.uint8)
            cv2.imwrite(str(synth_dir / fname), img)
            synth_ann[fname] = {"corners": [[30.0, 7.5], [170.0, 7.5], [170.0, 42.5], [30.0, 42.5]]}

        synth_ann_path = tmp_path / "synth_annotations.json"
        synth_ann_path.write_text(json.dumps(synth_ann))

        cfg = make_training_config(
            data_dir=str(crops_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
            synthetic_data_dir=str(synth_dir),
            synthetic_annotations_path=str(synth_ann_path),
        )

        dataset = PlateCornerDataset(cfg, split="train")
        # Should be able to get items without error
        for idx in range(len(dataset)):
            item = dataset[idx]
            assert item[0].shape == (3, 80, 256)  # CHW

    def test_dataset_len_includes_synthetic(
        self, source_plates_dir: tuple[Path, Path, Path], tmp_path: Path
    ) -> None:
        """Dataset length = len(real) + len(synthetic) when synthetic configured."""
        from tests.conftest_training import make_training_config
        from training.dataset import PlateCornerDataset

        crops_dir, ann_path, split_path = source_plates_dir

        synth_dir = tmp_path / "synth_crops"
        synth_dir.mkdir()
        rng = np.random.RandomState(42)

        synth_ann: dict[str, dict[str, list[list[float]]]] = {}
        for i in range(3):
            fname = f"synth_{i:04d}.jpg"
            img = rng.randint(40, 200, (50, 200, 3), dtype=np.uint8)
            cv2.imwrite(str(synth_dir / fname), img)
            synth_ann[fname] = {"corners": [[30.0, 7.5], [170.0, 7.5], [170.0, 42.5], [30.0, 42.5]]}

        synth_ann_path = tmp_path / "synth_annotations.json"
        synth_ann_path.write_text(json.dumps(synth_ann))

        cfg = make_training_config(
            data_dir=str(crops_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
            synthetic_data_dir=str(synth_dir),
            synthetic_annotations_path=str(synth_ann_path),
        )

        with open(split_path) as f:
            splits = json.load(f)
        n_real = len(splits["train"])
        n_synth = len(synth_ann)

        dataset = PlateCornerDataset(cfg, split="train")
        assert len(dataset) == n_real + n_synth

    def test_val_dataset_excludes_synthetic(
        self, source_plates_dir: tuple[Path, Path, Path], tmp_path: Path
    ) -> None:
        """Val split should not include synthetic data."""
        from tests.conftest_training import make_training_config
        from training.dataset import PlateCornerDataset

        crops_dir, ann_path, split_path = source_plates_dir

        synth_dir = tmp_path / "synth_crops"
        synth_dir.mkdir()

        synth_ann: dict[str, dict[str, list[list[float]]]] = {}
        synth_ann["synth_0000.jpg"] = {
            "corners": [[30.0, 7.5], [170.0, 7.5], [170.0, 42.5], [30.0, 42.5]]
        }
        synth_ann_path = tmp_path / "synth_annotations.json"
        synth_ann_path.write_text(json.dumps(synth_ann))

        cfg = make_training_config(
            data_dir=str(crops_dir),
            annotations_path=str(ann_path),
            split_path=str(split_path),
            synthetic_data_dir=str(synth_dir),
            synthetic_annotations_path=str(synth_ann_path),
        )

        val_dataset = PlateCornerDataset(cfg, split="val")
        with open(split_path) as f:
            splits = json.load(f)
        # Val should only have real images
        assert len(val_dataset) == len(splits["val"])


# ===================================================================
# Test Group 6: Visual verification (CRITICAL)
# ===================================================================


class TestVisualVerification:
    """Generate sample composites with annotations drawn for visual inspection.

    These tests produce actual image files that a human can review.
    """

    def test_generate_10_composites_with_annotations(
        self,
        source_plates_dir: tuple[Path, Path, Path],
        vehicle_rears_dir: Path,
        scene_backgrounds_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Generate 10 composite images and save with annotations drawn."""
        from tools.synthetic_data.generate_composites import (
            composite_on_scene_patch,
            composite_on_vehicle_rear,
            load_scene_backgrounds,
            load_source_plates,
            load_vehicle_rears,
            validate_composite,
        )
        from tools.synthetic_data.validate_synthetic import draw_corners_on_image

        crops_dir, ann_path, split_path = source_plates_dir
        plates = load_source_plates(ann_path, split_path, crops_dir)
        vehicle_rears = load_vehicle_rears([vehicle_rears_dir])
        scene_bgs = load_scene_backgrounds(scene_backgrounds_dir)

        output_dir = tmp_path / "composites"
        output_dir.mkdir()

        composites: list[tuple[np.ndarray, list[tuple[float, float]]]] = []
        rng = np.random.RandomState(42)

        for i in range(10):
            plate_id, plate_path, plate_corners = plates[rng.randint(0, len(plates))]
            plate_img = cv2.imread(str(plate_path))
            assert plate_img is not None

            if i % 2 == 0 and vehicle_rears:
                vr_path, vr_bbox = vehicle_rears[rng.randint(0, len(vehicle_rears))]
                vr_img = cv2.imread(str(vr_path))
                assert vr_img is not None
                composite, corners = composite_on_vehicle_rear(
                    plate_img, plate_corners, vr_img, vr_bbox, rng
                )
                method = "vehicle_rear"
            else:
                sc_path = scene_bgs[rng.randint(0, len(scene_bgs))]
                composite, corners = composite_on_scene_patch(
                    plate_img, plate_corners, sc_path, rng
                )
                method = "scene_patch"

            composites.append((composite, corners))

            # Draw annotations and save
            corners_list = [[c[0], c[1]] for c in corners]
            vis = draw_corners_on_image(
                composite,
                corners_list,
                label=f"{i}: {method} src={plate_id[:15]}",
            )
            cv2.imwrite(str(output_dir / f"composite_{i:02d}.png"), vis)

        # Assertions
        assert len(composites) == 10

        for i, (comp, corners) in enumerate(composites):
            h, w = comp.shape[:2]
            for j, (cx, cy) in enumerate(corners):
                assert 0 <= cx < w, f"composite[{i}] corner[{j}] x={cx} OOB"
                assert 0 <= cy < h, f"composite[{i}] corner[{j}] y={cy} OOB"

            assert validate_composite(corners, w, h), f"composite[{i}] failed validation"

        # Generate 2x5 mosaic
        cell_h, cell_w = 100, 250
        mosaic = np.full((cell_h * 2, cell_w * 5, 3), 128, dtype=np.uint8)
        for i, (comp, corners) in enumerate(composites):
            row, col = i // 5, i % 5
            corners_list = [[c[0], c[1]] for c in corners]
            vis = draw_corners_on_image(comp, corners_list)
            vis_resized = cv2.resize(vis, (cell_w, cell_h))
            mosaic[row * cell_h : (row + 1) * cell_h, col * cell_w : (col + 1) * cell_w] = (
                vis_resized
            )

        mosaic_path = output_dir / "mosaic_10.png"
        cv2.imwrite(str(mosaic_path), mosaic)
        assert mosaic_path.exists()
        # Verify it's a valid PNG
        loaded = cv2.imread(str(mosaic_path))
        assert loaded is not None
        assert loaded.shape == (cell_h * 2, cell_w * 5, 3)

    def test_generate_40_augmented_composites_with_annotations(
        self,
        source_plates_dir: tuple[Path, Path, Path],
        vehicle_rears_dir: Path,
        scene_backgrounds_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Generate 10 composites, apply FULL_DRONE augmentation 4x each = 40."""
        from tools.synthetic_data.generate_composites import (
            composite_on_scene_patch,
            composite_on_vehicle_rear,
            load_scene_backgrounds,
            load_source_plates,
            load_vehicle_rears,
        )
        from tools.synthetic_data.validate_synthetic import draw_corners_on_image

        crops_dir, ann_path, split_path = source_plates_dir
        plates = load_source_plates(ann_path, split_path, crops_dir)
        vehicle_rears = load_vehicle_rears([vehicle_rears_dir])
        scene_bgs = load_scene_backgrounds(scene_backgrounds_dir)

        output_dir = tmp_path / "augmented_composites"
        output_dir.mkdir()

        cfg = TrainingConfig(
            aug_shadow_prob=0.5,
            aug_glare_prob=0.5,
            aug_color_temp_prob=0.5,
            aug_directional_blur_prob=0.5,
            aug_poisson_prob=0.5,
            aug_sensor_banding_prob=0.5,
            aug_defocus_prob=0.5,
        )

        base_composites: list[tuple[np.ndarray, list[tuple[float, float]]]] = []
        gen_rng = np.random.RandomState(42)

        # Generate 10 base composites
        for i in range(10):
            plate_id, plate_path, plate_corners = plates[gen_rng.randint(0, len(plates))]
            plate_img = cv2.imread(str(plate_path))
            assert plate_img is not None

            if i % 2 == 0 and vehicle_rears:
                vr_path, vr_bbox = vehicle_rears[gen_rng.randint(0, len(vehicle_rears))]
                vr_img = cv2.imread(str(vr_path))
                assert vr_img is not None
                composite, corners = composite_on_vehicle_rear(
                    plate_img, plate_corners, vr_img, vr_bbox, gen_rng
                )
            else:
                sc_path = scene_bgs[gen_rng.randint(0, len(scene_bgs))]
                composite, corners = composite_on_scene_patch(
                    plate_img, plate_corners, sc_path, gen_rng
                )

            base_composites.append((composite, corners))

        # Apply FULL_DRONE augmentation 4x each = 40 images
        augmented: list[tuple[np.ndarray, list[tuple[float, float]]]] = []
        aug_count = 0
        for base_idx, (base_img, base_corners) in enumerate(base_composites):
            for aug_iter in range(4):
                seed = 1000 + base_idx * 4 + aug_iter
                aug_rng = np.random.RandomState(seed)
                with patch("training.augmentations._rng", aug_rng):
                    result = apply_augmentation(
                        base_img.copy(),
                        list(base_corners),
                        AugmentationPhase.FULL_DRONE,
                        cfg,
                    )
                if result is not None:
                    aug_img, aug_corners = result
                    augmented.append((aug_img, aug_corners))

                    corners_list = [[c[0], c[1]] for c in aug_corners]
                    vis = draw_corners_on_image(
                        aug_img,
                        corners_list,
                        label=f"b{base_idx}_a{aug_iter}",
                    )
                    cv2.imwrite(str(output_dir / f"aug_{aug_count:02d}.png"), vis)
                    aug_count += 1

        # Should have generated close to 40 (some may fail OOB retry)
        assert len(augmented) >= 30, f"Expected >=30 augmented, got {len(augmented)}"

        # Verify corners tracked through augmentation
        for i, (aug_img, aug_corners) in enumerate(augmented):
            h, w = aug_img.shape[:2]
            for j, (cx, cy) in enumerate(aug_corners):
                assert 0 <= cx < w, f"augmented[{i}] corner[{j}] x={cx} OOB"
                assert 0 <= cy < h, f"augmented[{i}] corner[{j}] y={cy} OOB"

        # Augmented images should differ from base composites
        # (at least some should differ since we have augmentations active)
        differ_count = 0
        for base_idx, (base_img, _) in enumerate(base_composites):
            if base_idx < len(augmented):
                aug_img, _ = augmented[base_idx]
                if base_img.shape == aug_img.shape:
                    if not np.array_equal(base_img, aug_img):
                        differ_count += 1
        assert differ_count > 0, "No augmented images differ from base"

        # Generate 5x8 mosaic
        cell_h, cell_w = 80, 200
        n_rows, n_cols = 5, 8
        mosaic = np.full((cell_h * n_rows, cell_w * n_cols, 3), 128, dtype=np.uint8)
        for i, (aug_img, aug_corners) in enumerate(augmented[: n_rows * n_cols]):
            row, col = i // n_cols, i % n_cols
            corners_list = [[c[0], c[1]] for c in aug_corners]
            vis = draw_corners_on_image(aug_img, corners_list)
            vis_resized = cv2.resize(vis, (cell_w, cell_h))
            mosaic[row * cell_h : (row + 1) * cell_h, col * cell_w : (col + 1) * cell_w] = (
                vis_resized
            )

        mosaic_path = output_dir / "mosaic_40.png"
        cv2.imwrite(str(mosaic_path), mosaic)
        assert mosaic_path.exists()
        loaded = cv2.imread(str(mosaic_path))
        assert loaded is not None


# ===================================================================
# Test Group 7: Compositing quality improvements (v2)
# ===================================================================


class TestGaussianFeathering:
    def test_mask_center_is_one(self) -> None:
        """Center of feathered mask should be 1.0."""
        from tools.synthetic_data.generate_composites import _create_feathered_mask

        mask = _create_feathered_mask(100, 200, feather_px=10)
        assert mask[50, 100] == pytest.approx(1.0, abs=0.01)

    def test_mask_edge_is_low(self) -> None:
        """Edge pixels of feathered mask should be well below 1.0."""
        from tools.synthetic_data.generate_composites import _create_feathered_mask

        mask = _create_feathered_mask(100, 200, feather_px=10)
        # Corners should be close to 0
        assert mask[0, 0] < 0.3
        assert mask[0, -1] < 0.3
        assert mask[-1, 0] < 0.3
        assert mask[-1, -1] < 0.3

    def test_mask_smooth_gradient(self) -> None:
        """Mask should have smooth gradient from edge to center (no hard jumps)."""
        from tools.synthetic_data.generate_composites import _create_feathered_mask

        mask = _create_feathered_mask(100, 200, feather_px=15)
        # Walk from edge to center along a row — values should be monotonically non-decreasing
        mid_row = mask[50, :]
        for i in range(1, 100):
            assert mid_row[i] >= mid_row[i - 1] - 0.01, (
                f"Non-monotonic at col {i}: {mid_row[i]} < {mid_row[i - 1]}"
            )


class TestLightingMatching:
    def test_dark_plate_on_bright_bg_gets_brighter(self) -> None:
        """A dark plate pasted on bright background should be shifted brighter."""
        from tools.synthetic_data.generate_composites import _match_lighting

        dark_plate = np.full((50, 100, 3), 40, dtype=np.uint8)
        bright_bg = np.full((50, 100, 3), 200, dtype=np.uint8)

        adjusted = _match_lighting(dark_plate, bright_bg, strength=0.6)
        # Adjusted should be brighter than original
        assert adjusted.mean() > dark_plate.mean()

    def test_bright_plate_on_dark_bg_gets_darker(self) -> None:
        """A bright plate pasted on dark background should be shifted darker."""
        from tools.synthetic_data.generate_composites import _match_lighting

        bright_plate = np.full((50, 100, 3), 200, dtype=np.uint8)
        dark_bg = np.full((50, 100, 3), 40, dtype=np.uint8)

        adjusted = _match_lighting(bright_plate, dark_bg, strength=0.6)
        assert adjusted.mean() < bright_plate.mean()

    def test_strength_zero_is_noop(self) -> None:
        """Strength=0 should return image unchanged (short-circuits LAB conversion)."""
        from tools.synthetic_data.generate_composites import _match_lighting

        plate = np.random.RandomState(42).randint(50, 200, (50, 100, 3), dtype=np.uint8)
        bg = np.random.RandomState(99).randint(50, 200, (50, 100, 3), dtype=np.uint8)

        adjusted = _match_lighting(plate, bg, strength=0.0)
        np.testing.assert_array_equal(adjusted, plate)

    def test_preserves_valid_range(self) -> None:
        """Output should always be in [0, 255]."""
        from tools.synthetic_data.generate_composites import _match_lighting

        plate = np.full((50, 100, 3), 250, dtype=np.uint8)
        bg = np.full((50, 100, 3), 10, dtype=np.uint8)

        adjusted = _match_lighting(plate, bg, strength=1.0)
        assert adjusted.min() >= 0
        assert adjusted.max() <= 255


class TestPostCompositeDegradation:
    def test_output_differs_from_input(self) -> None:
        """Degradation should add noise / compression artifacts."""
        from tools.synthetic_data.generate_composites import (
            _apply_post_composite_degradation,
        )

        img = np.full((100, 200, 3), 128, dtype=np.uint8)
        rng = np.random.RandomState(42)
        out = _apply_post_composite_degradation(img, rng)
        assert not np.array_equal(img, out)

    def test_output_valid_range(self) -> None:
        """Output should be valid uint8."""
        from tools.synthetic_data.generate_composites import (
            _apply_post_composite_degradation,
        )

        rng = np.random.RandomState(42)
        img = rng.randint(0, 256, (100, 200, 3), dtype=np.uint8)
        out = _apply_post_composite_degradation(img, rng)
        assert out.dtype == np.uint8
        assert out.shape == img.shape


class TestSizeNormalization:
    def test_downscale_oversized(self) -> None:
        """Large composites should be downscaled to max bounds."""
        from tools.synthetic_data.generate_composites import _normalize_composite_size

        big_img = np.zeros((400, 600, 3), dtype=np.uint8)
        corners = [(50.0, 50.0), (550.0, 50.0), (550.0, 350.0), (50.0, 350.0)]

        result = _normalize_composite_size(big_img, corners, max_w=300, max_h=180)
        assert result is not None
        img_out, corners_out = result
        h, w = img_out.shape[:2]
        assert w <= 300
        assert h <= 180
        # Corners should be scaled proportionally
        assert corners_out[0][0] < corners[0][0]

    def test_reject_undersized(self) -> None:
        """Tiny composites should be rejected."""
        from tools.synthetic_data.generate_composites import _normalize_composite_size

        tiny_img = np.zeros((30, 50, 3), dtype=np.uint8)
        corners = [(5.0, 5.0), (45.0, 5.0), (45.0, 25.0), (5.0, 25.0)]

        result = _normalize_composite_size(tiny_img, corners, min_w=80, min_h=50)
        assert result is None

    def test_passthrough_normal_size(self) -> None:
        """Normal-sized composites should pass through unchanged."""
        from tools.synthetic_data.generate_composites import _normalize_composite_size

        img = np.zeros((100, 200, 3), dtype=np.uint8)
        corners = [(20.0, 10.0), (180.0, 10.0), (180.0, 90.0), (20.0, 90.0)]

        result = _normalize_composite_size(img, corners)
        assert result is not None
        img_out, corners_out = result
        assert img_out.shape == img.shape
        assert corners_out == corners
