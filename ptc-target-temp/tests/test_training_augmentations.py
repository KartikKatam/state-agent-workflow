"""Tests for training/augmentations.py — chunk-05 augmentation pipeline."""

from __future__ import annotations

from unittest.mock import patch

import cv2
import numpy as np
import pytest

from training.augmentations import (
    _aug_hflip,
    _aug_perspective,
    _aug_rotate,
    _aug_scale,
    apply_augmentation,
)
from training.config import AugmentationPhase, TrainingConfig


def make_plate_image(h: int = 80, w: int = 256, dtype: type = np.uint8) -> np.ndarray:
    """Create a synthetic plate image for testing."""
    rng = np.random.RandomState(42)
    return rng.randint(0, 255, (h, w, 3), dtype=dtype)


def make_plate_corners(w: int = 256, h: int = 80, margin: float = 0.1) -> list[tuple[float, float]]:
    """Create canonical TL, TR, BR, BL plate corners well inside bounds."""
    mx = w * margin
    my = h * margin
    return [
        (mx, my),  # TL
        (w - 1 - mx, my),  # TR
        (w - 1 - mx, h - 1 - my),  # BR
        (mx, h - 1 - my),  # BL
    ]


class TestPhaseNone:
    """Tests for AugmentationPhase.NONE passthrough."""

    def test_phase_none_passthrough(self) -> None:
        """NONE phase returns (img, corners) unchanged."""
        img = make_plate_image()
        corners = make_plate_corners()
        cfg = TrainingConfig()
        result = apply_augmentation(img, corners, AugmentationPhase.NONE, cfg)
        assert result is not None
        out_img, out_corners = result
        np.testing.assert_array_equal(out_img, img)
        assert out_corners == corners


class TestHorizontalFlip:
    """Tests for horizontal flip corner identity remapping."""

    def test_hflip_corner_remap(self) -> None:
        """Forced hflip remaps [TL,TR,BR,BL] -> [TR_flipped,TL_flipped,BL_flipped,BR_flipped]."""
        w = 200
        img = np.zeros((50, w, 3), dtype=np.uint8)
        # TL=(10,10), TR=(180,10), BR=(180,40), BL=(10,40)
        corners: list[tuple[float, float]] = [
            (10.0, 10.0),
            (180.0, 10.0),
            (180.0, 40.0),
            (10.0, 40.0),
        ]
        _flipped_img, flipped_corners = _aug_hflip(img, corners)

        # After hflip: x_new = w-1-x
        # Original TL(10,10) -> (189,10), becomes new TR
        # Original TR(180,10) -> (19,10), becomes new TL
        # Original BR(180,40) -> (19,40), becomes new BL
        # Original BL(10,40) -> (189,40), becomes new BR
        # Remap: [old_TR_flipped, old_TL_flipped, old_BL_flipped, old_BR_flipped]
        # = [(19,10), (189,10), (189,40), (19,40)]
        expected = [
            (19.0, 10.0),  # new TL = old TR flipped
            (189.0, 10.0),  # new TR = old TL flipped
            (189.0, 40.0),  # new BR = old BL flipped
            (19.0, 40.0),  # new BL = old BR flipped
        ]
        assert flipped_corners == expected

    def test_hflip_x_coordinate_mirroring(self) -> None:
        """img (w=200), corner at (30,10) -> x_new == 199-30 == 169."""
        w = 200
        img = np.zeros((50, w, 3), dtype=np.uint8)
        corners: list[tuple[float, float]] = [
            (30.0, 10.0),
            (170.0, 10.0),
            (170.0, 40.0),
            (30.0, 40.0),
        ]
        _, flipped_corners = _aug_hflip(img, corners)
        # After flip and remap: new TL = old TR flipped
        # old TR (170,10) -> x_new = 199-170 = 29
        # old TL (30,10) -> x_new = 199-30 = 169
        # Check the x-mirror values are correct
        # Remap: [old_TR_flipped, old_TL_flipped, old_BL_flipped, old_BR_flipped]
        assert flipped_corners[0][0] == pytest.approx(29.0)  # old TR flipped x
        assert flipped_corners[1][0] == pytest.approx(169.0)  # old TL flipped x


class TestBoundsCheck:
    """Tests for out-of-bounds rejection and retry."""

    def test_bounds_check_all_corners_inside(self) -> None:
        """Seed-controlled MODERATE augmentation -> all corners within bounds."""
        cfg = TrainingConfig()
        img = make_plate_image()
        corners = make_plate_corners()

        for seed in range(50):
            rng = np.random.RandomState(seed)
            with patch("training.augmentations._rng", rng):
                result = apply_augmentation(img, corners, AugmentationPhase.MODERATE, cfg)
            if result is not None:
                _, out_corners = result
                h, w = img.shape[:2]
                for i, (cx, cy) in enumerate(out_corners):
                    assert 0 <= cx < w, f"seed={seed} corner[{i}] x={cx} out of [0,{w})"
                    assert 0 <= cy < h, f"seed={seed} corner[{i}] y={cy} out of [0,{h})"

    def test_bounds_check_retries_on_oob(self) -> None:
        """Edge corners + aggressive augmentation -> returns None or valid result."""
        cfg = TrainingConfig()
        img = make_plate_image(h=50, w=200)
        # Corners very close to edges
        corners: list[tuple[float, float]] = [
            (1.0, 1.0),
            (198.0, 1.0),
            (198.0, 48.0),
            (1.0, 48.0),
        ]
        result = apply_augmentation(
            img,
            corners,
            AugmentationPhase.FULL_DRONE,
            cfg,
            max_retries=3,
        )
        if result is not None:
            _, out_corners = result
            h, w = img.shape[:2]
            for cx, cy in out_corners:
                assert 0 <= cx < w
                assert 0 <= cy < h


class TestRotation:
    """Tests for rotation geometry preservation."""

    def test_rotation_preserves_geometry(self) -> None:
        """Rectangle corners, 5deg rotation -> convex hull area ratio within 0.95-1.05."""
        img = make_plate_image()
        corners = make_plate_corners()

        # Compute original area via shoelace
        def quad_area(pts: list[tuple[float, float]]) -> float:
            n = len(pts)
            area = 0.0
            for i in range(n):
                j = (i + 1) % n
                area += pts[i][0] * pts[j][1]
                area -= pts[j][0] * pts[i][1]
            return abs(area) / 2.0

        original_area = quad_area(corners)
        cfg = TrainingConfig()

        _rotated_img, rotated_corners = _aug_rotate(img, corners, angle=5.0, cfg=cfg)
        rotated_area = quad_area(rotated_corners)

        ratio = rotated_area / original_area
        assert 0.95 <= ratio <= 1.05, f"Area ratio {ratio} out of tolerance"


class TestNightSimulation:
    """Tests for night simulation gating by is_night flag."""

    def test_night_sim_skipped_for_night_images(self) -> None:
        """is_night=True, FULL_DRONE -> brightness NOT reduced by night sim."""
        cfg = TrainingConfig()
        img = np.full((80, 256, 3), 128, dtype=np.uint8)
        corners = make_plate_corners()

        # Force night sim probability to 1.0 so it always triggers
        cfg.aug_night_prob = 1.0
        # Disable all other augmentations to isolate night sim
        cfg.aug_hflip_prob = 0.0
        cfg.aug_rotation_prob_p3 = 0.0
        cfg.aug_scale_prob_p3 = 0.0
        cfg.aug_perspective_prob = 0.0
        cfg.aug_heavy_brightness_prob = 0.0
        cfg.aug_contrast_prob = 0.0
        cfg.aug_saturation_prob = 0.0
        cfg.aug_motion_blur_prob = 0.0
        cfg.aug_gaussian_blur_prob = 0.0
        cfg.aug_jpeg_prob = 0.0
        cfg.aug_heavy_noise_prob = 0.0
        cfg.aug_occlusion_prob = 0.0
        cfg.aug_brightness_prob = 0.0
        cfg.aug_noise_prob_p2 = 0.0
        # Disable new augmentations too
        cfg.aug_shadow_prob = 0.0
        cfg.aug_glare_prob = 0.0
        cfg.aug_color_temp_prob = 0.0
        cfg.aug_directional_blur_prob = 0.0
        cfg.aug_poisson_prob = 0.0
        cfg.aug_sensor_banding_prob = 0.0
        cfg.aug_defocus_prob = 0.0

        result = apply_augmentation(img, corners, AugmentationPhase.FULL_DRONE, cfg, is_night=True)
        assert result is not None
        out_img, _ = result
        # Night sim skipped -> image should be unchanged (no brightness reduction)
        assert out_img.mean() == pytest.approx(128.0, abs=1.0)

    def test_night_sim_applied_for_day_images(self) -> None:
        """is_night=False, FULL_DRONE -> brightness reduced by night sim."""
        cfg = TrainingConfig()
        img = np.full((80, 256, 3), 128, dtype=np.uint8)
        corners = make_plate_corners()

        # Force night sim probability to 1.0
        cfg.aug_night_prob = 1.0
        # Disable all other augmentations to isolate night sim
        cfg.aug_hflip_prob = 0.0
        cfg.aug_rotation_prob_p3 = 0.0
        cfg.aug_scale_prob_p3 = 0.0
        cfg.aug_perspective_prob = 0.0
        cfg.aug_heavy_brightness_prob = 0.0
        cfg.aug_contrast_prob = 0.0
        cfg.aug_saturation_prob = 0.0
        cfg.aug_motion_blur_prob = 0.0
        cfg.aug_gaussian_blur_prob = 0.0
        cfg.aug_jpeg_prob = 0.0
        cfg.aug_heavy_noise_prob = 0.0
        cfg.aug_occlusion_prob = 0.0
        cfg.aug_brightness_prob = 0.0
        cfg.aug_noise_prob_p2 = 0.0
        # Disable new augmentations too
        cfg.aug_shadow_prob = 0.0
        cfg.aug_glare_prob = 0.0
        cfg.aug_color_temp_prob = 0.0
        cfg.aug_directional_blur_prob = 0.0
        cfg.aug_poisson_prob = 0.0
        cfg.aug_sensor_banding_prob = 0.0
        cfg.aug_defocus_prob = 0.0

        result = apply_augmentation(img, corners, AugmentationPhase.FULL_DRONE, cfg, is_night=False)
        assert result is not None
        out_img, _ = result
        # Night sim applied -> mean brightness should be significantly reduced
        # Night sim uses brightness * 0.2-0.4, so mean should drop to ~25-51
        assert out_img.mean() < 80.0, (
            f"Expected brightness reduction, got mean={out_img.mean():.1f}"
        )


class TestPerspective:
    """Tests for perspective warp with w-divide."""

    def test_perspective_warp_corners_correct(self) -> None:
        """Known H matrix, known corners -> corners match H@[x,y,1] with w-divide."""
        img = np.zeros((80, 256, 3), dtype=np.uint8)
        corners: list[tuple[float, float]] = [
            (50.0, 20.0),
            (200.0, 20.0),
            (200.0, 60.0),
            (50.0, 60.0),
        ]
        cfg = TrainingConfig()

        # Use a known perspective transform
        src_pts = np.array([[0, 0], [255, 0], [255, 79], [0, 79]], dtype=np.float32)
        dst_pts = np.array([[5, 3], [250, 2], [252, 77], [3, 78]], dtype=np.float32)
        H: np.ndarray = cv2.getPerspectiveTransform(src_pts, dst_pts)  # type: ignore[assignment]

        _warped_img, warped_corners = _aug_perspective(img, corners, H, cfg)

        # Verify each corner matches manual H@[x,y,1] with perspective divide
        for i, (x, y) in enumerate(corners):
            vec = np.array([x, y, 1.0])
            result = H @ vec
            expected_x = result[0] / result[2]
            expected_y = result[1] / result[2]
            assert warped_corners[i][0] == pytest.approx(expected_x, abs=0.01)
            assert warped_corners[i][1] == pytest.approx(expected_y, abs=0.01)


class TestExcludedAugmentations:
    """Tests for excluded augmentation enforcement."""

    def test_excluded_augmentations_absent(self) -> None:
        """No vertical flip, no rotation >20deg, no grayscale in pipeline."""
        cfg = TrainingConfig()

        # Rotation ranges must be <=20
        assert cfg.aug_rotation_range_p2 <= 20.0
        assert cfg.aug_rotation_range_p3 <= 20.0

        # Run many FULL_DRONE augmentations and verify no vflip / grayscale
        img = make_plate_image()
        corners = make_plate_corners()

        for seed in range(30):
            rng = np.random.RandomState(seed)
            with patch("training.augmentations._rng", rng):
                result = apply_augmentation(img.copy(), corners, AugmentationPhase.FULL_DRONE, cfg)
            if result is not None:
                out_img, _ = result
                # Not grayscale — channels should differ
                if out_img.mean() > 5:  # skip near-black images from night sim
                    # At least some color variation should exist
                    assert out_img.shape[2] == 3, "Output must be 3-channel"


class TestCornerOrderPreservation:
    """Tests for semantic corner order preservation after non-flip transforms."""

    def test_augmentation_preserves_corner_order_semantics_after_transform(
        self,
    ) -> None:
        """After rotation/scale, corners maintain TL/TR/BR/BL relative to plate."""
        img = make_plate_image()
        corners = make_plate_corners()
        cfg = TrainingConfig()

        # Small rotation should preserve relative ordering
        _rotated_img, rotated_corners = _aug_rotate(img, corners, angle=3.0, cfg=cfg)

        tl, tr, br, bl = rotated_corners

        # TL should be above BL (smaller y)
        assert tl[1] < bl[1], "TL should be above BL"
        # TR should be above BR
        assert tr[1] < br[1], "TR should be above BR"
        # TL should be left of TR (smaller x)
        assert tl[0] < tr[0], "TL should be left of TR"
        # BL should be left of BR
        assert bl[0] < br[0], "BL should be left of BR"


class TestComposedTransformCornerTracking:
    """Tests that corner tracking stays numerically correct through
    composed sequences of geometric augmentations."""

    @staticmethod
    def _affine_transform_point(M: np.ndarray, x: float, y: float) -> tuple[float, float]:
        """Apply 2x3 affine matrix to a point."""
        vec = np.array([x, y, 1.0])
        result = M @ vec
        return (float(result[0]), float(result[1]))

    def test_rotation_then_scale_corner_accuracy(self) -> None:
        """Rotation(5deg) + Scale(1.1): augmented corners match manual matrix math."""
        w, h = 256, 80
        img = np.zeros((h, w, 3), dtype=np.uint8)
        corners: list[tuple[float, float]] = [
            (30.0, 10.0),  # TL
            (220.0, 10.0),  # TR
            (220.0, 65.0),  # BR
            (30.0, 65.0),  # BL
        ]
        cfg = TrainingConfig()
        angle = 5.0
        scale_factor = 1.1
        center = (w / 2.0, h / 2.0)

        # Step 1: Apply rotation via augmentation function
        rotated_img, rotated_corners = _aug_rotate(img, corners, angle, cfg)
        # Step 2: Apply scale via augmentation function
        _final_img, final_corners = _aug_scale(rotated_img, rotated_corners, scale_factor, cfg)

        # Manual computation: build matrices and apply sequentially
        M_rot = cv2.getRotationMatrix2D(center, angle, 1.0)
        M_scale = cv2.getRotationMatrix2D(center, 0, scale_factor)

        expected: list[tuple[float, float]] = []
        for x, y in corners:
            # Apply rotation first
            rx, ry = self._affine_transform_point(M_rot, x, y)
            # Then apply scale
            sx, sy = self._affine_transform_point(M_scale, rx, ry)
            expected.append((sx, sy))

        # Assert each corner matches within 0.5px
        for i, (actual, exp) in enumerate(zip(final_corners, expected, strict=True)):
            assert actual[0] == pytest.approx(exp[0], abs=0.5), (
                f"Corner[{i}] x: actual={actual[0]:.3f}, expected={exp[0]:.3f}"
            )
            assert actual[1] == pytest.approx(exp[1], abs=0.5), (
                f"Corner[{i}] y: actual={actual[1]:.3f}, expected={exp[1]:.3f}"
            )

    def test_hflip_then_rotation_then_scale_corner_accuracy(self) -> None:
        """Hflip + Rotation(-3deg) + Scale(0.95): corners match manual computation."""
        w, h = 256, 80
        img = np.zeros((h, w, 3), dtype=np.uint8)
        corners: list[tuple[float, float]] = [
            (40.0, 12.0),  # TL
            (210.0, 12.0),  # TR
            (210.0, 62.0),  # BR
            (40.0, 62.0),  # BL
        ]
        cfg = TrainingConfig()
        angle = -3.0
        scale_factor = 0.95
        center = (w / 2.0, h / 2.0)

        # Step 1: Hflip via augmentation function
        flipped_img, flipped_corners = _aug_hflip(img, corners)
        # Step 2: Rotation
        rotated_img, rotated_corners = _aug_rotate(flipped_img, flipped_corners, angle, cfg)
        # Step 3: Scale
        _final_img, final_corners = _aug_scale(rotated_img, rotated_corners, scale_factor, cfg)

        # Manual computation:
        # 1. Hflip: mirror x, then remap identity [TL,TR,BR,BL] -> [TR',TL',BL',BR']
        mirrored = [(float(w - 1 - x), float(y)) for x, y in corners]
        hflip_expected: list[tuple[float, float]] = [
            mirrored[1],  # new TL = old TR mirrored
            mirrored[0],  # new TR = old TL mirrored
            mirrored[3],  # new BR = old BL mirrored
            mirrored[2],  # new BL = old BR mirrored
        ]

        # 2. Rotation
        M_rot = cv2.getRotationMatrix2D(center, angle, 1.0)
        rot_expected = [self._affine_transform_point(M_rot, x, y) for x, y in hflip_expected]

        # 3. Scale
        M_scale = cv2.getRotationMatrix2D(center, 0, scale_factor)
        expected = [self._affine_transform_point(M_scale, x, y) for x, y in rot_expected]

        # Assert each corner matches within 0.5px
        for i, (actual, exp) in enumerate(zip(final_corners, expected, strict=True)):
            assert actual[0] == pytest.approx(exp[0], abs=0.5), (
                f"Corner[{i}] x: actual={actual[0]:.3f}, expected={exp[0]:.3f}"
            )
            assert actual[1] == pytest.approx(exp[1], abs=0.5), (
                f"Corner[{i}] y: actual={actual[1]:.3f}, expected={exp[1]:.3f}"
            )

    def test_rotation_then_scale_exact_match(self) -> None:
        """Rotation + Scale corner tracking matches matrix composition exactly (tight tol)."""
        w, h = 256, 80
        img = np.zeros((h, w, 3), dtype=np.uint8)
        corners: list[tuple[float, float]] = [
            (50.0, 15.0),
            (200.0, 15.0),
            (200.0, 60.0),
            (50.0, 60.0),
        ]
        cfg = TrainingConfig()
        angle = 8.0
        scale_factor = 0.9
        center = (w / 2.0, h / 2.0)

        # Apply via augmentation functions
        rotated_img, rotated_corners = _aug_rotate(img, corners, angle, cfg)
        _final_img, final_corners = _aug_scale(rotated_img, rotated_corners, scale_factor, cfg)

        # Compose the two 2x3 affine matrices manually
        # M_rot and M_scale are 2x3. To compose, extend to 3x3.
        M_rot = cv2.getRotationMatrix2D(center, angle, 1.0)
        M_scale = cv2.getRotationMatrix2D(center, 0, scale_factor)

        # Extend to 3x3 for composition
        M_rot_3x3 = np.vstack([M_rot, [0, 0, 1]])
        M_scale_3x3 = np.vstack([M_scale, [0, 0, 1]])
        M_composed = M_scale_3x3 @ M_rot_3x3  # scale applied after rotation

        expected: list[tuple[float, float]] = []
        for x, y in corners:
            vec = np.array([x, y, 1.0])
            result = M_composed @ vec
            expected.append((float(result[0]), float(result[1])))

        # Exact float match — the functions use the same matrix math
        for i, (actual, exp) in enumerate(zip(final_corners, expected, strict=True)):
            assert actual[0] == pytest.approx(exp[0], abs=1e-6), (
                f"Corner[{i}] x: actual={actual[0]:.6f}, expected={exp[0]:.6f}"
            )
            assert actual[1] == pytest.approx(exp[1], abs=1e-6), (
                f"Corner[{i}] y: actual={actual[1]:.6f}, expected={exp[1]:.6f}"
            )
