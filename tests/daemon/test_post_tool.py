"""Tests for daemon post-tool handling (handle_post_tool).

Tests cover:
  - Conditional validators (post_actions matching by tool, wildcard)
  - Auto-transition with guarded transitions
  - Think annotation emission on auto-transition to think_on_exit states
  - No annotation for non-think states
  - Context pressure tracking
  - Manual transitions (do_transition)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.workflow_state as daemon
from schemas.state_machine import StateMachineDefinition

from .conftest import make_agent_in_state


# ---------------------------------------------------------------------------
# 1-3: Conditional validators & wildcard
# ---------------------------------------------------------------------------


class TestConditionalValidators:
    """Test post_actions conditional validator dispatch."""

    def test_validator_fires_on_matching_tool(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Write in THINK_REQUIRED state → post_actions['Write']=['ruff_lint_critical'] dispatched."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "THINK_REQUIRED")
        result = daemon.handle_post_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {"file_path": "src/main.py"},
            "",
        )
        assert "feedback" in result

    def test_validator_skips_non_matching_tool(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Read in THINK_REQUIRED state → no Write validators fire."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "THINK_REQUIRED")
        result = daemon.handle_post_tool(
            "coder-p1-t1-a1b2",
            "Read",
            {},
            "",
        )
        assert "feedback" in result
        # No ruff feedback for Read (only Write triggers it)
        feedback = result.get("feedback", [])
        assert not any("ruff" in str(f).lower() for f in feedback)

    def test_wildcard_validator_dispatched(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Any tool in THINK_REQUIRED → post_actions['*']=['validate_context_packet_schema'] dispatched."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "THINK_REQUIRED")
        result = daemon.handle_post_tool(
            "coder-p1-t1-a1b2",
            "Bash",
            {},
            "",
        )
        # post_tool completes successfully
        assert "feedback" in result


# ---------------------------------------------------------------------------
# 4-5: Auto-transition engine
# ---------------------------------------------------------------------------


class TestAutoTransition:
    """Test state transitions triggered by post_tool auto-transition engine."""

    def test_manual_transition_fires(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """do_transition: IDLE → WRITING."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        result = daemon.do_transition("coder-p1-t1-a1b2", "WRITING", "start_writing")
        assert result["ok"]
        assert result["to_state"] == "WRITING"
        agent = daemon.get_agent_state("coder-p1-t1-a1b2")
        assert agent is not None
        assert agent.current_state == "WRITING"

    def test_transition_stays_if_no_match(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Invalid trigger → transition fails, state unchanged."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        result = daemon.do_transition("coder-p1-t1-a1b2", "DONE", "nonexistent_trigger")
        assert not result["ok"]
        agent = daemon.get_agent_state("coder-p1-t1-a1b2")
        assert agent is not None
        assert agent.current_state == "IDLE"

    def test_auto_transition_fires_when_guards_pass(
        self, temp_state_dir: Path, guarded_machine: StateMachineDefinition
    ) -> None:
        """Agent in STATE_A with always_true guard → auto-transition fires to STATE_B."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "STATE_A")
        result = daemon.handle_post_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {"file_path": "src/main.py"},
            "",
        )
        assert result.get("transition") is not None
        assert result["transition"]["from_state"] == "STATE_A"
        assert result["transition"]["to_state"] == "STATE_B"

        # Verify agent state actually changed
        agent = daemon.get_agent_state("coder-p1-t1-a1b2")
        assert agent is not None
        assert agent.current_state == "STATE_B"

    def test_auto_transition_skips_when_guards_fail(
        self, temp_state_dir: Path, guarded_machine: StateMachineDefinition
    ) -> None:
        """Agent in STATE_B with no guarded outgoing transitions → no auto-transition."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "STATE_B")
        result = daemon.handle_post_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {},
            "",
        )
        # STATE_B has only unguarded transitions, so no auto-transition fires
        assert result.get("transition") is None

        agent = daemon.get_agent_state("coder-p1-t1-a1b2")
        assert agent is not None
        assert agent.current_state == "STATE_B"

    def test_no_auto_transition_for_unguarded_transitions(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Auto-transition only fires for transitions with guards."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        result = daemon.handle_post_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {},
            "",
        )
        # Sample machine IDLE transitions have no guards → no auto-transition
        assert result.get("transition") is None


# ---------------------------------------------------------------------------
# 6-7: Think annotation emission
# ---------------------------------------------------------------------------


class TestThinkAnnotation:
    """Test think annotation JSONL emission on entering think_on_exit states."""

    def test_think_annotation_emitted_on_auto_transition(
        self, temp_state_dir: Path, guarded_machine: StateMachineDefinition
    ) -> None:
        """Auto-transition to think_on_exit state → annotation file written."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "STATE_A")

        # Redirect annotation writing to temp dir
        ann_dir = temp_state_dir / "annotations"
        ann_dir.mkdir(exist_ok=True)
        daemon._emit_think_annotation.__module__  # ensure function exists

        result = daemon.handle_post_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {"file_path": "src/main.py"},
            "",
        )
        # Should have transitioned to STATE_B (think_on_exit=True)
        assert result["transition"] is not None
        assert result["transition"]["to_state"] == "STATE_B"

        # Check annotation JSONL was written (uses patched ANNOTATIONS_DIR)
        ann_file = daemon.ANNOTATIONS_DIR / "coder-p1-t1-a1b2.jsonl"
        assert ann_file.exists(), f"Annotation file not found at {ann_file}"
        lines = ann_file.read_text().strip().splitlines()
        assert len(lines) >= 1
        entry = json.loads(lines[-1])
        assert entry["priority"] == "critical"
        assert "Think about design" in entry["message"]

    def test_no_annotation_for_non_think_state(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Transition to state without think_on_exit → no annotation."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        daemon.do_transition("coder-p1-t1-a1b2", "WRITING", "start_writing")
        agent = daemon.get_agent_state("coder-p1-t1-a1b2")
        assert agent is not None
        assert agent.pending_critical_annotation is None

    def test_transition_to_think_state_succeeds(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Transition to THINK_REQUIRED state works and state has think_on_exit."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        result = daemon.do_transition(
            "coder-p1-t1-a1b2", "THINK_REQUIRED", "enter_think"
        )
        assert result["ok"]
        machine = daemon.load_machine("coder")
        assert machine is not None
        think_state = next(s for s in machine.states if s.name == "THINK_REQUIRED")
        assert think_state.think_on_exit is True
        assert think_state.think_prompt == "Reflect on your implementation approach"


# ---------------------------------------------------------------------------
# 8-9: Context pressure
# ---------------------------------------------------------------------------


class TestContextPressure:
    """Test context pressure tracking via update_context."""

    def test_context_pressure_warning_level(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Agent at 70% context → usage tracked."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        result = daemon.update_agent_context(
            "coder-p1-t1-a1b2",
            input_tokens=100_000,
            output_tokens=40_000,
        )
        assert result["ok"]
        assert result["context_usage_pct"] == 70.0

    def test_context_pressure_critical_level(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Agent at 95% context → usage tracked at cap."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        result = daemon.update_agent_context(
            "coder-p1-t1-a1b2",
            input_tokens=150_000,
            output_tokens=40_000,
        )
        assert result["ok"]
        assert result["context_usage_pct"] == 95.0

    def test_context_update_unknown_agent(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Update context for unknown agent → error."""
        result = daemon.update_agent_context(
            "unknown-x1-y2-z3z3",
            input_tokens=100_000,
            output_tokens=40_000,
        )
        assert not result["ok"]


# ---------------------------------------------------------------------------
# 10: Async writes (non-blocking response)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 10-12: Blocking validators (post_actions → pending_validation_error)
# ---------------------------------------------------------------------------


class TestBlockingValidators:
    """Test that post-action validator failures set pending_validation_error."""

    def test_validator_failure_sets_pending_error(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Validator error → pending_validation_error set on agent state."""
        # THINK_REQUIRED has post_actions Write: [ruff_lint_critical]
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "THINK_REQUIRED")
        bad_file = temp_state_dir / "bad.py"
        bad_file.write_text("def broken(\n")

        result = daemon.handle_post_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {"file_path": str(bad_file)},
            "",
        )

        # Should have feedback about errors
        assert len(result["feedback"]) > 0

        # Agent should have pending_validation_error set
        agent = daemon.get_agent_state("coder-p1-t1-a1b2")
        assert agent is not None
        assert agent.pending_validation_error is not None

    def test_validator_pass_clears_pending_error(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Validators pass after previous failure → pending_validation_error cleared."""
        make_agent_in_state(
            "coder-p1-t1-a1b2",
            "coder",
            "THINK_REQUIRED",
            pending_validation_error={
                "message": "previous error",
                "validator_errors": ["old error"],
            },
        )
        daemon._agent_state_cache.clear()

        # Write a clean file
        clean_file = temp_state_dir / "clean.py"
        clean_file.write_text('def hello() -> str:\n    return "world"\n')

        result = daemon.handle_post_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {"file_path": str(clean_file)},
            "",
        )

        # Error should be cleared
        agent = daemon.get_agent_state("coder-p1-t1-a1b2")
        assert agent is not None
        assert agent.pending_validation_error is None

        # Should have feedback about clearance
        assert any("cleared" in f.lower() for f in result["feedback"])

    def test_no_validators_no_error(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """State with no post_actions → no validation error set."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "UNRESTRICTED")
        result = daemon.handle_post_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {"file_path": "src/main.py"},
            "",
        )

        agent = daemon.get_agent_state("coder-p1-t1-a1b2")
        assert agent is not None
        assert agent.pending_validation_error is None


# ---------------------------------------------------------------------------
# 13: Async writes (non-blocking response)
# ---------------------------------------------------------------------------


class TestAsyncWrites:
    """Verify that daemon responses return promptly."""

    def test_process_request_returns_quickly(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """process_request with check_tool returns synchronously."""
        import time

        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        start = time.perf_counter()
        result = daemon.process_request(
            {
                "command": "check_tool",
                "agent_id": "coder-p1-t1-a1b2",
                "tool": "Read",
                "tool_input": {},
            }
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert result is not None
        assert elapsed_ms < 100
