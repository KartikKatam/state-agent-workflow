"""Research entry schema — unified model for all research output.

Replaces V1's separate persistent/ephemeral schemas.
All research persists. No expiration, no promotion, no classification overhead.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConfidenceLevel(str, Enum):
    """Research confidence levels based on source quality."""

    HIGH = "high"  # 0.8-1.0: Official docs, multiple agreeing sources
    MEDIUM = "medium"  # 0.6-0.8: Reputable sources, some gaps
    LOW = "low"  # 0.4-0.6: Single source, conflicting info
    UNCERTAIN = "uncertain"  # <0.4: Speculation, no good sources


class SourceType(str, Enum):
    """Source categorization for confidence scoring."""

    OFFICIAL_DOCS = "official_docs"  # Base confidence: 0.9-1.0
    GITHUB = "github"  # Base confidence: 0.8-0.9
    TUTORIAL = "tutorial"  # Base confidence: 0.7-0.8
    STACKOVERFLOW = "stackoverflow"  # Base confidence: 0.6-0.7
    BLOG = "blog"  # Base confidence: 0.5-0.6


class Source(BaseModel):
    """A source consulted during research."""

    model_config = ConfigDict(extra="forbid")

    title: str
    url: str
    type: SourceType


class ResearchMeta(BaseModel):
    """Metadata for a research entry."""

    model_config = ConfigDict(extra="forbid")

    research_id: str = Field(description="Unique identifier for this research")
    topic: str = Field(description="What was researched")
    requested_by: str = Field(description="Agent that requested this research")
    completed_at: datetime
    updated_at: datetime | None = Field(
        default=None,
        description="When this research was last updated (None if never updated)",
    )
    sources_used: list[str] = Field(
        description="Tools used: websearch, ptc, etc."
    )
    confidence: ConfidenceLevel
    confidence_reason: str = Field(
        description="Brief explanation of confidence level"
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="Areas not fully covered",
    )
    from_cache: bool = Field(
        default=False,
        description="True if returned from existing research without new queries",
    )


class ResearchFindings(BaseModel):
    """Structured research findings. Fields are optional — populate only what's relevant."""

    model_config = ConfigDict(extra="forbid")

    key_points: list[str] = Field(
        default_factory=list,
        description="Main insights as concise bullet points",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific structured content. "
        "API: endpoints, classes. "
        "Comparison: options, recommendation. "
        "Troubleshooting: causes, solutions. "
        "Architecture: patterns, decision_matrix.",
    )
    examples: list[dict[str, str]] = Field(
        default_factory=list,
        description="Code examples: [{title, code, description}]",
    )
    gotchas: list[str] = Field(
        default_factory=list,
        description="Common pitfalls and mistakes",
    )


class ResearchEntry(BaseModel):
    """A single research output. All research persists — no ephemeral/persistent split."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="1-0-0")
    meta: ResearchMeta
    summary: str = Field(
        description="2-3 sentence answer to the research question"
    )
    findings: ResearchFindings
    sources: list[Source]


class ResearchIndexEntry(BaseModel):
    """Entry in the research index (_index.json)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Matches filename without .json")
    path: str = Field(description="Relative path from research dir")
    topic: str
    keywords: list[str] = Field(
        description="Search terms for finding this research"
    )
    confidence: ConfidenceLevel
    created_at: datetime
    source_count: int


class ResearchIndex(BaseModel):
    """Top-level research index."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="1-0-0")
    entries: list[ResearchIndexEntry] = Field(default_factory=list)
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="Aggregate stats: total_entries, topics_covered",
    )
