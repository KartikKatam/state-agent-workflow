# TOON Conversion Audit: Complete JSON Injection Point Inventory

## Executive Summary

This audit identifies **every place** where JSON data gets injected into an agent's context window in the V2 agentic workflow system. Each injection point is assessed for TOON (Tabular Object-Oriented Notation) or compact key conversion potential.

**Total injection points found: 32**
**Total estimated baseline token cost per workflow session: ~12,500-25,000 tokens**
**Total estimated savings with TOON/compact: ~4,500-10,000 tokens (35-40%)**

## Summary Table

| # | Injection Point | Location | Recipient | Frequency | Baseline Tokens | TOON/Compact Tokens | Savings | Type |
|---|---|---|---|---|---|---|---|---|
| 1 | SubagentStart: Message Bus Window | `hooks/subagent_start.py` | All sub-agents | Per spawn | 200 | 80 | 60% | TOON |
| 2 | SubagentStart: Key Files Read | `hooks/subagent_start.py` | All sub-agents | Per spawn | 75 | 35 | 53% | TOON |
| 3 | SubagentStart: Parent Task (plan chunk) | `hooks/subagent_start.py` | All sub-agents | Per spawn | 150 | 100 | 33% | compact |
| 4 | SubagentStart: Preferences | `hooks/subagent_start.py` | All sub-agents | Per spawn | 100 | 60 | 40% | compact |
| 5 | SubagentStart: PTC Context | `hooks/subagent_start.py` | All sub-agents | Per spawn | 50 | 30 | 40% | compact |
| 6 | SubagentStart: Workflow Context | `hooks/subagent_start.py` | All sub-agents | Per spawn | 75 | 50 | 33% | compact |
| 7 | SessionStart: Agent Registration | `hooks/session_start.py` | All agents | Per spawn | 150 | 100 | 33% | compact |
| 8 | SessionStart: Continuation (post-compact) | `hooks/session_start.py` | Resuming agent | Per compact | 375 | 250 | 33% | compact |
| 9 | SessionStart: Workflow Resumption | `hooks/session_start.py` | Orchestrator | Per resume | 200 | 120 | 40% | TOON+compact |
| 10 | SessionStart: Dead Agents Roster | `hooks/session_start.py` | Orchestrator | Per resume | 150 | 60 | 60% | TOON |
| 11 | PostToolUse: Annotations | `hooks/post_tool_use.py` | All agents | Per tool call | 50-500 | 30-350 | 30% | compact |
| 12 | PostToolUse: Daemon Context | `hooks/post_tool_use.py` | All agents | Conditional | 125 | 90 | 28% | compact |
| 13 | PreToolUse: Daemon Context | `hooks/pre_tool_use.py` | All agents | Conditional | 125 | 90 | 28% | compact |
| 14 | Codebase Context: structure.blocks | `.claude/context/_codebase.json` | Planner, Coder | Per feature | 800 | 400 | 50% | TOON |
| 15 | Codebase Context: types arrays | `.claude/context/_codebase.json` | Planner, Coder | Per feature | 500 | 200 | 60% | TOON |
| 16 | Codebase Context: configs | `.claude/context/_codebase.json` | Planner, Coder | Per feature | 300 | 150 | 50% | TOON |
| 17 | Codebase Context: patterns | `.claude/context/_codebase.json` | Planner, Coder | Per feature | 200 | 130 | 35% | compact |
| 18 | Feature Context: touchpoints | `.claude/context/{feature}-context.json` | Planner, Coder | Per feature | 400 | 160 | 60% | TOON |
| 19 | Feature Context: dependencies | `.claude/context/{feature}-context.json` | Planner, Coder | Per feature | 300 | 150 | 50% | TOON |
| 20 | Feature Context: new_items | `.claude/context/{feature}-context.json` | Planner, Coder | Per feature | 250 | 125 | 50% | TOON |
| 21 | Query Result: details array | `.claude/context/queries/{topic}.json` | Any agent | On demand | 200 | 80 | 60% | TOON |
| 22 | Query Result: sources array | `.claude/context/queries/{topic}.json` | Any agent | On demand | 150 | 60 | 60% | TOON |
| 23 | Plan: chunks array | `.claude/plans/{feature}-plan.json` | Coder | Per chunk | 600 | 350 | 42% | TOON+compact |
| 24 | Plan: invariants array | `.claude/plans/{feature}-plan.json` | Coder | Per chunk | 300 | 120 | 60% | TOON |
| 25 | Plan: test_spec.cases | `.claude/plans/{feature}-plan.json` | Coder | Per chunk | 250 | 100 | 60% | TOON |
| 26 | Session Log: tests_written | `.claude/logs/{feature}-{chunk}-log.json` | Replacement agent | Per handoff | 400 | 160 | 60% | TOON |
| 27 | Session Log: decisions_made | `.claude/logs/{feature}-{chunk}-log.json` | Replacement/next chunk | Per handoff | 200 | 130 | 35% | compact |
| 28 | Session Log: phase_history | `.claude/logs/{feature}-{chunk}-log.json` | Replacement agent | Per handoff | 150 | 60 | 60% | TOON |
| 29 | Session Log: files_modified | `.claude/logs/{feature}-{chunk}-log.json` | Replacement agent | Per handoff | 100 | 40 | 60% | TOON |
| 30 | Session Log: invariants_status | `.claude/logs/{feature}-{chunk}-log.json` | Replacement agent | Per handoff | 100 | 40 | 60% | TOON |
| 31 | Research: persistent endpoints | `.claude/research/persistent/{id}.json` | Coder, Planner | On demand | 500 | 200 | 60% | TOON |
| 32 | Research: persistent examples | `.claude/research/persistent/{id}.json` | Coder, Planner | On demand | 300 | 150 | 50% | TOON |

---

## Detailed Injection Point Analysis

---

### CATEGORY A: Hook-Based Injections (hooks/)

These are the most critical because they fire automatically via `additionalContext` / `hookSpecificOutput`.

---

#### IP-01: SubagentStart — Message Bus Window

**File:** `hooks/subagent_start.py` (lines 88-91, 164-241)
**Recipient:** All sub-agents (coder, explorer, researcher, strategist, tester, auditor)
**Frequency:** Once per sub-agent spawn
**Source data:** `~/.claude/logs/message-bus.jsonl` (last 15 entries, sorted by priority, top 10 shown)

**Current JSON (injected as text):**
```
=== WHY YOU WERE SPAWNED ===
[1] orchestrator-abc-1234 -> coder-xyz-5678 (task_assignment, 5min ago):
    Implement chunk-02: Core streaming handler
[2] coder-xyz-5678 -> researcher-abc-9012 (context_request, 3min ago):
    Need docs on FastAPI streaming patterns
[3] researcher-abc-9012 -> coder-xyz-5678 (context_response, 2min ago):
    Found 3 related patterns in state transitions
```

**TOON format:**
```
# SPAWN_CONTEXT
from|to|type|ago|summary
orchestrator-abc-1234|coder-xyz-5678|task_assign|5min|Implement chunk-02: Core streaming handler
coder-xyz-5678|researcher-abc-9012|context_request|3min|Need docs on FastAPI streaming patterns
researcher-abc-9012|coder-xyz-5678|context_response|2min|Found 3 related patterns in state transitions
```

**Baseline:** ~800 chars = **200 tokens**
**TOON:** ~320 chars = **80 tokens**
**Savings: 120 tokens (60%)**

---

#### IP-02: SubagentStart — Key Files Read

**File:** `hooks/subagent_start.py` (lines 99-101, 316-353)
**Recipient:** All sub-agents
**Frequency:** Per spawn (if parent has session log)
**Source:** `.claude/logs/{feature}-{chunk}-log.json` → `key_files_read[]`

**Current JSON:**
```
=== KEY FILES (parent has read) ===
  - src/streaming.py
    (core streaming implementation)
  - docs/streaming-guide.md
    (design reference)
  - src/handlers.py
    (request handler patterns)
```

**TOON format:**
```
# KEY_FILES
path|relevance
src/streaming.py|core streaming implementation
docs/streaming-guide.md|design reference
src/handlers.py|request handler patterns
```

**Baseline:** ~300 chars = **75 tokens**
**TOON:** ~140 chars = **35 tokens**
**Savings: 40 tokens (53%)**

---

#### IP-03: SubagentStart — Parent's Task Details

**File:** `hooks/subagent_start.py` (lines 94-96, 244-313)
**Recipient:** All sub-agents
**Frequency:** Per spawn (if plan exists)
**Source:** `.claude/plans/{feature}-plan.json` → current chunk

**Current format:**
```
=== YOUR PARENT'S TASK ===
chunk-02: Core streaming handler
  Implement request streaming with proper cleanup and error handling
  Files to modify: src/streaming.py, src/handlers.py, tests/test_streaming.py
  Depends on: chunk-01
  Exploration queries:
    - FastAPI streaming patterns
    - Memory management in async streams
  Research queries:
    - WebSocket vs SSE comparison
```

**Compact format:**
```
# PARENT_TASK id=chunk_id n=name d=description f=files dep=depends_on
id:chunk-02 n:Core streaming handler dep:chunk-01
d:Implement request streaming with proper cleanup and error handling
f:src/streaming.py,src/handlers.py,tests/test_streaming.py
eq:FastAPI streaming patterns|Memory management in async streams
rq:WebSocket vs SSE comparison
```

**Baseline:** ~600 chars = **150 tokens**
**Compact:** ~400 chars = **100 tokens**
**Savings: 50 tokens (33%)**

---

#### IP-04: SubagentStart — Active Preferences

**File:** `hooks/subagent_start.py` (lines 104-106, 356-380)
**Recipient:** All sub-agents
**Frequency:** Per spawn (if preferences exist)
**Source:** `~/.claude/state/preferences.json`

**Current format:**
```
=== ACTIVE PREFERENCES ===
  - test_framework: pytest
  - max_context_pct: 85
  - [chunk-coder] impl_style: tdd
  - [chunk-coder] commit_frequency: per_chunk
```

**Compact format:**
```
# PREFS k=key v=value r=role
k:test_framework v:pytest
k:max_context_pct v:85
k:impl_style v:tdd r:chunk-coder
k:commit_frequency v:per_chunk r:chunk-coder
```

**Baseline:** ~400 chars = **100 tokens**
**Compact:** ~240 chars = **60 tokens**
**Savings: 40 tokens (40%)**

---

#### IP-05: SubagentStart — PTC Sandbox Context

**File:** `hooks/subagent_start.py` (lines 109-111, 383-416)
**Recipient:** All sub-agents (if PTC configured)
**Frequency:** Per spawn

**Current format:**
```
=== PTC SANDBOX CONTEXT ===
Container: kernel-chunk-coder-abc123
Image: ptc-chunk-coder:latest
Network: bridge
Project mount: /workspace (read-write)
```

**Compact format:**
```
# PTC c=container i=image n=network m=mount
c:kernel-chunk-coder-abc123 i:ptc-chunk-coder:latest n:bridge m:/workspace(rw)
```

**Baseline:** ~200 chars = **50 tokens**
**Compact:** ~120 chars = **30 tokens**
**Savings: 20 tokens (40%)**

---

#### IP-06: SubagentStart — Workflow Context

**File:** `hooks/subagent_start.py` (lines 84-86, 134-161)
**Recipient:** All sub-agents
**Frequency:** Per spawn

**Current format:**
```
=== WORKFLOW CONTEXT ===
Spawned by: coder-p1-t3-a7f2 (role: chunk-coder, state: IMPLEMENTATION)
Phase: IMPLEMENTATION
Task: chunk-02
Feature: add-streaming-mode | Workflow: IN_PROGRESS
```

**Compact format:**
```
# WORKFLOW parent=spawner r=role s=state p=phase t=task f=feature ws=workflow_state
parent:coder-p1-t3-a7f2 r:chunk-coder s:IMPLEMENTATION p:IMPLEMENTATION t:chunk-02 f:add-streaming-mode ws:IN_PROGRESS
```

**Baseline:** ~300 chars = **75 tokens**
**Compact:** ~200 chars = **50 tokens**
**Savings: 25 tokens (33%)**

---

#### IP-07: SessionStart — Agent Registration

**File:** `hooks/session_start.py` (lines 133-148)
**Recipient:** All workflow agents
**Frequency:** Once per agent spawn

**Current format:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "Agent ID: coder-p1-t3-a7f2\nRole: chunk-coder\n\nActive preferences:\n  Global:\n    - test_framework: pytest\n    - max_context_pct: 85\n  Role (chunk-coder):\n    - impl_style: tdd\n    - commit_frequency: per_chunk"
  }
}
```

**Compact format:**
```
# AGENT id=agent_id r=role
id:coder-p1-t3-a7f2 r:chunk-coder
# PREFS k=key v=value r=role
k:test_framework v:pytest
k:max_context_pct v:85
k:impl_style v:tdd r:chunk-coder
k:commit_frequency v:per_chunk r:chunk-coder
```

**Baseline:** ~600 chars = **150 tokens**
**Compact:** ~400 chars = **100 tokens**
**Savings: 50 tokens (33%)**

---

#### IP-08: SessionStart — Continuation After Compaction

**File:** `hooks/session_start.py` (lines 141-146), source: `hooks/pre_compact.py` (lines 391-557)
**Recipient:** Agent resuming after context compaction
**Frequency:** Once per compaction event

**Current format (narrative text ~1500 chars):**
```
=== CONTINUATION AFTER COMPACTION ===
Agent ID: coder-p1-t3-a7f2 (role: chunk-coder), state machine position: TEST_IMPLEMENTATION
Assigned task: chunk-03
Phase: IMPLEMENTATION
Feature: add-streaming-mode | Workflow state: IN_PROGRESS

Plan progress: 2/5 tasks complete, 1 in progress
Current task: chunk-03 — Advanced error handling

Session status: in_progress
Last state: TEST_IMPLEMENTATION (Tests failing: 2 of 7 passing)
Tests: 2/7 passing
Files modified: 3

Key decisions made (most recent):
  - Error handling strategy: exponential backoff with max retries
  - State cleanup approach: context manager pattern

ACTIVE_AGENTS:
  id=explorer-xyz789 | role=codebase-explorer | state=IDLE | task=none | context=42%

RECENT_MESSAGES:
  from=orchestrator | to=coder-p1-t3-a7f2 | type=task_assignment | summary=Implement chunk-03
  from=coder-p1-t3-a7f2 | to=researcher-abc456 | type=context_request | summary=Need FastAPI error handling docs
```

**Compact + TOON format:**
```
# CONTINUATION id=agent_id r=role s=state t=task p=phase f=feature ws=workflow_state
id:coder-p1-t3-a7f2 r:chunk-coder s:TEST_IMPLEMENTATION t:chunk-03 p:IMPLEMENTATION f:add-streaming-mode ws:IN_PROGRESS
plan:2/5 complete, 1 in progress | current:chunk-03 - Advanced error handling
session:in_progress | tests:2/7 | files_modified:3
# DECISIONS
- Error handling strategy: exponential backoff with max retries
- State cleanup approach: context manager pattern
# ACTIVE_AGENTS
id|role|state|task|ctx%
explorer-xyz789|codebase-explorer|IDLE|none|42
# RECENT_MSGS
from|to|type|summary
orchestrator|coder-p1-t3-a7f2|task_assign|Implement chunk-03
coder-p1-t3-a7f2|researcher-abc456|context_request|Need FastAPI error handling docs
```

**Baseline:** ~1500 chars = **375 tokens**
**Compact+TOON:** ~1000 chars = **250 tokens**
**Savings: 125 tokens (33%)**

---

#### IP-09: SessionStart — Workflow Resumption

**File:** `hooks/session_start.py` (lines 225-227, 232-331)
**Recipient:** Orchestrator
**Frequency:** Once per orchestrator resume with in-progress workflow

**Current format:**
```
=== WORKFLOW RESUMPTION DETECTED ===
Workflow: wf-streaming-20260310-a3b4
Feature: add-streaming-mode
State: PHASE_IMPLEMENTATION
Base branch: feature/streaming
Started: 2026-03-10T09:00:00Z
Last updated: 2026-03-10T14:30:00Z
```

**Compact format:**
```
# WORKFLOW_RESUME wf=workflow_id f=feature s=state br=branch started=start updated=last
wf:wf-streaming-20260310-a3b4 f:add-streaming-mode s:PHASE_IMPLEMENTATION br:feature/streaming started:2026-03-10T09:00 updated:2026-03-10T14:30
```

**Baseline:** ~800 chars = **200 tokens**
**Compact:** ~480 chars = **120 tokens**
**Savings: 80 tokens (40%)**

---

#### IP-10: SessionStart — Dead Agents Roster

**File:** `hooks/session_start.py` (within workflow resumption block)
**Recipient:** Orchestrator
**Frequency:** Once per resume (if agents died)

**Current format:**
```
Dead agents found:
  - coder-p1-t3-a7f2 (chunk-coder, IMPLEMENTATION) [has handoff]
  - explorer-xyz-789 (codebase-explorer, EXPLORING) [no handoff]
  - researcher-abc-456 (researcher, RESEARCHING) [has handoff]

Handoff files:
  1. .claude/handoffs/coder-p1-t3-a7f2.json
  2. .claude/handoffs/researcher-abc-456.json

Agents without handoffs:
  1. explorer-xyz-789
```

**TOON format:**
```
# DEAD_AGENTS
id|role|state|handoff
coder-p1-t3-a7f2|chunk-coder|IMPLEMENTATION|.claude/handoffs/coder-p1-t3-a7f2.json
explorer-xyz-789|codebase-explorer|EXPLORING|none
researcher-abc-456|researcher|RESEARCHING|.claude/handoffs/researcher-abc-456.json
```

**Baseline:** ~600 chars = **150 tokens**
**TOON:** ~240 chars = **60 tokens**
**Savings: 90 tokens (60%)**

---

#### IP-11: PostToolUse — Annotations

**File:** `hooks/post_tool_use.py` (lines 90-98)
**Recipient:** All agents
**Frequency:** After EVERY tool call (if unacknowledged annotations exist)
**Source:** `~/.claude/annotations/{agent-id}.jsonl`

**Current format (formatted text):**
```
[ANNOTATION CRITICAL] [ann-a7f2c1d5] (state_machine) Use Think tool to decide: fairness-based (A) or throughput-based (B)?

[ANNOTATION NORMAL] [ann-b3e1f2d6] (user) Consider memory constraints for batch size

[ANNOTATION FYI] [ann-c4d5e6f7] (quality_gate) Lint passed: no style issues
```

**Compact format:**
```
# ANN p=priority id=ann_id s=source m=message
p:CRIT id:ann-a7f2c1d5 s:state_machine m:Use Think tool to decide: fairness-based (A) or throughput-based (B)?
p:NORM id:ann-b3e1f2d6 s:user m:Consider memory constraints for batch size
p:FYI id:ann-c4d5e6f7 s:quality_gate m:Lint passed: no style issues
```

**Baseline:** Variable, ~200-2000 chars = **50-500 tokens**
**Compact:** ~140-1400 chars = **35-350 tokens**
**Savings: ~30% average**

Note: This is the **highest-frequency** injection point. Even small per-instance savings compound significantly. With ~100 tool calls per session and ~30% having annotations, this saves ~300-900 tokens/session.

---

#### IP-12 & IP-13: PreToolUse/PostToolUse — Daemon Inject Context

**File:** `hooks/pre_tool_use.py` (lines 125-138), `hooks/post_tool_use.py` (lines 90-98)
**Recipient:** All agents
**Frequency:** Conditional (when daemon returns `inject_context`)

These are free-form text from the daemon. Savings depend on daemon output format. The daemon should use compact format when building `inject_context` strings.

**Baseline:** ~500 chars = **125 tokens** each
**Compact:** ~360 chars = **90 tokens** each
**Savings: 35 tokens (28%) each**

---

### CATEGORY B: Context Packet Injections

Agents read these JSON files directly. The conversion happens at a serialization layer — a `to_toon()` utility that agents call when loading context into their working memory.

---

#### IP-14: Codebase Context — structure.blocks[]

**File:** `.claude/context/_codebase.json`
**Recipient:** Plan-architect, Chunk-coder
**Frequency:** Once per feature (at startup)

**Current JSON:**
```json
{
  "blocks": [
    {
      "name": "producer",
      "path": "producer/",
      "purpose": "detection, tracking, ROI cropping, quality scoring",
      "key_files": {
        "config": "config.py",
        "models": "models.py",
        "pipeline": "pipeline.py",
        "ops": ["ops_detection.py", "ops_tracking.py"]
      }
    },
    {
      "name": "consumer",
      "path": "consumer/",
      "purpose": "batch processing, result aggregation",
      "key_files": {
        "config": "config.py",
        "processor": "processor.py"
      }
    }
  ]
}
```

**TOON format:**
```
# BLOCKS
name|path|purpose|key_files
producer|producer/|detection, tracking, ROI cropping, quality scoring|config.py,models.py,pipeline.py,ops_detection.py,ops_tracking.py
consumer|consumer/|batch processing, result aggregation|config.py,processor.py
```

**Baseline:** ~3200 chars (typical 8-block project) = **800 tokens**
**TOON:** ~1600 chars = **400 tokens**
**Savings: 400 tokens (50%)**

---

#### IP-15: Codebase Context — types arrays

**File:** `.claude/context/_codebase.json`
**Recipient:** Plan-architect, Chunk-coder
**Frequency:** Once per feature

**Current JSON:**
```json
{
  "shared": [
    {
      "name": "RoiImage",
      "file": "common/models.py:15",
      "kind": "dataclass",
      "fields": [
        {"name": "image_id", "type": "str"},
        {"name": "bbox", "type": "tuple[int,int,int,int]"}
      ],
      "usage": "Region of interest with metadata"
    }
  ]
}
```

**TOON format:**
```
# TYPES
name|file|kind|fields|usage
RoiImage|common/models.py:15|dataclass|image_id:str,bbox:tuple[int,int,int,int]|Region of interest with metadata
DetectionResult|producer/models.py:42|dataclass|detections:list[RoiImage]|Output of detection pipeline
TrackId|common/models.py:5|alias(str)||Unique track identifier
```

**Baseline:** ~2000 chars (typical 10 types) = **500 tokens**
**TOON:** ~800 chars = **200 tokens**
**Savings: 300 tokens (60%)**

---

#### IP-16: Codebase Context — configs[]

**File:** `.claude/context/_codebase.json`
**Recipient:** Plan-architect, Chunk-coder
**Frequency:** Once per feature

**Current JSON:**
```json
{
  "configs": [
    {
      "name": "ProducerConfig",
      "file": "producer/config.py:12",
      "fields": [
        {"name": "max_batch_size", "type": "int", "default": "32", "description": "Maximum ROIs per batch"},
        {"name": "min_quality", "type": "float", "default": "0.5", "description": "Minimum quality threshold"}
      ]
    }
  ]
}
```

**TOON format:**
```
# CONFIGS
config_name|file|field|type|default|description
ProducerConfig|producer/config.py:12|max_batch_size|int|32|Maximum ROIs per batch
ProducerConfig||min_quality|float|0.5|Minimum quality threshold
```

**Baseline:** ~1200 chars (typical 3 configs, 5 fields each) = **300 tokens**
**TOON:** ~600 chars = **150 tokens**
**Savings: 150 tokens (50%)**

---

#### IP-17: Codebase Context — patterns

**File:** `.claude/context/_codebase.json`
**Recipient:** Plan-architect, Chunk-coder
**Frequency:** Once per feature

This is a nested object (not an array), so TOON doesn't apply. Use compact key aliases.

**Current JSON:**
```json
{
  "error_handling": {
    "style": "custom_exceptions",
    "base_class": "common/errors.py:AppError",
    "catch_at": "pipeline boundaries",
    "examples": ["producer/errors.py:20", "producer/pipeline.py:50"]
  },
  "logging": {
    "library": "structlog",
    "import": "from common.log import logger",
    "style": "structured json logging"
  }
}
```

**Compact format:**
```
# PATTERNS
error_handling: style=custom_exceptions base=common/errors.py:AppError catch=pipeline_boundaries ex:producer/errors.py:20,producer/pipeline.py:50
logging: lib=structlog import="from common.log import logger" style=structured_json
testing: fw=pytest style=test_*.py fixtures=tests/conftest.py
imports: style=absolute rules="from common.models import X"
naming: fn=snake_case cls=PascalCase files=snake_case ops=ops_*.py
```

**Baseline:** ~800 chars = **200 tokens**
**Compact:** ~520 chars = **130 tokens**
**Savings: 70 tokens (35%)**

---

#### IP-18: Feature Context — touchpoints[]

**File:** `.claude/context/{feature}-context.json`
**Recipient:** Plan-architect, Chunk-coder
**Frequency:** Once per feature

**Current JSON:**
```json
{
  "touchpoints": [
    {
      "file": "producer/ops_batch.py",
      "action": "create",
      "reason": "New batch selection logic",
      "lines_of_interest": null,
      "key_items": ["select_batch", "BatchCandidate"]
    },
    {
      "file": "producer/pipeline.py",
      "action": "modify",
      "reason": "Call batch selection in pipeline",
      "lines_of_interest": "80-100",
      "key_items": ["run_frame"]
    }
  ]
}
```

**TOON format:**
```
# TOUCHPOINTS
file|action|reason|lines|key_items
producer/ops_batch.py|create|New batch selection logic||select_batch,BatchCandidate
producer/pipeline.py|modify|Call batch selection in pipeline|80-100|run_frame
producer/config.py|modify|Add batch config fields|30-50|ProducerConfig
```

**Baseline:** ~1600 chars (typical 8 touchpoints) = **400 tokens**
**TOON:** ~640 chars = **160 tokens**
**Savings: 240 tokens (60%)**

---

#### IP-19: Feature Context — dependencies

**File:** `.claude/context/{feature}-context.json`
**Recipient:** Plan-architect, Chunk-coder
**Frequency:** Once per feature

**Current JSON:**
```json
{
  "dependencies": {
    "types_needed": [
      {"name": "RoiImage", "from": "common/models.py", "used_for": "Input candidate ROIs"}
    ],
    "configs_needed": [
      {"name": "ProducerConfig", "fields_used": ["max_batch_size", "min_quality"]}
    ],
    "functions_needed": [
      {"name": "compute_quality_score", "from": "producer/ops_detection.py", "used_for": "Score each ROI"}
    ]
  }
}
```

**TOON format:**
```
# DEPS_TYPES
name|from|used_for
RoiImage|common/models.py|Input candidate ROIs
# DEPS_CONFIGS
name|fields_used
ProducerConfig|max_batch_size,min_quality
# DEPS_FUNCTIONS
name|from|used_for
compute_quality_score|producer/ops_detection.py|Score each ROI
```

**Baseline:** ~1200 chars = **300 tokens**
**TOON:** ~600 chars = **150 tokens**
**Savings: 150 tokens (50%)**

---

#### IP-20: Feature Context — new_items

**File:** `.claude/context/{feature}-context.json`
**Recipient:** Plan-architect, Chunk-coder
**Frequency:** Once per feature

**Current JSON:**
```json
{
  "new_items": {
    "types": [
      {"name": "BatchCandidate", "file": "producer/models.py", "purpose": "ROI with diversity score", "fields": [{"name": "roi", "type": "RoiImage"}, {"name": "diversity_score", "type": "float"}]}
    ],
    "functions": [
      {"name": "select_batch", "file": "producer/ops_batch.py", "signature": "(candidates: list[RoiImage], cfg: ProducerConfig) -> list[RoiImage]", "purpose": "Core batch selection algorithm"}
    ]
  }
}
```

**TOON format:**
```
# NEW_TYPES
name|file|purpose|fields
BatchCandidate|producer/models.py|ROI with diversity score|roi:RoiImage,diversity_score:float
# NEW_FUNCTIONS
name|file|signature|purpose
select_batch|producer/ops_batch.py|(candidates: list[RoiImage], cfg: ProducerConfig) -> list[RoiImage]|Core batch selection algorithm
```

**Baseline:** ~1000 chars = **250 tokens**
**TOON:** ~500 chars = **125 tokens**
**Savings: 125 tokens (50%)**

---

#### IP-21: Query Result — details[]

**File:** `.claude/context/queries/{topic}.json`
**Recipient:** Any requesting agent
**Frequency:** On demand

**Current JSON:**
```json
{
  "details": [
    {"item": "OpsError base class", "location": "producer/errors.py:12", "relevance": "Custom base exception to inherit from"},
    {"item": "Catch point in pipeline", "location": "producer/pipeline.py:45-50", "relevance": "Where errors surface and are caught"}
  ]
}
```

**TOON format:**
```
# QUERY_DETAILS
item|location|relevance
OpsError base class|producer/errors.py:12|Custom base exception to inherit from
Catch point in pipeline|producer/pipeline.py:45-50|Where errors surface and are caught
```

**Baseline:** ~800 chars = **200 tokens**
**TOON:** ~320 chars = **80 tokens**
**Savings: 120 tokens (60%)**

---

#### IP-22: Query Result — sources[]

**File:** `.claude/context/queries/{topic}.json`
**Recipient:** Any requesting agent
**Frequency:** On demand

**Current JSON:**
```json
{
  "sources": [
    {"file": "producer/errors.py", "lines_read": "1-30", "relevant": true},
    {"file": "producer/pipeline.py", "lines_read": "40-60", "relevant": true},
    {"file": "producer/ops_detection.py", "lines_read": "1-20", "relevant": false}
  ]
}
```

**TOON format:**
```
# QUERY_SOURCES
file|lines|relevant
producer/errors.py|1-30|true
producer/pipeline.py|40-60|true
producer/ops_detection.py|1-20|false
```

**Baseline:** ~600 chars = **150 tokens**
**TOON:** ~240 chars = **60 tokens**
**Savings: 90 tokens (60%)**

---

### CATEGORY C: Plan & Task Injections

---

#### IP-23: Plan — chunks[] array (overview)

**File:** `.claude/plans/{feature}-plan.json`
**Recipient:** Chunk-coder (reads full plan, extracts current chunk)
**Frequency:** Once per chunk start

The full plan JSON is large. When the coder loads it, the chunk list overview can be TOONified.

**Current JSON (chunk list overview):**
```json
{
  "chunks": [
    {"id": "chunk-01", "name": "Types and Config", "purpose": "foundation", "status": "completed", "dependencies": []},
    {"id": "chunk-02", "name": "Core Logic", "purpose": "core_logic", "status": "in_progress", "dependencies": ["chunk-01"]},
    {"id": "chunk-03", "name": "Pipeline Integration", "purpose": "integration", "status": "pending", "dependencies": ["chunk-02"]}
  ]
}
```

**TOON format:**
```
# PLAN_CHUNKS
id|name|purpose|status|dependencies
chunk-01|Types and Config|foundation|completed|
chunk-02|Core Logic|core_logic|in_progress|chunk-01
chunk-03|Pipeline Integration|integration|pending|chunk-02
```

**Baseline:** (for chunk overview portion) ~2400 chars (10 chunks) = **600 tokens**
**TOON:** ~1400 chars = **350 tokens**
**Savings: 250 tokens (42%)**

---

#### IP-24: Plan — invariants[] per chunk

**File:** `.claude/plans/{feature}-plan.json`
**Recipient:** Chunk-coder
**Frequency:** Per chunk

**Current JSON:**
```json
{
  "invariants": [
    {"id": "inv-02-01", "description": "Executor importable with correct __init__ signature", "level": "existence", "verify": {"command": "python -c \"...\"", "expect": "exit_code_0"}},
    {"id": "inv-02-02", "description": "Execute returns tuple with stdout, stderr, return_code", "level": "behavioral", "verify": {"command": "python -c \"...\"", "expect": "exit_code_0"}}
  ]
}
```

**TOON format:**
```
# INVARIANTS
id|level|description|verify_cmd|expect
inv-02-01|existence|Executor importable with correct __init__ signature|python -c "..."|exit_code_0
inv-02-02|behavioral|Execute returns tuple with stdout, stderr, return_code|python -c "..."|exit_code_0
```

**Baseline:** ~1200 chars (4 invariants) = **300 tokens**
**TOON:** ~480 chars = **120 tokens**
**Savings: 180 tokens (60%)**

---

#### IP-25: Plan — test_spec.cases[]

**File:** `.claude/plans/{feature}-plan.json`
**Recipient:** Chunk-coder (during test_design phase)
**Frequency:** Per chunk

**Current JSON:**
```json
{
  "cases": [
    {"name": "test_executor_init_creates_namespace", "tests": "Executor.__init__ initializes empty _namespace dict", "category": "existence"},
    {"name": "test_execute_simple_print", "tests": "execute('print(42)') captures stdout", "category": "core_logic", "data_strategy": "L1 golden"},
    {"name": "test_namespace_persists", "tests": "x=1 in call 1, accessible in call 2", "category": "behavioral", "data_strategy": "L1 golden"}
  ]
}
```

**TOON format:**
```
# TEST_CASES
name|tests|category|data_strategy
test_executor_init_creates_namespace|Executor.__init__ initializes empty _namespace dict|existence|
test_execute_simple_print|execute('print(42)') captures stdout|core_logic|L1 golden
test_namespace_persists|x=1 in call 1, accessible in call 2|behavioral|L1 golden
```

**Baseline:** ~1000 chars (7 test cases) = **250 tokens**
**TOON:** ~400 chars = **100 tokens**
**Savings: 150 tokens (60%)**

---

### CATEGORY D: Session Log / Handoff Injections

---

#### IP-26: Session Log — tests_written[]

**File:** `.claude/logs/{feature}-{chunk}-log.json`
**Recipient:** Replacement agent (on handoff) or next chunk coder
**Frequency:** Per handoff

**Current JSON:**
```json
{
  "tests_written": [
    {"name": "test_select_batch_empty_input", "file": "tests/test_batch.py", "status": "pass", "failure_count": 0},
    {"name": "test_select_batch_single_item", "file": "tests/test_batch.py", "status": "pass", "failure_count": 2},
    {"name": "test_apply_filters_diverse", "file": "tests/test_batch.py", "status": "pass", "failure_count": 1},
    {"name": "test_batch_overflow_handling", "file": "tests/test_batch.py", "status": "fail", "failure_count": 3}
  ]
}
```

**TOON format:**
```
# TESTS
name|file|status|failures
test_select_batch_empty_input|tests/test_batch.py|pass|0
test_select_batch_single_item|tests/test_batch.py|pass|2
test_apply_filters_diverse|tests/test_batch.py|pass|1
test_batch_overflow_handling|tests/test_batch.py|fail|3
```

**Baseline:** ~1600 chars (20 tests typical) = **400 tokens**
**TOON:** ~640 chars = **160 tokens**
**Savings: 240 tokens (60%)**

---

#### IP-27: Session Log — decisions_made[]

**File:** `.claude/logs/{feature}-{chunk}-log.json`
**Recipient:** Replacement agent or next chunk coder
**Frequency:** Per handoff or chunk boundary

**Current JSON:**
```json
{
  "decisions_made": [
    {"decision": "Use composition for batch state management", "reason": "User preference + matches existing pattern", "locked": true, "source": "user_preference"},
    {"decision": "Batch overflow -> yield empty batch", "reason": "Preserves pipeline flow", "locked": false, "source": "judgment"}
  ]
}
```

**Compact format (decisions are heterogeneous, keep readable):**
```
# DECISIONS locked=L src=source
[L:yes src:user_preference] Use composition for batch state management — User preference + matches existing pattern
[L:no src:judgment] Batch overflow -> yield empty batch — Preserves pipeline flow
```

**Baseline:** ~800 chars = **200 tokens**
**Compact:** ~520 chars = **130 tokens**
**Savings: 70 tokens (35%)**

---

#### IP-28: Session Log — phase_history[]

**File:** `.claude/logs/{feature}-{chunk}-log.json`
**Recipient:** Replacement agent
**Frequency:** Per handoff

**Current JSON:**
```json
{
  "phase_history": [
    {"phase": "test_design", "timestamp": "2026-02-28T19:20:00Z", "details": "Designed 7 tests"},
    {"phase": "red_verified", "timestamp": "2026-02-28T19:24:00Z", "details": "7 tests failed: ModuleNotFoundError"},
    {"phase": "green_verified", "timestamp": "2026-02-28T19:30:00Z", "details": "7/7 tests passing"}
  ]
}
```

**TOON format:**
```
# PHASE_HISTORY
phase|time|details
test_design|19:20|Designed 7 tests
red_verified|19:24|7 tests failed: ModuleNotFoundError
green_verified|19:30|7/7 tests passing
```

**Baseline:** ~600 chars = **150 tokens**
**TOON:** ~240 chars = **60 tokens**
**Savings: 90 tokens (60%)**

---

#### IP-29: Session Log — files_modified[]

**File:** `.claude/logs/{feature}-{chunk}-log.json`
**Recipient:** Replacement agent, re-exploration
**Frequency:** Per handoff

**Current JSON:**
```json
{
  "files_modified": [
    {"path": "src/producer/batch.py", "action": "modified", "lines_changed": 145},
    {"path": "tests/test_batch_selection.py", "action": "created", "lines_changed": 320}
  ]
}
```

**TOON format:**
```
# FILES_MODIFIED
path|action|lines
src/producer/batch.py|modified|145
tests/test_batch_selection.py|created|320
```

**Baseline:** ~400 chars = **100 tokens**
**TOON:** ~160 chars = **40 tokens**
**Savings: 60 tokens (60%)**

---

#### IP-30: Session Log — invariants_status{}

**File:** `.claude/logs/{feature}-{chunk}-log.json`
**Recipient:** Replacement agent
**Frequency:** Per handoff

**Current JSON:**
```json
{
  "invariants_status": {
    "inv-02-01": {"status": "pass", "last_checked": "2026-02-05T10:45:00Z"},
    "inv-02-02": {"status": "fail", "last_checked": "2026-02-05T10:50:00Z", "failure_message": "Generator return type mismatches"},
    "inv-02-03": {"status": "not_run"},
    "inv-02-04": {"status": "not_run"}
  }
}
```

**TOON format:**
```
# INVARIANT_STATUS
id|status|checked|failure
inv-02-01|pass|10:45|
inv-02-02|fail|10:50|Generator return type mismatches
inv-02-03|not_run||
inv-02-04|not_run||
```

**Baseline:** ~400 chars = **100 tokens**
**TOON:** ~160 chars = **40 tokens**
**Savings: 60 tokens (60%)**

---

### CATEGORY E: Research Injections

---

#### IP-31: Persistent Research — endpoints[]

**File:** `.claude/research/persistent/{id}.json`
**Recipient:** Chunk-coder, Plan-architect
**Frequency:** On demand

**Current JSON:**
```json
{
  "documentation": {
    "endpoints": [
      {
        "name": "batch_predict",
        "signature": "(images: list[np.ndarray], batch_size: int = 4) -> list[Mask]",
        "description": "Run batch prediction on multiple images",
        "parameters": [
          {"name": "images", "type": "list[np.ndarray]", "required": true, "description": "Input images"},
          {"name": "batch_size", "type": "int", "required": false, "description": "Batch size", "default": "4"}
        ],
        "returns": "list[Mask]",
        "raises": ["OOMError", "InvalidImageError"]
      }
    ]
  }
}
```

**TOON format:**
```
# API_ENDPOINTS
name|signature|description|returns|raises
batch_predict|(images: list[np.ndarray], batch_size: int = 4) -> list[Mask]|Run batch prediction on multiple images|list[Mask]|OOMError,InvalidImageError
# PARAMS endpoint=batch_predict
name|type|required|default|description
images|list[np.ndarray]|yes||Input images
batch_size|int|no|4|Batch size
```

**Baseline:** ~2000 chars (3-4 endpoints) = **500 tokens**
**TOON:** ~800 chars = **200 tokens**
**Savings: 300 tokens (60%)**

---

#### IP-32: Persistent Research — examples[]

**File:** `.claude/research/persistent/{id}.json`
**Recipient:** Chunk-coder, Plan-architect
**Frequency:** On demand

**Current JSON:**
```json
{
  "examples": [
    {
      "title": "Basic batch inference",
      "description": "Run SAM2 on a batch of images",
      "code": "from sam2 import SAM2Predictor\npredictor = SAM2Predictor.from_pretrained('sam2-base')\nresults = predictor.batch_predict(images, batch_size=4)",
      "output": "list of Mask objects with shape matching input"
    }
  ]
}
```

Code blocks should NOT be TOONified (they need to be readable). But the metadata can be compacted:

**Compact format:**
```
# EXAMPLE title="Basic batch inference" desc="Run SAM2 on a batch of images"
```python
from sam2 import SAM2Predictor
predictor = SAM2Predictor.from_pretrained('sam2-base')
results = predictor.batch_predict(images, batch_size=4)
```
output: list of Mask objects with shape matching input
```

**Baseline:** ~1200 chars (3 examples) = **300 tokens**
**Compact:** ~600 chars = **150 tokens**
**Savings: 150 tokens (50%)**

---

## Non-Injection Points (Ruled Out)

These data flows were investigated but do NOT inject into agent context windows:

| Data | Why Not Injected |
|---|---|
| `~/.claude/logs/workflow-events.jsonl` | Fire-and-forget observability; never read by agents |
| `~/.claude/logs/state-transitions.jsonl` | Written by daemon for audit trail; never injected |
| `~/.claude/logs/decisions/{agent}.jsonl` | Async PostToolUse logging; never re-read into context |
| `~/.claude/logs/context-checks.jsonl` | Observability only |
| `~/.claude/logs/snapshots/*.json` | Dashboard consumption; never injected |
| `hooks/stop.py` output | Prints to stdout for user; no `additionalContext` |
| `hooks/subagent_stop.py` output | Validates but does not inject |
| `hooks/pre_compact.py` output | Saves snapshot to disk; SessionStart reads it later (covered in IP-08) |
| Trace context (`CLAUDE_TRACE_ID` etc.) | Environment variables; zero tokens |

---

## Implementation Priority

### Tier 1: Highest Impact (implement first)

These are either high-frequency or large data structures:

| Priority | IP # | Description | Per-Instance Savings | Frequency | Session Savings |
|---|---|---|---|---|---|
| P1 | IP-14 | Codebase blocks | 400 tokens | Per feature | 400 |
| P1 | IP-15 | Codebase types | 300 tokens | Per feature | 300 |
| P1 | IP-18 | Feature touchpoints | 240 tokens | Per feature | 240 |
| P1 | IP-26 | Session log tests | 240 tokens | Per handoff | 480 (2 handoffs) |
| P1 | IP-11 | PostToolUse annotations | ~15 tokens avg | Per tool call | 450 (~30 injections) |
| P1 | IP-01 | Message bus window | 120 tokens | Per spawn | 480 (4 agents) |
| P1 | IP-24 | Plan invariants | 180 tokens | Per chunk | 900 (5 chunks) |
| P1 | IP-25 | Plan test cases | 150 tokens | Per chunk | 750 (5 chunks) |

### Tier 2: Medium Impact

| Priority | IP # | Description | Per-Instance Savings | Frequency | Session Savings |
|---|---|---|---|---|---|
| P2 | IP-08 | Continuation post-compact | 125 tokens | Per compact | 250 (2 compactions) |
| P2 | IP-23 | Plan chunks overview | 250 tokens | Per chunk | 250 |
| P2 | IP-19 | Feature dependencies | 150 tokens | Per feature | 150 |
| P2 | IP-20 | Feature new_items | 125 tokens | Per feature | 125 |
| P2 | IP-31 | Research endpoints | 300 tokens | On demand | 300 |
| P2 | IP-10 | Dead agents roster | 90 tokens | Per resume | 90 |
| P2 | IP-28 | Phase history | 90 tokens | Per handoff | 180 |

### Tier 3: Lower Impact (but easy wins)

| Priority | IP # | Description | Per-Instance Savings |
|---|---|---|---|
| P3 | IP-02 | Key files read | 40 tokens |
| P3 | IP-03 | Parent task | 50 tokens |
| P3 | IP-04 | Preferences | 40 tokens |
| P3 | IP-05 | PTC context | 20 tokens |
| P3 | IP-06 | Workflow context | 25 tokens |
| P3 | IP-07 | Agent registration | 50 tokens |
| P3 | IP-09 | Workflow resumption | 80 tokens |
| P3 | IP-16 | Codebase configs | 150 tokens |
| P3 | IP-17 | Codebase patterns | 70 tokens |
| P3 | IP-21 | Query details | 120 tokens |
| P3 | IP-22 | Query sources | 90 tokens |
| P3 | IP-27 | Decisions made | 70 tokens |
| P3 | IP-29 | Files modified | 60 tokens |
| P3 | IP-30 | Invariant status | 60 tokens |
| P3 | IP-32 | Research examples | 150 tokens |

---

## Implementation Notes

### Conversion Functions Needed

All live in `hooks/utils/state_helpers.py`:

```python
def to_toon(header: str, objects: list[dict], fields: list[str]) -> str:
    """Convert array of homogeneous objects to pipe-delimited table."""
    lines = [f"# {header}", "|".join(fields)]
    for obj in objects:
        lines.append("|".join(str(obj.get(f, "")) for f in fields))
    return "\n".join(lines)

def to_compact(header: str, obj: dict, aliases: dict[str, str]) -> str:
    """Convert single object to abbreviated key:value pairs."""
    legend = " ".join(f"{v}={k}" for k, v in aliases.items())
    values = " ".join(f"{aliases[k]}:{obj[k]}" for k in aliases if k in obj)
    return f"# {header} {legend}\n{values}"

def dump_compact(model: BaseModel) -> dict:
    """Serialize Pydantic model excluding defaults and None values."""
    return model.model_dump(exclude_defaults=True, exclude_none=True)
```

### Key Principle

Agents WRITE normal JSON to disk. Hooks validate JSON. At INJECTION TIME (when data enters another agent's context via `additionalContext`), it gets converted to TOON/compact. Agents never write TOON — they only read it.

### Where Conversions Happen

| Conversion Site | Handles IPs |
|---|---|
| `hooks/subagent_start.py` | IP-01 through IP-06 |
| `hooks/session_start.py` | IP-07 through IP-10 |
| `hooks/post_tool_use.py` | IP-11, IP-12 |
| `hooks/pre_tool_use.py` | IP-13 |
| `hooks/pre_compact.py` (continuation builder) | IP-08 |
| Agent skill code (context loader) | IP-14 through IP-32 |

For IP-14 through IP-32 (file-based injections), the agents read JSON files but should use a `load_as_toon()` utility when loading context packets, plans, session logs, and research files into their working context. This utility reads JSON, validates it, then converts relevant arrays to TOON format for internal consumption.
