"""Message bus log entry schema.

Each line in ~/.claude/logs/message-bus.jsonl is one MessageBusLogEntry.
Written by PostToolUse hook on SendMessage — pure Python, zero tokens.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field

from ._constants import SUMMARY_MAX_LENGTH, MessageType, TracingMixin


class MessageBusLogEntry(TracingMixin):
    """A single inter-agent message event.

    Logs all SendMessage traffic for observability, debugging, and the
    TUI message viewer. content_hash enables dedup detection.
    """

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    message_id: str  # uuid
    from_agent: str
    to_agent: str
    message_type: MessageType
    summary: str = Field(..., max_length=SUMMARY_MAX_LENGTH)
    content_hash: str | None = None  # SHA-256 of full_content for dedup detection
    full_content: str | None = None
    metadata: dict[str, Any] | None = None
