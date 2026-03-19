---
name: debugging
version: 1-0-0
triggers:
  - agent_role: "*"
    conditions: when encountering a failing test, unexpected error, or behavior that doesn't match expectations
description: >
  Use when any agent hits a test failure, runtime error, unexpected behavior,
  or a fix attempt that didn't work. Activates for: TDD red-green failures,
  quality gate failures, integration errors, pipeline bugs, flaky tests,
  regression bugs, auditor-reported defects, scenario failures, or when
  code runs without errors but produces incorrect or empty output.
  Also use when you've tried a fix and it didn't resolve the issue.
  Do NOT use for: writing new tests (test-design), reviewing code quality
  (code-review), planning test architecture (test-design), or routine
  test runs that pass as expected.
---

# Debugging

## Core Principle

**Debugging is evidence collection, not guessing.** Every fix must be traceable to a specific root cause identified through systematic investigation. If you can't explain WHY a fix works, you haven't found the root cause — you've found a workaround that will break again.

## Quick Reference

| Situation | Action |
|-----------|--------|
| Test fails unexpectedly | Start at Phase 1 — reproduce and read the FULL error |
| Error message is clear and points to one line | Skip to Phase 3 — form hypothesis, test with smallest change |
| Multiple things broke at once | Phase 1 with boundary logging — isolate which component fails first |
| Fix attempt didn't work | Return to Phase 2 — your mental model is wrong, re-compare working vs broken |
| 3 fix attempts failed | STOP — invoke 3-Strikes escalation (see below) |
| Flaky test (passes sometimes) | Phase 1 with repetition — run 5+ times, log environment state each run |
| Flaky test — need isolation strategy | Read `references/patterns.md` § Flaky Test Isolation |
| Need structured hypothesis | Read `references/patterns.md` § Hypothesis Templates |
| Complex bug — multiple plausible causes | Read `references/patterns.md` § Multi-Hypothesis Generation |
| Error after merge/rebase | `git diff` against last known good — Phase 2 starts with the diff |
| Code runs but output is wrong/empty | Phase 1 — add logging at key points, compare actual vs expected values |
| Bug is in production (can't freely change code) | Read `references/patterns.md` § Production Debugging |

## Core Workflow

Four phases, each producing evidence the next phase consumes. Skipping a phase means the next phase operates on assumptions, not facts.

### Phase 1: Reproduce and Gather Evidence

**Goal:** See the failure yourself. Collect the raw data.

1. **Read the COMPLETE error** — full stack trace, full log output, exit codes. Truncated errors hide the root cause. If the error is long, read it anyway — the cause is often in the middle, not the first or last line.
2. **Reproduce consistently** — run the exact failing command. If it doesn't fail, change ONE variable at a time (environment, input data, test isolation) until it does. A bug you can't reproduce is a bug you can't verify as fixed.
3. **Check what changed** — `git diff` against the last working state. For multi-component systems, add diagnostic logging at EVERY component boundary, run once, then read the logs. The boundary where input looks correct but output looks wrong is where the bug lives.

**Phase 1 produces:** A reproducible failure + the complete error output + what changed since it last worked.

### Phase 2: Compare Working vs Broken

**Goal:** Narrow from "something is wrong" to "this specific thing is different."

4. **Find a working example** — search the codebase for similar code that works correctly. If nothing similar exists, find the last commit where this code worked (`git log`, `git bisect`).
5. **List every difference** — between working and broken, no matter how small. Don't filter yet — the difference you think "can't matter" is often the cause. Include: imports, argument order, types, environment variables, config values, test fixtures.
6. **Check assumptions** — for each difference, verify whether it actually behaves as you expect. Print intermediate values. Read the library source. Call the function in isolation.
7. **Trace backward from the error** — if comparing differences doesn't narrow it down, trace the call chain backward from the error site. At each frame, check: is the data correct here? The first frame where data goes wrong is the origin, even if the error surfaces later.

**Phase 2 produces:** A ranked list of suspicious differences between working and broken code. Phase 3 tests them.

### Phase 3: Hypothesize and Test

**Goal:** Confirm the root cause with a single, targeted experiment.

8. **Form one hypothesis** — state it explicitly: "I think [specific cause] because [specific evidence from Phase 2]." Vague hypotheses ("something is wrong with the config") lead to shotgun fixes. Good hypotheses are falsifiable. For complex bugs with multiple plausible causes, see `references/patterns.md` § Multi-Hypothesis Generation.
9. **Seek disconfirming evidence** — before testing, ask: "What would prove this hypothesis wrong?" Actively look for that evidence. If you find it, update the hypothesis before investing in an experiment. This counters the natural tendency to only look for confirming evidence.
10. **Test with the SMALLEST possible change** — change ONE variable. If the hypothesis is correct, the test should pass (or the error should change). If you change multiple things, you can't tell which one fixed it.
11. **Interpret the result:**
    - Fix worked → proceed to Phase 4
    - Fix didn't work → **return to Phase 2** with new information. Your model was wrong — update it, don't stack another guess on top.
    - Fix changed the error → progress. The old hypothesis was partially right. Form a new, more specific hypothesis.

**Phase 3 produces:** A confirmed root cause with evidence, OR a return to Phase 2 with updated understanding.

### Phase 4: Fix and Verify

**Goal:** Implement a proper fix and prove it works.

12. **Write a failing test** that captures the bug — this test should fail WITHOUT your fix and pass WITH it. This prevents regression and proves your fix actually addresses the root cause, not a side effect.
13. **Implement the minimal fix** — change only what's necessary to make the failing test pass. Resist the urge to "clean up nearby code" during a bugfix — that introduces new variables.
14. **Run the full relevant test suite** — not just the one test you wrote. Fixes that solve one problem by creating another are not fixes.

**Phase 4 produces:** A tested fix with a regression test. If the full suite passes, you're done.

## Example: Full Phase 1→4 Walkthrough

**Bug:** `test_parse_config` fails with `KeyError: 'timeout'`.

- **Phase 1:** Read the full traceback → error is at `config.py:42` in `get_timeout()` which accesses `settings["timeout"]`. Reproduce: `pytest test_config.py::test_parse_config` → fails consistently. `git diff`: the `load_defaults()` function was refactored yesterday.
- **Phase 2:** Compare working (previous commit) vs broken (current). Difference: `load_defaults()` used to return `{"timeout": 30, ...}` but now returns `{"request_timeout": 30, ...}` — the key was renamed.
- **Phase 3:** Hypothesis: "`get_timeout()` uses the old key name `timeout` but `load_defaults()` now uses `request_timeout`." Test: change `settings["timeout"]` to `settings["request_timeout"]` → test passes. Root cause confirmed.
- **Phase 4:** Write regression test asserting `get_timeout()` returns the default value. Apply the key name fix. Run full suite → all pass.

## The 3-Strikes Rule

After 3 failed fix attempts on the same bug, **STOP.** This is no longer a simple bug — it's a signal that your mental model of the system is wrong.

### How to Count Strikes

A "strike" is a Phase 3 hypothesis that failed — you tested a specific change and it didn't fix the problem. Strikes reset when you return to Phase 1 with genuinely new evidence (not just re-reading the same error).

| Strike | What It Means | What to Do |
|--------|---------------|------------|
| 1 | Normal — first guess was wrong | Return to Phase 2, re-examine differences |
| 2 | Your mental model has a gap | Widen Phase 2 — check things you assumed were correct |
| 3 | **Architectural mismatch** | STOP. Escalate. See below. |

### 3-Strikes Escalation

When you hit strike 3, report to the lead/user with:
1. **What you tried** — all 3 hypotheses and why each failed
2. **What you learned** — new understanding gained from the failures
3. **What you suspect** — your best guess at the real issue, even if you can't confirm it
4. **Recommendation** — one of:
   - **Scrap and re-approach:** The implementation path is fundamentally wrong. Start the task over with a different approach.
   - **Request exploration:** Need a codebase-explorer or researcher to investigate a specific question you can't answer from current context.
   - **Escalate to user:** The bug involves domain knowledge, external system behavior, or architectural decisions that require human judgment.

### Patterns That Signal "Scrap, Don't Fix"

- Each fix reveals new shared state or hidden coupling you didn't know about
- Fixes require changes across 4+ files that weren't in the original task scope
- Each fix creates new test failures elsewhere
- You're fighting the framework/library rather than using it

When these patterns appear, the cost of continued fixing exceeds the cost of re-implementing with a correct mental model.

## Critical Rules

- **Read the FULL error before acting.**
  Why: The root cause is in the error output — often in the middle of the stack trace, not the first line. Skimming leads to fixing symptoms while the cause persists.

- **One variable at a time.**
  Why: Changing multiple things simultaneously makes it impossible to identify which change fixed the problem (or made it worse). Revert everything, change one thing, test.

- **Don't stack fixes** — if fix A didn't work, REVERT it before trying fix B.
  Why: Layered failed fixes create compound bugs. You end up debugging your own fixes instead of the original problem.

- **State your hypothesis explicitly** — "I think X because Y."
  Why: Explicit hypotheses are falsifiable. "Let me try this" is not a hypothesis — it's guessing, and you can't learn from a guess that fails.

- **Return to Phase 2, not Phase 3, after a failed fix.**
  Why: A failed fix means your understanding is incomplete. Forming a new hypothesis from the same flawed model produces the same kind of wrong answer. Re-examine the evidence first.

- **Regression test every bug.**
  Why: A bug fixed without a test will recur. The test is proof you found the real cause — if the test is too hard to write, you may have fixed a side effect, not the root cause.

## References

- For proven debugging patterns (rubber duck technique, binary search within code, multi-hypothesis generation, production debugging, boundary logging, 5-whys, hypothesis templates, git bisect, flaky test isolation, defense-in-depth), read `references/patterns.md`
- For common debugging anti-patterns with WRONG/RIGHT examples (8 patterns including confirmation bias), read `references/anti-patterns.md`
