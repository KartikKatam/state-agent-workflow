"""Tests for decision guards: think_chosen and think_completed.

Tests cover:
  - think_chosen:VALUE matches/mismatches
  - think_completed for states with/without completed think
  - Stale CHOSEN (different state) doesn't match
  - Parameterized guard parsing (name:param split)
"""

from __future__ import annotations

from pathlib import Path

import scripts.workflow_state as daemon
from schemas.state_machine import StateMachineDefinition
from scripts.daemon.guards.decision import _guard_think_chosen, _guard_think_completed

from .conftest import make_agent_in_state


# ---------------------------------------------------------------------------
# think_chosen guard (direct function tests)
# ---------------------------------------------------------------------------


class TestThinkChosenGuard:
    """Test _guard_think_chosen function directly."""

    def test_matches_when_chosen_and_state_match(self) -> None:
        """CHOSEN=APPROVE, state=ASSESS, current=ASSESS → True."""
        agent = {
            "last_think_chosen": "APPROVE",
            "last_think_state": "ASSESS",
            "current_state": "ASSESS",
        }
        assert _guard_think_chosen("test-agent", agent, None, "APPROVE") is True

    def test_fails_when_chosen_differs(self) -> None:
        """CHOSEN=RETRY but param=APPROVE → False."""
        agent = {
            "last_think_chosen": "RETRY",
            "last_think_state": "ASSESS",
            "current_state": "ASSESS",
        }
        assert _guard_think_chosen("test-agent", agent, None, "APPROVE") is False

    def test_fails_when_no_param(self) -> None:
        """No param provided → False (param is required)."""
        agent = {
            "last_think_chosen": "APPROVE",
            "last_think_state": "ASSESS",
            "current_state": "ASSESS",
        }
        assert _guard_think_chosen("test-agent", agent, None, None) is False

    def test_fails_when_no_agent(self) -> None:
        """No agent state → False."""
        assert _guard_think_chosen("test-agent", None, None, "APPROVE") is False

    def test_stale_chosen_different_state(self) -> None:
        """CHOSEN made in STATE_A but agent now in STATE_B → False (stale)."""
        agent = {
            "last_think_chosen": "APPROVE",
            "last_think_state": "STATE_A",
            "current_state": "STATE_B",
        }
        assert _guard_think_chosen("test-agent", agent, None, "APPROVE") is False

    def test_case_sensitive(self) -> None:
        """CHOSEN matching is case-sensitive: 'approve' != 'APPROVE'."""
        agent = {
            "last_think_chosen": "approve",
            "last_think_state": "ASSESS",
            "current_state": "ASSESS",
        }
        assert _guard_think_chosen("test-agent", agent, None, "APPROVE") is False

    def test_no_chosen_set(self) -> None:
        """No last_think_chosen set → False."""
        agent = {
            "last_think_chosen": None,
            "last_think_state": "ASSESS",
            "current_state": "ASSESS",
        }
        assert _guard_think_chosen("test-agent", agent, None, "APPROVE") is False


# ---------------------------------------------------------------------------
# think_completed guard (direct function tests)
# ---------------------------------------------------------------------------


class TestThinkCompletedGuard:
    """Test _guard_think_completed function directly."""

    def test_passes_when_think_state_matches_current(self) -> None:
        """last_think_state == current_state → True."""
        agent = {
            "last_think_state": "WRITING",
            "current_state": "WRITING",
        }
        assert _guard_think_completed("test-agent", agent, None) is True

    def test_fails_when_think_state_differs(self) -> None:
        """last_think_state != current_state → False."""
        agent = {
            "last_think_state": "OLD_STATE",
            "current_state": "WRITING",
        }
        assert _guard_think_completed("test-agent", agent, None) is False

    def test_fails_when_no_think_state(self) -> None:
        """No last_think_state → False (never thought)."""
        agent = {
            "last_think_state": None,
            "current_state": "WRITING",
        }
        assert _guard_think_completed("test-agent", agent, None) is False

    def test_fails_when_no_agent(self) -> None:
        """No agent state → False."""
        assert _guard_think_completed("test-agent", None, None) is False

    def test_ignores_chosen_value(self) -> None:
        """think_completed doesn't care about CHOSEN value."""
        agent = {
            "last_think_chosen": None,
            "last_think_state": "ASSESS",
            "current_state": "ASSESS",
        }
        assert _guard_think_completed("test-agent", agent, None) is True

    def test_ignores_param(self) -> None:
        """Extra param is ignored by think_completed."""
        agent = {
            "last_think_state": "ASSESS",
            "current_state": "ASSESS",
        }
        assert _guard_think_completed("test-agent", agent, None, "ignored") is True


# ---------------------------------------------------------------------------
# Parameterized guard evaluation via evaluate_guards
# ---------------------------------------------------------------------------


class TestParameterizedGuardEval:
    """Test parameterized guard parsing through evaluate_guards."""

    def test_think_chosen_via_evaluate(self, temp_state_dir: Path) -> None:
        """evaluate_guards with 'think_chosen:APPROVE' → splits name:param correctly."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(
            agent_id,
            "coder",
            "ASSESS",
            last_think_chosen="APPROVE",
            last_think_state="ASSESS",
        )
        daemon._agent_state_cache.clear()

        passed, results, _ = daemon.evaluate_guards(
            ["think_chosen:APPROVE"], agent_id
        )
        assert passed is True
        assert results["think_chosen:APPROVE"] is True

    def test_think_chosen_mismatch_via_evaluate(self, temp_state_dir: Path) -> None:
        """evaluate_guards with wrong CHOSEN value → fails."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(
            agent_id,
            "coder",
            "ASSESS",
            last_think_chosen="RETRY",
            last_think_state="ASSESS",
        )
        daemon._agent_state_cache.clear()

        passed, results, _ = daemon.evaluate_guards(
            ["think_chosen:APPROVE"], agent_id
        )
        assert passed is False
        assert results["think_chosen:APPROVE"] is False

    def test_think_completed_via_evaluate(self, temp_state_dir: Path) -> None:
        """evaluate_guards with 'think_completed' (no param) → works."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(
            agent_id,
            "coder",
            "ASSESS",
            last_think_state="ASSESS",
        )
        daemon._agent_state_cache.clear()

        passed, results, _ = daemon.evaluate_guards(
            ["think_completed"], agent_id
        )
        assert passed is True
        assert results["think_completed"] is True

    def test_multiple_colons_in_param(self, temp_state_dir: Path) -> None:
        """Guard 'think_chosen:FOO:BAR' → name='think_chosen', param='FOO:BAR'."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(
            agent_id,
            "coder",
            "ASSESS",
            last_think_chosen="FOO:BAR",
            last_think_state="ASSESS",
        )
        daemon._agent_state_cache.clear()

        passed, results, _ = daemon.evaluate_guards(
            ["think_chosen:FOO:BAR"], agent_id
        )
        assert passed is True
        assert results["think_chosen:FOO:BAR"] is True


# ---------------------------------------------------------------------------
# Decision guard routing via auto-transition
# ---------------------------------------------------------------------------


class TestDecisionGuardRouting:
    """Test think_chosen guards fire correct auto-transitions."""

    def test_chosen_approve_routes_to_approved(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """Agent with CHOSEN=APPROVE in ASSESS → auto-transitions to APPROVED."""
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

        assert result["transition"] is not None
        assert result["transition"]["to_state"] == "APPROVED"

        agent = daemon.get_agent_state(agent_id)
        assert agent is not None
        assert agent.current_state == "APPROVED"

    def test_chosen_retry_routes_to_retry(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """Agent with CHOSEN=RETRY in ASSESS → auto-transitions to RETRY."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(
            agent_id,
            "coder",
            "ASSESS",
            last_think_chosen="RETRY",
            last_think_state="ASSESS",
        )
        daemon._agent_state_cache.clear()

        result = daemon.handle_post_tool(agent_id, "Read", {}, "")

        assert result["transition"] is not None
        assert result["transition"]["to_state"] == "RETRY"

    def test_no_chosen_no_transition(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """Agent in ASSESS with no CHOSEN → no transition fires."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(agent_id, "coder", "ASSESS")
        daemon._agent_state_cache.clear()

        result = daemon.handle_post_tool(agent_id, "Read", {}, "")
        assert result["transition"] is None

    def test_stale_chosen_no_transition(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """CHOSEN from different state → no transition (stale)."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(
            agent_id,
            "coder",
            "ASSESS",
            last_think_chosen="APPROVE",
            last_think_state="SOME_OTHER_STATE",
        )
        daemon._agent_state_cache.clear()

        result = daemon.handle_post_tool(agent_id, "Read", {}, "")
        assert result["transition"] is None
