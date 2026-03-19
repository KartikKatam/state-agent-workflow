"""Context packet schemas — structured exploration output consumed by downstream agents.

Three packet types, one per exploration mode:
- CodebaseContext: full or incremental codebase analysis (Modes 1 & 2)
- QueryResult: targeted answer to a specific question (Mode 3)
- FeatureContext: feature-scoped exploration before implementation (Mode 4)

Written to: .claude/context/_codebase.json, .claude/context/queries/{topic}.json,
            .claude/context/{feature}-context.json
Written by: explorer agent (synthesis step)
Read by: strategist (planning), coder (implementation), tester (scenario design),
         auditor (scope verification), orchestrator (status monitoring)

All packets are JSON on disk. These models define structure, provide runtime
validation, and enable typed PTC extraction.

Design doc ref: "Context Packet Architecture" section.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas._constants import AGENT_ID_PATTERN, TracingMixin
from schemas.agent_state import AgentModel, AgentRole


# ---------------------------------------------------------------------------
# Shared sub-models (used across packet types)
# ---------------------------------------------------------------------------

DESCRIPTION_MAX_LENGTH = 50
"""Hard limit on free-text descriptions in context packets.
Enforced at validation. Keeps downstream token cost predictable."""


ImplementationStatus = Literal["implemented", "stub", "planned", "empty"]

ConfidenceLevel = float  # 0.0–1.0, see Epistemic Standards in SKILL.md


class FileRef(BaseModel):
    """Minimal file reference — the atomic unit of context packets."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Relative path from project root")
    lines: str | None = Field(
        default=None,
        description="Line reference: single (45), range (45-67), or null",
        examples=["45", "45-67"],
    )
    purpose: str = Field(
        max_length=DESCRIPTION_MAX_LENGTH,
        description="What this file does, ≤50 chars",
    )
    line_count: int | None = Field(default=None, ge=0)


class TypeEntry(BaseModel):
    """A public type, dataclass, or shared model discovered during exploration."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Type name, e.g. 'BatchProcessor'")
    file: str = Field(
        description="path:line reference",
        examples=["src/producer/batch.py:23"],
    )
    kind: Literal[
        "class", "dataclass", "protocol", "typedalias", "enum", "interface", "struct",
    ]
    usage: str = Field(
        max_length=DESCRIPTION_MAX_LENGTH,
        description="What this type is used for, ≤50 chars",
    )
    exported: bool = Field(
        default=True,
        description="Whether this type is part of the module's public API",
    )


class PatternEntry(BaseModel):
    """A coding pattern observed in the codebase (error handling, logging, config, etc.)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description="Pattern name",
        examples=["custom_exceptions", "singleton_config", "factory_dispatch"],
    )
    style: str = Field(
        max_length=DESCRIPTION_MAX_LENGTH,
        description="Brief characterization, ≤50 chars",
    )
    example_location: str = Field(
        description="path:line of one representative example",
        examples=["src/common/errors.py:5"],
    )
    read_if: str | None = Field(
        default=None,
        description="Condition under which a downstream agent should read this",
        examples=["adding new error types", "implementing a new processor"],
    )


class ConfigEntry(BaseModel):
    """A config class or dataclass with its fields and defaults."""

    model_config = ConfigDict(extra="forbid")

    name: str
    file: str = Field(description="path:line reference")
    fields: list[ConfigFieldEntry] = Field(default_factory=list)


class ConfigFieldEntry(BaseModel):
    """Single field within a config class."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: str = Field(description="Type annotation as string")
    default: str | None = Field(
        default=None,
        description="Default value as string, null if required",
    )


class DependencyEntry(BaseModel):
    """A project dependency from the manifest (not inferred from imports)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Package name as in manifest")
    version_constraint: str | None = Field(
        default=None,
        description="Version spec from manifest, e.g. '>=2.0,<3.0'",
    )
    source: str = Field(
        description="Which manifest file this came from",
        examples=["requirements.txt", "pyproject.toml", "package.json"],
    )
    dev_only: bool = False


class ImplicitContract(BaseModel):
    """An implicit dependency or ordering assumption not visible in imports."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(
        max_length=100,
        description="What the contract is, ≤100 chars",
    )
    between: list[str] = Field(
        description="Modules or files involved",
        min_length=2,
    )
    evidence: str = Field(
        description="path:line where the contract is observable",
    )
    confidence: ConfidenceLevel
    needs_verification: bool = False


# ---------------------------------------------------------------------------
# Module block — the primary structural unit
# ---------------------------------------------------------------------------


class ModuleBlock(BaseModel):
    """One logical module or directory in the codebase."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Directory path relative to project root")
    purpose: str = Field(
        max_length=DESCRIPTION_MAX_LENGTH,
        description="Module purpose, ≤50 chars",
    )
    status: ImplementationStatus
    status_evidence: str | None = Field(
        default=None,
        max_length=100,
        description="Why this status was assigned, e.g. 'all methods have pass/TODO bodies'",
    )
    files: list[FileRef] = Field(default_factory=list)
    entry_points: list[str] = Field(
        default_factory=list,
        description="Key entry point files or functions",
        examples=["src/producer/pipeline.py:main", "src/api/app.py:create_app"],
    )
    confidence: ConfidenceLevel = Field(
        default=1.0,
        description="Confidence in this module's analysis (0.0-1.0)",
    )
    needs_verification: bool = False


# ---------------------------------------------------------------------------
# Packet metadata (shared across all packet types)
# ---------------------------------------------------------------------------


class PacketMeta(BaseModel):
    """Metadata block present in every context packet."""

    model_config = ConfigDict(extra="forbid")

    created_at: datetime
    updated_at: datetime
    update_type: Literal["full", "incremental"]
    update_summary: str = Field(
        max_length=200,
        description="What changed in this update",
    )
    explorer_agent_id: str = Field(
        pattern=AGENT_ID_PATTERN,
        description="Which explorer produced this packet",
    )
    explorer_model: AgentModel
    commit_ref: str | None = Field(
        default=None,
        description="Git commit SHA at time of exploration — used for staleness checks",
    )
    files_analyzed: list[str] = Field(
        default_factory=list,
        description="All file paths read during exploration — used for git-diff staleness",
    )
    token_count: int | None = Field(
        default=None,
        ge=0,
        description="Measured token count of this packet (via tiktoken)",
    )
    languages_detected: list[str] = Field(
        default_factory=list,
        description="Programming languages found in the codebase",
        examples=[["python", "typescript"]],
    )


# ---------------------------------------------------------------------------
# Packet Type 1: CodebaseContext (Modes 1 & 2)
# ---------------------------------------------------------------------------


class CodebaseContext(TracingMixin):
    """Full or incremental codebase analysis packet.

    Written to: .claude/context/_codebase.json
    This is the primary structural reference for all downstream agents.
    Planners use it to break work into tasks. Coders use it to understand
    module boundaries. Auditors use it for scope verification.
    """

    model_config = ConfigDict(extra="forbid")

    meta: PacketMeta

    # --- Structure ---
    structure: CodebaseStructure

    # --- Types (indexed by module) ---
    types: dict[str, list[TypeEntry]] = Field(
        default_factory=dict,
        description="Types by module path. Key = module path, value = list of types",
    )

    # --- Patterns ---
    patterns: list[PatternEntry] = Field(default_factory=list)

    # --- Config ---
    configs: list[ConfigEntry] = Field(default_factory=list)

    # --- Dependencies (from manifest, NOT imports) ---
    dependencies: list[DependencyEntry] = Field(default_factory=list)

    # --- Implicit contracts ---
    implicit_contracts: list[ImplicitContract] = Field(default_factory=list)

    # --- Test conventions ---
    test_conventions: TestConventions | None = None

    # --- Name bank (avoid collisions when creating new code) ---
    name_bank: list[str] = Field(
        default_factory=list,
        description="Existing type/function/module names to avoid collisions",
    )

    # --- Human observations (NEVER regenerated, always preserved) ---
    manual_notes: list[str] = Field(
        default_factory=list,
        description="Human-added observations. Preserved verbatim on every update.",
    )

    # --- Cross-references ---
    related_context: list[str] = Field(
        default_factory=list,
        description="Paths to related context packets (feature contexts, query results)",
    )


class CodebaseStructure(BaseModel):
    """Top-level structural overview of the codebase."""

    model_config = ConfigDict(extra="forbid")

    root: str = Field(
        description="Project root directory",
        examples=["/workspace"],
    )
    blocks: list[ModuleBlock] = Field(
        description="Logical modules/directories and their contents",
    )
    dependency_edges: list[DependencyEdge] = Field(
        default_factory=list,
        description="Import/dependency relationships between modules",
    )


class DependencyEdge(BaseModel):
    """Directed dependency between two modules."""

    model_config = ConfigDict(extra="forbid")

    from_module: str
    to_module: str
    import_count: int = Field(
        default=1,
        ge=1,
        description="Number of import statements from source to target",
    )


class TestConventions(BaseModel):
    """Testing patterns observed in the codebase (from one sample test file)."""

    model_config = ConfigDict(extra="forbid")

    framework: str = Field(
        description="Test framework in use",
        examples=["pytest", "jest", "unittest", "vitest"],
    )
    test_directory: str = Field(
        description="Root test directory",
        examples=["tests/", "src/__tests__/"],
    )
    naming_pattern: str | None = Field(
        default=None,
        description="Test file naming convention",
        examples=["test_{module}.py", "{module}.test.ts"],
    )
    example_file: str = Field(
        description="path:line of one representative test file",
    )
    fixtures_location: str | None = None
    coverage_config: str | None = Field(
        default=None,
        description="Path to coverage config if present",
    )


# ---------------------------------------------------------------------------
# Packet Type 2: QueryResult (Mode 3)
# ---------------------------------------------------------------------------


class QueryResult(TracingMixin):
    """Answer to a targeted question about the codebase.

    Written to: .claude/context/queries/{topic-slug}.json
    Each query gets its own packet. Future identical or overlapping queries
    can check existing packets before re-exploring.
    """

    model_config = ConfigDict(extra="forbid")

    meta: QueryMeta

    # --- The question ---
    query: QueryInput

    # --- The answer ---
    answer: QueryAnswer

    # --- Sources consulted ---
    sources: list[QuerySource] = Field(default_factory=list)

    # --- Related context ---
    related_context: list[str] = Field(
        default_factory=list,
        description="Paths to related context packets",
    )


class QueryMeta(BaseModel):
    """Metadata for query result packets."""

    model_config = ConfigDict(extra="forbid")

    created_at: datetime
    explorer_agent_id: str = Field(pattern=AGENT_ID_PATTERN)
    explorer_model: AgentModel
    commit_ref: str | None = None
    files_analyzed: list[str] = Field(default_factory=list)
    token_count: int | None = Field(default=None, ge=0)
    supersedes: str | None = Field(
        default=None,
        description="Path to an older query packet this one replaces",
    )


class QueryInput(BaseModel):
    """The original question with context."""

    model_config = ConfigDict(extra="forbid")

    question: str
    scope: Literal["file", "directory", "module", "cross_module", "external"] | None = None
    requesting_agent: str | None = None
    requesting_task: str | None = None
    specific_questions: list[str] | None = Field(
        default=None,
        description="Sub-questions broken out from the main query",
    )


class QueryAnswer(BaseModel):
    """Structured answer to the query."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(
        max_length=200,
        description="Concise answer, ≤200 chars",
    )
    details: list[QueryDetail] = Field(
        default_factory=list,
        description="Supporting evidence and findings",
    )
    confidence: ConfidenceLevel
    needs_verification: bool = False
    limitations: list[str] = Field(
        default_factory=list,
        description="What couldn't be determined and why",
    )
    follow_up_suggested: list[str] = Field(
        default_factory=list,
        description="Additional questions the lead might want to ask based on findings",
    )


class QueryDetail(BaseModel):
    """One piece of evidence supporting the answer."""

    model_config = ConfigDict(extra="forbid")

    item: str = Field(
        max_length=100,
        description="The finding, ≤100 chars",
    )
    location: str = Field(
        description="path:line reference",
    )
    relevance: Literal["direct", "supporting", "peripheral"]


class QuerySource(BaseModel):
    """A file consulted while answering the query."""

    model_config = ConfigDict(extra="forbid")

    path: str
    lines_read: str | None = Field(
        default=None,
        description="Line range read, e.g. '1-50' or null for whole file",
    )
    relevant: bool = Field(
        default=True,
        description="Whether this source contributed to the answer",
    )


# ---------------------------------------------------------------------------
# Packet Type 3: FeatureContext (Mode 4)
# ---------------------------------------------------------------------------


class FeatureContext(TracingMixin):
    """Feature-scoped exploration packet produced before implementation.

    Written to: .claude/context/{feature}-context.json
    Maps everything a coder needs to know before implementing a feature:
    what to create, what to modify, what to follow, what to avoid.
    """

    model_config = ConfigDict(extra="forbid")

    meta: FeatureMeta

    # --- What the feature needs ---
    touchpoints: list[Touchpoint] = Field(
        description="Files to create, modify, or reference for this feature",
    )
    dependencies: FeatureDependencies

    # --- What the feature introduces ---
    new_items: list[NewItem] = Field(
        default_factory=list,
        description="Types, functions, config fields the feature will create",
    )

    # --- Conventions to follow ---
    patterns_to_follow: list[PatternRef] = Field(
        default_factory=list,
        description="Existing patterns the feature should match",
    )

    # --- Constraints ---
    naming_constraints: list[NamingConstraint] = Field(
        default_factory=list,
        description="Names to avoid and why (collision prevention)",
    )

    # --- Risks and unknowns ---
    risks: list[FeatureRisk] = Field(
        default_factory=list,
        description="Potential issues identified during exploration",
    )

    # --- Related context ---
    related_context: list[str] = Field(
        default_factory=list,
        description="Paths to related packets (codebase context, design doc)",
    )


class FeatureMeta(BaseModel):
    """Metadata for feature context packets."""

    model_config = ConfigDict(extra="forbid")

    created_at: datetime
    updated_at: datetime
    update_type: Literal["full", "incremental"]
    explorer_agent_id: str = Field(pattern=AGENT_ID_PATTERN)
    explorer_model: AgentModel
    feature: str = Field(description="Feature name, matches plan")
    design_doc_path: str | None = Field(
        default=None,
        description="Path to the design document that prompted this exploration",
    )
    commit_ref: str | None = None
    files_analyzed: list[str] = Field(default_factory=list)
    token_count: int | None = Field(default=None, ge=0)
    confidence: ConfidenceLevel = Field(
        default=1.0,
        description="Overall confidence in this feature context (0.0-1.0)",
    )


class Touchpoint(BaseModel):
    """A file that needs to be created, modified, or read for the feature."""

    model_config = ConfigDict(extra="forbid")

    file: str = Field(description="File path relative to project root")
    action: Literal["create", "modify", "read"]
    reason: str = Field(
        max_length=DESCRIPTION_MAX_LENGTH,
        description="Why this file is a touchpoint, ≤50 chars",
    )
    lines_of_interest: str | None = Field(
        default=None,
        description="Specific line range, e.g. '45-67'",
    )
    confidence: ConfidenceLevel = 1.0
    needs_verification: bool = False


class FeatureDependencies(BaseModel):
    """Types, configs, and functions the feature depends on."""

    model_config = ConfigDict(extra="forbid")

    types: list[TypeRef] = Field(
        default_factory=list,
        description="Existing types the feature will use",
    )
    configs: list[str] = Field(
        default_factory=list,
        description="Config fields the feature reads or writes",
    )
    functions: list[str] = Field(
        default_factory=list,
        description="Existing functions the feature calls, as path:line refs",
    )
    external_packages: list[str] = Field(
        default_factory=list,
        description="External packages the feature requires (may need installing)",
    )


class TypeRef(BaseModel):
    """Reference to an existing type the feature depends on."""

    model_config = ConfigDict(extra="forbid")

    name: str
    file: str = Field(description="path:line reference")
    usage: str = Field(
        max_length=DESCRIPTION_MAX_LENGTH,
        description="How the feature uses this type, ≤50 chars",
    )


class NewItem(BaseModel):
    """A new type, function, or config field the feature will introduce."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["type", "function", "config_field", "module", "file"]
    name: str
    location: str = Field(
        description="Where this will be created (path or module)",
    )
    purpose: str = Field(
        max_length=DESCRIPTION_MAX_LENGTH,
        description="What this new item does, ≤50 chars",
    )


class PatternRef(BaseModel):
    """An existing pattern the feature should follow for consistency."""

    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(
        max_length=DESCRIPTION_MAX_LENGTH,
        description="Pattern name/description, ≤50 chars",
    )
    example_location: str = Field(description="path:line of example to follow")
    why: str = Field(
        max_length=100,
        description="Why this pattern applies to the feature",
    )


class NamingConstraint(BaseModel):
    """A name to avoid when creating new code for the feature."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="The name that's taken or reserved")
    location: str = Field(description="Where it's already used (path:line)")
    reason: str = Field(
        max_length=DESCRIPTION_MAX_LENGTH,
        description="Why collision would be a problem, ≤50 chars",
    )


class FeatureRisk(BaseModel):
    """A potential issue identified during feature exploration."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(max_length=100)
    severity: Literal["high", "medium", "low"]
    location: str | None = Field(
        default=None,
        description="path:line where the risk is observable",
    )
    mitigation: str | None = Field(
        default=None,
        max_length=100,
        description="Suggested approach to address the risk",
    )
    confidence: ConfidenceLevel = 1.0
