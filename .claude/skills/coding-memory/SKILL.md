---
name: coding-memory
description: Zettelkasten-inspired memory system for learning from coding sessions. Tracks patterns and instances, links related memories, and promotes high-frequency patterns to CLAUDE.md. Only loaded by scribe when learning signals are detected.
---

# Coding Memory Skill

> **Purpose**: Zettelkasten-inspired memory system for extracting learning patterns from coding sessions, tracking frequency, and promoting high-confidence patterns to CLAUDE.md.
> **Consumers**: scribe (conditional: learning_signals non-empty in session log)
> **Schemas**: `coding-memory/schemas/memory.schema.json`
> **Depends on**: None

## Contract

- ONLY load when `session_log.learning_signals` is non-empty
- ALWAYS search for existing patterns before creating new ones (threshold 0.85)
- Create links between related patterns after storing memories
- Check promotions after all signals processed (frequency >= 5, confidence >= 0.8)
- NEVER create duplicate patterns — match on root cause and solution, not surface text

A memory system that learns from mistakes, preferences, and patterns across coding sessions. Uses pattern-level tracking to correctly identify repeated learnings even when surface text differs.

## Core Concepts

### Patterns vs Instances

| Level | What It Is | Example |
|-------|------------|---------|
| **Pattern** | The generalized learning | "Check name-bank.md before naming functions" |
| **Instance** | A specific occurrence | "Used process_batch instead of select_batch in ops_batch.py" |

Multiple instances can map to the same pattern. Frequency is tracked at the pattern level.

### Scope

Memories have a `scope` tag:
- `universal` - Applies to all projects (e.g., "always test empty inputs")
- `project:{name}` - Project-specific (e.g., "producer block uses X pattern")

All memories live in one database. Filter by scope when querying.

## Database Location

```
{project}/.claude/memory/
├── memory.db           # SQLite database
└── embeddings/         # Cached embeddings (optional optimization)
```

## When This Skill Is Used

**Only loaded when learning signals exist.** Scribe checks `session_log.learning_signals` before loading this skill. If empty, skip memory extraction entirely.

## Learning Signal Types

| Signal Type | What Triggered It | What to Extract |
|-------------|-------------------|-----------------|
| `test_fix_cycle` | Tests failed, then passed after changes | What was wrong, what fixed it |
| `user_correction` | User edited agent-generated code | What user changed, infer preference |
| `stuck_then_solved` | Same invariant/test failed 3+ times, then passed | What was the blocker, what solved it |
| `deviation` | Agent deviated from plan | What gap existed in plan |
| `review_feedback` | User requested changes during review | What preference was revealed |

---

## Memory Extraction Process

### Step 1: Initialize Database

```bash
python .claude/scripts/memory-init.py
```

Creates the database if it doesn't exist.

### Step 2: Process Each Learning Signal

For each signal in `session_log.learning_signals`:

#### 2a. Extract the Learning

From the signal context, identify:
- **what**: What specifically happened
- **context**: Where it happened (file, function, task)
- **resolution**: How it was fixed
- **learned**: The generalized takeaway (THIS IS KEY)

Example extraction:
```
Signal: test_fix_cycle
Context: test_select_batch_empty failed 3 times

Extract:
- what: "select_batch returned None instead of empty list for empty input"
- context: "Implementing select_batch in producer/ops_batch.py"
- resolution: "Added early return: if not candidates: return []"
- learned: "Always handle empty collection inputs with early return"
```

#### 2b. Determine Pattern

Search for existing patterns that match this learning:

```bash
python .claude/scripts/memory-search.py \
  --query "Always handle empty collection inputs with early return" \
  --type pattern \
  --threshold 0.75 \
  --limit 5
```

Review candidates returned. For each, decide:
> "Is this new learning describing the same underlying pattern?"

**Same pattern if:**
- Same root cause
- Same prevention/solution
- Developer would call it "the same mistake"

**Different pattern if:**
- Different root causes
- Different solutions needed
- Would need separate rules in CLAUDE.md

#### 2c. Store Memory

If matching pattern found:
```bash
python .claude/scripts/memory-store.py add-instance \
  --pattern-id "pat-007" \
  --what "select_batch returned None instead of empty list" \
  --context "Implementing select_batch in producer/ops_batch.py" \
  --resolution "Added early return for empty input" \
  --scope "project:lpr-module" \
  --tags "testing,empty-input,producer" \
  --source-session ".claude/logs/batch-selection-chunk-01-log.json" \
  --source-signal "sig-001"
```

If no matching pattern:
```bash
python .claude/scripts/memory-store.py add-pattern \
  --learned "Always handle empty collection inputs with early return" \
  --category "testing" \
  --scope "universal" \
  --tags "testing,empty-input,edge-cases"

# Then add the instance linked to the new pattern
python .claude/scripts/memory-store.py add-instance \
  --pattern-id "{new_pattern_id}" \
  --what "..." \
  ...
```

### Step 3: Create Links

After adding memories, identify links to other patterns:

```bash
python .claude/scripts/memory-search.py \
  --query "Always handle empty collection inputs with early return" \
  --type pattern \
  --threshold 0.6 \
  --limit 10 \
  --exclude "{current_pattern_id}"
```

For similar patterns (but not same), create links:

```bash
python .claude/scripts/memory-store.py add-link \
  --from-id "pat-007" \
  --to-id "pat-003" \
  --relationship "similar" \
  --confidence 0.7
```

Link relationships:
- `similar` - Related patterns, might co-occur
- `caused_by` - This pattern often follows that one
- `led_to` - This pattern often causes that one
- `supersedes` - This pattern replaces/refines that one

### Step 4: Check Promotions

After all signals processed:

```bash
python .claude/scripts/memory-promote.py --check
```

This identifies patterns ready for promotion (frequency >= 5, confidence >= 0.8).

For each promotion candidate, append to CLAUDE.md:

```markdown
## Learned Rules

### Mistakes to Avoid
- **Always handle empty collection inputs with early return** (learned from 5 occurrences)
```

Then mark pattern as promoted:
```bash
python .claude/scripts/memory-store.py mark-promoted --pattern-id "pat-007"
```

---

## Memory Querying (For chunk-coder)

At session start or chunk start, query relevant memories:

```bash
python .claude/scripts/memory-query.py \
  --task "Implementing diversity scoring" \
  --files "producer/ops_batch.py,producer/ops_diversity.py" \
  --tags "producer,scoring" \
  --scopes "universal,project:lpr-module" \
  --max-tokens 500
```

Returns formatted memories for injection into context.

### Query Algorithm

1. Embed the task description
2. Search patterns by similarity (threshold 0.7)
3. Filter by scope
4. Expand via links (similar patterns)
5. Rank by: similarity × frequency × recency
6. Return until token budget reached

### Output Format

```
## Relevant Memories

### Patterns to Remember
- **Always handle empty collection inputs with early return** (5 occurrences, confidence: 0.85)
- **Check name-bank.md before naming functions** (3 occurrences, confidence: 0.72)

### Related Context
- Pattern "empty inputs" is similar to "null checking" (see also)
- Recent instance in producer/ops_batch.py (2 days ago)
```

---

## Pattern Matching Criteria

When deciding if a new learning matches an existing pattern:

### YES - Same Pattern
```
New: "Check name-bank.md before naming functions"
Existing: "Verify function names against name-bank.md"
→ SAME (both about checking name-bank before naming)
```

```
New: "Test empty list input"
Existing: "Handle empty collection edge case"
→ SAME (both about empty input handling)
```

### NO - Different Pattern
```
New: "Check name-bank.md before naming functions"
Existing: "Use descriptive variable names"
→ DIFFERENT (name-bank vs general naming style)
```

```
New: "Test empty list input"
Existing: "Test None input"
→ DIFFERENT (empty vs null - different edge cases)
```

---

## Confidence Calculation

Pattern confidence increases with confirmed instances:

```
Initial: 0.5
After 2nd instance: 0.5 + (1-0.5) × 0.15 = 0.575
After 3rd instance: 0.575 + (1-0.575) × 0.15 = 0.639
After 4th instance: 0.639 + (1-0.639) × 0.15 = 0.693
After 5th instance: 0.693 + (1-0.693) × 0.15 = 0.739
...
Maximum: 0.95
```

Confidence also affected by:
- LLM confirmation strength (scribe's certainty)
- Instance diversity (same pattern in different contexts = higher confidence)

---

## Promotion Criteria

Pattern promotes to CLAUDE.md when:
- `frequency >= 5` (occurred 5+ times)
- `confidence >= 0.8` (well-confirmed)
- `promoted_to_claude_md = false` (not already promoted)

Promoted patterns:
- Added to CLAUDE.md under "## Learned Rules"
- Stay in database (for context, linking, querying)
- Marked `promoted_to_claude_md = true`

---

## Database Maintenance

### Deduplication

Before adding a pattern, always search first. Threshold 0.85 for "same pattern" determination.

### Scope Migration

To extract universal patterns for a new project:
```bash
python .claude/scripts/memory-export.py \
  --scope universal \
  --min-frequency 3 \
  --output universal-memories.json
```

### Cleanup

Patterns with frequency=1 and age > 90 days can be archived:
```bash
python .claude/scripts/memory-cleanup.py --archive-stale
```

---

## Integration Points

### Scribe
- Loads this skill only when `learning_signals` is non-empty
- Extracts memories from signals
- Creates links
- Checks promotions
- Updates CLAUDE.md

### chunk-coder
- Queries memory at task start (via session-start hook or explicit query)
- Doesn't write to memory directly

### Hooks
- `session-start.py`: Queries relevant memories
- `post-tool-use.py`: Detects user corrections → adds to learning_signals
- `subagent-stop.py`: Triggers scribe with learning_signals

---

## Example Session

```
1. chunk-coder implements batch selection
2. Tests fail: "test_empty_input"
3. chunk-coder fixes: adds early return
4. Tests pass
5. Hook detects: test_fix_cycle signal added to session log
6. Chunk completes
7. subagent-stop hook triggers scribe
8. Scribe sees learning_signals is non-empty
9. Scribe loads coding-memory skill
10. Scribe extracts:
    - what: "select_batch didn't handle empty input"
    - learned: "Always handle empty collection inputs"
11. Scribe searches patterns → finds "pat-003: Handle edge cases first"
12. Scribe decides: similar but not same (edge cases is broader)
13. Scribe creates new pattern "pat-008: Handle empty collection inputs"
14. Scribe links pat-008 → pat-003 (similar)
15. Scribe checks promotions → pat-008 frequency=1, not ready
16. Scribe completes, memory saved
```
