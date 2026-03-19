"""
Hook utilities for the agentic workflow system.

Modules:
- state_helpers: Agent identity, state file access, directory setup
- schema_validator: Pydantic-based JSON file validation
- event_logger: Fire-and-forget JSONL event logging
- context_monitor: Context window usage monitoring and handoff decisions
"""

from hooks.utils.error_logger import log_hook_error
from hooks.utils.context_monitor import (
    ContextStatus,
    check_context_usage,
    format_context_warning,
    log_context_check,
    should_auto_handoff,
)
from hooks.utils.event_logger import (
    emit,
    emit_agent_registered,
    emit_agent_terminated,
    emit_context_pressure,
    emit_decision_logged,
    emit_file_written,
    emit_message_sent,
    emit_skill_loaded,
    emit_state_transition,
    rotate_events_file,
)
from hooks.utils.schema_validator import (
    get_model_for_type,
    match_schema_type,
    validate_json_file,
)
from hooks.utils.state_helpers import (
    atomic_write,
    ensure_dirs,
    get_agent_id,
    get_agent_role,
    get_current_state,
    read_json_safe,
    read_stdin,
)
from hooks.utils.trace_context import (
    TraceContext,
    generate_span_id,
    generate_trace_id,
)

__all__ = [
    # state_helpers
    "get_agent_id",
    "get_agent_role",
    "get_current_state",
    "ensure_dirs",
    "read_json_safe",
    "read_stdin",
    "atomic_write",
    # schema_validator
    "match_schema_type",
    "validate_json_file",
    "get_model_for_type",
    # event_logger
    "emit",
    "emit_file_written",
    "emit_skill_loaded",
    "emit_context_pressure",
    "emit_state_transition",
    "emit_message_sent",
    "emit_decision_logged",
    "emit_agent_registered",
    "emit_agent_terminated",
    "rotate_events_file",
    # trace_context
    "TraceContext",
    "generate_trace_id",
    "generate_span_id",
    # context_monitor
    # error_logger
    "log_hook_error",
    # context_monitor
    "ContextStatus",
    "check_context_usage",
    "should_auto_handoff",
    "format_context_warning",
    "log_context_check",
]
