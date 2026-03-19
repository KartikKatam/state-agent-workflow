# Task Handling Anti-Patterns

Common failure modes during assignment comprehension, with WRONG/RIGHT examples. Each pattern represents an observed agent failure under pressure.

## Contents

1. [Skipping Uncertainty Declaration](#1)
2. [Self-Answering Queries](#2)
3. [Performative Compliance](#3)
4. [Delegating with Incomplete Context](#4)
5. [Misclassifying Blocking Uncertainties](#5)
6. [Reinterpreting Requirements](#6)

## 1. Skipping Uncertainty Declaration {#1}

WRONG — Coder jumps to delegation:
```
Received Phase 2 Task 3. Requirements are clear: implement scoring
function. Let me compose the delegation prompt for the test-writer...
```

WRONG — Tester jumps to strategy:
```
Received test strategy assignment for Phase 2. Design doc looks
comprehensive. Let me start designing tier allocation...
```

RIGHT — either role declares uncertainty first:
```
Received assignment. Let me parse all fields before acting.

I KNOW:
- [entries with sources]

I DON'T KNOW:
- [specific, actionable entries with resolution paths]

Resolving unknowns before proceeding...
```

WHY: The "clear" assignment in the WRONG examples hides unknowns that surface mid-work. For the Coder, the implementer gets an incomplete delegation prompt and builds on assumptions. For the Tester, scenarios test the wrong behavior or miss capabilities.

## 2. Self-Answering Queries {#2}

WRONG — Coder answers from assumption:
```
Task queries: "Does BatchProcessor support async iteration?"

I'm fairly confident it does based on the class name pattern.
I'll include async patterns in the delegation prompt...
```

WRONG — Tester answers from inference:
```
Task queries: "Which Phase 1 behaviors are regression-testable?"

Based on the plan, I think all Phase 1 tasks are complete.
I'll design regression scenarios for everything...
```

RIGHT — resolve through proper channels:
```
Task queries: "Does BatchProcessor support async iteration?"

I don't have this in my context. Messaging Explorer to verify
BatchProcessor interface in producer/batch.py.

[WAITS for response before proceeding]
```

WHY: "Fairly confident" and "I think" are not evidence. If the planner put it in `queries`, they flagged it as uncertain. Self-answering defeats the purpose — you build on a guess and every downstream action inherits the risk.

## 3. Performative Compliance {#3}

WRONG — declaration exists but is vacuous:
```
I KNOW:
- This task implements scoring [assignment]

I DON'T KNOW:
- Edge cases might exist
- Codebase may have changed
- There could be issues

Proceeding to delegation...
```

RIGHT — declaration is specific and actionable:
```
I KNOW:
- score_candidate signature: (BatchCandidate, ScoringWeights) -> float [requirements]
- Return range [0,1] [requirements]

I DON'T KNOW:
- Can ScoringWeights have negative values? [not addressed]
  → Message Explorer for type definition
- Is score_roi's pattern still current after Phase 1 changes? [unverified]
  → Read consumer/scoring.py myself
```

WHY: Generic unknowns ("edge cases might exist") apply to every assignment and drive no resolution action. They're checkbox-filling, not genuine uncertainty examination. Specific unknowns lead to specific resolutions.

## 4. Delegating with Incomplete Context {#4}

WRONG — Coder delegates before resolution completes:
```
I've messaged the Explorer about the API signatures but I can
compose most of the delegation prompt while waiting. The
implementer can probably figure out the signatures from the
existing code...
```

WRONG — Tester designs strategy before context arrives:
```
I've asked for public API specs but I can start tier allocation
based on the design doc. I'll adjust if the API specs reveal
something unexpected...
```

RIGHT — wait for resolution, then act:
```
Explorer response received with API signatures. Updating my
declaration — all blocking unknowns resolved. Now composing
the delegation prompt with complete context.
```

WHY: "While waiting" and "adjust if needed" mean you're building on assumptions. The delegation prompt or test strategy gets composed with gaps, and the sub-agent inherits those gaps. The cost of waiting for resolution is minutes. The cost of re-dispatching a sub-agent that built on wrong assumptions is the sub-agent's entire context window.

## 5. Misclassifying Blocking Uncertainties {#5}

WRONG — reclassifies architectural uncertainty as non-blocking:
```
I DON'T KNOW:
- Whether the scoring function should be sync or async [non-blocking —
  I'll default to sync and the implementer can refactor if needed]
```

RIGHT — recognizes it as blocking:
```
I DON'T KNOW:
- Whether the scoring function should be sync or async [BLOCKING —
  this changes function signatures, caller patterns, and test design.
  Asking orchestrator for clarification before delegating.]
```

WHY: "Default to sync and refactor if needed" sounds pragmatic but means the implementer writes sync code, tests are written for sync behavior, and if async is actually needed, ALL of that gets thrown away. Sync vs async changes function signatures, control flow, error handling, and test patterns — it's architectural, not cosmetic.

**The test:** If the uncertainty could change function signatures, control flow, error handling, data structures, test strategy, or scenario design — it's blocking. Only naming, formatting, and documentation uncertainties are non-blocking.

## 6. Reinterpreting Requirements {#6}

WRONG — Coder "fixes" an apparent plan error:
```
Requirement says "return empty list on error" but I know ValueError
is better practice. I'll include ValueError in the delegation prompt
since the planner probably didn't think about Python conventions.
```

WRONG — Tester reinterprets scope:
```
Assignment scope is per-phase but some Phase 1 behaviors seem
undertested. I'll include Phase 1 regression scenarios too since
we're here anyway.
```

RIGHT — report and ask:
```
Requirement says "return empty list on error" but the established
pattern in this codebase is ValueError (consumer/scoring.py:78).
Reporting to orchestrator: "Should I follow the existing convention
or the plan requirement? The plan may not have accounted for the
established error handling pattern."
```

WHY: Requirements come from a planning process that considered cross-task dependencies. Silently reinterpreting them means your work diverges from what other tasks expect. Even when you're right about the better approach, the unilateral change breaks coordination.
