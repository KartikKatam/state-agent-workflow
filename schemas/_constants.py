"""Shared constants and base classes for schema models.

Centralizes patterns, type aliases, and mixins used across multiple schema files.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --- Patterns ---
AGENT_ID_PATTERN = r"^[a-z]+-[a-z0-9]+-[a-z0-9]+-[a-f0-9]{4}$"

# --- Field constraints ---
SUMMARY_MAX_LENGTH = 500

# --- Shared type aliases ---
HandoffReason = Literal[
    "context_limit",
    "task_complete",
    "error",
    "user_requested",
    "scrap_and_retry",
]

MessageType = Literal[
    "task_assign",
    "task_complete",
    "info_request",
    "info_ready",
    "status_update",
    "context_query",
    "context_response",
    "handoff",
    "shutdown",
    "peer_notify",
    "bug_report",
]


# --- Mixins ---
class TracingMixin(BaseModel):
    """W3C Trace Context fields for distributed tracing.

    Inherit from this instead of BaseModel for models that need tracing.
    """

    model_config = ConfigDict(extra="forbid")

    trace_id: str | None = Field(
        default=None,
        description="W3C Trace Context trace ID for distributed tracing",
    )
    span_id: str | None = Field(
        default=None,
        description="W3C Trace Context span ID",
    )
    parent_span_id: str | None = Field(
        default=None,
        description="W3C Trace Context parent span ID",
    )
