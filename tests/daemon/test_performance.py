"""Performance tests for daemon core operations.

Ensures critical-path operations meet latency budgets:
  - handle_pre_tool: < 5ms
  - do_transition: < 10ms
  - path_matches_glob: < 1ms
  - evaluate_guards: < 3ms
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest

import scripts.workflow_state as daemon
from schemas.state_machine import StateMachineDefinition

from .conftest import make_agent_in_state

N_ITERS = 100


def _median_ms(fn: Callable[[], object], n: int = N_ITERS) -> float:
    """Run fn() n times, return median elapsed time in milliseconds."""
    times: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        fn()
        times.append((time.perf_counter() - start) * 1000)
    times.sort()
    return times[n // 2]


class TestPreToolPerformance:
    """handle_pre_tool must complete in < 5ms."""

    def test_handle_pre_tool_simple_under_5ms(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Simple allowed case: Read tool in IDLE state."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        daemon._agent_state_cache.clear()

        median = _median_ms(
            lambda: daemon.handle_pre_tool("coder-p1-t1-a1b2", "Read", {})
        )
        assert median < 5, f"handle_pre_tool took {median:.2f}ms (budget: 5ms)"

    def test_handle_pre_tool_write_gating_under_5ms(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Write with glob matching should still be < 5ms."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "WRITING")
        daemon._agent_state_cache.clear()

        median = _median_ms(
            lambda: daemon.handle_pre_tool(
                "coder-p1-t1-a1b2",
                "Write",
                {"file_path": "src/deep/nested/module.py"},
            )
        )
        assert median < 5, f"handle_pre_tool (write gating) took {median:.2f}ms"


class TestTransitionPerformance:
    """do_transition must complete in < 10ms (no validators)."""

    def test_transition_under_10ms(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Simple transition IDLE → UNRESTRICTED (no guards)."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")

        median = _median_ms(
            lambda: daemon.do_transition(
                "coder-p1-t1-a1b2", "UNRESTRICTED", "enter_unrestricted"
            ),
            n=1,  # Single run since transition changes state
        )
        assert median < 10, f"do_transition took {median:.2f}ms (budget: 10ms)"


class TestGlobPerformance:
    """path_matches_glob must complete in < 1ms."""

    def test_glob_match_under_1ms(self) -> None:
        """Recursive glob match performance."""
        median = _median_ms(
            lambda: daemon.path_matches_glob(
                "src/deep/nested/very/deep/module.py", "src/**/*.py"
            )
        )
        assert median < 1, f"path_matches_glob took {median:.2f}ms (budget: 1ms)"


class TestGuardPerformance:
    """Guard evaluation must complete in < 3ms."""

    def test_empty_guards_under_3ms(self, temp_state_dir: Path) -> None:
        """Empty guards list evaluation."""
        median = _median_ms(lambda: daemon.evaluate_guards([], "any-agent-x1-a1b2"))
        assert median < 3, f"evaluate_guards (empty) took {median:.2f}ms (budget: 3ms)"

    def test_single_guard_under_3ms(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Single guard evaluation."""

        def fast_guard(
            _agent_id: str, _agent: dict | None, _system: dict | None
        ) -> bool:
            return True

        monkeypatch.setitem(daemon._GUARD_REGISTRY, "fast_guard", fast_guard)
        median = _median_ms(
            lambda: daemon.evaluate_guards(["fast_guard"], "any-agent-x1-a1b2")
        )
        assert median < 3, (
            f"evaluate_guards (1 guard) took {median:.2f}ms (budget: 3ms)"
        )
