# Session 4 Handoff — Agent Definition .md Files (Continued)

**Date:** 2026-03-20
**Status:** Phase A — 8 of 9 sub-agent .md files complete. 1 existing .md revised. 0 new skills created.
**Previous handoffs:** `SESSION-3-HANDOFF.md`, `SESSION-2-HANDOFF.md` (original 26 patterns), `IMPLEMENTATION-HANDOFF.md` (full project context)

---

## Required Skills — Load Before Writing Any .md File

Same as Sessions 2-3. Load all three skills with ALL reference files before writing anything.

### 1. Sub-Agent MD Writing Skill (for sub-agent .md files — Phase A)
```
Read: /home/kartik/personal/agentic_workflow/skills/sub-agent-md-writing/SKILL.md
Read: /home/kartik/personal/agentic_workflow/skills/sub-agent-md-writing/references/patterns.md
Read: /home/kartik/personal/agentic_workflow/skills/sub-agent-md-writing/references/anti-patterns.md
Read: /home/kartik/personal/agentic_workflow/skills/sub-agent-md-writing/references/complete-examples.md
Read: /home/kartik/personal/agentic_workflow/skills/sub-agent-md-writing/references/delegation-prompt-design.md
```

### 2. Agent MD Writing Skill (for teammate .md files — Phase B)
```
Read: /home/kartik/personal/agentic_workflow/skills/agent-md-writing/SKILL.md
Read: /home/kartik/personal/agentic_workflow/skills/agent-md-writing/references/patterns.md
Read: /home/kartik/personal/agentic_workflow/skills/agent-md-writing/references/anti-patterns.md
Read: /home/kartik/personal/agentic_workflow/skills/agent-md-writing/references/complete-examples.md
Read: /home/kartik/personal/agentic_workflow/skills/agent-md-writing/references/translation-walkthrough.md
```

### 3. Writing Skills Skill (general writing methodology)
```
Read: /home/kartik/personal/agentic_workflow/skills/writing-skills/SKILL.md
Read: /home/kartik/personal/agentic_workflow/skills/writing-skills/references/writing-patterns.md
Read: /home/kartik/personal/agentic_workflow/skills/writing-skills/references/anti-rationalization-architecture.md
```

---

## Design Source Documents

### Primary Design Documents (in `new_claude/agents/`)

| Document | Content | Read When |
|----------|---------|-----------|
| `AGENT-ARCHITECTURE.md` | 7 teammates, 9 sub-agents, 6 invariants, dispatch rules | Before writing ANY .md file |
| `SUB-AGENT-SPECS.md` | All 9 sub-agent specs: behavioral steps, contracts, validation, tools, failure modes | Primary source for optimizer .md (Phase A) |
| `TEAMMATE-DIRECTOR-PATTERNS.md` | Per-teammate extension points for all 6 teammates | Primary source for Phase B (teammate .md files) |
| `DELEGATION-RETURN-SCHEMAS.md` | Return types, shared sub-models | Reference for output contract sections |
| `BASE-DIRECTOR-PATTERN.md` | Universal teammate lifecycle (design reference, NOT runtime) | Phase B |

### V2 Skills (in `new_claude/skills/`)

Only use skills from `skills/` (top-level: ptc-sandbox, writing skills) and `new_claude/skills/` (all V2 operational skills). V1 skills in `.claude/skills/` are deprecated.

**Skills relevant to remaining work:**

| Skill | Path | Used By |
|-------|------|---------|
| `ptc-sandbox` | `skills/ptc-sandbox/SKILL.md` | ALL sub-agents |
| `code-design` | `new_claude/skills/code-design/SKILL.md` | optimizer (routing pointer, not frontmatter), implementer (frontmatter) |
| `task-handling` | `new_claude/skills/task-handling/SKILL.md` | Coder teammate, Tester teammate (Phase B) |
| `task-execution-impl` | `new_claude/skills/task-execution-impl/SKILL.md` | Implementer sub-agent |

**Skill that is NOW SUPERSEDED:**

| Old Skill | Superseded By | Status |
|-----------|--------------|--------|
| `new_claude/skills/task-execution/SKILL.md` | `task-handling` (directors) + `task-execution-impl` (implementer) | **Needs deletion or deprecation marker in Phase B** |

---

## Progress — What's Done

### Phase A: Sub-Agent .md Files (8 of 9 complete)

| # | File | Status | Skills Loaded | Key Design Notes |
|---|------|--------|---------------|-----------------|
| 1 | `sub-agents/codebase-scout.md` | ✅ Session 2 | ptc-sandbox, codebase-exploration | ~110 lines |
| 2 | `sub-agents/research-scout.md` | ✅ Session 2 | ptc-sandbox, research-methodology | ~120 lines |
| 3 | `sub-agents/plan-checker.md` | ✅ Session 2 | ptc-sandbox, plan-verification | ~110 lines |
| 4 | `sub-agents/audit-checker.md` | ✅ Session 2, modified Session 3 | ptc-sandbox, code-review | ~165 lines. Fraudulent vs misaligned tests in step 4 |
| 5 | `sub-agents/test-writer.md` | ✅ Session 3, **revised Session 4** | ptc-sandbox, test-design | ~145 lines. Now single-parent (Coder only). Targeted mode = audit test fixes |
| 6 | `sub-agents/implementer.md` | ✅ Session 3 | ptc-sandbox, code-design, task-execution-impl | ~143 lines |
| 7 | `sub-agents/scenario-writer.md` | ✅ Session 3 | ptc-sandbox, test-design | ~174 lines. code-design is routing pointer, not frontmatter |
| 8 | `sub-agents/debugger.md` | ✅ **Session 4** | ptc-sandbox, debugging | ~145 lines. **Opus model.** code-design as routing pointer. WebSearch/WebFetch enabled |
| 9 | `sub-agents/optimizer.md` | ❌ Not started | ptc-sandbox | code-design as routing pointer. Ephemeral worktree, compare-and-discard |

### Revision to Existing File

**`sub-agents/test-writer.md`** — 5 targeted edits to convert from multi-parent (Coder + Tester) to single-parent (Coder only):

1. **Description:** "infrastructure needs repair" → "tests need fixes based on audit findings"
2. **Routing table:** `targeted` row — goal, read-first, and success criterion now reference audit findings instead of infrastructure repair
3. **Targeted mode validation table:** Replaced `pytest --collect-only` infrastructure checks with audit-fix validation (resolve findings, check regressions, handle ambiguity)
4. **Boundaries:** "Don't rewrite logic" → "Don't expand scope" — scoped to audit finding file:line references
5. **Failure table:** "infrastructure unfixable" → "audit finding unclear or unfixable"

### New File

**`sub-agents/debugger.md`** (~145 lines) — Hypothesis-driven bug investigator:
- **Opus model** — final automated barrier before human escalation, needs deep reasoning for open-ended investigation
- **2 skills** (ptc-sandbox, debugging) — code-design as routing pointer for architectural root causes (~20% of dispatches)
- **WebSearch/WebFetch** enabled — external causes (library quirks, API contract changes) are a real debugger need
- **Multi-parent:** Coder (implementer stuck) and Auditor (unclear finding investigation)
- **INV-1:** blind to test source, sees test results as behavioral evidence
- **No `stuck` status** — mapped to `partial` with exhausted-approaches `carry_forward` to comply with universal 3-status schema (completed/partial/failed)
- **No numeric stall threshold** — debugging skill's 3-strikes handles qualitative signal, daemon handles mechanical counting
- **code-design as routing pointer** (pattern #29) — most debugging is logic/data flow, not architectural mismatch

---

## Architecture Decisions Made This Session

### 1. Debugger Uses Opus Model

**Before (spec):** Sonnet (fixed), same as all sub-agents.
**After:** Opus — the only sub-agent using Opus.

**Why:** The debugger is the final automated barrier before human escalation. It does open-ended investigation requiring deep reasoning — synthesizing evidence across multiple hypotheses, understanding complex code interactions, and deciding when automated approaches are exhausted. The extra token cost per dispatch is justified because debugger dispatches are already high-stakes (the implementer already failed after 5 iterations).

### 2. Debugger Gets WebSearch/WebFetch

**Before (spec):** No WebSearch — "debugging is project-specific."
**After:** WebSearch and WebFetch enabled.

**Why:** The debugger hits cases where the root cause is external: library version quirks, framework behavioral changes, API contract mismatches, concurrency semantics. The implementer doesn't need WebSearch (its scope is plan execution). The debugger's scope is open-ended investigation — exactly the situation where searching "numpy broadcast rules for shape (3,) vs (3,1)" could resolve a stall that would otherwise escalate to the user.

### 3. `stuck` Status Mapped to `partial`

**Before:** SUB-AGENT-SPECS §8 defined 4 statuses: completed, partial, stuck, failed.
**After:** Body uses only 3 universal statuses (completed, partial, failed). The `stuck` concept is preserved as a sub-case of `partial`, distinguished by `carry_forward` content.

**Why:** The SubagentStop hook validates against the 3 universal statuses from Common Conventions. A custom `stuck` status would fail validation. The semantic distinction (exhausted approaches vs incomplete work) is communicated through `carry_forward` content — "all hypotheses tested, evidence gathered, further automated investigation won't help" signals stall to the parent without needing a non-standard status value.

### 4. No Numeric Stall Threshold for Debugger

**Before (spec):** "Stall threshold: 3 hypothesis cycles."
**After:** No numeric threshold in the body.

**Why:** Stating "3 attempts" creates an anchoring effect — the agent treats 3 as a budget (rushing shallow hypotheses to use it up) or a ceiling (stopping at 3 even when hypothesis #3 just revealed productive new evidence). The debugging skill's 3-strikes rule handles the qualitative signal ("your mental model is wrong") which is the actual stop condition. The daemon handles mechanical stall counting. The body doesn't need to restate either.

### 5. Tester Does Not Need Debugger

**Question explored:** Should the Tester dispatch the debugger when scenario tests have bugs?
**Decision:** No — the current architecture handles it.

**Why:** Scenario tests are structurally simple — they call public APIs with known inputs and assert on outputs. They don't have complex internal state, concurrency, or deep dependency chains. The failure modes they actually hit are: infrastructure (collect-only catches, scenario-writer self-fixes), wrong assertions (audit-checker catches, test-writer fixes), flaky environment issues (Tester investigates via PTC, dispatches test-writer). A scenario test with a genuine hard-to-debug logic bug would mean the test is overengineered — a design problem, not a debugging problem.

### 6. code-design as Routing Pointer for Debugger and Optimizer

**Applied pattern #29 (on-demand skill loading) to both agents.**

**For debugger:** Most debugging is logic errors, data flow issues, or concurrency problems (~80%). Architectural bugs requiring code-design knowledge are ~15-20%. Routing pointer saves ~244 lines of cold-start tokens on the majority of dispatches.

**For optimizer (planned):** Same reasoning. Most optimization targets are algorithmic or memory-related, handled by general engineering knowledge. Architectural restructuring for performance is the exception. Routing pointer, not frontmatter.

### 7. No Optimization Skill Needed

**Question explored:** Should we create a dedicated code-optimization skill before writing the optimizer .md?
**Decision:** No — write the body with optimization judgment inline.

**Why:**
- Claude already knows optimization techniques (Big O, memory patterns, cache locality) — these are general engineering knowledge, not a disciplined workflow agents skip without enforcement
- The judgment calls (when to stop, what's "marginal," compare-and-discard methodology) are sub-agent-specific — no other agent optimizes code, so there's no reuse case for a skill
- The skill creation HARD-GATE requires pressure testing with baseline scenarios showing the optimizer failing without the skill — we don't have that data
- If pressure testing later reveals consistent bad optimization decisions, the skill can be created then with real failure data

---

## Patterns From This Session — Apply to Remaining Files

All 32 patterns from Sessions 2-3 still apply. These additional patterns were established this session:

### 33. Opus for Final-Barrier Sub-Agents
When a sub-agent is the last automated line of defense before human escalation, and its task requires open-ended investigation with deep reasoning (not bounded execution), Opus is justified despite the higher token cost. The debugger is the only current example. Apply this test: "If this sub-agent fails, does a human need to intervene?" AND "Does the task require synthesizing evidence across multiple hypotheses?" Both must be yes.

### 34. Map Non-Standard Statuses to Universal Schema
The SubagentStop hook validates 3 statuses: completed, partial, failed. Sub-agent-specific concepts (stuck, stalled, exhausted) should be mapped to `partial` with the distinction communicated through `carry_forward` content. The parent reads the semantic signal from the content, not from a custom status field. This keeps the hook simple and the schema universal.

### 35. Don't State Numeric Thresholds When Skills Handle Qualitative Signals
When a loaded skill already teaches the qualitative stop condition (debugging skill's 3-strikes: "your mental model is wrong"), and the daemon handles mechanical counting, the body should not anchor on a specific number. Numeric thresholds create anchoring effects — agents treat them as budgets or ceilings rather than using the qualitative signal the skill teaches.

### 36. No Skill Creation Without Pressure-Tested Failure Data
The writing-skills HARD-GATE requires baseline pressure scenarios before writing any skill content. When considering a new skill (e.g., code-optimization), if you can't demonstrate that the agent consistently fails without it, defer creation. General engineering knowledge that Claude already has doesn't need skill enforcement. Create skills for disciplined workflows that agents skip, not for domain knowledge they already possess.

### 37. WebSearch for Open-Ended Investigation Agents
Sub-agents with bounded, plan-execution tasks (implementer, test-writer) don't need WebSearch — their work is project-specific. Sub-agents with open-ended investigation tasks (debugger) benefit from WebSearch because root causes can be external (library behavior, framework quirks). Apply this test: "Could the root cause of this agent's task be outside the codebase?" If yes, WebSearch is justified.

---

## User Interaction Preferences (confirmed/new this session)

All preferences from Sessions 2-3 still apply. Additional:

- **Question skill assignments aggressively.** The user pushed back on code-design as frontmatter for the debugger (~20% use case) — same pattern #29 reasoning as scenario-writer in Session 3. Default assumption: routing pointer unless 80%+ test is clearly met.
- **Question whether infrastructure handles something before putting it in the body.** The user caught that progress-based stall tracking is daemon/think-prompt territory, not body content. Apply the State Machine Narrator test from anti-patterns.
- **Trace architectural questions through the full system.** When asked "does X need Y?", trace the full verification chain (who dispatches what, who catches what failure type, who fixes it) before answering. The Tester/debugger question was resolved by tracing the scenario test failure chain end-to-end.
- **Don't over-engineer for edge cases.** The user validated that complex scenario test bugs are rare enough to not warrant architectural changes. Apply the "is this something I should really be worried about?" test before proposing new architecture.

---

## What Still Needs to Be Done

### Phase A Remaining (1 sub-agent .md file)

| # | File | Source | Key Concerns | Likely Skills |
|---|------|--------|-------------|---------------|
| 9 | `sub-agents/optimizer.md` | SUB-AGENT-SPECS §9 | Ephemeral worktree, compare-and-discard. Single parent (Coder). INV-1: blind to tests. `no_optimization_found` as valid outcome. Before/after comparison for each optimization. | ptc-sandbox (frontmatter), code-design (routing pointer) |

**Before writing #9 (optimizer):**
1. Read SUB-AGENT-SPECS §9 (lines 1321-1471)
2. Read `new_claude/skills/code-design/SKILL.md` — understand what it covers for the routing pointer reference
3. Key design decisions already made this session:
   - code-design as routing pointer, not frontmatter (decision #6)
   - No optimization skill needed (decision #7)
   - Single parent (Coder only) — no multi-parent complexity
4. Present plan for user review, then write
5. Apply all 37 patterns from Sessions 2-4

**Optimizer-specific design notes:**
- The ephemeral worktree compare-and-discard pattern is unique to this agent — it's the only sub-agent that works in a disposable environment where all changes can be discarded without consequence
- `no_optimization_found: true` is a valid `completed` return — unlike other sub-agents, "I found nothing to do" is a legitimate and useful outcome
- Before/after comparison (`before_after` field) needs semantic quality guidance in the body — the hook validates structure, the body teaches what makes a good comparison
- Impact estimation (high/medium/low) needs calibration guidance — what counts as "high impact"?

### Phase B (6 teammate .md files) — After Phase A

Uses the agent-md-writing skill (not sub-agent-md-writing). Requires reading:
- `TEAMMATE-DIRECTOR-PATTERNS.md`
- `BASE-DIRECTOR-PATTERN.md`
- Each teammate's skills to audit for duplication

| Teammate | Key Concerns |
|----------|-------------|
| Explorer | Context reuse decisions, scout partitioning, model selection per scout |
| Researcher | Cache vs fresh research, depth calibration, MCP routing |
| Planner | Grounding completeness, phase boundaries, task granularity |
| Coder | Sub-agent orchestration (test-writer → implementer → debugger → optimizer), task-handling skill (NEW), audit response loop |
| Tester | Scenario strategy, scenario-writer dispatch, execution cycle, failure reporting to Coder |
| Auditor | Adversarial independence, audit-checker dispatch, arbitration mode, debugger dispatch for investigation |

Key task: the Coder teammate .md must load `task-handling` (new) instead of `task-execution` (old/deprecated).

### Phase C (orchestrator .md) — After Phase B

Custom writing, not mechanical. See `IMPLEMENTATION-HANDOFF.md`.

### Cleanup

- Mark or delete `new_claude/skills/task-execution/` as deprecated (superseded by task-handling + task-execution-impl)
- Update any cross-references in design docs that point to the old task-execution skill

---

## File Inventory — Everything Written or Modified This Session

### New Files

```
new_claude/agents/sub-agents/debugger.md              (~145 lines)
new_claude/agents/SESSION-4-HANDOFF.md                 (this file)
```

### Modified Files

```
new_claude/agents/sub-agents/test-writer.md  (5 targeted edits: single-parent conversion, targeted mode = audit fixes)
```

### Files NOT Modified (from prior sessions, still current)

```
new_claude/agents/sub-agents/codebase-scout.md    (Session 2)
new_claude/agents/sub-agents/research-scout.md    (Session 2)
new_claude/agents/sub-agents/plan-checker.md      (Session 2)
new_claude/agents/sub-agents/audit-checker.md     (Session 2 + Session 3 modification)
new_claude/agents/sub-agents/implementer.md       (Session 3)
new_claude/agents/sub-agents/scenario-writer.md   (Session 3)
```
