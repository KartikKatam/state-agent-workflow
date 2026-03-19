#!/usr/bin/env python3
"""
Context Monitor Utility

Monitors context window usage and determines when to trigger handoff.
Reads context_usage_pct from the agent state file (populated by PostToolUse
hook from statusline metrics). Falls back to reading the statusline metrics
file directly if the agent state file doesn't have the percentage yet.

Thresholds:
- 65%: Warning — agent should wrap up current work, plan for handoff
- 90%: Critical — agent stops new work, writes handoff, terminates
- Last 10% is reserved for handoff state serialization
"""

import json
from datetime import datetime, timezone
from typing import Literal, TypedDict

from hooks.utils.error_logger import log_hook_error
from hooks.utils.state_helpers import (
    AGENT_STATE_DIR,
    LOGS_DIR,
    TEMP_DIR,
    read_json_safe,
)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

WARNING_THRESHOLD = 0.65  # 65% — start wrapping up
CRITICAL_THRESHOLD = 0.90  # 90% — stop, handoff now
HANDOFF_RESERVE_PCT = 0.10  # Last 10% reserved for handoff management


class ContextStatus(TypedDict):
    percentage: float  # 0-100 scale
    level: Literal["normal", "warning", "critical"]
    message: str


def check_context_usage(agent_id: str) -> ContextStatus:
    """
    Check current context usage for an agent.

    Reads context_usage_pct from:
    1. Agent state file (primary — populated by PostToolUse hook)
    2. Statusline metrics file (fallback — written by statusline.sh)

    Returns ContextStatus with percentage, level, and message.
    If neither source has data, returns normal status (safe default —
    PreCompact hook is the ultimate safety net).
    """
    pct: float | None = None

    # Primary: read from agent state file
    state = read_json_safe(AGENT_STATE_DIR / f"{agent_id}.json")
    if state:
        pct = state.get("context_usage_pct")

    # Fallback: read statusline metrics file directly
    if pct is None and state:
        session_id = state.get("session_id")
        if session_id:
            metrics = read_json_safe(TEMP_DIR / f"context-metrics-{session_id}.json")
            if metrics:
                pct = metrics.get("used_percentage")

    # Safe default
    if pct is None:
        pct = 0.0

    return _evaluate_threshold(pct)


def _evaluate_threshold(pct: float) -> ContextStatus:
    """Apply threshold logic to a context usage percentage (0-100 scale)."""
    ratio = pct / 100.0

    if ratio >= CRITICAL_THRESHOLD:
        return ContextStatus(
            percentage=pct,
            level="critical",
            message=(
                f"Context at {pct:.0f}%. Stop new work — "
                f"write handoff and terminate. "
                f"Last {HANDOFF_RESERVE_PCT:.0%} reserved for handoff."
            ),
        )
    elif ratio >= WARNING_THRESHOLD:
        return ContextStatus(
            percentage=pct,
            level="warning",
            message=(
                f"Context at {pct:.0f}%. Wrap up current work and plan for handoff."
            ),
        )
    else:
        return ContextStatus(
            percentage=pct,
            level="normal",
            message=f"Context at {pct:.0f}%. Proceeding normally.",
        )


def should_auto_handoff(
    level: Literal["normal", "warning", "critical"],
    agent_mode: Literal["foreground", "background"],
) -> bool:
    """
    Determine if an agent should automatically initiate handoff.

    Background agents auto-handoff at critical level (no user prompt).
    Foreground agents are warned via annotations but don't auto-handoff.
    """
    return agent_mode == "background" and level == "critical"


def format_context_warning(status: ContextStatus, agent_type: str) -> str:
    """
    Format a context warning message for display/annotation.

    Args:
        status: Context status from check_context_usage
        agent_type: Agent role (e.g. "coder", "explorer")

    Returns:
        Formatted warning string
    """
    icon = "!!" if status["level"] == "critical" else "!"

    return (
        f"[{icon} CONTEXT {status['level'].upper()}] "
        f"Agent: {agent_type} | "
        f"Usage: {status['percentage']:.0f}% | "
        f"{status['message']}"
    )


def log_context_check(agent_id: str, status: ContextStatus) -> None:
    """
    Log context check to ~/.claude/logs/context-checks.jsonl.

    Fire-and-forget — errors are silently caught.
    """
    try:
        log_file = LOGS_DIR / "context-checks.jsonl"
        log_file.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": agent_id,
            "percentage": status["percentage"],
            "level": status["level"],
        }

        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        log_hook_error("context_monitor", "log_context_check", e)
