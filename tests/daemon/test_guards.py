"""Tests for guard evaluation engine."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.workflow_state as daemon
import scripts.daemon.state_manager as _sm
import scripts.daemon.guards.mechanical as _mech


class TestEvaluateGuards:
    """Tests for evaluate_guards function."""

    def test_empty_guards_pass(self, temp_state_dir: Path):
        passed, results, reason = daemon.evaluate_guards([], "test-agent")
        assert passed is True
        assert results == {}

    def test_unregistered_guard_fails_strict(self, temp_state_dir: Path):
        """Unknown guard name → False (strict mode)."""
        passed, results, _ = daemon.evaluate_guards(
            ["totally_unknown_guard"], "test-agent"
        )
        assert passed is False
        assert results["totally_unknown_guard"] is False

    def test_guard_exception_fails_strict(self, temp_state_dir: Path):
        def bad_guard(_agent_id, _agent, _system, _param=None):
            raise RuntimeError("boom")

        daemon._GUARD_REGISTRY["bad_guard_test"] = bad_guard
        try:
            passed, results, _ = daemon.evaluate_guards(
                ["bad_guard_test"], "test-agent"
            )
            assert passed is False
            assert results["bad_guard_test"] is False
        finally:
            del daemon._GUARD_REGISTRY["bad_guard_test"]

    def test_guard_failure_blocks(self, temp_state_dir: Path):
        def failing_guard(_a, _b, _c, _d=None):
            return False

        daemon._GUARD_REGISTRY["failing_guard_test"] = failing_guard
        try:
            passed, _, reason = daemon.evaluate_guards(
                ["failing_guard_test"], "test-agent"
            )
            assert passed is False
            assert "failing_guard_test" in reason
        finally:
            del daemon._GUARD_REGISTRY["failing_guard_test"]

    def test_mixed_guards(self, temp_state_dir: Path):
        daemon._GUARD_REGISTRY["pass_test"] = lambda _a, _b, _c, _d=None: True
        daemon._GUARD_REGISTRY["fail_test"] = lambda _a, _b, _c, _d=None: False
        try:
            passed, results, _ = daemon.evaluate_guards(
                ["pass_test", "fail_test"], "test-agent"
            )
            assert passed is False
            assert results["pass_test"] is True
            assert results["fail_test"] is False
        finally:
            del daemon._GUARD_REGISTRY["pass_test"]
            del daemon._GUARD_REGISTRY["fail_test"]


class TestConcreteGuards:
    """Tests for specific guard implementations."""

    def test_context_packets_exist_false(self, temp_state_dir: Path, tmp_path: Path):
        with patch.object(_mech, "_project_dir", return_value=tmp_path):
            result = _mech._guard_context_packets_exist("agent", None, None)
            assert result is False

    def test_context_packets_exist_true(self, temp_state_dir: Path, tmp_path: Path):
        ctx_dir = tmp_path / ".claude" / "context"
        ctx_dir.mkdir(parents=True)
        (ctx_dir / "_codebase.json").write_text("{}")
        with patch.object(_mech, "_project_dir", return_value=tmp_path):
            result = _mech._guard_context_packets_exist("agent", None, None)
            assert result is True

    def test_pytest_exit_zero_no_gate(self, temp_state_dir: Path, tmp_path: Path):
        with patch.object(_mech, "_get_gate_result", return_value=None):
            result = _mech._guard_pytest_exit_zero("agent", None, None)
            assert result is False

    def test_pytest_exit_zero_pass(self, temp_state_dir: Path, tmp_path: Path):
        gate = {"tests": "pass", "format": "pass", "lint": "pass", "typecheck": "pass"}
        with patch.object(_mech, "_get_gate_result", return_value=gate):
            result = _mech._guard_pytest_exit_zero("agent", None, None)
            assert result is True

    def test_test_files_exist_no_tests_dir(self, temp_state_dir: Path, tmp_path: Path):
        with patch.object(_mech, "_project_dir", return_value=tmp_path):
            result = _mech._guard_test_files_exist("agent", {}, None)
            assert result is False

    def test_test_files_exist_with_files(self, temp_state_dir: Path, tmp_path: Path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_something.py").write_text("def test_x(): pass")
        with patch.object(_mech, "_project_dir", return_value=tmp_path):
            result = _mech._guard_test_files_exist("agent", {"task": ""}, None)
            assert result is True
