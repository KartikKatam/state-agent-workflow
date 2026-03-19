# Infrastructure Audit Report

**Date:** 2026-02-26
**Auditor:** System Audit Agent (Opus 4.6)
**Scope:** All implemented components in `~/personal/agentic_workflow/`

---

## Executive Summary

**Total files audited:** 33 (13 schemas, 7 hooks, 5 hook utils, 8 state machines, 8 scripts, 4 rules)
**Total lines of code:** ~8,600+ (Python/Shell/JSON/Markdown)
**Overall system grade:** **B+** (strong infrastructure, significant gaps in behavioral/data layers)

The infrastructure backbone (hooks, daemon, state machines) is production-quality with good error handling and observability hooks. The schemas are well-structured but lack validation depth. Critical issues: shell injection vulnerability, guards not enforced at runtime, race conditions in all read-modify-write operations.

---

## File-by-File Quality Ratings

### Schemas (`schemas/`)

| File | Lines | Tokens | Grade | Critical Issues |
|------|-------|--------|-------|-----------------|
| `__init__.py` | 10 | ~89 | **D** | Exports nothing. Dead weight. No `__all__`, no re-exports. |
| `agent_state.py` | 64 | ~533 | **B+** | Agent ID regex duplicated in 4 files. No `model_config`. |
| `system_state.py` | 76 | ~575 | **B** | No tracing fields (inconsistent with other models). `SystemState` has 18 ambiguous Literal values. |
| `preferences.py` | 44 | ~350 | **C+** | `Preference.value` typed as `Any` -- no validation. `global` alias is a Python keyword. |
| `state_machine.py` | 103 | ~875 | **B-** | No cross-validation: `from_state`/`to_state` in transitions are unchecked strings. Needs `model_validator`. |
| `annotation.py` | 72 | ~663 | **B** | Leading whitespace on line 28. Inconsistent default patterns. |
| `message_protocol.py` | 274 | ~2,450 | **A-** | Sophisticated discriminated union. Function-based discriminator is slower than field-based. `HandoffPayload` duplicates fields from `handoff.py`. |
| `session_log.py` | 241 | ~2,050 | **B** | 27 fields (too large). Absolute import inconsistency. `LearningSignal.id` limited to 999 max. Forward reference ordering. |
| `handoff.py` | 155 | ~1,375 | **B** | `Handoff.reason` has `scrap_and_retry` not in `HandoffPayload.reason` -- **enum drift**. `key_files_read` type mismatch with `session_log.py`. |
| `hook_output.py` | 68 | ~625 | **B-** | Only camelCase alias in entire codebase. `dict[str, object]` inconsistent with `dict[str, Any]` everywhere else. |
| `state_transition_log.py` | 46 | ~413 | **B+** | Uses absolute import. Uses `...` (Ellipsis) inconsistently. |
| `decision_log.py` | 39 | ~350 | **B** | No pattern validation on `agent_id` (unlike other files). No length constraints on text fields. |
| `message_bus_log.py` | 52 | ~425 | **B** | `MessageType` Literal duplicates types from `message_protocol.py`. Summary `max_length=200` vs 500 elsewhere. |

**Schema-wide issues:**
- **Zero `model_config` anywhere.** No `extra="forbid"` means typo keys silently pass validation.
- **Zero custom validators.** Not a single `@field_validator` or `@model_validator` across 13 files.
- **Import style split.** 4 files use absolute imports (`from schemas.agent_state`), 2 use relative (`from .agent_state`).
- **Agent ID regex pattern duplicated in 4 files.** Should be a shared constant.
- **13 domain schemas referenced but not yet written** (context packets, plans, research, etc.).

### Hooks (`hooks/`)

| File | Lines | Tokens | Grade | Critical Issues |
|------|-------|--------|-------|-----------------|
| `pre_tool_use.py` | 259 | ~1,650 | **A-** | Self-contained (no internal imports). 3-tier enforcement. Fork bomb regex too rigid. `generalist` role bypasses Tier 2. |
| `post_tool_use.py` | 701 | ~4,450 | **B** | Dispatch table architecture is clean. ~50 bare `except Exception: pass` blocks. Race conditions in annotation/tracking file handling. Ruff subprocess on every Python write. |
| `session_start.py` | 467 | ~2,975 | **B** | **CRITICAL: Shell injection in `_write_env_vars()`.** Unsanitized `agent_id` interpolated into shell file. Fix: `shlex.quote()`. |
| `subagent_start.py` | 483 | ~3,025 | **B** | Reads entire message bus log (grows unbounded). UTF-8 truncation unsafe. No top-level exception handler. |
| `subagent_stop.py` | 368 | ~2,350 | **B-** | Keyword-based validation is extremely brittle. `MIN_OUTPUT_LENGTH = 50` is arbitrary. `emit_agent_terminated` called even when blocking (misleading semantics). |
| `stop.py` | 299 | ~1,900 | **B+** | Orchestrator-scoped. Summary output is plain text, not JSON -- may conflict with hook contract. Race condition in workflow timestamp update. |
| `pre_compact.py` | 423 | ~2,675 | **B** | Session log match on `agent_id OR status == "in_progress"` could return wrong agent's log. Snapshot overwrites without backup. |

**Hook-wide issues:**
- **~50 bare `except Exception: pass` blocks** across all hooks. Zero diagnostic visibility. Bugs hide indefinitely.
- **`_read_stdin()` copy-pasted in 5 files.** Should be extracted to `state_helpers.py`.
- **Race conditions** in all read-modify-write operations (annotations, tracking, agent state). No file locking.
- **Shell injection vulnerability** in `session_start.py:_write_env_vars()` -- highest severity bug found.

### Hook Utilities (`hooks/utils/`)

| File | Lines | Tokens | Grade | Critical Issues |
|------|-------|--------|-------|-----------------|
| `__init__.py` | 81 | ~515 | **C+** | Imports all 4 modules at load time. Exports `trace_context` which is never used by any hook. |
| `state_helpers.py` | 107 | ~680 | **B+** | Leaf module, no internal deps. `read_json_safe()` rejects non-dict JSON (breaks array files). |
| `context_monitor.py` | 164 | ~1,035 | **B+** | Mixed percentage scale (0-100 input, 0-1 thresholds). Brand-new agents skip fallback to metrics. |
| `schema_validator.py` | 355 | ~2,260 | **B** | 21 try/except import blocks at load time. JSON parsed twice (syntax check + Pydantic). 13 of 21 schema imports fail (expected -- schemas not yet written). |
| `trace_context.py` | 264 | ~1,660 | **B+** | W3C traceparent interop. 4 propagation channels. **Completely unused by any hook file** -- dead code from hooks' perspective. |
| `event_logger.py` | 195 | ~1,225 | **B+** | Fire-and-forget logging. File open/close per event (no buffering). `os.path.relpath()` assumes CWD == project dir. |

### State Machines (`state-machines/`)

| File | Lines | Tokens | Grade | Critical Issues |
|------|-------|--------|-------|-----------------|
| `system.json` | 237 | ~1,250 | **A** | 19 states, 21 transitions. No error recovery from EXPLORING/STRATEGIZING. No PAUSED/ERROR state. PLAN_TEXT_REVIEW has no rejection path. |
| `coder.json` | 264 | ~1,400 | **A** | TDD enforcement via state transitions. `**/*.py` write glob too broad in IMPLEMENTATION. INVARIANT_CHECK loop has no max_occurrences. |
| `tester.json` | 125 | ~650 | **B+** | No failure path from SCENARIO_BUILDING. |
| `auditor-task.json` | 81 | ~430 | **A-** | max_occurrences=3 on review loop. Gets stuck after exhausting max -- no escalation transition. |
| `auditor-phase.json` | 75 | ~400 | **B+** | Strictly linear -- no iteration. Real review processes need loops. |
| `explorer.json` | 118 | ~620 | **A-** | Validation loop with max 2 retries. No sub-agent failure transition. |
| `researcher.json` | 135 | ~720 | **A-** | EXISTING_RESEARCH_CHECK shortcircuit is good. Minor semantic mismatch on SYNTHESIS state. |
| `strategist.json` | 210 | ~1,100 | **A** | Most sophisticated machine. 4 user review checkpoints. Major rework capped at 1 occurrence (may need 2). |

**State machine-wide issues:**
- **Guards are purely declarative.** The daemon (`workflow_state.py`) never evaluates guard conditions -- only checks from/to/trigger match. 60+ guard conditions are documentation, not enforcement.
- **No ERROR/FAILED states** in any machine. Unrecoverable failures leave agents stuck with no valid exit transition.
- **No timeout transitions.** Agents waiting for external input (context, sub-agent results) have no fallback.

### Scripts (`scripts/`)

| File | Lines | Tokens | Grade | Critical Issues |
|------|-------|--------|-------|-----------------|
| `workflow_state.py` | 807 | ~5,400 | **A-** | Central daemon. Non-atomic file writes (race condition). Guards not enforced. `count_transition_occurrences` is O(n) over JSONL. Socket in `/tmp/` (security). No request size limit. |
| `gate.sh` | 250 | ~1,600 | **A-** | Blocking + informational checks. No per-check timeout. No parallel mode. |
| `annotate.sh` | 147 | ~900 | **B+** | Shell injection risk: unsanitized AGENT variable interpolated into Python code. |
| `allocate-ports.sh` | 294 | ~1,700 | **B** | No file locking -- race condition on parallel allocate. Depends on `ss` (Linux-only). |
| `merge-worktree.sh` | 268 | ~1,600 | **A-** | Backup refs before merge. No `trap` handler for cleanup on unexpected failure. |
| `convert_design.py` | 620 | ~4,300 | **B+** | Solid parser. Only matches first section per canonical name. Single-quote XML attributes not matched. |
| `validate_merge_readiness.py` | 232 | ~1,500 | **B+** | Dry-run merge modifies working tree (not truly side-effect-free). Should use `git merge-tree`. |
| `generate_phase_report.py` | 747 | ~5,000 | **B** | Phase identification is fragile. Placeholder collectors for unbuilt features. Duplicate time-window filtering. |

### Rules (`rules/`)

| File | Lines | Tokens | Grade | Critical Issues |
|------|-------|--------|-------|-----------------|
| `testing.md` | 47 | ~310 | **B+** | Glob scope too narrow (only `tests/**/*.py`). No coverage thresholds, mocking strategy, or parallelism rules. |
| `workflow-state.md` | 52 | ~370 | **B+** | Does not document that guards are not enforced. Claims trace_id logging that doesn't exist yet. |
| `hooks.md` | 64 | ~470 | **A-** | Most comprehensive. No idempotency, ordering, or CI testing rules. |
| `plans.md` | 63 | ~460 | **B+** | References `validate_plan_conversion.py` and `validate_function_overlap.py` which don't exist. |

**Missing rules:** `agents.md`, `context-packets.md`, `security.md`, `merge-strategy.md`, `scripts.md`.

---

## Cross-File Consistency Analysis

### Import Dependency Graph

```
schemas/agent_state.py (LEAF - no local imports)
    ^
    |--- preferences.py       (relative: from .agent_state)     OK
    |--- state_machine.py     (relative: from .agent_state)     OK
    |--- session_log.py       (ABSOLUTE: from schemas.agent_state) BUG RISK
    |--- handoff.py           (ABSOLUTE: from schemas.agent_state) BUG RISK
    |--- state_transition_log.py (ABSOLUTE)                        BUG RISK
    |--- decision_log.py      (ABSOLUTE)                           BUG RISK

hooks/pre_tool_use.py (SELF-CONTAINED - stdlib only)

hooks/post_tool_use.py
    |--- hooks.utils.context_monitor
    |--- hooks.utils.event_logger
    |--- hooks.utils.schema_validator
    |--- hooks.utils.state_helpers
    |--- schemas.decision_log (conditional)
    |--- schemas.message_bus_log (conditional)

hooks/session_start.py
    |--- hooks.utils.event_logger
    |--- hooks.utils.state_helpers
    |--- schemas.agent_state (conditional)
    |--- schemas.preferences (conditional)

hooks/utils/ dependency chain:
    state_helpers.py  (LEAF - stdlib only)
    event_logger.py   (LEAF - stdlib only)
    context_monitor.py -> state_helpers
    schema_validator.py -> schemas.* (21 conditional imports)
    trace_context.py  (LEAF - stdlib only, UNUSED by hooks)
```

### Duplicated Concepts

| Concept | Locations | Inconsistency |
|---------|-----------|---------------|
| Agent ID regex | 4 schema files, hardcoded | Should be shared constant |
| `_read_stdin()` | 5 hook files, copy-pasted | Should be in `state_helpers.py` |
| Decision model | `session_log.Decision`, `handoff.HandoffDecision` | Different fields, same concept |
| Files modified | `session_log.FileEntry` (structured), `handoff.files_modified` (`list[str]`) | Type mismatch |
| Key files read | `session_log.KeyFileEntry` (structured), `handoff.key_files_read` (`list[str]`) | Type mismatch |
| Message types | `message_protocol.py` (10 wrappers), `message_bus_log.MessageType` (Literal) | Separate definitions, can drift |
| Handoff reason | `HandoffPayload.reason` (4 values), `Handoff.reason` (5 values) | **Enum drift** |
| Summary max_length | `message_protocol.py` (500), `message_bus_log.py` (200) | Different limits |
| State file read-modify-write | `post_tool_use.py`, `subagent_stop.py`, `stop.py`, `pre_compact.py` | No file locking anywhere |

---

## Bug Inventory

### Severity: CRITICAL

1. **Shell injection in `session_start.py:_write_env_vars()`** (lines 420-422)
   - Unsanitized `agent_id` from `CLAUDE_CODE_AGENT_NAME` env var interpolated into shell file
   - Exploit: `CLAUDE_CODE_AGENT_NAME="test'; rm -rf /; echo '"` executes arbitrary commands
   - Fix: `shlex.quote()` on all values written to shell files

### Severity: HIGH

2. **Guards never enforced at runtime** (`workflow_state.py`)
   - 60+ guard conditions across 8 state machines are purely decorative
   - `do_transition()` only checks from_state/to_state/trigger -- never evaluates guards
   - Impact: Premature transitions allowed (e.g., CONTEXT_READY before context exists)

3. **Non-atomic file writes** (`workflow_state.py`)
   - `Path.write_text()` for agent/system state files
   - Under parallel agents, two concurrent writes corrupt JSON
   - Fix: write to temp file + `os.rename()` (atomic on same filesystem)

4. **Race conditions in all read-modify-write operations**
   - Annotation file handling (`post_tool_use.py` lines 416-448)
   - Agent write tracking (`post_tool_use.py` lines 293-303)
   - Agent state updates (`post_tool_use.py` lines 349-365)
   - Port allocation (`allocate-ports.sh`)
   - Fix: `fcntl.flock()` on Linux

### Severity: MEDIUM

5. **Enum drift** between `HandoffPayload.reason` and `Handoff.reason`
6. **Path traversal risk** in `pre_tool_use.py` socket path (unsanitized `WORKFLOW_ID`)
7. **`generalist` role bypasses all Tier 2 safety checks** silently
8. **Keyword-based SubagentStop validation** is trivially bypassable
9. **No ERROR states** in any state machine
10. **No `extra="forbid"`** on any Pydantic model
11. **`__init__.py` exports nothing** -- package has no public API
12. **Dead code:** `trace_context.py` imported but never used by hooks

### Severity: LOW

13. Leading whitespace on `annotation.py` line 28
14. `_parse_decision_output` is verbose and duplicates flush logic
15. `read_json_safe()` rejects non-dict JSON (breaks array files)
16. Fork bomb regex is too rigid (only matches exact classic form)
17. `_find_active_session_log` mtime sort with 50-file cap may miss newer logs
