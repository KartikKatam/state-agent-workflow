"""Tests for JSONL event observability.

PTC V2 has no tool dispatch, so there are no tool_call_start/complete events.
Tests verify container lifecycle and execution events are properly logged.
"""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.integration]


async def test_events_logged_for_full_lifecycle(container_manager, event_log_path):
    """Full lifecycle produces expected events."""
    mgr = container_manager
    await mgr.get_or_create_repl("obs-lifecycle-1", "explorer")
    await mgr.execute_code("obs-lifecycle-1", "print('hello')", 30)

    events = _read_events(event_log_path)
    event_types = {e["event"] for e in events}

    # Should have container creation and execution events
    assert "container_create" in event_types
    assert "repl_start" in event_types
    assert "exec_start" in event_types or "exec_complete" in event_types


async def test_event_timestamps_monotonically_increasing(
    container_manager, event_log_path
):
    """Event timestamps are in order."""
    mgr = container_manager
    await mgr.get_or_create_repl("obs-ts-1", "explorer")
    await mgr.execute_code("obs-ts-1", "print(1)", 30)
    await mgr.execute_code("obs-ts-1", "print(2)", 30)

    events = _read_events(event_log_path)
    timestamps = [e["ts"] for e in events if "ts" in e]
    for i in range(1, len(timestamps)):
        assert timestamps[i] >= timestamps[i - 1], (
            f"Timestamp out of order: {timestamps[i - 1]} > {timestamps[i]}"
        )


async def test_agent_ids_correct_in_events(container_manager, event_log_path):
    """Agent IDs in events match the agent used."""
    mgr = container_manager
    await mgr.get_or_create_repl("obs-agent-1", "explorer")
    await mgr.execute_code("obs-agent-1", "print('test')", 30)

    events = _read_events(event_log_path)
    agent_events = [e for e in events if e.get("agent_id") == "obs-agent-1"]
    assert len(agent_events) > 0, "No events found for agent obs-agent-1"


async def test_execution_events_present(container_manager, event_log_path):
    """Code execution produces exec_start/exec_complete events."""
    mgr = container_manager
    await mgr.get_or_create_repl("obs-exec-1", "explorer")
    code = "data = open('/workspace/_test_server.py').read(); print(len(data))"
    await mgr.execute_code("obs-exec-1", code, 30)

    events = _read_events(event_log_path)
    event_types = {e["event"] for e in events}
    assert "exec_start" in event_types or "exec_complete" in event_types, (
        f"No execution events found. Events: {event_types}"
    )


async def test_error_events_logged(container_manager, event_log_path):
    """Errors produce error events."""
    mgr = container_manager
    await mgr.get_or_create_repl("obs-err-1", "explorer")
    await mgr.execute_code("obs-err-1", "1/0", 30)

    events = _read_events(event_log_path)
    event_types = {e["event"] for e in events}
    assert "exec_error" in event_types or "exec_complete" in event_types


async def test_events_valid_json(container_manager, event_log_path):
    """Every line in JSONL is valid JSON with required fields."""
    mgr = container_manager
    await mgr.get_or_create_repl("obs-json-1", "explorer")
    await mgr.execute_code("obs-json-1", "print('valid')", 30)

    with open(event_log_path) as f:
        for line_num, line in enumerate(f, 1):
            if not line.strip():
                continue
            event = json.loads(line)  # Will raise if invalid JSON
            assert "ts" in event, f"Line {line_num} missing 'ts'"
            assert "event" in event, f"Line {line_num} missing 'event'"


def _read_events(log_path) -> list[dict]:
    """Read all JSONL events from log file."""
    events = []
    try:
        with open(log_path) as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line))
    except FileNotFoundError:
        pass
    return events
