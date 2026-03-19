# Token Efficiency Analysis

**Date:** 2026-02-26
**Auditor:** System Audit Agent (Opus 4.6)

---

## Executive Summary

The current schema package consumes ~10,773 tokens when fully loaded. The state machines add ~8,570 tokens. Hook scripts range from 1,650 to 4,450 tokens each. Optimization could reduce LLM context injection overhead by 30-60% using compact serialization, field abbreviation, and progressive disclosure.

---

## 1. Measured Token Counts

### Schema Files (Character-Based Estimate: chars/4)

| File | Characters | Tokens (est.) | Injected into LLM? |
|------|-----------|---------------|---------------------|
| `__init__.py` | 355 | 89 | No |
| `agent_state.py` | 2,130 | 533 | Indirectly (via state files) |
| `system_state.py` | 2,300 | 575 | Indirectly (via workflow state) |
| `preferences.py` | 1,400 | 350 | Yes (SessionStart context) |
| `state_machine.py` | 3,500 | 875 | Yes (state definition context) |
| `annotation.py` | 2,650 | 663 | Yes (PostToolUse additionalContext) |
| `message_protocol.py` | 9,800 | 2,450 | Yes (SendMessage validation) |
| `session_log.py` | 8,200 | 2,050 | Yes (handoff/continuation context) |
| `handoff.py` | 5,500 | 1,375 | Yes (SubagentStart/PreCompact context) |
| `hook_output.py` | 2,500 | 625 | No (internal hook output) |
| `state_transition_log.py` | 1,650 | 413 | Indirectly (via reports) |
| `decision_log.py` | 1,400 | 350 | Yes (PreCompact continuation) |
| `message_bus_log.py` | 1,700 | 425 | Yes (SubagentStart context) |
| **Schema Total** | **43,085** | **10,773** | — |

### State Machine Files

| File | Characters | Tokens (est.) |
|------|-----------|---------------|
| `system.json` | 5,000 | 1,250 |
| `coder.json` | 5,600 | 1,400 |
| `tester.json` | 2,600 | 650 |
| `auditor-task.json` | 1,720 | 430 |
| `auditor-phase.json` | 1,600 | 400 |
| `explorer.json` | 2,480 | 620 |
| `researcher.json` | 2,880 | 720 |
| `strategist.json` | 4,400 | 1,100 |
| **State Machine Total** | **26,280** | **6,570** |

### Hook Scripts

| File | Characters | Tokens (est.) | Frequency |
|------|-----------|---------------|-----------|
| `pre_tool_use.py` | 6,580 | 1,650 | Every tool call |
| `post_tool_use.py` | 17,800 | 4,450 | Every tool call |
| `session_start.py` | 11,900 | 2,975 | Session start/compact |
| `subagent_start.py` | 12,100 | 3,025 | Per sub-agent spawn |
| `subagent_stop.py` | 9,400 | 2,350 | Per sub-agent completion |
| `stop.py` | 7,600 | 1,900 | Session end |
| `pre_compact.py` | 10,700 | 2,675 | Per compaction |
| **Hook Total** | **76,080** | **19,025** | — |

### Hook Utilities

| File | Characters | Tokens (est.) |
|------|-----------|---------------|
| `__init__.py` | 2,060 | 515 |
| `state_helpers.py` | 2,720 | 680 |
| `context_monitor.py` | 4,140 | 1,035 |
| `schema_validator.py` | 9,040 | 2,260 |
| `trace_context.py` | 6,640 | 1,660 |
| `event_logger.py` | 4,900 | 1,225 |
| **Utility Total** | **29,500** | **7,375** |

### Rules

| File | Characters | Tokens (est.) |
|------|-----------|---------------|
| `testing.md` | 1,240 | 310 |
| `workflow-state.md` | 1,480 | 370 |
| `hooks.md` | 1,880 | 470 |
| `plans.md` | 1,840 | 460 |
| **Rules Total** | **6,440** | **1,610** |

### Grand Total

| Category | Characters | Tokens (est.) |
|----------|-----------|---------------|
| Schemas | 43,085 | 10,773 |
| State Machines | 26,280 | 6,570 |
| Hooks | 76,080 | 19,025 |
| Hook Utilities | 29,500 | 7,375 |
| Rules | 6,440 | 1,610 |
| **TOTAL** | **181,385** | **~45,353** |

---

## 2. Token Flow Analysis: What Gets Injected Into LLM Context?

Not all code is injected into the LLM context. The key question is: **what tokens does the agent actually see?**

### Direct LLM Context Injection Points

| Injection Point | Content | Est. Tokens | Frequency |
|-----------------|---------|-------------|-----------|
| SessionStart `systemMessage` | Workflow state, preferences, continuation | 500-2,000 | Once per session |
| SessionStart `additionalContext` | Pre-compact snapshot, agent identity | 200-800 | Once per session |
| PreToolUse (exit 2 stderr) | Block reason | 20-50 | Per blocked call |
| PostToolUse `additionalContext` | Annotations, context warnings, validation errors | 100-500 | Per tool call |
| SubagentStart `additionalContext` | Parent context, comm log, task, key files, prefs | Up to 3,000 (MAX_CONTEXT_CHARS) | Per sub-agent |
| SubagentStop (exit 2 stderr) | Validation failure reason | 50-200 | Per blocked stop |
| Stop `systemMessage` | Session summary | 200-500 | Once per session |
| PreCompact continuation | Narrative handoff message | 500-1,500 | Per compaction |

### Estimated Per-Session Context Budget

For a typical workflow session:
- 1 SessionStart: ~1,500 tokens
- ~200 tool calls with PostToolUse: ~200 * 200 = ~40,000 tokens (cumulative, but injected per-turn)
- ~5 sub-agent spawns: ~5 * 2,000 = ~10,000 tokens
- ~2 compactions: ~2 * 1,000 = ~2,000 tokens
- 1 Stop: ~400 tokens

**Total context injected over a session: ~54,000 tokens** (spread across conversation turns)

The per-turn injection is typically 200-500 tokens (PostToolUse additionalContext), spiking to 2,000-3,000 during SubagentStart.

---

## 3. Optimization Opportunities

### 3.1 Compact Serialization for LLM Context (30-60% savings)

**Current:** All data serialized as standard verbose JSON with full field names.

**Recommended:** Dual-layer serialization:
- **Validation layer:** Full Pydantic models with verbose field names (for writes and validation)
- **Context layer:** Compact format for LLM injection

**Implementation options:**

**Option A: TOON (Token-Oriented Object Notation)**
```
# Standard JSON (11,842 tokens for 500 rows):
[{"status": "active", "agent_role": "coder", "agent_model": "opus-4-6"}, ...]

# TOON (4,617 tokens — 61% reduction):
status | agent_role | agent_model
active | coder | opus-4-6
...
```
- Best for: array data (test results, file lists, decisions)
- Benchmark: 30-60% token reduction, accuracy improves from 69.7% to 73.9%
- Source: [TOON vs JSON (Tensorlake)](https://www.tensorlake.ai/blog/toon-vs-json)

**Option B: Short Key Aliases with Legend**
```json
// Legend (inject once per session): s=status, r=role, m=model, c=context_usage_pct
// Compact: {"s":"active","r":"coder","m":"opus","c":45}
// Verbose: {"status":"active","agent_role":"coder","agent_model":"opus-4-6","context_usage_pct":45}
```
- Savings: ~20-30% per object
- Best for: structured objects (agent state, session log snapshots)

**Option C: Omit Defaults and Nulls**
```python
class AgentState(BaseModel):
    model_config = ConfigDict(...)

    def to_compact(self) -> dict:
        """Serialize omitting default/null values."""
        return self.model_dump(exclude_defaults=True, exclude_none=True)
```
- Savings: 15-40% depending on how many fields match defaults
- Zero implementation complexity -- Pydantic supports this natively

### 3.2 Progressive Schema Disclosure

**Current:** SubagentStart injects up to 3,000 characters of context regardless of agent type.

**Recommended:** Inject only the schema subset relevant to the current agent's role:
- Coder gets: session log fields it writes, test entry format, file entry format
- Explorer gets: context packet schema, query result format
- Scribe gets: commit message format, session summary fields

**Estimated savings:** 30-50% reduction in SubagentStart context.

### 3.3 Annotation Batching

**Current:** Each PostToolUse call reads the full annotation file and injects all unacknowledged annotations.

**Recommended:**
- Batch annotations: inject at most 3 per turn (oldest first)
- Summarize remaining: "5 more annotations pending"
- Mark as acknowledged after injection

**Estimated savings:** Prevents annotation context from growing unbounded.

### 3.4 Message Bus Log Windowing

**Current:** `subagent_start.py` reads the ENTIRE message bus log (grows unbounded).

**Recommended:** Read only the last N entries or entries from the last T minutes.

**Estimated savings:** Prevents linear growth in SubagentStart context with session duration.

### 3.5 State Machine Compact Representation

**Current:** Full JSON state machine definitions (6,570 tokens total).

**When injected:** The system machine definition is ~1,250 tokens. The coder machine is ~1,400 tokens.

**Recommended compact format:**
```
# coder machine (compact):
SPAWNED → TASK_CLAIMED → WORKTREE_CREATED → CONTEXT_REQUESTED → CONTEXT_LOADED
→ TEST_DESIGN [write: tests/**] → TESTS_WRITTEN → TDD_RED → RED_VERIFIED [max:3]
→ IMPLEMENTATION [write: src/**/*.py] → TDD_GREEN [max:10] → INVARIANT_CHECK
→ QUALITY_GATE → TASK_REVIEW_REQUESTED → FIXES → MERGE [blocked: Write,Edit]
→ MERGE_RESOLVED → TASK_COMPLETE
```
**Estimated savings:** ~70% (from 1,400 to ~420 tokens per machine).

---

## 4. Schema Overhead in Validation Pipeline

### schema_validator.py Import Cost

The validator attempts 21 Pydantic model imports at module load time:
- 8 succeed (current schemas)
- 13 fail silently (unwritten domain schemas)

Each failed import attempt has Python overhead (~1-5ms for ImportError). Total: ~13-65ms wasted on expected failures.

**Fix:** Lazy import only when validation is requested for that specific schema type.

### Double JSON Parse

`validate_json_file()` calls `json.loads(content)` for syntax check, then `model.model_validate_json(content)` for schema validation. The content is parsed from JSON twice.

**Fix:** Use `model.model_validate_json(content)` directly and catch both `json.JSONDecodeError` and `pydantic.ValidationError` separately.

---

## 5. Recommendations Summary

| Optimization | Effort | Token Savings | Priority |
|-------------|--------|---------------|----------|
| Omit defaults/nulls (`exclude_defaults=True`) | 1 hour | 15-40% per object | **P0** |
| Progressive schema disclosure by role | 1 day | 30-50% on SubagentStart | **P1** |
| Message bus log windowing | 2 hours | Prevents unbounded growth | **P1** |
| Annotation batching | 2 hours | Prevents unbounded growth | **P1** |
| Short key aliases for agent state | 1 day | 20-30% per object | **P2** |
| TOON format for array data | 2 days | 30-60% for arrays | **P2** |
| Compact state machine representation | 1 day | ~70% for machine defs | **P2** |
| Lazy schema imports | 2 hours | 13-65ms startup saved | **P3** |
| Remove double JSON parse | 30 min | Negligible tokens, faster validation | **P3** |

---

## Sources

- [TOON vs JSON: Token-Optimized Data Format (Tensorlake)](https://www.tensorlake.ai/blog/toon-vs-json)
- [TOON Benchmarks](https://www.toontools.app/benchmarks)
- [Token-Efficient Data Prep for LLM Workloads (The New Stack)](https://thenewstack.io/a-guide-to-token-efficient-data-prep-for-llm-workloads/)
- [Pydantic Performance Documentation](https://docs.pydantic.dev/latest/concepts/performance/)
- [LLM Optimization with JSON-Based Prompting (ByteDoodle)](https://blog.bytedoodle.com/llm-optimization-with-json-based-prompting-and-token-minimization/)
- [Reduce Token Usage by 60% with TOON (LogRocket)](https://blog.logrocket.com/reduce-tokens-with-toon/)
