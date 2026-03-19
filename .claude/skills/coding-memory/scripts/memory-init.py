#!/usr/bin/env python3
"""
Memory database initialization script.

Creates the SQLite database with the required schema for
patterns, instances, and links.

Usage:
    python memory-init.py [--db-path PATH]
"""

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

DEFAULT_DB_PATH = ".claude/memory/memory.db"


SCHEMA = """
-- Patterns: generalized learnings
CREATE TABLE IF NOT EXISTS patterns (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('mistake', 'preference', 'pattern', 'insight')),
    learned TEXT NOT NULL,
    category TEXT NOT NULL,
    trigger_patterns TEXT,  -- JSON array
    frequency INTEGER DEFAULT 1,
    confidence REAL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    promoted_to_claude_md INTEGER DEFAULT 0,
    promotion_text TEXT,
    embedding BLOB  -- Dense vector for semantic search
);

-- Instances: specific occurrences of patterns
CREATE TABLE IF NOT EXISTS instances (
    id TEXT PRIMARY KEY,
    pattern_id TEXT,
    scope TEXT NOT NULL,
    what TEXT NOT NULL,
    context TEXT,
    resolution TEXT,
    tags TEXT,  -- JSON array
    source_session TEXT,
    source_signal TEXT,
    created_at TEXT NOT NULL,
    embedding BLOB,
    FOREIGN KEY (pattern_id) REFERENCES patterns(id)
);

-- Links: relationships between memories
CREATE TABLE IF NOT EXISTS memory_links (
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    from_type TEXT NOT NULL CHECK (from_type IN ('pattern', 'instance')),
    to_type TEXT NOT NULL CHECK (to_type IN ('pattern', 'instance')),
    relationship TEXT NOT NULL CHECK (relationship IN ('similar', 'caused_by', 'led_to', 'supersedes')),
    confidence REAL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id, relationship)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_patterns_scope ON patterns(scope);
CREATE INDEX IF NOT EXISTS idx_patterns_type ON patterns(type);
CREATE INDEX IF NOT EXISTS idx_patterns_category ON patterns(category);
CREATE INDEX IF NOT EXISTS idx_patterns_frequency ON patterns(frequency DESC);
CREATE INDEX IF NOT EXISTS idx_patterns_promoted ON patterns(promoted_to_claude_md);
CREATE INDEX IF NOT EXISTS idx_patterns_confidence ON patterns(confidence DESC);

CREATE INDEX IF NOT EXISTS idx_instances_pattern ON instances(pattern_id);
CREATE INDEX IF NOT EXISTS idx_instances_scope ON instances(scope);

CREATE INDEX IF NOT EXISTS idx_links_from ON memory_links(from_id);
CREATE INDEX IF NOT EXISTS idx_links_to ON memory_links(to_id);

-- Metadata table for database info
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def init_database(db_path: str) -> None:
    """Initialize the memory database."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Execute schema
    cursor.executescript(SCHEMA)

    # Set metadata
    now = datetime.now().isoformat()
    cursor.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("schema_version", "1.0"),
    )
    cursor.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("created_at", now),
    )
    cursor.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("last_updated", now),
    )

    conn.commit()
    conn.close()

    print(f"[memory-init] Database initialized: {db_path}")


def get_stats(db_path: str) -> dict:
    """Get database statistics."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    stats = {}

    cursor.execute("SELECT COUNT(*) FROM patterns")
    stats["patterns"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM instances")
    stats["instances"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM memory_links")
    stats["links"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM patterns WHERE promoted_to_claude_md = 1")
    stats["promoted"] = cursor.fetchone()[0]

    cursor.execute("SELECT AVG(frequency) FROM patterns")
    avg_freq = cursor.fetchone()[0]
    stats["avg_frequency"] = round(avg_freq, 2) if avg_freq else 0

    conn.close()

    return stats


def main():
    parser = argparse.ArgumentParser(description="Initialize memory database")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Database path")
    parser.add_argument("--stats", action="store_true", help="Show database stats")
    args = parser.parse_args()

    if args.stats:
        if Path(args.db_path).exists():
            stats = get_stats(args.db_path)
            print("[memory-init] Database stats:")
            print(f"  Patterns: {stats['patterns']}")
            print(f"  Instances: {stats['instances']}")
            print(f"  Links: {stats['links']}")
            print(f"  Promoted: {stats['promoted']}")
            print(f"  Avg frequency: {stats['avg_frequency']}")
        else:
            print(f"[memory-init] Database does not exist: {args.db_path}")
    else:
        init_database(args.db_path)


if __name__ == "__main__":
    main()
