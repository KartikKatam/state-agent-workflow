"""Tests for daemon validator infrastructure.

The validator registry (ruff, schema) is planned for handle_post_tool but
not yet implemented. Tests here cover:
  - State machine schema validation (Pydantic models)
  - Guard evaluation engine (registered, unregistered, failing guards)
  - Path glob matching
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import scripts.workflow_state as daemon
from schemas.state_machine import StateDefinition, StateMachineDefinition


# ---------------------------------------------------------------------------
# 1-3: Ruff validator (via VALIDATOR_REGISTRY["ruff_lint_critical"])
# ---------------------------------------------------------------------------


class TestRuffValidator:
    """Test ruff validator integration via the validator registry."""

    def test_ruff_clean_file_no_feedback(self, tmp_path: Path) -> None:
        """Clean Python file → ruff returns no feedback."""
        clean_file = tmp_path / "clean.py"
        clean_file.write_text('def hello() -> str:\n    return "world"\n')
        validator = daemon.VALIDATOR_REGISTRY["ruff_lint_critical"]
        feedback = validator(str(clean_file), {}, "")
        assert feedback == []

    def test_ruff_syntax_error_returns_feedback(self, tmp_path: Path) -> None:
        """Syntax error file → ruff returns error feedback."""
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("def broken(\n")
        validator = daemon.VALIDATOR_REGISTRY["ruff_lint_critical"]
        feedback = validator(str(bad_file), {}, "")
        assert len(feedback) > 0
        assert any("invalid-syntax" in f for f in feedback)

    def test_ruff_timeout_graceful(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ruff timeout → graceful handling (no crash)."""
        py_file = tmp_path / "slow.py"
        py_file.write_text("x = 1\n")

        def _raise_timeout(*_args: object, **_kwargs: object) -> None:
            raise subprocess.TimeoutExpired("ruff", 5)

        monkeypatch.setattr(subprocess, "run", _raise_timeout)
        validator = daemon.VALIDATOR_REGISTRY["ruff_lint_critical"]
        feedback = validator(str(py_file), {}, "")
        assert feedback == []


# ---------------------------------------------------------------------------
# 4-5: Schema validator (via VALIDATOR_REGISTRY["validate_*_schema"])
# ---------------------------------------------------------------------------


class TestSchemaValidator:
    """Test schema validation integration via the validator registry."""

    def test_schema_valid_file_no_feedback(self, tmp_path: Path) -> None:
        """Valid JSON in .claude/ path → no feedback."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        valid_file = claude_dir / "test.json"
        valid_file.write_text('{"key": "value"}')
        validator = daemon.VALIDATOR_REGISTRY["validate_context_packet_schema"]
        feedback = validator(str(valid_file), {}, "")
        assert feedback == []

    def test_schema_invalid_file_returns_errors(self, tmp_path: Path) -> None:
        """Invalid JSON in .claude/ path → feedback with validation errors."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        invalid_file = claude_dir / "bad.json"
        invalid_file.write_text("{invalid json")
        validator = daemon.VALIDATOR_REGISTRY["validate_context_packet_schema"]
        feedback = validator(str(invalid_file), {}, "")
        assert len(feedback) > 0
        assert any("Schema validation" in f for f in feedback)


# ---------------------------------------------------------------------------
# 6: Unknown validator
# ---------------------------------------------------------------------------


class TestUnknownValidator:
    """Test behavior with unregistered validators."""

    def test_unknown_validator_warns_no_crash(self, tmp_path: Path) -> None:
        """Unknown validator name → registry returns None, no crash."""
        vfn = daemon.VALIDATOR_REGISTRY.get("nonexistent_validator_xyz")
        assert vfn is None


# ---------------------------------------------------------------------------
# Guard evaluation engine (IMPLEMENTED — test thoroughly)
# ---------------------------------------------------------------------------


class TestGuardEvaluation:
    """Test the guard evaluation engine in workflow_state.py."""

    def test_empty_guards_pass(self, temp_state_dir: Path) -> None:
        """Empty guard list → all pass."""
        passed, results, reason = daemon.evaluate_guards([], "any-agent-x1-a1b2")
        assert passed
        assert results == {}
        assert reason == ""

    def test_unregistered_guard_fails_strict(self, temp_state_dir: Path) -> None:
        """Unknown guard name → False (strict mode)."""
        passed, results, reason = daemon.evaluate_guards(
            ["nonexistent_guard"], "any-agent-x1-a1b2"
        )
        assert not passed
        assert results["nonexistent_guard"] is False

    def test_registered_guard_passes(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Register a guard that returns True → passes."""

        def always_true(
            _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
        ) -> bool:
            return True

        monkeypatch.setitem(daemon._GUARD_REGISTRY, "always_true", always_true)
        passed, results, reason = daemon.evaluate_guards(
            ["always_true"], "any-agent-x1-a1b2"
        )
        assert passed
        assert results["always_true"] is True

    def test_registered_guard_fails(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Register a guard that returns False → fails."""

        def always_false(
            _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
        ) -> bool:
            return False

        monkeypatch.setitem(daemon._GUARD_REGISTRY, "always_false", always_false)
        passed, results, reason = daemon.evaluate_guards(
            ["always_false"], "any-agent-x1-a1b2"
        )
        assert not passed
        assert results["always_false"] is False
        assert "always_false" in reason

    def test_guard_exception_fails_strict(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guard that raises → False (strict mode)."""

        def exploding_guard(
            _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
        ) -> bool:
            msg = "boom"
            raise RuntimeError(msg)

        monkeypatch.setitem(daemon._GUARD_REGISTRY, "exploding", exploding_guard)
        passed, results, reason = daemon.evaluate_guards(
            ["exploding"], "any-agent-x1-a1b2"
        )
        assert not passed
        assert results["exploding"] is False

    def test_mixed_guards_one_fails(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Multiple guards, one fails → overall fails."""

        def ok_guard(
            _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
        ) -> bool:
            return True

        def fail_guard(
            _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
        ) -> bool:
            return False

        monkeypatch.setitem(daemon._GUARD_REGISTRY, "ok_guard", ok_guard)
        monkeypatch.setitem(daemon._GUARD_REGISTRY, "fail_guard", fail_guard)
        passed, results, reason = daemon.evaluate_guards(
            ["ok_guard", "fail_guard"], "any-agent-x1-a1b2"
        )
        assert not passed
        assert results["ok_guard"] is True
        assert results["fail_guard"] is False


# ---------------------------------------------------------------------------
# Path glob matching (IMPLEMENTED — test thoroughly)
# ---------------------------------------------------------------------------


class TestPathMatchesGlob:
    """Test the path_matches_glob utility function."""

    def test_simple_glob_match(self) -> None:
        """src/*.py matches src/main.py."""
        assert daemon.path_matches_glob("src/main.py", "src/*.py")

    def test_simple_glob_no_match(self) -> None:
        """src/*.py does NOT match src/sub/main.py (single * doesn't cross dirs)."""
        assert not daemon.path_matches_glob("src/sub/main.py", "src/*.py")

    def test_recursive_glob_match(self) -> None:
        """src/**/*.py matches src/deep/nested/file.py."""
        assert daemon.path_matches_glob("src/deep/nested/file.py", "src/**/*.py")

    def test_recursive_glob_top_level(self) -> None:
        """src/**/*.py matches src/main.py (** can match zero segments)."""
        assert daemon.path_matches_glob("src/main.py", "src/**/*.py")

    def test_leading_dot_slash_stripped(self) -> None:
        """Leading ./ is stripped from both path and pattern."""
        assert daemon.path_matches_glob("./src/main.py", "./src/**/*.py")

    def test_leading_slash_stripped(self) -> None:
        """Leading / is stripped from path."""
        assert daemon.path_matches_glob("/src/main.py", "src/**/*.py")

    def test_tests_glob(self) -> None:
        """tests/**/*.py matches tests/daemon/test_foo.py."""
        assert daemon.path_matches_glob("tests/daemon/test_foo.py", "tests/**/*.py")

    def test_conftest_glob(self) -> None:
        """**/conftest.py matches tests/conftest.py."""
        assert daemon.path_matches_glob("tests/conftest.py", "**/conftest.py")

    def test_no_match_different_extension(self) -> None:
        """src/**/*.py does not match src/main.js."""
        assert not daemon.path_matches_glob("src/main.js", "src/**/*.py")


# ---------------------------------------------------------------------------
# State machine schema validation (Pydantic models)
# ---------------------------------------------------------------------------


class TestStateMachineSchema:
    """Test that Pydantic models correctly validate state machine definitions."""

    def test_valid_machine_parses(self) -> None:
        """Valid JSON → StateMachineDefinition."""
        from tests.daemon.conftest import SAMPLE_MACHINE_DICT

        machine = StateMachineDefinition.model_validate(SAMPLE_MACHINE_DICT)
        assert machine.name == "test-coder"
        assert machine.initial_state == "IDLE"
        assert len(machine.states) == 6

    def test_invalid_initial_state_rejected(self) -> None:
        """initial_state referencing nonexistent state → error."""
        from tests.daemon.conftest import SAMPLE_MACHINE_DICT

        bad = {**SAMPLE_MACHINE_DICT, "initial_state": "NONEXISTENT"}
        with pytest.raises(ValueError, match="initial_state"):
            StateMachineDefinition.model_validate(bad)

    def test_invalid_terminal_state_rejected(self) -> None:
        """terminal_states referencing nonexistent state → error."""
        from tests.daemon.conftest import SAMPLE_MACHINE_DICT

        bad = {**SAMPLE_MACHINE_DICT, "terminal_states": ["NONEXISTENT"]}
        with pytest.raises(ValueError, match="terminal_state"):
            StateMachineDefinition.model_validate(bad)

    def test_state_definition_think_fields(self) -> None:
        """StateDefinition correctly captures think_on_exit and think_prompt."""
        state = StateDefinition(
            name="THINK",
            description="Must think",
            think_on_exit=True,
            think_prompt="Consider the design",
            post_actions={"Write": ["ruff"]},
        )
        assert state.think_on_exit is True
        assert state.think_prompt == "Consider the design"
        assert state.post_actions == {"Write": ["ruff"]}

    def test_state_definition_defaults(self) -> None:
        """StateDefinition defaults are sensible."""
        state = StateDefinition(name="X", description="desc")
        assert state.write_allowed is True
        assert state.write_globs is None
        assert state.blocked_tools == []
        assert state.think_on_exit is False
        assert state.think_prompt is None
        assert state.post_actions == {}
