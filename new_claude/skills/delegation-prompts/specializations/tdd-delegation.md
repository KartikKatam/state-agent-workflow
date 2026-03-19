# TDD Delegation Specialization

Extends the base delegation prompt schema with chunk-specific fields for TDD implementation delegation. Used by plan-architect when creating chunk specs and by orchestrator when spawning chunk-coders.

## Chunk-Specific Fields

Add these fields to the delegation prompt JSON alongside the base fields. These use `additionalProperties` in the schema -- no base schema change needed.

```json
{
  "task": "Implement chunk 02: input validation for Pipeline.process_frame",
  "known_context": { "..." },
  "output_contract": { "..." },

  "test_specifications": {
    "pass_a": [
      {"name": "test_validate_input_rejects_none", "description": "ProcessResult raises ValueError on None input"},
      {"name": "test_validate_input_accepts_ndarray", "description": "ProcessResult accepts valid np.ndarray"}
    ],
    "pass_b": [
      {"name": "test_pipeline_end_to_end", "description": "Pipeline.run() produces valid ProcessResult from sample frame"}
    ],
    "pass_c": []
  },

  "pass_gate_criteria": [
    "All Pass A tests pass (unit)",
    "All Pass B tests pass (integration)",
    "Quality gate passes: ./scripts/gate.sh (format, lint, typecheck, tests)",
    "No regressions in existing tests from previous chunks",
    "Session log updated with phase_history entries for red_verified and green_verified"
  ],

  "previous_chunk_decisions": [
    "Chunk 01 chose ProcessResult as the return type for all pipeline stages",
    "Chunk 02 established the Stage base class at /workspace/src/stages/base.py:12",
    "Frame validation uses frame.dtype == np.uint8 check (from chunk 01, line 45)"
  ],

  "cross_chunk_continuity": [
    {"artifact": "ProcessResult", "source": "/workspace/src/types.py", "from_chunk": "01"},
    {"artifact": "Stage base class", "source": "/workspace/src/stages/base.py", "from_chunk": "02"},
    {"artifact": "Chunk 02 session log", "source": ".claude/logs/pipeline-chunk-02-log.json", "from_chunk": "02"}
  ]
}
```

## Plan-to-Prompt Translation

When translating a chunk spec from `.claude/plans/{feature}-plan.json` into a delegation prompt, map these fields:

| Plan Field | JSON Prompt Field |
|-----------|-------------------|
| `chunks[N].description` | `task` |
| `chunks[N].files_to_create` | `output_contract` (primary), `known_context.file_coordinates` (as targets) |
| `chunks[N].files_to_modify` | `known_context.file_coordinates` (with current state) |
| `chunks[N].test_specs.pass_a` | `test_specifications.pass_a` |
| `chunks[N].test_specs.pass_b` | `test_specifications.pass_b` |
| `chunks[N].test_specs.pass_c` | `test_specifications.pass_c` |
| `chunks[N].invariants` | `scope_boundary.do_not` |
| `chunks[N].dependencies` | `previous_chunk_decisions` + `cross_chunk_continuity` |

**Important:** Do not just copy plan JSON into the prompt. Translate fields with absolute file paths and concrete details. The plan uses relative descriptions; the prompt needs executable coordinates.

**PTC composition** is ideal for this translation -- read the plan JSON in PTC, extract the chunk, and compose the delegation prompt programmatically:

```python
import json

with open("/workspace/.claude/plans/pipeline-plan.json") as f:
    plan = json.load(f)
with open("/workspace/.claude/logs/pipeline-chunk-01-log.json") as f:
    prev_log = json.load(f)

chunk = plan["chunks"][1]  # chunk 02

prompt = {
    "task": chunk["description"],
    "known_context": {
        "file_coordinates": [
            {"path": f"/workspace/{f}", "description": "To be modified"}
            for f in chunk.get("files_to_modify", [])
        ],
        "findings": [d["description"] for d in prev_log.get("decisions", [])]
    },
    "test_specifications": chunk.get("test_specs", {}),
    "pass_gate_criteria": [
        "All Pass A tests pass", "All Pass B tests pass",
        "Quality gate passes: ./scripts/gate.sh",
        "No regressions in previous chunk tests"
    ],
    "previous_chunk_decisions": [
        s["description"] for s in prev_log.get("learning_signals", [])
        if s.get("type") == "carry_forward"
    ],
    "cross_chunk_continuity": [
        {"artifact": e.get("path", ""), "source": f"/workspace/{e.get('path', '')}",
         "from_chunk": "01"}
        for e in prev_log.get("files_modified", [])
    ],
    "output_contract": {
        "path": f"/workspace/{chunk['files_to_create'][0]}",
        "format": "Python source file"
    },
    "scope_boundary": {
        "do_not": chunk.get("invariants", []),
        "file_set": chunk.get("files_to_create", []) + chunk.get("files_to_modify", [])
    }
}

print(json.dumps(prompt, indent=2))
```

## Carry-Forward Decision Extraction

Before spawning a chunk-coder for chunk N, read the session log from chunk N-1. Extract:

| Log Field | Prompt Field |
|-----------|-------------|
| `learning_signals` where `type == "carry_forward"` | `previous_chunk_decisions` |
| `decisions` | `previous_chunk_decisions` (architectural choices) |
| `files_modified` | `cross_chunk_continuity` (artifacts created) |
| `files_modified` | `known_context.file_coordinates` (current state of modified files) |

## Dependency Handling

When a chunk depends on artifacts from previous chunks, include them in the prompt:

| Dependency Type | JSON Prompt Field |
|----------------|-------------------|
| File created by previous chunk | `cross_chunk_continuity` with absolute path |
| Type/class defined by previous chunk | `known_context.findings` with import path + signature |
| Test pattern established by previous chunk | `adjacent_context` with test file path |
| Configuration set by previous chunk | `known_context.file_coordinates` with config path + keys |

Provide paths and coordinates, not full file contents. The goal is to eliminate search, not to eliminate reading.
