# Coder SM Hardening — Handoff

## What's Done

- All C1-C7 fixes from the plan are implemented in `state-machines/coder.json`
- All infrastructure changes done (schema extensions, variable resolution, guards, read restrictions)
- Test file `tests/daemon/test_coder_hardening.py` — 65 tests, all passing
- Full daemon test suite — 307 tests, all passing, zero regressions

## Bugs Remaining

### Bug 1: QUALITY_GATE → IMPLEMENTATION transition can never fire

**File:** `state-machines/coder.json`, line 528-534

**What's wrong:** The transition guard includes `think_completed`, but the QUALITY_GATE state does not have `think_on_exit: true`. Without `think_on_exit`, the daemon never forces a Think, so `think_completed` never becomes true. If the quality gate fails, the agent is stuck forever.

**Fix:** Add `think_on_exit: true` and a `think_prompt` to the QUALITY_GATE state. Remove `think_completed` from the transition guard. The existing guards `gate_failures_exist` (on the fail path) and `format_pass, lint_pass, typecheck_pass, tests_pass` (on the pass path) handle routing. The agent needs a way to signal "I can't fix this in QUALITY_GATE, send me back to IMPLEMENTATION" — this could be a `think_chosen:UNFIXABLE` guard, or a max retry mechanism.

### Bug 2: FIXES state — agent can never write

**File:** `state-machines/coder.json`, line 189-205

**What's wrong:** FIXES has both `write_allowed: true` and `think_on_exit: true`. The exit transition uses `think_completed` as its guard. When the agent Thinks, the daemon clears the annotation and then immediately checks outgoing guards — `think_completed` is now true, so the auto-transition fires. The agent exits FIXES without ever writing any fixes.

**Fix:** Remove `think_completed` from the exit guard. Replace with mechanical guards that are only true after the agent actually does the work. For FIXES specifically, quality gate guards (`format_pass, lint_pass, typecheck_pass, tests_pass`) make sense — the coder fixes auditor issues, runs the gate, and exits when it passes. `think_on_exit` stays (it primes the agent's context with auditor feedback), but it no longer controls the exit.

### Bug 3: FIXES ↔ TASK_REVIEW_REQUESTED has no cycle limit

**File:** `state-machines/coder.json`

**What's wrong:** Neither the FIXES → TASK_REVIEW_REQUESTED nor TASK_REVIEW_REQUESTED → FIXES transition has `max_occurrences`. The auditor could repeatedly find issues, looping forever.

**Fix:** Add `max_occurrences` to one or both transitions. When exhausted, route to APPROACH_ASSESSMENT or SCRAP_RETRY.

### Bug 4 (other SMs, not coder): Same write + think_on_exit conflict exists in strategist

**File:** `state-machines/strategist.json`

**What's wrong:** Four strategist states (PHASE_BREAKDOWN, TASK_BREAKDOWN, TASK_DETAILING, TEST_PLANNING) have the same `write_allowed: true` + `think_on_exit: true` + `think_completed` exit guard pattern. Same problem as Bug 2.

**Fix:** Same approach — remove `think_completed` from exit guards. These states already have mechanical guards alongside `think_completed` (like `phase_breakdown_file_exists`). But note: `file_exists` guards may fire on re-entry during iteration (file from previous pass still exists). Needs design decision on what the correct exit condition is for iterative planning states.

### Bug 5 (other SMs): Explorer SUB_AGENT_DISPATCH — think is decorative

**File:** `state-machines/explorer.json`

**What's wrong:** SUB_AGENT_DISPATCH has `think_on_exit: true` but all exit transitions use only mechanical guards (no `think_completed` or `think_chosen`). The auto-transition engine can fire mechanical guards before the agent Thinks, bypassing the think-priming entirely.

**Fix:** Either add `think_completed` to the mechanical guards (so Think must happen first), or remove `think_on_exit` if the priming isn't needed here.

### Bug 6 (other SMs): Researcher — 3 states missing think_prompt

**File:** `state-machines/researcher.json`

**What's wrong:** EXISTING_RESEARCH_CHECK, SEARCH_STRATEGY_FALLBACK, and QUERY_RELATEDNESS_ASSESSMENT have `think_on_exit: true` but no `think_prompt`. The daemon's `validate_think()` skips validation when `think_prompt` is missing — returns `complete: true` immediately. The agent isn't forced to answer any specific questions, so the context-priming doesn't happen.

**Fix:** Add `think_prompt` with numbered questions to each state (matching the patterns in explorer.json which has prompts for equivalent states).

## Scope

Bugs 1-3 are in the coder SM (the current task). Bugs 4-6 are in other SMs (flagged for awareness, separate task).
