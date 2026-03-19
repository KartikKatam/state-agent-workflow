"""Inter-agent message protocol models — V2 rework.

Defines the structured message envelope and typed payloads for all agent-to-agent
communication. Messages are JSON, sent via SendMessage, logged to
~/.claude/logs/message-bus.jsonl by PostToolUse hook.

Key V2 changes from V1:
- Direct peer-to-peer messaging (orchestrator is NOT a relay)
- Typed payloads per message type (discriminated union)
- peer_notify type routes through annotation channel for immediate delivery
- context_query/context_response for direct coder ↔ explorer exchanges
- handoff/shutdown for agent lifecycle

Design doc ref: "Communication System" section, lines 257-277.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag

from ._constants import SUMMARY_MAX_LENGTH, HandoffReason, TracingMixin


# ---------------------------------------------------------------------------
# Payload models — one per message type
# ---------------------------------------------------------------------------


class TaskAssignPayload(BaseModel):
    """Orchestrator → agent. Assigns work with inputs and expected output."""

    model_config = ConfigDict(extra="forbid")

    task_type: str = Field(
        description="Category of work: explore, research, implement, plan, review, test, audit, log",
    )
    instructions: str  # natural language task instructions
    inputs: TaskAssignInputs | None = None
    output: TaskAssignOutput | None = None


class TaskAssignInputs(BaseModel):
    """Input context for an assigned task."""

    model_config = ConfigDict(extra="forbid")

    files_to_read: list[str] | None = None  # file paths for context
    session_log: str | None = None  # path to session log for resumption
    resume_instructions: str | None = None  # 1-line summary of where to pick up
    exploration_queries: list[str] | None = None  # queries for explorer
    research_queries: list[str] | None = None  # queries for researcher


class TaskAssignOutput(BaseModel):
    """Expected output location for an assigned task."""

    model_config = ConfigDict(extra="forbid")

    write_to: str | None = None  # expected output file path


class TaskCompletePayload(BaseModel):
    """Agent → orchestrator. Reports task outcome."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "partial", "failed", "blocked"]
    summary: str = Field(
        max_length=SUMMARY_MAX_LENGTH
    )  # lead uses for routing decisions
    output_files: list[str] | None = None
    context_pressure: bool = False  # true if agent is low on context


class InfoRequestPayload(BaseModel):
    """Any → any. Request information from another agent.

    Used for coder → explorer queries, strategist → researcher lookups,
    auditor → coder fix requests, etc.
    """

    model_config = ConfigDict(extra="forbid")

    request_type: Literal[
        "documentation", "codebase", "research", "fix", "clarification"
    ]
    query: str
    priority: Literal["blocking", "normal"]  # blocking = wait, normal = continue
    what_we_need: str | None = None  # more specific than query
    why_we_need_it: str | None = None  # helps responder understand nuance
    context_narrative: str | None = Field(
        default=None,
        description="Free-form explanation of the subtle problem. Use when structured "
        "fields can't capture the nuance — e.g., 'the BatchProcessor uses a generator "
        "pattern but type annotations suggest sync — need to know if intentional before "
        "choosing async vs sync for my extension.'",
    )
    relevant_context: InfoRequestContext | None = None
    existing_checked: list[str] | None = None  # paths already checked, prevent dup work


class InfoRequestContext(BaseModel):
    """Structured context to guide an info request."""

    model_config = ConfigDict(extra="forbid")

    design_doc_section: str | None = None
    existing_knowledge: str | None = None
    related_files: list[str] | None = None
    specific_questions: list[str] | None = None  # one sub-agent per question


class InfoReadyPayload(BaseModel):
    """Any → any. Delivers requested information."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["fulfilled", "partial", "not_found"]
    file_paths: list[str] | None = None  # files containing the info
    summary: str | None = Field(default=None, max_length=SUMMARY_MAX_LENGTH)


class StatusUpdatePayload(BaseModel):
    """Agent → orchestrator. Progress report and context pressure signals."""

    model_config = ConfigDict(extra="forbid")

    phase: str  # current work phase
    progress: str  # e.g., "4/7 tests passing"
    context_pressure: bool = False  # true if context >= 70%
    needs_replacement: bool = False  # true if agent should be replaced
    session_log: str | None = None  # path to state file for replacement
    resume_instructions: str | None = None  # 1-line for replacement agent


class ContextQueryPayload(BaseModel):
    """Any → explorer/researcher. Direct peer request for fresh context.

    Coders use this to request task-specific context from explorer/researcher
    without routing through orchestrator.
    """

    model_config = ConfigDict(extra="forbid")

    query: str
    scope: Literal["file", "directory", "module", "cross_module", "external"]
    requesting_task: str | None = None  # task ID for context
    file_hints: list[str] | None = None  # starting points for search
    specific_questions: list[str] | None = (
        None  # compound queries — one sub-agent per question
    )
    context_narrative: str | None = Field(
        default=None,
        description="Free-form explanation of what you need to understand and why. "
        "Use for complex questions like 'how do planner state transitions interact "
        "with worktree merge ordering, specifically the race condition tiebreaker "
        "and whether it accounts for priority inversion.'",
    )


class ContextResponsePayload(BaseModel):
    """Explorer/researcher → any. Delivers requested context."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["fulfilled", "partial", "not_found"]
    file_paths: list[str] | None = (
        None  # context packet paths — answers live in these files
    )
    summary: str | None = Field(default=None, max_length=SUMMARY_MAX_LENGTH)
    confidence: float | None = Field(default=None, ge=0, le=1)


class HandoffPayload(BaseModel):
    """Dying agent → orchestrator. Transfers state before termination."""

    model_config = ConfigDict(extra="forbid")

    reason: HandoffReason
    handoff_file: str  # path to handoff JSON
    resume_instructions: str  # 1-line for replacement agent
    files_modified: list[str] | None = None
    unresolved_questions: list[str] | None = None


class ShutdownPayload(BaseModel):
    """Orchestrator → agent. Graceful termination request."""

    model_config = ConfigDict(extra="forbid")

    reason: str
    graceful: bool = True  # false = immediate termination expected


class PeerNotifyPayload(BaseModel):
    """Any → any. Fire-and-forget alert, no response expected.

    Routed through annotation channel by PostToolUse hook — target agent
    sees it on their next tool call via additionalContext, not at turn boundary.
    """

    model_config = ConfigDict(extra="forbid")

    alert: str  # the notification content
    urgency: Literal["critical", "normal", "fyi"] = "normal"
    related_files: list[str] | None = None  # files relevant to the alert


class BugReportPayload(BaseModel):
    """Tester → coder(s). Structured failure report starting at remediation cycle 2.

    Replaces the freeform info_request used in cycle 1 with structured fields
    that help coders converge faster on fixes.
    """

    model_config = ConfigDict(extra="forbid")

    scenario_label: str = Field(
        description="Behavioral label, e.g. 'large_batch_timeout'"
    )
    expected_behavior: str
    actual_behavior: str
    traceback_files: list[str] = Field(
        description="file:line chain from coder's own code (pytest traceback)",
    )
    pass_count: int = Field(ge=0)
    previous_pass_count: int | None = None
    total_scenarios: int = Field(ge=1)
    remediation_cycle: int = Field(
        ge=2, description="Structured reports start at cycle 2"
    )
    requires_peer_coordination: bool = Field(
        default=False,
        description="True on cycle 3+ — coders must coordinate fixes",
    )


# ---------------------------------------------------------------------------
# Per-type message wrappers (for discriminated union)
# ---------------------------------------------------------------------------


class _MessageBase(TracingMixin):
    """Common fields for all message types."""

    model_config = ConfigDict(extra="forbid")

    from_agent: str  # sender name
    to_agent: str  # recipient name
    timestamp: datetime
    metadata: dict[str, Any] | None = None


class TaskAssignMessage(_MessageBase):
    type: Literal["task_assign"]
    payload: TaskAssignPayload


class TaskCompleteMessage(_MessageBase):
    type: Literal["task_complete"]
    payload: TaskCompletePayload


class InfoRequestMessage(_MessageBase):
    type: Literal["info_request"]
    payload: InfoRequestPayload


class InfoReadyMessage(_MessageBase):
    type: Literal["info_ready"]
    payload: InfoReadyPayload


class StatusUpdateMessage(_MessageBase):
    type: Literal["status_update"]
    payload: StatusUpdatePayload


class ContextQueryMessage(_MessageBase):
    type: Literal["context_query"]
    payload: ContextQueryPayload


class ContextResponseMessage(_MessageBase):
    type: Literal["context_response"]
    payload: ContextResponsePayload


class HandoffMessage(_MessageBase):
    type: Literal["handoff"]
    payload: HandoffPayload


class ShutdownMessage(_MessageBase):
    type: Literal["shutdown"]
    payload: ShutdownPayload


class PeerNotifyMessage(_MessageBase):
    type: Literal["peer_notify"]
    payload: PeerNotifyPayload


class BugReportMessage(_MessageBase):
    type: Literal["bug_report"]
    payload: BugReportPayload


# ---------------------------------------------------------------------------
# Discriminated union — validates the right payload for each type
# ---------------------------------------------------------------------------


def _get_message_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        return v.get("type", "")
    return getattr(v, "type", "")


AgentMessage = Annotated[
    Annotated[TaskAssignMessage, Tag("task_assign")]
    | Annotated[TaskCompleteMessage, Tag("task_complete")]
    | Annotated[InfoRequestMessage, Tag("info_request")]
    | Annotated[InfoReadyMessage, Tag("info_ready")]
    | Annotated[StatusUpdateMessage, Tag("status_update")]
    | Annotated[ContextQueryMessage, Tag("context_query")]
    | Annotated[ContextResponseMessage, Tag("context_response")]
    | Annotated[HandoffMessage, Tag("handoff")]
    | Annotated[ShutdownMessage, Tag("shutdown")]
    | Annotated[PeerNotifyMessage, Tag("peer_notify")]
    | Annotated[BugReportMessage, Tag("bug_report")],
    Discriminator(_get_message_discriminator),
]
"""Top-level message type. Use with:
    from pydantic import TypeAdapter
    adapter = TypeAdapter(AgentMessage)
    msg = adapter.validate_json(raw_line)
"""
