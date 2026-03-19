#!/usr/bin/env python3
"""PostToolUse Hook — Thin daemon proxy.

Reads hook input from stdin, sends {command: "post_tool", ...} to the
workflow daemon over Unix socket, translates the response to Claude Code
hookSpecificOutput format.

Permissive fallback: if daemon is unreachable, produce empty output.
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
            return
        raw = sys.stdin.read().strip()
        if not raw:
            return
        data = json.loads(raw)
    except Exception:
        return  # Can't parse — permissive fallback

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    tool_output = data.get("tool_output", "")
    if not isinstance(tool_output, str):
        tool_output = json.dumps(tool_output, default=str)
    # Truncate large outputs to avoid socket overhead
    tool_output_summary = tool_output[:2000] if tool_output else ""

    resp = _send_to_daemon(
        {
            "command": "post_tool",
            "agent_id": _AGENT_ID,
            "tool": tool_name,
            "tool_input": tool_input,
            "tool_output_summary": tool_output_summary,
        }
    )

    if resp is None:
        return  # Daemon down — permissive fallback (empty output)

    # Build hookSpecificOutput
    result: dict = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
        }
    }

    # Inject context
    inject = resp.get("inject_context")
    if inject:
        result["hookSpecificOutput"]["additionalContext"] = inject

    # Feedback array
    feedback = resp.get("feedback")
    if feedback:
        result["feedback"] = feedback

    # Only print if we have something to say
    if inject or feedback:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
