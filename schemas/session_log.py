"""Session log model — tracks per-task implementation progress.

Written to: $PROJECT_DIR/.claude/logs/{feature}-{task}-log.json
Written by: coder agent (state tracking), hooks (automatic updates)
Read by: orchestrator (monitoring), scribe (commit info + memory extraction),
         replacement coders (handoff resumption), stop hooks (completion validation)

Rework of V1 session-log.schema.json. Key V2 changes:
- Uses agent IDs (not session IDs)
- Granular coder state machine states
- Task-based (not chunk-based)
- Imports AgentRole from agent_state

Design doc ref: "Task-Coder-Auditor Flow" section, lines 373-388.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ._constants import AGENT_ID_PATTERN, TracingMixin
from .agent_state import AgentModel, AgentRole


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class StateEntry(BaseModel):
    """Timestamped state transition. Proves TDD red->green cycle was followed."""

    model_config = ConfigDict(extra="forbid")

    state: str  # coder state machine state
    timestamp: datetime
    details: str | None = Field(
        default=None,
        description="State-specific evidence. For TDD_RED: test failure count. "
        "For TDD_GREEN: all-pass confirmation.",
        examples=[
            "18 tests failed: 7 ModuleNotFoundError, 4 ImportError, 7 NameError",
            "18/18 tests passing",
            "5/5 invariants passing",
        ],
    )


class TestEntry(BaseModel):
    """Individual test result with failure history for learning extraction."""

    model_config = ConfigDict(extra="forbid")

    name: str  # test function name
    file: str  # test file path
    status: Literal["pass", "fail", "skip", "error"]
    category: Literal["unit", "integration", "e2e", "property"] | None = None
    failure_count: int = Field(
        default=0,
        ge=0,
        description="How many times this test failed before passing (learning signal)",
    )
    failure_messages: list[str] | None = None  # history for learning extraction


class InvariantEntry(BaseModel):
    """Status of a single invariant from the plan."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["pass", "fail", "skip", "not_run"]
    last_checked: datetime | None = None
    failure_message: str | None = None


class FileEntry(BaseModel):
    """File touched during implementation."""

    model_config = ConfigDict(extra="forbid")

    path: str  # relative from project root
    action: Literal["created", "modified", "deleted"]
    in_scope: bool = Field(
        default=True,
        description="Whether file was in plan's touched_files",
    )
    lines_changed: int | None = Field(default=None, ge=0)


class KeyFileEntry(BaseModel):
    """File read that was important for the work — enables replacement agent context loading."""

    model_config = ConfigDict(extra="forbid")

    path: str
    relevance: str | None = Field(
        default=None,
        description="Why this file matters for the current task",
        examples=["Contains BatchProcessor base class being extended"],
    )


class Decision(BaseModel):
    """Design/implementation decision. Prevents replacement agents from re-deciding differently."""

    model_config = ConfigDict(extra="forbid")

    decision: str
    reason: str
    source: Literal["user_preference", "plan", "codebase_evidence", "judgment"] | None = None
    locked: bool = Field(
        default=False,
        description="True if decision is non-negotiable (user_preference or plan source)",
    )
    timestamp: datetime | None = None


class LearningSignal(BaseModel):
    """Captured event for memory extraction by scribe."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^sig-\d{3}$")
    type: Literal[
        "test_fix_cycle",
        "scope_deviation",
        "pattern_discovered",
        "naming_choice",
        "error_recovery",
        "refactor_opportunity",
        "invariant_violation",
        "user_correction",
    ]
    context: dict[str, Any]  # type-specific context data
    timestamp: datetime | None = None
    extracted: bool = Field(
        default=False,
        description="Whether scribe has processed this signal",
    )


class Deviation(BaseModel):
    """Plan deviation documented for transparency."""

    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "scope_addition", "scope_removal", "approach_change", "dependency_change"
    ]
    description: str
    reason: str | None = None
    user_approved: bool = False
    timestamp: datetime | None = None


# ---------------------------------------------------------------------------
# Main model
# ---------------------------------------------------------------------------


class SessionLog(TracingMixin):
    """Per-task implementation session log. Tracks TDD progress, enables resumption,
    and captures learning signals for the coding memory system.
    """

    model_config = ConfigDict(extra="forbid")

    # --- Identity ---
    agent_id: str = Field(
        description="Coder agent ID",
        pattern=AGENT_ID_PATTERN,
    )
    agent_role: AgentRole = "coder"
    agent_model: AgentModel

    feature: str  # feature name (matches plan)
    task_id: str  # task from plan
    workflow_phase: str  # e.g., "p1"
    worktree_path: str | None = None  # coder's worktree

    # --- Timing ---
    started_at: datetime
    last_updated: datetime | None = None
    completed_at: datetime | None = None

    # --- Status ---
    status: Literal[
        "in_progress",
        "tests_written",
        "implementation_done",
        "verified",
        "approved",
        "committed",
        "blocked",
        "failed",
    ]
    current_state: str  # coder state machine state
    state_history: list[StateEntry] = Field(
        default_factory=list,
        description="Timestamped state transitions. REQUIRED for proving TDD cycle. "
        "Must include TDD_RED (tests fail) and TDD_GREEN (tests pass) entries.",
    )

    # --- TDD tracking ---
    tests_written: list[TestEntry] = Field(default_factory=list)
    invariants_status: dict[str, InvariantEntry] = Field(default_factory=dict)

    # --- Quality & files ---
    quality_gate: QualityGateSnapshot | None = None
    files_modified: list[FileEntry] = Field(default_factory=list)

    # --- Learning & decisions ---
    learning_signals: list[LearningSignal] = Field(default_factory=list)
    deviations: list[Deviation] = Field(default_factory=list)
    decisions_made: list[Decision] = Field(default_factory=list)
    user_preferences: list[str] = Field(
        default_factory=list,
        description="User preferences expressed during this session",
    )

    # --- Handoff support ---
    resume_point: str | None = Field(
        default=None,
        description="Human-readable description of where to resume",
    )
    resume_from_state: str | None = Field(
        default=None,
        description="State machine state to resume from on handoff",
    )
    resume_from_step: str | None = Field(
        default=None,
        description="Specific step within the state, e.g., '4/7 — test case for batch validation'",
    )
    pending_decisions: list[str] | None = None
    key_files_read: list[KeyFileEntry] | None = None
    handoff_count: int = Field(
        default=0,
        ge=0,
        description="Number of times this task's session has been handed off",
    )

    # --- Resource tracking ---
    context_usage_pct: float = Field(default=0, ge=0, le=100)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class QualityGateSnapshot(BaseModel):
    """Inline snapshot of the last quality gate run. For full detail, see QualityGateResult."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    timestamp: datetime
    format: Literal["pass", "fail", "not_run"] = "not_run"
    lint: Literal["pass", "fail", "not_run"] = "not_run"
    typecheck: Literal["pass", "fail", "not_run"] = "not_run"
    tests: Literal["pass", "fail", "not_run"] = "not_run"
    security: Literal["pass", "fail", "not_run"] = "not_run"
    failure_output: str | None = None  # captured stderr/stdout if gate failed
