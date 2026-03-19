"""Phase 2 integration tests — resource contention and limits."""

from __future__ import annotations

import asyncio
import subprocess
import time

import pytest

from conftest import assert_complete


@pytest.mark.integration
@pytest.mark.phase2
@pytest.mark.slow
async def test_max_containers_limit(container_manager):
    """Eight containers can be created and all function correctly."""
    mgr = container_manager
    agents = [
        ("limit-1", "explorer"),
        ("limit-2", "coder"),
        ("limit-3", "tester"),
        ("limit-4", "auditor"),
        ("limit-5", "researcher"),
        ("limit-6", "strategist"),
        ("limit-7", "explorer"),
        ("limit-8", "coder"),
    ]

    for agent_id, role in agents:
        await mgr.get_or_create_repl(agent_id, role)
        result = await mgr.execute_code(agent_id, "print('ok')", timeout=300)
        assert_complete(result)
        assert "ok" in result["stdout"]

    assert len(mgr._agent_to_container) >= 8, (
        f"Expected 8 agents tracked, got {len(mgr._agent_to_container)}"
    )


@pytest.mark.integration
@pytest.mark.phase2
@pytest.mark.slow
async def test_eight_containers_memory_footprint(container_manager):
    """Eight containers total Docker memory stays under 10GB."""
    mgr = container_manager
    agents = [
        ("mem-1", "explorer"),
        ("mem-2", "coder"),
        ("mem-3", "tester"),
        ("mem-4", "auditor"),
        ("mem-5", "researcher"),
        ("mem-6", "strategist"),
        ("mem-7", "explorer"),
        ("mem-8", "coder"),
    ]

    for agent_id, role in agents:
        await mgr.get_or_create_repl(agent_id, role)
        result = await mgr.execute_code(agent_id, "print('ok')", timeout=120)
        assert_complete(result)

    # Check total memory usage via docker stats
    proc = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    total_mb = 0.0
    for line in proc.stdout.strip().splitlines():
        if not line.startswith("ptc-"):
            continue
        # Format: "ptc-xxx 123.4MiB / 512MiB" or "1.2GiB / 2GiB"
        parts = line.split()
        if len(parts) >= 2:
            usage = parts[1]
            if "GiB" in usage:
                total_mb += float(usage.replace("GiB", "")) * 1024
            elif "MiB" in usage:
                total_mb += float(usage.replace("MiB", ""))
            elif "KiB" in usage:
                total_mb += float(usage.replace("KiB", "")) / 1024

    total_gb = total_mb / 1024
    assert total_gb < 10.0, f"Total memory {total_gb:.2f}GB exceeds 10GB limit"


@pytest.mark.integration
@pytest.mark.phase2
@pytest.mark.slow
async def test_cpu_fair_sharing_under_load(container_manager):
    """Four agents running CPU-intensive work all complete within reasonable time."""
    mgr = container_manager
    agents = [
        ("cpu-1", "explorer"),
        ("cpu-2", "coder"),
        ("cpu-3", "tester"),
        ("cpu-4", "auditor"),
    ]

    for agent_id, role in agents:
        await mgr.get_or_create_repl(agent_id, role)

    code = "s = sum(range(10**7)); print('done')"
    start = time.monotonic()
    results = await asyncio.gather(
        *[mgr.execute_code(agent_id, code, timeout=60) for agent_id, _ in agents]
    )
    elapsed = time.monotonic() - start

    for i, result in enumerate(results):
        assert_complete(result)
        assert "done" in result["stdout"], f"Agent {agents[i][0]} missing 'done'"

    assert elapsed < 60.0, f"CPU-intensive work took {elapsed:.1f}s, expected < 60s"


@pytest.mark.integration
@pytest.mark.phase2
@pytest.mark.slow
async def test_system_stable_under_full_load(container_manager):
    """Eight agents survive 3 rounds of concurrent execution without crashes."""
    mgr = container_manager
    agents = [
        ("stable-1", "explorer"),
        ("stable-2", "coder"),
        ("stable-3", "tester"),
        ("stable-4", "auditor"),
        ("stable-5", "researcher"),
        ("stable-6", "strategist"),
        ("stable-7", "explorer"),
        ("stable-8", "coder"),
    ]

    # Create all REPLs
    for agent_id, role in agents:
        await mgr.get_or_create_repl(agent_id, role)

    # 3 rounds of concurrent execution
    for round_num in range(1, 4):
        code = f"print({round_num})"
        results = await asyncio.gather(
            *[mgr.execute_code(agent_id, code, timeout=60) for agent_id, _ in agents]
        )
        for i, result in enumerate(results):
            assert_complete(result)
            assert str(round_num) in result["stdout"], (
                f"Round {round_num}, agent {agents[i][0]}: "
                f"expected '{round_num}' in stdout, got: {result['stdout']}"
            )
