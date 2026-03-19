#!/usr/bin/env python3
"""
Stop Hook — Orchestrator-only session end validation and summary.

Fires when the main Claude Code agent finishes responding. Does NOT fire
on user interrupt.

Scoped to orchestrator only — skips immediately if CLAUDE_CODE_AGENT_NAME
is set (teammates are handled by SubagentStop). This prevents adding latency
to every teammate turn.

Checks stop_hook_active to prevent infinite loops.

Stdin: {"stop_hook_active": bool, "last_assistant_message": "...", ...}
Output: Summary text to stdout (shown to user), or
        {"decision": "block", "reason": "..."} to prevent stopping.

Design doc ref: "Stop Hooks" section (lines 392-405).
"""

from __future__ import annotations

import os
import subprocess
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
from hooks.utils.event_logger import emit_session_ended
from hooks.utils.state_helpers import (
    AGENT_STATE_DIR,
    get_agent_id,
    locked_read_modify_write,
    read_json_safe,
    read_stdin,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CLAUDE_HOME = Path(os.path.expanduser("~/.claude"))
WORKFLOW_STATE_FILE = _CLAUDE_HOME / "state" / "workflow.json"


def main() -> None:
    """Orchestrator-only stop hook: summary, state update, event emission."""

    # Skip immediately for teammates — SubagentStop handles them
    agent_name = os.environ.get("CLAUDE_CODE_AGENT_NAME")
    if agent_name:
        return  # Teammate — exit silently, no latency added

    stdin_data = read_stdin()

    # Prevent infinite loops: if stop hook already active, don't block
    if stdin_data.get("stop_hook_active", False):
        return

    agent_id = get_agent_id()

    # Emit session ended event
    emit_session_ended(agent_id)

    # Update workflow state timestamp
    _update_workflow_timestamp()

    # Build summary for user
    output = _build_session_summary()

    if output:
        print(output)


# ---------------------------------------------------------------------------
# Session summary (user-facing)
# ---------------------------------------------------------------------------


def _build_session_summary() -> str:
    """Build end-of-session summary for the user."""
    sections: dict[str, list[str]] = {
        "summary": [],
        "warnings": [],
        "next_steps": [],
    }

    # Workflow state
    workflow = read_json_safe(WORKFLOW_STATE_FILE)
    if workflow:
        feature = workflow.get("feature", "unknown")
        wf_state = workflow.get("current_state", "IDLE")
        sections["summary"].append(f"Workflow: {feature} ({wf_state})")

        phase = workflow.get("current_phase")
        if phase:
            sections["summary"].append(f"Phase: {phase}")

    # Check for orphaned active agents
    orphaned = _find_orphaned_agents()
    if orphaned:
        sections["warnings"].append(
            f"{len(orphaned)} agent(s) still marked active (may be stale):"
        )
        for agent in orphaned[:5]:
            aid = agent.get("id", "?")
            role = agent.get("role", "?")
            state = agent.get("current_state", "?")
            sections["warnings"].append(f"  - {aid} ({role}, state: {state})")

    # Check for uncommitted changes
    uncommitted = _get_uncommitted_changes()
    if uncommitted:
        sections["warnings"].append(f"{len(uncommitted)} uncommitted change(s):")
        for change in uncommitted[:10]:
            sections["warnings"].append(f"  {change}")
        if len(uncommitted) > 10:
            sections["warnings"].append(f"  ... and {len(uncommitted) - 10} more")
        sections["next_steps"].append(
            "Consider committing your changes before next session"
        )

    # Check for active plan with in-progress work
    active_plan = _get_active_plan_status()
    if active_plan:
        sections["summary"].append(
            f"Plan: {active_plan['feature']} — "
            f"{active_plan['completed']}/{active_plan['total']} tasks complete"
        )
        if active_plan.get("current_task"):
            ct = active_plan["current_task"]
            sections["next_steps"].append(f"Resume: {ct['id']} — {ct['name']}")

    # Check for handoff files
    handoff_count = _count_handoff_files()
    if handoff_count:
        sections["warnings"].append(
            f"{handoff_count} handoff file(s) found — agents may need re-spawning"
        )

    # Format
    lines: list[str] = []

    if sections["summary"]:
        lines.append("## Session Summary")
        lines.extend(sections["summary"])
        lines.append("")

    if sections["warnings"]:
        lines.append("## Warnings")
        lines.extend(sections["warnings"])
        lines.append("")

    if sections["next_steps"]:
        lines.append("## Next Steps")
        for step in sections["next_steps"]:
            lines.append(f"- {step}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# State checks
# ---------------------------------------------------------------------------


def _find_orphaned_agents() -> list[dict]:
    """Find agent state files still marked active (likely stale from crashes)."""
    agents = []
    try:
        if not AGENT_STATE_DIR.exists():
            return []
        for state_file in AGENT_STATE_DIR.glob("*.json"):
            state = read_json_safe(state_file)
            if state and state.get("status") in ("active", "draining"):
                agents.append(state)
    except Exception as e:
        log_hook_error("stop", "_find_orphaned_agents", e)
    return agents


def _get_uncommitted_changes() -> list[str]:
    """Get list of uncommitted changes via git status."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout:
            return [
                line.strip() for line in proc.stdout.strip().split("\n") if line.strip()
            ]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return []


def _get_active_plan_status() -> dict | None:
    """Read active plan and summarize progress."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    plans_dir = Path(project_dir) / ".claude" / "plans"
    if not plans_dir.exists():
        return None

    try:
        for plan_file in sorted(
            plans_dir.glob("*-plan.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            plan = read_json_safe(plan_file)
            if not plan:
                continue

            tasks = plan.get("tasks", plan.get("chunks", []))
            feature = plan.get("meta", {}).get("feature_name", plan_file.stem)
            completed = sum(1 for t in tasks if t.get("status") == "completed")
            total = len(tasks)

            current_task = None
            for t in tasks:
                if t.get("status") == "in_progress":
                    current_task = {
                        "id": t.get("id"),
                        "name": t.get("name"),
                    }
                    break

            return {
                "feature": feature,
                "completed": completed,
                "total": total,
                "current_task": current_task,
            }
    except Exception as e:
        log_hook_error("stop", "_get_active_plan_status", e)
    return None


def _count_handoff_files() -> int:
    """Count handoff files in project directory."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    handoffs_dir = Path(project_dir) / ".claude" / "handoffs"
    if not handoffs_dir.exists():
        return 0
    try:
        return len(list(handoffs_dir.glob("*.json")))
    except Exception as e:
        log_hook_error("stop", "_count_handoff_files", e)
        return 0


# ---------------------------------------------------------------------------
# State update
# ---------------------------------------------------------------------------


def _update_workflow_timestamp() -> None:
    """Update workflow state file with last_updated timestamp."""
    try:
        if not WORKFLOW_STATE_FILE.exists():
            return

        def _set_timestamp(data: dict) -> dict:
            data["last_updated"] = datetime.now(timezone.utc).isoformat()
            return data

        locked_read_modify_write(WORKFLOW_STATE_FILE, _set_timestamp)
    except Exception as e:
        log_hook_error("stop", "_update_workflow_timestamp", e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    main()
