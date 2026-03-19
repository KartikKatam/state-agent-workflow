from __future__ import annotations

import logging
from collections.abc import Iterator

import torch
import torch.nn as nn
from torch import Tensor
from torch.nn import Parameter
from torchvision.models import EfficientNet_B3_Weights, efficientnet_b3

from training.efficientnet_b3.config import TrainingConfig

logger = logging.getLogger(__name__)


class CornerHeatmapNet(nn.Module):
    """CenterNet-style heatmap regression network for license plate corner detection.

    Architecture: EfficientNet-B3 encoder (truncated after stage4) + transposed convolution
    decoder with skip connections + dual heads (4ch heatmap + 8ch offset).

    Stride-4: 2-stage decoder
        Input:  (N, 3, 80, 256) → Output: (N, 12, 20, 64)
    """

    def __init__(self, cfg: TrainingConfig) -> None:
        super().__init__()
        self.cfg = cfg

        # --- Encoder: pretrained EfficientNet-B3 truncated after stage4 ---
        backbone = efficientnet_b3(weights=EfficientNet_B3_Weights.DEFAULT)

        # enc_pre: features[0:3] (stem + stage1 + stage2) → (N, 32, 20, 64) stride 4
        self.enc_pre = nn.Sequential(*list(backbone.features[:3]))
        # enc_mid: features[3] (stage3) → (N, 48, 10, 32) stride 8
        self.enc_mid = backbone.features[3]
        # enc_deep: features[4] (stage4) → (N, 96, 5, 16) stride 16
        self.enc_deep = backbone.features[4]

        # --- Decoder: transposed conv with skip connections ---
        # Stage 1: (5, 16) -> (10, 32), concat enc_mid skip
        self.up1 = nn.ConvTranspose2d(96, 48, kernel_size=4, stride=2, padding=1)
        self.fuse1 = nn.Sequential(
            nn.Conv2d(96, 48, kernel_size=3, padding=1),  # cat(up1=48, skip2=48) = 96
            nn.BatchNorm2d(48),
            nn.ReLU(inplace=True),
        )

        # Stage 2: (10, 32) -> (20, 64), concat enc_pre skip
        self.up2 = nn.ConvTranspose2d(48, 32, kernel_size=4, stride=2, padding=1)
        self.fuse2 = nn.Sequential(
            nn.Conv2d(64, 32, kernel_size=3, padding=1),  # cat(up2=32, skip1=32) = 64
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
        )

        # --- Dual heads ---
        self.heatmap_head = nn.Conv2d(32, 4, kernel_size=1)
        self.offset_head = nn.Conv2d(32, 8, kernel_size=1)

        # --- CenterNet-style weight init (decoder + heads only) ---
        self._init_weights()

        logger.info(
            "CornerHeatmapNet (EfficientNet-B3) initialized: stride=%d, input=(%d,%d), output=(%d,%d), params=%d",
            cfg.stride,
            cfg.input_h,
            cfg.input_w,
            cfg.input_h // cfg.stride,
            cfg.input_w // cfg.stride,
            sum(p.numel() for p in self.parameters()),
        )

    def _init_weights(self) -> None:
        """Apply CenterNet-style initialization to decoder and heads.

        Encoder keeps ImageNet pretrained weights untouched.
        """
        # Decoder transposed convolutions: Kaiming normal
        for m in [self.up1, self.up2]:
            nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
            if m.bias is not None:
                nn.init.zeros_(m.bias)

        # Decoder fuse conv + batchnorm
        for fuse in [self.fuse1, self.fuse2]:
            for m in fuse.modules():
                if isinstance(m, nn.Conv2d):
                    nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
                    if m.bias is not None:
                        nn.init.zeros_(m.bias)
                elif isinstance(m, nn.BatchNorm2d):
                    nn.init.ones_(m.weight)
                    nn.init.zeros_(m.bias)

        # Heatmap head: bias = -2.19 so sigmoid(-2.19) ~ 0.1 (low initial confidence)
        nn.init.kaiming_normal_(self.heatmap_head.weight, mode="fan_out", nonlinearity="relu")
        assert self.heatmap_head.bias is not None
        nn.init.constant_(self.heatmap_head.bias, -2.19)

        # Offset head: zero init
        nn.init.zeros_(self.offset_head.weight)
        assert self.offset_head.bias is not None
        nn.init.zeros_(self.offset_head.bias)

    def forward(self, x: Tensor) -> Tensor:
        """Forward pass.

        Args:
            x: Input tensor of shape (N, 3, 80, 256).

        Returns:
            (N, 12, 20, 64). Channels 0-3 are sigmoid heatmaps, channels 4-11 are raw offsets.
        """
        # Encoder
        s1 = self.enc_pre(x)  # (N, 32, 20, 64) — skip1
        s2 = self.enc_mid(s1)  # (N, 48, 10, 32) — skip2
        bottleneck = self.enc_deep(s2)  # (N, 96, 5, 16)

        # Decoder stage 1: upsample + skip from enc_mid
        up1 = self.up1(bottleneck)  # (N, 48, 10, 32)
        fused1 = self.fuse1(torch.cat([up1, s2], dim=1))  # (N, 48, 10, 32)

        # Decoder stage 2: upsample + skip from enc_pre
        up2 = self.up2(fused1)  # (N, 32, 20, 64)
        features = self.fuse2(torch.cat([up2, s1], dim=1))  # (N, 32, 20, 64)

        # Dual heads
        heatmaps = torch.sigmoid(self.heatmap_head(features))
        offsets = self.offset_head(features)

        return torch.cat([heatmaps, offsets], dim=1)

    def encoder_params(self) -> Iterator[Parameter]:
        """Yield parameters from the encoder (enc_pre + enc_mid + enc_deep)."""
        yield from self.enc_pre.parameters()
        yield from self.enc_mid.parameters()
        yield from self.enc_deep.parameters()

    def decoder_params(self) -> Iterator[Parameter]:
        """Yield parameters from the decoder (upsample + fuse blocks)."""
        yield from self.up1.parameters()
        yield from self.fuse1.parameters()
        yield from self.up2.parameters()
        yield from self.fuse2.parameters()

    def head_params(self) -> Iterator[Parameter]:
        """Yield parameters from the prediction heads."""
        yield from self.heatmap_head.parameters()
        yield from self.offset_head.parameters()
