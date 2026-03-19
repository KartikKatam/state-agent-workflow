#!/usr/bin/env python3
"""
Stop Hook: Session summary and uncommitted changes warning.

Triggered when a Claude Code session ends. Provides summary of
work done and warns about uncommitted changes.

Hook type: Stop
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Add parent directory to path for utils import
sys.path.insert(0, str(Path(__file__).parent))

from utils.event_logger import emit


def get_hook_input() -> dict:
    """Read hook input from environment or stdin."""
    hook_data = os.environ.get("CLAUDE_HOOK_DATA")
    if hook_data:
        return json.loads(hook_data)

    if not sys.stdin.isatty():
        return json.load(sys.stdin)

    return {}


def get_uncommitted_changes() -> list[str]:
    """Get list of uncommitted changes."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5
        )
        if proc.returncode == 0 and proc.stdout:
            return [line.strip() for line in proc.stdout.strip().split("\n") if line]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return []


def get_active_chunk() -> dict | None:
    """Find any in-progress chunk."""
    plans_dir = Path(".claude/plans")
    if not plans_dir.exists():
        return None

    for plan_file in plans_dir.glob("*-plan.json"):
        try:
            with open(plan_file) as f:
                plan = json.load(f)

            for chunk in plan.get("chunks", []):
                if chunk.get("status") == "in_progress":
                    return {
                        "feature": plan.get("meta", {}).get("feature_name"),
                        "chunk_id": chunk.get("id"),
                        "chunk_name": chunk.get("name"),
                        "plan_path": str(plan_file),
                    }
        except (json.JSONDecodeError, KeyError):
            pass

    return None


def get_session_log_summary() -> dict | None:
    """Get summary from most recent session log (chunk or planning)."""
    logs_dir = Path(".claude/logs")
    if not logs_dir.exists():
        return None

    # Find most recent log (covers both chunk logs and planning logs)
    logs = sorted(
        logs_dir.glob("*-log.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not logs:
        return None

    try:
        with open(logs[0]) as f:
            log = json.load(f)

        is_planning = "-planning-log.json" in logs[0].name

        summary = {
            "feature": log.get("meta", {}).get("feature"),
            "status": log.get("status"),
        }

        if is_planning:
            summary["type"] = "planning"
            summary["edits"] = len(log.get("edit_history", []))
            summary["files_created"] = len(log.get("files_created", []))
        else:
            summary["type"] = "chunk"
            summary["chunk"] = log.get("meta", {}).get("chunk")
            summary["files_modified"] = len(log.get("files_modified", []))
            summary["tests_written"] = len(log.get("tests_written", []))

        return summary
    except (json.JSONDecodeError, KeyError):
        pass

    return None


def main():
    """Main hook handler."""
    # Emit session end event (before team check — we want observability for all agents)
    agent_name = os.environ.get("CLAUDE_CODE_AGENT_NAME", "main")
    emit("session_ended", agent=agent_name)

    # Teammates communicate via SendMessage, not user-facing hooks
    if os.environ.get("CLAUDE_CODE_TEAM_NAME"):
        return

    output = {"summary": [], "warnings": [], "next_steps": []}

    # Check for uncommitted changes
    uncommitted = get_uncommitted_changes()
    if uncommitted:
        output["warnings"].append(f"⚠️  {len(uncommitted)} uncommitted change(s):")
        for change in uncommitted[:10]:  # Limit display
            output["warnings"].append(f"   {change}")
        if len(uncommitted) > 10:
            output["warnings"].append(f"   ... and {len(uncommitted) - 10} more")
        output["next_steps"].append(
            "Consider committing your changes before next session"
        )

    # Check for in-progress chunk
    active = get_active_chunk()
    if active:
        output["summary"].append(
            f"📝 In progress: {active['feature']} / {active['chunk_id']}"
        )
        output["summary"].append(f"   {active['chunk_name']}")
        output["next_steps"].append(
            f'Resume with: "Continue {active["chunk_id"]} of {active["feature"]}"'
        )

    # Session log summary
    session = get_session_log_summary()
    if session:
        if session.get("type") == "planning":
            output["summary"].append(f"📊 Planning: {session['feature']}")
            output["summary"].append(f"   Status: {session['status']}")
            output["summary"].append(f"   User edits: {session['edits']}")
            output["summary"].append(f"   Files created: {session['files_created']}")
        else:
            output["summary"].append(
                f"📊 Session: {session['feature']} / {session.get('chunk', '?')}"
            )
            output["summary"].append(f"   Status: {session['status']}")
            output["summary"].append(
                f"   Files modified: {session.get('files_modified', 0)}"
            )
            output["summary"].append(
                f"   Tests written: {session.get('tests_written', 0)}"
            )

    # Format output
    lines = []

    if output["summary"]:
        lines.append("## Session Summary")
        lines.extend(output["summary"])
        lines.append("")

    if output["warnings"]:
        lines.append("## Warnings")
        lines.extend(output["warnings"])
        lines.append("")

    if output["next_steps"]:
        lines.append("## Next Steps")
        for step in output["next_steps"]:
            lines.append(f"- {step}")

    if lines:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
