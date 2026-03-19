"""Delegation prompt schema models — Pydantic discriminated union.

Defines the 5 delegation prompt types used by the orchestrator when dispatching
work via Task tool calls. Each type has different required fields:

- targeted: You have specific file coordinates. Requires non-empty
  file_coordinates, output format.
- guided: Delegate needs judgment. Requires unknowns, scope_boundary.
- tdd_chunk: TDD implementation with test specs. Requires test_specifications
  with pass_a, success_criteria.
- exploration: Scoped codebase exploration. Requires exploration_scope with
  primary_targets, essential_output.
- research: Source-targeted research. Requires research_scope with
  primary_targets, source_directives, essential_output.

All types share: type, task, known_context, output_contract.

Validated by PreToolUse hook (validate_delegation_prompt.py).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    TypeAdapter,
    field_validator,
    model_validator,
)


# ---------------------------------------------------------------------------
# Nested component models
# ---------------------------------------------------------------------------


class FileCoordinate(BaseModel):
    """A file coordinate pointing to a specific file (and optionally a region)."""

    model_config = ConfigDict(extra="allow")

    path: str = Field(min_length=1)


class OutputContract(BaseModel):
    """Output contract specifying where and how the delegate writes output."""

    model_config = ConfigDict(extra="allow")

    path: str = Field(min_length=1)
    format: str | None = None  # required for targeted, optional for others

    @field_validator("path")
    @classmethod
    def path_must_be_absolute_or_dotclaude(cls, v: str) -> str:
        if not v.startswith("/") and not v.startswith(".claude/"):
            raise ValueError(
                "output_contract.path '{}' is not absolute. "
                "Use /home/... or .claude/... paths.".format(v)
            )
        return v


class KnownContext(BaseModel):
    """Context the orchestrator already has — passed to the delegate."""

    model_config = ConfigDict(extra="allow")

    file_coordinates: list[FileCoordinate] = Field(default_factory=list)


class ExplorationScope(BaseModel):
    """Scope definition for exploration delegation type."""

    model_config = ConfigDict(extra="allow")

    primary_targets: list[Any] = Field(min_length=1)


class ResearchScope(BaseModel):
    """Scope definition for research delegation type."""

    model_config = ConfigDict(extra="allow")

    primary_targets: list[Any] = Field(min_length=1)


class TestSpecifications(BaseModel):
    """Test specifications for TDD chunk delegation type."""

    model_config = ConfigDict(extra="allow")

    pass_a: list[Any] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Base model with common fields
# ---------------------------------------------------------------------------


class _DelegationBase(BaseModel):
    """Common fields shared by all delegation prompt types."""

    model_config = ConfigDict(extra="allow")

    task: str = Field(min_length=1)
    known_context: KnownContext
    output_contract: OutputContract

    @field_validator("task")
    @classmethod
    def task_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError(
                "Missing required 'task' field. "
                "Describe what the delegate should accomplish."
            )
        return v

    @model_validator(mode="after")
    def check_file_coordinate_paths(self) -> _DelegationBase:
        """Validate that file_coordinates have at least one absolute path."""
        fc = self.known_context.file_coordinates
        if not fc:
            return self

        has_abs = any(
            c.path.startswith("/") or c.path.startswith(".claude/") for c in fc
        )
        if not has_abs:
            raise ValueError(
                "file_coordinates entries have no absolute paths. "
                "Use /home/... or /workspace/... paths so the delegate doesn't search."
            )
        return self


# ---------------------------------------------------------------------------
# Per-type models
# ---------------------------------------------------------------------------


class TargetedDelegation(_DelegationBase):
    """Delegation type for when you have specific file coordinates."""

    type: Literal["targeted"]

    @model_validator(mode="after")
    def check_targeted_requirements(self) -> TargetedDelegation:
        # known_context.file_coordinates must be non-empty
        fc = self.known_context.file_coordinates
        if not fc:
            raise ValueError(
                "Type 'targeted' requires known_context.file_coordinates with at least "
                "one entry. You chose 'targeted' because you have specific coordinates -- "
                "include them. If you don't have coordinates, use type 'guided' instead."
            )

        # output_contract.format required
        if not self.output_contract.format:
            raise ValueError(
                "Type 'targeted' requires output_contract.format. "
                "Specify the output format (e.g., 'JSON', 'Python source file')."
            )

        return self


class GuidedDelegation(_DelegationBase):
    """Delegation type for when the delegate needs judgment/discretion."""

    type: Literal["guided"]
    unknowns: list[str] = Field(min_length=1)
    scope_boundary: dict[str, Any]

class TddChunkDelegation(_DelegationBase):
    """Delegation type for TDD implementation with test specifications."""

    type: Literal["tdd_chunk"]
    test_specifications: TestSpecifications
    success_criteria: list[Any] = Field(min_length=1)

class ExplorationDelegation(_DelegationBase):
    """Delegation type for scoped codebase exploration."""

    type: Literal["exploration"]
    exploration_scope: ExplorationScope
    essential_output: list[Any] = Field(min_length=1)

class ResearchDelegation(_DelegationBase):
    """Delegation type for source-targeted research."""

    type: Literal["research"]
    research_scope: ResearchScope
    source_directives: dict[str, Any]
    essential_output: list[Any] = Field(min_length=1)

# ---------------------------------------------------------------------------
# Discriminated union
# ---------------------------------------------------------------------------

VALID_TYPES = ("targeted", "guided", "tdd_chunk", "exploration", "research")


def _get_delegation_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        return v.get("type", "")
    return getattr(v, "type", "")


DelegationPrompt = Annotated[
    Annotated[TargetedDelegation, Tag("targeted")]
    | Annotated[GuidedDelegation, Tag("guided")]
    | Annotated[TddChunkDelegation, Tag("tdd_chunk")]
    | Annotated[ExplorationDelegation, Tag("exploration")]
    | Annotated[ResearchDelegation, Tag("research")],
    Discriminator(_get_delegation_discriminator),
]
"""Top-level delegation prompt type. Use with:
    from pydantic import TypeAdapter
    adapter = TypeAdapter(DelegationPrompt)
    prompt = adapter.validate_python(data_dict)
"""

DelegationPromptAdapter = TypeAdapter(DelegationPrompt)
"""Pre-built TypeAdapter for DelegationPrompt validation."""
