"""Tests for messaging observability emitters."""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import hooks.utils.event_logger as event_logger
from hooks.utils.event_logger import (
    emit_inbox_blocked,
    emit_inbox_unblocked,
    emit_message_acked,
    emit_message_delivered,
    emit_message_escalated,
    emit_team_created,
    emit_team_joined,
)


@pytest.fixture(autouse=True)
def redirect_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    events_file = tmp_path / "events.jsonl"
    monkeypatch.setattr(event_logger, "EVENTS_FILE", events_file)
    return events_file


def _last_event(events_file: Path) -> dict[str, object]:
    lines = events_file.read_text().strip().split("\n")
    return json.loads(lines[-1])


def test_emit_message_delivered(redirect_events: Path) -> None:
    emit_message_delivered("orch", "coder-01", "task_assign", "msg-123", "blocking")
    entry = _last_event(redirect_events)
    assert entry["event"] == "message_delivered"
    assert entry["from_agent"] == "orch"
    assert entry["to_agent"] == "coder-01"
    assert entry["message_type"] == "task_assign"
    assert entry["message_id"] == "msg-123"
    assert entry["priority"] == "blocking"


def test_emit_message_acked(redirect_events: Path) -> None:
    emit_message_acked("coder-01", "msg-456")
    entry = _last_event(redirect_events)
    assert entry["event"] == "message_acked"
    assert entry["agent"] == "coder-01"
    assert entry["message_id"] == "msg-456"


def test_emit_inbox_blocked(redirect_events: Path) -> None:
    emit_inbox_blocked("coder-01", "msg-789", "orch")
    entry = _last_event(redirect_events)
    assert entry["event"] == "inbox_blocked"
    assert entry["agent"] == "coder-01"
    assert entry["message_id"] == "msg-789"
    assert entry["from_agent"] == "orch"


def test_emit_inbox_unblocked(redirect_events: Path) -> None:
    emit_inbox_unblocked("coder-01", "msg-789")
    entry = _last_event(redirect_events)
    assert entry["event"] == "inbox_unblocked"
    assert entry["agent"] == "coder-01"
    assert entry["message_id"] == "msg-789"


def test_emit_message_escalated(redirect_events: Path) -> None:
    emit_message_escalated("coder-01", "msg-321", "idle_timeout")
    entry = _last_event(redirect_events)
    assert entry["event"] == "message_escalated"
    assert entry["agent"] == "coder-01"
    assert entry["message_id"] == "msg-321"
    assert entry["trigger_state"] == "idle_timeout"


def test_emit_team_created(redirect_events: Path) -> None:
    emit_team_created("team-alpha", "orch")
    entry = _last_event(redirect_events)
    assert entry["event"] == "team_created"
    assert entry["team_id"] == "team-alpha"
    assert entry["creator"] == "orch"


def test_emit_team_joined(redirect_events: Path) -> None:
    emit_team_joined("team-alpha", "coder-01", "chunk-coder")
    entry = _last_event(redirect_events)
    assert entry["event"] == "team_joined"
    assert entry["team_id"] == "team-alpha"
    assert entry["agent"] == "coder-01"
    assert entry["role"] == "chunk-coder"
