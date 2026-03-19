"""System-level workflow state — tracks the overall workflow lifecycle.

Written to: ~/.claude/state/workflow.json
Written by: orchestrator (phase transitions), workflow_state.py (validation)
Read by: orchestrator (current phase), hooks (state gating), PTC (selective extraction)

Design doc ref: "System-Level State Machine" section, lines 306-313.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SystemState = Literal[
    "IDLE",
    "DESIGN_LOADED",
    "EXPLORING",
    "CONTEXT_READY",
    "STRATEGIZING",
    "PLAN_TEXT_REVIEW",
    "PLAN_JSON_CONVERSION",
    "PLAN_READY",
    "PHASE_ACTIVE",
    "PHASE_IMPLEMENTATION",
    "PHASE_TASKS_COMPLETE",
    "PHASE_SCENARIO_EXECUTION",
    "PHASE_AUDIT",
    "PHASE_REPORT",
    "PHASE_COMMITTED",
    "PHASE_REMEDIATION",
    "PHASE_ARBITRATION",
    "FINAL_AUDIT",
    "COMPLETE",
    "ERROR",
]


class RemediationCycleEntry(BaseModel):
    """One cycle of remediation with pass count tracking."""

    model_config = ConfigDict(extra="forbid")

    cycle: int = Field(ge=1, le=5)
    pass_count: int = Field(ge=0)
    total_scenarios: int = Field(ge=1)
    timestamp: datetime


class PhaseHistoryEntry(BaseModel):
    """One state transition in the workflow's history."""

    model_config = ConfigDict(extra="forbid")

    state: SystemState
    entered_at: datetime
    exited_at: datetime | None = None


class WorkflowState(BaseModel):
    """Top-level workflow state. One per active workflow run."""

    model_config = ConfigDict(extra="forbid")

    # --- Required at workflow init ---
    workflow_id: str = Field(
        description="Unique workflow run identifier",
        examples=["wf-lpr-tracking-20260225"],
    )
    feature: str = Field(
        description="Feature being implemented (matches plan filename)",
        examples=["lpr-tracking", "batch-selection"],
    )
    base_branch: str = Field(
        description="Configurable base branch for all worktrees and merges",
        examples=["feature/lpr-tracking", "main"],
    )
    current_state: SystemState
    started_at: datetime

    # --- Populated progressively ---
    last_updated: datetime | None = None
    active_agents: list[str] = Field(
        default_factory=list,
        description="Agent IDs currently alive in this workflow",
    )
    phase_history: list[PhaseHistoryEntry] = Field(
        default_factory=list,
        description="Ordered record of state transitions",
    )
    remediation_cycle_count: int = Field(default=0, ge=0, le=5)
    remediation_pass_history: list[RemediationCycleEntry] = Field(default_factory=list)
