#!/usr/bin/env python3
"""Think MCP — structured deliberation tool for the workflow daemon.

Single stateless tool. Agents call think(thought, chosen) to deliberate.
The daemon reads `chosen` from tool_input and maps it to state transitions.
Agents are blind to their state — identity is read from environment.

Decision logging with W3C trace context to ~/.claude/logs/decisions/.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Decision vocabulary — enum tree
# ---------------------------------------------------------------------------

class DecisionCategory(str, Enum):
    REVIEW = "review"
    INVESTIGATION = "investigation"
    RECOVERY = "recovery"
    EXPLORATION = "exploration"
    RELATEDNESS = "relatedness"
    STRATEGY = "strategy"
    SYNTHESIS = "synthesis"


DECISION_TREE: dict[DecisionCategory, set[str]] = {
    DecisionCategory.REVIEW: {"APPROVE", "CRITIQUE"},
    DecisionCategory.INVESTIGATION: {"FINALIZE", "REINVESTIGATE"},
    DecisionCategory.RECOVERY: {"RETRY", "HANDOFF", "ABORT"},
    DecisionCategory.EXPLORATION: {"REUSE", "EXPLORE", "DISPATCH", "CLARIFY"},
    DecisionCategory.RELATEDNESS: {"RELATED", "UNRELATED"},
    DecisionCategory.STRATEGY: {"FALLBACK"},
    DecisionCategory.SYNTHESIS: {"SYNTHESIZE", "PARTIAL_SYNTHESIS"},
}

ALL_VALID_CHOSEN: set[str] = set()
for _values in DECISION_TREE.values():
    ALL_VALID_CHOSEN |= _values

CHOSEN_TO_CATEGORY: dict[str, str] = {}
for _cat, _values in DECISION_TREE.items():
    for _v in _values:
        CHOSEN_TO_CATEGORY[_v] = _cat.value

# ---------------------------------------------------------------------------
# Trace context (lightweight — reads from env/file, never blocks)
# ---------------------------------------------------------------------------

_TRACE_DIR = Path.home() / ".claude" / "state" / "traces"


def _read_trace(agent_id: str) -> dict[str, str | None]:
    """Read W3C trace context from env vars or trace state file."""
    trace_id = os.environ.get("CLAUDE_TRACE_ID")
    span_id = os.environ.get("CLAUDE_SPAN_ID")
    parent_span_id = os.environ.get("CLAUDE_PARENT_SPAN_ID")

    if trace_id and span_id:
        return {
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": parent_span_id,
        }

    # Fallback: read from trace state file
    try:
        trace_file = _TRACE_DIR / f"{agent_id}.json"
        if trace_file.exists():
            data = json.loads(trace_file.read_text())
            return {
                "trace_id": data.get("trace_id"),
                "span_id": data.get("span_id"),
                "parent_span_id": data.get("parent_span_id"),
            }
    except Exception:
        pass

    return {"trace_id": None, "span_id": None, "parent_span_id": None}


def _generate_span_id() -> str:
    """Generate a 16-char hex span ID for this think call."""
    import secrets
    return secrets.token_hex(8)


# ---------------------------------------------------------------------------
# Decision logging
# ---------------------------------------------------------------------------

_DECISIONS_DIR = Path.home() / ".claude" / "logs" / "decisions"


def _log_decision(
    agent_id: str,
    chosen: str,
    thought: str,
    category: str | None,
) -> None:
    """Append structured decision entry to agent's JSONL log.

    Logs full thought content for post-hoc traceability. Each agent writes
    to its own file so concurrent agents never contend on the same path.
    """
    try:
        _DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
        trace = _read_trace(agent_id)
        think_span_id = _generate_span_id()

        entry: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_id": agent_id,
            "chosen": chosen or None,
            "thought": thought,
            "thought_length": len(thought),
            "decision_category": category,
            "think_span_id": think_span_id,
        }
        # Add trace context if available
        if trace.get("trace_id"):
            entry["trace_id"] = trace["trace_id"]
            entry["span_id"] = think_span_id
            entry["parent_span_id"] = trace.get("span_id")

        log_file = _DECISIONS_DIR / f"{agent_id}.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # Fire-and-forget — never block the agent


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

server = FastMCP(
    "think",
    instructions=(
        "Deliberation tool for structured reasoning. "
        "Write your full reasoning in `thought`, answering any numbered questions from the prompt. "
        "If the prompt asks you to choose a path, provide the value in `chosen`. "
        "Valid chosen values: "
        + ", ".join(sorted(ALL_VALID_CHOSEN))
        + ". Leave `chosen` empty for reflective deliberation with no decision required."
    ),
)


@server.tool(
    description=(
        "Deliberate before acting. Write reasoning in `thought`. "
        "If a decision is needed, set `chosen` to one of: "
        + ", ".join(sorted(ALL_VALID_CHOSEN))
        + ". Leave `chosen` empty when no decision is required."
    ),
)
def think(thought: str, chosen: str = "") -> str:
    """Process a deliberation. Validates chosen, logs decision, returns minimal confirmation."""
    agent_id = os.environ.get("CLAUDE_CODE_AGENT_NAME", "unknown")
    chosen = chosen.strip().upper()

    # Validate chosen against enum tree
    if chosen and chosen not in ALL_VALID_CHOSEN:
        return (
            f"Invalid CHOSEN value '{chosen}'. "
            f"Valid values: {', '.join(sorted(ALL_VALID_CHOSEN))}. "
            "Use one of these or leave chosen empty for no-decision deliberation."
        )

    # Determine category
    category = CHOSEN_TO_CATEGORY.get(chosen) if chosen else None

    # Log decision with trace context (full thought content for observability)
    _log_decision(agent_id, chosen, thought, category)

    # Minimal return — thought is already in tool_input context
    if chosen:
        return f"Recorded. CHOSEN={chosen}"
    return "Recorded."


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    server.run(transport="stdio")
