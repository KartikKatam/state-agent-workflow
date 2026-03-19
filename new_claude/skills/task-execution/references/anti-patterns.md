# Task Execution Anti-Patterns

Common failure modes with WRONG/RIGHT markers. Each pattern represents an observed or likely coder failure under pressure.

## Contents

1. [Skipping Uncertainty Declaration](#1-skipping-uncertainty-declaration)
2. [Self-Answering Queries](#2-self-answering-queries)
3. [Scope Creep via "While I'm Here"](#3-scope-creep-via-while-im-here)
4. [Arguing with Scrap Decision](#4-arguing-with-scrap-decision)
5. [Verification by Confidence](#5-verification-by-confidence)
6. [Fixing Beyond the Feedback](#6-fixing-beyond-the-feedback)
7. [Treating Limitations as Guidelines](#7-treating-limitations-as-guidelines)
8. [Skipping Test Expectations](#8-skipping-test-expectations)

## 1. Skipping Uncertainty Declaration

WRONG — jumps straight to implementation:
```
Received task-02-03. Requirements are clear: implement scoring function
that returns [0,1]. Let me start with the test...

[writes test immediately]
```

RIGHT — declares uncertainty first:
```
Received task-02-03. Let me parse all fields before starting.

I KNOW:
- Function signature and return range [requirements]
- Pattern to follow in scoring.py:45-67 [suggestions]

I DON'T KNOW:
- Can ScoringWeights have negative values? [not addressed]
- What happens when all weights are zero? [edge case]
- Is score_roi's pattern still current? [need to verify reference_files]

Resolving unknowns before implementing...
```

WHY: The "clear" task in the WRONG example hides two edge cases (negative weights, all-zero weights) that would cause bugs discovered only during review or production.

## 2. Self-Answering Queries

WRONG — answers the query from assumption:
```
Task queries: "Does BatchProcessor support async iteration?"

I'm fairly confident it does based on the class name pattern.
Proceeding with async implementation...
```

RIGHT — resolves through proper channels:
```
Task queries: "Does BatchProcessor support async iteration?"

I don't have this info in my context. Requesting orchestrator
dispatch explorer to verify BatchProcessor interface in
producer/batch.py.

[WAITS for response before implementing]
```

WHY: "Fairly confident" is not verification. If the query exists in the task spec, the strategist specifically flagged it as uncertain. Self-answering defeats the purpose.

## 3. Scope Creep via "While I'm Here"

WRONG — adds unrequested improvements:
```
# Implementing score_candidate as specified...
# Done. While I'm here, this scoring module could use better
# error messages. Let me also:
# - Add logging to all scoring functions
# - Refactor the import structure
# - Add type hints to score_roi (reference file, not target)
```

RIGHT — implements only what's specified:
```
# Implementing score_candidate as specified...
# Done. I noticed score_roi has incomplete type hints, but that
# file is in reference_files (read-only), not target_files.
# Noting for potential future task, not acting on it.
```

WHY: Every "improvement" is an untested change. It wasn't in the plan, wasn't reviewed by the strategist, and won't be specifically checked by the auditor. "While I'm here" is how regressions enter codebases.

## 4. Arguing with Scrap Decision

WRONG — tries to salvage:
```
Auditor: "Scrap. The approach couples scoring to batch lifecycle,
which will break when we add streaming in Phase 3."

But my implementation works for the current phase. I can add an
abstraction layer to handle streaming later. Let me revise
rather than starting over...
```

RIGHT — accepts and prepares handoff:
```
Auditor: "Scrap. The approach couples scoring to batch lifecycle,
which will break when we add streaming in Phase 3."

Understood. Preparing handoff notes:
- Approach taken: scoring embedded in BatchProcessor methods
- Auditor concern: lifecycle coupling prevents streaming (Phase 3)
- Suggestion for next coder: use standalone scoring functions
  that accept data, not BatchProcessor instances
- Deleting implementation now.
```

WHY: "Scrap" means the architecture is wrong, not just the details. An abstraction layer on a wrong foundation creates technical debt. The auditor sees cross-phase implications the coder doesn't.

## 5. Verification by Confidence

WRONG — claims completion without evidence:
```
All requirements implemented. Tests should pass based on my
implementation. The scoring function looks correct and handles
the edge cases mentioned in considerations. Done!
```

RIGHT — verifies with actual commands:
```
Checking each requirement:
1. score_candidate returns float → confirmed, type annotation + test_score_return_type passes
2. Zero-weight exclusion → test_zero_weight_excluded passes
3. Return in [0,1] → test_score_bounds passes

Quality gate: ./scripts/gate.sh → all checks pass (output below)
Modified files: git diff --name-only shows only producer/ops_batch.py
  and tests/test_scoring.py — both in target_files.

Task complete with evidence.
```

WHY: "Should pass" and "looks correct" are confidence statements, not evidence. Every unverified claim is a potential lie — not intentional, but the agent genuinely cannot know if code works without running it.

## 6. Fixing Beyond the Feedback

WRONG — applies fixes plus extras:
```
Auditor fix feedback: "score_candidate doesn't handle NaN weights"

Fixed the NaN handling. Also noticed:
- Added input validation for negative weights (not in feedback)
- Improved variable naming throughout (not in feedback)
- Added docstring (not in feedback)
```

RIGHT — applies exactly what was requested:
```
Auditor fix feedback: "score_candidate doesn't handle NaN weights"

Fixed: Added NaN check in score_candidate. Test added:
test_nan_weight_excluded. All existing tests still pass.

No other changes made.
```

WHY: Each "extra" fix is an untested, unreviewed change introduced during a review cycle. The auditor will check their specific feedback item — they won't re-review everything. Extras slip through.

## 7. Treating Limitations as Guidelines

WRONG — interprets limitations flexibly:
```
Task limitations: "Do not modify the BatchConfig class"

BatchConfig doesn't have a field I need. Rather than modifying
BatchConfig directly, I'll add a subclass ExtendedBatchConfig
that adds the field. Technically I'm not modifying BatchConfig...
```

RIGHT — treats limitations as hard constraints:
```
Task limitations: "Do not modify the BatchConfig class"

I need a field not present in BatchConfig. This is a plan gap.
Reporting to orchestrator:
"Requirement 2 needs a 'priority' field, but limitation says
do not modify BatchConfig. How should I provide this field?
Options: separate config, function parameter, or plan revision."
```

WHY: Finding creative workarounds to bypass limitations is still violating the limitation. The limitation exists because the strategist assessed cross-task impact. Subclassing, wrapping, or monkey-patching are all violations of intent.

## 8. Skipping Test Expectations

WRONG — writes tests based on intuition:
```
Test expectations say: te-02-03-01, te-02-03-02, te-02-03-03

I'll write comprehensive tests that cover the function thoroughly.
[writes 8 tests based on what seems important, but te-02-03-02
about zero-weight handling is not explicitly tested]
```

RIGHT — maps tests to expectation IDs:
```
Test expectations:
- te-02-03-01: "Weighted sum computed correctly" → test_weighted_sum_basic
- te-02-03-02: "Zero-weight fields excluded" → test_zero_weight_excluded
- te-02-03-03: "Return value in [0,1]" → test_score_bounds

All 3 expectations have corresponding tests. Adding additional
edge case tests beyond expectations is fine, but every te-XX ID
must be covered first.
```

WHY: Test expectations are the strategist's specification of what matters. Skipping one means a deliberate requirement goes unverified. Additional tests are welcome, but expectations are mandatory.
