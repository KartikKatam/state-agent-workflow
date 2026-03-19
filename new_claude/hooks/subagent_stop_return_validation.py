#!/usr/bin/env python3
"""
SubagentStop hook: Validates sub-agent return JSON against delegation return schemas.

When a sub-agent finishes, this hook checks if its last assistant message is
valid JSON matching the return schema for its declared delegation_type.

Hook type: SubagentStop
Exit code 2 = BLOCK (deny, sub-agent gets one retry via stop_hook_active guard)
Exit code 0 = ALLOW (permit the sub-agent to stop)

The stop_hook_active guard prevents infinite retry loops: if the sub-agent was
already blocked once, the second attempt always passes through.
"""

import json
import os
import sys

# Import validate_return from the delegation-prompts skill scripts.
# Both the project root (for schemas package) and the skill scripts dir
# (for validate_return module) must be on sys.path.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
SKILL_SCRIPTS_DIR = os.path.join(
    SCRIPT_DIR, "..", "skills", "delegation-prompts", "scripts"
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, SKILL_SCRIPTS_DIR)

from validate_return import validate_return  # noqa: E402


def get_hook_input() -> dict:
    """Read hook input from environment or stdin."""
    hook_data = os.environ.get("CLAUDE_HOOK_DATA")
    if hook_data:
        return json.loads(hook_data)

    if not sys.stdin.isatty():
        return json.load(sys.stdin)

    return {}


def _deny(reason: str):
    """Print deny output and exit with code 2."""
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStop",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(output))
    sys.exit(2)


def main():
    """Main hook handler."""
    hook_input = get_hook_input()

    # stop_hook_active guard: if this is a retry after a previous block,
    # let it through to prevent infinite loops
    if hook_input.get("stop_hook_active"):
        return

    last_message = hook_input.get("last_assistant_message", "")
    if not last_message:
        return

    # Only validate if the return looks like it could be structured
    # (contains a brace). Plain text returns from non-delegation sub-agents
    # pass through.
    if "{" not in last_message:
        return

    # Try to validate
    result = validate_return(last_message)

    if not result["valid"]:
        error_summary = " ".join(result["errors"])
        _deny(error_summary)


if __name__ == "__main__":
    main()
