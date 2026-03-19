"""Preferences model — global and per-role configuration for agent behavior.

Written to: ~/.claude/state/preferences.json
Written by: orchestrator (user preferences), scribe (learned preferences)
Read by: SessionStart hook (loads global + role-specific), annotation system (live updates)

Design doc ref: "Decision Propagation" section, lines 599-604.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .agent_state import AgentRole


PreferenceSource = Literal["user", "learned", "default"]
PreferenceConfidence = Literal["high", "medium", "low"]

AuditMode = Literal["universal", "conditional"]


class Preference(BaseModel):
    """Single preference entry with provenance tracking."""

    model_config = ConfigDict(extra="forbid")

    value: Any = Field(
        description="The preference value (string, bool, number, list, etc.)"
    )
    source: PreferenceSource
    confidence: PreferenceConfidence
    added_at: datetime


class Preferences(BaseModel):
    """Global + per-role preference store. SessionStart hook loads global + agent's role-specific subset."""

    model_config = ConfigDict(extra="forbid")

    global_prefs: dict[str, Preference] = Field(
        default_factory=dict,
        alias="global",
        description="Preferences applied to all agents regardless of role",
    )
    by_role: dict[AgentRole, dict[str, Preference]] = Field(
        default_factory=dict,
        description="Role-scoped preferences, keyed by role name",
    )
