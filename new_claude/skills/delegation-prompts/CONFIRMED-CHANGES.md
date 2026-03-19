# Confirmed Changes: delegation-prompts Skill

**Session:** 2026-03-11
**Status:** All 5 delegation types implemented, return protocol implemented

---

## What Exists (Implemented & Tested)

All files under `agentic_workflow/new_claude/skills/delegation-prompts/`:

```
SKILL.md                              (v4-0-0)
schemas/targeted.schema.json
schemas/guided.schema.json
schemas/tdd-chunk.schema.json
schemas/exploration.schema.json
schemas/research.schema.json
schemas/return-targeted.schema.json
schemas/return-guided.schema.json
schemas/return-tdd-chunk.schema.json
schemas/return-exploration.schema.json
schemas/return-research.schema.json
scripts/validate_delegation_prompt.py  (5 types, 22+ tests)
scripts/validate_return.py            (return validation utility)
references/anti-patterns.md           (7 WRONG/RIGHT pairs)
specializations/ptc-delegation.md
specializations/tdd-delegation.md
```

Hook files under `agentic_workflow/new_claude/hooks/`:
```
subagent_stop_return_validation.py     (SubagentStop hook for return validation)
```

Hooks are NOT wired into settings.json yet (intentional — waiting for empirical testing).

## Confirmed Design Decisions

### 1. Prompts are JSON with a `type` field

Every Task delegation prompt is a JSON object. The `type` field selects the schema. Non-JSON prompts are rejected by the hook. No markdown fallback.

### 2. Five delegation types

| Type | When | Extra Required Fields |
|------|------|----------------------|
| `targeted` | Spawner has file coordinates and knows the solution shape | `known_context.file_coordinates` (non-empty), `output_contract.format` |
| `guided` | Delegate needs to make judgment calls | `unknowns` (non-empty), `scope_boundary` |
| `tdd_chunk` | TDD implementation from a plan | `test_specifications.pass_a` (non-empty), `success_criteria` (non-empty) |
| `exploration` | Scoped exploration with partitioned agents | `exploration_scope.primary_targets` (non-empty), `essential_output` (non-empty) |
| `research` | Source-targeted research with citations | `research_scope.primary_targets` (non-empty), `source_directives`, `citation_requirements`, `essential_output` (non-empty) |

### 3. Common required fields (all types)

- `type` — schema selection
- `task` — what the delegate should accomplish
- `known_context` — file coordinates, findings, context packets
- `output_contract` — where to write, what format

### 4. Common optional fields (all types)

- `unknowns` — specific things to verify (required for `guided`)
- `scope_boundary` — prohibitions, tool budget, file set (required for `guided`)
- `adjacent_context` — fallback resources if delegate hits unexpected cases
- `rejected_approaches` — dead ends with reasons
- `environment` — working directory, available tools, PTC/MCP
- `success_criteria` — when to stop (required for `tdd_chunk`)
- `ptc_hints` — PTC loading instructions
- `return_schema` — JSON schema for sub-agent return format
- `return_instruction` — instruction text for sub-agent return

### 5. Pre-delegation reasoning chain (Q1-Q5)

Unchanged from original design. 5 questions that map to JSON fields:

- Q1 (What do I know?) → `known_context.findings` + `adjacent_context`
- Q2 (What files?) → `known_context.file_coordinates` + `context_packets`
- Q3 (What constraints?) → `known_context.findings` + `scope_boundary`
- Q4 (What did I reject?) → `rejected_approaches`
- Q5 (What does done look like?) → `output_contract` + `success_criteria`

### 6. PTC prompt composition

Spawner uses PTC to compose delegation prompts from context packets, session logs, and plan files. Raw data stays in PTC container, only the assembled JSON enters the spawner's context.

### 7. Hook validation (delegation prompts)

`scripts/validate_delegation_prompt.py` — PreToolUse hook on Task tool:
- Parses prompt as JSON (rejects non-JSON)
- Checks `type` field, dispatches to type-specific validator
- Each type checks its required fields, absolute paths, vague discovery verbs
- Exit code 2 = BLOCK, exit code 0 = ALLOW
- Escape hatch: `SKIP_DELEGATION_VALIDATION` in prompt

### 8. Return protocol

Sub-agent returns are structured JSON matching per-type return schemas:
- Return schemas define required fields per delegation type
- `return_schema` field in delegation prompts tells sub-agents the expected format
- `scripts/validate_return.py` validates returns (importable utility + CLI)
- `hooks/subagent_stop_return_validation.py` blocks invalid returns (one retry via `stop_hook_active`)
- Parent-side PTC validation as fallback for mixed text/JSON returns

### 9. Return schema fields

All returns require `delegation_type` (const per type) and `status` (completed/partial/failed).

Type-specific required return fields:

| Type | Required | Optional (all types) |
|------|----------|---------------------|
| `targeted` | `result`, `decisions_made` | `unexpected_findings`, `scope_extensions`, `cross_scope_findings`, `gaps`, `carry_forward` |
| `guided` | `unknowns_resolved`, `decisions_made` | (same) |
| `exploration` | `findings`, `essential_output_confidence` | (same) |
| `research` | `findings`, `essential_output_confidence`, `citations` | (same) |
| `tdd_chunk` | `tests_written`, `pass_gate_results`, `decisions_made` | (same) |

---

## Confirmed Removals

- No scribe delegation type (scribe removed from workflow)
- No markdown fallback in hook (JSON only, no dead code)
- No single evolving schema (replaced by per-type schemas)
- No backward compatibility paths
