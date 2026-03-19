#!/usr/bin/env python3
"""
PreCompact Hook — Save workflow state before context compaction.

Fires before context compaction (auto or manual /compact). Writes a narrative
continuation message + structured snapshot so the post-compaction agent can
seamlessly continue. SessionStart (compact matcher) reads the snapshot and
injects the continuation message as additionalContext.

Cannot block compaction. Only type: "command" handlers supported.
No decision control — purely for side effects.

Stdin: {"trigger": "auto"|"manual", "custom_instructions": "...", ...}
Output: None (side effect only — writes snapshot file)

Design doc ref: "Context Pressure Handling" (lines 207-213), "PreCompact hook saves" (line 21).

PTC future-proofing: Detects active PTC kernels and notes them in the
continuation message. Kernel state is lost on compaction — sub-agents
need re-dispatch. PTC server not yet built; detection is stubbed.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from hooks.utils.error_logger import log_hook_error
from hooks.utils.event_logger import emit_pre_compact
from hooks.utils.state_helpers import (
    AGENT_STATE_DIR,
    ANNOTATIONS_DIR,
    LOGS_DIR,
    atomic_write,
    get_agent_id,
    read_json_safe,
    read_stdin,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CLAUDE_HOME = Path(os.path.expanduser("~/.claude"))
WORKFLOW_STATE_FILE = _CLAUDE_HOME / "state" / "workflow.json"
PREFERENCES_FILE = _CLAUDE_HOME / "state" / "preferences.json"
SNAPSHOTS_DIR = _CLAUDE_HOME / "state" / "pre-compact-snapshots"

# PTC future-proofing: kernel state directory (not yet built)
PTC_KERNELS_DIR = _CLAUDE_HOME / "state" / "ptc-kernels"


def main() -> None:
    """Read stdin, snapshot state, emit event."""
    stdin_data = read_stdin()
    trigger = stdin_data.get("trigger", "auto")
    agent_id = get_agent_id()

    # Emit observability event
    emit_pre_compact(agent_id, trigger)

    # Read all state sources
    agent_state = _read_agent_state(agent_id)
    workflow_state = read_json_safe(WORKFLOW_STATE_FILE)
    recent_decisions = _read_recent_decisions(agent_id)
    plan_summary = _read_active_plan_summary(workflow_state)
    session_log = _read_active_session_log(agent_id)
    ptc_kernels = _detect_active_ptc_kernels(agent_id)

    # Read additional state for enhanced continuation
    user_decisions = _read_user_decisions(agent_id)
    pending_approvals = _read_pending_approvals(workflow_state)
    active_agents = _read_active_agents()
    recent_messages = _read_recent_messages()
    pending_annotations = _read_pending_critical_annotations(agent_id)

    # Build narrative continuation message
    continuation = _build_continuation_message(
        agent_id=agent_id,
        agent_state=agent_state,
        workflow_state=workflow_state,
        recent_decisions=recent_decisions,
        plan_summary=plan_summary,
        session_log=session_log,
        ptc_kernels=ptc_kernels,
        trigger=trigger,
        user_decisions=user_decisions,
        pending_approvals=pending_approvals,
        active_agents=active_agents,
        recent_messages=recent_messages,
        pending_annotations=pending_annotations,
    )

    # Build structured snapshot
    snapshot = {
        "agent_id": agent_id,
        "timestamp": _now_iso(),
        "trigger": trigger,
        "continuation_message": continuation,
        "state": agent_state or {},
        "workflow": workflow_state or {},
        "recent_decisions": recent_decisions,
        "plan_summary": plan_summary,
        "session_log_summary": _summarize_session_log(session_log),
        "ptc_kernels": ptc_kernels,
        "user_decisions": user_decisions,
        "pending_approvals": pending_approvals,
        "active_agents": active_agents,
        "recent_messages": recent_messages,
        "pending_critical_annotations": pending_annotations,
    }

    # Write snapshot (overwrite — only latest matters)
    _write_snapshot(agent_id, snapshot)


# ---------------------------------------------------------------------------
# State reading
# ---------------------------------------------------------------------------


def _read_agent_state(agent_id: str) -> dict | None:
    """Read the agent's state file."""
    if agent_id == "unknown":
        return None
    return read_json_safe(AGENT_STATE_DIR / f"{agent_id}.json")


def _read_recent_decisions(agent_id: str, limit: int = 10) -> list[dict]:
    """Read the last N decisions from the agent's decision log."""
    if agent_id == "unknown":
        return []
    log_file = LOGS_DIR / "decisions" / f"{agent_id}.jsonl"
    if not log_file.exists():
        return []
    try:
        lines: list[str] = []
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)
        # Take the last N entries
        recent = lines[-limit:]
        return [json.loads(line) for line in recent]
    except Exception as e:
        log_hook_error("pre_compact", "_read_recent_decisions", e)
        return []


def _read_active_plan_summary(workflow_state: dict | None) -> dict | None:
    """Read the active plan and summarize task statuses."""
    if not workflow_state:
        return None
    feature = workflow_state.get("feature")
    if not feature:
        return None

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    plan_file = Path(project_dir) / ".claude" / "plans" / f"{feature}-plan.json"
    plan = read_json_safe(plan_file)
    if not plan:
        return None

    try:
        tasks = plan.get("tasks", plan.get("chunks", []))
        summary = {
            "feature": feature,
            "total_tasks": len(tasks),
            "completed": sum(1 for t in tasks if t.get("status") == "completed"),
            "in_progress": sum(1 for t in tasks if t.get("status") == "in_progress"),
            "pending": sum(1 for t in tasks if t.get("status") in ("pending", None)),
            "current_task": None,
        }
        # Find the in-progress task
        for t in tasks:
            if t.get("status") == "in_progress":
                summary["current_task"] = {
                    "id": t.get("id"),
                    "name": t.get("name"),
                    "description": t.get("description", "")[:200],
                }
                break
        return summary
    except Exception as e:
        log_hook_error("pre_compact", "_read_active_plan_summary", e)
        return None


def _read_active_session_log(agent_id: str) -> dict | None:
    """Read the most recent in-progress session log for this agent."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    logs_dir = Path(project_dir) / ".claude" / "logs"
    if not logs_dir.exists():
        return None
    try:
        # Find logs for this agent
        for log_file in sorted(
            logs_dir.glob("*-log.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            data = read_json_safe(log_file)
            if not data:
                continue
            if data.get("agent_id") == agent_id or data.get("status") == "in_progress":
                return data
    except Exception as e:
        log_hook_error("pre_compact", "_read_active_session_log", e)
    return None


def _detect_active_ptc_kernels(agent_id: str) -> list[dict]:
    """
    Detect active PTC (Programmatic Tool Calling) kernels for this agent.

    PTC FUTURE-PROOFING: The PTC server is not yet built. This function
    checks for kernel state files that the PTC server will write when
    implemented. Currently returns empty list.

    When PTC is implemented, kernel state files will be at:
        ~/.claude/state/ptc-kernels/{agent-id}.json
    containing: kernel_id, status, last_activity, pending_operations
    """
    if not PTC_KERNELS_DIR.exists():
        return []  # PTC not yet built
    try:
        kernel_file = PTC_KERNELS_DIR / f"{agent_id}.json"
        kernel = read_json_safe(kernel_file)
        if kernel and kernel.get("status") == "active":
            return [kernel]
        # Also check sub-agent kernels spawned by this agent
        kernels = []
        for kf in PTC_KERNELS_DIR.glob("*.json"):
            k = read_json_safe(kf)
            if k and k.get("parent_agent") == agent_id and k.get("status") == "active":
                kernels.append(k)
        return kernels
    except Exception as e:
        log_hook_error("pre_compact", "_detect_active_ptc_kernels", e)
        return []


def _read_user_decisions(agent_id: str, limit: int = 3) -> list[dict]:
    """Filter decision log for user-sourced decisions (most recent N)."""
    if agent_id == "unknown":
        return []
    log_file = LOGS_DIR / "decisions" / f"{agent_id}.jsonl"
    if not log_file.exists():
        return []
    try:
        user_decisions: list[dict] = []
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                # User decisions have source hints or are from user approval states
                dp = entry.get("decision_point", "")
                if "user" in dp.lower() or "approv" in dp.lower():
                    user_decisions.append(entry)
        return user_decisions[-limit:]
    except Exception as e:
        log_hook_error("pre_compact", "_read_user_decisions", e)
        return []


def _read_pending_approvals(workflow_state: dict | None) -> list[str]:
    """Check if system is in a user-approval state."""
    if not workflow_state:
        return []
    approval_states = {"PLAN_TEXT_REVIEW", "PHASE_REPORT"}
    current = workflow_state.get("current_state", "")
    if current in approval_states:
        return [current]
    return []


def _read_active_agents() -> list[dict]:
    """Scan agent state directory for status=active/draining agents."""
    agents: list[dict] = []
    if not AGENT_STATE_DIR.exists():
        return agents
    try:
        for state_file in AGENT_STATE_DIR.glob("*.json"):
            state = read_json_safe(state_file)
            if not state:
                continue
            status = state.get("status", "")
            if status in ("active", "draining"):
                agents.append(
                    {
                        "id": state.get("id", state_file.stem),
                        "role": state.get("role", "unknown"),
                        "state": state.get("current_state", "UNKNOWN"),
                        "task": state.get("task"),
                        "context_pct": state.get("context_usage_pct", 0),
                    }
                )
    except Exception as e:
        log_hook_error("pre_compact", "_read_active_agents", e)
    return agents


def _read_recent_messages(limit: int = 10) -> list[dict]:
    """Tail message bus JSONL for recent messages."""
    bus_file = LOGS_DIR / "message-bus.jsonl"
    if not bus_file.exists():
        return []
    try:
        lines: list[str] = []
        with open(bus_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)
        recent = lines[-limit:]
        messages = []
        for line in recent:
            entry = json.loads(line)
            messages.append(
                {
                    "from": entry.get("from_agent", "?"),
                    "to": entry.get("to_agent", "?"),
                    "type": entry.get("message_type", "?"),
                    "summary": entry.get("summary", "")[:100],
                }
            )
        return messages
    except Exception as e:
        log_hook_error("pre_compact", "_read_recent_messages", e)
        return []


def _read_pending_critical_annotations(agent_id: str) -> list[dict]:
    """Check for unacknowledged critical annotations."""
    if agent_id == "unknown":
        return []
    # Check agent state for pending_critical_annotation
    state = read_json_safe(AGENT_STATE_DIR / f"{agent_id}.json")
    if not state:
        return []
    pending = state.get("pending_critical_annotation")
    if pending:
        return [pending]
    # Also check annotation file for unacked critical entries
    ann_file = ANNOTATIONS_DIR / f"{agent_id}.jsonl"
    if not ann_file.exists():
        return []
    try:
        unacked: list[dict] = []
        with open(ann_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if (
                    not entry.get("acknowledged")
                    and entry.get("priority") == "critical"
                ):
                    unacked.append(
                        {
                            "annotation_id": entry.get("annotation_id", "unknown"),
                            "message": entry.get("message", "")[:100],
                        }
                    )
        return unacked
    except Exception as e:
        log_hook_error("pre_compact", "_read_pending_critical_annotations", e)
        return []


# ---------------------------------------------------------------------------
# Continuation message builder
# ---------------------------------------------------------------------------


def _build_continuation_message(
    agent_id: str,
    agent_state: dict | None,
    workflow_state: dict | None,
    recent_decisions: list[dict],
    plan_summary: dict | None,
    session_log: dict | None,
    ptc_kernels: list[dict],
    trigger: str,
    user_decisions: list[dict] | None = None,
    pending_approvals: list[str] | None = None,
    active_agents: list[dict] | None = None,
    recent_messages: list[dict] | None = None,
    pending_annotations: list[dict] | None = None,
) -> str:
    """Build a narrative handoff message for post-compaction continuity."""
    lines: list[str] = ["=== CONTINUATION AFTER COMPACTION ==="]

    # Identity
    if agent_state:
        role = agent_state.get("role", "unknown")
        current_state = agent_state.get("current_state", "UNKNOWN")
        lines.append(
            f"You are {agent_id} (role: {role}), "
            f"state machine position: {current_state}"
        )
        if agent_state.get("task"):
            lines.append(f"Assigned task: {agent_state['task']}")
        if agent_state.get("phase"):
            lines.append(f"Phase: {agent_state['phase']}")
    elif agent_id != "unknown":
        lines.append(f"Agent: {agent_id}")
    else:
        lines.append("Session: orchestrator / main")

    # Workflow context
    if workflow_state:
        feature = workflow_state.get("feature", "unknown")
        wf_state = workflow_state.get("current_state", "unknown")
        lines.append(f"Feature: {feature} | Workflow state: {wf_state}")

    # Plan progress
    if plan_summary:
        ct = plan_summary.get("current_task")
        lines.append("")
        lines.append(
            f"Plan progress: {plan_summary['completed']}/{plan_summary['total_tasks']} "
            f"tasks complete, {plan_summary['in_progress']} in progress"
        )
        if ct:
            lines.append(f"Current task: {ct['id']} — {ct['name']}")
            if ct.get("description"):
                lines.append(f"  {ct['description']}")

    # Session log state
    if session_log:
        lines.append("")
        lines.append(f"Session status: {session_log.get('status', 'unknown')}")
        state_history = session_log.get("state_history", [])
        if state_history:
            last_state = state_history[-1]
            lines.append(
                f"Last state: {last_state.get('state')} "
                f"({last_state.get('details', 'no details')})"
            )
        # Test progress
        tests = session_log.get("tests_written", [])
        if tests:
            passing = sum(1 for t in tests if t.get("status") == "pass")
            lines.append(f"Tests: {passing}/{len(tests)} passing")
        # Files modified
        files = session_log.get("files_modified", [])
        if files:
            lines.append(f"Files modified: {len(files)}")
            for f in files[:5]:
                path = f.get("path", f) if isinstance(f, dict) else f
                lines.append(f"  - {path}")
        # Resume point
        resume = session_log.get("resume_point")
        if resume:
            lines.append(f"Resume point: {resume}")

    # Key decisions
    if recent_decisions:
        lines.append("")
        lines.append("Key decisions made (most recent):")
        for d in recent_decisions[-5:]:
            chosen = d.get("chosen", "")
            point = d.get("decision_point", "")
            if chosen:
                lines.append(f"  - {point}: {chosen[:150]}")

    # User preferences from session
    if session_log:
        prefs = session_log.get("user_preferences", [])
        if prefs:
            lines.append("")
            lines.append("User preferences expressed this session:")
            for p in prefs:
                lines.append(f"  - {p}")

    # PTC kernel warning
    if ptc_kernels:
        lines.append("")
        lines.append("!! PTC KERNELS LOST ON COMPACTION !!")
        lines.append("The following PTC kernels had active state that was lost:")
        for k in ptc_kernels:
            kid = k.get("kernel_id", "unknown")
            pending = k.get("pending_operations", [])
            lines.append(f"  - Kernel {kid}")
            if pending:
                lines.append(
                    f"    Pending operations: {', '.join(str(p) for p in pending)}"
                )
        lines.append("Re-dispatch sub-agents for these operations.")

    # User decisions (structured facts)
    if user_decisions:
        lines.append("")
        lines.append("USER_DECISIONS:")
        for d in user_decisions:
            dp = d.get("decision_point", "?")
            chosen = d.get("chosen", "?")
            ts = d.get("timestamp", "?")
            lines.append(f"  decision_point={dp} | chosen={chosen} | timestamp={ts}")

    # Pending approvals
    if pending_approvals:
        lines.append("")
        lines.append("PENDING_APPROVALS:")
        for state_name in pending_approvals:
            lines.append(f"  state={state_name}")

    # Active agents roster
    if active_agents:
        lines.append("")
        lines.append("ACTIVE_AGENTS:")
        for a in active_agents:
            lines.append(
                f"  id={a['id']} | role={a['role']} | state={a['state']} "
                f"| task={a.get('task', 'none')} | context={a.get('context_pct', 0):.0f}%"
            )

    # Recent messages
    if recent_messages:
        lines.append("")
        lines.append("RECENT_MESSAGES:")
        for m in recent_messages:
            lines.append(
                f"  from={m['from']} | to={m['to']} | type={m['type']} | summary={m['summary']}"
            )

    # Pending critical annotations
    if pending_annotations:
        lines.append("")
        lines.append(f"PENDING_CRITICAL_ANNOTATIONS: count={len(pending_annotations)}")
        for ann in pending_annotations:
            ann_id = ann.get("annotation_id", "?")
            msg = ann.get("message", "?")[:100]
            lines.append(f"  {ann_id}: {msg}")

    # Compaction trigger
    lines.append("")
    lines.append(f"Compaction trigger: {trigger}")
    lines.append(f"Compacted at: {_now_iso()}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Session log summarizer
# ---------------------------------------------------------------------------


def _summarize_session_log(session_log: dict | None) -> dict | None:
    """Extract compact summary from session log for snapshot."""
    if not session_log:
        return None
    try:
        return {
            "status": session_log.get("status"),
            "current_state": session_log.get("current_state"),
            "tests_total": len(session_log.get("tests_written", [])),
            "tests_passing": sum(
                1
                for t in session_log.get("tests_written", [])
                if t.get("status") == "pass"
            ),
            "files_modified": len(session_log.get("files_modified", [])),
            "resume_point": session_log.get("resume_point"),
            "resume_from_state": session_log.get("resume_from_state"),
        }
    except Exception as e:
        log_hook_error("pre_compact", "_summarize_session_log", e)
        return None


# ---------------------------------------------------------------------------
# Snapshot writing
# ---------------------------------------------------------------------------


def _write_snapshot(agent_id: str, snapshot: dict) -> None:
    """Write snapshot to well-known location for SessionStart to read."""
    try:
        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        snapshot_file = SNAPSHOTS_DIR / f"{agent_id}-latest.json"
        atomic_write(snapshot_file, json.dumps(snapshot, indent=2, default=str))
    except Exception as e:
        log_hook_error("pre_compact", "_write_snapshot", e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
