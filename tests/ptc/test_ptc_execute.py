"""Tests for MCP Server (server.py).

Simplified PTC: No registry/dispatcher. ContainerManager takes no registry param.
ptc_execute no longer checks role validity via registry.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Make PTC server importable
PTC_SERVER_DIR = Path.home() / ".claude" / "mcp" / "ptc-server"
sys.path.insert(0, str(PTC_SERVER_DIR))

from server import _cap_output, mcp  # noqa: E402


# --- Fixtures ---


@pytest.fixture
def mock_container_manager() -> AsyncMock:
    """Mock ContainerManager with all async methods."""
    cm = AsyncMock()
    cm.docker_available = True
    cm.get_or_create_repl = AsyncMock()
    cm.execute_code = AsyncMock(
        return_value={
            "type": "complete",
            "stdout": "42",
            "stderr": "",
            "return_code": 0,
            "namespace_keys": [],
            "namespace_size_kb": 0,
        }
    )
    cm.health_check = AsyncMock(return_value={"healthy": True, "agent_id": "a1"})
    cm.reset_namespace = AsyncMock(return_value={"stdout": "namespace reset"})
    cm.stop_repl = AsyncMock()
    cm.stop_all = AsyncMock()
    cm.get_all_status = MagicMock(return_value={"containers": []})
    return cm


@pytest.fixture
def mock_config() -> dict:
    """Config dict with resource_limits."""
    return {
        "resource_limits": {
            "memory_mb": 1024,
            "timeout_seconds": 60,
            "max_output_bytes": 65536,
            "max_containers": 8,
        },
    }


# --- TestPtcExecute ---


class TestPtcExecute:
    """Tests for ptc_execute MCP tool."""

    @pytest.mark.asyncio
    async def test_ptc_execute_returns_stdout(
        self,
        mock_container_manager: AsyncMock,
        mock_config: dict,
    ) -> None:
        """Verify ptc_execute returns stdout from container execution."""
        with (
            patch("server.container_manager", mock_container_manager),
            patch("server.config", mock_config),
        ):
            from server import ptc_execute

            result = await ptc_execute("a1", "explorer", "print(42)")
            assert "42" in result

    @pytest.mark.asyncio
    async def test_ptc_execute_docker_unavailable(
        self,
        mock_container_manager: AsyncMock,
        mock_config: dict,
    ) -> None:
        """Verify ptc_execute returns error when Docker is unavailable."""
        mock_container_manager.docker_available = False
        with (
            patch("server.container_manager", mock_container_manager),
            patch("server.config", mock_config),
        ):
            from server import ptc_execute

            result = await ptc_execute("a1", "explorer", "x")
            parsed = json.loads(result)
            assert "error" in parsed
            assert "docker" in parsed["error"].lower()

    @pytest.mark.asyncio
    async def test_ptc_execute_respects_timeout(
        self,
        mock_container_manager: AsyncMock,
        mock_config: dict,
    ) -> None:
        """Verify ptc_execute passes explicit timeout to execute_code."""
        with (
            patch("server.container_manager", mock_container_manager),
            patch("server.config", mock_config),
        ):
            from server import ptc_execute

            await ptc_execute("a1", "explorer", "x", timeout=10)
            mock_container_manager.execute_code.assert_called_once()
            call_args = mock_container_manager.execute_code.call_args
            assert call_args[0][2] == 10 or call_args[1].get("timeout") == 10

    @pytest.mark.asyncio
    async def test_ptc_execute_default_timeout_from_config(
        self,
        mock_container_manager: AsyncMock,
        mock_config: dict,
    ) -> None:
        """Verify ptc_execute uses config timeout when timeout=0."""
        with (
            patch("server.container_manager", mock_container_manager),
            patch("server.config", mock_config),
        ):
            from server import ptc_execute

            await ptc_execute("a1", "explorer", "x", timeout=0)
            mock_container_manager.execute_code.assert_called_once()
            call_args = mock_container_manager.execute_code.call_args
            timeout_used = (
                call_args[0][2]
                if len(call_args[0]) > 2
                else call_args[1].get("timeout")
            )
            assert timeout_used == 60

    @pytest.mark.asyncio
    async def test_ptc_execute_error_result(
        self,
        mock_container_manager: AsyncMock,
        mock_config: dict,
    ) -> None:
        """Verify ptc_execute returns error info from error result."""
        mock_container_manager.execute_code = AsyncMock(
            return_value={
                "type": "error",
                "exec_id": "e-1",
                "message": "ZeroDivisionError: division by zero",
                "traceback": "Traceback (most recent call last):\n  File...\nZeroDivisionError",
            }
        )
        with (
            patch("server.container_manager", mock_container_manager),
            patch("server.config", mock_config),
        ):
            from server import ptc_execute

            result = await ptc_execute("a1", "explorer", "1/0")
            assert "error" in result.lower() or "ZeroDivisionError" in result

    @pytest.mark.asyncio
    async def test_ptc_execute_exception_returns_error(
        self,
        mock_container_manager: AsyncMock,
        mock_config: dict,
    ) -> None:
        """Verify ptc_execute handles exceptions from execute_code."""
        mock_container_manager.execute_code.side_effect = RuntimeError(
            "container crashed"
        )
        with (
            patch("server.container_manager", mock_container_manager),
            patch("server.config", mock_config),
        ):
            from server import ptc_execute

            result = await ptc_execute("a1", "explorer", "x")
            assert "ERROR" in result
            assert "container crashed" in result

    @pytest.mark.asyncio
    async def test_module_level_globals_exist(self) -> None:
        """Verify server module has expected globals (no registry)."""
        import server

        assert hasattr(server, "container_manager")
        assert hasattr(server, "event_logger")
        assert hasattr(server, "config")
        assert hasattr(server, "mcp")
        assert not hasattr(server, "registry")


# --- TestOutputCap ---


class TestOutputCap:
    """Tests for _cap_output truncation."""

    def test_output_cap_under_limit(self) -> None:
        """Verify output under limit is unchanged."""
        output = "x" * 1000
        result = _cap_output(output, 65536)
        assert result == output

    def test_output_cap_at_limit(self) -> None:
        """Verify output at exact limit is unchanged."""
        output = "x" * 65536
        result = _cap_output(output, 65536)
        assert result == output

    def test_output_cap_over_limit(self) -> None:
        """Verify output over limit is truncated."""
        output = "x" * 100000
        result = _cap_output(output, 65536)
        assert len(result) <= 65536 + 200
        assert "truncated" in result.lower()


# --- TestPtcStatus ---


class TestPtcStatus:
    """Tests for ptc_status MCP tool."""

    @pytest.mark.asyncio
    async def test_ptc_status_specific_agent(
        self, mock_container_manager: AsyncMock
    ) -> None:
        """Verify ptc_status returns agent health info."""
        with patch("server.container_manager", mock_container_manager):
            from server import ptc_status

            result = await ptc_status(agent_id="a1")
            parsed = json.loads(result)
            assert "healthy" in parsed or "agent_id" in parsed

    @pytest.mark.asyncio
    async def test_ptc_status_all(self, mock_container_manager: AsyncMock) -> None:
        """Verify ptc_status with empty agent_id returns all containers."""
        with patch("server.container_manager", mock_container_manager):
            from server import ptc_status

            result = await ptc_status(agent_id="")
            parsed = json.loads(result)
            assert isinstance(parsed, dict)


# --- TestPtcResetNamespace ---


class TestPtcResetNamespace:
    """Tests for ptc_reset_namespace MCP tool."""

    @pytest.mark.asyncio
    async def test_ptc_reset_namespace(self, mock_container_manager: AsyncMock) -> None:
        """Verify ptc_reset_namespace delegates to container_manager."""
        with patch("server.container_manager", mock_container_manager):
            from server import ptc_reset_namespace

            result = await ptc_reset_namespace("a1")
            parsed = json.loads(result)
            assert "namespace_reset" in str(parsed) or "reset" in str(parsed).lower()
            mock_container_manager.reset_namespace.assert_called_once_with("a1")


# --- TestPtcShutdown ---


class TestPtcShutdown:
    """Tests for ptc_shutdown and ptc_shutdown_all MCP tools."""

    @pytest.mark.asyncio
    async def test_ptc_shutdown(self, mock_container_manager: AsyncMock) -> None:
        """Verify ptc_shutdown stops the agent's container."""
        with patch("server.container_manager", mock_container_manager):
            from server import ptc_shutdown

            result = await ptc_shutdown("a1")
            parsed = json.loads(result)
            assert "stopped" in str(parsed).lower()

    @pytest.mark.asyncio
    async def test_ptc_shutdown_all(self, mock_container_manager: AsyncMock) -> None:
        """Verify ptc_shutdown_all stops all containers."""
        with patch("server.container_manager", mock_container_manager):
            from server import ptc_shutdown_all

            result = await ptc_shutdown_all()
            parsed = json.loads(result)
            assert "stopped" in str(parsed).lower() or "all" in str(parsed).lower()
            mock_container_manager.stop_all.assert_called_once()


# --- TestMcpToolRegistration ---


class TestMcpToolRegistration:
    """Tests for MCP tool registration."""

    @pytest.mark.asyncio
    async def test_ptc_execute_registered(self) -> None:
        """Verify ptc_execute is registered as MCP tool."""
        tools = await mcp.list_tools()
        tool_names = [t.name for t in tools]
        assert "ptc_execute" in tool_names

    @pytest.mark.asyncio
    async def test_old_tools_removed(self) -> None:
        """Verify none of the 15 old tool names are registered."""
        old_tools = [
            "ptc_read_file",
            "ptc_list_dir",
            "ptc_search_code",
            "ptc_analyze_imports",
            "ptc_count_tokens",
            "ptc_run_tests",
            "ptc_run_linter",
            "ptc_write_file",
            "ptc_git_diff_summary",
            "ptc_coverage_summary",
            "ptc_analyze_structure",
            "ptc_web_fetch_summary",
            "ptc_mcp_call_summary",
            "ptc_diff_summary",
            "ptc_list_changes",
        ]
        tools = await mcp.list_tools()
        tool_names = [t.name for t in tools]
        for old in old_tools:
            assert old not in tool_names, f"Old tool '{old}' should be removed"
