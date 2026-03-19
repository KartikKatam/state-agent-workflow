#!/usr/bin/env python3
"""
PostToolUse Hook: Auto-lint, detect user corrections, check context thresholds.

Triggered after tool calls. Runs quick lint on file writes,
detects if user manually edited agent-generated files, and
monitors context window usage to trigger handoff protocols.

Hook type: PostToolUse
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

# Add parent directory to path for utils import
sys.path.insert(0, str(Path(__file__).parent))

from utils.context_monitor import (
    check_context_usage,
    format_context_warning,
    log_context_check,
    should_auto_handoff,
)
from utils.event_logger import (
    emit_context_pressure,
    emit_file_written,
    emit_skill_loaded,
)


def get_hook_input() -> dict:
    """Read hook input from environment or stdin."""
    hook_data = os.environ.get("CLAUDE_HOOK_DATA")
    if hook_data:
        return json.loads(hook_data)

    if not sys.stdin.isatty():
        return json.load(sys.stdin)

    return {}


def get_agent_info(hook_input: dict) -> dict:
    """Extract agent information from hook input and environment."""
    return {
        "agent_id": os.environ.get(
            "CLAUDE_CODE_AGENT_NAME", hook_input.get("session_id", "unknown")
        ),
        "agent_type": os.environ.get("CLAUDE_CODE_AGENT_TYPE", "unknown"),
        "agent_mode": "background"
        if os.environ.get("CLAUDE_CODE_AGENT_TYPE") in ("background",)
        else "foreground",
        "model": "sonnet",
    }


# Directory for context metrics written by scripts/statusline.sh
_CONTEXT_METRICS_DIR = Path.home() / ".claude/temp"


def get_context_info(session_id: str = "default") -> dict | None:
    """
    Read context usage from the status line's per-session temp file.

    The status line script writes context_window metrics to
    ~/.claude/temp/context-metrics-{session_id}.json on every update.
    Each agent gets its own file so concurrent agents don't clobber.
    """
    metrics_file = _CONTEXT_METRICS_DIR / f"context-metrics-{session_id}.json"
    if not metrics_file.exists():
        return None

    try:
        with open(metrics_file) as f:
            metrics = json.load(f)

        tokens_used = metrics.get("input_tokens")
        tokens_max = metrics.get("context_window_size")

        if tokens_used is not None:
            return {
                "tokens_used": tokens_used,
                "tokens_max": tokens_max,
            }
    except (OSError, json.JSONDecodeError, KeyError):
        pass

    return None


def check_context_thresholds(
    agent_info: dict,
    session_id: str = "default",
) -> dict | None:
    """
    Check context thresholds and return status/actions needed.

    Returns None if context info not available, otherwise returns
    status dict with level, message, and recommended action.
    """
    context_info = get_context_info(session_id)
    if not context_info:
        return None

    # Check current usage
    status = check_context_usage(
        tokens_used=context_info["tokens_used"],
        tokens_max=context_info.get("tokens_max"),
        model=agent_info.get("model", "sonnet"),
    )

    # Log the check
    log_context_check(agent_info["agent_id"], status)

    # Determine action based on agent mode
    agent_mode: Literal["foreground", "background"] = agent_info.get(
        "agent_mode", "foreground"
    )

    result = {
        "level": status["level"],
        "percentage": status["percentage"],
        "tokens_used": status["tokens_used"],
        "tokens_max": status["tokens_max"],
        "message": status["message"],
        "action_required": False,
        "action": None,
        "prompt": None,
    }

    if status["level"] == "normal":
        return result

    if agent_mode == "background":
        # Background agents auto-handoff at critical
        if should_auto_handoff(status["level"], agent_mode):
            result["action_required"] = True
            result["action"] = "auto_handoff"
            result["prompt"] = None  # No user prompt for background
    else:
        # Foreground agents prompt user
        result["action_required"] = True
        if status["level"] == "warning":
            result["action"] = "prompt_user"
            result["prompt"] = {
                "title": f"Context at {status['percentage']:.1f}%",
                "message": format_context_warning(status, agent_info["agent_type"]),
                "options": [
                    {
                        "key": "A",
                        "label": "Terminate and handoff",
                        "action": "terminate",
                    },
                    {"key": "B", "label": "Continue working", "action": "continue"},
                ],
                "default": "B",
                "severity": "warning",
            }
        else:  # critical
            result["action"] = "require_decision"
            result["prompt"] = {
                "title": f"Context at {status['percentage']:.1f}% - Decision Required",
                "message": format_context_warning(status, agent_info["agent_type"]),
                "options": [
                    {
                        "key": "A",
                        "label": "Terminate and handoff (Recommended)",
                        "action": "terminate",
                    },
                    {
                        "key": "B",
                        "label": "Force continue (may hit limits)",
                        "action": "force_continue",
                    },
                ],
                "default": "A",
                "severity": "critical",
            }

    return result


def run_quick_lint(file_path: str) -> dict:
    """
    Run quick lint check on the file.

    Returns all issues split into critical (blocks execution) and style
    (formatting, conventions). Only critical issues should be surfaced
    mid-work — style issues are handled by the quality gate (gate.sh)
    at the end of each implementation phase.
    """
    # Critical rules: code won't parse or will fail at runtime
    CRITICAL_RULES = {
        "E999",  # syntax error
        "F821",  # undefined name
        "F811",  # redefined unused name
        "F401",  # imported but unused (only critical if it shadows a needed import)
    }

    result = {
        "ran": False,
        "passed": True,
        "critical": [],
        "style": [],
        "total_issues": 0,
    }

    # Only lint Python files
    if not file_path.endswith(".py"):
        return result

    result["ran"] = True

    # Run ruff check (fast)
    try:
        proc = subprocess.run(
            ["ruff", "check", file_path, "--output-format=json"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if proc.returncode != 0 and proc.stdout:
            issues = json.loads(proc.stdout)
            result["total_issues"] = len(issues)
            result["passed"] = len(issues) == 0

            for i in issues:
                formatted = f"{i['code']}: {i['message']} (line {i['location']['row']})"
                if i["code"] in CRITICAL_RULES:
                    result["critical"].append(formatted)
                else:
                    result["style"].append(formatted)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass

    return result


def _load_sent_warnings(session_id: str = "default") -> set[str]:
    """Load which context warnings have already been sent this session."""
    tracking_file = Path(f".claude/temp/sent-context-warnings-{session_id}.json")
    if tracking_file.exists():
        try:
            with open(tracking_file) as f:
                data = json.load(f)
                return set(data.get("sent", []))
        except (json.JSONDecodeError, KeyError):
            pass
    return set()


def _mark_warning_sent(level: str, session_id: str = "default"):
    """Record that a context warning was sent, so it won't repeat."""
    tracking_file = Path(f".claude/temp/sent-context-warnings-{session_id}.json")
    tracking_file.parent.mkdir(parents=True, exist_ok=True)

    sent = _load_sent_warnings(session_id)
    sent.add(level)

    with open(tracking_file, "w") as f:
        json.dump({"sent": list(sent), "updated": datetime.now().isoformat()}, f)


def load_recent_agent_writes(session_id: str = "default") -> list[str]:
    """Load list of files recently written by this agent."""
    tracking_file = Path(f".claude/temp/recent-agent-writes-{session_id}.json")
    if tracking_file.exists():
        try:
            with open(tracking_file) as f:
                data = json.load(f)
                return data.get("files", [])
        except (json.JSONDecodeError, KeyError):
            pass
    return []


def track_agent_write(file_path: str, session_id: str = "default"):
    """Track that agent wrote to this file."""
    tracking_file = Path(f".claude/temp/recent-agent-writes-{session_id}.json")
    tracking_file.parent.mkdir(parents=True, exist_ok=True)

    files = load_recent_agent_writes(session_id)
    if file_path not in files:
        files.append(file_path)

    # Keep only last 50 files
    files = files[-50:]

    with open(tracking_file, "w") as f:
        json.dump({"files": files, "updated": datetime.now().isoformat()}, f)


def find_active_session_log() -> str | None:
    """Find the most recent active session log."""
    logs_dir = Path(".claude/logs")
    if not logs_dir.exists():
        return None

    # Find all session logs
    log_files = list(logs_dir.glob("*-chunk-*-log.json"))
    if not log_files:
        return None

    # Sort by modification time, get most recent
    log_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    # Check if it's still in_progress
    for log_file in log_files:
        try:
            with open(log_file) as f:
                data = json.load(f)
            if data.get("status") == "in_progress":
                return str(log_file)
        except (json.JSONDecodeError, KeyError):
            pass

    return None


def get_next_signal_id(session_log: dict) -> str:
    """Generate next signal ID."""
    signals = session_log.get("learning_signals", [])
    if signals:
        last_num = max(int(s["id"].split("-")[1]) for s in signals)
        return f"sig-{last_num + 1:03d}"
    return "sig-001"


def add_learning_signal(
    session_log_path: str, signal_type: str, context: dict, severity: str = "medium"
) -> bool:
    """Add a learning signal to the session log."""
    try:
        with open(session_log_path) as f:
            session_log = json.load(f)

        if "learning_signals" not in session_log:
            session_log["learning_signals"] = []

        signal = {
            "id": get_next_signal_id(session_log),
            "type": signal_type,
            "timestamp": datetime.now().isoformat(),
            "severity": severity,
            "context": context,
            "extracted": False,
        }

        session_log["learning_signals"].append(signal)

        if "meta" in session_log:
            session_log["meta"]["last_updated"] = datetime.now().isoformat()

        with open(session_log_path, "w") as f:
            json.dump(session_log, f, indent=2)

        return True
    except (OSError, json.JSONDecodeError, KeyError):
        return False


def detect_and_record_user_correction(
    file_path: str, tool_user: str, recent_agent_writes: list[str], tool_input: dict
) -> dict | None:
    """Detect if this is a user correcting agent code, and record it."""
    # Only care about human edits
    if tool_user != "human":
        return None

    # Only if agent recently wrote this file
    if file_path not in recent_agent_writes:
        return None

    # This is a user correction
    correction = {
        "type": "user_correction",
        "file": file_path,
        "timestamp": datetime.now().isoformat(),
    }

    # Try to capture what changed
    if "old_str" in tool_input and "new_str" in tool_input:
        correction["original_code"] = tool_input.get("old_str", "")[:200]
        correction["corrected_code"] = tool_input.get("new_str", "")[:200]

    # Record to session log
    session_log_path = find_active_session_log()
    if session_log_path:
        add_learning_signal(
            session_log_path,
            "user_correction",
            {
                "file_path": file_path,
                "original_code": correction.get("original_code", ""),
                "corrected_code": correction.get("corrected_code", ""),
            },
            severity="high",  # User corrections are high-value learning
        )
        correction["recorded_to"] = session_log_path

    return correction


def main():
    """Main hook handler."""
    hook_input = get_hook_input()

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    tool_user = hook_input.get("user", "agent")  # "agent" or "human"

    # Extract file path from tool input
    file_path = tool_input.get("path") or tool_input.get("file_path") or ""

    # Get agent info for context checking
    agent_info = get_agent_info(hook_input)
    session_id = hook_input.get("session_id", "default")

    output = {
        "lint": None,
        "correction_detected": None,
        "learning_signal_added": False,
        "context_status": None,
        "feedback": [],
    }

    # Check context thresholds (runs for all tool calls)
    # Each threshold level fires feedback ONCE — repeated warnings waste
    # context tokens and actively worsen the pressure they're warning about.
    context_result = check_context_thresholds(agent_info, session_id)
    if context_result:
        output["context_status"] = context_result

        # Track which warnings have already been sent this session
        sent_warnings = _load_sent_warnings(session_id)

        if context_result["level"] == "warning" and "warning" not in sent_warnings:
            output["feedback"].append(
                f"⚠️ Context at {context_result['percentage']:.1f}% - good stopping point available"
            )
            _mark_warning_sent("warning", session_id)
            emit_context_pressure(
                context_result["percentage"], "warning", agent_info["agent_id"]
            )
        elif context_result["level"] == "critical" and "critical" not in sent_warnings:
            output["feedback"].append(
                f"🔴 Context at {context_result['percentage']:.1f}% - handoff recommended"
            )
            _mark_warning_sent("critical", session_id)
            emit_context_pressure(
                context_result["percentage"], "critical", agent_info["agent_id"]
            )

            # For background agents at critical, signal auto-handoff (once)
            if context_result.get("action") == "auto_handoff":
                output["feedback"].append(
                    "[Background agent: initiating auto-handoff protocol]"
                )

    # Skip file-specific checks if no file path
    if not file_path:
        print(json.dumps(output))
        return

    # Track agent writes
    if tool_user == "agent":
        track_agent_write(file_path, session_id)

    # Run quick lint (only for write operations)
    # Strategy: only surface critical issues (syntax errors, undefined names)
    # that block execution. Style issues (line length, whitespace, formatting)
    # are handled in batch by the quality gate (gate.sh) at the end of each
    # implementation phase. This prevents context churn from repeated
    # lint-fix-rewrite cycles on cosmetic issues.
    if tool_name in ["Write", "Edit", "str_replace"]:
        lint_result = run_quick_lint(file_path)
        output["lint"] = lint_result

        if lint_result["ran"] and lint_result["critical"]:
            output["feedback"].append(
                f"Critical lint error in {file_path} (code won't run):"
            )
            output["feedback"].extend([f"  - {i}" for i in lint_result["critical"]])
        # Style issues: tracked silently in output["lint"] but NOT
        # injected into feedback. The quality gate catches them later.

        # Observability: log file writes under .claude/
        emit_file_written(file_path, agent_info["agent_id"])

    # Observability: detect skill loads (Read of .claude/skills/*/SKILL.md)
    if tool_name == "Read":
        emit_skill_loaded(file_path, agent_info["agent_id"])

    # Detect user corrections
    if tool_user == "human":
        recent_writes = load_recent_agent_writes(session_id)
        correction = detect_and_record_user_correction(
            file_path, tool_user, recent_writes, tool_input
        )
        if correction:
            output["correction_detected"] = correction
            output["learning_signal_added"] = "recorded_to" in correction
            output["feedback"].append(f"[Learning signal: User corrected {file_path}]")

    print(json.dumps(output))


if __name__ == "__main__":
    main()
