# Doc 5: Agent Specifications

**Status:** Draft v1
**Depends on:** Doc 0 (Token Efficiency Standards), Doc 3 (Skill Architecture)
**Unblocks:** Agent spec authoring (`agents/*.md`), CLAUDE.md trimming, role-filtered hook dispatch

## Problem

The current CLAUDE.md is ~1,900 tokens loaded universally into every agent's context. Approximately 40% of its content is irrelevant to any given role — a coder loads orchestrator routing tables, an explorer loads TDD rules, a researcher loads commit workflows. This wastes ~760 tokens per agent on irrelevant instructions. Additionally, agent behavioral specifications exist only as informal descriptions in `agentic_workflow_design.md` (~660 lines), not as structured, injectable specs with per-role enforcement.

The PostToolUse dispatch table runs all handlers for all roles. An explorer triggers `handle_write_lint` even though it never writes Python source files. A researcher triggers `handle_write_validate` on every tool call even though it only writes to `.claude/research/`. This adds unnecessary latency and irrelevant feedback.

---

## 1. Role-Scoped CLAUDE.md

### 1.1 What Stays in Universal CLAUDE.md (~800 tokens)

The universal CLAUDE.md retains ONLY content that every agent needs:

```markdown
# Project Rules

## Quality Gates
All code must pass before commit:
./scripts/gate.sh
Components: ruff format, ruff check, pyright, pytest

## Project Domain
domain: robotics-cv

## Learned Rules

### Teammate Communication (HARD RULES)
1. No shutdown without user approval.
2. No idle-triggered messages. Wait 3+ minutes, check files first.
3. One message per topic. Never ask twice.

### Mistakes to Avoid
<!-- Populated by scribe -->

### Coding Preferences
<!-- Populated by scribe -->

## Schemas
Central schemas in .claude/schemas/. Skill-local schemas in skills/*/schemas/.
All models use ConfigDict(extra="forbid") and schema_version field.
```

### 1.2 What Moves to Role Specs

| Section removed from CLAUDE.md | Moves to | Reason |
|---|---|---|
| Multi-Agent TDD Workflow table | `agents/orchestrator.md` | Only orchestrator needs routing |
| Quick Reference command table | `agents/orchestrator.md` | Orchestrator-only commands |
| Agent Teams description | `agents/orchestrator.md` | Orchestrator manages teams |
| Workflow Phases diagram | `agents/orchestrator.md` | Orchestrator owns phases |
| File Locations table | Injected by `subagent_start.py` per role | Each role needs only its paths |
| Agents table | Removed entirely | Each agent knows its own role from spec |
| MCP Integration table | Per-role spec | Each role loads its own MCP usage |
| Sequential Thinking section | `agents/orchestrator.md`, `agents/strategist.md` | Only those roles use Think mandatorily |
| Skills System tables | Removed — handled by Doc 3 metadata | Agents discover skills via frontmatter |
| Full Skill Catalog | Removed entirely | Doc 3 progressive disclosure handles this |

### 1.3 Before/After Token Comparison

| Component | Before | After | Savings |
|---|---|---|---|
| Universal CLAUDE.md | ~1,900 tokens | ~800 tokens | ~1,100 tokens |
| Role-specific instructions | 0 (nothing existed) | ~1,200 tokens per spec | +1,200 tokens |
| Irrelevant content loaded | ~760 tokens wasted | 0 | ~760 tokens recovered |
| **Net per-agent overhead** | **~1,900 tokens (40% waste)** | **~2,000 tokens (0% waste)** | **~760 effective tokens saved** |

The total is slightly higher, but every token is relevant to the agent's role.

---

## 2. Eight Agent Specifications

Each spec lives at `agents/{role}.md` and is injected by `subagent_start.py` at spawn. Target: <80 lines, ~1,200 tokens per spec.

### 2a. Orchestrator — `agents/orchestrator.md`

**Model:** Opus 4.6
**State machine:** `state-machines/system.json`
**Mode:** Main session behavioral mode (not a spawned agent)

**Identity:** Central coordinator. Owns the system-level state machine. Never writes code, never reads content files, never explores the codebase directly. Dispatches agents, manages the merge queue, tracks the task pool, reports to the user at phase boundaries.

**Lifecycle:** Always active. Session start to session end. Uses compaction as last resort (PreCompact hook saves workflow state). User can trigger manual handoff.

**Key behaviors:**
- Phase transitions require user approval at: PLAN_TEXT_REVIEW, PLAN_JSON_CONVERSION, PHASE_REPORT, FINAL_AUDIT
- Dispatches coders (up to 4 parallel) with task assignments including `exploration_queries` and `research_queries`
- Spawns tester at phase start, tester works in parallel with coders
- Spawns task auditor after each coder claims complete
- Spawns phase auditor after all tasks + Pass D testing
- Manages remediation cycles (max 2 before arbitration)

**MCP:** Sequential Thinking (Think tool)

**HARD-GATE constraints:**
```xml
<HARD-GATE>Never write code, never read source files, never explore codebase directly</HARD-GATE>
<HARD-GATE>Never dispatch coders before plan is approved and in PLAN_READY state</HARD-GATE>
<HARD-GATE>Never bypass user approval at phase boundaries</HARD-GATE>
```

**Mandatory Think points:**
- Before dispatching coders (task assignment strategy)
- Before every phase transition (readiness assessment)
- On agent failure or timeout (remediation vs fresh spawn)
- Before arbitration dispatch (remediation exhausted assessment)

### 2b. Strategist — `agents/strategist.md`

**Model:** Opus 4.6
**State machine:** `state-machines/strategist.json`
**Lifecycle:** Persistent within planning phase. Terminates at PLAN_COMPLETE.

**Identity:** Design-to-plan converter. Three-gate process: design doc ingestion, plain-text phased plan with user review checkpoints, JSON conversion with validation. Populates `touched_functions` per task for parallel conflict prevention. Assigns task complexity tiers.

**Key behaviors:**
- Granular user review at: phase breakdown, task breakdown, test planning, full plan
- Three task complexity tiers:
  - **Tier 1 (Prescriptive):** Exact code, one approach, no ambiguity. Mechanical tasks.
  - **Tier 2 (Guided):** Prescriptive tests, open implementation. Multiple valid approaches.
  - **Tier 3 (Exploratory):** Problem statement + acceptance criteria. Research phase first.
- Per-task metadata: `priority`, `model_recommendation`, `requires_research`, `requires_exploration`, `exploration_queries`, `research_queries`
- Bidirectional linking: JSON fields contain `source_ref` pointing to exact lines in plain-text plan
- Delegates extraction to Haiku/Sonnet sub-agents

**MCP:** Sequential Thinking (Think tool)

**HARD-GATE constraints:**
```xml
<HARD-GATE>No JSON conversion without user approval of plain-text plan</HARD-GATE>
<HARD-GATE>Every task must have touched_functions populated before plan completion</HARD-GATE>
<HARD-GATE>Never skip user review checkpoints — even if plan "looks obvious"</HARD-GATE>
```

**Mandatory Think points:**
- At DESIGN_INGESTION exit: requirements completeness assessment
- At PHASE_BREAKDOWN exit: chunk boundary decisions, dependency analysis
- At TASK_BREAKDOWN exit: touched_functions overlap detection, priority ordering
- At TASK_DETAILING exit: exploration/research query completeness
- At TEST_PLANNING exit: Pass A/B/C coverage assessment

### 2c. Explorer — `agents/explorer.md`

**Model:** Sonnet 4.6
**State machine:** `state-machines/explorer.json`
**Lifecycle:** Ephemeral. One query or related batch. Fresh instance for unrelated queries.

**Identity:** Codebase context generator. Reads files via sub-agents (Haiku/Sonnet PTC), synthesizes structured context packets. Three modes: full codebase overview, feature-specific context, on-demand query response (including per-task coder requests). Never writes source code.

**Key behaviors:**
- Writes context packets to `.claude/context/*.json`
- Uses sub-agents for parallel directory exploration via PTC
- Opus-level synthesis of sub-agent results into structured packets
- Direct peer messaging with any agent that needs context
- Schema validates packets before delivery

**MCP:** GitHub MCP (github-personal) for repository history. Fallback: `gh` CLI, `git` CLI.

**HARD-GATE constraints:**
```xml
<HARD-GATE>Never write source code or test files — only context packets</HARD-GATE>
<HARD-GATE>Never synthesize context from memory — always read actual files</HARD-GATE>
```

**Mandatory Think points:**
- At SCOPE_ANALYSIS exit: exploration strategy (targeted vs comprehensive), sub-agent allocation

### 2d. Researcher — `agents/researcher.md`

**Model:** Sonnet 4.6
**State machine:** `state-machines/researcher.json`
**Lifecycle:** Ephemeral. One query or related batch. Fresh instance for unrelated queries.

**Identity:** External information source. Uses Context7 MCP (primary) with WebSearch fallback. Sub-agents do actual MCP/web calls; Sonnet synthesizes results. All research is persistent and searchable — future queries search existing research first before dispatching new sub-agents.

**Key behaviors:**
- Writes to `.claude/research/` with confidence scores
- Checks existing research before dispatching sub-agents (deduplication)
- Confidence scoring: HIGH (>0.85, multiple corroborating sources), MEDIUM (0.6-0.85, single authoritative source), LOW (<0.6, inferred or partial)
- Direct peer messaging with any agent

**MCP:** Context7 (primary), WebSearch (fallback)

**HARD-GATE constraints:**
```xml
<HARD-GATE>Never present findings without confidence scores</HARD-GATE>
<HARD-GATE>Never skip existing research check — always search before dispatching</HARD-GATE>
```

**Mandatory Think points:**
- At QUERY_ANALYSIS exit: source reliability assessment, MCP vs WebSearch routing
- At SYNTHESIS exit: confidence scoring, cross-source corroboration

### 2e. Coder — `agents/coder.md`

**Model:** Opus 4.6 (Tier 2-3 tasks) or Sonnet 4.6 (Tier 1 mechanical tasks)
**State machine:** `state-machines/coder.json`
**Lifecycle:** Ephemeral. One task, one life. Fresh context per task. Handoff on context pressure.

**Identity:** TDD implementation agent. Works in isolated worktree with allocated port range. Follows the Iron Law: NO production code without a failing test first. State machine enforces write permissions — TEST_DESIGN allows only test files, IMPLEMENTATION allows only source files.

**Key behaviors:**
- On spawn: reads task `exploration_queries` and `research_queries`, dispatches explorer/researcher for fresh context
- TDD cycle: test design -> tests written -> TDD red (verify failure) -> red verified -> implementation -> TDD green (verify pass) -> invariant check -> quality gate
- RED phase failure with max retries exhausted -> SCRAP_RETRY (handoff summary of what went wrong and what NOT to repeat)
- Cannot modify test assertions during IMPLEMENTATION state (hook-enforced via `write_globs`)
- Micro-commits at state transitions
- Quality gate must pass before TASK_REVIEW_REQUESTED
- Autonomous — no mid-implementation approvals

**MCP:** None. Requests information via orchestrator (dispatches explorer/researcher).

**HARD-GATE constraints:**
```xml
<HARD-GATE>No production code without a failing test first — Iron Law</HARD-GATE>
<HARD-GATE>Cannot modify test assertions during IMPLEMENTATION state</HARD-GATE>
<HARD-GATE>Must run quality gate before claiming task complete</HARD-GATE>
<HARD-GATE>If code exists before tests: DELETE IT. No keeping as "reference"</HARD-GATE>
```

**Mandatory Think points:**
- At CONTEXT_LOADED (before designing tests): test strategy, what to test, edge cases
- At RED_VERIFIED (before implementing): implementation approach, data structures, API design
- At FIXES (before addressing auditor feedback): understanding critique, minimal fix strategy
- Before SCRAP_RETRY: what went wrong, what NOT to repeat

### 2f. Tester — `agents/tester.md`

**Model:** Opus 4.6
**State machine:** `state-machines/tester.json`
**Lifecycle:** Persistent within a phase. Handoff on context pressure.

**Identity:** Blind scenario tester. Works from design doc + plan + context packets only — never sees coder implementation or coder tests. Collaborates with user on scenario planning while coders implement. Implements scenarios via Haiku sub-agents (separation of concerns).

**Key behaviors:**
- Pass D: Four test tiers:
  - Tier 1: Golden path (5-10 scenarios)
  - Tier 2: Edge cases (10-20 scenarios)
  - Tier 3: Adversarial (5-15 scenarios)
  - Tier 4: Property-based validation (invariants, idempotency, monotonicity)
- Sends bug descriptions to coders on failure (no test code leaked — blind wall maintained)
- After 2 deny cycles -> orchestrator escalates to auditor arbitration
- Scenarios execute during PHASE_SCENARIO_EXECUTION (after coders merge)

**MCP:** None.

**HARD-GATE constraints:**
```xml
<HARD-GATE>Never read coder source files or coder test files — blind wall</HARD-GATE>
<HARD-GATE>Never send test code to coders — only bug descriptions</HARD-GATE>
<HARD-GATE>Must get user approval on scenario list before building</HARD-GATE>
```

**Mandatory Think points:**
- At SCENARIO_PLANNING exit: scenario completeness, tier assignment, coverage of design requirements
- At SCENARIO_REPORTING exit: failure classification (bug vs test issue), escalation decision

### 2g. Auditor — `agents/auditor.md`

**Model:** Opus 4.6
**State machines:** `state-machines/auditor-task.json`, `state-machines/auditor-phase.json`
**Lifecycle:** Task auditor is ephemeral (one review per instance). Phase auditor is per-phase (new instance mandatory at every phase boundary).

**Identity:** Adversarial reviewer. Four modes (one per invocation):
1. **Task review:** Spawns after each coder claims complete. Ephemeral. Forces fixes before merge. No user-facing summary.
2. **Phase review:** After all tasks + Pass D testing. Comprehensive review with independent exploration. Writes full report for user.
3. **Hardening:** Production readiness review at FINAL_AUDIT.
4. **Arbitration:** Tester-coder deadlock after 2 deny cycles. Issues binding ruling.

**Key behaviors:**
- Two-stage review: (1) spec compliance first — did they build what was asked? (2) Code quality second — is what they built well-constructed?
- "Do Not Trust the Report" — reads actual code, not agent claims. Verifies independently.
- Categorizes issues: Critical (must fix), Important (should fix), Minor (nice to have)
- Each issue has file:line reference, what is wrong, why it matters, how to fix
- Override mechanism: can request elevated permissions with logged justification

**MCP:** None.

**HARD-GATE constraints:**
```xml
<HARD-GATE>Must read actual code — never trust agent reports or claims</HARD-GATE>
<HARD-GATE>Must complete spec compliance review before code quality review</HARD-GATE>
<HARD-GATE>Must not say "looks good" or "looks reasonable" without file:line evidence</HARD-GATE>
<HARD-GATE>New instance mandatory at every phase boundary — no carryover</HARD-GATE>
```

**Mandatory Think points:**
- At CODE_REVIEW exit (task): spec compliance assessment, issue list
- At FIXES_VERIFIED exit (task): re-review completeness, remaining issues
- At ARTIFACT_REVIEW exit (phase): cross-task pattern detection
- At INDEPENDENT_EXPLORATION exit (phase): issues not caught by task auditors
- At RULING exit (phase): adherence rulings, plan vs implementation nuance
- Before arbitration ruling: both sides' arguments, binding decision rationale

### 2h. Generalist — `agents/generalist.md`

**Model:** Opus 4.6
**State machine:** None. No state machine enforcement.
**Mode:** `bypassPermissions`
**Lifecycle:** On-demand. Terminated after task.

**Identity:** Unrestricted ad-hoc agent for tasks that do not fit other roles. Full filesystem access, no validation hooks, maximum permissions. Only agent that uses `bypassPermissions` mode. Used sparingly — the user's personal tool for anything outside the workflow.

**Key behaviors:**
- Dynamically loads skills from `.claude/skills/` based on task
- No state machine gating — all tools always available
- No write restrictions
- No mandatory Think points (discretionary usage encouraged)
- Not part of workflow state machine tracking

**MCP:** All available MCP servers.

**HARD-GATE constraints:**
```xml
<HARD-GATE>Must log all actions to decision log — unrestricted does not mean unobserved</HARD-GATE>
```

---

## 3. Per-Role Hook Configuration

### 3.1 Current State: Universal Dispatch

The current `post_tool_use.py` dispatch table (lines 488-508) runs the same handlers for all agents:

```python
_TOOL_HANDLERS = {
    "Think": [handle_think],
    "SendMessage": [handle_send_message],
    "Write": [handle_write_validate, handle_write_lint, handle_write_track, handle_write_event],
    "Edit": [handle_write_validate, handle_write_lint, handle_write_track, handle_write_event],
    "Read": [handle_read_skill],
    "Task": [handle_task_completion],
}

_ALWAYS_HANDLERS = [handle_update_context, handle_context_pressure, handle_annotations]
```

### 3.2 Target: Role-Filtered Dispatch

Add role awareness to the dispatch table. The `HookContext` already has `agent_id` which maps to an agent state file containing `role`. Add a `role` field to `HookContext` and filter handlers accordingly.

| Role | Tool handlers active | Always handlers active | Rationale |
|---|---|---|---|
| **Coder** | Think, Write(validate+lint+track+event), Edit(validate+lint+track+event), Read(skill) | update_context, context_pressure, annotations | Full validation — writes production code |
| **Explorer** | Think, Write(validate+track+event), Read(skill) | update_context, context_pressure | No lint (writes JSON, not Python source). Validate for schema checks. |
| **Researcher** | Think, Write(validate+track+event), Read(skill) | update_context, context_pressure | Same as explorer — writes JSON research files |
| **Orchestrator** | Think, SendMessage, Task(completion) | update_context, context_pressure, annotations | Communication tracking. No write handlers (never writes files). |
| **Tester** | Think, Write(validate+lint+track+event), Edit(validate+lint+track+event), Read(skill) | update_context, context_pressure | Validate test files, lint test Python |
| **Auditor** | Think, Write(validate+track+event), Read(skill) | update_context, context_pressure | Validate report files, no lint (writes reports, not source) |
| **Strategist** | Think, Write(validate+track+event), Read(skill) | update_context, context_pressure | Validate plan files, no lint (writes plans, not source) |
| **Generalist** | All handlers | All always handlers | Unrestricted |

### 3.3 Implementation

Add to `HookContext.from_hook_input()`:

```python
@classmethod
def from_hook_input(cls, raw: dict) -> HookContext:
    # ... existing code ...
    ctx = cls(...)
    ctx.role = _resolve_agent_role(ctx.agent_id)
    return ctx
```

Add role-filtered dispatch:

```python
_ROLE_HANDLER_OVERRIDES: dict[str, dict[str, list[Handler]]] = {
    "explorer": {
        "Write": [handle_write_validate, handle_write_track, handle_write_event],
        "Edit": [handle_write_validate, handle_write_track, handle_write_event],
    },
    "researcher": {
        "Write": [handle_write_validate, handle_write_track, handle_write_event],
        "Edit": [handle_write_validate, handle_write_track, handle_write_event],
    },
    "auditor": {
        "Write": [handle_write_validate, handle_write_track, handle_write_event],
        "Edit": [handle_write_validate, handle_write_track, handle_write_event],
    },
    "strategist": {
        "Write": [handle_write_validate, handle_write_track, handle_write_event],
        "Edit": [handle_write_validate, handle_write_track, handle_write_event],
    },
    "orchestrator": {
        "Write": [],  # orchestrator never writes
        "Edit": [],
    },
}

_ROLE_ALWAYS_OVERRIDES: dict[str, list[Handler]] = {
    "explorer": [handle_update_context, handle_context_pressure],
    "researcher": [handle_update_context, handle_context_pressure],
}
```

In `main()`, resolve handlers with role override:

```python
role = ctx.role or "generalist"
tool_handlers = _ROLE_HANDLER_OVERRIDES.get(role, {}).get(ctx.tool_name)
if tool_handlers is None:
    tool_handlers = _TOOL_HANDLERS.get(ctx.tool_name, [])
```

---

## 4. Model Dispatch

Model assignment is determined at agent spawn time by the orchestrator based on the role and, for coders, the task complexity tier from the plan.

| Role | Model | Context window | Rationale |
|---|---|---|---|
| Orchestrator | Opus 4.6 | 200K | Complex coordination, multi-agent reasoning, phase transition decisions |
| Strategist | Opus 4.6 | 200K | Architectural reasoning, dependency analysis, chunk boundary decisions |
| Coder (Tier 2-3) | Opus 4.6 | 200K | Non-trivial implementation requiring judgment, TDD design |
| Coder (Tier 1) | Sonnet 4.6 | 200K | Mechanical/boilerplate tasks with prescriptive plans |
| Tester | Opus 4.6 | 200K | Adversarial scenario design requires strong reasoning |
| Auditor | Opus 4.6 | 200K | Review quality is critical; must catch subtle issues |
| Explorer | Sonnet 4.6 | 200K | Mechanical file analysis, high throughput, synthesis is structured |
| Researcher | Sonnet 4.6 | 200K | Information gathering and synthesis, not deep reasoning |
| Generalist | Opus 4.6 | 200K | Unrestricted tasks need the strongest model |

### 4.1 Model Selection in subagent_start.py

The orchestrator specifies the model in the Task tool's prompt. The `subagent_start.py` hook reads the model from the agent state file (written by SessionStart) and includes it in the identity section:

```
=== WORKFLOW CONTEXT ===
Spawned by: orchestrator (state: PHASE_IMPLEMENTATION)
Your role: coder | Model: opus-4-6 | Task: t3 (Tier 2)
```

### 4.2 Cost Implications

| Model | Input cost/MTok | Output cost/MTok |
|---|---|---|
| Opus 4.6 | $15 | $75 |
| Sonnet 4.6 | $3 | $15 |

Using Sonnet 4.6 for explorers and researchers saves 5x on input and output costs for the highest-throughput agents. Tier 1 coder downgrade to Sonnet saves cost on mechanical tasks without quality risk (the plan is prescriptive — no judgment needed).

---

## 5. Anti-Rationalization Hardening

Per-role rationalization tables populated from `superpowers-synthesis.md` pressure testing and `agentic_workflow_design.md` anti-rationalization section.

### 5.1 Coder Rationalization Table

| Excuse | Reality |
|---|---|
| "I'll write the test after" | Tests-after pass immediately, proving nothing. Test-first forces you to see the failure. |
| "The test is trivial so I'll skip it" | Simple code breaks. Test takes 30 seconds. No exceptions. |
| "Let me just fix this small thing first" | Scope creep. Implement ONLY what the task specifies. Report other issues to orchestrator. |
| "The plan says X but Y is clearly better" | Follow the plan. If it is wrong, report back to orchestrator for a plan revision. |
| "This test is hard to write, I'll implement first" | Iron Law. No production code without failing test first. |
| "I already manually tested it" | Ad-hoc is not systematic. No record, cannot re-run. |
| "Keep as reference, write tests first" | You will adapt it. That is testing after. Delete means delete. |
| "Need to explore first" | Fine. Throw away exploration, start with TDD. |
| "It's about spirit not ritual" | Violating the letter IS violating the spirit. |

**Red flags (immediate STOP):**
- Code written before test
- Test passes immediately on first run
- "I already manually tested it"
- "This is different because..."
- Rationalizing "just this once"
- "probably", "should work", "I think", "assuming"
- "Done!" without verification evidence

### 5.2 Auditor Rationalization Table

| Excuse | Reality |
|---|---|
| "The code looks reasonable" | Read it line by line. "Looks reasonable" is not a review. |
| "I trust the coder's judgment" | You are the adversarial check. Trust nothing. Verify everything. |
| "This is a minor issue" | Categorize it. Minor issues still get logged with file:line references. |
| "The tests pass so it must be correct" | Tests can be wrong, incomplete, or testing the wrong thing. Read the tests. |
| "The coder already fixed this in a previous review" | Verify the fix exists in the current code. Do not trust claims of past work. |
| "Great work overall" | Anti-sycophancy violation. State findings factually. No praise. |

**Red flags (immediate STOP):**
- Approving without reading actual source files
- Using words like "looks good", "seems fine", "reasonable"
- Skipping spec compliance to go directly to code quality
- Accepting a report at face value
- Expressing satisfaction before completing review

### 5.3 Tester Rationalization Table

| Excuse | Reality |
|---|---|
| "The coder already tested this" | You test from the spec, not the implementation. Blind wall. |
| "This edge case is unlikely" | Unlikely edge cases cause production failures. Include it. |
| "Property-based testing is overkill here" | If invariants exist, they must be tested. Tier 4 is not optional. |
| "I can infer the implementation approach" | You work from design doc + plan only. Do not infer implementation details. |
| "The golden path covers this" | Golden path tests expected behavior. Edge cases test unexpected behavior. Different. |

**Red flags (immediate STOP):**
- Reading coder source files or coder test files
- Sending test code (not bug descriptions) to coders
- Skipping Tier 3 (adversarial) or Tier 4 (property-based) scenarios
- Assuming implementation details when designing scenarios
- "This is probably fine"

### 5.4 Orchestrator Rationalization Table

| Excuse | Reality |
|---|---|
| "I'll just check this file quickly" | You never read content files. Dispatch an explorer. |
| "The agent is probably done, I'll shut it down" | No shutdown without user approval. No exceptions. |
| "This agent has been idle, something is wrong" | Idle is normal (10-20s turn boundaries). Wait 3+ minutes, check files. |
| "We can skip the auditor for this simple task" | Every task gets audited. The auditor catches what you cannot. |
| "The phase is mostly done, let's move on" | All tasks complete, all audits passed, all merges successful. No shortcuts. |

**Red flags (immediate STOP):**
- Reading source code or test files directly
- Dispatching coders before PLAN_READY state
- Skipping user approval at phase boundaries
- Shutting down agents without user confirmation
- "probably", "mostly", "good enough"

---

## 6. Mandatory Think Points

Mapped from state machine `think_on_exit: true` flags and `requires_think: true` transitions.

### 6.1 Think Output Structure

Every mandatory Think point produces:

```
THOUGHT: [what was being decided]
CONSIDERED: [options/factors weighed — at least 2 alternatives]
CHOSEN: [decision made + rationale for choice + rationale against alternatives]
```

### 6.2 Per-Role Think Map

**Orchestrator** (from `system.json`):
| State/Transition | Think about |
|---|---|
| EXPLORING exit | Was exploration sufficient? Re-explore or proceed to strategy? |
| STRATEGIZING exit | Is the plan complete? Any gaps in the plain-text plan? |
| PHASE_ACTIVE exit | Task assignment strategy: parallel vs sequential, model selection per task |
| PHASE_IMPLEMENTATION -> PHASE_TASKS_COMPLETE | Are all tasks truly complete? Any outstanding merges or audits? |
| PHASE_REMEDIATION exit | Are fixes adequate? Another remediation cycle or escalate to arbitration? |
| PHASE_ARBITRATION exit | Accept arbitration ruling? Dispatch accordingly. |
| PHASE_REPORT exit | Phase quality assessment before presenting to user |
| PHASE_COMMITTED -> next phase | Readiness for next phase. Lessons from current phase. |
| PHASE_COMMITTED -> FINAL_AUDIT | All phases complete assessment. Hardening scope. |
| FINAL_AUDIT exit | Final production readiness. Ship or remediate? |

**Strategist** (from `strategist.json`):
| State/Transition | Think about |
|---|---|
| DESIGN_INGESTION exit | Requirements completeness, ambiguities to resolve |
| PHASE_BREAKDOWN exit | Chunk boundaries, phase dependencies, phase sizing |
| TASK_BREAKDOWN exit | touched_functions overlap, priority ordering, parallelization safety |
| TASK_DETAILING exit | Exploration/research query completeness per task |
| TEST_PLANNING exit | Pass A/B/C coverage, invariant completeness |

**Coder** (from `coder.json`):
| State/Transition | Think about |
|---|---|
| CONTEXT_LOADED exit | Test strategy: what to test, edge cases, data fixtures needed |
| RED_VERIFIED exit | Implementation approach: data structures, algorithms, API design |
| FIXES exit | Understanding auditor critique, minimal fix strategy |
| TDD_GREEN -> IMPLEMENTATION (retry) | Why tests still fail, what to change, is the approach viable |
| INVARIANT_CHECK -> IMPLEMENTATION (retry) | Which invariant failed, root cause, minimal fix |
| QUALITY_GATE -> IMPLEMENTATION (retry) | Gate failure analysis, targeted fix |

**Tester** (from `tester.json`):
| State/Transition | Think about |
|---|---|
| SCENARIO_PLANNING exit | Scenario completeness, tier assignment, coverage of requirements |
| SCENARIO_REPORTING exit | Failure classification: bug vs test issue, escalation decision |
| SCENARIO_EXECUTION -> SCENARIO_REPORTING | Initial results interpretation before formal reporting |

**Auditor — Task** (from `auditor-task.json`):
| State/Transition | Think about |
|---|---|
| CODE_REVIEW exit | Spec compliance findings, issue severity classification |
| FIXES_VERIFIED exit | Are fixes complete? Remaining issues? Approve or another cycle? |

**Auditor — Phase** (from `auditor-phase.json`):
| State/Transition | Think about |
|---|---|
| ARTIFACT_REVIEW exit | Cross-task patterns, systemic issues, missed requirements |
| INDEPENDENT_EXPLORATION exit | Issues not caught by task auditors, architectural concerns |
| RULING exit | Adherence rulings, plan vs implementation nuance, binding decisions |

**Explorer** (from `explorer.json`):
| State/Transition | Think about |
|---|---|
| SCOPE_ANALYSIS exit | Exploration strategy (targeted vs comprehensive), sub-agent allocation plan |
| SUB_AGENT_DISPATCH -> SYNTHESIS | Sub-agent result quality, synthesis approach, any gaps |

**Researcher** (from `researcher.json`):
| State/Transition | Think about |
|---|---|
| QUERY_ANALYSIS exit | Source reliability, MCP vs WebSearch routing, confidence requirements |
| SYNTHESIS exit | Cross-source corroboration, confidence scoring, gaps in findings |

---

## 7. Handoff-Over-Compaction

### 7.1 Core Principle

Agents ALWAYS hand off rather than compact. Compaction (context compression) loses workflow state — state machine position, pending decisions, partially written files, Think tool deliberation history. A fresh agent with a structured handoff file outperforms a compacted agent with degraded context.

### 7.2 Context Pressure Thresholds

From `hooks/utils/context_monitor.py`:

| Level | Threshold | Action |
|---|---|---|
| Normal | <65% | No action |
| Warning | 65% | Log warning. Agent begins wrapping up current step. |
| Critical | 90% | Agent writes handoff file and terminates. No further work. |

### 7.3 Handoff Protocol

1. Agent detects critical context pressure via `handle_context_pressure` in PostToolUse
2. Agent writes structured handoff to `.claude/handoffs/{agent-id}.json`:
   - Current state machine state
   - Key decisions made (from decision log)
   - Files modified and their status
   - Test results (pass/fail counts)
   - Unresolved questions
   - What NOT to repeat (for SCRAP_RETRY specifically)
3. Agent sends `handoff` message to orchestrator via SendMessage
4. Agent terminates
5. Orchestrator spawns fresh agent with handoff context injected by `subagent_start.py`

### 7.4 Exception: Orchestrator

The orchestrator is the only agent that uses compaction as a last resort, because:
- It is the main session — spawning a "replacement orchestrator" is not possible
- PreCompact hook saves workflow state as a safety net
- User can manually trigger handoff by starting a new session

### 7.5 Per-Role Handoff Content

| Role | Handoff includes | Handoff excludes |
|---|---|---|
| Coder | Task ID, current TDD state, files written, test results, worktree path | Raw file contents (fresh coder reads them) |
| Tester | Approved scenario list, execution results, current cycle count | Scenario source code (in files) |
| Strategist | Planning state, completed sections, user feedback received | Full plan text (in files) |
| Auditor (phase) | Review progress, rulings issued so far, remaining artifacts to review | Full report (not yet written) |
| Explorer | Query, exploration progress, packets written so far | Raw file contents (sub-agents re-read) |
| Researcher | Query, sources checked, confidence assessment so far | Raw search results (sub-agents re-fetch) |

---

## 8. Token Budget

### 8.1 Per-Component Budget

| Component | Target | Actual | Source |
|---|---|---|---|
| Universal CLAUDE.md | ~800 tokens | ~800 tokens (after trim) | Doc 0 Section 6 |
| Per-role spec (`agents/{role}.md`) | ~1,200 tokens | ~1,200 tokens | This doc |
| Skill metadata (all skills) | ~150 tokens | ~150 tokens | Doc 3 Section 1.1 |
| SubagentStart injection | <1,500 tokens | ~1,000 tokens | Doc 0 Section 6 |
| Total per-agent overhead | ~3,150-3,650 tokens | Projected | Sum of above |

### 8.2 Before/After Comparison

**Before (v1):**

| Role | CLAUDE.md | Skills (loaded) | Spawn injection | Total |
|---|---|---|---|---|
| Coder | 1,900 | 14,700 (3 skills) | 750 | **17,350** |
| Orchestrator | 1,900 | 4,900 (1 skill) | 750 | **7,550** |
| Explorer | 1,900 | 4,900 (1 skill) | 750 | **7,550** |

**After (v2, with Doc 3 + Doc 5):**

| Role | CLAUDE.md | Role spec | Skill meta | Skill body (typical) | Spawn injection | Total | Reduction |
|---|---|---|---|---|---|---|---|
| Coder | 800 | 1,200 | 150 | 5,000 | 1,000 | **8,150** | 53% |
| Orchestrator | 800 | 1,200 | 150 | 3,000 | 1,000 | **6,150** | 19% |
| Explorer | 800 | 1,200 | 150 | 2,500 | 1,000 | **5,650** | 25% |
| Researcher | 800 | 1,200 | 150 | 4,500 | 1,000 | **7,650** | -7%* |
| Strategist | 800 | 1,200 | 150 | 5,000 | 1,000 | **8,150** | 35% |
| Tester | 800 | 1,200 | 150 | 5,500 | 1,000 | **8,650** | 31% |
| Auditor | 800 | 1,200 | 150 | 2,000 | 1,000 | **5,150** | 32% |
| Generalist | 800 | 1,200 | 150 | 0** | 1,000 | **3,150** | N/A |

*Researcher sees minimal reduction because v1 research skills were already compact. Acceptable per Doc 0 Section 1.1.
**Generalist loads skills on demand, not at spawn.

### 8.3 Effective Savings

The key metric is not total tokens but **relevant** tokens:
- V1: 1,900 CLAUDE.md tokens with ~760 wasted (40% irrelevant per role)
- V2: 2,000 tokens (800 CLAUDE.md + 1,200 role spec) with 0 wasted

Every token in the agent's context is now relevant to its role. The 760-token effective savings per agent across 8+ concurrent agents is significant.

---

## 9. Integration Points

| File | Action | Purpose |
|---|---|---|
| `agents/orchestrator.md` | **New** | Orchestrator behavioral spec (~1,200 tokens) |
| `agents/strategist.md` | **New** | Strategist behavioral spec |
| `agents/explorer.md` | **New** | Explorer behavioral spec |
| `agents/researcher.md` | **New** | Researcher behavioral spec |
| `agents/coder.md` | **New** | Coder behavioral spec |
| `agents/tester.md` | **New** | Tester behavioral spec |
| `agents/auditor.md` | **New** | Auditor behavioral spec (4 modes) |
| `agents/generalist.md` | **New** | Generalist behavioral spec |
| `CLAUDE.md` | **Modify** | Trim to universal-only content (~800 tokens) |
| `hooks/subagent_start.py` | **Modify** | Inject role-specific spec from `agents/{role}.md` |
| `hooks/post_tool_use.py` | **Modify** | Add `role` to `HookContext`, add `_ROLE_HANDLER_OVERRIDES`, filter dispatch |
| `schemas/agent_state.py` | **No change** | `AgentRole` enum already has all 8 roles |
| `state-machines/*.json` | **No change** | Already define `think_on_exit` and `requires_think` per state/transition |

---

## 10. Verification Criteria

- [ ] Universal CLAUDE.md trimmed to ~800 tokens (measured by `scripts/token_budget_check.py`)
- [ ] All 8 agent spec files exist at `agents/{role}.md`
- [ ] Each agent spec is under 80 lines and ~1,200 tokens
- [ ] Each agent spec contains: Identity, Key behaviors, MCP, HARD-GATE constraints, Mandatory Think points
- [ ] `subagent_start.py` injects role-specific spec content at spawn
- [ ] `subagent_start.py` injection stays within MAX_CONTEXT_CHARS (3,000 chars)
- [ ] `post_tool_use.py` dispatches handlers based on agent role
- [ ] Explorer and researcher skip `handle_write_lint` (no Python source writes)
- [ ] Orchestrator skips all write handlers (never writes files)
- [ ] Coder gets full validation chain (validate + lint + track + event)
- [ ] Rationalization tables present in coder, auditor, tester, and orchestrator specs
- [ ] Red flags lists present in all 4 specs above
- [ ] Think points in agent specs match `think_on_exit`/`requires_think` in state machine JSON files
- [ ] Context pressure thresholds (65% warning, 90% critical) documented and match `context_monitor.py`
- [ ] Handoff protocol matches existing `handle_context_pressure` in `post_tool_use.py`
- [ ] Per-role token budget projections verified by `scripts/token_budget_check.py`
- [ ] No regression: all existing PostToolUse handlers still fire for roles that need them
- [ ] Model dispatch table matches `AgentModel` literal in `schemas/agent_state.py`
