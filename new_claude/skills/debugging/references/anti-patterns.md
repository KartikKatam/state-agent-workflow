# Debugging Anti-Patterns

Common mistakes that waste cycles or obscure root causes. Each pattern shows the wrong approach and why it fails, then the correct approach.

## Anti-Pattern 1: Shotgun Debugging

Changing multiple things at once, hoping one of them fixes it.

```
WRONG:
"Let me update the config, change the import, and add a try-catch — one of these should fix it."
→ If it works, you don't know which change fixed it.
→ If it doesn't work, you've introduced 3 new variables to untangle.

RIGHT:
"The error says 'module not found'. I'll check the import path first.
Hypothesis: the import uses a relative path that breaks from this directory.
Test: change to absolute import, run again."
→ One variable. Clear hypothesis. Falsifiable.
```

## Anti-Pattern 2: Stacking Failed Fixes

Leaving failed fix A in place while trying fix B.

```
WRONG:
Fix A (add null check) → still fails → add Fix B (change return type) on top
→ Now you have two changes and a new error. Is the new error from A, B, or their interaction?

RIGHT:
Fix A (add null check) → still fails → REVERT Fix A → try Fix B (change return type) alone
→ Clean baseline for each attempt. Each test is independent.
```

**Why this matters:** Stacked fixes create compound bugs. The agent ends up debugging its own fixes instead of the original problem. Reverting after each failed attempt keeps the debugging space small.

## Anti-Pattern 3: Fixing the Symptom

Suppressing the error message instead of addressing its cause.

```
WRONG:
Error: "ValueError: list index out of range"
Fix: try: result = items[5] except IndexError: result = None
→ Silences the error but doesn't fix why items has fewer than 6 elements.
→ The None propagates and causes a harder-to-trace failure downstream.

RIGHT:
Error: "ValueError: list index out of range"
Investigation: Why does items have fewer than 6 elements?
→ The upstream filter removes duplicates, sometimes reducing the list below expected size.
Fix: Handle variable-length lists in the business logic, not with exception swallowing.
```

## Anti-Pattern 4: Debugging by Print Without a Plan

Adding print statements everywhere without a specific question to answer.

```
WRONG:
print("here 1")
print("here 2")
print(f"x = {x}")
print(f"y = {y}")
print("here 3")
→ Produces a wall of output. Agent scans it without knowing what to look for.

RIGHT:
"Hypothesis: the transform function receives the wrong input type."
print(f"transform input: type={type(input)}, value={input!r}")
print(f"transform output: type={type(result)}, value={result!r}")
→ Two prints. Specific question. Clear answer from the output.
```

## Anti-Pattern 5: "Works on My Machine" Dismissal

Assuming the bug is in the environment rather than the code.

```
WRONG:
"It passes locally, so CI must be misconfigured. I'll re-run the pipeline."
→ Ignores real differences between environments that expose real bugs.

RIGHT:
"It passes locally but fails in CI. What's different?
- Python version? Local: 3.11, CI: 3.10 → check for 3.11-specific syntax
- Dependencies? Compare pip freeze outputs
- Environment variables? Check CI config vs local .env
- File paths? CI runs from a different working directory"
→ Systematic comparison. Often reveals an actual code bug that local environment masks.
```

## Anti-Pattern 6: Premature Architecture Blame

Jumping to "the architecture is wrong" before exhausting simpler explanations.

```
WRONG (at strike 1):
"This keeps failing because the whole module is poorly designed. We need to refactor."
→ Refactoring is expensive. One failed fix doesn't indicate architectural failure.

RIGHT (at strike 3):
"Three hypotheses failed:
1. Wrong input type → fixed, but new error appeared in a different module
2. Missing validation → added, but the same bad data comes from two other callers
3. Changed the data format → broke 4 downstream consumers
→ Each fix reveals more coupling. This IS architectural — escalating per 3-strikes rule."
```

The 3-strikes rule exists precisely to prevent premature escalation. One failed fix is normal. Three failed fixes with expanding scope is a signal.

## Anti-Pattern 7: Skipping Reproduction

Attempting to fix a bug you haven't seen yourself.

```
WRONG:
"The user reported an error. Based on their description, I think the fix is..."
→ User descriptions are often incomplete or inaccurate.
→ You can't verify the fix without seeing the original failure.

RIGHT:
"The user reported an error. First, reproduce it:
1. Run the exact command they described
2. Confirm I see the same error
3. NOW investigate with the actual error output"
→ Reproduction often reveals the bug immediately (wrong input, missing file, etc.)
```

**Exception:** If reproduction requires hardware/environment you don't have access to, work from the COMPLETE error output. But always note that you haven't reproduced it — the fix confidence is lower.

## Anti-Pattern 8: Confirmation Bias Testing

Designing experiments that can only confirm your hypothesis, never disprove it.

```
WRONG:
Hypothesis: "The cache is returning stale data"
Test: Check the cache value → it's stale → "Hypothesis confirmed!"
→ You never checked whether the API ALSO returns stale data.
→ If the API is stale too, the cache is innocent — the bug is upstream.

RIGHT:
Hypothesis: "The cache is returning stale data"
Test: Log BOTH the cache value AND the raw API response in the same call.
→ If API is fresh but cache is stale → cache is the bug.
→ If API is also stale → cache is not the bug; look upstream.
→ One experiment, two hypotheses tested, no confirmation bias.
```

**Why this is dangerous:** A falsifiable hypothesis is necessary but not sufficient. You must also design the test to look for disconfirming evidence, not just confirming evidence. Otherwise you'll "confirm" a wrong hypothesis and waste a fix attempt on the wrong component.
