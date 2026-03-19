"""Phase 2 integration tests — concurrent execution across agents.

PTC V2 agents use direct Python (open(), subprocess, etc.) instead of
tool stubs. Concurrency tests verify that multiple agents can execute
simultaneously with isolated namespaces.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from conftest import assert_complete


@pytest.mark.integration
@pytest.mark.phase2
async def test_two_agents_execute_concurrently(container_manager):
    """Two agents executing sleep(2) concurrently complete in under 5s wall-clock."""
    mgr = container_manager

    # Create REPLs first
    await mgr.get_or_create_repl("conc-a", "explorer")
    await mgr.get_or_create_repl("conc-b", "coder")

    code = "import time; time.sleep(2); print('done')"
    start = time.monotonic()
    r1, r2 = await asyncio.gather(
        mgr.execute_code("conc-a", code, timeout=30),
        mgr.execute_code("conc-b", code, timeout=30),
    )
    elapsed = time.monotonic() - start

    assert_complete(r1)
    assert_complete(r2)
    assert "done" in r1["stdout"]
    assert "done" in r2["stdout"]
    assert elapsed < 5.0, f"Expected < 5s for concurrent execution, took {elapsed:.1f}s"


@pytest.mark.integration
@pytest.mark.phase2
async def test_four_agents_execute_concurrently(container_manager):
    """Four agents executing concurrently all complete successfully."""
    mgr = container_manager
    agents = [
        ("conc-4a", "explorer"),
        ("conc-4b", "coder"),
        ("conc-4c", "tester"),
        ("conc-4d", "auditor"),
    ]

    # Create REPLs first
    for agent_id, role in agents:
        await mgr.get_or_create_repl(agent_id, role)

    # Execute concurrently
    results = await asyncio.gather(
        *[mgr.execute_code(agent_id, "print('ok')", timeout=30) for agent_id, _ in agents]
    )

    for i, result in enumerate(results):
        assert_complete(result)
        assert "ok" in result["stdout"], f"Agent {agents[i][0]} missing 'ok' in stdout"


@pytest.mark.integration
@pytest.mark.phase2
async def test_concurrent_file_reads_across_agents(container_manager):
    """Two agents reading files simultaneously both get content."""
    mgr = container_manager

    await mgr.get_or_create_repl("conc-file-a", "explorer")
    await mgr.get_or_create_repl("conc-file-b", "coder")

    code = 'data = open("/workspace/_test_server.py").read()\nprint(f"got {len(data)} chars")'

    r1, r2 = await asyncio.gather(
        mgr.execute_code("conc-file-a", code, timeout=60),
        mgr.execute_code("conc-file-b", code, timeout=60),
    )

    assert_complete(r1)
    assert_complete(r2)
    assert "got" in r1["stdout"] and "chars" in r1["stdout"]
    assert "got" in r2["stdout"] and "chars" in r2["stdout"]


@pytest.mark.integration
@pytest.mark.phase2
async def test_concurrent_execute_preserves_namespaces(container_manager):
    """Concurrent variable assignments don't bleed between agents."""
    mgr = container_manager

    await mgr.get_or_create_repl("conc-ns-a", "explorer")
    await mgr.get_or_create_repl("conc-ns-b", "coder")

    # Set variables concurrently
    await asyncio.gather(
        mgr.execute_code("conc-ns-a", 'x = "a"', timeout=30),
        mgr.execute_code("conc-ns-b", 'x = "b"', timeout=30),
    )

    # Verify isolation — each agent sees its own value
    ra, rb = await asyncio.gather(
        mgr.execute_code("conc-ns-a", "print(x)", timeout=30),
        mgr.execute_code("conc-ns-b", "print(x)", timeout=30),
    )

    assert_complete(ra)
    assert_complete(rb)
    assert "a" in ra["stdout"], f"Agent A expected 'a', got: {ra['stdout']}"
    assert "b" in rb["stdout"], f"Agent B expected 'b', got: {rb['stdout']}"
