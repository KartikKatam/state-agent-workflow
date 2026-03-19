#!/usr/bin/env python3
"""CLI entry point for workflow state daemon.

Implementation has been refactored into scripts/daemon/ package.
This module re-exports all names for backwards compatibility.
"""

# Re-export all public and private names for backwards compat.
# Tests import this module as `daemon` and access both public names
# (register_agent, handle_pre_tool) and private names (_machine_cache, etc.)

# --- State manager ---
from scripts.daemon.state_manager import (  # noqa: F401
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
from scripts.daemon.guards import (  # noqa: F401
    GuardFn,
    _GUARD_REGISTRY,
    evaluate_guards,
)

# Import guard modules to trigger registration
import scripts.daemon.guards.mechanical  # noqa: F401
import scripts.daemon.guards.decision  # noqa: F401

# --- Validators ---
from scripts.daemon.validators import (  # noqa: F401
    VALIDATOR_REGISTRY,
    ValidatorFn,
    _validate_post_actions,
)

# --- Permissions ---
from scripts.daemon.permissions import (  # noqa: F401
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
from scripts.daemon.annotations import (  # noqa: F401
    _async_validate_think,
    _emit_think_annotation,
    validate_think,
)

# --- Transitions ---
from scripts.daemon.transitions import (  # noqa: F401
    _auto_populate_session_log,
    _post_tool_call_count,
    _sent_context_warnings,
    _try_auto_transition,
    do_transition,
    do_system_transition,
    handle_post_tool,
)

# --- Server ---
from scripts.daemon.server import (  # noqa: F401
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

if __name__ == "__main__":
    main()
