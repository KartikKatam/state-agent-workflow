---
name: task-execution
description: >
  Use when a coder receives a task assignment and needs to execute it within
  a phased implementation plan. Activates for: task spec comprehension,
  uncertainty declaration, query resolution, scope checking during implementation,
  handling auditor feedback (pass/fix/scrap), deviation documentation.
  Do NOT use for: TDD cycle mechanics (infrastructure), test design
  (test-design skill), code-level design decisions (code-design skill),
  quality gate execution (infrastructure).
---

# Task Execution

## Core Principle

**Understand before you implement. Declare what you don't know before you act on what you do.** A coder who starts implementing with unresolved uncertainties produces code that drifts from the plan. Uncertainty discovered mid-implementation costs 10x more than uncertainty declared upfront.

**Violating the letter of the rules is violating the spirit of the rules.**

<HARD-GATE>
Do NOT write any production code, test code, or modify any source file until you have:
1. Read the FULL task specification (all fields: requirements, limitations, considerations, suggestions, queries, test_expectations)
2. Produced a written uncertainty declaration (explicit know/don't-know list)
3. Resolved all task `queries` — if any query cannot be answered from available context, request info from orchestrator and WAIT

If ANY condition is unmet, STOP and request what you need.
This applies to EVERY task regardless of perceived simplicity.
</HARD-GATE>

## Quick Reference

| Situation | Action |
|-----------|--------|
| Task has `queries` field with entries | Resolve ALL queries before writing code — request info if needed |
| You're unsure about a requirement | Add to uncertainty declaration, request clarification |
| Task requirement seems wrong or impossible | Report to orchestrator — do NOT silently "fix" the plan |
| File not in `target_files` needs modification | STOP — scope violation. Document deviation, request approval if behavioral |
| Auditor says "pass" | Task complete. Proceed to next task |
| Auditor says "fix" with guidance | Apply ONLY the requested fixes. Re-submit |
| Auditor says "scrap" | Delete implementation. Prepare handoff notes for fresh coder |
| You want to "improve" something not in requirements | Don't. Requirements are the scope |
| `limitations` say "do NOT do X" | Hard boundary — no exceptions |
| Mid-implementation: unexpected code behavior | Pause, document, ask orchestrator before proceeding |
| First task of first phase (task-01-01) | You're establishing patterns, not following them — extra scrutiny on naming, structure, error handling. See `references/patterns.md` |
| Task has `review_focus` entries | Address these proactively during implementation — the auditor will check them specifically |

## Core Workflow

### Step 1: Load and Parse Task

Read the task spec completely. Verify you understand every field:

| Field | What to extract |
|-------|----------------|
| `phase_context` | The larger goal — what capability this phase delivers |
| `target_files` | Files you WILL create or modify — your scope boundary |
| `reference_files` | Files to READ, not modify — read these before implementing |
| `requirements` | Exact behaviors to implement — each must be testable |
| `limitations` | Hard boundaries — what you must NOT do |
| `considerations` | Edge cases, patterns to follow, non-obvious factors |
| `suggestions` | Recommended approaches — you may deviate with justification |
| `queries` | Questions you must answer before implementing |
| `test_expectations` | What your TDD tests must verify — each has an ID for coverage tracking |
| `review_level` | 1=no auditor, 2=phase-end, 3=full task auditor |
| `review_focus` | What the auditor will specifically scrutinize — address proactively during implementation, not reactively during review |

Step 1 produces: your mental model of the task. Step 2 tests this model for gaps.

### Step 2: Declare Uncertainty

Before any implementation, produce a written declaration with two columns:

**I KNOW (with source):**
- What you understand about this task and where the understanding comes from
- Source must be: task spec field, reference file, context packet, or domain knowledge (state which)

**I DON'T KNOW / I'M UNSURE:**
- Anything unclear, ambiguous, or where your confidence is below "certain"
- Include: missing context about existing code, unclear requirement interpretation, unfamiliar APIs, edge cases not addressed

**An empty "don't know" list is a red flag, not competence.** Every task has unknowns — if you can't find any, you haven't examined deeply enough. Even simple tasks have at least: "Is the existing code I'll interact with exactly as the plan describes?"

**Unknowns must be task-specific.** Entries like "edge cases might exist" or "codebase may have changed" apply to every task and drive no resolution action — they're performative compliance, not genuine uncertainty. Each unknown should name a specific aspect of THIS task that you cannot verify from available context. If an unknown could be copy-pasted to any other task unchanged, it's vacuous.

Step 2 produces: a written declaration. Step 3 resolves the "don't know" entries before implementation begins.

### Step 3: Resolve Before Implementing

For each "don't know" entry AND each task `queries` entry:

| Resolution Path | When |
|----------------|------|
| Read `reference_files` from task spec | File-level questions about existing code |
| Request orchestrator to dispatch explorer | Structural questions about codebase |
| Request orchestrator to dispatch researcher | API/library/domain questions |
| Ask orchestrator directly | Plan interpretation questions |

**Do NOT proceed to Step 4 until every query and blocking uncertainty is resolved.**

**Default: uncertainties are blocking** unless they ONLY affect naming, formatting, or documentation. If the uncertainty could change your function signatures, control flow, error handling, or data structures — it's blocking. WHY: Agents under pressure reclassify architectural uncertainties as "non-blocking" to proceed faster. "I can implement sync first and refactor if needed" sounds pragmatic but means building on an unverified assumption. The refactor never comes — it becomes tech debt.

Step 3 produces: all queries resolved, declaration updated. Step 4 implements with full understanding.

### Step 4: Implement with Scope Discipline

Implementation follows the TDD cycle (managed by infrastructure). Your discipline during implementation:

**Before modifying any file:**
```
Is this file in target_files?
  Yes → Proceed
  No  → STOP. Scope violation.
        Mechanically necessary? (e.g., __init__.py export)
          → Document as deviation, proceed
        Behavioral change?
          → Request approval from orchestrator FIRST
```

**Before implementing any behavior:**
```
Is this in the task requirements?
  Yes → Implement it
  No  → Is it in limitations (must NOT do)?
        Yes → Do NOT implement
        No  → Out of scope. Don't implement it.
```

**Track deviations as they occur** — document each deviation the moment you make it, not batched later. WHY: Fresh reasoning is accurate; recalled reasoning is reconstructed and incomplete.

Step 4 produces: implementation with scope integrity and deviation log. Step 5 handles review.

### Step 5: Handle Auditor Feedback

When the auditor reviews (review_level 2-3), respond based on their assessment:

| Auditor Response | Your Action |
|-----------------|-------------|
| **Pass** | Task complete. Proceed to next task. |
| **Fix** (with guidance) | Apply ONLY the requested fixes. Do NOT "also improve" other things. Re-submit with changes described. |
| **Scrap** | Delete all implementation code. Do NOT argue or salvage. Prepare handoff notes: what you tried, why it failed, auditor's assessment. A fresh coder starts over. |

**Responding to fix feedback:**
- Read the full feedback before changing anything
- Address each issue individually
- If you disagree, explain your technical reasoning once with evidence. If the auditor maintains their position, follow their guidance — they have broader context
- Never change unrelated code while fixing

**WHY "scrap" means delete:** A fundamentally wrong approach cannot be salvaged by patching. The patterns, assumptions, and structure are wrong. Patching creates Frankenstein code that passes today and fails tomorrow. A fresh start with auditor notes produces cleaner code faster.

Step 5 produces: approved implementation or handoff notes. Step 6 verifies completion.

### Step 6: Post-Task Verification

Before marking the task complete:

- [ ] All `requirements` implemented (check each one against your code)
- [ ] All `test_expectations` covered (each `te-XX-XX-XX` has a corresponding test)
- [ ] All `limitations` respected (re-read each, confirm no violations)
- [ ] No files modified outside `target_files` (or deviations documented)
- [ ] All deviations documented with type, reason, and impact
- [ ] `review_focus` items addressed (if present — verify you proactively handled what the auditor will check)
- [ ] Quality gate passes
- [ ] No unverified claims — run commands, read output, then report

Step 6 produces: verified completion or identified gaps. Task is complete ONLY when ALL checks pass.

## Deviation Documentation

When you deviate from the plan, document immediately:

| Field | What to Record |
|-------|---------------|
| `type` | `scope_addition`, `scope_removal`, `task_modification`, `task_addition`, `task_removal` |
| `description` | What you did differently |
| `reason` | Why — cite evidence (error message, type system requirement, missing export) |
| `plan_gap` | What the plan missed or got wrong |
| `impact` | Low (mechanical) / Medium (API change) / High (behavioral change) |
| `approval_needed` | true if behavioral or API surface change |

Deviations are quality signals that feed back into planning. Undocumented deviations are plan violations.

## Critical Rules

- **Uncertainty declaration is mandatory.** An empty "don't know" column means you haven't examined deeply enough. Every task has unknowns.
- **Queries block implementation.** Unresolved `queries` mean the strategist flagged unknowns. No coding until these are answered. WHY: building on assumptions compounds errors.
- **Scope is a hard boundary.** `target_files` defines what you touch. `limitations` defines what you don't do. Crossing without documentation is a violation, not initiative.
- **Auditor "scrap" means delete.** Not "revise heavily." Not "keep the good parts." Delete and hand off. Sunk cost is irrelevant.
- **Fix means fix, not improve.** Address exactly what was flagged. Improvements during fix cycles introduce untested changes.
- **Evidence, not confidence.** "Should work" is not evidence. Run verification. Read output. Then claim completion.

## Anti-Rationalization

| Excuse | Reality |
|--------|---------|
| "This task is simple, no need to declare uncertainty" | Simple tasks are where assumptions hide. The declaration takes 30 seconds. |
| "I know this codebase, no unknowns" | Then "I know" is easy to fill. "Don't know" still needs entries — edge cases you haven't verified. |
| "The query is obvious, I can answer it myself" | If the strategist put it in `queries`, they flagged it deliberately. Resolve through proper channels. |
| "This file isn't in target_files but obviously needs changing" | If obvious, the plan would include it. Either the plan has a gap (report it) or you're drifting (stop). |
| "The auditor is wrong, my approach is better" | State your case once with evidence. If they maintain, follow. They see the full picture. |
| "I'll document the deviation later" | You won't — or you'll reconstruct incomplete reasoning. Document now. |
| "This limitation doesn't apply here" | Limitations are bright lines. "Doesn't apply" is rationalization. Report to orchestrator if truly inapplicable. |
| "Scrapping wastes too much time" | Sunk cost fallacy. Fresh start with auditor notes beats patching a wrong foundation. |

**Red Flags — STOP and re-check your process:**
- Writing code before completing uncertainty declaration
- Answering your own queries without external verification
- Modifying files not in `target_files` without a documented deviation
- Saying "should work" without running verification
- Adding features not in task requirements
- Arguing with auditor feedback beyond one technical exchange
- Skipping `test_expectations` because "covered by other tests"

## References

For task execution patterns, uncertainty declaration templates, and deviation examples, read `references/patterns.md`.

For common execution failures with WRONG/RIGHT examples, read `references/anti-patterns.md`.

For eval scenarios to verify skill compliance under pressure, read `evals/scenarios.md`.
