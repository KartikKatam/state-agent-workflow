"""Tests for training config and shared geometry (chunk-01)."""

from __future__ import annotations


class TestTrainingConfigDefaults:
    """Verify TrainingConfig creates with correct defaults."""

    def test_training_config_importable_with_defaults(self):
        """TrainingConfig() creates instance with input_h==80, input_w==256, stride==4."""
        from training.config import TrainingConfig

        c = TrainingConfig()
        assert c.input_h == 80
        assert c.input_w == 256
        assert c.stride == 4

    def test_training_config_with_overrides(self):
        """TrainingConfig(batch_size=8, base_lr=5e-4) overrides defaults."""
        from training.config import TrainingConfig

        c = TrainingConfig(batch_size=8, base_lr=5e-4)
        assert c.batch_size == 8
        assert c.base_lr == 5e-4
        # Other defaults unchanged
        assert c.input_h == 80

    def test_training_config_mutable(self):
        """TrainingConfig frozen=False allows field mutation."""
        from training.config import TrainingConfig

        c = TrainingConfig()
        c.base_lr = 0.001
        assert c.base_lr == 0.001


class TestAugmentationPhase:
    """Verify AugmentationPhase enum values."""

    def test_augmentation_phase_enum_values(self):
        """AugmentationPhase.NONE==0, MODERATE==1, FULL_DRONE==2."""
        from training.config import AugmentationPhase

        assert AugmentationPhase.NONE == 0
        assert AugmentationPhase.MODERATE == 1
        assert AugmentationPhase.FULL_DRONE == 2

    def test_augmentation_phase_is_intenum(self):
        """AugmentationPhase members are valid ints."""
        from training.config import AugmentationPhase

        assert isinstance(AugmentationPhase.NONE, int)
        assert int(AugmentationPhase.FULL_DRONE) == 2


class TestTrainingConfigAugFields:
    """Verify all augmentation fields exist on TrainingConfig."""

    def test_training_config_all_aug_fields_exist(self):
        """All aug_* fields present: phase 2 and phase 3 augmentation params."""
        from training.config import TrainingConfig

        c = TrainingConfig()

        # Phase 2 augmentation fields
        phase2_fields = [
            "aug_hflip_prob",
            "aug_brightness_range",
            "aug_brightness_prob",
            "aug_contrast_range",
            "aug_contrast_prob",
            "aug_saturation_range",
            "aug_saturation_prob",
            "aug_rotation_range_p2",
            "aug_rotation_prob_p2",
            "aug_scale_range_p2",
            "aug_scale_prob_p2",
            "aug_noise_sigma_p2",
            "aug_noise_prob_p2",
        ]

        # Phase 3 augmentation fields
        phase3_fields = [
            "aug_rotation_range_p3",
            "aug_rotation_prob_p3",
            "aug_perspective_range",
            "aug_perspective_prob",
            "aug_motion_blur_range",
            "aug_motion_blur_prob",
            "aug_gaussian_blur_sigma",
            "aug_gaussian_blur_prob",
            "aug_heavy_brightness_range",
            "aug_heavy_brightness_prob",
            "aug_gamma_range",
            "aug_jpeg_quality_range",
            "aug_jpeg_prob",
            "aug_heavy_noise_sigma",
            "aug_heavy_noise_prob",
            "aug_occlusion_area_range",
            "aug_occlusion_prob",
            "aug_scale_range_p3",
            "aug_scale_prob_p3",
            "aug_night_brightness",
            "aug_night_gamma",
            "aug_night_noise_sigma",
            "aug_night_prob",
        ]

        for field in phase2_fields + phase3_fields:
            assert hasattr(c, field), f"Missing field: {field}"


class TestOrderKeypointsByAngle:
    """Verify order_keypoints_by_angle in common.geometry."""

    def test_order_keypoints_importable_from_common(self):
        """from common.geometry import order_keypoints_by_angle succeeds."""
        from common.geometry import order_keypoints_by_angle

        assert callable(order_keypoints_by_angle)

    def test_order_keypoints_canonicalization(self):
        """Random-order corners -> canonical [TL,TR,BR,BL]."""
        from common.geometry import order_keypoints_by_angle

        # Random order input
        corners = [(190.0, 40.0), (10.0, 10.0), (10.0, 40.0), (190.0, 10.0)]
        result = order_keypoints_by_angle(corners)

        # Expected canonical: TL(10,10), TR(190,10), BR(190,40), BL(10,40)
        expected = [(10.0, 10.0), (190.0, 10.0), (190.0, 40.0), (10.0, 40.0)]
        assert result == expected


class TestConsumerBackwardCompat:
    """Verify consumer import still works after extraction."""

    def test_consumer_import_backward_compat(self):
        """from consumer.ops_quality_rich import order_keypoints_by_angle still works."""
        from common.geometry import order_keypoints_by_angle as common_func
        from consumer.ops_quality_rich import order_keypoints_by_angle as consumer_func

        # Same function (re-exported)
        assert consumer_func is common_func

    def test_consumer_function_still_works(self):
        """Consumer's re-exported function produces correct results."""
        from consumer.ops_quality_rich import order_keypoints_by_angle

        corners = [(190.0, 40.0), (10.0, 10.0), (10.0, 40.0), (190.0, 10.0)]
        result = order_keypoints_by_angle(corners)
        expected = [(10.0, 10.0), (190.0, 10.0), (190.0, 40.0), (10.0, 40.0)]
        assert result == expected
