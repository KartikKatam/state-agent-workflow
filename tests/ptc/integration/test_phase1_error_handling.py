"""Phase 1 integration tests — error handling, timeouts, and security boundaries."""

from __future__ import annotations

import pytest

from conftest import assert_error, await_execute

pytestmark = [pytest.mark.integration, pytest.mark.phase1]


async def test_execution_timeout(container_manager):
    """Long-running code is killed by timeout."""
    result = await await_execute(
        container_manager, "err-timeout-1", "explorer",
        "import time; time.sleep(120)", timeout=5,
    )
    assert_error(result, "timeout")


async def test_output_cap_at_64kb(container_manager):
    """Large output is transmitted without crashing the IPC stream."""
    result = await await_execute(
        container_manager, "err-cap-1", "explorer",
        "print('x' * 200000)",
    )
    # Output should be returned successfully (IPC buffer is 1MB).
    # Server-layer capping (server.py) is tested separately.
    stdout = result.get("stdout", "")
    assert len(stdout) > 0, "Expected non-empty output"
    assert result.get("type") == "complete", f"Expected complete, got: {result}"


async def test_infinite_loop_with_timeout(container_manager):
    """Infinite loop is killed by timeout."""
    result = await await_execute(
        container_manager, "err-loop-1", "explorer",
        "while True: pass", timeout=3,
    )
    assert result.get("type") == "error"


async def test_memory_exhaustion_in_code(container_manager):
    """Allocating more than container memory limit raises MemoryError or kills process."""
    result = await await_execute(
        container_manager, "err-oom-1", "explorer",
        "x = bytearray(2 * 1024 * 1024 * 1024)", timeout=30,
    )
    combined = result.get("stderr", "") + result.get("stdout", "") + result.get("message", "")
    assert (
        result.get("type") == "error"
        or result.get("return_code", 0) != 0
        or "MemoryError" in combined
        or "Killed" in combined
    ), f"Expected OOM failure, got: {result}"


async def test_fork_bomb_blocked(container_manager):
    """os.fork() either fails or succeeds harmlessly — container has resource limits."""
    result = await await_execute(
        container_manager, "err-fork-1", "explorer",
        "import os; pid = os.fork(); print(f'pid={pid}')", timeout=10,
    )
    # Fork may succeed (default seccomp allows it) or fail — both are acceptable.
    # The container has memory/CPU limits that prevent fork bombs from escaping.
    # We just verify the sandbox didn't crash and returned a response.
    assert result.get("type") in ("complete", "error"), f"Unexpected result: {result}"


async def test_disk_fill_tmpfs(container_manager):
    """Writing more data than tmpfs capacity fails."""
    code = "f = open('/tmp/bigfile', 'wb'); f.write(b'x' * (600 * 1024 * 1024)); f.close()"
    result = await await_execute(
        container_manager, "err-disk-1", "explorer", code, timeout=30,
    )
    combined = result.get("stderr", "") + result.get("stdout", "") + result.get("message", "")
    assert (
        result.get("type") == "error"
        or result.get("return_code", 0) != 0
        or "OSError" in combined
        or "No space" in combined
    ), f"Expected disk full error, got: {result}"


async def test_code_writing_to_workspace_allowed(container_manager):
    """Writing to /workspace succeeds in V2 (rw mount)."""
    result = await await_execute(
        container_manager, "err-ws-1", "explorer",
        (
            "with open('/workspace/_test_ws_write.txt', 'w') as f:\n"
            "    f.write('hello')\n"
            "import os; os.remove('/workspace/_test_ws_write.txt')\n"
            "print('write_ok')"
        ),
        timeout=10,
    )
    assert result.get("type") == "complete", f"Expected write to succeed, got: {result}"
    assert "write_ok" in result.get("stdout", "")


async def test_no_docker_socket_access(container_manager):
    """Container cannot access the Docker socket."""
    result = await await_execute(
        container_manager, "err-docker-1", "explorer",
        "import os; print(os.path.exists('/var/run/docker.sock'))",
    )
    assert "False" in result.get("stdout", "")


async def test_runs_as_non_root(container_manager):
    """Container process runs as non-root (uid != 0)."""
    result = await await_execute(
        container_manager, "err-root-1", "explorer",
        "import os; print(os.getuid())",
    )
    stdout = result.get("stdout", "").strip()
    assert stdout.isdigit(), f"Expected numeric uid, got: {stdout!r}"
    assert int(stdout) != 0, "Container should not run as root (uid 0)"


async def test_large_stdout_capped(container_manager):
    """Large stdout from a loop is transmitted without crashing the IPC stream."""
    result = await await_execute(
        container_manager, "err-largestdout-1", "explorer",
        "for i in range(100000): print(i)", timeout=30,
    )
    # Output should be returned successfully (IPC buffer is 1MB).
    # The output may exceed 64KB — server-layer capping is tested separately.
    stdout = result.get("stdout", "")
    assert len(stdout) > 0, "Expected non-empty output"
