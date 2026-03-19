"""Tests for the daemon Unix socket server."""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.workflow_state as daemon
from schemas.state_machine import StateMachineDefinition


def _send_request(sock_path: str, request: dict, timeout: float = 5.0) -> dict:
    """Send a JSON request to the daemon and return the response."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(sock_path)
    s.sendall((json.dumps(request) + "\n").encode())
    response = b""
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        response += chunk
        if b"\n" in response:
            break
    s.close()
    return json.loads(response.decode().strip())


@pytest.fixture()
def daemon_server(
    tmp_path: Path,
    temp_state_dir: Path,
    sample_machine: StateMachineDefinition,
):
    """Start a real daemon server on a tmp socket for integration testing."""
    sock_path = str(tmp_path / "test-daemon.sock")

    if os.path.exists(sock_path):
        os.unlink(sock_path)

    server = daemon.WorkflowDaemon(sock_path, daemon.RequestHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.1)

    yield sock_path

    server.shutdown()
    server.server_close()
    if os.path.exists(sock_path):
        os.unlink(sock_path)


class TestDaemonServer:
    """Integration tests with a real socket server."""

    def test_register_and_query(self, daemon_server: str):
        """Register an agent via socket, then check tool permissions."""
        result = _send_request(
            daemon_server,
            {
                "command": "register",
                "agent_id": "coder-p1-t1-a1b2",
                "role": "coder",
                "model": "opus-4-6",
            },
        )
        assert result["ok"] is True

        result = _send_request(
            daemon_server,
            {
                "command": "check_tool",
                "agent_id": "coder-p1-t1-a1b2",
                "tool": "Write",
                "tool_input": {},
            },
        )
        # Coder starts in IDLE (sample machine) where write_allowed=False
        assert result["allowed"] is False

    def test_get_state_via_socket(self, daemon_server: str):
        """get_state returns full agent state via socket."""
        _send_request(
            daemon_server,
            {
                "command": "register",
                "agent_id": "coder-p1-t2-b2c3",
                "role": "coder",
                "model": "sonnet-4-6",
            },
        )
        result = _send_request(
            daemon_server,
            {
                "command": "get_state",
                "agent_id": "coder-p1-t2-b2c3",
            },
        )
        assert result["ok"] is True
        assert result["state"]["role"] == "coder"

    def test_unknown_command_via_socket(self, daemon_server: str):
        result = _send_request(daemon_server, {"command": "nonexistent"})
        assert result["ok"] is False
        assert "Unknown command" in result["reason"]

    def test_concurrent_requests(self, daemon_server: str):
        """Server handles concurrent connections."""
        _send_request(
            daemon_server,
            {
                "command": "register",
                "agent_id": "coder-p1-t1-a1b2",
                "role": "coder",
                "model": "opus-4-6",
            },
        )

        results = []
        errors = []

        def check_tool():
            try:
                r = _send_request(
                    daemon_server,
                    {
                        "command": "check_tool",
                        "agent_id": "coder-p1-t1-a1b2",
                        "tool": "Read",
                        "tool_input": {},
                    },
                )
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=check_tool) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Errors during concurrent requests: {errors}"
        assert len(results) == 5
        assert all(r["allowed"] is True for r in results)
