"""Shared fixtures for PTC integration tests.

Provides real Docker containers, ContainerManager instances, and helper
functions for executing code and asserting results.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Make PTC server and this directory importable
# ---------------------------------------------------------------------------
PTC_SERVER_DIR = Path.home() / ".claude" / "mcp" / "ptc-server"
sys.path.insert(0, str(PTC_SERVER_DIR))
sys.path.insert(0, str(Path(__file__).parent))

from container_manager import ContainerManager  # noqa: E402
from event_logger import PtcEventLogger  # noqa: E402

# ---------------------------------------------------------------------------
# Pytest markers
# ---------------------------------------------------------------------------


def pytest_configure(config: Any) -> None:
    config.addinivalue_line("markers", "integration: PTC integration tests")
    config.addinivalue_line("markers", "phase1: Phase 1 single agent tests")
    config.addinivalue_line("markers", "phase2: Phase 2 multi-agent tests")
    config.addinivalue_line("markers", "phase3: Phase 3 handoff tests")
    config.addinivalue_line("markers", "phase4: Phase 4 sub-agent tests")
    config.addinivalue_line("markers", "slow: Slow tests requiring extra timeout")
    config.addinivalue_line("markers", "benefit: Benefit measurement tests")


# ---------------------------------------------------------------------------
# Resource monitor — lightweight CSV logger for container metrics
# ---------------------------------------------------------------------------


class ResourceMonitor:
    """Background thread that periodically samples container resource usage."""

    def __init__(self, output_dir: Path, interval: float = 5.0) -> None:
        self._output_dir = output_dir
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._csv_path = output_dir / f"resources-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.csv"

    def start(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _run(self) -> None:
        with open(self._csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "container", "cpu_pct", "mem_usage", "mem_limit", "pids"])
            while not self._stop_event.is_set():
                try:
                    result = subprocess.run(
                        ["docker", "stats", "--no-stream", "--format",
                         "{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.PIDs}}"],
                        capture_output=True, text=True, timeout=10,
                    )
                    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    for line in result.stdout.strip().splitlines():
                        if line and line.startswith("ptc-"):
                            parts = line.split(",")
                            if len(parts) >= 4:
                                name, cpu, mem, pids = parts[0], parts[1], parts[2], parts[3]
                                writer.writerow([ts, name, cpu, mem, "", pids])
                    f.flush()
                except Exception:
                    pass
                self._stop_event.wait(self._interval)


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def docker_available() -> None:
    """Skip entire session if Docker daemon is not reachable."""
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        pytest.skip("Docker not available")


@pytest.fixture(scope="session")
def docker_image(docker_available: None) -> None:  # noqa: ARG001
    """Skip entire session if ptc-sandbox:latest image is not built."""
    result = subprocess.run(
        ["docker", "image", "inspect", "ptc-sandbox:latest"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        pytest.skip("ptc-sandbox:latest image not built")


@pytest.fixture(scope="session")
def project_root() -> str:
    """Project root mounted at /workspace:rw inside containers."""
    return "/home/kartik/personal/agentic_workflow"


@pytest.fixture(scope="session", autouse=True)
def workspace_fixture_file(project_root: str):  # type: ignore[misc]
    """Create a test fixture Python file in the workspace root for tests that read files."""
    fixture_path = Path(project_root) / "_test_server.py"
    fixture_content = (
        '"""Fixture file for PTC integration tests."""\n\n'
        "class ContainerManager:\n"
        '    """Manages persistent Docker containers."""\n\n'
        "    def __init__(self):\n"
        "        self.containers = {}\n\n"
        "    def start(self):\n"
        "        pass\n\n"
        "    def stop(self):\n"
        "        pass\n"
    )
    fixture_path.write_text(fixture_content)
    yield str(fixture_path)
    fixture_path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def ptc_config() -> dict:
    """Load PTC server configuration."""
    config_path = PTC_SERVER_DIR / "config.json"
    with open(config_path) as f:
        return json.load(f)


@pytest.fixture(scope="session")
def resource_monitor():  # type: ignore[misc]
    """Background resource monitor writing CSV to tests/ptc/integration/data/."""
    data_dir = Path(__file__).parent / "data"
    monitor = ResourceMonitor(output_dir=data_dir)
    monitor.start()
    yield monitor
    monitor.stop()


@pytest.fixture(scope="session", autouse=True)
def cleanup_all_ptc_containers():  # type: ignore[misc]
    """Remove all ptc- containers at session start and end."""

    def _cleanup() -> None:
        # Get container IDs, then remove if any exist
        result = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=ptc-"],
            capture_output=True, text=True,
        )
        container_ids = result.stdout.strip()
        if container_ids:
            subprocess.run(
                ["docker", "rm", "-f", *container_ids.split("\n")],
                capture_output=True, text=True,
            )

    _cleanup()
    yield
    _cleanup()


# ---------------------------------------------------------------------------
# Function-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_log_path(tmp_path: Path) -> Path:
    """Temporary JSONL event log per test."""
    return tmp_path / "ptc-events.jsonl"


@pytest_asyncio.fixture
async def container_manager(
    ptc_config: dict,
    project_root: str,
    event_log_path: Path,
    docker_image: None,  # noqa: ARG001
):  # type: ignore[misc]
    """Real ContainerManager with Docker and IPC."""
    event_logger = PtcEventLogger(log_path=event_log_path)
    mgr = ContainerManager(
        config=ptc_config,
        project_root=project_root,
        event_logger=event_logger,
    )
    mgr.docker_available = True
    yield mgr
    await mgr.stop_all()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


async def await_execute(
    mgr: ContainerManager,
    agent_id: str,
    role: str,
    code: str,
    timeout: int = 60,
) -> dict:
    """Get or create REPL then execute code."""
    await mgr.get_or_create_repl(agent_id, role)
    return await mgr.execute_code(agent_id, code, timeout)


def assert_complete(result: dict) -> None:
    """Assert execution completed successfully."""
    assert result.get("type") == "complete", f"Expected complete, got: {result}"


def assert_error(result: dict, substring: str = "") -> None:
    """Assert execution returned an error."""
    msg = result.get("message", "") + result.get("stderr", "") + result.get("stdout", "")
    assert (
        result.get("type") == "error" or result.get("return_code", 0) != 0
    ), f"Expected error, got: {result}"
    if substring:
        assert substring.lower() in msg.lower(), f"Expected '{substring}' in error: {msg}"


def count_containers() -> int:
    """Count running ptc containers."""
    result = subprocess.run(
        ["docker", "ps", "--filter", "name=ptc-", "-q"],
        capture_output=True, text=True,
    )
    lines = result.stdout.strip()
    return len(lines.split("\n")) if lines else 0


def get_container_memory_bytes(name: str) -> int:
    """Get container memory limit in bytes via docker inspect."""
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.HostConfig.Memory}}", name],
        capture_output=True, text=True,
    )
    value = result.stdout.strip()
    return int(value) if value else 0
