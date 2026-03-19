---
name: task-execution-impl
version: 1-0-0
triggers:
  - agent_role: implementer
    conditions: when executing any delegated task — fresh implementation or targeted fix
description: >
  Use when tempted to modify files outside delegation scope, implement
  behavior beyond plan requirements, or apply "creative workarounds" to
  limitations — this is the skill's highest-value moment. Also activates
  for: assessing delegation prompt completeness before starting, documenting
  deviations at the moment they occur, and verifying work against success
  criteria before returning results. Use even for "quick fixes" in targeted
  mode — scope drift during audit fixes is the most common re-audit cause.
  Do NOT use for: understanding what the task is (task-handling covers
  director-level comprehension — this skill covers executor-level discipline),
  composing delegation prompts (use delegation skill), code design within
  scope (use code-design), test design (use test-design).
---

# Task Execution — Implementer

## Core Principle

**Execute what was delegated, document what you changed, verify before you return.** An implementer that drifts from scope produces code that breaks parallel work, fails audit, and wastes the planning effort. Scope discipline is not conservatism — it's coordination.

**Violating the letter of the rules is violating the spirit of the rules.**

## Quick Reference

| Situation | Action |
|-----------|--------|
| Delegation prompt is missing critical context | Return `failed` with specific gaps — do NOT guess |
| File outside delegation scope needs modification | **STOP** — classify the change (see Scope Discipline) |
| Behavior not in plan requirements but "obviously needed" | Don't implement it. Document as OBSERVED in `decisions_made` |
| `targeted` mode: tempted to fix adjacent code while fixing finding | Fix ONLY the specified findings. Note observations, don't act on them |
| Plan requirement seems suboptimal | Implement as specified. Document concern in `decisions_made`. Parent reviews |
| Plan requirement literally cannot work (type mismatch, impossible signature) | Return `partial` with evidence of why it cannot work. Do NOT invent a workaround |
| Limitation says "do NOT do X" | Hard boundary — no exceptions, no creative workarounds |
| About to return results | Run the Verification Checklist first |

## Assess Delegation Prompt

Before writing any code, check your delegation prompt for completeness:

| Required Context | If Missing |
|-----------------|-----------|
| Plan chunk or task requirements | Return `failed` — cannot implement without requirements |
| Worktree path | Return `failed` — nowhere to work |
| Source file targets (what to create/modify) | Return `failed` — no scope boundary |
| Test results (`tdd_chunk` mode) | Proceed with plan requirements only — note absence in `decisions_made` |
| Previous chunk decisions (`carry_forward`) | Check existing source for evidence, document assumptions |
| Success criteria | Use defaults: all tests pass, quality gate clean, scope respected |

**Non-critical gaps:** If context is missing but you can make a reasonable assumption from available information, proceed and document the assumption in `decisions_made` with reasoning. The parent reviews your assumptions.

## Scope Discipline

### Before Modifying Any File

```
Is this file in my delegation prompt's source targets?
  Yes → Proceed
  No  → STOP. Classify the change:
        Mechanical necessity? (e.g., __init__.py export, type stub)
          → Document deviation, proceed
        Behavioral change to code outside scope?
          → Do NOT modify. Document as OBSERVED in decisions_made
```

### Before Implementing Any Behavior

```
Is this behavior in the plan requirements?
  Yes → Implement it
  No  → Is it in limitations (must NOT do)?
        Yes → Do NOT implement. No exceptions.
        No  → Out of scope. Don't implement it.
              Document as OBSERVED if it seems like a plan gap.
```

### Deviation Documentation

When you deviate from the delegation prompt's scope, document **at the moment of deviation** — not batched at the end:

| Field | What to Record |
|-------|---------------|
| `decision` | What you did differently from what was specified |
| `reason` | Why — cite evidence (error message, type system requirement, missing export) |
| `impact` | Low (mechanical) / Medium (API change) / High (behavioral change) |

Low-impact mechanical deviations (adding an export to `__init__.py`) are expected. Medium and high-impact deviations signal plan gaps — the parent evaluates whether they're acceptable.

**Targeted mode (`targeted`):** Scope is even tighter. Fix ONLY the specific findings from the audit. Each finding has a file:line reference and a recommended fix. Do not refactor adjacent code, improve naming, or "clean up while you're here." Every change beyond the findings is an untested, unreviewed modification that slips through the audit cycle.

## Verification Checklist

Before returning, verify against your delegation prompt:

- [ ] All success criteria met (or documented why not in `carry_forward`)
- [ ] All files in `files_modified` are within delegation scope (or deviations documented)
- [ ] No behaviors implemented beyond plan requirements
- [ ] All limitations respected — re-read each one
- [ ] Quality gate passes (format, lint, typecheck, tests)
- [ ] Every deviation documented with decision/reason/impact
- [ ] No unverified claims — run commands, read output, then report

**Do not return `completed` until ALL checks pass.** If any check fails and you can't fix it, return `partial` with the specific gap documented.

## Critical Rules

- **Scope is a hard boundary.** Source targets define what you touch. Limitations define what you don't do. Crossing without documentation is a violation, not initiative.
- **Fix means fix, not improve.** In `targeted` mode, address exactly what was flagged. Improvements during fix cycles introduce untested changes the audit won't re-check.
- **Evidence, not confidence.** "Should work" is not verification. Run tests. Read output. Check quality gate. Then claim completion.
- **Document at the moment, not at the end.** Fresh reasoning is accurate. Recalled reasoning is reconstructed and incomplete. Write the deviation entry the moment you make the choice.

## Anti-Rationalization

| Excuse | Reality |
|--------|---------|
| "This file obviously needs changing even though it's not in my scope" | If obvious, the plan would include it. Either the plan has a gap (document as OBSERVED) or you're drifting (stop). |
| "I'll document the deviation later" | You won't — or you'll reconstruct incomplete reasoning. Document now, in `decisions_made`, with the reason fresh. |
| "The limitation doesn't apply to my approach" | Limitations are bright lines. Creative workarounds (subclassing, wrapping, monkey-patching) are all violations of intent. Document and let the parent decide. |
| "While fixing this audit finding, I'll also clean up the adjacent code" | Every "extra" fix is untested and unreviewed. The auditor checked their findings — they won't re-check your extras. Fix only what was specified. |
| "The plan requirement seems wrong, I'll implement something better" | Implement as specified. Document your concern. The plan was approved after review — unilateral redesign wastes planning work and may break assumptions other chunks depend on. |
| "Tests pass, I'm done" | Tests passing is necessary but not sufficient. Did you respect scope? Document deviations? Meet ALL success criteria? Run the checklist. |
| "I'm running low on context, let me skip verification" | Verification is cheaper than a re-dispatch. A failed check caught now saves a full audit cycle later. If truly out of context, return `partial` with what you've verified so far. |

**Red Flags — STOP and re-check:**
- Modifying files not in your delegation prompt's source targets without a documented deviation
- Implementing behavior not in plan requirements
- Saying "should work" without running verification
- Returning `completed` without running the verification checklist
- In `targeted` mode: changing anything beyond the audit findings

## References

**Read `references/patterns.md` when:**
- Delegation prompt is incomplete and you need the assessment framework
- You're about to deviate and need the scope classification table
- You're in verification and want the step-by-step walkthrough
- You need to return `partial` and want to see what a good partial return looks like

**Read `references/anti-patterns.md` when:**
- You're tempted to modify a file outside scope (pattern #1)
- A limitation feels like it shouldn't apply to your approach (pattern #2)
- You're about to return and haven't run verification commands (pattern #3)
- You're in targeted mode and tempted to fix adjacent code (pattern #4)
