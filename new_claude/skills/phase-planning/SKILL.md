---
name: phase-planning
version: 1-0-0
triggers:
  - agent_role: strategist
    conditions: when decomposing features into phases and tasks, ingesting design docs, or creating implementation plans
description: >
  Use when a strategist needs to decompose a feature into a phased implementation
  plan. Activates for planning that requires codebase grounding, architectural
  approach decisions, or phase/task decomposition. Also activates when revising
  an existing plan after scope changes. Do NOT use for: executing tasks
  (task-execution), test architecture design (test-design), workflow coordination
  (workflow-coordination), code-level design decisions (code-design).
depends_on: [test-design]
---

# Phase Planning

## Core Principle

**Every planning decision must be traceable to evidence: codebase context, research findings, design doc requirements, or verified domain knowledge.** Unsupported decisions create plans that coders cannot trust and auditors cannot verify. When evidence is unavailable, declare the gap explicitly and request exploration or research before proceeding.

## Quick Reference

| Situation | Action |
|-----------|--------|
| Design doc received, no context packets | Request codebase exploration before planning |
| Domain concept not mapped to codebase construct | Context gap — send `info_request` before chunking |
| 2+ viable architectural approaches | Divergent exploration — evaluate in parallel, present with recommendation |
| Only 1 clear approach matching codebase patterns | Skip divergent exploration, proceed with decomposition |
| Task touches unknown code | Add `queries` to task metadata — coder must resolve before implementing |
| Task is pure boilerplate (config, imports, wiring) | `review_level: 1` — no auditor needed |
| Task modifies core logic or algorithms | `review_level: 3` — full task-level auditor |
| Task is standard but non-trivial | `review_level: 2` — phase-end audit sufficient |
| User hasn't seen the plan yet | Write full plan first, then present — never plan interactively without a written artifact |
| Plan exists but feature scope changed | Re-ground against context, revise affected phases only |
| Codebase changed since plan was written | Re-validate grounding table — see Plan Staleness below |

## Core Workflow

### Step 1: Ingest and Ground

Read the design doc and all available context packets (`_codebase.json`, `{feature}-context.json`).

**Ground every domain concept** to a codebase construct. For each design doc section, extract what it references and map it:

| Design Doc Says | Map To |
|-----------------|--------|
| Data sources, inputs | Existing types, module imports, function signatures |
| Output structure, guarantees | Return types, dataclass definitions |
| Constraints (MUST/MUST NOT) | Existing patterns, config fields |
| Configuration knobs | Config classes, parameter patterns |
| Conceptual steps | Modules, functions, or files where each step belongs |

**Check each mapping** against context packets. If a concept appears in `structure.blocks` or `touchpoints` — it's covered. If neither — it's a **context gap**.

Persist the results as the plan's `grounding.concept_map` — an array of `ConceptMapping` objects. Each entry records the design concept, the codebase construct it maps to (with file:line), which context packet provided the mapping, and a status (`grounded`, `resolved`, `gap_blocking`, `gap_non_blocking`). This persisted map enables programmatic staleness detection later — hooks can diff it against refreshed context packets without the strategist manually re-reading everything.

Step 1 produces: a concept map and a list of context gaps. Step 2 cannot begin until all blocking gaps are resolved.

### Step 2: Identify and Resolve Gaps

For each context gap from Step 1:

| Gap Type | Resolution | Priority |
|----------|-----------|----------|
| Code within repo but outside feature directory | Request targeted codebase exploration | `blocking` if affects phase boundaries |
| External library or API behavior | Request researcher dispatch | `blocking` if affects approach choice |
| Ambiguous design doc requirement | Ask user for clarification | `blocking` always |
| Implementation detail within known code | `normal` — can resolve during task detailing |

Send `info_request` messages for blocking gaps. Wait for responses before Step 3. Non-blocking gaps can proceed in parallel.

When a gap is resolved, update its `concept_map` entry: change status to `resolved` and record how it was resolved in the `resolution` field (e.g., "Explorer confirmed RegistrationAPI uses decorator pattern in pipeline/registry.py:45").

Step 2 produces: resolved concept map with no blocking gaps. Step 3 uses this to assess approaches.

### Step 3: Explore Approaches (When Needed)

**Skip this step** if the grounding table reveals one clear approach consistent with existing codebase patterns. Most features don't need multi-approach analysis.

**Activate when** 2+ viable architectural approaches exist and the choice meaningfully affects phase structure, file organization, or integration cost.

Protocol:
1. Identify 2-4 distinct approaches and evaluation criteria (feasibility, integration cost, pattern consistency, test complexity)
2. Spawn parallel Explore sub-agents — one per approach, each with context packet paths and an evaluation checklist (see `references/patterns.md` for checklist template)
3. If sub-agents surface unknowns, resolve them before synthesizing
4. Synthesize findings into a comparison with a clear recommendation and reasoning
5. Present to user. Iterate until the user locks the approach

Record the locked approach in `grounding.approach` and `grounding.approach_rationale`. Record rejected alternatives in `grounding.alternatives_considered` with name, summary, and rejection reason — this creates a decision audit trail. When Step 3 is skipped (single clear approach), state why the approach was obvious in `approach_rationale` and leave `alternatives_considered` as an empty array.

Step 3 produces: a locked approach with rationale and alternatives record. Step 4 uses this to define phase boundaries.

### Step 4: Decompose into Phases

Break the feature into **milestone-based phases**. Each phase delivers a verifiable capability — not just code changes.

A phase is NOT:
- "A group of related files"
- "A set of tasks"
- "Frontend then backend"

A phase IS:
- A transition from one stable system state to another
- Independently verifiable: you can demonstrate the capability works
- A meaningful milestone a stakeholder would recognize

For each phase, define:
- **What capability it delivers** (one sentence, concrete)
- **Verification criteria** (how to prove the phase is complete)
- **Dependencies** on prior phases (forms a DAG)
- **Rationale** (why this boundary, not a different one)
- **Design doc sections** it implements (e.g., "3.2 Batch Selection") — enables auditor traceability. Every design doc section must map to at least one phase; any unmapped section is a planning gap.

Prefer vertical slices (full capability across layers) over horizontal layers (all types, then all logic, then all integration).

Step 4 produces: ordered list of phases with capabilities and verification criteria. Step 5 breaks each phase into tasks.

### Step 5: Detail Tasks

Break each phase into tasks. A task is the unit a coder receives and executes.

**Task granularity target:** A focused coder should complete one task in a single session. If a task requires "and then also..." — it's too large. If a task is just "add an import" — it's too small.

Each task must specify:

| Field | What It Contains | Why |
|-------|-----------------|-----|
| **phase_context** | One-line phase summary: "Phase N: {capability}" | Zero-context handoff — coder understands the larger goal |
| **target_files** | Files the coder will create or modify | Orchestrator conflict detection for parallel tasks |
| **reference_files** | Files to read but not modify (with line ranges) | Separates what to touch from what to consult |
| **Requirements** | Exact behavior the implementation must satisfy | Coder knows WHAT to build |
| **Limitations** | What the task must NOT do, boundaries of scope | Prevents scope creep |
| **Considerations** | Non-obvious factors: edge cases, performance, existing patterns to follow | Coder avoids pitfalls |
| **Suggestions** | Recommended approaches, relevant code references (file:line) | Coder has a starting point |
| **Dependencies** | Which tasks must complete first | Orchestrator knows ordering |
| **review_level** | 1 (no auditor), 2 (phase-end audit), 3 (full task auditor) | Right level of oversight |
| **review_focus** | What the auditor should specifically verify (for level 2-3) | Encodes the strategist's specific concerns |
| **queries** | Questions the coder must answer before implementing | Forces uncertainty declaration |

**Writing for a context-free coder:** The coder has no project history, no design doc, and no prior conversation. Tasks must be self-contained. Every file path must be exact. Every behavior requirement must be testable. Every constraint must be explicit.

WRONG: "Implement the scoring logic as described in the design doc"
RIGHT: "Implement `score_candidate(candidate: BatchCandidate, weights: ScoringWeights) -> float` in `producer/ops_batch.py` that computes a weighted sum of quality metrics. Must handle zero-weight fields by excluding them. Must return a value in [0.0, 1.0]. Follow the existing `score_roi` pattern in `consumer/scoring.py:45-67`."

Step 5 produces: fully detailed task list within each phase. Step 6 defines test expectations at all three levels.

### Step 6: Define Test Architecture

Tests exist at three levels in every plan. The strategist defines WHAT must be tested at each level; the `test-design` skill (loaded via `depends_on`) guides HOW to design good tests.

**Level 1 — Task Tests:** Every task includes its own test expectations. These are unit-level tests the coder writes as part of TDD. Each test expectation is a structured object with:
- `id` — unique identifier (e.g., `te-01-01-01`) for programmatic coverage tracking
- `description` — what this test verifies in plain language
- `requirement_refs` — which task requirements this expectation covers, ensuring every requirement has at least one test

A task without test expectations is incomplete. If a task is purely structural (config wiring, imports), its tests verify the structure is correct (importability, config loading).

**Level 2 — Phase Tests:** Each phase includes phase-level tests that verify the phase's capability works end-to-end within the phase's scope. These are integration tests that exercise the interaction between tasks within the phase:
- Define in the phase's `phase_tests` field
- Must be runnable at the end of the phase with no dependency on future phases
- Test the capability the phase claims to deliver, not individual task outputs

**Level 3 — Design Tests:** The complete plan includes design-level integration tests that verify the full feature works as specified in the design doc. These span all phases:
- Define in the plan's `design_tests` field
- Cover the design doc's acceptance criteria end-to-end
- Include golden-path scenarios, cross-phase integration, and critical edge cases from the design doc
- Runnable only after all phases complete

The three levels form a testing pyramid within the plan: many task tests (fast, narrow), fewer phase tests (medium scope), few design tests (full scope).

Step 6 produces: test expectations at all three levels embedded in the plan. Step 7 writes the plan file.

### Step 7: Write Plan, Then Discuss

**Write the complete plan as a JSON file** at `.claude/plans/{feature}-plan.json` before presenting to the user. This is the write-then-discuss pattern — it ensures the plan survives handoff even if the session ends during discussion.

After writing:
1. Present a structured summary to the user: phase overview, key decisions, task count per phase, any open questions
2. Invite feedback: the user may adjust phase boundaries, change task granularity, modify review levels, or ask for re-exploration
3. Apply feedback as edits to the written plan, show diffs
4. Repeat until the user approves

The plan file is the source of truth. Discussion refines it; it doesn't replace it.

## Plan Staleness

Plans go stale when the codebase changes between plan creation and later phase execution. Re-validation triggers:

| Trigger | Action |
|---------|--------|
| Explorer produces updated context packets after plan approval | Diff grounding table against new packets — re-ground any shifted constructs |
| A coder reports that a file path or type in the plan no longer exists | Re-ground the affected task, update plan, notify orchestrator |
| A phase completes and changes files referenced by later phases | Re-validate grounding for downstream phases before they start |
| User requests re-exploration mid-feature | Treat as a new grounding pass — run Steps 1-2 against fresh context |

The plan's `grounding.concept_map` persists every design-to-codebase mapping. When context packets refresh, diff their `files_analyzed` lists against the concept map's `codebase_construct` locations. If constructs moved, renamed, or changed signatures — update status to `gap_blocking`, re-resolve, and update affected tasks before coders start them.

## Critical Rules

- **Evidence or gap, never assumption.** Every decision must cite its source (context packet, research entry, design doc section, user statement). If no source exists, declare a gap and request resolution. Guessing creates plans that collapse during implementation.
- **Vertical slices, not horizontal layers.** A phase that "adds all the types" followed by a phase that "adds all the logic" forces coders to work without testable behavior. Each phase must deliver a capability.
- **Tasks are instructions, not code templates.** Specify requirements, limitations, and considerations — not implementation code. The coder makes code-level decisions. Code templates in plans create false precision that doesn't survive contact with the real codebase.
- **Write before discuss.** Always produce a written plan artifact before presenting to the user. Interactive planning without a written artifact loses work on handoff and makes iteration harder to track.
- **Review levels are deliberate.** Every task gets a review_level based on risk, not convenience. Boilerplate gets 1. Core logic gets 3. Default to 2 when uncertain — never default to 1.
- **Queries force honesty.** If a task touches code the strategist hasn't verified, add queries the coder must resolve. This prevents the strategist from silently guessing and the coder from silently assuming.
- **Lock approaches before detailing tasks.** Multi-approach decisions must be resolved (Step 3) before task detailing (Step 5). Detailing tasks for an unlocked approach wastes work when the user picks differently.
- **Phases form a DAG.** No circular dependencies between phases. If two phases depend on each other, they are actually one phase.
- **Tests at every level.** Every task needs `test_expectations`. Every phase needs `phase_tests`. Every plan needs `design_tests`. A plan without test expectations at all three levels is incomplete — the coder and tester cannot do their jobs without knowing what to verify.

## References

For proven planning patterns, grounding examples, divergent exploration checklist, task-writing templates, and plan JSON structure, read `references/patterns.md`.

For common planning failures with WRONG/RIGHT examples, read `references/anti-patterns.md`.

For the plan JSON schema, see `schemas/implementation-plan.schema.json`.

For eval scenarios to verify skill compliance, see `evals/scenarios.md`.
