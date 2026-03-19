#!/usr/bin/env python3
"""
Context Monitor Utility

Monitors context window usage and determines when to trigger handoff protocols.
Used by both foreground and background agent hooks.

Thresholds:
- 50%: Warning level - offer early termination (foreground only)
- 70%: Critical level - require termination decision (foreground) or auto-handoff (background)
"""

import json
import os
from datetime import datetime
from typing import Literal, TypedDict


class ContextStatus(TypedDict):
    percentage: float
    tokens_used: int
    tokens_max: int
    level: Literal["normal", "warning", "critical"]
    should_prompt: bool
    message: str


# Default context limits by model
MODEL_CONTEXT_LIMITS = {
    "haiku": 200_000,
    "sonnet": 200_000,
    "opus": 200_000,
}

# Thresholds
WARNING_THRESHOLD = 0.50  # 50%
CRITICAL_THRESHOLD = 0.70  # 70%


def check_context_usage(
    tokens_used: int, tokens_max: int | None = None, model: str = "sonnet"
) -> ContextStatus:
    """
    Check current context usage and return status.

    Args:
        tokens_used: Current token count in context
        tokens_max: Maximum context size (defaults to model limit)
        model: Model name for default limit lookup

    Returns:
        ContextStatus with percentage, level, and recommended action
    """
    if tokens_max is None:
        tokens_max = MODEL_CONTEXT_LIMITS.get(model, 200_000)

    percentage = tokens_used / tokens_max

    if percentage >= CRITICAL_THRESHOLD:
        level = "critical"
        should_prompt = True
        message = (
            f"Context at {percentage:.1%}. Approaching limit - handoff recommended."
        )
    elif percentage >= WARNING_THRESHOLD:
        level = "warning"
        should_prompt = True
        message = f"Context at {percentage:.1%}. Good stopping point available."
    else:
        level = "normal"
        should_prompt = False
        message = f"Context at {percentage:.1%}. Proceeding normally."

    return ContextStatus(
        percentage=percentage * 100,  # Return as percentage (0-100)
        tokens_used=tokens_used,
        tokens_max=tokens_max,
        level=level,
        should_prompt=should_prompt,
        message=message,
    )


def should_prompt_termination(
    level: Literal["normal", "warning", "critical"],
    agent_mode: Literal["foreground", "background"],
) -> bool:
    """
    Determine if user should be prompted for termination decision.

    Args:
        level: Current context level (normal/warning/critical)
        agent_mode: Whether agent is foreground (interactive) or background (autonomous)

    Returns:
        True if user should be prompted
    """
    if agent_mode == "foreground":
        # Foreground agents prompt at warning AND critical
        return level in ["warning", "critical"]
    # Background agents only auto-handoff at critical (no prompting)
    return False  # They don't prompt, they just handoff


def get_termination_prompt(
    level: Literal["warning", "critical"],
    agent_type: str,
    current_phase: str,
    percentage: float,
) -> dict:
    """
    Generate the user prompt for termination decision.

    Args:
        level: Current context level
        agent_type: Type of agent (chunk-coder, plan-architect, etc.)
        current_phase: Current phase of work
        percentage: Context usage percentage

    Returns:
        Dict with prompt message and options
    """
    if level == "warning":
        return {
            "title": f"Context at {percentage:.1f}%",
            "message": f"""
The {agent_type} has used {percentage:.1f}% of available context.

Current phase: {current_phase}

This is a good opportunity to save state and continue with a fresh agent.
You can also continue working - you'll be prompted again at 70%.
            """.strip(),
            "options": [
                {
                    "key": "A",
                    "label": "Terminate now and handoff",
                    "action": "terminate",
                },
                {"key": "B", "label": "Continue working", "action": "continue"},
            ],
            "default": "B",
        }
    else:  # critical
        return {
            "title": f"Context at {percentage:.1f}% - Decision Required",
            "message": f"""
The {agent_type} has used {percentage:.1f}% of available context.

Current phase: {current_phase}

Continuing may result in truncated responses or context overflow.
Recommended: Terminate and handoff to a fresh agent.
            """.strip(),
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
        }


def format_context_warning(status: ContextStatus, agent_type: str) -> str:
    """
    Format a context warning message for display.

    Args:
        status: Context status from check_context_usage
        agent_type: Type of agent

    Returns:
        Formatted warning string
    """
    icon = "⚠️" if status["level"] == "warning" else "🔴"

    return f"""
{icon} CONTEXT STATUS: {status["level"].upper()}

Agent: {agent_type}
Usage: {status["tokens_used"]:,} / {status["tokens_max"]:,} tokens ({status["percentage"]:.1f}%)

{status["message"]}

To terminate at any time, say: "terminate agent"
    """.strip()


def should_auto_handoff(
    level: Literal["normal", "warning", "critical"],
    agent_mode: Literal["foreground", "background"],
) -> bool:
    """
    Determine if background agent should automatically initiate handoff.

    Args:
        level: Current context level
        agent_mode: Agent mode

    Returns:
        True if agent should auto-handoff without user input
    """
    if agent_mode == "background" and level == "critical":
        return True
    return False


def log_context_check(
    agent_id: str, status: ContextStatus, log_dir: str = ".claude/logs"
) -> None:
    """
    Log context check for debugging and analysis.

    Args:
        agent_id: Unique agent identifier
        status: Context status
        log_dir: Directory for logs
    """
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "context-checks.jsonl")

    entry = {
        "timestamp": datetime.now().isoformat(),
        "agent_id": agent_id,
        "percentage": status["percentage"],
        "level": status["level"],
        "tokens_used": status["tokens_used"],
        "tokens_max": status["tokens_max"],
    }

    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    # Test the utility
    import sys

    if len(sys.argv) >= 2:
        tokens_used = int(sys.argv[1])
        tokens_max = int(sys.argv[2]) if len(sys.argv) >= 3 else 200_000

        status = check_context_usage(tokens_used, tokens_max)
        print(json.dumps(status, indent=2))
    else:
        # Demo mode
        print("Context Monitor Utility")
        print("=" * 40)

        for pct in [0.3, 0.5, 0.55, 0.7, 0.85]:
            tokens = int(200_000 * pct)
            status = check_context_usage(tokens, 200_000)
            print(f"\n{pct:.0%} usage:")
            print(f"  Level: {status['level']}")
            print(f"  Should prompt: {status['should_prompt']}")
            print(f"  Message: {status['message']}")
