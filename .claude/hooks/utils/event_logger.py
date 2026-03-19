#!/usr/bin/env python3
"""
Event Logger Utility

Append-only JSONL event logging for workflow observability.
All functions are fire-and-forget — errors are silently caught
so observability never blocks agent work.

Output: .claude/temp/workflow-events.jsonl
"""

import json
import os
from datetime import datetime
from pathlib import Path

EVENTS_FILE = ".claude/temp/workflow-events.jsonl"


def emit(event: str, **kwargs) -> None:
    """
    Append a single JSONL event line.

    Args:
        event: Event name (e.g. "session_started", "file_written")
        **kwargs: Additional fields merged into the event dict
    """
    try:
        path = Path(EVENTS_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)

        entry = {"ts": datetime.now().isoformat(), "event": event}
        entry.update(kwargs)

        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # Observability must never block


def emit_file_written(file_path: str, agent_id: str = "unknown") -> None:
    """Emit file_written event for files under .claude/."""
    # Normalize to relative path for consistent matching
    rel = file_path
    if os.path.isabs(file_path):
        try:
            rel = os.path.relpath(file_path)
        except ValueError:
            rel = file_path

    if rel.startswith(".claude/"):
        emit("file_written", file=rel, agent=agent_id)


def emit_skill_loaded(skill_path: str, agent_id: str = "unknown") -> None:
    """Emit skill_loaded when a SKILL.md file is read."""
    # Extract skill name from path like .claude/skills/tdd-workflow/SKILL.md
    rel = skill_path
    if os.path.isabs(skill_path):
        try:
            rel = os.path.relpath(skill_path)
        except ValueError:
            rel = skill_path

    # Match .claude/skills/*/SKILL.md or similar skill entry points
    parts = Path(rel).parts
    if (
        len(parts) >= 4
        and parts[0] == ".claude"
        and parts[1] == "skills"
        and parts[-1].upper().startswith("SKILL")
    ):
        skill_name = parts[2]
        emit("skill_loaded", skill=skill_name, file=rel, agent=agent_id)


def emit_context_pressure(
    percentage: float, level: str, agent_id: str = "unknown"
) -> None:
    """Emit context_pressure when a threshold is crossed."""
    emit(
        "context_pressure", percentage=round(percentage, 1), level=level, agent=agent_id
    )


def rotate_events_file(max_archives: int = 5) -> None:
    """
    Rotate the current events file to a timestamped archive.

    Called at session start to give each session a clean timeline
    while preserving previous sessions for debugging.
    """
    try:
        path = Path(EVENTS_FILE)
        if not path.exists() or path.stat().st_size == 0:
            return

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        archive = path.with_name(f"workflow-events-{ts}.jsonl")
        path.rename(archive)

        # Prune old archives, keep only the most recent N
        archives = sorted(path.parent.glob("workflow-events-2*.jsonl"))
        for old in archives[:-max_archives]:
            old.unlink()
    except Exception:
        pass  # Never block session start
