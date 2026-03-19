---
globs: [".claude/context/**/*.json"]
---

# Context Packet Rules

## Purpose

Context packets are structured JSON summaries of codebase knowledge. They are written by explorer agents and consumed by planners, coders, and auditors. They reduce the need for repeated file reads across agent lifetimes.

## File Locations

| Type | Path | Written By |
|------|------|------------|
| Codebase context | `.claude/context/_codebase.json` | Explorer (initial scan) |
| Feature context | `.claude/context/{feature}-context.json` | Explorer (feature-scoped) |
| Query results | `.claude/context/queries/{topic}.json` | Explorer (on-demand) |

## Exploration Modes

### Targeted Exploration

Single explorer answers specific queries. Used when:
- A coder needs context for a specific task
- The orchestrator needs to understand a narrow area
- Query is well-scoped (e.g., "how does BatchProcessor handle errors?")

### Comprehensive Exploration

Parallel explorers scan the full codebase. Used when:
- Starting a new feature with no existing context
- The codebase has changed significantly since last exploration
- Multiple unrelated areas need mapping

## Required Fields

Context packets must include:

- **`generated_at`** — ISO 8601 timestamp for freshness tracking
- **`generated_by`** — Agent ID of the explorer that created the packet
- **`scope`** — What the packet covers (module, directory, cross-module)
- **`files_analyzed`** — List of files read to produce this context
- **`summary`** — Human-readable overview of findings
- **`key_patterns`** — Architectural patterns, conventions, idioms found
- **`dependencies`** — Internal and external dependencies discovered
- **`open_questions`** — Anything the explorer couldn't resolve

## Rules

- Context packets are **append-only during a workflow** — never overwrite an existing packet mid-workflow
- If context is stale (>1 hour or codebase changed), create a new packet rather than updating
- Explorers MUST record `files_analyzed` — this enables freshness checks and prevents duplicate reads
- Query results in `queries/` are scoped to a single question — keep them focused
- Codebase context (`_codebase.json`) is a broad overview — do not put task-specific details here
- Feature context includes design doc references, relevant code paths, and integration points
- If an explorer cannot answer a query, write the packet with `open_questions` populated rather than guessing
- Context packets must be valid JSON — explorers write files directly, no schema validation at write time, but consumers may validate on read
