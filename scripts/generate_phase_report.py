#!/usr/bin/env python3
"""Phase report generator — automated collection from all log sources.

Collects state transitions, decisions, messages, session logs, quality gate
results, and scenario results for a specific phase and aggregates them into
a single structured JSON report.

Usage:
    python scripts/generate_phase_report.py <phase-id> [options]

    # Minimal — infers feature from workflow state
    python scripts/generate_phase_report.py p1

    # Explicit feature and output path
    python scripts/generate_phase_report.py p1 --feature lpr-tracking --output report.json

    # Custom .claude root (for testing)
    python scripts/generate_phase_report.py p1 --claude-home /tmp/test-claude

Output: ~/.claude/reports/{phase-id}-report.json (or --output path)

Each data source is collected independently with graceful degradation —
missing logs or unwritten schemas produce empty sections, not crashes.

Design doc ref: "Phase Reports" section, lines 608-612.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root for schema imports
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Report model (inline until phase-report schema lands in task #11)
# ---------------------------------------------------------------------------
# Defined as typed dicts / plain dicts for now. When schemas/phase_report.py
# is written, this script can import and validate against it.


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _claude_home(override: str | None = None) -> Path:
    if override:
        return Path(override)
    return Path.home() / ".claude"


def _project_claude(project_root: Path) -> Path:
    return project_root / ".claude"


# ---------------------------------------------------------------------------
# Collectors — each returns a dict section for the report
# ---------------------------------------------------------------------------


def collect_phase_timing(claude_home: Path, phase_id: str) -> dict:
    """Extract phase start/end times from workflow state phase_history."""
    state_file = claude_home / "state" / "system.json"
    if not state_file.exists():
        return {
            "status": "no_workflow_state",
            "started_at": None,
            "ended_at": None,
            "duration_s": None,
        }

    try:
        state = json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {
            "status": "read_error",
            "started_at": None,
            "ended_at": None,
            "duration_s": None,
        }

    # Find PHASE_ACTIVE entry for this phase by scanning history
    # Phase history entries are ordered; PHASE_ACTIVE marks phase start
    history = state.get("phase_history", [])

    # We identify a phase's span as the entries from PHASE_ACTIVE through
    # PHASE_COMMITTED (or the last entry if phase is still in progress)
    phase_entries = _extract_phase_entries(history, phase_id, state.get("feature", ""))
    if not phase_entries:
        return {
            "status": "phase_not_found",
            "started_at": None,
            "ended_at": None,
            "duration_s": None,
        }

    started = phase_entries[0].get("entered_at")
    last = phase_entries[-1]
    ended = last.get("exited_at")

    duration_s = None
    if started and ended:
        try:
            t0 = datetime.fromisoformat(started)
            t1 = datetime.fromisoformat(ended)
            duration_s = round((t1 - t0).total_seconds(), 1)
        except (ValueError, TypeError):
            pass

    return {
        "status": "ok",
        "started_at": started,
        "ended_at": ended,
        "duration_s": duration_s,
        "states_traversed": [e.get("state") for e in phase_entries],
    }


def _extract_phase_entries(
    history: list[dict], phase_id: str, feature: str
) -> list[dict]:
    """Extract phase_history entries belonging to a specific phase.

    Phases are numbered (p1, p2, ...). The Nth PHASE_ACTIVE entry in
    history corresponds to phase pN. We collect all entries from that
    PHASE_ACTIVE through the next PHASE_COMMITTED (or end of history).
    """
    phase_num = _parse_phase_number(phase_id)
    if phase_num is None:
        return []

    active_count = 0
    collecting = False
    entries: list[dict] = []

    for entry in history:
        state = entry.get("state", "")

        if state == "PHASE_ACTIVE" and not collecting:
            active_count += 1
            if active_count == phase_num:
                collecting = True

        if collecting:
            entries.append(entry)
            if state == "PHASE_COMMITTED":
                break

    return entries


def _parse_phase_number(phase_id: str) -> int | None:
    """Parse 'p1' -> 1, 'p2' -> 2, etc."""
    if phase_id.startswith("p") and phase_id[1:].isdigit():
        return int(phase_id[1:])
    return None


def collect_phase_goals(project_claude: Path, feature: str, phase_id: str) -> dict:
    """Extract phase goals and tasks from the plan file."""
    plan_file = project_claude / "plans" / f"{feature}-plan.json"
    if not plan_file.exists():
        return {"status": "no_plan_file", "goals": [], "tasks": []}

    try:
        plan = json.loads(plan_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {"status": "read_error", "goals": [], "tasks": []}

    # Plans have phases as a list; find the matching phase
    phases = plan.get("phases", [])
    for phase in phases:
        pid = phase.get("id", "")
        if pid == phase_id:
            return {
                "status": "ok",
                "name": phase.get("name", ""),
                "goals": phase.get("goals", []),
                "tasks": [
                    {
                        "id": t.get("id", ""),
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                    }
                    for t in phase.get("tasks", [])
                ],
            }

    return {"status": "phase_not_in_plan", "goals": [], "tasks": []}


def collect_agent_summary(claude_home: Path, phase_id: str) -> dict:
    """Aggregate token usage and roles from agent state files for this phase."""
    agents_dir = claude_home / "state" / "agents"
    if not agents_dir.exists():
        return {"status": "no_agents_dir", "agents": [], "total_tokens": {}}

    agents: list[dict] = []
    total_input = 0
    total_output = 0

    for agent_file in sorted(agents_dir.glob("*.json")):
        try:
            agent = json.loads(agent_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        # Filter to agents that belong to this phase
        agent_phase = agent.get("phase")
        if agent_phase != phase_id:
            continue

        input_t = agent.get("input_tokens", 0)
        output_t = agent.get("output_tokens", 0)
        total_input += input_t
        total_output += output_t

        agents.append(
            {
                "id": agent.get("id", agent_file.stem),
                "role": agent.get("role", "unknown"),
                "model": agent.get("model", "unknown"),
                "status": agent.get("status", "unknown"),
                "input_tokens": input_t,
                "output_tokens": output_t,
                "context_usage_pct": agent.get("context_usage_pct", 0),
            }
        )

    return {
        "status": "ok" if agents else "no_agents_for_phase",
        "agents": agents,
        "total_tokens": {
            "input": total_input,
            "output": total_output,
            "total": total_input + total_output,
        },
    }


def collect_state_transitions(claude_home: Path, phase_timing: dict) -> dict:
    """Collect state transitions that occurred during this phase's time window."""
    log_file = claude_home / "logs" / "state-transitions.jsonl"
    if not log_file.exists():
        return {"status": "no_log_file", "count": 0, "transitions": []}

    started_at = phase_timing.get("started_at")
    ended_at = phase_timing.get("ended_at")
    t0 = _parse_iso(started_at)
    t1 = _parse_iso(ended_at)

    transitions: list[dict] = []
    try:
        for line in log_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = _parse_iso(entry.get("timestamp"))
            if not _in_time_window(ts, t0, t1):
                continue

            transitions.append(
                {
                    "timestamp": entry.get("timestamp"),
                    "entity_id": entry.get("entity_id"),
                    "from_state": entry.get("from_state"),
                    "to_state": entry.get("to_state"),
                    "trigger": entry.get("trigger"),
                }
            )
    except OSError:
        return {"status": "read_error", "count": 0, "transitions": []}

    return {
        "status": "ok",
        "count": len(transitions),
        "transitions": transitions,
    }


def collect_decisions(claude_home: Path, phase_timing: dict) -> dict:
    """Collect decisions from all agent decision logs within the phase window."""
    decisions_dir = claude_home / "logs" / "decisions"
    if not decisions_dir.exists():
        return {"status": "no_decisions_dir", "count": 0, "decisions": []}

    started_at = phase_timing.get("started_at")
    ended_at = phase_timing.get("ended_at")
    t0 = _parse_iso(started_at)
    t1 = _parse_iso(ended_at)

    decisions: list[dict] = []
    for log_file in sorted(decisions_dir.glob("*.jsonl")):
        try:
            for line in log_file.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = _parse_iso(entry.get("timestamp"))
                if not _in_time_window(ts, t0, t1):
                    continue

                decisions.append(
                    {
                        "timestamp": entry.get("timestamp"),
                        "agent_id": entry.get("agent_id"),
                        "decision_point": entry.get("decision_point"),
                        "thought": entry.get("thought"),
                        "chosen": entry.get("chosen"),
                        "mandatory": entry.get("mandatory", False),
                    }
                )
        except OSError:
            continue

    return {
        "status": "ok",
        "count": len(decisions),
        "decisions": decisions,
    }


def collect_messages(claude_home: Path, phase_timing: dict) -> dict:
    """Collect inter-agent messages from the message bus log within the phase window."""
    log_file = claude_home / "logs" / "message-bus.jsonl"
    if not log_file.exists():
        return {"status": "no_log_file", "count": 0, "messages": [], "by_type": {}}

    started_at = phase_timing.get("started_at")
    ended_at = phase_timing.get("ended_at")
    t0 = _parse_iso(started_at)
    t1 = _parse_iso(ended_at)

    messages: list[dict] = []
    by_type: dict[str, int] = {}

    try:
        for line in log_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = _parse_iso(entry.get("timestamp"))
            if not _in_time_window(ts, t0, t1):
                continue

            msg_type = entry.get("message_type", "unknown")
            by_type[msg_type] = by_type.get(msg_type, 0) + 1

            messages.append(
                {
                    "timestamp": entry.get("timestamp"),
                    "from_agent": entry.get("from_agent"),
                    "to_agent": entry.get("to_agent"),
                    "message_type": msg_type,
                    "summary": entry.get("summary"),
                }
            )
    except OSError:
        return {"status": "read_error", "count": 0, "messages": [], "by_type": {}}

    return {
        "status": "ok",
        "count": len(messages),
        "messages": messages,
        "by_type": by_type,
    }


def collect_session_logs(project_claude: Path, feature: str, phase_id: str) -> dict:
    """Collect session logs (test results, quality gates, deviations) for this phase."""
    logs_dir = project_claude / "logs"
    if not logs_dir.exists():
        return {
            "status": "no_logs_dir",
            "sessions": [],
            "test_summary": {},
            "quality_gate_aggregate": None,
        }

    sessions: list[dict] = []
    total_tests = 0
    total_passed = 0
    total_failed = 0
    total_skipped = 0
    all_deviations: list[dict] = []
    all_learning_signals: list[dict] = []
    gate_results: list[dict] = []

    # Session logs are named {feature}-{task}-log.json
    for log_file in sorted(logs_dir.glob(f"{feature}-*-log.json")):
        try:
            session = json.loads(log_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        # Filter to this phase
        session_phase = session.get("workflow_phase")
        if session_phase != phase_id:
            continue

        # Test results
        tests = session.get("tests_written", [])
        for t in tests:
            total_tests += 1
            status = t.get("status", "")
            if status == "pass":
                total_passed += 1
            elif status == "fail":
                total_failed += 1
            elif status == "skip":
                total_skipped += 1

        # Quality gate
        qg = session.get("quality_gate")
        if qg:
            gate_results.append(qg)

        # Deviations
        all_deviations.extend(session.get("deviations", []))

        # Learning signals
        all_learning_signals.extend(session.get("learning_signals", []))

        sessions.append(
            {
                "agent_id": session.get("agent_id"),
                "task_id": session.get("task_id"),
                "status": session.get("status"),
                "tests_count": len(tests),
                "tests_passing": sum(1 for t in tests if t.get("status") == "pass"),
                "quality_gate_passed": qg.get("passed") if qg else None,
                "deviations_count": len(session.get("deviations", [])),
            }
        )

    # Aggregate quality gate
    quality_gate_aggregate = None
    if gate_results:
        all_passed = all(g.get("passed", False) for g in gate_results)
        quality_gate_aggregate = {
            "all_passed": all_passed,
            "total_runs": len(gate_results),
            "format": _aggregate_gate_status(gate_results, "format"),
            "lint": _aggregate_gate_status(gate_results, "lint"),
            "typecheck": _aggregate_gate_status(gate_results, "typecheck"),
            "tests": _aggregate_gate_status(gate_results, "tests"),
            "security": _aggregate_gate_status(gate_results, "security"),
        }

    return {
        "status": "ok" if sessions else "no_sessions_for_phase",
        "sessions": sessions,
        "test_summary": {
            "total": total_tests,
            "passed": total_passed,
            "failed": total_failed,
            "skipped": total_skipped,
        },
        "quality_gate_aggregate": quality_gate_aggregate,
        "deviations": all_deviations,
        "learning_signals_count": len(all_learning_signals),
    }


def _aggregate_gate_status(gates: list[dict], check: str) -> str:
    """Aggregate a specific gate check across multiple runs."""
    statuses = [g.get(check, "not_run") for g in gates]
    if any(s == "fail" for s in statuses):
        return "fail"
    if all(s == "pass" for s in statuses):
        return "pass"
    return "mixed"


def collect_scenario_results(
    project_claude: Path, _feature: str, phase_id: str
) -> dict:
    """Collect scenario execution results (Pass D) for this phase.

    Placeholder — scenario_result schema is not yet written (task #11).
    """
    scenarios_dir = project_claude / "scenarios"
    if not scenarios_dir.exists():
        return {"status": "no_scenarios_dir", "scenarios": []}

    scenarios: list[dict] = []
    for result_file in sorted(scenarios_dir.glob("*.json")):
        try:
            result = json.loads(result_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        # Filter by phase (schema TBD — assume phase_id field exists)
        if result.get("phase_id") != phase_id:
            continue

        scenarios.append(
            {
                "file": result_file.name,
                "status": result.get("status", "unknown"),
                "summary": result.get("summary"),
            }
        )

    return {
        "status": "ok" if scenarios else "no_scenarios_for_phase",
        "scenarios": scenarios,
    }


def collect_audit_report(project_claude: Path, _feature: str, phase_id: str) -> dict:
    """Collect the auditor's phase review.

    Placeholder — left for the auditor agent to fill in. The script
    creates the section structure; the auditor writes the actual content.
    """
    audits_dir = project_claude / "audits"
    if not audits_dir.exists():
        return {"status": "placeholder", "auditor_review": None}

    # Look for audit report matching this phase
    for audit_file in sorted(audits_dir.glob("*.json")):
        try:
            audit = json.loads(audit_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        if audit.get("phase_id") == phase_id:
            return {
                "status": "ok",
                "auditor_review": audit,
            }

    return {"status": "placeholder", "auditor_review": None}


# ---------------------------------------------------------------------------
# Time utilities
# ---------------------------------------------------------------------------


def _parse_iso(ts: str | None) -> datetime | None:
    """Parse an ISO timestamp, returning None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _in_time_window(
    ts: datetime | None, t0: datetime | None, t1: datetime | None
) -> bool:
    """Check if a timestamp falls within [t0, t1].

    If t0 is None, accepts everything (no phase timing available).
    If t1 is None, accepts everything after t0 (phase still in progress).
    """
    if t0 is None:
        return True  # No timing info — include everything
    if ts is None:
        return False
    if ts < t0:
        return False
    if t1 is not None and ts > t1:
        return False
    return True


# ---------------------------------------------------------------------------
# Main report assembly
# ---------------------------------------------------------------------------


def generate_report(
    phase_id: str,
    feature: str | None = None,
    claude_home_override: str | None = None,
    project_root: str | None = None,
) -> dict:
    """Generate a complete phase report by collecting from all data sources."""
    claude_home = _claude_home(claude_home_override)
    proj_root = Path(project_root) if project_root else Path.cwd()
    project_claude = _project_claude(proj_root)

    # Resolve feature from workflow state if not provided
    if not feature:
        state_file = claude_home / "state" / "system.json"
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text())
                feature = str(state.get("feature", "unknown"))
            except (json.JSONDecodeError, OSError):
                feature = "unknown"
        else:
            feature = "unknown"

    resolved_feature: str = feature or "unknown"

    # Collect all sections
    timing = collect_phase_timing(claude_home, phase_id)
    goals = collect_phase_goals(project_claude, resolved_feature, phase_id)
    agents = collect_agent_summary(claude_home, phase_id)
    transitions = collect_state_transitions(claude_home, timing)
    decisions = collect_decisions(claude_home, timing)
    messages = collect_messages(claude_home, timing)
    sessions = collect_session_logs(project_claude, resolved_feature, phase_id)
    scenarios = collect_scenario_results(project_claude, resolved_feature, phase_id)
    audit = collect_audit_report(project_claude, resolved_feature, phase_id)

    report = {
        "report_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase_id": phase_id,
        "feature": resolved_feature,
        # Phase goals from plan
        "phase_goals": goals,
        # Timing
        "timing": timing,
        # Tasks completed (from session logs)
        "tasks_completed": sessions.get("sessions", []),
        # Test results (all passes aggregated)
        "test_results": sessions.get("test_summary", {}),
        # Quality gate aggregate
        "quality_gate": sessions.get("quality_gate_aggregate"),
        # Scenario results (Pass D)
        "scenario_results": scenarios,
        # Decisions made during phase
        "decisions": decisions,
        # Deviations from plan
        "deviations": sessions.get("deviations", []),
        # Inter-agent messages
        "message_summary": {
            "total_messages": messages.get("count", 0),
            "by_type": messages.get("by_type", {}),
        },
        # State transitions
        "state_transitions": {
            "total": transitions.get("count", 0),
            "transitions": transitions.get("transitions", []),
        },
        # Token/cost summary
        "token_summary": agents.get("total_tokens", {}),
        "agents": agents.get("agents", []),
        # Auditor phase review (placeholder — filled by auditor agent)
        "auditor_review": audit,
        # Learning signals count
        "learning_signals_count": sessions.get("learning_signals_count", 0),
    }

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a phase report from workflow logs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/generate_phase_report.py p1
    python scripts/generate_phase_report.py p1 --feature lpr-tracking
    python scripts/generate_phase_report.py p1 --output /tmp/report.json
    python scripts/generate_phase_report.py p2 --claude-home /tmp/test-claude
        """,
    )
    parser.add_argument(
        "phase_id",
        help="Phase identifier (e.g., p1, p2)",
    )
    parser.add_argument(
        "--feature",
        default=None,
        help="Feature name (inferred from workflow state if omitted)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output file path (default: ~/.claude/reports/{phase_id}-report.json)",
    )
    parser.add_argument(
        "--claude-home",
        default=None,
        help="Override ~/.claude root (for testing)",
    )
    parser.add_argument(
        "--project-root",
        default=None,
        help="Override project root (for .claude/ project files)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print report to stdout instead of writing to file",
    )

    args = parser.parse_args()

    # Validate phase_id format
    if _parse_phase_number(args.phase_id) is None:
        print(
            f"Error: Invalid phase_id '{args.phase_id}'. Expected format: p1, p2, ...",
            file=sys.stderr,
        )
        sys.exit(1)

    report = generate_report(
        phase_id=args.phase_id,
        feature=args.feature,
        claude_home_override=args.claude_home,
        project_root=args.project_root,
    )

    report_json = json.dumps(report, indent=2, default=str) + "\n"

    if args.stdout:
        print(report_json, end="")
        return

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = (
            _claude_home(args.claude_home) / "reports" / f"{args.phase_id}-report.json"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_json)
    print(f"Phase report written to: {output_path}")


if __name__ == "__main__":
    main()
