"""Hard-negative mining — scores training images by difficulty and oversamples the hardest."""

from __future__ import annotations

import logging

import cv2
import numpy as np
import torch
from ultralytics.data.augment import LetterBox
from ultralytics.utils.nms import non_max_suppression

logger = logging.getLogger(__name__)

try:
    import wandb
except ImportError:
    wandb = None  # type: ignore[assignment]


class HardNegativeMiner:
    """Validation-pass hard-negative mining for weighted dataset oversampling.

    Runs every ``mining_interval`` epochs (after ``mining_min_epoch``),
    scoring each training image by detection difficulty and boosting
    the hardest ``mining_top_fraction`` with ``mining_oversample_factor``.
    """

    def __init__(self, cfg: object, trainer: object) -> None:
        self.cfg = cfg
        self.trainer = trainer

    # ------------------------------------------------------------------
    # Public scoring formula
    # ------------------------------------------------------------------

    @staticmethod
    def compute_difficulty(n_gt: int, n_det: int, avg_conf: float) -> float:
        """Compute per-image difficulty score.

        Formula: ``(1 - avg_conf) + 2 * miss_rate``
        where ``miss_rate = max(0, n_gt - n_det) / n_gt``.

        Range [0, 3].  No detections → 3.0, perfect → 0.0.
        """
        miss_rate = max(0, n_gt - n_det) / n_gt
        return (1.0 - avg_conf) + 2.0 * miss_rate

    # ------------------------------------------------------------------
    # Epoch callback
    # ------------------------------------------------------------------

    def on_epoch_end(self, trainer_arg: object) -> None:
        """Guard-gated entry point called after each training epoch."""
        if not self.cfg.mining_enabled:  # type: ignore[attr-defined]
            return

        epoch: int = trainer_arg.epoch  # type: ignore[attr-defined]
        if epoch < self.cfg.mining_min_epoch:  # type: ignore[attr-defined]
            return
        if epoch % self.cfg.mining_interval != 0:  # type: ignore[attr-defined]
            return

        logger.info("Mining triggered at epoch %d", epoch)
        self._run_mining_pass(trainer_arg)

    # ------------------------------------------------------------------
    # Mining pass
    # ------------------------------------------------------------------

    def _run_mining_pass(self, trainer_arg: object) -> None:
        """Score all training images and update sample weights."""
        scored = self._score_images(trainer_arg)

        dataset = trainer_arg.train_loader.dataset  # type: ignore[attr-defined]
        n_images = len(dataset.labels)
        weights = np.ones(n_images, dtype=np.float64)

        if not scored:
            logger.warning("Mining pass scored 0 images — keeping uniform weights")
            self.trainer.update_sample_weights(weights)  # type: ignore[attr-defined]
            return

        difficulties = np.array(list(scored.values()))
        threshold = float(
            np.percentile(difficulties, (1.0 - self.cfg.mining_top_fraction) * 100)  # type: ignore[attr-defined]
        )

        n_hard = 0
        for idx, diff in scored.items():
            if diff >= threshold:
                weights[idx] = self.cfg.mining_oversample_factor  # type: ignore[attr-defined]
                n_hard += 1

        self.trainer.update_sample_weights(weights)  # type: ignore[attr-defined]

        logger.info(
            "Mining pass: %d images scored, threshold=%.2f, %d hard images (%.1f%%)",
            len(scored),
            threshold,
            n_hard,
            100.0 * n_hard / len(scored) if scored else 0.0,
        )

        if wandb is not None:
            try:
                wandb.log(
                    {
                        "mining/threshold": threshold,
                        "mining/n_scored": len(scored),
                        "mining/n_hard": n_hard,
                        "mining/mean_difficulty": float(difficulties.mean()),
                    }
                )
            except Exception:
                logger.debug("wandb.log failed in mining pass", exc_info=True)

    # ------------------------------------------------------------------
    # Image scoring
    # ------------------------------------------------------------------

    def _score_images(self, trainer_arg: object) -> dict[int, float]:
        """Iterate training images, run inference, return {idx: difficulty}.

        Background images (n_gt == 0) are excluded from scoring.
        """
        model = (
            trainer_arg.ema.ema  # type: ignore[attr-defined]
            if hasattr(trainer_arg, "ema") and trainer_arg.ema is not None  # type: ignore[attr-defined]
            else trainer_arg.model  # type: ignore[attr-defined]
        )
        model.eval()

        dataset = trainer_arg.train_loader.dataset  # type: ignore[attr-defined]
        imgsz: int = self.cfg.imgsz  # type: ignore[attr-defined]
        letterbox = LetterBox((imgsz, imgsz))

        scored: dict[int, float] = {}
        device = next(model.parameters()).device

        for idx in range(len(dataset.labels)):
            label = dataset.labels[idx]
            bboxes = label.get("bboxes", np.zeros((0, 4)))
            n_gt = len(bboxes)

            if n_gt == 0:
                continue

            try:
                img = cv2.imread(dataset.im_files[idx])
                if img is None:
                    logger.debug("Skipping unreadable image idx=%d", idx)
                    continue

                img_resized = letterbox(image=img)
                tensor = (
                    torch.from_numpy(img_resized)
                    .permute(2, 0, 1)
                    .float()
                    .div_(255.0)
                    .unsqueeze_(0)
                    .to(device)
                )

                with torch.no_grad():
                    preds = model(tensor)

                results = non_max_suppression(preds, conf_thres=0.25, iou_thres=0.45, max_det=100)
                dets = results[0]
                n_det = len(dets)
                avg_conf = float(dets[:, 4].mean()) if n_det > 0 else 0.0

                scored[idx] = self.compute_difficulty(n_gt, n_det, avg_conf)

            except Exception:
                logger.debug(
                    "Inference error at idx=%d, assigning max difficulty", idx, exc_info=True
                )
                scored[idx] = 3.0

        return scored
