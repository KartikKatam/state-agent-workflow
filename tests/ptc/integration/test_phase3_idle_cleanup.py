"""Phase 3 integration tests — idle container cleanup."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from conftest import assert_complete

pytestmark = [pytest.mark.integration, pytest.mark.phase3]


async def test_idle_container_cleaned_up(container_manager):
    """Container with 0 REPLs and old timestamp is cleaned up."""
    mgr = container_manager
    await mgr.get_or_create_repl("idle-cleanup", "explorer")
    container_name = mgr._agent_to_container["idle-cleanup"]

    # Stop the REPL (container has 0 repls now)
    await mgr.stop_repl("idle-cleanup")
    assert container_name in mgr._containers

    # Backdate last_used_at to trigger cleanup
    mgr._containers[container_name].last_used_at = datetime.now(timezone.utc) - timedelta(
        minutes=10
    )

    await mgr.cleanup_idle_containers(idle_timeout_seconds=1)

    assert container_name not in mgr._containers, (
        f"Idle container {container_name} was not cleaned up"
    )


async def test_active_container_not_cleaned(container_manager):
    """Container with an active REPL is not removed by cleanup."""
    mgr = container_manager
    await mgr.get_or_create_repl("idle-active", "explorer")
    container_name = mgr._agent_to_container["idle-active"]

    # Verify it can execute
    r = await mgr.execute_code("idle-active", "print('alive')", 10)
    assert_complete(r)

    # Run cleanup — should not touch active container
    await mgr.cleanup_idle_containers(idle_timeout_seconds=1)

    assert container_name in mgr._containers, (
        f"Active container {container_name} was incorrectly removed"
    )


async def test_idle_cleanup_removes_correct_container(container_manager):
    """Only the idle container is removed; active ones survive."""
    mgr = container_manager

    # Create 3 agents in different containers
    await mgr.get_or_create_repl("idle-select-1", "explorer")
    await mgr.get_or_create_repl("idle-select-2", "coder")
    await mgr.get_or_create_repl("idle-select-3", "tester")

    c1 = mgr._agent_to_container["idle-select-1"]
    c2 = mgr._agent_to_container["idle-select-2"]
    c3 = mgr._agent_to_container["idle-select-3"]

    # Stop REPL in container 2, backdate it
    await mgr.stop_repl("idle-select-2")
    mgr._containers[c2].last_used_at = datetime.now(timezone.utc) - timedelta(minutes=10)

    await mgr.cleanup_idle_containers(idle_timeout_seconds=1)

    # Only c2 should be removed
    assert c1 in mgr._containers, "Active container c1 was incorrectly removed"
    assert c2 not in mgr._containers, "Idle container c2 was not cleaned up"
    assert c3 in mgr._containers, "Active container c3 was incorrectly removed"
