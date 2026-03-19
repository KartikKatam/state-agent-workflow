#!/usr/bin/env python3
"""
Memory search script for finding similar patterns and instances.

Uses text similarity (without external embeddings) for pattern matching.
For production, replace with proper embedding search.

Usage:
    python memory-search.py --query "..." --type pattern [options]
    python memory-search.py --query "..." --type instance [options]
"""

import argparse
import json
import re
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = ".claude/memory/memory.db"


def tokenize(text: str) -> set[str]:
    """Simple tokenization for text similarity."""
    # Lowercase and extract words
    words = re.findall(r"\b[a-z]+\b", text.lower())
    # Remove common stop words
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
        "above",
        "below",
        "between",
        "under",
        "again",
        "further",
        "then",
        "once",
        "and",
        "but",
        "or",
        "nor",
        "so",
        "yet",
        "both",
        "either",
        "neither",
        "not",
        "only",
        "own",
        "same",
        "than",
        "too",
        "very",
        "just",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "i",
        "you",
        "he",
        "she",
        "we",
        "they",
        "what",
        "which",
        "who",
        "whom",
        "when",
        "where",
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


def search_patterns(
    db_path: str,
    query: str,
    threshold: float = 0.3,
    limit: int = 10,
    scopes: list[str] = None,
    exclude_ids: list[str] = None,
    category: str = None,
) -> list[dict]:
    """Search for similar patterns."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Build query
    sql = """
        SELECT id, scope, type, learned, category, frequency, confidence,
               created_at, updated_at, promoted_to_claude_md
        FROM patterns WHERE 1=1
    """
    params = []

    if scopes:
        placeholders = ",".join("?" * len(scopes))
        sql += f" AND scope IN ({placeholders})"
        params.extend(scopes)

    if exclude_ids:
        placeholders = ",".join("?" * len(exclude_ids))
        sql += f" AND id NOT IN ({placeholders})"
        params.extend(exclude_ids)

    if category:
        sql += " AND category = ?"
        params.append(category)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    # Calculate similarities
    results = []
    for row in rows:
        similarity = jaccard_similarity(query, row[3])  # Compare with 'learned'

        if similarity >= threshold:
            results.append(
                {
                    "id": row[0],
                    "scope": row[1],
                    "type": row[2],
                    "learned": row[3],
                    "category": row[4],
                    "frequency": row[5],
                    "confidence": row[6],
                    "created_at": row[7],
                    "updated_at": row[8],
                    "promoted_to_claude_md": bool(row[9]),
                    "similarity": round(similarity, 3),
                }
            )

    # Sort by similarity (primary) and frequency (secondary)
    results.sort(key=lambda x: (x["similarity"], x["frequency"]), reverse=True)

    return results[:limit]


def search_instances(
    db_path: str,
    query: str,
    threshold: float = 0.3,
    limit: int = 10,
    scopes: list[str] = None,
    pattern_id: str = None,
) -> list[dict]:
    """Search for similar instances."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Build query
    sql = """
        SELECT id, pattern_id, scope, what, context, resolution, tags, created_at
        FROM instances WHERE 1=1
    """
    params = []

    if scopes:
        placeholders = ",".join("?" * len(scopes))
        sql += f" AND scope IN ({placeholders})"
        params.extend(scopes)

    if pattern_id:
        sql += " AND pattern_id = ?"
        params.append(pattern_id)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    # Calculate similarities
    results = []
    for row in rows:
        # Combine 'what' and 'context' for matching
        text = f"{row[3]} {row[4] or ''}"
        similarity = jaccard_similarity(query, text)

        if similarity >= threshold:
            results.append(
                {
                    "id": row[0],
                    "pattern_id": row[1],
                    "scope": row[2],
                    "what": row[3],
                    "context": row[4],
                    "resolution": row[5],
                    "tags": json.loads(row[6]) if row[6] else [],
                    "created_at": row[7],
                    "similarity": round(similarity, 3),
                }
            )

    # Sort by similarity
    results.sort(key=lambda x: x["similarity"], reverse=True)

    return results[:limit]


def get_linked_patterns(db_path: str, pattern_id: str) -> list[dict]:
    """Get patterns linked to the given pattern."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT p.id, p.learned, p.frequency, p.confidence, ml.relationship, ml.confidence
        FROM memory_links ml
        JOIN patterns p ON (
            (ml.to_id = p.id AND ml.from_id = ?)
            OR (ml.from_id = p.id AND ml.to_id = ?)
        )
        WHERE ml.from_type = 'pattern' AND ml.to_type = 'pattern'
    """,
        (pattern_id, pattern_id),
    )

    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "id": row[0],
            "learned": row[1],
            "frequency": row[2],
            "confidence": row[3],
            "relationship": row[4],
            "link_confidence": row[5],
        }
        for row in rows
    ]


def main():
    parser = argparse.ArgumentParser(description="Search memory database")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Database path")
    parser.add_argument("--query", required=True, help="Search query")
    parser.add_argument(
        "--type",
        choices=["pattern", "instance"],
        default="pattern",
        help="What to search",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.3, help="Similarity threshold"
    )
    parser.add_argument("--limit", type=int, default=10, help="Max results")
    parser.add_argument("--scopes", nargs="*", help="Filter by scopes")
    parser.add_argument("--exclude", nargs="*", help="Exclude these IDs")
    parser.add_argument("--category", help="Filter by category (patterns only)")
    parser.add_argument("--pattern-id", help="Filter by pattern (instances only)")
    parser.add_argument(
        "--expand-links", action="store_true", help="Include linked patterns"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if not Path(args.db_path).exists():
        print(f"[memory-search] Database not found: {args.db_path}")
        return

    if args.type == "pattern":
        results = search_patterns(
            args.db_path,
            args.query,
            args.threshold,
            args.limit,
            args.scopes,
            args.exclude,
            args.category,
        )

        # Expand links if requested
        if args.expand_links:
            for result in results:
                result["linked"] = get_linked_patterns(args.db_path, result["id"])
    else:
        results = search_instances(
            args.db_path,
            args.query,
            args.threshold,
            args.limit,
            args.scopes,
            args.pattern_id,
        )

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        if not results:
            print(
                f"[memory-search] No {args.type}s found above threshold {args.threshold}"
            )
            return

        print(f"[memory-search] Found {len(results)} {args.type}(s):\n")
        for r in results:
            if args.type == "pattern":
                print(
                    f"  {r['id']} (sim: {r['similarity']}, freq: {r['frequency']}, conf: {r['confidence']:.2f})"
                )
                print(f"    {r['learned']}")
                print(f"    category: {r['category']}, scope: {r['scope']}")
                if args.expand_links and r.get("linked"):
                    print(f"    linked to: {[link['id'] for link in r['linked']]}")
            else:
                print(f"  {r['id']} (sim: {r['similarity']})")
                print(f"    {r['what'][:80]}...")
                if r["pattern_id"]:
                    print(f"    pattern: {r['pattern_id']}")
            print()


if __name__ == "__main__":
    main()
