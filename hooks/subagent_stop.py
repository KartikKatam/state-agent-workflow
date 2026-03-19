#!/usr/bin/env python3
"""
SubagentStop Hook — Validate workflow sub-agent output before accepting completion.

Fires when a sub-agent finishes responding. Matched to workflow agent types
(coder|explorer|researcher|strategist|tester|auditor) — extraction sub-agents
pass through unmatched.

File-based validation: checks if expected output files were written.

Checks stop_hook_active to prevent infinite loops.

Stdin: {
    "stop_hook_active": bool,
    "agent_id": "...",
    "agent_type": "...",
    "agent_transcript_path": "...",
    "last_assistant_message": "...",
    ...
}
Output: {"decision": "block", "reason": "..."} to prevent stopping, or
        exit 0 with no output to allow.
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
from hooks.utils.event_logger import emit_agent_terminated, emit_subagent_validated
from hooks.utils.state_helpers import (
    AGENT_STATE_DIR,
    get_agent_id,
    locked_read_modify_write,
    read_stdin,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum meaningful output length (chars)
MIN_OUTPUT_LENGTH = 50


def main() -> None:
    """Validate sub-agent output, block if incomplete."""
    stdin_data = read_stdin()

    # Prevent infinite loops
    if stdin_data.get("stop_hook_active", False):
        return  # Already continuing from a stop hook — allow through

    agent_id = stdin_data.get("agent_id", "unknown")
    agent_type = stdin_data.get("agent_type", "unknown")
    last_message = stdin_data.get("last_assistant_message", "")

    # Emit termination event (observability — always, even if we block)
    parent_id = get_agent_id()
    emit_agent_terminated(agent_id, reason="subagent_stop")

    # Validate output
    valid, reason = _validate_agent_output(agent_type, last_message)

    # Emit validation event
    emit_subagent_validated(agent_id, agent_type, valid, reason)

    if not valid:
        # Block — sub-agent continues working
        output = {
            "decision": "block",
            "reason": reason,
        }
        print(json.dumps(output))
    else:
        # Allow — update parent state if needed
        _update_parent_state(parent_id, agent_id, agent_type)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_agent_output(
    agent_type: str, last_message: str
) -> tuple[bool, str]:
    """
    Validate sub-agent output. Uses file-based checks for agent types
    that produce deliverable files. Permissive for unknown types.
    """
    # Basic output check — applies to all modes
    if not last_message or len(last_message.strip()) < MIN_OUTPUT_LENGTH:
        return False, (
            f"Sub-agent produced insufficient output ({len(last_message.strip())} chars). "
            "Complete your assigned work before stopping."
        )

    # File-based validation by agent type
    file_result = _validate_file_output(agent_type)
    if file_result is not None:
        return file_result

    # No file check applicable — allow (permissive fallback)
    return True, ""


def _validate_file_output(agent_type: str) -> tuple[bool, str] | None:
    """
    Check if expected output files were written.
    Returns None if no file check is applicable for this agent type.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")

    checks: dict[str, tuple[str, str]] = {
        "explorer": (
            str(Path(project_dir) / ".claude" / "context"),
            "Context packet not found. Write your exploration results to "
            ".claude/context/ before stopping.",
        ),
        "researcher": (
            str(Path(project_dir) / ".claude" / "research"),
            "Research results not found. Write your findings to "
            ".claude/research/ before stopping.",
        ),
        "strategist": (
            str(Path(project_dir) / ".claude" / "plans"),
            "Plan not found. Write the plan to .claude/plans/ before stopping.",
        ),
    }

    check = checks.get(agent_type)
    if check is None:
        return None  # No file check for this agent type

    dir_path, fail_message = check

    try:
        output_dir = Path(dir_path)
        if not output_dir.exists():
            return False, fail_message

        # Check for recently modified JSON files (within last 10 minutes)
        now = datetime.now(timezone.utc).timestamp()
        recent_cutoff = now - 600  # 10 minutes

        for f in output_dir.glob("*.json"):
            try:
                if f.stat().st_mtime > recent_cutoff:
                    return True, ""
            except OSError:
                continue

        return False, fail_message
    except Exception as e:
        log_hook_error("subagent_stop", "_validate_file_output", e)
        return None


# ---------------------------------------------------------------------------
# Parent state update
# ---------------------------------------------------------------------------


def _update_parent_state(parent_id: str, subagent_id: str, agent_type: str) -> None:
    """
    Update the parent agent's state file to note sub-agent completion.
    Fire-and-forget — errors silently caught.
    """
    if parent_id == "unknown":
        return
    try:
        state_file = AGENT_STATE_DIR / f"{parent_id}.json"
        if not state_file.exists():
            return

        def _add_subagent(data: dict) -> dict:
            completed = data.setdefault("completed_subagents", [])
            completed.append(
                {
                    "id": subagent_id,
                    "type": agent_type,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            data["completed_subagents"] = completed[-20:]
            return data

        locked_read_modify_write(state_file, _add_subagent)
    except Exception as e:
        log_hook_error("subagent_stop", "_update_parent_state", e)


if __name__ == "__main__":
    main()
