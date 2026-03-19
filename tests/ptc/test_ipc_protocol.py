"""Tests for IPC protocol messages and serialization.

Simplified PTC: only 5 message types — Execute, Complete, Error, Ping, Pong.
No more ToolCallMsg, ToolResultMsg, CancelMsg, or new_call_id.
"""

import pytest

from ipc_protocol import (
    MAX_MESSAGE_SIZE,
    CompleteMsg,
    ErrorMsg,
    ExecuteMsg,
    PingMsg,
    PongMsg,
    decode,
    encode,
    new_exec_id,
)


@pytest.mark.unit
class TestExecuteMsg:
    """Tests for ExecuteMsg dataclass."""

    def test_execute_msg_fields(self):
        """Verify field assignment and default type."""
        msg = ExecuteMsg(exec_id="e-abc", code="x=1")
        assert msg.exec_id == "e-abc"
        assert msg.code == "x=1"
        assert msg.type == "execute"

    def test_execute_msg_round_trip(self):
        """Verify encode/decode preserves all fields."""
        msg = ExecuteMsg("e-1", "print(1)")
        d = decode(encode(msg))
        assert d["exec_id"] == "e-1"
        assert d["code"] == "print(1)"
        assert d["type"] == "execute"

    def test_execute_msg_default_type(self):
        """Verify type defaults to 'execute' without explicit type arg."""
        msg = ExecuteMsg("e-1", "x")
        assert msg.type == "execute"


@pytest.mark.unit
class TestPingPong:
    """Tests for PingMsg and PongMsg dataclasses."""

    def test_ping_msg_type(self):
        """Verify PingMsg type field."""
        msg = PingMsg()
        assert msg.type == "ping"

    def test_pong_msg_type(self):
        """Verify PongMsg type field."""
        msg = PongMsg()
        assert msg.type == "pong"

    def test_ping_round_trip(self):
        """Verify PingMsg encode/decode round-trip."""
        msg = PingMsg()
        d = decode(encode(msg))
        assert d["type"] == "ping"

    def test_pong_round_trip(self):
        """Verify PongMsg encode/decode round-trip."""
        msg = PongMsg()
        d = decode(encode(msg))
        assert d["type"] == "pong"


@pytest.mark.unit
class TestCompleteMsg:
    """Tests for CompleteMsg dataclass."""

    def test_complete_msg_defaults(self):
        """Verify default values for optional fields."""
        msg = CompleteMsg(exec_id="e-1", stdout="hello")
        assert msg.stderr == ""
        assert msg.return_code == 0
        assert msg.namespace_keys == []
        assert msg.namespace_size_kb == 0

    def test_complete_msg_full(self):
        """Verify all fields when explicitly set."""
        msg = CompleteMsg(
            exec_id="e-1",
            stdout="out",
            stderr="err",
            return_code=1,
            namespace_keys=["x", "y"],
            namespace_size_kb=42,
        )
        assert msg.exec_id == "e-1"
        assert msg.stdout == "out"
        assert msg.stderr == "err"
        assert msg.return_code == 1
        assert msg.namespace_keys == ["x", "y"]
        assert msg.namespace_size_kb == 42
        assert msg.type == "complete"

    def test_complete_msg_round_trip(self):
        """Verify encode/decode preserves all fields including lists."""
        msg = CompleteMsg(
            exec_id="e-1",
            stdout="out",
            stderr="err",
            return_code=1,
            namespace_keys=["x", "y"],
            namespace_size_kb=42,
        )
        d = decode(encode(msg))
        assert d["exec_id"] == "e-1"
        assert d["stdout"] == "out"
        assert d["stderr"] == "err"
        assert d["return_code"] == 1
        assert d["namespace_keys"] == ["x", "y"]
        assert d["namespace_size_kb"] == 42
        assert d["type"] == "complete"


@pytest.mark.unit
class TestErrorMsg:
    """Tests for ErrorMsg dataclass."""

    def test_error_msg_fields(self):
        """Verify field assignment and type."""
        msg = ErrorMsg(exec_id="e-1", message="boom", traceback="line 1")
        assert msg.exec_id == "e-1"
        assert msg.message == "boom"
        assert msg.traceback == "line 1"
        assert msg.type == "error"

    def test_error_msg_default_traceback(self):
        """Verify traceback defaults to empty string."""
        msg = ErrorMsg(exec_id="e-1", message="boom")
        assert msg.traceback == ""

    def test_error_msg_round_trip(self):
        """Verify encode/decode preserves all fields."""
        msg = ErrorMsg(exec_id="e-1", message="boom", traceback="line 1")
        d = decode(encode(msg))
        assert d["exec_id"] == "e-1"
        assert d["message"] == "boom"
        assert d["traceback"] == "line 1"
        assert d["type"] == "error"


@pytest.mark.unit
class TestEncodeDecode:
    """Tests for encode() and decode() functions."""

    def test_encode_returns_bytes(self):
        """Verify encode returns bytes ending with newline."""
        msg = ExecuteMsg(exec_id="e-1", code="x=1")
        result = encode(msg)
        assert isinstance(result, bytes)
        assert result.endswith(b"\n")

    def test_encode_compact_json(self):
        """Verify encode produces compact JSON (no spaces after separators)."""
        msg = ExecuteMsg(exec_id="e-1", code="x=1")
        result = encode(msg)
        text = result.decode().strip()
        # Compact JSON: no spaces after : or ,
        assert ": " not in text
        assert ", " not in text

    def test_decode_strips_whitespace(self):
        """Verify decode handles extra whitespace around the line."""
        msg = ExecuteMsg(exec_id="e-1", code="x=1")
        encoded = encode(msg)
        # Add extra whitespace
        padded = b"  " + encoded + b"  "
        d = decode(padded)
        assert d["exec_id"] == "e-1"
        assert d["code"] == "x=1"

    def test_encode_decode_unicode(self):
        """Verify unicode content survives round-trip."""
        msg = ExecuteMsg(exec_id="e-1", code="x = '\u4e16\u754c'")
        d = decode(encode(msg))
        assert d["code"] == "x = '\u4e16\u754c'"


@pytest.mark.unit
class TestIdGeneration:
    """Tests for new_exec_id() function."""

    def test_new_exec_id_format(self):
        """Verify exec ID format: 'e-' prefix + 12 hex chars = 14 total."""
        eid = new_exec_id()
        assert eid.startswith("e-")
        assert len(eid) == 14
        # Verify hex chars after prefix
        hex_part = eid[2:]
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_id_uniqueness(self):
        """Verify 1000 generated exec IDs are all unique."""
        ids = {new_exec_id() for _ in range(1000)}
        assert len(ids) == 1000


@pytest.mark.unit
class TestConstants:
    """Tests for module-level constants."""

    def test_max_message_size(self):
        """Verify MAX_MESSAGE_SIZE is 1MB (1048576 bytes)."""
        assert MAX_MESSAGE_SIZE == 1_048_576
