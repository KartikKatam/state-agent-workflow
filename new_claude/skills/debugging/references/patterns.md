# Debugging Patterns

Proven approaches for systematic root cause analysis. Load when Phase 1-2 needs more structure or when debugging multi-component systems.

## Multi-Component Evidence Gathering

When a system has multiple components in a pipeline, the failure you see at the end may originate anywhere upstream. Instrument every boundary before guessing.

### The Boundary Logging Pattern

At each component boundary, log what enters and exits:

```python
# Component A → Component B boundary
log.debug(f"A output: type={type(result)}, len={len(result)}, sample={result[:3]}")
log.debug(f"B input:  expected_type={B.input_type}, received={type(result)}")
```

Run once. Read all logs. The boundary where input looks correct but output looks wrong is where the bug lives. This single run replaces multiple rounds of guessing.

**Why this beats guessing:** A pipeline with 5 components has 4 boundaries. Without logging, you have a 1-in-5 chance of looking at the right component first. With boundary logging, one run tells you exactly where to look.

## The 5-Whys Technique

For bugs where the proximate cause is obvious but the root cause isn't, ask "why" iteratively:

```
Symptom: Test times out after 30 seconds
Why? → The database query takes 28 seconds
Why? → The query scans the full table instead of using the index
Why? → The WHERE clause uses a function on the indexed column: WHERE LOWER(name) = 'x'
Why? → The original code used case-sensitive matching; someone added LOWER() for case-insensitivity
Root cause: Need a functional index on LOWER(name), or use ILIKE
```

**Stop when:** You reach something you can fix directly. Going deeper than the actionable cause wastes time.

**Warning:** 5-Whys is for understanding, not for forming hypotheses. After 5-Whys, you should have ONE clear hypothesis to test in Phase 3 — not 5 things to try.

## Hypothesis Templates

Good hypotheses are specific and falsifiable. Use these templates:

| Template | Example |
|----------|---------|
| "The error occurs because [A] sends [X] but [B] expects [Y]" | "The error occurs because the serializer sends a datetime string but the parser expects a Unix timestamp" |
| "This works in [context A] but fails in [context B] because [difference]" | "This works locally but fails in CI because CI uses Python 3.9 which lacks the `match` statement" |
| "The fix for [bug A] introduced [side effect] which causes [bug B]" | "The fix for the timeout added a cache, but the cache returns stale data after updates" |

**Bad hypotheses** (too vague to test):
- "Something is wrong with the config"
- "It might be a race condition"
- "The library probably has a bug"

Each of these has dozens of possible specific causes. Narrow down BEFORE testing.

## Rubber Duck Debugging

Before forming hypotheses, write a 3-5 sentence plain-language narrative explaining:
1. What the code is **supposed** to do
2. What it **actually** does
3. Where the gap is

The act of formulating the explanation forces a shift from investigator mode (looking at details) to explainer mode (understanding the whole). This shift often surfaces incorrect assumptions — you'll write "it sends the data to..." and realize you never verified that it actually sends there.

For AI agents: write the narrative in a think block or as a comment before Phase 3. Don't skip this for "simple" bugs — the ones you think are simple are where hidden assumptions live.

## Binary Search Within Code

When the bug is definitely in a specific function or module but you can't identify the line by reading:

1. Comment out or mock the second half of the function body
2. Test: if bug disappears → bug is in the commented half; if it persists → bug is in the remaining half
3. Restore and repeat with the offending half
4. Continue until the minimal bad statement is identified

**When to use:** Long functions (50+ lines) where reading every line hasn't identified the cause. Binary search finds the line in O(log n) steps instead of O(n) reading.

**When NOT to use:** You need to find which *commit* introduced the bug — use git bisect for that. Binary search debugging operates on the current code state, git bisect operates across time.

## Multi-Hypothesis Generation (For Complex Bugs)

When Phase 2 produces multiple suspicious differences with no clear winner, generate 3-5 competing hypotheses BEFORE testing any:

1. **List all plausible root causes** — aim for at least 3
2. **Eliminate implausible ones via deduction** — does the mechanism make sense? Does the timing match? Would this explain ALL observed symptoms, not just some?
3. **Rank remaining hypotheses** by plausibility (evidence strength from Phase 2)
4. **Design discriminating experiments** — ideally, one experiment whose result rules out 2+ hypotheses simultaneously (Strong Inference). Example: if you suspect either the cache or the API, log both the cache value AND the raw API response in one call — one line proves which is wrong.

**When to use:** After Phase 2 when multiple explanations are plausible and no single one stands out.
**When to use single-hypothesis (standard Phase 3):** When the error or boundary log clearly points to one specific cause. Don't overcomplicate simple bugs.

## Production Debugging

When debugging in production or production-like environments where you can't freely modify code or data:

| Constraint | Safe Approach |
|-----------|---------------|
| Can't change production code | Gather evidence from existing logs, error tracking, and monitoring first |
| Sensitive data | Reproduce locally using anonymized production data in a matching environment |
| Live user impact | Test fixes in staging with feature flags before production deployment |
| No direct access to runtime | Use structured logging, distributed tracing, or observability tools already in place |

**The production workflow:**
1. Gather all available evidence from logs and monitoring (Phase 1 without code changes)
2. Reproduce the bug locally in a production-matching environment
3. Debug and fix locally using the standard 4-phase workflow
4. Deploy fix to staging → verify → deploy to production behind feature flag → verify → roll out

**Key rule:** Never debug by trial-and-error on production. The standard 4-phase workflow applies, but Phase 1 evidence comes from existing observability rather than adding new logging.

## Git Bisect for Regressions

When something "used to work" and you can't spot the change in `git diff`:

```bash
git bisect start
git bisect bad                    # Current commit is broken
git bisect good <last-known-good> # This commit was working
# Git checks out a middle commit
# Run the failing test
git bisect good  # or  git bisect bad
# Repeat until git identifies the exact commit
git bisect reset
```

**When to use:** The regression is in a file with many recent changes, making `git diff` too noisy to scan. Bisect narrows hundreds of commits to one in O(log n) steps.

**When NOT to use:** You already know which commit introduced the change (from `git log` or `git blame`). Bisect is slower than just reading the diff in that case.

## Flaky Test Isolation

Flaky tests (pass sometimes, fail sometimes) have three common root causes:

| Root Cause | How to Detect | How to Fix |
|------------|---------------|------------|
| **Shared mutable state** | Fails only when run after specific other tests | Isolate test fixtures; each test creates/destroys its own state |
| **Timing dependency** | Fails more under load or CI | Replace `sleep()` with condition-based waiting; mock time-dependent APIs |
| **External dependency** | Fails when network/service is slow | Mock external calls; use deterministic test doubles |

### Detection approach

Run the failing test in isolation 10 times:
- If it always passes alone → shared state (test pollution from another test)
- If it still flakes alone → timing or external dependency
- If it always fails alone → not flaky; it's a real bug that other tests mask

For test pollution, run tests in pairs to find the polluter:
```bash
# Run suspected polluter + failing test in sequence
pytest test_suspected_polluter.py test_flaky.py -v
```

## Defense-in-Depth (Post-Fix)

After fixing a root cause, prevent recurrence at multiple layers. This turns "we fixed the bug" into "we made the bug impossible":

| Layer | What It Does | Example |
|-------|-------------|---------|
| **Input validation** | Reject bad data at entry points | `assert frame.shape[2] == 3, "Expected RGB"` |
| **Business logic guards** | Verify invariants hold during processing | `if confidence < 0 or confidence > 1: raise ValueError(...)` |
| **Output validation** | Catch corruption before returning results | Validate JSON schema before writing to disk |
| **Diagnostic logging** | Capture forensic context for future issues | Log input/output shapes at component boundaries (can be DEBUG level) |

Not every bug needs all four layers. Use judgment: a simple off-by-one needs a test; a data pipeline failure that was invisible for weeks needs defense-in-depth.
