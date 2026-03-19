#!/usr/bin/env python3
"""Lightweight PreToolUse hook for /orchestrate freelance mode.

Enforces: Think before delegating (spawning sub-agents).
Injects: Role-specific think prompts based on the agent's role.

Think prompts are loaded from:
  ~/personal/agentic_workflow/commands/orchestrate/think_prompts/{role}.txt

If no role-specific prompt exists, falls back to a structured default.
When the delegation-prompts skill is finalized, it will provide richer
prompts through this same mechanism.
"""

import json
import os
import sys
from pathlib import Path

STATE_DIR = Path(os.environ.get("ORCHESTRATE_STATE_DIR", "/tmp/orchestrate-state"))
PROMPTS_DIR = Path(__file__).parent.parent / "think_prompts"


def _agent_state_path() -> Path:
    agent_name = os.environ.get("CLAUDE_CODE_AGENT_NAME", "main")
    return STATE_DIR / f"{agent_name}.json"


KNOWN_ROLES = {"orchestrator", "coder", "explorer", "researcher", "auditor", "planner", "scribe"}


def _infer_role() -> str:
    """Infer agent role from CLAUDE_CODE_AGENT_NAME or env var.

    Naming convention: agents are named like 'coder-auth-refactor',
    'explorer-data-layer', etc. The first segment before '-' is the role.
    Falls back to ORCHESTRATE_AGENT_ROLE env var, then 'unknown'.
    """
    # Explicit env var takes priority
    env_role = os.environ.get("ORCHESTRATE_AGENT_ROLE", "")
    if env_role in KNOWN_ROLES:
        return env_role

    # Infer from agent name prefix
    agent_name = os.environ.get("CLAUDE_CODE_AGENT_NAME", "")
    if agent_name:
        prefix = agent_name.split("-")[0].lower()
        if prefix in KNOWN_ROLES:
            return prefix

    # Main session is the orchestrator
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
        "has_thought_since_last_delegation": False,
        "role": _infer_role(),
    }


def _load_think_prompt(role: str) -> str:
    """Load role-specific think prompt, falling back to default."""
    role_file = PROMPTS_DIR / f"{role}.txt"
    if role_file.exists():
        try:
            return role_file.read_text().strip()
        except OSError:
            pass

    default_file = PROMPTS_DIR / "default.txt"
    if default_file.exists():
        try:
            return default_file.read_text().strip()
        except OSError:
            pass

    # Hardcoded fallback if no prompt files exist at all
    return (
        "Before delegating, think through:\n"
        "1. What is the sub-agent's specific task and success criteria?\n"
        "2. What known context (files, functions, constraints) do they need?\n"
        "3. What is the output contract (path, format, completion signal)?\n"
        "4. Which role and skills from ~/personal/agentic_workflow/.claude/skills/ apply?"
    )


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    tool_name = hook_input.get("tool_name", "")

    if tool_name == "Agent":
        state = _read_state()
        if not state.get("has_thought_since_last_delegation", False):
            role = state.get("role", os.environ.get("ORCHESTRATE_AGENT_ROLE", "unknown"))
            think_prompt = _load_think_prompt(role)

            result = {
                "permissionDecision": "deny",
                "reason": (
                    f"Think before delegating. You must call Think and reason through "
                    f"the following before spawning a sub-agent:\n\n{think_prompt}"
                ),
            }
            json.dump(result, sys.stdout)
            return

    json.dump({}, sys.stdout)


if __name__ == "__main__":
    main()
