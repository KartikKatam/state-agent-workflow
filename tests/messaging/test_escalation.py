"""Tests for non-blocking message escalation (Chunk 5)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.daemon.state_manager as _sm
from scripts.daemon.messaging import (
    _pending_escalations,
    check_escalation,
    escalate_to_blocking,
)


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    annotations_dir = tmp_path / "annotations"
    annotations_dir.mkdir()
    monkeypatch.setattr(_sm, "AGENTS_DIR", agents_dir)

    # Also patch the AGENTS_DIR and ANNOTATIONS_DIR in the messaging module
    import scripts.daemon.messaging as msg_mod
    monkeypatch.setattr(msg_mod, "AGENTS_DIR", agents_dir)
    from hooks.utils import state_helpers
    monkeypatch.setattr(msg_mod, "ANNOTATIONS_DIR", annotations_dir)
    monkeypatch.setattr(state_helpers, "ANNOTATIONS_DIR", annotations_dir)

    _sm._agent_state_cache.clear()
    _pending_escalations.clear()
    yield
    _sm._agent_state_cache.clear()
    _pending_escalations.clear()


def _create_agent(agents_dir: Path, agent_id: str = "coder-p1-t1-a1b2") -> None:
    state = {
        "id": agent_id,
        "role": "coder",
        "model": "opus-4-6",
        "spawned_at": "2024-01-01T00:00:00Z",
        "status": "active",
        "current_state": "IMPLEMENTATION",
    }
    (agents_dir / f"{agent_id}.json").write_text(json.dumps(state))


class TestCheckEscalation:
    def test_fires_on_ready_state(self):
        agent_id = "coder-p1-t1-a1b2"
        _pending_escalations[agent_id] = [
            {"message_id": "msg-001", "from_agent": "orch", "message_type": "task_assign"}
        ]
        result = check_escalation(agent_id, "TASK_COMPLETE")
        assert result == ["msg-001"]

    def test_no_fire_on_non_ready_state(self):
        agent_id = "coder-p1-t1-a1b2"
        _pending_escalations[agent_id] = [
            {"message_id": "msg-001", "from_agent": "orch", "message_type": "task_assign"}
        ]
        result = check_escalation(agent_id, "IMPLEMENTATION")
        assert result == []

    def test_fires_on_idle_state(self):
        agent_id = "coder-p1-t1-a1b2"
        _pending_escalations[agent_id] = [
            {"message_id": "msg-001", "from_agent": "orch", "message_type": "info_request"}
        ]
        result = check_escalation(agent_id, "IDLE")
        assert result == ["msg-001"]

    def test_empty_when_no_escalations(self):
        result = check_escalation("coder-p1-t1-a1b2", "TASK_COMPLETE")
        assert result == []


class TestEscalateToBlocking:
    def test_sets_inbox_blocked(self, tmp_path):
        agent_id = "coder-p1-t1-a1b2"
        agents_dir = tmp_path / "agents"
        _create_agent(agents_dir, agent_id)
        _pending_escalations[agent_id] = [
            {"message_id": "msg-001", "from_agent": "orch", "message_type": "task_assign"}
        ]
        escalate_to_blocking(agent_id, "msg-001")

        state = json.loads((agents_dir / f"{agent_id}.json").read_text())
        assert state["inbox_blocked"] is not None
        assert state["inbox_blocked"]["blocked_by_message_id"] == "msg-001"
        assert state["inbox_blocked"]["from_agent"] == "orch"

    def test_removes_from_escalations(self):
        agent_id = "coder-p1-t1-a1b2"
        _pending_escalations[agent_id] = [
            {"message_id": "msg-001", "from_agent": "orch", "message_type": "task_assign"},
            {"message_id": "msg-002", "from_agent": "orch", "message_type": "info_request"},
        ]
        escalate_to_blocking(agent_id, "msg-001")
        assert len(_pending_escalations[agent_id]) == 1
        assert _pending_escalations[agent_id][0]["message_id"] == "msg-002"

    def test_writes_critical_annotation(self, tmp_path):
        agent_id = "coder-p1-t1-a1b2"
        agents_dir = tmp_path / "agents"
        annotations_dir = tmp_path / "annotations"
        _create_agent(agents_dir, agent_id)
        _pending_escalations[agent_id] = [
            {"message_id": "msg-001", "from_agent": "orch", "message_type": "task_assign"}
        ]
        escalate_to_blocking(agent_id, "msg-001")

        ann_file = annotations_dir / f"{agent_id}.jsonl"
        assert ann_file.exists()
        lines = ann_file.read_text().strip().split("\n")
        ann = json.loads(lines[-1])
        assert ann["priority"] == "critical"
        assert "ESCALATED" in ann["message"]

    def test_noop_for_unknown_message(self):
        agent_id = "coder-p1-t1-a1b2"
        _pending_escalations[agent_id] = [
            {"message_id": "msg-001", "from_agent": "orch", "message_type": "task_assign"}
        ]
        escalate_to_blocking(agent_id, "msg-999")  # not in pending
        # Should not crash, should not modify escalations
        assert len(_pending_escalations[agent_id]) == 1
