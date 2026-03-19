"""CornerRegressionNetWithVariance — extends base model with learned uncertainty.

Adds a variance head parallel to the attention head. Each corner gets a predicted
sigma (in pixels) representing the model's uncertainty about that corner.

Input:  (N, 3, 80, 256)
Output: (N, 8) coordinates + (N, 4) per-corner sigma in pixel units
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


class CornerRegressionNetWithVariance(nn.Module):
    """Soft-argmax regression network with learned per-corner variance.

    Architecture identical to CornerRegressionNet, plus a variance head
    that predicts per-corner sigma via global average pooling + softplus.
    """

    x_grid: Tensor
    y_grid: Tensor

    def __init__(self, cfg: RegressionConfig) -> None:
        super().__init__()
        self.cfg = cfg

        grid_h = cfg.input_h // cfg.stride  # 20
        grid_w = cfg.input_w // cfg.stride  # 64

        # --- Encoder: pretrained ResNet18 truncated after layer3 ---
        backbone = resnet18(weights=ResNet18_Weights.DEFAULT)

        self.stem = nn.Sequential(
            backbone.conv1,
            backbone.bn1,
            backbone.relu,
            backbone.maxpool,
        )
        self.layer1 = backbone.layer1
        self.layer2 = backbone.layer2
        self.layer3 = backbone.layer3

        # --- Decoder: transposed conv with skip connections ---
        self.up1 = nn.ConvTranspose2d(256, 128, kernel_size=4, stride=2, padding=1)
        self.fuse1 = nn.Sequential(
            nn.Conv2d(256, 128, kernel_size=3, padding=1),
            nn.BatchNorm2d(128),
            nn.ReLU(inplace=True),
        )

        self.up2 = nn.ConvTranspose2d(128, 64, kernel_size=4, stride=2, padding=1)
        self.fuse2 = nn.Sequential(
            nn.Conv2d(128, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
        )

        # --- Attention head: 4 channels (one per corner) ---
        self.attention_head = nn.Conv2d(64, 4, kernel_size=1)

        # --- Variance head: 4 channels (one sigma per corner) ---
        self.variance_head = nn.Conv2d(64, 4, kernel_size=1)

        # --- Coordinate grids (registered as buffers, non-trainable) ---
        x_coords = torch.arange(grid_w, dtype=torch.float32) * cfg.stride
        y_coords = torch.arange(grid_h, dtype=torch.float32) * cfg.stride

        y_grid, x_grid = torch.meshgrid(y_coords, x_coords, indexing="ij")
        self.register_buffer("x_grid", x_grid.unsqueeze(0).unsqueeze(0))
        self.register_buffer("y_grid", y_grid.unsqueeze(0).unsqueeze(0))

        # --- Weight initialization ---
        self._init_weights()

        logger.info(
            "CornerRegressionNetWithVariance initialized: stride=%d, input=(%d,%d), "
            "grid=(%d,%d), params=%d",
            cfg.stride,
            cfg.input_h,
            cfg.input_w,
            grid_h,
            grid_w,
            sum(p.numel() for p in self.parameters()),
        )

    def _init_weights(self) -> None:
        """Apply CenterNet-style initialization to decoder, attention, and variance heads."""
        for m in [self.up1, self.up2]:
            nn.init.kaiming_normal_(m.weight, mode="fan_out", nonlinearity="relu")
            if m.bias is not None:
                nn.init.zeros_(m.bias)

        for fuse in [self.fuse1, self.fuse2]:
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

        # Variance head: zero weights, bias=0 -> softplus(0) ≈ 0.69 -> moderate initial uncertainty
        nn.init.zeros_(self.variance_head.weight)
        assert self.variance_head.bias is not None
        nn.init.constant_(self.variance_head.bias, 0.0)

    def _encode_decode(self, x: Tensor) -> Tensor:
        """Run encoder + decoder, return stride-4 feature map."""
        x0 = self.stem(x)
        s1 = self.layer1(x0)
        s2 = self.layer2(s1)
        bottleneck = self.layer3(s2)

        up1 = self.up1(bottleneck)
        fused1 = self.fuse1(torch.cat([up1, s2], dim=1))

        up2 = self.up2(fused1)
        fused2 = self.fuse2(torch.cat([up2, s1], dim=1))

        return fused2

    def _soft_argmax(self, logits: Tensor) -> tuple[Tensor, Tensor]:
        """Compute soft-argmax coordinates from attention logits."""
        N, C, H, W = logits.shape

        scaled = logits / self.cfg.attention_temperature

        flat = scaled.reshape(N, C, H * W)
        attention = torch.softmax(flat, dim=2)
        attention = attention.reshape(N, C, H, W)

        x_coords = (attention * self.x_grid).sum(dim=(2, 3))
        y_coords = (attention * self.y_grid).sum(dim=(2, 3))

        coords = torch.stack([x_coords, y_coords], dim=2).reshape(N, 8)

        return coords, attention

    def _predict_sigma(self, features: Tensor) -> Tensor:
        """Predict per-corner sigma from decoder features.

        Args:
            features: (N, 64, 20, 64) decoder output.

        Returns:
            sigma: (N, 4) per-corner sigma in pixel units.
        """
        var_logits = self.variance_head(features)  # (N, 4, 20, 64)
        var_pooled = var_logits.mean(dim=(2, 3))  # (N, 4)
        sigma = torch.nn.functional.softplus(var_pooled) * self.cfg.stride  # (N, 4)
        sigma = sigma.clamp(min=1e-4)
        return sigma

    def forward(self, x: Tensor) -> tuple[Tensor, Tensor]:
        """Forward pass returning coordinates and sigma.

        Args:
            x: (N, 3, 80, 256) input images.

        Returns:
            coords: (N, 8) corner coordinates in pixel space.
            sigma: (N, 4) per-corner predicted sigma in pixel units.
        """
        features = self._encode_decode(x)
        logits = self.attention_head(features)
        coords, _ = self._soft_argmax(logits)
        sigma = self._predict_sigma(features)
        return coords, sigma

    def forward_with_attention(self, x: Tensor) -> tuple[Tensor, Tensor, Tensor]:
        """Forward pass returning coordinates, attention maps, and sigma.

        Args:
            x: (N, 3, 80, 256) input images.

        Returns:
            coords: (N, 8) corner coordinates in pixel space.
            attention: (N, 4, 20, 64) normalized attention weights.
            sigma: (N, 4) per-corner predicted sigma in pixel units.
        """
        features = self._encode_decode(x)
        logits = self.attention_head(features)
        coords, attention = self._soft_argmax(logits)
        sigma = self._predict_sigma(features)
        return coords, attention, sigma

    def encoder_params(self) -> Iterator[Parameter]:
        """Yield parameters from the encoder (stem + layer1-3)."""
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

    def head_params(self) -> Iterator[Parameter]:
        """Yield parameters from the attention head and variance head."""
        yield from self.attention_head.parameters()
        yield from self.variance_head.parameters()
