# Base Director Pattern — Universal Teammate Lifecycle

## 1. Purpose

This document defines the behavioral specification that ALL V2 teammate agents inherit. It is the abstract base class — complete for all universal behaviors, with clearly marked extension points where per-teammate specializations plug in.

**Relationship to AGENT-ARCHITECTURE.md:** That document defines the *structural* spec (what agents exist, their properties, invariants, dispatch rules). This document defines the *behavioral* spec (how agents operate through their lifecycle).

**Inheritance rule:** If a behavior is not defined in a per-teammate specialization, this document defines it. Per-teammate documents only specify their differences.

**Applies to:** Explorer, Researcher, Planner, Coder, Tester, Auditor (all 6 Tier 1 teammates).

---

## 2. The Five Lifecycle Phases

Every teammate cycles through the same five phases for each unit of work. The phases are sequential for a single work item, but a persistent teammate may cycle through them multiple times (one per work item) before terminating.

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│   INGRESS ──► DELIBERATION ──► DELEGATION ──► SYNTHESIS ──► EGRESS │
│                     │                                        │      │
│                     ▼                                        ▼      │
│              [SELF_EXECUTE] ────────────────────────► [EGRESS]      │
│                                                                     │
│   ═══════════════════════════════════════════════════════════════   │
│   Orthogonal (any phase):  HANDOFF | ERROR | CLARIFICATION         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Phase 1: Request Ingress

The teammate receives a work item and loads the context needed to reason about it.

**Work sources:**

| Source | Message Type | Example |
|--------|-------------|---------|
| Orchestrator assigns work | `task_assign` | "Explore the auth module" |
| Another teammate needs info | `info_request` (via orchestrator or direct SendMessage) | "Need codebase context for Pipeline class" |
| Sub-agent results routed back | `info_ready` | "Results from codebase-scout at .claude/temp/result-abc.json" |
| Re-execution trigger | `task_assign` with flag | "Re-run scenarios — remediation fixes merged" |

**Universal flow:**

1. Parse incoming message — extract task type, instructions, input file paths
2. Load context files referenced in `inputs` / `file_paths`
3. Validate prerequisites — required files exist, dependencies met
4. Transition to Deliberation

**`[EXT: INGRESS_CONTEXT_LOADING]`** — Each teammate defines what context it loads:

| Teammate | Context Loaded |
|----------|---------------|
| Explorer | Project structure, existing context packets for overlap check |
| Researcher | Research index, existing research files for dedup check |
| Planner | Design document, codebase context packet, existing plan state |
| Coder | Plan chunk, task worktree, previous handoff if continuation |
| Tester | Design doc, plan (phase-level), approved scenario list |
| Auditor | Design doc, plan, implementation source + test source |

### Phase 2: Deliberation

The teammate reasons about how to handle the work item. This is the critical decision point that determines whether the teammate acts directly or delegates.

**Three sub-decisions (evaluated in order):**

**Decision A: Cross-Domain Check**

Does this work require expertise outside my domain?

| Signal | Action |
|--------|--------|
| Work targets files/modules outside my domain | Send `info_request` to specialist teammate |
| Need API docs but I'm not Researcher | Send `info_request` to Researcher |
| Need codebase context but I'm not Explorer | Send `info_request` to Explorer |

Cross-domain requests are routed per INV-2 (Domain Separation). The teammate NEVER dispatches another teammate's sub-agent types. See AGENT-ARCHITECTURE.md Section 5.2 for the full cross-domain communication table.

**Decision B: Self-vs-Delegate**

Can I do this work myself?

| Condition | Action |
|-----------|--------|
| Task requires < 3 tool calls AND no judgment | Do it yourself |
| Task is a single Bash command or file read | Do it yourself |
| Delegation cost estimate < spawn overhead (15-20 tool calls) | Do it yourself |
| Task requires substantial mechanical work | Delegate |
| Task requires parallel agents for depth/accuracy | Delegate |
| Task requires judgment about delegation strategy | Use Think, then delegate |

**Delegation cost heuristic** (from delegation-prompts skill): Count items in `file_coordinates` + `unknowns` + `essential_output`. Each item is ~2-4 tool calls for the delegate. If `sum * 3 < 15`, do it yourself.

**Decision C: Scope Analysis (if delegating)**

How many sub-agents, what partition, sequential or parallel?

| Situation | Dispatch Mode |
|-----------|--------------|
| One clear deliverable | Single sub-agent |
| Multiple independent parts, no shared write targets | Parallel sub-agents |
| Tasks share files or depend on each other's output | Sequential sub-agents |
| Unsure about independence | Default to sequential |

**Think-on-exit for DELIBERATION state:**

```
Q1: What is the core objective of this work item?
Q2: Does this require cross-domain expertise? If yes, which specialist?
Q3: Can I do this myself? (< 3 tool calls, no judgment, delegation cost < spawn overhead)
Q4: If delegating: how many sub-agents? What partition? Sequential or parallel?
Q5: What delegation type fits? (targeted/guided/exploration/research/tdd_chunk)
[EXT: DOMAIN_THINK_QUESTIONS — appended per-teammate]
CHOSEN: SELF_EXECUTE | DELEGATE_SINGLE | DELEGATE_PARALLEL | CROSS_DOMAIN | CLARIFY
```

**`[EXT: DELIBERATION_THINK_PROMPT]`** — Each teammate appends domain-specific questions after Q5. For example, Explorer adds partitioning strategy questions; Coder adds TDD phase questions; Auditor adds severity assessment questions.

### Phase 3: Sub-Agent Delegation

The teammate composes a delegation prompt, writes it to disk, and requests the orchestrator to spawn the sub-agent. This phase is always orchestrator-mediated — even though the teammate writes the prompt, the orchestrator is the sole spawner.

**Step 1: Compose delegation prompt**

Run the Q1-Q5 pre-delegation reasoning chain (from delegation-prompts skill):

| Question | Maps to JSON field |
|----------|--------------------|
| Q1: What do I know that the delegate won't? | `known_context.findings` + `adjacent_context` |
| Q2: What files did I read? | `known_context.file_coordinates` |
| Q3: What decisions constrain the solution? | `scope_boundary` |
| Q4: What did I try or reject? | `rejected_approaches` |
| Q5: What does "done" look like? | `output_contract` + `success_criteria` |

Select delegation type based on what you have:

| You have... | Delegation type |
|-------------|----------------|
| File coordinates + known solution shape | `targeted` |
| Context but delegate must make decisions | `guided` |
| Plan chunk with test specifications | `tdd_chunk` |
| Partitioned exploration targets | `exploration` |
| Research questions with source preferences | `research` |

**Step 2: Write delegation file**

Write the complete delegation JSON to `.claude/temp/delegation-{uuid}.json`:

```json
{
  "delegation_id": "<uuid>",
  "requesting_teammate": "<teammate_type>",
  "sub_agent_type": "<authorized_type | general>",
  "model": "sonnet | haiku",
  "skills_to_load": [],
  "parallel_group": null,
  "priority": "normal | blocking",
  "delegation_prompt": {
    "type": "<delegation_type>",
    "task": "...",
    "known_context": { "file_coordinates": [], "findings": [] },
    "output_contract": { "path": "...", "format": "..." },
    "return_schema": { ... },
    "return_instruction": "Return a JSON object with delegation_type and status fields."
  }
}
```

**Step 3: Request spawn via orchestrator**

Send `info_request` to orchestrator:

```json
{
  "type": "info_request",
  "from": "<teammate>",
  "to": "lead",
  "payload": {
    "request_type": "sub_agent_spawn",
    "delegation_file": ".claude/temp/delegation-<uuid>.json",
    "sub_agent_type": "<type>",
    "model": "sonnet",
    "count": 1,
    "parallel": false,
    "priority": "blocking"
  }
}
```

For parallel dispatches: write N delegation files, send one request with `count: N, parallel: true` and `delegation_files: [...]`.

**Step 4: Orchestrator processes request**

1. Reads delegation file
2. Validates requesting teammate is an authorized dispatcher for the sub-agent type (AGENT-ARCHITECTURE.md Section 5.1)
3. Spawns sub-agent via Task tool with the delegation prompt
4. Sub-agent executes and returns structured JSON
5. Orchestrator writes result to `.claude/temp/result-{uuid}.json`
6. Orchestrator sends `info_ready` back to teammate

```json
{
  "type": "info_ready",
  "from": "lead",
  "to": "<teammate>",
  "payload": {
    "file_paths": [".claude/temp/result-<uuid>.json"],
    "summary": "codebase-scout completed: mapped 12 modules in src/pipeline/",
    "status": "fulfilled | partial | failed"
  }
}
```

**Step 5: Teammate reads results**

Teammate reads the result file(s) and transitions to Synthesis.

---

#### General Sub-Agent Protocol

Any teammate can request a general-purpose sub-agent by setting `sub_agent_type: "general"`. This handles edge cases where:
- Authorized sub-agents are insufficient for this specific task
- The work doesn't fit any specialist teammate's domain
- A one-off analysis is needed

```json
{
  "sub_agent_type": "general",
  "model": "sonnet",
  "skills_to_load": ["context-packets", "quality-gate"],
  "delegation_prompt": { ... }
}
```

The orchestrator spawns the general sub-agent with `base-agent` + the requested skills. The general sub-agent follows the same return protocol as specialized sub-agents.

**When NOT to use general sub-agents:**
- Work is within another teammate's domain → message the specialist (INV-2)
- Work fits an authorized sub-agent type → use the authorized type
- Work requires < 3 tool calls → do it yourself

General sub-agents are a pressure valve, not a primary delegation path.

---

**`[EXT: AUTHORIZED_SUB_AGENTS]`** — List of sub-agent types this teammate can dispatch:

| Teammate | Authorized Sub-Agents |
|----------|----------------------|
| Explorer | codebase-scout |
| Researcher | research-scout |
| Planner | plan-checker |
| Coder | implementer, test-writer, debugger, optimizer |
| Tester | scenario-writer, test-writer |
| Auditor | audit-checker, debugger |

**`[EXT: DELEGATION_COMPOSITION]`** — Primary delegation type(s) used by this teammate.

**`[EXT: MODEL_SELECTION_HEURISTIC]`** — How this teammate chooses Haiku vs Sonnet (relevant for Explorer and Researcher whose sub-agents support both models).

### Phase 4: Results Synthesis

The teammate processes sub-agent return(s) and produces a synthesized result.

#### Single Sub-Agent Synthesis

1. **Read result file** from the `info_ready` file path
2. **Validate return schema** — check `delegation_type` and `status` fields present and valid
3. **Route by status:**
   - `completed` → verify artifacts exist on disk, extract `decisions_made`, proceed
   - `partial` → assess coverage — is the partial result useful? Accept or retry
   - `failed` → route to ERROR state for recovery decision
4. **Apply domain verification** — domain-specific checks on the result content
5. **Extract carry-forward data** — decisions, unexpected findings, cross-scope findings for future work items

#### Multi-Agent Synthesis (Parallel Returns)

When N sub-agents were dispatched in parallel:

1. **Collect** all result files from `info_ready` file paths
2. **Triage by status** — separate completed, partial, and failed returns
3. **Handle mixed statuses:**
   - All completed → proceed to full synthesis
   - Some partial/failed → assess: is what succeeded enough? Route to ERROR if not
4. **Deduplicate** overlapping findings between partitioned sub-agents
5. **Resolve conflicts** — when two sub-agents contradict each other, investigate evidence from both. Pick the substantiated finding or flag for escalation
6. **Cross-cutting connections** — individual sub-agents see their partition, not the whole. Dependencies, patterns, or issues that span partitions are the teammate's responsibility to identify
7. **Verify the whole** — the combined result must be checked as a unit, not just the sum of parts

#### PTC-Based Synthesis

When sub-agent results are large (multiple JSON files, extensive code analysis), use PTC to process them:

1. Load all result files into PTC container
2. Run synthesis script that merges, deduplicates, and structures
3. Print JSON summary to context (raw data stays in container)
4. Use the summary to compose the final output

This follows the "process in container, print JSON summary" discipline — 400KB of sub-agent results stays in the container; only the 200-byte structured summary enters the teammate's context.

**Think-on-exit for SYNTHESIS state:**

```
Q1: Did all sub-agents return? List each and its status.
Q2: For parallel returns: any contradictions or overlapping findings?
Q3: Are results complete relative to the original scope from DELIBERATION?
Q4: Any unexpected findings that change the work plan?
[EXT: DOMAIN_SYNTHESIS_QUESTIONS — appended per-teammate]
CHOSEN: ACCEPT | PARTIAL_ACCEPT | RETRY | ESCALATE
```

**`[EXT: SYNTHESIS_OUTPUT_TYPE]`** — What the teammate produces:

| Teammate | Output Type |
|----------|------------|
| Explorer | Context packet (`.claude/context/`) |
| Researcher | Research file with citations (`.claude/research/`) |
| Planner | Implementation plan (`.claude/plans/`) |
| Coder | Production code + passing tests (task worktree) |
| Tester | Scenario test code + execution report (`.claude/reports/`) |
| Auditor | Audit report with findings (`.claude/reports/`) |

**`[EXT: DOMAIN_VERIFICATION]`** — Domain-specific checks on synthesized results (e.g., Explorer validates context packet schema; Planner runs plan-checker loop; Auditor checks escalation severity).

### Phase 5: Answer Egress

The teammate delivers the synthesized result and signals completion.

**Step 1: Write output to disk**

Write the final output to the appropriate location per the file locations table. All outputs are files — never sent as message content.

**Step 2: Run post-action validation**

Each write state has `post_actions` that run automatically:

| Teammate | Post-Action |
|----------|------------|
| Explorer | `validate_context_packet_schema` |
| Researcher | `validate_research_schema` |
| Planner | `validate_plan_schema` |
| Coder | `ruff_lint_critical`, `run_quality_gate` |
| Tester | `validate_scenario_schema` |
| Auditor | `validate_audit_report_schema` |

If validation fails, fix and re-validate before sending the completion message.

**Step 3: Notify requester**

Send `task_complete` to orchestrator (or `info_ready` if responding to an `info_request`):

```json
{
  "type": "task_complete",
  "from": "<teammate>",
  "to": "lead",
  "payload": {
    "status": "success | partial",
    "output_files": ["<path/to/output>"],
    "summary": "<= 200 chars describing what was produced>",
    "context_pressure": false
  }
}
```

**Step 4: Transition**

| Persistence Model | Next State | Condition |
|-------------------|-----------|-----------|
| Persistent (Explorer, Researcher, Coder, Auditor) | IDLE | Wait for next work item |
| Per-feature (Planner) | TERMINATED | Plan approved, work done |
| Per-phase (Tester) | IDLE within phase | Wait for next execution cycle |
| Any | HANDOFF | Context pressure detected |

**`[EXT: OUTPUT_WRITE_GLOBS]`** — Glob patterns for where this teammate writes output.

**`[EXT: POST_ACTIONS]`** — Validation checks applied after writing.

**`[EXT: PERSISTENCE_MODEL]`** — Whether this teammate goes to IDLE or TERMINATED after delivery.

---

## 3. Base State Template

These states form the universal skeleton. Per-teammate state machines extend this by INSERTING domain-specific state clusters between base states (e.g., Coder inserts the TDD cycle between DELIBERATION and SYNTHESIS) or AUGMENTING base states with additional guards, actions, or write globs.

### 3.1 State Catalog

```
SPAWNED
  │
  ▼
CONTEXT_LOADING ─────────────────────────────────────────────────┐
  │                                                               │
  ▼                                                               │
DELIBERATION ──── CLARIFY ────► AWAITING_CLARIFICATION ──────┐   │
  │                                (max 2 rounds)             │   │
  ├── SELF_EXECUTE ──────────────────────────────────► EGRESS │   │
  │                                                           │   │
  ├── CROSS_DOMAIN ──► (send info_request, wait) ────► back   │   │
  │                                                           │   │
  ▼                                                           │   │
SUB_AGENT_DISPATCH ──── [EXT: DOMAIN_STATES] ──────────┐      │   │
  │                                                     │      │   │
  ▼                                                     │      │   │
SYNTHESIS                                               │      │   │
  │                                                     │      │   │
  ▼                                                     ▼      │   │
OUTPUT_VALIDATION ◄─────────────────────────────────────┘      │   │
  │                                                            │   │
  ▼                                                            │   │
DELIVERY ◄─────────────────────────────────────────────────────┘   │
  │                                                                │
  ├──► IDLE ──► RELATEDNESS_ASSESSMENT ──► CONTEXT_LOADING (loop)  │
  │                     │                                          │
  │                     ▼                                          │
  ▼                 TERMINATED ◄───────────────────────────────────┘

═══════════════════════════════════════════════════════════════════
Orthogonal (from any state):
  Any ──► ERROR (on failure)
  Any ──► HANDOFF (on context_pressure_exceeded)
```

### 3.2 State Definitions

#### SPAWNED

| Property | Value |
|----------|-------|
| **Purpose** | Agent just spawned. Reading initial assignment. |
| **write_allowed** | `false` |
| **think_on_exit** | `false` |

Transitions:
- → `CONTEXT_LOADING` on `assignment_read` (guard: `task_id_valid`)

#### CONTEXT_LOADING

| Property | Value |
|----------|-------|
| **Purpose** | Loading domain-specific context files. |
| **write_allowed** | `false` |
| **think_on_exit** | `false` |
| **Extension** | `[EXT: INGRESS_CONTEXT_LOADING]` defines what is loaded |

Transitions:
- → `DELIBERATION` on `context_loaded` (guard: `required_context_available`)

#### DELIBERATION

| Property | Value |
|----------|-------|
| **Purpose** | Core decision point: self-execute, delegate, cross-domain, or clarify. |
| **write_allowed** | `false` |
| **think_on_exit** | `true` |
| **think_prompt** | See Phase 2 think prompt (Q1-Q5 + domain extensions) |

Transitions:
- → `SELF_EXECUTING` on `self_execute` (guard: `think_chosen:SELF_EXECUTE`)
- → `SUB_AGENT_DISPATCH` on `delegate_single` (guard: `think_chosen:DELEGATE_SINGLE`)
- → `SUB_AGENT_DISPATCH` on `delegate_parallel` (guard: `think_chosen:DELEGATE_PARALLEL`)
- → `AWAITING_CROSS_DOMAIN` on `cross_domain` (guard: `think_chosen:CROSS_DOMAIN`)
- → `AWAITING_CLARIFICATION` on `need_clarification` (guard: `think_chosen:CLARIFY`)
- → `[EXT: DOMAIN_STATES]` — teammates may add transitions to domain-specific states

#### AWAITING_CLARIFICATION

| Property | Value |
|----------|-------|
| **Purpose** | Blocked on orchestrator/user input. Cannot resolve scope ambiguity from context alone. |
| **write_allowed** | `false` |
| **think_on_exit** | `false` |

Transitions:
- → `DELIBERATION` on `clarification_received` (guard: `orchestrator_response_received`, max_occurrences: 2)
- → `TERMINATED` on `clarification_timeout` (guard: `clarification_wait_exceeded`)

**Rule:** After 2 unanswered clarification rounds, the work item is too ill-defined. Terminate with notification.

#### SELF_EXECUTING

| Property | Value |
|----------|-------|
| **Purpose** | Minor work done directly (< 3 tool calls, no judgment). |
| **write_allowed** | `true` (scoped to `[EXT: OUTPUT_WRITE_GLOBS]`) |
| **think_on_exit** | `false` |
| **post_actions** | `[EXT: POST_ACTIONS]` |

Transitions:
- → `OUTPUT_VALIDATION` on `self_work_complete`

#### AWAITING_CROSS_DOMAIN

| Property | Value |
|----------|-------|
| **Purpose** | Sent info_request to specialist teammate. Waiting for info_ready. |
| **write_allowed** | `false` |
| **think_on_exit** | `false` |

Transitions:
- → `DELIBERATION` on `cross_domain_response_received` (guard: `info_ready_received`)
  - Re-enters deliberation with new context to decide how to proceed
- → `ERROR` on `cross_domain_timeout` (guard: `response_wait_exceeded`)

#### SUB_AGENT_DISPATCH

| Property | Value |
|----------|-------|
| **Purpose** | Delegation file(s) written. Spawn request sent to orchestrator. Waiting for results. |
| **write_allowed** | `true` (`.claude/temp/delegation-*.json` only) |
| **think_on_exit** | `true` |
| **think_prompt** | "1) Did all requested sub-agents return? 2) Are any results empty or suspiciously shallow? 3) Do combined results cover the scope from DELIBERATION? 4) Any unexpected findings? CHOSEN: PROCEED | RETRY | PARTIAL" |

Transitions:
- → `SYNTHESIS` on `all_results_received` (guard: `think_chosen:PROCEED`)
- → `[EXT: DOMAIN_STATES]` — teammates may route to domain-specific states before synthesis
- → `ERROR` on `dispatch_failed` (guard: `sub_agent_error_reported`)

#### SYNTHESIS

| Property | Value |
|----------|-------|
| **Purpose** | Processing sub-agent returns into synthesized output. |
| **write_allowed** | `true` (scoped to `[EXT: OUTPUT_WRITE_GLOBS]`) |
| **think_on_exit** | `true` |
| **think_prompt** | See Phase 4 synthesis think prompt |
| **post_actions** | `[EXT: POST_ACTIONS]` |

Transitions:
- → `OUTPUT_VALIDATION` on `synthesis_complete` (guard: `think_chosen:ACCEPT`)
- → `SUB_AGENT_DISPATCH` on `retry_needed` (guard: `think_chosen:RETRY`)
- → `OUTPUT_VALIDATION` on `partial_accepted` (guard: `think_chosen:PARTIAL_ACCEPT`)
- → `ERROR` on `escalation_needed` (guard: `think_chosen:ESCALATE`)

#### OUTPUT_VALIDATION

| Property | Value |
|----------|-------|
| **Purpose** | Post-action validation checks on written output. |
| **write_allowed** | `false` |
| **think_on_exit** | `false` |

Transitions:
- → `DELIVERY` on `validation_passed` (guard: `all_post_actions_pass`)
- → `SYNTHESIS` on `validation_failed` (guard: `validation_errors_exist`, max_occurrences: 2)

#### DELIVERY

| Property | Value |
|----------|-------|
| **Purpose** | Sending task_complete or info_ready with output file paths. |
| **write_allowed** | `false` |
| **think_on_exit** | `false` |

Transitions:
- → `IDLE` on `delivery_confirmed` (guard: `[EXT: PERSISTENCE_MODEL] == persistent`)
- → `TERMINATED` on `work_complete` (guard: `[EXT: PERSISTENCE_MODEL] == ephemeral`)

#### IDLE

| Property | Value |
|----------|-------|
| **Purpose** | Between work items. Persistent teammates only. Waiting for next assignment or termination. |
| **write_allowed** | `false` |
| **think_on_exit** | `false` |

Transitions:
- → `RELATEDNESS_ASSESSMENT` on `new_work_received` (guard: `work_item_non_empty`)
- → `TERMINATED` on `no_more_work` (guard: `no_pending_items`)

#### RELATEDNESS_ASSESSMENT

| Property | Value |
|----------|-------|
| **Purpose** | Evaluate whether new work is related to previous scope. Persistent teammates only. |
| **write_allowed** | `false` |
| **think_on_exit** | `true` |
| **think_prompt** | "1) What scope did the previous work cover? What does the new work target? 2) Is there meaningful overlap (shared directories, modules, topic area)? 3) Would stale assumptions from previous work mislead on the new work? 4) Would terminating lose expensive cached context? CHOSEN: RELATED \| UNRELATED" |

Transitions:
- → `CONTEXT_LOADING` on `related` (guard: `think_chosen:RELATED`)
  - Re-enters with cached context intact, loads delta
- → `TERMINATED` on `unrelated` (guard: `think_chosen:UNRELATED`)
  - Terminates so orchestrator can spawn fresh instance with clean context

#### ERROR

| Property | Value |
|----------|-------|
| **Purpose** | Failure diagnosis and recovery. See Section 5 for full protocol. |
| **write_allowed** | `false` |
| **think_on_exit** | `true` |
| **think_prompt** | See Section 5 error recovery think prompt |

Transitions:
- → `DELIBERATION` on `retry` (guard: `think_chosen:RETRY`, max_occurrences: 1)
- → `SYNTHESIS` on `partial_salvage` (guard: `think_chosen:PARTIAL_SYNTHESIS`)
- → `HANDOFF` on `handoff_recovery` (guard: `think_chosen:HANDOFF`)
- → `TERMINATED` on `abort` (guard: `think_chosen:ABORT`)
- → `[EXT: DOMAIN_ERROR_PATHS]` — teammates may add domain-specific recovery paths

#### HANDOFF

| Property | Value |
|----------|-------|
| **Purpose** | Context pressure escape or error recovery handoff. Write summary and terminate for replacement. |
| **write_allowed** | `true` (`.claude/handoffs/**` only) |
| **think_on_exit** | `false` |

Actions on entry:
1. Write handoff summary to `.claude/handoffs/{teammate}-{uuid}.json` containing:
   - Current phase and progress
   - Files modified / output written so far
   - Decisions made and their rationale
   - Unresolved questions
   - Resume instructions (one-line for successor)
2. Send `status_update` to orchestrator with `needs_replacement: true` and handoff path

Transitions:
- → `TERMINATED` on `handoff_written`

#### TERMINATED

Terminal state. Agent shuts down.

### 3.3 Universal Transitions

These fire from ANY state:

```json
{
  "to_state": "HANDOFF",
  "trigger": "context_pressure_exceeded",
  "guards": ["context_usage_above_threshold"],
  "actions": ["write_handoff_summary", "notify_orchestrator_handoff"]
}
```

### 3.4 Extension Mechanisms

Per-teammate state machines extend the base in three ways:

**INSERT** — Add domain-specific state clusters between base states:
```
DELIBERATION → [DOMAIN_STATES: TDD_RED, IMPLEMENTATION, TDD_GREEN, ...] → SYNTHESIS
```

**AUGMENT** — Add guards, actions, or write_globs to a base state:
```
SYNTHESIS state gets additional guard: "plan_invariants_checked"
```

**OVERRIDE** — Replace a base state with a domain-specific version:
```
OUTPUT_VALIDATION replaced with multi-gate PLAN_TEXT_REVIEW → USER_APPROVAL → JSON_CONVERSION
```

---

## 4. Think Protocol

Every deliberation point uses the Think MCP (or internal think-on-exit mechanism) with the same structured format.

### 4.1 Format

```
[Universal questions — numbered, present for all teammates]
[EXT: Domain questions — appended per-teammate]
CHOSEN: OPTION_A | OPTION_B | ...
```

Guards check the CHOSEN value: `think_chosen:OPTION_A`.

### 4.2 Universal Think Points

**T1: Post-Context Deliberation** (DELIBERATION state)
```
Q1: What is the core objective of this work item?
Q2: Does this require cross-domain expertise? If yes, which specialist?
Q3: Can I do this myself? (< 3 tool calls, no judgment, delegation cost < spawn overhead)
Q4: If delegating: how many sub-agents? What partition? Sequential or parallel?
Q5: What delegation type fits? (targeted/guided/exploration/research/tdd_chunk)
[EXT: DOMAIN_THINK_QUESTIONS]
CHOSEN: SELF_EXECUTE | DELEGATE_SINGLE | DELEGATE_PARALLEL | CROSS_DOMAIN | CLARIFY
```

**T2: Sub-Agent Results Assessment** (SUB_AGENT_DISPATCH exit)
```
Q1: Did all requested sub-agents return? List each and its status.
Q2: Are any results suspiciously empty or shallow for their assigned scope?
Q3: Do combined results cover the full scope from DELIBERATION?
Q4: Any unexpected findings that change the plan?
[EXT: DOMAIN_DISPATCH_ASSESSMENT]
CHOSEN: PROCEED | RETRY | PARTIAL
```

**T3: Synthesis Assessment** (SYNTHESIS exit)
```
Q1: For parallel returns: any contradictions between sub-agent findings?
Q2: Are results complete relative to the original work item scope?
Q3: Did synthesis identify cross-cutting connections the sub-agents missed?
Q4: Any unexpected findings that should be escalated or carried forward?
[EXT: DOMAIN_SYNTHESIS_QUESTIONS]
CHOSEN: ACCEPT | PARTIAL_ACCEPT | RETRY | ESCALATE
```

**T4: Error Recovery** (ERROR state — see Section 5)

**T5: Relatedness Assessment** (RELATEDNESS_ASSESSMENT state)
```
Q1: What scope did the previous work cover? What does the new work target?
Q2: Is there meaningful overlap (shared directories, modules, topic area)?
Q3: Would stale assumptions from previous work mislead?
Q4: Would terminating lose expensive cached context?
CHOSEN: RELATED | UNRELATED
```

---

## 5. Failure Handling

### 5.1 ERROR State Entry Conditions

The ERROR state is entered from any phase when:

| Condition | Source |
|-----------|--------|
| Sub-agent dispatch failure | Crash, timeout, wrong output |
| Infrastructure failure | PTC unavailable, container error, MCP down |
| Validation failure | Output schema invalid after retries |
| Cross-domain timeout | Specialist teammate didn't respond |
| Cascade failure | Same sub-agent type failed 3x across dispatch attempts |

### 5.2 Four-Path Recovery

Every ERROR state uses the same think structure. All four paths MUST be evaluated — no shortcuts.

```
DIAGNOSIS:
1) Which specific operation failed? What error type (timeout, crash, wrong output, permission)?
2) Is the failure transient (timeout, rate limit, container restart) or structural (path doesn't exist, API removed, environment corrupted)? State evidence.
3) What work completed successfully before the failure? List files/artifacts.
4) What percentage of the original scope is covered by successful work?

RECOVERY DECISION (evaluate ALL four, choose one):
5) RETRY: Is the failure transient AND has retry budget not been used? What would you change on retry?
6) PARTIAL_SYNTHESIS: Do partial results exist AND are they useful (not misleading)? What coverage %?
7) HANDOFF: Does meaningful progress exist that a successor can continue? What transfers?
8) ABORT: Is there genuinely no salvageable work?

CHOSEN: RETRY | PARTIAL_SYNTHESIS | HANDOFF | ABORT
```

**Recovery path details:**

| Path | Guard | Action | Next State |
|------|-------|--------|------------|
| RETRY | Transient failure + budget remaining (max 1) | Reset sub-agent state, adjust prompt | DELIBERATION |
| PARTIAL_SYNTHESIS | Partial results exist + meet minimum coverage | Mark output as partial, reduce confidence | SYNTHESIS |
| HANDOFF | Meaningful progress exists | Write handoff summary | HANDOFF |
| ABORT | No salvageable work | Log failure, notify orchestrator | TERMINATED |

### 5.3 Additional Failure Patterns

**Clarification exhaustion:** AWAITING_CLARIFICATION has max 2 rounds. After 2, terminate with notification — the work item is too ill-defined.

**Stuck detection:** For teammates with iterative work (Coder's implement-test loop, Tester's execution-reporting cycle), a PROGRESS_CHECK state evaluates forward progress:

```
Q1: Compared to the previous iteration, did measurable progress occur?
Q2: What specific changes happened since last check?
Q3: If continuing: what specific action will make progress?
Q4: If stuck: what evidence shows the approach cannot succeed?
CHOSEN: CONTINUE | STUCK
```

STUCK routes to APPROACH_ASSESSMENT or ERROR. This is a `[EXT: DOMAIN_ERROR_PATHS]` extension.

**Cascade failure:** If the same sub-agent type fails 3 times across different dispatch attempts within the same work item, escalate to orchestrator. Don't keep retrying the same broken pattern.

---

## 6. Communication Protocol

This section specializes the team-messaging protocol (`.claude/protocols/team-messaging.md`) for the director context.

### 6.1 Message Types Used by Directors

| Direction | Type | When |
|-----------|------|------|
| Teammate → Orchestrator | `task_complete` | Work done, output files written |
| Teammate → Orchestrator | `info_request` | Need sub-agent spawn, need info from another teammate |
| Teammate → Orchestrator | `status_update` | Context pressure, progress, needs_replacement |
| Orchestrator → Teammate | `task_assign` | New work item, re-execution trigger |
| Orchestrator → Teammate | `info_ready` | Sub-agent results routed back, info from specialist |
| Teammate → Teammate | `info_request` | Direct cross-domain query (via SendMessage) |
| Teammate → Teammate | `info_ready` | Direct response to cross-domain query |

### 6.2 Sub-Agent Spawn Request

Specialized `info_request` with `request_type: "sub_agent_spawn"`:

```json
{
  "type": "info_request",
  "from": "<teammate>",
  "to": "lead",
  "payload": {
    "request_type": "sub_agent_spawn",
    "delegation_file": ".claude/temp/delegation-<uuid>.json",
    "sub_agent_type": "<type | general>",
    "model": "sonnet | haiku",
    "count": 1,
    "parallel": false,
    "priority": "blocking"
  }
}
```

For parallel dispatches:
```json
{
  "payload": {
    "request_type": "sub_agent_spawn",
    "delegation_files": [
      ".claude/temp/delegation-<uuid1>.json",
      ".claude/temp/delegation-<uuid2>.json"
    ],
    "sub_agent_type": "codebase-scout",
    "model": "haiku",
    "count": 2,
    "parallel": true,
    "priority": "blocking"
  }
}
```

### 6.3 Cross-Domain Query Flow

```
Coder                    Orchestrator              Explorer
  │                          │                        │
  ├─ info_request ──────────►│                        │
  │  (request_type:          │                        │
  │   "codebase",            ├── task_assign ────────►│
  │   query: "Pipeline       │                        ├─ dispatches scouts
  │    class structure")     │                        ├─ synthesizes
  │                          │                        ├─ writes context packet
  │                          │  ◄── task_complete ────┤
  │                          │                        │
  │  ◄── info_ready ─────────┤                        │
  │  (file_paths: [          │                        │
  │   ".claude/context/      │                        │
  │    pipeline-ctx.json"])   │                        │
  │                          │                        │
  ├─ reads context packet    │                        │
  └─ re-enters DELIBERATION  │                        │
```

### 6.4 Communication Rules

All rules from team-messaging.md apply. Key rules for directors:

1. **Write first, message second** — finish all work, then send ONE message
2. **Paths not content** — messages contain file paths, never file content
3. **Summaries <= 200 chars** — for routing decisions, not full context transfer
4. **One message per event** — don't combine message types
5. **No idle-triggered messages** — wait 3 minutes, check files silently first
6. **No shutdown without user approval** — even for completed teammates

**`[EXT: CROSS_DOMAIN_PARTNERS]`** — Regular communication partners:

| Teammate | Partners |
|----------|----------|
| Explorer | Researcher (API docs), all others (context requests) |
| Researcher | Explorer (codebase context) |
| Planner | Explorer (context), Researcher (feasibility) |
| Coder | Explorer (context), Auditor (reviews) |
| Tester | Explorer (interface info), Coder (failure reports) |
| Auditor | Coder (fix instructions), Tester (test fixes), Explorer (context) |

---

## 7. Customization Points — Extension API

Consolidated table of all extension points. Per-teammate specialization documents fill these in.

| Extension Point | Type | Description |
|-----------------|------|-------------|
| `INGRESS_CONTEXT_LOADING` | State override | What context this teammate loads on spawn |
| `DELIBERATION_THINK_PROMPT` | Think augmentation | Domain-specific questions appended to universal Q1-Q5 |
| `DOMAIN_THINK_QUESTIONS` | Think augmentation | Questions added to the deliberation think point |
| `DOMAIN_DISPATCH_ASSESSMENT` | Think augmentation | Questions added to the dispatch results assessment |
| `DOMAIN_SYNTHESIS_QUESTIONS` | Think augmentation | Questions added to the synthesis assessment |
| `AUTHORIZED_SUB_AGENTS` | Configuration | Sub-agent types this teammate can dispatch |
| `DELEGATION_COMPOSITION` | Behavioral | Primary delegation type(s) and how prompts are composed |
| `MODEL_SELECTION_HEURISTIC` | Behavioral | Haiku vs Sonnet selection logic |
| `SYNTHESIS_OUTPUT_TYPE` | Configuration | What the teammate produces from synthesis |
| `DOMAIN_VERIFICATION` | Behavioral | Domain-specific checks on synthesized results |
| `OUTPUT_WRITE_GLOBS` | Configuration | Glob patterns for output files |
| `POST_ACTIONS` | Configuration | Validation checks after writes |
| `PERSISTENCE_MODEL` | Configuration | IDLE (persistent) vs TERMINATED (ephemeral) |
| `DOMAIN_STATES` | State insertion | Domain-specific state clusters inserted between base states |
| `DOMAIN_ERROR_PATHS` | State extension | Additional error recovery paths beyond the base 4 |
| `SELF_EXECUTE_SCOPE` | Behavioral | What minor work this teammate does directly |
| `CROSS_DOMAIN_PARTNERS` | Configuration | Regular cross-domain communication partners |

---

## Appendix A: Base State Machine JSON Template

```json
{
  "name": "base-director",
  "description": "Universal teammate lifecycle. Extended by per-teammate state machines.",
  "version": "1.0.0",
  "agent_role": "[TEAMMATE_TYPE]",
  "initial_state": "SPAWNED",
  "terminal_states": ["TERMINATED"],
  "universal_transitions": [
    {
      "to_state": "HANDOFF",
      "trigger": "context_pressure_exceeded",
      "guards": ["context_usage_above_threshold"],
      "actions": ["write_handoff_summary", "notify_orchestrator_handoff"]
    }
  ],
  "states": [
    {
      "name": "SPAWNED",
      "description": "Agent just spawned. Reading initial assignment.",
      "write_allowed": false
    },
    {
      "name": "CONTEXT_LOADING",
      "description": "Loading domain-specific context. [EXT: INGRESS_CONTEXT_LOADING]",
      "write_allowed": false
    },
    {
      "name": "DELIBERATION",
      "description": "Core decision: self-execute, delegate, cross-domain, or clarify.",
      "write_allowed": false,
      "think_on_exit": true,
      "think_prompt": "Q1: Core objective? Q2: Cross-domain needed? Q3: Self-executable? Q4: Sub-agent count/partition? Q5: Delegation type? [EXT: DOMAIN_THINK_QUESTIONS] CHOSEN: SELF_EXECUTE | DELEGATE_SINGLE | DELEGATE_PARALLEL | CROSS_DOMAIN | CLARIFY"
    },
    {
      "name": "AWAITING_CLARIFICATION",
      "description": "Blocked on orchestrator/user clarification. Max 2 rounds.",
      "write_allowed": false
    },
    {
      "name": "SELF_EXECUTING",
      "description": "Minor work done directly (< 3 tool calls).",
      "write_allowed": true,
      "write_globs": ["[EXT: OUTPUT_WRITE_GLOBS]"],
      "post_actions": "[EXT: POST_ACTIONS]"
    },
    {
      "name": "AWAITING_CROSS_DOMAIN",
      "description": "Sent info_request to specialist. Waiting for info_ready.",
      "write_allowed": false
    },
    {
      "name": "SUB_AGENT_DISPATCH",
      "description": "Delegation files written. Waiting for sub-agent results.",
      "write_allowed": true,
      "write_globs": [".claude/temp/delegation-*.json"],
      "think_on_exit": true,
      "think_prompt": "Q1: All sub-agents returned? Q2: Results shallow? Q3: Scope covered? Q4: Unexpected findings? CHOSEN: PROCEED | RETRY | PARTIAL"
    },
    {
      "name": "SYNTHESIS",
      "description": "Processing sub-agent returns. [EXT: DOMAIN_VERIFICATION]",
      "write_allowed": true,
      "write_globs": ["[EXT: OUTPUT_WRITE_GLOBS]"],
      "think_on_exit": true,
      "think_prompt": "Q1: Contradictions? Q2: Complete? Q3: Cross-cutting? Q4: Unexpected? [EXT: DOMAIN_SYNTHESIS_QUESTIONS] CHOSEN: ACCEPT | PARTIAL_ACCEPT | RETRY | ESCALATE",
      "post_actions": "[EXT: POST_ACTIONS]"
    },
    {
      "name": "OUTPUT_VALIDATION",
      "description": "Post-action validation on written output.",
      "write_allowed": false
    },
    {
      "name": "DELIVERY",
      "description": "Sending task_complete/info_ready with file paths.",
      "write_allowed": false
    },
    {
      "name": "IDLE",
      "description": "Between work items. Persistent teammates only.",
      "write_allowed": false
    },
    {
      "name": "RELATEDNESS_ASSESSMENT",
      "description": "New work related to cached context? Persistent teammates only.",
      "write_allowed": false,
      "think_on_exit": true,
      "think_prompt": "Q1: Previous scope vs new target? Q2: Meaningful overlap? Q3: Stale assumptions? Q4: Expensive cached context lost? CHOSEN: RELATED | UNRELATED"
    },
    {
      "name": "ERROR",
      "description": "Failure diagnosis + 4-path recovery. [EXT: DOMAIN_ERROR_PATHS]",
      "write_allowed": false,
      "think_on_exit": true,
      "think_prompt": "DIAGNOSIS: 1) What failed? 2) Transient or structural? 3) What succeeded? 4) Coverage %? RECOVERY: 5) RETRY viable? 6) PARTIAL_SYNTHESIS viable? 7) HANDOFF viable? 8) ABORT? CHOSEN: RETRY | PARTIAL_SYNTHESIS | HANDOFF | ABORT"
    },
    {
      "name": "HANDOFF",
      "description": "Write handoff summary, terminate for replacement.",
      "write_allowed": true,
      "write_globs": [".claude/handoffs/**"]
    },
    {
      "name": "TERMINATED",
      "description": "Agent shut down."
    }
  ],
  "transitions": [
    {"from_state": "SPAWNED", "to_state": "CONTEXT_LOADING", "trigger": "assignment_read", "guards": ["task_id_valid"]},
    {"from_state": "CONTEXT_LOADING", "to_state": "DELIBERATION", "trigger": "context_loaded", "guards": ["required_context_available"]},

    {"from_state": "DELIBERATION", "to_state": "SELF_EXECUTING", "trigger": "self_execute", "guards": ["think_chosen:SELF_EXECUTE"]},
    {"from_state": "DELIBERATION", "to_state": "SUB_AGENT_DISPATCH", "trigger": "delegate_single", "guards": ["think_chosen:DELEGATE_SINGLE"]},
    {"from_state": "DELIBERATION", "to_state": "SUB_AGENT_DISPATCH", "trigger": "delegate_parallel", "guards": ["think_chosen:DELEGATE_PARALLEL"]},
    {"from_state": "DELIBERATION", "to_state": "AWAITING_CROSS_DOMAIN", "trigger": "cross_domain_request", "guards": ["think_chosen:CROSS_DOMAIN"]},
    {"from_state": "DELIBERATION", "to_state": "AWAITING_CLARIFICATION", "trigger": "need_clarification", "guards": ["think_chosen:CLARIFY"]},

    {"from_state": "AWAITING_CLARIFICATION", "to_state": "DELIBERATION", "trigger": "clarification_received", "guards": ["orchestrator_response_received"], "max_occurrences": 2},
    {"from_state": "AWAITING_CLARIFICATION", "to_state": "TERMINATED", "trigger": "clarification_timeout", "guards": ["clarification_wait_exceeded"]},

    {"from_state": "SELF_EXECUTING", "to_state": "OUTPUT_VALIDATION", "trigger": "self_work_complete"},

    {"from_state": "AWAITING_CROSS_DOMAIN", "to_state": "DELIBERATION", "trigger": "cross_domain_response", "guards": ["info_ready_received"]},
    {"from_state": "AWAITING_CROSS_DOMAIN", "to_state": "ERROR", "trigger": "cross_domain_timeout", "guards": ["response_wait_exceeded"]},

    {"from_state": "SUB_AGENT_DISPATCH", "to_state": "SYNTHESIS", "trigger": "results_received", "guards": ["think_chosen:PROCEED"]},
    {"from_state": "SUB_AGENT_DISPATCH", "to_state": "ERROR", "trigger": "dispatch_failed", "guards": ["sub_agent_error_reported"]},

    {"from_state": "SYNTHESIS", "to_state": "OUTPUT_VALIDATION", "trigger": "synthesis_accepted", "guards": ["think_chosen:ACCEPT"]},
    {"from_state": "SYNTHESIS", "to_state": "OUTPUT_VALIDATION", "trigger": "partial_accepted", "guards": ["think_chosen:PARTIAL_ACCEPT"]},
    {"from_state": "SYNTHESIS", "to_state": "SUB_AGENT_DISPATCH", "trigger": "retry_synthesis", "guards": ["think_chosen:RETRY"]},
    {"from_state": "SYNTHESIS", "to_state": "ERROR", "trigger": "escalation_needed", "guards": ["think_chosen:ESCALATE"]},

    {"from_state": "OUTPUT_VALIDATION", "to_state": "DELIVERY", "trigger": "validation_passed", "guards": ["all_post_actions_pass"]},
    {"from_state": "OUTPUT_VALIDATION", "to_state": "SYNTHESIS", "trigger": "validation_failed", "guards": ["validation_errors_exist"], "max_occurrences": 2},

    {"from_state": "DELIVERY", "to_state": "IDLE", "trigger": "delivered_persistent", "guards": ["persistence_model_persistent"]},
    {"from_state": "DELIVERY", "to_state": "TERMINATED", "trigger": "delivered_ephemeral", "guards": ["persistence_model_ephemeral"]},

    {"from_state": "IDLE", "to_state": "RELATEDNESS_ASSESSMENT", "trigger": "new_work_received", "guards": ["work_item_non_empty"]},
    {"from_state": "IDLE", "to_state": "TERMINATED", "trigger": "no_more_work", "guards": ["no_pending_items"]},

    {"from_state": "RELATEDNESS_ASSESSMENT", "to_state": "CONTEXT_LOADING", "trigger": "related", "guards": ["think_chosen:RELATED"]},
    {"from_state": "RELATEDNESS_ASSESSMENT", "to_state": "TERMINATED", "trigger": "unrelated", "guards": ["think_chosen:UNRELATED"]},

    {"from_state": "ERROR", "to_state": "DELIBERATION", "trigger": "retry", "guards": ["think_chosen:RETRY"], "max_occurrences": 1},
    {"from_state": "ERROR", "to_state": "SYNTHESIS", "trigger": "partial_salvage", "guards": ["think_chosen:PARTIAL_SYNTHESIS"]},
    {"from_state": "ERROR", "to_state": "HANDOFF", "trigger": "handoff_recovery", "guards": ["think_chosen:HANDOFF"]},
    {"from_state": "ERROR", "to_state": "TERMINATED", "trigger": "abort", "guards": ["think_chosen:ABORT"]},

    {"from_state": "HANDOFF", "to_state": "TERMINATED", "trigger": "handoff_written"}
  ]
}
```

---

## Appendix B: Delegation JSON Template

### Standard Sub-Agent Request

```json
{
  "delegation_id": "d7a3f1b2-4c5e-6789-abcd-ef0123456789",
  "requesting_teammate": "explorer",
  "sub_agent_type": "codebase-scout",
  "model": "haiku",
  "skills_to_load": [],
  "parallel_group": "pg-001",
  "priority": "blocking",
  "delegation_prompt": {
    "type": "exploration",
    "task": "Map the Pipeline class hierarchy and data flow in src/pipeline/",
    "known_context": {
      "file_coordinates": [
        {"path": "/workspace/src/pipeline/__init__.py", "lines": "1-30", "description": "Pipeline package entry point"}
      ],
      "findings": ["Pipeline uses a stage-based architecture with BaseStage abstract class"]
    },
    "exploration_scope": {
      "primary_targets": ["Class hierarchy", "Data flow between stages", "Public entry points"],
      "peer_scopes": ["Config module (peer agent)", "Stage implementations (peer agent)"],
      "extension_policy": "Only extend if a primary target depends on an external module not covered by peers."
    },
    "essential_output": [
      "Complete class hierarchy with inheritance relationships",
      "Data flow from input to output through stages",
      "All public methods with type signatures"
    ],
    "output_contract": {
      "path": "/workspace/.claude/context/pipeline-hierarchy.json",
      "format": "JSON context packet"
    },
    "return_schema": {
      "delegation_type": "exploration",
      "status": "completed|partial|failed",
      "findings": {},
      "essential_output_confidence": {}
    },
    "return_instruction": "Return a JSON object with delegation_type, status, findings, and essential_output_confidence fields."
  }
}
```

### General Sub-Agent Request

```json
{
  "delegation_id": "a1b2c3d4-5678-9abc-def0-123456789abc",
  "requesting_teammate": "coder",
  "sub_agent_type": "general",
  "model": "sonnet",
  "skills_to_load": ["context-packets", "quality-gate"],
  "parallel_group": null,
  "priority": "normal",
  "delegation_prompt": {
    "type": "guided",
    "task": "Analyze test coverage gaps for the validation module",
    "known_context": {
      "file_coordinates": [
        {"path": "/workspace/src/validation.py", "description": "Validation module under test"}
      ],
      "findings": ["Coverage report shows 62% — need to identify uncovered branches"]
    },
    "unknowns": [
      "Which specific functions have < 50% branch coverage?",
      "Are there untested error paths in the validation pipeline?"
    ],
    "output_contract": {
      "path": "/workspace/.claude/context/queries/validation-coverage.json",
      "format": "JSON query result"
    },
    "scope_boundary": {
      "do_not": ["Modify any source or test files — analysis only"],
      "tool_budget": 15
    },
    "return_instruction": "Return JSON with delegation_type: 'guided', status, unknowns_resolved, and decisions_made."
  }
}
```
