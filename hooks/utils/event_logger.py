#!/usr/bin/env python3
"""
Event Logger Utility

Append-only JSONL event logging for workflow observability.
All functions are fire-and-forget — errors are silently caught
so observability never blocks agent work.

Output: ~/.claude/temp/workflow-events.jsonl
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from hooks.utils.error_logger import log_hook_error

EVENTS_FILE = Path(os.path.expanduser("~/.claude/temp/workflow-events.jsonl"))


def emit(event: str, **kwargs: object) -> None:
    """
    Append a single JSONL event line.

    Args:
        event: Event name (e.g. "file_written", "state_transition")
        **kwargs: Additional fields merged into the event dict
    """
    try:
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)

        entry: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        entry.update(kwargs)

        with open(EVENTS_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        log_hook_error("event_logger", "emit", e)


# ---------------------------------------------------------------------------
# Preserved v1 emitters
# ---------------------------------------------------------------------------


def emit_file_written(file_path: str, agent_id: str = "unknown") -> None:
    """Emit file_written event for files under .claude/."""
    rel = file_path
    if os.path.isabs(file_path):
        try:
            rel = os.path.relpath(file_path)
        except ValueError:
            rel = file_path

    if ".claude/" in rel:
        emit("file_written", file=rel, agent=agent_id)


def emit_skill_loaded(skill_path: str, agent_id: str = "unknown") -> None:
    """Emit skill_loaded when a SKILL.md file is read."""
    rel = skill_path
    if os.path.isabs(skill_path):
        try:
            rel = os.path.relpath(skill_path)
        except ValueError:
            rel = skill_path

    parts = Path(rel).parts
    if (
        len(parts) >= 4
        and parts[0] == ".claude"
        and parts[1] == "skills"
        and parts[-1].upper().startswith("SKILL")
    ):
        skill_name = parts[2]
        emit("skill_loaded", skill=skill_name, file=rel, agent=agent_id)


def emit_context_pressure(
    percentage: float, level: str, agent_id: str = "unknown"
) -> None:
    """Emit context_pressure when a threshold is crossed."""
    emit(
        "context_pressure",
        percentage=round(percentage, 1),
        level=level,
        agent=agent_id,
    )


# ---------------------------------------------------------------------------
# New v2 emitters
# ---------------------------------------------------------------------------


def emit_state_transition(
    agent_id: str, from_state: str, to_state: str, trigger: str
) -> None:
    """Emit state_transition when an agent changes state machine state."""
    emit(
        "state_transition",
        agent=agent_id,
        from_state=from_state,
        to_state=to_state,
        trigger=trigger,
    )


def emit_message_sent(from_agent: str, to_agent: str, message_type: str) -> None:
    """Emit message_sent when an inter-agent message is dispatched."""
    emit(
        "message_sent",
        from_agent=from_agent,
        to_agent=to_agent,
        message_type=message_type,
    )


def emit_decision_logged(agent_id: str, decision_point: str) -> None:
    """Emit decision_logged when a Think tool output is captured."""
    emit("decision_logged", agent=agent_id, decision_point=decision_point)


def emit_agent_registered(agent_id: str, role: str) -> None:
    """Emit agent_registered when SessionStart creates an agent state file."""
    emit("agent_registered", agent=agent_id, role=role)


def emit_agent_terminated(agent_id: str, reason: str) -> None:
    """Emit agent_terminated when an agent exits (handoff, completion, etc.)."""
    emit("agent_terminated", agent=agent_id, reason=reason)


def emit_pre_compact(agent_id: str, trigger: str) -> None:
    """Emit pre_compact when context compaction is about to occur."""
    emit("pre_compact", agent=agent_id, trigger=trigger)


def emit_session_ended(agent_id: str) -> None:
    """Emit session_ended when the main session (orchestrator) stops."""
    emit("session_ended", agent=agent_id)


def emit_subagent_started(parent_agent: str, subagent_id: str, agent_type: str) -> None:
    """Emit subagent_started when a workflow sub-agent is spawned."""
    emit(
        "subagent_started",
        parent_agent=parent_agent,
        subagent_id=subagent_id,
        agent_type=agent_type,
    )


def emit_subagent_validated(
    subagent_id: str, agent_type: str, valid: bool, reason: str = ""
) -> None:
    """Emit subagent_validated when SubagentStop validates a sub-agent's output."""
    emit(
        "subagent_validated",
        subagent_id=subagent_id,
        agent_type=agent_type,
        valid=valid,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Messaging emitters
# ---------------------------------------------------------------------------


def emit_message_delivered(
    from_agent: str, to_agent: str, message_type: str, message_id: str, priority: str
) -> None:
    """Emit message_delivered when a message is written to a recipient's inbox."""
    emit(
        "message_delivered",
        from_agent=from_agent,
        to_agent=to_agent,
        message_type=message_type,
        message_id=message_id,
        priority=priority,
    )


def emit_message_acked(agent_id: str, message_id: str) -> None:
    """Emit message_acked when an agent acknowledges a message."""
    emit("message_acked", agent=agent_id, message_id=message_id)


def emit_inbox_blocked(agent_id: str, message_id: str, from_agent: str) -> None:
    """Emit inbox_blocked when a blocking message locks an agent's tool access."""
    emit(
        "inbox_blocked",
        agent=agent_id,
        message_id=message_id,
        from_agent=from_agent,
    )


def emit_inbox_unblocked(agent_id: str, message_id: str) -> None:
    """Emit inbox_unblocked when a blocking message is acknowledged."""
    emit("inbox_unblocked", agent=agent_id, message_id=message_id)


def emit_message_escalated(agent_id: str, message_id: str, trigger_state: str) -> None:
    """Emit message_escalated when a non-blocking message is promoted to blocking."""
    emit(
        "message_escalated",
        agent=agent_id,
        message_id=message_id,
        trigger_state=trigger_state,
    )


def emit_team_created(team_id: str, creator: str) -> None:
    """Emit team_created when a new messaging team is initialized."""
    emit("team_created", team_id=team_id, creator=creator)


def emit_team_joined(team_id: str, agent_id: str, role: str) -> None:
    """Emit team_joined when an agent joins an existing team."""
    emit("team_joined", team_id=team_id, agent=agent_id, role=role)


# ---------------------------------------------------------------------------
# PTC (Python Tool Computer) emitters
# ---------------------------------------------------------------------------


def emit_ptc_kernel_started(agent_id: str, kernel_id: str) -> None:
    """Emit ptc_kernel_started when a Docker kernel container is created."""
    emit("ptc_kernel_started", agent=agent_id, kernel=kernel_id)


def emit_ptc_kernel_stopped(agent_id: str, kernel_id: str, reason: str) -> None:
    """Emit ptc_kernel_stopped when a kernel container is removed."""
    emit("ptc_kernel_stopped", agent=agent_id, kernel=kernel_id, reason=reason)


def emit_ptc_execution(
    agent_id: str, tool: str, duration_ms: int, tokens_saved: int
) -> None:
    """Emit ptc_execution for each tool call through the PTC sandbox."""
    emit(
        "ptc_execution",
        agent=agent_id,
        tool=tool,
        duration_ms=duration_ms,
        tokens_saved=tokens_saved,
    )


def emit_ptc_fallback(agent_id: str, reason: str) -> None:
    """Emit ptc_fallback when a kernel fails and agent falls back to direct tools."""
    emit("ptc_fallback", agent=agent_id, reason=reason)


# ---------------------------------------------------------------------------
# File rotation (preserved from v1)
# ---------------------------------------------------------------------------


def rotate_events_file(max_archives: int = 5) -> None:
    """
    Rotate the current events file to a timestamped archive.

    Called at session start to give each session a clean timeline
    while preserving previous sessions for debugging.
    """
    try:
        if not EVENTS_FILE.exists() or EVENTS_FILE.stat().st_size == 0:
            return

        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        archive = EVENTS_FILE.with_name(f"workflow-events-{ts}.jsonl")
        EVENTS_FILE.rename(archive)

        # Prune old archives, keep only the most recent N
        archives = sorted(EVENTS_FILE.parent.glob("workflow-events-2*.jsonl"))
        for old in archives[:-max_archives]:
            old.unlink()
    except Exception as e:
        log_hook_error("event_logger", "rotate_events_file", e)
