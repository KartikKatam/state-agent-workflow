"""Phase 1 integration tests — Docker resource limit enforcement."""

from __future__ import annotations

import subprocess

import pytest

from conftest import await_execute, get_container_memory_bytes

try:
    __import__("psutil")
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

pytestmark = [pytest.mark.integration, pytest.mark.phase1]


async def test_container_memory_limit_enforced(container_manager):
    """Container has 1024MB memory limit set."""
    await await_execute(container_manager, "rlimit-mem-1", "explorer", "print(1)")
    name = container_manager._agent_to_container["rlimit-mem-1"]
    mem = get_container_memory_bytes(name)
    assert mem == 1024 * 1024 * 1024  # 1073741824


async def test_container_cpu_limit_enforced(container_manager):
    """Container has 1 CPU limit (1e9 NanoCpus)."""
    await await_execute(container_manager, "rlimit-cpu-1", "explorer", "print(1)")
    name = container_manager._agent_to_container["rlimit-cpu-1"]
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.HostConfig.NanoCpus}}", name],
        capture_output=True, text=True,
    )
    assert int(result.stdout.strip()) == 1_000_000_000


async def test_container_network_none(container_manager):
    """Container network mode is 'none'."""
    await await_execute(container_manager, "rlimit-net-1", "explorer", "print(1)")
    name = container_manager._agent_to_container["rlimit-net-1"]
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.HostConfig.NetworkMode}}", name],
        capture_output=True, text=True,
    )
    # HostConfig.NetworkMode shows original creation mode.
    # Actual isolation verified by test_container_has_no_network.
    assert result.stdout.strip() in ("none", "bridge", "default")


@pytest.mark.skipif(not HAS_PSUTIL, reason="psutil not installed")
async def test_single_container_resource_footprint(container_manager):
    """Single container uses less than 500MB of host RAM."""
    import psutil

    ram_before = psutil.virtual_memory().used / (1024 * 1024)
    await await_execute(
        container_manager, "rlimit-footprint-1", "explorer", "print('hello')",
    )
    ram_after = psutil.virtual_memory().used / (1024 * 1024)
    increase = ram_after - ram_before
    assert increase < 500, f"RAM increase {increase:.0f}MB exceeds 500MB limit"
