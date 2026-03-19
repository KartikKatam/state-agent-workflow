# Task Handling Patterns

Proven approaches for assignment comprehension, uncertainty declaration, and resolution. Includes role-specific examples for both Coder and Tester teammates.

## Contents

- [Field Extraction by Role](#field-extraction)
- [Uncertainty Declaration Template — Coder](#coder-declaration)
- [Uncertainty Declaration Template — Tester](#tester-declaration)
- [Declaration Quality Checks](#declaration-quality)
- [Resolution Path Examples](#resolution-paths)
- [When a Requirement Seems Wrong](#requirement-wrong)
- [First Assignment in a New Phase](#first-assignment)

## Field Extraction by Role {#field-extraction}

### Coder — Implementation Assignment

| Field | What to Extract |
|-------|----------------|
| `phase_context` | What capability this phase delivers — the larger goal |
| `target_files` | Source files in scope — becomes the implementer's scope boundary |
| `reference_files` | Files to read for context — read BEFORE composing delegation prompts |
| `requirements` | Behavioral specs — each becomes a success criterion for the implementer |
| `limitations` | Hard boundaries — pass through to delegation prompts verbatim |
| `queries` | Questions flagged by planner — resolve before delegating |
| `test_expectations` | What tests must verify — pass to test-writer as specs |
| `review_level` | Audit depth — determines whether to request audit after green phase |
| `review_focus` | What auditor will scrutinize — inform your return review |

### Tester — Test Strategy Assignment

| Field | What to Extract |
|-------|----------------|
| `scope` | Per-task, per-phase, or full-design — determines scenario breadth |
| `design_document` | Source of behavioral requirements — primary input for scenarios |
| `plan_reference` | What capabilities exist to test — what the implementation promises |
| `public_api_specs` | Function signatures, types, contracts — from context packets |
| `behavioral_requirements` | User-facing behaviors to verify — each becomes a scenario |
| `tier_guidance` | Which test tiers to emphasize — from planner or default allocation |
| `queries` | Questions flagged by planner — resolve before designing strategy |

## Uncertainty Declaration Template — Coder {#coder-declaration}

```
ASSIGNMENT: Phase 2, Task 3 — Implement weighted scoring function
PHASE CONTEXT: Batch selection with constraint satisfaction

I KNOW:
- Function signature: score_candidate(candidate: BatchCandidate, weights: ScoringWeights) -> float [requirements]
- Must handle zero-weight fields by exclusion [requirements]
- Return value in [0.0, 1.0] [requirements]
- Existing pattern to follow: score_roi in consumer/scoring.py:45-67 [suggestions]
- BatchCandidate fields: quality, diversity, novelty [context packet: _codebase.json]
- test_expectations: te-02-03-01 (weighted sum), te-02-03-02 (zero-weight), te-02-03-03 (bounds) [test_expectations]

I DON'T KNOW:
- Whether ScoringWeights can have negative values (not addressed in requirements or considerations)
  → Resolution: message Explorer for ScoringWeights type definition
- How score_roi handles division-by-zero when all weights are zero (need to read reference file)
  → Resolution: read consumer/scoring.py:45-67 myself
- Whether the return value should be clamped or whether inputs guarantee the range naturally
  → Resolution: ask orchestrator for plan clarification
```

## Uncertainty Declaration Template — Tester {#tester-declaration}

```
ASSIGNMENT: Design and execute scenario tests for Phase 2 (batch selection)
SCOPE: Per-phase — cross-task integration
DESIGN DOC: .claude/designs/batch-selection.md

I KNOW:
- Phase delivers: candidate scoring, batch assembly, constraint satisfaction [plan reference]
- Design specifies 4 user-facing behaviors: score candidates, assemble batches, enforce constraints, report violations [design doc §3]
- Public API: select_batch(candidates, config) -> BatchResult [context packet]
- Tier guidance: T1+T2 for all behaviors, T3 for config validation (external input), T4 for scoring invariants [assignment]

I DON'T KNOW:
- Full BatchResult shape — context packet shows the type name but not all fields
  → Resolution: message Explorer for BatchResult type definition and fields
- Whether constraint violation reporting is logged or returned as data
  → Resolution: read design doc §3.4 more carefully, or ask orchestrator
- What existing test infrastructure exists (conftest fixtures, test factories)
  → Resolution: message Explorer for tests/ directory structure and conftest.py contents
- Whether Phase 1 scenarios exist that I should extend vs replace
  → Resolution: check .claude/context/ for existing test context packets
```

## Declaration Quality Checks {#declaration-quality}

| Check | Pass | Fail |
|-------|------|------|
| "I know" entries cite sources | "Return [0,1] [requirements]" | "Return [0,1]" (no source) |
| "Don't know" entries are specific | "Can ScoringWeights have negatives?" | "Unsure about types" |
| "Don't know" entries have resolution paths | "→ message Explorer for type definition" | No resolution listed |
| At least one "don't know" entry | Any genuine unknown | Empty column |
| Unknowns are actionable | "Need to read scoring.py:45" | "Might be tricky" |
| Unknowns are assignment-specific | "Can ScoringWeights be negative?" | "Edge cases might exist" |

## Resolution Path Examples {#resolution-paths}

### Coder resolving a codebase unknown

```
UNKNOWN: "What pattern does the existing scoring module use for error handling?"
RESOLUTION: Message Explorer:
  "I need context about error handling patterns in the producer/scoring module.
   Specifically: does score_roi raise exceptions or return sentinel values on
   invalid input? I need this to match the pattern in my delegation prompt."
RESULT: Explorer responds with context packet showing score_roi raises ValueError.
UPDATED DECLARATION: Moved to "I know" — "score_roi raises ValueError on invalid
  input [Explorer context packet: producer-scoring-context.json]"
```

### Tester resolving a design ambiguity

```
UNKNOWN: "Does constraint violation reporting return data or just log?"
RESOLUTION: Read design doc §3.4 first.
  → §3.4 says "violations are reported" but doesn't specify mechanism.
  → Escalate to orchestrator: "Design §3.4 says violations are 'reported'
    but doesn't specify whether this is return data (BatchResult.violations)
    or logged output. This affects my scenario assertions — do I assert on
    return values or captured logs?"
RESULT: Orchestrator clarifies: return data in BatchResult.violations.
UPDATED DECLARATION: Moved to "I know" — "violations in BatchResult.violations
  field [orchestrator clarification, aligns with design §3.4]"
```

### Resolving by reading files yourself

```
UNKNOWN: "How does score_roi handle division-by-zero?"
RESOLUTION: File is in reference_files — read it directly.
  → Read consumer/scoring.py:45-67
  → score_roi raises ValueError("At least one weight must be non-zero")
UPDATED DECLARATION: Moved to "I know" — "score_roi raises ValueError on
  all-zero weights [consumer/scoring.py:52]"
```

## When a Requirement Seems Wrong {#requirement-wrong}

**DO:** Report to orchestrator with evidence.

Coder example:
```
"Requirement 3 says 'return empty list on error' but the existing pattern
in consumer/scoring.py:78 raises ValueError. Should I follow the existing
pattern or the requirement? The plan may not have accounted for the
established error handling convention."
```

Tester example:
```
"Design §3.2 says 'Pipeline must process valid frames' but the plan's
Phase 2 only delivers batch selection — frame processing is Phase 3.
Should I write scenarios for frame processing now (testing unimplemented
behavior) or scope to Phase 2 capabilities only?"
```

**DO NOT:** Silently reinterpret the requirement by acting on what you think is better.

## First Assignment in a New Phase {#first-assignment}

When working on the first task/test cycle of a new phase, you're establishing patterns — not following them. Every subsequent assignment in the phase references your work.

**Coder — first task:**
- Naming conventions you set in delegation prompts become the vocabulary
- Error handling pattern the implementer establishes gets copied
- Module organization becomes the template

**Tester — first test cycle:**
- Scenario organization structure gets reused
- Fixture patterns become conventions
- Assertion style sets the standard

**Extra uncertainty entries for first assignments:**
- "Am I setting the right patterns for this phase?"
- "Does my approach align with what subsequent assignments assume?"
- "Should I verify my conventions with the orchestrator before proceeding?"

Implement/test exactly what's required, but with awareness that your patterns will be copied. Clean, minimal, well-structured work is the best foundation.
