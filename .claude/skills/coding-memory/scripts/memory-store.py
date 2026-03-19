#!/usr/bin/env python3
"""
Memory store script for adding and updating memories.

Handles patterns, instances, and links.

Usage:
    python memory-store.py add-pattern --learned "..." --category "..." [options]
    python memory-store.py add-instance --pattern-id "..." --what "..." [options]
    python memory-store.py add-link --from-id "..." --to-id "..." --relationship "..."
    python memory-store.py update-pattern --pattern-id "..." [--increment-frequency] [options]
    python memory-store.py mark-promoted --pattern-id "..."
"""

import argparse
import json
import sqlite3
from datetime import datetime

DEFAULT_DB_PATH = ".claude/memory/memory.db"


def get_next_id(cursor: sqlite3.Cursor, table: str, prefix: str) -> str:
    """Generate next sequential ID."""
    cursor.execute(f"SELECT id FROM {table} ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()

    if row:
        last_num = int(row[0].split("-")[1])
        return f"{prefix}-{last_num + 1:03d}"
    else:
        return f"{prefix}-001"


def add_pattern(
    db_path: str,
    learned: str,
    category: str,
    scope: str = "universal",
    pattern_type: str = "mistake",
    trigger_patterns: list[str] = None,
    tags: list[str] = None,
) -> str:
    """Add a new pattern to the database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    pattern_id = get_next_id(cursor, "patterns", "pat")
    now = datetime.now().isoformat()

    cursor.execute(
        """
        INSERT INTO patterns (
            id, scope, type, learned, category, trigger_patterns,
            frequency, confidence, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            pattern_id,
            scope,
            pattern_type,
            learned,
            category,
            json.dumps(trigger_patterns or []),
            1,
            0.5,
            now,
            now,
        ),
    )

    # Update metadata
    cursor.execute("UPDATE metadata SET value = ? WHERE key = 'last_updated'", (now,))

    conn.commit()
    conn.close()

    print(f"[memory-store] Added pattern: {pattern_id}")
    print(f"  learned: {learned}")
    print(f"  category: {category}")
    print(f"  scope: {scope}")

    return pattern_id


def add_instance(
    db_path: str,
    what: str,
    pattern_id: str = None,
    scope: str = "universal",
    context: str = None,
    resolution: str = None,
    tags: list[str] = None,
    source_session: str = None,
    source_signal: str = None,
) -> str:
    """Add a new instance to the database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    instance_id = get_next_id(cursor, "instances", "inst")
    now = datetime.now().isoformat()

    cursor.execute(
        """
        INSERT INTO instances (
            id, pattern_id, scope, what, context, resolution,
            tags, source_session, source_signal, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            instance_id,
            pattern_id,
            scope,
            what,
            context,
            resolution,
            json.dumps(tags or []),
            source_session,
            source_signal,
            now,
        ),
    )

    # If pattern_id provided, increment pattern frequency and update confidence
    if pattern_id:
        cursor.execute(
            """
            UPDATE patterns
            SET frequency = frequency + 1,
                confidence = MIN(0.95, confidence + (1 - confidence) * 0.15),
                updated_at = ?
            WHERE id = ?
        """,
            (now, pattern_id),
        )

    # Update metadata
    cursor.execute("UPDATE metadata SET value = ? WHERE key = 'last_updated'", (now,))

    conn.commit()
    conn.close()

    print(f"[memory-store] Added instance: {instance_id}")
    print(f"  what: {what[:50]}...")
    if pattern_id:
        print(f"  linked to pattern: {pattern_id}")

    return instance_id


def add_link(
    db_path: str,
    from_id: str,
    to_id: str,
    relationship: str,
    from_type: str = None,
    to_type: str = None,
    confidence: float = 0.5,
) -> None:
    """Add a link between two memories."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Auto-detect types if not provided
    if not from_type:
        from_type = "pattern" if from_id.startswith("pat-") else "instance"
    if not to_type:
        to_type = "pattern" if to_id.startswith("pat-") else "instance"

    now = datetime.now().isoformat()

    try:
        cursor.execute(
            """
            INSERT INTO memory_links (
                from_id, to_id, from_type, to_type, relationship, confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (from_id, to_id, from_type, to_type, relationship, confidence, now),
        )

        conn.commit()
        print(f"[memory-store] Added link: {from_id} --{relationship}--> {to_id}")
    except sqlite3.IntegrityError:
        print(
            f"[memory-store] Link already exists: {from_id} --{relationship}--> {to_id}"
        )

    conn.close()


def update_pattern(
    db_path: str,
    pattern_id: str,
    increment_frequency: bool = False,
    learned: str = None,
    confidence: float = None,
    trigger_patterns: list[str] = None,
) -> None:
    """Update an existing pattern."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    now = datetime.now().isoformat()
    updates = ["updated_at = ?"]
    params = [now]

    if increment_frequency:
        updates.append("frequency = frequency + 1")
        updates.append("confidence = MIN(0.95, confidence + (1 - confidence) * 0.15)")

    if learned:
        updates.append("learned = ?")
        params.append(learned)

    if confidence is not None:
        updates.append("confidence = ?")
        params.append(confidence)

    if trigger_patterns:
        updates.append("trigger_patterns = ?")
        params.append(json.dumps(trigger_patterns))

    params.append(pattern_id)

    cursor.execute(
        f"""
        UPDATE patterns SET {", ".join(updates)} WHERE id = ?
    """,
        params,
    )

    conn.commit()
    conn.close()

    print(f"[memory-store] Updated pattern: {pattern_id}")


def mark_promoted(db_path: str, pattern_id: str) -> None:
    """Mark a pattern as promoted to CLAUDE.md."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    now = datetime.now().isoformat()

    cursor.execute(
        """
        UPDATE patterns
        SET promoted_to_claude_md = 1, updated_at = ?
        WHERE id = ?
    """,
        (now, pattern_id),
    )

    conn.commit()
    conn.close()

    print(f"[memory-store] Marked as promoted: {pattern_id}")


def get_pattern(db_path: str, pattern_id: str) -> dict | None:
    """Get a pattern by ID."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, scope, type, learned, category, trigger_patterns,
               frequency, confidence, created_at, updated_at, promoted_to_claude_md
        FROM patterns WHERE id = ?
    """,
        (pattern_id,),
    )

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row[0],
        "scope": row[1],
        "type": row[2],
        "learned": row[3],
        "category": row[4],
        "trigger_patterns": json.loads(row[5]) if row[5] else [],
        "frequency": row[6],
        "confidence": row[7],
        "created_at": row[8],
        "updated_at": row[9],
        "promoted_to_claude_md": bool(row[10]),
    }


def main():
    parser = argparse.ArgumentParser(description="Memory store operations")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Database path")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # add-pattern
    add_pat = subparsers.add_parser("add-pattern", help="Add a new pattern")
    add_pat.add_argument("--learned", required=True, help="The generalized learning")
    add_pat.add_argument(
        "--category", required=True, help="Category (naming, testing, etc.)"
    )
    add_pat.add_argument(
        "--scope", default="universal", help="Scope (universal or project:name)"
    )
    add_pat.add_argument(
        "--type",
        dest="pattern_type",
        default="mistake",
        help="Type (mistake, preference, pattern, insight)",
    )
    add_pat.add_argument("--trigger-patterns", nargs="*", help="Regex trigger patterns")
    add_pat.add_argument("--tags", nargs="*", help="Tags")

    # add-instance
    add_inst = subparsers.add_parser("add-instance", help="Add a new instance")
    add_inst.add_argument("--what", required=True, help="What specifically happened")
    add_inst.add_argument("--pattern-id", help="Link to parent pattern")
    add_inst.add_argument("--scope", default="universal", help="Scope")
    add_inst.add_argument("--context", help="Where it happened")
    add_inst.add_argument("--resolution", help="How it was fixed")
    add_inst.add_argument("--tags", nargs="*", help="Tags")
    add_inst.add_argument("--source-session", help="Source session log path")
    add_inst.add_argument("--source-signal", help="Source signal ID")

    # add-link
    add_lnk = subparsers.add_parser("add-link", help="Add a link between memories")
    add_lnk.add_argument("--from-id", required=True, help="Source memory ID")
    add_lnk.add_argument("--to-id", required=True, help="Target memory ID")
    add_lnk.add_argument(
        "--relationship",
        required=True,
        choices=["similar", "caused_by", "led_to", "supersedes"],
    )
    add_lnk.add_argument(
        "--confidence", type=float, default=0.5, help="Link confidence"
    )

    # update-pattern
    update_pat = subparsers.add_parser("update-pattern", help="Update a pattern")
    update_pat.add_argument("--pattern-id", required=True, help="Pattern to update")
    update_pat.add_argument(
        "--increment-frequency", action="store_true", help="Increment frequency"
    )
    update_pat.add_argument("--learned", help="Update learned text")
    update_pat.add_argument("--confidence", type=float, help="Set confidence")
    update_pat.add_argument(
        "--trigger-patterns", nargs="*", help="Set trigger patterns"
    )

    # mark-promoted
    mark_prom = subparsers.add_parser("mark-promoted", help="Mark pattern as promoted")
    mark_prom.add_argument("--pattern-id", required=True, help="Pattern to mark")

    # get-pattern
    get_pat = subparsers.add_parser("get-pattern", help="Get pattern details")
    get_pat.add_argument("--pattern-id", required=True, help="Pattern ID")

    args = parser.parse_args()

    if args.command == "add-pattern":
        add_pattern(
            args.db_path,
            args.learned,
            args.category,
            args.scope,
            args.pattern_type,
            args.trigger_patterns,
            args.tags,
        )
    elif args.command == "add-instance":
        add_instance(
            args.db_path,
            args.what,
            args.pattern_id,
            args.scope,
            args.context,
            args.resolution,
            args.tags,
            args.source_session,
            args.source_signal,
        )
    elif args.command == "add-link":
        add_link(
            args.db_path,
            args.from_id,
            args.to_id,
            args.relationship,
            confidence=args.confidence,
        )
    elif args.command == "update-pattern":
        update_pattern(
            args.db_path,
            args.pattern_id,
            args.increment_frequency,
            args.learned,
            args.confidence,
            args.trigger_patterns,
        )
    elif args.command == "mark-promoted":
        mark_promoted(args.db_path, args.pattern_id)
    elif args.command == "get-pattern":
        pattern = get_pattern(args.db_path, args.pattern_id)
        if pattern:
            print(json.dumps(pattern, indent=2))
        else:
            print(f"Pattern not found: {args.pattern_id}")


if __name__ == "__main__":
    main()
