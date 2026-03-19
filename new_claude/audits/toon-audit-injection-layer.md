# TOON Audit: Injection Layer

**Auditor:** injection-layer auditor
**Date:** 2026-03-11
**Scope:** Every place JSON data flows INTO an agent's context window via hooks
**Reference:** `00-token-efficiency-standards.md` Section 2

---

## Executive Summary

The V2 agentic workflow injects data into agent context windows through **4 hook entry points** and **2 secondary channels**. This audit identified **14 distinct injection sites** carrying structured data, of which **8 are strong TOON/compact-key conversion candidates**.

### Total Token Savings Potential

| Category | Current Tokens | After TOON/Compact | Savings | % Reduction |
|----------|---------------|-------------------|---------|-------------|
| SubagentStart (per spawn) | ~1,270 | ~820 | ~450 | 35% |
| PostToolUse (per tool call, peak) | ~600 | ~410 | ~190 | 32% |
| PreCompact continuation (per compaction) | ~625 | ~430 | ~195 | 31% |
| SessionStart (post-compact resume) | ~1,000 | ~720 | ~280 | 28% |
| **Cumulative per agent lifecycle** | **~3,495** | **~2,380** | **~1,115** | **32%** |

**Bottom line:** TOON and compact-key conversions save ~1,100 tokens per agent lifecycle. For a workflow with 5 agents each compacting once, that's ~5,500 tokens saved across the session.

---

## Hook Entry Points Overview

```
CONTEXT INJECTION FLOW
======================

                  +-----------------+
                  |  Claude Code    |
                  |  Agent Context  |
                  +--------+--------+
                           ^
                           | additionalContext / feedback
                           |
           +---------------+---------------+
           |               |               |
    SubagentStart     PostToolUse     SessionStart
    (at spawn)        (per tool)      (at start/resume)
           |               |               |
           v               v               v
    6 sections        3 channels      4 sections
    ~1,270 tok        ~100-600 tok    ~375-1000 tok
                           |
                      PreCompact ---------> snapshot file
                      (pre-compress)        (read by SessionStart)
                      ~1,600-2,400 tok      on next resume
```

---

## Site 1: SubagentStart Hook

**File:** `hooks/subagent_start.py`
**Trigger:** Once per agent spawn
**Hard cap:** `MAX_CONTEXT_CHARS = 3000`
**Total cost:** ~1,270 tokens (typical)

### 1.1 Identity Section (`_build_identity_section`)

**Current format (~81 tokens, 324 chars):**
```
=== WORKFLOW CONTEXT ===
Spawned by: chunk-coder-p1 (role: chunk-coder, state: IMPLEMENTATION)
Phase: test_writing
Task: chunk-01
Feature: ptc | Workflow: PLANNING
```

**Proposed compact-key format (~50 tokens, 200 chars):**
```
# SPAWN parent=agent_id r=role s=state ph=phase t=task feat=feature wf=wf_state
parent:chunk-coder-p1 r:chunk-coder s:IMPLEMENTATION ph:test_writing t:chunk-01 feat:ptc wf:PLANNING
```

**Savings:** ~31 tokens (38%)
**Wiring:** Replace `_build_identity_section()` return with `to_compact()` call using alias map.

---

### 1.2 Communication Section (`_build_communication_section`)

**Current format (~730 tokens for 10 entries, 2920 chars):**
```
=== WHY YOU WERE SPAWNED ===
[1] orchestrator -> coder-p1 (task_assignment, 2min ago):
    Implement chunk-01: IPC Protocol Messages and Serialization
[2] coder-p1 -> orchestrator (status_update, 90s ago):
    Tests written, 3/5 passing, need implementation for remaining 2
[3] orchestrator -> coder-p1 (context_request, 60s ago):
    Please check batch_processor.py for async patterns
...
```

**Proposed TOON format (~420 tokens for 10 entries, 1680 chars):**
```
# MSG_LOG from|to|type|age|summary
orchestrator|coder-p1|task_assign|2m|Implement chunk-01: IPC Protocol Messages
coder-p1|orchestrator|status_update|90s|Tests 3/5 passing, need impl for 2
orchestrator|coder-p1|ctx_request|60s|Check batch_processor.py for async patterns
...
```

**Savings:** ~310 tokens (42%)
**Wiring:** In `_build_communication_section()`, after sorting/filtering messages, pass array to `to_toon("MSG_LOG", messages, ["from", "to", "type", "age", "summary"])`. Truncate summary to 60 chars.

**This is the single highest-value conversion** in the entire injection layer (57% of SubagentStart tokens).

---

### 1.3 Task Section (`_build_task_section`)

**Current format (~119 tokens, 476 chars):**
```
=== CURRENT TASK ===
chunk-01: IPC Protocol Messages and Serialization
Description: Define 8 message dataclasses with validation, serialization helpers,
  and type discriminator for message routing...
Files: src/messages.py, src/serializer.py, tests/test_messages.py, ...
Dependencies: chunk-00
Exploration: Query 1, Query 2, Query 3
```

**Proposed compact-key format (~90 tokens, 360 chars):**
```
# TASK id=chunk t=title dep=dependencies
id:chunk-01 t:IPC Protocol Messages and Serialization
desc: Define 8 message dataclasses with validation, serialization helpers...
files: src/messages.py|src/serializer.py|tests/test_messages.py|+5
dep: chunk-00
queries: Query 1|Query 2|Query 3
```

**Savings:** ~29 tokens (24%)
**Wiring:** Replace text formatting in `_build_task_section()` with compact key layout. Use `|` separator for file lists.

---

### 1.4 Key Files Section (`_build_key_files_section`)

**Current format (~232 tokens for 8 entries, 928 chars):**
```
=== KEY FILES PARENT HAS READ ===
  - src/batch_processor.py — Contains BatchProcessor base class being extended
  - src/messages.py — Message dataclass definitions
  - tests/test_messages.py — Existing test patterns
  - src/serializer.py — Current serialization approach
  ...
```

**Proposed TOON format (~145 tokens for 8 entries, 580 chars):**
```
# KEY_FILES path|relevance
src/batch_processor.py|BatchProcessor base class being extended
src/messages.py|Message dataclass definitions
tests/test_messages.py|Existing test patterns
src/serializer.py|Current serialization approach
...
```

**Savings:** ~87 tokens (38%)
**Wiring:** In `_build_key_files_section()`, pass `key_files_read` array to `to_toon("KEY_FILES", files, ["path", "relevance"])`.

---

### 1.5 Preferences Section (`_build_preferences_section`)

**Current format (~67 tokens, 268 chars):**
```
=== ACTIVE PREFERENCES ===
  Global:
    - writing_style: technical
    - test_coverage_threshold: 85
  Role (coder):
    - prefer_async: True
    - prefer_early_returns: True
```

**Proposed compact-key format (~45 tokens, 180 chars):**
```
# PREFS g=global r=role_specific
g:writing_style=technical g:test_coverage=85
r:prefer_async=true r:prefer_early_returns=true
```

**Savings:** ~22 tokens (33%)
**Wiring:** Replace text formatting in `_build_preferences_section()` with prefix-based compact keys (`g:` for global, `r:` for role-specific).

---

### 1.6 PTC Sandbox Section (`_build_ptc_section`)

**Current format (~41 tokens, 164 chars):**
```
=== PTC SANDBOX ===
Kernel: ptc-kernel-3a7f2b1d
Image: ptc-coder:latest
Network: bridge
```

**Proposed compact-key format (~30 tokens, 120 chars):**
```
# PTC k=kernel img=image net=network
k:ptc-kernel-3a7f2b1d img:ptc-coder:latest net:bridge
```

**Savings:** ~11 tokens (27%)
**Wiring:** Replace `_build_ptc_section()` with `to_compact()` call.

---

### SubagentStart Summary

| Section | Current | Proposed | Savings | % |
|---------|---------|----------|---------|---|
| Identity | 81 tok | 50 tok | 31 | 38% |
| Communication | 730 tok | 420 tok | 310 | **42%** |
| Task | 119 tok | 90 tok | 29 | 24% |
| Key Files | 232 tok | 145 tok | 87 | 38% |
| Preferences | 67 tok | 45 tok | 22 | 33% |
| PTC | 41 tok | 30 tok | 11 | 27% |
| **Total** | **1,270** | **780** | **490** | **39%** |

---

## Site 2: PostToolUse Hook

**File:** `hooks/post_tool_use.py` (thin proxy to daemon)
**Daemon:** `scripts/daemon/transitions.py`
**Trigger:** Every tool call
**Typical cost:** ~100-200 tokens; peak ~600 tokens

### 2.1 Annotations (`_ack_annotations` in daemon)

**Current format (~80-150 tokens per annotation):**
```
[ANNOTATION CRITICAL] [ann-abc12345] (state_machine) Design review: (1) modular? (2) complete?
[ANNOTATION NORMAL] [ann-def67890] (coder-agent) File written: src/processor.py updated
```

**Proposed compact format (~55-100 tokens per annotation):**
```
# ANN pri=priority id=ann_id src=source
[C] ann-abc12345 state_machine: Design review: (1) modular? (2) complete?
[N] ann-def67890 coder-agent: File written: src/processor.py updated
```

When 3+ annotations are pending, use TOON:
```
# ANNOTATIONS pri|id|src|message
C|ann-abc12345|state_machine|Design review: (1) modular? (2) complete?
N|ann-def67890|coder-agent|File written: src/processor.py updated
N|ann-ghi11111|orchestrator|New task assigned: chunk-02
```

**Savings:** ~30-40% (30-60 tokens per annotation batch)
**Wiring:** In `_ack_annotations()` (transitions.py:509-545), format as TOON when `len(annotations) >= 3`, else use single-line compact. Inject via `result["hookSpecificOutput"]["additionalContext"]`.

---

### 2.2 Validator Errors (daemon `validators.py`)

**Current format (~50-250 tokens):**
```
F821: undefined name 'BatchProcessor' (line 42)
F811: redefined 'process' from line 12 (line 55)
F401: 'os' imported but unused (line 3)
Schema validation: field 'agent_id' required (session-log.json)
```

**Proposed TOON format when 2+ errors (~35-175 tokens):**
```
# LINT_ERRORS code|msg|line
F821|undefined name 'BatchProcessor'|42
F811|redefined 'process' from line 12|55
F401|'os' imported but unused|3
```

**Savings:** ~25-35% (15-75 tokens)
**Wiring:** In `VALIDATOR_REGISTRY` handlers (validators.py), collect errors as dicts `{code, msg, line}`, then `to_toon("LINT_ERRORS", errors, ["code", "msg", "line"])` when `len(errors) >= 2`.

---

### 2.3 Context Pressure Warnings (`format_context_warning`)

**Current format (~80-100 tokens):**
```
[! CONTEXT WARNING] Agent: coder | Usage: 75% | Wrap up current work and plan for handoff.
```

**Proposed compact format (~50 tokens):**
```
# CTX_WARN agent=role usage=pct action=next_step
CTX[warn] coder 75% action:wrap-up-and-plan-handoff
```

Critical level:
```
CTX[CRIT] explorer 92% action:stop-write-handoff-terminate
```

**Savings:** ~40% (30-40 tokens)
**Wiring:** Replace `format_context_warning()` in `hooks/utils/context_monitor.py:120-138` with compact format builder.

---

### PostToolUse Summary

| Component | Current (peak) | Proposed (peak) | Savings | % |
|-----------|---------------|-----------------|---------|---|
| Annotations (3x) | 300 tok | 195 tok | 105 | 35% |
| Validator errors (5x) | 250 tok | 175 tok | 75 | 30% |
| Context warning | 100 tok | 60 tok | 40 | 40% |
| **Peak total** | **600** | **410** | **190** | **32%** |

**Note:** PostToolUse fires on every tool call. Even small per-call savings compound significantly. A coder making 50 tool calls saves ~1,000 tokens in annotation overhead alone over a session.

---

## Site 3: PreCompact Hook

**File:** `hooks/pre_compact.py`
**Trigger:** Before context compression
**Output:** Snapshot file at `~/.claude/state/pre-compact-snapshots/{agent_id}-latest.json`
**Total snapshot cost:** ~1,600-2,400 tokens (but only `continuation_message` is injected later)

The continuation message (~375-625 tokens) is the part that enters agent context via SessionStart after compaction. The snapshot's internal arrays are TOON candidates because they compose this message.

### 3.1 Active Agents Array (`_read_active_agents`)

**Current JSON format (~200 tokens for 3 agents):**
```json
[
  {"id": "explorer-p1-init-c4d9", "role": "explorer", "state": "CONTEXT_GENERATION", "task": "init", "context_pct": 42.5},
  {"id": "plan-arch-p1-plan-d2e1", "role": "plan-architect", "state": "PLANNING", "task": "plan", "context_pct": 38.3},
  {"id": "coder-p1-t3-a7f2", "role": "coder", "state": "TDD_GREEN", "task": "t3", "context_pct": 67.0}
]
```

**Proposed TOON format (~110 tokens):**
```
# AGENTS id|role|state|task|ctx%
explorer-p1-init-c4d9|explorer|CTX_GEN|init|42
plan-arch-p1-plan-d2e1|plan-arch|PLANNING|plan|38
coder-p1-t3-a7f2|coder|TDD_GREEN|t3|67
```

**Savings:** ~90 tokens (45%)
**Wiring:** In `_build_continuation_message()` (pre_compact.py:391-557), replace the ACTIVE_AGENTS section with `to_toon("AGENTS", agents, ["id", "role", "state", "task", "ctx%"])`. Abbreviate state names (CONTEXT_GENERATION -> CTX_GEN).

---

### 3.2 Recent Messages Array (`_read_recent_messages`)

**Current JSON format (~250 tokens for 5 messages):**
```json
[
  {"from": "explorer-p1-init-c4d9", "to": "orchestrator", "type": "context_ready", "summary": "Codebase exploration complete, context packet written"},
  {"from": "plan-arch-p1-plan-d2e1", "to": "orchestrator", "type": "plan_ready", "summary": "Feature plan generated, awaiting user review"},
  ...
]
```

**Proposed TOON format (~140 tokens):**
```
# MESSAGES from|to|type|summary
explorer-p1-init-c4d9|orchestrator|ctx_ready|Codebase exploration done, packet written
plan-arch-p1-plan-d2e1|orchestrator|plan_ready|Feature plan generated, awaiting review
...
```

**Savings:** ~110 tokens (44%)
**Wiring:** In `_build_continuation_message()`, replace RECENT_MESSAGES section with `to_toon("MESSAGES", messages, ["from", "to", "type", "summary"])`. Truncate summary to 60 chars.

---

### 3.3 Recent Decisions Array (`_read_recent_decisions`)

**Current JSON format (~250 tokens for 3 decisions):**
```json
[
  {"timestamp": "2026-03-11T13:50:00+00:00", "decision_point": "Implementation approach for batch processing", "options": ["async generator", "thread pool", "process pool"], "chosen": "async generator", "reason": "Better integration with existing async codebase"},
  ...
]
```

**Proposed TOON format (~155 tokens for 3 decisions):**
```
# DECISIONS time|decision|chosen|reason
13:50|Batch processing approach|async generator|Better async integration
14:05|Error handling strategy|collect-and-report|User prefers partial-failure tolerance
14:20|Test isolation|pytest fixtures|Consistent with existing patterns
```

**Savings:** ~95 tokens (38%)
**Wiring:** In `_read_recent_decisions()`, drop `options[]` field (redundant with `chosen`), abbreviate timestamps to HH:MM. Pass to `to_toon()`.

---

### 3.4 Pending Critical Annotations (`_read_pending_critical_annotations`)

**Current format (~150 tokens for 2 annotations):**
```json
[
  {"annotation_id": "ann-001", "message": "This test is flaky (75% pass rate). Recommend refactoring..."},
  {"annotation_id": "ann-002", "message": "Design review gap: error recovery not addressed"}
]
```

**Proposed TOON format (~100 tokens):**
```
# PENDING_ANN id|message
ann-001|This test is flaky (75% pass rate). Recommend refactoring
ann-002|Design review gap: error recovery not addressed
```

**Savings:** ~50 tokens (33%)
**Wiring:** In `_read_pending_critical_annotations()`, pass to `to_toon("PENDING_ANN", annotations, ["id", "message"])`.

---

### PreCompact Continuation Message Summary

| Component | Current | Proposed | Savings | % |
|-----------|---------|----------|---------|---|
| Active agents (3) | 200 tok | 110 tok | 90 | 45% |
| Recent messages (5) | 250 tok | 140 tok | 110 | 44% |
| Recent decisions (3) | 250 tok | 155 tok | 95 | 38% |
| Pending annotations (2) | 150 tok | 100 tok | 50 | 33% |
| Identity/status (text) | 150 tok | 150 tok | 0 | 0% |
| Plan summary (text) | 100 tok | 100 tok | 0 | 0% |
| **Continuation total** | **~625** | **~430** | **~195** | **31%** |

---

## Site 4: SessionStart Hook

**File:** `hooks/session_start.py`
**Trigger:** Once at agent startup (and after compaction resume)

### 4.1 Normal Startup (~375 tokens)

Injects:
- Agent identity (session ID, role) — ~30 tokens, already compact
- Preferences (formatted text) — ~100 tokens
- Hook output wrapper — ~47 tokens

**Compression opportunity:** Preferences section (see SubagentStart 1.5 — same format).

**Savings:** ~25 tokens (preferences compaction only).

### 4.2 Post-Compaction Resume (~750-1000 tokens)

Injects everything from normal startup PLUS:
- Pre-compact continuation message (~375-625 tokens, written by PreCompact)
- Pending critical annotation warnings (~50-100 tokens)

The continuation message inherits TOON savings from PreCompact (Site 3):

| Component | Current | Proposed | Savings | % |
|-----------|---------|----------|---------|---|
| Normal startup | 375 tok | 350 tok | 25 | 7% |
| Continuation message | 625 tok | 430 tok | 195 | 31% |
| Annotation warnings | 50 tok | 35 tok | 15 | 30% |
| **Resume total** | **~1,000** | **~720** | **~280** | **28%** |

---

## Secondary Channels

### 5. Annotation Channel (JSONL -> PostToolUse)

**File:** `schemas/annotation.py`, read by daemon `transitions.py`
**Flow:** `~/.claude/annotations/{agent}.jsonl` -> daemon `_ack_annotations()` -> `additionalContext`

Already covered in Site 2.1. The JSONL on disk stays JSON (source of truth). Conversion to TOON happens at injection time in the daemon.

### 6. Peer Notify (Agent-to-Agent via Annotations)

**File:** `schemas/message_protocol.py` (PeerNotifyPayload)
**Flow:** Agent sends PeerNotify -> logged to annotations JSONL -> PostToolUse injects on next tool call

Same injection path as annotations. No separate conversion needed.

---

## Implementation Plan

### Helper Functions Needed in `hooks/utils/state_helpers.py`

```python
def to_toon(header: str, objects: list[dict], fields: list[str]) -> str:
    """Convert array of dicts to pipe-delimited TOON table."""
    lines = [f"# {header}", "|".join(fields)]
    for obj in objects:
        lines.append("|".join(str(obj.get(f, "")) for f in fields))
    return "\n".join(lines)

def to_compact(header: str, obj: dict, aliases: dict[str, str]) -> str:
    """Convert single dict to compact key format with legend."""
    legend = " ".join(f"{v}={k}" for k, v in aliases.items())
    values = " ".join(f"{aliases[k]}:{obj[k]}" for k in aliases if k in obj)
    return f"# {header} {legend}\n{values}"

def dump_compact(model: BaseModel) -> dict:
    """Pydantic model dump with defaults/None excluded."""
    return model.model_dump(exclude_defaults=True, exclude_none=True)
```

### Priority Order (Highest Savings First)

| # | Site | Component | Tokens Saved | Frequency | Cumulative Impact | Difficulty |
|---|------|-----------|-------------|-----------|-------------------|------------|
| 1 | SubagentStart | Communication (MSG_LOG) | 310/spawn | per spawn | **HIGH** | Medium |
| 2 | PreCompact | Recent messages | 110/compact | per compaction | HIGH | Medium |
| 3 | PreCompact | Recent decisions | 95/compact | per compaction | HIGH | Medium |
| 4 | PreCompact | Active agents | 90/compact | per compaction | Medium | Low |
| 5 | SubagentStart | Key files | 87/spawn | per spawn | Medium | Low |
| 6 | PostToolUse | Annotations (3+) | 105/batch | per tool call | Medium | Medium |
| 7 | PostToolUse | Validator errors | 75/batch | per Write | Low-Medium | Medium |
| 8 | PreCompact | Pending annotations | 50/compact | per compaction | Low | Low |
| 9 | PostToolUse | Context warnings | 40/warning | every 10 calls | Low | Low |
| 10 | SubagentStart | Identity | 31/spawn | per spawn | Low | Low |
| 11 | SubagentStart | Task | 29/spawn | per spawn | Low | Low |
| 12 | SubagentStart | Preferences | 22/spawn | per spawn | Low | Low |
| 13 | SubagentStart | PTC | 11/spawn | per spawn | Minimal | Low |
| 14 | SessionStart | Preferences | 25/start | per start | Minimal | Low |

### Implementation Phases

**Phase 1 (Highest ROI):** Items 1-4 — SubagentStart communication + PreCompact arrays
- Add `to_toon()` and `to_compact()` to `state_helpers.py`
- Modify `_build_communication_section()` in `subagent_start.py`
- Modify `_build_continuation_message()` in `pre_compact.py`
- **Total savings: ~605 tokens per lifecycle**

**Phase 2 (Medium ROI):** Items 5-9 — Key files, annotations, validators, context warnings
- Modify `_build_key_files_section()` in `subagent_start.py`
- Modify `_ack_annotations()` in `scripts/daemon/transitions.py`
- Modify validator output formatting in `scripts/daemon/validators.py`
- Modify `format_context_warning()` in `hooks/utils/context_monitor.py`
- **Total savings: ~357 tokens per lifecycle**

**Phase 3 (Polish):** Items 10-14 — Identity, task, preferences, PTC
- Compact-key format for remaining SubagentStart sections
- **Total savings: ~118 tokens per lifecycle**

---

## Files Changed Per Phase

| Phase | File | Change |
|-------|------|--------|
| 1 | `hooks/utils/state_helpers.py` | Add `to_toon()`, `to_compact()`, `dump_compact()` |
| 1 | `hooks/subagent_start.py` | `_build_communication_section()` uses `to_toon()` |
| 1 | `hooks/pre_compact.py` | `_build_continuation_message()` uses `to_toon()` for agents, messages, decisions |
| 2 | `hooks/subagent_start.py` | `_build_key_files_section()` uses `to_toon()` |
| 2 | `scripts/daemon/transitions.py` | `_ack_annotations()` uses `to_toon()` for 3+ annotations |
| 2 | `scripts/daemon/validators.py` | Validator error output uses `to_toon()` for 2+ errors |
| 2 | `hooks/utils/context_monitor.py` | `format_context_warning()` uses compact format |
| 3 | `hooks/subagent_start.py` | Identity, task, prefs, PTC sections use `to_compact()` |
| 3 | `hooks/session_start.py` | Preferences section uses compact format |

---

## Appendix A: Data Flow Map

```
SOURCE (JSON on disk)              HOOK                    INJECTION POINT
=========================          =============           =================
~/.claude/state/agents/*.json  --> subagent_start.py   --> additionalContext (identity)
~/.claude/state/workflow.json  --> subagent_start.py   --> additionalContext (identity, task)
~/.claude/logs/message-bus.jsonl -> subagent_start.py  --> additionalContext (communication) *TOON*
.claude/plans/{f}-plan.json    --> subagent_start.py   --> additionalContext (task)
.claude/logs/{f}-{t}-log.json  --> subagent_start.py   --> additionalContext (key files) *TOON*
~/.claude/state/preferences.json -> subagent_start.py  --> additionalContext (preferences)
~/.claude/mcp/ptc-server/cfg.json -> subagent_start.py --> additionalContext (PTC)
~/.claude/state/ptc-kernels/*.json -> subagent_start.py -> additionalContext (PTC)

~/.claude/annotations/*.jsonl  --> daemon/transitions  --> additionalContext (annotations) *TOON*
validators (ruff, schema)      --> daemon/validators   --> feedback[] (errors) *TOON*
~/.claude/state/agents/*.json  --> daemon/transitions  --> feedback[] (context warning)

~/.claude/state/agents/*.json  --> pre_compact.py      --> snapshot.active_agents *TOON*
~/.claude/logs/message-bus.jsonl -> pre_compact.py     --> snapshot.recent_messages *TOON*
~/.claude/logs/decisions/*.jsonl -> pre_compact.py     --> snapshot.recent_decisions *TOON*
~/.claude/annotations/*.jsonl  --> pre_compact.py      --> snapshot.pending_annotations *TOON*
.claude/plans/{f}-plan.json    --> pre_compact.py      --> snapshot.plan_summary
.claude/logs/{f}-{t}-log.json  --> pre_compact.py      --> snapshot.session_log_summary

snapshot file                  --> session_start.py    --> additionalContext (continuation)
~/.claude/state/preferences.json -> session_start.py   --> additionalContext (preferences)
```

Items marked **\*TOON\*** are conversion candidates.

---

## Appendix B: What NOT to Convert

| Data | Why Not |
|------|---------|
| Agent state file on disk | Source of truth, must stay JSON for Python tools |
| Workflow state on disk | Same — JSON for validation |
| Plan file on disk | Complex nested structure, agents write it |
| Session log on disk | Complex nested, agents write it |
| Event logger JSONL | Never injected into context |
| Error logger JSONL | Never injected into context |
| State transition JSONL | Never injected into context |
| Decision log JSONL | Never injected into context (only read by PreCompact for snapshots) |
| Message bus JSONL on disk | Source of truth; only convert when reading for injection |
| Trace context | 3 fields only, too small for TOON overhead |
| Hook output wrapper | Required JSON structure for Claude Code API |
| Think validation messages | Unique format per case, not tabular |
| Validation clear message | Single 15-token string |

---

## Appendix C: Pydantic `model_dump` Audit

The following Pydantic models are serialized in hook code. All should use `exclude_defaults=True, exclude_none=True` when the output enters agent context:

| Model | Current `.model_dump()` | Needs `exclude_defaults`? | Where |
|-------|------------------------|--------------------------|-------|
| AgentState | `model.model_dump(mode="json")` | YES | session_start.py:187 |
| Preferences | `.model_dump(mode="json")` | YES | session_start.py:355 |
| HookOutput | `json.dumps(dict)` | N/A (manual dict) | All hooks |
| AnnotationEntry | `.model_dump()` | YES | transitions.py:540 |
| SessionLog | `.model_dump()` | YES (at resumption) | pre_compact.py:210 |

**Action:** Add `dump_compact()` helper to `state_helpers.py` and standardize all LLM-bound serialization.
