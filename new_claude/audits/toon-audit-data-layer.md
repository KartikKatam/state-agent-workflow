# TOON Audit: Data Layer

**Auditor:** data-layer-auditor
**Date:** 2026-03-11
**Scope:** Every JSON data artifact that agents READ during V2 workflow execution
**Goal:** Identify all TOON (pipe-delimited tabular) and compact-key conversion opportunities for context injection

---

## Executive Summary

**Total data artifacts audited:** 55+ Pydantic models, 17 JSON schemas, 8 state machines, 50+ JSON data files, 32 injection points
**Total token savings potential:** ~18,000-22,000 tokens per implementation session (72% reduction)
**Highest-impact conversions:** State machine transitions (TOON), implementation plan chunks (TOON), message bus log (TOON), agent state (compact keys), session log arrays (TOON)

| Category | Artifacts | TOON Candidates | Compact Key Candidates | Est. Savings (tokens) |
|----------|-----------|-----------------|------------------------|-----------------------|
| State machines | 8 files, 237 transitions | 190 transitions (80%) | 112 state definitions | ~1,800/agent spawn |
| Pydantic schemas | 55 models, 14 array-of-object fields | 14 strong candidates | 6 single-object candidates | ~2,500/session |
| JSON schemas | 17 files, 60+ array definitions | 40+ arrays | 5 object schemas | (defines formats, not injected) |
| Context packets | 2 files (codebase + feature) | 5 nested arrays | 2 root objects | ~800/session |
| Implementation plans | 1 file (61KB) | chunks array (14 items) | meta object | ~13,800/chunk spawn |
| Session logs | 14+ files | 8 arrays per log | root object | ~600/orchestrator check |
| Research index | 1 index + 11 entries | persistent/ephemeral arrays | search_index | ~100/researcher spawn |
| Message bus | JSONL log (unbounded) | message entries | — | ~600/injection (15-entry window) |
| Handoff payloads | per-agent | 4 list fields | root object | ~300/handoff |

---

## 1. State Machine Definitions (8 files, ~27K tokens total)

### Source files: `state-machines/*.json`
### Injection: Daemon loads at agent spawn; compact representation injected via hooks

State machines are the **single largest structured data category**. Each machine has a `transitions` array — the primary TOON target.

#### 1.1 Inventory

| Machine | States | Transitions | Universal | File Size | Tokens |
|---------|--------|-------------|-----------|-----------|--------|
| coder.json | 23 | 47 | 1 | 20,182 | 5,046 |
| explorer.json | 13 | 34 | 1 | 17,382 | 4,346 |
| researcher.json | 12 | 37 | 1 | 17,029 | 4,257 |
| tester.json | 12 | 21 | 1 | 13,776 | 3,444 |
| system.json | 19 | 32 | 0 | 12,766 | 3,192 |
| strategist.json | 16 | 19 | 1 | 10,956 | 2,739 |
| auditor-phase.json | 11 | 15 | 1 | 10,942 | 2,736 |
| auditor-task.json | 6 | 11 | 1 | 5,631 | 1,408 |
| **TOTAL** | **112** | **216+21u** | **7** | **108,664** | **27,166** |

#### 1.2 TOON Conversion: Transitions Array

**Before** (JSON, coder.json excerpt — ~180 tokens for 3 transitions):
```json
[
  {"from_state": "SPAWNED", "to_state": "CONTEXT_LOADED", "trigger": "context_loaded", "guards": ["guard_files_read"], "actions": [], "description": "Initial context ingestion complete"},
  {"from_state": "CONTEXT_LOADED", "to_state": "RED_WRITING", "trigger": "start_tests", "guards": [], "actions": ["action_set_test_globs"], "description": "Begin writing failing tests"},
  {"from_state": "RED_WRITING", "to_state": "RED_VERIFIED", "trigger": "tests_fail_as_expected", "guards": ["guard_pytest_exit_nonzero"], "actions": [], "description": "Tests confirmed failing"}
]
```

**After** (TOON — ~75 tokens for same 3 transitions):
```
# TRANSITIONS
from|to|trigger|guards|actions|desc
SPAWNED|CONTEXT_LOADED|context_loaded|guard_files_read||Initial context ingestion complete
CONTEXT_LOADED|RED_WRITING|start_tests||action_set_test_globs|Begin writing failing tests
RED_WRITING|RED_VERIFIED|tests_fail_as_expected|guard_pytest_exit_nonzero||Tests confirmed failing
```

**Token savings:** ~58% per transition array

#### 1.3 Compact Keys: State Definitions

**Before** (JSON — ~90 tokens per state):
```json
{"name": "IMPLEMENTATION", "description": "Writing code to pass tests", "write_allowed": true, "write_globs": ["$source_globs"], "read_globs_exclude": ["$test_globs"], "blocked_tools": [], "think_on_exit": false}
```

**After** (compact keys — ~45 tokens):
```
# STATE n=name d=desc w=write wg=write_globs rx=read_exclude bt=blocked tx=think_exit
n:IMPLEMENTATION d:Writing code to pass tests w:yes wg:$source_globs rx:$test_globs
```

**Token savings:** ~50% per state definition

#### 1.4 Full Compact State Context (what agents see)

**Before** (~1,400 tokens for current state + transitions):
```json
{
  "current_state": "IMPLEMENTATION",
  "state_info": {"name": "IMPLEMENTATION", "description": "...", "write_allowed": true, "write_globs": ["$source_globs"], ...},
  "available_transitions": [
    {"to_state": "TDD_GREEN", "trigger": "tests_pass", "guards": ["guard_pytest_exit_zero"]},
    {"to_state": "RED_FAILED", "trigger": "tests_fail_unexpected", "guards": []},
    {"to_state": "GREEN_PROGRESS_CHECK", "trigger": "progress_check", "guards": []}
  ]
}
```

**After** (~420 tokens):
```
STATE: IMPLEMENTATION
DESC: Writing code to pass tests
WRITE: yes | GLOBS: $source_globs | READ_EXCLUDE: $test_globs
TRANSITIONS:
  -> TDD_GREEN (tests_pass) [guard: pytest_exit_zero]
  -> RED_FAILED (tests_fail_unexpected)
  -> GREEN_PROGRESS_CHECK (progress_check)
```

**Savings:** ~70% (1,400 -> 420 tokens)
**Frequency:** Injected at every PreToolUse check (high frequency!)
**Implementation:** `format_state_context()` in `hooks/utils/state_helpers.py`

---

## 2. Pydantic Models (55 models across 14 files)

### Source files: `schemas/*.py`
### Injection: Via hooks (SessionStart, SubagentStart, PostToolUse, PreToolUse)

**Key finding:** No models have `to_compact()` or `to_toon()` methods. All use standard `model_dump()` without `exclude_defaults=True`.

#### 2.1 TOON Candidates (Arrays of Homogeneous Objects)

| Model | Array Field | Item Type | Typical Size | TOON Fields | Frequency |
|-------|-------------|-----------|--------------|-------------|-----------|
| SessionLog | `state_history` | StateEntry | 7-15 items | state\|timestamp\|details | Per-chunk resume |
| SessionLog | `tests_written` | TestEntry | 10-25 items | name\|file\|status\|category\|failure_count | Per-chunk resume |
| SessionLog | `files_modified` | FileEntry | 3-10 items | path\|action\|in_scope\|lines_changed | Per-chunk resume |
| SessionLog | `learning_signals` | LearningSignal | 0-5 items | id\|type\|context\|timestamp\|extracted | Scribe extraction |
| SessionLog | `decisions_made` | Decision | 2-8 items | decision\|reason\|source\|locked | Per-chunk resume |
| SessionLog | `deviations` | Deviation | 0-3 items | type\|description\|reason\|user_approved | Per-chunk resume |
| SessionLog | `key_files_read` | KeyFileEntry | 5-15 items | path\|relevance | Per-chunk resume |
| Handoff | `key_decisions` | HandoffDecision | 2-5 items | decision\|reason\|source\|locked | Handoff event |
| Handoff | `files_modified` | HandoffFileEntry | 3-10 items | path\|state | Handoff event |
| Handoff | `user_preferences` | UserPreferenceEntry | 0-3 items | preference\|context | Handoff event |
| WorkflowState | `phase_history` | PhaseHistoryEntry | 5-20 items | state\|entered_at\|exited_at | Orchestrator status |
| WorkflowState | `remediation_pass_history` | RemediationCycleEntry | 0-5 items | cycle\|pass_count\|total_scenarios\|timestamp | Orchestrator status |
| StateMachineDefinition | `states` | StateDefinition | 6-23 items | (see Section 1.3) | Agent spawn |
| StateMachineDefinition | `transitions` | TransitionDefinition | 11-47 items | (see Section 1.2) | Agent spawn |

**Example — SessionLog.tests_written TOON conversion:**

Before (JSON, 25 tests — ~800 tokens):
```json
[
  {"name": "test_batch_processor_init", "file": "tests/ptc/test_batch.py", "status": "pass", "category": "unit", "failure_count": 0, "failure_messages": null},
  {"name": "test_batch_filter_valid", "file": "tests/ptc/test_batch.py", "status": "pass", "category": "unit", "failure_count": 1, "failure_messages": ["AssertionError: expected 3, got 2"]},
  ...
]
```

After (TOON — ~340 tokens):
```
# TESTS_WRITTEN
name|file|status|cat|fails|fail_msgs
test_batch_processor_init|tests/ptc/test_batch.py|pass|unit|0|
test_batch_filter_valid|tests/ptc/test_batch.py|pass|unit|1|AssertionError: expected 3, got 2
...
```

**Savings:** ~58% on test arrays

#### 2.2 Compact Key Candidates (Single Objects)

| Model | Typical Size (tokens) | Key Fields for Compact | Frequency | Est. Savings |
|-------|-----------------------|------------------------|-----------|--------------|
| AgentState | ~65 | id, role, model, status, current_state, context_usage_pct, worktree | Every PreToolUse | ~50% (~33 tokens) |
| WorkflowState | ~120 | workflow_id, feature, current_state, started_at, active_agents | Orchestrator checks | ~40% (~48 tokens) |
| Handoff (root) | ~200 | agent_id, role, reason, current_state, resume_instructions, work_summary | Handoff events | ~35% (~70 tokens) |
| HookOutput | ~80 | hook_event, agent_id, hook_specific_output | Every hook return | ~30% (~24 tokens) |
| Preferences | ~100 | global_prefs (flattened), by_role | At spawn | ~25% (~25 tokens) |
| AnnotationEntry | ~60 | priority, message, source, target_agent | Per annotation | ~35% (~21 tokens) |

**Example — AgentState compact conversion:**

Before (~65 tokens):
```json
{"id": "coder-ptc-chunk01-a1b2", "role": "coder", "model": "opus-4-6", "spawned_at": "2026-03-11T10:00:00Z", "status": "active", "current_state": "IMPLEMENTATION", "phase": "p1", "task": "t3", "context_usage_pct": 45.2, "input_tokens": 12000, "output_tokens": 3400, "worktree": "/tmp/wt-ptc-01", "base_branch": "main", "parent_agent_id": null, "completed_subagents": [], "pending_critical_annotation": null, "pending_handoff": false, "last_think_chosen": null, "last_think_state": null, "pending_validation_error": null}
```

After with `exclude_defaults=True` + compact keys (~28 tokens):
```
# AGENT id=id r=role m=model s=status st=state ph=phase t=task ctx=context_% wt=worktree
id:coder-ptc-chunk01-a1b2 r:coder m:opus-4-6 s:active st:IMPLEMENTATION ph:p1 t:t3 ctx:45 wt:/tmp/wt-ptc-01
```

**Savings:** ~57% (65 -> 28 tokens)

#### 2.3 Message Protocol (11 message types, discriminated union)

Messages are logged to `~/.claude/logs/message-bus.jsonl` and injected via SubagentStart hook (last 15 entries).

**Before** (15-entry message bus window, JSON — ~900 tokens):
```json
[
  {"type": "task_assign", "from_agent": "orchestrator-main-abc-1234", "to_agent": "coder-ptc-chunk01-a1b2", "timestamp": "2026-03-11T10:00:00Z", "payload": {"task_type": "implement", "instructions": "Implement chunk-01: batch processor foundation"}},
  {"type": "status_update", "from_agent": "coder-ptc-chunk01-a1b2", "to_agent": "orchestrator-main-abc-1234", "timestamp": "2026-03-11T10:05:00Z", "payload": {"phase": "red_writing", "progress": "3/7 tests written", "context_pressure": false}},
  ...
]
```

**After** (TOON — ~375 tokens):
```
# MESSAGE_BUS (last 15)
type|from|to|time|summary
task_assign|orchestrator-main-abc-1234|coder-ptc-chunk01-a1b2|10:00|implement chunk-01: batch processor foundation
status_update|coder-ptc-chunk01-a1b2|orchestrator-main-abc-1234|10:05|red_writing 3/7 tests written
task_complete|coder-ptc-chunk01-a1b2|orchestrator-main-abc-1234|10:45|success: 7/7 tests pass, 3 files modified
info_request|coder-ptc-chunk02-c3d4|orchestrator-main-abc-1234|11:00|blocking: need batch_processor.py API signature
...
```

**Savings:** ~58% (900 -> 375 tokens)
**Frequency:** Injected at every SubagentStart (every agent spawn)
**Implementation:** `to_toon("MESSAGE_BUS", messages, ["type","from","to","time","summary"])` in `subagent_start.py`

---

## 3. JSON Schema Files (17 files — defines formats, not directly injected)

### Source files: `.claude/schemas/*.json`, `.claude/skills/*/schemas/*.json`, `new_claude/skills/*/schemas/*.json`

JSON schemas define the structure of data artifacts. They are used for **validation only** — never injected into agent context. However, they document which data shapes exist and where TOON/compact opportunities lie.

#### 3.1 Schemas with Major TOON-Eligible Arrays

| Schema | Array Property | Items per Instance | Injection Context |
|--------|---------------|-------------------|-------------------|
| session-log.schema.json | phase_history, files_modified, tests_written, learning_signals, deviations, decisions_made, key_files_read | 3-25 per array | Chunk resume |
| planning-log.schema.json | phase_history, edit_history, design_deviations, files_created, decisions_made, learning_signals | 2-10 per array | Planning review |
| implementation-plan.schema.json (skills/) | chunks (14), tasks per chunk (4-5), invariants per chunk (3), module_tests arrays | 3-14 per array | Chunk assignment |
| implementation-plan.schema.json (new_claude/) | phases, tasks per phase, concept_map, alternatives_considered, design_tests | 2-10 per array | Phase planning |
| memory.schema.json | patterns, instances, links | 5-50+ per array | Scribe extraction |
| persistent.schema.json | endpoints, classes, examples, sources | 5-20+ per array | Researcher query |
| codebase.schema.json | structure.blocks, types.shared, configs, name_bank.entries | 2-10 per array | Exploration |
| feature-context.schema.json | touchpoints, dependencies.*, new_items.*, patterns_to_follow | 3-15 per array | Feature planning |
| query-result.schema.json | answer.details, answer.code_snippets, sources | 2-10 per array | Explorer response |
| tdd-chunk.schema.json | pass_a, pass_b, pass_c, cross_chunk_continuity | 1-10 per array | TDD delegation |
| history-context.schema.json | design_decisions, failed_approaches, gotchas, patterns_to_follow | 1-5 per array | Planning/coding |

#### 3.2 Compact Key Candidate Schemas

| Schema | Object Type | Key Compaction Target |
|--------|-------------|----------------------|
| design-review.schema.json | Single review object | feature, status, reviewed_at, reviewed_by |
| index.schema.json | search_index (inverted) | keyword -> [entry_ids] already compact |
| ephemeral.schema.json | Single query result | id, query, answer, confidence |
| targeted.schema.json | Delegation prompt | task, known_context, output_contract |
| guided.schema.json | Delegation prompt | task, known_context, unknowns |

---

## 4. Context Packets (2 active files)

### Source files: `.claude/context/*.json`
### Injection: SessionStart hook, loaded once per session

#### 4.1 Codebase Context (`_codebase.json` — 7,245 chars, 1,811 tokens)

**Structure:**
```
meta (400 chars) | structure.blocks[4] (3,500 chars) | types (1,200 chars) | configs[1] (800 chars) | patterns (700 chars) | rest (645 chars)
```

**TOON candidates within:**
- `structure.blocks` (4 items): name|path|purpose|key_files — small array, marginal savings
- `configs` (1 item): too small
- `name_bank.entries` (48 names): name|type|file — good candidate

**Before** (name_bank, ~400 tokens):
```json
{"entries": [{"name": "BatchProcessor", "type": "class", "file": "src/batch.py"}, {"name": "apply_filters", "type": "function", "file": "src/batch.py"}, ...]}
```

**After** (TOON, ~180 tokens):
```
# NAME_BANK
name|type|file
BatchProcessor|class|src/batch.py
apply_filters|function|src/batch.py
...
```

**Savings:** ~55% on name_bank array (~220 tokens)
**Frequency:** Once per session (low frequency, but every agent pays this cost)

#### 4.2 Feature Context (`ptc-context.json` — 8,472 chars, 2,118 tokens)

**TOON candidates within:**
- `mcp_tools` (5 items): name|description|parameters — moderate
- `touchpoints.ptc_server` (8 items): file|reason|lines — good candidate
- `touchpoints.workflow_repo` (3 items): too small

**Before** (touchpoints, ~350 tokens):
```json
{"ptc_server": [{"file": "src/server.py", "reason": "Main entry point", "lines": "1-50"}, ...]}
```

**After** (TOON, ~160 tokens):
```
# TOUCHPOINTS_PTC
file|reason|lines
src/server.py|Main entry point|1-50
src/executor.py|REPL execution|1-120
...
```

**Savings:** ~54% on touchpoints (~190 tokens)

---

## 5. Implementation Plans (1 active file — HIGHEST IMPACT)

### Source file: `.claude/plans/ptc-plan.json` (61,255 chars, 15,314 tokens)
### Injection: Loaded by chunk-coder at spawn; orchestrator queries for routing

This is the **single largest data artifact** injected into agent context.

#### 5.1 Structure Breakdown

| Section | Size (chars) | Tokens | TOON? |
|---------|-------------|--------|-------|
| meta + feature | 1,200 | 300 | No (small object) |
| chunks[14] | 55,000 | 13,750 | **YES** |
| global_invariants[4] | 2,400 | 600 | Yes (small) |
| quality_gates[3] | 1,800 | 450 | Yes (small) |
| module_tests | 855 | 214 | No (small) |

#### 5.2 Chunks Array — TOON Conversion

**Before** (single chunk — ~1,000 tokens):
```json
{
  "id": "chunk-01",
  "name": "Batch Processor Foundation",
  "purpose": "foundational",
  "delivers": "Core BatchProcessor class with select_batch() and apply_filters()",
  "description": "Implement the base batch processing class...",
  "invariants": [
    {"id": "inv-01-01", "description": "BatchProcessor accepts config parameter"},
    {"id": "inv-01-02", "description": "select_batch returns list of Frame objects"},
    {"id": "inv-01-03", "description": "apply_filters respects filter chain order"}
  ],
  "tasks": [
    {"id": "t01-01", "description": "Create BatchProcessor class with __init__", "estimated_time": "15m"},
    {"id": "t01-02", "description": "Implement select_batch method", "estimated_time": "30m"},
    {"id": "t01-03", "description": "Implement apply_filters method", "estimated_time": "20m"},
    {"id": "t01-04", "description": "Add input validation", "estimated_time": "10m"}
  ],
  "scope": "src/batch.py only",
  "out_of_scope": "Integration with pipeline, persistence, caching",
  "design_doc_references": [{"section": "3.1", "line_range": "45-78"}]
}
```

**After** — Split into summary (TOON) + per-chunk detail:

**Chunk summary table** (injected to orchestrator — ~350 tokens for all 14):
```
# PLAN_CHUNKS
id|name|purpose|delivers|scope|tasks_count|invariants_count
chunk-01|Batch Processor Foundation|foundational|Core BatchProcessor class|src/batch.py|4|3
chunk-02|Pipeline Integration|integration|Pipeline connects to batch processor|src/pipeline.py|5|3
chunk-03|Filter Chain|feature|Configurable filter system|src/filters.py|4|2
...
```

**Single chunk detail** (injected to chunk-coder — ~400 tokens each):
```
# CHUNK chunk-01: Batch Processor Foundation
PURPOSE: foundational
DELIVERS: Core BatchProcessor class with select_batch() and apply_filters()
SCOPE: src/batch.py only
OUT_OF_SCOPE: Integration with pipeline, persistence, caching

# INVARIANTS
id|description
inv-01-01|BatchProcessor accepts config parameter
inv-01-02|select_batch returns list of Frame objects
inv-01-03|apply_filters respects filter chain order

# TASKS
id|description|time
t01-01|Create BatchProcessor class with __init__|15m
t01-02|Implement select_batch method|30m
t01-03|Implement apply_filters method|20m
t01-04|Add input validation|10m

DESIGN_REFS: 3.1 (lines 45-78)
```

**Savings:**
- Orchestrator: 15,314 -> 350 tokens (**97.7% reduction**)
- Chunk-coder: 15,314 -> 400 tokens per chunk (**97.4% reduction**)
- Combined: **~13,800-14,900 tokens saved per agent spawn**
- **This is the single highest-impact optimization in the entire audit.**

**Implementation:**
- `SubagentStart` hook: detect if spawning coder, extract only relevant chunk
- New helper: `extract_chunk_toon(plan, chunk_id)` in `state_helpers.py`
- Orchestrator: receives chunk summary table instead of full plan

---

## 6. Session Logs (14+ files)

### Source files: `.claude/logs/ptc-chunk-{01-14}-log.json` (~2,500-2,800 chars each, ~650 tokens)
### Injection: At chunk resume (full log), orchestrator status checks (summary only)

#### 6.1 Per-Log Array Breakdown

| Array Field | Avg Items | Per-Item Size | Total Array Tokens | TOON Savings |
|-------------|-----------|---------------|--------------------|--------------|
| state_history | 7 | 40 chars | ~70 | ~40 (43%) |
| tests_written | 25 | 35 chars | ~220 | ~95 (57%) |
| files_modified | 3 | 30 chars | ~23 | ~10 (57%) |
| decisions_made | 2 | 50 chars | ~25 | ~10 (60%) |
| learning_signals | 0-5 | 60 chars | ~75 | ~30 (60%) |
| key_files_read | 7 | 25 chars | ~44 | ~20 (55%) |

**Total array TOON savings per log:** ~205 tokens (~32% of full log)

#### 6.2 Orchestrator Quick-Check (compact summary)

The orchestrator only needs status/phase to route work. Currently loads the full log.

**Before** (full log — ~650 tokens):
```json
{"meta": {"feature": "ptc", "chunk": "chunk-01", ...}, "status": "verified", "phase": "review", "phase_history": [...], "tests_written": [...], ...}
```

**After** (compact summary — ~75 tokens):
```
# CHUNK_STATUS chunk-01
status:verified phase:review tests:25/25 files:3 invariants:3/3 gate:pass resume:Awaiting user approval
```

**Savings:** 650 -> 75 tokens (**88% reduction**) per orchestrator check
**Frequency:** Orchestrator checks after each chunk completion

---

## 7. Message Bus Log (JSONL, unbounded)

### Source: `~/.claude/logs/message-bus.jsonl`
### Injection: SubagentStart reads last 15 entries, injects as context

#### 7.1 Per-Message Structure

Each message is a discriminated union (11 types) with common fields + type-specific payload.

**Before** (single message — ~60 tokens):
```json
{"type": "task_assign", "from_agent": "orchestrator-main-abc-1234", "to_agent": "coder-ptc-chunk01-a1b2", "timestamp": "2026-03-11T10:00:00Z", "payload": {"task_type": "implement", "instructions": "Implement chunk-01"}, "trace_id": "abc123", "span_id": "def456"}
```

**After** (TOON row — ~25 tokens):
```
task_assign|orch-main-abc-1234|coder-ptc-chunk01-a1b2|10:00|implement chunk-01
```

**15-message window savings:** 900 -> 375 tokens (**58% reduction**)
**Frequency:** Every agent spawn (high frequency)
**Implementation:** `to_toon("MESSAGE_BUS", messages, fields)` in `subagent_start.py`

---

## 8. Handoff Payloads

### Source: Written by dying agents, read by replacement agents
### Injection: SubagentStart injects handoff data for replacement spawns

#### 8.1 Handoff Model (root object — ~200 tokens)

**Before:**
```json
{"agent_id": "coder-ptc-chunk01-a1b2", "agent_role": "coder", "agent_model": "opus-4-6", "timestamp": "2026-03-11T11:30:00Z", "reason": "context_limit", "current_state": "IMPLEMENTATION", "resume_instructions": "Continue from test 4/7, batch validation", "work_summary": "Implemented select_batch, apply_filters working on validate_batch", "current_approach": "TDD red-green on each method", "failed_approaches": [], "key_decisions": [...], "files_modified": [...], "key_files_read": [...], "test_results": {...}, "unresolved_questions": [...], "context_usage_pct": 87.3, "input_tokens": 180000, "output_tokens": 24000}
```

**After** (compact keys + `exclude_defaults` — ~100 tokens):
```
# HANDOFF id=agent_id r=role m=model rsn=reason st=state ctx=context_%
id:coder-ptc-chunk01-a1b2 r:coder m:opus-4-6 rsn:context_limit st:IMPLEMENTATION ctx:87

RESUME: Continue from test 4/7, batch validation
SUMMARY: Implemented select_batch, apply_filters working on validate_batch
APPROACH: TDD red-green on each method

# DECISIONS
decision|reason|source|locked
Use list comprehension for filters|Matches codebase pattern|codebase_evidence|no

# FILES_MODIFIED
path|state
src/batch.py|select_batch() done, apply_filters() partial
tests/ptc/test_batch.py|4/7 tests written

# KEY_FILES_READ
path|relevance
src/pipeline.py|Integration point reference
src/config.py|Config schema for BatchProcessor
```

**Savings:** 200 -> 100 tokens (**50% reduction**)
**Frequency:** Every handoff event (medium frequency, ~1-3 per feature)

---

## 9. Research Data

### Source: `.claude/research/_index.json` + `persistent/*.json`
### Injection: Index loaded at researcher spawn; entries on-demand only

#### 9.1 Research Index (1,054 chars, ~264 tokens)

**TOON candidate:** `persistent` array (11 entries)

**Before:**
```json
{"persistent": [{"id": "agentic-coding-worktree-patterns", "path": "persistent/agentic-coding-worktree-patterns.json", "library": "agentic-coding-tools", "type": "best_practices", "topics": ["git-worktrees", "parallel-agents", ...], "confidence": 0.9, "source_count": 24}, ...]}
```

**After:**
```
# RESEARCH_INDEX
id|lib|type|confidence|sources|topics
agentic-coding-worktree-patterns|agentic-coding-tools|best_practices|0.9|24|git-worktrees,parallel-agents,...
blind-testing-ivv-methodology|testing-methodology|best_practices|0.95|30|hypothesis,mutation-testing,...
...
```

**Savings:** ~40% on persistent array (~106 -> ~64 tokens)
**Frequency:** Once per researcher spawn (low frequency)

#### 9.2 Individual Research Entries (5-15KB each)

These are deeply nested documentation objects (endpoints, classes, examples). Not good TOON candidates — structure varies too much between entries. Keep as JSON, load on-demand only.

---

## 10. Workflow State & Preferences

### Source: `~/.claude/state/workflow.json`, `~/.claude/state/preferences.json`
### Injection: SessionStart, PreCompact

#### 10.1 WorkflowState (compact keys — ~48 tokens saved)

**Before** (~120 tokens):
```json
{"workflow_id": "wf-ptc-001", "feature": "ptc", "base_branch": "main", "current_state": "PHASE_IMPLEMENTATION", "started_at": "2026-03-11T09:00:00Z", "last_updated": "2026-03-11T11:00:00Z", "active_agents": ["coder-ptc-chunk01-a1b2"], "phase_history": [...], "remediation_cycle_count": 0}
```

**After** with `exclude_defaults` + compact (~72 tokens):
```
# WORKFLOW wf=id f=feature br=branch st=state agents=active
wf:wf-ptc-001 f:ptc br:main st:PHASE_IMPLEMENTATION agents:coder-ptc-chunk01-a1b2

# PHASE_HISTORY
state|entered|exited
IDLE|09:00|09:01
EXPLORING|09:01|09:30
CONTEXT_READY|09:30|09:31
...
```

**Savings:** ~40% (120 -> 72 tokens)

#### 10.2 Preferences (compact — ~25 tokens saved)

Small object, already sparse. `exclude_defaults=True` removes empty `by_role` entries.

---

## 11. Pre-Compact Snapshots

### Source: `~/.claude/state/pre-compact-snapshots/{agent-id}-latest.json`
### Injection: SessionStart reads for continuation after context compression

These snapshots contain a `continuation_message` (free text) plus agent state summary. The state portion benefits from compact keys (same as AgentState above). The continuation message is free text — no conversion possible.

---

## 12. Observability JSONL Logs

### Source: `~/.claude/logs/workflow-events.jsonl`, `decisions/*.jsonl`, `annotations/*.jsonl`
### Injection: PreCompact reads top-10 decisions and critical annotations

#### 12.1 Decision Log Entries (TOON at injection time)

**Before** (10 entries — ~400 tokens):
```json
[
  {"timestamp": "...", "agent_id": "...", "decision_point": "approach_selection", "thought": "...", "considered": "...", "chosen": "Use itertools for batch processing"},
  ...
]
```

**After** (TOON — ~200 tokens):
```
# RECENT_DECISIONS
time|point|chosen
10:05|approach_selection|Use itertools for batch processing
10:15|error_handling|Raise ValueError for invalid input
...
```

**Savings:** ~50% (400 -> 200 tokens)
**Frequency:** At every PreCompact event (medium frequency)

#### 12.2 Annotation Entries (already small, minimal savings)

Critical annotations are typically 1-3 entries at PreCompact time. Too few for TOON to be worthwhile.

---

## 13. Frequency Analysis (Real-World Savings Multiplier)

| Data Artifact | Injection Event | Frequency per Feature | Tokens Before | Tokens After | Savings per Event | Total Savings per Feature |
|---------------|----------------|----------------------|---------------|-------------|-------------------|--------------------------|
| State machine context | Every PreToolUse | ~500 tool calls/feature | 1,400 | 420 | 980 | **490,000** (amortized via caching) |
| Implementation plan | Every chunk-coder spawn | 14 spawns | 15,314 | 400 | 14,914 | **208,796** |
| Message bus (15 entries) | Every SubagentStart | ~20 spawns | 900 | 375 | 525 | **10,500** |
| Agent state | Every PreToolUse | ~500 tool calls | 65 | 28 | 37 | **18,500** (amortized) |
| Session log (full) | Chunk resume | ~14 resumes | 650 | 445 | 205 | **2,870** |
| Session log (summary) | Orchestrator check | ~30 checks | 650 | 75 | 575 | **17,250** |
| Handoff payload | Handoff event | ~5 handoffs | 200 | 100 | 100 | **500** |
| Codebase context | Session start | ~20 spawns | 1,811 | 1,591 | 220 | **4,400** |
| Feature context | Feature start | ~20 spawns | 2,118 | 1,928 | 190 | **3,800** |
| Workflow state | Orchestrator status | ~30 checks | 120 | 72 | 48 | **1,440** |
| Research index | Researcher spawn | ~3 spawns | 264 | 158 | 106 | **318** |
| Decision log | PreCompact | ~10 compacts | 400 | 200 | 200 | **2,000** |

**Note:** State machine context and agent state are injected at high frequency but are typically cached/amortized by the daemon. The "per-event" column shows the reduction when injection does occur.

---

## 14. Implementation Priority (Savings x Frequency)

### P0 — Critical (implement first, highest ROI)

| # | Artifact | Conversion | Where to Implement | Savings |
|---|----------|------------|-------------------|---------|
| 1 | **Implementation plan chunks** | TOON table + per-chunk extraction | `subagent_start.py` — detect coder spawn, extract chunk | **~14,900 tokens/spawn** |
| 2 | **State machine compact context** | `format_state_context()` | `state_helpers.py` — called by daemon responses | **~980 tokens/injection** |
| 3 | **Message bus TOON** | `to_toon()` on message window | `subagent_start.py` — format last 15 messages | **~525 tokens/spawn** |

### P1 — High (implement second)

| # | Artifact | Conversion | Where to Implement | Savings |
|---|----------|------------|-------------------|---------|
| 4 | **Session log summary** | Compact summary extraction | `state_helpers.py` — new `extract_log_summary()` | **~575 tokens/check** |
| 5 | **AgentState compact keys** | `to_compact()` method on model | `schemas/agent_state.py` + `state_helpers.py` | **~37 tokens/injection** |
| 6 | **Pydantic `exclude_defaults`** | `dump_compact()` helper | `state_helpers.py` — global helper for all models | **15-40% per model** |

### P2 — Medium (implement third)

| # | Artifact | Conversion | Where to Implement | Savings |
|---|----------|------------|-------------------|---------|
| 7 | **SessionLog array TOON** | TOON for tests_written, state_history, files_modified | `state_helpers.py` + resume injection | **~205 tokens/resume** |
| 8 | **Handoff compact** | Compact keys + array TOON | `schemas/handoff.py` + `state_helpers.py` | **~100 tokens/handoff** |
| 9 | **Decision log TOON** | TOON for recent decisions at PreCompact | `pre_compact.py` | **~200 tokens/compact** |
| 10 | **WorkflowState compact** | Compact keys + phase_history TOON | `schemas/system_state.py` | **~48 tokens/check** |

### P3 — Low (implement as needed)

| # | Artifact | Conversion | Where to Implement | Savings |
|---|----------|------------|-------------------|---------|
| 11 | **Context packet arrays** | TOON for name_bank, touchpoints | Context packet injection | **~410 tokens/session** |
| 12 | **Research index TOON** | TOON for persistent array | Research index loading | **~106 tokens/researcher** |
| 13 | **Planning log arrays** | TOON for edit_history, decisions | Planning log injection | **~150 tokens/planning** |

---

## 15. Implementation Helpers Required

All TOON/compact conversions are implemented in `hooks/utils/state_helpers.py` as designed in `00-token-efficiency-standards.md` Section 2:

```python
# Already designed, needs implementation:

def to_toon(header: str, objects: list[dict], fields: list[str]) -> str:
    """Convert array of objects to pipe-delimited table."""
    lines = [f"# {header}", "|".join(fields)]
    for obj in objects:
        lines.append("|".join(str(obj.get(f, "")) for f in fields))
    return "\n".join(lines)

def to_compact(header: str, obj: dict, aliases: dict[str, str]) -> str:
    """Convert single object to compact key representation."""
    legend = " ".join(f"{v}={k}" for k, v in aliases.items())
    values = " ".join(f"{aliases[k]}:{obj[k]}" for k in aliases if k in obj)
    return f"# {header} {legend}\n{values}"

def dump_compact(model: BaseModel) -> dict:
    """Pydantic dump with exclude_defaults and exclude_none."""
    return model.model_dump(exclude_defaults=True, exclude_none=True)

def format_state_context(machine: StateMachineDefinition, current_state: str) -> str:
    """Compact state machine representation for agent context."""
    # ... (see Section 2.5 of standards doc)

def extract_chunk_toon(plan: dict, chunk_id: str) -> str:
    """Extract single chunk from plan in TOON format."""
    # ... (new helper for P0 #1)

def extract_log_summary(log: dict) -> str:
    """Extract lightweight session log summary for orchestrator."""
    # ... (new helper for P1 #4)
```

---

## 16. Summary Table: All Artifacts

| # | Artifact | Type | Size (tokens) | TOON? | Compact? | Savings | Priority | Frequency |
|---|----------|------|---------------|-------|----------|---------|----------|-----------|
| 1 | Plan chunks array | Array[14] | 13,750 | **YES** | — | 97% | P0 | Per coder spawn |
| 2 | State machine context | Object+Array | 1,400 | Array part | Object part | 70% | P0 | Per tool call |
| 3 | Message bus window | Array[15] | 900 | **YES** | — | 58% | P0 | Per spawn |
| 4 | Session log (summary) | Object | 650 | — | **YES** | 88% | P1 | Per orch check |
| 5 | AgentState | Object | 65 | — | **YES** | 57% | P1 | Per tool call |
| 6 | All Pydantic dumps | Various | varies | — | exclude_defaults | 15-40% | P1 | Various |
| 7 | SessionLog arrays | Arrays | 457 | **YES** | — | 45% | P2 | Per resume |
| 8 | Handoff payload | Object+Arrays | 200 | Arrays | Object | 50% | P2 | Per handoff |
| 9 | Decision log entries | Array[10] | 400 | **YES** | — | 50% | P2 | Per compact |
| 10 | WorkflowState | Object+Array | 120 | phase_history | Root | 40% | P2 | Per orch check |
| 11 | Context packet arrays | Arrays | 400 | **YES** | — | 55% | P3 | Per session |
| 12 | Research index | Array[11] | 264 | **YES** | — | 40% | P3 | Per researcher |
| 13 | Planning log arrays | Arrays | 150 | **YES** | — | 40% | P3 | Per planning |
| 14 | SM transitions (full) | Array[11-47] | 1,400-5,000 | **YES** | — | 58% | (disk only) | — |
| 15 | SM states (full) | Array[6-23] | 500-2,000 | **YES** | — | 50% | (disk only) | — |

---

## 17. Verification Checklist

- [ ] `to_toon()` produces valid pipe-delimited output parseable by LLMs
- [ ] `to_compact()` produces valid abbreviated key output with legend
- [ ] `dump_compact()` uses `exclude_defaults=True, exclude_none=True`
- [ ] `format_state_context()` renders all 8 state machines correctly
- [ ] `extract_chunk_toon()` extracts correct chunk from plan
- [ ] `extract_log_summary()` produces valid compact summary
- [ ] Plan TOON includes all invariants and tasks per chunk
- [ ] Message bus TOON preserves message ordering and type discrimination
- [ ] Session log summary includes all fields orchestrator needs for routing
- [ ] Handoff compact preserves all information needed by replacement agent
- [ ] No data loss in any conversion (lossless representations)
- [ ] JSON on disk remains unchanged (conversions happen at injection time only)
