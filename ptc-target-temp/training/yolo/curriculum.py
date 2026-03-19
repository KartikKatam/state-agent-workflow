"""CurriculumScheduler — continuous curriculum with backbone freezing.

Registered via ``trainer.add_callback("on_train_epoch_start", scheduler.on_epoch_start)``.
Updates augmentation intensity every epoch (smooth ramp, not hard jumps),
manages backbone freezing/unfreezing, and logs phase transitions to console/W&B.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import torch.nn as nn

if TYPE_CHECKING:
    from training.yolo.config import YoloTrainingConfig

try:
    import wandb
except ImportError:
    wandb = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class CurriculumScheduler:
    """Continuous curriculum that scales augmentation intensity every epoch.

    Call :meth:`on_epoch_start` from the Ultralytics ``on_train_epoch_start``
    callback. The scheduler computes a per-epoch intensity value via
    ``cfg.get_intensity(epoch)`` and updates the trainer's augmentation
    parameters smoothly. Phase labels (WARM/RAMP/REFINE) are kept for logging.
    """

    def __init__(self, cfg: YoloTrainingConfig, trainer: Any) -> None:
        self.cfg = cfg
        self.trainer = trainer
        self._last_phase: str = "WARM"
        self._last_intensity: float = 0.0
        self._unfrozen: bool = False

    # ------------------------------------------------------------------
    # Main callback
    # ------------------------------------------------------------------

    def on_epoch_start(self, trainer_arg: Any) -> None:
        """Called at the start of each training epoch.

        Args:
            trainer_arg: The Ultralytics trainer object (has ``.epoch``, ``.model``).
        """
        epoch: int = trainer_arg.epoch
        intensity = self.cfg.get_intensity(epoch)
        phase = self.cfg.get_phase(epoch)

        # Log phase transitions (still useful for W&B dashboards)
        if phase != self._last_phase:
            self._log_phase_transition(self._last_phase, phase, intensity)
            self._last_phase = phase

        # Update augmentation intensity every epoch
        self.trainer.update_augmentation_intensity(intensity)
        self._last_intensity = intensity

        # Backbone freezing management (unchanged)
        if self.cfg.freeze_backbone:
            if epoch < self.cfg.freeze_epochs:
                self._freeze_backbone(trainer_arg)
            elif epoch == self.cfg.freeze_epochs and not self._unfrozen:
                self._unfreeze_backbone(trainer_arg)
                self._unfrozen = True

    # ------------------------------------------------------------------
    # Phase transition logging
    # ------------------------------------------------------------------

    def _log_phase_transition(self, old_phase: str, new_phase: str, intensity: float) -> None:
        """Log transition to console and W&B."""
        logger.info("curriculum phase: %s -> %s (intensity=%.2f)", old_phase, new_phase, intensity)

        if wandb is not None and wandb.run is not None:
            wandb.log(
                {
                    "curriculum/phase": new_phase,
                    "curriculum/intensity": intensity,
                    "curriculum/drone_aug_active": intensity > 0.0 and self.cfg.drone_aug_enabled,
                }
            )

    # ------------------------------------------------------------------
    # Backbone freeze / unfreeze
    # ------------------------------------------------------------------

    def _freeze_backbone(self, trainer_arg: Any) -> None:
        """Freeze the first ``freeze_layers`` parameters and set frozen BN to eval."""
        model = trainer_arg.model

        # Track which parameter names are frozen
        frozen_param_names: set[str] = set()
        for i, (name, param) in enumerate(model.named_parameters()):
            if i >= self.cfg.freeze_layers:
                break
            param.requires_grad = False
            frozen_param_names.add(name)

        # Set BatchNorm modules with all-frozen params to eval mode
        # to prevent running_mean / running_var updates
        for name, module in model.named_modules():
            if isinstance(module, (nn.BatchNorm1d, nn.BatchNorm2d, nn.SyncBatchNorm)):
                # Check if all of this module's own parameters are frozen
                module_param_names = {
                    f"{name}.{pname}" for pname, _ in module.named_parameters(recurse=False)
                }
                if module_param_names and module_param_names.issubset(frozen_param_names):
                    module.eval()

        logger.debug(
            "froze %d parameters, %d BN modules set to eval",
            len(frozen_param_names),
            sum(
                1
                for _, m in model.named_modules()
                if isinstance(m, (nn.BatchNorm1d, nn.BatchNorm2d, nn.SyncBatchNorm))
                and not m.training
            ),
        )

    def _unfreeze_backbone(self, trainer_arg: Any) -> None:
        """Unfreeze all model parameters."""
        model = trainer_arg.model
        for param in model.parameters():
            param.requires_grad = True
        logger.info("backbone unfrozen at epoch %d", trainer_arg.epoch)
