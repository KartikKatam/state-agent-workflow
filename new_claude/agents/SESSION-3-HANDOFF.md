# Session 3 Handoff — Agent Definition .md Files (Continued)

**Date:** 2026-03-19
**Status:** Phase A — 7 of 9 sub-agent .md files complete. 2 new skills created. 1 existing .md modified.
**Previous handoffs:** `SESSION-2-HANDOFF.md` (read that for the original 26 patterns), `IMPLEMENTATION-HANDOFF.md` (full project context)

---

## Required Skills — Load Before Writing Any .md File

Same as Session 2. Load all three skills with ALL reference files before writing anything.

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
| `SUB-AGENT-SPECS.md` | All 9 sub-agent specs: behavioral steps, contracts, validation, tools, failure modes | Primary source for each sub-agent .md |
| `TEAMMATE-DIRECTOR-PATTERNS.md` | Per-teammate extension points for all 6 teammates | Phase B (teammate .md files) |
| `DELEGATION-RETURN-SCHEMAS.md` | Return types, shared sub-models | Reference for output contract sections |
| `BASE-DIRECTOR-PATTERN.md` | Universal teammate lifecycle (design reference, NOT runtime) | Phase B |

### V2 Skills (in `new_claude/skills/`)

Only use skills from `skills/` (top-level: ptc-sandbox, writing skills) and `new_claude/skills/` (all V2 operational skills). V1 skills in `.claude/skills/` are deprecated.

**Skills created this session:**

| Skill | Path | Lines | Used By |
|-------|------|-------|---------|
| `task-handling` | `new_claude/skills/task-handling/SKILL.md` | 183 | Coder teammate, Tester teammate (Phase B) |
| `task-execution-impl` | `new_claude/skills/task-execution-impl/SKILL.md` | 148 | Implementer sub-agent |

Both have `references/patterns.md` and `references/anti-patterns.md`.

**Skills relevant to remaining sub-agents:**

| Skill | Path | Used By |
|-------|------|---------|
| `ptc-sandbox` | `skills/ptc-sandbox/SKILL.md` | ALL sub-agents |
| `debugging` | `new_claude/skills/debugging/SKILL.md` | debugger sub-agent |
| `code-design` | `new_claude/skills/code-design/SKILL.md` | implementer, optimizer; scenario-writer (routing pointer only) |
| `test-design` | `new_claude/skills/test-design/SKILL.md` | test-writer, scenario-writer |

**Skill that is NOW SUPERSEDED:**

| Old Skill | Superseded By | Status |
|-----------|--------------|--------|
| `new_claude/skills/task-execution/SKILL.md` | `task-handling` (directors) + `task-execution-impl` (implementer) | **Needs deletion or deprecation marker in Phase B** |

The old task-execution was written when the Coder implemented code directly. The Coder is now a director that delegates. task-handling covers the director's comprehension phase; task-execution-impl covers the implementer's scope discipline. The old skill should not be loaded by any agent.

---

## Progress — What's Done

### Phase A: Sub-Agent .md Files (7 of 9 complete)

| # | File | Status | Skills Loaded | Key Design Notes |
|---|------|--------|---------------|-----------------|
| 1 | `sub-agents/codebase-scout.md` | ✅ Session 2 | ptc-sandbox, codebase-exploration | ~110 lines |
| 2 | `sub-agents/research-scout.md` | ✅ Session 2 | ptc-sandbox, research-methodology | ~120 lines |
| 3 | `sub-agents/plan-checker.md` | ✅ Session 2 | ptc-sandbox, plan-verification | ~110 lines |
| 4 | `sub-agents/audit-checker.md` | ✅ Session 2, **modified Session 3** | ptc-sandbox, code-review | ~165 lines. Added "fraudulent vs misaligned tests" in step 4 |
| 5 | `sub-agents/test-writer.md` | ✅ Session 3 | ptc-sandbox, test-design | ~140 lines. **Needs revision — see Required Revisions below** |
| 6 | `sub-agents/implementer.md` | ✅ Session 3 | ptc-sandbox, code-design, task-execution-impl | ~143 lines |
| 7 | `sub-agents/scenario-writer.md` | ✅ Session 3 | ptc-sandbox, test-design | ~174 lines. code-design is routing pointer, not frontmatter |
| 8 | `sub-agents/debugger.md` | ❌ Not started | ptc-sandbox, debugging | Hypothesis-driven, ≥2 hypothesis log, stall threshold 3 |
| 9 | `sub-agents/optimizer.md` | ❌ Not started | ptc-sandbox, code-design (?) | Ephemeral worktree, compare-and-discard |

### Skills Created This Session

**`new_claude/skills/task-handling/`** (183-line body + 2 reference files)
- Director-level comprehension discipline for Coder and Tester teammates
- HARD-GATE: no delegation/strategy design until uncertainty declared and resolved
- 3-step workflow: Parse Assignment → Declare Uncertainty → Resolve Before Acting
- Role-specific field tables moved to references/patterns.md
- Declaration-to-delegation handoff bridge mapping know/don't-know to delegation prompt fields
- Time budget heuristic, async wait guidance, assignment update handling
- Scored A- (92/100) by user

**`new_claude/skills/task-execution-impl/`** (148-line body + 2 reference files)
- Executor-level scope discipline for implementer sub-agent
- Delegation prompt assessment (complete/incomplete/critical framework)
- Scope discipline decision trees (file boundary, behavior boundary)
- Deviation documentation format (decision/reason/impact, at-moment-of-deviation)
- Verification checklist (7 items before returning)
- Partial return guidance with full worked JSON example in patterns.md
- Targeted mode fix scope (allowed/not-allowed table, "trace to finding" test)
- Scored A- (92/100) by user

### Modification to Existing File

**`sub-agents/audit-checker.md`** — Added 9 lines to step 4 distinguishing "fraudulent tests" (structurally broken — tautology, mock echo) from "misaligned tests" (structurally sound but verify wrong behavior — test checks for different outcome than design requires). Includes concrete example: design says ValueError on None, test asserts empty list return. Both are major findings.

---

## Required Revisions Before Continuing

### test-writer.md — Update for Single-Parent Architecture

**Context:** During this session, the user decided the Tester teammate no longer dispatches test-writer for infrastructure repair. The scenario-writer handles its own infrastructure. This simplifies test-writer to single-parent (Coder only).

**Current state:** test-writer.md still describes `targeted` mode as infrastructure repair for the Tester ("Fix broken test INFRASTRUCTURE only — conftest.py issues, fixture problems, import errors, collection failures").

**Required change:** Revise `targeted` mode to be Coder-dispatched for audit-related test fixes instead:

- `tdd_chunk` (Coder, TDD red phase): Write new failing tests — unchanged
- `targeted` (Coder, audit fix): Fix test issues identified during audit review — the test equivalent of the implementer's targeted mode

The dispatch type routing table in step 2, the targeted mode collect-only table in step 4, and the "Don't rewrite logic in targeted mode" boundary all need updating to reflect Coder-dispatched audit fixes rather than Tester-dispatched infrastructure repair.

**The identity section and information barrier are unaffected.** Only the multi-parent references and targeted mode description change.

---

## Architecture Decisions Made This Session

These decisions shaped the files written. The next agent should understand them.

### 1. Test-Writer is Single-Parent (Coder Only)

**Before:** test-writer served Coder (TDD red) and Tester (infrastructure repair).
**After:** test-writer serves Coder only. Two dispatch types from the same parent:
- `tdd_chunk` — write new failing tests
- `targeted` — fix test issues from audit findings

**Why:** The Tester's infrastructure repair use case was an edge case. The scenario-writer handles its own collect-only failures. Removing the Tester as a parent simplifies the test-writer (no multi-parent routing) and the Tester (doesn't need to coordinate with test-writer).

### 2. Scenario-Writer Handles Own Infrastructure

**Before:** If scenario-writer's collect-only failed persistently, Tester dispatched test-writer.
**After:** Scenario-writer attempts self-fix (3 attempts), then returns partial if it can't fix.

**Why:** Extra coordination layer for an edge case. The scenario-writer has Write/Edit tools and its failure handling covers collect-only errors. If it truly can't fix, the Tester re-dispatches with more context — no need for a different sub-agent.

### 3. task-execution Skill Split

**Before:** One monolithic `task-execution` skill (221 lines) that assumed the Coder implemented code directly.
**After:** Two focused skills:
- `task-handling` (183 lines) — director-level comprehension for Coder/Tester teammates
- `task-execution-impl` (148 lines) — executor-level scope discipline for implementer sub-agent

**Why:** The Coder is now a director that delegates, not an implementer. The old skill mixed director concerns (parse task, resolve queries, handle audit feedback) with executor concerns (scope discipline, deviation documentation). Splitting lets each agent load only what applies to its role.

**Impact on Phase B:** When writing the Coder teammate .md, it loads `task-handling` instead of `task-execution`. The old `task-execution` skill at `new_claude/skills/task-execution/` should be deprecated or deleted.

### 4. Fraudulent vs Misaligned Test Detection

Added to audit-checker.md: a distinction between fraudulent tests (structurally broken — can't prove correctness) and misaligned tests (structurally sound but verify wrong behavior). Both are major findings.

**Why:** The user identified a gap: if the test-writer writes tests that check for wrong behavior (misreads the spec), those tests still fail during red phase (no implementation exists), so red verification passes. The implementer makes the wrong behavior pass. The audit-checker is the primary guard — it reads design, tests, AND source. The distinction ensures it catches both structural test problems (fraudulent) and semantic test problems (misaligned).

### 5. code-design as Routing Pointer for Scenario-Writer

**Before (initial draft):** code-design in frontmatter skills (loaded every invocation).
**After (critique revision):** code-design as on-demand routing pointer in the body.

**Why:** The 80% test — most scenario tasks produce straightforward test files. Complex multi-file infrastructure with factories/fixtures/helpers is the exception (~20%). Loading code-design (~244 lines) on every cold start wastes tokens for the common case. The body says "read code-design skill when you need concern decomposition for complex suites."

---

## Patterns From This Session — Apply to Remaining Files

All 26 patterns from SESSION-2-HANDOFF.md still apply. These additional patterns were established this session:

### 27. Single-Parent Simplification
When a sub-agent's multi-parent design has one dominant parent and one edge-case parent, simplify to single-parent. The edge-case can be handled by the sub-agent itself (self-fix + return partial) or by the primary parent with a different dispatch type.

### 28. Skill Split When Architecture Changes
When the agent that loads a skill changes role (implementer → director), the skill needs splitting, not just updating. Director concerns and executor concerns should not share a skill — they have different enforcement needs and different resolution paths.

### 29. On-Demand Skill Loading via Routing Pointers
When a skill is needed for <80% of invocations, don't put it in frontmatter. Add a routing pointer in the body: "For complex X, read the Y skill's Z before proceeding." Saves cold-start tokens while keeping the knowledge accessible.

### 30. Misaligned vs Fraudulent as Distinct Finding Categories
Fraudulent tests are structurally broken (can't prove correctness). Misaligned tests are structurally sound but verify wrong behavior. Both are major audit findings but require different detection approaches — fraud is detectable from test structure alone, misalignment requires comparing tests against the design document.

### 31. Verification Layer Independence
The scenario-writer's double-blind barrier (no source AND no unit tests) exists because scenario tests are the LAST verification layer. If they share blind spots with unit tests (by being able to read them), they can't catch the case where implementation and unit tests agree on wrong behavior. This principle should inform any future verification agent design.

### 32. Questioning Skill Assignments
Before assigning a skill, read it and ask: "Does every invocation of this sub-agent need this skill?" If the answer is "only for complex cases," use a routing pointer instead of frontmatter loading. The user specifically pushed back on unexamined skill assignments during this session.

---

## User Interaction Preferences (confirmed/new this session)

All preferences from SESSION-2-HANDOFF.md still apply. Additional:

- **Discuss architecture before writing.** The user wants to understand reasoning behind design decisions. Present plans with tradeoff analysis before drafting files. Don't skip straight to writing.
- **Question assumptions from design docs.** The user specifically questioned: why separate test-writer and scenario-writer? Why not script fraudulent test detection? Does the Tester need test-writer? Should the implementer have task-execution? The user values an agent that interrogates the design rather than accepting it.
- **Trace responsibility through the architecture.** When the user asks "who catches X?", trace the full verification chain (audit-checker → scenario-writer → plan-checker). Don't give a single-layer answer.
- **Present skill coverage analysis before assigning.** Read each candidate skill's SKILL.md, list what it covers, evaluate what % applies to the sub-agent, and present the analysis. The user decides based on the analysis.
- **When splitting is discussed, show the full side-by-side.** The task-execution split was done by mapping every component to who actually uses it (Coder vs implementer vs Tester). Present this analysis before proposing the split.

---

## What Still Needs to Be Done

### Phase A Remaining (2 sub-agent .md files)

Write one file at a time. Present plan for user review before writing.

| # | File | Source | Key Concerns | Likely Skills |
|---|------|--------|-------------|---------------|
| 8 | `sub-agents/debugger.md` | SUB-AGENT-SPECS §8 | Hypothesis-driven, ≥2 hypothesis log. Stall threshold 3. Multi-parent: Coder (TDD workflow when implementer stalls) + Auditor (ad-hoc debugging). INV-1: blind to tests. | ptc-sandbox, debugging |
| 9 | `sub-agents/optimizer.md` | SUB-AGENT-SPECS §9 | Ephemeral worktree, compare-and-discard. Multi-parent: Coder + Auditor + Orchestrator. Performance focus. | ptc-sandbox, code-design (?) |

**Before writing #8 (debugger):**
1. Read SUB-AGENT-SPECS §8
2. Read `new_claude/skills/debugging/SKILL.md` to understand what it covers
3. Present plan with skill coverage analysis
4. Apply all 32 patterns from Sessions 2-3

**Before writing #9 (optimizer):**
1. Read SUB-AGENT-SPECS §9
2. Read `new_claude/skills/code-design/SKILL.md` (already read this session — covers concern decomposition, complexity reduction, design verification)
3. Evaluate whether code-design should be frontmatter or routing pointer for optimizer
4. Present plan with skill coverage analysis

### Required Revision: test-writer.md

Update targeted mode from Tester-dispatched infrastructure repair to Coder-dispatched audit test fixes. See "Required Revisions" section above for details.

### Phase B (6 teammate .md files) — After Phase A

Uses the agent-md-writing skill (not sub-agent-md-writing). Requires reading:
- `TEAMMATE-DIRECTOR-PATTERNS.md`
- `BASE-DIRECTOR-PATTERN.md`
- Each teammate's skills to audit for duplication

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
new_claude/agents/sub-agents/test-writer.md          (~140 lines)
new_claude/agents/sub-agents/implementer.md           (~143 lines)
new_claude/agents/sub-agents/scenario-writer.md       (~174 lines)
new_claude/skills/task-handling/SKILL.md               (183 lines)
new_claude/skills/task-handling/references/patterns.md (183 lines)
new_claude/skills/task-handling/references/anti-patterns.md (175 lines)
new_claude/skills/task-execution-impl/SKILL.md         (148 lines)
new_claude/skills/task-execution-impl/references/patterns.md (200 lines)
new_claude/skills/task-execution-impl/references/anti-patterns.md (187 lines)
```

### Modified Files

```
new_claude/agents/sub-agents/audit-checker.md  (added ~9 lines: fraudulent vs misaligned tests)
```

### Files NOT Modified (from Session 2, still current)

```
new_claude/agents/sub-agents/codebase-scout.md
new_claude/agents/sub-agents/research-scout.md
new_claude/agents/sub-agents/plan-checker.md
```
