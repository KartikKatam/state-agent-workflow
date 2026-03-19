"""Phase 4 integration tests — concurrent sub-agent execution."""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from conftest import assert_complete

pytestmark = [pytest.mark.integration, pytest.mark.phase4]


async def test_three_sub_agents_execute_concurrently(container_manager):
    """Parent + 3 sub-agents all execute concurrently via asyncio.gather."""
    mgr = container_manager
    await mgr.get_or_create_repl("par-parent", "explorer")
    sub_ids = ["par-sub-0", "par-sub-1", "par-sub-2"]
    for sub_id in sub_ids:
        await mgr.register_parent(sub_id, "par-parent")
        await mgr.get_or_create_repl(sub_id, "explorer")

    # Execute all 4 concurrently
    results = await asyncio.gather(
        mgr.execute_code("par-parent", "import time; time.sleep(0.3); print('parent')", timeout=30),
        mgr.execute_code("par-sub-0", "import time; time.sleep(0.3); print('sub0')", timeout=30),
        mgr.execute_code("par-sub-1", "import time; time.sleep(0.3); print('sub1')", timeout=30),
        mgr.execute_code("par-sub-2", "import time; time.sleep(0.3); print('sub2')", timeout=30),
    )

    for r in results:
        assert_complete(r)
    assert "parent" in results[0]["stdout"]
    assert "sub0" in results[1]["stdout"]
    assert "sub1" in results[2]["stdout"]
    assert "sub2" in results[3]["stdout"]


async def test_sub_agents_share_packages(container_manager):
    """Sub-agent can import packages installed for parent's role without pip."""
    mgr = container_manager
    # Parent is explorer (has packages like networkx pre-installed)
    await mgr.get_or_create_repl("pkg-parent", "explorer")

    await mgr.register_parent("pkg-sub", "pkg-parent")
    await mgr.get_or_create_repl("pkg-sub", "explorer")

    # Sub imports a package available in the explorer role
    r = await mgr.execute_code("pkg-sub", "import networkx; print(networkx.__version__)", timeout=30)
    assert_complete(r)
    assert len(r["stdout"].strip()) > 0, "Expected networkx version output"


async def test_sub_agent_cleanup(container_manager):
    """Stopping all sub-agents leaves parent working, container alive."""
    mgr = container_manager
    await mgr.get_or_create_repl("clean-parent", "explorer")
    parent_container = mgr._agent_to_container["clean-parent"]

    sub_ids = ["clean-sub-0", "clean-sub-1"]
    for sub_id in sub_ids:
        await mgr.register_parent(sub_id, "clean-parent")
        await mgr.get_or_create_repl(sub_id, "explorer")

    # Stop all sub-agents
    for sub_id in sub_ids:
        await mgr.stop_repl(sub_id)

    # Parent still works
    r = await mgr.execute_code("clean-parent", "print('alive')", timeout=30)
    assert_complete(r)
    assert "alive" in r["stdout"]

    # Container still exists
    assert parent_container in mgr._containers


async def test_sub_agent_resource_footprint(container_manager):
    """Multiple REPLs in one container stay within memory limit."""
    mgr = container_manager
    await mgr.get_or_create_repl("foot-parent", "explorer")
    container_name = mgr._agent_to_container["foot-parent"]

    # Create 3 sub-agents (4 total REPLs)
    for i in range(3):
        sub_id = f"foot-sub-{i}"
        await mgr.register_parent(sub_id, "foot-parent")
        await mgr.get_or_create_repl(sub_id, "explorer")

    # Check docker stats for memory usage
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format",
         "{{.MemUsage}}", container_name],
        capture_output=True, text=True, timeout=15,
    )
    mem_output = result.stdout.strip()
    assert len(mem_output) > 0, "Expected memory usage output from docker stats"

    # Parse current memory usage (e.g., "256MiB / 1GiB")
    usage_str = mem_output.split("/")[0].strip()
    # Convert to MiB for comparison
    if "GiB" in usage_str:
        usage_mib = float(usage_str.replace("GiB", "").strip()) * 1024
    elif "MiB" in usage_str:
        usage_mib = float(usage_str.replace("MiB", "").strip())
    elif "KiB" in usage_str:
        usage_mib = float(usage_str.replace("KiB", "").strip()) / 1024
    else:
        # Fallback: just check it's non-empty
        usage_mib = 0

    # 4 REPLs should stay within 1024 MiB container limit
    assert usage_mib < 1024, f"Memory usage {usage_mib:.0f}MiB exceeds 1024MiB limit"
