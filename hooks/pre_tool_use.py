#!/usr/bin/env python3
"""PreToolUse Hook — Thin daemon proxy.

Reads hook input from stdin, sends {command: "pre_tool", ...} to the
workflow daemon over Unix socket, translates the response to Claude Code
hookSpecificOutput format.

Permissive fallback: if daemon is unreachable, allow everything through.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys

_WORKFLOW_ID = os.environ.get("WORKFLOW_ID", "default")
if not re.match(r"^[a-zA-Z0-9_-]+$", _WORKFLOW_ID):
    _WORKFLOW_ID = "default"
_AGENT_ID = os.environ.get("CLAUDE_CODE_AGENT_NAME", "")
_SOCKET_PATH = f"/tmp/workflow-state-{_WORKFLOW_ID}.sock"
_SOCKET_TIMEOUT = 2.0


def _send_to_daemon(request: dict) -> dict | None:
    """Send request to daemon, return response or None on failure."""
    if not os.path.exists(_SOCKET_PATH):
        return None
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(_SOCKET_TIMEOUT)
        s.connect(_SOCKET_PATH)
        s.sendall((json.dumps(request) + "\n").encode())
        response = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"\n" in response:
                break
        s.close()
        return json.loads(response.decode().strip())
    except Exception:
        return None


def main() -> None:
    """Read stdin, proxy to daemon, translate response."""
    try:
        if sys.stdin.isatty():
            sys.exit(0)
        raw = sys.stdin.read().strip()
        if not raw:
            sys.exit(0)
        data = json.loads(raw)
    except Exception:
        sys.exit(0)  # Can't parse — permissive fallback

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    resp = _send_to_daemon(
        {
            "command": "pre_tool",
            "agent_id": _AGENT_ID,
            "tool": tool_name,
            "tool_input": tool_input,
        }
    )

    if resp is None:
        sys.exit(0)  # Daemon down — permissive fallback

    # Hard block
    if resp.get("hard_block"):
        reason = resp.get("reason", "Hard blocked by daemon")
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
        sys.exit(2)

    # Ask user
    if not resp.get("allowed", True) and resp.get("ask_user"):
        reason = resp.get("reason", "Requires user approval")
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "ask",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
        sys.exit(0)

    # Deny
    if not resp.get("allowed", True):
        reason = resp.get("reason", "Denied by daemon")
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
        sys.exit(0)

    # Inject context (allowed but with additional info)
    inject = resp.get("inject_context")
    if inject:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "additionalContext": inject,
                    }
                }
            )
        )
        sys.exit(0)

    # Allowed — no output needed
    sys.exit(0)


if __name__ == "__main__":
    main()
