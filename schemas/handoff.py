"""Handoff model — structured context transfer for agent replacement.

Written to: $PROJECT_DIR/.claude/handoffs/{agent-id}.json
Written by: any agent before termination (PreCompact hook or voluntary)
Read by: orchestrator (spawns replacement), replacement agent (loads context)

Any agent role can produce a handoff — not just coders. Handoff contains
everything the replacement needs to continue without re-discovering context.

Design doc ref: "Handoff Protocol" section, lines 214-218.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ._constants import AGENT_ID_PATTERN, HandoffReason, TracingMixin
from .agent_state import AgentModel, AgentRole


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class HandoffDecision(BaseModel):
    """Key decision made by the original agent. Prevents replacement from re-deciding."""

    model_config = ConfigDict(extra="forbid")

    decision: str
    reason: str
    source: Literal["user_preference", "plan", "codebase_evidence", "judgment"] | None = None
    locked: bool = Field(
        default=False,
        description="True if decision is non-negotiable (user_preference or plan source)",
    )
    state_when_made: str | None = None  # state machine state when decision was made


class TestSummary(BaseModel):
    """Compact test results snapshot for handoff context."""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(default=0, ge=0)
    failing_tests: list[str] | None = None  # names of failing tests
    last_run: datetime | None = None

    @model_validator(mode="after")
    def _validate_counts(self) -> TestSummary:
        if self.passed + self.failed + self.skipped > self.total:
            msg = f"passed ({self.passed}) + failed ({self.failed}) + skipped ({self.skipped}) exceeds total ({self.total})"
            raise ValueError(msg)
        return self


class HandoffFileEntry(BaseModel):
    """File modified during the session — includes state description for successor context."""

    model_config = ConfigDict(extra="forbid")

    path: str
    state: str = Field(
        description="Current state of the file — what was done, what remains",
        examples=[
            "BatchProcessor with select_batch() implemented and tested. apply_filters() is a stub.",
        ],
    )


class UserPreferenceEntry(BaseModel):
    """User preference with context of when/where it was expressed."""

    model_config = ConfigDict(extra="forbid")

    preference: str
    context: str | None = Field(
        default=None,
        description="When/where the user expressed this preference",
        examples=["User corrected nested if/else in select_batch during chunk-01"],
    )


# ---------------------------------------------------------------------------
# Main model
# ---------------------------------------------------------------------------


class Handoff(TracingMixin):
    """Structured handoff for agent replacement. Contains everything the replacement
    agent needs to continue work without re-discovering context.
    """

    model_config = ConfigDict(extra="forbid")

    # --- Identity ---
    agent_id: str = Field(
        description="ID of the agent being replaced",
        pattern=AGENT_ID_PATTERN,
    )
    agent_role: AgentRole
    agent_model: AgentModel

    timestamp: datetime  # when handoff was written
    reason: HandoffReason

    # --- What was being done ---
    task_id: str | None = None  # null for explorer/researcher on ad-hoc queries
    feature: str | None = None
    workflow_phase: str | None = None  # e.g., "p1"
    current_state: str = Field(
        description="State machine state at the moment of handoff",
    )
    resume_instructions: str = Field(
        description="1-line instruction for where replacement should pick up",
        examples=[
            "4/7 tests passing, implement error handler next",
            "Synthesis complete, write context packet",
            "Phase breakdown done, start task detailing for chunk-03",
        ],
    )

    # --- Narrative context for replacement ---
    work_summary: str = Field(
        description="Free-form narrative of what was accomplished, approaches tried, "
        "and blockers hit. Gives replacement agent the full picture.",
        examples=[
            "Implemented batch selection with async generators. Unit tests for empty input, "
            "single item, and overflow cases all pass. Blocked on type mismatch between "
            "BatchProcessor.process_batch() return type (Generator) and the plan's expected "
            "List[Result]. Need to resolve whether to wrap the generator or change the plan.",
        ],
    )
    current_approach: str | None = Field(
        default=None,
        description="What strategy the agent was following and why",
        examples=[
            "Using async generators to match existing BatchProcessor pattern in producer/deps.py"
        ],
    )
    failed_approaches: list[str] = Field(
        default_factory=list,
        description="Approaches that were tried and didn't work — prevents replacement from repeating mistakes",
        examples=[
            [
                "Tried sync wrapper around generator — caused type errors with downstream consumers",
                "Attempted monkeypatching BatchProcessor.process_batch return type — broke 3 other tests",
            ],
        ],
    )

    # --- Structured context for replacement ---
    key_decisions: list[HandoffDecision] = Field(default_factory=list)
    files_modified: list[HandoffFileEntry] = Field(
        default_factory=list,
        description="Files modified with state descriptions — successor knows what exists without reading each file",
    )
    key_files_read: list[str] = Field(
        default_factory=list,
        description="Files the agent read that were important — replacement should load these",
    )
    test_results: TestSummary | None = None
    unresolved_questions: list[str] = Field(
        default_factory=list,
        description="Questions that need resolving — replacement should address or escalate",
    )
    user_preferences: list[UserPreferenceEntry] = Field(
        default_factory=list,
        description="User preferences with context of when they were expressed",
    )

    # --- Pointers ---
    session_log_path: str | None = Field(
        default=None,
        description="Path to session log for full history",
    )
    worktree_path: str | None = Field(
        default=None,
        description="Coder worktree path to pick up (null for non-coders)",
    )
    base_branch: str | None = Field(
        default=None,
        description="Base branch for coder worktree",
    )

    # --- Resource usage at handoff ---
    context_usage_pct: float = Field(default=0, ge=0, le=100)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_context_limit_has_usage(self) -> Handoff:
        if self.reason == "context_limit" and self.context_usage_pct <= 0:
            msg = "context_usage_pct must be > 0 when reason is 'context_limit'"
            raise ValueError(msg)
        return self
