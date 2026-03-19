"""Checkpoint download, validation, baseline evaluation, and data.yaml generation."""

from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path

from huggingface_hub import hf_hub_download
from ultralytics import YOLO  # pyright: ignore[reportPrivateImportUsage]

logger = logging.getLogger(__name__)

# --- Constants ---
REPO_ID = "morsetechlab/yolov11-license-plate-detection"
FILENAME = "license-plate-finetune-v1m.pt"
LOCAL_PATH = Path("models/yolov11m-lpr-base.pt")


def download_checkpoint() -> Path:
    """Download the LPR checkpoint from HuggingFace Hub.

    Returns the local path to the downloaded checkpoint.
    """
    if LOCAL_PATH.exists():
        logger.info("Checkpoint already exists at %s", LOCAL_PATH)
        return LOCAL_PATH

    LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Downloading checkpoint from %s/%s", REPO_ID, FILENAME)
    cached_path = hf_hub_download(
        repo_id=REPO_ID,
        filename=FILENAME,
        local_dir=str(LOCAL_PATH.parent),
    )

    # Move to canonical name if needed
    cached = Path(cached_path)
    if cached.name != LOCAL_PATH.name:
        shutil.move(str(cached), str(LOCAL_PATH))
        logger.info("Renamed %s -> %s", cached.name, LOCAL_PATH.name)

    logger.info("Checkpoint saved to %s", LOCAL_PATH)
    return LOCAL_PATH


def validate_checkpoint(path: Path) -> dict:
    """Validate that the checkpoint is a single-class LPR model.

    Returns model info dict with class names.
    Raises AssertionError if validation fails.
    """
    model = YOLO(str(path))
    names = model.names

    assert len(names) == 1, f"Expected 1 class, got {len(names)}: {names}"

    class_name = names[0].lower()
    assert "license" in class_name or "plate" in class_name, (
        f"Expected class name containing 'license' or 'plate', got '{names[0]}'"
    )

    logger.info("Checkpoint validated: %d class(es) — %s", len(names), names)
    return {"names": names, "nc": len(names)}


def run_baseline_eval(checkpoint_path: Path, data_yaml: str) -> dict:
    """Run baseline evaluation on the checkpoint before fine-tuning.

    Returns metrics dict with baseline_mAP50, baseline_mAP50_95, etc.
    """
    model = YOLO(str(checkpoint_path))
    results = model.val(data=data_yaml)

    metrics = {
        "baseline_mAP50": float(results.box.map50),
        "baseline_mAP50_95": float(results.box.map),
        "baseline_precision": float(results.box.mp),
        "baseline_recall": float(results.box.mr),
    }

    logger.info(
        "Baseline: mAP50=%.3f mAP50-95=%.3f P=%.3f R=%.3f",
        metrics["baseline_mAP50"],
        metrics["baseline_mAP50_95"],
        metrics["baseline_precision"],
        metrics["baseline_recall"],
    )
    return metrics


def generate_data_yaml(project_root: Path | None = None) -> Path:
    """Generate data.yaml with absolute paths for the current machine.

    Writes to training/yolo/data.yaml with absolute paths derived
    from project_root.

    Returns the path to the generated file.
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent

    dataset_dir = project_root / "data" / "roboflow_dataset"
    output_path = project_root / "training" / "yolo" / "data.yaml"

    content = (
        "# YOLO LPR dataset configuration (auto-generated with absolute paths)\n"
        f"train: {dataset_dir / 'train' / 'images'}\n"
        f"val: {dataset_dir / 'valid' / 'images'}\n"
        f"test: {dataset_dir / 'test' / 'images'}\n"
        "\n"
        "nc: 1\n"
        "names:\n"
        "  0: License_Plate\n"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)
    logger.info("Generated data.yaml at %s", output_path)
    return output_path


def main() -> None:
    """CLI entrypoint for setup_checkpoint."""
    parser = argparse.ArgumentParser(description="YOLO LPR checkpoint setup")
    parser.add_argument("--eval", action="store_true", help="Run baseline evaluation")
    parser.add_argument(
        "--data", type=str, default="training/yolo/data.yaml", help="data.yaml path"
    )
    parser.add_argument(
        "--generate-yaml", action="store_true", help="Generate data.yaml with absolute paths"
    )
    args = parser.parse_args()

    if args.generate_yaml:
        generate_data_yaml()
        return

    path = download_checkpoint()
    validate_checkpoint(path)

    if args.eval:
        run_baseline_eval(path, args.data)


if __name__ == "__main__":
    main()
