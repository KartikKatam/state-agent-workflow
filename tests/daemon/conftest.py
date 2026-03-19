"""Shared fixtures for daemon tests.

Provides isolated state directories, sample state machines, and registered
agent instances so tests never touch real ~/.claude/ state.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.workflow_state as daemon
import scripts.daemon.state_manager as _sm
import scripts.daemon.transitions as _tr
from schemas.agent_state import AgentRole, AgentState
from schemas.state_machine import StateMachineDefinition


# ---------------------------------------------------------------------------
# Minimal state machine for testing (no guarded transitions)
# ---------------------------------------------------------------------------
SAMPLE_MACHINE_DICT: dict[str, Any] = {
    "name": "test-coder",
    "description": "Minimal test coder machine",
    "version": "1.0.0",
    "agent_role": "coder",
    "initial_state": "IDLE",
    "terminal_states": ["DONE"],
    "universal_transitions": [],
    "states": [
        {
            "name": "IDLE",
            "description": "Idle — no writes",
            "write_allowed": False,
        },
        {
            "name": "WRITING",
            "description": "Writing allowed in src only",
            "write_allowed": True,
            "write_globs": ["src/**/*.py"],
        },
        {
            "name": "BLOCKED_BASH",
            "description": "Bash explicitly blocked",
            "write_allowed": True,
            "blocked_tools": ["Bash"],
        },
        {
            "name": "THINK_REQUIRED",
            "description": "Must think before leaving",
            "write_allowed": True,
            "think_on_exit": True,
            "think_prompt": "Reflect on your implementation approach",
            "post_actions": {"Write": ["ruff_lint_critical"], "*": ["validate_context_packet_schema"]},
        },
        {
            "name": "UNRESTRICTED",
            "description": "No restrictions",
            "write_allowed": True,
        },
        {
            "name": "DONE",
            "description": "Terminal",
        },
    ],
    "transitions": [
        {
            "from_state": "IDLE",
            "to_state": "WRITING",
            "trigger": "start_writing",
        },
        {
            "from_state": "IDLE",
            "to_state": "BLOCKED_BASH",
            "trigger": "enter_blocked",
        },
        {
            "from_state": "IDLE",
            "to_state": "THINK_REQUIRED",
            "trigger": "enter_think",
        },
        {
            "from_state": "IDLE",
            "to_state": "UNRESTRICTED",
            "trigger": "enter_unrestricted",
        },
        {
            "from_state": "WRITING",
            "to_state": "DONE",
            "trigger": "finish",
        },
        {
            "from_state": "WRITING",
            "to_state": "IDLE",
            "trigger": "loop_back",
            "max_occurrences": 2,
        },
        {
            "from_state": "BLOCKED_BASH",
            "to_state": "DONE",
            "trigger": "finish",
        },
        {
            "from_state": "THINK_REQUIRED",
            "to_state": "DONE",
            "trigger": "finish",
            "guards": ["think_completed"],
        },
        {
            "from_state": "UNRESTRICTED",
            "to_state": "DONE",
            "trigger": "finish",
        },
    ],
}


# ---------------------------------------------------------------------------
# Machine with guarded transitions for auto-transition testing
# ---------------------------------------------------------------------------
GUARDED_MACHINE_DICT: dict[str, Any] = {
    "name": "test-coder-guarded",
    "description": "Coder machine with guarded auto-transitions",
    "version": "1.0.0",
    "agent_role": "coder",
    "initial_state": "STATE_A",
    "terminal_states": ["STATE_C"],
    "universal_transitions": [],
    "states": [
        {
            "name": "STATE_A",
            "description": "Initial state, writes allowed",
            "write_allowed": True,
        },
        {
            "name": "STATE_B",
            "description": "Think required state",
            "write_allowed": True,
            "think_on_exit": True,
            "think_prompt": "Think about design",
            "post_actions": {"Write": ["ruff_lint_critical"]},
        },
        {
            "name": "STATE_NO_THINK",
            "description": "Non-think state (no think_on_exit)",
            "write_allowed": True,
        },
        {
            "name": "STATE_C",
            "description": "Terminal state",
        },
    ],
    "transitions": [
        {
            "from_state": "STATE_A",
            "to_state": "STATE_B",
            "trigger": "auto_guard_pass",
            "guards": ["always_true"],
        },
        {
            "from_state": "STATE_A",
            "to_state": "STATE_NO_THINK",
            "trigger": "auto_no_think",
            "guards": ["always_false"],
        },
        {
            "from_state": "STATE_A",
            "to_state": "STATE_C",
            "trigger": "auto_guard_fail",
            "guards": ["always_false"],
        },
        {
            "from_state": "STATE_B",
            "to_state": "STATE_C",
            "trigger": "finish",
        },
        {
            "from_state": "STATE_NO_THINK",
            "to_state": "STATE_C",
            "trigger": "finish",
        },
    ],
}


def _guard_always_true(
    _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    return True


def _guard_always_false(
    _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    return False


@pytest.fixture()
def temp_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect all daemon state paths to a temp directory.

    Returns the base temp path. Creates required subdirectories.
    """
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    annotations_dir = tmp_path / "annotations"
    annotations_dir.mkdir()

    # Patch the actual source module where functions read these variables
    monkeypatch.setattr(_sm, "STATE_DIR", tmp_path)
    monkeypatch.setattr(_sm, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(_sm, "SYSTEM_STATE_FILE", tmp_path / "system.json")
    monkeypatch.setattr(_sm, "TRANSITION_LOG", logs_dir / "state-transitions.jsonl")
    monkeypatch.setattr(_sm, "ANNOTATIONS_DIR", annotations_dir)

    # Also patch re-exported names in workflow_state for tests that read them
    monkeypatch.setattr(daemon, "STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(daemon, "SYSTEM_STATE_FILE", tmp_path / "system.json")
    monkeypatch.setattr(daemon, "TRANSITION_LOG", logs_dir / "state-transitions.jsonl")
    monkeypatch.setattr(daemon, "ANNOTATIONS_DIR", annotations_dir)

    # Clear caches between tests
    _sm._machine_cache.clear()
    _sm._agent_state_cache.clear()
    _tr._post_tool_call_count.clear()
    _tr._sent_context_warnings.clear()

    return tmp_path


@pytest.fixture()
def sample_machine(
    temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> StateMachineDefinition:
    """Write the sample state machine JSON and point MACHINES_DIR at it.

    Returns the validated StateMachineDefinition.
    """
    machines_dir = temp_state_dir / "machines"
    machines_dir.mkdir()
    (machines_dir / "coder.json").write_text(json.dumps(SAMPLE_MACHINE_DICT))

    monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)

    return StateMachineDefinition.model_validate(SAMPLE_MACHINE_DICT)


@pytest.fixture()
def guarded_machine(
    temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> StateMachineDefinition:
    """Write the guarded state machine and register custom guards.

    Returns the validated StateMachineDefinition. Registers always_true
    and always_false guards in the guard registry.
    """
    machines_dir = temp_state_dir / "machines"
    machines_dir.mkdir(exist_ok=True)
    (machines_dir / "coder.json").write_text(json.dumps(GUARDED_MACHINE_DICT))

    monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)

    # Register test guards
    daemon._GUARD_REGISTRY["always_true"] = _guard_always_true
    daemon._GUARD_REGISTRY["always_false"] = _guard_always_false

    return StateMachineDefinition.model_validate(GUARDED_MACHINE_DICT)


@pytest.fixture()
def registered_agent(
    temp_state_dir: Path, sample_machine: StateMachineDefinition
) -> AgentState:
    """Register a coder agent using the sample machine. Starts in IDLE."""
    result = daemon.register_agent(
        agent_id="coder-p1-t1-a1b2",
        role="coder",
        model="opus-4-6",
        phase="p1",
        task="t1",
    )
    assert result["ok"]
    agent = daemon.get_agent_state("coder-p1-t1-a1b2")
    assert agent is not None
    assert agent.current_state == "IDLE"
    return agent


# ---------------------------------------------------------------------------
# Machine with decision guards (think_chosen routing)
# ---------------------------------------------------------------------------
DECISION_MACHINE_DICT: dict[str, Any] = {
    "name": "test-decision",
    "description": "Machine with decision guards for think_chosen routing",
    "version": "1.0.0",
    "agent_role": "coder",
    "initial_state": "ASSESS",
    "terminal_states": ["DONE"],
    "universal_transitions": [],
    "states": [
        {
            "name": "ASSESS",
            "description": "Decision state — must think before leaving",
            "write_allowed": True,
            "think_on_exit": True,
            "think_prompt": "1) Is the code correct? 2) What approach? CHOSEN: APPROVE or RETRY",
        },
        {
            "name": "APPROVED",
            "description": "Approved path",
            "write_allowed": True,
        },
        {
            "name": "RETRY",
            "description": "Retry path",
            "write_allowed": True,
        },
        {
            "name": "DONE",
            "description": "Terminal",
        },
    ],
    "transitions": [
        {
            "from_state": "ASSESS",
            "to_state": "APPROVED",
            "trigger": "approved",
            "guards": ["think_chosen:APPROVE"],
        },
        {
            "from_state": "ASSESS",
            "to_state": "RETRY",
            "trigger": "retry",
            "guards": ["think_chosen:RETRY"],
        },
        {
            "from_state": "APPROVED",
            "to_state": "DONE",
            "trigger": "finish",
            "guards": ["think_completed"],
        },
        {
            "from_state": "RETRY",
            "to_state": "ASSESS",
            "trigger": "reassess",
            "guards": ["think_completed"],
        },
    ],
}


# ---------------------------------------------------------------------------
# Machine for ambiguity detection testing
# ---------------------------------------------------------------------------
AMBIGUITY_MACHINE_DICT: dict[str, Any] = {
    "name": "test-ambiguity",
    "description": "Machine with ambiguous transitions for testing",
    "version": "1.0.0",
    "agent_role": "coder",
    "initial_state": "START",
    "terminal_states": ["END_A", "END_B"],
    "universal_transitions": [],
    "states": [
        {"name": "START", "description": "Start", "write_allowed": True},
        {"name": "END_A", "description": "End A"},
        {"name": "END_B", "description": "End B"},
    ],
    "transitions": [
        {
            "from_state": "START",
            "to_state": "END_A",
            "trigger": "go_a",
            "guards": ["always_true"],
        },
        {
            "from_state": "START",
            "to_state": "END_B",
            "trigger": "go_b",
            "guards": ["always_true"],
        },
    ],
}


@pytest.fixture()
def decision_machine(
    temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> StateMachineDefinition:
    """Write the decision machine with think_chosen/think_completed guards.

    Returns the validated StateMachineDefinition.
    """
    machines_dir = temp_state_dir / "machines"
    machines_dir.mkdir(exist_ok=True)
    (machines_dir / "coder.json").write_text(json.dumps(DECISION_MACHINE_DICT))

    monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)

    return StateMachineDefinition.model_validate(DECISION_MACHINE_DICT)


@pytest.fixture()
def ambiguity_machine(
    temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> StateMachineDefinition:
    """Write the ambiguity machine with two always_true transitions from START.

    Registers always_true guard. Returns the validated StateMachineDefinition.
    """
    machines_dir = temp_state_dir / "machines"
    machines_dir.mkdir(exist_ok=True)
    (machines_dir / "coder.json").write_text(json.dumps(AMBIGUITY_MACHINE_DICT))

    monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)

    daemon._GUARD_REGISTRY["always_true"] = _guard_always_true

    return StateMachineDefinition.model_validate(AMBIGUITY_MACHINE_DICT)


def make_agent_in_state(
    agent_id: str, role: AgentRole, state: str, **extra: Any
) -> AgentState:
    """Create and save an agent state file directly (bypassing registration).

    Extra kwargs that are valid AgentState fields are passed to the constructor.
    Unknown extras (runtime flags like approach_concern_flagged) are written
    directly to the JSON file, since guards read them from the raw dict.
    """
    from pydantic import BaseModel as _BaseModel

    # Split extras: known AgentState fields vs runtime flags
    known_fields = AgentState.model_fields
    agent_kwargs: dict[str, Any] = {}
    runtime_flags: dict[str, Any] = {}
    for k, v in extra.items():
        if k in known_fields:
            agent_kwargs[k] = v
        else:
            runtime_flags[k] = v

    agent = AgentState(
        id=agent_id,
        role=role,
        model="opus-4-6",
        spawned_at=datetime.now(timezone.utc),
        status="active",
        current_state=state,
        **agent_kwargs,
    )
    daemon.save_agent_state(agent)

    # Patch runtime flags directly into the JSON file (guards read raw dict)
    if runtime_flags:
        path = _sm.AGENTS_DIR / f"{agent_id}.json"
        data = json.loads(path.read_text())
        for k, v in runtime_flags.items():
            # Handle Pydantic models in values
            if isinstance(v, _BaseModel):
                data[k] = v.model_dump()
            else:
                data[k] = v
        path.write_text(json.dumps(data, indent=2))

    return agent


# ---------------------------------------------------------------------------
# Machine with variable references (for coder hardening tests)
# ---------------------------------------------------------------------------
VARIABLE_MACHINE_DICT: dict[str, Any] = {
    "name": "test-variable",
    "description": "Machine with $variable references in globs",
    "version": "1.0.0",
    "agent_role": "coder",
    "initial_state": "WORK",
    "terminal_states": ["DONE"],
    "variables": {
        "source_globs": ["src/**/*.py"],
        "test_globs": ["tests/**/*.py", "**/test_*.py", "**/conftest.py"],
    },
    "states": [
        {
            "name": "WORK",
            "description": "Working state with variable globs",
            "write_allowed": True,
            "write_globs": ["$source_globs"],
            "read_globs_exclude": ["$test_globs"],
        },
        {
            "name": "DONE",
            "description": "Terminal",
        },
    ],
    "transitions": [
        {"from_state": "WORK", "to_state": "DONE", "trigger": "finish"},
    ],
}


@pytest.fixture()
def variable_machine(
    temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> StateMachineDefinition:
    """Write the variable machine and return resolved definition."""
    machines_dir = temp_state_dir / "machines"
    machines_dir.mkdir(exist_ok=True)
    (machines_dir / "coder.json").write_text(json.dumps(VARIABLE_MACHINE_DICT))

    monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)

    # Load via load_machine to trigger variable resolution
    return _sm.load_machine("coder")  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Machine with read_globs_exclude (for read restriction tests)
# ---------------------------------------------------------------------------
READ_EXCLUDE_MACHINE_DICT: dict[str, Any] = {
    "name": "test-read-exclude",
    "description": "Machine with read_globs_exclude",
    "version": "1.0.0",
    "agent_role": "coder",
    "initial_state": "IMPL",
    "terminal_states": ["DONE"],
    "states": [
        {
            "name": "IMPL",
            "description": "Implementation - cannot read tests",
            "write_allowed": True,
            "write_globs": ["src/**/*.py"],
            "read_globs_exclude": ["tests/**/*.py", "**/test_*.py", "**/conftest.py"],
        },
        {
            "name": "DONE",
            "description": "Terminal",
        },
    ],
    "transitions": [
        {"from_state": "IMPL", "to_state": "DONE", "trigger": "finish"},
    ],
}


@pytest.fixture()
def read_exclude_machine(
    temp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> StateMachineDefinition:
    """Write the read-exclude machine."""
    machines_dir = temp_state_dir / "machines"
    machines_dir.mkdir(exist_ok=True)
    (machines_dir / "coder.json").write_text(json.dumps(READ_EXCLUDE_MACHINE_DICT))

    monkeypatch.setattr(_sm, "MACHINES_DIR", machines_dir)

    return StateMachineDefinition.model_validate(READ_EXCLUDE_MACHINE_DICT)
