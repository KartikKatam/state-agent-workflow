"""Transition engine — auto-transitions, system transitions, handle_post_tool.

Extracted from scripts/workflow_state.py (Chunk 2 of daemon hardening plan).
Chunk 5: Consolidated handle_post_tool with blocking validators, synchronous
think validation, ambiguity detection in auto-transitions, and think annotation
emission on state entry.

do_transition() removed (was dead code). do_system_transition() kept for orchestrator CLI.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone

from hooks.utils.context_monitor import check_context_usage, format_context_warning
from hooks.utils.state_helpers import (
    locked_read_modify_write,
    locked_read_modify_write_jsonl,
)
from schemas.state_machine import StateDefinition
from schemas.system_state import PhaseHistoryEntry

from scripts.daemon.annotations import _emit_think_annotation, validate_think
from scripts.daemon.guards import evaluate_guards
from scripts.daemon import state_manager as _sm
from scripts.daemon.state_manager import (
    _async_write,
    _get_agent_state_cached,
    _get_gate_result,
    _invalidate_agent_cache,
    _project_dir,
    _set_pending_critical,
    _set_pending_handoff,
    count_transition_occurrences,
    get_agent_state,
    get_system_state,
    load_machine,
    log_transition,
    resolve_machine,
    save_agent_state,
    save_system_state,
)
from scripts.daemon.validators import VALIDATOR_REGISTRY

_log = logging.getLogger("workflow_state")

# ---------------------------------------------------------------------------
# Module-level state (shared by daemon threads)
# ---------------------------------------------------------------------------
_post_tool_call_count: dict[str, int] = {}
_sent_context_warnings: dict[str, set[str]] = {}


# ---------------------------------------------------------------------------
# System transitions (kept for orchestrator CLI)
# ---------------------------------------------------------------------------
def do_transition(agent_id: str, to_state: str, trigger: str) -> dict:
    """Validate and execute a state transition.

    Returns {"ok": bool, "reason": str}.
    NOTE: This is dead code in production (daemon uses _try_auto_transition).
    Kept temporarily for backward compat with tests — will be removed in Chunk 6.
    """
    agent = get_agent_state(agent_id)
    if not agent:
        return {"ok": False, "reason": "Agent state not found"}

    machine = resolve_machine(agent.role, agent.current_state)
    if not machine:
        return {"ok": False, "reason": "No machine found for role"}

    from_state = agent.current_state

    # Find matching transition (regular then universal)
    matching = [
        t
        for t in machine.transitions
        if t.from_state == from_state
        and t.to_state == to_state
        and t.trigger == trigger
    ]
    if not matching:
        # Check universal transitions (from_state="*")
        matching = [
            t
            for t in machine.universal_transitions
            if t.to_state == to_state and t.trigger == trigger
        ]
    if not matching:
        return {
            "ok": False,
            "reason": (
                f"No valid transition: {from_state} -> {to_state} (trigger: {trigger})"
            ),
        }

    t = matching[0]

    # Check max_occurrences
    if t.max_occurrences is not None:
        count = count_transition_occurrences(agent_id, from_state, to_state)
        if count >= t.max_occurrences:
            return {
                "ok": False,
                "reason": (
                    f"Transition {from_state} -> {to_state} exceeded "
                    f"max occurrences ({t.max_occurrences})"
                ),
            }

    # Evaluate guards
    guard_results: dict[str, bool] = {}
    if t.guards:
        passed, guard_results, reason = evaluate_guards(t.guards, agent_id)
        if not passed:
            return {"ok": False, "reason": reason, "guard_results": guard_results}

    # Execute: update state
    agent.current_state = to_state
    save_agent_state(agent)
    _invalidate_agent_cache(agent_id)

    # Log
    log_transition(
        agent_id, machine.name, from_state, to_state, trigger, guard_results or None
    )

    return {"ok": True, "reason": "", "from_state": from_state, "to_state": to_state}


def do_system_transition(to_state: str, trigger: str) -> dict:
    """Validate and execute a system-level state transition."""
    system = get_system_state()
    if not system:
        return {"ok": False, "reason": "System state not found"}

    machine = load_machine("system")
    if not machine:
        return {"ok": False, "reason": "System machine not found"}

    from_state = system.current_state

    matching = [
        t
        for t in machine.transitions
        if t.from_state == from_state
        and t.to_state == to_state
        and t.trigger == trigger
    ]
    if not matching:
        return {
            "ok": False,
            "reason": f"No valid system transition: {from_state} -> {to_state} (trigger: {trigger})",
        }

    t = matching[0]

    if t.max_occurrences is not None:
        count = count_transition_occurrences("system", from_state, to_state)
        if count >= t.max_occurrences:
            return {
                "ok": False,
                "reason": f"System transition {from_state} -> {to_state} exceeded max ({t.max_occurrences})",
            }

    # Evaluate guards
    guard_results: dict[str, bool] = {}
    if t.guards:
        passed, guard_results, reason = evaluate_guards(t.guards, "system")
        if not passed:
            return {"ok": False, "reason": reason, "guard_results": guard_results}

    # Execute
    # Close previous phase history entry
    if system.phase_history:
        last = system.phase_history[-1]
        if last.exited_at is None:
            last.exited_at = datetime.now(timezone.utc)

    # Add new entry
    system.phase_history.append(
        PhaseHistoryEntry(state=to_state, entered_at=datetime.now(timezone.utc))  # pyright: ignore[reportArgumentType]
    )
    system.current_state = to_state  # pyright: ignore[reportAttributeAccessIssue]
    save_system_state(system)

    log_transition(
        "system", "system", from_state, to_state, trigger, guard_results or None
    )

    return {"ok": True, "reason": "", "from_state": from_state, "to_state": to_state}


# ---------------------------------------------------------------------------
# Auto-transition engine (with ambiguity detection)
# ---------------------------------------------------------------------------
def _try_auto_transition(agent_id: str) -> dict | None:
    """Try to auto-fire a mechanical transition.

    Evaluates ALL outgoing transitions with guards (non-user-approval).
    - 0 pass: no transition
    - 1 pass: fire it
    - >1 pass: ambiguity error, NO transition fired

    Returns transition result dict or None.
    """
    agent = get_agent_state(agent_id)
    if not agent:
        return None

    machine = resolve_machine(agent.role, agent.current_state)
    if not machine:
        return None

    current = agent.current_state

    # Collect candidates: regular + universal, must have guards
    candidates = [
        t
        for t in machine.transitions
        if t.from_state == current and not t.requires_user_approval and t.guards
    ]
    for t in machine.universal_transitions:
        if not t.requires_user_approval and t.guards:
            candidates.append(t)

    # Evaluate ALL candidates, collect those that pass
    passing = []
    for t in candidates:
        if t.max_occurrences is not None:
            count = count_transition_occurrences(agent_id, current, t.to_state)
            if count >= t.max_occurrences:
                continue

        passed, guard_results, _ = evaluate_guards(t.guards, agent_id)
        if passed:
            passing.append((t, guard_results))

    # Ambiguity detection
    if len(passing) == 0:
        return None

    if len(passing) > 1:
        targets = [f"{t.to_state} (trigger={t.trigger})" for t, _ in passing]
        _log.error(
            "AMBIGUITY: %d transitions pass from %s for agent %s: %s — "
            "firing NONE. Fix state machine guards to be mutually exclusive.",
            len(passing),
            current,
            agent_id,
            ", ".join(targets),
        )
        return None

    # Exactly 1 passes — fire it
    t, guard_results = passing[0]
    agent.current_state = t.to_state
    save_agent_state(agent)
    _invalidate_agent_cache(agent_id)

    log_transition(
        agent_id,
        machine.name,
        current,
        t.to_state,
        t.trigger,
        guard_results or None,
    )

    return {
        "ok": True,
        "from_state": current,
        "to_state": t.to_state,
        "trigger": t.trigger,
    }


# ---------------------------------------------------------------------------
# Session log auto-population
# ---------------------------------------------------------------------------
def _auto_populate_session_log(
    agent_id: str, tool: str, file_path: str, current_state: str
) -> None:
    """Update mechanical fields in the active session log."""
    try:
        project_dir = _project_dir()
        logs_dir = project_dir / ".claude" / "logs"
        if not logs_dir.exists():
            return

        # Find active session log
        session_log_path = None
        for lf in sorted(
            logs_dir.glob("*-log.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            try:
                data = json.loads(lf.read_text())
                if data.get("status") == "in_progress":
                    session_log_path = lf
                    break
            except Exception:
                continue

        if not session_log_path:
            return

        # Read context_usage_pct from cache
        cached = _sm._agent_state_cache.get(agent_id, {})
        ctx_pct = cached.get("context_usage_pct", 0)

        def _update(data: dict) -> dict:
            now = datetime.now(timezone.utc).isoformat()
            data["last_updated"] = now

            # state_history
            history = data.setdefault("state_history", [])
            if not history or history[-1].get("state") != current_state:
                history.append({"state": current_state, "timestamp": now})

            # files_modified
            if tool in ("Write", "Edit") and file_path:
                files = data.setdefault("files_modified", [])
                if file_path not in files:
                    files.append(file_path)

            # context_usage_pct
            data["context_usage_pct"] = ctx_pct

            # quality_gate
            gate = _get_gate_result()
            if gate:
                data["quality_gate"] = gate

            return data

        locked_read_modify_write(str(session_log_path), _update)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# handle_post_tool — consolidated post-tool processing (Chunk 5)
# ---------------------------------------------------------------------------
def handle_post_tool(
    agent_id: str, tool: str, tool_input: dict, tool_output_summary: str
) -> dict:
    """Post-tool processing — consolidated flow per Chunk 5 of hardening plan.

    Flow:
    1. Run post-action validators (blocking — sets pending_validation_error)
    2. If tool == Think and pending_critical_annotation → validate_think() synchronously
    3. Try auto-transition (ambiguity detection: fire only if exactly 1 passes)
    4. Read and deliver annotations (inject_context)
    5. Context pressure / async writes (unchanged)

    Returns:
        {"feedback": [...], "inject_context": "..." | null, "transition": {...} | null}
    """
    feedback: list[str] = []
    inject_context: list[str] = []
    transition_result: dict | None = None
    async_writes: list[Callable[[], None]] = []

    file_path = tool_input.get("file_path") or tool_input.get("path") or ""

    # Load agent and machine
    agent = get_agent_state(agent_id)
    if not agent:
        return {"feedback": [], "inject_context": None, "transition": None}

    # Update session liveness
    from scripts.daemon.state_manager import touch_session
    touch_session(agent_id)

    machine = resolve_machine(agent.role, agent.current_state)
    state_def: StateDefinition | None = None
    if machine:
        state_def = next(
            (s for s in machine.states if s.name == agent.current_state), None
        )

    # -----------------------------------------------------------------------
    # Step 1: Post-action validators (BLOCKING)
    # -----------------------------------------------------------------------
    if state_def and state_def.post_actions:
        validator_names: list[str] = []
        validator_names.extend(state_def.post_actions.get(tool, []))
        validator_names.extend(state_def.post_actions.get("*", []))
        validator_errors: list[str] = []
        for vname in validator_names:
            vfn = VALIDATOR_REGISTRY.get(vname)
            if vfn:
                validator_errors.extend(vfn(file_path, tool_input, tool_output_summary))

        if validator_errors:
            # Set pending_validation_error to block mutations
            feedback.extend(validator_errors)
            error_payload = {
                "validator_errors": validator_errors,
                "file_path": file_path,
                "tool": tool,
                "message": "; ".join(validator_errors),
            }
            try:
                path = _sm.AGENTS_DIR / f"{agent_id}.json"

                def _set_verr(data: dict) -> dict:
                    data["pending_validation_error"] = error_payload
                    return data

                locked_read_modify_write(path, _set_verr)
                _invalidate_agent_cache(agent_id)
            except Exception:
                pass
        else:
            # All validators passed — clear pending_validation_error if it exists
            cached = _get_agent_state_cached(agent_id)
            if cached and cached.get("pending_validation_error"):
                try:
                    path = _sm.AGENTS_DIR / f"{agent_id}.json"

                    def _clear_verr(data: dict) -> dict:
                        data["pending_validation_error"] = None
                        return data

                    locked_read_modify_write(path, _clear_verr)
                    _invalidate_agent_cache(agent_id)
                    feedback.append(
                        "Validation error cleared. Mutation tools unblocked."
                    )
                except Exception:
                    pass

    # -----------------------------------------------------------------------
    # Step 2: Think validation (SYNCHRONOUS, BLOCKING)
    # -----------------------------------------------------------------------
    if tool == "Think" or tool.endswith("__think"):
        cached = _get_agent_state_cached(agent_id)
        pending = (cached or {}).get("pending_critical_annotation")
        if pending:
            # Validate think output synchronously
            if machine and agent:
                result = validate_think(
                    agent_id, agent.current_state, machine, tool_output_summary
                )

                # Override chosen from structured tool_input (MCP provides it directly)
                input_chosen = tool_input.get("chosen", "").strip().upper()
                if input_chosen:
                    result["chosen"] = input_chosen
                    result["complete"] = True
                    result["issues"] = []
                elif tool.endswith("__think"):
                    # MCP pacing think — no decision, but still a valid deliberation
                    result["complete"] = True
                    result["issues"] = []

                if result["complete"]:
                    # Store CHOSEN and state in agent state, clear annotation
                    chosen = result.get("chosen")
                    current_st = agent.current_state
                    try:
                        path = _sm.AGENTS_DIR / f"{agent_id}.json"

                        def _store_chosen(data: dict) -> dict:
                            data["pending_critical_annotation"] = None
                            if chosen:
                                data["last_think_chosen"] = chosen
                            data["last_think_state"] = current_st
                            return data

                        locked_read_modify_write(path, _store_chosen)
                        _invalidate_agent_cache(agent_id)
                    except Exception:
                        pass

                    # No inject_context — agent already saw MCP response
                else:
                    # Incomplete — annotation stays, mutations stay blocked
                    issues = result.get("issues", [])
                    feedback.append(
                        f"Think incomplete: {', '.join(issues)}. "
                        "Address missing items and use Think again."
                    )

    # -----------------------------------------------------------------------
    # Step 3: Auto-transition (ambiguity detection)
    # -----------------------------------------------------------------------
    transition_result = _try_auto_transition(agent_id)
    if transition_result and machine:
        new_state = transition_result["to_state"]
        new_state_def = next((s for s in machine.states if s.name == new_state), None)
        if new_state_def and new_state_def.think_on_exit and new_state_def.think_prompt:
            _emit_think_annotation(agent_id, new_state_def)
        # Re-load agent with new state for session log
        agent = get_agent_state(agent_id) or agent

    # -----------------------------------------------------------------------
    # Step 3.5: Non-blocking message escalation on state transition
    # -----------------------------------------------------------------------
    if transition_result:
        new_state = transition_result["to_state"]
        from scripts.daemon.messaging import check_escalation, escalate_to_blocking
        escalation_ids = check_escalation(agent_id, new_state)
        if escalation_ids:
            # Escalate first pending message only — subsequent will escalate on next transition
            escalate_to_blocking(agent_id, escalation_ids[0])

    # -----------------------------------------------------------------------
    # Step 4: Annotation reading (deliver new annotations)
    # -----------------------------------------------------------------------
    ann_file = _sm.ANNOTATIONS_DIR / f"{agent_id}.jsonl"
    if ann_file.exists():
        try:
            collected_lines: list[str] = []
            critical_annotations: list[dict] = []

            def _ack_annotations(entries: list[dict]) -> list[dict]:
                unacked = [e for e in entries if not e.get("acknowledged", False)]
                if not unacked:
                    return entries

                priority_order = {"critical": 0, "normal": 1, "fyi": 2}
                unacked.sort(
                    key=lambda e: priority_order.get(e.get("priority", "fyi"), 3)
                )

                now_iso = datetime.now(timezone.utc).isoformat()
                for entry in unacked:
                    priority = entry.get("priority", "fyi").upper()
                    message = entry.get("message", "")
                    source = entry.get("from_agent") or entry.get("source", "system")

                    if priority == "CRITICAL":
                        ann_id = (
                            entry.get("annotation_id")
                            or f"ann-{hashlib.sha256((message + now_iso).encode()).hexdigest()[:8]}"
                        )
                        entry["annotation_id"] = ann_id
                        collected_lines.append(
                            f"[ANNOTATION CRITICAL] [{ann_id}] ({source}) {message}"
                        )
                        critical_annotations.append(
                            {"annotation_id": ann_id, "message": message}
                        )
                    else:
                        collected_lines.append(
                            f"[ANNOTATION {priority}] ({source}) {message}"
                        )

                    entry["acknowledged"] = True
                    entry["acknowledged_at"] = now_iso

                return entries

            locked_read_modify_write_jsonl(ann_file, _ack_annotations)

            if critical_annotations:
                _set_pending_critical(agent_id, critical_annotations[-1])

            if collected_lines:
                inject_context.append("\n".join(collected_lines))
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Step 5: Context pressure (every 10th call)
    # -----------------------------------------------------------------------
    call_count = _post_tool_call_count.get(agent_id, 0) + 1
    _post_tool_call_count[agent_id] = call_count

    if call_count % 10 == 0:
        try:
            status = check_context_usage(agent_id)
            if status["level"] != "normal":
                level = status["level"]
                sent = _sent_context_warnings.get(agent_id, set())
                if level not in sent:
                    feedback.append(format_context_warning(status, agent.role))
                    _sent_context_warnings.setdefault(agent_id, set()).add(level)

                    if level == "critical":
                        _set_pending_handoff(agent_id)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Step 6: Async writes (background thread)
    # -----------------------------------------------------------------------
    if tool.endswith("__think"):

        def _log_decision() -> None:
            try:
                from hooks.utils.event_logger import emit_decision_logged

                desc = tool_input.get("description", "unspecified")
                emit_decision_logged(agent_id, desc)
            except Exception:
                pass

        async_writes.append(_log_decision)

    if tool == "SendMessage":

        def _log_message() -> None:
            try:
                from hooks.utils.event_logger import emit_message_sent

                to_agent = tool_input.get(
                    "recipient", tool_input.get("target_agent_id", "unknown")
                )
                msg_type = tool_input.get("type", "message")
                emit_message_sent(agent_id, to_agent, msg_type)
            except Exception:
                pass

        async_writes.append(_log_message)

    if tool in ("Write", "Edit") and file_path:

        def _track_file() -> None:
            try:
                from hooks.utils.event_logger import emit_file_written

                emit_file_written(file_path, agent_id)
            except Exception:
                pass

        async_writes.append(_track_file)

    # Session log auto-population (async)
    current_state = agent.current_state

    def _update_log() -> None:
        _auto_populate_session_log(agent_id, tool, file_path, current_state)

    async_writes.append(_update_log)

    if async_writes:
        _async_write(async_writes)

    return {
        "feedback": feedback,
        "inject_context": "\n".join(inject_context) if inject_context else None,
        "transition": transition_result,
    }
