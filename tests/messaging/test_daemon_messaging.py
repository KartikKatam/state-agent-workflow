"""Tests for scripts/daemon/messaging.py — notification, ack, escalation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.daemon.messaging as _msg
import scripts.daemon.state_manager as _sm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolate_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect AGENTS_DIR and ANNOTATIONS_DIR to tmp_path; clear escalations."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    annotations_dir = tmp_path / "annotations"
    annotations_dir.mkdir()

    # Patch in the messaging module (where the code reads these at call time)
    monkeypatch.setattr(_msg, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(_msg, "ANNOTATIONS_DIR", annotations_dir)

    # Also patch state_manager so _invalidate_agent_cache works against same dir
    monkeypatch.setattr(_sm, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(_sm, "ANNOTATIONS_DIR", annotations_dir)

    # Clear in-memory escalation state
    _msg._pending_escalations.clear()

    # Suppress emit calls (no real event log in tests)
    monkeypatch.setattr(_msg, "emit", lambda *a, **kw: None)

    return tmp_path


def _write_agent_state(tmp_path: Path, agent_id: str, **extra: object) -> Path:
    """Write a minimal agent state JSON file and return its path."""
    agents_dir = tmp_path / "agents"
    state = {
        "id": agent_id,
        "role": "coder",
        "model": "opus-4-6",
        "status": "active",
        "current_state": "IMPLEMENTATION",
        "inbox_blocked": None,
    }
    state.update(extra)
    path = agents_dir / f"{agent_id}.json"
    path.write_text(json.dumps(state, indent=2))
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_notification_creates_annotation(tmp_path: Path) -> None:
    """Blocking notification writes an annotation JSONL entry for the target agent."""
    result = _msg.handle_message_notification(
        from_agent="lead-abc",
        to_agent="coder-xyz",
        team_id="team-1",
        message_id="msg-11112222-3333",
        priority="blocking",
        message_type="task_assignment",
    )

    assert result["ok"] is True
    assert result["annotation_written"] is True

    ann_file = tmp_path / "annotations" / "coder-xyz.jsonl"
    assert ann_file.exists()

    lines = ann_file.read_text().strip().splitlines()
    assert len(lines) == 1

    ann = json.loads(lines[0])
    assert ann["priority"] == "critical"
    assert "lead-abc" in ann["message"]
    assert ann["annotation_id"] == "msg-msg-1111"
    assert ann["acknowledged"] is False


def test_notification_sets_inbox_blocked(tmp_path: Path) -> None:
    """Blocking notification sets inbox_blocked on the target agent's state."""
    agent_id = "coder-xyz"
    _write_agent_state(tmp_path, agent_id)

    _msg.handle_message_notification(
        from_agent="lead-abc",
        to_agent=agent_id,
        team_id="team-1",
        message_id="msg-aaaa-bbbb",
        priority="blocking",
        message_type="info",
    )

    state = json.loads((tmp_path / "agents" / f"{agent_id}.json").read_text())
    assert state["inbox_blocked"] is not None
    assert state["inbox_blocked"]["blocked_by_message_id"] == "msg-aaaa-bbbb"
    assert state["inbox_blocked"]["from_agent"] == "lead-abc"


def test_notification_nonblocking_registers_escalation(tmp_path: Path) -> None:
    """Non-blocking notification registers an entry in _pending_escalations."""
    _msg.handle_message_notification(
        from_agent="lead-abc",
        to_agent="coder-xyz",
        team_id="team-1",
        message_id="msg-nb-0001",
        priority="non-blocking",
        message_type="status_update",
    )

    assert "coder-xyz" in _msg._pending_escalations
    entries = _msg._pending_escalations["coder-xyz"]
    assert len(entries) == 1
    assert entries[0]["message_id"] == "msg-nb-0001"
    assert entries[0]["from_agent"] == "lead-abc"


def test_ack_clears_inbox_blocked(tmp_path: Path) -> None:
    """Acking a blocking message clears inbox_blocked from agent state."""
    agent_id = "coder-xyz"
    message_id = "msg-block-1234"
    _write_agent_state(
        tmp_path,
        agent_id,
        inbox_blocked={
            "blocked_by_message_id": message_id,
            "from_agent": "lead-abc",
        },
    )

    result = _msg.handle_message_ack(agent_id, message_id, team_id="team-1")
    assert result["ok"] is True

    state = json.loads((tmp_path / "agents" / f"{agent_id}.json").read_text())
    assert state["inbox_blocked"] is None


def test_ack_removes_escalation(tmp_path: Path) -> None:
    """Acking a non-blocking message removes it from _pending_escalations."""
    agent_id = "coder-xyz"
    message_id = "msg-nb-rem"

    _msg._pending_escalations[agent_id] = [
        {"message_id": message_id, "from_agent": "lead-abc", "message_type": "info"},
        {"message_id": "msg-other", "from_agent": "lead-abc", "message_type": "info"},
    ]

    _msg.handle_message_ack(agent_id, message_id, team_id="team-1")

    remaining = _msg._pending_escalations[agent_id]
    assert len(remaining) == 1
    assert remaining[0]["message_id"] == "msg-other"


def test_escalation_fires_on_ready_state() -> None:
    """check_escalation returns message_ids when new_state is a ready state."""
    agent_id = "coder-xyz"
    _msg._pending_escalations[agent_id] = [
        {"message_id": "msg-e1", "from_agent": "lead", "message_type": "info"},
    ]

    result = _msg.check_escalation(agent_id, "TASK_COMPLETE")
    assert result == ["msg-e1"]


def test_escalation_doesnt_fire_on_non_ready_state() -> None:
    """check_escalation returns empty list for non-ready states."""
    agent_id = "coder-xyz"
    _msg._pending_escalations[agent_id] = [
        {"message_id": "msg-e2", "from_agent": "lead", "message_type": "info"},
    ]

    result = _msg.check_escalation(agent_id, "IMPLEMENTATION")
    assert result == []


def test_escalate_to_blocking_sets_block(tmp_path: Path) -> None:
    """escalate_to_blocking sets inbox_blocked and writes critical annotation."""
    agent_id = "coder-xyz"
    message_id = "msg-esc-001"

    _write_agent_state(tmp_path, agent_id)
    _msg._pending_escalations[agent_id] = [
        {"message_id": message_id, "from_agent": "lead-abc", "message_type": "query"},
    ]

    _msg.escalate_to_blocking(agent_id, message_id)

    # Verify inbox_blocked is set
    state = json.loads((tmp_path / "agents" / f"{agent_id}.json").read_text())
    assert state["inbox_blocked"] is not None
    assert state["inbox_blocked"]["blocked_by_message_id"] == message_id
    assert state["inbox_blocked"]["from_agent"] == "lead-abc"

    # Verify critical annotation was written
    ann_file = tmp_path / "annotations" / f"{agent_id}.jsonl"
    assert ann_file.exists()
    lines = ann_file.read_text().strip().splitlines()
    assert len(lines) == 1
    ann = json.loads(lines[0])
    assert ann["priority"] == "critical"
    assert "ESCALATED" in ann["message"]

    # Verify removed from _pending_escalations
    assert all(e["message_id"] != message_id for e in _msg._pending_escalations.get(agent_id, []))
