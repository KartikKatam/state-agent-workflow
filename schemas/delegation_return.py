"""Delegation return models — Pydantic discriminated union for sub-agent returns.

Sub-agents return structured JSON when completing a delegation. The return
format depends on the delegation_type (targeted, guided, exploration,
research, tdd_chunk). These models validate that the return matches the
expected schema.

Used by: validate_return.py (SubagentStop hook), parent agent validation.

Design: extra="allow" on all models because sub-agents may include
additional fields beyond the schema, and that is acceptable.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag


# --- Shared sub-models ---


class UnknownResolved(BaseModel):
    """A single resolved unknown from a guided delegation."""

    model_config = ConfigDict(extra="allow")

    unknown: str
    answer: str
    confidence: str


# --- Delegation return types ---

DelegationStatus = Literal["completed", "partial", "failed"]


class TargetedReturn(BaseModel):
    """Return schema for targeted delegations."""

    model_config = ConfigDict(extra="allow")

    delegation_type: Literal["targeted"]
    status: DelegationStatus
    result: dict[str, Any]
    decisions_made: list[dict[str, Any]]
    unexpected_findings: list[str] = Field(default_factory=list)


class GuidedReturn(BaseModel):
    """Return schema for guided delegations."""

    model_config = ConfigDict(extra="allow")

    delegation_type: Literal["guided"]
    status: DelegationStatus
    unknowns_resolved: list[UnknownResolved]
    decisions_made: list[dict[str, Any]]
    scope_extensions: list[dict[str, Any]] = Field(default_factory=list)


class ExplorationReturn(BaseModel):
    """Return schema for exploration delegations."""

    model_config = ConfigDict(extra="allow")

    delegation_type: Literal["exploration"]
    status: DelegationStatus
    findings: dict[str, Any]
    essential_output_confidence: dict[str, Any]
    scope_extensions: list[dict[str, Any]] = Field(default_factory=list)
    cross_scope_findings: list[dict[str, Any]] = Field(default_factory=list)


class ResearchReturn(BaseModel):
    """Return schema for research delegations."""

    model_config = ConfigDict(extra="allow")

    delegation_type: Literal["research"]
    status: DelegationStatus
    findings: dict[str, Any]
    essential_output_confidence: dict[str, Any]
    citations: list[dict[str, Any]]
    gaps: list[str] = Field(default_factory=list)


class TddChunkReturn(BaseModel):
    """Return schema for tdd_chunk delegations."""

    model_config = ConfigDict(extra="allow")

    delegation_type: Literal["tdd_chunk"]
    status: DelegationStatus
    tests_written: list[str]
    pass_gate_results: dict[str, Any]
    decisions_made: list[dict[str, Any]]
    carry_forward: list[str] = Field(default_factory=list)


def _delegation_type_discriminator(v: Any) -> str:
    """Extract delegation_type for discriminated union dispatch."""
    if isinstance(v, dict):
        return v.get("delegation_type", "")
    return getattr(v, "delegation_type", "")


DelegationReturn = Annotated[
    Annotated[TargetedReturn, Tag("targeted")]
    | Annotated[GuidedReturn, Tag("guided")]
    | Annotated[ExplorationReturn, Tag("exploration")]
    | Annotated[ResearchReturn, Tag("research")]
    | Annotated[TddChunkReturn, Tag("tdd_chunk")],
    Discriminator(_delegation_type_discriminator),
]
