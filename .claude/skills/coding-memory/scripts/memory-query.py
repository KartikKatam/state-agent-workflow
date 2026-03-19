#!/usr/bin/env python3
"""
Memory query script for retrieving relevant memories at task start.

Used by chunk-coder (via session-start hook) to get contextually
relevant patterns and instances.

Usage:
    python memory-query.py --task "..." --files "..." [options]
"""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DEFAULT_DB_PATH = ".claude/memory/memory.db"


def tokenize(text: str) -> set[str]:
    """Simple tokenization for text similarity."""
    import re

    words = re.findall(r"\b[a-z]+\b", text.lower())
    stop_words = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "it",
        "its",
    }
    return set(w for w in words if w not in stop_words and len(w) > 2)


def jaccard_similarity(text1: str, text2: str) -> float:
    """Calculate Jaccard similarity between two texts."""
    tokens1 = tokenize(text1)
    tokens2 = tokenize(text2)
    if not tokens1 or not tokens2:
        return 0.0
    intersection = tokens1 & tokens2
    union = tokens1 | tokens2
    return len(intersection) / len(union)


def query_memories(
    db_path: str,
    task: str,
    files: list[str] = None,
    tags: list[str] = None,
    scopes: list[str] = None,
    similarity_threshold: float = 0.25,
    max_tokens: int = 500,
) -> dict:
    """
    Query relevant memories for the current task.

    Returns patterns and instances ranked by relevance,
    limited by token budget.
    """
    if not Path(db_path).exists():
        return {"patterns": [], "instances": [], "total_tokens": 0}

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Build search context from task + files + tags
    search_context = task
    if files:
        search_context += " " + " ".join(files)
    if tags:
        search_context += " " + " ".join(tags)

    # Get all patterns
    scope_filter = ""
    params = []
    if scopes:
        placeholders = ",".join("?" * len(scopes))
        scope_filter = f"AND scope IN ({placeholders})"
        params = scopes

    cursor.execute(
        f"""
        SELECT id, scope, type, learned, category, frequency, confidence,
               updated_at, promoted_to_claude_md
        FROM patterns
        WHERE 1=1 {scope_filter}
        ORDER BY frequency DESC, confidence DESC
    """,
        params,
    )

    all_patterns = cursor.fetchall()

    # Score and rank patterns
    scored_patterns = []
    for row in all_patterns:
        learned = row[3]
        category = row[4]
        frequency = row[5]
        confidence = row[6]
        updated_at = row[7]

        # Calculate similarity
        similarity = jaccard_similarity(search_context, learned + " " + category)

        # Calculate recency score (decay over 30 days)
        try:
            updated = datetime.fromisoformat(updated_at)
            days_ago = (datetime.now() - updated).days
            recency = max(0, 1 - (days_ago / 30))
        except (ValueError, TypeError):
            recency = 0.5

        # Combined score: similarity * frequency_boost * recency
        frequency_boost = min(2.0, 1 + (frequency - 1) * 0.1)  # Max 2x boost
        score = similarity * frequency_boost * (0.7 + 0.3 * recency)

        if similarity >= similarity_threshold or frequency >= 3:
            scored_patterns.append(
                {
                    "id": row[0],
                    "scope": row[1],
                    "type": row[2],
                    "learned": learned,
                    "category": category,
                    "frequency": frequency,
                    "confidence": confidence,
                    "promoted": bool(row[8]),
                    "similarity": round(similarity, 3),
                    "score": round(score, 3),
                }
            )

    # Sort by score
    scored_patterns.sort(key=lambda x: x["score"], reverse=True)

    # Get linked patterns for top results
    top_pattern_ids = [p["id"] for p in scored_patterns[:5]]
    linked_ids = set()

    if top_pattern_ids:
        placeholders = ",".join("?" * len(top_pattern_ids))
        cursor.execute(
            f"""
            SELECT DISTINCT
                CASE WHEN from_id IN ({placeholders}) THEN to_id ELSE from_id END as linked_id
            FROM memory_links
            WHERE (from_id IN ({placeholders}) OR to_id IN ({placeholders}))
              AND from_type = 'pattern' AND to_type = 'pattern'
        """,
            top_pattern_ids * 3,
        )

        linked_ids = set(row[0] for row in cursor.fetchall())
        linked_ids -= set(top_pattern_ids)  # Don't duplicate

    # Add linked patterns with lower priority
    for pattern in scored_patterns:
        if pattern["id"] in linked_ids:
            pattern["via_link"] = True
            pattern["score"] *= 0.8  # Slightly lower priority

    # Get recent instances for context
    cursor.execute(
        f"""
        SELECT id, pattern_id, scope, what, context, resolution, created_at
        FROM instances
        WHERE 1=1 {scope_filter}
        ORDER BY created_at DESC
        LIMIT 20
    """,
        params,
    )

    recent_instances = []
    for row in cursor.fetchall():
        what = row[3]
        context = row[4] or ""
        similarity = jaccard_similarity(search_context, what + " " + context)

        if similarity >= similarity_threshold:
            recent_instances.append(
                {
                    "id": row[0],
                    "pattern_id": row[1],
                    "scope": row[2],
                    "what": what,
                    "context": context,
                    "resolution": row[5],
                    "created_at": row[6],
                    "similarity": round(similarity, 3),
                }
            )

    recent_instances.sort(key=lambda x: x["similarity"], reverse=True)

    conn.close()

    # Apply token budget
    result_patterns = []
    result_instances = []
    total_tokens = 0

    # Estimate tokens: ~1.3 tokens per word
    def estimate_tokens(text: str) -> int:
        return int(len(text.split()) * 1.3)

    # Add patterns first (higher priority)
    for pattern in scored_patterns:
        tokens = estimate_tokens(pattern["learned"]) + 20  # overhead
        if total_tokens + tokens <= max_tokens * 0.7:  # Reserve 30% for instances
            result_patterns.append(pattern)
            total_tokens += tokens
        if len(result_patterns) >= 10:
            break

    # Add instances
    for instance in recent_instances[:5]:
        tokens = estimate_tokens(instance["what"]) + 15
        if total_tokens + tokens <= max_tokens:
            result_instances.append(instance)
            total_tokens += tokens

    return {
        "patterns": result_patterns,
        "instances": result_instances,
        "total_tokens": total_tokens,
    }


def format_for_context(memories: dict) -> str:
    """Format memories for injection into agent context."""
    lines = []

    if memories["patterns"]:
        lines.append("## Relevant Patterns to Remember\n")

        for p in memories["patterns"]:
            freq_str = f"{p['frequency']}x" if p["frequency"] > 1 else "new"
            conf_str = f"{p['confidence']:.0%}"
            promoted_str = " ✓" if p["promoted"] else ""

            lines.append(f"- **{p['learned']}** ({freq_str}, {conf_str}){promoted_str}")
            lines.append(f"  - Category: {p['category']}, Scope: {p['scope']}")

        lines.append("")

    if memories["instances"]:
        lines.append("## Recent Related Instances\n")

        for i in memories["instances"]:
            lines.append(f"- {i['what'][:100]}...")
            if i["resolution"]:
                lines.append(f"  - Resolution: {i['resolution'][:80]}...")
            if i["pattern_id"]:
                lines.append(f"  - Pattern: {i['pattern_id']}")

        lines.append("")

    if not memories["patterns"] and not memories["instances"]:
        lines.append("No relevant memories found for this task.\n")

    lines.append(f"_({memories['total_tokens']} tokens used)_")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Query memories for current task")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Database path")
    parser.add_argument("--task", required=True, help="Task description")
    parser.add_argument("--files", nargs="*", help="Files being worked on")
    parser.add_argument("--tags", nargs="*", help="Relevant tags")
    parser.add_argument(
        "--scopes", nargs="*", default=["universal"], help="Scopes to search"
    )
    parser.add_argument(
        "--threshold", type=float, default=0.25, help="Similarity threshold"
    )
    parser.add_argument("--max-tokens", type=int, default=500, help="Token budget")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--format", action="store_true", help="Output formatted for context"
    )

    args = parser.parse_args()

    # Add current project scope if not universal-only
    scopes = args.scopes or ["universal"]

    memories = query_memories(
        args.db_path,
        args.task,
        args.files,
        args.tags,
        scopes,
        args.threshold,
        args.max_tokens,
    )

    if args.json:
        print(json.dumps(memories, indent=2))
    elif args.format:
        print(format_for_context(memories))
    else:
        print(
            f"[memory-query] Found {len(memories['patterns'])} patterns, {len(memories['instances'])} instances"
        )
        print(
            f"[memory-query] Token usage: {memories['total_tokens']}/{args.max_tokens}\n"
        )

        if memories["patterns"]:
            print("Patterns:")
            for p in memories["patterns"]:
                print(f"  {p['id']}: {p['learned'][:60]}... (score: {p['score']})")

        if memories["instances"]:
            print("\nInstances:")
            for i in memories["instances"]:
                print(f"  {i['id']}: {i['what'][:60]}...")


if __name__ == "__main__":
    main()
