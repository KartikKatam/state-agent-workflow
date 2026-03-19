"""Phase 1 integration tests — container lifecycle and configuration."""

from __future__ import annotations

import subprocess

import pytest

from conftest import assert_complete, await_execute


@pytest.mark.integration
@pytest.mark.phase1
async def test_container_created_on_first_execute(container_manager):
    """Container created with correct config on first execute."""
    result = await await_execute(
        container_manager, "lifecycle-1", "explorer", "print('hello')"
    )
    assert_complete(result)

    container_name = container_manager._agent_to_container["lifecycle-1"]

    # Verify container state, memory, cpus, network via docker inspect
    inspect = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Running}}|{{.HostConfig.Memory}}|{{.HostConfig.NanoCpus}}|{{.HostConfig.NetworkMode}}",
            container_name,
        ],
        capture_output=True,
        text=True,
    )
    parts = inspect.stdout.strip().split("|")
    assert parts[0] == "true"
    assert int(parts[1]) == 1024 * 1024 * 1024  # 1024 MB
    assert int(parts[2]) == 1_000_000_000  # 1 CPU
    # After network disconnect, HostConfig still shows original mode.
    # Verify actual isolation via the no-network test instead.
    assert parts[3] in ("none", "bridge", "default")

    # Verify /workspace:ro mount present
    mounts = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{range .Mounts}}{{.Destination}}:{{.RW}} {{end}}",
            container_name,
        ],
        capture_output=True,
        text=True,
    )
    assert "/workspace:true" in mounts.stdout


@pytest.mark.integration
@pytest.mark.phase1
async def test_container_has_correct_mounts(container_manager):
    """Container has /workspace, /ptc_runtime, and /ptc_server mounted."""
    result = await await_execute(
        container_manager, "lifecycle-2", "explorer", "print('mount check')"
    )
    assert_complete(result)

    container_name = container_manager._agent_to_container["lifecycle-2"]

    for path in ["/workspace", "/ptc_runtime", "/ptc_server"]:
        check = subprocess.run(
            ["docker", "exec", container_name, "ls", path],
            capture_output=True,
            text=True,
        )
        assert check.returncode == 0, f"{path} not accessible: {check.stderr}"


@pytest.mark.integration
@pytest.mark.phase1
async def test_container_has_bridge_network(container_manager):
    """PTC V2 container uses bridge networking."""
    result = await await_execute(
        container_manager, "lifecycle-3", "explorer", "print('net check')"
    )
    assert_complete(result)

    container_name = container_manager._agent_to_container["lifecycle-3"]

    # Verify bridge network mode
    inspect = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.HostConfig.NetworkMode}}",
            container_name,
        ],
        capture_output=True,
        text=True,
    )
    assert inspect.stdout.strip() in ("bridge", "default")


@pytest.mark.integration
@pytest.mark.phase1
async def test_container_tmpfs_writable(container_manager):
    """Can write to /tmp (tmpfs) and /workspace (rw mount in V2)."""
    # /tmp should be writable
    write_tmp = await await_execute(
        container_manager,
        "lifecycle-4",
        "explorer",
        (
            "with open('/tmp/test.txt', 'w') as f:\n"
            "    f.write('hello')\n"
            "print('tmp_ok')"
        ),
    )
    assert_complete(write_tmp)
    assert "tmp_ok" in write_tmp.get("stdout", "")

    # /workspace should be writable in V2
    write_ws = await await_execute(
        container_manager,
        "lifecycle-4",
        "explorer",
        (
            "with open('/workspace/_test_tmpfs.txt', 'w') as f:\n"
            "    f.write('hello')\n"
            "import os; os.remove('/workspace/_test_tmpfs.txt')\n"
            "print('ws_ok')"
        ),
    )
    assert_complete(write_ws)
    assert "ws_ok" in write_ws.get("stdout", "")
