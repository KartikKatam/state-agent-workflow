"""Tests for namespace persistence across executions."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.phase1]


async def test_namespace_persists_across_executions(container_manager):
    """Variables set in one execution are available in the next."""
    mgr = container_manager
    agent = "ns-persist-1"
    await mgr.get_or_create_repl(agent, "explorer")

    # Set variable
    await mgr.execute_code(agent, "x = 42", 30)
    # Read it back in a new execution
    r2 = await mgr.execute_code(agent, "print(x)", 30)
    assert "42" in r2.get("stdout", "")


async def test_namespace_accumulates_data(container_manager):
    """Data accumulates across multiple executions."""
    mgr = container_manager
    agent = "ns-accum-1"
    await mgr.get_or_create_repl(agent, "explorer")

    await mgr.execute_code(agent, "data = []", 30)
    await mgr.execute_code(agent, "data.append(1)", 30)
    r = await mgr.execute_code(agent, "data.append(2); print(data)", 30)
    assert "[1, 2]" in r.get("stdout", "")


async def test_namespace_survives_error(container_manager):
    """Namespace persists even after an error in code."""
    mgr = container_manager
    agent = "ns-survive-1"
    await mgr.get_or_create_repl(agent, "explorer")

    await mgr.execute_code(agent, "x = 99", 30)
    await mgr.execute_code(agent, "1/0", 30)  # Error
    r = await mgr.execute_code(agent, "print(x)", 30)
    assert "99" in r.get("stdout", "")


async def test_namespace_reset_clears_all(container_manager):
    """reset_namespace clears all variables."""
    mgr = container_manager
    agent = "ns-reset-1"
    await mgr.get_or_create_repl(agent, "explorer")

    await mgr.execute_code(agent, "x = 42", 30)
    await mgr.reset_namespace(agent)
    r = await mgr.execute_code(agent, "print(x)", 30)
    # Should get NameError
    assert r.get("type") == "error" or "NameError" in r.get("stderr", "") + r.get(
        "stdout", ""
    )
