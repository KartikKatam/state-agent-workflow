"""Integration tests for daemon Think MCP handling.

Tests cover:
  - Daemon reads chosen from MCP tool_input (not regex)
  - Annotation clearing on valid think
  - last_think_chosen/last_think_state storage
  - Auto-transition after MCP think
  - Pacing think with think_completed guard
  - Permission: think allowed during annotation blocking, Write blocked
  - Mutation blocking until think clears annotation
  - validate_think MCP fast-path (CHOSEN= extraction)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.workflow_state as daemon
import scripts.daemon.state_manager as _sm
from schemas.agent_state import PendingAnnotation
from schemas.state_machine import StateMachineDefinition
from scripts.daemon.annotations import validate_think
from scripts.daemon.permissions import handle_pre_tool

from tests.daemon.conftest import make_agent_in_state


# ---------------------------------------------------------------------------
# Fixtures — auditor-task machine with think_chosen guards
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

    monkeypatch.setattr(_sm, "STATE_DIR", tmp_path)
    monkeypatch.setattr(_sm, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(_sm, "SYSTEM_STATE_FILE", tmp_path / "system.json")
    monkeypatch.setattr(_sm, "TRANSITION_LOG", logs_dir / "state-transitions.jsonl")
    monkeypatch.setattr(_sm, "ANNOTATIONS_DIR", annotations_dir)

    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(daemon, "SYSTEM_STATE_FILE", tmp_path / "system.json")
    monkeypatch.setattr(daemon, "TRANSITION_LOG", logs_dir / "state-transitions.jsonl")
    monkeypatch.setattr(daemon, "ANNOTATIONS_DIR", annotations_dir)

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
    """Load the real auditor-task state machine."""
    machines_dir = temp_state_dir / "machines"
    machines_dir.mkdir(exist_ok=True)

    src = PROJECT_ROOT / "state-machines" / "auditor-task.json"
    data = json.loads(src.read_text())
    (machines_dir / "auditor-task.json").write_text(json.dumps(data))

    monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)
    return StateMachineDefinition.model_validate(data)


# ---------------------------------------------------------------------------
# 10. Daemon reads chosen from tool_input
# ---------------------------------------------------------------------------


class TestDaemonReadsChosen:
    def test_daemon_reads_chosen_from_tool_input(
        self, temp_state_dir: Path, auditor_task_machine: StateMachineDefinition
    ) -> None:
        """handle_post_tool with mcp__think__think reads chosen from tool_input."""
        agent_id = "auditor-p1-review-b3e1"
        make_agent_in_state(
            agent_id,
            "auditor",
            "CODE_REVIEW",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-010",
                message="Think prompt",
            ),
        )
        _sm._agent_state_cache.clear()

        result = daemon.handle_post_tool(
            agent_id,
            "mcp__think__think",
            {"thought": "reasoning here", "chosen": "APPROVE"},
            "Recorded. CHOSEN=APPROVE",
        )

        # Should not have error feedback about think
        feedback = result.get("feedback", [])
        assert not any("incomplete" in f.lower() for f in feedback)


# ---------------------------------------------------------------------------
# 11. Annotation cleared on valid think
# ---------------------------------------------------------------------------


class TestAnnotationClearing:
    def test_daemon_clears_annotation_on_valid_think(
        self, temp_state_dir: Path, auditor_task_machine: StateMachineDefinition
    ) -> None:
        """Valid think clears pending_critical_annotation."""
        agent_id = "auditor-p1-review-b3e1"
        make_agent_in_state(
            agent_id,
            "auditor",
            "CODE_REVIEW",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-011",
                message="Think prompt",
            ),
        )
        _sm._agent_state_cache.clear()

        daemon.handle_post_tool(
            agent_id,
            "mcp__think__think",
            {"thought": "detailed reasoning", "chosen": "APPROVE"},
            "Recorded. CHOSEN=APPROVE",
        )

        agent = daemon.get_agent_state(agent_id)
        assert agent is not None
        assert agent.pending_critical_annotation is None


# ---------------------------------------------------------------------------
# 12. Stores last_think_chosen
# ---------------------------------------------------------------------------


class TestStoresThinkChosen:
    def test_daemon_stores_last_think_chosen(
        self, temp_state_dir: Path, auditor_task_machine: StateMachineDefinition
    ) -> None:
        """After think with chosen=APPROVE, agent state has correct fields."""
        agent_id = "auditor-p1-review-b3e1"
        make_agent_in_state(
            agent_id,
            "auditor",
            "CODE_REVIEW",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-012",
                message="Think prompt",
            ),
        )
        _sm._agent_state_cache.clear()

        daemon.handle_post_tool(
            agent_id,
            "mcp__think__think",
            {"thought": "analysis", "chosen": "APPROVE"},
            "Recorded. CHOSEN=APPROVE",
        )

        agent = daemon.get_agent_state(agent_id)
        assert agent is not None
        assert agent.last_think_chosen == "APPROVE"
        assert agent.last_think_state == "CODE_REVIEW"


# ---------------------------------------------------------------------------
# 13. Auto-transition after think
# ---------------------------------------------------------------------------


class TestAutoTransitionAfterThink:
    def test_daemon_auto_transition_after_think(
        self, temp_state_dir: Path, auditor_task_machine: StateMachineDefinition
    ) -> None:
        """Think with APPROVE in CODE_REVIEW → auto-transition to APPROVED."""
        agent_id = "auditor-p1-review-b3e1"
        make_agent_in_state(
            agent_id,
            "auditor",
            "CODE_REVIEW",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-013",
                message="Think prompt",
            ),
        )
        _sm._agent_state_cache.clear()

        result = daemon.handle_post_tool(
            agent_id,
            "mcp__think__think",
            {"thought": "all looks good", "chosen": "APPROVE"},
            "Recorded. CHOSEN=APPROVE",
        )

        assert result["transition"] is not None
        assert result["transition"]["to_state"] == "APPROVED"

        agent = daemon.get_agent_state(agent_id)
        assert agent is not None
        assert agent.current_state == "APPROVED"


# ---------------------------------------------------------------------------
# 14. Pacing think with think_completed
# ---------------------------------------------------------------------------


class TestPacingThinkCompleted:
    def test_daemon_pacing_think_completed(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pacing think (empty chosen) in ARTIFACT_REVIEW → think_completed fires transition."""
        # Load auditor-phase machine
        machines_dir = temp_state_dir / "machines"
        machines_dir.mkdir(exist_ok=True)
        src = PROJECT_ROOT / "state-machines" / "auditor-phase.json"
        data = json.loads(src.read_text())
        (machines_dir / "auditor-phase.json").write_text(json.dumps(data))
        monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)

        agent_id = "auditor-p1-phase-c4d9"
        make_agent_in_state(
            agent_id,
            "auditor",
            "ARTIFACT_REVIEW",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-014",
                message="Review artifacts",
            ),
        )
        _sm._agent_state_cache.clear()

        # Pacing think — empty chosen, but think_completed should fire
        result = daemon.handle_post_tool(
            agent_id,
            "mcp__think__think",
            {"thought": "reviewed all artifacts thoroughly", "chosen": ""},
            "Recorded.",
        )

        # think_completed guard should pass → transition to INDEPENDENT_EXPLORATION
        assert result["transition"] is not None
        assert result["transition"]["to_state"] == "INDEPENDENT_EXPLORATION"

        agent = daemon.get_agent_state(agent_id)
        assert agent is not None
        assert agent.current_state == "INDEPENDENT_EXPLORATION"


# ---------------------------------------------------------------------------
# 15. Think allowed during annotation blocking
# ---------------------------------------------------------------------------


class TestThinkPermissions:
    def test_daemon_allows_think_during_annotation_blocking(
        self, temp_state_dir: Path, auditor_task_machine: StateMachineDefinition
    ) -> None:
        """mcp__think__think is allowed when annotation is pending; Write is blocked."""
        agent_id = "auditor-p1-review-b3e1"
        make_agent_in_state(
            agent_id,
            "auditor",
            "CODE_REVIEW",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-015",
                message="Think prompt",
            ),
        )
        _sm._agent_state_cache.clear()

        # Think should be allowed
        think_result = handle_pre_tool(agent_id, "mcp__think__think", {})
        assert think_result["allowed"] is True

        # Write should be blocked (annotation pending)
        write_result = handle_pre_tool(agent_id, "Write", {"file_path": "test.py"})
        assert write_result["allowed"] is False


# ---------------------------------------------------------------------------
# 16. Mutations blocked until think
# ---------------------------------------------------------------------------


class TestMutationBlocking:
    def test_daemon_rejects_mutations_until_think(
        self, temp_state_dir: Path, auditor_task_machine: StateMachineDefinition
    ) -> None:
        """Write blocked → think clears annotation → Write allowed."""
        agent_id = "auditor-p1-review-b3e1"
        make_agent_in_state(
            agent_id,
            "auditor",
            "CODE_REVIEW",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-016",
                message="Think prompt",
            ),
        )
        _sm._agent_state_cache.clear()

        # Before think: Write blocked
        pre = handle_pre_tool(agent_id, "Write", {"file_path": "test.py"})
        assert pre["allowed"] is False

        # Do think
        daemon.handle_post_tool(
            agent_id,
            "mcp__think__think",
            {"thought": "approval reasoning", "chosen": "APPROVE"},
            "Recorded. CHOSEN=APPROVE",
        )

        # After think: annotation cleared, agent now in APPROVED state
        # (APPROVED has no annotation blocking and no write restrictions)
        _sm._agent_state_cache.clear()
        post = handle_pre_tool(agent_id, "Write", {"file_path": "test.py"})
        assert post["allowed"] is True


# ---------------------------------------------------------------------------
# 17. validate_think MCP fast-path
# ---------------------------------------------------------------------------


class TestValidateThinkFastPath:
    def test_validate_think_mcp_fast_path(
        self, temp_state_dir: Path, auditor_task_machine: StateMachineDefinition
    ) -> None:
        """MCP-style output 'Recorded. CHOSEN=APPROVE' triggers fast-path extraction."""
        result = validate_think(
            "auditor-p1-review-b3e1",
            "CODE_REVIEW",
            auditor_task_machine,
            "Recorded. CHOSEN=APPROVE",
        )
        assert result["complete"] is True
        assert result["chosen"] == "APPROVE"
        assert result["issues"] == []
