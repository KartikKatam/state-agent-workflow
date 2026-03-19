# V2 Sub-Agent Architecture Restructuring — Implementation Plan

## Purpose

This document is the ground truth for restructuring the agentic workflow from an all-teammate architecture to a director-teammate + ephemeral-sub-agent architecture. It covers every decision made during the analysis sessions and provides specific implementation instructions for each component.

## Source Context

- Analysis sessions: March 2026
- Codebase: `/home/kartik/personal/agentic_workflow/`
- Skills location: `new_claude/skills/` (NOT `.claude/skills/`)
- State machines: `state-machines/`
- Daemon: `scripts/daemon/`
- Hooks: `hooks/`
- Schemas: `schemas/`
- PTC MCP: `~/.claude/mcp/ptc-server/` (SSE transport, port 8741)
- Think MCP: `scripts/mcp_think.py` (custom, 16 decision values, daemon-integrated)

---

## 1. Final Architecture

### Role Assignments

| Role | Type | Model | PTC | Persistence | Spawns Sub-Agents? |
|------|------|-------|-----|-------------|---------------------|
| Orchestrator | Main session | Opus | No | /clear + handoff between phases | Yes (sole spawner) |
| Explorer | Teammate | Sonnet | Yes (persistent) | Across session, /clear at pressure | No (requests via orchestrator) |
| Researcher | Teammate | Sonnet | Yes (persistent) | Across session, /clear at pressure | No (requests via orchestrator) |
| Planner | Teammate | Opus | No | Per-feature | No (requests via orchestrator) |
| Coder | Teammate (director) | Opus | Yes (persistent) | Across ALL phases, /clear between phases | No (requests via orchestrator) |
| Tester | Teammate | Opus | Yes (persistent) | Per-phase | No (requests via orchestrator) |
| Auditor | Sub-agent (resumable) | Sonnet | Yes (own container) | Within session only | N/A |

### Sub-Agent Types (Spawned by Orchestrator)

| Sub-Agent | Parent Teammate | Model | PTC Container | Validation |
|-----------|----------------|-------|---------------|------------|
| File Reader | Explorer | Haiku | Explorer's container | Output minimum length |
| Structural Analyzer | Explorer | Sonnet | Explorer's container | Context packet schema |
| WebSearch Researcher | Researcher | Sonnet | Researcher's container | Research schema + confidence |
| Library Doc Researcher | Researcher | Haiku | Researcher's container | Research schema |
| Test Writer | Coder | Sonnet | Coder's container | Tests exist, red phase verified |
| Implementer | Coder | Sonnet | Own worktree, coder's PTC | Quality gate (ruff, pyright, pytest) |
| Code Fixer | Coder | Sonnet | Implementer's worktree | Quality gate |
| Test Fixer | Coder | Sonnet | Coder's container | Tests valid, no implementation code touched |
| Task Auditor | Coder (via orchestrator) | Sonnet | Own auditor container | APPROVED/CRITIQUE/ESCALATED with file:line |
| Phase Auditor | Orchestrator | Sonnet | Own auditor container | Phase report schema |
| Scenario Writer | Tester | Sonnet | Tester's container | Tests exist, blind to implementation |
| Scenario Executor | Tester | Sonnet | Tester's container | Pytest results parsed |

### Communication Architecture

```
Direct messaging (SendMessage, teammate <-> teammate):
  - Coder <-> Explorer: codebase questions, context requests
  - Coder <-> Researcher: API/library questions
  - Planner <-> Explorer: context grounding
  - Planner <-> Researcher: design research
  - Tester <-> Explorer: interface surface queries (blind to implementation)

Orchestrator-mediated (ONLY for sub-agent spawning):
  - Any teammate -> orchestrator: delegation request (file path to delegation JSON)
  - Orchestrator spawns sub-agent, routes result back to requesting teammate

User <-> Orchestrator:
  - User talks to orchestrator for coordination, questions, decisions
  - Orchestrator routes user questions to appropriate teammate
  - Orchestrator spawns ad-hoc sub-agents for user queries
```

### Constraint: Only orchestrator can spawn agents (Task tool stripped from teammates)

The coder, explorer, researcher, and tester cannot spawn sub-agents directly. They write delegation prompt JSON to disk, send an info_request to the orchestrator with the file path, and the orchestrator spawns on their behalf. Sub-agent results are routed back via info_ready with output file paths.

---

## 2. State Machine Changes

### 2.1 System State Machine (`system.json`)

**New states for wave-based phase execution:**

```
IDLE -> DESIGN_LOADED -> EXPLORING -> CONTEXT_READY -> STRATEGIZING ->
PLAN_TEXT_REVIEW -> PLAN_JSON_CONVERSION -> PLAN_READY ->
PHASE_ACTIVE -> WAVE_DISPATCHED -> WAVE_AWAITING -> WAVE_REVIEW -> WAVE_MERGE ->
[loop back to WAVE_DISPATCHED for next wave, or] ->
PHASE_TASKS_COMPLETE -> PHASE_SCENARIO_EXECUTION -> PHASE_REMEDIATION ->
PHASE_SIMPLIFY -> PHASE_AUDIT -> PHASE_REPORT -> PHASE_COMMITTED ->
[next phase or] FINAL_AUDIT -> COMPLETE
```

**Key additions:**
- `WAVE_DISPATCHED`: All tasks in current wave spawned as parallel sub-agents with worktrees
- `WAVE_AWAITING`: Guards check all sub-agents for this wave have returned
- `WAVE_REVIEW`: Auditor sub-agents review each task, /simplify runs on changed files (excluding JSON)
- `WAVE_MERGE`: All worktrees merged, conflicts resolved
- `PHASE_SIMPLIFY`: /simplify runs on all phase changes before scenario testing
- Wave cycle: WAVE_DISPATCHED -> WAVE_AWAITING -> WAVE_REVIEW -> WAVE_MERGE -> [next wave or PHASE_TASKS_COMPLETE]

**Think-gated states:**
- `PHASE_ACTIVE`: Think about wave decomposition, which tasks are parallel
- `WAVE_REVIEW`: Think about review results, whether to proceed or remediate
- `PHASE_REMEDIATION`: Think about remediation strategy

### 2.2 Coder State Machine (`coder.json`) — Major Restructure

**Old model:** Per-tool TDD states (TEST_DESIGN -> IMPLEMENTATION -> TDD_GREEN -> etc.)

**New model:** Director states managing sub-agent lifecycle

```
SPAWNED -> CONTEXT_LOADED ->
TASK_RECEIVED -> DELEGATION_THINK ->
TEST_WRITER_DISPATCHED -> TEST_WRITER_AWAITING -> TEST_REVIEW ->
[resume test writer if issues] ->
RED_VERIFIED ->
IMPLEMENTER_DISPATCHED -> IMPLEMENTER_AWAITING -> IMPLEMENTATION_REVIEW ->
[resume implementer if issues] ->
AUDIT_REQUESTED -> AUDIT_AWAITING -> AUDIT_REVIEW ->
[if CRITIQUE: FIX_PLANNING -> FIX_DISPATCHED -> FIX_AWAITING -> AUDIT_REQUESTED] ->
[if APPROVED:] TASK_COMPLETE ->
[next task in wave, or] WAVE_TASKS_DONE
```

**Universal transitions:**
- `context_pressure_exceeded` -> `HANDOFF` (any state)

**Think-gated states:**
- `DELEGATION_THINK`: Think about test strategy, sub-agent prompt, success criteria. References delegation-prompts skill. CHOSEN: DISPATCH
- `TEST_REVIEW`: Think about test quality, edge cases. CHOSEN: APPROVE or CRITIQUE
- `IMPLEMENTATION_REVIEW`: Think about code quality, plan adherence. CHOSEN: APPROVE or CRITIQUE
- `AUDIT_REVIEW`: Think about audit findings, which side to fix. CHOSEN: APPROVE (accept audit) or CRITIQUE (push back on audit)
- `FIX_PLANNING`: Think about fix approach, test vs code separation. CHOSEN: FIX_TESTS or FIX_CODE

**Sub-agent tracking:** Agent state includes `active_subagents[]` and `subagent_history[]` (never deleted, only appended). Guards check sub-agent status for transitions.

**Per-state permissions:**
- `TEST_WRITER_DISPATCHED/AWAITING`: Coder is read-only (waiting for sub-agent)
- `IMPLEMENTATION_REVIEW`: Coder can read implementation files
- `FIX_PLANNING`: Coder can read both tests and code (deliberation only, no writes)

### 2.3 Explorer State Machine (`explorer.json`) — Add Delegation

**New states for sub-agent delegation:**

```
SPAWNED -> EXISTING_CONTEXT_CHECK ->
SCOPE_ANALYSIS (think: exploration strategy, sub-agent count) ->
DELEGATION_THINK (think: what does each sub-agent need?) ->
SUB_AGENTS_REQUESTED -> SUB_AGENTS_AWAITING ->
SYNTHESIS (PTC-based, merge sub-agent results) ->
PACKET_VALIDATION -> PACKET_WRITTEN -> IDLE
```

**Key changes:**
- `DELEGATION_THINK`: Think about how many sub-agents, what model each, what files each reads. CHOSEN: DISPATCH or EXPLORE (do it yourself for simple queries)
- `SUB_AGENTS_REQUESTED`: Explorer sends delegation request to orchestrator
- `SUB_AGENTS_AWAITING`: Waits for orchestrator to route results back
- `SYNTHESIS`: Uses PTC to merge, de-duplicate, resolve conflicts from multiple sub-agent results

### 2.4 Researcher State Machine (`researcher.json`) — Add Delegation

Similar to explorer. Add DELEGATION_THINK state before dispatching research sub-agents. Think prompt references research-methodology skill routes.

### 2.5 Tester State Machine (`tester.json`) — Add Delegation

Add DELEGATION_THINK before scenario writer dispatch. Think prompt references scenario-testing skill tier allocation.

### 2.6 Strategist/Planner State Machine (`strategist.json`)

**New requirements in plan output:**
- Dependency graph with explicit `depends_on` per task
- Wave grouping: tasks pre-grouped into parallelizable waves per phase
- Phase parallelizability validation: all tasks in a wave must have no shared files

### 2.7 Auditor State Machines — Simplify for Sub-Agent

Auditor-task and auditor-phase are sub-agents now. Their "state machines" become prompt instructions rather than daemon-enforced states. The daemon tracks them only via the parent's `active_subagents[]` field.

---

## 3. Skills Updates (all in `new_claude/skills/`)

### 3.1 delegation-prompts (Major Revamp)

**Add per-agent route tables:**
- Coder routes: test_writing, implementation, audit_request, fix_tests, fix_code
- Explorer routes: file_analysis, dependency_map, git_history, structural_analysis
- Researcher routes: library_docs, comparison, best_practices, survey
- Tester routes: scenario_writing, scenario_execution

**Add think MCP integration:**
- Every delegation MUST be preceded by a think call
- Think prompt template that references this skill's route table
- CHOSEN values: DISPATCH, CLARIFY, EXPLORE (do it yourself)

**Add resume protocol:**
- When to resume vs fresh-spawn decision tree
- Resume preserves full conversation history (within same session)
- Resume not available after /clear (session-bound)

**Add quality gate exit condition:**
- All sub-agents must pass their type-specific validation before returning
- Implementers: full quality gate (ruff, pyright, pytest)
- Test writers: tests exist, red phase verified
- Auditors: structured verdict with file:line references

**Add orchestrator-mediated spawn protocol:**
- Teammate writes delegation JSON to `.claude/temp/delegation-{task}.json`
- Teammate sends info_request to orchestrator with file path
- Orchestrator reads, spawns, routes result back via info_ready

### 3.2 sub-agent-delegation (Update)

- Add PTC container routing guidance (register_parent for shared container, skip for own container)
- Add resume vs fresh-spawn decision tree
- Add orchestrator-mediated spawn protocol
- Update agent-specific routes table for new architecture
- Add sub-agent pre-compact handoff protocol (rare, safety net)

### 3.3 task-execution (Restructure)

- Restructure for director pattern: deliberate (think) -> delegate (sub-agent) -> review
- Add sub-agent batched iteration spec: "implement until tests pass, max 5 iterations"
- Add strict test/code separation: test writer cannot touch code, implementer cannot touch tests
- Add audit response protocol: coder evaluates critique, decides test-fix vs code-fix

### 3.4 handoff-protocol (Major Update)

- Add /clear-specific handoff template with schema
- Add orchestrator handoff schema (active teammates, phase progress, decisions)
- Add PTC field extraction pattern: "use PTC to extract specific fields, don't read full JSON"
- Add handoff consumption: consumed handoffs moved to `.claude/temp/consumed/` or deleted
- Add per-role handoff templates (orchestrator, coder, explorer, researcher, tester)

### 3.5 codebase-exploration (Update)

- Update for Sonnet director model
- Add think-gated sub-agent dispatch (DELEGATION_THINK state)
- Add PTC synthesis patterns (merge results in container namespace)
- Add interactive Q&A protocol (inline answers + context packet generation)

### 3.6 research-methodology (Update)

- Update for Sonnet director model
- Add think-gated dispatch with multi-avenue pattern
- Add orchestrator-mediated spawn for WebSearch sub-agents
- Add synthesis via PTC (merge findings in container namespace)

### 3.7 code-review (Update)

- Update for sub-agent invocation context (auditor is a sub-agent, not teammate)
- Add resume-based multi-critique flow
- Add adversarial framing from Superpowers: "Do Not Trust the Report"
- Add structured verdict format (APPROVED/CRITIQUE/ESCALATED with file:line)

### 3.8 scenario-testing (Update)

- Add memory: project patterns for cross-session knowledge
- Add cross-session test pattern persistence
- Add stall detection from previous sessions

### 3.9 phase-planning (Update)

- Add wave decomposition requirement: tasks pre-grouped into parallelizable waves
- Add dependency graph output requirement with explicit `depends_on` per task
- Add phase parallelizability validation
- Add task granularity guidance from Superpowers: "each step is one action, 2-5 minutes"

### 3.10 NEW: ptc-sandbox (Universal)

- Container routing: when to register_parent (shared container) vs skip (own container)
- REPL management: namespace persistence, data sharing patterns
- Batch analysis patterns: tree-sitter, dependency graphs, test result parsing
- Handoff field extraction: check for handoff file on startup, extract via PTC
- Cross-agent data: how to read data left by a previous agent in the container
- Package reference: what's available per role image
- All agents load this skill

### 3.11 NEW: quality-gate (Fork Skill)

```
---
name: quality-gate
context: fork
model: sonnet
---
```

- Language-aware: detect project type from config files
- Python: ruff format, ruff check --fix, pyright, pytest
- JS/TS: prettier, eslint --fix, tsc, jest/vitest
- Rust: cargo fmt, cargo clippy --fix, cargo test
- Auto-fix everything fixable, report unfixable
- Return structured result: {passed, fixed[], unfixed[]}
- Usable in any codebase without modification

### 3.12 NEW: handoff (User-Invocable Skill)

```
---
name: handoff
description: Write agent handoff and prepare for /clear.
  Triggers: /handoff, "prepare for clear", "write handoff"
---
```

- Writes role-specific handoff using schema
- Validates handoff via schema validator
- Outputs: "Handoff written to {path}. Type /clear to continue."
- On resume: SessionStart hook reads handoff, PTC extracts fields

---

## 4. Hook Changes

### 4.1 SubagentStart Hook (`hooks/subagent_start.py`)

**Add:**
- Register sub-agent in parent teammate's agent state (`active_subagents[]` append)
- Record: sub-agent ID, type, task, model, spawned_at, trace_id
- Inject PTC context (container routing based on sub-agent type)
- Set up ptc_register_parent for sub-agents sharing parent container

### 4.2 SubagentStop Hook (`hooks/subagent_stop.py`)

**Add per-type validation:**
- Implementer: Quality gate result file exists and all_passed
- Test Writer: Test files exist, no implementation files modified
- Auditor: Structured verdict (APPROVED/CRITIQUE/ESCALATED)
- Explorer: Context packet schema compliance
- Researcher: Research file schema + _index.json updated

**Add daemon notification:**
- Ping daemon with sub-agent completion: ID, parent, task, outcome
- Daemon updates parent's `active_subagents[]` status
- Daemon records in `subagent_history[]` (permanent, never deleted)
- Daemon writes quality gate result to `~/.claude/logs/quality-gates.jsonl`

**Block termination if validation fails** (existing behavior, extended to new types)

### 4.3 PreCompact Hook for Sub-Agents

**New hook (or extension of existing pre_compact.py):**
- Detects context approaching 90% for sub-agents
- Forces sub-agent to write partial work to disk
- Sub-agent returns with status: NEEDS_CONTINUATION + handoff path
- Parent teammate (via orchestrator) spawns fresh sub-agent with handoff
- Should be exceedingly rare — well-scoped delegations complete within one window

### 4.4 SessionStart Hook (`hooks/session_start.py`)

**Add:**
- On /clear event: check for handoff file at expected path
- If handoff exists: inject resume context with handoff file path
- Include instruction: "Use PTC to extract handoff fields, don't read full JSON"
- After successful resume: move handoff to `.claude/temp/consumed/`

---

## 5. Schema Changes

### 5.1 New: Handoff Schema (`schemas/handoff_state.py`)

```python
class TeammateStatus(BaseModel):
    name: str
    role: AgentRole
    status: Literal["active", "idle", "awaiting_subagent"]
    current_task: str | None
    last_output: str | None  # file path

class SubagentRecord(BaseModel):
    id: str
    type: str  # implementer, test_writer, auditor, etc.
    for_teammate: str
    task: str
    model: str
    status: Literal["active", "terminated", "idle"]
    spawned_at: datetime
    terminated_at: datetime | None
    quality_gate_outcome: str | None
    output_files: list[str]
    token_usage: dict | None  # {input, output}
    trace_id: str | None

class HandoffState(BaseModel):
    agent_id: str
    agent_role: AgentRole
    written_at: datetime
    workflow_id: str
    feature: str
    current_phase: str
    phase_number: int
    current_wave: int | None
    active_teammates: list[TeammateStatus]
    subagent_history: list[SubagentRecord]  # last N relevant
    completed_phases: list[str]
    completed_waves_this_phase: list[int]
    completed_tasks_this_phase: list[str]
    remaining_tasks_this_phase: list[str]
    decisions_made: list[dict]  # {decision, reason, scope, locked}
    pending_decisions: list[dict]
    user_preferences: list[dict]
    key_files: dict[str, str]  # role -> path
    resume_instructions: str
    notes: str
```

### 5.2 Update: Agent State Schema (`schemas/agent_state.py`)

**Add fields:**
```python
class AgentState(BaseModel):
    # ... existing fields ...

    # Sub-agent tracking (new)
    active_subagents: list[SubagentRecord] = []
    subagent_history: list[SubagentRecord] = []  # permanent, never deleted
    last_completed_subagent: str | None = None  # for guard checks
    pending_delegation: str | None = None  # path to delegation JSON
```

### 5.3 Update: Plan Schema (in phase-planning skill)

**Add wave structure:**
```python
class Wave(BaseModel):
    wave: int
    tasks: list[str]
    parallel: bool = True
    depends_on_wave: int | None = None
    note: str | None = None

class Phase(BaseModel):
    id: str
    waves: list[Wave]
    depends_on_phase: str | None = None
```

### 5.4 New: Quality Gate Log Entry

```python
class QualityGateLogEntry(BaseModel):
    timestamp: datetime
    subagent_id: str
    parent_teammate: str
    task: str
    subagent_type: str
    gate_results: dict[str, Literal["pass", "fail"]]  # format, lint, typecheck, tests
    outcome: Literal["all_passed", "failed"]
    trace_id: str | None
```

---

## 6. Daemon Changes

### 6.1 Sub-Agent Registration

New daemon commands:
- `register_subagent`: Called by SubagentStart hook. Records sub-agent in parent's state.
- `complete_subagent`: Called by SubagentStop hook. Updates sub-agent status, records quality gate.
- `get_subagent_status`: Guard helper. Returns sub-agent status for transition evaluation.

### 6.2 New Guards

```python
# Sub-agent guards
"all_wave_subagents_complete"  # All sub-agents for current wave have terminated
"subagent_quality_gate_passed"  # Last completed sub-agent passed quality gate
"all_wave_quality_gates_passed"  # All sub-agents in wave passed quality gate
"no_active_subagents"  # No sub-agents currently running
"delegation_prompt_valid"  # Delegation JSON validates against schema

# Wave guards
"wave_has_remaining_tasks"  # Current wave has undispatched tasks
"all_waves_complete"  # All waves in current phase are done
"current_wave_merged"  # All worktrees for current wave merged successfully
```

### 6.3 Quality Gate Logging

Daemon writes to `~/.claude/logs/quality-gates.jsonl` on every sub-agent completion. Entry includes: sub-agent ID, parent, task, type, gate results, outcome, trace context.

### 6.4 Sub-Agent History Persistence

Sub-agent records are NEVER deleted from agent state. They are appended to `subagent_history[]` on termination. This provides:
- Complete audit trail for every sub-agent ever spawned
- Trace context linking (sub-agent trace_id -> parent trace_id)
- Token usage tracking per sub-agent type (for optimization)

---

## 7. Agent Definition Updates (`.claude/agents/`)

All agent definitions need rewriting to reflect:
- Director pattern (teammates delegate via orchestrator, don't implement directly for mechanical work)
- Think MCP usage at delegation points (not sequential-thinking)
- Delegation skill references in system prompts
- PTC sandbox skill as universal loaded skill
- /clear + handoff protocol
- Direct teammate-to-teammate messaging for information exchange
- Orchestrator-mediated sub-agent spawning only

### Agents to Write/Rewrite

| Agent File | Status | Key Changes |
|-----------|--------|-------------|
| `orchestrator.md` | Rewrite | Spawn dispatcher, phase coordinator, user interface, /clear + handoff |
| `codebase-explorer.md` | Rewrite | Sonnet director, sub-agent delegation via orchestrator, PTC synthesis |
| `researcher.md` | Rewrite | Sonnet director, multi-avenue research via orchestrator, PTC synthesis |
| `plan-architect.md` | Update | Wave decomposition, dependency graphs, phase parallelizability |
| `chunk-coder.md` | Major rewrite | Opus director, sub-agent lifecycle, strict test/code separation, parallel waves |
| `scenario-tester.md` | Write (new) | Opus designer, sub-agent execution, memory: project, blind testing |
| `auditor-task.md` | Write (new) | Sonnet sub-agent definition, structured verdict, resume protocol |
| `auditor-phase.md` | Write (new) | Sonnet sub-agent definition, phase report schema |

---

## 8. Quality Gate Architecture

### Sub-Agent Level (Mandatory)

Every sub-agent runs its type-specific validation before termination. SubagentStop hook enforces this. The sub-agent cannot return until validation passes.

### Coder Director Level (Guard-Based, Not Re-Run)

The coder's state machine guard reads the sub-agent's quality gate result file. It does NOT re-run the quality gate — it verifies the EVIDENCE that the gate passed. Zero redundancy, mechanical verification.

```python
def _guard_subagent_gate_passed(agent_id, agent_dict, ...):
    last_subagent = agent_dict.get("last_completed_subagent")
    # Read the quality gate log entry for this sub-agent
    return gate_log_entry.outcome == "all_passed"
```

### Freeflow Level (Fork Skill)

The `quality-gate` fork skill is language-aware, auto-fixing, and usable outside the workflow. Available as a user-invocable skill for ad-hoc development.

### /simplify (Daemon-Triggered)

At `WAVE_REVIEW` or `PHASE_SIMPLIFY` state, daemon triggers /simplify on changed files. Excludes JSON/JSONL/MD files. Catches cross-task code quality issues.

---

## 9. Token Impact Estimate

### Per Phase (4 tasks, 2 waves of 2 parallel tasks)

| Component | Current (All-Opus Teammates) | Restructured (Directors + Sub-Agents) | Savings |
|-----------|-------|-------------|---------|
| Orchestrator | 50K Opus | 50K Opus | 0% |
| Explorer | 150K Sonnet (5 teammate spawns) | 80K Sonnet director + 40K Haiku sub-agents | ~40% cost |
| Researcher | 75K Sonnet (3 teammate spawns) | 50K Sonnet director + 25K Sonnet sub-agents | ~20% cost |
| Planner | 80K Opus | 80K Opus | 0% |
| Coder | 400K Opus (4 tasks serial) | 100K Opus director + 200K Sonnet sub-agents | ~60% cost |
| Auditor | 240K (undefined, est.) | 80K Sonnet sub-agents (resumable) | ~67% cost |
| Tester | 80K (undefined, est.) | 50K Opus director + 30K Sonnet sub-agents | ~30% cost |
| Scribe | 90K Haiku | 0K (removed, fork skill) | 100% |
| **Total** | **~1,165K mixed** | **~785K mixed** | **~33% total tokens** |
| **Cost-adjusted** | **~1,165K Opus-equiv** | **~450K Opus-equiv** | **~61% cost reduction** |

The cost reduction is larger than the token reduction because mechanical work shifts from Opus ($15/$75 per M) to Sonnet ($3/$15 per M) and Haiku ($0.80/$4 per M).

### Additional Savings from /clear + PTC

- /clear saves ~10-20K tokens per agent transition (no re-initialization)
- PTC saves ~90-95% on data analysis tasks (100K -> 500 tokens for file analysis)
- Think MCP saves ~5-10K per delegation (better prompts -> fewer iterations)
- RTK saves ~30-60% on CLI output tokens

---

## 10. Migration Plan

### Phase 1: Schemas + Guards (Foundation)

1. Create `schemas/handoff_state.py` with HandoffState, SubagentRecord
2. Add `active_subagents`, `subagent_history` to `schemas/agent_state.py`
3. Add wave structure to plan schema in `new_claude/skills/phase-planning/schemas/`
4. Create `schemas/quality_gate_log.py`
5. Implement new guards in `scripts/daemon/guards/mechanical.py`:
   - `all_wave_subagents_complete`, `subagent_quality_gate_passed`
   - `all_waves_complete`, `current_wave_merged`, `delegation_prompt_valid`
6. Add daemon commands: `register_subagent`, `complete_subagent`
7. Write tests for all new schemas and guards

### Phase 2: Hook Updates

8. Update `hooks/subagent_start.py`: sub-agent registration in parent state
9. Update `hooks/subagent_stop.py`: per-type validation, daemon notification, quality gate logging
10. Add pre-compact handoff for sub-agents (extension of `hooks/pre_compact.py`)
11. Update `hooks/session_start.py`: /clear handoff detection and resume
12. Write tests for all hook changes

### Phase 3: State Machine Updates

13. Update `state-machines/system.json`: wave-based phase execution
14. Rewrite `state-machines/coder.json`: director states with sub-agent lifecycle
15. Update `state-machines/explorer.json`: delegation think-gate
16. Update `state-machines/researcher.json`: delegation think-gate
17. Update `state-machines/tester.json`: delegation think-gate
18. Update `state-machines/strategist.json`: wave decomposition requirement
19. Simplify `state-machines/auditor-task.json`: sub-agent lifecycle
20. Simplify `state-machines/auditor-phase.json`: sub-agent lifecycle
21. Validate all state machines load and resolve correctly

### Phase 4: Skills Updates

22. Major revamp: `delegation-prompts` (route tables, think integration, resume, quality gate)
23. Update: `sub-agent-delegation` (PTC routing, resume decision tree, orchestrator protocol)
24. Restructure: `task-execution` (director pattern, batched iteration)
25. Major update: `handoff-protocol` (/clear handoff, orchestrator handoff, PTC extraction)
26. Update: `codebase-exploration` (think-gated delegation, PTC synthesis)
27. Update: `research-methodology` (think-gated delegation, PTC synthesis)
28. Update: `code-review` (sub-agent context, resume multi-critique, adversarial framing)
29. Update: `scenario-testing` (memory: project, cross-session knowledge)
30. Update: `phase-planning` (wave decomposition, dependency graphs)
31. Create: `ptc-sandbox` (universal PTC skill)
32. Create: `quality-gate` fork skill (language-aware, auto-fix)
33. Create: `handoff` user-invocable skill

### Phase 5: Agent Definitions

34. Rewrite: `orchestrator.md`
35. Rewrite: `codebase-explorer.md`
36. Rewrite: `researcher.md`
37. Update: `plan-architect.md`
38. Major rewrite: `chunk-coder.md`
39. Write: `scenario-tester.md`
40. Write: `auditor-task.md` (sub-agent definition)
41. Write: `auditor-phase.md` (sub-agent definition)

### Phase 6: Integration Testing

42. End-to-end test: single task through coder director flow
43. End-to-end test: parallel wave with 2 tasks
44. End-to-end test: /clear + handoff resume for orchestrator
45. End-to-end test: /clear + handoff resume for coder between phases
46. End-to-end test: sub-agent resume within session (auditor multi-critique)
47. End-to-end test: full phase with scenario testing
48. Token usage measurement and comparison against estimates

---

## 11. Open Items for Future Sessions

### Deferred (Out of Scope)

- Debug state machine (`debug.json`) — parallel hypothesis investigation
- Git management fork skill — branch analysis, merge conflict resolution
- TOON conversion for delegation prompts — premature, revisit with data
- /simplify custom version — use built-in first, enhance if domain-specific gaps found

### To Revisit with Research

- Think gate question design: optimal questions for each delegation type
- Sub-agent prompt templates: what makes a maximally effective sub-agent prompt
- Wave sizing: how many parallel tasks before diminishing returns
- Model tier validation: compare Sonnet vs Opus output quality for each sub-agent type

### Superpowers Patterns Adopted

- Controller-never-implements: orchestrator and director teammates never write code directly
- Paste-don't-reference: delegation prompts contain full task spec inline
- Adversarial review framing: "Do Not Trust the Report" in auditor prompts
- Escalation caps: max 5 review iterations, max 3 fix attempts
- Status-code-based branching: sub-agent returns DONE/DONE_WITH_CONCERNS/BLOCKED/NEEDS_CONTINUATION
- Bite-sized tasks: each sub-agent task is 2-5 minutes of work

### Superpowers Patterns NOT Adopted (and why)

- Sequential task execution: we use wave-based parallelism instead
- No formal dependency DAG: we require explicit dependency graph from planner
- Checkpoint via checkboxes: we use daemon state + handoff files instead
- Plan-document-reviewer: we use think MCP for plan quality gating instead
