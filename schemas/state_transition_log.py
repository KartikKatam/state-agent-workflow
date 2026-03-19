"""State transition log entry schema.

Each line in ~/.claude/logs/state-transitions.jsonl is one StateTransitionEntry.
Written by workflow_state.py at every state transition — pure Python, zero tokens.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field

from ._constants import AGENT_ID_PATTERN, TracingMixin

from .agent_state import AgentRole


class StateTransitionEntry(TracingMixin):
    """A single state transition event.

    Captures who transitioned, from/to which states, what triggered it,
    and optionally which guard conditions were evaluated.
    """

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    agent_id: str = Field(
        ...,
        description="Agent identifier, e.g. 'coder-p1-t3-a7f2'",
        pattern=AGENT_ID_PATTERN,
    )
    agent_role: AgentRole
    from_state: str  # free-form — states vary per agent role
    to_state: str
    trigger: str  # what caused the transition, e.g. "task_claimed", "tests_passed"
    guard_results: dict[str, bool] | None = None  # guard name -> pass/fail
    duration_ms: int | None = Field(
        None,
        ge=0,
        description="Time spent in from_state (ms). Absent on first transition.",
    )
    metadata: dict[str, Any] | None = None
