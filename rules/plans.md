---
globs: [".claude/plans/**/*.json", ".claude/plans/**/*.md"]
---

# Plan Format Rules

## Plan Lifecycle

```
Design doc → convert_design.py → Structured design
→ Strategist: plain-text phased plan → User review
→ Strategist: JSON plan with bidirectional links → Validation
→ Implementation
```

## Plain-Text Plan

The strategist first produces a human-readable plain-text plan for user review. Structure:
- Phase breakdown with clear boundaries
- Tasks per phase, ordered by priority and dependencies
- Test approach per task (which passes: A, B, C)
- Estimated complexity (used for model dispatch, not time)

User can edit the plain-text plan. Edits are tracked in the planning log.

## JSON Plan Structure

After user approves plain text, the strategist converts to JSON. Key fields per task:
- `source_ref` — exact line references back to plain-text plan (bidirectional linking)
- `touched_functions` — functions this task will modify (for parallel conflict detection)
- `priority` — task ordering within phase
- `model_recommendation` — `opus-4-6` for complex, `sonnet-4-6` for mechanical
- `requires_research` / `requires_exploration` — boolean flags
- `exploration_queries` / `research_queries` — specific queries for explorer/researcher
- `test_passes` — which test passes apply (A, B, C)
- `dependencies` — task IDs that must complete first
- `review_level` — audit intensity for this task:
  - `light`: boilerplate, mechanical, single-file changes
  - `phase`: important but no downstream task dependencies
  - `full`: other tasks depend on this task's output being correct

## Rules

- Original design doc text MUST be quoted verbatim in plans — never paraphrased
- Every task MUST have at least one test pass assigned
- `touched_functions` MUST be populated — it drives parallel conflict detection via `validate_function_overlap.py`
- Tasks with overlapping `touched_functions` MUST NOT be assigned to parallel coders
- `source_ref` MUST point to actual lines in the plain-text plan — validated by `validate_plan_conversion.py`
- Every task MUST have a `review_level` assigned
- Plan JSON MUST pass schema validation before implementation begins
- Phase boundaries must align with natural integration points — not arbitrary task counts

## Plan Modifications During Implementation

- Coders MUST NOT modify the plan
- If a coder discovers the plan is wrong, they document the deviation in the session log and continue with the plan's intent
- Plan modifications require user approval via the orchestrator
- Deviations are tracked in `planning-log.json` for auditor review

## Validation

```bash
# Validate plan JSON structure
python3 scripts/validate_plan_conversion.py plan.json plan.md

# Check function overlap for parallel safety
python3 scripts/validate_function_overlap.py plan.json
```
