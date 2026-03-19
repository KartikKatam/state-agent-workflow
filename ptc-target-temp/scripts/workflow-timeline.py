#!/usr/bin/env python3
"""
Workflow Timeline Viewer

Reads the JSONL event log and prints a human-readable timeline
with relative timestamps and agent nesting.

Usage:
    python scripts/workflow-timeline.py                    # full timeline
    python scripts/workflow-timeline.py --tail 20          # last 20 events
    python scripts/workflow-timeline.py --filter file_written,skill_loaded
    python scripts/workflow-timeline.py --archives         # list archived logs
    python scripts/workflow-timeline.py --archive 20260207-143000  # view archive
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

EVENTS_FILE = Path(".claude/temp/workflow-events.jsonl")
EVENTS_DIR = Path(".claude/temp")

# Display formatting per event type
EVENT_ICONS = {
    "session_started": "+",
    "session_ended": "-",
    "agent_spawned": "->",
    "agent_stopped": "<-",
    "agent_summary": "  ",
    "file_written": "  ",
    "skill_loaded": "  ",
    "context_pressure": "!!",
}


def parse_ts(ts_str: str) -> datetime:
    """Parse ISO8601 timestamp."""
    try:
        return datetime.fromisoformat(ts_str)
    except ValueError:
        return datetime.min


def fmt_relative(start: datetime, current: datetime) -> str:
    """Format as [MM:SS] relative to start."""
    delta = (current - start).total_seconds()
    if delta < 0:
        delta = 0
    minutes = int(delta // 60)
    seconds = int(delta % 60)
    return f"[{minutes:02d}:{seconds:02d}]"


def fmt_event(event: dict, start_ts: datetime) -> str:
    """Format a single event as a timeline line."""
    ts = parse_ts(event.get("ts", ""))
    rel = fmt_relative(start_ts, ts)
    etype = event.get("event", "unknown")
    icon = EVENT_ICONS.get(etype, "  ")

    if etype == "session_started":
        agent = event.get("agent", "unknown")
        atype = event.get("agent_type", "")
        return f"{rel} {icon} session started ({agent}, {atype})"

    elif etype == "session_ended":
        agent = event.get("agent", "unknown")
        return f"{rel} {icon} session ended ({agent})"

    elif etype == "agent_spawned":
        agent = event.get("agent_type", "unknown")
        model = event.get("model", "")
        extra = f", {model}" if model else ""
        return f"{rel} {icon} spawned {agent}{extra}"

    elif etype == "agent_stopped":
        agent = event.get("agent_type", "unknown")
        dur = event.get("duration_s")
        extra = f" ({dur}s)" if dur else ""
        return f"{rel} {icon} {agent} stopped{extra}"

    elif etype == "agent_summary":
        tools = event.get("tools_used", [])
        files = event.get("files_written", [])
        preview = event.get("output_preview", "")[:80]
        lines = []
        if files:
            lines.append(f"{rel}      files: {', '.join(files)}")
        if tools:
            lines.append(f"{rel}      tools: {', '.join(tools)}")
        if preview:
            lines.append(f"{rel}      output: {preview}")
        return "\n".join(lines) if lines else f"{rel}    agent summary (no details)"

    elif etype == "file_written":
        path = event.get("file", "unknown")
        agent = event.get("agent", "")
        return f"{rel}    wrote: {path}"

    elif etype == "skill_loaded":
        skill = event.get("skill", "unknown")
        return f"{rel}    skill loaded: {skill}"

    elif etype == "context_pressure":
        pct = event.get("percentage", "?")
        level = event.get("level", "unknown")
        return f"{rel} {icon} context at {pct}% ({level})"

    else:
        # Generic fallback
        details = {k: v for k, v in event.items() if k not in ("ts", "event")}
        return f"{rel}    {etype}: {json.dumps(details)}"


def read_events(path: Path) -> list[dict]:
    """Read all events from a JSONL file."""
    events = []
    if not path.exists():
        return events

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def list_archives() -> list[Path]:
    """List available archive files."""
    if not EVENTS_DIR.exists():
        return []
    return sorted(EVENTS_DIR.glob("workflow-events-2*.jsonl"))


def main():
    parser = argparse.ArgumentParser(description="Workflow timeline viewer")
    parser.add_argument("--tail", type=int, help="Show only last N events")
    parser.add_argument("--filter", type=str, help="Comma-separated event types to show")
    parser.add_argument("--archives", action="store_true", help="List available archives")
    parser.add_argument("--archive", type=str, help="View a specific archive by timestamp suffix")
    args = parser.parse_args()

    if args.archives:
        archives = list_archives()
        if not archives:
            print("No archived event logs found.")
            return
        print("Archived event logs:")
        for a in archives:
            size = a.stat().st_size
            print(f"  {a.name}  ({size:,} bytes)")
        return

    # Determine which file to read
    if args.archive:
        target = EVENTS_DIR / f"workflow-events-{args.archive}.jsonl"
        if not target.exists():
            print(f"Archive not found: {target}")
            print("Use --archives to list available archives.")
            return 1
    else:
        target = EVENTS_FILE

    if not target.exists():
        print(f"No events file found at {target}")
        return 1

    events = read_events(target)
    if not events:
        print("No events recorded.")
        return

    # Apply filter
    if args.filter:
        allowed = set(args.filter.split(","))
        events = [e for e in events if e.get("event") in allowed]

    # Apply tail
    if args.tail:
        events = events[-args.tail :]

    if not events:
        print("No events match the filter.")
        return

    # Find start timestamp for relative times
    start_ts = parse_ts(events[0].get("ts", ""))

    # Print header
    source = target.name
    print(f"--- Workflow Timeline ({source}) ---")
    print()

    for event in events:
        line = fmt_event(event, start_ts)
        if line:
            print(line)

    print()
    print(f"--- {len(events)} events ---")


if __name__ == "__main__":
    sys.exit(main() or 0)
