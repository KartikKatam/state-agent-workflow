#!/usr/bin/env python3
"""
SessionStart Hook: Loads context packets, queries memories, and detects team context.

Triggered when a new Claude Code session starts. Identifies relevant
context packets, queries memories for the current task, detects if running
as part of an Agent Team, and injects context into the system prompt.

Hook type: SessionStart
"""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add parent directory to path for utils import
sys.path.insert(0, str(Path(__file__).parent))

from utils.event_logger import emit, rotate_events_file


def get_hook_input() -> dict:
    """Read hook input from environment or stdin."""
    hook_data = os.environ.get("CLAUDE_HOOK_DATA")
    if hook_data:
        return json.loads(hook_data)

    if not sys.stdin.isatty():
        return json.load(sys.stdin)

    return {}


# ============================================
# Team Detection
# ============================================


def get_team_context() -> dict | None:
    """
    Detect if running as part of an Agent Team.

    Checks CLAUDE_CODE_TEAM_NAME env var set by Agent Teams infrastructure.
    Returns team info dict or None if not in a team.
    """
    team_name = os.environ.get("CLAUDE_CODE_TEAM_NAME")
    if not team_name:
        return None

    return {
        "team_name": team_name,
        "agent_name": os.environ.get("CLAUDE_CODE_AGENT_NAME", "unknown"),
        "agent_type": os.environ.get("CLAUDE_CODE_AGENT_TYPE", "unknown"),
    }


# ============================================
# Design Review Staleness Check
# ============================================


def check_design_review_staleness() -> list[str]:
    """
    Check if any design reviews are stale and should be re-run.

    Reads staleness threshold from settings.json (default: 24 hours).
    Only reports if stale reviews are found.
    """
    warnings = []
    stale_reviews = []

    # Get staleness threshold from project settings (default 24 hours)
    staleness_hours = 24
    settings_path = Path(".claude/settings.json")
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                settings = json.load(f)
                staleness_hours = settings.get("design_review", {}).get(
                    "staleness_hours", 24
                )
        except (json.JSONDecodeError, PermissionError, OSError):
            pass  # Use default

    # Check design review annotations
    context_dir = Path(".claude/context")
    if not context_dir.exists():
        return []

    for review_file in context_dir.glob("*-design-review.json"):
        try:
            with open(review_file) as f:
                review = json.load(f)

            reviewed_at = review.get("reviewed_at")
            if not reviewed_at:
                continue

            # Parse ISO timestamp (handle Z suffix and timezone)
            try:
                # Handle both "Z" suffix and "+00:00" format
                if reviewed_at.endswith("Z"):
                    reviewed_at = reviewed_at[:-1] + "+00:00"
                review_time = datetime.fromisoformat(reviewed_at)

                # Ensure timezone aware comparison
                now = datetime.now(UTC)
                if review_time.tzinfo is None:
                    review_time = review_time.replace(tzinfo=UTC)

                age_hours = (now - review_time).total_seconds() / 3600

                if age_hours > staleness_hours:
                    feature = review.get(
                        "feature", review_file.stem.replace("-design-review", "")
                    )
                    stale_reviews.append(
                        {
                            "feature": feature,
                            "age_hours": int(age_hours),
                            "path": str(review_file),
                        }
                    )
            except ValueError:
                # Invalid timestamp format - skip this review
                pass

        except (json.JSONDecodeError, PermissionError, OSError):
            pass  # Skip unreadable files

    if stale_reviews:
        warnings.append("\n## Stale Design Reviews")
        for review in stale_reviews:
            warnings.append(
                f"  - {review['feature']}: {review['age_hours']}h old (threshold: {staleness_hours}h)"
            )
        warnings.append("  Re-run design review if design docs have changed.")

    return warnings


# ============================================
# Per-Session Tracking Reset
# ============================================


def _write_ptc_session_id(session_id: str) -> None:
    """Write session ID for PTC container labeling and pulse cleanup."""
    if not session_id:
        return
    try:
        (Path.home() / ".claude" / ".ptc-session-id").write_text(session_id)
    except Exception:
        pass  # Non-critical — container labeling degrades gracefully


def _reset_session_tracking():
    """
    Clear per-session tracking files so warnings fire fresh.

    Called at session start. Removes all per-session tracking files
    from previous sessions (sent-context-warnings-*.json,
    recent-agent-writes-*.json, context-metrics-*.json).
    """
    temp_dir = Path(".claude/temp")
    if not temp_dir.exists():
        return

    patterns = [
        "sent-context-warnings-*.json",
        "recent-agent-writes-*.json",
    ]
    for pattern in patterns:
        for f in temp_dir.glob(pattern):
            f.unlink()

    # Also clean metrics files from ~/.claude/temp/ (written by statusline)
    home_temp = Path.home() / ".claude/temp"
    if home_temp.exists():
        for f in home_temp.glob("context-metrics-*.json"):
            f.unlink()


# ============================================
# Existing functions (unchanged)
# ============================================


def find_active_context() -> dict:
    """Find all relevant context files for this project."""
    context = {
        "codebase": None,
        "active_features": [],
        "active_plans": [],
        "memory_db": None,
        "project_name": Path.cwd().name,
    }

    # Codebase context
    codebase_path = Path(".claude/context/_codebase.json")
    if codebase_path.exists():
        context["codebase"] = str(codebase_path)

    # Feature contexts (non-underscore JSON files in context/)
    context_dir = Path(".claude/context")
    if context_dir.exists():
        for f in context_dir.glob("*.json"):
            if not f.name.startswith("_") and not f.name.startswith("query-"):
                context["active_features"].append(str(f))

    # Active plans
    plans_dir = Path(".claude/plans")
    if plans_dir.exists():
        for f in plans_dir.glob("*-plan.json"):
            try:
                with open(f) as pf:
                    plan = json.load(pf)
                    status = plan.get("meta", {}).get("status", "")
                    if status in ["approved", "in_progress"]:
                        context["active_plans"].append(
                            {
                                "path": str(f),
                                "feature": plan.get("meta", {}).get("feature_name"),
                                "status": status,
                            }
                        )
            except (json.JSONDecodeError, KeyError):
                pass

    # Memory database
    memory_db = Path(".claude/memory/memory.db")
    if memory_db.exists():
        context["memory_db"] = str(memory_db)

    return context


def format_context_summary(context: dict) -> str:
    """Format context for injection into system prompt."""
    lines = ["## Active Context"]

    if context["codebase"]:
        lines.append(f"- Codebase context: `{context['codebase']}`")

    if context["active_features"]:
        lines.append("- Feature contexts:")
        for f in context["active_features"]:
            lines.append(f"  - `{f}`")

    if context["active_plans"]:
        lines.append("- Active plans:")
        for p in context["active_plans"]:
            lines.append(f"  - `{p['path']}` ({p['feature']}) - {p['status']}")

    if context["memory_db"]:
        lines.append(f"- Memory database: `{context['memory_db']}`")

    return "\n".join(lines)


def query_memories(db_path: str, task: str, project_name: str) -> str | None:
    """Query relevant memories for the current task."""
    # Check multiple possible locations for the script
    possible_paths = [
        Path(".claude/scripts/memory-query.py"),
        Path.home() / ".claude/skills/coding-memory/scripts/memory-query.py",
    ]

    script_path = None
    for p in possible_paths:
        if p.exists():
            script_path = p
            break

    if not script_path:
        return None

    try:
        result = subprocess.run(
            [
                "python",
                str(script_path),
                "--db-path",
                db_path,
                "--task",
                task,
                "--scopes",
                "universal",
                f"project:{project_name}",
                "--max-tokens",
                "400",
                "--format",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None


def get_next_task_description(context: dict) -> str | None:
    """Try to determine what the user is likely to work on next."""
    # Check active plans for incomplete chunks
    for plan_info in context["active_plans"]:
        try:
            with open(plan_info["path"]) as f:
                plan = json.load(f)

            for chunk in plan.get("chunks", []):
                if chunk.get("status") not in ["completed"]:
                    # Found next chunk
                    return f"{chunk.get('name', '')} - {chunk.get('delivers', '')}"
        except (json.JSONDecodeError, KeyError):
            pass

    return None


# ============================================
# Main
# ============================================


def main():
    """Main hook handler."""
    hook_input = get_hook_input()

    # Rotate previous session's event log and start fresh
    rotate_events_file()

    # Emit session start event
    agent_name = os.environ.get("CLAUDE_CODE_AGENT_NAME", "main")
    agent_type = os.environ.get("CLAUDE_CODE_AGENT_TYPE", "interactive")
    session_id = hook_input.get("session_id", "")
    emit(
        "session_started",
        agent=agent_name,
        agent_type=agent_type,
        session_id=session_id,
    )

    # Write session ID for PTC container labeling
    _write_ptc_session_id(session_id)

    # Reset per-session tracking files so warnings fire fresh
    _reset_session_tracking()

    context = find_active_context()

    # Detect team context
    team = get_team_context()

    output = {
        "context_summary": format_context_summary(context),
        "files_to_load": [],
        "memories": None,
        "warnings": [],
        "team": team,
    }

    # Inject session_id so agents can write it into session logs.
    # The completion validation hook matches session logs by this ID.
    if session_id:
        output["hookSpecificOutput"] = {
            "hookEventName": "SessionStart",
            "additionalContext": f"Your Claude Code session ID is: {session_id}. Use this as meta.session_id when creating or updating session logs.",
        }

    # Always load codebase context if available
    if context["codebase"]:
        output["files_to_load"].append(context["codebase"])

    # Query memories if database exists
    if context["memory_db"]:
        task = get_next_task_description(context)
        if task:
            memories = query_memories(
                context["memory_db"], task, context["project_name"]
            )
            if memories:
                output["memories"] = memories
                output["warnings"].append("## Relevant Memories Loaded")
                output["warnings"].append(memories)

    # Note active work
    if context["active_plans"]:
        output["warnings"].append("\n## Active Work")
        for p in context["active_plans"]:
            try:
                with open(p["path"]) as f:
                    plan = json.load(f)
                incomplete = [
                    c
                    for c in plan.get("chunks", [])
                    if c.get("status") not in ["completed"]
                ]
                if incomplete:
                    next_chunk = incomplete[0]
                    output["warnings"].append(
                        f"- {p['feature']}: Next chunk is `{next_chunk['id']}` - {next_chunk['name']}"
                    )
            except (json.JSONDecodeError, KeyError, FileNotFoundError):
                pass

    # Note team context if present
    if team:
        output["warnings"].append("\n## Team Context")
        output["warnings"].append(f"  Team: {team['team_name']}")
        output["warnings"].append(
            f"  Role: {team['agent_name']} ({team['agent_type']})"
        )

    # Check for stale design reviews
    stale_warnings = check_design_review_staleness()
    if stale_warnings:
        output["warnings"].extend(stale_warnings)

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
