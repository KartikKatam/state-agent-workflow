#!/usr/bin/env python3
"""
SessionStart Hook — Agent registration, state initialization, preferences loading.

Fires at: session startup, resume, clear, compact
Configured in: ~/.claude/settings.json or .claude/settings.json

Two modes:
1. Workflow agent (CLAUDE_CODE_AGENT_NAME set):
   - Creates agent state file, loads preferences, registers agent
   - Handoff context is in the agent's spawn prompt (orchestrator's job)

2. Orchestrator / non-workflow session (no CLAUDE_CODE_AGENT_NAME):
   - Checks for in-progress workflow state
   - Reports resumption context if unfinished work exists
   - Minimal setup otherwise

Output: JSON to stdout (HookOutput format)
Environment: Writes AGENT_ID/AGENT_ROLE to $CLAUDE_ENV_FILE if available
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Import hook utilities (from task #17)
# ---------------------------------------------------------------------------

# Add project root to path so imports work when invoked as a script
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from hooks.utils.error_logger import log_hook_error
from hooks.utils.event_logger import emit_agent_registered, rotate_events_file
from hooks.utils.state_helpers import (
    AGENT_STATE_DIR,
    atomic_write,
    ensure_dirs,
    get_agent_id,
    read_json_safe,
    read_stdin,
)

# ---------------------------------------------------------------------------
# Import Pydantic models (from task #2)
# ---------------------------------------------------------------------------

AgentState: type | None = None
Preferences: type | None = None

try:
    from schemas.agent_state import AgentState  # type: ignore[assignment]
except ImportError:
    pass

try:
    from schemas.preferences import Preferences  # type: ignore[assignment]
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CLAUDE_HOME = Path(os.path.expanduser("~/.claude"))
_PTC_DAEMON = _CLAUDE_HOME / "mcp" / "ptc-server" / "ptc-daemon.sh"
WORKFLOW_STATE_FILE = _CLAUDE_HOME / "state" / "workflow.json"
PREFERENCES_FILE = _CLAUDE_HOME / "state" / "preferences.json"
HANDOFFS_DIR = Path(".claude/handoffs")  # Relative to project dir
PRE_COMPACT_SNAPSHOTS_DIR = _CLAUDE_HOME / "state" / "pre-compact-snapshots"


def _ensure_ptc_daemon() -> None:
    """Start PTC daemon if not already running. Fail silently."""
    if not _PTC_DAEMON.exists():
        return
    try:
        status = subprocess.run(
            [str(_PTC_DAEMON), "status"],
            capture_output=True, text=True, timeout=5,
        )
        if status.returncode == 0:
            return  # Already running

        subprocess.run(
            [str(_PTC_DAEMON), "start"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        log_hook_error("session_start", "_ensure_ptc_daemon", e)


def main() -> None:
    """Entry point. Reads stdin, determines mode, outputs hook JSON."""
    # Read stdin (Claude Code may pass session event context)
    stdin_data = read_stdin()

    # Ensure directories exist (safe for all sessions)
    ensure_dirs()

    # Ensure PTC daemon is running (SSE transport)
    _ensure_ptc_daemon()

    agent_id = get_agent_id()

    if agent_id != "unknown":
        # Workflow agent — full registration
        _handle_agent_session(agent_id, stdin_data)
    else:
        # Orchestrator or non-workflow session
        _handle_orchestrator_session(stdin_data)


# ---------------------------------------------------------------------------
# Agent session (CLAUDE_CODE_AGENT_NAME is set)
# ---------------------------------------------------------------------------


def _handle_agent_session(agent_id: str, stdin_data: dict) -> None:
    """Full agent registration: state file, preferences, env vars, events."""

    source = stdin_data.get("source", "startup")

    # Rotate events file on fresh startup (not on compact/resume)
    if source == "startup":
        rotate_events_file()

    # Build agent state
    state_file = AGENT_STATE_DIR / f"{agent_id}.json"
    existing_state = read_json_safe(state_file)

    if existing_state:
        # Agent state already exists (resume/compact scenario)
        # Don't overwrite — just reload preferences
        role = existing_state.get("role", "unknown")
    else:
        # Fresh agent — create state file from spawn context
        role = _create_agent_state(agent_id, stdin_data, state_file)

    # Load preferences
    prefs_context = _load_preferences(role)

    # Write env vars
    _write_env_vars(agent_id, role)

    # Emit registration event
    emit_agent_registered(agent_id, role)

    # Build additionalContext
    context_parts = [
        f"Agent ID: {agent_id}",
        f"Role: {role}",
    ]
    if prefs_context:
        context_parts.append("")
        context_parts.append(prefs_context)

    # Load pre-compact continuation message (if resuming after compaction)
    if source == "compact":
        continuation = _load_pre_compact_continuation(agent_id)
        if continuation:
            context_parts.append("")
            context_parts.append(continuation)

    _output_hook_result("\n".join(context_parts))


def _create_agent_state(agent_id: str, stdin_data: dict, state_file: Path) -> str:
    """Create a new agent state file. Returns the agent role."""

    # Extract metadata from stdin (orchestrator passes this in spawn context)
    # or from environment variables as fallback
    role = stdin_data.get("role") or os.environ.get("AGENT_ROLE", "unknown")
    model = stdin_data.get("model") or os.environ.get("AGENT_MODEL", "opus-4-6")
    phase = stdin_data.get("phase") or os.environ.get("AGENT_PHASE")
    task = stdin_data.get("task") or os.environ.get("AGENT_TASK")
    parent = stdin_data.get("parent_agent_id") or os.environ.get("PARENT_AGENT_ID")
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")

    now = datetime.now(timezone.utc).isoformat()

    state_data = {
        "id": agent_id,
        "role": role,
        "model": model,
        "spawned_at": now,
        "status": "active",
        "current_state": "SPAWNED",
        "phase": phase,
        "task": task,
        "context_usage_pct": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "worktree": None,
        "base_branch": None,
        "parent_agent_id": parent,
        "session_id": session_id,
    }

    # Validate with Pydantic if available
    if AgentState is not None:
        try:
            validated = AgentState.model_validate(state_data)
            state_data = validated.model_dump(mode="json", exclude_defaults=True, exclude_none=True)
        except Exception as e:
            log_hook_error("session_start", "_create_agent_state/validate", e)

    # Write state file
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(state_file, json.dumps(state_data, indent=2, default=str))
    except Exception as e:
        log_hook_error("session_start", "_create_agent_state/write", e)

    return role


# ---------------------------------------------------------------------------
# Orchestrator / non-workflow session
# ---------------------------------------------------------------------------


def _handle_orchestrator_session(stdin_data: dict) -> None:
    """Check for in-progress workflow and report resumption context."""

    source = stdin_data.get("source", "startup")

    # Rotate events file on fresh startup (not on compact/resume)
    if source == "startup":
        rotate_events_file()

    context_parts: list[str] = []

    # Load pre-compact continuation (if resuming after compaction)
    if source == "compact":
        # For orchestrator, agent_id is "unknown" — use session-level snapshot
        continuation = _load_pre_compact_continuation("unknown")
        if continuation:
            context_parts.append(continuation)

    # Check for workflow resumption
    resumption_context = _check_workflow_resumption()
    if resumption_context:
        context_parts.append(resumption_context)

    _output_hook_result("\n".join(context_parts))


def _check_workflow_resumption() -> str:
    """
    Scan for in-progress workflow state.

    Checks:
    1. ~/.claude/state/workflow.json — is there an active workflow?
    2. ~/.claude/state/agents/*.json — any agents that were active (now dead)?
    3. .claude/handoffs/*.json — any handoff files from terminated agents?

    Returns formatted resumption context, or empty string if no active workflow.
    """
    workflow = read_json_safe(WORKFLOW_STATE_FILE)
    if not workflow:
        return ""

    current_state = workflow.get("current_state", "IDLE")
    terminal_states = {"IDLE", "COMPLETE"}

    if current_state in terminal_states:
        return ""

    # Active workflow found — gather context
    lines = [
        "=== WORKFLOW RESUMPTION DETECTED ===",
        f"Workflow: {workflow.get('workflow_id', 'unknown')}",
        f"Feature: {workflow.get('feature', 'unknown')}",
        f"State: {current_state}",
        f"Base branch: {workflow.get('base_branch', 'unknown')}",
        f"Started: {workflow.get('started_at', 'unknown')}",
        f"Last updated: {workflow.get('last_updated', 'unknown')}",
    ]

    # Scan agent state files for previously active agents
    dead_agents = []
    agents_with_handoff = set()

    try:
        if AGENT_STATE_DIR.exists():
            for state_file in AGENT_STATE_DIR.glob("*.json"):
                agent_state = read_json_safe(state_file)
                if not agent_state:
                    continue
                status = agent_state.get("status", "unknown")
                if status in ("active", "draining"):
                    dead_agents.append(agent_state)
    except Exception as e:
        log_hook_error("session_start", "_check_workflow_resumption/scan_agents", e)

    # Scan for handoff files
    handoff_files = []
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    handoffs_path = Path(project_dir) / HANDOFFS_DIR

    try:
        if handoffs_path.exists():
            for hf in handoffs_path.glob("*.json"):
                handoff_files.append(hf.stem)
                agents_with_handoff.add(hf.stem)
    except Exception as e:
        log_hook_error("session_start", "_check_workflow_resumption/scan_handoffs", e)

    # Scan for active messaging teams
    messages_dir = Path(os.path.expanduser("~/.claude/messages"))
    try:
        registry_path = messages_dir / "_registry.json"
        registry = read_json_safe(str(registry_path))
        if registry and isinstance(registry.get("teams"), dict):
            active_teams = [
                (name, info)
                for name, info in registry["teams"].items()
                if isinstance(info, dict) and info.get("status") == "active"
            ]
            if active_teams:
                lines.append("")
                lines.append("Active messaging teams:")
                for team_name, team_info in active_teams:
                    wf_id = team_info.get("workflow_id", "unknown")
                    lines.append(f"  - {team_name} (workflow: {wf_id})")
                    # Read manifest for member details
                    manifest = read_json_safe(
                        str(messages_dir / team_name / "_manifest.json")
                    )
                    if manifest and isinstance(manifest.get("agents"), dict):
                        for agent_name, agent_info in manifest["agents"].items():
                            if isinstance(agent_info, dict):
                                status = agent_info.get("status", "unknown")
                                role = agent_info.get("role", "unknown")
                                lines.append(
                                    f"    {agent_name} ({role}) — {status}"
                                )
                lines.append("")
                lines.append(
                    "To rejoin a team: /agent-init {team_name} --role {your_role}"
                )
                lines.append(
                    "To start as orchestrator: /orchestrator-init {team_name}"
                )
    except Exception as e:
        log_hook_error("session_start", "_check_workflow_resumption/scan_teams", e)

    # Format dead agents
    if dead_agents:
        lines.append("")
        lines.append("Previously active agents (now dead):")
        for agent in dead_agents:
            aid = agent.get("id", "?")
            arole = agent.get("role", "?")
            astate = agent.get("current_state", "?")
            handoff_marker = (
                " [handoff available]" if aid in agents_with_handoff else ""
            )
            lines.append(f"  - {aid} ({arole}, was in {astate}){handoff_marker}")

    # Format handoff files
    if handoff_files:
        lines.append("")
        lines.append("Handoff files found:")
        for hf in handoff_files:
            lines.append(f"  - .claude/handoffs/{hf}.json")

    # Agents without handoff
    no_handoff = [
        a.get("id", "?")
        for a in dead_agents
        if a.get("id", "?") not in agents_with_handoff
    ]
    if no_handoff:
        lines.append("")
        lines.append("No handoff found for:")
        for aid in no_handoff:
            lines.append(f"  - {aid}")

    lines.append("")
    lines.append(
        "Action needed: Re-assess workflow state, check handoff files, "
        "re-spawn agents as needed."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Preferences loading
# ---------------------------------------------------------------------------


def _load_preferences(role: str) -> str:
    """
    Load global + role-specific preferences from preferences.json.

    Returns formatted preference context string, or empty string if
    no preferences file exists.
    """
    raw = read_json_safe(PREFERENCES_FILE)
    if not raw:
        return ""

    # Validate with Pydantic if available
    prefs_data = raw
    if Preferences is not None:
        try:
            validated = Preferences.model_validate(raw)
            prefs_data = validated.model_dump(mode="json", exclude_defaults=True, exclude_none=True)
        except Exception as e:
            log_hook_error("session_start", "_load_preferences/validate", e)

    # Merge global + role-specific
    global_prefs = prefs_data.get("global", {})
    role_prefs = prefs_data.get("by_role", {}).get(role, {})

    if not global_prefs and not role_prefs:
        return ""

    lines = ["Active preferences:"]

    if global_prefs:
        lines.append("  Global:")
        for key, pref in global_prefs.items():
            value = pref.get("value", pref) if isinstance(pref, dict) else pref
            lines.append(f"    - {key}: {value}")

    if role_prefs:
        lines.append(f"  Role ({role}):")
        for key, pref in role_prefs.items():
            value = pref.get("value", pref) if isinstance(pref, dict) else pref
            lines.append(f"    - {key}: {value}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pre-compact continuation loading
# ---------------------------------------------------------------------------


def _load_pre_compact_continuation(agent_id: str) -> str:
    """
    Load the continuation message saved by PreCompact hook.

    PreCompact writes a snapshot to:
        ~/.claude/state/pre-compact-snapshots/{agent_id}-latest.json

    This function reads the continuation_message field from that snapshot
    and returns it for injection as additionalContext. The agent picks up
    exactly where it left off after compaction.
    """
    snapshot_file = PRE_COMPACT_SNAPSHOTS_DIR / f"{agent_id}-latest.json"
    snapshot = read_json_safe(snapshot_file)
    if not snapshot:
        return ""

    continuation = snapshot.get("continuation_message", "")
    if not continuation:
        return ""

    # Check for pending critical annotations and surface a warning
    pending_annotations = snapshot.get("pending_critical_annotations", [])
    if pending_annotations and len(pending_annotations) > 0:
        count = len(pending_annotations)
        continuation += (
            f"\n\n!! WARNING: {count} pending critical annotation(s) require "
            "acknowledgment via Think tool before mutation tools are unblocked. !!"
        )

    return continuation


# ---------------------------------------------------------------------------
# Environment persistence
# ---------------------------------------------------------------------------


def _write_env_vars(agent_id: str, role: str) -> None:
    """Write agent identity to CLAUDE_ENV_FILE for session persistence."""
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if not env_file:
        return

    try:
        with open(env_file, "a") as f:
            f.write(f"export AGENT_ID={shlex.quote(agent_id)}\n")
            f.write(f"export AGENT_ROLE={shlex.quote(role)}\n")
    except Exception as e:
        log_hook_error("session_start", "_write_env_vars", e)


# ---------------------------------------------------------------------------
# Hook output
# ---------------------------------------------------------------------------


def _output_hook_result(additional_context: str) -> None:
    """Print HookOutput JSON to stdout for Claude Code to consume."""
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
        }
    }

    if additional_context:
        output["hookSpecificOutput"]["additionalContext"] = additional_context

    print(json.dumps(output))


# ---------------------------------------------------------------------------
# stdin parsing
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    main()
