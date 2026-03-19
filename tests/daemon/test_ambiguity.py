"""Tests for ambiguity detection in auto-transition engine.

Tests cover:
  - Multiple guards pass → no transition fires, error logged
  - Exactly one passes → fires correctly
  - Zero pass → no transition
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import scripts.workflow_state as daemon
from schemas.state_machine import StateMachineDefinition

from .conftest import make_agent_in_state


class TestAmbiguityDetection:
    """Test that ambiguous transitions (multiple guards pass) are blocked."""

    def test_multiple_passing_guards_no_transition(
        self, temp_state_dir: Path, ambiguity_machine: StateMachineDefinition
    ) -> None:
        """Two transitions from START both pass → no transition fires."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(agent_id, "coder", "START")
        daemon._agent_state_cache.clear()

        result = daemon.handle_post_tool(agent_id, "Read", {}, "")

        # No transition should fire due to ambiguity
        assert result["transition"] is None

        # Agent should stay in START
        agent = daemon.get_agent_state(agent_id)
        assert agent is not None
        assert agent.current_state == "START"

    def test_ambiguity_logs_error(
        self,
        temp_state_dir: Path,
        ambiguity_machine: StateMachineDefinition,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Ambiguous transitions → error is logged with details."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(agent_id, "coder", "START")
        daemon._agent_state_cache.clear()

        with caplog.at_level(logging.ERROR, logger="workflow_state"):
            daemon.handle_post_tool(agent_id, "Read", {}, "")

        assert any("AMBIGUITY" in r.message for r in caplog.records)

    def test_exactly_one_passes_fires(
        self, temp_state_dir: Path, guarded_machine: StateMachineDefinition
    ) -> None:
        """Exactly one transition's guards pass → that transition fires."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(agent_id, "coder", "STATE_A")
        daemon._agent_state_cache.clear()

        # guarded_machine: STATE_A has always_true→STATE_B and always_false→STATE_C/STATE_NO_THINK
        result = daemon.handle_post_tool(agent_id, "Read", {}, "")

        assert result["transition"] is not None
        assert result["transition"]["to_state"] == "STATE_B"

    def test_zero_pass_no_transition(
        self, temp_state_dir: Path, guarded_machine: StateMachineDefinition
    ) -> None:
        """All guards fail → no transition."""
        agent_id = "coder-p1-t1-a1b2"
        # STATE_B has only unguarded transitions, so auto-transition skips them
        make_agent_in_state(agent_id, "coder", "STATE_B")
        daemon._agent_state_cache.clear()

        result = daemon.handle_post_tool(agent_id, "Read", {}, "")
        assert result["transition"] is None

        agent = daemon.get_agent_state(agent_id)
        assert agent is not None
        assert agent.current_state == "STATE_B"

    def test_ambiguity_with_think_chosen_is_exclusive(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """think_chosen guards are mutually exclusive by design — only one can match."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(
            agent_id,
            "coder",
            "ASSESS",
            last_think_chosen="APPROVE",
            last_think_state="ASSESS",
        )
        daemon._agent_state_cache.clear()

        result = daemon.handle_post_tool(agent_id, "Read", {}, "")

        # Exactly APPROVE route fires, no ambiguity
        assert result["transition"] is not None
        assert result["transition"]["to_state"] == "APPROVED"
