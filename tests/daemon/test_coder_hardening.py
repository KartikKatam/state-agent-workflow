"""Tests for coder state machine hardening (C1-C7).

Covers: schema extensions, variable resolution, new guards, read restrictions,
and all new states/transitions in the hardened coder SM.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.daemon.state_manager as _sm
import scripts.workflow_state as daemon
from schemas.state_machine import StateDefinition, StateMachineDefinition
from tests.daemon.conftest import make_agent_in_state


# ============================================================================
# Chunk 1: Schema Extensions
# ============================================================================
class TestSchemaExtensions:
    """Tests for read_globs_exclude and variables fields."""

    def test_state_def_accepts_read_globs_exclude(self) -> None:
        sd = StateDefinition(
            name="TEST",
            description="test",
            read_globs_exclude=["tests/**/*.py"],
        )
        assert sd.read_globs_exclude == ["tests/**/*.py"]

    def test_state_def_read_globs_exclude_default_none(self) -> None:
        sd = StateDefinition(name="TEST", description="test")
        assert sd.read_globs_exclude is None

    def test_machine_def_accepts_variables(self) -> None:
        sm = StateMachineDefinition(
            name="test",
            description="test",
            initial_state="A",
            terminal_states=["A"],
            states=[StateDefinition(name="A", description="a")],
            transitions=[],
            variables={"src": ["src/**/*.py"]},
        )
        assert sm.variables == {"src": ["src/**/*.py"]}

    def test_machine_def_variables_default_empty(self) -> None:
        sm = StateMachineDefinition(
            name="test",
            description="test",
            initial_state="A",
            terminal_states=["A"],
            states=[StateDefinition(name="A", description="a")],
            transitions=[],
        )
        assert sm.variables == {}

    def test_state_def_rejects_unknown_field(self) -> None:
        with pytest.raises(Exception):
            StateDefinition(name="T", description="t", bogus_field=True)  # type: ignore[call-arg]

    def test_machine_def_rejects_unknown_field(self) -> None:
        with pytest.raises(Exception):
            StateMachineDefinition(
                name="t",
                description="t",
                initial_state="A",
                terminal_states=["A"],
                states=[StateDefinition(name="A", description="a")],
                transitions=[],
                bogus=True,  # type: ignore[call-arg]
            )


# ============================================================================
# Chunk 2: Variable Resolution
# ============================================================================
class TestVariableResolution:
    """Tests for $variable resolution in load_machine()."""

    def test_variable_resolution_write_globs(
        self, variable_machine: StateMachineDefinition
    ) -> None:
        work = next(s for s in variable_machine.states if s.name == "WORK")
        assert work.write_globs == ["src/**/*.py"]

    def test_variable_resolution_read_globs_exclude(
        self, variable_machine: StateMachineDefinition
    ) -> None:
        work = next(s for s in variable_machine.states if s.name == "WORK")
        assert work.read_globs_exclude == [
            "tests/**/*.py",
            "**/test_*.py",
            "**/conftest.py",
        ]

    def test_no_variables_no_resolution(
        self, sample_machine: StateMachineDefinition
    ) -> None:
        """Machine without variables keeps globs unchanged."""
        writing = next(s for s in sample_machine.states if s.name == "WRITING")
        assert writing.write_globs == ["src/**/*.py"]

    def test_unknown_variable_stays_literal(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        machine_dict: dict[str, Any] = {
            "name": "test-unknown-var",
            "description": "t",
            "agent_role": "coder",
            "initial_state": "A",
            "terminal_states": ["A"],
            "variables": {"src": ["src/**/*.py"]},
            "states": [
                {
                    "name": "A",
                    "description": "a",
                    "write_globs": ["$unknown"],
                }
            ],
            "transitions": [],
        }
        machines_dir = temp_state_dir / "machines"
        machines_dir.mkdir(exist_ok=True)
        (machines_dir / "coder.json").write_text(json.dumps(machine_dict))
        monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)
        m = _sm.load_machine("coder")
        assert m is not None
        a = next(s for s in m.states if s.name == "A")
        assert a.write_globs == ["$unknown"]

    def test_mixed_variable_and_literal(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        machine_dict: dict[str, Any] = {
            "name": "test-mixed-var",
            "description": "t",
            "agent_role": "coder",
            "initial_state": "A",
            "terminal_states": ["A"],
            "variables": {"src": ["src/**/*.py"]},
            "states": [
                {
                    "name": "A",
                    "description": "a",
                    "write_globs": ["$src", "configs/**"],
                }
            ],
            "transitions": [],
        }
        machines_dir = temp_state_dir / "machines"
        machines_dir.mkdir(exist_ok=True)
        (machines_dir / "coder.json").write_text(json.dumps(machine_dict))
        monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)
        m = _sm.load_machine("coder")
        assert m is not None
        a = next(s for s in m.states if s.name == "A")
        assert a.write_globs == ["src/**/*.py", "configs/**"]

    def test_resolved_machine_cached(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.daemon.conftest import VARIABLE_MACHINE_DICT

        machines_dir = temp_state_dir / "machines"
        machines_dir.mkdir(exist_ok=True)
        (machines_dir / "coder.json").write_text(json.dumps(VARIABLE_MACHINE_DICT))
        monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)

        m1 = _sm.load_machine("coder")
        m2 = _sm.load_machine("coder")
        assert m1 is m2  # Same object from cache


# ============================================================================
# Chunk 3: New Guard Registrations
# ============================================================================
class TestNewGuards:
    """Tests for coder hardening guards (C1-C7)."""

    def test_approach_concern_flagged_true(self, temp_state_dir: Path) -> None:
        make_agent_in_state("coder-g1-t1-a1b2", "coder", "IMPL", approach_concern_flagged=True)
        _sm._agent_state_cache.clear()
        passed, _, _ = daemon.evaluate_guards(["approach_concern_flagged"], "coder-g1-t1-a1b2")
        assert passed

    def test_approach_concern_flagged_false(self, temp_state_dir: Path) -> None:
        make_agent_in_state("coder-g1-t1-a1b2", "coder", "IMPL")
        _sm._agent_state_cache.clear()
        passed, _, _ = daemon.evaluate_guards(["approach_concern_flagged"], "coder-g1-t1-a1b2")
        assert not passed

    def test_merge_conflicts_exist_true(self, temp_state_dir: Path) -> None:
        make_agent_in_state("coder-g1-t1-a1b2", "coder", "MERGE", merge_conflicts_exist=True)
        _sm._agent_state_cache.clear()
        passed, _, _ = daemon.evaluate_guards(["merge_conflicts_exist"], "coder-g1-t1-a1b2")
        assert passed

    def test_merge_conflicts_exist_false(self, temp_state_dir: Path) -> None:
        make_agent_in_state("coder-g1-t1-a1b2", "coder", "MERGE")
        _sm._agent_state_cache.clear()
        passed, _, _ = daemon.evaluate_guards(["merge_conflicts_exist"], "coder-g1-t1-a1b2")
        assert not passed

    def test_all_conflicts_resolved_true(self, temp_state_dir: Path) -> None:
        make_agent_in_state(
            "coder-g1-t1-a1b2", "coder", "MERGE_CONFLICT", all_conflicts_resolved=True
        )
        _sm._agent_state_cache.clear()
        passed, _, _ = daemon.evaluate_guards(["all_conflicts_resolved"], "coder-g1-t1-a1b2")
        assert passed

    def test_all_conflicts_resolved_false(self, temp_state_dir: Path) -> None:
        make_agent_in_state("coder-g1-t1-a1b2", "coder", "MERGE_CONFLICT")
        _sm._agent_state_cache.clear()
        passed, _, _ = daemon.evaluate_guards(["all_conflicts_resolved"], "coder-g1-t1-a1b2")
        assert not passed

    def test_tests_already_written_true(self, temp_state_dir: Path) -> None:
        make_agent_in_state(
            "coder-g1-t1-a1b2", "coder", "CONTEXT_LOADED", tests_already_written=True
        )
        _sm._agent_state_cache.clear()
        passed, _, _ = daemon.evaluate_guards(["tests_already_written"], "coder-g1-t1-a1b2")
        assert passed

    def test_tests_already_written_false(self, temp_state_dir: Path) -> None:
        make_agent_in_state("coder-g1-t1-a1b2", "coder", "CONTEXT_LOADED")
        _sm._agent_state_cache.clear()
        passed, _, _ = daemon.evaluate_guards(["tests_already_written"], "coder-g1-t1-a1b2")
        assert not passed

    def test_tests_not_yet_written_no_flag(self, temp_state_dir: Path) -> None:
        make_agent_in_state("coder-g1-t1-a1b2", "coder", "CONTEXT_LOADED")
        _sm._agent_state_cache.clear()
        passed, _, _ = daemon.evaluate_guards(["tests_not_yet_written"], "coder-g1-t1-a1b2")
        assert passed  # Negation: no flag → True

    def test_tests_not_yet_written_with_flag(self, temp_state_dir: Path) -> None:
        make_agent_in_state(
            "coder-g1-t1-a1b2", "coder", "CONTEXT_LOADED", tests_already_written=True
        )
        _sm._agent_state_cache.clear()
        passed, _, _ = daemon.evaluate_guards(["tests_not_yet_written"], "coder-g1-t1-a1b2")
        assert not passed  # Negation: flag set → False

    def test_auditor_found_test_issues_true(self, temp_state_dir: Path) -> None:
        make_agent_in_state(
            "coder-g1-t1-a1b2", "coder", "TASK_REVIEW_REQUESTED",
            auditor_found_test_issues=True,
        )
        _sm._agent_state_cache.clear()
        passed, _, _ = daemon.evaluate_guards(
            ["auditor_found_test_issues"], "coder-g1-t1-a1b2"
        )
        assert passed

    def test_auditor_found_test_issues_false(self, temp_state_dir: Path) -> None:
        make_agent_in_state("coder-g1-t1-a1b2", "coder", "TASK_REVIEW_REQUESTED")
        _sm._agent_state_cache.clear()
        passed, _, _ = daemon.evaluate_guards(
            ["auditor_found_test_issues"], "coder-g1-t1-a1b2"
        )
        assert not passed


# ============================================================================
# Chunk 4: Read Restriction Enforcement
# ============================================================================
class TestReadRestriction:
    """Tests for read_globs_exclude enforcement in check_tool_allowed()."""

    def test_read_blocked_by_read_globs_exclude(
        self, read_exclude_machine: StateMachineDefinition
    ) -> None:
        make_agent_in_state("coder-r1-t1-a1b2", "coder", "IMPL")
        _sm._agent_state_cache.clear()
        result = daemon.check_tool_allowed(
            "coder-r1-t1-a1b2", "Read", {"file_path": "tests/test_foo.py"}
        )
        assert not result["allowed"]
        assert "read_globs_exclude" in result["reason"]

    def test_read_allowed_outside_exclude(
        self, read_exclude_machine: StateMachineDefinition
    ) -> None:
        make_agent_in_state("coder-r1-t1-a1b2", "coder", "IMPL")
        _sm._agent_state_cache.clear()
        result = daemon.check_tool_allowed(
            "coder-r1-t1-a1b2", "Read", {"file_path": "src/main.py"}
        )
        assert result["allowed"]

    def test_read_no_exclude_allows_all(
        self, sample_machine: StateMachineDefinition
    ) -> None:
        make_agent_in_state("coder-r1-t1-a1b2", "coder", "WRITING")
        _sm._agent_state_cache.clear()
        result = daemon.check_tool_allowed(
            "coder-r1-t1-a1b2", "Read", {"file_path": "tests/test_foo.py"}
        )
        assert result["allowed"]

    def test_grep_blocked_by_read_globs_exclude(
        self, read_exclude_machine: StateMachineDefinition
    ) -> None:
        make_agent_in_state("coder-r1-t1-a1b2", "coder", "IMPL")
        _sm._agent_state_cache.clear()
        result = daemon.check_tool_allowed(
            "coder-r1-t1-a1b2", "Grep", {"path": "tests/test_foo.py"}
        )
        assert not result["allowed"]

    def test_glob_blocked_by_read_globs_exclude(
        self, read_exclude_machine: StateMachineDefinition
    ) -> None:
        make_agent_in_state("coder-r1-t1-a1b2", "coder", "IMPL")
        _sm._agent_state_cache.clear()
        result = daemon.check_tool_allowed(
            "coder-r1-t1-a1b2", "Glob", {"path": "tests/test_helpers.py"}
        )
        assert not result["allowed"]


# ============================================================================
# Chunk 5: C1 (GREEN_PROGRESS_CHECK) + C7 (INVARIANT_PROGRESS_CHECK)
# ============================================================================
class TestCoderSMLoads:
    """Verify the hardened coder SM loads and validates correctly."""

    def test_coder_sm_loads_without_error(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        machines_dir = temp_state_dir / "machines"
        machines_dir.mkdir(exist_ok=True)
        coder_path = Path(PROJECT_ROOT) / "state-machines" / "coder.json"
        (machines_dir / "coder.json").write_text(coder_path.read_text())
        monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)

        sm = _sm.load_machine("coder")
        assert sm is not None
        assert sm.name == "coder"

    def test_coder_sm_has_expected_state_count(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        machines_dir = temp_state_dir / "machines"
        machines_dir.mkdir(exist_ok=True)
        coder_path = Path(PROJECT_ROOT) / "state-machines" / "coder.json"
        (machines_dir / "coder.json").write_text(coder_path.read_text())
        monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)

        sm = _sm.load_machine("coder")
        assert sm is not None
        assert len(sm.states) == 27

    def test_coder_sm_terminal_states(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        machines_dir = temp_state_dir / "machines"
        machines_dir.mkdir(exist_ok=True)
        coder_path = Path(PROJECT_ROOT) / "state-machines" / "coder.json"
        (machines_dir / "coder.json").write_text(coder_path.read_text())
        monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)

        sm = _sm.load_machine("coder")
        assert sm is not None
        assert "PLAN_ERROR" in sm.terminal_states
        assert "RED_VERIFIED_COMPLETE" in sm.terminal_states
        assert len(sm.terminal_states) == 5

    def test_coder_sm_has_variables(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        machines_dir = temp_state_dir / "machines"
        machines_dir.mkdir(exist_ok=True)
        coder_path = Path(PROJECT_ROOT) / "state-machines" / "coder.json"
        (machines_dir / "coder.json").write_text(coder_path.read_text())
        monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)

        sm = _sm.load_machine("coder")
        assert sm is not None
        assert "source_globs" in sm.variables
        assert "test_globs" in sm.variables


def _load_coder_sm(
    temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> StateMachineDefinition:
    """Helper: load actual coder SM into temp machines dir."""
    machines_dir = temp_state_dir / "machines"
    machines_dir.mkdir(exist_ok=True)
    coder_path = Path(PROJECT_ROOT) / "state-machines" / "coder.json"
    (machines_dir / "coder.json").write_text(coder_path.read_text())
    monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)
    sm = _sm.load_machine("coder")
    assert sm is not None
    return sm


class TestGreenProgressCheck:
    """C1: TDD_GREEN routes through progress assessment, not max_occurrences."""

    def test_green_progress_check_has_think_on_exit(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        state = next(s for s in sm.states if s.name == "GREEN_PROGRESS_CHECK")
        assert state.think_on_exit is True

    def test_no_max_occurrences_on_green_loop(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        # No transition from TDD_GREEN should have max_occurrences
        green_transitions = [t for t in sm.transitions if t.from_state == "TDD_GREEN"]
        for t in green_transitions:
            assert t.max_occurrences is None

    def test_tdd_green_failing_routes_to_progress_check(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "TDD_GREEN" and t.to_state == "GREEN_PROGRESS_CHECK"
        )
        assert "pytest_exit_nonzero" in t.guards

    def test_green_progress_continue_to_implementation(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "GREEN_PROGRESS_CHECK"
            and t.to_state == "IMPLEMENTATION"
        )
        assert "think_chosen:CONTINUE" in t.guards

    def test_green_progress_stuck_to_approach_assessment(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "GREEN_PROGRESS_CHECK"
            and t.to_state == "APPROACH_ASSESSMENT"
        )
        assert "think_chosen:STUCK" in t.guards


class TestInvariantProgressCheck:
    """C7: INVARIANT_CHECK routes through progress assessment."""

    def test_invariant_progress_check_has_think_on_exit(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        state = next(
            s for s in sm.states if s.name == "INVARIANT_PROGRESS_CHECK"
        )
        assert state.think_on_exit is True

    def test_invariant_failure_routes_to_progress_check(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "INVARIANT_CHECK"
            and t.to_state == "INVARIANT_PROGRESS_CHECK"
        )
        assert "invariant_failure_exists" in t.guards

    def test_invariant_progress_continue_to_implementation(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "INVARIANT_PROGRESS_CHECK"
            and t.to_state == "IMPLEMENTATION"
        )
        assert "think_chosen:CONTINUE" in t.guards

    def test_invariant_progress_stuck_to_approach_assessment(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "INVARIANT_PROGRESS_CHECK"
            and t.to_state == "APPROACH_ASSESSMENT"
        )
        assert "think_chosen:STUCK" in t.guards


# ============================================================================
# Chunk 6: C2+C6 (APPROACH_ASSESSMENT + PLAN_ERROR)
# ============================================================================
class TestApproachAssessment:
    """C2+C6: Voluntary scrap and plan error escalation."""

    def test_approach_assessment_has_think_on_exit(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        state = next(s for s in sm.states if s.name == "APPROACH_ASSESSMENT")
        assert state.think_on_exit is True

    def test_implementation_concern_routes_to_approach_assessment(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "IMPLEMENTATION"
            and t.to_state == "APPROACH_ASSESSMENT"
        )
        assert "approach_concern_flagged" in t.guards

    def test_approach_continue_to_implementation(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "APPROACH_ASSESSMENT"
            and t.to_state == "IMPLEMENTATION"
        )
        assert "think_chosen:CONTINUE" in t.guards

    def test_approach_scrap_to_scrap_retry(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "APPROACH_ASSESSMENT"
            and t.to_state == "SCRAP_RETRY"
        )
        assert "think_chosen:SCRAP" in t.guards

    def test_approach_escalate_to_plan_error(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "APPROACH_ASSESSMENT"
            and t.to_state == "PLAN_ERROR"
        )
        assert "think_chosen:ESCALATE" in t.guards

    def test_approach_assessment_three_exclusive_exits(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        exits = [
            t for t in sm.transitions if t.from_state == "APPROACH_ASSESSMENT"
        ]
        assert len(exits) == 3
        chosen_values = set()
        for t in exits:
            for g in t.guards:
                if g.startswith("think_chosen:"):
                    chosen_values.add(g.split(":")[1])
        assert chosen_values == {"CONTINUE", "SCRAP", "ESCALATE"}

    def test_plan_error_is_terminal(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        assert "PLAN_ERROR" in sm.terminal_states

    def test_plan_error_allows_handoff_writes(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        state = next(s for s in sm.states if s.name == "PLAN_ERROR")
        assert state.write_allowed is True
        assert state.write_globs == [".claude/handoffs/**"]


# ============================================================================
# Chunk 7: C3 (MERGE_CONFLICT)
# ============================================================================
class TestMergeConflict:
    """C3: MERGE_CONFLICT state for manual conflict resolution."""

    def test_merge_clean_to_merge_resolved(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "MERGE"
            and t.to_state == "MERGE_RESOLVED"
            and t.trigger == "merge_successful"
        )
        assert "merge_exit_zero" in t.guards

    def test_merge_conflicts_to_merge_conflict(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "MERGE" and t.to_state == "MERGE_CONFLICT"
        )
        assert "merge_conflicts_exist" in t.guards

    def test_merge_conflict_allows_all_writes(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        state = next(s for s in sm.states if s.name == "MERGE_CONFLICT")
        assert state.write_allowed is True
        assert state.write_globs == ["**/*"]

    def test_merge_conflict_no_think_on_exit(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        state = next(s for s in sm.states if s.name == "MERGE_CONFLICT")
        assert state.think_on_exit is False

    def test_merge_conflict_resolved_to_merge_resolved(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "MERGE_CONFLICT"
            and t.to_state == "MERGE_RESOLVED"
        )
        assert "all_conflicts_resolved" in t.guards

    def test_merge_state_still_blocks_writes(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        state = next(s for s in sm.states if s.name == "MERGE")
        assert "Write" in state.blocked_tools
        assert "Edit" in state.blocked_tools


# ============================================================================
# Chunk 8: C4 (RED_VERIFIED_COMPLETE) + C5 (Variables) + FIXES
# ============================================================================
class TestMandatoryHandoff:
    """C4: Mandatory handoff at RED_VERIFIED for context isolation."""

    def test_red_verified_routes_to_red_verified_complete(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "RED_VERIFIED"
        )
        assert t.to_state == "RED_VERIFIED_COMPLETE"
        assert "think_completed" in t.guards

    def test_red_verified_complete_is_terminal(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        assert "RED_VERIFIED_COMPLETE" in sm.terminal_states

    def test_no_direct_red_verified_to_implementation(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        direct = [
            t
            for t in sm.transitions
            if t.from_state == "RED_VERIFIED" and t.to_state == "IMPLEMENTATION"
        ]
        assert len(direct) == 0

    def test_context_loaded_no_tests_routes_to_test_design(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "CONTEXT_LOADED" and t.to_state == "TEST_DESIGN"
        )
        assert "think_completed" in t.guards
        assert "tests_not_yet_written" in t.guards

    def test_context_loaded_with_tests_routes_to_implementation(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "CONTEXT_LOADED" and t.to_state == "IMPLEMENTATION"
        )
        assert "think_completed" in t.guards
        assert "tests_already_written" in t.guards

    def test_context_loaded_dual_exit_mutual_exclusivity(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """tests_already_written and tests_not_yet_written are mutual negations."""
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        exits = [
            t for t in sm.transitions if t.from_state == "CONTEXT_LOADED"
        ]
        assert len(exits) == 2
        guards_sets = [set(t.guards) for t in exits]
        # One has tests_not_yet_written, the other has tests_already_written
        all_guards = set()
        for gs in guards_sets:
            all_guards.update(gs)
        assert "tests_not_yet_written" in all_guards
        assert "tests_already_written" in all_guards

    def test_implementation_blocks_test_reads(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        state = next(s for s in sm.states if s.name == "IMPLEMENTATION")
        assert state.read_globs_exclude is not None
        assert len(state.read_globs_exclude) > 0
        # After variable resolution, should contain resolved test globs
        assert "tests/**/*.py" in state.read_globs_exclude

    def test_implementation_allows_source_writes(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        state = next(s for s in sm.states if s.name == "IMPLEMENTATION")
        assert state.write_globs is not None
        assert "src/**/*.py" in state.write_globs

    def test_auditor_test_issues_routes_to_scrap_retry(
        self, temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sm = _load_coder_sm(temp_state_dir, monkeypatch)
        t = next(
            t
            for t in sm.transitions
            if t.from_state == "TASK_REVIEW_REQUESTED"
            and t.trigger == "auditor_found_test_issues"
        )
        assert t.to_state == "SCRAP_RETRY"
        assert "auditor_found_test_issues" in t.guards
