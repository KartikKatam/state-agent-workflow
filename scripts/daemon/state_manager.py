"""State manager — paths, machine loading/caching, agent/system state CRUD, transition logging.

Extracted from scripts/workflow_state.py (Chunk 2 of daemon hardening plan).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path for schema imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from hooks.utils.state_helpers import (
    atomic_write,
    locked_read_modify_write,
)
from schemas.agent_state import AgentState
from schemas.state_machine import StateDefinition, StateMachineDefinition
from schemas.system_state import WorkflowState

_log = logging.getLogger("workflow_state")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
STATE_DIR = Path.home() / ".claude" / "state"
AGENTS_DIR = STATE_DIR / "agents"
SYSTEM_STATE_FILE = STATE_DIR / "system.json"
MACHINES_DIR = Path(__file__).resolve().parent.parent.parent / "state-machines"
TRANSITION_LOG = Path.home() / ".claude" / "logs" / "state-transitions.jsonl"
ANNOTATIONS_DIR = Path.home() / ".claude" / "annotations"
TEMP_DIR = Path.home() / ".claude" / "temp"
DECISIONS_LOG_DIR = Path.home() / ".claude" / "logs" / "decisions"
MESSAGE_BUS_LOG = Path.home() / ".claude" / "logs" / "message-bus.jsonl"


def _socket_path(workflow_id: str) -> str:
    return f"/tmp/workflow-state-{workflow_id}.sock"


def _pid_path(workflow_id: str) -> str:
    return f"/tmp/workflow-state-{workflow_id}.pid"


def _project_dir() -> Path:
    """Resolve the project directory."""
    return Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()


# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------
_agent_state_cache: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Session registry (in-memory — daemon is the liveness authority)
# ---------------------------------------------------------------------------
_session_registry: dict[str, dict] = {}  # agent_id → {team_id, role, workflow_id, ...}


def register_session(
    agent_id: str,
    team_id: str,
    role: str,
    workflow_id: str,
) -> None:
    """Register an agent session in the daemon's in-memory registry.

    Called during agent registration (slash command init).
    The registry is the authority on which sessions are alive.
    """
    _session_registry[agent_id] = {
        "agent_id": agent_id,
        "team_id": team_id,
        "role": role,
        "workflow_id": workflow_id,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "status": "active",
    }


def touch_session(agent_id: str) -> None:
    """Update last_seen timestamp. Called on every hook invocation."""
    entry = _session_registry.get(agent_id)
    if entry:
        entry["last_seen"] = datetime.now(timezone.utc).isoformat()


def depart_session(agent_id: str) -> None:
    """Mark a session as departed (agent left team or session ended)."""
    entry = _session_registry.get(agent_id)
    if entry:
        entry["status"] = "departed"


def get_session(agent_id: str) -> dict | None:
    """Get session registry entry for an agent."""
    return _session_registry.get(agent_id)


def get_team_sessions(team_id: str) -> list[dict]:
    """Get all active sessions for a team."""
    return [
        entry for entry in _session_registry.values()
        if entry["team_id"] == team_id and entry["status"] == "active"
    ]


def resolve_role_in_team(team_id: str, role: str) -> list[str]:
    """Find agent_ids for a given role in a team. Returns list (may be multiple coders)."""
    return [
        entry["agent_id"]
        for entry in _session_registry.values()
        if entry["team_id"] == team_id
        and entry["role"] == role
        and entry["status"] == "active"
    ]


def get_stale_sessions(timeout_seconds: int = 300) -> list[dict]:
    """Find sessions that haven't called in within timeout. Returns stale entries."""
    now = datetime.now(timezone.utc)
    stale = []
    for entry in _session_registry.values():
        if entry["status"] != "active":
            continue
        last_seen = datetime.fromisoformat(entry["last_seen"])
        if (now - last_seen).total_seconds() > timeout_seconds:
            stale.append(entry)
    return stale


def _get_agent_state_cached(agent_id: str) -> dict | None:
    """Get agent state dict from cache, falling back to disk read."""
    cached = _agent_state_cache.get(agent_id)
    if cached is not None:
        return cached
    agent = get_agent_state(agent_id)
    if agent is None:
        return None
    state_dict = json.loads(agent.model_dump_json())
    _agent_state_cache[agent_id] = state_dict
    return state_dict


def _invalidate_agent_cache(agent_id: str) -> None:
    """Invalidate cached agent state (call after state changes)."""
    _agent_state_cache.pop(agent_id, None)


# ---------------------------------------------------------------------------
# Machine loading (cached)
# ---------------------------------------------------------------------------
_machine_cache: dict[str, StateMachineDefinition] = {}


def _resolve_variables(machine: StateMachineDefinition) -> StateMachineDefinition:
    """Resolve $variable references in state definition glob fields.

    For each entry in write_globs / read_globs_exclude that starts with '$',
    look up machine.variables[name] and replace the single entry with the
    full list from the variable. Non-variable entries pass through unchanged.
    Unknown $references stay as literals (no crash).
    """
    variables = machine.variables

    def _resolve_glob_list(globs: list[str] | None) -> list[str] | None:
        if globs is None:
            return None
        resolved: list[str] = []
        for g in globs:
            if g.startswith("$") and g[1:] in variables:
                resolved.extend(variables[g[1:]])
            else:
                resolved.append(g)
        return resolved

    new_states = []
    changed = False
    for state in machine.states:
        new_wg = _resolve_glob_list(state.write_globs)
        new_rge = _resolve_glob_list(state.read_globs_exclude)
        if new_wg != state.write_globs or new_rge != state.read_globs_exclude:
            state_dict = state.model_dump()
            state_dict["write_globs"] = new_wg
            state_dict["read_globs_exclude"] = new_rge
            new_states.append(StateDefinition.model_validate(state_dict))
            changed = True
        else:
            new_states.append(state)

    if changed:
        machine_dict = machine.model_dump()
        machine_dict["states"] = [s.model_dump() for s in new_states]
        return StateMachineDefinition.model_validate(machine_dict)
    return machine


def load_machine(machine_id: str) -> StateMachineDefinition | None:
    """Load a state machine definition. Cached after first load."""
    if machine_id in _machine_cache:
        return _machine_cache[machine_id]

    path = MACHINES_DIR / f"{machine_id}.json"
    if not path.exists():
        return None

    machine = StateMachineDefinition.model_validate_json(path.read_text())

    # Resolve $variable references in glob fields
    if machine.variables:
        machine = _resolve_variables(machine)

    _machine_cache[machine_id] = machine
    return machine


def resolve_machine(role: str, current_state: str) -> StateMachineDefinition | None:
    """Resolve the correct state machine for an agent role.

    Special cases:
    - orchestrator -> system machine
    - generalist -> None (no restrictions)
    - auditor -> try auditor-task and auditor-phase, match by current_state
    """
    if role == "generalist":
        return None
    if role == "orchestrator":
        return load_machine("system")
    if role == "auditor":
        for name in ("auditor-task", "auditor-phase"):
            machine = load_machine(name)
            if machine and any(s.name == current_state for s in machine.states):
                return machine
        return load_machine("auditor-task")  # default
    return load_machine(role)


# ---------------------------------------------------------------------------
# Agent state management
# ---------------------------------------------------------------------------
def get_agent_state(agent_id: str) -> AgentState | None:
    """Read agent state file. Returns None if missing or corrupt."""
    path = AGENTS_DIR / f"{agent_id}.json"
    if not path.exists():
        return None
    try:
        return AgentState.model_validate_json(path.read_text())
    except Exception:
        return None


def save_agent_state(agent: AgentState) -> None:
    """Write agent state file."""
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = AGENTS_DIR / f"{agent.id}.json"
    atomic_write(path, agent.model_dump_json(indent=2, exclude_defaults=True, exclude_none=True))


def get_system_state() -> WorkflowState | None:
    """Read system state file."""
    if not SYSTEM_STATE_FILE.exists():
        return None
    try:
        return WorkflowState.model_validate_json(SYSTEM_STATE_FILE.read_text())
    except Exception:
        return None


def save_system_state(state: WorkflowState) -> None:
    """Write system state file."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state.last_updated = datetime.now(timezone.utc)
    atomic_write(SYSTEM_STATE_FILE, state.model_dump_json(indent=2, exclude_defaults=True, exclude_none=True))


# ---------------------------------------------------------------------------
# Transition logging
# ---------------------------------------------------------------------------
def log_transition(
    entity_id: str,
    machine_name: str,
    from_state: str,
    to_state: str,
    trigger: str,
    guard_results: dict[str, bool] | None = None,
) -> None:
    """Append a transition to the JSONL log."""
    TRANSITION_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "entity_id": entity_id,
        "machine": machine_name,
        "from_state": from_state,
        "to_state": to_state,
        "trigger": trigger,
    }
    if guard_results:
        entry["guard_results"] = guard_results
    with TRANSITION_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def count_transition_occurrences(entity_id: str, from_state: str, to_state: str) -> int:
    """Count how many times a specific transition has fired for an entity."""
    if not TRANSITION_LOG.exists():
        return 0
    count = 0
    for line in TRANSITION_LOG.read_text().splitlines():
        try:
            entry = json.loads(line)
            if (
                entry.get("entity_id") == entity_id
                and entry.get("from_state") == from_state
                and entry.get("to_state") == to_state
            ):
                count += 1
        except json.JSONDecodeError:
            continue
    return count


# ---------------------------------------------------------------------------
# Agent registration and context
# ---------------------------------------------------------------------------
def register_agent(
    agent_id: str,
    role: str,
    model: str,
    phase: str | None = None,
    task: str | None = None,
    parent_agent_id: str | None = None,
    worktree: str | None = None,
    base_branch: str | None = None,
    team_id: str | None = None,
    workflow_id: str | None = None,
) -> dict:
    """Create a new agent state file. Returns the created state."""
    machine = resolve_machine(role, "SPAWNED")
    initial_state = "SPAWNED"
    if machine:
        initial_state = machine.initial_state

    agent = AgentState(
        id=agent_id,
        role=role,  # pyright: ignore[reportArgumentType]
        model=model,  # pyright: ignore[reportArgumentType]
        spawned_at=datetime.now(timezone.utc),
        status="active",
        current_state=initial_state,
        phase=phase,
        task=task,
        parent_agent_id=parent_agent_id,
        worktree=worktree,
        base_branch=base_branch,
        team_id=team_id,
    )
    save_agent_state(agent)

    # Populate cache
    agent_dict = json.loads(agent.model_dump_json())
    _agent_state_cache[agent_id] = agent_dict

    # Log initial state
    if machine:
        log_transition(agent_id, machine.name, "", initial_state, "spawn")

    # Register in session registry if team_id provided
    if team_id:
        register_session(agent_id, team_id, role, workflow_id or "default")

    return {"ok": True, "agent": agent_dict}


def update_agent_context(agent_id: str, input_tokens: int, output_tokens: int) -> dict:
    """Update an agent's token counts and context usage percentage."""
    agent = get_agent_state(agent_id)
    if not agent:
        return {"ok": False, "reason": "Agent state not found"}

    agent.input_tokens = input_tokens
    agent.output_tokens = output_tokens
    # Estimate context usage: assume 200k token window
    total = input_tokens + output_tokens
    agent.context_usage_pct = min(100.0, (total / 200_000) * 100)
    save_agent_state(agent)

    return {"ok": True, "context_usage_pct": agent.context_usage_pct}


# ---------------------------------------------------------------------------
# Low-level dict readers (for guards — avoids Pydantic overhead)
# ---------------------------------------------------------------------------
def _get_agent_state_dict(agent_id: str) -> dict | None:
    """Read agent state as a plain dict (avoids Pydantic overhead in guards)."""
    path = AGENTS_DIR / f"{agent_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _get_system_state_dict() -> dict | None:
    """Read system state as a plain dict."""
    if not SYSTEM_STATE_FILE.exists():
        return None
    try:
        return json.loads(SYSTEM_STATE_FILE.read_text())
    except Exception:
        return None


def _get_gate_result() -> dict | None:
    """Read the most recent quality gate result."""
    gate_file = _project_dir() / ".claude" / "state" / "last-gate-result.json"
    if not gate_file.exists():
        return None
    try:
        return json.loads(gate_file.read_text())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Pending state helpers
# ---------------------------------------------------------------------------
def _set_pending_critical(agent_id: str, annotation: dict) -> None:
    """Set pending_critical_annotation in agent state file."""
    try:
        path = AGENTS_DIR / f"{agent_id}.json"

        def _update(data: dict) -> dict:
            data["pending_critical_annotation"] = annotation
            return data

        locked_read_modify_write(path, _update)
        _invalidate_agent_cache(agent_id)
    except Exception:
        pass


def _set_pending_handoff(agent_id: str) -> None:
    """Set pending_handoff=True in agent state file."""
    try:
        path = AGENTS_DIR / f"{agent_id}.json"

        def _update(data: dict) -> dict:
            data["pending_handoff"] = True
            return data

        locked_read_modify_write(path, _update)
        _invalidate_agent_cache(agent_id)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Async writer layer
# ---------------------------------------------------------------------------
def _async_write(writes: list[Callable[[], None]]) -> None:
    """Execute write operations in a background daemon thread."""
    threading.Thread(target=lambda: [fn() for fn in writes], daemon=True).start()
