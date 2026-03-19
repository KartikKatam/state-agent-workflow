# Phase Planning Anti-Patterns

Common planning failures with WRONG/RIGHT examples.

## Contents
- Anti-Pattern 1: Horizontal Layers Instead of Vertical Slices
- Anti-Pattern 2: Vague Task Descriptions
- Anti-Pattern 3: Code Templates in Plans
- Anti-Pattern 4: Missing Dependencies
- Anti-Pattern 5: Single Approach When Multiple Exist
- Anti-Pattern 6: Planning Without Context Grounding
- Anti-Pattern 7: Review Level Defaults to 1
- Anti-Pattern 8: Orphan Phases
- Anti-Pattern 9: No Queries for Unknown Code

## Anti-Pattern 1: Horizontal Layers Instead of Vertical Slices

WRONG:
```
Phase 1: "Create all types and dataclasses"
Phase 2: "Implement all business logic"
Phase 3: "Wire into pipeline"
Phase 4: "Add all tests"
```
Phase 1 produces code with no testable behavior. Phase 2 cannot be verified without Phase 3. Phase 4 violates TDD entirely.

RIGHT:
```
Phase 1: "Basic candidate scoring" — types + scoring logic + tests
Phase 2: "Batch selection with constraints" — extends scoring, adds selection + tests
Phase 3: "Pipeline integration" — wires scoring into existing pipeline + integration tests
```
Each phase delivers a capability that can be demonstrated and tested independently.

## Anti-Pattern 2: Vague Task Descriptions

WRONG:
```
Task: "Implement the scoring logic as described in the design doc"
```
The coder has no design doc in context. "Scoring logic" is ambiguous. No file paths, no function signatures, no edge cases.

RIGHT:
```
Task: "Implement score_candidate(candidate: BatchCandidate, weights: ScoringWeights) -> float
  in producer/ops_batch.py"
Requirements:
  - Computes weighted sum of quality metrics (sharpness, contrast, coverage)
  - Handles zero-weight fields by excluding them from the sum
  - Returns value in [0.0, 1.0], clamped
  - Raises ValueError if all weights are zero
Considerations:
  - Follow the existing score_roi pattern in consumer/scoring.py:45-67
  - BatchCandidate.quality_metrics may have None fields — treat as zero
```

## Anti-Pattern 3: Code Templates in Plans

WRONG:
```
Task: "Add the following code to ops_batch.py:
  def select_batch(candidates, max_size=32):
      scored = [(c, score(c)) for c in candidates]
      scored.sort(key=lambda x: x[1], reverse=True)
      return scored[:max_size]"
```
Code templates create false precision. They don't account for the actual codebase state, existing patterns, or edge cases the coder discovers. The coder either copies blindly (fragile) or ignores the template (wasted planning effort).

RIGHT:
```
Task: "Implement batch selection in producer/ops_batch.py"
Requirements:
  - Select top candidates by score, respecting max_size constraint
  - Return selected candidates in score-descending order
  - Handle ties deterministically (by candidate ID)
Suggestions:
  - Consider the existing top_k_selection pattern in consumer/selection.py:23-40
  - max_size should come from BatchConfig, not be hardcoded
```

## Anti-Pattern 4: Missing Dependencies

WRONG:
```
Phase 2 tasks:
  - Task 2.1: "Use ScoringWeights to configure scoring"
  - Task 2.2: "Define ScoringWeights dataclass"
```
Task 2.1 uses a type defined in Task 2.2. Without explicit dependency, a coder could start 2.1 first and fail.

RIGHT:
```
Phase 2 tasks:
  - Task 2.1: "Define ScoringWeights dataclass" [no dependencies]
  - Task 2.2: "Use ScoringWeights to configure scoring" [depends on: 2.1]
```

## Anti-Pattern 5: Single Approach When Multiple Exist

WRONG:
```
Phase 2: "Implement event-driven state sync"
[no discussion of alternatives, no rationale for this choice]
```
If the coder discovers polling would be simpler, they have no basis to evaluate the tradeoff. If the approach fails, no fallback exists.

RIGHT:
```
Phase 2: "Implement state sync using event-driven pattern"
Approach rationale: Evaluated event-driven vs polling vs hybrid.
  - Event-driven chosen because: existing pipeline uses EventBus (pipeline.py:89),
    reduces polling overhead for high-frequency updates, matches team's async patterns
  - Polling rejected because: would require new timer infrastructure, higher latency
  - Decision locked with user on [date/context]
```

## Anti-Pattern 6: Planning Without Context Grounding

WRONG:
```
[Strategist reads design doc, immediately starts decomposing into phases]
Phase 1: "Add vehicle tracking module"
— but doesn't know what tracking infrastructure already exists
— discovers mid-planning that a Tracker class already exists with incompatible API
— backtracks and restructures phases 1-3
```

RIGHT:
```
[Strategist reads design doc + context packets]
[Builds grounding table: "vehicle tracking" → existing Tracker in tracking/base.py:12]
[Identifies gap: Tracker API doesn't support batch updates needed by design]
[Sends info_request for Tracker internals before decomposing]
[Plans Phase 1 with full knowledge of what exists and what needs changing]
```

## Anti-Pattern 7: Review Level Defaults to 1

WRONG:
```
All 12 tasks: review_level: 1
Rationale: "These are straightforward implementations"
```
Calling everything "straightforward" is a judgment shortcut. Core logic tasks silently pass without auditor review, bugs ship.

RIGHT:
```
Tasks 1, 4, 8: review_level: 1 (config wiring, import additions)
Tasks 2, 3, 5, 6, 9, 10: review_level: 2 (standard implementations)
Tasks 7, 11, 12: review_level: 3 (new algorithm, shared infrastructure change, performance-critical)
```
Each level assignment has a reason tied to the task's risk profile.

## Anti-Pattern 8: Orphan Phases

WRONG:
```
Phase 1: "Create types and utilities"
  - No verification criteria
  - No capability delivered
  - Exists only because "we need the types first"
```

RIGHT: Merge the types into the first phase that uses them. If types are shared across phases, include them in the first phase that needs them with a minimal usage to verify they work.

## Anti-Pattern 9: No Queries for Unknown Code

WRONG:
```
Task: "Integrate scoring into the pipeline stage registration system"
  [No queries, no considerations about registration API]
  [Strategist has never verified how stage registration works]
```
The coder will discover the registration API is different from what the strategist assumed, waste time investigating, and possibly implement incorrectly.

RIGHT:
```
Task: "Integrate scoring into the pipeline stage registration system"
Queries:
  - "How does stage registration work? Check pipeline/registry.py for the register() API"
  - "Are there ordering constraints between stages? Check if priority field exists"
Considerations:
  - The registration pattern may use decorators or explicit calls — adapt accordingly
```
