# Phase Planning Patterns

Proven approaches for phase decomposition, task writing, and multi-approach decisions.

## Contents
- Grounding Table Template
- Divergent Exploration Checklist
- Phase Boundary Heuristics
- Task Writing Patterns
- Review Level Decision Matrix
- Write-Then-Discuss Template
- Plan JSON Structure

## Grounding Table Template

After reading the design doc and context packets, build this table:

| Design Doc Concept | Codebase Construct | Source | Status |
|---|---|---|---|
| "list of ROI crops" | `List[RoiRichQuality]` in `consumer/models.py` | `_codebase.json` types | Grounded |
| "consumer configuration" | `ConsumerConfig` in `consumer/config.py` | feature context `configs_needed` | Grounded |
| "eligibility flags" | `top8_eligible` field on quality metrics | feature context `touchpoints` | Grounded |
| "scoring weights" | ??? | Not found | **Gap — blocking** |

Every row must reach "Grounded" or have a resolution plan (info_request sent) before phase decomposition begins.

## Divergent Exploration Checklist

When spawning sub-agents to evaluate approaches, each sub-agent receives this evaluation checklist:

1. **Feasibility:** high/medium/low + reasoning + blockers
2. **Files affected:** which files need changes, approximate scope
3. **Pattern compatibility:** does this match existing codebase patterns? Which ones?
4. **Friction points:** where does this approach create friction with existing code?
5. **Test impact:** how many tests needed, what fixtures, how complex?
6. **Pros and cons:** concrete advantages and disadvantages
7. **Unknowns:** anything discovered that wasn't in the original context
8. **Code references:** file:line locations that informed the analysis

Present results as a structured comparison. Always include a recommendation with reasoning — never just list options.

## Phase Boundary Heuristics

Good phase boundaries tend to share these properties:

| Property | Why It Matters |
|----------|---------------|
| Each phase adds a demonstrable capability | Stakeholders can see progress; tests have real behavior to verify |
| Phase N's verification doesn't require Phase N+1 | Phases are independently verifiable |
| The boundary corresponds to a natural integration point | Reduces cross-phase coupling |
| Developers on separate phases rarely need to coordinate | Enables parallel work |

**Sizing heuristic:** A phase typically contains 2-6 tasks. Fewer than 2 suggests the phase is too granular (merge with adjacent). More than 6 suggests it covers too much (split at a capability boundary).

## Task Writing Patterns

### The Four Fields

**Requirements** — what MUST be true when the task is done:
- Written as testable assertions
- Each requirement maps to at least one test
- Use exact types, function signatures, file paths

**Limitations** — what the task must NOT do:
- Explicit scope boundaries prevent coder drift
- "Do NOT modify `pipeline.py`" is clearer than "focus on `scoring.py`"
- Include performance constraints if relevant

**Considerations** — what the coder should keep in mind:
- Existing patterns to follow (with file:line references)
- Edge cases the design doc identifies
- Integration points with other phases
- Thread safety, error handling, or performance concerns

**Suggestions** — starting points, not mandates:
- Recommended approach with brief reasoning
- Relevant similar code in the codebase
- Useful library functions or patterns
- The coder may deviate if they justify it

### Task Independence Check

Before finalizing a task, verify:
- Can this task be tested without mocking unfinished tasks?
- Does this task have at least one verification criterion that would fail before the task?
- Can a coder with only this task description (no prior tasks, no design doc) implement it?

If any answer is "no," the task needs more detail or different boundaries.

## Review Level Decision Matrix

| Task Characteristic | review_level | Rationale |
|---|---|---|
| Pure config/wiring, no behavioral change | 1 | Low risk, high confidence in automated tests |
| Standard implementation, follows existing patterns | 2 | Phase-end audit catches pattern deviations |
| New algorithm, complex logic, or novel pattern | 3 | Full auditor catches subtle correctness issues |
| Modifies shared infrastructure (config, types, pipeline) | 3 | Blast radius affects all consumers |
| Performance-critical path | 3 | Auditor verifies performance characteristics |
| Test infrastructure changes | 2 | Phase-end audit verifies test quality |

When uncertain, default to 2. Never default to 1 — the cost of under-reviewing exceeds the cost of over-reviewing.

## Write-Then-Discuss Template

After writing the plan JSON, present this summary to the user:

```
## Plan Summary: {feature name}

### Phases ({N} total)

| Phase | Capability | Tasks | Dependencies |
|-------|-----------|-------|-------------|
| 1. {name} | {what it delivers} | {count} | None |
| 2. {name} | {what it delivers} | {count} | Phase 1 |
| ... | ... | ... | ... |

### Key Decisions
- {Decision 1}: {what was chosen and why}
- {Decision 2}: {what was chosen and why}

### Review Level Distribution
- Level 1 (no auditor): {count} tasks
- Level 2 (phase-end): {count} tasks
- Level 3 (full auditor): {count} tasks

### Open Questions
- {Any unresolved considerations for user input}

### Parallelization Opportunities
- {Which phases/tasks can run concurrently}
```

The user can then adjust any element. Apply changes as edits to the plan file and show the diff.

## Plan JSON Structure

Plans live at `.claude/plans/{feature}-plan.json` and follow `schemas/implementation-plan.schema.json`. Core structure:

```json
{
  "schema_version": "1-0-0",
  "feature": "batch-selection",
  "status": "draft",
  "design_doc": ".claude/designs/batch-selection.md",
  "grounding": {
    "context_packets": ["_codebase.json", "batch-selection-context.json"],
    "concept_map": [
      {
        "design_concept": "batch candidate with quality metrics",
        "codebase_construct": "BatchCandidate in producer/models.py:12",
        "source": "_codebase.json types",
        "status": "grounded"
      },
      {
        "design_concept": "scoring weights configuration",
        "codebase_construct": "ScoringWeights in producer/config.py:23",
        "source": "batch-selection-context.json configs_needed",
        "status": "grounded"
      },
      {
        "design_concept": "pipeline stage registration",
        "codebase_construct": "register() decorator in pipeline/registry.py:45",
        "source": "explorer response 2026-02-28",
        "status": "resolved",
        "resolution": "Explorer confirmed RegistrationAPI uses decorator pattern in pipeline/registry.py:45"
      }
    ],
    "approach": "Event-driven with existing EventBus",
    "approach_rationale": "Matches pipeline.py:89 pattern, lower latency than polling, consistent with existing async patterns in core/events.py",
    "alternatives_considered": [
      {
        "name": "Polling-based sync",
        "summary": "Timer-based polling of producer state at fixed intervals",
        "rejected_reason": "Requires new timer infrastructure not present in codebase, higher latency for high-frequency updates, conflicts with existing event-driven patterns"
      }
    ]
  },
  "phases": [
    {
      "id": "phase-01",
      "name": "Basic candidate scoring",
      "capability": "System can score batch candidates using weighted quality metrics",
      "verification": "score_candidate returns valid [0,1] scores for test fixtures",
      "dependencies": [],
      "rationale": "Scoring is the foundation — selection and diversity depend on it",
      "design_doc_sections": ["3.1 Scoring Algorithm", "3.2 Quality Metrics"],
      "phase_tests": [
        {
          "description": "Scoring pipeline processes candidates end-to-end",
          "scope": "Integration across scoring types, config loading, and score computation"
        }
      ],
      "tasks": [
        {
          "id": "task-01-01",
          "name": "Implement candidate scoring",
          "phase_context": "Phase 1: Basic candidate scoring with weighted quality metrics",
          "target_files": ["producer/ops_batch.py", "producer/config.py"],
          "reference_files": ["consumer/scoring.py:45-67", "producer/models.py:12-30"],
          "requirements": [
            "score_candidate(candidate, weights) -> float in producer/ops_batch.py",
            "Weighted sum of quality metrics, zero-weight excluded",
            "Return value clamped to [0.0, 1.0]"
          ],
          "limitations": ["Do NOT modify consumer/ code", "Do NOT add CLI interface"],
          "considerations": [
            "Follow score_roi pattern in consumer/scoring.py:45-67",
            "BatchCandidate.quality_metrics may have None fields"
          ],
          "suggestions": ["Use existing MetricWeights pattern from config.py"],
          "dependencies": [],
          "review_level": 2,
          "review_focus": ["Verify floating-point precision in weighted sum", "Confirm None-field handling matches existing scorer behavior"],
          "queries": [],
          "test_expectations": [
            {
              "id": "te-01-01-01",
              "description": "Correct score for known inputs with all weights non-zero",
              "requirement_refs": ["score_candidate(candidate, weights) -> float"]
            },
            {
              "id": "te-01-01-02",
              "description": "Zero-weight fields excluded from computation",
              "requirement_refs": ["Weighted sum of quality metrics, zero-weight excluded"]
            },
            {
              "id": "te-01-01-03",
              "description": "None metric fields treated as zero"
            },
            {
              "id": "te-01-01-04",
              "description": "ValueError when all weights are zero"
            },
            {
              "id": "te-01-01-05",
              "description": "Output always in [0.0, 1.0] range",
              "requirement_refs": ["Return value clamped to [0.0, 1.0]"]
            }
          ]
        }
      ]
    }
  ],
  "design_tests": [
    {
      "description": "Full batch selection pipeline from raw candidates to ranked output",
      "scope": "End-to-end: scoring → selection → diversity → pipeline integration",
      "acceptance_criteria": "Design Doc Section 4.2 Acceptance Criteria"
    }
  ],
  "quality_gates": {
    "format": "ruff format .",
    "lint": "ruff check . --fix && ruff check .",
    "typecheck": "pyright",
    "test": "pytest",
    "gate_script": "scripts/gate.sh"
  }
}
```

### Structure Rules

- `concept_map` persists the full grounding table — enables programmatic staleness detection
- `alternatives_considered` records rejected approaches — decision audit trail
- `design_doc_sections` on every phase — auditor verifies design doc coverage
- `phase_context` on every task — zero-context handoff for coders
- `target_files` on every task — orchestrator conflict detection for parallel scheduling
- `review_focus` on level 2-3 tasks — encodes strategist concerns for auditors
- `test_expectations` are structured objects with `id` and `requirement_refs` — programmatic coverage verification
- `phases` is ordered — array index implies execution order
- `dependencies` reference phase/task IDs, forming a DAG
- `quality_gates` are project-wide, not per-phase
