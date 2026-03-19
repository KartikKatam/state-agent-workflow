"""End-to-end tests simulating full auditor state machine paths with think calls.

Tests cover:
  - auditor-task: APPROVE flow (CODE_REVIEW → APPROVED)
  - auditor-task: CRITIQUE escalation cycle
  - auditor-phase: FINALIZE flow (RULING → REPORT_WRITING)
  - auditor-phase: reinvestigation exhaustion
  - auditor-phase: error retry exhaustion

These tests use make_agent_in_state (valid Pydantic) and focus on think-driven
transitions. Mechanical guard transitions (SPAWNED→CODE_REVIEW, etc.) are
tested separately — here we start agents in the states that require think.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.daemon.guards.mechanical as _mech
import scripts.daemon.state_manager as _sm
import scripts.workflow_state as daemon
from schemas.agent_state import PendingAnnotation
from schemas.state_machine import StateMachineDefinition

from tests.daemon.conftest import make_agent_in_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _do_mcp_think(agent_id: str, chosen: str) -> dict:
    """Simulate an MCP think call through daemon handle_post_tool."""
    _sm._agent_state_cache.clear()
    output = f"Recorded. CHOSEN={chosen}" if chosen else "Recorded."
    return daemon.handle_post_tool(
        agent_id,
        "mcp__think__think",
        {"thought": "deliberation", "chosen": chosen},
        output,
    )


def _get_current_state(agent_id: str) -> str:
    """Read current_state from agent state file."""
    _sm._agent_state_cache.clear()
    agent = daemon.get_agent_state(agent_id)
    assert agent is not None, f"Agent {agent_id} not found"
    return agent.current_state


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def temp_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect all daemon state paths to a temp directory."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    annotations_dir = tmp_path / "annotations"
    annotations_dir.mkdir()

    transition_log = logs_dir / "state-transitions.jsonl"

    monkeypatch.setattr(_sm, "STATE_DIR", tmp_path)
    monkeypatch.setattr(_sm, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(_sm, "SYSTEM_STATE_FILE", tmp_path / "system.json")
    monkeypatch.setattr(_sm, "TRANSITION_LOG", transition_log)
    monkeypatch.setattr(_sm, "ANNOTATIONS_DIR", annotations_dir)

    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(daemon, "SYSTEM_STATE_FILE", tmp_path / "system.json")
    monkeypatch.setattr(daemon, "TRANSITION_LOG", transition_log)
    monkeypatch.setattr(daemon, "ANNOTATIONS_DIR", annotations_dir)

    # Also patch guards that locally import TRANSITION_LOG
    monkeypatch.setattr(_mech, "TRANSITION_LOG", transition_log)

    _sm._machine_cache.clear()
    _sm._agent_state_cache.clear()
    from scripts.daemon import transitions as _tr

    _tr._post_tool_call_count.clear()
    _tr._sent_context_warnings.clear()

    return tmp_path


@pytest.fixture()
def auditor_task_machine(
    temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> StateMachineDefinition:
    """Load auditor-task state machine."""
    machines_dir = temp_state_dir / "machines"
    machines_dir.mkdir(exist_ok=True)
    src = PROJECT_ROOT / "state-machines" / "auditor-task.json"
    data = json.loads(src.read_text())
    (machines_dir / "auditor-task.json").write_text(json.dumps(data))
    monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)
    return StateMachineDefinition.model_validate(data)


@pytest.fixture()
def auditor_phase_machine(
    temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> StateMachineDefinition:
    """Load auditor-phase state machine."""
    machines_dir = temp_state_dir / "machines"
    machines_dir.mkdir(exist_ok=True)
    src = PROJECT_ROOT / "state-machines" / "auditor-phase.json"
    data = json.loads(src.read_text())
    (machines_dir / "auditor-phase.json").write_text(json.dumps(data))
    monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)
    return StateMachineDefinition.model_validate(data)


# ---------------------------------------------------------------------------
# 18. Auditor-task approve flow
# ---------------------------------------------------------------------------


class TestAuditorTaskApproveFlow:
    def test_auditor_task_approve_flow(
        self,
        temp_state_dir: Path,
        auditor_task_machine: StateMachineDefinition,
    ) -> None:
        """CODE_REVIEW → think(APPROVE) → APPROVED terminal state."""
        agent_id = "auditor-p1-review-b3e1"

        # Start in CODE_REVIEW with pending annotation
        make_agent_in_state(
            agent_id,
            "auditor",
            "CODE_REVIEW",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-018",
                message="Think prompt",
            ),
        )

        # Think with APPROVE → auto-transition to APPROVED
        result = _do_mcp_think(agent_id, "APPROVE")

        assert result["transition"] is not None
        assert result["transition"]["to_state"] == "APPROVED"
        assert _get_current_state(agent_id) == "APPROVED"


# ---------------------------------------------------------------------------
# 19. Auditor-task critique escalation
# ---------------------------------------------------------------------------


class TestAuditorTaskCritiqueEscalation:
    def test_auditor_task_critique_escalation(
        self,
        temp_state_dir: Path,
        auditor_task_machine: StateMachineDefinition,
    ) -> None:
        """CODE_REVIEW → CRITIQUE_SENT → 3 fix cycles → ESCALATED.

        The critique cycle is: FIXES_VERIFIED → think(CRITIQUE) → CRITIQUE_SENT.
        max_occurrences=3 on the FIXES_VERIFIED→CRITIQUE_SENT transition.
        After 3 such transitions, the 4th think(CRITIQUE) from FIXES_VERIFIED
        triggers critique_cycles_exceeded → ESCALATED.
        """
        agent_id = "auditor-p1-review-b3e1"

        # Step 1: CODE_REVIEW → CRITIQUE_SENT
        make_agent_in_state(
            agent_id,
            "auditor",
            "CODE_REVIEW",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-019a",
                message="Think prompt",
            ),
        )
        result = _do_mcp_think(agent_id, "CRITIQUE")
        assert result["transition"] is not None
        assert result["transition"]["to_state"] == "CRITIQUE_SENT"

        # Steps 2-5: 3 normal cycles + 1 escalated = 4 iterations
        for i in range(4):
            # Place agent in FIXES_VERIFIED with annotation
            make_agent_in_state(
                agent_id,
                "auditor",
                "FIXES_VERIFIED",
                pending_critical_annotation=PendingAnnotation(
                    annotation_id=f"ann-019-fix-{i}",
                    message="Think prompt",
                ),
            )

            result = _do_mcp_think(agent_id, "CRITIQUE")
            assert result["transition"] is not None

            if i < 3:
                # First 3 cycles: FIXES_VERIFIED → CRITIQUE_SENT
                assert result["transition"]["to_state"] == "CRITIQUE_SENT"
            else:
                # 4th cycle: max_occurrences=3 exhausted,
                # critique_cycles_exceeded + think_chosen:CRITIQUE → ESCALATED
                assert result["transition"]["to_state"] == "ESCALATED"

        assert _get_current_state(agent_id) == "ESCALATED"


# ---------------------------------------------------------------------------
# 20. Auditor-phase finalize flow
# ---------------------------------------------------------------------------


class TestAuditorPhaseFinalizeFlow:
    def test_auditor_phase_finalize_flow(
        self,
        temp_state_dir: Path,
        auditor_phase_machine: StateMachineDefinition,
    ) -> None:
        """RULING → think(FINALIZE) → REPORT_WRITING."""
        agent_id = "auditor-p1-phase-c4d9"

        make_agent_in_state(
            agent_id,
            "auditor",
            "RULING",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-020",
                message="Ruling think prompt",
            ),
        )

        result = _do_mcp_think(agent_id, "FINALIZE")

        assert result["transition"] is not None
        assert result["transition"]["to_state"] == "REPORT_WRITING"
        assert _get_current_state(agent_id) == "REPORT_WRITING"


# ---------------------------------------------------------------------------
# 21. Auditor-phase reinvestigation exhausted
# ---------------------------------------------------------------------------


class TestAuditorPhaseReinvestigationExhausted:
    def test_auditor_phase_reinvestigation_exhausted(
        self,
        temp_state_dir: Path,
        auditor_phase_machine: StateMachineDefinition,
    ) -> None:
        """RULING → REINVESTIGATE cycles → forced finalize to REPORT_WRITING.

        Cycle: RULING → think(REINVESTIGATE) → INDEPENDENT_EXPLORATION
                      → think(any) → RULING
        max_occurrences=2 on RULING→INDEPENDENT_EXPLORATION.
        On 3rd think(REINVESTIGATE) from RULING, reinvestigation_cycles_exhausted
        fires and routes to REPORT_WRITING.
        """
        agent_id = "auditor-p1-phase-c4d9"

        for cycle in range(3):
            # Place agent in RULING with annotation
            make_agent_in_state(
                agent_id,
                "auditor",
                "RULING",
                pending_critical_annotation=PendingAnnotation(
                    annotation_id=f"ann-021-{cycle}",
                    message="Ruling think prompt",
                ),
            )

            result = _do_mcp_think(agent_id, "REINVESTIGATE")
            assert result["transition"] is not None

            if cycle < 2:
                # First 2 cycles: RULING → INDEPENDENT_EXPLORATION
                assert result["transition"]["to_state"] == "INDEPENDENT_EXPLORATION"

                # Return to RULING via think_completed
                make_agent_in_state(
                    agent_id,
                    "auditor",
                    "INDEPENDENT_EXPLORATION",
                    pending_critical_annotation=PendingAnnotation(
                        annotation_id=f"ann-021-explore-{cycle}",
                        message="Exploration think prompt",
                    ),
                )
                explore_result = _do_mcp_think(agent_id, "")
                assert explore_result["transition"] is not None
                assert explore_result["transition"]["to_state"] == "RULING"
            else:
                # 3rd cycle: max_occurrences=2 exhausted → REPORT_WRITING
                assert result["transition"]["to_state"] == "REPORT_WRITING"

        assert _get_current_state(agent_id) == "REPORT_WRITING"


# ---------------------------------------------------------------------------
# 22. Auditor-phase error retry exhausted
# ---------------------------------------------------------------------------


class TestAuditorPhaseErrorRetryExhausted:
    def test_auditor_phase_error_retry_exhausted(
        self,
        temp_state_dir: Path,
        auditor_phase_machine: StateMachineDefinition,
    ) -> None:
        """ERROR → think(RETRY) → CONTEXT_LOADING → ERROR → think(RETRY) → HANDOFF.

        The ERROR→CONTEXT_LOADING transition has max_occurrences=1.
        When retry_exhausted fires, the fallback routes to HANDOFF.
        """
        agent_id = "auditor-p1-phase-c4d9"

        # First attempt: ERROR → RETRY → CONTEXT_LOADING
        make_agent_in_state(
            agent_id,
            "auditor",
            "ERROR",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-022a",
                message="Error think prompt",
            ),
        )

        result = _do_mcp_think(agent_id, "RETRY")
        assert result["transition"] is not None
        assert result["transition"]["to_state"] == "CONTEXT_LOADING"

        # Second attempt: back in ERROR, retry budget exhausted
        make_agent_in_state(
            agent_id,
            "auditor",
            "ERROR",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-022b",
                message="Error think prompt",
            ),
        )

        result = _do_mcp_think(agent_id, "RETRY")
        assert result["transition"] is not None
        assert result["transition"]["to_state"] == "HANDOFF"
        assert _get_current_state(agent_id) == "HANDOFF"
