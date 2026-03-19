"""File-based messaging system schema models.

Defines the message envelope, team manifest, and registry models
for the file-based inter-agent messaging layer.

Message files: ~/.claude/messages/{team_name}/{agent}.inbox.json
Registry: ~/.claude/messages/_registry.json
Manifests: ~/.claude/messages/{team_name}/_manifest.json
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ._constants import MessageType
from .agent_state import AgentRole


class MessageEnvelope(BaseModel):
    """Single message in an agent's inbox."""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(description="UUID4 identifier")
    sequence: int = Field(ge=1, description="Monotonic per inbox")
    from_agent: str
    to_agent: str
    team_id: str
    message_type: MessageType
    priority: Literal["blocking", "non-blocking"]
    reply_to: str | None = None
    timestamp: datetime
    read: bool = False
    body: dict = Field(
        default_factory=dict,
        description="Lightweight metadata (task_type, status, etc.). "
        "Full payload lives at content_path if set.",
    )
    content_path: str | None = Field(
        default=None,
        description="Path to the full validated payload file. "
        "Written by send_msg.py to payloads/{message_id}.json",
    )


class AgentRegistration(BaseModel):
    """Per-agent entry in team manifest."""

    model_config = ConfigDict(extra="forbid")

    role: AgentRole
    pid: int | None = None
    session_id: str | None = None
    status: Literal["active", "idle", "departed", "terminated"]
    joined_at: datetime
    inbox_path: str


class TeamManifest(BaseModel):
    """Team membership and metadata."""

    model_config = ConfigDict(extra="forbid")

    team_id: str
    workflow_id: str
    created_at: datetime
    created_by: str
    agents: dict[str, AgentRegistration] = Field(default_factory=dict)


class TeamRegistryEntry(BaseModel):
    """Single team entry in the global registry."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    created_at: datetime
    status: Literal["active", "archived"]


class TeamRegistry(BaseModel):
    """Index of all active teams."""

    model_config = ConfigDict(extra="forbid")

    teams: dict[str, TeamRegistryEntry] = Field(default_factory=dict)
