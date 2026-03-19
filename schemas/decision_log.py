"""Decision log entry schema.

Each line in ~/.claude/logs/decisions/{agent-id}.jsonl is one DecisionLogEntry.
Written by PostToolUse hook on Think tool — pure Python, zero tokens.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ConfigDict

from ._constants import TracingMixin

from .agent_state import AgentRole


class DecisionLogEntry(TracingMixin):
    """A single Think tool decision event.

    Captures the structured THOUGHT/CONSIDERED/CHOSEN output from mandatory
    and discretionary think points. Used for observability and audit trails.
    """

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    agent_id: str
    agent_role: AgentRole | None = None
    current_state: str | None = None  # agent's state when decision was made
    decision_point: str  # what triggered the think, e.g. "phase_transition"
    thought: str  # THOUGHT label content
    considered: str  # CONSIDERED label content
    chosen: str  # CHOSEN label content
    mandatory: bool = False  # whether this was a mandatory think point
    metadata: dict[str, Any] | None = None
