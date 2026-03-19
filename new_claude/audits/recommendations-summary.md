# Recommendations Summary

**Date:** 2026-02-26
**Auditor:** System Audit Agent (Opus 4.6)

---

## Prioritized Recommendations

### P0 — Fix NOW (Before Any New Feature Work)

| # | Recommendation | Files | Effort | Impact |
|---|---------------|-------|--------|--------|
| 1 | **Fix shell injection in `_write_env_vars()`** -- use `shlex.quote()` on `agent_id` and `role` values interpolated into shell file | `hooks/session_start.py` lines 420-422 | 30 min | Security critical. CVE-class vulnerability. |
| 2 | **Add `model_config = ConfigDict(extra="forbid")` to ALL Pydantic models** -- prevents silent data corruption from typo keys | All 13 `schemas/*.py` files | 1 hour | Highest-impact schema fix. Catches bugs at write time. |
| 3 | **Atomic file writes in daemon** -- use write-to-temp + `os.rename()` for agent state and system state files | `scripts/workflow_state.py` | 1 hour | Prevents JSON corruption under parallel agent execution. |
| 4 | **Sanitize `WORKFLOW_ID` in socket path** -- validate env var contains only `[a-zA-Z0-9_-]` before constructing path | `hooks/pre_tool_use.py` line 123 | 15 min | Prevents path traversal via env var manipulation. |
| 5 | **Normalize import style** -- change 4 files from `from schemas.agent_state` (absolute) to `from .agent_state` (relative) | `session_log.py`, `handoff.py`, `state_transition_log.py`, `decision_log.py` | 30 min | Prevents breakage during package restructuring. |
| 6 | **Reconcile HandoffPayload.reason and Handoff.reason** -- add `scrap_and_retry` to `HandoffPayload` or remove from `Handoff` | `schemas/message_protocol.py`, `schemas/handoff.py` | 15 min | Enum drift causes silent validation mismatch. |

**Total P0 effort: ~4 hours**

---

### P1 — Fix Next Sprint (Within 1-2 Weeks)

| # | Recommendation | Files | Effort | Impact |
|---|---------------|-------|--------|--------|
| 7 | **Implement guard evaluation in daemon** -- add `_GUARD_REGISTRY` dict mapping guard names to evaluation functions, call during `do_transition()` | `scripts/workflow_state.py` | 3-5 days | The single biggest architectural gap. 60+ guards are currently decorative. |
| 8 | **Add ERROR states to state machines** -- at minimum add ERROR state with recovery transitions to system, coder, and explorer machines | `state-machines/{system,coder,explorer}.json` | 1 day | Prevents agents from getting stuck with no valid exit. |
| 9 | **Extract `_read_stdin()` to shared utility** -- eliminate 5-way copy-paste | `hooks/utils/state_helpers.py` + 5 hook files | 2 hours | DRY. One function to maintain. |
| 10 | **Add hook error logging** -- write failures to `~/.claude/logs/hook-errors.jsonl` with `except Exception: pass` wrapping | New: `hooks/utils/error_logger.py`, modify all hooks | 1 day | Zero diagnostic visibility currently. Bugs hide indefinitely. |
| 11 | **Add file locking for critical read-modify-write operations** -- use `fcntl.flock()` on annotation files, agent state updates, tracking files | `hooks/post_tool_use.py`, `hooks/subagent_stop.py`, `hooks/stop.py` | 1 day | Race conditions under parallel agent execution. |
| 12 | **Create `.claude/settings.json`** with hook wiring -- hooks exist but are not connected to Claude Code | New file: `.claude/settings.json` | 2 hours | **Hooks don't fire without this.** |
| 13 | **Add missing rules files** -- `agents.md`, `context-packets.md`, `security.md` | `rules/` | 1 day | 5 missing rule domains. |
| 14 | **Populate `schemas/__init__.py`** with re-exports and `__all__` | `schemas/__init__.py` | 30 min | Package has no public API surface. |
| 15 | **Extract shared constants** -- `AGENT_ID_PATTERN`, message type literals | New: `schemas/_constants.py` | 1 hour | Agent ID regex duplicated in 4 files. |

**Total P1 effort: ~2 weeks**

---

### P2 — Fix Next Month (Within 4-6 Weeks)

| # | Recommendation | Files | Effort | Impact |
|---|---------------|-------|--------|--------|
| 16 | **Write 13 missing domain schemas** -- context packets, plans, research, quality gate, test plan, scenario result, audit report, phase report, design document, research index | `schemas/` | 2-3 weeks | Entire data pipeline is currently untyped. The schema_validator is wired for them. |
| 17 | **Add cross-field validators** -- `@model_validator` on `StateMachineDefinition` (state references), `@field_validator` on `TestSummary` (passed+failed+skipped <= total) | `schemas/state_machine.py`, `schemas/session_log.py` | 2 days | Prevents structurally invalid data from loading. |
| 18 | **Write agent prompt files** -- starting with orchestrator, coder, explorer | `agents/` | 2-3 weeks | The entire behavioral layer is absent. System cannot orchestrate without prompts. |
| 19 | **Convert PostToolUse event logging to async** -- move non-critical logging, tracking, and event emission to async hooks | `hooks/post_tool_use.py`, `.claude/settings.json` | 2 days | Reported 3x faster workflows with async non-blocking operations. |
| 20 | **Token optimization: `exclude_defaults=True`** -- add `to_compact()` method on models used in LLM context injection | `schemas/*.py`, hooks that inject context | 1 day | 15-40% token reduction per injected object. |
| 21 | **Create `init-workflow.sh`** -- onboarding script for new projects | `scripts/init-workflow.sh` | 2 days | System cannot be deployed to new projects without manual setup. |
| 22 | **Add message bus log windowing** -- read only last N entries in `subagent_start.py` | `hooks/subagent_start.py` | 2 hours | Prevents unbounded context growth with session duration. |
| 23 | **Add schema versioning** -- `schemaVersion: "1-0-0"` field on all inter-agent payloads | `schemas/*.py` | 1 day | Enables schema evolution without breaking existing data. |
| 24 | **Write core skills** -- `session-lifecycle`, `tdd-workflow`, `workflow-orchestration` | `skills/` | 2-3 weeks | Agents need specialized workflow knowledge. |

**Total P2 effort: ~6-8 weeks**

---

### P3 — Fix Next Quarter (Within 3 Months)

| # | Recommendation | Files | Effort | Impact |
|---|---------------|-------|--------|--------|
| 25 | **Rust rewrite: pre_tool_use** -- single binary, ~50x startup improvement | New Rust workspace | 1-2 weeks | Saves ~10s per 200-tool-call session. |
| 26 | **Rust rewrite: workflow_state daemon** -- Tokio async, 8-10x memory reduction | New Rust binary | 2-3 weeks | Eliminates GIL contention, reduces memory from ~100MB to ~10MB. |
| 27 | **Rust rewrite: post_tool_use** -- bundled schema validation + event logging | New Rust binary | 2-3 weeks | Eliminates Pydantic import overhead. |
| 28 | **State machine testing** -- property-based tests generating random event sequences | New: `tests/test_state_machines.py` | 1 week | State machines currently untested. |
| 29 | **Upgrade to statechart features for system machine** -- parallel regions for concurrent execution, history states for recovery | `state-machines/system.json`, daemon | 2 weeks | System machine has 19 states -- approaching flat FSM complexity threshold. |
| 30 | **TUI viewers** -- message viewer + state transition viewer | New Python Textual app | 2-3 weeks | No real-time observability during execution. |
| 31 | **SQLite aggregation layer** -- periodic JSONL ingestion for cross-session queries | New script + schema | 1 week | Currently no way to query across sessions. |
| 32 | **PTC server** -- ipybox-based sandbox for sub-agent delegation | New MCP server | 3-4 weeks | Design promises 37-85% token savings for sub-agent work. |
| 33 | **TOON/compact format for array data** -- context packets, test results, file lists | Context injection points | 2 days | 30-60% token reduction for array-heavy data. |
| 34 | **Remaining agent prompts + skills** -- tester, auditor, scribe, strategist | `agents/`, `skills/` | 3-4 weeks | Completes the behavioral layer. |

**Total P3 effort: ~3 months**

---

## Quick Reference: What's Broken vs What's Missing

### Broken (Bugs/Security Issues)
1. Shell injection in `_write_env_vars()` [P0]
2. Non-atomic file writes in daemon [P0]
3. Enum drift between `HandoffPayload.reason` and `Handoff.reason` [P0]
4. Race conditions in all read-modify-write ops [P1]
5. Guards declared but never enforced [P1]
6. No `extra="forbid"` -- typo keys silently pass [P0]

### Missing (Not Yet Built)
1. `.claude/settings.json` hook wiring [P1]
2. 13 domain schemas [P2]
3. 8 agent prompt files [P2]
4. ~15 skills [P2-P3]
5. ERROR states in state machines [P1]
6. Guard evaluation engine [P1]
7. Hook error logging [P1]
8. 5 validation scripts [P3]
9. TUI viewers [P3]
10. PTC server [P3]

### Working Well (Keep As-Is)
1. 3-tier enforcement model in PreToolUse
2. Socket daemon architecture (low latency, clean protocol)
3. State machine definitions (well-designed, comprehensive)
4. W3C Trace Context integration
5. Event logger pattern (fire-and-forget JSONL)
6. Dispatch table in PostToolUse
7. Permissive fallback design
8. PTC future-proofing stubs
9. Write-gating and tool-blocking by state

---

## Implementation Order Recommendation

```
Week 1:    P0 fixes (4 hours) + start P1 guard enforcement
Week 2:    Finish guard enforcement + settings.json + error logging
Week 3-4:  Domain schemas (first 5 most critical)
Week 5-6:  Agent prompts (orchestrator + coder + explorer)
Week 7-8:  Core skills + remaining schemas
Week 9-10: Rust pre_tool_use rewrite
Week 11-13: Rust daemon rewrite + post_tool_use rewrite
```

**Key principle:** Fix security bugs and data integrity issues (P0) immediately. Build the enforcement layer (P1) before adding the behavioral layer (P2). Don't optimize (P3) until the system can actually run a workflow.
