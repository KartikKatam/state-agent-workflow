#!/usr/bin/env python3
"""
SubagentStart Hook — Inject parent workflow context into sub-agents at spawn.

Fires when a sub-agent is spawned via the Task tool. Matched to workflow
agent types only (coder|explorer|researcher|strategist|tester|auditor) —
extraction sub-agents (Bash, Explore, Plan) pass through unmatched.

Reads parent agent state, workflow state, plan task details, user preferences,
AND the inter-agent communication log so the sub-agent understands the full
chain of WHY it was spawned.

Performance target: ~30ms. Conditional Pydantic imports for validation with graceful fallback.

Stdin: {"agent_id": "...", "agent_type": "...", ...}
Output: {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": "..."}}

Design doc ref: "Sub-Agent Delegation Model" (lines 222-227).

PTC future-proofing: When PTC server exists, injects sandbox context
(kernel ID, available tools, expected output format). Currently stubbed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from hooks.utils.error_logger import log_hook_error
from hooks.utils.event_logger import emit_subagent_started
from hooks.utils.state_helpers import (
    AGENT_STATE_DIR,
    LOGS_DIR,
    get_agent_id,
    read_json_safe,
    read_stdin,
)

# ---------------------------------------------------------------------------
# Conditional Pydantic imports — graceful fallback to raw dicts
# ---------------------------------------------------------------------------

try:
    from schemas.agent_state import AgentState
    from schemas.system_state import WorkflowState
    from schemas.preferences import Preferences

    _HAS_PYDANTIC = True
except ImportError:
    AgentState = None  # type: ignore[assignment,misc]
    WorkflowState = None  # type: ignore[assignment,misc]
    Preferences = None  # type: ignore[assignment,misc]
    _HAS_PYDANTIC = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CLAUDE_HOME = Path(os.path.expanduser("~/.claude"))
WORKFLOW_STATE_FILE = _CLAUDE_HOME / "state" / "workflow.json"
PREFERENCES_FILE = _CLAUDE_HOME / "state" / "preferences.json"
MESSAGE_BUS_LOG = LOGS_DIR / "message-bus.jsonl"

# PTC future-proofing
PTC_KERNELS_DIR = _CLAUDE_HOME / "state" / "ptc-kernels"
PTC_CONFIG_FILE = _CLAUDE_HOME / "mcp" / "ptc-server" / "config.json"

# Maximum context injection size (chars) to avoid bloating sub-agent context
MAX_CONTEXT_CHARS = 3000
MAX_MESSAGE_ENTRIES = 15


def main() -> None:
    """Read parent context, build additionalContext, output JSON."""
    stdin_data = read_stdin()
    subagent_id = stdin_data.get("agent_id", "unknown")
    agent_type = stdin_data.get("agent_type", "unknown")

    # Parent identity (hook runs in parent's process context)
    parent_id = get_agent_id()
    parent_state = _read_agent_state(parent_id)

    # Emit observability event
    emit_subagent_started(parent_id, subagent_id, agent_type)

    # Build context sections
    sections: list[str] = []

    # Section 1: Parent identity and state
    identity = _build_identity_section(parent_id, parent_state)
    if identity:
        sections.append(identity)

    # Section 2: Communication log — WHY was this sub-agent spawned
    comms = _build_communication_section(parent_id)
    if comms:
        sections.append(comms)

    # Section 3: Parent's task details (from plan)
    task_ctx = _build_task_section(parent_state)
    if task_ctx:
        sections.append(task_ctx)

    # Section 4: Key files the parent has read (from session log)
    files_ctx = _build_key_files_section(parent_id)
    if files_ctx:
        sections.append(files_ctx)

    # Section 5: Active preferences
    prefs_ctx = _build_preferences_section(parent_state)
    if prefs_ctx:
        sections.append(prefs_ctx)

    # Section 6: PTC sandbox context (future-proofing)
    ptc_ctx = _build_ptc_section(parent_id, agent_type)
    if ptc_ctx:
        sections.append(ptc_ctx)

    # Combine and cap size
    context = "\n\n".join(sections)
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n[... context truncated]"

    # Output
    if context:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": context,
            }
        }
        print(json.dumps(output))


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_identity_section(parent_id: str, parent_state: dict | None) -> str:
    """Build parent identity and workflow position."""
    lines = ["=== WORKFLOW CONTEXT ==="]

    if parent_state:
        role = parent_state.get("role", "unknown")
        current_state = parent_state.get("current_state", "UNKNOWN")
        lines.append(f"Spawned by: {parent_id} (role: {role}, state: {current_state})")

        phase = parent_state.get("phase")
        task = parent_state.get("task")
        if phase:
            lines.append(f"Phase: {phase}")
        if task:
            lines.append(f"Task: {task}")
    elif parent_id != "unknown":
        lines.append(f"Spawned by: {parent_id}")
    else:
        lines.append("Spawned by: orchestrator / main session")

    # Workflow-level context
    workflow = _read_workflow_state()
    if workflow:
        feature = workflow.get("feature", "unknown")
        wf_state = workflow.get("current_state", "unknown")
        lines.append(f"Feature: {feature} | Workflow: {wf_state}")

    return "\n".join(lines)


def _build_communication_section(parent_id: str) -> str:
    """
    Read message bus and extract messages relevant to the parent agent.

    Shows the chain of communication that led to the parent's current work:
    task assignments, context requests, status updates, clarifications.
    """
    if not MESSAGE_BUS_LOG.exists():
        return ""

    try:
        # Read all messages involving the parent
        relevant: list[dict] = []
        with open(MESSAGE_BUS_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                from_agent = entry.get("from_agent", "")
                to_agent = entry.get("to_agent", "")

                if parent_id in (from_agent, to_agent):
                    relevant.append(entry)

        if not relevant:
            return ""

        # Sort by timestamp, take most recent
        relevant.sort(key=lambda e: e.get("timestamp", ""), reverse=False)
        recent = relevant[-MAX_MESSAGE_ENTRIES:]

        # Prioritize: task_assignment first, then context_request, then others
        priority = {
            "task_assignment": 0,
            "context_request": 1,
            "context_response": 2,
            "status_update": 3,
        }
        recent.sort(
            key=lambda e: (
                priority.get(e.get("message_type", ""), 9),
                e.get("timestamp", ""),
            )
        )

        lines = ["=== WHY YOU WERE SPAWNED ==="]
        for i, entry in enumerate(recent[:10], 1):
            from_a = entry.get("from_agent", "?")
            to_a = entry.get("to_agent", "?")
            msg_type = entry.get("message_type", "message")
            ts = entry.get("timestamp", "")

            # Use summary if available, else truncated content
            text = entry.get("summary", "")
            if not text:
                text = entry.get("full_content", "")[:200]
            if not text:
                continue

            # Format relative time if possible
            time_label = _format_relative_time(ts) if ts else ""
            time_suffix = f", {time_label}" if time_label else ""

            lines.append(f"[{i}] {from_a} -> {to_a} ({msg_type}{time_suffix}):")
            # Indent the message content
            for msg_line in text.split("\n")[:3]:
                lines.append(f"    {msg_line.strip()}")

        return "\n".join(lines) if len(lines) > 1 else ""

    except Exception as e:
        log_hook_error("subagent_start", "_build_communication_section", e)
        return ""


def _build_task_section(parent_state: dict | None) -> str:
    """Read plan and extract details for the parent's current task."""
    if not parent_state:
        return ""

    task_id = parent_state.get("task")
    if not task_id:
        return ""

    # Find the plan
    workflow = _read_workflow_state()
    if not workflow:
        return ""

    feature = workflow.get("feature")
    if not feature:
        return ""

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    plan_file = Path(project_dir) / ".claude" / "plans" / f"{feature}-plan.json"
    plan = read_json_safe(plan_file)
    if not plan:
        return ""

    try:
        tasks = plan.get("tasks", plan.get("chunks", []))
        target_task = None
        for t in tasks:
            if t.get("id") == task_id:
                target_task = t
                break

        if not target_task:
            return ""

        lines = ["=== YOUR PARENT'S TASK ==="]
        lines.append(f"{target_task.get('id')}: {target_task.get('name', 'unnamed')}")

        desc = target_task.get("description", "")
        if desc:
            lines.append(f"  {desc[:300]}")

        # Touched files
        touched = target_task.get("touched_files", [])
        if touched:
            lines.append(f"  Files to modify: {', '.join(touched[:8])}")

        # Dependencies
        deps = target_task.get("dependencies", [])
        if deps:
            lines.append(f"  Depends on: {', '.join(deps)}")

        # Exploration/research queries from task metadata
        exp_queries = target_task.get("exploration_queries", [])
        if exp_queries:
            lines.append("  Exploration queries:")
            for q in exp_queries[:3]:
                lines.append(f"    - {q}")

        res_queries = target_task.get("research_queries", [])
        if res_queries:
            lines.append("  Research queries:")
            for q in res_queries[:3]:
                lines.append(f"    - {q}")

        return "\n".join(lines)

    except Exception as e:
        log_hook_error("subagent_start", "_build_key_files_section", e)
        return ""


def _build_key_files_section(parent_id: str) -> str:
    """Extract key files the parent has read from its session log."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    logs_dir = Path(project_dir) / ".claude" / "logs"
    if not logs_dir.exists():
        return ""

    try:
        for log_file in sorted(
            logs_dir.glob("*-log.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            data = read_json_safe(log_file)
            if not data:
                continue
            if data.get("agent_id") != parent_id:
                continue

            key_files = data.get("key_files_read", [])
            if not key_files:
                return ""

            lines = ["=== KEY FILES (parent has read) ==="]
            for entry in key_files[:8]:
                if isinstance(entry, dict):
                    path = entry.get("path", "")
                    relevance = entry.get("relevance", "")
                    lines.append(f"  - {path}")
                    if relevance:
                        lines.append(f"    ({relevance})")
                elif isinstance(entry, str):
                    lines.append(f"  - {entry}")

            return "\n".join(lines)
    except Exception as e:
        log_hook_error("subagent_start", "_build_preferences_section", e)
    return ""


def _build_preferences_section(parent_state: dict | None) -> str:
    """Load global + role-specific preferences."""
    raw = read_json_safe(PREFERENCES_FILE)
    if not raw:
        return ""

    # Validate with Pydantic if available
    if _HAS_PYDANTIC:
        try:
            validated = Preferences.model_validate(raw)  # type: ignore[union-attr]
            raw = validated.model_dump(mode="json", exclude_defaults=True, exclude_none=True)
        except Exception:
            pass  # fallback to raw dict

    role = parent_state.get("role", "unknown") if parent_state else "unknown"

    global_prefs = raw.get("global", {})
    role_prefs = raw.get("by_role", {}).get(role, {})

    if not global_prefs and not role_prefs:
        return ""

    lines = ["=== ACTIVE PREFERENCES ==="]

    for key, pref in global_prefs.items():
        value = pref.get("value", pref) if isinstance(pref, dict) else pref
        lines.append(f"  - {key}: {value}")

    for key, pref in role_prefs.items():
        value = pref.get("value", pref) if isinstance(pref, dict) else pref
        lines.append(f"  - [{role}] {key}: {value}")

    return "\n".join(lines)


def _build_ptc_section(parent_id: str, agent_type: str) -> str:
    """
    Build PTC sandbox context for the sub-agent.

    PTC V2: Bridge-networked containers with /workspace:rw.
    Agents write direct Python (open(), requests, subprocess) — no tool dispatch.
    Pre-built role images with packages baked in. Only print() enters context.
    """
    if not PTC_CONFIG_FILE.exists():
        return ""

    ptc_config = read_json_safe(PTC_CONFIG_FILE)
    if not ptc_config:
        return ""

    lines = ["=== PTC SANDBOX CONTEXT ==="]

    # Container info
    kernel_file = PTC_KERNELS_DIR / f"{parent_id}.json"
    kernel = read_json_safe(kernel_file)
    if kernel:
        lines.append(f"Container: {kernel.get('kernel_id', 'unknown')}")

    # Role image
    docker_cfg = ptc_config.get("docker", {})
    role_image = docker_cfg.get("role_image_pattern", "ptc-{role}:latest").replace("{role}", agent_type)
    lines.append(f"Image: {role_image}")

    # Network and mount info
    network = docker_cfg.get("network_mode", "bridge")
    lines.append(f"Network: {network}")
    lines.append("Project mount: /workspace (read-write)")

    return "\n".join(lines) if len(lines) > 1 else ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_workflow_state() -> dict | None:
    """Read workflow state file, validating with Pydantic if available."""
    raw = read_json_safe(WORKFLOW_STATE_FILE)
    if raw is None:
        return None
    if _HAS_PYDANTIC:
        try:
            validated = WorkflowState.model_validate(raw)  # type: ignore[union-attr]
            return validated.model_dump(mode="json", exclude_defaults=True, exclude_none=True)
        except Exception:
            return raw  # fallback to raw dict
    return raw


def _read_agent_state(agent_id: str) -> dict | None:
    """Read an agent's state file, validating with Pydantic if available."""
    if agent_id == "unknown":
        return None
    raw = read_json_safe(AGENT_STATE_DIR / f"{agent_id}.json")
    if raw is None:
        return None
    if _HAS_PYDANTIC:
        try:
            validated = AgentState.model_validate(raw)  # type: ignore[union-attr]
            return validated.model_dump(mode="json", exclude_defaults=True, exclude_none=True)
        except Exception:
            return raw  # fallback to raw dict
    return raw


def _format_relative_time(iso_ts: str) -> str:
    """Format an ISO timestamp as relative time (e.g., '2min ago')."""
    try:
        from datetime import datetime, timezone

        then = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - then
        seconds = int(delta.total_seconds())

        if seconds < 60:
            return f"{seconds}s ago"
        elif seconds < 3600:
            return f"{seconds // 60}min ago"
        elif seconds < 86400:
            return f"{seconds // 3600}h ago"
        else:
            return f"{seconds // 86400}d ago"
    except Exception as e:
        log_hook_error("subagent_start", "_format_relative_time", e)
        return ""


if __name__ == "__main__":
    main()
