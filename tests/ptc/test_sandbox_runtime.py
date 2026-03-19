"""Tests for sandbox runtime: executor and runtime loop.

Simplified PTC: Executor() takes no args (no ipc_client, no allowed_tools).
No more tool stubs, IpcClient, or CancelMsg handling in runtime.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from ipc_protocol import (
    ExecuteMsg,
    PingMsg,
    decode,
    encode,
)
from sandbox_runtime.executor import Executor


# --- Fixtures ---


@pytest.fixture
def executor():
    """Executor with no args (simplified)."""
    return Executor()


# --- Tests ---


@pytest.mark.unit
class TestExecutorInit:
    """Tests for Executor.__init__."""

    def test_executor_init_creates_namespace(self):
        """Verify namespace created as empty dict."""
        ex = Executor()
        assert isinstance(ex._namespace, dict)


@pytest.mark.unit
class TestExecutorExecute:
    """Tests for Executor.execute() — async code execution."""

    @pytest.mark.asyncio
    async def test_execute_simple_print(self, executor):
        """Verify stdout capture for simple print."""
        stdout, stderr, rc, keys, size = await executor.execute("print(42)")
        assert stdout == "42\n"
        assert rc == 0

    @pytest.mark.asyncio
    async def test_execute_captures_stderr(self, executor):
        """Verify stderr capture via sys.stderr.write."""
        code = "import sys\nsys.stderr.write('error msg')"
        stdout, stderr, rc, keys, size = await executor.execute(code)
        assert "error msg" in stderr

    @pytest.mark.asyncio
    async def test_execute_returns_namespace_keys(self, executor):
        """Verify namespace keys include user-defined variables."""
        stdout, stderr, rc, keys, size = await executor.execute("x = 1; y = 2")
        assert "x" in keys
        assert "y" in keys

    @pytest.mark.asyncio
    async def test_execute_returns_namespace_size_kb(self, executor):
        """Verify namespace size is tracked after data assignment."""
        stdout, stderr, rc, keys, size = await executor.execute("data = 'a' * 10000")
        assert size > 0

    @pytest.mark.asyncio
    async def test_execute_syntax_error(self, executor):
        """Verify syntax errors return non-zero rc with error in stderr."""
        stdout, stderr, rc, keys, size = await executor.execute("def(")
        assert rc != 0
        assert "SyntaxError" in stderr

    @pytest.mark.asyncio
    async def test_execute_runtime_error(self, executor):
        """Verify runtime errors return non-zero rc with error in stderr."""
        stdout, stderr, rc, keys, size = await executor.execute("1/0")
        assert rc != 0
        assert "ZeroDivisionError" in stderr

    @pytest.mark.asyncio
    async def test_execute_multiline(self, executor):
        """Verify multiple statements and prints in single exec."""
        code = "x = 1\ny = 2\nprint(x)\nprint(y)"
        stdout, stderr, rc, keys, size = await executor.execute(code)
        assert "1" in stdout
        assert "2" in stdout


@pytest.mark.unit
class TestExecutorNamespacePersistence:
    """Tests for namespace persistence across execute() calls."""

    @pytest.mark.asyncio
    async def test_namespace_persists_across_calls(self, executor):
        """Verify variables survive between execute calls."""
        await executor.execute("x = 1")
        stdout, stderr, rc, keys, size = await executor.execute("print(x)")
        assert stdout == "1\n"

    @pytest.mark.asyncio
    async def test_namespace_accumulates(self, executor):
        """Verify multiple variables accumulate in namespace."""
        await executor.execute("x = 1")
        await executor.execute("y = 2")
        stdout, stderr, rc, keys, size = await executor.execute("print(x + y)")
        assert stdout == "3\n"

    @pytest.mark.asyncio
    async def test_namespace_overwrite(self, executor):
        """Verify variables can be overwritten."""
        await executor.execute("x = 1")
        await executor.execute("x = 2")
        stdout, stderr, rc, keys, size = await executor.execute("print(x)")
        assert stdout == "2\n"


@pytest.mark.unit
class TestExecutorAwaitWrapping:
    """Tests for async await detection and wrapping."""

    @pytest.mark.asyncio
    async def test_await_detection(self, executor):
        """Verify code with await is detected and async-wrapped."""
        code = "import asyncio\nresult = await asyncio.sleep(0)"
        stdout, stderr, rc, keys, size = await executor.execute(code)
        assert rc == 0

    @pytest.mark.asyncio
    async def test_no_await_runs_sync(self, executor):
        """Verify code without await runs synchronously."""
        stdout, stderr, rc, keys, size = await executor.execute("x = 1 + 2")
        assert rc == 0
        assert "x" in keys


@pytest.mark.unit
class TestExecutorNamespaceSize:
    """Tests for namespace size tracking."""

    @pytest.mark.asyncio
    async def test_namespace_size_tracks_growth(self, executor):
        """Verify namespace size increases after large data assignment."""
        stdout, stderr, rc, keys, size = await executor.execute("big = 'x' * 100000")
        assert size > 0

    @pytest.mark.asyncio
    async def test_namespace_keys_listed(self, executor):
        """Verify all user-defined keys appear in namespace_keys."""
        stdout, stderr, rc, keys, size = await executor.execute(
            "a = 1; b = 2; c = 3"
        )
        assert set(keys) >= {"a", "b", "c"}


# =============================================================================
# Runtime main loop tests
# =============================================================================


def _make_mock_reader(*messages):
    """Create mock async reader returning encoded messages then EOF."""
    encoded = []
    for msg in messages:
        encoded.append(encode(msg) if hasattr(msg, "type") else msg)
    encoded.append(b"")  # EOF
    reader = AsyncMock()
    reader.readline = AsyncMock(side_effect=encoded)
    return reader


def _make_mock_writer():
    """Create mock async writer that captures written bytes."""
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    return writer


@pytest.mark.unit
class TestRuntimeMainLoop:
    """Tests for runtime.py main loop — message dispatch."""

    @pytest.mark.asyncio
    async def test_runtime_processes_execute_msg(self):
        """Verify ExecuteMsg produces CompleteMsg with stdout."""
        from sandbox_runtime.runtime import run

        exec_msg = ExecuteMsg(exec_id="e-test001", code="print(1)")
        reader = _make_mock_reader(exec_msg)
        writer = _make_mock_writer()

        await run(reader, writer)
        # _handle_execute runs as a task; give it time to complete
        await asyncio.sleep(0.05)

        calls = writer.write.call_args_list
        assert len(calls) >= 1
        sent = decode(calls[0][0][0])
        assert sent["type"] == "complete"
        assert sent["exec_id"] == "e-test001"
        assert "1" in sent["stdout"]

    @pytest.mark.asyncio
    async def test_runtime_handles_ping(self):
        """Verify PingMsg triggers PongMsg response."""
        from sandbox_runtime.runtime import run

        ping_msg = PingMsg()
        reader = _make_mock_reader(ping_msg)
        writer = _make_mock_writer()

        await run(reader, writer)

        calls = writer.write.call_args_list
        assert len(calls) >= 1
        sent = decode(calls[0][0][0])
        assert sent["type"] == "pong"

    @pytest.mark.asyncio
    async def test_runtime_error_returns_error_msg(self):
        """Verify code error produces ErrorMsg."""
        from sandbox_runtime.runtime import run

        exec_msg = ExecuteMsg(exec_id="e-test001", code="1/0")
        reader = _make_mock_reader(exec_msg)
        writer = _make_mock_writer()

        await run(reader, writer)
        # _handle_execute runs as a task; give it time to complete
        await asyncio.sleep(0.05)

        calls = writer.write.call_args_list
        assert len(calls) >= 1
        sent = decode(calls[0][0][0])
        assert sent["type"] == "error"
        assert sent["exec_id"] == "e-test001"
        assert "ZeroDivisionError" in sent.get(
            "message", ""
        ) or "ZeroDivisionError" in sent.get("traceback", "")

    def test_runtime_socket_arg_parsing(self):
        """Verify --socket arg maps to /ptc_ipc/ path."""
        from sandbox_runtime.runtime import parse_args

        path = parse_args(["--socket", "repl-2.sock"])
        assert path == "/ptc_ipc/repl-2.sock"


# ===========================================================================
# Namespace Eviction (2 tests)
# ===========================================================================
@pytest.mark.unit
class TestNamespaceEviction:
    """Tests for namespace eviction at 512MB threshold."""

    @pytest.mark.asyncio
    async def test_namespace_eviction_at_512mb(self):
        """Namespace > 512MB triggers eviction, size reduced."""
        ex = Executor()
        await ex.execute("big1 = 'a' * (200 * 1024 * 1024)")  # ~200MB
        await ex.execute("big2 = 'b' * (200 * 1024 * 1024)")  # ~200MB
        await ex.execute("big3 = 'c' * (200 * 1024 * 1024)")  # ~200MB

        ex._evict_if_over_limit()

        size_kb = ex._namespace_size_kb()
        assert size_kb < 262144 or len(ex._user_keys()) < 3

    @pytest.mark.asyncio
    async def test_namespace_eviction_preserves_callables(self):
        """Eviction preserves functions, evicts data."""
        ex = Executor()
        await ex.execute("def my_func(): return 42")
        await ex.execute("big_data = 'x' * (300 * 1024 * 1024)")  # ~300MB

        ex._evict_if_over_limit()

        assert "my_func" in ex._namespace
        stdout, stderr, rc, keys, size = await ex.execute("print(my_func())")
        assert "42" in stdout
