"""Daemon package — state machine enforcement daemon.

Re-exports all public names for backwards compatibility with existing code
that imports from scripts.workflow_state.

Extracted from scripts/workflow_state.py (Chunk 2 of daemon hardening plan).
"""

from __future__ import annotations

# --- State manager (paths, loading, CRUD, caching) ---
from scripts.daemon.state_manager import (
    AGENTS_DIR,
    ANNOTATIONS_DIR,
    DECISIONS_LOG_DIR,
    MACHINES_DIR,
    MESSAGE_BUS_LOG,
    STATE_DIR,
    SYSTEM_STATE_FILE,
    TEMP_DIR,
    TRANSITION_LOG,
    _agent_state_cache,
    _async_write,
    _get_agent_state_cached,
    _get_agent_state_dict,
    _get_gate_result,
    _get_system_state_dict,
    _invalidate_agent_cache,
    _machine_cache,
    _pid_path,
    _project_dir,
    _set_pending_critical,
    _set_pending_handoff,
    _socket_path,
    count_transition_occurrences,
    get_agent_state,
    get_system_state,
    load_machine,
    log_transition,
    register_agent,
    resolve_machine,
    save_agent_state,
    save_system_state,
    update_agent_context,
)

# --- Guards ---
from scripts.daemon.guards import (
    GuardFn,
    _GUARD_REGISTRY,
    evaluate_guards,
)

# Import mechanical and decision guards to trigger registration
import scripts.daemon.guards.mechanical  # noqa: F401
import scripts.daemon.guards.decision  # noqa: F401

# --- Validators ---
from scripts.daemon.validators import (
    VALIDATOR_REGISTRY,
    ValidatorFn,
    _validate_post_actions,
)

# --- Permissions ---
from scripts.daemon.permissions import (
    _ASK_BASH_PATTERNS,
    _ASK_EXEMPT_ROLES,
    _HARD_BLOCK_BASH_PATTERNS,
    _is_hard_blocked,
    _needs_user_ask,
    check_tool_allowed,
    handle_pre_tool,
    path_matches_glob,
)

# --- Annotations ---
from scripts.daemon.annotations import (
    _async_validate_think,
    _emit_think_annotation,
    validate_think,
)

# --- Transitions ---
from scripts.daemon.transitions import (
    _auto_populate_session_log,
    _post_tool_call_count,
    _sent_context_warnings,
    _try_auto_transition,
    do_system_transition,
    handle_post_tool,
)

# --- Server ---
from scripts.daemon.server import (
    RequestHandler,
    WorkflowDaemon,
    _detect_workflow_id,
    _shutdown_event,
    daemon_status,
    execute,
    main,
    process_request,
    send_to_daemon,
    serve,
    stop_daemon,
)

__all__ = [
    # Paths
    "STATE_DIR",
    "AGENTS_DIR",
    "SYSTEM_STATE_FILE",
    "MACHINES_DIR",
    "TRANSITION_LOG",
    "ANNOTATIONS_DIR",
    "TEMP_DIR",
    "DECISIONS_LOG_DIR",
    "MESSAGE_BUS_LOG",
    # State manager
    "load_machine",
    "resolve_machine",
    "get_agent_state",
    "save_agent_state",
    "get_system_state",
    "save_system_state",
    "log_transition",
    "count_transition_occurrences",
    "register_agent",
    "update_agent_context",
    # Guards
    "GuardFn",
    "_GUARD_REGISTRY",
    "evaluate_guards",
    # Validators
    "VALIDATOR_REGISTRY",
    "ValidatorFn",
    # Permissions
    "check_tool_allowed",
    "handle_pre_tool",
    "path_matches_glob",
    # Annotations
    "_emit_think_annotation",
    "_async_validate_think",
    # Transitions
    "do_system_transition",
    "_try_auto_transition",
    "handle_post_tool",
    # Server
    "process_request",
    "execute",
    "main",
    "serve",
    "stop_daemon",
    "daemon_status",
    "send_to_daemon",
]
