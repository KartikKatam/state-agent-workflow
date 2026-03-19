---
name: implementation-plans
description: Schema and instructions for creating chunked implementation plans with invariants and quality gates. MUST be loaded by plan-architect and chunk-coder agents. Defines how features are broken into implementable chunks. Test specs are added separately during the Test Architecture Phase (see test-architecture skill).
---

# Implementation Plans Skill

> **Purpose**: Defines how features are broken into implementable, testable chunks with invariants, quality gates, and dependency ordering.
> **Consumers**: plan-architect, chunk-coder
> **Schemas**: `implementation-plans/schemas/implementation-plan.schema.json`
> **Depends on**: None

## Contract

- Every chunk MUST have at least one machine-verifiable invariant
- Chunks MUST be vertical slices delivering a capability, not horizontal layers
- ALWAYS verify context completeness (all integration points covered) BEFORE chunk breakdown
- NEVER save a plan without user approval
- Test specs (added during Test Architecture Phase) MUST be runnable at the end of their chunk with no reliance on unfinished chunks
- All scope files MUST be declared in `scope.touched_files`

Implementation plans break features into chunks that can be implemented, tested, and verified independently.

## Core Principles

1. **Each chunk is independently testable** - Can write and run tests without other chunks
2. **Invariants are machine-verifiable** - Shell commands that return pass/fail
3. **Dependencies are explicit** - Chunks declare what must complete first
4. **Scope is bounded** - Each chunk modifiable in one coding session

## Plan Structure

Plans live in `.claude/plans/{feature}-plan.json` and follow `schemas/implementation-plan.schema.json`.

### Chunks

A chunk is the atomic unit of implementation. Each chunk must have:

| Field | Purpose | Example |
|-------|---------|---------|
| `id` | Unique identifier | `"chunk-01"` |
| `name` | Short descriptive name | `"Create batch scoring types"` |
| `scope.primary_file` | Main file being created/modified | `"producer/ops_batch.py"` |
| `scope.touched_files` | All files affected | `["producer/config.py", "producer/__init__.py"]` |
| `tasks` | Specific coding tasks | `["Define BatchCandidate dataclass", "Add config fields"]` |
| `invariants` | Verifiable conditions | See below |
| `dependencies` | Chunks that must complete first | `["chunk-01"]` |

### Invariants

Invariants are **machine-verifiable** conditions. Each must have a command that exits 0 on success:

```json
{
  "id": "inv-01-01",
  "description": "BatchCandidate type exists and is importable",
  "verify": {
    "command": "python -c \"from producer.models import BatchCandidate\"",
    "expect": "exit_code_0"
  }
}
```

```json
{
  "id": "inv-01-02",
  "description": "select_batch function has correct signature",
  "verify": {
    "command": "python -c \"from producer.ops_batch import select_batch; import inspect; sig = inspect.signature(select_batch); assert 'candidates' in sig.parameters\"",
    "expect": "exit_code_0"
  }
}
```

```json
{
  "id": "inv-02-03",
  "description": "All tests pass",
  "verify": {
    "command": "pytest tests/test_batch.py -v",
    "expect": "exit_code_0"
  }
}
```

**Invariant Rules:**
- Every chunk needs at least one invariant
- Invariants must be runnable without human judgment
- Use `python -c` for import/signature checks
- Use `pytest` for behavior verification
- Use `grep` for content checks if needed

### Test Specs — NOT Part of Planning

`test_spec` (per chunk) and `module_tests` (holistic) are **populated during the Test Architecture Phase**, not during planning. Do NOT include them when creating or saving a plan.

When the plan-architect loads the `test-architecture` skill and the user activates the test architecture phase, the plan-architect appends these sections to the existing plan JSON. See the `test-architecture` skill for the full workflow.

### Quality Gates

Every plan must specify quality gate commands:

```json
{
  "quality_gates": {
    "format": "ruff format .",
    "lint": "ruff check . --fix && ruff check .",
    "typecheck": "pyright",
    "test": "pytest",
    "gate_script": "scripts/gate.sh"
  }
}
```

The `gate_script` is the comprehensive check. Individual commands are for quick iteration.

## The Chunking Mental Model

**A chunk is a minimal, testable slice of functionality that moves the system from one stable state to another.**

NOT:
- "A set of tasks"
- "A group of edits"
- "A phase"

BUT:
- State A → State B
- With invariants proving the transition is correct

**Good chunk progression example:**

| Chunk | Before State | After State |
|-------|--------------|-------------|
| chunk-01 | System has no batch concept | System can represent batch candidates with scores |
| chunk-02 | System can hold candidates but cannot choose | System can deterministically select a valid batch under constraints |
| chunk-03 | Batch selection ignores diversity | System balances quality and diversity in selection |

Each chunk delivers a **capability**, not just code changes.

---

## Critical Chunking Rules

### Rule 1: Vertical Slice Completeness
Every chunk must include the minimum dataclasses + config keys required to run and test the functions implemented in that chunk.

❌ **Bad**: chunk-01 creates types, chunk-02 adds config, chunk-03 uses both
✅ **Good**: chunk-01 creates types + config + basic function that uses them

### Rule 2: No Orphan Plumbing
Do not create a chunk that only adds dataclasses/config unless those are shared by 2+ future chunks.

❌ **Bad**: "chunk-01: Add BatchCandidate dataclass" (no function uses it yet)
✅ **Good**: "chunk-01: Implement candidate scoring with BatchCandidate type"

### Rule 3: Test Runnable at End of Chunk
The chunk's `test_spec` must be runnable at the end of the chunk with no TODOs or reliance on unfinished chunks (except declared dependencies).

❌ **Bad**: Tests that mock functions from chunk-03 while implementing chunk-02
✅ **Good**: Tests that only depend on chunk-01's completed code

### Rule 4: Scope Must Include Plumbing Files
If a chunk adds config or dataclasses, the relevant files must appear in `scope.touched_files`.

❌ **Bad**: `scope.primary_file: "ops_batch.py"` but silently modifies `config.py`
✅ **Good**: `scope.touched_files: ["ops_batch.py", "config.py", "models.py"]`

---

## Chunking Validation Checklist

Before finalizing any chunk, verify:

1. ✓ Can be tested without mocking future chunks
2. ✓ Has ≥1 invariant that would fail before the chunk
3. ✓ Enables a new capability (not just structure)
4. ✓ Touches ≤4 files
5. ✓ Can be implemented in ≤1 focused session (~30-60 min)
6. ✓ `delivers` field describes a real capability, not just "X exists"
7. ✓ `out_of_scope` explicitly lists what NOT to do

---

## Chunk Purpose Types

Every chunk must declare its purpose:

| Purpose | Description | Invariant Level Required |
|---------|-------------|-------------------------|
| `foundational` | Types, configs, shared utilities | `existence` ok |
| `core_logic` | Main algorithms and functions | `behavioral` required |
| `extension` | Additional features building on core | `behavioral` + `constraint` |
| `integration` | Wiring into pipeline/existing code | `system` required |
| `hardening` | Edge cases, error handling, robustness | `behavioral` + `constraint` |

**Invariant levels:**
- `existence` - Thing imports and has correct signature
- `behavioral` - Function produces correct output for given input
- `constraint` - Function respects limits (max size, thresholds, etc.)
- `system` - End-to-end behavior works correctly

---

## Chunk Sizing Guidelines

**Too Small:**
- "Add import statement" - Not independently testable
- "Define one field" - No meaningful invariant possible
- "Create dataclass only" - Violates Rule 2 (orphan plumbing)

**Too Large:**
- "Implement entire batch selection system" - Too many things to verify
- "Add all tests" - Tests should accompany each chunk

**Just Right:**
- "Implement basic batch selection with quality scoring" - Types + logic + tests
- "Add diversity scoring to batch selection" - Extends existing capability
- "Integrate batch selection into pipeline" - Clear integration boundary

---

## Chunk Ordering

Chunks should be ordered by dependency and purpose:

```
foundational chunks (if truly shared)
    ↓
core_logic chunks
    ↓
extension chunks (can be parallel)
    ↓
integration chunks
    ↓
hardening chunks
```

**Prefer vertical slices over horizontal layers.** A chunk that adds one complete feature is better than a chunk that adds types for three features.

## Deferred Testing (tested_by)

Sometimes a chunk genuinely cannot be tested independently. Use `tested_by` as an escape hatch.

### When Deferred Testing Is Allowed

**Only for `foundational` purpose chunks** that:
- Add config fields with no behavior yet
- Define abstract interfaces
- Set up infrastructure used by multiple chunks

### Requirements

```json
{
  "id": "chunk-01",
  "purpose": "foundational",
  "tested_by": {
    "chunks": ["chunk-02", "chunk-03"],
    "reason": "Config fields have no behavior until select_batch and diversity_score use them"
  }
}
```

- `tested_by.reason` is **required** - explain why this chunk can't be tested alone
- Referenced chunks must exist and should have tests
- Prefer growing chunk size (vertical slice) over deferring tests

### Validation Rules

- Error if non-foundational chunk uses `tested_by`
- Error if `tested_by.reason` is missing
- Warning if >20% of chunks defer testing (suggests poor chunking)

---

## Module Tests & Test Specs — Test Architecture Phase

`module_tests` and per-chunk `test_spec` are NOT part of the planning phase. They are populated when the `test-architecture` skill is loaded and the user activates the Test Architecture Phase.

See the `test-architecture` skill for:
- Per-chunk `test_spec` structure (test names, categories, assertions)
- `module_tests` structure (integration scenarios, golden examples, error recovery, performance criteria)
- Test categories: `edge_case`, `core_logic`, `constraint`, `golden`, `log_assertion`, `error`, `integration`
- Hardening chunk requirement (must have `purpose: "hardening"` with system-level invariant)

The plan-architect appends these to the existing plan JSON during the test architecture phase. The chunk-coder reads its chunk's `test_spec` to know what tests to write.

---

## Plan Lifecycle

Each chunk in the plan links to a session log:

```json
{
  "id": "chunk-02",
  "status": "in_progress",
  "session_log": ".claude/logs/batch-selection-chunk-02-log.json",
  "started_at": "2026-01-27T10:00:00Z",
  "last_updated": "2026-01-27T10:30:00Z"
}
```

This enables resuming work on a chunk across sessions.

## Plan Lifecycle

```
draft → approved → in_progress → completed
                       ↓
                   (individual chunks)
                   pending → in_progress → review → completed
                                              ↓
                                          blocked (if issues)
```

## Session Continuity

## Design Doc Grounding & Context Completeness Check (Before Chunking)

**Critical**: Design documents are codebase-agnostic — they use domain language, not file paths or type names. Before chunking, the planner MUST ground every domain concept against codebase context and verify complete coverage. Spending tokens upfront on thorough grounding prevents mid-planning backtracking.

### Protocol

After reading design doc + context packets:

1. **GROUND** the design doc against codebase context. For each section, extract domain concepts and map them to codebase constructs:

   | Design Doc Section | What to Extract | Map To |
   |---|---|---|
   | I/O Contract — Ingests | Data sources, input structure, key fields | Codebase types, module imports, function signatures |
   | I/O Contract — Produces | Output structure, guarantees, metadata | Return types, dataclass definitions |
   | Design Decisions — Constraints | MUST/MUST NOT rules referencing data or behavior | Existing patterns, config fields, module capabilities |
   | Configuration Surface | Tuning knobs with defaults | Config class, existing param patterns |
   | Technical Approach | Each conceptual step | Modules, functions, or files where each step belongs |

2. **CHECK** each grounded mapping against available context:
   - In `_codebase.json` `structure.blocks`? → covered
   - In `{feature}-context.json` `touchpoints`? → covered
   - Neither? → **CONTEXT GAP**

3. **For each CONTEXT GAP**, send an `info_request` to the orchestrator (see `protocols/team-messaging.md` for message format):
   - Within the repo but outside feature directory → targeted codebase exploration (query-response mode)
   - External (library, API) → researcher dispatch
   - Use `priority: "blocking"` for gaps that affect chunk boundaries
   - Use `priority: "normal"` for gaps that only affect implementation details

4. **WAIT** for all blocking context gaps before proceeding to chunk breakdown. Non-blocking gaps can be filled in parallel with design review.

### Grounding Examples

| Design Doc Says (Domain) | Planner Grounds To (Codebase) | Where to Check |
|---|---|---|
| "list of ROI crops with quality metrics" | `List[RoiRichQuality]` in consumer/models.py | `_codebase.json` types |
| "consumer configuration" | `ConsumerConfig` in consumer/config.py | feature context `configs_needed` |
| "buffer snapshot of up to 32 ROIs" | `BinSnapshot` from producer buffer | feature context `touchpoints` |
| "eligibility flags (top-8 viable)" | `top8_eligible` field on quality metrics | feature context `touchpoints` |
| "registers as pipeline stage" | existing stage registration pattern | feature context `patterns_to_follow` |

### Key Principle

The design doc provides WHAT and WHY. The planner provides WHERE and HOW by grounding against the codebase. Better to make 3 targeted info_requests before planning than to discover ungrounded concepts mid-chunk-detail and backtrack.

---

## Validation

Before saving a plan, verify:
1. Every chunk has at least one invariant
2. Every invariant has a runnable command
3. Dependencies form a DAG (no cycles)
4. All referenced files are in touchpoints
5. Quality gates are specified
6. All design doc domain concepts are grounded to codebase constructs (no unresolved blocking gaps)
