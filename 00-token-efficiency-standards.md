# Doc 0: Token Efficiency Standards

**Status:** Draft v2
**Depends on:** None (foundation document)
**Unblocks:** All other design documents

## Problem

Current overhead per agent spawn: ~2,100-2,500 tokens before real work begins. CLAUDE.md alone is ~1,900 tokens loaded universally with ~40% irrelevant per role. A coder loading 3 v1 skills burns ~14,700 tokens. System overhead consumes 35-50% of an agent's context window.

Additionally, the current hook system uses only command hooks (shell scripts). Claude Code supports three hook handler types — command, prompt, and agent — across 16 events. We're using 6 events with 1 handler type. This leaves significant architectural capability untapped.

---

## 1. Context Budget Philosophy

### 1.1 The 10/10/80 Target

The target allocation for every agent's context window:

| Allocation | Target % | Purpose |
|---|---|---|
| System overhead | ~10% | CLAUDE.md + skill metadata + injected context + hook annotations |
| Handoff reserve | ~10% | State snapshot for replacement agent if context pressure triggers |
| Actual work | ~80% | Code reading, writing, reasoning, tool calls |

**This is an optimization target, not a hard constraint.** If a coder needs 4 skills loaded to do its job correctly, or a strategist needs deep context to make good decisions, that's the right tradeoff. A working workflow that uses 20% overhead is better than a broken one that hits 10%.

The budget exists to **identify waste** — tokens spent on irrelevant CLAUDE.md sections, verbose serialization, redundant context. It does NOT exist to amputate functionality.

### 1.2 Where Budgets Are Hard vs. Soft

| Component | Constraint | Rationale |
|---|---|---|
| CLAUDE.md universal section | **Soft** (~800 tokens target) | Trim waste, but include what's needed |
| Skill metadata | **Soft** (~150 tokens target) | Trigger conditions only — naturally small |
| Skill body (SKILL.md) | **Soft** (500-line guideline) | Complex skills may need more; measure, don't truncate |
| SubagentStart injection | **Hard** (MAX_CONTEXT_CHARS=3000) | Already enforced; prevents runaway injection |
| Per-turn annotations | **Hard** (max 3) | Prevents unbounded context growth per turn |
| Message bus window | **Hard** (last 15 entries) | Already enforced; prevents O(n) growth |
| Serialization format | **Standard** (compact for LLM, JSON for disk) | Always use compact when injecting into context |

### 1.3 Per-Model Context

| Model | Context window | 10% target | Notes |
|---|---|---|---|
| Opus 4.6 | 200K tokens | ~20,000 | Orchestrator, strategist, coder, tester, auditor |
| Sonnet 4.6 | 200K tokens | ~20,000 | Explorer, researcher |
| Haiku 4.5 | 200K tokens | ~20,000 | Extraction sub-agents |

---

## 2. Encoding Standards

### 2.1 Core Principle: Two Representations, One Truth

Data lives in two places with different constraints:

```
ON DISK (JSON)                      INJECTED INTO AGENT CONTEXT (compact)
──────────────────                  ────────────────────────────────────
Full-fidelity JSON                  Token-optimized representation
Read by hooks, scripts,             Read by the LLM
validators, Python code

Source of truth — never changes     Converted AT INJECTION TIME only
                                    by hooks (subagent_start, post_tool_use)

Written by agents normally          LLM never writes compact format back
(SendMessage, Write, etc.)          Hooks capture output → write full JSON
```

**No information loss.** The compact formats are lossless alternative representations. Data flows one way: JSON on disk → compact in context. The LLM's outputs are always captured as full JSON by hooks. There is no round-trip or reconversion.

**When to use compact encoding:** Any time data is injected into an agent's context window via `additionalContext`, `subagent_start.py`, or skill loading. Never for data written to disk.

### 2.2 TOON Format (Tabular Object-Oriented Notation)

For arrays of homogeneous objects, use pipe-delimited tabular format instead of JSON arrays. Measured savings: **30-60%** (audit finding: 61% on 256-entry array, 11,842 → 4,617 tokens). LLMs parse tabular data more accurately than verbose JSON.

**When to use:** Any array of >3 objects with shared keys injected into agent context.

**Format:**
```
# HEADER
field1|field2|field3
value1|value2|value3
value1|value2|value3
```

**Example — message bus log:**

Before (JSON, ~180 tokens):
```json
[
  {"from": "orchestrator-abc-1234", "to": "coder-xyz-5678", "type": "task_assign", "timestamp": "2026-02-26T10:00:00Z", "summary": "Implement auth module"},
  {"from": "coder-xyz-5678", "to": "orchestrator-abc-1234", "type": "status_update", "timestamp": "2026-02-26T10:05:00Z", "summary": "Tests written, starting impl"}
]
```

After (TOON, ~75 tokens):
```
# MESSAGE_LOG
from|to|type|time|summary
orchestrator-abc-1234|coder-xyz-5678|task_assign|10:00|Implement auth module
coder-xyz-5678|orchestrator-abc-1234|status_update|10:05|Tests written, starting impl
```

**Implementation:** Add `to_toon()` helper to `hooks/utils/state_helpers.py`:

```python
def to_toon(header: str, objects: list[dict], fields: list[str]) -> str:
    lines = [f"# {header}", "|".join(fields)]
    for obj in objects:
        lines.append("|".join(str(obj.get(f, "")) for f in fields))
    return "\n".join(lines)
```

### 2.3 Short Key Aliases with Legend

For single objects or small arrays, use abbreviated keys with a one-line legend in the header.

**Measured savings:** 20-30% per object.

**Example — agent state:**

Before (~65 tokens):
```json
{"agent_id": "coder-xyz-5678", "role": "coder", "current_state": "IMPLEMENTATION", "context_usage_pct": 45.2, "worktree": "/tmp/wt-auth"}
```

After (~35 tokens):
```
# AGENT id=agent_id r=role s=state ctx=context_% wt=worktree
id:coder-xyz-5678 r:coder s:IMPLEMENTATION ctx:45 wt:/tmp/wt-auth
```

The legend (`id=agent_id r=role ...`) is in the header — the LLM reads it once and understands all subsequent entries. No guessing, no ambiguity.

**Implementation:** Add `to_compact()` to `state_helpers.py`:

```python
def to_compact(header: str, obj: dict, aliases: dict[str, str]) -> str:
    legend = " ".join(f"{v}={k}" for k, v in aliases.items())
    values = " ".join(f"{aliases[k]}:{obj[k]}" for k in aliases if k in obj)
    return f"# {header} {legend}\n{values}"
```

### 2.4 Pydantic Serialization Defaults

All Pydantic model `.model_dump()` calls for LLM injection MUST use:

```python
model.model_dump(exclude_defaults=True, exclude_none=True)
```

**Measured savings:** 15-40% per object. Eliminates null fields and fields at default values. The LLM knows the defaults (they're in the schema or skill instructions), so omitting them loses no information.

**Additionally**, add to every schema's `model_config`:

```python
class MyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")  # catches typo keys at validation time
```

Standardize in `state_helpers.py`:

```python
def dump_compact(model: BaseModel) -> dict:
    return model.model_dump(exclude_defaults=True, exclude_none=True)
```

### 2.5 Compact State Machine Representation

When injecting state machine context into an agent's window (showing current state and available transitions), use compact format.

**Measured savings:** ~70% (1,400 → ~420 tokens).

Before:
```json
{
  "current_state": "IMPLEMENTATION",
  "available_transitions": [
    {"to_state": "TDD_GREEN", "trigger": "tests_pass", "guards": ["guard_pytest_exit_zero"]},
    {"to_state": "RED_FAILED", "trigger": "tests_fail_unexpected", "guards": []}
  ]
}
```

After:
```
STATE: IMPLEMENTATION
TRANSITIONS:
  → TDD_GREEN (tests_pass) [guard: pytest_exit_zero]
  → RED_FAILED (tests_fail_unexpected)
```

Same information. The agent sees exactly which transitions are available, what triggers them, and what guards must pass.

**Implementation:** Add `format_state_context(machine, current_state)` to `state_helpers.py`.

---

## 3. Progressive Disclosure Pattern

Three-tier information loading to minimize upfront context cost:

| Tier | When loaded | Target size | Contents |
|---|---|---|---|
| **Metadata** | Always (at spawn) | ~150 tokens | Skill name, trigger conditions, YAML frontmatter |
| **Body** | On trigger match | ~3,000 tokens | Core workflow instructions (SKILL.md) |
| **References** | On explicit demand | Unlimited | Patterns, edge cases, anti-patterns, examples |

**Applies to:**
- **Skills:** Metadata always → SKILL.md on trigger → references/ on demand (see Doc 3)
- **Context packets:** Summary always → full content on query → raw files via PTC (see Doc 2)
- **Plans:** Phase list always → current chunk details on assignment → full plan on demand
- **Research:** Index always → entry on topic match → full source on demand

**Trigger mechanism:** Agent reads file (detected by `post_tool_use.py` `handle_read_skill` handler) or explicitly requests via PTC tool.

---

## 4. Hook Architecture: Three Handler Types

Claude Code supports three distinct hook handler types. The current system uses only command hooks. This design incorporates all three.

### 4.1 Hook Type Overview

| Type | What it does | Latency | Best for |
|---|---|---|---|
| **Command** (`type: "command"`) | Runs a shell script, reads stdin JSON, returns stdout JSON | Variable (target <50ms for PreToolUse, <10s for PostToolUse) | State machine checks, file validation, JSONL logging, schema validation |
| **Prompt** (`type: "prompt"`) | Sends hook input + custom prompt to a fast LLM (Haiku by default) | ~1-3s | Low-frequency judgment calls where rules can't capture the decision |
| **Agent** (`type: "agent"`) | Spawns a sub-agent with multi-turn tool access (Read, Grep, Glob — up to 50 turns) | ~5-30s | Complex verification requiring codebase inspection |

**When NOT to use prompt/agent hooks:**
- **High-frequency events** (PreToolUse, PostToolUse): These fire on every tool call. A coder doing 50 writes would burn 50 API calls just on hooks. Use command hooks — the state machine already handles write-gating via `write_allowed` and `write_globs` per state, deterministically, in <50ms.
- **Events where the hook can't see the real work product**: SubagentStop receives the last message, not the files written. A prompt judging "does this look right?" without reading the actual code is theater. If output needs real validation, the orchestrator dispatches a proper auditor agent that can read files, run tests, and log structured results.

**When prompt/agent hooks earn their cost:**
- **Low-frequency, high-stakes events** (Stop, UserPromptSubmit): Fire once per agent lifecycle or per user interaction. The latency cost is amortized.
- **Decisions that genuinely require judgment**: "Did this agent actually complete its task or did it give up?" can't be reduced to a file-exists check.

### 4.2 Event Coverage (16 Events)

Current usage vs. design target:

| Event | Current | Target | Handler type | Purpose |
|---|---|---|---|---|
| **PreToolUse** | command | command | command | State machine permission check (<50ms) |
| **PostToolUse** | command | command (sync) + command (async) | command | sync: validation, annotations; async: logging |
| **PostToolUseFailure** | — | command | command | Log failure, update state if needed |
| **SessionStart** | command | command | command | Agent registration, env setup |
| **PreCompact** | command | command | command | Save workflow state before compression |
| **Stop** | command | command | command | State cleanup, snapshot |
| **SubagentStart** | command | command | command | Context injection (must be fast) |
| **SubagentStop** | command | command | command | State update, result logging |
| **UserPromptSubmit** | — | prompt | prompt | Workflow phase scope check (low-frequency) |
| **PermissionRequest** | — | — | — | Not needed: state machine handles permissions via PreToolUse; agents run in automated mode |
| **TaskCompleted** | — | command | command | Update task list, trigger next assignment |
| **TeammateIdle** | — | command | command | Log idle event (no action — idle is normal) |
| **Notification** | — | — | — | Not used initially |
| **SessionEnd** | — | command | command | Cleanup, final state snapshot |
| **WorktreeCreate** | — | command | command | Worktree lifecycle (designed in Doc 1: Git Management) |
| **WorktreeRemove** | — | command | command | Worktree lifecycle (designed in Doc 1: Git Management) |

**Verification is the auditor's job.** The auditor agent (dispatched by the orchestrator after a coder signals completion) is the verification mechanism. It has full tool access, its own state machine (`auditor-task.json` with 4 modes), structured decision logs, and its results feed into the workflow's permanent record. Adding hook-based verification on top would be a redundant, less-observable layer. Hooks handle mechanics (state, permissions, logging); agents handle judgment (review, audit, approval).

**One prompt hook:** UserPromptSubmit is the only prompt hook in the initial design. It fires once per user interaction (not per tool call), so latency is acceptable. It checks whether the user's request fits the current workflow phase — e.g., preventing an "implement X" request when the workflow is still in exploration. This is a judgment call that can't be reduced to rules, and it's low-frequency enough that a Haiku call is fine.

### 4.3 New Hook Designs

#### 4.3.1 Worktree Lifecycle (WorktreeCreate/WorktreeRemove)

Deferred to **Doc 1: Git Management**. These hooks register/release worktree allocations in agent state, update port assignments, and log lifecycle events. The exact design depends on worktree allocation strategy and branch naming conventions defined in Doc 1.

### 4.4 Async Hook Pattern

Non-critical operations should not block the tool call pipeline.

**Current:** All PostToolUse handlers run synchronously in one script (10-second timeout).

**Target:** Split into synchronous (must-return) and asynchronous (fire-and-forget):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "type": "command",
        "command": "python hooks/post_tool_use.py",
        "timeout": 3,
        "statusMessage": "Validating..."
      },
      {
        "type": "command",
        "command": "python hooks/post_tool_use_async.py",
        "async": true
      }
    ]
  }
}
```

**Synchronous (post_tool_use.py):** Schema validation, lint checks, context pressure, annotation injection. Must return `additionalContext`.

**Asynchronous (post_tool_use_async.py):** Event logging, state transition JSONL, decision JSONL, message bus JSONL. Fire-and-forget, output delivered on next turn if needed.

**Async limitation:** Only command hooks support `async: true`. Prompt and agent hooks always run synchronously (they block until the LLM responds).

### 4.5 Hook Architecture Summary

```
PreToolUse          → command: State machine permission check (<50ms)
PostToolUse         → command (sync): Schema validation, lint, context pressure, annotations
                    → command (async): Event logging, JSONL appends
PostToolUseFailure  → command: Log failure, update state
SessionStart        → command: Registration, env setup
PreCompact          → command: State snapshot
Stop                → command: State cleanup, snapshot
SubagentStart       → command: Context injection
SubagentStop        → command: State update, result logging
UserPromptSubmit    → prompt: Workflow phase scope check (Haiku, low-frequency)
TaskCompleted       → command: Task list update
TeammateIdle        → command: Log only
SessionEnd          → command: Cleanup, final snapshot
WorktreeCreate      → command: Worktree lifecycle (see Doc 1)
WorktreeRemove      → command: Worktree lifecycle (see Doc 1)
```

**Division of responsibility:**
- **Hooks** handle mechanics: permissions, state transitions, logging, serialization. Deterministic, fast, no LLM cost.
- **Agents** handle judgment: code review, spec compliance, architectural decisions. Full tool access, observable, tracked.
- **One exception**: UserPromptSubmit uses a prompt hook because it's low-frequency and the decision (is this request in-phase?) requires judgment.

### 4.6 Designing New Hooks vs. Modifying Existing

| Hook file | Action | Changes |
|---|---|---|
| `hooks/pre_tool_use.py` | **Modify** | Remains command hook |
| `hooks/post_tool_use.py` | **Split** | Extract async handlers to `post_tool_use_async.py` |
| `hooks/post_tool_use_async.py` | **New** | Async event/JSONL logging |
| `hooks/post_tool_use_failure.py` | **New** | Failure logging, state update |
| `hooks/task_completed.py` | **New** | Task list update on completion |
| `hooks/session_end.py` | **New** | Final cleanup and snapshot |
| `hooks/worktree_create.py` | **New** | Worktree lifecycle (designed in Doc 1) |
| `hooks/worktree_remove.py` | **New** | Worktree lifecycle (designed in Doc 1) |
| UserPromptSubmit prompt | **Config only** | Defined in `.claude/settings.json`, no script |

---

## 5. Measurement Framework

### 5.1 Estimation Method

**Default:** Character count / 4 (industry standard approximation).

```python
def estimate_tokens(text: str) -> int:
    return len(text) // 4
```

**For critical paths** (CLAUDE.md, skill injection, subagent_start context): Use `tiktoken` cl100k_base tokenizer for exact counts during development. Deploy with character/4 estimation at runtime (tokenizer adds ~50ms latency).

### 5.2 Budget Verification Script

A standalone script (`scripts/token_budget_check.py`) that:
1. Reads all files that contribute to agent spawn context
2. Tokenizes each component
3. Reports per-role overhead breakdown
4. Flags components exceeding soft targets (warning, not failure)

**Run:** Part of CI/quality gate (informational, not blocking).

### 5.3 Runtime Monitoring

The existing `context_monitor.py` tracks `context_usage_pct` via agent state. Extend with:
- `overhead_tokens` field on `AgentState`: tracks cumulative injected overhead
- Informational warning when overhead exceeds target (log only, don't constrain)

---

## 6. Budget Targets Per Component

These are **optimization targets** — soft limits that flag waste, not hard caps that block functionality.

| Component | Target | Current estimate | Notes |
|---|---|---|---|
| CLAUDE.md (universal) | ~800 tokens | ~1,900 tokens | Role-specific content moves to agent specs (Doc 5) |
| Role instructions (per agent) | ~400 tokens | N/A (not yet built) | Injected by `subagent_start.py` |
| Skill metadata (all loaded) | ~150 tokens total | N/A (v2 not built) | YAML frontmatter, trigger conditions only |
| Skill body (on trigger) | ~3,000 tokens each | ~4,900 avg (v1) | SKILL.md, 500-line guideline |
| SubagentStart injection | <1,500 tokens | ~750 tokens | Identity + comms + task sections |
| State machine in context | ~500 tokens | ~1,400 tokens | Compact representation (Section 2.5) |
| Per-turn annotation cap | <200 tokens | Unbounded | Max 3 annotations, abbreviated |
| Message bus window | ~600 tokens | Unbounded potential | 15 entries in TOON format |

### Per-Role Overhead Projections (v2)

| Role | CLAUDE.md | Role spec | Skill meta | Skill body (est.) | Spawn injection | Total |
|---|---|---|---|---|---|---|
| Orchestrator | 800 | 400 | 100 | 3,000 | 1,500 | 5,800 |
| Coder | 800 | 400 | 100 | 3,000-6,000 | 1,500 | 5,800-8,800 |
| Explorer | 800 | 400 | 100 | 0* | 1,500 | 2,800 |
| Researcher | 800 | 400 | 100 | 3,000 | 1,500 | 5,800 |
| Tester | 800 | 400 | 100 | 3,000 | 1,500 | 5,800 |
| Auditor | 800 | 400 | 100 | 3,000 | 1,500 | 5,800 |
| Strategist | 800 | 400 | 100 | 3,000 | 1,500 | 5,800 |

*Explorer skills are reference-heavy; body stays minimal, content accessed via PTC.

Note: Coder shows a range because complex tasks may load 2 skill bodies. That's fine — functionality over budget.

**V1 comparison (coder):** CLAUDE.md (1,900) + 3 skills (14,700) + spawn (750) = **17,350 tokens**
**V2 projection (coder):** 800 + 400 + 100 + 3,000 + 1,500 = **5,800 tokens** (67% reduction)
**V2 projection (coder, 2 skills):** 800 + 400 + 100 + 6,000 + 1,500 = **8,800 tokens** (49% reduction, still good)

---

## 7. Key Decisions

### 7.1 Budget enforcement: Advisory with CI check

Rationale: Hard-enforcing in hooks adds latency and creates failure modes when estimates are off. Instead:
- `scripts/token_budget_check.py` runs in CI and during `gate.sh` (informational)
- `subagent_start.py` keeps `MAX_CONTEXT_CHARS=3000` as a hard cap on injection size (this one stays hard — it's a runaway prevention measure, not a quality constraint)
- Skill loading is naturally bounded by the 500-line SKILL.md guideline (see Doc 3)

### 7.2 `to_compact()` on Pydantic models AND hook serialization layer

- **Pydantic models** get a `to_compact()` method for self-serialization (agent state, handoff payloads)
- **Hook layer** (`state_helpers.py`) provides `to_toon()` and `to_compact()` as standalone functions for ad-hoc data

This avoids coupling: models don't need to know about TOON, and hooks don't need to know about model internals.

### 7.3 Schema versioning: MODEL-REVISION-ADDITION

Format: `1-0-0`
- **MODEL** increments: Breaking changes (field removed, type changed)
- **REVISION** increments: Behavioral changes (new required field)
- **ADDITION** increments: Additive changes (new optional field)

Add `schema_version` field to all Pydantic models. Validators check: same MODEL required, REVISION >= expected, ADDITION ignored.

### 7.4 Prompt and agent hooks: Config-only where possible

Prompt hooks (Haiku LLM checks) and agent hooks (sub-agent verification) are defined entirely in `.claude/settings.json` — no script files needed. This keeps the hook codebase lean. Only command hooks need Python scripts.

Exception: If a prompt/agent hook needs dynamic context (e.g., the current task description), a command hook pre-processes and injects it via environment variables or a temp file that the prompt template references.

---

## 8. Integration Points

| File | Action | Purpose |
|---|---|---|
| `hooks/utils/state_helpers.py` | Modify | Add `to_toon()`, `to_compact()`, `dump_compact()`, `format_state_context()`, `estimate_tokens()` |
| `hooks/subagent_start.py` | Modify | Use compact serializers for context injection |
| `hooks/post_tool_use.py` | Split | Extract async handlers to separate script |
| `hooks/post_tool_use_async.py` | New | Async event/JSONL logging |
| `hooks/post_tool_use_failure.py` | New | Failure logging, state update |
| `hooks/task_completed.py` | New | Task list update on completion |
| `hooks/session_end.py` | New | Final cleanup and snapshot |
| `schemas/*.py` | Modify | Add `ConfigDict(extra="forbid")`, `to_compact()`, `schema_version` |
| `scripts/token_budget_check.py` | New | Budget verification (informational) |
| `.claude/settings.json` | Modify | Add prompt hooks, agent hooks, async hooks, new events |

---

## 9. Verification Criteria

- [ ] `token_budget_check.py` reports per-role overhead breakdown
- [ ] CLAUDE.md trimmed to ~800 tokens (after Doc 5 role extraction)
- [ ] `to_toon()` and `to_compact()` produce valid output parseable by agents
- [ ] `exclude_defaults=True, exclude_none=True` applied to all LLM-injection `.model_dump()` calls
- [ ] Compact state machine representation renders correctly for all 8 machines
- [ ] PostToolUse split into sync/async with no functional regression
- [ ] UserPromptSubmit prompt hook fires correctly for phase-scope checks
- [ ] New command hooks (post_tool_use_failure, task_completed, session_end) registered and functional
- [ ] Worktree hooks (worktree_create/remove) designed and implemented per Doc 1
- [ ] Per-spawn overhead measured and reported per role
