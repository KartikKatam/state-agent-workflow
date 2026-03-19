"""Tests for IpcHost — host-side Unix socket server for IPC lifecycle.

Simplified PTC: IpcHost no longer takes tool_dispatcher. send_execute takes
(exec_id, code, timeout) — no role param. No tool dispatch or cancel messages.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ipc_host import IpcHost  # type: ignore[import-not-found]
from ipc_protocol import (  # type: ignore[import-not-found]
    CompleteMsg,
    ErrorMsg,
    PongMsg,
    encode,
)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------
class MockReader:
    """Simulates an asyncio.StreamReader returning pre-queued NDJSON lines."""

    def __init__(self, messages: list | None = None):
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        for msg in messages or []:
            self._queue.put_nowait(encode(msg))

    def feed(self, msg) -> None:
        """Add a message to the read queue."""
        self._queue.put_nowait(encode(msg))

    async def readline(self) -> bytes:
        """Return next queued NDJSON line, or block forever (simulating hang)."""
        return await self._queue.get()


class MockWriter:
    """Simulates an asyncio.StreamWriter capturing written data."""

    def __init__(self):
        self.written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass

    def get_messages(self) -> list[dict]:
        """Parse all written NDJSON lines into dicts."""
        result = []
        for chunk in self.written:
            for line in chunk.decode().strip().splitlines():
                if line:
                    result.append(json.loads(line))
        return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_event_logger():
    """Mock event logger that records all calls."""
    return MagicMock()


@pytest.fixture
def tmp_socket_dir(tmp_path):
    """Temp directory for socket files."""
    d = tmp_path / "sockets"
    d.mkdir()
    return d


@pytest.fixture
def host(tmp_socket_dir, mock_event_logger):
    """IpcHost instance with mock dependencies."""
    socket_path = str(tmp_socket_dir / "repl-0.sock")
    return IpcHost(
        socket_path=socket_path,
        event_logger=mock_event_logger,
    )


def _inject_streams(host: IpcHost, reader: MockReader, writer: MockWriter):
    """Inject mock reader/writer into host, bypassing socket setup."""
    host._reader = reader
    host._writer = writer


# ===========================================================================
# TestIpcHostInit (2 tests)
# ===========================================================================
class TestIpcHostInit:
    """Tests for IpcHost initialization."""

    def test_ipc_host_init(self, tmp_socket_dir, mock_event_logger):
        """IpcHost stores socket_path and event_logger."""
        socket_path = str(tmp_socket_dir / "repl-0.sock")

        h = IpcHost(
            socket_path=socket_path,
            event_logger=mock_event_logger,
        )

        assert h.socket_path == socket_path
        assert h._event_logger is mock_event_logger

    @pytest.mark.asyncio
    async def test_ipc_host_start_creates_socket(self, host):
        """start() creates the Unix socket server."""
        await host.start()

        assert Path(host.socket_path).exists() or host._server is not None

        await host.stop()


# ===========================================================================
# TestIpcHostSendExecute (4 tests)
# ===========================================================================
class TestIpcHostSendExecute:
    """Tests for the send_execute method."""

    @pytest.mark.asyncio
    async def test_send_execute_simple_complete(self, host, mock_event_logger):
        """Simple execute -> complete cycle returns CompleteMsg data."""
        complete = CompleteMsg(exec_id="e-1", stdout="hello\n")
        reader = MockReader([complete])
        writer = MockWriter()
        _inject_streams(host, reader, writer)

        result = await host.send_execute("e-1", "x=1", 60)

        assert result["exec_id"] == "e-1"
        assert result["stdout"] == "hello\n"
        assert result["type"] == "complete"
        sent = writer.get_messages()
        assert any(m["type"] == "execute" and m["exec_id"] == "e-1" for m in sent)

    @pytest.mark.asyncio
    async def test_send_execute_with_error_msg(self, host):
        """Execute that returns ErrorMsg."""
        error = ErrorMsg(
            exec_id="e-1", message="NameError: x", traceback="Traceback..."
        )
        reader = MockReader([error])
        writer = MockWriter()
        _inject_streams(host, reader, writer)

        result = await host.send_execute("e-1", "bad code", 60)

        assert result["type"] == "error"
        assert result["message"] == "NameError: x"

    @pytest.mark.asyncio
    async def test_send_execute_timeout(self, host):
        """Timeout returns error result."""
        reader = MockReader([])
        writer = MockWriter()
        _inject_streams(host, reader, writer)

        result = await host.send_execute("e-1", "while True: pass", 0.1)

        assert result["type"] == "error"
        assert "timeout" in result.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_send_execute_logs_events(self, host, mock_event_logger):
        """send_execute logs exec_start and exec_complete events."""
        complete = CompleteMsg(exec_id="e-1", stdout="ok")
        reader = MockReader([complete])
        writer = MockWriter()
        _inject_streams(host, reader, writer)

        await host.send_execute("e-1", "x=1", 60)

        mock_event_logger.log_exec_start.assert_called()
        mock_event_logger.log_exec_complete.assert_called()


# ===========================================================================
# TestIpcHostHealthCheck (2 tests)
# ===========================================================================
class TestIpcHostHealthCheck:
    """Tests for health_check method."""

    @pytest.mark.asyncio
    async def test_health_check_success(self, host):
        """Health check returns True when pong received."""
        reader = MockReader([PongMsg()])
        writer = MockWriter()
        _inject_streams(host, reader, writer)

        result = await host.health_check()

        assert result is True
        sent = writer.get_messages()
        assert any(m["type"] == "ping" for m in sent)

    @pytest.mark.asyncio
    async def test_health_check_timeout(self, host):
        """Health check returns False when no pong received."""
        reader = MockReader([])
        writer = MockWriter()
        _inject_streams(host, reader, writer)

        result = await host.health_check(timeout=0.1)

        assert result is False


# ===========================================================================
# TestIpcHostStop (2 tests)
# ===========================================================================
class TestIpcHostStop:
    """Tests for stop method."""

    @pytest.mark.asyncio
    async def test_stop_closes_connection(self, host):
        """stop() closes the writer."""
        reader = MockReader([])
        writer = MockWriter()
        _inject_streams(host, reader, writer)

        await host.stop()

        assert writer.closed

    @pytest.mark.asyncio
    async def test_stop_removes_socket_file(self, host):
        """stop() removes the socket file if it exists."""
        socket_path = Path(host.socket_path)
        socket_path.touch()
        assert socket_path.exists()
        reader = MockReader([])
        writer = MockWriter()
        _inject_streams(host, reader, writer)

        await host.stop()

        assert not socket_path.exists()


# ===========================================================================
# TestCrashHandling (1 test)
# ===========================================================================
class TestCrashHandling:
    """Tests for crash detection during execution."""

    @pytest.mark.asyncio
    async def test_crash_during_execution_returns_error(self, host):
        """EOF during execution returns error with connection closed."""
        reader = MockReader([])
        reader._queue.put_nowait(b"")
        writer = MockWriter()
        _inject_streams(host, reader, writer)

        result = await host.send_execute("e-1", "print(1)", 60)

        assert result["type"] == "error"
        assert (
            "crash" in result.get("message", "").lower()
            or "closed" in result.get("message", "").lower()
        )
