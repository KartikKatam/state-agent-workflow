# Task Execution Anti-Patterns — Implementer

Common failure modes during delegated task execution, with WRONG/RIGHT examples.

## Contents

1. [Scope Creep via "While I'm Here"](#1)
2. [Treating Limitations as Guidelines](#2)
3. [Verification by Confidence](#3)
4. [Fixing Beyond the Findings](#4)
5. [Batching Deviation Documentation](#5)
6. [Guessing at Missing Context](#6)

## 1. Scope Creep via "While I'm Here" {#1}

WRONG — adds unrequested improvements:
```
# Implementing score_candidate as specified...
# Done. While I'm here, this module could use better error
# messages. Let me also:
# - Add logging to all scoring functions
# - Refactor the import structure
# - Add type hints to score_roi (not in my scope)
```

RIGHT — implements only what's specified:
```
# Implementing score_candidate as specified...
# Done. I noticed score_roi has incomplete type hints, but that
# file is outside my source targets. Documenting as OBSERVED:

decisions_made: "OBSERVED: score_roi in consumer/scoring.py
lacks parameter type annotations. Not in scope — noting for
parent review."
```

WHY: Every "improvement" is an untested, unreviewed change. It wasn't in the plan, wasn't part of the delegation, and won't be specifically checked by the auditor. "While I'm here" is how regressions enter codebases.

## 2. Treating Limitations as Guidelines {#2}

WRONG — interprets limitations flexibly:
```
Limitation: "Do not modify the BatchConfig class"

BatchConfig doesn't have a field I need. Rather than modifying
BatchConfig directly, I'll add a subclass ExtendedBatchConfig
that adds the field. Technically I'm not modifying BatchConfig...
```

RIGHT — treats limitations as hard constraints:
```
Limitation: "Do not modify the BatchConfig class"

I need a field not in BatchConfig. This is a plan gap.
Documenting in decisions_made:
"Plan requires 'priority' field but limitation prevents
BatchConfig modification. Implemented with a separate
PriorityConfig parameter instead. Medium impact — changes
function signature from plan spec."
```

WHY: Creative workarounds to bypass limitations (subclassing, wrapping, monkey-patching) all violate intent. The limitation exists because the planner assessed cross-task impact. Your workaround may break assumptions in other chunks.

## 3. Verification by Confidence {#3}

WRONG — claims completion without evidence:
```
All requirements implemented. Tests should pass based on my
implementation. The scoring function looks correct. Done!

Return: { status: "completed", quality_gate_passed: true }
```

RIGHT — verifies with actual commands:
```
Checking each success criterion:
1. score_candidate returns float → type annotation + test passes ✓
2. Zero-weight exclusion → test_zero_weight_excluded passes ✓
3. Return in [0,1] → test_score_bounds passes ✓

Quality gate: ruff format ✓, ruff check ✓, pyright ✓, pytest ✓
Modified files: only src/producer/ops_batch.py — within scope ✓

Return: { status: "completed", quality_gate_passed: true }
```

WHY: "Should pass" and "looks correct" are confidence statements, not evidence. You cannot know if code works without running it. Every unverified claim is a potential lie — not intentional, but you genuinely don't know until you run the commands.

## 4. Fixing Beyond the Findings {#4}

WRONG — applies fixes plus extras:
```
Audit finding: "score_candidate doesn't handle NaN weights
at ops_batch.py:47"

Fixed NaN handling. Also noticed:
- Added input validation for negative weights (not in findings)
- Improved variable naming throughout (not in findings)
- Added docstring (not in findings)
```

RIGHT — applies exactly what was requested:
```
Audit finding: "score_candidate doesn't handle NaN weights
at ops_batch.py:47"

Fixed: Added NaN check at ops_batch.py:47 before weight
accumulation. Test test_nan_weight passes. All existing
tests still pass.

No other changes made.
```

WHY: Each "extra" fix is untested and unreviewed. The auditor checked their specific findings — they won't re-review everything you touched. Extras slip through the audit cycle unexamined. If you notice other issues, document as OBSERVED and let the parent decide.

## 5. Batching Deviation Documentation {#5}

WRONG — documents deviations at the end:
```
# [implements for 20 minutes, makes 3 scope decisions]

# Now let me document what I did...
decisions_made: [
  "Added __init__.py export" (what was the exact reason again?),
  "Changed return type to TypedDict" (I think it was for JSON...),
  "Modified shared types file" (was this necessary?)
]
```

RIGHT — documents at the moment of decision:
```
# About to add export to __init__.py...
decisions_made.append({
  decision: "Added re-export of ScoreResult to __init__.py",
  reason: "Import system requires explicit export. File not in
    source targets but change is mechanical.",
  impact: "Low"
})

# Now continuing implementation...
# [later] About to use TypedDict instead of dataclass...
decisions_made.append({
  decision: "Used TypedDict for ScoringResult instead of dataclass",
  reason: "JSON serialization needed — discovered reading existing
    consumer code at consumer/api.py:34 which json.dumps results",
  impact: "Medium — API surface changes"
})
```

WHY: Fresh reasoning captures the actual decision factors. Reconstructed reasoning fills gaps with plausible-sounding justifications that may not reflect what actually happened. The parent uses your decisions to evaluate scope compliance — inaccurate reasoning leads to wrong conclusions.

## 6. Guessing at Missing Context {#6}

WRONG — fills gaps with assumptions:
```
Delegation prompt doesn't mention whether scoring should be
sync or async. Based on the function name pattern, I'll assume
sync and implement accordingly. If it needs to be async, the
parent can re-dispatch.
```

RIGHT — documents the gap explicitly:
```
Delegation prompt doesn't specify sync vs async for scoring.
This affects function signatures and caller patterns.

Checking existing source... producer/ops_batch.py doesn't exist
yet (this is a new file). producer/pipeline.py uses sync
patterns throughout.

decisions_made: "Implemented score_candidate as sync — delegation
prompt didn't specify, existing codebase uses sync patterns
(producer/pipeline.py). If async is needed, function signature
and all callers change."
```

CRITICAL — when the gap is too large:
```
Delegation prompt has no plan chunk and no behavioral
requirements. I don't know what to implement.

Return: { status: "failed", reason: "Delegation prompt missing
plan chunk and behavioral requirements. Cannot implement without
knowing what behavior to build." }
```

WHY: Guessing at missing context means building on assumptions. If the assumption is wrong, all work is wasted. For non-critical gaps, document the assumption with reasoning so the parent can catch wrong assumptions in your return. For critical gaps, return `failed` — it's cheaper than building wrong.
