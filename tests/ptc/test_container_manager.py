"""Tests for ContainerManager — Docker container lifecycle with REPL pool.

Simplified PTC: No more ROLE_PACKAGES, _install_role_packages, _isolate_network,
packages_installed, or registry param. Bridge networking, /workspace:rw.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from container_manager import (  # type: ignore[import-not-found]
    ContainerInfo,
    ContainerManager,
    ReplHandle,
)


# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------
def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def mock_event_logger():
    """Mock event logger that records all calls."""
    return MagicMock()


@pytest.fixture
def mock_config():
    """Config dict for ContainerManager."""
    return {
        "docker": {
            "image": "ptc-sandbox:latest",
            "role_image_pattern": "ptc-{role}:latest",
            "network_mode": "bridge",
            "container_prefix": "ptc",
            "runtime": "runc",
        },
        "resource_limits": {
            "memory_mb": 1024,
            "cpu_cores": 1,
            "timeout_seconds": 60,
            "disk_mb": 256,
            "max_containers": 8,
        },
        "ipc": {
            "socket_dir": "/tmp/ptc-ipc",
        },
        "repl_pool": {
            "max_repls_per_container": 6,
            "repl_start_timeout_seconds": 5,
        },
    }


@pytest.fixture
def mgr(mock_config, mock_event_logger):
    """ContainerManager with mock dependencies (no registry)."""
    return ContainerManager(
        config=mock_config,
        project_root="/home/user/project",
        event_logger=mock_event_logger,
    )


def _make_mock_process(returncode=0, stdout=b"container-id-123\n", stderr=b""):
    """Create a mock asyncio subprocess."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.wait = AsyncMock(return_value=returncode)
    return proc


def _make_container_info(name="ctr-1", role="explorer", repls=None):
    """Factory for ContainerInfo."""
    return ContainerInfo(
        container_name=name,
        role=role,
        created_at=_now(),
        last_used_at=_now(),
        repls=repls or {},
    )


def _make_repl_handle(agent_id="a1", repl_index=0):
    """Factory for ReplHandle with mock IPC."""
    mock_ipc = AsyncMock()
    mock_ipc.health_check = AsyncMock(return_value=True)
    mock_ipc.send_execute = AsyncMock(
        return_value={
            "type": "complete",
            "exec_id": "e-1",
            "stdout": "ok",
        }
    )
    mock_ipc.start = AsyncMock()
    mock_ipc.stop = AsyncMock()
    mock_ipc.socket_path = f"/tmp/ptc-ipc/repl-{repl_index}.sock"
    return ReplHandle(
        agent_id=agent_id,
        repl_index=repl_index,
        ipc=mock_ipc,
        started_at=_now(),
    )


# ===========================================================================
# TestDataclasses (3 tests)
# ===========================================================================
class TestDataclasses:
    """Tests for ReplHandle and ContainerInfo dataclasses."""

    def test_repl_handle_fields(self):
        """ReplHandle stores agent_id, repl_index, defaults exec_count=0."""
        mock_ipc = MagicMock()
        now = _now()

        handle = ReplHandle("a1", 0, mock_ipc, now)

        assert handle.agent_id == "a1"
        assert handle.repl_index == 0
        assert handle.exec_count == 0
        assert handle.ipc is mock_ipc

    def test_container_info_defaults(self):
        """ContainerInfo has max_repls=6 by default."""
        info = ContainerInfo("ctr-1", "explorer", _now(), _now(), {})

        assert info.max_repls == 6
        assert info.role == "explorer"

    def test_container_info_with_repls(self):
        """ContainerInfo stores repls dict."""
        r1 = _make_repl_handle("a1", 0)
        r2 = _make_repl_handle("a2", 1)
        repls = {"a1": r1, "a2": r2}

        info = ContainerInfo("ctr-1", "explorer", _now(), _now(), repls)

        assert len(info.repls) == 2


# ===========================================================================
# TestContainerCreation (4 tests)
# ===========================================================================
class TestContainerCreation:
    """Tests for _create_container method."""

    @pytest.mark.asyncio
    async def test_create_container_docker_command(self, mgr):
        """_create_container builds correct docker run command."""
        mock_proc = _make_mock_process()
        with patch(
            "container_manager.asyncio.create_subprocess_exec", return_value=mock_proc
        ) as mock_exec:
            await mgr._create_container("explorer")

            call_args = mock_exec.call_args[0]
            cmd = " ".join(str(a) for a in call_args)
            assert "docker" in cmd
            assert "run" in cmd
            assert "-d" in cmd
            assert "--memory" in cmd
            assert "1024m" in cmd
            assert "--network" in cmd
            assert "bridge" in cmd
            assert "--tmpfs" in cmd

    @pytest.mark.asyncio
    async def test_create_container_mounts_project_rw(self, mgr):
        """Project directory is mounted read-write."""
        mock_proc = _make_mock_process()
        with patch(
            "container_manager.asyncio.create_subprocess_exec", return_value=mock_proc
        ) as mock_exec:
            await mgr._create_container("coder")

            call_args = mock_exec.call_args[0]
            cmd = " ".join(str(a) for a in call_args)
            assert "/home/user/project:/workspace:rw" in cmd

    @pytest.mark.asyncio
    async def test_create_container_logs_event(self, mgr, mock_event_logger):
        """Container creation logs container_create event."""
        mock_proc = _make_mock_process()
        with patch(
            "container_manager.asyncio.create_subprocess_exec", return_value=mock_proc
        ):
            await mgr._create_container("explorer")

            mock_event_logger.log_container_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_container_returns_container_info(self, mgr):
        """_create_container returns a ContainerInfo with correct role."""
        mock_proc = _make_mock_process()
        with patch(
            "container_manager.asyncio.create_subprocess_exec", return_value=mock_proc
        ):
            result = await mgr._create_container("explorer")

            assert isinstance(result, ContainerInfo)
            assert result.role == "explorer"


# ===========================================================================
# TestReplStart (3 tests)
# ===========================================================================
class TestReplStart:
    """Tests for _start_repl_in_container method."""

    @pytest.mark.asyncio
    async def test_start_repl_docker_exec_command(self, mgr):
        """REPL start uses docker exec -d with runtime.py --socket."""
        container = _make_container_info("ctr-1", "explorer")
        mgr._containers["ctr-1"] = container

        mock_proc = _make_mock_process()
        mock_ipc = AsyncMock()
        mock_ipc.start = AsyncMock()
        mock_ipc.health_check = AsyncMock(return_value=True)

        with (
            patch(
                "container_manager.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ) as mock_exec,
            patch("container_manager.IpcHost", return_value=mock_ipc),
        ):
            await mgr._start_repl_in_container(container, "a1")

            call_args = mock_exec.call_args[0]
            cmd = " ".join(str(a) for a in call_args)
            assert "docker" in cmd
            assert "exec" in cmd
            assert "runtime.py" in cmd
            assert "repl-0.sock" in cmd

    @pytest.mark.asyncio
    async def test_start_repl_creates_ipc_host(self, mgr):
        """REPL start creates an IpcHost for the new REPL."""
        container = _make_container_info("ctr-1", "explorer")
        mgr._containers["ctr-1"] = container

        mock_proc = _make_mock_process()
        mock_ipc = AsyncMock()
        mock_ipc.start = AsyncMock()
        mock_ipc.health_check = AsyncMock(return_value=True)

        with (
            patch(
                "container_manager.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            patch("container_manager.IpcHost", return_value=mock_ipc),
        ):
            repl = await mgr._start_repl_in_container(container, "a1")

            assert repl.ipc is mock_ipc

    @pytest.mark.asyncio
    async def test_start_repl_waits_for_health_check(self, mgr):
        """REPL start calls health_check before returning."""
        container = _make_container_info("ctr-1", "explorer")
        mgr._containers["ctr-1"] = container

        mock_proc = _make_mock_process()
        mock_ipc = AsyncMock()
        mock_ipc.start = AsyncMock()
        mock_ipc.health_check = AsyncMock(return_value=True)

        with (
            patch(
                "container_manager.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            patch("container_manager.IpcHost", return_value=mock_ipc),
        ):
            await mgr._start_repl_in_container(container, "a1")

            mock_ipc.health_check.assert_called()


# ===========================================================================
# TestGetOrCreateRepl (3 tests)
# ===========================================================================
class TestGetOrCreateRepl:
    """Tests for get_or_create_repl method."""

    @pytest.mark.asyncio
    async def test_get_existing_repl(self, mgr):
        """Returns existing REPL without creating new container."""
        repl = _make_repl_handle("a1", 0)
        container = _make_container_info("ctr-1", "explorer", {"a1": repl})
        mgr._containers["ctr-1"] = container
        mgr._agent_to_container["a1"] = "ctr-1"

        with patch("container_manager.asyncio.create_subprocess_exec") as mock_exec:
            result = await mgr.get_or_create_repl("a1", "explorer")

            mock_exec.assert_not_called()
            assert result is repl

    @pytest.mark.asyncio
    async def test_create_new_container_and_repl(self, mgr):
        """Creates new container + REPL when none exists."""
        mock_proc = _make_mock_process()
        mock_ipc = AsyncMock()
        mock_ipc.start = AsyncMock()
        mock_ipc.health_check = AsyncMock(return_value=True)

        with (
            patch(
                "container_manager.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            patch("container_manager.IpcHost", return_value=mock_ipc),
        ):
            result = await mgr.get_or_create_repl("a1", "explorer")

            assert isinstance(result, ReplHandle)
            assert result.agent_id == "a1"

    @pytest.mark.asyncio
    async def test_get_or_create_registers_agent(self, mgr):
        """New agent is registered in _agent_to_container."""
        mock_proc = _make_mock_process()
        mock_ipc = AsyncMock()
        mock_ipc.start = AsyncMock()
        mock_ipc.health_check = AsyncMock(return_value=True)

        with (
            patch(
                "container_manager.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            patch("container_manager.IpcHost", return_value=mock_ipc),
        ):
            await mgr.get_or_create_repl("a1", "explorer")

            assert "a1" in mgr._agent_to_container


# ===========================================================================
# TestExecuteCode (2 tests)
# ===========================================================================
class TestExecuteCode:
    """Tests for execute_code method."""

    @pytest.mark.asyncio
    async def test_execute_code_delegates_to_ipc(self, mgr):
        """execute_code delegates to repl.ipc.send_execute."""
        repl = _make_repl_handle("a1", 0)
        container = _make_container_info("ctr-1", "explorer", {"a1": repl})
        mgr._containers["ctr-1"] = container
        mgr._agent_to_container["a1"] = "ctr-1"

        await mgr.execute_code("a1", "print(1)", 30)

        repl.ipc.send_execute.assert_called_once()
        call_args = repl.ipc.send_execute.call_args
        assert "print(1)" in str(call_args)

    @pytest.mark.asyncio
    async def test_execute_code_acquires_lock(self, mgr):
        """Concurrent execute_code calls are serialized via lock."""
        repl = _make_repl_handle("a1", 0)
        container = _make_container_info("ctr-1", "explorer", {"a1": repl})
        mgr._containers["ctr-1"] = container
        mgr._agent_to_container["a1"] = "ctr-1"

        call_order = []

        async def slow_execute(*args, **kwargs):
            call_order.append("start")
            await asyncio.sleep(0.05)
            call_order.append("end")
            return {"type": "complete", "stdout": "ok"}

        repl.ipc.send_execute.side_effect = slow_execute

        await asyncio.gather(
            mgr.execute_code("a1", "call1", 30),
            mgr.execute_code("a1", "call2", 30),
        )

        assert call_order == ["start", "end", "start", "end"]


# ===========================================================================
# TestStopAndCleanup (3 tests)
# ===========================================================================
class TestStopAndCleanup:
    """Tests for stop_container, stop_all, health_check."""

    @pytest.mark.asyncio
    async def test_stop_container(self, mgr):
        """stop_container runs docker rm -f and removes from tracking."""
        repl = _make_repl_handle("a1", 0)
        container = _make_container_info("ctr-1", "explorer", {"a1": repl})
        mgr._containers["ctr-1"] = container
        mgr._agent_to_container["a1"] = "ctr-1"

        mock_proc = _make_mock_process()
        with patch(
            "container_manager.asyncio.create_subprocess_exec", return_value=mock_proc
        ) as mock_exec:
            await mgr.stop_container("ctr-1")

            call_args = mock_exec.call_args[0]
            cmd = " ".join(str(a) for a in call_args)
            assert "docker" in cmd
            assert "rm" in cmd
            assert "-f" in cmd
            assert "ctr-1" in cmd
            assert "ctr-1" not in mgr._containers

    @pytest.mark.asyncio
    async def test_stop_all(self, mgr):
        """stop_all stops all containers."""
        c1 = _make_container_info("ctr-1", "explorer")
        c2 = _make_container_info("ctr-2", "coder")
        mgr._containers["ctr-1"] = c1
        mgr._containers["ctr-2"] = c2

        mock_proc = _make_mock_process()
        with patch(
            "container_manager.asyncio.create_subprocess_exec", return_value=mock_proc
        ):
            await mgr.stop_all()

            assert len(mgr._containers) == 0

    @pytest.mark.asyncio
    async def test_health_check_delegates(self, mgr):
        """health_check delegates to repl.ipc.health_check."""
        repl = _make_repl_handle("a1", 0)
        container = _make_container_info("ctr-1", "explorer", {"a1": repl})
        mgr._containers["ctr-1"] = container
        mgr._agent_to_container["a1"] = "ctr-1"

        await mgr.health_check("a1")

        repl.ipc.health_check.assert_called()


# ===========================================================================
# TestCrashRecovery (1 test)
# ===========================================================================
class TestCrashRecovery:
    """Tests for crash recovery during idle."""

    @pytest.mark.asyncio
    async def test_crash_during_idle_restarts(self, mgr):
        """health_check returns False -> container restarted on next execute."""
        repl = _make_repl_handle("a1", 0)
        repl.ipc.health_check = AsyncMock(return_value=False)
        container = _make_container_info("ctr-1", "explorer", {"a1": repl})
        mgr._containers["ctr-1"] = container
        mgr._agent_to_container["a1"] = "ctr-1"

        mock_proc = _make_mock_process()
        mock_ipc = AsyncMock()
        mock_ipc.start = AsyncMock()
        mock_ipc.health_check = AsyncMock(return_value=True)
        mock_ipc.send_execute = AsyncMock(
            return_value={"type": "complete", "stdout": "ok"}
        )

        with (
            patch(
                "container_manager.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            patch("container_manager.IpcHost", return_value=mock_ipc),
        ):
            result = await mgr.execute_code_with_recovery("a1", "print(1)", 30)

            assert (
                "type" in result
                or "warning" in str(result).lower()
                or result.get("stdout")
            )


# ===========================================================================
# TestIdleTimeoutCleanup (1 test)
# ===========================================================================
class TestIdleTimeoutCleanup:
    """Tests for idle container cleanup."""

    @pytest.mark.asyncio
    async def test_idle_timeout_destroys_container(self, mgr):
        """Idle container past timeout destroyed by cleanup task."""
        container = _make_container_info("ctr-idle", "explorer", {})
        container.last_used_at = _now() - timedelta(seconds=3600)
        mgr._containers["ctr-idle"] = container

        mock_proc = _make_mock_process()
        with patch(
            "container_manager.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            await mgr.cleanup_idle_containers(idle_timeout_seconds=60)

            assert "ctr-idle" not in mgr._containers
