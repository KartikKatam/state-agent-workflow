# Sub-Agent Detailed Specifications

**Purpose:** Design reference for all 9 sub-agent types. Covers the 8 dimensions not fully specified in `AGENT-ARCHITECTURE.md` §4.2: detailed behavioral steps, input contracts, output contracts, validation rules, tool access, file access globs, failure modes, and state machine integration.

**Relationship to other documents:**
- `AGENT-ARCHITECTURE.md` §4.2 — defines WHAT each sub-agent is (structural spec, return schemas, inheritance)
- `sub-agents/*.md` — agent definition files (operational instructions the sub-agent reads)
- **This document** — design reference for HOOKS, STATE MACHINES, and TEAMMATE DIRECTORS that interact with sub-agents

**Who reads this:**
- Hook authors: SubagentStart, SubagentStop, PreToolUse validation rules
- State machine authors: guard conditions, transition triggers
- Teammate definition authors: delegation composition, result review logic
- NOT the sub-agents themselves — they read their `.md` agent definition files

---

## Common Conventions

### Status Values

All sub-agents return one of three statuses:

| Status | Meaning | Parent Action |
|--------|---------|---------------|
| `completed` | All essential output produced, quality checks passed | Process return normally |
| `partial` | Some output produced, gaps documented | Decide: re-dispatch for gaps, or work with partial |
| `failed` | Unable to produce meaningful output | Investigate cause, retry with better prompt, or escalate |

### Ephemeral State File

All sub-agents write progress to `.claude/temp/sub-agent-state-{delegation_id}.json` at end of each turn. This is the crash-recovery path — read by the dispatching teammate only if the sub-agent didn't return cleanly. Deleted when the task merges.

### Cold-Start as Primary Path

Sub-agent reliability does NOT depend on resume. A fresh sub-agent gets: original delegation prompt + previous sub-agent's return (`decisions_made`, `carry_forward`, `files_modified`, `quality_gate_result`). Resume via the Agent tool's `resume` parameter is attempted first (fast path — preserves full context window). If resume fails or is unavailable, cold-start with the previous return is functionally equivalent.

**For multi-gate sub-agents (plan-checker):** When the same sub-agent is dispatched across multiple review gates (e.g., plan-checker at PHASE_REVIEW → TASK_REVIEW → DETAIL_REVIEW), each dispatch includes `previous_verification_report` — the structured return from the prior gate. Resume is attempted first. On cold-start, the fresh instance reads the prior report and has the same accumulated context.

### Handling Partial Returns

When a sub-agent returns `status: "partial"`, the dispatching teammate checks the return's `carry_forward` and `decisions_made` fields to understand what was completed and what remains. The teammate then **deliberates** (via its existing EVIDENCE_REVIEW or RESULT_REVIEW think prompt) whether to:

1. **Accept partial** — the essential outputs are covered, gaps are tolerable. Proceed with available results.
2. **Re-dispatch narrower** — the missing information is valuable. Compose a new delegation prompt with a narrower scope targeting the specific gaps, including the partial return as context.
3. **Self-investigate** — the gap is small enough that the teammate can fill it via PTC or direct Read, without a sub-agent round-trip.

No special mechanism is needed — the `status` field in the return is already checked by the SubagentStop hook (structural validation), and the partial return flows through the teammate's existing deliberation cycle.

### Validation Layers

Two conceptually distinct layers, enforced at different points:

1. **SubagentStop hook (structural)** — Fires when the sub-agent tries to terminate. Pure stdlib JSON parsing (<50ms). Checks: return JSON matches schema, required fields present, quality gate results included, `files_modified` non-empty, `decisions_made` populated, delegation prompt's `task_id` and `sub_agent_type` match the return. Can reject and force the sub-agent to continue ("your return is missing X"). Implementation helper: `scripts/validate_return.py` handles JSON embedded in prose, returns `{"valid": bool, "errors": [...], "extracted": {...}}`.

2. **Parent semantic review (Coder's RESULT_REVIEW / teammate equivalent)** — After the sub-agent returns, the dispatching teammate evaluates whether the sub-agent *actually did the right thing*. Did the test-writer write tests that fail for the right reasons (AssertionError, not SyntaxError)? Did the implementer's changes address the task requirements? Does the debugger's fix make sense? This is judgment the hook cannot perform — it requires understanding the delegation prompt's intent. The parent decides: advance the pipeline, re-dispatch, or escalate.

**The split:** Hooks catch "you forgot to run the quality gate" or "your return JSON is malformed." The parent catches "your tests fail with ImportError but the function exists — you imported from the wrong module."

### Quality Gate Hooks (Universal)

**Lint and type checking run as a PostToolUse hook on Write/Edit — universal, phase-independent.** Every sub-agent that writes files gets `ruff format`, `ruff check`, and `pyright` enforced automatically on each write. This catches formatting and type errors immediately, not just at return time.

**Pytest is NOT a hook.** Test execution and interpretation is the sub-agent's responsibility because the meaning of test results is phase-dependent:
- Red phase (test-writer): tests MUST fail — passing tests indicate a test design problem
- Green phase (implementer): tests MUST pass — failing tests indicate an implementation problem
- Debug phase (debugger): tests must pass after fix — failing tests indicate the fix didn't work

A hook cannot make this semantic distinction. The sub-agent runs pytest, interprets results per its phase instructions, and reports them in the return. The SubagentStop hook validates that `quality_gate_result` is present and lint/type passed; the parent validates that pytest results match phase expectations.

### Stall Threshold Principle

Stall thresholds are calibrated to task scope: **3 for verification/analysis tasks** (plan-checker, audit-checker, debugger — quick feedback loops where stalling means the problem needs human judgment) and **5 for implementation/execution tasks** (implementer, Tester execution cycle — more iteration is expected before declaring stall). This is not arbitrary — verification sub-agents are checking existing work (bounded), while implementation sub-agents are creating new work (open-ended).

### INV-1 and Execution Results

**All agents may see RESULTS of code or tests being run** — stdout, stderr, pytest output, stack traces, error messages. INV-1 restricts reading SOURCE FILES, not execution output. A debugger seeing `assert result == expected` in a pytest stack trace is not an INV-1 violation — it's seeing what FAILED, not reading test logic. The distinction: reading a test file to understand its structure and assertions = violation. Seeing pytest output that includes assertion details from a failed test = acceptable.

This applies universally: implementers see test results (pass/fail, error messages) but not test source. Test-writers see implementation error messages (ImportError, AttributeError) but not source code. Audit-checkers read both sides' source (they're read-only, INV-1 compliant).

### Daemon State Tracking

Sub-agents do NOT report state to the daemon. Their lifecycle is short enough that live observability is not worth the overhead. Auditability comes from:
- **Post-hoc:** The structured return (`decisions_made`, `carry_forward`, `files_modified`) is a complete record of what happened
- **Crash recovery:** Ephemeral state files written at end of each turn (see below)
- **Parent tracking:** The dispatching teammate tracks sub-agent status in its own task metadata

---

## 1. codebase-scout

**Identity:** General-purpose codebase exploration agent. Reads files, analyzes structure, traces dependencies, extracts patterns.

**Delegation type received:** `exploration`
**Return type:** `ExplorationReturn`
**Dispatched by:** Explorer
**Model:** Sonnet or Haiku (Explorer specifies per dispatch)

### 1.1 Behavioral Spec (Cold Start → Return)

1. **Parse delegation prompt** — Extract `exploration_scope.primary_targets`, `peer_scopes`, `essential_output`, output path
2. **Initialize PTC** — Load project structure into PTC container. Set up working namespace
3. **Map partition structure** — Use PTC `os.walk` or Glob to build file tree of assigned partition before reading details
4. **Execute 6-point checklist** per AGENT-ARCHITECTURE.md §4.2:
   - File catalog (path, line count, purpose)
   - Internal architecture (types, inheritance, call flow)
   - Patterns (error handling, logging, config, naming — one example per pattern)
   - External interfaces (imports from outside partition, exports consumed externally)
   - Implicit contracts (ordering, init sequences, env vars, singletons)
   - Unknowns (ambiguous/undocumented items with confidence < 0.6)
5. **Apply depth requirement** — Surface structure (points 1,4 only), Implementation details (all 6), Specific questions (focus on answers)
6. **Check extension policy** — If primary target requires files outside partition, check policy. Only extend if policy permits and dependency is blocking
7. **Note cross-scope findings** — Information relevant to `peer_scopes` goes in `cross_scope_findings` without exploring it
8. **Score confidence** per finding (direct: 0.8-1.0, inference: 0.6-0.8, indirect: 0.3-0.6, speculation: 0.0-0.3)
9. **Write output file** to `output_contract.path` if specified
10. **Return structured JSON**

### 1.2 Input Contract

**Required fields in delegation prompt:**

| Field | Type | Validation |
|-------|------|------------|
| `type` | `"exploration"` | Exact match |
| `task` | string | Non-empty, no vague discovery verbs without targets |
| `exploration_scope.primary_targets` | array | Non-empty |
| `exploration_scope.peer_scopes` | array | Can be empty for single-scout dispatch |
| `exploration_scope.extension_policy` | string | Non-empty |
| `essential_output` | array | Non-empty |
| `output_contract.path` | string | Absolute path under `.claude/context/` |
| `output_contract.format` | string | Non-empty |

**Optional fields:**

| Field | Purpose |
|-------|---------|
| `known_context.file_coordinates` | Starting points for exploration |
| `known_context.findings` | What Explorer already knows |
| `scope_boundary.tool_budget` | Max tool calls |
| `scope_boundary.do_not` | Explicit exclusions |
| `known_context.context_packets` | Existing packets to avoid re-exploring |
| `adjacent_context` | Fallback resources |
| `ptc_hints` | PTC loading instructions |

### 1.3 Output Contract

```json
{
  "delegation_type": "exploration",
  "status": "completed|partial|failed",
  "findings": {
    "<primary_target>": {
      "file_catalog": [{"path": "...", "lines": 0, "purpose": "..."}],
      "types": [{"name": "...", "file": "...:line", "kind": "class|function|type", "bases": [], "usage": "..."}],
      "patterns": [{"name": "...", "example": "...:line", "description": "..."}],
      "external_interfaces": {
        "imports_from_outside": [{"module": "...", "used_for": "..."}],
        "exports_consumed_by": [{"symbol": "...", "consumed_by": "..."}]
      },
      "implicit_contracts": [{"type": "...", "description": "...", "file": "..."}],
      "unknowns": [{"description": "...", "file": "...", "confidence": 0.0, "needs_verification": true}]
    }
  },
  "essential_output_confidence": {
    "<essential_output_item>": {"level": "high|medium|low", "reason": "..."}
  },
  "scope_extensions": [
    {"target": "...", "reason": "...", "policy_justification": "..."}
  ],
  "cross_scope_findings": [
    {"peer_scope": "...", "finding": "...", "file": "..."}
  ],
  "gaps": ["..."]
}
```

### 1.4 Validation Rules (SubagentStop Hook)

| # | Check | Rule | Severity |
|---|-------|------|----------|
| 1 | Structure | JSON with `delegation_type: "exploration"` and valid `status` | Block + retry |
| 2 | Essential output coverage | Every `essential_output` item has entry in `essential_output_confidence` | Block + retry |
| 3 | Confidence populated | Each confidence entry has `level` (high/medium/low) and `reason` (non-empty string) | Block + retry |
| 4 | Findings non-empty | `findings` has ≥1 key (unless `status: "failed"`) | Block + retry |
| 5 | Write target exists | If `output_contract.path` was specified, file exists at that path | Warn only |
| 6 | Write boundary | No files written outside `.claude/context/**` and `.claude/temp/**` | Block (hard, no retry) |

### 1.5 Tool Access

| Tool | Access | Notes |
|------|--------|-------|
| Read | **Yes** | Primary exploration tool |
| Grep | **Yes** | Pattern search across codebase |
| Glob | **Yes** | File discovery |
| Bash | **Yes** | `git log`, `git blame`, directory listing |
| PTC | **Yes** | AST analysis, batch extraction, dependency graphs |
| Write | **Limited** | Only `.claude/context/**` and `.claude/temp/sub-agent-state-*.json` |
| Edit | **No** | Scouts don't modify existing files |
| WebSearch | **No** | Codebase only |
| WebFetch | **No** | Codebase only |
| Task | **No** | Sub-agents cannot spawn sub-agents |

### 1.6 File Access Globs

| Direction | Glob | Purpose |
|-----------|------|---------|
| Read | `**/*` | Broad read access for exploration |
| Write allow | `.claude/context/**` | Context packet output |
| Write allow | `.claude/temp/sub-agent-state-*.json` | Ephemeral progress |
| Write deny | `**/*` (everything else) | No source, test, plan, or research writes |

### 1.7 Failure Modes

| Failure | Detection | Handling | Parent Recourse |
|---------|-----------|----------|-----------------|
| Partition too large | Tool budget exceeded before covering all targets | Return `partial`, document coverage in `findings`, gaps in `gaps` | Re-dispatch with narrower partition or higher budget |
| Primary target not found | Glob/Read returns empty for expected paths | Check adjacent paths, report in `essential_output_confidence` as low with "not found" reason | Explorer adjusts prompt with corrected paths |
| PTC unavailable | `ptc_execute` returns error | Fall back to Read/Grep/Bash — same output, more tool calls | Transparent to parent |
| Context pressure | Approaching 90% window | Write partial to ephemeral state, return `partial` with `carry_forward` | Fresh scout with delegation + previous return |
| Contradictory findings | File A imports X from B, but B doesn't export X | Report both with evidence, set confidence "low" | Explorer resolves in synthesis |
| Extension policy violation | Target depends on peer's scope | Report in `cross_scope_findings`, don't explore. Set affected output confidence to "low" | Explorer requests additional scout for gap |

### 1.8 State Machine Integration

**Dispatching teammate:** Explorer

| Explorer State | Scout Phase |
|---------------|-------------|
| `DELIBERATION` | Explorer decides to dispatch scout(s), writes delegation JSON |
| `SUB_AGENT_DISPATCH` | Orchestrator spawns scout(s) — scout begins cold start |
| `SUB_AGENT_DISPATCH` (waiting) | Scout executing (steps 1-10) |
| `SYNTHESIS` | Scout returns — Explorer collects return in PTC |
| `OUTPUT_VALIDATION` | Explorer validates essential output coverage, schema compliance |

**Parallel dispatch:** Multiple scouts simultaneously (one per partition). Explorer waits for ALL before SYNTHESIS. If any returns `partial`, Explorer can request follow-up scout for gaps while synthesizing completed results.

**Guard conditions:**
- `all_scouts_returned`: All dispatched scouts have returned (any status) → enter SYNTHESIS
- `essential_output_complete`: All essential outputs have confidence ≥ "medium" → PASS; otherwise → retry or accept partial

---

## 2. research-scout

**Identity:** General-purpose research agent. Searches web, reads docs, extracts API info, compares libraries.

**Delegation type received:** `research`
**Return type:** `ResearchReturn`
**Dispatched by:** Researcher
**Model:** Sonnet or Haiku (Researcher specifies per dispatch)

### 2.1 Behavioral Spec (Cold Start → Return)

1. **Parse delegation prompt** — Extract `research_scope.primary_targets`, `source_directives`, `citation_requirements`, `essential_output`
2. **Plan search order** — Order targets by dependency. Route each: WebSearch for discovery, WebFetch for deep extraction, Read for local refs
3. **Apply source directives** — Prioritize `preferred_sources`, skip `blocked_sources`. Match `required_depth`: surface (signatures), usage_examples (working code), deep (internals, edge cases)
4. **Execute research** per target:
   - WebSearch with version-pinned queries
   - WebFetch for authoritative pages
   - PTC for content extraction (trafilatura or manual parsing)
   - Track citation for every claim
5. **Score confidence** per finding using source quality table (official docs: 0.9-1.0, GitHub: 0.8-0.9, tutorials: 0.7-0.8, SO: 0.6-0.7, blogs: 0.5-0.6, single source: 0.3-0.5)
6. **Check for premise contradictions** — If findings show the question's premise is wrong (deprecated API, wrong version), report prominently as first finding
7. **Write output file** to `output_contract.path` if specified
8. **Return structured JSON** with findings, confidence, citations, gaps

### 2.2 Input Contract

**Required fields in delegation prompt:**

| Field | Type | Validation |
|-------|------|------------|
| `type` | `"research"` | Exact match |
| `task` | string | Non-empty |
| `research_scope.primary_targets` | array | Non-empty |
| `source_directives.preferred_sources` | array | Can be empty |
| `source_directives.blocked_sources` | array | Can be empty |
| `source_directives.required_depth` | string | One of: `surface`, `usage_examples`, `deep` |
| `citation_requirements.format` | string | `structured` or `inline` |
| `citation_requirements.required_fields` | array | Non-empty, subset of `[url, retrieved_date, source_name, claim]` |
| `essential_output` | array | Non-empty |
| `output_contract.path` | string | Absolute path under `.claude/research/` |
| `output_contract.format` | string | Non-empty |

### 2.3 Output Contract

```json
{
  "delegation_type": "research",
  "status": "completed|partial|failed",
  "findings": {
    "<primary_target>": {
      "summary": "...",
      "details": { ... },
      "sources_consulted": ["url1", "url2"]
    }
  },
  "essential_output_confidence": {
    "<essential_output_item>": {"level": "high|medium|low", "reason": "..."}
  },
  "citations": [
    {"claim": "...", "url": "...", "source_name": "...", "retrieved": "YYYY-MM-DD"}
  ],
  "gaps": ["..."],
  "cross_scope_findings": [
    {"peer_scope": "...", "finding": "..."}
  ]
}
```

### 2.4 Validation Rules (SubagentStop Hook)

| # | Check | Rule | Severity |
|---|-------|------|----------|
| 1 | Structure | JSON with `delegation_type: "research"` and valid `status` | Block + retry |
| 2 | Essential output coverage | Every `essential_output` item in `essential_output_confidence` | Block + retry |
| 3 | Citations non-empty | `citations` array has ≥1 entry (unless `status: "failed"`) | Block + retry |
| 4 | Citation fields | Each citation has all `citation_requirements.required_fields` | Block + retry |
| 5 | Findings non-empty | `findings` has ≥1 key (unless `status: "failed"`) | Block + retry |
| 6 | No blocked sources | No citation URL matches patterns in `blocked_sources` | Warn only |
| 7 | Write boundary | No files written outside `.claude/research/**` and `.claude/temp/**` | Block (hard) |

### 2.5 Tool Access

| Tool | Access | Notes |
|------|--------|-------|
| Read | **Yes** | Any local file — source, tests, research, context, designs |
| Grep | **Yes** | Search any local files for grounding research |
| Glob | **Yes** | Find relevant files in codebase |
| Bash | **No** | No shell access for research |
| PTC | **Yes** | Content extraction from fetched pages |
| WebSearch | **Yes** | Primary research tool |
| WebFetch | **Yes** | Retrieve specific documentation pages |
| Write | **Limited** | Only `.claude/research/**` and `.claude/temp/**` |
| Edit | **No** | Don't modify existing research files |
| Task | **No** | Sub-agents cannot spawn sub-agents |

**Note:** Research-scout is read-only (never writes source/tests), so INV-1 does not apply. Broad read access helps ground web findings against actual codebase patterns.

### 2.6 File Access Globs

| Direction | Glob | Purpose |
|-----------|------|---------|
| Read | `**/*` | Broad read access — source, tests, configs, research, context, designs |
| Read | Web URLs | Via WebSearch/WebFetch |
| Write allow | `.claude/research/**` | Research output |
| Write allow | `.claude/temp/sub-agent-state-*.json` | Ephemeral progress |
| Write deny | Everything else | Read-only for all non-research files |

### 2.7 Failure Modes

| Failure | Detection | Handling | Parent Recourse |
|---------|-----------|----------|-----------------|
| Source unavailable | WebFetch returns 404/timeout | Fall back to general WebSearch. Report unavailable source in `gaps`. Downgrade confidence | Researcher re-dispatches with relaxed sources |
| Contradictory sources | Source A says X, Source B says Y | Report both with citations. Set confidence "medium" or "low" | Researcher resolves in synthesis |
| Insufficient depth | `required_depth: "deep"` but only surface available | Return `partial`. Set affected confidence to "low" | Researcher accepts partial or re-dispatches with different sources |
| All sources blocked | Every relevant result matches `blocked_sources` | Return `failed`. Document in `gaps` | Researcher re-dispatches with relaxed directives |
| PTC unavailable | `ptc_execute` error | WebSearch/WebFetch still work. In-context extraction (more tokens) | Transparent to parent |
| Rate limiting | WebSearch/WebFetch throttled | Try alternative queries. If fully blocked, return `partial` | Researcher waits and re-dispatches |
| Context pressure | 90% window | Write partial to ephemeral state, return `partial` | Fresh scout with delegation + previous return |

### 2.8 State Machine Integration

**Dispatching teammate:** Researcher

| Researcher State | Scout Phase |
|-----------------|-------------|
| `DELIBERATION` | Researcher decomposes query into avenues, writes delegation JSON per scout |
| `SUB_AGENT_DISPATCH` | Orchestrator spawns scout(s) |
| `SUB_AGENT_DISPATCH` (waiting) | Scout executing |
| `SEARCH_STRATEGY_FALLBACK` | If primary tool failed, Researcher adjusts source directives and re-dispatches |
| `SYNTHESIS` | Scout returns — Researcher collects in PTC |
| `OUTPUT_VALIDATION` | Researcher validates citations, cross-references across scouts |

**Parallel dispatch:** Multiple scouts simultaneously (one per research avenue). Researcher waits for ALL before synthesis.

**Guard conditions:**
- `all_scouts_returned`: All dispatched scouts returned → enter SYNTHESIS
- `citation_coverage_sufficient`: All findings have citations → PASS
- `essential_output_complete`: All essential outputs have confidence ≥ "medium" → PASS

---

## 3. plan-checker

**Identity:** Pre-execution plan verifier. Fresh-eyes gate between planning and implementation.

**Delegation type received:** `guided` (exercises judgment on 8 verification dimensions)
**Return type:** Custom `plan_verification` (extends `GuidedReturn`)
**Dispatched by:** Planner
**Model:** Sonnet (fixed)

### 3.1 Behavioral Spec (Cold Start → Return)

1. **Read design document FIRST** — Before looking at the plan. Build mental model of requirements, constraints, acceptance criteria
2. **Read implementation plan** — Full plan: phases, tasks, waves, dependencies, acceptance criteria
3. **Read codebase context** — Context packets for technical feasibility checks
4. **Verify 8 dimensions systematically:**
   - **Goal coverage** — Every design requirement maps to ≥1 task. Flag orphan requirements
   - **Task completeness** — Each task has inputs, outputs, acceptance criteria, file targets
   - **Dependency accuracy** — `depends_on` links valid, no circular deps
   - **Scope boundaries** — No task has > 5 file targets or > 3 distinct concerns
   - **Risk identification** — Tasks touching concurrency/external APIs/complex state have fallback plans
   - **Wave feasibility** — Tasks in same wave share NO write targets (file target comparison)
   - **Acceptance criteria clarity** — Each criterion is mechanically verifiable (can write a test for it)
   - **Technical feasibility** — Referenced APIs/patterns exist in codebase context
5. **Use PTC for mechanical checks** — Cycle detection in dependency graph, wave file-independence matrix, goal coverage matrix
6. **Compile verdict** — PASS (all dimensions pass) or REVISE (any fails, with specific suggestions)
7. **Return structured JSON**

### 3.2 Input Contract

**Required fields in delegation prompt:**

| Field | Type | Validation |
|-------|------|------------|
| `type` | `"guided"` | Exact match |
| `task` | string | Must reference plan verification |
| `known_context.file_coordinates` | array | Must include plan file, design doc, context file paths |
| `unknowns` | array | Specific verification questions |
| `output_contract.path` | string | Absolute path |
| `output_contract.format` | string | "JSON verification report" |
| `scope_boundary.do_not` | array | Must include "Do not modify the plan" |

**Conditional fields:**

| Field | When | Purpose |
|-------|------|---------|
| `known_context.findings` | Re-check after revision | Previous REVISE dimensions to re-verify |
| `previous_verification_report` | Multi-gate dispatch (resume failed or cold-start) | Full structured return from the prior gate's plan-checker invocation. Gives the fresh instance accumulated context: which dimensions were verified, prior verdicts, prior revision suggestions. |

### 3.3 Output Contract

```json
{
  "delegation_type": "plan_verification",
  "status": "completed|partial|failed",
  "verdict": "PASS|REVISE",
  "dimensions": {
    "goal_coverage": {"pass": true, "notes": "...", "evidence": [{"requirement": "...", "covered_by": ["task-01"]}]},
    "task_completeness": {"pass": true, "notes": "..."},
    "dependency_accuracy": {"pass": true, "notes": "..."},
    "scope_boundaries": {"pass": true, "notes": "..."},
    "risk_identification": {"pass": true, "notes": "..."},
    "wave_feasibility": {"pass": true, "notes": "..."},
    "acceptance_criteria_clarity": {"pass": true, "notes": "..."},
    "technical_feasibility": {"pass": true, "notes": "..."}
  },
  "revision_suggestions": [
    {"dimension": "...", "issue": "...", "suggestion": "..."}
  ],
  "unknowns_resolved": [
    {"unknown": "...", "answer": "...", "confidence": "high|medium|low"}
  ],
  "decisions_made": []
}
```

### 3.4 Validation Rules (SubagentStop Hook)

| # | Check | Rule | Severity |
|---|-------|------|----------|
| 1 | Structure | JSON with `delegation_type: "plan_verification"` and valid `status` | Block + retry |
| 2 | Verdict present | `verdict` is `"PASS"` or `"REVISE"` | Block + retry |
| 3 | All 8 dimensions | `dimensions` has all 8 keys | Block + retry |
| 4 | Dimension structure | Each dimension has `pass` (bool) and `notes` (string) | Block + retry |
| 5 | Verdict consistency | PASS → all `pass: true`. Any `pass: false` → verdict must be REVISE | Block + retry |
| 6 | Revision suggestions | If REVISE, `revision_suggestions` non-empty | Block + retry |
| 7 | No files written | No Write/Edit tool calls executed | Block (hard) |

### 3.5 Tool Access

| Tool | Access | Notes |
|------|--------|-------|
| Read | **Yes** | Any local file — plans, design docs, context, source, tests, configs |
| Grep | **Yes** | Search any local files |
| Glob | **Yes** | Find codebase files for feasibility checks |
| Bash | **No** | Read-only verification, no shell |
| PTC | **Yes** | Cycle detection, wave validation, coverage matrix |
| Write | **No** | Strictly read-only |
| Edit | **No** | Strictly read-only |
| WebSearch | **No** | Verifies against codebase, not web |
| Task | **No** | Sub-agents cannot spawn sub-agents |

**Note:** Plan-checker is strictly read-only (never writes anything), so INV-1 does not apply. Broad read access including tests helps verify test plan feasibility against existing test infrastructure.

### 3.6 File Access Globs

| Direction | Glob | Purpose |
|-----------|------|---------|
| Read | `**/*` | Broad read access — plans, designs, context, source, tests, configs |
| Write allow | `.claude/temp/sub-agent-state-*.json` | Ephemeral progress only |
| Write deny | Everything else | Strictly read-only |

### 3.7 Failure Modes

| Failure | Detection | Handling | Parent Recourse |
|---------|-----------|----------|-----------------|
| Plan file not found | Read returns error | Return `failed` | Planner re-dispatches with correct path |
| Design doc missing | Read returns error | Return `failed` — cannot verify without design | Planner provides design doc |
| Insufficient codebase context | Context packets don't cover plan's modules | Return `partial`. Set `technical_feasibility.pass: false` | Planner requests Explorer for context, re-dispatches |
| Ambiguous plan structure | Plan doesn't follow schema (missing phases/tasks/waves) | Return `partial`. Document structural issues as `revision_suggestions` | Planner fixes structure |
| Context pressure | Large plan + design consuming window | Use PTC for mechanical checks. If still pressured, return `partial` with dimensions verified so far | Planner accepts partial or re-dispatches for remaining |

### 3.8 State Machine Integration

**Dispatching teammate:** Planner

| Planner State | Plan-Checker Phase |
|--------------|---------------------|
| `PHASE_REVIEW` | **Spawn** (first invocation) — verify phase-level dimensions |
| `TASK_REVIEW` | **Resume** — verify task-level dimensions (has phase context) |
| `DETAIL_REVIEW` | **Resume** — verify detail-level dimensions |
| `TEST_PLAN_REVIEW` | **Resume** — verify test architecture dimensions |

**Planner-Checker loop:** Planner dispatches → checker returns → if REVISE: Planner fixes → re-dispatches. Max 3 consecutive stall iterations (no progress detected) before escalation to user.

**Guard conditions:**
- `checker_verdict_pass`: Plan-checker returned PASS → Planner presents to user
- `checker_verdict_revise`: Plan-checker returned REVISE → Planner revises before user sees it
- `stall_threshold_exceeded`: 3 consecutive no-progress iterations → escalate to user

---

## 4. audit-checker

**Identity:** Adversarial plan adherence and quality auditor. Checks implementation against design, tests against behaviors, code against conventions. Read-only — reports only.

**Delegation type received:** `guided` (exercises judgment on severity and verdict)
**Return type:** Custom `audit` (extends `GuidedReturn`)
**Dispatched by:** Auditor
**Model:** Sonnet (fixed)

### 4.1 Behavioral Spec (Cold Start → Return)

1. **Read design document FIRST** — Before any code. Build checklist of behavioral requirements: "What MUST be true for the design to be satisfied?"
2. **Read implementation plan** — Task scope, acceptance criteria, expected behavior
3. **Read implementation source** — Trace code paths. Check: does code implement design intent, not just "work"?
4. **Read test code** — Check: do tests verify design-grounded behaviors, or test implementation artifacts? Are edge cases covered?
5. **Run quality checks via PTC** — Parse test results, check coverage metrics, static analysis
6. **Audit 6 dimensions:**
   - **Plan adherence** — Implementation matches plan's task scope and acceptance criteria
   - **Design coherence** — Implementation satisfies design intent, not just plan letter
   - **Test coverage** — Tests cover required behaviors from design
   - **Code quality** — Error handling, edge cases, naming, abstractions
   - **Style conformance** — Project conventions, patterns, formatting
   - **Integration** (phase-level only) — Cross-task wiring: exports consumed, APIs connected, data contracts matched
7. **Classify findings by severity** per INV-6: minor, moderate, major, critical
8. **Determine verdict:**
   - **APPROVED** — No major/critical findings
   - **CRITIQUE** — Major findings requiring changes (specific file:line references)
   - **ESCALATED** — Critical findings requiring design changes or user decision
9. **Return structured JSON**

**Audit methodology enforcement order:**
1. Design document → 2. Plan → 3. Implementation → 4. Tests → 5. Quality → 6. Style. This order is non-negotiable — the auditor must build the "what should be true" mental model from design before seeing "what is true" in code.

### 4.2 Input Contract

**Required fields in delegation prompt:**

| Field | Type | Validation |
|-------|------|------------|
| `type` | `"guided"` | Exact match |
| `task` | string | Must reference audit (task-level or phase-level) |
| `known_context.file_coordinates` | array | Design doc, plan, source files, test files |
| `unknowns` | array | Specific audit questions |
| `output_contract.path` | string | Absolute path |
| `scope_boundary.do_not` | array | Must include "Do not modify any files" |

**Conditional fields:**

| Field | When | Purpose |
|-------|------|---------|
| `audit_scope` | Always | `"task"` or `"phase"` — determines if integration checking applies |
| `previous_audit` | Re-audit after fixes | Previous findings to verify resolution |

### 4.3 Output Contract

```json
{
  "delegation_type": "audit",
  "status": "completed|partial|failed",
  "verdict": "APPROVED|CRITIQUE|ESCALATED",
  "findings": [
    {
      "severity": "minor|moderate|major|critical",
      "category": "plan_adherence|test_coverage|code_quality|style|design_coherence|integration",
      "file": "/absolute/path/to/file.py",
      "line": 142,
      "description": "Missing validation for None input on process_frame()",
      "expected_behavior": "Design S3.2 requires ValueError on None input",
      "actual_behavior": "No validation, passes None downstream causing AttributeError",
      "recommendation": "Add explicit None check at function entry, raise ValueError"
    }
  ],
  "design_alignment_score": "high|medium|low",
  "test_coverage_assessment": "Tests cover 5/7 design behaviors. Missing: X, Y.",
  "style_conformance_notes": ["..."],
  "unknowns_resolved": [
    {"unknown": "...", "answer": "...", "confidence": "high|medium|low"}
  ],
  "decisions_made": [
    {"decision": "Classified X as major not critical", "reason": "Downstream catch prevents crash"}
  ]
}
```

### 4.4 Validation Rules (SubagentStop Hook)

| # | Check | Rule | Severity |
|---|-------|------|----------|
| 1 | Structure | JSON with `delegation_type: "audit"` and valid `status` | Block + retry |
| 2 | Verdict present | `verdict` is `"APPROVED"`, `"CRITIQUE"`, or `"ESCALATED"` | Block + retry |
| 3 | Findings array | `findings` is an array | Block + retry |
| 4 | Finding structure | Each finding has `severity`, `category`, `file`, `description`, `recommendation` | Block + retry |
| 5 | Verdict-findings: CRITIQUE | If CRITIQUE, ≥1 finding with `severity: "major"` | Block + retry |
| 6 | Verdict-findings: ESCALATED | If ESCALATED, ≥1 finding with `severity: "critical"` | Block + retry |
| 7 | Verdict-findings: APPROVED | If APPROVED, no findings with `severity: "major"` or `"critical"` | Block + retry |
| 8 | File references | Every `file` field is an absolute path | Warn only |
| 9 | No files written | No Write/Edit tool calls executed | Block (hard) |

### 4.5 Tool Access

| Tool | Access | Notes |
|------|--------|-------|
| Read | **Yes** | Source, tests, design docs, plans — reads BOTH sides (INV-1: no write = compliant) |
| Grep | **Yes** | Search across source and test files |
| Glob | **Yes** | Find relevant files |
| Bash | **Yes** | Run tests, check quality gate results |
| PTC | **Yes** | Parse test output, coverage analysis |
| Write | **No** | Audit-checker NEVER modifies files |
| Edit | **No** | Audit-checker NEVER modifies files |
| WebSearch | **No** | Audits against design, not web |
| Task | **No** | Sub-agents cannot spawn sub-agents |

### 4.6 File Access Globs

| Direction | Glob | Purpose |
|-----------|------|---------|
| Read | `src/**`, `lib/**`, `**/*.py` (source) | Implementation code |
| Read | `tests/**` | Test code |
| Read | `.claude/designs/**` | Design documents |
| Read | `.claude/plans/**` | Implementation plans |
| Read | `.claude/context/**` | Codebase context |
| Write allow | `.claude/temp/sub-agent-state-*.json` | Ephemeral progress only |
| Write deny | Everything else | Strictly read-only |

### 4.7 Failure Modes

| Failure | Detection | Handling | Parent Recourse |
|---------|-----------|----------|-----------------|
| Design doc not found | Read error | Return `failed` — hard requirement | Auditor re-dispatches with correct path |
| Source files missing | Read error on expected paths | Return `failed` | Auditor re-dispatches with correct worktree path |
| Test files missing | No test files found for task | Report as major finding under `test_coverage` | Coder dispatches test-writer |
| Ambiguous design intent | Design vague about specific behavior | Report as moderate `design_coherence` finding. Set `design_alignment_score: "medium"` | Auditor escalates for design clarification |
| Context pressure | Large audit scope | Use PTC for batch analysis. If pressured, return `partial` with dimensions checked so far | Auditor accepts partial or re-dispatches for remaining |
| Previous findings unresolved | Re-audit finds same issues | Report each unresolved finding with updated status. Verdict stays CRITIQUE | Auditor escalates if multiple re-audit rounds fail |

### 4.8 State Machine Integration

**Dispatching teammate:** Auditor

**Task-level audit:**

| Auditor State | Audit-Checker Phase |
|--------------|---------------------|
| `DELIBERATION` | Auditor receives request, determines scope and files |
| `SUB_AGENT_DISPATCH` | Orchestrator spawns audit-checker |
| `SYNTHESIS` | Checker returns — Auditor reads report |
| `DELIVERY` (APPROVED) | Auditor messages Coder: task approved |
| `DELIVERY` (CRITIQUE) | Auditor messages Coder with fix instructions (file:line) |
| `DELIVERY` (ESCALATED) | Auditor escalates to user in tmux pane |

**Phase-level audit:**

| Auditor State | Audit-Checker Phase |
|--------------|---------------------|
| `DELIBERATION` | Auditor dispatches N parallel checkers (one per task) |
| `SUB_AGENT_DISPATCH` | Orchestrator spawns N checkers in parallel |
| `SYNTHESIS` | All return — Auditor consolidates, identifies cross-task issues |

**Arbitration mode:**

| Auditor State | Audit-Checker Phase |
|--------------|---------------------|
| `DELIBERATION` | Auditor dispatches checker for root cause of Coder/Tester disagreement |
| `SYNTHESIS` | Checker returns — Auditor determines which side needs change |
| `DELIVERY` | Auditor messages both Coder and Tester with resolution |

**Guard conditions:**
- `audit_verdict_approved`: Checker returned APPROVED → task proceeds to merge
- `audit_verdict_critique`: Checker returned CRITIQUE → route findings to Coder
- `audit_verdict_escalated`: Checker returned ESCALATED → surface to user
- `all_checkers_returned` (phase-level): All parallel checkers returned → enter synthesis

---

## 5. test-writer

**Identity:** Test suite designer and implementer. Writes unit/integration tests from behavioral specs. Completely blind to implementation source (INV-1).

**Delegation type received:** `tdd_chunk`
**Return type:** Custom `test_writing` (extends `TddChunkReturn`)
**Dispatched by:** Coder, Tester
**Model:** Sonnet (fixed)

### Dispatch Context — Multi-Parent Sub-Agent

The test-writer serves two parents for fundamentally different purposes:

**When dispatched by Coder (TDD red phase):** Writing Pass A/B tests that MUST FAIL against unimplemented behavior. The test-writer receives plan chunks and behavioral specs. Its job is to encode design intent as failing tests. Red verification is mandatory — all tests must fail with appropriate failure types (AssertionError, ImportError, NOT SyntaxError). The test-writer never sees source code.

**When dispatched by Tester (infrastructure repair):** Fixing broken test INFRASTRUCTURE — conftest.py issues, fixture problems, import errors, collection failures. The test-writer is NOT writing new scenarios. It receives error output and existing test files. Its job is to fix the plumbing so existing scenarios can run. The delegation prompt explicitly scopes the work: "Fix infrastructure, not test logic."

The behavioral spec below covers the Coder dispatch path (primary). When dispatched by Tester, steps 6-7 (red verification) do not apply — the test-writer verifies that collect-only passes and existing tests are importable, not that tests fail.

### 5.1 Behavioral Spec (Cold Start → Return)

1. **Parse delegation prompt** — Extract plan chunk, test specifications (`pass_a`, `pass_b`), behavioral specs, worktree path
2. **Read plan chunk** — Understand what behavior the tests should verify. Focus on acceptance criteria and behavioral requirements
3. **Read existing tests** (if any) — In the task worktree, check for existing test infrastructure, conftest.py, test patterns
4. **Design tests** — For each test in `pass_a` (unit tests) and `pass_b` (integration tests):
   - Map to a specific behavioral requirement from the plan/design
   - Define test name, inputs, expected outputs, edge cases
   - Follow project test patterns (fixtures, parametrize, assertion style)
5. **Write test files** — Create/modify test files in the task worktree at paths specified by `test_globs`
6. **Run tests to verify RED** — All new tests MUST fail (they test behavior that doesn't exist yet). Run via `pytest` or PTC
7. **Verify failure types** — Failures should be `ImportError`, `AttributeError`, `AssertionError` (missing behavior), NOT `SyntaxError` or `CollectionError` (broken tests)
8. **Run quality gate** — `ruff format`, `ruff check`, `pyright` on test files only
9. **Return structured JSON** with test list, red verification, quality gate result

**INV-1 enforcement:** The test-writer receives behavioral specifications and plan chunks but NEVER reads implementation source. Tests verify design intent, not implementation details. PreToolUse hook blocks Read/Grep on source file globs.

### 5.2 Input Contract

**Required fields in delegation prompt:**

| Field | Type | Validation |
|-------|------|------------|
| `type` | `"tdd_chunk"` | Exact match |
| `task` | string | Must reference test writing |
| `known_context.file_coordinates` | array | Plan chunk, design doc sections. NO source file coordinates |
| `known_context.findings` | array | Previous chunk decisions, behavioral specs |
| `test_specifications.pass_a` | array | Non-empty — unit test specs |
| `output_contract.path` | string | Test file path in worktree |
| `output_contract.format` | string | "Python test file" |
| `success_criteria` | array | Non-empty, must include "All tests fail (red phase)" |

**Optional fields:**

| Field | Purpose |
|-------|---------|
| `test_specifications.pass_b` | Integration test specs |
| `previous_chunk_decisions` | Context from prior chunks |
| `scope_boundary.do_not` | Must include "Do not read implementation source" |

### 5.3 Output Contract

```json
{
  "delegation_type": "test_writing",
  "status": "completed|partial|failed",
  "tests_written": [
    {"file": "tests/test_pipeline.py", "test_name": "test_validate_rejects_none", "description": "Raises ValueError on None input", "pass_tier": "A"}
  ],
  "red_verification": {
    "all_tests_fail": true,
    "failure_types": ["AssertionError", "ImportError"],
    "problematic_failures": []
  },
  "quality_gate_passed": true,
  "decisions_made": [
    {"decision": "Used parametrize for input validation variants", "reason": "3 similar tests with different invalid inputs"}
  ],
  "carry_forward": ["conftest.py fixture for pipeline_instance may need adjustment after implementation"]
}
```

### 5.4 Validation Rules (SubagentStop Hook)

| # | Check | Rule | Severity |
|---|-------|------|----------|
| 1 | Structure | JSON with `delegation_type: "test_writing"` and valid `status` | Block + retry |
| 2 | Tests written | `tests_written` array non-empty (unless `status: "failed"`) | Block + retry |
| 3 | Red verification | `red_verification.all_tests_fail` is `true` | Block + retry |
| 4 | No problematic failures | `red_verification.problematic_failures` is empty | Warn (don't block — Coder reviews) |
| 5 | Quality gate | `quality_gate_passed` is `true` | Warn (Coder decides if blocking) |
| 6 | Test files exist | All files in `tests_written[].file` exist on disk | Block + retry |
| 7 | No source files modified | No Write/Edit to source file globs (e.g., `src/**`) | Block (hard) |
| 8 | INV-1 compliance | No Read/Grep on source file globs during execution | Warn (logged for audit) |

### 5.5 Tool Access

| Tool | Access | Notes |
|------|--------|-------|
| Read | **Restricted** | Test files, plan, design docs, conftest.py. **NEVER source code** |
| Write | **Yes** | Test files only (per `test_globs`) |
| Edit | **Yes** | Test files only |
| Bash | **Yes** | Run pytest, quality gate |
| Glob | **Yes** | Find test files, fixtures |
| Grep | **Restricted** | Search test files, plans, design docs. **NEVER source code** |
| PTC | **Yes** | Run tests, parse output |
| WebSearch | **No** | Tests are project-specific |
| Task | **No** | Sub-agents cannot spawn sub-agents |

### 5.6 File Access Globs

| Direction | Glob | Purpose |
|-----------|------|---------|
| Read allow | `tests/**` | Existing test infrastructure |
| Read allow | `.claude/plans/**` | Plan chunks |
| Read allow | `.claude/designs/**` | Design documents |
| Read allow | `.claude/context/**` | Context packets for behavioral specs |
| Read allow | `conftest.py`, `pytest.ini`, `pyproject.toml` | Test configuration |
| Read deny | `src/**`, `lib/**` (source code) | **INV-1: blind to implementation** |
| Write allow | `tests/**` (per task's `test_globs`) | Test files |
| Write allow | `.claude/temp/sub-agent-state-*.json` | Ephemeral progress |
| Write deny | `src/**`, `lib/**` | Never writes source code |

### 5.7 Failure Modes

| Failure | Detection | Handling | Parent Recourse |
|---------|-----------|----------|-----------------|
| Tests don't fail (green on write) | `all_tests_fail: false` | Re-examine: are tests testing existing behavior? Rewrite to test NEW behavior from plan chunk | Coder reviews — may indicate plan chunk is already implemented |
| Problematic failure types | `SyntaxError`, `CollectionError` | Fix the test code — these are test bugs, not missing implementation | Coder dispatches test-writer again with error info |
| Quality gate fails | Format/lint/typecheck errors | Auto-fix what's fixable (`ruff format`, `ruff check --fix`). Report unfixable | Coder reviews unfixable issues |
| Insufficient spec | Plan chunk doesn't have enough detail for test design | Return `partial` with tests written so far and `carry_forward` noting gaps | Coder provides more context or dispatches again |
| INV-1 violation attempt | PreToolUse hook blocks source read | Must work from specs only — that's the design constraint | Should never happen if prompt is correct |
| Context pressure | 90% window | Write partial tests to disk, return `partial` | Fresh test-writer with delegation + previous return |

### 5.8 State Machine Integration

**Dispatching teammate:** Coder (TDD red phase) or Tester (test fixes)

**When dispatched by Coder (red phase):**

| Coder State | Test-Writer Phase |
|------------|-------------------|
| `DISPATCH_READY` | Coder writes delegation JSON for test-writer (think: delegation prompt) |
| `COORDINATING` | Test-writer executing (steps 1-9) |
| `RESULT_REVIEW` | Test-writer returns — Coder reviews red verification |
| → if red verified | Coder dispatches implementer with test RESULTS (not test code) |
| → if red failed | Coder adjusts prompt, re-dispatches test-writer |

**When dispatched by Tester (test fixes):**

| Tester State | Test-Writer Phase |
|-------------|-------------------|
| `SUB_AGENT_DISPATCH` | Tester dispatches test-writer to fix broken test infrastructure |
| `SYNTHESIS` | Test-writer returns — Tester validates fixes |

**Guard conditions:**
- `red_phase_verified`: `red_verification.all_tests_fail == true` AND `problematic_failures` empty → proceed to green phase
- `test_files_exist`: All `tests_written[].file` exist → valid output
- `quality_gate_clean`: All gate results are "pass" → ready for implementation phase

---

## 6. implementer

**Identity:** Production code writer. Implements plan chunks, fixes audit findings. Blind to test code (INV-1).

**Delegation type received:** `tdd_chunk` (fresh implementation) or `targeted` (audit fixes)
**Return type:** Custom `implementation` (extends `TddChunkReturn`)
**Dispatched by:** Coder
**Model:** Sonnet (fixed)

### 6.1 Behavioral Spec (Cold Start → Return)

1. **Parse delegation prompt** — Extract plan chunk, source file targets, test RESULTS (not test code), success criteria, worktree path
2. **Read plan chunk** — Understand what to implement: acceptance criteria, behavioral requirements, file targets
3. **Read existing source** — In the task worktree, understand current code structure, patterns, imports
4. **Implement** — Write/modify source files to satisfy plan chunk requirements:
   - Follow project conventions (from context packets)
   - Adhere to plan's approach and constraints
   - Reference test results for expected behavior (what tests expect, not how they test it)
5. **Run tests** — Execute pytest. Check which tests pass/fail
6. **Iterate if needed** — If tests fail, read error output (NOT test code), adjust implementation. Max 5 iterations
7. **Run full quality gate** — `ruff format`, `ruff check --fix`, `pyright`, `pytest`
8. **Return structured JSON** with files modified, quality gate result, decisions made

**INV-1 enforcement:** The implementer receives test RESULTS (pytest output showing which tests fail and error messages) but NEVER reads test source files. It implements based on plan chunk and error descriptions. PreToolUse hook blocks Read/Grep on test file globs.

### 6.2 Input Contract

**For fresh implementation (`tdd_chunk`):**

| Field | Type | Validation |
|-------|------|------------|
| `type` | `"tdd_chunk"` | Exact match |
| `task` | string | Must reference implementation |
| `known_context.file_coordinates` | array | Source files to modify. NO test file coordinates |
| `known_context.findings` | array | Plan chunk details, previous chunk decisions, test RESULTS |
| `test_specifications.pass_a` | array | Test names and descriptions (not test code) |
| `output_contract.path` | string | Source file path(s) in worktree |
| `success_criteria` | array | Must include "All tests pass", "Quality gate passes" |
| `previous_chunk_decisions` | array | Decisions from prior chunks affecting this one |

**For targeted fix (`targeted`):**

| Field | Type | Validation |
|-------|------|------------|
| `type` | `"targeted"` | Exact match |
| `task` | string | Specific fix description |
| `known_context.file_coordinates` | array | file:line references from audit findings |
| `known_context.findings` | array | Audit findings, expected vs actual behavior |
| `output_contract.path` | string | Source file path |
| `scope_boundary.do_not` | array | "Only fix specified issues, no refactoring" |

### 6.3 Output Contract

```json
{
  "delegation_type": "implementation",
  "status": "completed|partial|failed",
  "files_modified": ["/workspace/src/pipeline.py"],
  "quality_gate_passed": true,
  "test_results": {
    "total": 12,
    "passed": 12,
    "failed": 0,
    "errors": 0
  },
  "decisions_made": [
    {"decision": "Used dataclass instead of dict for ProcessResult", "reason": "Type safety, plan didn't specify"}
  ],
  "carry_forward": ["ProcessResult type exported from types.py for downstream chunks"],
  "iterations": 3
}
```

### 6.4 Validation Rules (SubagentStop Hook)

| # | Check | Rule | Severity |
|---|-------|------|----------|
| 1 | Structure | JSON with `delegation_type: "implementation"` and valid `status` | Block + retry |
| 2 | Files modified | `files_modified` array non-empty (unless `status: "failed"`) | Block + retry |
| 3 | Quality gate | `quality_gate_passed` is `true` | Block + retry |
| 6 | Modified files exist | All files in `files_modified` exist on disk | Block + retry |
| 7 | No test files modified | No Write/Edit to test file globs (e.g., `tests/**`) | Block (hard) |
| 8 | INV-1 compliance | No Read/Grep on test file globs during execution | Warn (logged for audit) |

### 6.5 Tool Access

| Tool | Access | Notes |
|------|--------|-------|
| Read | **Restricted** | Source code, plan, design docs, error output. **NEVER test code** |
| Write | **Yes** | Source files only (per task's `source_globs`) |
| Edit | **Yes** | Source files only |
| Bash | **Yes** | Run tests (`pytest`), quality gate (`./scripts/gate.sh`) |
| Glob | **Yes** | Find source files |
| Grep | **Restricted** | Search source, plan, docs. **NEVER test code** |
| PTC | **Yes** | Run tests, parse output, validation |
| WebSearch | **No** | Implementation is project-specific |
| Task | **No** | Sub-agents cannot spawn sub-agents |

### 6.6 File Access Globs

| Direction | Glob | Purpose |
|-----------|------|---------|
| Read allow | `src/**`, `lib/**` (per task's `source_globs`) | Source code |
| Read allow | `.claude/plans/**` | Plan chunks |
| Read allow | `.claude/designs/**` | Design docs |
| Read allow | `.claude/context/**` | Codebase context |
| Read allow | `pyproject.toml`, `setup.cfg` | Project configuration |
| Read deny | `tests/**` | **INV-1: blind to test code** |
| Write allow | `src/**`, `lib/**` (per task's `source_globs`) | Source files |
| Write allow | `.claude/temp/sub-agent-state-*.json` | Ephemeral progress |
| Write deny | `tests/**` | Never writes test code |

### 6.7 Failure Modes

| Failure | Detection | Handling | Parent Recourse |
|---------|-----------|----------|-----------------|
| Tests won't pass after 5 iterations | Iteration count = 5, tests still failing | Return `partial` with files_modified and current test results. Set `carry_forward` with what was tried | Coder dispatches debugger |
| Quality gate fails (non-test) | Format/lint/typecheck errors after fixes | Auto-fix what's fixable. Report unfixable in return | Coder reviews — may re-dispatch with guidance |
| Plan ambiguity | Plan chunk unclear about implementation approach | Make reasonable decision, document in `decisions_made` | Coder reviews decisions, may adjust |
| INV-1 violation attempt | PreToolUse hook blocks test read | Work from test results only | Should never happen if prompt is correct |
| Missing dependency | Implementation needs code from another chunk | Report in `carry_forward`. Return `partial` if blocking | Coder checks dependency ordering |
| Context pressure | 90% window | Write partial implementation to disk, return `partial` | Fresh implementer with delegation + previous return |

### 6.8 State Machine Integration

**Dispatching teammate:** Coder

| Coder State | Implementer Phase |
|------------|-------------------|
| `RESULT_REVIEW` (red verified) | Coder dispatches implementer with plan chunk + test RESULTS |
| `COORDINATING` | Implementer executing (steps 1-8) |
| `RESULT_REVIEW` | Implementer returns — Coder reviews quality gate |
| → if all green | Coder messages Auditor for review |
| → if stuck (5 iterations) | Coder dispatches debugger |
| → if partial | Coder provides more context, re-dispatches |

**For audit fixes:**

| Coder State | Implementer Phase |
|------------|-------------------|
| `AUDIT_RESPONSE` | Coder resumes implementer with audit findings as targeted fix |
| `RESULT_REVIEW` | Implementer returns — Coder re-requests audit |

**Guard conditions:**
- `implementation_complete`: `quality_gate_passed` is true → ready for audit
- `implementation_stuck`: `iterations >= 5` AND tests still failing → dispatch debugger
- `files_exist`: All `files_modified` exist on disk → valid output

---

## 7. scenario-writer

**Identity:** Scenario/eval test designer. Writes Pass D evaluation scenarios testing user-facing behaviors across tasks. Most isolated sub-agent — blind to implementation AND unit tests.

**Delegation type received:** `tdd_chunk`
**Return type:** Custom `scenario_writing`
**Dispatched by:** Tester
**Model:** Sonnet (fixed)

### 7.1 Behavioral Spec (Cold Start → Return)

1. **Parse delegation prompt** — Extract design doc path, plan (phase-level), scenario specifications, public API specs
2. **Read design document** — Understand user-facing behaviors, acceptance criteria, edge cases
3. **Read plan (phase-level)** — Understand what capabilities the phase delivers
4. **Read public API specs** (if provided) — Function signatures, input/output types, error contracts
5. **Design scenarios** — For each required scenario:
   - Map to a specific user-facing behavior or acceptance criterion
   - Classify tier: critical (core functionality), important (expected behavior), edge_case
   - Design inputs, expected outputs, assertions
6. **Write scenario test files** — Create test files in `tests/scenarios/` or `tests/eval/`
7. **Run collect-only** — `pytest --collect-only` to verify scenarios are discoverable without running them
8. **Run quality gate** on scenario files only
9. **Return structured JSON** with scenarios, collect result, decisions

**INV-1 enforcement:** The scenario-writer reads ONLY the design document and plan. It never reads implementation source or unit tests. Scenarios test design intent, not implementation artifacts. PreToolUse hook blocks Read/Grep on both source and test globs.

### 7.2 Input Contract

| Field | Type | Validation |
|-------|------|------------|
| `type` | `"tdd_chunk"` | Exact match |
| `task` | string | Must reference scenario writing |
| `known_context.file_coordinates` | array | Design doc, plan. NO source or test file coordinates |
| `known_context.findings` | array | Phase capabilities, public API signatures (from design/plan, not source) |
| `test_specifications.pass_a` | array | Scenario specs with tier, behavior description |
| `output_contract.path` | string | Scenario test directory |
| `success_criteria` | array | Must include "All scenarios discoverable (collect-only passes)" |

### 7.3 Output Contract

```json
{
  "delegation_type": "scenario_writing",
  "status": "completed|partial|failed",
  "scenarios_written": [
    {
      "file": "tests/scenarios/test_pipeline_e2e.py",
      "scenario_name": "test_pipeline_processes_valid_frame",
      "tier": "critical",
      "description": "Pipeline processes a valid numpy array frame and returns ProcessResult",
      "design_requirement": "Design S3.1: Pipeline must process valid frames"
    }
  ],
  "collect_only_result": {
    "exit_code": 0,
    "scenarios_discovered": 12,
    "collection_errors": []
  },
  "quality_gate_passed": true,
  "decisions_made": [
    {"decision": "Used conftest fixture for frame generation", "reason": "Multiple scenarios need valid frame input"}
  ]
}
```

### 7.4 Validation Rules (SubagentStop Hook)

| # | Check | Rule | Severity |
|---|-------|------|----------|
| 1 | Structure | JSON with `delegation_type: "scenario_writing"` and valid `status` | Block + retry |
| 2 | Scenarios written | `scenarios_written` array non-empty (unless `status: "failed"`) | Block + retry |
| 3 | Collect-only passes | `collect_only_result.exit_code == 0` | Block + retry |
| 4 | No collection errors | `collect_only_result.collection_errors` is empty | Block + retry |
| 5 | Scenario files exist | All `scenarios_written[].file` exist on disk | Block + retry |
| 6 | No source files modified | No Write/Edit to source file globs | Block (hard) |
| 7 | No unit test files modified | No Write/Edit to `tests/unit/**` or `tests/test_*.py` (non-scenario) | Block (hard) |
| 8 | INV-1 compliance | No Read/Grep on source or unit test globs | Warn (logged) |

### 7.5 Tool Access

| Tool | Access | Notes |
|------|--------|-------|
| Read | **Restricted** | Design docs, plan, public API specs. **NEVER source code or unit tests** |
| Write | **Yes** | Scenario test files only (`tests/scenarios/**`, `tests/eval/**`) |
| Edit | **Yes** | Scenario test files only |
| Bash | **Yes** | `pytest --collect-only`, quality gate |
| Glob | **Restricted** | Find scenario files. **NOT source files** |
| Grep | **Restricted** | Search design docs, plan. **NOT source or unit tests** |
| PTC | **Yes** | Run collect-only, parse output |
| WebSearch | **No** | Scenarios are project-specific |
| Task | **No** | Sub-agents cannot spawn sub-agents |

### 7.6 File Access Globs

| Direction | Glob | Purpose |
|-----------|------|---------|
| Read allow | `.claude/designs/**` | Design documents |
| Read allow | `.claude/plans/**` | Implementation plans |
| Read allow | `.claude/context/**` | Context packets (public API specs only) |
| Read allow | `tests/scenarios/**`, `tests/eval/**` | Existing scenario tests |
| Read allow | `conftest.py` | Shared test configuration |
| Read deny | `src/**`, `lib/**` | **INV-1: blind to implementation** |
| Read deny | `tests/unit/**`, `tests/test_*.py` | **INV-1: blind to unit tests** |
| Write allow | `tests/scenarios/**`, `tests/eval/**` | Scenario output |
| Write allow | `.claude/temp/sub-agent-state-*.json` | Ephemeral progress |
| Write deny | Everything else | Most restricted writer |

### 7.7 Failure Modes

| Failure | Detection | Handling | Parent Recourse |
|---------|-----------|----------|-----------------|
| Collect-only fails | Exit code != 0 | Fix collection errors (import issues, syntax). Re-run | Tester reviews — may indicate missing dependency |
| Insufficient design spec | Design doc doesn't describe enough behaviors | Return `partial` with scenarios for documented behaviors. Gap in `carry_forward` | Tester requests more design detail |
| Public API unclear | No API signatures available | Write scenarios against design-level behavioral contracts only | Tester provides API specs from Explorer context |
| INV-1 violation attempt | Hook blocks source/test reads | Must work from design docs only | Should never happen if prompt is correct |
| Context pressure | 90% window | Write partial scenarios, return `partial` | Fresh writer with delegation + previous return |

### 7.8 State Machine Integration

**Dispatching teammate:** Tester

| Tester State | Scenario-Writer Phase |
|-------------|----------------------|
| `DELIBERATION` | Tester designs scenario strategy with user, writes delegation JSON |
| `SUB_AGENT_DISPATCH` | Orchestrator spawns scenario-writer |
| `SYNTHESIS` | Writer returns — Tester validates collect-only, reviews scenarios |
| → if collect passes | Tester runs scenarios against merged code |
| → if collect fails | Tester dispatches test-writer to fix, or adjusts prompt |

**Guard conditions:**
- `scenarios_discoverable`: `collect_only_result.exit_code == 0` → scenarios valid
- `scenario_files_exist`: All files in `scenarios_written` exist → valid output

---

## 8. debugger

**Identity:** Hypothesis-driven bug investigator. Handles complex failures that simple fixes can't resolve. Can edit source code.

**Delegation type received:** `guided`
**Return type:** Custom `debugging`
**Dispatched by:** Coder (workflow — implementation stuck), Auditor (ad-hoc user requests only)
**Model:** Sonnet (fixed)

### 8.1 Behavioral Spec (Cold Start → Return)

1. **Parse delegation prompt** — Extract error symptoms, affected source files, what has been tried, worktree path
2. **Read error symptoms** — Test output, stack traces, error messages. Understand the failure pattern
3. **Read affected source files** — Trace code paths involved in the failure
4. **Form 2-3 hypotheses** about root cause, ranked by likelihood:
   - Each hypothesis has: description, predicted evidence, test method
5. **Test each hypothesis systematically:**
   - Use PTC for code analysis (trace data flow, check variable states)
   - Use targeted Bash commands (run specific tests with verbose output)
   - Use Read/Grep for evidence gathering
6. **Identify root cause** with evidence — which hypothesis was confirmed, what evidence supports it
7. **Apply surgical fix** — Minimal changes to fix the root cause. No refactoring, no "while I'm here" improvements
8. **Run tests** — Verify fix resolves the target failures
9. **Progress check:**
   - All target tests pass → run full quality gate → step 11
   - Some tests now pass that didn't before (progress) → reset stall counter, form new hypotheses for remaining failures → back to step 4
   - Same failures or new failures with no net improvement (stall) → increment stall counter → if counter < 3, form new hypotheses → back to step 4; if counter ≥ 3 → step 10
10. **Stall exit** — Return `partial` with root cause analysis, hypothesis log, fixes attempted, remaining failures. Status: `stuck`
11. **Document related risks** — Areas that may have similar issues
12. **Return structured JSON** with root cause, fix, quality gate, risks

**Progress-based stall tracking:** Same pattern as implementer/planner. The debugger gets a shorter leash (stall threshold: 3) because it's already the second line of defense — the implementer failed first. If the debugger is stalling, the problem likely requires human judgment, not more automated attempts.

**INV-1 enforcement:** The debugger reads source code and test RESULTS (pytest output, stack traces) but NEVER test source code. It investigates from the implementation side. PreToolUse hook blocks Read/Grep on test file globs. Note: pytest verbose output may include test function names, assertion lines, and assertion code (`assert result == expected`). This is acceptable — the debugger uses these to understand WHAT behavior is expected, not to reverse-engineer test structure. See Common Conventions § "INV-1 and Execution Results."

### 8.2 Input Contract

| Field | Type | Validation |
|-------|------|------------|
| `type` | `"guided"` | Exact match |
| `task` | string | Must describe the bug/failure |
| `known_context.file_coordinates` | array | Affected source files. NO test file coordinates |
| `known_context.findings` | array | Error output, stack traces, what has been tried |
| `unknowns` | array | Specific questions: "Is this a timing issue?", "Does this happen with all inputs?" |
| `output_contract.path` | string | Source file(s) to fix |
| `scope_boundary.do_not` | array | "Fix only the identified bug", "No refactoring" |
| `rejected_approaches` | array | What has been tried and failed |

### 8.3 Output Contract

```json
{
  "delegation_type": "debugging",
  "status": "completed|partial|stuck|failed",
  "root_cause": {
    "description": "Race condition between frame buffer update and model inference",
    "category": "logic|timing|concurrency|memory|architecture|data_flow|configuration",
    "affected_files": [
      {"path": "/workspace/src/pipeline.py", "lines": "142-158", "role_in_bug": "Buffer accessed without lock"}
    ],
    "reproduction_steps": ["1. Start pipeline with 2 concurrent streams", "2. Send frame during model inference"],
    "hypothesis_log": [
      {"hypothesis": "Buffer overflow", "evidence": "No — buffer size is bounded", "result": "rejected"},
      {"hypothesis": "Race condition", "evidence": "Thread A reads buffer while B writes", "result": "confirmed"}
    ]
  },
  "fix_applied": {
    "files_modified": ["/workspace/src/pipeline.py"],
    "description": "Added threading.Lock around buffer access in process_frame()",
    "confidence": "high|medium|low"
  },
  "iterations": {
    "total": 2,
    "stall_count": 0,
    "progress_log": [
      {"iteration": 1, "fix": "Added Lock to process_frame", "tests_before": "0/5 pass", "tests_after": "3/5 pass", "progress": true},
      {"iteration": 2, "fix": "Extended lock scope to include buffer.flush()", "tests_before": "3/5 pass", "tests_after": "5/5 pass", "progress": true}
    ]
  },
  "related_risks": ["Similar unprotected buffer access in src/camera.py:89"],
  "quality_gate_passed": true,
  "unknowns_resolved": [
    {"unknown": "Is this a timing issue?", "answer": "Yes — race condition under concurrent access", "confidence": "high"}
  ],
  "decisions_made": [
    {"decision": "Used threading.Lock not asyncio.Lock", "reason": "Pipeline uses threading, not asyncio"}
  ],
  "carry_forward": ["camera.py has same unprotected pattern — flag for separate debugger dispatch"]
}
```

**Status values:** `completed` = all target tests pass, gate clean. `partial` = root cause identified but fix incomplete (context pressure, cascading issues). `stuck` = stall threshold hit, no further automated progress possible. `failed` = couldn't identify root cause at all.

### 8.4 Validation Rules (SubagentStop Hook)

| # | Check | Rule | Severity |
|---|-------|------|----------|
| 1 | Structure | JSON with `delegation_type: "debugging"` and valid `status` (including `stuck`) | Block + retry |
| 2 | Root cause | `root_cause` object exists with `description` and `category` | Block + retry |
| 3 | Root cause category | `category` is one of the valid enum values | Block + retry |
| 4 | Fix or explanation | If `status: "completed"`, `fix_applied` must exist with `files_modified` non-empty | Block + retry |
| 5 | Quality gate | `quality_gate_passed` is `true` (if `status: "completed"`) | Block + retry |
| 6 | Iterations | `iterations` object exists with `total`, `stall_count`, `progress_log` | Block + retry |
| 7 | Hypothesis log | `root_cause.hypothesis_log` has ≥2 entries (systematic investigation, not guessing) | Warn only |
| 8 | Stuck consistency | If `status: "stuck"`, `iterations.stall_count` ≥ 3 | Warn only |
| 9 | No test files modified | No Write/Edit to test file globs | Block (hard) |
| 10 | INV-1 compliance | No Read/Grep on test file globs | Warn (logged) |

### 8.5 Tool Access

| Tool | Access | Notes |
|------|--------|-------|
| Read | **Restricted** | Source code, error output, plan, design docs. **NEVER test code** |
| Write | **Yes** | Source files (in task worktree) |
| Edit | **Yes** | Source files |
| Bash | **Yes** | Run tests, diagnostic commands, quality gate |
| Glob | **Yes** | Find source files |
| Grep | **Restricted** | Search source, error output. **NEVER test code** |
| PTC | **Yes** | Code analysis, tracing, test parsing |
| WebSearch | **No** | Debugging is project-specific |
| Task | **No** | Sub-agents cannot spawn sub-agents |

### 8.6 File Access Globs

| Direction | Glob | Purpose |
|-----------|------|---------|
| Read allow | `src/**`, `lib/**` | Source code under investigation |
| Read allow | `.claude/plans/**`, `.claude/designs/**` | Plan/design for expected behavior |
| Read allow | `.claude/context/**` | Codebase context |
| Read deny | `tests/**` | **INV-1: sees test RESULTS only, not test code** |
| Write allow | `src/**`, `lib/**` (per task's `source_globs`) | Bug fixes |
| Write allow | `.claude/temp/sub-agent-state-*.json` | Ephemeral progress |
| Write deny | `tests/**` | Never modifies tests |

### 8.7 Failure Modes

| Failure | Detection | Handling | Parent Recourse |
|---------|-----------|----------|-----------------|
| Root cause not found | All hypotheses rejected in first iteration | Form new hypotheses from different angles. If all rejected again → return `failed` | Coder escalates to user (STUCK_ESCALATION) |
| Fix doesn't resolve all failures | Tests run after fix, some still fail | Progress check: if more tests pass than before → progress, reset stall, iterate. If same failures → stall, increment counter | Automatic — debugger handles internally |
| Stall threshold hit | `stall_count` ≥ 3 | Return `stuck` with full progress_log, hypothesis_log, fixes attempted, remaining failures | Coder escalates to user (STUCK_ESCALATION) |
| Fix breaks other tests | Tests that previously passed now fail | Roll back fix, try alternative approach. Counts as stall if net test count doesn't improve | Coder reviews — may need broader fix |
| Multiple root causes | Investigation reveals cascading issues | Fix the primary root cause per iteration. Document related issues in `related_risks`. Progress loop handles the cascade naturally | Coder decides whether to dispatch another debugger for `related_risks` |
| INV-1 violation attempt | Hook blocks test code read | Must work from error output only | Should never happen if prompt is correct |
| Context pressure | 90% window | Write diagnosis to ephemeral state, return `partial` with root cause analysis and progress_log so far | Fresh debugger with delegation + previous return |

### 8.8 State Machine Integration

**Dispatching teammate:** Coder or Auditor

**When dispatched by Coder (implementation stuck):**

| Coder State | Debugger Phase |
|------------|----------------|
| `RESULT_REVIEW` (implementer stuck) | Coder dispatches debugger with: error output, affected files, what implementer tried |
| `COORDINATING` | Debugger executing (may iterate internally — progress loop with stall threshold 3) |
| `RESULT_REVIEW` | Debugger returns — Coder reviews status |
| → if `completed` | Fix applied, all tests pass — Coder messages Auditor |
| → if `stuck` | Stall threshold hit — Coder escalates to user (STUCK_ESCALATION) with debugger's progress_log |
| → if `partial` | Context pressure — Coder dispatches fresh debugger with previous return |

**When dispatched by Auditor (unclear audit finding):**

| Auditor State | Debugger Phase |
|--------------|----------------|
| `DELIBERATION` | Auditor dispatches debugger with: audit finding, affected files, design intent |
| `SYNTHESIS` | Debugger returns — Auditor routes fix instructions to Coder |

**Guard conditions:**
- `root_cause_identified`: `root_cause.description` non-empty → investigation successful
- `fix_verified`: `status == "completed"` AND `quality_gate_passed` is true → fix is safe, all iterations resolved
- `stuck`: `status == "stuck"` AND `iterations.stall_count` ≥ 3 → no further automated progress, escalate
- `partial_progress`: `status == "partial"` → context pressure or cascading issues, fresh debugger can continue

---

## 9. optimizer

**Identity:** Post-implementation code optimizer. Works in ephemeral worktree — optimizations can be compared and discarded safely.

**Delegation type received:** `guided`
**Return type:** Custom `optimization`
**Dispatched by:** Coder
**Model:** Sonnet (fixed)

### 9.1 Behavioral Spec (Cold Start → Return)

1. **Parse delegation prompt** — Extract source files to optimize, worktree branch reference, optimization focus areas
2. **Receive ephemeral worktree** — Hooks create a worktree branched from the task's primary worktree. Optimizer works here — primary implementation is never at risk
3. **Read source files** — Analyze current implementation for optimization opportunities
4. **Identify optimization targets** using focus areas:
   - Algorithmic complexity (O(n^2) → O(n log n))
   - Memory allocation (unnecessary copies, large temporaries)
   - Cache-friendly data access
   - I/O efficiency (batch operations, async)
   - Hot path identification (frame processing loops, detection, real-time constraints)
   - Unnecessary abstractions adding overhead
5. **Apply optimizations** — Make changes in ephemeral worktree
6. **Run quality gate** — Verify optimizations don't break existing behavior
7. **Generate diff summary** — Clear before/after comparison for each optimization
8. **Return structured JSON** with optimizations, quality gate, worktree branch, diff

### 9.2 Input Contract

| Field | Type | Validation |
|-------|------|------------|
| `type` | `"guided"` | Exact match |
| `task` | string | Must reference optimization |
| `known_context.file_coordinates` | array | Source files to optimize. NO test file coordinates |
| `known_context.findings` | array | Known performance characteristics, bottleneck hints |
| `unknowns` | array | "Are there algorithmic improvements possible?", "Is memory allocation a concern?" |
| `output_contract.format` | string | "Optimized source files in ephemeral worktree" |
| `scope_boundary.do_not` | array | "No behavioral changes", "No API signature changes" |

**Optional fields:**

| Field | Purpose |
|-------|---------|
| `optimization_focus` | Specific areas: `["algorithmic", "memory", "io", "concurrency"]` |
| `performance_constraints` | "Must maintain < 16ms per frame", "Memory budget: 512MB" |

### 9.3 Output Contract

```json
{
  "delegation_type": "optimization",
  "status": "completed|partial|failed",
  "optimizations_applied": [
    {
      "file": "/workspace/src/pipeline.py",
      "description": "Replaced linear search with bisect for sorted frame buffer lookup",
      "category": "algorithmic|memory|cache|io|concurrency|architecture",
      "impact_estimate": "high|medium|low",
      "before_after": {
        "before": "for frame in buffer: if frame.id == target: ...",
        "after": "idx = bisect.bisect_left(buffer, target, key=lambda f: f.id)"
      }
    }
  ],
  "quality_gate_passed": true,
  "worktree_branch": "optimize/phase-01-task-03",
  "diff_summary": "+42 -38 across 2 files",
  "no_optimization_found": false,
  "unknowns_resolved": [
    {"unknown": "Are there algorithmic improvements?", "answer": "Yes — frame lookup was O(n), now O(log n)", "confidence": "high"}
  ],
  "decisions_made": [
    {"decision": "Did not optimize config parsing", "reason": "Only called once at startup, not a hot path"}
  ]
}
```

### 9.4 Validation Rules (SubagentStop Hook)

| # | Check | Rule | Severity |
|---|-------|------|----------|
| 1 | Structure | JSON with `delegation_type: "optimization"` and valid `status` | Block + retry |
| 2 | Quality gate | `quality_gate_passed` is `true` (if optimizations applied) | Block + retry |
| 3 | Worktree branch | `worktree_branch` is non-empty string | Block + retry |
| 4 | Optimizations or no-op | Either `optimizations_applied` non-empty OR `no_optimization_found: true` | Block + retry |
| 5 | Before/after | Each optimization has `before_after` with both fields | Warn only |
| 6 | No test files modified | No Write/Edit to test file globs | Block (hard) |
| 7 | INV-1 compliance | No Read/Grep on test file globs | Warn (logged) |

### 9.5 Tool Access

| Tool | Access | Notes |
|------|--------|-------|
| Read | **Restricted** | Source code only. **NEVER test code** |
| Write | **Yes** | Optimized source (in ephemeral worktree) |
| Edit | **Yes** | Source files |
| Bash | **Yes** | Run tests, quality gate, benchmarks, git diff |
| Glob | **Yes** | Find source files |
| Grep | **Restricted** | Search source code. **NEVER test code** |
| PTC | **Yes** | Performance analysis, profiling, complexity estimation |
| WebSearch | **No** | Optimization is project-specific |
| Task | **No** | Sub-agents cannot spawn sub-agents |

### 9.6 File Access Globs

| Direction | Glob | Purpose |
|-----------|------|---------|
| Read allow | `src/**`, `lib/**` | Source code |
| Read allow | `.claude/plans/**`, `.claude/designs/**` | Plan/design for context |
| Read allow | `pyproject.toml`, `setup.cfg` | Configuration |
| Read deny | `tests/**` | **INV-1: optimizes algorithmically, not against test behavior** |
| Write allow | `src/**`, `lib/**` (in ephemeral worktree) | Optimized source |
| Write allow | `.claude/temp/sub-agent-state-*.json` | Ephemeral progress |
| Write deny | `tests/**` | Never modifies tests |

### 9.7 Failure Modes

| Failure | Detection | Handling | Parent Recourse |
|---------|-----------|----------|-----------------|
| No optimizations found | Analysis shows no improvements | Return `completed` with `no_optimization_found: true` and explanation | Coder skips optimization step — no harm |
| Optimization breaks tests | Quality gate tests fail | Roll back specific optimization. Try alternative or skip it | Coder discards ephemeral worktree |
| Optimization changes behavior | Tests pass but semantics changed | Quality gate should catch via test failures. If subtle, audit-checker will catch | Auditor reviews optimization branch |
| Marginal improvements only | Impact estimates all "low" | Report honestly. Coder decides whether to merge | Coder discards — ephemeral worktree is disposable |
| Context pressure | 90% window | Write partial optimizations, run quality gate on what's done, return `partial` | Coder evaluates partial optimizations |

### 9.8 State Machine Integration

**Dispatching teammate:** Coder

| Coder State | Optimizer Phase |
|------------|-----------------|
| `RESULT_REVIEW` (after audit approval) | Coder dispatches optimizer (optional step) |
| `COORDINATING` | Optimizer executing in ephemeral worktree |
| `RESULT_REVIEW` | Optimizer returns — Coder reviews optimizations |
| → if substantive + safe | Coder merges optimization worktree into task worktree |
| → if marginal/risky | Coder cherry-picks specific improvements or discards |
| → if no optimization | Coder proceeds to merge (no harm done) |

**Worktree lifecycle:**
1. Orchestrator/hooks create ephemeral worktree branched from task worktree
2. Optimizer works exclusively in ephemeral worktree
3. Optimizer returns with branch name
4. Coder compares branches: `git diff task-branch..optimize-branch`
5. Coder decides: merge all, cherry-pick, or discard
6. Ephemeral worktree cleaned up after decision

**Guard conditions:**
- `optimization_quality_gate_pass`: All gate results pass → safe to consider merging
- `has_optimizations`: `optimizations_applied` non-empty → worth reviewing
- `no_optimization_found`: `no_optimization_found: true` → skip optimization step

---

## Appendix A: Validation Rule Summary

Quick reference for SubagentStop hook implementation.

| Sub-Agent | delegation_type | Hard Blocks | Warn Only |
|-----------|----------------|-------------|-----------|
| codebase-scout | `exploration` | Structure, essential output, confidence, findings, write boundary | Output file exists |
| research-scout | `research` | Structure, essential output, citations, citation fields, findings, write boundary | Blocked sources |
| plan-checker | `plan_verification` | Structure, verdict, 8 dimensions, dimension structure, consistency, revision suggestions, no writes | — |
| audit-checker | `audit` | Structure, verdict, findings array, finding structure, verdict consistency (×3), no writes | File references absolute |
| test-writer | `test_writing` | Structure, tests written, red verification, test files exist, no source writes | Problematic failures, quality gate, INV-1 |
| implementer | `implementation` | Structure, files modified, quality gate, all gates pass, tests pass, files exist, no test writes | INV-1 |
| scenario-writer | `scenario_writing` | Structure, scenarios written, collect-only, no collection errors, files exist, no source writes, no unit test writes | INV-1 |
| debugger | `debugging` | Structure, root cause, category, fix+quality gate (if completed), no test writes | Hypothesis log, INV-1 |
| optimizer | `optimization` | Structure, quality gate, worktree branch, optimizations-or-noop, no test writes | Before/after, INV-1 |

## Appendix B: INV-1 Enforcement Matrix

| Sub-Agent | Reads Source | Reads Tests | Writes Source | Writes Tests | INV-1 Status |
|-----------|-------------|-------------|--------------|--------------|--------------|
| codebase-scout | Yes | Yes* | No | No | N/A (reads both, writes neither) |
| research-scout | Yes | Yes | No | No | N/A (read-only, broad read helps ground research) |
| plan-checker | Yes | Yes | No | No | N/A (read-only, broad read for feasibility + test verification) |
| audit-checker | **Yes** | **Yes** | **No** | **No** | Reads BOTH, writes NEITHER |
| test-writer | **No** | Yes | No | **Yes** | Reads tests, blind to source |
| implementer | **Yes** | **No** | **Yes** | No | Reads source, blind to tests |
| scenario-writer | **No** | **No** | No | **Yes** (scenarios only) | Blind to everything except design |
| debugger | **Yes** | **No** | **Yes** | No | Reads source + test RESULTS only |
| optimizer | **Yes** | **No** | **Yes** | No | Reads source, blind to tests |

\* codebase-scout may read test directories during codebase exploration, but this is read-only and doesn't violate INV-1.

## Appendix C: Delegation Type Mapping

| Sub-Agent | Delegation Type Received | Return delegation_type | Notes |
|-----------|-------------------------|----------------------|-------|
| codebase-scout | `exploration` | `exploration` | Matches ExplorationReturn |
| research-scout | `research` | `research` | Matches ResearchReturn |
| plan-checker | `guided` | `plan_verification` | Custom return extending GuidedReturn |
| audit-checker | `guided` | `audit` | Custom return extending GuidedReturn |
| test-writer | `tdd_chunk` | `test_writing` | Custom return extending TddChunkReturn |
| implementer | `tdd_chunk` or `targeted` | `implementation` | Custom return; accepts both delegation types |
| scenario-writer | `tdd_chunk` | `scenario_writing` | Custom return extending TddChunkReturn |
| debugger | `guided` | `debugging` | Custom return extending GuidedReturn |
| optimizer | `guided` | `optimization` | Custom return extending GuidedReturn |

**Note on custom return types:** Sub-agents with custom return types (plan_verification, audit, test_writing, implementation, scenario_writing, debugging, optimization) extend the base return schema for their delegation type but add domain-specific fields. The SubagentStop hook dispatches validation by `delegation_type` in the return, not by the delegation type sent. New Pydantic models in `schemas/delegation_return.py` are needed for: `PlanVerificationReturn`, `AuditReturn`, `TestWritingReturn`, `ImplementationReturn`, `ScenarioWritingReturn`, `DebuggingReturn`, `OptimizationReturn`.
