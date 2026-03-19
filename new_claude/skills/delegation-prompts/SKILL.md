---
name: composing-delegation-prompts
version: 4-0-0
triggers:
  - agent_role: "*"
    conditions: when composing a Task tool call
description: >
  Use when composing any Task tool call that spawns a sub-agent or teammate.
  Activates for: single-agent delegation, batch/parallel spawns, any Task tool
  with a prompt parameter, plan-architect chunk spec creation, and deciding
  whether to delegate vs. doing work yourself. Also use when a sub-agent
  returns and you need to validate or parse the return.
  Do NOT use for: SendMessage teammate communication (use v2 message protocol),
  direct tool calls, orchestrator workflow coordination.
---

# Composing Delegation Prompts

## Core Principle

The most expensive delegation mistake is making a delegate re-discover information the spawner already has. Every fact in the spawner's context costs zero marginal tokens to include but 10-50x in re-discovery if omitted.

## Quick Reference

| Situation | Action |
|-----------|--------|
| About to call Task | Run reasoning chain (Q1-Q5), compose JSON prompt |
| You have file coordinates and know the solution | Type `targeted` |
| Delegate needs to make judgment calls | Type `guided` |
| TDD chunk from a plan | Type `tdd_chunk` |
| Scoped codebase exploration with partitioned agents | Type `exploration` |
| Source-targeted research with citations | Type `research` |
| Task requires < 3 tool calls, no judgment | Do it yourself |
| Spawning N agents in parallel | Shared `known_context` + per-agent prompt |
| Complex prompt needing data from multiple files | Use PTC to compose the JSON |

## When NOT to Use This Skill

This skill covers Task tool delegation only. Do NOT use it for:

- **SendMessage to teammates** — Teammate communication uses the v2 message protocol (`schemas/message_protocol.py`). A teammate receiving work via SendMessage `task_complete` is a different channel from a sub-agent receiving a Task prompt.
- **Direct tool calls** — If you're calling Read, Bash, Grep, etc. yourself, no delegation prompt needed.
- **Orchestrator workflow coordination** — Phase transitions, team management, and state machine logic are workflow-orchestration skill territory, not delegation.
- **Sub-agent lifecycle management** — This skill handles COMPOSITION of prompts and VALIDATION of returns. It does NOT handle WHEN to delegate, HOW MANY agents to spawn, or HOW to aggregate N returns. A future `sub-agent-delegation` skill will cover lifecycle and aggregation.

## Delegation Types

Every Task prompt is a JSON object with a `type` field. The type selects which schema validates it. Each type has different required fields because the spawner's knowledge level is different.

### Type Selection

| You have... | Use |
|-------------|-----|
| File coordinates + known solution shape | `targeted` |
| Context but delegate must make decisions | `guided` |
| Plan chunk with test specifications | `tdd_chunk` |
| Partitioned exploration targets for parallel agents | `exploration` |
| Research questions with source preferences | `research` |

**Edge cases:**

| Situation | Type | Why |
|-----------|------|-----|
| Plan chunk with < 5 lines of change, no tests | `targeted` | Not worth TDD overhead |
| Some coordinates but not all, delegate must search | `guided` | Include what you have in `file_coordinates`, list unknowns |
| Single exploration agent (no peers) | `exploration` | Still use it — `peer_scopes` can be empty, `essential_output` prevents unbounded scope |
| Codebase search for specific facts (not broad mapping) | `guided` | Exploration is for mapping structure; guided is for finding answers |

### `targeted` — "I know exactly what to do"

The spawner has file coordinates, knows the problem, and knows the output shape. The delegate executes, not discovers.

**Extra requirements beyond common:** `known_context.file_coordinates` (non-empty), `output_contract.format`

**Use when:** You can name the files, the line numbers, and the output format. If you can't, use `guided`.

```json
{
  "type": "targeted",
  "task": "Fix field name mismatches in scorers for tasks 35, 36, 38",
  "known_context": {
    "file_coordinates": [
      {"path": "/home/user/project/scripts/score.py", "lines": "683-715",
       "description": "Task 35 scorer: checks top_level_keys but result uses keys"}
    ],
    "findings": ["All three are field name mismatches"]
  },
  "output_contract": {
    "path": "/home/user/project/scripts/score.py",
    "format": "Python source file with fixed elif branches"
  },
  "scope_boundary": {"tool_budget": 10, "do_not": ["Modify tasks other than 35, 36, 38"]}
}
```

### `guided` — "Delegate needs to make decisions"

The spawner has context but the delegate must exercise judgment. Unknowns and scope_boundary are required to frame the delegate's discretion.

**Extra requirements beyond common:** `unknowns` (non-empty), `scope_boundary`

**Use when:** You know the area but not the answer. If you have no unknowns, use `targeted`.

```json
{
  "type": "guided",
  "task": "Diagnose root cause of session cleanup failure",
  "known_context": {
    "file_coordinates": [
      {"path": "/home/user/project/cleanup.py", "lines": "87-92",
       "description": "Reads session ID from file that may not exist"}
    ],
    "findings": ["Race condition suspected between cleanup and session end"]
  },
  "unknowns": [
    "Confirm whether session_start.py writes or reads the session ID file",
    "Check timing: does cleanup fire before or after session end?"
  ],
  "output_contract": {
    "path": "/home/user/project/.claude/context/queries/cleanup-diagnosis.json",
    "format": "JSON",
    "schema_example": {"root_cause": "...", "fix_location": "file:line", "confidence": "high|medium|low"}
  },
  "scope_boundary": {
    "do_not": ["Fix the bug -- diagnosis only"],
    "tool_budget": 8
  }
}
```

### `tdd_chunk` — "Implement a plan chunk with TDD"

The spawner has a plan with test specifications. The delegate follows the red-green TDD cycle.

**Extra requirements beyond common:** `test_specifications` (with `pass_a` non-empty), `success_criteria` (non-empty)

**Use when:** Delegating a chunk from `.claude/plans/`. See `specializations/tdd-delegation.md` for plan-to-prompt translation.

```json
{
  "type": "tdd_chunk",
  "task": "Implement chunk 02: input validation for Pipeline.process_frame",
  "known_context": {
    "file_coordinates": [
      {"path": "/workspace/src/pipeline.py", "lines": "340-370",
       "description": "process_frame method, currently no validation"}
    ],
    "findings": ["Chunk 01 established ProcessResult at src/types.py"]
  },
  "test_specifications": {
    "pass_a": [
      {"name": "test_validate_rejects_none", "description": "Raises ValueError on None"},
      {"name": "test_validate_accepts_ndarray", "description": "Accepts valid np.ndarray"}
    ],
    "pass_b": [
      {"name": "test_pipeline_end_to_end", "description": "Pipeline.run() produces valid result"}
    ]
  },
  "output_contract": {"path": "/workspace/src/pipeline.py", "format": "Python source file"},
  "success_criteria": ["All Pass A tests pass", "Quality gate passes: ./scripts/gate.sh"],
  "previous_chunk_decisions": ["Chunk 01 chose ProcessResult as return type"]
}
```

### `exploration` — "Scoped exploration with partitioned agents"

The spawner defines what this agent explores, what peers cover, and what essential outputs are required. Not "explore this abstract thing" but "explore this specific partition."

**Extra requirements beyond common:** `exploration_scope` (with non-empty `primary_targets`), `essential_output` (non-empty)

**Use when:** Multiple exploration agents work in parallel on different facets. Each agent gets a partition via `exploration_scope.primary_targets` and knows what peers cover via `peer_scopes`.

```json
{
  "type": "exploration",
  "task": "Map the Pipeline class hierarchy and data flow",
  "known_context": {
    "file_coordinates": [
      {"path": "/workspace/src/pipeline.py", "lines": "1-50",
       "description": "Pipeline base class definition"}
    ],
    "findings": ["Pipeline uses a stage-based architecture"]
  },
  "exploration_scope": {
    "primary_targets": ["Class hierarchy", "Data flow between stages", "Public entry points"],
    "peer_scopes": ["Config module", "Stage implementations"],
    "extension_policy": "Only extend if a primary target depends on an external module not covered by peers."
  },
  "essential_output": [
    "Complete class hierarchy diagram",
    "Data flow from input to output",
    "All public methods with signatures"
  ],
  "output_contract": {
    "path": "/workspace/.claude/context/pipeline-context.json",
    "format": "JSON context packet"
  }
}
```

### `research` — "Source-targeted research with citations"

The spawner defines research targets, preferred/blocked sources, required depth, and citation format. The delegate researches using documentation, APIs, or external sources.

**Extra requirements beyond common:** `research_scope` (with non-empty `primary_targets`), `source_directives`, `citation_requirements`, `essential_output` (non-empty)

**Use when:** Delegating documentation research, API investigation, or comparative analysis. Source directives prevent wasted time on outdated or unreliable sources.

```json
{
  "type": "research",
  "task": "Research ArUco marker detection API and calibration requirements",
  "known_context": {
    "findings": ["Project uses OpenCV 4.8", "ArUco chosen over AprilTag for speed"]
  },
  "research_scope": {
    "primary_targets": ["detectMarkers() API signature and parameters", "Camera calibration inputs"],
    "peer_scopes": ["AprilTag comparison agent covers accuracy benchmarks"],
    "extension_policy": "Extend to calibration utilities if detectMarkers requires calibrated input."
  },
  "source_directives": {
    "preferred_sources": ["docs.opencv.org", "OpenCV GitHub examples"],
    "blocked_sources": ["stackoverflow answers older than 2024"],
    "required_depth": "usage_examples"
  },
  "citation_requirements": {
    "format": "structured",
    "required_fields": ["url", "retrieved_date", "source_name"]
  },
  "essential_output": [
    "detectMarkers() full signature with parameter descriptions",
    "Minimum calibration data required",
    "Dictionary type recommendation for indoor use"
  ],
  "output_contract": {
    "path": "/workspace/.claude/research/aruco-detection.json",
    "format": "JSON research results"
  }
}
```

**Example research return with citations:**
```json
{
  "delegation_type": "research",
  "status": "completed",
  "findings": {
    "detectMarkers_signature": "cv2.aruco.detectMarkers(image, dictionary, parameters=None) -> corners, ids, rejected",
    "calibration_inputs": "Camera matrix (3x3) + distortion coefficients (5x1), obtained via cv2.calibrateCamera",
    "dictionary_recommendation": "cv2.aruco.DICT_4X4_50 for indoor — fewer bits = faster detection, 50 IDs sufficient"
  },
  "essential_output_confidence": {
    "detectMarkers() full signature with parameter descriptions": {"level": "high", "reason": "Official OpenCV 4.8 docs"},
    "Minimum calibration data required": {"level": "high", "reason": "OpenCV calibration tutorial"},
    "Dictionary type recommendation for indoor use": {"level": "medium", "reason": "Based on general guidance, not indoor-specific benchmarks"}
  },
  "citations": [
    {"claim": "detectMarkers uses adaptive thresholding internally", "url": "https://docs.opencv.org/4.8.0/d5/dae/tutorial_aruco_detection.html", "source_name": "OpenCV ArUco Tutorial", "retrieved": "2026-03-11"},
    {"claim": "DICT_4X4_50 recommended for speed-critical applications", "url": "https://docs.opencv.org/4.8.0/d9/d6a/group__aruco.html", "source_name": "OpenCV ArUco Module Reference", "retrieved": "2026-03-11"}
  ],
  "gaps": ["No ArUco vs AprilTag accuracy comparison at sub-10m indoor range"]
}
```

### Common Required Fields (all types)

| Field | Required | Purpose |
|-------|----------|---------|
| `type` | Yes | Schema selection: `targeted`, `guided`, `tdd_chunk`, `exploration`, `research` |
| `task` | Yes | What the delegate should accomplish |
| `known_context` | Yes | File coordinates, findings, context packets |
| `output_contract` | Yes | Where to write, what format |

### Optional Fields (all types)

| Field | From Q | Purpose |
|-------|--------|---------|
| `unknowns` | Q1 | Specific things to verify (required for `guided`) |
| `scope_boundary` | Q3 | Prohibitions, tool budget, file set (required for `guided`) |
| `adjacent_context` | Q1 | Fallback resources if delegate hits unexpected cases |
| `rejected_approaches` | Q4 | Dead ends with reasons -- prevents re-exploration |
| `environment` | -- | Working directory, available tools, PTC/MCP |
| `success_criteria` | Q5 | When to stop (required for `tdd_chunk`) |
| `ptc_hints` | -- | PTC loading instructions (see `specializations/ptc-delegation.md`) |
| `return_schema` | -- | JSON schema for the sub-agent's return (see Return Protocol) |
| `return_instruction` | -- | Instruction text for sub-agent return format |

## Return Protocol

Sub-agents return a single string via the Task tool result. The return protocol structures this string as JSON with typed metadata.

### How it works

1. **Include `return_schema` in the delegation prompt** — The sub-agent sees the schema and knows the expected return format. Add `return_instruction` to reinforce. Including the full schema adds ~200-400 tokens. For simple targeted tasks, `return_instruction` alone may suffice. For complex research/exploration tasks, the full schema is worth the tokens.
2. **Sub-agent outputs JSON** — Its final output is a single JSON object matching the return schema. The `delegation_type` field self-declares the type.
3. **SubagentStop hook validates** — `hooks/subagent_stop_return_validation.py` (outside this skill directory — registered in hooks config) checks the return JSON has required fields. If invalid, the sub-agent gets one retry.
4. **Parent validates via PTC** — Parent uses `scripts/validate_return.py` in PTC (background mode) to extract/validate without context pollution. Falls back to direct read if PTC fails.

### Return schema structure

All return schemas share these required fields:

| Field | Type | Description |
|-------|------|-------------|
| `delegation_type` | string (const) | Must match the delegation type |
| `status` | enum | `"completed"`, `"partial"`, `"failed"` |

Type-specific required return fields:

| Type | Required return fields |
|------|----------------------|
| `targeted` | `result` (object), `decisions_made` (array) |
| `guided` | `unknowns_resolved` (array of {unknown, answer, confidence}), `decisions_made` (array) |
| `exploration` | `findings` (object), `essential_output_confidence` (object with level+reason per target) |
| `research` | `findings` (object), `essential_output_confidence` (object), `citations` (array) |
| `tdd_chunk` | `tests_written` (array), `pass_gate_results` (object), `decisions_made` (array) |

Optional return fields (all types): `unexpected_findings`, `scope_extensions`, `cross_scope_findings`, `gaps`, `carry_forward`.

### Validation layers

1. **SubagentStop hook** — Fast structural check (<50ms). Pure stdlib JSON parsing. Blocks once if invalid; sub-agent retries via `stop_hook_active` guard.
2. **Parent-side PTC validation** — `scripts/validate_return.py` called via PTC. Handles JSON embedded in prose (brace-matching extraction). Returns `{"valid": bool, "errors": [...], "extracted": {...}}`.

### Handling invalid returns

If the SubagentStop hook passes an invalid return after one retry (via `stop_hook_active`), the parent should:

1. **Use PTC to extract** — Call `validate_return.py` in PTC. It will attempt brace-matching extraction from mixed text. If `extracted` is non-null, use whatever fields are present.
2. **Treat missing fields as gaps** — If `decisions_made` is missing, assume no decisions were surfaced. If `essential_output_confidence` is missing, treat all outputs as low confidence.
3. **Do not retry the delegation** — The sub-agent already had two chances (original + hook retry). Re-spawning will likely produce the same result. Work with what you have.
4. **Log the compliance failure** — Note in the session log that the return was unstructured. This feeds future prompt iteration.

## Delegation Cost Estimation

Before delegating, estimate whether the overhead is worth it.

**Spawn overhead:** ~15-20 tool calls (init, skill loading, context reading).

**Delegation cost heuristic:** Count items in `file_coordinates` + `unknowns` + `essential_output`. Each item is ~2-4 tool calls for the delegate. If `(sum of items) * 3 < 15`, do it yourself.

| Items to process | Estimated delegate calls | Verdict |
|-----------------|-------------------------|---------|
| 1-3 | 3-12 | Do it yourself (< spawn overhead) |
| 4-6 | 12-24 | Delegate — net positive |
| 7+ | 21+ | Delegate — clear win |

## Insufficient Context Recovery

When Q1-Q5 reveals missing context:

| What's missing | Action |
|---------------|--------|
| File coordinates (Q2: "I don't know which files") | Switch to `exploration` — the delegate will find them |
| Solution shape unknown (Q5: "I don't know what done looks like") | Switch to `guided` — list unknowns, let delegate decide |
| Entire problem domain unclear | Spawn `exploration` first, then compose `targeted`/`tdd_chunk` from findings |
| Some coordinates known, some missing | Use `guided` — include what you have in `file_coordinates`, gaps in `unknowns` |

Do NOT compose a low-quality `targeted` prompt with vague coordinates. A well-formed `guided` prompt outperforms a poorly-formed `targeted` prompt every time.

## Pre-Delegation Reasoning Chain

Before creating any Task call, run these 5 questions. They produce the content for JSON fields.

**Q1: What do I know that the delegate will not?** -> `known_context.findings` + `adjacent_context`
WHY: Zero marginal tokens to include, 10-50x to re-discover.

**Q2: What files did I read?** -> `known_context.file_coordinates` + `known_context.context_packets`
WHY: "The scoring code" = 5+ search calls. `/home/.../score.py:683-715` = 1 call.

**Q3: What decisions constrain the solution?** -> `known_context.findings` + `scope_boundary`
WHY: Without constraints, delegates re-explore rejected approaches.

**Q4: What did I try or reject?** -> `rejected_approaches`
WHY: Most commonly omitted, most expensive to re-discover.

**Q5: What does "done" look like?** -> `output_contract` + `success_criteria`
WHY: output_contract = WHERE/FORMAT. success_criteria = WHEN TO STOP. Need both.

## PTC Prompt Composition

When the prompt draws from context packets, session logs, or plan files, use PTC to compose it. Raw data stays in PTC container — only the assembled JSON enters your context.

```python
import json
with open("/workspace/.claude/context/pipeline-context.json") as f:
    ctx = json.load(f)
prompt = {
    "type": "targeted",
    "task": "Add validation to process_frame",
    "known_context": {
        "file_coordinates": [
            {"path": f"/workspace/{b['file']}", "lines": str(b.get('line', '')),
             "description": b.get("role", "")}
            for b in ctx.get("structure", {}).get("blocks", [])
        ]
    },
    "output_contract": {"path": "/workspace/src/pipeline.py", "format": "Python source file"}
}
print(json.dumps(prompt, indent=2))
```

**When to use:** Prompt draws from 2+ source files, batch delegation, complex extraction.
**When to compose manually:** Simple delegation, context already in your head.

## Batch Delegation

Compose shared `known_context` once, merge per-agent. For `exploration` batch, partition `primary_targets` across agents and list each agent's scope in peers' `peer_scopes`.

```python
shared = {"known_context": {"file_coordinates": [...], "findings": [...]}}
agents = [
    {"type": "targeted", "task": "Analyze A", "output_contract": {"path": "/workspace/a.json", "format": "JSON"}},
    {"type": "targeted", "task": "Analyze B", "output_contract": {"path": "/workspace/b.json", "format": "JSON"}},
]
for agent in agents:
    prompt = {**shared, **agent}
    print(json.dumps(prompt, indent=2))
```

## When to Do It Yourself

| Condition | Action |
|-----------|--------|
| Task requires < 3 tool calls AND no judgment | Do it yourself |
| Task is a single Bash command | Do it yourself |
| Reading one file, extracting one fact | Do it yourself |
| Delegation cost estimate < spawn overhead (see heuristic above) | Do it yourself |

## Validation

### Delegation prompt validation

The hook at `scripts/validate_delegation_prompt.py` requires JSON with a `type` field and dispatches to type-specific validation. Schemas at `schemas/{type}.schema.json`.

**What the hook validates:** Required fields per type, absolute paths, vague discovery verbs in `task`.
**What it cannot validate:** Context completeness, path correctness, scope appropriateness. That's what the reasoning chain is for.

**Vague verb check limitations:** The hook checks for standalone "explore", "investigate", "figure out", "look into" not followed by specific targets (whether, if, /, function, etc.). The negative lookahead covers common cases but may produce false positives. If the hook blocks a legitimate prompt, add `SKIP_DELEGATION_VALIDATION` to the prompt or rephrase the `task` field with more specific language.

### Return validation

The script at `scripts/validate_return.py` validates sub-agent return JSON. The SubagentStop hook at `hooks/subagent_stop_return_validation.py` (outside this skill directory — registered in hooks config) uses it to block invalid returns.

**What return validation checks:** `delegation_type` present and valid, `status` present and valid, type-specific required fields.
**What it handles gracefully:** JSON embedded in prose text (extracted via brace matching).

## Critical Rules

- **Prompts are JSON with a `type` field.** WHY: Type selection enforces the right required fields for your delegation pattern. No guessing.

- **Run the reasoning chain before every Task call.** WHY: Q1-Q5 surface knowledge you forgot you had. They map directly to JSON fields.

- **Absolute file paths only.** WHY: Relative paths break across working directories.

- **`output_contract` is mandatory (all types).** WHY: 24 documented instances of unusable output format.

- **Include `rejected_approaches` when you have them.** WHY: Most commonly omitted, most expensive to re-discover.

- **Use PTC to compose complex prompts.** WHY: Keeps raw data out of spawner's context.

- **Include `return_schema` for structured returns.** WHY: Sub-agents have no hooks or skills -- their only instructions are the delegation prompt. The return schema must be in the prompt.

- **Returns must include `delegation_type` and `status`.** WHY: The SubagentStop hook dispatches validation by type. Without `delegation_type`, it cannot validate.

## Anti-Rationalization

| Excuse | Reality |
|--------|---------|
| "The delegate will figure it out" | That IS discovery. 10-50x more expensive than execution. |
| "I don't have time for a detailed prompt" | 5-minute prompt saves 30 minutes of re-discovery. |
| "I don't know what type to pick" | If you have coordinates -> targeted. If delegate decides -> guided. If TDD -> tdd_chunk. If exploring codebase -> exploration. If researching docs -> research. |
| "JSON is overkill" | If < 3 calls, do it yourself. If you're delegating, you need structure. |
| "I'll let them ask if they need something" | Round-trip = 2 turns minimum. Front-loading = 0 turns. |
| "The return will be obvious" | Freeform returns lose confidence, decisions, gaps, and cross-scope findings. Structure the return. |

## Eval Scenarios

**Should trigger:** (1) "I need to spawn a sub-agent to analyze the benchmark results" — intent to delegate. (2) "Let me create a Task for the coder to implement chunk 3" — explicit Task mention. (3) "I'll have two parallel agents explore the codebase" — batch delegation. (4) "The plan says chunk 4 needs these test specs" — plan-architect chunk spec. (5) "Should I delegate this or do it myself?" — self-delegation decision.

**Should NOT trigger:** (1) "I'll send a message to the planner asking for status" — SendMessage, use v2 message protocol. (2) "Let me read this file myself" — self-action. (3) "The orchestrator should coordinate the next phase" — workflow coordination. (4) "I need to run pytest on the coder's output" — direct tool call.

## References

For 7 before/after anti-patterns, read `references/anti-patterns.md`.
For PTC delegation patterns, read `specializations/ptc-delegation.md`.
For TDD chunk delegation patterns, read `specializations/tdd-delegation.md`.
For delegation schemas, read `schemas/{type}.schema.json` for your specific type. Each schema shares common fields (known_context, output_contract, environment, etc.) — only read the schema for the type you're composing.
For return schemas, read `schemas/return-{type}.schema.json` for the type you expect back.
For return validation, read `scripts/validate_return.py`.
