"""Phase 3 integration tests — container persistence across REPL lifecycle."""

from __future__ import annotations

import subprocess

import pytest

from conftest import assert_complete

pytestmark = [pytest.mark.integration, pytest.mark.phase3]


async def test_container_survives_repl_stop(container_manager):
    """Stopping a REPL does not destroy the container."""
    mgr = container_manager
    await mgr.get_or_create_repl("persist-stop", "explorer")
    container_name = mgr._agent_to_container["persist-stop"]

    await mgr.stop_repl("persist-stop")

    # Container should still be running
    result = subprocess.run(
        ["docker", "ps", "--filter", f"name={container_name}", "-q"],
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip(), f"Container {container_name} not running after stop_repl"


async def test_container_filesystem_persists(container_manager):
    """Filesystem state survives REPL stop and handoff."""
    mgr = container_manager
    await mgr.get_or_create_repl("persist-fs-a", "explorer")

    # Write a marker file
    r1 = await mgr.execute_code(
        "persist-fs-a",
        "with open('/tmp/marker.txt', 'w') as f: f.write('hello-persist')",
        10,
    )
    assert_complete(r1)

    # Handoff to new agent (stops old REPL, starts new in same container)
    await mgr.handle_handoff("persist-fs-a", "persist-fs-b")

    # New agent reads the marker file
    r2 = await mgr.execute_code(
        "persist-fs-b",
        "with open('/tmp/marker.txt') as f: print(f.read())",
        10,
    )
    assert_complete(r2)
    assert "hello-persist" in r2["stdout"], f"Expected 'hello-persist', got: {r2['stdout']}"


async def test_multiple_handoffs_same_container(container_manager):
    """Multiple sequential handoffs all reuse the same container."""
    mgr = container_manager
    await mgr.get_or_create_repl("chain-a", "explorer")
    original_container = mgr._agent_to_container["chain-a"]

    # Chain: A -> B -> C -> D
    for old, new in [("chain-a", "chain-b"), ("chain-b", "chain-c"), ("chain-c", "chain-d")]:
        await mgr.handle_handoff(old, new)
        assert mgr._agent_to_container[new] == original_container, (
            f"Handoff {old}->{new} changed container"
        )

    # Final agent D can execute code
    r = await mgr.execute_code("chain-d", "print(1 + 1)", 10)
    assert_complete(r)
    assert "2" in r["stdout"]
