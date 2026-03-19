---
name: context-packets
description: Schema definitions and instructions for creating token-efficient context packets. MUST be loaded by any agent that reads or writes context packets (.claude/context/*.json). Provides schemas for codebase overview, query results, and feature context.
---

# Context Packets Skill

> **Purpose**: Defines how to create, read, and update token-efficient context packets.
> **Consumers**: codebase-explorer, plan-architect, chunk-coder
> **Schemas**: `schemas/codebase.schema.json`, `schemas/query-result.schema.json`, `schemas/feature-context.schema.json`
> **Depends on**: None
> **Reference**: `exploration-modes.md` in this directory (explorer-specific mode processes — load when generating packets)

## What You Learn From This Skill

- Core principles for token-efficient context packet design
- Packet types, schemas, and field conventions
- How to read and cross-reference packets
- Validation rules

## Contract

- All context packets MUST follow the schema for their type
- File references MUST use `path:line` format
- Prose descriptions MUST be <100 characters
- `manual_notes` MUST be preserved during incremental updates
- Every packet MUST include `meta` with `type`, `created`, `updated`, `schema_version`
- After writing any packet, send `task_complete` to lead with output file paths

---

Context packets are structured JSON files that transfer information between agents with maximum clarity and minimum tokens.

## Core Principles

1. **Structured over prose** - Use fields, not sentences
2. **References over duplication** - Point to files, don't copy content
3. **Hierarchical compression** - Nest related items
4. **Explicit read-more pointers** - Tell the reader when to dig deeper

## Packet Types

### 1. Codebase Overview (`_codebase.json`)

**Purpose**: Persistent understanding of project structure, types, patterns
**Location**: `.claude/context/_codebase.json`
**Lifespan**: Lives forever, updated incrementally
**Schema**: `schemas/codebase.schema.json`

### 2. Query Result (`queries/*.json`)

**Purpose**: Answer to a specific question from another agent (including BLOCKED requests)
**Location**: `.claude/context/queries/{topic-slug}.json`
**Lifespan**: Ephemeral - read by requester, can be deleted after
**Schema**: `schemas/query-result.schema.json`

### 3. Feature Context (`{feature}-context.json`)

**Purpose**: Exploration results for a specific feature/design doc
**Location**: `.claude/context/{feature}-context.json`
**Lifespan**: Lives until feature is completed
**Schema**: `schemas/feature-context.schema.json`

## Writing Packets

### Token Efficiency Rules

**DO:**
```json
{"file": "producer/models.py:15-30", "exports": ["RoiImage", "BatchCandidate"]}
```

**DON'T:**
```json
{"description": "The file producer/models.py contains the RoiImage class defined on lines 15-30 and also exports BatchCandidate which is used for batch selection."}
```

**DO:**
```json
{
  "pattern": "error_handling",
  "style": "custom_exceptions",
  "base_class": "common/errors.py:AppError",
  "catch_at": "pipeline boundaries",
  "read_if": "adding new error types"
}
```

**DON'T:**
```json
{
  "error_handling": "The codebase uses custom exception classes that inherit from AppError defined in common/errors.py. These exceptions are typically caught at pipeline boundaries. If you need to add a new error type, you should read the AppError class first."
}
```

### Field Conventions

| Field | Format | Example |
|-------|--------|---------|
| File reference | `path:line` or `path:start-end` | `"producer/config.py:45"` |
| Type reference | `module.TypeName` | `"common.models.RoiImage"` |
| List of names | Array of strings | `["TrackId", "RoiImage"]` |
| Conditional read | `read_if` field | `"read_if": "implementing OCR"` |
| Confidence | `confidence` field (0-1) | `"confidence": 0.9` |

### Required Metadata

Every packet MUST have:
```json
{
  "meta": {
    "type": "codebase|query|feature",
    "created": "ISO8601",
    "updated": "ISO8601",
    "schema_version": "1.0"
  }
}
```

## Reading Packets

### Loading Priority

1. Check `meta.updated` - if stale (>7 days for codebase), suggest refresh
2. Read `summary` field first - often sufficient
3. Follow `read_if` conditions - only load details when relevant
4. Use `detail_in` pointers - fetch source only when needed

### Cross-References

Packets reference each other:
```json
{
  "related_context": {
    "codebase": ".claude/context/_codebase.json",
    "feature": ".claude/context/batch-selection-context.json"
  },
  "load_if_needed": ["types.from_common", "patterns.error_handling"]
}
```

When you see `load_if_needed`, only read those sections from the referenced packet if your current task requires them.

## Validation

Before writing any packet, validate against the schema:
```bash
python3 .claude/skills/context-packets/scripts/validate-packet.py <packet-file>
```

If the skill scripts aren't available locally, validate manually:
1. Check `meta` section exists with required fields
2. Ensure no prose descriptions > 100 characters
3. Verify file references use `path:line` format
4. Confirm all arrays are arrays (not comma-separated strings)

## Schema Reference

Load the specific schema file for detailed field definitions:
- **Codebase**: `schemas/codebase.schema.json`
- **Query Result**: `schemas/query-result.schema.json`
- **Feature Context**: `schemas/feature-context.schema.json`
