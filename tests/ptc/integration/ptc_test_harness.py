"""Instrumented wrapper around ContainerManager for timing measurements."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class TimingRecord:
    """Timing for a single agent's operations."""

    agent_id: str
    role: str
    container_create_ms: float = 0.0
    pip_install_ms: float = 0.0
    repl_start_ms: float = 0.0
    execute_ms: float = 0.0
    total_ms: float = 0.0


class PtcTestHarness:
    """Wraps ContainerManager with timing instrumentation.

    Records wall-clock time for:
    - Container creation (docker run)
    - Package installation (pip install)
    - REPL start (docker exec + IPC connect)
    - Code execution
    """

    def __init__(self, container_manager: Any) -> None:
        self._mgr = container_manager
        self._timings: dict[str, TimingRecord] = {}
        self._initialized_agents: set[str] = set()

    async def execute_and_measure(
        self,
        agent_id: str,
        role: str,
        code: str,
        timeout: int = 60,
    ) -> tuple[dict, dict]:
        """Execute code and return (result, timing_dict).

        On first call for an agent_id, measures get_or_create_repl (which includes
        container creation, pip install, and REPL start). On subsequent calls,
        only measures execute time.

        Returns:
            tuple of (execution result dict, timing dict with keys:
                container_create_ms, pip_install_ms, repl_start_ms, execute_ms, total_ms)
        """
        total_start = time.monotonic()

        record = self._timings.get(agent_id)
        if record is None:
            record = TimingRecord(agent_id=agent_id, role=role)
            self._timings[agent_id] = record

        # First call: measure REPL creation (includes container + pip + REPL start)
        if agent_id not in self._initialized_agents:
            create_start = time.monotonic()
            await self._mgr.get_or_create_repl(agent_id, role)
            create_duration = (time.monotonic() - create_start) * 1000
            record.repl_start_ms = create_duration  # Combined creation time
            self._initialized_agents.add(agent_id)

        # Execute code
        exec_start = time.monotonic()
        result = await self._mgr.execute_code(agent_id, code, timeout)
        record.execute_ms = (time.monotonic() - exec_start) * 1000

        record.total_ms = (time.monotonic() - total_start) * 1000

        timing_dict = {
            "container_create_ms": record.container_create_ms,
            "pip_install_ms": record.pip_install_ms,
            "repl_start_ms": record.repl_start_ms,
            "execute_ms": record.execute_ms,
            "total_ms": record.total_ms,
        }

        return result, timing_dict

    def get_timing_summary(self) -> dict[str, dict]:
        """Return all recorded timings keyed by agent_id."""
        return {
            agent_id: {
                "role": record.role,
                "container_create_ms": record.container_create_ms,
                "pip_install_ms": record.pip_install_ms,
                "repl_start_ms": record.repl_start_ms,
                "execute_ms": record.execute_ms,
                "total_ms": record.total_ms,
            }
            for agent_id, record in self._timings.items()
        }
