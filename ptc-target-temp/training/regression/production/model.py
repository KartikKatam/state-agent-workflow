"""ProductionCornerNet — unified stride-2/4 model with variance head.

Combines CornerRegressionNet (stride-4) and CornerRegressionNetS2 (stride-2)
with a learned variance head for uncertainty estimation.

Input:  (N, 3, 80, 256)
Output: (N, 8) coordinates + (N, 8) log_sigma
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import torch
import torch.nn as nn
from torch import Tensor
from torch.nn import Parameter
from torchvision.models import ResNet18_Weights, resnet18

from training.regression.production.config import ProductionConfig

logger = logging.getLogger(__name__)


class ProductionCornerNet(nn.Module):
    """Soft-argmax regression network with variance head and configurable stride.

    Stride-4: 2-stage decoder, 20x64 attention maps (1280 positions), 64ch heads
    Stride-2: 3-stage decoder with split stem, 40x128 attention maps (5120 positions), 32ch heads
    """

    x_grid: Tensor
    y_grid: Tensor

    def __init__(self, cfg: ProductionConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self._stride = cfg.stride

        grid_h = cfg.input_h // cfg.stride
        grid_w = cfg.input_w // cfg.stride

        # --- Encoder: pretrained ResNet18 truncated after layer3 ---
        backbone = resnet18(weights=ResNet18_Weights.DEFAULT)

        if cfg.stride == 2:
            self.stem_pre = nn.Sequential(
                backbone.conv1,
                backbone.bn1,
                backbone.relu,
            )
            self.stem_post = backbone.maxpool
            self._has_split_stem = True
        else:
            self.stem = nn.Sequential(
                backbone.conv1,
                backbone.bn1,
                backbone.relu,
                backbone.maxpool,
            )
            self._has_split_stem = False

        self.layer1 = backbone.layer1
        self.layer2 = backbone.layer2
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

        if cfg.stride == 2:
            # Stage 3: (20, 64) -> (40, 128), concat stem_pre skip
            self.up3 = nn.ConvTranspose2d(64, 32, kernel_size=4, stride=2, padding=1)
            self.fuse3 = nn.Sequential(
                nn.Conv2d(96, 32, kernel_size=3, padding=1),  # 32 + 64 = 96
                nn.BatchNorm2d(32),
                nn.ReLU(inplace=True),
            )
            head_ch = 32
        else:
            head_ch = 64

        # --- Attention head: 4 channels (one per corner) ---
        self.attention_head = nn.Conv2d(head_ch, 4, kernel_size=1)

        # --- Variance head: 8 channels (one per coordinate) ---
        self.variance_head = nn.Conv2d(head_ch, 8, kernel_size=1)

        # --- Coordinate grids (registered as buffers, non-trainable) ---
        x_coords = torch.arange(grid_w, dtype=torch.float32) * cfg.stride
        y_coords = torch.arange(grid_h, dtype=torch.float32) * cfg.stride

        y_grid, x_grid = torch.meshgrid(y_coords, x_coords, indexing="ij")
        self.register_buffer("x_grid", x_grid.unsqueeze(0).unsqueeze(0).contiguous())
        self.register_buffer("y_grid", y_grid.unsqueeze(0).unsqueeze(0).contiguous())

        # --- Weight initialization ---
        self._init_weights()

        logger.info(
            "ProductionCornerNet initialized: stride=%d, grid=(%d,%d), params=%d",
            cfg.stride,
            grid_h,
            grid_w,
            sum(p.numel() for p in self.parameters()),
        )

    def _init_weights(self) -> None:
        """Apply CenterNet-style initialization to decoder and heads."""
        up_modules = [self.up1, self.up2]
        fuse_modules = [self.fuse1, self.fuse2]
        if self._has_split_stem:
            up_modules.append(self.up3)
            fuse_modules.append(self.fuse3)

        for m in up_modules:
            nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
            if m.bias is not None:
                nn.init.zeros_(m.bias)

        for fuse in fuse_modules:
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

        # Variance head: zero init -> log_sigma ≈ 0 -> sigma ≈ 1.0 initially
        nn.init.zeros_(self.variance_head.weight)
        assert self.variance_head.bias is not None
        nn.init.zeros_(self.variance_head.bias)

    def _encode_decode(self, x: Tensor) -> Tensor:
        """Run encoder + decoder, return feature map at configured stride."""
        s0: Tensor | None = None
        if self._has_split_stem:
            s0 = self.stem_pre(x)  # (N, 64, 40, 128)
            x0 = self.stem_post(s0)  # (N, 64, 20, 64)
        else:
            x0 = self.stem(x)  # (N, 64, 20, 64)

        s1 = self.layer1(x0)  # (N, 64, 20, 64) — skip 1
        s2 = self.layer2(s1)  # (N, 128, 10, 32) — skip 2
        bottleneck = self.layer3(s2)  # (N, 256, 5, 16)

        # Decoder stage 1
        up1 = self.up1(bottleneck)
        fused1 = self.fuse1(torch.cat([up1, s2], dim=1))

        # Decoder stage 2
        up2 = self.up2(fused1)
        fused2 = self.fuse2(torch.cat([up2, s1], dim=1))

        if self._has_split_stem:
            assert s0 is not None
            skip0: Tensor = s0
            # Decoder stage 3 (stride-2)
            up3 = self.up3(fused2)
            fused3 = self.fuse3(torch.cat([up3, skip0], dim=1))
            return fused3

        return fused2

    def _soft_argmax(self, logits: Tensor) -> tuple[Tensor, Tensor]:
        """Compute soft-argmax coordinates from attention logits."""
        N, C, H, W = logits.shape

        scaled = logits / self.cfg.attention_temperature

        flat = scaled.reshape(N, C, H * W)
        attention = torch.softmax(flat, dim=2)
        attention = attention.reshape(N, C, H, W)

        x_coords = (attention * self.x_grid).sum(dim=(2, 3))  # (N, 4)
        y_coords = (attention * self.y_grid).sum(dim=(2, 3))  # (N, 4)

        coords = torch.stack([x_coords, y_coords], dim=2).reshape(N, 8)

        return coords, attention

    def _predict_log_sigma(self, features: Tensor) -> Tensor:
        """Predict per-coordinate log(sigma) from decoder features via GAP."""
        var_logits = self.variance_head(features)  # (N, 8, H, W)
        log_sigma = var_logits.mean(dim=(2, 3))  # (N, 8)
        return log_sigma

    def forward(self, x: Tensor) -> tuple[Tensor, Tensor]:
        """Forward pass returning coordinates and log_sigma.

        Args:
            x: (N, 3, 80, 256) input images.

        Returns:
            coords: (N, 8) corner coordinates in pixel space.
            log_sigma: (N, 8) per-coordinate log(sigma).
        """
        features = self._encode_decode(x)
        logits = self.attention_head(features)
        coords, _ = self._soft_argmax(logits)
        log_sigma = self._predict_log_sigma(features)
        return coords, log_sigma

    def forward_with_attention(self, x: Tensor) -> tuple[Tensor, Tensor, Tensor]:
        """Forward pass returning coordinates, log_sigma, and attention maps.

        Args:
            x: (N, 3, 80, 256) input images.

        Returns:
            coords: (N, 8) corner coordinates in pixel space.
            log_sigma: (N, 8) per-coordinate log(sigma).
            attention: (N, 4, H, W) normalized attention weights.
        """
        features = self._encode_decode(x)
        logits = self.attention_head(features)
        coords, attention = self._soft_argmax(logits)
        log_sigma = self._predict_log_sigma(features)
        return coords, log_sigma, attention

    def encoder_params(self) -> Iterator[Parameter]:
        """Yield parameters from the encoder."""
        if self._has_split_stem:
            yield from self.stem_pre.parameters()
            # stem_post is MaxPool2d — no parameters
        else:
            yield from self.stem.parameters()
        yield from self.layer1.parameters()
        yield from self.layer2.parameters()
        yield from self.layer3.parameters()

    def decoder_params(self) -> Iterator[Parameter]:
        """Yield parameters from the decoder."""
        yield from self.up1.parameters()
        yield from self.fuse1.parameters()
        yield from self.up2.parameters()
        yield from self.fuse2.parameters()
        if self._has_split_stem:
            yield from self.up3.parameters()
            yield from self.fuse3.parameters()

    def head_params(self) -> Iterator[Parameter]:
        """Yield parameters from the attention and variance heads."""
        yield from self.attention_head.parameters()
        yield from self.variance_head.parameters()
