# Logging and Commit

> **Purpose**: Provides the scribe with a parameterized commit workflow for all agent types, commit message formatting, handoff-aware commits, and memory extraction triggers.
> **Consumers**: scribe
> **Schemas**: `schemas/session-log.schema.json` (for reading)
> **Depends on**: `session-lifecycle` (for context pressure and handoff protocols), `coding-memory` (conditional: when learning_signals are non-empty)

## What You Learn From This Skill
- Parameterized commit workflow that handles all agent types with one flow
- Commit message format with session log references and traceability
- Handoff-aware commits when context handoffs occurred
- Memory extraction trigger and learning signal processing
- Persistent scribe lifecycle: accumulating context across multiple commits

## Contract
- Commit directly on task_assign — the lead has already obtained user approval
- ALWAYS include session log reference in commit message
- ALWAYS send task_complete after commit with commit hash
- When committing chunk-N (N > 1), reference relevant changes from previous chunks if applicable
- Persist between commits — do NOT request shutdown after task_complete; go idle and wait
- Load coding-memory skill only when learning_signals are non-empty

---

## Commit Workflow (All Agent Types)

Every commit follows the same 7-step flow regardless of which agent completed.

### Input

Task message from lead:

```json
{
  "agent": "codebase-explorer|plan-architect|chunk-coder",
  "feature": "feature-name",
  "trigger": "completion",
  "context": {
    "chunk": "chunk-01",
    "session_log_path": ".claude/logs/...",
    "files_modified": [...],
    "test_results": {...},
    "learning_signals": [...],
    "user_approved": true
  }
}
```

### Steps

1. **Read output** from the completed agent (see paths table below)
2. **Write log file** directly using Write tool (see paths table and log formats below)
3. **Stage files**: `git add {files}` via Bash
4. **Execute `git commit`** via Bash (user already approved through the lead)
5. **Send task_complete** with commit hash and summary, then **go idle**

### Agent-Specific Paths

| Agent | Output to Read | Log to Write | Staging Paths | Commit Type |
|-------|---------------|-------------|---------------|-------------|
| codebase-explorer | `.claude/context/_codebase.json` or feature context | `.claude/logs/exploration-log.json` | `.claude/context/` `.claude/logs/exploration-log.json` | `chore(context)` |
| plan-architect | `.claude/plans/{feature}-plan.json` | `.claude/logs/planning-log.json` | `.claude/plans/` `.claude/context/` `.claude/logs/planning-log.json` | `docs(plan)` |
| chunk-coder | `.claude/logs/{feature}-{chunk}-log.json` | Finalize existing session log | Implementation files + session log | `feat\|fix\|refactor` |

### Log Formats

**Exploration log** (`.claude/logs/exploration-log.json`):
```json
{
  "explorations": [{
    "id": "exp-001",
    "timestamp": "2026-02-03T14:00:00Z",
    "type": "codebase",
    "output_file": ".claude/context/_codebase.json"
  }]
}
```

**Planning log** (`.claude/logs/planning-log.json`):
```json
{
  "plans": [{
    "id": "plan-001",
    "feature": "batch-selection",
    "timestamp": "2026-02-03T14:30:00Z",
    "plan_file": ".claude/plans/batch-selection-plan.json",
    "status": "approved",
    "chunks_created": 4,
    "total_invariants": 12
  }]
}
```

**Implementation log** — finalize the chunk-coder's existing session log by setting `status: "completed"` and adding `completed_at` timestamp.

---

## Commit Message Format

```
{type}({scope}): {chunk.delivers}

Chunk: {chunk.id} - {chunk.name}
Feature: {feature}

Changes:
{list of files}

Tests: {count} written, all passing
Invariants: {count}/{total} verified

{if learning_signals:}
Learning artifacts:
- {signal_type}: {brief description}
{endif}

Session log: .claude/logs/{feature}-{chunk}-log.json
```

**Types:** `chore(context)` (exploration), `docs(plan)` (planning), `feat|fix|refactor` (implementation), `test` (test-only)

---

## Handoff-Aware Commits

When the task context includes `had_handoff: true`:

1. **Read session log** — inspect the `handoffs` field
2. **Add handoff history** to the commit message body:
   ```
   Handoff History:
   - Handoff at 71% context (chunk-coder-abc123)
   - Continued by chunk-coder-def456
   ```
3. **Update session log** with `continuation_agent` and `completed_at`
4. **Clean up** handoff packet from `.claude/temp/` (optional)

---

## Memory Extraction (Conditional)

**Only when `learning_signals` is non-empty in the session log.**

1. Load `coding-memory` skill
2. Run extraction:
   ```bash
   python .claude/scripts/extract_memory.py \
     --signal-type "{type}" \
     --what "{what}" \
     --context "{file}" \
     --resolution "{fix}" \
     --learned "{lesson}" \
     --category "{category}" \
     --scope "{scope}"
   ```
3. Check promotions:
   ```bash
   python .claude/scripts/check_promotions.py
   ```

**Signal types**: `test_fix_cycle`, `user_correction`, `pattern_discovery`, `api_gotcha`

**Data fields per signal**: signal_type, what (went wrong), context (file/function), resolution (fix), learned (lesson), category (testing/error_handling/performance), scope (universal/project:{name}/file:{path})

---

## Persistent Scribe Awareness

The scribe persists across the workflow session, receiving multiple task_assign messages and accumulating context.

### What Persistence Enables

1. **Cross-chunk commit references**: When committing chunk-N (N > 1), reference previous chunks' changes in the commit message (e.g., "Extends BatchCandidate from chunk-01")

2. **Learning signal correlation**: Correlate repeated mistake patterns across chunks during memory extraction

3. **Session-wide progress summaries**: On orchestrator or user request, produce a summary of all commits, files changed, and tests passing

### Internal State

Maintain a running record of all commits made this session:
```
[
  { "chunk": "chunk-01", "hash": "abc123", "files": [...], "summary": "..." },
  { "chunk": "chunk-02", "hash": "def456", "files": [...], "summary": "..." }
]
```

### Lifecycle Rules

- After task_complete: go idle, do NOT request shutdown
- On commit for chunk-N (N > 1): check if current files overlap with previous chunk files — reference if so
- On context pressure: write commit history to `.claude/temp/scribe-commit-history.json` before handoff so replacement scribe inherits cross-chunk awareness
