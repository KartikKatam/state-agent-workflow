"""CornerRegressionNetS2 — stride-2 soft-argmax regression model.

Architecture: ResNet18 encoder with split stem + 3-stage transposed convolution
decoder with skip connections + spatial attention head with soft-argmax.

Compared to stride-4 CornerRegressionNet:
- Stem split into stem_pre (conv1+bn1+relu) and stem_post (maxpool)
- Third decoder stage: up3/fuse3 upsamples to 40x128 with stem_pre skip
- Attention head operates on 32ch (not 64ch) over 5120 positions (not 1280)
- Coordinate grids at stride-2: x=[0,2,...,254], y=[0,2,...,78]

Input:  (N, 3, 80, 256)
Output: (N, 8) corner coordinates [x0, y0, x1, y1, x2, y2, x3, y3]
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import torch
import torch.nn as nn
from torch import Tensor
from torch.nn import Parameter
from torchvision.models import ResNet18_Weights, resnet18

from training.regression.config import RegressionConfig

logger = logging.getLogger(__name__)


class CornerRegressionNetS2(nn.Module):
    """Stride-2 soft-argmax regression network for license plate corner detection.

    3-stage decoder outputs at 40x128 (stride-2) instead of 20x64 (stride-4).
    The stem is split to expose pre-maxpool features as a skip connection source.
    """

    x_grid: Tensor
    y_grid: Tensor

    def __init__(self, cfg: RegressionConfig) -> None:
        super().__init__()
        self.cfg = cfg

        grid_h = cfg.input_h // cfg.stride  # 40
        grid_w = cfg.input_w // cfg.stride  # 128

        # --- Encoder: pretrained ResNet18 truncated after layer3 ---
        backbone = resnet18(weights=ResNet18_Weights.DEFAULT)

        # Split stem to expose pre-maxpool features at (N, 64, 40, 128)
        self.stem_pre = nn.Sequential(
            backbone.conv1,
            backbone.bn1,
            backbone.relu,
        )
        self.stem_post = backbone.maxpool

        self.layer1 = backbone.layer1
        self.layer2 = backbone.layer2
        self.layer3 = backbone.layer3

        # --- Decoder: 3-stage transposed conv with skip connections ---
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

        # Stage 3: (20, 64) -> (40, 128), concat stem_pre skip
        self.up3 = nn.ConvTranspose2d(64, 32, kernel_size=4, stride=2, padding=1)
        self.fuse3 = nn.Sequential(
            nn.Conv2d(96, 32, kernel_size=3, padding=1),  # 32 + 64 = 96
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
        )

        # --- Attention head: 4 channels (one per corner), 32ch input ---
        self.attention_head = nn.Conv2d(32, 4, kernel_size=1)

        # --- Coordinate grids (registered as buffers, non-trainable) ---
        x_coords = torch.arange(grid_w, dtype=torch.float32) * cfg.stride  # [0, 2, 4, ..., 254]
        y_coords = torch.arange(grid_h, dtype=torch.float32) * cfg.stride  # [0, 2, 4, ..., 78]

        y_grid, x_grid = torch.meshgrid(y_coords, x_coords, indexing="ij")
        self.register_buffer("x_grid", x_grid.unsqueeze(0).unsqueeze(0))  # (1, 1, 40, 128)
        self.register_buffer("y_grid", y_grid.unsqueeze(0).unsqueeze(0))  # (1, 1, 40, 128)

        # --- Weight initialization ---
        self._init_weights()

        logger.info(
            "CornerRegressionNetS2 initialized: stride=%d, input=(%d,%d), grid=(%d,%d), params=%d",
            cfg.stride,
            cfg.input_h,
            cfg.input_w,
            grid_h,
            grid_w,
            sum(p.numel() for p in self.parameters()),
        )

    def _init_weights(self) -> None:
        """Apply CenterNet-style initialization to decoder and attention head."""
        # Decoder transposed convolutions: Kaiming normal
        for m in [self.up1, self.up2, self.up3]:
            nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
            if m.bias is not None:
                nn.init.zeros_(m.bias)

        # Decoder fuse conv + batchnorm
        for fuse in [self.fuse1, self.fuse2, self.fuse3]:
            for m in fuse.modules():
                if isinstance(m, nn.Conv2d):
                    nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
                    if m.bias is not None:
                        nn.init.zeros_(m.bias)
                elif isinstance(m, nn.BatchNorm2d):
                    nn.init.ones_(m.weight)
                    nn.init.zeros_(m.bias)

        # Attention head: Kaiming weights, zero bias
        nn.init.kaiming_normal_(self.attention_head.weight, mode="fan_out", nonlinearity="relu")
        assert self.attention_head.bias is not None
        nn.init.zeros_(self.attention_head.bias)

    def _encode_decode(self, x: Tensor) -> Tensor:
        """Run encoder + 3-stage decoder, return stride-2 feature map."""
        # Encoder with split stem
        s0 = self.stem_pre(x)          # (N, 64, 40, 128) — skip 0
        x0 = self.stem_post(s0)        # (N, 64, 20, 64)
        s1 = self.layer1(x0)           # (N, 64, 20, 64) — skip 1
        s2 = self.layer2(s1)           # (N, 128, 10, 32) — skip 2
        bottleneck = self.layer3(s2)   # (N, 256, 5, 16)

        # Decoder stage 1
        up1 = self.up1(bottleneck)                          # (N, 128, 10, 32)
        fused1 = self.fuse1(torch.cat([up1, s2], dim=1))   # (N, 128, 10, 32)

        # Decoder stage 2
        up2 = self.up2(fused1)                              # (N, 64, 20, 64)
        fused2 = self.fuse2(torch.cat([up2, s1], dim=1))   # (N, 64, 20, 64)

        # Decoder stage 3 (stride-2)
        up3 = self.up3(fused2)                              # (N, 32, 40, 128)
        fused3 = self.fuse3(torch.cat([up3, s0], dim=1))   # (N, 32, 40, 128)

        return fused3

    def _soft_argmax(self, logits: Tensor) -> tuple[Tensor, Tensor]:
        """Compute soft-argmax coordinates from attention logits.

        Args:
            logits: (N, 4, H, W) raw attention logits.

        Returns:
            coords: (N, 8) pixel coordinates [x0, y0, x1, y1, ...].
            attention: (N, 4, H, W) normalized attention weights.
        """
        N, C, H, W = logits.shape

        # Temperature scaling
        scaled = logits / self.cfg.attention_temperature

        # Spatial softmax over flattened spatial dimension
        flat = scaled.reshape(N, C, H * W)          # (N, 4, 5120)
        attention = torch.softmax(flat, dim=2)       # (N, 4, 5120)
        attention = attention.reshape(N, C, H, W)    # (N, 4, 40, 128)

        # Expected coordinates via weighted sum
        x_coords = (attention * self.x_grid).sum(dim=(2, 3))  # (N, 4)
        y_coords = (attention * self.y_grid).sum(dim=(2, 3))  # (N, 4)

        # Interleave: [x0, y0, x1, y1, x2, y2, x3, y3]
        coords = torch.stack([x_coords, y_coords], dim=2).reshape(N, 8)

        return coords, attention

    def forward(self, x: Tensor) -> Tensor:
        """Forward pass returning coordinates only.

        Args:
            x: (N, 3, 80, 256) input images.

        Returns:
            (N, 8) corner coordinates in pixel space.
        """
        features = self._encode_decode(x)
        logits = self.attention_head(features)  # (N, 4, 40, 128)
        coords, _ = self._soft_argmax(logits)
        return coords

    def forward_with_attention(self, x: Tensor) -> tuple[Tensor, Tensor]:
        """Forward pass returning both coordinates and attention maps.

        Args:
            x: (N, 3, 80, 256) input images.

        Returns:
            coords: (N, 8) corner coordinates in pixel space.
            attention: (N, 4, 40, 128) normalized attention weights.
        """
        features = self._encode_decode(x)
        logits = self.attention_head(features)
        return self._soft_argmax(logits)

    def encoder_params(self) -> Iterator[Parameter]:
        """Yield parameters from the encoder (stem_pre + stem_post + layer1-3)."""
        yield from self.stem_pre.parameters()
        yield from self.stem_post.parameters()
        yield from self.layer1.parameters()
        yield from self.layer2.parameters()
        yield from self.layer3.parameters()

    def decoder_params(self) -> Iterator[Parameter]:
        """Yield parameters from the decoder (3 upsample + fuse stages)."""
        yield from self.up1.parameters()
        yield from self.fuse1.parameters()
        yield from self.up2.parameters()
        yield from self.fuse2.parameters()
        yield from self.up3.parameters()
        yield from self.fuse3.parameters()

    def head_params(self) -> Iterator[Parameter]:
        """Yield parameters from the attention head."""
        yield from self.attention_head.parameters()
