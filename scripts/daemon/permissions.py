"""Permission checking — handle_pre_tool, hard blocks, ask-user, SM gating.

Extracted from scripts/workflow_state.py (Chunk 2 of daemon hardening plan).
"""

from __future__ import annotations

import re

from schemas.state_machine import StateDefinition

from scripts.daemon.state_manager import (
    _get_agent_state_cached,
    get_agent_state,
    resolve_machine,
)


# ---------------------------------------------------------------------------
# Glob matching (supports ** recursive patterns)
# ---------------------------------------------------------------------------
def path_matches_glob(file_path: str, pattern: str) -> bool:
    """Match a file path against a glob pattern. Supports ** for recursive."""
    file_path = file_path.strip("/")
    pattern = pattern.strip("/")
    # Strip leading ./ from both
    if file_path.startswith("./"):
        file_path = file_path[2:]
    if pattern.startswith("./"):
        pattern = pattern[2:]

    # Convert glob to regex
    regex = ""
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*" and i + 1 < len(pattern) and pattern[i + 1] == "*":
            regex += ".*"
            i += 2
            if i < len(pattern) and pattern[i] == "/":
                i += 1  # skip slash after **
        elif c == "*":
            regex += "[^/]*"
        elif c == "?":
            regex += "[^/]"
        elif c in r".+^${}()|[]\\":
            regex += "\\" + c
        else:
            regex += c
        i += 1

    return bool(re.match(regex + "$", file_path))


# ---------------------------------------------------------------------------
# Core tool permission check (state machine gating)
# ---------------------------------------------------------------------------
def check_tool_allowed(agent_id: str, tool_name: str, tool_input: dict) -> dict:
    """Check if a tool call is allowed for an agent in its current state.

    Returns {"allowed": bool, "reason": str}.
    Permissive fallback: if anything is missing, allow with warning.
    """
    agent = get_agent_state(agent_id)
    if not agent:
        return {"allowed": True, "reason": "warning: agent state not found, allowing"}

    machine = resolve_machine(agent.role, agent.current_state)
    if not machine:
        # Generalist or no machine found — allow everything
        return {"allowed": True, "reason": ""}

    # Find current state definition
    state_def: StateDefinition | None = next(
        (s for s in machine.states if s.name == agent.current_state), None
    )
    if not state_def:
        return {
            "allowed": True,
            "reason": f"warning: state {agent.current_state} not in machine, allowing",
        }

    # Check blocked_tools
    if tool_name in state_def.blocked_tools:
        return {
            "allowed": False,
            "reason": f"{agent.role} in {agent.current_state}: {tool_name} is blocked",
        }

    # Check write/edit restrictions
    if tool_name in ("Write", "Edit"):
        if not state_def.write_allowed:
            return {
                "allowed": False,
                "reason": f"{agent.role} in {agent.current_state}: writes not allowed",
            }

        file_path = tool_input.get("file_path", "")
        if state_def.write_globs and file_path:
            if not any(path_matches_glob(file_path, g) for g in state_def.write_globs):
                return {
                    "allowed": False,
                    "reason": (
                        f"{agent.role} in {agent.current_state}: "
                        f"write to {file_path} not in allowed globs "
                        f"{state_def.write_globs}"
                    ),
                }

    # Check read restrictions (read_globs_exclude)
    if tool_name in ("Read", "Glob", "Grep") and state_def.read_globs_exclude:
        file_path = tool_input.get("file_path") or tool_input.get("path") or ""
        if file_path:
            if any(path_matches_glob(file_path, g) for g in state_def.read_globs_exclude):
                return {
                    "allowed": False,
                    "reason": (
                        f"{agent.role} in {agent.current_state}: "
                        f"read of {file_path} blocked by read_globs_exclude"
                    ),
                }

    # Permissive fallback — allow everything not explicitly blocked
    return {"allowed": True, "reason": ""}


# ---------------------------------------------------------------------------
# Tier 1: Hard-blocked patterns (no override)
# ---------------------------------------------------------------------------
_HARD_BLOCK_BASH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-rf\s+/"),  # rm -rf /
    re.compile(r"\brm\s+-rf\s+~"),  # rm -rf ~
    re.compile(r"\brm\s+-rf\s+\*"),  # rm -rf *
    re.compile(r"\bDROP\s+(TABLE|DATABASE)\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+777\b"),  # chmod 777
    re.compile(r"\bsystemctl\b"),  # systemctl
    re.compile(r"\bmkfs\b"),  # mkfs
    re.compile(r"\bdd\s+if="),  # dd if=
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\}"),  # fork bomb
    re.compile(r"\bshutdown\b"),  # shutdown
    re.compile(r"\breboot\b"),  # reboot
    re.compile(r"\bformat\s+[A-Z]:", re.IGNORECASE),  # format drive
]


def _is_hard_blocked(tool: str, tool_input: dict) -> str | None:
    """Check if a tool call is hard-blocked. Returns reason or None."""
    if tool == "Bash":
        command = tool_input.get("command", "")
        for pattern in _HARD_BLOCK_BASH_PATTERNS:
            if pattern.search(command):
                return f"Hard blocked: dangerous command pattern ({pattern.pattern})"
    return None


# ---------------------------------------------------------------------------
# Tier 2: Ask-user patterns (user can approve)
# ---------------------------------------------------------------------------
_ASK_BASH_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bgit\s+push\b"), "git push — affects remote repository"),
    (re.compile(r"\bgit\s+push\s+.*--force\b"), "force push — destructive"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "hard reset — discards changes"),
    (re.compile(r"\bpip\s+install\b"), "pip install — modifies environment"),
    (re.compile(r"\buv\s+(add|install|pip)\b"), "uv package operation"),
    (re.compile(r"\bnpm\s+install\b"), "npm install — modifies node_modules"),
    (re.compile(r"\bcurl\b"), "network request via curl"),
    (re.compile(r"\bwget\b"), "network request via wget"),
    (re.compile(r"\bssh\b"), "SSH connection"),
    (re.compile(r"\bdocker\s+(rm|rmi|system\s+prune)\b"), "docker cleanup"),
    (re.compile(r"\brm\s+(-[rfi]+\s+)*[^\s/]"), "file deletion"),
]

# Roles exempt from ask-user checks (elevated permissions)
_ASK_EXEMPT_ROLES: set[str] = {"generalist"}


def _needs_user_ask(tool: str, tool_input: dict, role: str) -> str | None:
    """Check if a tool call should ask the user. Returns reason or None."""
    if role in _ASK_EXEMPT_ROLES:
        return None
    if tool == "Bash":
        command = tool_input.get("command", "")
        for pattern, reason in _ASK_BASH_PATTERNS:
            if pattern.search(command):
                return reason
    return None


# ---------------------------------------------------------------------------
# handle_pre_tool — consolidated pre-tool checks
# ---------------------------------------------------------------------------
def handle_pre_tool(agent_id: str, tool: str, tool_input: dict) -> dict:
    """Handle pre-tool checks. Returns {"allowed": True/False, ...}."""
    # 1. Hard blocks
    reason = _is_hard_blocked(tool, tool_input)
    if reason:
        return {"allowed": False, "reason": reason, "hard_block": True}

    # 2. Handoff blocking
    agent_state = _get_agent_state_cached(agent_id)
    if agent_state and agent_state.get("pending_handoff"):
        if tool in ("Write", "Edit"):
            path = tool_input.get("file_path", "") or tool_input.get("path", "")
            if not path or ".claude/handoffs/" not in path:
                return {
                    "allowed": False,
                    "reason": "Handoff pending. Only handoff file writes allowed.",
                }
        elif tool not in ("Read", "Glob", "Grep", "Think") and not tool.endswith("__think"):
            return {
                "allowed": False,
                "reason": "Handoff pending. Only reads and handoff writes allowed.",
            }

    # 2.5 Inbox blocking (blocking message awaiting ack)
    if agent_state and agent_state.get("inbox_blocked"):
        blocked_info = agent_state["inbox_blocked"]
        # Allow: PTC tools, send-msg CLI, read tools, think tools
        if tool.startswith("mcp__local-ptc__"):
            pass  # Allow PTC tools (read inbox)
        elif tool == "Bash":
            command = tool_input.get("command", "")
            if "send-msg" not in command:
                return {
                    "allowed": False,
                    "reason": (
                        f"Inbox blocked: unread blocking message from "
                        f"{blocked_info.get('from_agent', 'unknown')}. "
                        f"Read inbox via PTC, then run: "
                        f"send-msg ack --message-id {blocked_info.get('blocked_by_message_id')}"
                    ),
                }
        elif tool in ("Read", "Glob", "Grep"):
            pass  # Allow reads
        elif tool.endswith("__think") or tool == "Think":
            pass  # Allow thinking
        else:
            return {
                "allowed": False,
                "reason": (
                    f"Inbox blocked: unread blocking message from "
                    f"{blocked_info.get('from_agent', 'unknown')}. "
                    f"Read inbox via PTC, then run: "
                    f"send-msg ack --message-id {blocked_info.get('blocked_by_message_id')}"
                ),
            }

    # 3. Validation error blocking
    if agent_state and agent_state.get("pending_validation_error"):
        if tool in ("Write", "Edit", "Bash"):
            verr = agent_state["pending_validation_error"]
            msg = (
                verr.get("message", "fix required")
                if isinstance(verr, dict)
                else str(verr)
            )
            return {
                "allowed": False,
                "reason": (
                    f"Validation error pending: {msg}. "
                    "Fix the issue and the validator will clear the block."
                ),
            }

    # 4. Annotation blocking
    if agent_state and agent_state.get("pending_critical_annotation"):
        if tool in ("Write", "Edit", "Bash"):
            ann = agent_state["pending_critical_annotation"]
            ann_id = (
                ann if isinstance(ann, str) else ann.get("annotation_id", "unknown")
            )
            return {
                "allowed": False,
                "reason": (
                    f"Critical annotation pending. Use Think tool to acknowledge "
                    f"annotation '{ann_id}' before proceeding."
                ),
            }

    # 5. State machine gating
    sm_result = check_tool_allowed(agent_id, tool, tool_input)
    if sm_result and not sm_result.get("allowed", True):
        return sm_result

    # 6. Ask-user patterns
    role = (agent_state or {}).get("role", "")
    ask_reason = _needs_user_ask(tool, tool_input, role)
    if ask_reason:
        return {"allowed": False, "ask_user": True, "reason": ask_reason}

    # 7. All checks pass
    return {"allowed": True}
