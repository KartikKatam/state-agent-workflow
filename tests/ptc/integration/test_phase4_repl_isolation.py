"""Phase 4 integration tests — REPL isolation between parent and sub-agents."""

from __future__ import annotations

import asyncio

import pytest

from conftest import assert_complete

pytestmark = [pytest.mark.integration, pytest.mark.phase4]


async def test_parent_and_sub_agent_independent_namespaces(container_manager):
    """Parent and sub-agent have independent Python namespaces."""
    mgr = container_manager
    # Set up parent + sub in same container
    await mgr.get_or_create_repl("ns-parent", "explorer")
    await mgr.register_parent("ns-sub", "ns-parent")
    await mgr.get_or_create_repl("ns-sub", "explorer")

    # Parent sets x
    r1 = await mgr.execute_code("ns-parent", "x = 'parent'", timeout=30)
    assert_complete(r1)

    # Sub sets x to a different value
    r2 = await mgr.execute_code("ns-sub", "x = 'child'", timeout=30)
    assert_complete(r2)

    # Parent's x is still "parent"
    r3 = await mgr.execute_code("ns-parent", "print(x)", timeout=30)
    assert_complete(r3)
    assert "parent" in r3["stdout"], f"Expected 'parent', got: {r3['stdout']}"


async def test_sub_agent_crash_does_not_affect_parent(container_manager):
    """Exception in sub-agent REPL does not affect parent REPL."""
    mgr = container_manager
    await mgr.get_or_create_repl("crash-parent", "explorer")
    await mgr.register_parent("crash-sub", "crash-parent")
    await mgr.get_or_create_repl("crash-sub", "explorer")

    # Sub crashes with ZeroDivisionError
    r1 = await mgr.execute_code("crash-sub", "1/0", timeout=30)
    assert r1.get("type") == "error" or "ZeroDivisionError" in (
        r1.get("stderr", "") + r1.get("stdout", "")
    )

    # Parent still works fine
    r2 = await mgr.execute_code("crash-parent", "print(1)", timeout=30)
    assert_complete(r2)
    assert "1" in r2["stdout"]


async def test_parent_and_sub_agent_concurrent_execution(container_manager):
    """Parent and sub-agent can execute code concurrently via asyncio.gather."""
    mgr = container_manager
    await mgr.get_or_create_repl("conc-parent", "explorer")
    await mgr.register_parent("conc-sub", "conc-parent")
    await mgr.get_or_create_repl("conc-sub", "explorer")

    # Execute concurrently
    r_parent, r_sub = await asyncio.gather(
        mgr.execute_code("conc-parent", "import time; time.sleep(0.5); print('parent-done')", timeout=30),
        mgr.execute_code("conc-sub", "import time; time.sleep(0.5); print('sub-done')", timeout=30),
    )

    assert_complete(r_parent)
    assert_complete(r_sub)
    assert "parent-done" in r_parent["stdout"]
    assert "sub-done" in r_sub["stdout"]


async def test_sub_agent_file_reads_independent(container_manager):
    """Parent and sub-agent can read different files independently."""
    mgr = container_manager
    await mgr.get_or_create_repl("tool-parent", "explorer")
    await mgr.register_parent("tool-sub", "tool-parent")
    await mgr.get_or_create_repl("tool-sub", "explorer")

    # Parent reads /etc/hostname, sub reads /etc/os-release
    r_parent = await mgr.execute_code(
        "tool-parent",
        "print(open('/etc/hostname').read().strip())",
        timeout=30,
    )
    r_sub = await mgr.execute_code(
        "tool-sub",
        "print(open('/etc/os-release').readline().strip())",
        timeout=30,
    )

    assert_complete(r_parent)
    assert_complete(r_sub)
    # Both should return non-empty content
    assert len(r_parent["stdout"].strip()) > 0
    assert len(r_sub["stdout"].strip()) > 0
    # They read different files so content should differ
    assert r_parent["stdout"].strip() != r_sub["stdout"].strip()
