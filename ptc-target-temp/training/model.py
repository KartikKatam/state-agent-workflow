from __future__ import annotations

import logging
from collections.abc import Iterator

import torch
import torch.nn as nn
from torch import Tensor
from torch.nn import Parameter
from torchvision.models import ResNet18_Weights, resnet18

from training.config import TrainingConfig

logger = logging.getLogger(__name__)


class CornerHeatmapNet(nn.Module):
    """CenterNet-style heatmap regression network for license plate corner detection.

    Architecture: ResNet18 encoder (truncated after layer3) + transposed convolution
    decoder with skip connections + dual heads (4ch heatmap + 8ch offset).

    Stride-4 (default): 2-stage decoder
        Input:  (N, 3, 80, 256) → Output: (N, 12, 20, 64)
    Stride-2: 3-stage decoder with stem_pre skip connection
        Input:  (N, 3, 80, 256) → Output: (N, 12, 40, 128)
    """

    def __init__(self, cfg: TrainingConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self._stride2 = cfg.stride == 2

        # --- Encoder: pretrained ResNet18 truncated after layer3 ---
        backbone = resnet18(weights=ResNet18_Weights.DEFAULT)

        if self._stride2:
            # Split stem to expose pre-maxpool features at (N, 64, 40, 128)
            self.stem_pre = nn.Sequential(
                backbone.conv1,
                backbone.bn1,
                backbone.relu,
            )
            self.stem_post = backbone.maxpool
        else:
            # stem: conv1 + bn1 + relu + maxpool -> (N, 64, 20, 64) stride 4
            self.stem = nn.Sequential(
                backbone.conv1,
                backbone.bn1,
                backbone.relu,
                backbone.maxpool,
            )
        # layer1: (N, 64, 20, 64) stride 4 — skip connection target
        self.layer1 = backbone.layer1
        # layer2: (N, 128, 10, 32) stride 8 — skip connection target
        self.layer2 = backbone.layer2
        # layer3: (N, 256, 5, 16) stride 16 — bottleneck
        self.layer3 = backbone.layer3

        # --- Decoder: transposed conv with skip connections ---
        # Stage 1: (5, 16) -> (10, 32), concat layer2 skip
        self.up1 = nn.ConvTranspose2d(256, 128, kernel_size=4, stride=2, padding=1)
        self.fuse1 = nn.Sequential(
            nn.Conv2d(256, 128, kernel_size=3, padding=1),
            nn.BatchNorm2d(128),
            nn.ReLU(inplace=True),
        )

        # Stage 2: (10, 32) -> (20, 64), concat layer1 skip
        self.up2 = nn.ConvTranspose2d(128, 64, kernel_size=4, stride=2, padding=1)
        self.fuse2 = nn.Sequential(
            nn.Conv2d(128, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
        )

        if self._stride2:
            # Stage 3: (20, 64) -> (40, 128), concat stem_pre skip
            self.up3 = nn.ConvTranspose2d(64, 32, kernel_size=4, stride=2, padding=1)
            self.fuse3 = nn.Sequential(
                nn.Conv2d(96, 32, kernel_size=3, padding=1),  # 32 + 64 = 96
                nn.BatchNorm2d(32),
                nn.ReLU(inplace=True),
            )
            head_in_ch = 32
        else:
            head_in_ch = 64

        # --- Dual heads ---
        self.heatmap_head = nn.Conv2d(head_in_ch, 4, kernel_size=1)
        self.offset_head = nn.Conv2d(head_in_ch, 8, kernel_size=1)

        # --- CenterNet-style weight init (decoder + heads only) ---
        self._init_weights()

        logger.info(
            "CornerHeatmapNet initialized: stride=%d, input=(%d,%d), output=(%d,%d), params=%d",
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
        deconvs = [self.up1, self.up2]
        if self._stride2:
            deconvs.append(self.up3)
        for m in deconvs:
            nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
            if m.bias is not None:
                nn.init.zeros_(m.bias)

        # Decoder fuse conv + batchnorm
        fuses = [self.fuse1, self.fuse2]
        if self._stride2:
            fuses.append(self.fuse3)
        for fuse in fuses:
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
            Stride-4: (N, 12, 20, 64), Stride-2: (N, 12, 40, 128).
            Channels 0-3 are sigmoid heatmaps, channels 4-11 are raw offsets.
        """
        # Encoder
        s0: Tensor | None = None
        if self._stride2:
            s0 = self.stem_pre(x)       # (N, 64, 40, 128) — skip 0
            x0 = self.stem_post(s0)     # (N, 64, 20, 64)
        else:
            x0 = self.stem(x)           # (N, 64, 20, 64)
        s1 = self.layer1(x0)            # (N, 64, 20, 64) — skip 1
        s2 = self.layer2(s1)            # (N, 128, 10, 32) — skip 2
        bottleneck = self.layer3(s2)    # (N, 256, 5, 16)

        # Decoder stage 1: upsample + skip from layer2
        up1 = self.up1(bottleneck)                          # (N, 128, 10, 32)
        fused1 = self.fuse1(torch.cat([up1, s2], dim=1))   # (N, 128, 10, 32)

        # Decoder stage 2: upsample + skip from layer1
        up2 = self.up2(fused1)                              # (N, 64, 20, 64)
        fused2 = self.fuse2(torch.cat([up2, s1], dim=1))   # (N, 64, 20, 64)

        if self._stride2 and s0 is not None:
            # Decoder stage 3: upsample + skip from stem_pre
            up3 = self.up3(fused2)                                  # (N, 32, 40, 128)
            features = self.fuse3(torch.cat([up3, s0], dim=1))     # (N, 32, 40, 128)
        else:
            features = fused2                                       # (N, 64, 20, 64)

        # Dual heads
        heatmaps = torch.sigmoid(self.heatmap_head(features))
        offsets = self.offset_head(features)

        return torch.cat([heatmaps, offsets], dim=1)

    def encoder_params(self) -> Iterator[Parameter]:
        """Yield parameters from the encoder (stem + layer1-3)."""
        if self._stride2:
            yield from self.stem_pre.parameters()
            yield from self.stem_post.parameters()
        else:
            yield from self.stem.parameters()
        yield from self.layer1.parameters()
        yield from self.layer2.parameters()
        yield from self.layer3.parameters()

    def decoder_params(self) -> Iterator[Parameter]:
        """Yield parameters from the decoder (upsample + fuse blocks)."""
        yield from self.up1.parameters()
        yield from self.fuse1.parameters()
        yield from self.up2.parameters()
        yield from self.fuse2.parameters()
        if self._stride2:
            yield from self.up3.parameters()
            yield from self.fuse3.parameters()

    def head_params(self) -> Iterator[Parameter]:
        """Yield parameters from the prediction heads."""
        yield from self.heatmap_head.parameters()
        yield from self.offset_head.parameters()
