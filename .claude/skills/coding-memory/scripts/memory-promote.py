#!/usr/bin/env python3
"""
Memory promotion script for checking and promoting patterns to CLAUDE.md.

Patterns promote when they reach frequency >= 5 and confidence >= 0.8.

Usage:
    python memory-promote.py --check [--db-path PATH]
    python memory-promote.py --promote --pattern-id PAT-XXX [--claude-md PATH]
    python memory-promote.py --list-candidates
"""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DEFAULT_DB_PATH = ".claude/memory/memory.db"
DEFAULT_CLAUDE_MD = "CLAUDE.md"


LEARNED_RULES_HEADER = """
## Learned Rules

These rules were automatically learned from repeated patterns during development.
Do not edit manually - they are managed by the memory system.

### Mistakes to Avoid
"""


def get_promotion_candidates(db_path: str) -> list[dict]:
    """Get patterns that are ready for promotion."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, scope, type, learned, category, frequency, confidence, created_at
        FROM patterns
        WHERE frequency >= 5
          AND confidence >= 0.8
          AND promoted_to_claude_md = 0
        ORDER BY frequency DESC, confidence DESC
    """)

    candidates = []
    for row in cursor.fetchall():
        candidates.append(
            {
                "id": row[0],
                "scope": row[1],
                "type": row[2],
                "learned": row[3],
                "category": row[4],
                "frequency": row[5],
                "confidence": row[6],
                "created_at": row[7],
            }
        )

    conn.close()
    return candidates


def get_already_promoted(db_path: str) -> list[dict]:
    """Get patterns that are already promoted."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, learned, category, frequency, confidence
        FROM patterns
        WHERE promoted_to_claude_md = 1
        ORDER BY category, learned
    """)

    promoted = []
    for row in cursor.fetchall():
        promoted.append(
            {
                "id": row[0],
                "learned": row[1],
                "category": row[2],
                "frequency": row[3],
                "confidence": row[4],
            }
        )

    conn.close()
    return promoted


def mark_as_promoted(db_path: str, pattern_id: str) -> None:
    """Mark a pattern as promoted in the database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE patterns
        SET promoted_to_claude_md = 1, updated_at = ?
        WHERE id = ?
    """,
        (datetime.now().isoformat(), pattern_id),
    )

    conn.commit()
    conn.close()


def read_claude_md(path: str) -> str:
    """Read CLAUDE.md content."""
    if Path(path).exists():
        return Path(path).read_text()
    return ""


def write_claude_md(path: str, content: str) -> None:
    """Write CLAUDE.md content."""
    Path(path).write_text(content)


def has_learned_rules_section(content: str) -> bool:
    """Check if CLAUDE.md has the Learned Rules section."""
    return "## Learned Rules" in content


def add_learned_rules_section(content: str) -> str:
    """Add the Learned Rules section to CLAUDE.md."""
    if has_learned_rules_section(content):
        return content

    # Add at the end
    return content.rstrip() + "\n\n" + LEARNED_RULES_HEADER + "\n"


def add_rule_to_claude_md(
    claude_md_path: str,
    pattern: dict,
) -> bool:
    """Add a promoted pattern to CLAUDE.md."""
    content = read_claude_md(claude_md_path)

    # Ensure section exists
    content = add_learned_rules_section(content)

    # Format the rule
    rule_text = f"- **{pattern['learned']}** (learned from {pattern['frequency']} occurrences)\n"

    # Check if already present (avoid duplicates)
    if pattern["learned"] in content:
        print(f"[memory-promote] Rule already in CLAUDE.md: {pattern['id']}")
        return False

    # Find the section and add the rule
    if "### Mistakes to Avoid" in content:
        # Add after the header
        parts = content.split("### Mistakes to Avoid\n", 1)
        if len(parts) == 2:
            content = parts[0] + "### Mistakes to Avoid\n" + rule_text + parts[1]
    else:
        # Just append
        content = content.rstrip() + "\n" + rule_text

    write_claude_md(claude_md_path, content)
    return True


def check_and_report(db_path: str) -> None:
    """Check for promotion candidates and report."""
    candidates = get_promotion_candidates(db_path)
    already = get_already_promoted(db_path)

    print("[memory-promote] Promotion status:")
    print(f"  Already promoted: {len(already)}")
    print(f"  Ready to promote: {len(candidates)}")

    if candidates:
        print("\n  Candidates:")
        for c in candidates:
            print(f"    {c['id']}: {c['learned'][:50]}...")
            print(
                f"           freq={c['frequency']}, conf={c['confidence']:.2f}, category={c['category']}"
            )

    if already:
        print("\n  Already promoted:")
        for p in already:
            print(f"    {p['id']}: {p['learned'][:50]}...")


def promote_pattern(db_path: str, pattern_id: str, claude_md_path: str) -> bool:
    """Promote a specific pattern to CLAUDE.md."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, scope, type, learned, category, frequency, confidence
        FROM patterns
        WHERE id = ?
    """,
        (pattern_id,),
    )

    row = cursor.fetchone()
    conn.close()

    if not row:
        print(f"[memory-promote] Pattern not found: {pattern_id}")
        return False

    pattern = {
        "id": row[0],
        "scope": row[1],
        "type": row[2],
        "learned": row[3],
        "category": row[4],
        "frequency": row[5],
        "confidence": row[6],
    }

    # Add to CLAUDE.md
    added = add_rule_to_claude_md(claude_md_path, pattern)

    if added:
        # Mark as promoted in DB
        mark_as_promoted(db_path, pattern_id)
        print(f"[memory-promote] Promoted: {pattern_id}")
        print(f"  Rule: {pattern['learned']}")
        return True

    return False


def promote_all_candidates(db_path: str, claude_md_path: str) -> int:
    """Promote all eligible candidates."""
    candidates = get_promotion_candidates(db_path)
    promoted_count = 0

    for candidate in candidates:
        if promote_pattern(db_path, candidate["id"], claude_md_path):
            promoted_count += 1

    return promoted_count


def main():
    parser = argparse.ArgumentParser(description="Memory promotion to CLAUDE.md")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Database path")
    parser.add_argument("--claude-md", default=DEFAULT_CLAUDE_MD, help="CLAUDE.md path")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Check promotion status")
    group.add_argument("--promote", action="store_true", help="Promote a pattern")
    group.add_argument(
        "--promote-all", action="store_true", help="Promote all candidates"
    )
    group.add_argument(
        "--list-candidates", action="store_true", help="List candidates as JSON"
    )

    parser.add_argument("--pattern-id", help="Pattern ID to promote (with --promote)")

    args = parser.parse_args()

    if not Path(args.db_path).exists():
        print(f"[memory-promote] Database not found: {args.db_path}")
        return

    if args.check:
        check_and_report(args.db_path)

    elif args.list_candidates:
        candidates = get_promotion_candidates(args.db_path)
        print(json.dumps(candidates, indent=2))

    elif args.promote:
        if not args.pattern_id:
            print("[memory-promote] --pattern-id required with --promote")
            return
        promote_pattern(args.db_path, args.pattern_id, args.claude_md)

    elif args.promote_all:
        count = promote_all_candidates(args.db_path, args.claude_md)
        print(f"[memory-promote] Promoted {count} pattern(s)")


if __name__ == "__main__":
    main()
