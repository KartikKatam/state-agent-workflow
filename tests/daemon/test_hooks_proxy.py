"""Tests for pre_tool_use.py and post_tool_use.py thin daemon proxies."""

from __future__ import annotations

import json
import os
import socketserver
import sys
import threading
import time
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Mock daemon for testing
# ---------------------------------------------------------------------------
class _MockHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.rfile.readline()
        response = json.dumps(self.server._mock_response)  # type: ignore[attr-defined]
        self.wfile.write((response + "\n").encode())


class _MockDaemon(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True
    _mock_response: dict = {}


def _start_mock_daemon(sock_path: str, response: dict) -> _MockDaemon:
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    server = _MockDaemon(sock_path, _MockHandler)
    server._mock_response = response
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    return server


# ---------------------------------------------------------------------------
# Pre-tool proxy tests
# ---------------------------------------------------------------------------
class TestPreToolProxy:
    """Tests for pre_tool_use.py daemon proxy."""

    def test_permissive_fallback_no_socket(self, tmp_path: Path):
        from hooks.pre_tool_use import _send_to_daemon

        with patch(
            "hooks.pre_tool_use._SOCKET_PATH", str(tmp_path / "nonexistent.sock")
        ):
            result = _send_to_daemon({"command": "pre_tool", "tool": "Write"})
            assert result is None

    def test_daemon_allowed_response(self, tmp_path: Path):
        sock_path = str(tmp_path / "test.sock")
        server = _start_mock_daemon(sock_path, {"allowed": True, "reason": ""})
        try:
            from hooks.pre_tool_use import _send_to_daemon

            with patch("hooks.pre_tool_use._SOCKET_PATH", sock_path):
                result = _send_to_daemon(
                    {
                        "command": "pre_tool",
                        "agent_id": "test",
                        "tool": "Read",
                    }
                )
                assert result is not None
                assert result["allowed"] is True
        finally:
            server.shutdown()

    def test_daemon_denied_response(self, tmp_path: Path):
        sock_path = str(tmp_path / "test.sock")
        server = _start_mock_daemon(
            sock_path,
            {"allowed": False, "reason": "coder in SPAWNED: writes not allowed"},
        )
        try:
            from hooks.pre_tool_use import _send_to_daemon

            with patch("hooks.pre_tool_use._SOCKET_PATH", sock_path):
                result = _send_to_daemon(
                    {
                        "command": "pre_tool",
                        "agent_id": "test",
                        "tool": "Write",
                    }
                )
                assert result is not None
                assert result["allowed"] is False
                assert "SPAWNED" in result["reason"]
        finally:
            server.shutdown()

    def test_daemon_inject_context(self, tmp_path: Path):
        sock_path = str(tmp_path / "test.sock")
        server = _start_mock_daemon(
            sock_path,
            {"allowed": True, "inject_context": "Think about your approach."},
        )
        try:
            from hooks.pre_tool_use import _send_to_daemon

            with patch("hooks.pre_tool_use._SOCKET_PATH", sock_path):
                result = _send_to_daemon(
                    {
                        "command": "pre_tool",
                        "agent_id": "test",
                        "tool": "Write",
                    }
                )
                assert result is not None
                assert result.get("inject_context") == "Think about your approach."
        finally:
            server.shutdown()

    def test_main_permissive_on_empty_stdin(self, tmp_path: Path):
        from hooks.pre_tool_use import main

        with (
            patch(
                "hooks.pre_tool_use._SOCKET_PATH", str(tmp_path / "nonexistent.sock")
            ),
            patch("sys.stdin", StringIO("")),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_main_permissive_on_bad_json(self, tmp_path: Path):
        from hooks.pre_tool_use import main

        mock_stdin = StringIO("not-json")
        mock_stdin.isatty = lambda: False  # type: ignore[assignment]

        with (
            patch(
                "hooks.pre_tool_use._SOCKET_PATH", str(tmp_path / "nonexistent.sock")
            ),
            patch("sys.stdin", mock_stdin),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Post-tool proxy tests
# ---------------------------------------------------------------------------
class TestPostToolProxy:
    """Tests for post_tool_use.py thin daemon proxy."""

    def test_permissive_fallback_no_socket(self, tmp_path: Path):
        from hooks.post_tool_use import _send_to_daemon

        with patch(
            "hooks.post_tool_use._SOCKET_PATH", str(tmp_path / "nonexistent.sock")
        ):
            result = _send_to_daemon({"command": "post_tool", "tool": "Write"})
            assert result is None

    def test_daemon_response_with_feedback(self, tmp_path: Path):
        sock_path = str(tmp_path / "test.sock")
        server = _start_mock_daemon(
            sock_path,
            {"inject_context": "Context info here", "feedback": ["lint warning"]},
        )
        try:
            from hooks.post_tool_use import _send_to_daemon

            with patch("hooks.post_tool_use._SOCKET_PATH", sock_path):
                result = _send_to_daemon(
                    {
                        "command": "post_tool",
                        "agent_id": "test",
                        "tool": "Write",
                    }
                )
                assert result is not None
                assert result.get("inject_context") == "Context info here"
                assert result.get("feedback") == ["lint warning"]
        finally:
            server.shutdown()

    def test_main_permissive_on_empty_stdin(self, tmp_path: Path):
        """Post-tool hook returns cleanly when stdin is empty."""
        from hooks.post_tool_use import main

        with (
            patch(
                "hooks.post_tool_use._SOCKET_PATH", str(tmp_path / "nonexistent.sock")
            ),
            patch("sys.stdin", StringIO("")),
        ):
            # Should return without raising (no sys.exit)
            main()
