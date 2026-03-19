#!/usr/bin/env python3
"""
Trace Context Propagation — W3C Trace Context for multi-agent workflows.

Generates and propagates trace_id / span_id / parent_span_id across agent
boundaries. Four propagation channels match the four process boundaries:

1. Environment variables  — orchestrator → spawned agent (at spawn time)
2. Dict serialization     — agent → agent (in AgentMessage trace fields)
3. Trace state file       — agent → hooks (file read per hook invocation)
4. Dict serialization     — dying agent → replacement (in handoff file)

W3C Trace Context format:
- trace_id:  32 lowercase hex chars (128-bit, e.g. "4bf92f3577b34da6a3ce929d0e0e4736")
- span_id:   16 lowercase hex chars (64-bit,  e.g. "00f067aa0ba902b7")
- parent_span_id: 16 lowercase hex chars or None (root spans have no parent)

Trace state files: ~/.claude/state/traces/{agent-id}.json

All I/O is fire-and-forget — errors are silently caught so tracing never
blocks agent work. Follows the same pattern as event_logger.py.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hooks.utils.error_logger import log_hook_error
from hooks.utils.state_helpers import atomic_write

# ---------------------------------------------------------------------------
# Environment variable names
# ---------------------------------------------------------------------------

ENV_TRACE_ID = "CLAUDE_TRACE_ID"
ENV_SPAN_ID = "CLAUDE_SPAN_ID"
ENV_PARENT_SPAN_ID = "CLAUDE_PARENT_SPAN_ID"

# ---------------------------------------------------------------------------
# Trace state directory
# ---------------------------------------------------------------------------

_CLAUDE_HOME = Path(os.path.expanduser("~/.claude"))
TRACE_STATE_DIR = _CLAUDE_HOME / "state" / "traces"


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def generate_trace_id() -> str:
    """Generate a W3C-compliant trace ID (32 lowercase hex chars, 128-bit)."""
    return secrets.token_hex(16)


def generate_span_id() -> str:
    """Generate a W3C-compliant span ID (16 lowercase hex chars, 64-bit)."""
    return secrets.token_hex(8)


# ---------------------------------------------------------------------------
# TraceContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TraceContext:
    """Immutable trace context for a single span.

    Represents one node in the trace tree. Create child spans via
    child_span() — the original context is never modified.

    Usage::

        # Orchestrator starts a workflow trace
        root = TraceContext.new_trace()

        # Spawn agent with trace context in env vars
        child = root.child_span()
        env = child.to_env()  # pass to agent spawn

        # Agent reads inherited context at startup
        inherited = TraceContext.from_env()
        my_span = inherited.child_span() if inherited else TraceContext.new_trace()

        # Agent saves context for hooks to read
        my_span.save("coder-p1-t3-a7f2")

        # Hook loads context to attach to log entries
        ctx = TraceContext.load("coder-p1-t3-a7f2")
        log_entry = StateTransitionEntry(..., **ctx.to_dict())

        # Message includes trace context in envelope
        message = TaskAssignMessage(..., **child.to_dict())
    """

    trace_id: str
    span_id: str
    parent_span_id: str | None = None

    # --- Factory methods ---

    @classmethod
    def new_trace(cls) -> TraceContext:
        """Create a root span for a new trace.

        The orchestrator calls this once per workflow run. All agents
        and operations within that workflow share the same trace_id.
        """
        return cls(
            trace_id=generate_trace_id(),
            span_id=generate_span_id(),
        )

    def child_span(self) -> TraceContext:
        """Create a child span within the same trace.

        Inherits trace_id and sets parent_span_id to this span's span_id.
        Call for each sub-operation: spawning an agent, sending a message,
        processing a hook, transitioning state, etc.
        """
        return TraceContext(
            trace_id=self.trace_id,
            span_id=generate_span_id(),
            parent_span_id=self.span_id,
        )

    # --- Channel 1: Environment variable propagation ---

    @classmethod
    def from_env(cls) -> TraceContext | None:
        """Read trace context from environment variables.

        Called by agents at startup to inherit the parent's trace context.
        Returns None if no trace context is set (running outside a traced workflow).
        """
        trace_id = os.environ.get(ENV_TRACE_ID)
        span_id = os.environ.get(ENV_SPAN_ID)
        if not trace_id or not span_id:
            return None
        return cls(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=os.environ.get(ENV_PARENT_SPAN_ID),
        )

    def to_env(self) -> dict[str, str]:
        """Export as environment variable dict for spawning child agents.

        The orchestrator calls ``child_span().to_env()`` and passes the
        result as env vars when spawning a new agent process.
        """
        env: dict[str, str] = {
            ENV_TRACE_ID: self.trace_id,
            ENV_SPAN_ID: self.span_id,
        }
        if self.parent_span_id:
            env[ENV_PARENT_SPAN_ID] = self.parent_span_id
        return env

    # --- Channel 2 & 4: Dict serialization (messages + handoffs) ---

    def to_dict(self) -> dict[str, str | None]:
        """Serialize to dict for embedding in Pydantic model fields.

        Works with any model that has trace_id, span_id, parent_span_id::

            msg = TaskAssignMessage(..., **ctx.to_dict())
            entry = StateTransitionEntry(..., **ctx.to_dict())
        """
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TraceContext | None:
        """Deserialize from a dict (Pydantic model fields, JSON, etc.).

        Returns None if trace_id or span_id are missing or None.
        """
        trace_id = data.get("trace_id")
        span_id = data.get("span_id")
        if not trace_id or not span_id:
            return None
        parent = data.get("parent_span_id")
        return cls(
            trace_id=str(trace_id),
            span_id=str(span_id),
            parent_span_id=str(parent) if parent else None,
        )

    # --- Channel 3: File-based propagation (agent → hooks) ---

    def save(self, agent_id: str, trace_dir: Path = TRACE_STATE_DIR) -> Path | None:
        """Write trace context to a state file for hook access.

        Hooks are separate Python processes invoked per tool call — they
        can't read the agent's in-memory context. This file bridges that gap.

        File: ~/.claude/state/traces/{agent-id}.json

        Returns the file path on success, None on failure. Never raises.
        """
        try:
            trace_dir.mkdir(parents=True, exist_ok=True)
            path = trace_dir / f"{agent_id}.json"
            atomic_write(path, json.dumps(self.to_dict(), indent=2) + "\n")
            return path
        except Exception as e:
            log_hook_error("trace_context", "TraceContext.save", e)
            return None

    @classmethod
    def load(
        cls, agent_id: str, trace_dir: Path = TRACE_STATE_DIR
    ) -> TraceContext | None:
        """Load trace context from a state file.

        Called by hooks to get the current agent's trace context.
        Returns None if no trace file exists or it's malformed. Never raises.
        """
        try:
            path = trace_dir / f"{agent_id}.json"
            if not path.exists():
                return None
            data = json.loads(path.read_text())
            return cls.from_dict(data)
        except Exception as e:
            log_hook_error("trace_context", "TraceContext.load", e)
            return None

    # --- W3C traceparent interop ---

    def to_traceparent(self) -> str:
        """Format as W3C traceparent header value.

        Format: ``{version}-{trace_id}-{span_id}-{trace_flags}``
        Example: ``00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01``

        Useful for future integration with OpenTelemetry, Jaeger, or Zipkin
        without changing any stored trace data.
        """
        return f"00-{self.trace_id}-{self.span_id}-01"

    @classmethod
    def from_traceparent(cls, header: str) -> TraceContext | None:
        """Parse a W3C traceparent header value.

        Returns None if the header is malformed.
        """
        parts = header.split("-")
        if len(parts) != 4:
            return None
        _, trace_id, span_id, _ = parts
        if len(trace_id) != 32 or len(span_id) != 16:
            return None
        try:
            int(trace_id, 16)
            int(span_id, 16)
        except ValueError:
            return None
        return cls(trace_id=trace_id, span_id=span_id)
