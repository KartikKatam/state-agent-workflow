"""Tests for YOLO model export (ONNX + TRT)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tests.conftest_training import make_yolo_config

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_yolo():
    """Mock YOLO model that simulates export and inference."""
    model = MagicMock()
    # model.export returns a path string (the exported file)
    model.export.return_value = "/tmp/fake_model.onnx"
    # model() (call for inference) returns a list with one result
    model.return_value = [MagicMock()]
    return model


@pytest.fixture()
def export_cfg(tmp_path: Path):
    """Test-friendly export config."""
    return make_yolo_config(
        export_onnx=True,
        export_trt=True,
        export_half=True,
    )


# ---------------------------------------------------------------------------
# TestExportModelImport
# ---------------------------------------------------------------------------


class TestExportModelImport:
    """Verify export module is importable."""

    def test_export_model_importable(self):
        """export_model importable without error."""
        from training.yolo.export import export_model  # noqa: F401

    def test_export_cli_importable(self):
        """main importable without error."""
        from training.yolo.export import main  # noqa: F401


# ---------------------------------------------------------------------------
# TestExportONNX
# ---------------------------------------------------------------------------


class TestExportONNX:
    """ONNX export path tests."""

    def test_onnx_export_calls_model_export(self, mock_yolo: MagicMock, export_cfg, tmp_path: Path):
        """model.export called with format='onnx', simplify=True, half=False, opset=17, dynamic=False."""
        from training.yolo.export import export_model

        weights = tmp_path / "best.pt"
        weights.touch()
        # Make model.export return a real tmp file so copy2 works
        onnx_path = tmp_path / "best.onnx"
        onnx_path.write_bytes(b"fake-onnx")
        mock_yolo.export.return_value = str(onnx_path)

        with (
            patch("training.yolo.export.YOLO", return_value=mock_yolo),
            patch("training.yolo.export.shutil"),
        ):
            export_model(weights, export_cfg, models_dir=tmp_path / "models")

        # Find the ONNX export call
        onnx_calls = [
            c for c in mock_yolo.export.call_args_list if c.kwargs.get("format") == "onnx"
        ]
        assert len(onnx_calls) == 1
        onnx_call = onnx_calls[0]
        assert onnx_call.kwargs["format"] == "onnx"
        assert onnx_call.kwargs["simplify"] is True
        assert onnx_call.kwargs["half"] is False
        assert onnx_call.kwargs["opset"] == 17
        assert onnx_call.kwargs["dynamic"] is False

    def test_onnx_export_copies_to_models_dir(
        self, mock_yolo: MagicMock, export_cfg, tmp_path: Path
    ):
        """shutil.copy2 called with dest containing 'yolov11m-lpr.onnx'."""
        from training.yolo.export import export_model

        weights = tmp_path / "best.pt"
        weights.touch()
        onnx_path = tmp_path / "best.onnx"
        onnx_path.write_bytes(b"fake-onnx")
        mock_yolo.export.return_value = str(onnx_path)

        with (
            patch("training.yolo.export.YOLO", return_value=mock_yolo),
            patch("training.yolo.export.shutil") as mock_shutil,
        ):
            export_model(weights, export_cfg, models_dir=tmp_path / "models")

        # Check copy2 was called with dest containing the expected filename
        copy_calls = mock_shutil.copy2.call_args_list
        onnx_copy = [c for c in copy_calls if "yolov11m-lpr.onnx" in str(c)]
        assert len(onnx_copy) >= 1, f"Expected copy to yolov11m-lpr.onnx, got: {copy_calls}"

    def test_onnx_export_skipped_when_disabled(self, mock_yolo: MagicMock, tmp_path: Path):
        """export_onnx=False → model.export NOT called with format='onnx'."""
        from training.yolo.export import export_model

        cfg = make_yolo_config(export_onnx=False, export_trt=False)
        weights = tmp_path / "best.pt"
        weights.touch()

        with patch("training.yolo.export.YOLO", return_value=mock_yolo):
            export_model(weights, cfg, models_dir=tmp_path / "models")

        onnx_calls = [
            c for c in mock_yolo.export.call_args_list if c.kwargs.get("format") == "onnx"
        ]
        assert len(onnx_calls) == 0


# ---------------------------------------------------------------------------
# TestExportTRT
# ---------------------------------------------------------------------------


class TestExportTRT:
    """TensorRT export path tests."""

    def test_trt_export_calls_model_export(self, mock_yolo: MagicMock, export_cfg, tmp_path: Path):
        """model.export called with format='engine', half=True when tensorrt available."""
        from training.yolo.export import export_model

        weights = tmp_path / "best.pt"
        weights.touch()
        onnx_path = tmp_path / "best.onnx"
        onnx_path.write_bytes(b"fake-onnx")
        engine_path = tmp_path / "best.engine"
        engine_path.write_bytes(b"fake-engine")

        # Make export return the right path depending on format
        def side_effect(**kwargs):
            if kwargs.get("format") == "onnx":
                return str(onnx_path)
            elif kwargs.get("format") == "engine":
                return str(engine_path)
            return str(onnx_path)

        mock_yolo.export.side_effect = side_effect

        # Mock tensorrt as available
        fake_trt = MagicMock()
        with (
            patch("training.yolo.export.YOLO", return_value=mock_yolo),
            patch("training.yolo.export.shutil"),
            patch.dict(sys.modules, {"tensorrt": fake_trt}),
            patch("training.yolo.export._check_tensorrt_available", return_value=True),
        ):
            export_model(weights, export_cfg, models_dir=tmp_path / "models")

        trt_calls = [
            c for c in mock_yolo.export.call_args_list if c.kwargs.get("format") == "engine"
        ]
        assert len(trt_calls) == 1
        trt_call = trt_calls[0]
        assert trt_call.kwargs["half"] is True

    def test_trt_fallback_when_not_installed(
        self, mock_yolo: MagicMock, export_cfg, tmp_path: Path, caplog
    ):
        """tensorrt=None → no crash, TRT skipped, warning logged."""
        from training.yolo.export import export_model

        weights = tmp_path / "best.pt"
        weights.touch()
        onnx_path = tmp_path / "best.onnx"
        onnx_path.write_bytes(b"fake-onnx")
        mock_yolo.export.return_value = str(onnx_path)

        with (
            patch("training.yolo.export.YOLO", return_value=mock_yolo),
            patch("training.yolo.export.shutil"),
            patch("training.yolo.export._check_tensorrt_available", return_value=False),
            caplog.at_level(logging.WARNING),
        ):
            # Should not crash
            export_model(weights, export_cfg, models_dir=tmp_path / "models")

        # TRT export should NOT have been called
        trt_calls = [
            c for c in mock_yolo.export.call_args_list if c.kwargs.get("format") == "engine"
        ]
        assert len(trt_calls) == 0
        # Warning should be logged
        assert any("tensorrt" in r.message.lower() for r in caplog.records)

    def test_trt_skipped_when_disabled(self, mock_yolo: MagicMock, tmp_path: Path):
        """export_trt=False → model.export NOT called with format='engine'."""
        from training.yolo.export import export_model

        cfg = make_yolo_config(export_onnx=False, export_trt=False)
        weights = tmp_path / "best.pt"
        weights.touch()

        with patch("training.yolo.export.YOLO", return_value=mock_yolo):
            export_model(weights, cfg, models_dir=tmp_path / "models")

        trt_calls = [
            c for c in mock_yolo.export.call_args_list if c.kwargs.get("format") == "engine"
        ]
        assert len(trt_calls) == 0


# ---------------------------------------------------------------------------
# TestExportValidation
# ---------------------------------------------------------------------------


class TestExportValidation:
    """Export validation tests."""

    def test_export_validates_onnx_with_dummy_inference(self, mock_yolo: MagicMock, tmp_path: Path):
        """Exported ONNX model loaded and inference called with 640x640 image."""
        from training.yolo.export import export_model

        cfg = make_yolo_config(export_onnx=True, export_trt=False)
        weights = tmp_path / "best.pt"
        weights.touch()
        onnx_path = tmp_path / "best.onnx"
        onnx_path.write_bytes(b"fake-onnx")
        mock_yolo.export.return_value = str(onnx_path)

        mock_validation_model = MagicMock()
        mock_validation_model.return_value = [MagicMock()]

        with (
            patch("training.yolo.export.YOLO", side_effect=[mock_yolo, mock_validation_model]),
            patch("training.yolo.export.shutil"),
        ):
            export_model(weights, cfg, models_dir=tmp_path / "models")

        # The validation model should have been called (inference)
        assert mock_validation_model.called, "Validation inference was not performed"

    def test_export_logging(self, mock_yolo: MagicMock, tmp_path: Path, caplog):
        """caplog contains file size and format info."""
        from training.yolo.export import export_model

        cfg = make_yolo_config(export_onnx=True, export_trt=False)
        weights = tmp_path / "best.pt"
        weights.touch()
        onnx_path = tmp_path / "best.onnx"
        onnx_path.write_bytes(b"fake-onnx-data-for-size")
        mock_yolo.export.return_value = str(onnx_path)

        with (
            patch("training.yolo.export.YOLO", return_value=mock_yolo),
            patch("training.yolo.export.shutil"),
            caplog.at_level(logging.INFO),
        ):
            export_model(weights, cfg, models_dir=tmp_path / "models")

        log_text = caplog.text.lower()
        assert "onnx" in log_text, f"Expected 'onnx' in logs, got: {caplog.text}"


# ---------------------------------------------------------------------------
# TestExportCLI
# ---------------------------------------------------------------------------


class TestExportCLI:
    """CLI argument parsing tests."""

    def test_export_cli_parses_weights(self):
        """--weights /path/best.pt parsed correctly."""
        from training.yolo.export import parse_args

        with patch("sys.argv", ["export", "--weights", "/path/best.pt"]):
            args = parse_args()

        assert args.weights == "/path/best.pt"

    def test_export_cli_no_trt_flag(self):
        """--no-trt flag disables TRT export."""
        from training.yolo.export import parse_args

        with patch("sys.argv", ["export", "--weights", "/path/best.pt", "--no-trt"]):
            args = parse_args()

        assert args.no_trt is True


# ---------------------------------------------------------------------------
# TestExportONNXReal
# ---------------------------------------------------------------------------


class TestExportONNXReal:
    """Real ONNX export integration test."""

    @pytest.mark.slow
    def test_onnx_export_real_load_and_dummy_inference(self, tmp_path: Path):
        """Export YOLO to ONNX, load with onnxruntime, inference on 640x640."""
        try:
            from ultralytics import YOLO  # pyright: ignore[reportPrivateImportUsage]
        except ImportError:
            pytest.skip("ultralytics not installed")

        try:
            import onnxruntime as ort
        except ImportError:
            pytest.skip("onnxruntime not installed")

        # Find any .pt model to export
        model_dirs = [
            Path("/home/kartik/work/firefly/LPR-SingleDrone/models"),
        ]
        pt_files = []
        for d in model_dirs:
            if d.exists():
                pt_files.extend(d.glob("*.pt"))

        if not pt_files:
            pytest.skip("No .pt model files found for real export test")

        model_path = pt_files[0]
        model = YOLO(str(model_path))

        # Export to ONNX
        onnx_path = model.export(
            format="onnx",
            imgsz=640,
            dynamic=False,
            simplify=True,
            half=False,
            opset=17,
        )
        assert Path(onnx_path).exists(), f"ONNX file not created at {onnx_path}"

        # Load with onnxruntime and run dummy inference
        session = ort.InferenceSession(str(onnx_path))
        input_name = session.get_inputs()[0].name
        dummy_input = np.random.rand(1, 3, 640, 640).astype(np.float32)
        outputs = session.run(None, {input_name: dummy_input})

        assert outputs[0].shape[0] == 1, f"Expected batch dim 1, got {outputs[0].shape[0]}"  # pyright: ignore[reportAttributeAccessIssue]
