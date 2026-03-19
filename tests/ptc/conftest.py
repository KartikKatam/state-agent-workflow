"""Shared fixtures for PTC sandbox tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make PTC server importable
PTC_SERVER_DIR = Path.home() / ".claude" / "mcp" / "ptc-server"
sys.path.insert(0, str(PTC_SERVER_DIR))

try:
    from ipc_protocol import (
        CompleteMsg,
        ErrorMsg,
        ExecuteMsg,
        new_exec_id,
    )

    _IPC_AVAILABLE = True
except ImportError:
    _IPC_AVAILABLE = False


# --- Message Factories ---


def make_execute_msg(exec_id: str | None = None, code: str = "print(1)") -> ExecuteMsg:
    """Create ExecuteMsg with auto-generated exec_id if None."""
    return ExecuteMsg(exec_id=exec_id or new_exec_id(), code=code)


def make_complete_msg(
    exec_id: str | None = None,
    stdout: str = "",
    stderr: str = "",
    return_code: int = 0,
    namespace_keys: list[str] | None = None,
    namespace_size_kb: int = 0,
) -> CompleteMsg:
    """Create CompleteMsg."""
    return CompleteMsg(
        exec_id=exec_id or new_exec_id(),
        stdout=stdout,
        stderr=stderr,
        return_code=return_code,
        namespace_keys=namespace_keys if namespace_keys is not None else [],
        namespace_size_kb=namespace_size_kb,
    )


def make_error_msg(
    exec_id: str | None = None,
    message: str = "error",
    traceback: str = "",
) -> ErrorMsg:
    """Create ErrorMsg."""
    return ErrorMsg(
        exec_id=exec_id or new_exec_id(), message=message, traceback=traceback
    )


# --- Existing Fixtures ---


@pytest.fixture
def config() -> dict:
    """Parsed config.json dict matching simplified PTC config."""
    return {
        "resource_limits": {
            "memory_mb": 1024,
            "cpu_cores": 1,
            "timeout_seconds": 60,
            "max_containers": 8,
            "disk_mb": 512,
            "max_output_bytes": 65536,
            "idle_timeout_seconds": 300,
            "container_ttl_seconds": 600,
        },
        "repl_pool": {
            "max_repls_per_container": 6,
            "repl_start_timeout_seconds": 5,
        },
        "docker": {
            "image": "ptc-sandbox:latest",
            "role_image_pattern": "ptc-{role}:latest",
            "network_mode": "bridge",
            "container_prefix": "ptc",
            "runtime": "runc",
        },
        "ipc": {
            "socket_dir": "/tmp/ptc-ipc",
        },
        "observability": {
            "log_file": "~/.claude/logs/ptc-events.jsonl",
            "debug_log_code": False,
            "result_preview_chars": 200,
        },
    }


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """Create a sample project directory for testing tools."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (src / "main.py").write_text(
        "import os\nimport sys\nfrom src.utils import helper\n\n"
        "def main():\n    print('hello')\n\n"
        "class App:\n    def run(self):\n        pass\n\n"
        "if __name__ == '__main__':\n    main()\n"
    )
    (src / "utils.py").write_text(
        "import json\n\ndef helper(x: int) -> str:\n    return str(x)\n\n"
        "class Config:\n    def __init__(self):\n        self.data = {}\n"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_main.py").write_text(
        "def test_passing():\n    assert 1 + 1 == 2\n\n"
        "def test_also_passing():\n    assert True\n"
    )
    return tmp_path
