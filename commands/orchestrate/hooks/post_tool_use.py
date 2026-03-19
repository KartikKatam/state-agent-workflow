#!/usr/bin/env python3
"""Lightweight PostToolUse hook for /orchestrate freelance mode.

Tracks Think calls with full content for audit trail.
Resets the delegation gate after each spawn.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path(os.environ.get("ORCHESTRATE_STATE_DIR", "/tmp/orchestrate-state"))


def _agent_state_path() -> Path:
    agent_name = os.environ.get("CLAUDE_CODE_AGENT_NAME", "main")
    return STATE_DIR / f"{agent_name}.json"


KNOWN_ROLES = {"orchestrator", "coder", "explorer", "researcher", "auditor", "planner", "scribe"}


def _infer_role() -> str:
    """Infer agent role from CLAUDE_CODE_AGENT_NAME or env var.

    Naming convention: agents named 'coder-auth-refactor', 'explorer-data-layer'.
    First segment before '-' is the role.
    """
    env_role = os.environ.get("ORCHESTRATE_AGENT_ROLE", "")
    if env_role in KNOWN_ROLES:
        return env_role

    agent_name = os.environ.get("CLAUDE_CODE_AGENT_NAME", "")
    if agent_name:
        prefix = agent_name.split("-")[0].lower()
        if prefix in KNOWN_ROLES:
            return prefix

    if not agent_name or agent_name == "main":
        return "orchestrator"

    return "unknown"


def _read_state() -> dict:
    path = _agent_state_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "agent_name": os.environ.get("CLAUDE_CODE_AGENT_NAME", "main"),
        "role": _infer_role(),
        "has_thought_since_last_delegation": False,
        "delegation_count": 0,
        "think_history": [],
    }


def _write_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _agent_state_path()
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, suffix=".tmp")
    try:
        os.write(fd, json.dumps(state, indent=2).encode())
        os.close(fd)
        os.replace(tmp, path)
    except Exception:
        os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _extract_think_content(hook_input: dict) -> str:
    """Extract the Think tool's content from the hook input.

    PostToolUse receives tool_output or tool_result depending on the tool.
    Think tool output is typically a string with the agent's reasoning.
    """
    # Try multiple paths where Claude Code might put the output
    for key in ("tool_output", "tool_result", "output"):
        val = hook_input.get(key)
        if val:
            if isinstance(val, str):
                return val
            if isinstance(val, dict):
                return json.dumps(val)
    # Fallback: grab the full input payload for debugging
    return hook_input.get("tool_input", {}).get("thought", "")


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    tool_name = hook_input.get("tool_name", "")
    state = _read_state()
    now = datetime.now(timezone.utc).isoformat()

    if tool_name in ("Think", "mcp__think__think"):
        content = _extract_think_content(hook_input)
        state["has_thought_since_last_delegation"] = True

        # Append to auditable think history
        entry = {
            "timestamp": now,
            "content": content[:4000],  # Cap at 4KB to prevent state file bloat
            "delegation_index": state.get("delegation_count", 0),
        }
        if "think_history" not in state:
            state["think_history"] = []
        state["think_history"].append(entry)
        _write_state(state)

    elif tool_name == "Agent":
        # Record what was delegated alongside the think that preceded it
        delegation_prompt = hook_input.get("tool_input", {}).get("prompt", "")
        entry = {
            "timestamp": now,
            "type": "delegation",
            "prompt_preview": delegation_prompt[:2000],
            "delegation_index": state.get("delegation_count", 0),
        }
        if "think_history" not in state:
            state["think_history"] = []
        state["think_history"].append(entry)

        state["has_thought_since_last_delegation"] = False
        state["delegation_count"] = state.get("delegation_count", 0) + 1
        _write_state(state)

    json.dump({}, sys.stdout)


if __name__ == "__main__":
    main()
