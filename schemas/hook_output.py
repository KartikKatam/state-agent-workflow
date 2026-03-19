"""Hook output model — standardized JSON structure that hooks print to stdout.

Claude Code reads this JSON to act on hook decisions. All custom workflow hooks
(PreToolUse, PostToolUse, SessionStart, PreCompact, Stop, SubagentStop) use this format.

Written to: stdout (by hook scripts)
Read by: Claude Code (permission decisions), event_logger (observability)

Design doc ref: "Permission Model" section, lines 287-299.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ._constants import TracingMixin


PermissionDecision = Literal["allow", "deny", "ask"]


class HookSpecificOutput(BaseModel):
    """Event-specific fields returned by the hook."""

    model_config = ConfigDict(extra="forbid")

    hook_event_name: str = Field(
        description="The hook event type that produced this output",
        examples=["PreToolUse", "PostToolUse", "SessionStart", "Stop"],
    )
    permission_decision: PermissionDecision | None = Field(
        default=None,
        description="Only relevant for PreToolUse hooks",
    )
    permission_decision_reason: str | None = Field(
        default=None,
        description="Human-readable reason for the permission decision",
    )
    additional_context: str | None = Field(
        default=None,
        description="Injected into agent context as guidance (e.g., 'Write tests first')",
    )


class HookOutput(TracingMixin):
    """Top-level hook output. Printed as JSON to stdout by hook scripts."""

    model_config = ConfigDict(extra="forbid")

    hook_event: str = Field(
        description="The hook lifecycle event",
        examples=[
            "PreToolUse",
            "PostToolUse",
            "SessionStart",
            "PreCompact",
            "Stop",
            "SubagentStop",
        ],
    )
    timestamp: datetime
    agent_id: str = Field(
        description="ID of the agent that triggered this hook",
        examples=["coder-p1-t3-a7f2"],
    )
    hook_specific_output: HookSpecificOutput = Field(alias="hookSpecificOutput")
    feedback: list[str] = Field(
        default_factory=list,
        description="Messages injected into agent context after this hook runs",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Additional structured data for logging and observability",
    )
