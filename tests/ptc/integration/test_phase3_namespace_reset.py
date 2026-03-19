"""Phase 3 integration tests — namespace reset across handoffs."""

from __future__ import annotations

import pytest

from conftest import assert_complete

pytestmark = [pytest.mark.integration, pytest.mark.phase3]


async def test_handoff_namespace_is_fresh(container_manager):
    """After handoff, new agent has a clean namespace (old vars gone)."""
    mgr = container_manager
    await mgr.get_or_create_repl("ns-reset-a", "explorer")

    # Agent A sets a variable
    r1 = await mgr.execute_code("ns-reset-a", "x = 42", 10)
    assert_complete(r1)

    # Handoff to agent B
    await mgr.handle_handoff("ns-reset-a", "ns-reset-b")

    # Agent B tries to read x — should get NameError
    r2 = await mgr.execute_code("ns-reset-b", "print(x)", 10)
    output = r2.get("message", "") + r2.get("stderr", "") + r2.get("stdout", "")
    assert "NameError" in output, f"Expected NameError, got: {r2}"


async def test_handoff_namespace_reset_does_not_lose_packages(container_manager):
    """Packages survive handoff since container persists."""
    mgr = container_manager
    await mgr.get_or_create_repl("ns-pkg-a", "explorer")

    # Verify a pre-installed package works for agent A
    r1 = await mgr.execute_code("ns-pkg-a", "import networkx; print('ok')", 30)
    assert_complete(r1)
    assert "ok" in r1["stdout"]

    # Handoff to agent B
    await mgr.handle_handoff("ns-pkg-a", "ns-pkg-b")

    # Agent B can still import the package
    r2 = await mgr.execute_code("ns-pkg-b", "import networkx; print('ok')", 30)
    assert_complete(r2)
    assert "ok" in r2["stdout"]


async def test_explicit_namespace_reset(container_manager):
    """reset_namespace clears user-defined variables."""
    mgr = container_manager
    await mgr.get_or_create_repl("ns-explicit", "explorer")

    # Set a variable
    r1 = await mgr.execute_code("ns-explicit", "x = 42", 10)
    assert_complete(r1)

    # Reset namespace
    r2 = await mgr.reset_namespace("ns-explicit")
    assert_complete(r2)

    # Variable should be gone
    r3 = await mgr.execute_code("ns-explicit", "print(x)", 10)
    output = r3.get("message", "") + r3.get("stderr", "") + r3.get("stdout", "")
    assert "NameError" in output, f"Expected NameError after reset, got: {r3}"
