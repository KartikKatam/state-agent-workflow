# Agentic Workflow v2 — Complete Design Reference

## Project Location
- Repo: `~/personal/agentic_workflow/` (github.com/KartikKatam/agentic_workflow)
- Source .claude folder copied from: `/home/kartik/work/firefly/LPR-SingleDrone/.claude/` (K-LPR-Messy branch)
- Statusline: Updated `~/.claude/statusline.sh` with new 2-line Almond-Pine themed version from K-LPR-Messy

## Architecture Overview
Next-generation multi-agent TDD workflow system. Evolution of the LPR-SingleDrone `.claude/` workflow. Key improvements: increased autonomy, blind scenario testing, parallel coders with worktrees, hook-enforced state machines, programmatic tool calling, agent observability, deterministic procedures.

---

## Agents (8 total)

### 1. Orchestrator (main claude-code session) — Opus 4.6
- Central coordinator, agent lifecycle management, phase transitions
- Owns the system-level state machine
- Never writes code, never explores — only coordinates
- Generates phase reports, manages preferences file
- Communicates with user for approvals at phase boundaries only
- PreCompact hook saves: workflow status, design decisions, user changes, important queries (NOT agent termination history or log-retrievable data)

### 2. Strategist (teammate) — Opus 4.6
- Converts design documents → plain-text phased plan → JSON plan
- Grounds general designs in codebase context via explorer/researcher queries
- Three gates: plain-text review → user approval → JSON conversion (with validation scripts)
- Bidirectional linking: JSON fields contain `source_ref` pointing to exact lines in plain-text plan
- Includes `touched_functions` per task for parallel conflict detection
- Assigns per-task metadata: `priority`, `model_recommendation`, `requires_research`, `requires_exploration`, `exploration_queries`, `research_queries`
- Delegates extraction work to Haiku/Sonnet sub-agents
- PreCompact hook saves planning state; user can trigger handoff to fresh strategist to continue

### 3. Explorer (teammate) — Opus 4.6
- Codebase context generator — reads files, generates structured context packets
- Modes: full codebase overview, feature-specific context, on-demand query response (including per-task coder requests)
- Uses sub-agents (Haiku/Sonnet) for parallel directory exploration via PTC
- Opus synthesis of sub-agent results into context packets
- Scope: mono-repo focused, designed for cross-repo extensibility
- Direct peer messaging with any agent that needs context

### 4. Researcher (teammate) — Sonnet 4.6
- External information source — MCP (Context7), WebSearch
- Sub-agents do actual MCP/web calls; Sonnet synthesizes results
- Outputs to `$PROJECT_DIR/.claude/research/` with confidence scores
- All research is persistent and searchable — future queries search existing research first before dispatching new sub-agents
- Direct peer messaging with any agent

### 5. Coder (teammate, up to 4 parallel) — Opus 4.6 (or Sonnet for mechanical tasks)
- TDD implementation per task from phase task pool
- Each coder gets its own worktree + port allocation
- On spawn: reads task's `exploration_queries` and `research_queries` from plan, dispatches explorer/researcher for fresh context before starting implementation
- Reads JSON plan + bidirectional links for targeted context (via PTC)
- Pass A/B/C tests (unit, integration, system)
- RED phase failure → scrap-and-retry with handoff summary (NOT correction loop)
- Cannot modify test assertions during IMPLEMENTATION state (hook-enforced)
- Delegates extraction to sub-agents; queries explorer for multi-file context
- Autonomous — no mid-implementation approvals
- Task auditor reviews work BEFORE merge (see Auditor modes)

### 6. Tester (teammate) — Opus 4.6
- Spawns after strategist completes, runs in parallel with coders
- Works from design doc + plan + context packets (blind to implementation)
- Collaborates with user on scenario planning while coders implement
- Pass D: 4-tier eval scenarios (golden path, edge cases, adversarial, property-based)
- Implements own scenarios via Haiku sub-agents (separation of concerns — coders never see tester code)
- Sends bug descriptions to coders on failure (no test code leaked)
- After 2 deny cycles → auditor arbitrates
- Scenarios execute during PHASE_SCENARIO_EXECUTION (after coders merge)

### 7. Auditor (teammate) — Opus 4.6
- Four modes (one per invocation, loads mode-specific skill):
  1. **Task review**: Spawns after each coder claims task complete, BEFORE merge. Ephemeral adversarial agent that critiques implementation, forces coder to fix issues. No user-facing summary — purely internal quality enforcement. Coder cannot merge until task auditor approves.
  2. **Phase review**: After all tasks + Pass D testing. Comprehensive review, independent exploration, writes full report for user.
  3. **Hardening check**: Production readiness review.
  4. **Arbitration**: Tester-coder deadlock after 2 deny cycles.
- Persists within a phase for task reviews (reviews task 1, 2, 3... accumulating phase knowledge)
- New auditor instance mandatory at every phase boundary
- Override mechanism: can request elevated permissions with logged justification

### 8. Generalist (special, on-demand) — Opus 4.6, bypassPermissions
- Unrestricted "mad scientist" agent — not part of workflow state machine
- Dynamically loads skills from `.claude/skills/` based on task
- Full filesystem access, no validation hooks, maximum permissions
- Only agent that uses `bypassPermissions` mode
- User's personal tool for anything that doesn't fit the workflow

---

## Think Tool (all agents)

Replaces Sequential Thinking MCP. Always available, never state-gated.

### How It Works
- Extended thinking (automatic, pre-response) provides baseline reasoning quality
- Think tool (explicit tool call) provides mid-chain deliberation
- All Think tool usage is logged to `~/.claude/logs/decisions/{agent-id}.jsonl` via PostToolUse hook

### Structured Decision Output
Every mandatory think point produces:
```
THOUGHT: [what was being decided]
CONSIDERED: [options/factors weighed]
CHOSEN: [decision made + rationale]
```

### Mandatory Think Points

| Agent | Must Think Before |
|-------|-------------------|
| **Orchestrator** | Phase transitions, agent dispatch decisions, accepting/rejecting agent results, remediation vs arbitration |
| **Strategist** | Phase breakdown boundaries, task priority ordering, task dependency classification, touched_functions overlap, test planning completeness |
| **Coder** | Starting implementation (post-RED), scrap-and-retry decisions, marking task complete |
| **Tester** | Suggesting initial scenario set to user, classifying failure as bug vs test issue, escalation to auditor, scenario tier assignment |
| **Auditor** | Writing the review report, adherence rulings (plan vs implementation nuance), arbitration decisions, override requests |
| **Explorer** | Deciding exploration scope, allocating sub-agents to directories/files, synthesizing sub-agent results |
| **Researcher** | Allocating sub-agents to search queries/MCP calls, synthesizing sub-agent results, classifying research confidence |

### Discretionary Usage (encouraged)
- When encountering unexpected behavior
- When a tool call returned surprising results
- When about to make an irreversible decision
- When detecting a potential rationalization pattern

---

## Anti-Rationalization Hardening

Each agent prompt includes role-specific defenses against the #1 failure mode of agentic workflows.

### Three Defense Layers Per Agent

**Layer 1 — Rationalization Table**: Two-column table of observed excuses → reality checks, specific to the agent role. Populated through pressure testing during implementation.

**Layer 2 — Red Flags List**: Thought patterns that trigger immediate STOP:
```
"probably", "should work", "I think", "assuming", "likely", "it seems like"
"I'll just do a quick X while I'm here" (scope creep)
"This is too simple to test" (TDD bypass)
"I already know this" (assumption)
"Done!" without verification evidence
```

**Layer 3 — Foundational Principle**: "Violating the letter of the rules IS violating the spirit of the rules."

### HARD-GATE Pattern (second enforcement layer)
XML-tagged absolute constraints in agent prompts. Complement hook enforcement — hooks block tools, HARD-GATEs block behaviors:
```xml
<HARD-GATE>No production code without a failing test first</HARD-GATE>
<HARD-GATE>Must read actual code, never trust reports</HARD-GATE>
<HARD-GATE>No JSON conversion without user approval of plain-text plan</HARD-GATE>
```

---

## No-Assumptions Rule (all agents)

Multi-layer enforcement that agents must resolve uncertainty before proceeding.

### Think Tool Integration
At mandatory think points, agents categorize knowledge:
- **CONFIRMED**: In loaded context, verified — proceed
- **UNCERTAIN**: Needs clarification — MUST query before proceeding
- **UNKNOWN**: Needs research — MUST dispatch explorer/researcher

If UNCERTAIN or UNKNOWN items exist, agent cannot proceed to implementation.

### Query Routing
- Codebase questions → Explorer
- External docs/APIs → Researcher
- Intent/architecture/design → Orchestrator
- User preference/approval → User (via orchestrator)

### HARD-GATE
```xml
<HARD-GATE>
If you are uncertain about ANY of the following, you MUST query before proceeding:
- Intent or expected behavior of the task
- How existing code works in areas you're modifying
- API contracts, data formats, or protocols
- Whether your approach matches the plan
</HARD-GATE>
```

---

## Agent Lifecycle

### Ephemeral Agents (terminated after task/query)

| Agent | Lifecycle | Termination |
|-------|-----------|-------------|
| **Coder** | One task, one life. Fresh context per task. | Task complete + auditor task review passes + merge succeeds → terminated |
| **Explorer** | One query or related batch | Next query unrelated → terminated, fresh spawn |
| **Researcher** | One query or related batch | Same as explorer |
| **Task Auditor** | One coder task review | Review complete → terminated |

### Persistent Agents (survive across tasks within scope)

| Agent | Lifecycle | Termination |
|-------|-----------|-------------|
| **Tester** | Planning complete → end of phase | Phase complete (or context limit → handoff) |
| **Strategist** | Design loaded → plan complete | Plan complete (or context limit → handoff) |
| **Phase Auditor** | Phase start → phase review complete | Phase boundary (mandatory) |
| **Orchestrator** | Always | Session end |

### Context Pressure Handling
- **No compaction for any agent.** All agents prefer handoff over compaction.
- **Coders**: At context pressure → write handoff summary → terminate → fresh coder spawns. Same as scrap-and-retry mechanically.
- **Orchestrator**: PreCompact hook saves workflow state as safety net. Continues with compaction (user would need new session otherwise). User can manually trigger handoff.
- **Strategist**: PreCompact hook saves planning state as safety net. User can manually trigger handoff to fresh strategist.
- **Tester**: PreCompact hook saves scenario state as safety net. User can manually trigger handoff. Scenarios are in files, so handoff is lightweight.
- **Auditor**: New instance mandatory at phase boundary. Within phase, handoff if context limit hit.

### Handoff Protocol
1. Agent writes structured handoff to `$PROJECT_DIR/.claude/handoffs/{agent-id}.json`
2. Contents: current task state, key decisions (from decision log), files modified, test results, unresolved questions
3. Agent terminates
4. Orchestrator spawns fresh agent with handoff context

---

## Sub-Agent Delegation Model (ALL agents)
- All agents delegate extraction work (file reads, MCP calls, web searches, parsing) to Haiku/Sonnet sub-agents
- Parent agent (Opus or Sonnet) only sees filtered, structured results
- PTC used for processing sub-agent results — intermediate data stays in sandbox, only summaries enter context
- Pattern: extraction → Haiku/Sonnet, synthesis → parent model

---

## Programmatic Tool Calling (PTC) — Local Implementation

### Architecture
- Custom MCP server wrapping ipybox as sandbox layer
- Located at `~/.claude/mcp/ptc-server/`
- Added to Claude Code once: `claude mcp add local-ptc -- python server.py`
- Each agent session gets its own ipybox kernel (Docker-based)
- Role-scoped tools: different agents get different tool functions in their sandbox
- Token savings: 37-85% reduction (intermediate results stay in sandbox)
- All agents use PTC for data processing, exploration batching, result filtering

### How Sub-Agent Returns Work
1. Parent agent (e.g., Explorer/Opus) spawns Haiku sub-agent for file reading
2. Sub-agent writes Python code executed in ipybox kernel
3. Code calls MCP tools (read_file, search, etc.) — raw results land in kernel MEMORY, not in any agent's context
4. Code processes results in sandbox: filters, deduplicates, extracts patterns, builds summaries
5. Only the structured summary crosses back to the parent agent's context
6. Example: sub-agent reads 50 files (~100KB raw) → sandbox processes → returns 5KB structured summary

### Use Cases
- Sub-agents reading large codebases (files stay in sandbox)
- Processing log files, data files, large artifacts (parsed in sandbox, summary returned)
- Batch exploration (multiple directory reads aggregated in sandbox)
- Result filtering before parent synthesis

---

## Communication System (3 layers)

### Layer 1: Structured Files
- Context packets, plans, research, reports, audit reviews
- Written to `$PROJECT_DIR/.claude/` subdirectories with schema validation

### Layer 2: Inter-Agent Messaging
- Standalone protocol — model-agnostic JSONL format
- Global PostToolUse hook on SendMessage (configured ONCE in ~/.claude/settings.json, applies to ALL agents automatically)
- Message bus log: `~/.claude/logs/message-bus.jsonl`
- Direct peer-to-peer messaging (orchestrator is NOT a relay)
- State machine validates message content (not routing)
- Full message content logged (not just summaries)

### Layer 3: User Annotations (non-blocking feedback)
- Annotation files: `~/.claude/annotations/{agent-name}.jsonl`
- PostToolUse hook checks annotations AFTER EVERY TOOL CALL
- Injects via `additionalContext` — agent sees annotation immediately, no turn boundary wait
- Priority levels: critical (stop and address), normal (integrate), fyi (acknowledge)
- CLI: `./scripts/annotate.sh {agent} {priority} "message"`

---

## Permission Model

### Approach: `dontAsk` + Allow Rules + PreToolUse Hooks
All workflow agents (except generalist) spawn with `mode: "dontAsk"` and explicit allow rules. PreToolUse hooks retain full veto power. Only the generalist uses `bypassPermissions`.

### Three-Tier Permission System

| Tier | What | Mechanism | Examples |
|------|------|-----------|---------|
| **Auto-approved** | Safe operations in allow list | `dontAsk` mode allow rules | Read, Glob, Grep, Edit, Write, Bash(pytest*), Bash(ruff*), Bash(git status/diff/log) |
| **Ask user** | Potentially dangerous | PreToolUse hook returns `"permissionDecision": "ask"` | git push, file deletion, pip/uv install, network requests |
| **Hard blocked** | Clearly dangerous | PreToolUse hook `exit 2` | rm -rf /, DROP TABLE, chmod 777, curl\|bash, systemctl |

User CAN approve "ask" tier actions via the permission dialog. Hard-blocked actions require the generalist.

### State Machine Gating
Beyond permissions, the state machine gates Write/Edit by agent state:
- Coder in TEST_DESIGN → can write test files only
- Coder in IMPLEMENTATION → can write source files only (`src/**/*.py`) — test assertions hook-enforced
- Coder in QUALITY_GATE → can write source + test files (for gate-identified fixes, not assertion edits)

---

## State Machines

### System-Level State Machine
```
IDLE → DESIGN_LOADED → EXPLORING → CONTEXT_READY → STRATEGIZING →
PLAN_TEXT_REVIEW → PLAN_JSON_CONVERSION → PLAN_READY →
PHASE_ACTIVE → PHASE_IMPLEMENTATION → PHASE_TASKS_COMPLETE →
PHASE_SCENARIO_EXECUTION → PHASE_AUDIT → PHASE_REPORT →
PHASE_COMMITTED → (next phase or FINAL_AUDIT) → COMPLETE
```
With remediation loops: PHASE_REMEDIATION (max 2 cycles) → PHASE_ARBITRATION (auditor)
With failure paths: EXPLORING → DESIGN_LOADED (retry), FINAL_AUDIT → PHASE_ACTIVE (critical findings)
With rejection paths: PLAN_TEXT_REVIEW → STRATEGIZING, PHASE_REPORT → PHASE_REMEDIATION
Arbitration split: ruling_favors_tester → REMEDIATION, ruling_favors_coder → AUDIT

### Per-Agent State Machines

**Coder** (21 states, 3 terminals: TASK_COMPLETE, SCRAP_RETRY, HANDOFF):
```
SPAWNED → TASK_CLAIMED → WORKTREE_CREATED → CONTEXT_REQUESTED →
CONTEXT_LOADED → TEST_DESIGN → TESTS_WRITTEN → TDD_RED →
(RED_VERIFIED or RED_FAILED→max 2 retries→SCRAP_RETRY) →
IMPLEMENTATION → TDD_GREEN (max 5 loops, think required) →
INVARIANT_CHECK → QUALITY_GATE → TASK_REVIEW_REQUESTED →
(FIXES if auditor finds issues, ESCALATED if deadlock) →
MERGE → MERGE_RESOLVED → TASK_COMPLETE
Universal: any state → HANDOFF on context pressure
```

**Tester** (11 states, 2 terminals: PHASE_COMPLETE, HANDOFF):
```
SPAWNED → SCENARIO_PLANNING (revision loop max 3) → SCENARIOS_APPROVED →
SCENARIO_BUILDING → SCENARIO_VALIDATION (dry-run) → SCENARIOS_READY →
SCENARIO_EXECUTION → SCENARIO_REPORTING (split: pass/fail paths) → IDLE
Universal: any state → HANDOFF on context pressure
```

**Auditor (task review mode)** (6 states, 2 terminals: APPROVED, ESCALATED):
```
SPAWNED → CODE_REVIEW → CRITIQUE_SENT → FIXES_VERIFIED →
(APPROVED or MORE_FIXES max 3 or ESCALATED if deadlock)
```

**Auditor (phase review mode)** (7 states):
```
SPAWNED → CONTEXT_LOADING → ARTIFACT_REVIEW → INDEPENDENT_EXPLORATION →
RULING → REPORT_WRITING → COMPLETE
```

**Explorer** (11 states, 2 terminals: TERMINATED, HANDOFF):
```
SPAWNED → EXISTING_CONTEXT_CHECK → SCOPE_ANALYSIS → SUB_AGENT_DISPATCH →
SYNTHESIS → PACKET_VALIDATION → PACKET_WRITTEN → (IDLE or TERMINATED)
With ERROR state for sub-agent failures (retry max 1 or abort)
Universal: any state → HANDOFF on context pressure
```

**Researcher** (10 states, 2 terminals: TERMINATED, HANDOFF):
```
SPAWNED → EXISTING_RESEARCH_CHECK → QUERY_ANALYSIS → SUB_AGENT_DISPATCH →
ADDITIONAL_SEARCH (self-loop max 2) → SYNTHESIS → RESEARCH_WRITTEN → (IDLE or TERMINATED)
Universal: any state → HANDOFF on context pressure
```

**Strategist** (16 states, 2 terminals: PLAN_COMPLETE, HANDOFF):
```
SPAWNED → DESIGN_INGESTION → PHASE_BREAKDOWN → (user review) →
TASK_BREAKDOWN → (user review) → TASK_DETAILING → (user review) →
TEST_PLANNING → (user review) → PLAN_TEXT_COMPLETE → (user review) →
JSON_CONVERSION → JSON_VALIDATION (think on failure loop) → PLAN_COMPLETE
Universal: any state → HANDOFF on context pressure
```
Each step has: mandatory Think tool usage, validation script (where applicable), user review checkpoint at key boundaries.

### State Machine Enforcement
- Python script: `scripts/workflow_state.py` — validates transitions, checks guards, manages state file
- PreToolUse hooks consult state machine: blocks tools not allowed in current state
- Dynamic tool availability: agents keep full read access always, write/edit scoped by state
- Override mechanism: agent requests elevated permissions → orchestrator/user approves → logged
- Permissive fallback: unmatched tool calls are ALLOWED by default (only explicitly blocked actions blocked)
- `universal_transitions` array: transitions that apply from ANY non-terminal state (e.g., HANDOFF on context pressure)

### State Machine Schema Extensions (v1.1)

**`universal_transitions`**: Top-level array of transitions that apply from any non-terminal state. Avoids repeating handoff/abort transitions on every state. Enforcement script checks universal transitions alongside normal ones.

**`think_category`**: Categorical think prompts for `requires_think` transitions. Instead of per-transition custom prompts, transitions are tagged with a category that maps to a template prompt. Categories:
- `loop-back`: "What failed? Same root cause or new? Fixing the real problem or working around it?"
- `escalation`: "What has been tried? Why hasn't it worked? What does the next handler need?"
- `approval`: "Have I verified each requirement? What could I be missing? Am I rationalizing a pass?"
- `planning`: "What is my approach? What are the risks? What do I need that I don't have?"
- `judgment`: "What evidence supports each side? What is the most objective interpretation?"
Custom `think_prompt` on a transition overrides the category template. Enforcement: PreToolUse hook blocks the next tool call until Think is called, injecting the prompt via `additionalContext`.

**Assertion-edit guard** (hook-level, not state machine): PreToolUse hook on Edit for test files blocks if the diff touches assertion lines (`assert`, `assertEqual`, `pytest.raises`, `pytest.approx`, `pytest.warns`, `assertIn`, `assertRaises`). Applies in IMPLEMENTATION and FIXES states.

### TODO: Review max_occurrences Values
All `max_occurrences` values across state machines need a dedicated review pass to verify they are tuned correctly. Current values were set during initial design — some may be too high (allowing too many retries) or too low (forcing premature escalation). Review should consider: token cost per retry, likelihood of success on retry, and blast radius of getting stuck in a loop.

---

## Task-Coder-Auditor Flow (per task)

1. Orchestrator assigns task to coder (spawns fresh coder)
2. Coder reads task metadata: `exploration_queries`, `research_queries`
3. Coder dispatches explorer/researcher for fresh, task-specific context
4. Coder receives context, proceeds with TDD (test → fail → implement → pass)
5. Coder runs quality gate (synchronous final check)
6. Coder signals task complete
7. Orchestrator spawns task auditor (ephemeral, adversarial)
8. Task auditor reviews coder's implementation against plan — forces coder to fix any issues
9. Coder fixes until task auditor approves
10. Coder merges worktree to base branch
11. Coder resolves any merge conflicts (Think tool + git rerere)
12. If merge conflict unresolvable → orchestrator spawns auditor in arbitration mode + user
13. Coder terminates
14. Stop hook: lightweight check that merge succeeded (does NOT block handoffs)

---

## Stop Hooks

### Purpose
Lightweight prompt-type hooks that validate basic completion before agent termination. NOT a substitute for auditor review — just catches obviously incomplete work.

### Behavior
- Checks: quality gate run? tests pass? Think tool used at mandatory completion point?
- Does NOT block handoff scenarios (context pressure exits)
- 5-second evaluation, not a deep review

### Configuration
Goes in `.claude/settings.json` (project-level), applies to coder agents via matcher.

---

## SubagentStop Hooks

### Purpose
Validates sub-agent work before accepting results.

### Behavior
- Evaluates: did sub-agent produce complete, structured results?
- If incomplete → `"decision": "block"` → sub-agent continues working (same instance)
- Only if sub-agent hits hard limits (context window, repeated failures) → parent spawns replacement with incomplete work context

---

## Testing Architecture (4 passes)

### Coder Tests (implementation-aware)
- **Pass A**: Unit tests per task — invariants, golden examples, negative paths, log assertions
- **Pass B**: Integration tests per phase — cross-task, config sensitivity, API contracts
- **Pass C**: System tests (if applicable) — e2e pipeline, performance, resource validation

### Tester Scenarios (blind to implementation)
- **Pass D**: Eval scenarios from design doc
  - Tier 1: Golden path (5-10 scenarios)
  - Tier 2: Edge cases (10-20 scenarios)
  - Tier 3: Adversarial (5-15 scenarios)
  - Tier 4: Property-based validation (invariants, idempotency, monotonicity, conservation)
- Tester implements own scenarios via Haiku sub-agents (blind wall maintained)
- Tester reasons (Opus) about scenario design, Haiku generates test code, tester validates

### Test Data Strategy (preserved from v1)
- L1 Golden (deterministic fixtures)
- L2 Config-Adaptive (factory helpers from config)
- L3 Property-Based (Hypothesis random generation)
- Factory Boy patterns, pytest.approx for non-deterministic outputs

---

## Quality Gate

### Two-Phase Approach

**Phase 1 — Async feedback during implementation:**
Quality checks run async after state transitions. Results written as annotations. Agent sees issues and fixes during normal workflow.

**Phase 2 — Synchronous final gate (blocking):**
```bash
ruff format . --check     # Formatting
ruff check .              # Linting
pyright                   # Type checking
pytest --cov --cov-report=term  # Tests + coverage (informational, no threshold)
bandit -r src/ -q         # Security scan
vulture src/ --min-confidence 80  # Dead code (flag only, no auto-delete)
pip-audit                 # Dependency vulnerabilities
```
Must pass before TASK_COMPLETE transition. Vulture and coverage are informational.

---

## Worktree Strategy
- Each coder gets isolated worktree + dynamically allocated port range
- Port allocation: `scripts/allocate-ports.sh` — dynamic from range 30000-39999, checks `ss -tlnp` for conflicts, writes to `~/.claude/state/ports/{agent-id}.json`, released on termination
- Base branch: configurable at workflow init (`--branch feature/my-feature`), NOT hardcoded to main
- All worktrees branch from base, all merges target base
- Merge ordering: generally FIFO (first complete, first merged). Race condition tiebreaker: higher priority (from strategist) merges first
- Merge script: `scripts/merge-worktree.sh` — backup ref, rebase, pre-merge validation (quality gate), fast-forward merge, cleanup
- Git rerere enabled globally — resolutions shared across worktrees
- Function-level conflict detection: tree-sitter AST analysis via `scripts/validate_function_overlap.py`
- Max 4 parallel coders
- Merge conflict resolution: coder attempts (Think tool + rerere) → if stuck, orchestrator spawns auditor arbitration + user
- Single shared simulation container (Isaac Sim) if needed — coordination lock, not per-agent

---

## Agent Identity & State

### ID Format
`{role}-{phase}-{task}-{short_uuid}` (e.g., `coder-p1-t3-a7f2`, `auditor-p1-review-b3e1`)

### Assignment
1. Orchestrator generates ID at dispatch time
2. ID passed in agent spawn prompt AND written to state file
3. Agent reads its own ID from spawn context
4. All logs, messages, state transitions include this ID

### Agent State File (`~/.claude/state/agents/{agent-id}.json`)
```json
{
  "id": "coder-p1-t3-a7f2",
  "role": "coder",
  "phase": "p1",
  "task": "t3",
  "model": "opus-4-6",
  "spawned_at": "2026-02-25T10:00:00Z",
  "status": "active",
  "context_usage_pct": 45,
  "input_tokens": 150000,
  "output_tokens": 35000,
  "worktree": "/path/to/worktree",
  "base_branch": "feature/my-feature"
}
```
- `context_usage_pct` updated by PostToolUse hook after every tool call
- Readable by agent (knows its own state) and orchestrator (monitors fleet)
- Fixes v1 issues: v1 used session_id (unmappable to agents), no central registry, agents couldn't read own metrics

---

## Modular Rules (.claude/rules/)
Glob-targeted rules for domain-specific enforcement. Keeps CLAUDE.md lean, increases rule compliance.

Planned rules:
- `.claude/rules/testing.md` (globs: `tests/**/*.py`) — test-specific rules
- `.claude/rules/workflow-state.md` (globs: `scripts/workflow_state.py`) — state machine rules
- `.claude/rules/hooks.md` (globs: `.claude/hooks/**`) — hook development rules
- `.claude/rules/plans.md` (globs: `.claude/plans/**`) — plan format rules

General/architectural rules stay in CLAUDE.md.

---

## Path Handling — Portable Workflow

### Design Principle
The workflow system is a standalone GitHub repo. A setup script initializes it for any project directory on any device.

### Setup
```bash
git clone github.com/KartikKatam/agentic_workflow
cd agentic_workflow && ./scripts/init-workflow.sh /path/to/project --branch feature/my-feature
```

### Directory Layout

| Location | Contains | Purpose |
|----------|----------|---------|
| `~/personal/agentic_workflow/` | Workflow system code (scripts, hooks, schemas, agent specs) | The "engine" — version controlled, cloneable |
| `$PROJECT_DIR/.claude/` | Project-specific artifacts (context, plans, research, designs, logs, handoffs) | Per-project workflow state |
| `~/.claude/settings.json` | Global hooks (messaging, annotations, state machine) | Applied to all projects |
| `~/.claude/logs/` | System-wide logs (message bus, state transitions) | Cross-project observability |
| `~/.claude/state/` | System-wide state (preferences, agent registry, port allocations) | Cross-project coordination |

### Cross-Device
- Clone the workflow repo + run init script on any device with Claude Code
- Device-specific config (paths, ports) lives in local files (not committed)
- Project-specific artifacts live in the project's `.claude/`

---

## Validation Scripts
- `validate_design_coverage.py` — design doc → plan semantic coverage (nomic embeddings)
- `validate_plan_conversion.py` — plain-text → JSON bidirectional diff
- `validate_function_overlap.py` — tree-sitter task parallelization safety
- `validate_merge_readiness.py` — pre-merge quality check
- `validate_scenario_coverage.py` — scenarios vs design requirements
- `validate_phase_completion.py` — phase gate checklist

---

## Design Doc Conversion
- Script: `scripts/convert_design.py input.md --output design.md --report coverage.md`
- Structural parsing: deterministic Python regex (no API needed)
- Semantic mapping: handled by strategist's Sonnet sub-agents via PTC (uses MAX subscription, no separate API calls)
- Original text quoted verbatim — never paraphrased
- Coverage report flags unmapped content

---

## Embedding Model
- `nomic-ai/nomic-embed-text-v1.5` — 768 dim, 550MB, ~10ms/embedding on GPU
- CPU fallback: ~100ms/embedding (degraded but functional)
- Used for: validation scripts (semantic similarity), message search (embedding retrieval), design coverage checking
- Matryoshka support: 768d for quality, 256d for speed

---

## Logging and Observability

### Enforced Logging
- State transitions: `~/.claude/logs/state-transitions.jsonl` — mandatory at every transition
- Decision logs: `~/.claude/logs/decisions/{agent-id}.jsonl` — all Think tool outputs + enforced at certain state transitions
- Message bus: `~/.claude/logs/message-bus.jsonl` — all inter-agent messages
- State snapshots: `~/.claude/logs/snapshots/{agent}-{state}-{timestamp}.json` — full agent state at each transition

### Token/Cost Tracking
- Per-agent: tracked in agent state file, written on termination
- Per-phase: aggregated in phase report (total input/output tokens, cost by agent)
- Session-wide: aggregate across all agents

### Agent Observability (layered)
- V1 (build now): Hook-based — tool calls, state transitions, decisions, messages, snapshots
- V2 (later): Transcript analysis — parse conversation JSONL for reasoning traces
- V3 (later): Extended thinking capture via PTC/API, agent graph visualization, cost dashboards

### Decision Propagation
- Preferences organized by agent role in `~/.claude/state/preferences.json`
- `global` preferences apply to all agents
- `by_role.{role}` preferences apply to specific agent types
- SessionStart hook loads global + role-specific preferences
- Active agents receive new preferences via annotation system

---

## Phase Reports (automated + auditor exploration)
- Generated by `scripts/generate-phase-report.py` — collects from all logs automatically
- Includes: phase goals, tasks completed, EVERY test with purpose/check method/result, quality gate results, auditor phase review (enforced template), scenario results, decisions made, token/cost summary
- Auditor also does independent exploration beyond automated report
- No mid-implementation approvals — only per-phase reports

---

## TUI Viewers (2 viewers)

### Message Viewer
- Python Textual, real-time via file watcher, persistent across sessions
- Columns: time, from, to, type, state, task, summary
- Detail pane shows full message content
- Hybrid search: FTS5 keywords + nomic embedding similarity
- Session scoping: current session / previous sessions / all sessions
- Features: agent focus mode, diff viewer for referenced files, bookmarks, export (JSONL/MD/CSV)
- Vim-style navigation, live pulse bar, cost ticker, token sparklines
- Model-agnostic: any system that writes the JSONL format works

### State Transition Viewer
- Same persistence and session browsing as message viewer
- Columns: agent, time, from state, to state, duration
- Clickable transitions show full state snapshot
- Filterable by agent, phase, state
- No search (not useful for transitions)
- Export to JSONL
- Old sessions viewable from archived logs

---

## Skills System (overhaul deferred)
- Will be completely overhauled to utilize hooks, scripts, and skill-writer-skill
- Each skill: SKILL.md (concise, loaded into context) + reference.md (read on-demand via PTC) + examples/ + schemas/
- Base skills loaded at startup, dynamic skills loaded on demand
- Skill loading tracked in decision log

---

## Key Design Principles
- Token efficiency: PTC for all agents, minimal spawn context, sub-agent delegation
- Determinism: Hook-enforced state machines, validation scripts, quality gates
- Autonomy: No mid-implementation approvals, annotation-based user feedback, `dontAsk` permissions
- Observability: Mandatory logging at every state transition and decision point
- Separation of concerns: Coders test implementation, tester tests requirements (blind)
- Parallelization: Up to 4 coders in worktrees, tester runs in parallel track
- Information preservation: Bidirectional linking, semantic diffing, verbatim quoting
- Model efficiency: Opus for reasoning/synthesis, Sonnet for research, Haiku for extraction
- Portability: Standalone workflow repo, cross-device setup, model-agnostic TUI
- Anti-rationalization: Defense tables, red flags, HARD-GATEs in every agent prompt
- No assumptions: Agents must resolve uncertainty via queries, never proceed on assumptions
- Fresh context: Coders get clean context per task, no compaction for any agent
