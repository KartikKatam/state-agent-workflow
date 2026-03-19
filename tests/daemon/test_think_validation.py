"""Tests for think validation lifecycle.

Tests cover:
  - Complete think (all questions + CHOSEN) → clears annotation
  - Missing CHOSEN → annotation stays
  - Missing answers → annotation stays
  - CHOSEN stored in agent state correctly
"""

from __future__ import annotations

import json
from pathlib import Path

import scripts.workflow_state as daemon
from schemas.agent_state import PendingAnnotation
from schemas.state_machine import StateMachineDefinition
from scripts.daemon.annotations import validate_think

from .conftest import make_agent_in_state


# ---------------------------------------------------------------------------
# validate_think() unit tests
# ---------------------------------------------------------------------------


class TestValidateThink:
    """Test validate_think() directly for completeness checking."""

    def test_complete_think_with_chosen(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """All questions answered + CHOSEN present → complete=True."""
        think_output = "1) Yes, the code is correct. 2) Use incremental approach. CHOSEN: APPROVE"
        result = validate_think(
            "coder-p1-t1-a1b2", "ASSESS", decision_machine, think_output
        )
        assert result["complete"] is True
        assert result["chosen"] == "APPROVE"
        assert result["issues"] == []

    def test_missing_chosen(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """All questions answered but no CHOSEN → incomplete."""
        think_output = "1) Yes, the code is correct. 2) Use incremental approach."
        result = validate_think(
            "coder-p1-t1-a1b2", "ASSESS", decision_machine, think_output
        )
        assert result["complete"] is False
        assert "missing_chosen" in result["issues"]

    def test_missing_answers(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """CHOSEN present but missing numbered answers → incomplete."""
        think_output = "CHOSEN: APPROVE"
        result = validate_think(
            "coder-p1-t1-a1b2", "ASSESS", decision_machine, think_output
        )
        assert result["complete"] is False
        assert any("missing_answers" in issue for issue in result["issues"])

    def test_missing_both(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """No answers and no CHOSEN → two issues."""
        think_output = "I'm not sure what to do."
        result = validate_think(
            "coder-p1-t1-a1b2", "ASSESS", decision_machine, think_output
        )
        assert result["complete"] is False
        assert len(result["issues"]) == 2

    def test_non_think_state_always_complete(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """State without think_on_exit → always complete, no validation needed."""
        result = validate_think(
            "coder-p1-t1-a1b2", "APPROVED", decision_machine, "anything"
        )
        assert result["complete"] is True


# ---------------------------------------------------------------------------
# End-to-end think validation via handle_post_tool
# ---------------------------------------------------------------------------


class TestThinkValidationEndToEnd:
    """Test think validation through handle_post_tool."""

    def test_complete_think_clears_annotation(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """Complete Think output → annotation cleared, CHOSEN stored."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(
            agent_id,
            "coder",
            "ASSESS",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-001",
                message="Think prompt",
            ),
        )
        daemon._agent_state_cache.clear()

        # Simulate Think tool with complete output
        think_output = "1) Yes 2) Incremental approach. CHOSEN: APPROVE"
        result = daemon.handle_post_tool(agent_id, "Think", {}, think_output)

        # Annotation should be cleared
        agent = daemon.get_agent_state(agent_id)
        assert agent is not None
        assert agent.pending_critical_annotation is None

        # CHOSEN should be stored
        assert agent.last_think_chosen == "APPROVE"
        assert agent.last_think_state == "ASSESS"

        # No inject_context — agent already saw MCP response
        assert result["inject_context"] is None

    def test_incomplete_think_keeps_annotation(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """Incomplete Think output → annotation stays, feedback given."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(
            agent_id,
            "coder",
            "ASSESS",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-001",
                message="Think prompt",
            ),
        )
        daemon._agent_state_cache.clear()

        # Think without CHOSEN
        think_output = "1) Yes 2) Incremental"
        result = daemon.handle_post_tool(agent_id, "Think", {}, think_output)

        # Annotation should still be set
        agent = daemon.get_agent_state(agent_id)
        assert agent is not None
        assert agent.pending_critical_annotation is not None

        # Feedback should mention incompleteness
        assert any("incomplete" in f.lower() for f in result["feedback"])

    def test_chosen_stored_correctly_in_agent_state(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """After complete Think, agent state has correct CHOSEN fields."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(
            agent_id,
            "coder",
            "ASSESS",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-002",
                message="Think prompt",
            ),
        )
        daemon._agent_state_cache.clear()

        think_output = "1) Correct 2) Retry approach. CHOSEN: RETRY"
        daemon.handle_post_tool(agent_id, "Think", {}, think_output)

        agent = daemon.get_agent_state(agent_id)
        assert agent is not None
        assert agent.last_think_chosen == "RETRY"
        assert agent.last_think_state == "ASSESS"

    def test_complete_think_then_auto_transition_fires(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """Complete Think + CHOSEN → annotation cleared → auto-transition fires based on CHOSEN."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(
            agent_id,
            "coder",
            "ASSESS",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-003",
                message="Think prompt",
            ),
        )
        daemon._agent_state_cache.clear()

        think_output = "1) Good 2) Approve it. CHOSEN: APPROVE"
        result = daemon.handle_post_tool(agent_id, "Think", {}, think_output)

        # The Think call stores CHOSEN, and the auto-transition should fire
        # based on think_chosen:APPROVE guard
        assert result["transition"] is not None
        assert result["transition"]["to_state"] == "APPROVED"

        agent = daemon.get_agent_state(agent_id)
        assert agent is not None
        assert agent.current_state == "APPROVED"

    def test_non_think_tool_does_not_validate(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """Non-Think tool with pending annotation → no validation attempt."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(
            agent_id,
            "coder",
            "ASSESS",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-004",
                message="Think prompt",
            ),
        )
        daemon._agent_state_cache.clear()

        # Read tool — should not trigger think validation
        result = daemon.handle_post_tool(agent_id, "Read", {}, "file content")

        # Annotation should still be there
        agent = daemon.get_agent_state(agent_id)
        assert agent is not None
        assert agent.pending_critical_annotation is not None
