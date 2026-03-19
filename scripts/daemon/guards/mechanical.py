"""Mechanical guard implementations — file/artifact/test/gate guards.

Extracted from scripts/workflow_state.py (Chunk 2 of daemon hardening plan).
All guards: original 19 + 94 new guards added in Chunk 3 (guard normalization).

Guards check objective, mechanically verifiable conditions:
- File/artifact existence on disk
- Agent state fields (read via raw dict from disk, not Pydantic model)
- System state fields
- Transition log counters
- Quality gate results

Runtime event flags (e.g., sub_agent_error_reported) are set by the daemon
using locked_read_modify_write on agent/system state JSON files. Guards read
them via _get_agent_state_dict()/_get_system_state_dict() which return raw dicts.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.daemon.guards import GuardFn, _GUARD_REGISTRY
from scripts.daemon.state_manager import (
    TRANSITION_LOG,
    _get_gate_result,
    _project_dir,
    count_transition_occurrences,
)


# ---------------------------------------------------------------------------
# File existence guards
# ---------------------------------------------------------------------------
def _guard_context_packets_exist(
    _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    """Check if codebase context packet exists."""
    return (_project_dir() / ".claude" / "context" / "_codebase.json").exists()


def _guard_plan_text_file_exists(
    _agent_id: str, _agent: dict | None, system: dict | None, _param: str | None = None
) -> bool:
    """Check if a plain-text plan file exists for the current feature."""
    if not system:
        return False
    feature = system.get("feature", "")
    if not feature:
        return False
    plans_dir = _project_dir() / ".claude" / "plans"
    return any(plans_dir.glob(f"{feature}*plan*.md")) or any(
        plans_dir.glob(f"{feature}*plan*.txt")
    )


def _guard_plan_json_valid(
    _agent_id: str, _agent: dict | None, system: dict | None, _param: str | None = None
) -> bool:
    """Check if a plan JSON file exists and parses correctly."""
    if not system:
        return False
    feature = system.get("feature", "")
    if not feature:
        return False
    plans_dir = _project_dir() / ".claude" / "plans"
    for f in plans_dir.glob(f"{feature}*plan*.json"):
        try:
            json.loads(f.read_text())
            return True
        except Exception:
            continue
    return False


def _guard_plan_json_file_exists(
    _agent_id: str, _agent: dict | None, system: dict | None, _param: str | None = None
) -> bool:
    """Check if any plan JSON file exists for the current feature."""
    return _guard_plan_json_valid(_agent_id, _agent, system, _param)


def _guard_test_files_exist(
    _agent_id: str, agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    """Check if test files exist for the agent's current task."""
    tests_dir = _project_dir() / "tests"
    if not tests_dir.exists():
        return False
    task = agent.get("task", "") if agent else ""
    if task:
        return any(tests_dir.rglob(f"*{task}*")) or any(tests_dir.rglob("test_*.py"))
    return any(tests_dir.rglob("test_*.py"))


def _guard_test_count_gt_zero(
    _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    """Check if there are any test files in the project."""
    tests_dir = _project_dir() / "tests"
    return tests_dir.exists() and any(tests_dir.rglob("test_*.py"))


# ---------------------------------------------------------------------------
# Quality gate guards
# ---------------------------------------------------------------------------
def _guard_pytest_exit_zero(
    _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    """Check if last quality gate shows tests passing."""
    gate = _get_gate_result()
    if not gate:
        return False
    return gate.get("tests") == "pass"


def _guard_pytest_exit_nonzero(
    _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    """Check if last quality gate shows tests failing."""
    gate = _get_gate_result()
    if not gate:
        return True  # If no gate result, assume tests haven't run yet (fail = expected for TDD RED)
    return gate.get("tests") == "fail"


def _guard_format_pass(
    _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    gate = _get_gate_result()
    return gate.get("format") == "pass" if gate else False


def _guard_lint_pass(
    _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    gate = _get_gate_result()
    return gate.get("lint") == "pass" if gate else False


def _guard_typecheck_pass(
    _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    gate = _get_gate_result()
    return gate.get("typecheck") == "pass" if gate else False


def _guard_tests_pass(
    _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    """Alias for pytest_exit_zero."""
    return _guard_pytest_exit_zero(_agent_id, _agent, _system, _param)


def _guard_gate_failures_exist(
    _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    """Check if any quality gate component failed."""
    gate = _get_gate_result()
    if not gate:
        return False
    return any(gate.get(k) == "fail" for k in ("format", "lint", "typecheck", "tests"))


# ---------------------------------------------------------------------------
# Agent/invariant guards
# ---------------------------------------------------------------------------
def _guard_all_invariants_pass(
    _agent_id: str, agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    """Check if all invariants pass in the agent's session log."""
    if not agent:
        return False
    task = agent.get("task", "")
    if not task:
        return True  # No task = no invariants to check
    logs_dir = _project_dir() / ".claude" / "logs"
    for log_file in logs_dir.glob("*-log.json"):
        try:
            data = json.loads(log_file.read_text())
            if data.get("task_id") == task:
                invariants = data.get("invariants_status", {})
                if not invariants:
                    return True
                return all(v.get("status") == "pass" for v in invariants.values())
        except Exception:
            continue
    return True  # No log found — permissive


def _guard_context_packet_file_exists(
    _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    """Check if any context packet JSON exists."""
    ctx_dir = _project_dir() / ".claude" / "context"
    return ctx_dir.exists() and any(ctx_dir.glob("*.json"))


def _guard_query_text_non_empty(
    _agent_id: str, agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    """Check that the agent has a non-empty task assignment (proxy for query text)."""
    if not agent:
        return False
    return bool(agent.get("task"))


def _guard_research_file_written(
    _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    """Check if any research result file exists."""
    research_dir = _project_dir() / ".claude" / "research"
    return research_dir.exists() and any(research_dir.glob("*.json"))


def _guard_scenario_files_exist(
    _agent_id: str, _agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    """Check if scenario test files exist."""
    scenarios_dir = _project_dir() / "tests" / "scenarios"
    return scenarios_dir.exists() and any(scenarios_dir.glob("test_*.py"))


# ---------------------------------------------------------------------------
# Conditional audit guards and escalation
# ---------------------------------------------------------------------------
def _load_plan_tasks(feature: str) -> list[dict]:
    """Load all tasks from the feature's plan JSON."""
    plans_dir = _project_dir() / ".claude" / "plans"
    for f in plans_dir.glob(f"{feature}*plan*.json"):
        try:
            plan = json.loads(f.read_text())
            return plan.get("tasks", plan.get("chunks", []))
        except Exception:
            continue
    return []


def _task_has_audit_approval(feature: str, task_id: str) -> bool:
    """Check if a specific task has an auditor APPROVED status."""
    logs_dir = _project_dir() / ".claude" / "logs"
    if not logs_dir.exists():
        return False
    # Look for audit log entries for this task
    for log_file in logs_dir.glob("*audit*log*.json"):
        try:
            data = json.loads(log_file.read_text())
            if data.get("task_id") == task_id and data.get("verdict") == "APPROVED":
                return True
        except Exception:
            continue
    # Also check session logs for audit approval markers
    for log_file in logs_dir.glob("*-log.json"):
        try:
            data = json.loads(log_file.read_text())
            if data.get("task_id") == task_id:
                audit = data.get("audit_status", {})
                if audit.get("verdict") == "APPROVED":
                    return True
        except Exception:
            continue
    return False


def _get_effective_review_level(feature: str, task_id: str, assigned_level: str) -> str:
    """Determine effective review_level, escalating based on session log signals."""
    if assigned_level == "full":
        return "full"

    logs_dir = _project_dir() / ".claude" / "logs"
    if not logs_dir.exists():
        return assigned_level

    for log_file in logs_dir.glob("*-log.json"):
        try:
            data = json.loads(log_file.read_text())
            if data.get("task_id") != task_id:
                continue

            state_history = data.get("state_history", [])
            if not state_history:
                continue

            tdd_green_to_impl = 0
            gate_to_impl = 0
            has_scrap = False

            for i, entry in enumerate(state_history):
                state = entry.get("state", "")
                if state == "SCRAP_RETRY":
                    has_scrap = True
                if i > 0:
                    prev_state = state_history[i - 1].get("state", "")
                    if prev_state == "TDD_GREEN" and state == "IMPLEMENTATION":
                        tdd_green_to_impl += 1
                    if prev_state == "QUALITY_GATE" and state == "IMPLEMENTATION":
                        gate_to_impl += 1

            if has_scrap or tdd_green_to_impl >= 3 or gate_to_impl >= 1:
                return "full"
        except Exception:
            continue

    return assigned_level


def _guard_all_task_audits_resolved_or_skipped(
    _agent_id: str, _agent: dict | None, system: dict | None, _param: str | None = None
) -> bool:
    """Check if all task audits are resolved based on audit_mode and review_level."""
    if not system:
        return False
    feature = system.get("feature", "")
    if not feature:
        return False

    tasks = _load_plan_tasks(feature)
    if not tasks:
        return True  # No tasks = nothing to audit

    # Load audit_mode from preferences
    prefs_file = Path.home() / ".claude" / "state" / "preferences.json"
    audit_mode = "universal"  # default
    if prefs_file.exists():
        try:
            prefs = json.loads(prefs_file.read_text())
            global_prefs = prefs.get("global", {})
            mode_pref = global_prefs.get("audit_mode", {})
            if isinstance(mode_pref, dict):
                audit_mode = mode_pref.get("value", "universal")
            elif isinstance(mode_pref, str):
                audit_mode = mode_pref
        except Exception:
            pass

    for task in tasks:
        task_id = task.get("id", "")
        if not task_id:
            continue

        if audit_mode == "universal":
            if not _task_has_audit_approval(feature, task_id):
                return False
        else:
            assigned_level = task.get("review_level", "full")
            effective_level = _get_effective_review_level(
                feature, task_id, assigned_level
            )

            if effective_level == "full":
                if not _task_has_audit_approval(feature, task_id):
                    return False
            elif effective_level == "light":
                gate = _get_gate_result()
                if not gate or any(
                    gate.get(k) == "fail"
                    for k in ("format", "lint", "typecheck", "tests")
                ):
                    return False
            # phase = pass-through, no check needed

    return True


# ---------------------------------------------------------------------------
# Guard factory helpers — reduce boilerplate for common patterns
# ---------------------------------------------------------------------------
def _agent_flag(field: str) -> GuardFn:
    """Create a guard that checks if agent_state[field] is truthy.

    The daemon sets these fields via locked_read_modify_write on the agent
    JSON file. Guards read them from the raw dict.
    """

    def _guard(
        _agent_id: str,
        agent: dict | None,
        _system: dict | None,
        _param: str | None = None,
    ) -> bool:
        return bool(agent.get(field)) if agent else False

    _guard.__name__ = f"_guard_{field}"
    _guard.__doc__ = f"Check agent state field '{field}' is truthy."
    return _guard


def _neg_agent_flag(field: str) -> GuardFn:
    """Create a guard that checks if agent_state[field] is falsy."""

    def _guard(
        _agent_id: str,
        agent: dict | None,
        _system: dict | None,
        _param: str | None = None,
    ) -> bool:
        return not agent.get(field) if agent else True

    _guard.__name__ = f"_guard_not_{field}"
    _guard.__doc__ = f"Check agent state field '{field}' is falsy."
    return _guard


def _system_flag(field: str) -> GuardFn:
    """Create a guard that checks if system_state[field] is truthy."""

    def _guard(
        _agent_id: str,
        _agent: dict | None,
        system: dict | None,
        _param: str | None = None,
    ) -> bool:
        return bool(system.get(field)) if system else False

    _guard.__name__ = f"_guard_{field}"
    _guard.__doc__ = f"Check system state field '{field}' is truthy."
    return _guard



# ---------------------------------------------------------------------------
# Context & threshold guards
# ---------------------------------------------------------------------------
def _guard_context_usage_above_threshold(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    param: str | None = None,
) -> bool:
    """Check if context window usage is above threshold.

    Default threshold is 80%. Override via param: 'context_usage_above_threshold:90'.
    """
    if not agent:
        return False
    threshold = float(param) if param else 80.0
    return agent.get("context_usage_pct", 0) >= threshold


def _guard_confidence_above_threshold(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    param: str | None = None,
) -> bool:
    """Check if research confidence is above threshold (default 0.7)."""
    if not agent:
        return False
    threshold = float(param) if param else 0.7
    return agent.get("confidence_score", 0) >= threshold


def _guard_confidence_below_threshold(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    param: str | None = None,
) -> bool:
    """Check if research confidence is below threshold (default 0.7)."""
    if not agent:
        return False
    threshold = float(param) if param else 0.7
    return agent.get("confidence_score", 0) < threshold


# ---------------------------------------------------------------------------
# Retry & counter guards
# ---------------------------------------------------------------------------
def _guard_retry_count_under_limit(
    agent_id: str,
    agent: dict | None,
    _system: dict | None,
    param: str | None = None,
) -> bool:
    """Check retry count is under limit by reading transition log.

    Counts transitions FROM the agent's current state. Limit via param (default 3).
    """
    if not agent:
        return False
    limit = int(param) if param else 3
    current_state = agent.get("current_state", "")
    if not current_state or not TRANSITION_LOG.exists():
        return True
    # Count all transitions FROM current state for this entity
    count = 0
    try:
        for line in TRANSITION_LOG.read_text().splitlines():
            try:
                entry = json.loads(line)
                if (
                    entry.get("entity_id") == agent_id
                    and entry.get("from_state") == current_state
                ):
                    count += 1
            except json.JSONDecodeError:
                continue
    except OSError:
        return True
    return count < limit


def _guard_max_red_retries_exceeded(
    agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if RED_FAILED → TDD_RED transitions exceeded limit (2)."""
    return count_transition_occurrences(agent_id, "RED_FAILED", "TDD_RED") >= 2


def _guard_critique_cycles_exceeded(
    agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if FIXES_VERIFIED → CRITIQUE_SENT transitions exceeded limit (3)."""
    return (
        count_transition_occurrences(agent_id, "FIXES_VERIFIED", "CRITIQUE_SENT") >= 3
    )


def _guard_reinvestigation_cycles_exhausted(
    agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if RULING → INDEPENDENT_EXPLORATION transitions exhausted (≥2).

    Used by the forced-finalize fallback in auditor-phase: when the agent chose
    REINVESTIGATE but the 2-cycle cap is reached, the normal reinvestigation
    transition is skipped by max_occurrences. This guard enables the fallback
    transition to REPORT_WRITING.
    """
    return (
        count_transition_occurrences(
            agent_id, "RULING", "INDEPENDENT_EXPLORATION"
        )
        >= 2
    )


def _guard_retry_exhausted(
    agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if retry transitions from ERROR are exhausted.

    Negation of retry_count_under_limit — counts transitions FROM ERROR state.
    Used by forced-handoff fallback: when agent chose RETRY but budget is spent,
    this enables the fallback transition to HANDOFF.
    """
    if not agent:
        return False
    current_state = agent.get("current_state", "")
    if not current_state or not TRANSITION_LOG.exists():
        return False
    count = 0
    try:
        for line in TRANSITION_LOG.read_text().splitlines():
            try:
                entry = json.loads(line)
                if (
                    entry.get("entity_id") == agent_id
                    and entry.get("from_state") == current_state
                ):
                    count += 1
            except (json.JSONDecodeError, KeyError):
                continue
    except Exception:
        return False
    return count >= 1


def _guard_max_additional_searches_exceeded(
    agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if additional search cycles exceeded limit (2)."""
    return (
        count_transition_occurrences(
            agent_id, "ADDITIONAL_SEARCH", "ADDITIONAL_SEARCH"
        )
        >= 2
    )


# ---------------------------------------------------------------------------
# Remediation cycle guards
# ---------------------------------------------------------------------------
def _guard_remediation_cycle_under_ceiling(
    _agent_id: str,
    _agent: dict | None,
    system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if remediation cycle count is under ceiling (5)."""
    if not system:
        return False
    return system.get("remediation_cycle_count", 0) < 5


def _guard_remediation_stalled_or_ceiling(
    _agent_id: str,
    _agent: dict | None,
    system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if remediation has stalled or hit ceiling.

    Stalled = last two cycles had same or worse pass count.
    Ceiling = cycle count >= 5.
    """
    if not system:
        return False
    cycle_count = system.get("remediation_cycle_count", 0)
    if cycle_count >= 5:
        return True
    # Check stall by comparing pass counts in history
    history = system.get("remediation_pass_history", [])
    if len(history) >= 2:
        last = history[-1].get("pass_count", 0)
        prev = history[-2].get("pass_count", 0)
        if last <= prev:
            return True
    return False


def _guard_remediation_cycle_eq_1(
    _agent_id: str,
    _agent: dict | None,
    system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if current remediation cycle is 1."""
    if not system:
        return False
    return system.get("remediation_cycle_count", 0) == 1


def _guard_remediation_cycle_eq_2(
    _agent_id: str,
    _agent: dict | None,
    system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if current remediation cycle is 2."""
    if not system:
        return False
    return system.get("remediation_cycle_count", 0) == 2


def _guard_remediation_cycle_gte_3(
    _agent_id: str,
    _agent: dict | None,
    system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if current remediation cycle is 3 or higher."""
    if not system:
        return False
    return system.get("remediation_cycle_count", 0) >= 3


# ---------------------------------------------------------------------------
# Tester stall counter guards (progress-based remediation)
# ---------------------------------------------------------------------------
def _guard_consecutive_stall_count_lt_5(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if consecutive stall count is under escalation threshold (5)."""
    if not agent:
        return False
    return agent.get("consecutive_stall_count", 0) < 5


def _guard_consecutive_stall_count_gte_5(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if consecutive stall count has reached escalation threshold (5+)."""
    if not agent:
        return False
    return agent.get("consecutive_stall_count", 0) >= 5


# ---------------------------------------------------------------------------
# Invariant & quality guards (additional)
# ---------------------------------------------------------------------------
def _guard_invariant_failure_exists(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if any invariant has failed (inverse of all_invariants_pass)."""
    return not _guard_all_invariants_pass(_agent_id, agent, _system, _param)


# ---------------------------------------------------------------------------
# File/artifact existence guards (additional)
# ---------------------------------------------------------------------------
def _guard_report_file_exists(
    _agent_id: str,
    _agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if an audit report file exists in .claude/reports/."""
    reports_dir = _project_dir() / ".claude" / "reports"
    return reports_dir.exists() and any(reports_dir.glob("*.md")) or any(
        reports_dir.glob("*.json") if reports_dir.exists() else []
    )


def _guard_all_adherence_rulings_issued(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if adherence rulings file exists for the current audit."""
    logs_dir = _project_dir() / ".claude" / "logs"
    if not logs_dir.exists():
        return False
    return any(logs_dir.glob("*ruling*")) or any(logs_dir.glob("*adherence*"))


def _guard_scenario_report_written(
    _agent_id: str,
    _agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if a scenario test report file exists."""
    reports_dir = _project_dir() / ".claude" / "reports"
    if not reports_dir.exists():
        return False
    return any(reports_dir.glob("*scenario*"))


def _guard_audit_report_written(
    _agent_id: str,
    _agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if a phase audit report exists."""
    reports_dir = _project_dir() / ".claude" / "reports"
    if not reports_dir.exists():
        return False
    return any(reports_dir.glob("*audit*"))


def _guard_phase_breakdown_file_exists(
    _agent_id: str,
    _agent: dict | None,
    system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if phase breakdown file exists in plan-plaintext/."""
    plans_dir = _project_dir() / ".claude" / "plans" / "plan-plaintext"
    if not plans_dir.exists():
        return False
    return any(plans_dir.glob("*phase*"))


def _guard_task_breakdown_file_exists(
    _agent_id: str,
    _agent: dict | None,
    system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if task breakdown file exists in plan-plaintext/."""
    plans_dir = _project_dir() / ".claude" / "plans" / "plan-plaintext"
    if not plans_dir.exists():
        return False
    return any(plans_dir.glob("*task*"))


def _guard_test_plan_file_exists(
    _agent_id: str,
    _agent: dict | None,
    system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if test plan file exists in plan-plaintext/."""
    plans_dir = _project_dir() / ".claude" / "plans" / "plan-plaintext"
    if not plans_dir.exists():
        return False
    return any(plans_dir.glob("*test*"))


def _guard_all_tasks_have_metadata(
    _agent_id: str,
    _agent: dict | None,
    system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if all tasks in the plan have required metadata fields."""
    if not system:
        return False
    feature = system.get("feature", "")
    if not feature:
        return False
    tasks = _load_plan_tasks(feature)
    if not tasks:
        return True
    required_fields = {"review_level", "requires_research", "requires_exploration"}
    return all(
        required_fields.issubset(set(t.keys())) for t in tasks if t.get("id")
    )


# ---------------------------------------------------------------------------
# Packet/schema validation guards
# ---------------------------------------------------------------------------
def _guard_packet_schema_valid(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if the last-written context packet validates against schema.

    Daemon sets 'packet_schema_valid' flag after post-action validation.
    """
    return bool(agent.get("packet_schema_valid")) if agent else False


def _guard_partial_packet_metadata_consistent(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if partial packet metadata is consistent.

    For partial packets: meta.completeness == 'partial', meta.missing_sections populated.
    Daemon sets this flag after checking packet metadata.
    """
    return bool(agent.get("partial_packet_metadata_consistent", True)) if agent else False


def _guard_no_duplicate_packet_exists(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check that no duplicate context packet exists for the same query."""
    return not agent.get("duplicate_packet_detected") if agent else True


def _guard_duplicate_packet_exists(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check that a duplicate context packet exists (for merge path)."""
    return bool(agent.get("duplicate_packet_detected")) if agent else False


def _guard_schema_errors_exist(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if schema validation produced errors."""
    return bool(agent.get("schema_errors_exist")) if agent else False


def _guard_no_duplicate_research_exists(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check no duplicate research file exists for the same query."""
    return not agent.get("duplicate_research_detected") if agent else True


def _guard_duplicate_research_exists(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check a duplicate research file exists (for merge path)."""
    return bool(agent.get("duplicate_research_detected")) if agent else False


def _guard_schema_valid(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if the last schema validation passed (strategist JSON validation)."""
    return bool(agent.get("schema_valid")) if agent else False


def _guard_validation_errors_exist(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if validation errors exist (strategist JSON validation failed)."""
    return bool(agent.get("validation_errors_exist")) if agent else False


def _guard_bidirectional_diff_clean(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if bidirectional diff between plan text and JSON is clean."""
    return bool(agent.get("bidirectional_diff_clean")) if agent else False


def _guard_plan_json_matches_text(
    _agent_id: str,
    _agent: dict | None,
    system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if JSON plan matches text plan (system-level validation)."""
    return bool(system.get("plan_json_matches_text")) if system else False


# ---------------------------------------------------------------------------
# Coder lifecycle guards
# ---------------------------------------------------------------------------
def _guard_task_id_valid(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if agent has a valid, non-empty task ID."""
    return bool(agent.get("task")) if agent else False


def _guard_plan_chunk_exists(
    _agent_id: str,
    agent: dict | None,
    system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if the plan chunk for the agent's task exists."""
    if not agent or not system:
        return False
    task_id = agent.get("task", "")
    feature = system.get("feature", "")
    if not task_id or not feature:
        return False
    tasks = _load_plan_tasks(feature)
    return any(t.get("id") == task_id for t in tasks)


def _guard_worktree_path_exists(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if the coder's worktree directory exists on disk."""
    if not agent:
        return False
    wt = agent.get("worktree")
    if not wt:
        return False
    return Path(wt).exists()


def _guard_port_allocated(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if a port has been allocated for the coder (daemon-managed flag)."""
    return bool(agent.get("port_allocated")) if agent else False


def _guard_merge_exit_zero(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if last merge operation succeeded."""
    return bool(agent.get("merge_exit_zero")) if agent else False


def _guard_rerere_applied_or_manual_resolve(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if merge conflicts were resolved via rerere or manual resolution."""
    return bool(agent.get("rerere_applied_or_manual_resolve")) if agent else False


# ---------------------------------------------------------------------------
# Test output classification guards
# ---------------------------------------------------------------------------
def _guard_failures_are_assertion_or_import(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if test failures are assertion errors or import errors (expected in TDD RED)."""
    return bool(agent.get("failures_are_assertion_or_import")) if agent else False


def _guard_syntax_errors_or_no_tests_collected(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if tests had syntax errors or no tests were collected."""
    return bool(agent.get("syntax_errors_or_no_tests_collected")) if agent else False


def _guard_collect_only_exit_zero(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if pytest --collect-only exited successfully."""
    return bool(agent.get("collect_only_exit_zero")) if agent else False


def _guard_all_scenarios_discoverable(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if all scenario tests are discoverable by pytest."""
    return bool(agent.get("all_scenarios_discoverable")) if agent else False


def _guard_import_errors_or_collection_errors(
    _agent_id: str,
    agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if scenario collection had import or collection errors."""
    return bool(agent.get("import_errors_or_collection_errors")) if agent else False


# ---------------------------------------------------------------------------
# Phase & system lifecycle guards
# ---------------------------------------------------------------------------
def _guard_remaining_phases_exist(
    _agent_id: str,
    _agent: dict | None,
    system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if there are remaining phases to implement."""
    return bool(system.get("remaining_phases_exist")) if system else False


def _guard_no_remaining_phases(
    _agent_id: str,
    _agent: dict | None,
    system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if all phases are complete."""
    return not system.get("remaining_phases_exist") if system else False


def _guard_context_packets_insufficient(
    _agent_id: str,
    _agent: dict | None,
    _system: dict | None,
    _param: str | None = None,
) -> bool:
    """Check if context packets are insufficient (inverse of context_packets_exist)."""
    return not _guard_context_packets_exist(_agent_id, _agent, _system, _param)


# ---------------------------------------------------------------------------
# Guard registration
# ---------------------------------------------------------------------------
_GUARD_REGISTRY.update(
    {
        # --- Original 19 guards ---
        # File existence guards
        "context_packets_exist": _guard_context_packets_exist,
        "context_packet_file_exists": _guard_context_packet_file_exists,
        "plan_text_file_exists": _guard_plan_text_file_exists,
        "plan_json_valid": _guard_plan_json_valid,
        "plan_json_file_exists": _guard_plan_json_file_exists,
        "test_files_exist": _guard_test_files_exist,
        "test_count_gt_zero": _guard_test_count_gt_zero,
        "research_file_written": _guard_research_file_written,
        "scenario_files_exist": _guard_scenario_files_exist,
        # Quality gate guards
        "pytest_exit_zero": _guard_pytest_exit_zero,
        "pytest_exit_nonzero": _guard_pytest_exit_nonzero,
        "format_pass": _guard_format_pass,
        "lint_pass": _guard_lint_pass,
        "typecheck_pass": _guard_typecheck_pass,
        "tests_pass": _guard_tests_pass,
        "gate_failures_exist": _guard_gate_failures_exist,
        "all_invariants_pass": _guard_all_invariants_pass,
        # Agent state guards
        "query_text_non_empty": _guard_query_text_non_empty,
        # Conditional audit guard
        "all_task_audits_resolved_or_skipped": _guard_all_task_audits_resolved_or_skipped,
        # --- New guards (Chunk 3: Guard Normalization) ---
        # Context & threshold
        "context_usage_above_threshold": _guard_context_usage_above_threshold,
        "confidence_above_threshold": _guard_confidence_above_threshold,
        "confidence_below_threshold": _guard_confidence_below_threshold,
        # Retry & counter
        "retry_count_under_limit": _guard_retry_count_under_limit,
        "max_red_retries_exceeded": _guard_max_red_retries_exceeded,
        "critique_cycles_exceeded": _guard_critique_cycles_exceeded,
        "reinvestigation_cycles_exhausted": _guard_reinvestigation_cycles_exhausted,
        "retry_exhausted": _guard_retry_exhausted,
        "max_additional_searches_exceeded": _guard_max_additional_searches_exceeded,
        # Remediation cycle
        "remediation_cycle_under_ceiling": _guard_remediation_cycle_under_ceiling,
        "remediation_stalled_or_ceiling": _guard_remediation_stalled_or_ceiling,
        "remediation_cycle_eq_1": _guard_remediation_cycle_eq_1,
        "remediation_cycle_eq_2": _guard_remediation_cycle_eq_2,
        "remediation_cycle_gte_3": _guard_remediation_cycle_gte_3,
        # Invariant (additional)
        "invariant_failure_exists": _guard_invariant_failure_exists,
        # File/artifact existence (additional)
        "report_file_exists": _guard_report_file_exists,
        "all_adherence_rulings_issued": _guard_all_adherence_rulings_issued,
        "scenario_report_written": _guard_scenario_report_written,
        "audit_report_written": _guard_audit_report_written,
        "phase_breakdown_file_exists": _guard_phase_breakdown_file_exists,
        "task_breakdown_file_exists": _guard_task_breakdown_file_exists,
        "test_plan_file_exists": _guard_test_plan_file_exists,
        "all_tasks_have_metadata": _guard_all_tasks_have_metadata,
        # Packet/schema validation
        "packet_schema_valid": _guard_packet_schema_valid,
        "partial_packet_metadata_consistent": _guard_partial_packet_metadata_consistent,
        "no_duplicate_packet_exists": _guard_no_duplicate_packet_exists,
        "duplicate_packet_exists": _guard_duplicate_packet_exists,
        "schema_errors_exist": _guard_schema_errors_exist,
        "no_duplicate_research_exists": _guard_no_duplicate_research_exists,
        "duplicate_research_exists": _guard_duplicate_research_exists,
        "schema_valid": _guard_schema_valid,
        "validation_errors_exist": _guard_validation_errors_exist,
        "bidirectional_diff_clean": _guard_bidirectional_diff_clean,
        "plan_json_matches_text": _guard_plan_json_matches_text,
        # Coder lifecycle
        "task_id_valid": _guard_task_id_valid,
        "plan_chunk_exists": _guard_plan_chunk_exists,
        "worktree_path_exists": _guard_worktree_path_exists,
        "port_allocated": _guard_port_allocated,
        "merge_exit_zero": _guard_merge_exit_zero,
        "rerere_applied_or_manual_resolve": _guard_rerere_applied_or_manual_resolve,
        # Test output classification
        "failures_are_assertion_or_import": _guard_failures_are_assertion_or_import,
        "syntax_errors_or_no_tests_collected": _guard_syntax_errors_or_no_tests_collected,
        "collect_only_exit_zero": _guard_collect_only_exit_zero,
        "all_scenarios_discoverable": _guard_all_scenarios_discoverable,
        "import_errors_or_collection_errors": _guard_import_errors_or_collection_errors,
        # Phase & system lifecycle
        "remaining_phases_exist": _guard_remaining_phases_exist,
        "no_remaining_phases": _guard_no_remaining_phases,
        "context_packets_insufficient": _guard_context_packets_insufficient,
        # --- Agent state flag guards (daemon-managed runtime flags) ---
        "orchestrator_response_received": _agent_flag("orchestrator_response_received"),
        "clarification_wait_exceeded_or_orchestrator_cancelled": _agent_flag(
            "clarification_wait_exceeded_or_orchestrator_cancelled"
        ),
        "all_sub_agent_results_received": _agent_flag(
            "all_sub_agent_results_received"
        ),
        "sub_agent_error_reported": _agent_flag("sub_agent_error_reported"),
        "sub_agent_results_received": _agent_flag("sub_agent_results_received"),
        "partial_sub_agent_results_exist": _agent_flag(
            "partial_sub_agent_results_exist"
        ),
        "all_additional_sub_agents_complete": _agent_flag(
            "all_additional_sub_agents_complete"
        ),
        "requester_acknowledged_receipt": _agent_flag(
            "requester_acknowledged_receipt"
        ),
        "no_pending_queries": _neg_agent_flag("pending_queries"),
        "exploration_queries_sent_or_skipped": _agent_flag(
            "exploration_queries_sent_or_skipped"
        ),
        "context_packets_loaded": _agent_flag("context_packets_loaded"),
        "context_wait_exceeded": _agent_flag("context_wait_exceeded"),
        "auditor_critique_received": _agent_flag("auditor_critique_received"),
        "auditor_approval_received": _agent_flag("auditor_approval_received"),
        "coder_notified_fixes_ready": _agent_flag("coder_notified_fixes_ready"),
        "coder_files_readable": _agent_flag("coder_files_readable"),
        "coder_quality_gate_passed": _agent_flag("coder_quality_gate_passed"),
        "plan_chunk_loaded": _agent_flag("plan_chunk_loaded"),
        "design_doc_loaded": _agent_flag("design_doc_loaded"),
        "codebase_context_loaded": _agent_flag("codebase_context_loaded"),
        "plan_loaded": _agent_flag("plan_loaded"),
        "user_feedback_received": _agent_flag("user_feedback_received"),
        "confidence_scores_assigned": _agent_flag("confidence_scores_assigned"),
        "alternative_strategy_exists": _agent_flag("alternative_strategy_exists"),
        "no_alternative_strategy_exists": _neg_agent_flag(
            "alternative_strategy_exists"
        ),
        "alternative_search_tool_identified": _agent_flag(
            "alternative_search_tool_identified"
        ),
        "fallback_query_adjusted": _agent_flag("fallback_query_adjusted"),
        "all_search_strategies_exhausted": _agent_flag(
            "all_search_strategies_exhausted"
        ),
        "task_logs_loaded": _agent_flag("task_logs_loaded"),
        "test_results_loaded": _agent_flag("test_results_loaded"),
        "scenario_reports_loaded": _agent_flag("scenario_reports_loaded"),
        # Auditor-specific flags (daemon-set when coder SM reaches terminal / artifact load fails)
        "coder_session_terminated": _agent_flag("coder_session_terminated"),
        "coder_fixes_not_submitted": _neg_agent_flag("coder_notified_fixes_ready"),
        "artifact_load_error_reported": _agent_flag("artifact_load_error_reported"),
        # --- System state flag guards (daemon-managed runtime flags) ---
        "all_coder_tasks_complete": _system_flag("all_coder_tasks_complete"),
        "all_merges_successful": _system_flag("all_merges_successful"),
        "tasks_assigned_to_coders": _system_flag("tasks_assigned_to_coders"),
        "tester_scenarios_ready": _system_flag("tester_scenarios_ready"),
        "all_scenarios_passed": _system_flag("all_scenarios_passed"),
        "scenario_failures_exist": _system_flag("scenario_failures_exist"),
        "fixes_applied": _system_flag("fixes_applied"),
        "strategist_error_reported": _system_flag("strategist_error_reported"),
        "auditor_ruling_issued": _system_flag("auditor_ruling_issued"),
        "ruling_requires_coder_fixes": _system_flag("ruling_requires_coder_fixes"),
        "ruling_accepts_implementation": _system_flag("ruling_accepts_implementation"),
        "hardening_report_approved": _system_flag("hardening_report_approved"),
        "hardening_failures_exist": _system_flag("hardening_failures_exist"),
        "phase_implementation_complete": _system_flag("phase_implementation_complete"),
        "all_coder_merges_complete": _system_flag("all_coder_merges_complete"),
        "remediation_fixes_merged": _system_flag("remediation_fixes_merged"),
        "all_scenarios_passed_or_arbitrated": _system_flag(
            "all_scenarios_passed_or_arbitrated"
        ),
        # --- Tester SM guards (hardening T1-T6) ---
        "consecutive_stall_count_lt_5": _guard_consecutive_stall_count_lt_5,
        "consecutive_stall_count_gte_5": _guard_consecutive_stall_count_gte_5,
        "execution_error_reported": _agent_flag("execution_error_reported"),
        "not_execution_error_reported": _neg_agent_flag("execution_error_reported"),
        "scenario_update_required": _agent_flag("scenario_update_required"),
        "not_scenario_update_required": _neg_agent_flag("scenario_update_required"),
        "scenario_pytest_completed": _agent_flag("scenario_pytest_completed"),
        # --- Coder hardening guards (C1-C7) ---
        "approach_concern_flagged": _agent_flag("approach_concern_flagged"),
        "merge_conflicts_exist": _agent_flag("merge_conflicts_exist"),
        "all_conflicts_resolved": _agent_flag("all_conflicts_resolved"),
        "tests_not_yet_written": _neg_agent_flag("tests_already_written"),
        "tests_already_written": _agent_flag("tests_already_written"),
        "auditor_found_test_issues": _agent_flag("auditor_found_test_issues"),
    }
)
