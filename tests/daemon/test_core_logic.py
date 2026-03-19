"""Tests for daemon core logic: register_agent, process_request, update_context, transitions."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.workflow_state as daemon
from schemas.state_machine import StateMachineDefinition


class TestRegisterAgent:
    """Tests for register_agent function."""

    def test_register_creates_state_file(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ):
        result = daemon.register_agent("coder-p1-t1-a1b2", "coder", "opus-4-6")
        assert result["ok"] is True
        assert result["agent"]["role"] == "coder"
        # Uses the sample machine's initial_state (IDLE)
        assert result["agent"]["current_state"] == "IDLE"

    def test_register_with_optional_fields(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ):
        result = daemon.register_agent(
            "coder-p1-t1-a1b2",
            "coder",
            "opus-4-6",
            phase="p1",
            task="t1",
            parent_agent_id="orchestrator-p1-init-f1e2",
            worktree="/tmp/wt-test",
            base_branch="feature/test",
        )
        assert result["ok"] is True
        assert result["agent"]["phase"] == "p1"
        assert result["agent"]["task"] == "t1"
        assert result["agent"]["worktree"] == "/tmp/wt-test"

    def test_register_generalist_no_machine(self, temp_state_dir: Path):
        result = daemon.register_agent(
            "generalist-p1-t1-a1b2", "generalist", "haiku-4-5"
        )
        assert result["ok"] is True
        assert result["agent"]["current_state"] == "SPAWNED"


class TestProcessRequest:
    """Tests for process_request dispatch."""

    def test_unknown_command(self, temp_state_dir: Path):
        result = daemon.process_request({"command": "bogus"})
        assert result["ok"] is False
        assert "Unknown command" in result["reason"]

    def test_register_via_process_request(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ):
        result = daemon.process_request(
            {
                "command": "register",
                "agent_id": "coder-p1-t1-a1b2",
                "role": "coder",
                "model": "opus-4-6",
            }
        )
        assert result["ok"] is True

    def test_check_tool_via_process_request(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ):
        daemon.register_agent("coder-p1-t1-a1b2", "coder", "opus-4-6")
        result = daemon.process_request(
            {
                "command": "check_tool",
                "agent_id": "coder-p1-t1-a1b2",
                "tool": "Read",
                "tool_input": {},
            }
        )
        assert result["allowed"] is True

    def test_get_state_via_process_request(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ):
        daemon.register_agent("coder-p1-t1-a1b2", "coder", "opus-4-6")
        result = daemon.process_request(
            {
                "command": "get_state",
                "agent_id": "coder-p1-t1-a1b2",
            }
        )
        assert result["ok"] is True
        assert result["state"]["role"] == "coder"

    def test_get_state_missing_agent(self, temp_state_dir: Path):
        result = daemon.process_request(
            {
                "command": "get_state",
                "agent_id": "nonexistent-p1-t1-a1b2",
            }
        )
        assert result["ok"] is False

    def test_update_context_via_process_request(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ):
        daemon.register_agent("coder-p1-t1-a1b2", "coder", "opus-4-6")
        result = daemon.process_request(
            {
                "command": "update_context",
                "agent_id": "coder-p1-t1-a1b2",
                "input_tokens": 50000,
                "output_tokens": 10000,
            }
        )
        assert result["ok"] is True
        assert result["context_usage_pct"] == pytest.approx(30.0)


class TestUpdateAgentContext:
    """Tests for update_agent_context."""

    def test_updates_token_counts(self, registered_agent, temp_state_dir: Path):
        result = daemon.update_agent_context("coder-p1-t1-a1b2", 100000, 50000)
        assert result["ok"] is True
        assert result["context_usage_pct"] == pytest.approx(75.0)

    def test_caps_at_100_percent(self, registered_agent, temp_state_dir: Path):
        result = daemon.update_agent_context("coder-p1-t1-a1b2", 200000, 200000)
        assert result["ok"] is True
        assert result["context_usage_pct"] == 100.0

    def test_missing_agent(self, temp_state_dir: Path):
        result = daemon.update_agent_context("nonexistent-p1-t1-a1b2", 1000, 1000)
        assert result["ok"] is False


class TestThinkCompletedGuardIntegration:
    """Tests for think_completed guard blocking transitions until think is done."""

    def test_think_completed_blocks_without_think(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ):
        """THINK_REQUIRED → DONE has think_completed guard; no think → no auto-transition."""
        from .conftest import make_agent_in_state

        make_agent_in_state("coder-p1-t1-a1b2", "coder", "THINK_REQUIRED")
        daemon._agent_state_cache.clear()
        result = daemon.handle_post_tool("coder-p1-t1-a1b2", "Read", {}, "")
        # No think_completed → no auto-transition
        assert result.get("transition") is None

    def test_think_completed_allows_after_think(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ):
        """After setting last_think_state = current state, think_completed fires."""
        from .conftest import make_agent_in_state

        make_agent_in_state(
            "coder-p1-t1-a1b2",
            "coder",
            "THINK_REQUIRED",
            last_think_state="THINK_REQUIRED",
        )
        daemon._agent_state_cache.clear()
        result = daemon.handle_post_tool("coder-p1-t1-a1b2", "Read", {}, "")
        assert result.get("transition") is not None
        assert result["transition"]["to_state"] == "DONE"
