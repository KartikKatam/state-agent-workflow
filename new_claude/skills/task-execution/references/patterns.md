# Task Execution Patterns

Proven approaches for each phase of task execution.

## Contents

- [Uncertainty Declaration Template](#uncertainty-declaration-template)
- [Scope Checking During Implementation](#scope-checking-during-implementation)
- [Auditor Feedback Handling](#auditor-feedback-handling)
- [Deviation Documentation Examples](#deviation-documentation-examples)
- [First Task in Phase 1](#first-task-in-phase-1)
- [Post-Task Verification Walkthrough](#post-task-verification-walkthrough)

## Uncertainty Declaration Template

Structure your declaration as a two-column artifact. This is what a well-formed declaration looks like:

```
TASK: task-02-03 — Implement weighted scoring function
PHASE CONTEXT: Phase 2: Batch selection with constraint satisfaction

I KNOW:
- Function signature: score_candidate(candidate: BatchCandidate, weights: ScoringWeights) -> float [task spec: requirements]
- Must handle zero-weight fields by exclusion [task spec: requirements]
- Return value in [0.0, 1.0] [task spec: requirements]
- Existing pattern to follow: score_roi in consumer/scoring.py:45-67 [task spec: suggestions]
- BatchCandidate fields: quality, diversity, novelty [context packet: _codebase.json]

I DON'T KNOW:
- Whether ScoringWeights can have negative values (not addressed in requirements or considerations)
- How score_roi handles division-by-zero when all weights are zero (need to read reference file)
- Whether the return value should be clamped or whether inputs guarantee the range naturally
```

### Declaration Quality Checks

| Check | Pass | Fail |
|-------|------|------|
| "I know" entries cite sources | "Return [0,1] [task spec: requirements]" | "Return [0,1]" (no source) |
| "Don't know" entries are specific | "Can ScoringWeights have negatives?" | "Unsure about types" |
| At least one "don't know" entry | Any genuine unknown | Empty column |
| Unknowns are actionable | "Need to read consumer/scoring.py:45" | "Might be tricky" |
| Unknowns are task-specific | "Can ScoringWeights be negative?" | "Edge cases might exist" (applies to any task) |

## Scope Checking During Implementation

### When a file outside target_files needs a change

**Step 1:** Classify the change.

| Classification | Example | Action |
|---------------|---------|--------|
| Mechanical necessity | Adding export to `__init__.py` | Document deviation, proceed — `approval_needed: false` |
| Type system requirement | Adding type to shared types file | Document deviation, `approval_needed: true` |
| Behavioral change | Modifying a function in another module | STOP — request orchestrator approval |
| Convenience improvement | "While I'm here, refactor X" | Do NOT do this |

**Step 2:** If proceeding, log the deviation immediately with exact fields.

### When a requirement feels wrong

DO: Report to orchestrator with evidence.
```
"Requirement 3 says 'return empty list on error' but the existing pattern
in consumer/scoring.py:78 raises ValueError. Should I follow the existing
pattern or the requirement? The plan may not have accounted for the
established error handling convention."
```

DO NOT: Silently "fix" the requirement by implementing what you think is better.

## Auditor Feedback Handling

### Fix Cycle Pattern

When auditor returns "fix" with specific guidance:

1. **Read all feedback items** before making any change
2. **Create a fix plan** — map each feedback item to a specific code change
3. **Apply fixes one at a time** — each fix should be atomic
4. **Re-run relevant tests** after each fix
5. **Re-submit** with a description of what you changed for each item

### Scrap Handoff Pattern

When auditor returns "scrap":

1. **Record** what approach you took and what tests you wrote
2. **Record** the auditor's specific concerns (quote their feedback)
3. **Record** what you'd do differently if you could start over (this helps the next coder)
4. **Delete** all implementation code
5. **Keep** any useful context you gathered (resolved queries, codebase observations)

## Deviation Documentation Examples

### Low-impact mechanical deviation
```json
{
  "type": "scope_addition",
  "description": "Added re-export of ScoreResult to producer/__init__.py",
  "reason": "Python import system requires explicit export for type to be accessible from producer package",
  "plan_gap": "Plan listed producer/ops_batch.py as target but the new type needs to be importable from the package root",
  "impact": "Low — no behavioral change, just import mechanics",
  "approval_needed": false
}
```

### Medium-impact API change deviation
```json
{
  "type": "task_modification",
  "description": "Used TypedDict instead of dataclass for ScoringResult",
  "reason": "JSON serialization required by downstream consumer (discovered in reference_files review)",
  "plan_gap": "Plan didn't specify serialization needs; dataclass requires custom encoder",
  "impact": "Medium — API surface changes: fields accessed via ['key'] not .key",
  "approval_needed": true
}
```

## First Task in Phase 1

When executing `task-01-01`, you are establishing patterns — not following them. Every subsequent task in the phase will reference your implementation as the convention. This changes the uncertainty declaration and implementation mindset.

**Additional uncertainties for task-01-01:**
- "Am I setting the right naming conventions for this module?"
- "Is my error handling pattern what subsequent tasks should follow?"
- "Does my test structure match what test_expectations across the phase assume?"

**Extra scrutiny areas:**
- Function and parameter naming — these become the vocabulary for the phase
- Error handling approach — raise vs return, exception types
- Module organization — import structure, public API surface
- Test file organization — naming, fixture patterns, assertion style

**What NOT to do:** Don't over-engineer to "set up for the future." Implement exactly what task-01-01 requires, but implement it with awareness that your patterns will be copied. Clean, minimal, well-named code is the best foundation.

## Post-Task Verification Walkthrough

Work through the checklist systematically, not from memory:

1. **Open the task spec** — re-read `requirements` one by one. For each, identify the line(s) of code that implement it.
2. **Open the test file** — for each `test_expectations` ID (`te-XX-XX-XX`), confirm a test exists that verifies it.
3. **Re-read `limitations`** — for each, confirm you didn't violate it. Check your git diff.
4. **Check modified files** — `git diff --name-only` against `target_files`. Flag any extras.
5. **Review deviation log** — any `approval_needed: true` entries must be flagged before claiming completion.
6. **Run quality gate** — `./scripts/gate.sh` or equivalent. Read the full output.
7. **Only then** report completion with evidence.
