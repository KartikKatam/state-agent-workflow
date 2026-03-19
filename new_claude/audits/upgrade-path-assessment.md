# Upgrade Path Assessment

**Date:** 2026-02-26
**Auditor:** System Audit Agent (Opus 4.6)

---

## Executive Summary

The system has **low coupling** between major components (hooks, daemon, schemas, state machines) thanks to JSON-over-socket and file-based communication. This makes it highly upgradeable. The primary upgrade paths are: (1) fix critical bugs now, (2) add guard enforcement to the daemon, (3) implement the behavioral layer (agent prompts + skills), (4) consider Rust rewrite for hot-path hooks. Extension points are well-designed -- the schema registry, state machine definitions, and hook dispatch table all support addition without modification.

---

## 1. Coupling Analysis

### Communication Patterns

```
┌─────────────┐     Unix Socket (JSON)     ┌─────────────────┐
│ pre_tool_use │ ──────────────────────────> │ workflow_state   │
│   (hook)     │ <────────────────────────── │   (daemon)       │
└─────────────┘                             └─────────────────┘
                                                    │
                                                    │ reads
                                                    v
                                            ┌─────────────────┐
                                            │ state-machines/  │
                                            │   *.json         │
                                            └─────────────────┘

┌─────────────┐     File I/O (JSON)         ┌─────────────────┐
│ post_tool_use│ ──────────────────────────> │ agent state/     │
│   (hook)     │ <────────────────────────── │ logs/annotations │
└─────────────┘                             └─────────────────┘

┌─────────────┐     File I/O (JSONL)        ┌─────────────────┐
│ event_logger │ ──────────────────────────> │ workflow-events  │
│   (util)     │                            │   .jsonl         │
└─────────────┘                             └─────────────────┘

┌─────────────┐     Import (Python)         ┌─────────────────┐
│ schema_      │ ──────────────────────────> │ schemas/         │
│ validator    │                            │   *.py           │
└─────────────┘                             └─────────────────┘
```

### Coupling Matrix

| Component A | Component B | Coupling Type | Strength | Can Replace Independently? |
|-------------|-------------|---------------|----------|---------------------------|
| pre_tool_use | workflow_state daemon | Socket (JSON) | **Loose** | Yes -- any process speaking the socket protocol works |
| post_tool_use | state_helpers | Python import | **Tight** | No -- same process |
| post_tool_use | event_logger | Python import | **Tight** | No -- same process |
| post_tool_use | schema_validator | Python import | **Tight** | No -- same process |
| post_tool_use | context_monitor | Python import | **Tight** | No -- same process |
| schema_validator | schemas/*.py | Python import | **Tight** | No -- same process, but graceful degradation on failure |
| workflow_state daemon | state-machines/*.json | File read | **Loose** | Yes -- swap JSON files |
| hooks (all) | agent state files | File I/O | **Loose** | Yes -- any file format |
| hooks (all) | CLAUDE_* env vars | Environment | **Loose** | Yes -- any env var provider |
| session_start | CLAUDE_ENV_FILE | File write | **Loose** | Yes -- shell-sourced env file |

### Key Insight: The Socket Protocol is the Best Decoupling Boundary

The `pre_tool_use.py` <-> `workflow_state.py` communication is the cleanest interface:
- Protocol: newline-delimited JSON over Unix socket
- Contract: `{"command": "check_tool", "agent_id": ..., "tool": ..., "tool_input": ...}` -> `{"allowed": bool, "reason": str}`
- Either side can be rewritten independently (Python -> Rust, etc.)
- The daemon is stateful; hooks are stateless

This means **the daemon is the #1 candidate for an independent Rust rewrite** -- the hooks don't care what process is on the other end of the socket.

---

## 2. Extension Points

### 2.1 Schema Registry (Open for Extension)

`schema_validator.py` uses a pattern-matching registry:
```python
_SCHEMA_REGISTRY = {
    "agent_state": AgentState,
    "workflow_state": WorkflowState,
    ...
}
_SUFFIX_MATCHES = [
    (".claude/state/agents/", ".json", "agent_state"),
    ...
]
```

**To add a new schema:**
1. Create `schemas/new_schema.py` with Pydantic model
2. Add import to `_SCHEMA_REGISTRY`
3. Add path pattern to `_EXACT_MATCHES` or `_SUFFIX_MATCHES`

**Assessment:** Well-designed. The 13 missing domain schemas can be added without modifying existing code. The conditional import pattern means missing schemas degrade gracefully.

### 2.2 State Machine Definitions (Open for Extension)

State machines are JSON files loaded by the daemon at startup:
```
state-machines/{role}.json
```

**To add a new agent type:**
1. Create `state-machines/new-agent.json` following the schema in `schemas/state_machine.py`
2. The daemon auto-discovers and loads it
3. Register agents with the new role via socket protocol

**Assessment:** Fully extensible. No code changes needed to add a new agent type.

### 2.3 Hook Dispatch Table (Open for Extension)

`post_tool_use.py` uses a handler registry:
```python
_TOOL_HANDLERS = {
    "Think": [handle_think],
    "SendMessage": [handle_send_message],
    "Write": [handle_write_validate, handle_write_lint, handle_write_track, handle_write_event],
    "Edit": [handle_write_validate, handle_write_lint, handle_write_track, handle_write_event],
    ...
}
_ALWAYS_HANDLERS = [handle_update_context, handle_context_pressure, handle_annotations]
_HUMAN_HANDLERS = [handle_user_correction]
```

**To add a new handler:**
1. Define handler function receiving `HookContext`
2. Add to appropriate dict/list

**Assessment:** Clean dispatch table pattern. Easy to extend. Consider making this configurable (load handlers from a config file) for the eventual Rust rewrite.

### 2.4 Event Logger (Open for Extension)

`event_logger.py` has typed emitters:
```python
def emit_file_written(agent_id, file_path, ...): ...
def emit_skill_loaded(agent_id, skill_name, ...): ...
```

**To add a new event type:**
1. Add a new `emit_*` function
2. Call it from the appropriate hook handler

**Assessment:** Simple, extensible. Consider switching to a generic `emit(event_type, **data)` pattern to reduce boilerplate.

### 2.5 Rules (Open for Extension)

Rules are Markdown files with glob-targeted scope:
```
rules/{topic}.md  # Glob-targeted via Claude Code's rules system
```

**To add a new rule domain:**
1. Create `rules/new-topic.md` with appropriate glob patterns

**Assessment:** Trivially extensible. The 5 missing rules (agents, context-packets, security, merge-strategy, scripts) can be added immediately.

---

## 3. Upgrade Recommendations

### Phase 0: Critical Bug Fixes (Do Now)

| Fix | Files | Effort | Impact |
|-----|-------|--------|--------|
| Shell injection in `_write_env_vars()` | `session_start.py` | 30 min | Security critical |
| Non-atomic file writes in daemon | `workflow_state.py` | 1 hour | Data integrity |
| Add `model_config = ConfigDict(extra="forbid")` to all schemas | `schemas/*.py` | 1 hour | Validation safety |
| Normalize import style (pick relative) | 4 schema files | 30 min | Consistency |
| Extract `_read_stdin()` to `state_helpers.py` | 5 hook files | 1 hour | DRY |
| Sanitize `WORKFLOW_ID` in socket path | `pre_tool_use.py` | 15 min | Security |

### Phase 1: Guard Enforcement (1-2 weeks)

The guards-not-enforced issue is the biggest architectural gap. Three approaches:

**Option A: Daemon-side guard evaluation (Recommended)**
- Add guard evaluator to `workflow_state.py:do_transition()`
- Guards check conditions by querying state files (e.g., `context_packets_exist` -> check if `.claude/context/_codebase.json` exists)
- Guard functions registered in a `_GUARD_REGISTRY` dict
- New guards added by implementing a function and registering it

**Option B: Hook-side guard evaluation**
- PreToolUse hook evaluates guards before requesting transition
- Pro: no daemon changes. Con: duplicates logic across hooks.

**Option C: Deferred -- document that guards are advisory**
- Update `workflow-state.md` rules to explicitly state guards are documentation
- Lowest effort but leaves the safety gap

### Phase 2: Domain Schemas (2-3 weeks)

Write the 13 missing domain schemas. Priority order:
1. `context_packet.py` (explorer output) -- blocks context generation
2. `implementation_plan.py` (plan structure) -- blocks planning phase
3. `research_output.py` (researcher output) -- blocks research
4. `quality_gate_result.py` (gate output) -- referenced by existing code
5. Remaining 9 schemas

### Phase 3: Behavioral Layer (3-4 weeks)

Write agent prompts and skills:
1. `agents/orchestrator.md` -- the main behavioral spec
2. Core skills: `session-lifecycle`, `tdd-workflow`, `workflow-orchestration`
3. Remaining agent prompts: coder, explorer, researcher, strategist, tester, auditor, scribe
4. Remaining skills

### Phase 4: Settings & Wiring (1 week)

1. Create `.claude/settings.json` with hook wiring
2. Create `scripts/init-workflow.sh` for project onboarding
3. Wire `PermissionRequest` hooks for auto-approve patterns
4. Configure async hooks for non-blocking operations

### Phase 5: Observability Tooling (2-3 weeks)

1. Hook error logger (JSONL-based diagnostic logging)
2. File locking for read-modify-write operations (`fcntl.flock`)
3. State machine testing (property-based)
4. SQLite aggregation layer for cross-session queries

### Phase 6: Rust Rewrite (4-6 weeks, see `rust-rewrite-analysis.md`)

1. Rust `pre_tool_use` binary (50x startup improvement)
2. Rust `workflow_state` daemon (8-10x memory reduction)
3. Rust `post_tool_use` binary (schema validation + event logging)

---

## 4. Upgrade Risk Assessment

### Low Risk Upgrades (Safe to do anytime)

- Add `model_config` to schemas (backward compatible)
- Add missing rules files (additive)
- Extract `_read_stdin()` (refactor, no behavior change)
- Add error logging (additive)
- Write domain schemas (additive)

### Medium Risk Upgrades (Test carefully)

- Guard enforcement in daemon (changes transition behavior)
- Import style normalization (ensure all consumers work)
- State machine ERROR state additions (changes machine topology)
- Async hook conversion (changes timing behavior)

### High Risk Upgrades (Require rollback plan)

- Rust rewrite of daemon (must match exact socket protocol)
- Rust rewrite of hooks (must match exact JSON output contract)
- Schema versioning addition (affects all consumers)
- Statechart migration (changes machine definition format)

---

## 5. Architecture Evolution Path

### Current: File + Socket Architecture (v1)

```
Hooks (Python) -> Socket -> Daemon (Python) -> JSON Files
```

### Near-term: Guard-Enforced Architecture (v1.5)

```
Hooks (Python) -> Socket -> Daemon (Python, guards active) -> JSON Files
                                                             -> Error JSONL
```

### Mid-term: Rust Hot-Path Architecture (v2)

```
Hooks (Rust) -> Socket -> Daemon (Rust/Tokio) -> JSON Files
                                                -> Error JSONL
                                                -> SQLite (aggregation)
```

### Long-term: Full Observability Architecture (v3)

```
Hooks (Rust) -> Socket -> Daemon (Rust/Tokio) -> JSON Files
                       -> OpenTelemetry        -> Grafana/Jaeger
                       -> SQLite               -> TUI Viewers
                       -> JSONL                -> Streaming Analysis
```

---

## 6. What to Fix NOW vs Later

### NOW (Before Next Feature)
1. Shell injection fix
2. Non-atomic file writes
3. `model_config = ConfigDict(extra="forbid")` on all models
4. Import normalization
5. `_read_stdin()` extraction

### NEXT SPRINT
1. Guard enforcement (Option A)
2. Missing rules files
3. Error logging infrastructure
4. File locking for critical paths
5. `settings.json` hook wiring

### NEXT MONTH
1. Domain schemas (13 files)
2. Agent prompts (8 files)
3. Core skills (6 skills)
4. `init-workflow.sh`

### NEXT QUARTER
1. Rust rewrite of hot-path hooks
2. Rust rewrite of daemon
3. State machine testing
4. TUI viewers
5. PTC server
