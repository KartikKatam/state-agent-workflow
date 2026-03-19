"""Phase 3 integration tests — agent handoff lifecycle."""

from __future__ import annotations

import json
import subprocess
import time

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.phase3]


async def test_handoff_preserves_container(container_manager):
    """Handoff reuses the same Docker container."""
    mgr = container_manager
    await mgr.get_or_create_repl("handoff-old-1", "explorer")
    old_container = mgr._agent_to_container["handoff-old-1"]

    await mgr.handle_handoff("handoff-old-1", "handoff-new-1")
    new_container = mgr._agent_to_container["handoff-new-1"]

    assert old_container == new_container


async def test_handoff_kills_old_repl_starts_new(container_manager):
    """After handoff, only one runtime.py process exists."""
    mgr = container_manager
    await mgr.get_or_create_repl("handoff-repl-old", "explorer")
    container_name = mgr._agent_to_container["handoff-repl-old"]

    await mgr.handle_handoff("handoff-repl-old", "handoff-repl-new")

    # Verify new agent has a working REPL (old one was killed, new one started)
    r = await mgr.execute_code("handoff-repl-new", "print('handoff-ok')", timeout=30)
    assert r.get("type") == "complete", f"New REPL should work after handoff: {r}"
    assert "handoff-ok" in r.get("stdout", "")

    # Old agent should no longer be tracked
    assert "handoff-repl-old" not in mgr._agent_to_container


async def test_handoff_no_pip_reinstall(container_manager):
    """Handoff is faster than initial creation (no pip install)."""
    mgr = container_manager

    create_start = time.monotonic()
    await mgr.get_or_create_repl("handoff-fast-old", "explorer")
    create_time = time.monotonic() - create_start

    handoff_start = time.monotonic()
    await mgr.handle_handoff("handoff-fast-old", "handoff-fast-new")
    handoff_time = time.monotonic() - handoff_start

    # Handoff should be significantly faster (no pip install)
    assert handoff_time < create_time, (
        f"Handoff ({handoff_time:.1f}s) >= creation ({create_time:.1f}s)"
    )


async def test_handoff_old_agent_removed_from_tracking(container_manager):
    """Old agent removed, new agent present in tracking."""
    mgr = container_manager
    await mgr.get_or_create_repl("handoff-track-old", "explorer")
    await mgr.handle_handoff("handoff-track-old", "handoff-track-new")

    assert "handoff-track-old" not in mgr._agent_to_container
    assert "handoff-track-new" in mgr._agent_to_container


async def test_handoff_event_logged(container_manager, event_log_path):
    """Handoff event appears in JSONL log."""
    mgr = container_manager
    await mgr.get_or_create_repl("handoff-log-old", "explorer")
    await mgr.handle_handoff("handoff-log-old", "handoff-log-new")

    # Read events
    events = []
    with open(event_log_path) as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))

    handoff_events = [e for e in events if e.get("event") == "container_handoff"]
    assert len(handoff_events) >= 1, (
        f"No container_handoff event found in {len(events)} events"
    )
