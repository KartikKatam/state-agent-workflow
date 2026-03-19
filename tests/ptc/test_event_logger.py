"""Tests for PtcEventLogger — JSONL observability for all PTC events.

Simplified PTC: No more tool_call, pip_install, or tool_dispatch event methods.
Kept: container lifecycle, REPL pool, code execution, IPC health events.
"""

import json
import subprocess

import pytest

from event_logger import PtcEventLogger  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_log_path(tmp_path):
    """Temporary JSONL log file path."""
    return tmp_path / "logs" / "ptc-events.jsonl"


@pytest.fixture
def logger(tmp_log_path):
    """PtcEventLogger writing to a temp file."""
    return PtcEventLogger(log_path=tmp_log_path)


def _read_events(path):
    """Read all events from a JSONL file."""
    lines = path.read_text().strip().splitlines()
    return [json.loads(line) for line in lines]


def _read_last_event(path):
    """Read the last event from a JSONL file."""
    return _read_events(path)[-1]


# ===========================================================================
# TestEventLoggerInit (2 tests)
# ===========================================================================
class TestEventLoggerInit:
    """Tests for PtcEventLogger initialization."""

    def test_init_creates_parent_dirs(self, tmp_log_path):
        """Parent directories are created on init."""
        assert not tmp_log_path.parent.exists()

        PtcEventLogger(log_path=tmp_log_path)

        assert tmp_log_path.parent.exists()

    def test_init_stores_path(self, tmp_log_path):
        """Logger stores the log path."""
        log = PtcEventLogger(log_path=tmp_log_path)

        assert log.log_path == tmp_log_path


# ===========================================================================
# TestEmitEnvelope (4 tests)
# ===========================================================================
class TestEmitEnvelope:
    """Tests for the _emit() base method envelope structure."""

    def test_emit_writes_jsonl_line(self, logger, tmp_log_path):
        """_emit writes a single valid JSONL line."""
        logger._emit(
            "test_event",
            agent_id="agent-1",
            exec_id="e-1",
            container="ctr-1",
            data={"key": "val"},
        )

        lines = tmp_log_path.read_text().strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert isinstance(parsed, dict)

    def test_emit_envelope_has_required_fields(self, logger, tmp_log_path):
        """Emitted event has ts, event, agent_id, and data fields."""
        logger._emit(
            "my_event", agent_id="a1", exec_id="e-1", container="ctr-1", data={"k": "v"}
        )

        ev = _read_last_event(tmp_log_path)
        assert "ts" in ev
        assert ev["event"] == "my_event"
        assert ev["agent_id"] == "a1"
        assert "data" in ev

    def test_emit_omits_empty_strings(self, logger, tmp_log_path):
        """Empty string fields are omitted from the envelope."""
        logger._emit("ev", agent_id="", exec_id="", container="", data={"k": "v"})

        ev = _read_last_event(tmp_log_path)
        assert "agent_id" not in ev
        assert "exec_id" not in ev
        assert "container" not in ev

    def test_emit_append_only(self, logger, tmp_log_path):
        """Multiple _emit calls append lines, not overwrite."""
        logger._emit("ev1", data={"a": 1})
        logger._emit("ev2", data={"b": 2})

        events = _read_events(tmp_log_path)
        assert len(events) == 2
        assert events[0]["event"] == "ev1"
        assert events[1]["event"] == "ev2"


# ===========================================================================
# TestContainerLifecycleMethods (6 tests)
# ===========================================================================
class TestContainerLifecycleMethods:
    """Tests for container lifecycle event methods."""

    def test_log_container_create(self, logger, tmp_log_path):
        """log_container_create emits correct event and data."""
        logger.log_container_create(
            "a1", "ctr-1", "explorer", "ptc-sandbox:latest", 1024
        )
        ev = _read_last_event(tmp_log_path)
        assert ev["event"] == "container_create"
        assert ev["agent_id"] == "a1"
        assert ev["container"] == "ctr-1"
        assert ev["data"]["role"] == "explorer"
        assert ev["data"]["image"] == "ptc-sandbox:latest"
        assert ev["data"]["memory_mb"] == 1024

    def test_log_container_ready(self, logger, tmp_log_path):
        """log_container_ready emits startup timing data."""
        logger.log_container_ready("a1", "ctr-1", 500, 3000, 16)

        ev = _read_last_event(tmp_log_path)
        assert ev["event"] == "container_ready"
        assert ev["data"]["startup_ms"] == 500
        assert ev["data"]["pip_install_ms"] == 3000
        assert ev["data"]["packages_installed"] == 16

    def test_log_container_stop(self, logger, tmp_log_path):
        """log_container_stop emits correct event."""
        logger.log_container_stop("a1", "ctr-1", "idle_timeout", 3600, 42)

        ev = _read_last_event(tmp_log_path)
        assert ev["event"] == "container_stop"
        assert ev["data"]["reason"] == "idle_timeout"
        assert ev["data"]["uptime_s"] == 3600
        assert ev["data"]["total_executions"] == 42

    def test_log_container_crash(self, logger, tmp_log_path):
        """log_container_crash includes reason and memory at crash."""
        logger.log_container_crash("a1", "ctr-1", "OOM", 1020, 3)

        ev = _read_last_event(tmp_log_path)
        assert ev["event"] == "container_crash"
        assert ev["data"]["reason"] == "OOM"
        assert ev["data"]["memory_at_crash_mb"] == 1020
        assert ev["data"]["active_repls"] == 3

    def test_log_container_health(self, logger, tmp_log_path):
        """log_container_health includes health metrics."""
        logger.log_container_health("a1", "ctr-1", 5, 256, 12.5, 2)

        ev = _read_last_event(tmp_log_path)
        assert ev["event"] == "container_health"
        assert ev["data"]["ping_latency_ms"] == 5
        assert ev["data"]["memory_mb"] == 256
        assert ev["data"]["cpu_pct"] == 12.5
        assert ev["data"]["repl_count"] == 2

    def test_log_container_handoff(self, logger, tmp_log_path):
        """log_container_handoff includes old and new agent IDs."""
        logger.log_container_handoff("ctr-1", "old-agent", "new-agent", False)

        ev = _read_last_event(tmp_log_path)
        assert ev["event"] == "container_handoff"
        assert ev["data"]["old_agent_id"] == "old-agent"
        assert ev["data"]["new_agent_id"] == "new-agent"
        assert ev["data"]["namespace_reset"] is False


# ===========================================================================
# TestReplPoolMethods (3 tests — pip_install removed)
# ===========================================================================
class TestReplPoolMethods:
    """Tests for REPL pool event methods."""

    def test_log_repl_start(self, logger, tmp_log_path):
        """log_repl_start emits repl_index and is_sub_agent."""
        logger.log_repl_start("a1", "ctr-1", 0, None, False)

        ev = _read_last_event(tmp_log_path)
        assert ev["event"] == "repl_start"
        assert ev["data"]["repl_index"] == 0
        assert ev["data"]["is_sub_agent"] is False

    def test_log_repl_stop(self, logger, tmp_log_path):
        """log_repl_stop emits correct event."""
        logger.log_repl_stop("a1", "ctr-1", 0, "shutdown", 15, 120)

        ev = _read_last_event(tmp_log_path)
        assert ev["event"] == "repl_stop"
        assert ev["data"]["repl_index"] == 0
        assert ev["data"]["reason"] == "shutdown"
        assert ev["data"]["exec_count"] == 15
        assert ev["data"]["uptime_s"] == 120

    def test_log_repl_crash(self, logger, tmp_log_path):
        """log_repl_crash emits crash details."""
        logger.log_repl_crash("a1", "ctr-1", 1, "segfault", False)

        ev = _read_last_event(tmp_log_path)
        assert ev["event"] == "repl_crash"
        assert ev["data"]["repl_index"] == 1
        assert ev["data"]["reason"] == "segfault"
        assert ev["data"]["other_repls_affected"] is False


# ===========================================================================
# TestExecutionMethods (4 tests)
# ===========================================================================
class TestExecutionMethods:
    """Tests for code execution event methods."""

    def test_log_exec_start(self, logger, tmp_log_path):
        """log_exec_start emits code_length and has_await."""
        logger.log_exec_start("a1", "e-1", 100, True)

        ev = _read_last_event(tmp_log_path)
        assert ev["event"] == "exec_start"
        assert ev["data"]["code_length"] == 100
        assert ev["data"]["has_await"] is True

    def test_log_exec_complete(self, logger, tmp_log_path):
        """log_exec_complete emits all execution metrics."""
        logger.log_exec_complete("a1", "e-1", 250, 64, 0, ["x", "y"], 10)

        ev = _read_last_event(tmp_log_path)
        assert ev["event"] == "exec_complete"
        assert ev["data"]["duration_ms"] == 250
        assert ev["data"]["stdout_bytes"] == 64
        assert ev["data"]["tool_calls"] == 0
        assert ev["data"]["namespace_keys"] == ["x", "y"]
        assert ev["data"]["namespace_size_kb"] == 10

    def test_log_exec_error(self, logger, tmp_log_path):
        """log_exec_error emits error details."""
        logger.log_exec_error("a1", "e-1", "NameError: x", "Traceback...", 50)
        ev = _read_last_event(tmp_log_path)
        assert ev["event"] == "exec_error"
        assert ev["data"]["error"] == "NameError: x"
        assert ev["data"]["traceback_preview"] == "Traceback..."
        assert ev["data"]["duration_ms"] == 50

    def test_log_exec_timeout(self, logger, tmp_log_path):
        """log_exec_timeout emits timeout details."""
        logger.log_exec_timeout("a1", "e-1", 60, 0)

        ev = _read_last_event(tmp_log_path)
        assert ev["event"] == "exec_timeout"
        assert ev["data"]["timeout_seconds"] == 60
        assert ev["data"]["tool_calls_before_timeout"] == 0


# ===========================================================================
# TestIpcMethods (3 tests)
# ===========================================================================
class TestIpcMethods:
    """Tests for IPC health event methods."""

    def test_log_ipc_connect(self, logger, tmp_log_path):
        """log_ipc_connect emits socket path."""
        logger.log_ipc_connect("a1", "/tmp/ptc-ipc/a1/repl-0.sock")

        ev = _read_last_event(tmp_log_path)
        assert ev["event"] == "ipc_connect"
        assert ev["data"]["socket_path"] == "/tmp/ptc-ipc/a1/repl-0.sock"

    def test_log_ipc_disconnect(self, logger, tmp_log_path):
        """log_ipc_disconnect emits reason and pending calls."""
        logger.log_ipc_disconnect("a1", "eof", 2)

        ev = _read_last_event(tmp_log_path)
        assert ev["event"] == "ipc_disconnect"
        assert ev["data"]["reason"] == "eof"
        assert ev["data"]["pending_calls"] == 2

    def test_log_ipc_ping(self, logger, tmp_log_path):
        """log_ipc_ping emits latency."""
        logger.log_ipc_ping("a1", 15)

        ev = _read_last_event(tmp_log_path)
        assert ev["event"] == "ipc_ping"
        assert ev["data"]["latency_ms"] == 15


# ===========================================================================
# TestJqQueryability (1 golden test)
# ===========================================================================
class TestJqQueryability:
    """Golden test: events are queryable with jq."""

    def test_events_queryable_with_jq(self, logger, tmp_log_path):
        """Events with matching exec_id are selectable via jq."""
        logger.log_exec_start("a1", "e-target", 100, False)
        logger.log_exec_complete("a1", "e-target", 200, 50, 0, ["x"], 5)
        logger.log_exec_start("a2", "e-other", 50, False)
        logger.log_exec_start("a3", "e-other2", 30, True)

        result = subprocess.run(
            ["jq", "-c", 'select(.exec_id=="e-target")', str(tmp_log_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        matching_lines = result.stdout.strip().splitlines()
        assert len(matching_lines) == 2
