# Sub-Agent Return Communication Protocol

**Session:** 2026-03-11
**Status:** Implemented — return schemas, validation script, and SubagentStop hook built
**Depends on:** Confirmed delegation schemas (see CONFIRMED-CHANGES.md)

---

## Problem Statement

We've structured delegation downward (typed JSON schemas for how parents assign work to delegates). The return path was unstructured. When a sub-agent finishes, it returns a single string via the Task tool result. That string contains both the actual information (findings, data, analysis) AND metadata about the work (confidence, decisions, gaps) — but in freeform text that the parent must interpret.

Sub-agents cannot write files to disk. Everything comes back through that one string. This is the only channel. If it's unstructured, the parent loses:

- Confidence levels per finding
- Decisions made during execution (and their rationale)
- Cross-scope findings relevant to peer agents
- Gaps the sub-agent couldn't resolve
- Whether the essential output was fully covered

## What Was Implemented

### Return schemas (per delegation type)

Five return schemas at `schemas/return-{type}.schema.json`. Each defines:
- `delegation_type` (const matching the type)
- `status` (enum: completed, partial, failed)
- Type-specific required fields (see table below)
- Optional metadata fields shared across all types

| Type | Required return fields |
|------|----------------------|
| `targeted` | `result` (object), `decisions_made` (array) |
| `guided` | `unknowns_resolved` (array of {unknown, answer, confidence}), `decisions_made` (array) |
| `exploration` | `findings` (object), `essential_output_confidence` (object with level+reason per target) |
| `research` | `findings` (object), `essential_output_confidence` (object), `citations` (array) |
| `tdd_chunk` | `tests_written` (array), `pass_gate_results` (object), `decisions_made` (array) |

Optional return fields (all types): `unexpected_findings`, `scope_extensions`, `cross_scope_findings`, `gaps`, `carry_forward`.

### Return validation utility

`scripts/validate_return.py` — importable module and CLI script:
1. Parses input as JSON (tries `json.loads`, falls back to brace-matching extraction from mixed text)
2. Checks `delegation_type` field exists and is valid
3. Checks `status` field exists and is valid enum
4. Dispatches to type-specific validation (same pattern as `validate_delegation_prompt.py`)
5. Returns structured result: `{"valid": true/false, "errors": [...], "extracted": {...}}`

### SubagentStop hook

`hooks/subagent_stop_return_validation.py` — validates sub-agent returns at stop time:
1. Reads hook input (stop_hook_active, last_assistant_message)
2. If `stop_hook_active` is true → allows through (prevents infinite retry loops)
3. If return contains `{` → attempts JSON validation via `validate_return.py`
4. If validation fails → blocks with specific field-level errors
5. If validation passes → allows through

### Delegation schema updates

All 5 delegation schemas now include optional fields:
- `return_schema` — the JSON schema the sub-agent's return must match
- `return_instruction` — instruction text telling the sub-agent to output JSON

## Resolved Questions

### 1. How do we make sub-agents actually use the return schema?
**Resolution:** Two layers:
- **Prompt engineering:** `return_schema` + `return_instruction` in the delegation prompt (level 2-3 enforcement)
- **SubagentStop hook:** Validates the return is JSON with required fields. Blocks once if invalid (sub-agent gets one retry)

### 2. What happens on validation failure?
**Resolution:** SubagentStop hook blocks with specific error messages. The `stop_hook_active` guard ensures at most one retry. If retry also fails, the return passes through and the parent handles it.

### 3. Schema size vs compliance tradeoff
**Status:** Schemas kept small (5-7 required fields per type). Empirical testing needed to measure compliance.

### 4. Teammate returns vs sub-agent returns
**Resolution:** These are separate channels. Teammates use SendMessage with existing `task_complete` type — their protocol is handled by the v2 message protocol in `schemas/message_protocol.py`. Sub-agent returns use the return schemas defined here.

### 5. How does aggregation work practically?
**Status:** Future work. Depends on return protocol being stable. The aggregation logic will live in a separate skill (sub-agent-delegation) once the return protocol is empirically validated.

## Remaining Work

1. **Wire hooks into settings.json** — Intentionally deferred until empirical testing
2. **Empirical compliance testing** — Spawn sub-agents with return schemas, measure adherence across models
3. **Aggregation logic** — How teammates synthesize N sub-agent returns into completion messages
4. **Schema size tuning** — Adjust required fields based on compliance data
