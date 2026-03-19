from __future__ import annotations

import torch
import torch.nn as nn


class TestCornerHeatmapNetOutputShape:
    """Tests for CornerHeatmapNet forward pass output shape."""

    def test_output_shape_single_batch(self) -> None:
        """CornerHeatmapNet with batch=1 input (1,3,80,256) -> output shape (1,12,20,64)."""
        from training.config import TrainingConfig
        from training.model import CornerHeatmapNet

        cfg = TrainingConfig()
        model = CornerHeatmapNet(cfg)
        model.eval()
        x = torch.randn(1, 3, 80, 256)
        with torch.no_grad():
            y = model(x)
        assert y.shape == (1, 12, 20, 64)

    def test_output_shape_multi_batch(self) -> None:
        """Batch=4 input (4,3,80,256) -> output shape (4,12,20,64)."""
        from training.config import TrainingConfig
        from training.model import CornerHeatmapNet

        cfg = TrainingConfig()
        model = CornerHeatmapNet(cfg)
        model.eval()
        x = torch.randn(4, 3, 80, 256)
        with torch.no_grad():
            y = model(x)
        assert y.shape == (4, 12, 20, 64)


class TestCornerHeatmapNetActivations:
    """Tests for output activation ranges."""

    def test_heatmap_sigmoid_range(self) -> None:
        """Heatmap channels y[:,0:4] have min>=0.0 and max<=1.0."""
        from training.config import TrainingConfig
        from training.model import CornerHeatmapNet

        cfg = TrainingConfig()
        model = CornerHeatmapNet(cfg)
        model.eval()
        x = torch.randn(2, 3, 80, 256)
        with torch.no_grad():
            y = model(x)
        hm = y[:, 0:4]
        assert hm.min().item() >= 0.0
        assert hm.max().item() <= 1.0


class TestCornerHeatmapNetWeightInit:
    """Tests for CenterNet-style weight initialization."""

    def test_heatmap_head_bias_init(self) -> None:
        """m.heatmap_head.bias all channels initialized to -2.19 (within 0.01)."""
        from training.config import TrainingConfig
        from training.model import CornerHeatmapNet

        cfg = TrainingConfig()
        model = CornerHeatmapNet(cfg)
        bias = model.heatmap_head.bias
        assert bias is not None
        for i in range(4):
            assert abs(bias.data[i].item() - (-2.19)) < 0.01

    def test_offset_head_zero_init(self) -> None:
        """m.offset_head.weight and .bias are all zeros."""
        from training.config import TrainingConfig
        from training.model import CornerHeatmapNet

        cfg = TrainingConfig()
        model = CornerHeatmapNet(cfg)
        bias = model.offset_head.bias
        assert bias is not None
        assert torch.all(model.offset_head.weight.data == 0).item()
        assert torch.all(bias.data == 0).item()

    def test_encoder_not_reinitialized_by_centernet_init(self) -> None:
        """Verify CenterNet custom init does NOT touch encoder weights.

        Snapshot encoder conv1 weights from a standalone ResNet18, then confirm
        they match after full CornerHeatmapNet.__init__().
        """
        from torchvision.models import ResNet18_Weights, resnet18

        from training.config import TrainingConfig
        from training.model import CornerHeatmapNet

        # Get reference weights from pretrained ResNet18
        ref = resnet18(weights=ResNet18_Weights.DEFAULT)
        ref_conv1 = ref.conv1.weight.data.clone()

        # Build CornerHeatmapNet (which runs CenterNet init)
        cfg = TrainingConfig()
        model = CornerHeatmapNet(cfg)

        # Encoder conv1 in our model lives inside self.stem
        conv1_module = model.stem[0]
        assert isinstance(conv1_module, nn.Conv2d)
        model_conv1 = conv1_module.weight.data
        assert torch.allclose(ref_conv1, model_conv1), (
            "Encoder conv1 weights were modified by CenterNet init"
        )
        # Verify weights have non-trivial statistics (not zeros)
        assert model_conv1.std().item() > 0.01


class TestCornerHeatmapNetParamGroups:
    """Tests for parameter group accessors."""

    def test_param_groups_disjoint_and_nonempty(self) -> None:
        """encoder/decoder/head param id sets are all non-empty and pairwise disjoint."""
        from training.config import TrainingConfig
        from training.model import CornerHeatmapNet

        cfg = TrainingConfig()
        model = CornerHeatmapNet(cfg)
        enc = set(id(p) for p in model.encoder_params())
        dec = set(id(p) for p in model.decoder_params())
        head = set(id(p) for p in model.head_params())
        assert len(enc) > 0, "encoder_params is empty"
        assert len(dec) > 0, "decoder_params is empty"
        assert len(head) > 0, "head_params is empty"
        assert not (enc & dec), "encoder and decoder params overlap"
        assert not (enc & head), "encoder and head params overlap"
        assert not (dec & head), "decoder and head params overlap"

    def test_param_groups_cover_all_params(self) -> None:
        """len(enc)+len(dec)+len(head)==len(list(model.parameters()))."""
        from training.config import TrainingConfig
        from training.model import CornerHeatmapNet

        cfg = TrainingConfig()
        model = CornerHeatmapNet(cfg)
        enc = list(model.encoder_params())
        dec = list(model.decoder_params())
        head = list(model.head_params())
        total = list(model.parameters())
        assert len(enc) + len(dec) + len(head) == len(total), (
            f"Param groups ({len(enc)}+{len(dec)}+{len(head)}={len(enc) + len(dec) + len(head)}) "
            f"don't cover all params ({len(total)})"
        )
