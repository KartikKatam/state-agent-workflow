"""Agent state model — tracks individual agent identity, lifecycle, and resource usage.

Written to: ~/.claude/state/agents/{agent-id}.json
Written by: SessionStart hook (creation), PostToolUse hook (updates)
Read by: orchestrator (fleet monitoring), agent itself (self-awareness), PTC (selective extraction)

Design doc ref: "Agent Identity & State" section, lines 479-509.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ._constants import AGENT_ID_PATTERN


AgentRole = Literal[
    "orchestrator",
    "strategist",
    "explorer",
    "researcher",
    "coder",
    "tester",
    "auditor",
    "generalist",
]

AgentModel = Literal["opus-4-6", "sonnet-4-6", "haiku-4-5"]

AgentStatus = Literal["active", "idle", "draining", "terminated"]


class PendingAnnotation(BaseModel):
    """A critical annotation awaiting acknowledgment via Think tool."""

    model_config = ConfigDict(extra="forbid")

    annotation_id: str
    message: str


class AgentState(BaseModel):
    """Per-agent state file. Created at spawn, updated by hooks, read by orchestrator and PTC."""

    model_config = ConfigDict(extra="forbid")

    # --- Required at spawn ---
    id: str = Field(
        description="Agent identifier: {role}-{phase}-{task}-{short_uuid}",
        pattern=AGENT_ID_PATTERN,
        examples=[
            "coder-p1-t3-a7f2",
            "auditor-p1-review-b3e1",
            "explorer-p1-init-c4d9",
        ],
    )
    role: AgentRole
    model: AgentModel
    spawned_at: datetime
    status: AgentStatus
    current_state: str = Field(
        description="Current state in the agent's state machine (e.g., TASK_CLAIMED, SYNTHESIS)",
    )

    # --- Populated progressively ---
    phase: str | None = Field(default=None, description="Workflow phase (e.g., 'p1')")
    task: str | None = Field(default=None, description="Task ID (e.g., 't3')")
    context_usage_pct: float = Field(
        default=0,
        ge=0,
        le=100,
        description="Context window usage, updated by PostToolUse hook",
    )
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    worktree: str | None = Field(
        default=None, description="Coder worktree path, null for non-coders"
    )
    base_branch: str | None = Field(
        default=None,
        description="Configurable base branch (not hardcoded to main)",
    )
    parent_agent_id: str | None = Field(
        default=None, description="ID of the agent that spawned this one"
    )
    completed_subagents: list[dict] = Field(
        default_factory=list,
        description="Sub-agents completed under this agent, managed by SubagentStop hook (capped at 20)",
    )
    pending_critical_annotation: PendingAnnotation | None = None
    pending_handoff: bool = False
    last_think_chosen: str | None = Field(
        default=None,
        description="CHOSEN value from last Think tool output (used for decision guard routing)",
    )
    last_think_state: str | None = Field(
        default=None,
        description="State the agent was in when last think was acknowledged "
        "(prevents stale CHOSEN from routing in wrong state)",
    )
    pending_validation_error: dict | None = Field(
        default=None,
        description="Blocks mutations until validation error is fixed "
        "(set by post-action validators, cleared when re-validation passes)",
    )
    inbox_blocked: dict | None = Field(
        default=None,
        description="Set when a blocking message awaits ack. "
        "Format: {blocked_by_message_id: str, from_agent: str}",
    )
    team_id: str | None = Field(
        default=None,
        description="Team this agent belongs to",
    )
