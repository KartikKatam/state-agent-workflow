# V2 Agent Architecture — Source of Truth

## Purpose

This document defines every agent teammate and sub-agent in the V2 agentic workflow. It is the authoritative reference for agent types, sub-agent types, dispatch rules, architectural invariants, inheritance design, skill assignments, and communication protocols.

All agent definitions, sub-agent definitions, delegation skills, hooks, and state machines MUST conform to this document. When conflicts arise between this document and other files, this document wins.

## Document History

- **Created:** 2026-03-15
- **Context:** Design session for Task #1 of V2 Sub-Agent Restructuring
- **Supersedes:** Section 1 of `v2-subagent-restructuring.md` (role assignments and sub-agent types tables)
- **Research inputs:** GSD (get-shit-done), VoltAgent/awesome-claude-code-subagents, 12+ community workflow repos, `subagent-architecture-research.md`

---

## 1. Two-Tier Architecture

The workflow operates on two tiers of agents:

**Tier 1 — Teammates (Directors)**
Full Claude Code instances spawned via Agent Teams. Persistent within a session. They reason about strategy, delegate mechanical work to sub-agents, synthesize results, and communicate with each other. Teammates do minor work directly (< 3 tool calls, no judgment required) but delegate anything substantial.

**Tier 2 — Sub-Agents (Workers)**
Ephemeral agents spawned via the Task tool. Fresh context per invocation. They execute bounded tasks, return structured results, and terminate. Sub-agents cannot spawn other sub-agents (Task tool is stripped). Sub-agents inherit 1M context windows from their parent.

**Orchestrator (Tier 0)**
The main Claude Code session. Not a teammate — it IS the session. Creates the Agent Team, spawns teammates, mediates sub-agent requests, manages workflow state, and interfaces with the user.

### Key Principles

1. **Teammates are directors, not doers.** They load initial context into PTC, reason about delegation strategy, spawn parallel sub-agents for maximum depth and efficiency, synthesize results, and message other teammates. They do NOT perform substantial mechanical work themselves.

2. **Sub-agents are versatile workers, not pigeonholes.** Each sub-agent type is capable within its domain. Specialization comes from the delegation prompt, not from having many narrow agent definitions.

3. **Only the orchestrator spawns agents.** Teammates write delegation JSON to disk, send an `info_request` to the orchestrator, and the orchestrator spawns on their behalf. Results are routed back via `info_ready`.

4. **PTC is universal.** Every agent (teammate and sub-agent) has PTC MCP access via the `ptc-sandbox` skill. When PTC is unavailable, agents fall back to normal tool calls (Read, Grep, Bash).

5. **Strong foundation, pluggable gaps.** The agent roster is designed for extension. Adding a new sub-agent type requires: one agent definition file, one route entry in the delegation skill, one validation case in SubagentStop hook.

---

## 2. Architectural Invariants

These are first-class rules enforced by hooks, state machine guards, and agent definitions. Violation of any invariant is a system error.

### INV-1: Test/Code Isolation

**Any agent that has READ both test code and implementation code MUST NOT EDIT either.**

- Agents that read both sides (audit-checker, plan-checker) can only observe and report.
- Agents that write source code (implementer, debugger, optimizer) MUST NOT read test code. They receive test RESULTS (pytest output, error traces, failure descriptions) but never the test source.
- Agents that write test code (test-writer, scenario-writer) MUST NOT read implementation source. They receive behavioral specifications, plan chunks, and design documents but never the implementation.
- **Purpose:** Prevents test overfitting — where tests are written to pass the current implementation rather than to verify the design intent.
- **Enforcement:** PreToolUse hooks check agent type against file globs. State machine `write_globs` and `read_globs_exclude` restrict access per state.

### INV-2: Domain Separation for Cross-Domain Work

**If a teammate needs work done outside its domain, it MUST message the specialist teammate. It MUST NOT dispatch another teammate's sub-agent types.**

- Coder needs codebase context → messages Explorer (not dispatching codebase-scout)
- Auditor needs API research → messages Researcher (not dispatching research-scout)
- Tester needs to understand module interfaces → messages Explorer
- **Purpose:** Prevents generalist drift. The specialist teammate knows how to partition, prompt, and verify its sub-agent types. A non-specialist dispatching unfamiliar sub-agents produces worse results.
- **Exception:** Shared sub-agents (debugger, test-writer) have multiple authorized dispatchers. See Section 5 for the authorized dispatcher table.
- **Enforcement:** Delegation skill route files only teach teammates about their authorized sub-agents. SubagentStart hook validates that the requesting teammate is an authorized dispatcher.

### INV-3: Controller Never Implements

**The orchestrator and teammate directors never write production code or test code directly for substantial tasks.**

- Teammates do minor work directly when it requires < 3 tool calls and no judgment.
- Anything requiring judgment, multiple file edits, or quality verification → sub-agent delegation.
- **Purpose:** Keeps director context windows lean and focused on strategy/coordination. Heavy work stays in sub-agent contexts where it doesn't accumulate.
- **Enforcement:** Behavioral — enforced via agent definitions and state machine states. Not mechanically blocked (teammates need Write/Edit for synthesis outputs, context packets, reports).

### INV-4: Adversarial Audit Independence

**Audit-checker sub-agents MUST be adversarial, granular, and critical about code quality, plan adherence, and style conformance.**

- The audit-checker's job is to find problems, not to approve. It checks:
  1. Implementation coherence with the original design document
  2. Test coverage of required behaviors grounded in the design
  3. Style and convention adherence to project specifications
  4. Code quality, error handling completeness, edge case coverage
- The audit-checker has NO Write/Edit tools. It cannot fix what it finds — only report.
- **Purpose:** Prevents quality drift where coding agents gradually deviate from project specifications. The audit function is the design plan's enforcement mechanism.
- **Enforcement:** Tool restriction in agent definition (no Write, no Edit). SubagentStop hook validates structured verdict output.

### INV-5: Structured Returns

**Every sub-agent MUST return structured JSON with `delegation_type` and `status` fields.**

- Return format is embedded in each sub-agent's agent definition body (not a universal skill).
- SubagentStop hook validates return structure before allowing termination.
- If validation fails, the sub-agent gets one retry. After two failures, the parent works with whatever was returned.
- **Purpose:** Enables mechanical verification of sub-agent output. Parents can process returns programmatically without parsing freeform text.
- **Enforcement:** SubagentStop hook (`hooks/subagent_stop.py`).

### INV-6: Escalation Tiers

**Structural discrepancies discovered during audit or arbitration follow a tiered escalation model.**

| Severity | Example | Action | User Approval |
|----------|---------|--------|---------------|
| Minor | Style issue, missing docstring | Auditor messages Coder with fix instructions | No |
| Moderate | Function/module rewrite needed | Auditor messages Coder with fix instructions | No |
| Major | Task-level rewrite, architectural change | Auditor escalates to user | Yes |
| Critical | Design plan contradiction, impossible requirement | Auditor escalates to user immediately | Yes — may scrap task |

- **Purpose:** User is not bottlenecked on trivial fixes but is always consulted on decisions that could invalidate downstream work.
- **User interface:** User interacts directly in the Auditor's tmux pane for audit discussions.

---

## 3. Teammate Definitions

### 3.1 Orchestrator (Tier 0 — Main Session)

| Property | Value |
|----------|-------|
| **Type** | Main Claude Code session (behavioral mode) |
| **Model** | Opus |
| **Persistence** | Across full workflow, /clear + handoff between phases |
| **PTC** | Yes |
| **Spawns** | All teammate types, all sub-agent types (sole spawner) |
| **Key Skills** | ptc-sandbox, workflow-orchestration, sub-agent-delegation (all routes) |

**Behavior:**
- Creates Agent Team, spawns teammates based on workflow phase
- Mediates sub-agent spawn requests from teammates
- Routes sub-agent results back to requesting teammates
- Manages workflow state machine transitions
- Interfaces with user for decisions, approvals, and coordination
- Uses Think MCP for mandatory decision points (exploration strategy, wave decomposition, phase transitions)

### 3.2 Explorer

| Property | Value |
|----------|-------|
| **Type** | Teammate (director) |
| **Model** | Sonnet |
| **Persistence** | Persistent across session, /clear at context pressure |
| **PTC** | Yes (persistent container) |
| **Dispatches** | codebase-scout |
| **Cross-Domain** | Messages Researcher for API/library docs |

**Behavior:**
1. On spawn: loads project/directory structure and key details into PTC persistent container
2. Receives exploration queries from other teammates or orchestrator
3. Reasons about partitioning strategy — how many parallel codebase-scouts, what partition each covers, what depth is needed
4. Requests orchestrator to spawn N parallel codebase-scout sub-agents
5. Collects results → synthesizes in PTC → writes structured context packet
6. Messages query source: "context packet at {path}, go read it"
7. Does NOT explore the codebase itself beyond initial setup — sub-agents do all mechanical exploration

**Skills:** ptc-sandbox, context-packets, sub-agent-delegation/explorer-routes, result-synthesis/exploration, codebase-exploration

### 3.3 Researcher

| Property | Value |
|----------|-------|
| **Type** | Teammate (director) |
| **Model** | Sonnet |
| **Persistence** | Persistent across session, /clear at context pressure |
| **PTC** | Yes (persistent container) |
| **Dispatches** | research-scout |
| **Cross-Domain** | Messages Explorer for codebase context |

**Behavior:**
1. On spawn: loads existing research index into PTC
2. Receives research requests from other teammates or orchestrator
3. Reasons about research avenues — how many parallel research-scouts, what avenue each covers, what sources to prioritize
4. Requests orchestrator to spawn N parallel research-scout sub-agents
5. Collects results → synthesizes in PTC → writes structured research file with citations
6. Messages query source with research file path
7. Does NOT perform research itself — sub-agents do all mechanical research

**Skills:** ptc-sandbox, sub-agent-delegation/researcher-routes, result-synthesis/research, research-methodology

### 3.4 Planner

| Property | Value |
|----------|-------|
| **Type** | Teammate (director) |
| **Model** | Opus |
| **Persistence** | Per-feature (terminates after plan is approved) |
| **PTC** | Yes |
| **Dispatches** | plan-checker |
| **Cross-Domain** | Messages Explorer for context, Researcher for technical feasibility |

**Behavior:**
1. Receives design document and codebase context
2. Decomposes feature into phases → tasks → waves with dependency graphs
3. Dispatches plan-checker sub-agent for pre-execution verification
4. Receives verification report → revises plan if needed → loops until checker passes
5. Presents plan to user for approval
6. Uses Think MCP for chunk boundary decisions and wave grouping

**Skills:** ptc-sandbox, implementation-plans, test-architecture, phase-planning, multi-perspective-analysis, sub-agent-delegation/planner-routes

### 3.5 Coder

| Property | Value |
|----------|-------|
| **Type** | Teammate (director) |
| **Model** | Opus |
| **Persistence** | Persistent across ALL phases, /clear between phases |
| **PTC** | Yes (persistent container) |
| **Dispatches** | implementer, test-writer, debugger, optimizer |
| **Cross-Domain** | Messages Explorer for context, Auditor for review requests |

**Behavior:**
1. Receives task assignment from orchestrator
2. Reasons about TDD strategy: test approach, file targets, implementation approach
3. Dispatches test-writer → reviews test output (reads test results, not test code, during implementation phase)
4. Dispatches implementer → reviews implementation output
5. For simple fixes: dispatches implementer with targeted fix prompt
6. For complex/mysterious failures: dispatches debugger
7. Post-implementation: dispatches optimizer (optional, in ephemeral worktree)
8. Requests audit from orchestrator when satisfied with implementation
9. Uses Think MCP for delegation decisions, test/implementation review, audit response

**Skills:** ptc-sandbox, sub-agent-delegation/coder-routes, result-synthesis/implementation, task-execution, plan-adherence

### 3.6 Tester

| Property | Value |
|----------|-------|
| **Type** | Teammate (director) |
| **Model** | Opus |
| **Persistence** | Per-phase |
| **PTC** | Yes (persistent container) |
| **Dispatches** | scenario-writer, test-writer |
| **Cross-Domain** | Messages Explorer for interface info, Coder for implementation fixes |

**Behavior:**
1. Loads design doc + plan → designs eval scenarios with user (this reasoning is the Tester's direct work — not delegated)
2. After user approval: dispatches scenario-writer for scenario test code
3. Validates scenarios (dry-run) → dispatches test-writer if broken
4. Executes scenarios against merged code
5. Reports results → sends failure info to Coder or escalates to Auditor
6. Blind to implementation — never reads source code, only public API specs and design docs

**Skills:** ptc-sandbox, sub-agent-delegation/tester-routes, test-architecture, scenario-testing

### 3.7 Auditor

| Property | Value |
|----------|-------|
| **Type** | Teammate (director) |
| **Model** | Opus |
| **Persistence** | Persistent across full session |
| **PTC** | Yes (persistent container) |
| **Dispatches** | audit-checker, debugger |
| **Cross-Domain** | Messages Coder for code fixes, Tester for test fixes, Explorer for codebase context |

**Behavior — Audit Mode:**
1. Receives audit request (task-level or phase-level)
2. For phase audit: dispatches N parallel audit-checker sub-agents (one per task)
3. For task audit: dispatches single audit-checker sub-agent
4. Receives adversarial audit report(s) → reasons about severity and impact
5. Minor/moderate issues → messages Coder or Tester with specific fix instructions (file:line references)
6. Major/critical issues → escalates to user in tmux pane with full audit findings
7. The audit-checker follows goal-backward verification: "what must be TRUE for the design to be satisfied?" not "did tasks complete?"

**Behavior — Arbitration Mode:**
1. Triggered when Coder and Tester are stuck in a disagreement loop (e.g., "tests are wrong" vs "code is wrong")
2. Dispatches audit-checker to verify plan adherence of the disputed area
3. Performs root-cause analysis from fresh context (not biased by code-writing or test-writing)
4. Focus is: "what is the actual problem and what solves it?" — not adversarial blame assignment
5. Reads both sub-agent reports, determines which side needs to change and why
6. Messages both Coder and Tester with the solution
7. Function/module-level fixes → autonomous. Task-level rewrites → user approval required.

**Behavior — Ad-Hoc Audit (User-Initiated):**
1. User can request audits at any point during the workflow via the Auditor's tmux pane
2. Auditor dispatches audit-checker or debugger as appropriate
3. Reports findings directly to user
4. This persistent availability is why the Auditor stays alive across the full session

**Skills:** ptc-sandbox, sub-agent-delegation/auditor-routes, result-synthesis/audit, code-review, plan-adherence

---

## 4. Sub-Agent Definitions

### 4.1 Inheritance Model

Sub-agents use a layered inheritance model to minimize duplication and enforce consistency:

```
Level 1: base-agent          (universal — all 9 sub-agents)
Level 2: base-code-agent     (code-writing — 5 sub-agents)
Level 3: specific definition  (per sub-agent)
```

Inheritance is implemented via the `skills:` field in agent definitions. Each sub-agent lists its inheritance chain as skills, loaded bottom-up:

```yaml
# Example: implementer.md
skills:
  - base-agent          # Level 1
  - base-code-agent     # Level 2
  - plan-adherence      # Level 3 specific
  - quality-gate        # Level 3 specific
  - ptc-sandbox         # Universal
```

#### Level 1: base-agent

Loaded by ALL 9 sub-agent types. Contains:

- **PTC access protocol:** How to use `ptc_execute`, namespace management, print discipline ("process in container, print JSON summary"), fallback to normal tools when PTC is unavailable
- **Return protocol:** Every sub-agent must return structured JSON with `delegation_type` and `status` fields. Return format details are in the specific agent's body (Level 3), but the requirement and structure are here
- **Test/code isolation invariant (INV-1):** The universal rule — agents that read both sides don't edit either. Each agent's specific read/write permissions are in Level 3, but the principle is here
- **Logging rules:** What to log, where, format requirements
- **Schema enforcement:** Output must validate against the expected schema
- **Common tool usage:** Absolute paths only, output contract mandatory, scope boundaries respected
- **Escalation protocol:** How to report NEEDS_CONTINUATION if context pressure approaches limit (write partial work to disk, return with status and handoff path)
- **Ephemeral state file:** At the end of each turn, write progress to `.claude/temp/sub-agent-state-{delegation_id}.json` (files modified, tests written/passing, decisions made so far, current work). This is the crash-recovery path — read by the dispatching teammate only if the sub-agent didn't return cleanly (crash, timeout, context pressure). Deleted when the task merges (cleanup alongside worktree removal). Together with the return protocol (clean exit) this covers both termination paths.
- **Cold-start reliability:** Sub-agent reliability does NOT depend on resume. Cold-start is the primary path. A fresh sub-agent gets: original delegation prompt + previous sub-agent's return (`decisions_made`, `carry_forward`, `files_modified`, `quality_gate_result`). Resume is a fast-path optimization when available, not a requirement.

#### Level 2: base-code-agent

Loaded by the 5 code-writing sub-agents (implementer, test-writer, scenario-writer, debugger, optimizer). Contains:

- **Quality gate awareness:** How to run `./scripts/gate.sh`, interpret results, fix auto-fixable issues
- **Write permission model:** Code-writing agents have Write/Edit tools; how to use them responsibly
- **Worktree context:** How to work within a git worktree, what branch to commit to, how to handle worktree-specific paths
- **Plan reference protocol:** How to read and reference the implementation plan for the current task
- **Iteration protocol:** "Implement until tests pass, max N iterations" — how to handle the implement-test-fix loop
- **PTC for validation:** Using PTC to run quality checks, parse test output, validate without context pollution

#### Level 3: Specific Agent Definitions

Each sub-agent's `.md` file contains only what is unique to that sub-agent type:
- Role-specific system prompt and behavioral instructions
- Tool restrictions (which tools are available)
- Read/write permissions (which files/globs are accessible)
- Return format specific to this agent type
- Skills unique to this agent type
- Any agent-specific rules or constraints

### 4.2 Sub-Agent Catalog

---

#### codebase-scout

**Identity:** General-purpose codebase exploration agent. Reads files, analyzes structure, traces dependencies, extracts patterns, maps module relationships. The Explorer teammate provides the partition and instructions — the scout does the mechanical exploration.

| Property | Value |
|----------|-------|
| **Model** | Sonnet or Haiku (teammate specifies per dispatch) |
| **Tools** | Read, Grep, Glob, Bash, PTC |
| **Authorized Dispatchers** | Explorer |
| **Reads** | Codebase files (any) |
| **Writes** | Context packet files only (`.claude/context/**`) |
| **Worktree** | No |

**Skills:** base-agent, context-packets, ptc-sandbox

**Return format:**
```json
{
  "delegation_type": "exploration",
  "status": "completed|partial|failed",
  "findings": { ... },
  "essential_output_confidence": { "<target>": {"level": "high|medium|low", "reason": "..."} },
  "scope_extensions": [],
  "cross_scope_findings": []
}
```

**Dispatch patterns:**
- Single scout for focused queries ("trace the dependency graph of Pipeline")
- Parallel scouts with partitions for broad queries ("map the full src/ directory" → 3 scouts, each covering a subdirectory)
- Haiku for simple extraction ("list all public functions in auth.py"), Sonnet for analysis ("analyze the architectural patterns in the processing module")

---

#### research-scout

**Identity:** General-purpose research agent. Searches the web, reads documentation, extracts API signatures, compares libraries, gathers best practices. The Researcher teammate provides the research avenue and source directives — the scout does the mechanical research.

| Property | Value |
|----------|-------|
| **Model** | Sonnet or Haiku (teammate specifies per dispatch) |
| **Tools** | Read, Grep, WebSearch, WebFetch, PTC |
| **Authorized Dispatchers** | Researcher |
| **Reads** | Web sources, documentation, local reference files |
| **Writes** | Research files only (`.claude/research/**`) |
| **Worktree** | No |

**Skills:** base-agent, ptc-sandbox

**Return format:**
```json
{
  "delegation_type": "research",
  "status": "completed|partial|failed",
  "findings": { ... },
  "essential_output_confidence": { "<target>": {"level": "high|medium|low", "reason": "..."} },
  "citations": [{"claim": "...", "url": "...", "source_name": "...", "retrieved": "..."}],
  "gaps": []
}
```

**Dispatch patterns:**
- Single scout for focused research ("find the OpenCV 4.8 ArUco detection API")
- Parallel scouts with avenues for broad research ("research state-of-the-art object tracking" → one scout per approach/library)
- Haiku for documentation extraction ("extract the function signature from the docs"), Sonnet for comparative analysis ("compare ArUco vs AprilTag for indoor use")

---

#### implementer

**Identity:** Production code writer. Implements plan chunks, fixes code from audit findings, and writes source code that adheres to the plan, project conventions, and quality standards. Focuses on plan adherence, code optimization, error handling, and style conformance.

| Property | Value |
|----------|-------|
| **Model** | Sonnet |
| **Tools** | Read, Write, Edit, Bash, Glob, Grep, PTC |
| **Authorized Dispatchers** | Coder |
| **Reads** | Source code, plan chunks, error output, design docs. **NEVER test code.** |
| **Writes** | Source code files (per task's `source_globs`) |
| **Worktree** | Yes — task worktree |

**Skills:** base-agent, base-code-agent, plan-adherence, quality-gate, ptc-sandbox

**Return format:**
```json
{
  "delegation_type": "implementation",
  "status": "completed|partial|failed",
  "files_modified": ["path/to/file.py"],
  "quality_gate_result": {"format": "pass|fail", "lint": "pass|fail", "typecheck": "pass|fail", "tests": "pass|fail"},
  "decisions_made": [{"decision": "...", "reason": "..."}],
  "carry_forward": []
}
```

**INV-1 enforcement:** The implementer receives test RESULTS (pytest output showing which tests fail and with what errors) but never reads test files. It implements based on the plan chunk, error descriptions, and behavioral specifications. PreToolUse hook blocks Read/Grep on test file globs.

**Used for both fresh implementation and targeted fixes.** The Coder teammate differentiates via the delegation prompt:
- Fresh implementation: full plan chunk context, success criteria, file coordinates
- Targeted fix: specific audit finding with file:line references, expected behavior

---

#### test-writer

**Identity:** Test suite designer and implementer. Writes unit tests, integration tests, and fixes broken tests. Focuses on comprehensive coverage, edge case identification, and test quality. Always has test architecture knowledge loaded.

| Property | Value |
|----------|-------|
| **Model** | Sonnet |
| **Tools** | Read, Write, Edit, Bash, Glob, Grep, PTC |
| **Authorized Dispatchers** | Coder, Tester |
| **Reads** | Test files, plan chunks, behavioral specs, design docs. **NEVER implementation source.** |
| **Writes** | Test files (per task's `test_globs`) |
| **Worktree** | Yes — task worktree |

**Skills:** base-agent, base-code-agent, tdd-workflow, test-architecture, ptc-sandbox

**Return format:**
```json
{
  "delegation_type": "test_writing",
  "status": "completed|partial|failed",
  "tests_written": [{"file": "...", "test_name": "...", "description": "..."}],
  "red_verification": {"all_tests_fail": true, "failure_types": ["AssertionError", "ImportError"]},
  "decisions_made": [{"decision": "...", "reason": "..."}],
  "carry_forward": []
}
```

**INV-1 enforcement:** The test-writer receives behavioral specifications and plan chunks describing what the code SHOULD do, but never reads the implementation source. Tests are written against the design intent, not the current implementation. PreToolUse hook blocks Read/Grep on source file globs.

**When dispatched by Coder:** TDD red phase — writes tests that define expected behavior, verifies they fail (red), and terminates. A fresh implementer is then spawned with clean context.

**When dispatched by Tester:** Test fixes — repairs broken test infrastructure, import errors, or collection failures in existing test suites.

---

#### scenario-writer

**Identity:** Scenario and eval test designer. Writes Pass D evaluation scenarios that test user-facing behaviors across tasks. Completely blind to implementation — tests behavioral contracts from design docs only.

| Property | Value |
|----------|-------|
| **Model** | Sonnet |
| **Tools** | Read, Write, Edit, Bash, Glob, Grep, PTC |
| **Authorized Dispatchers** | Tester |
| **Reads** | Design docs, plan (phase-level), public API specs. **NEVER implementation source or unit tests.** |
| **Writes** | Scenario test files (`tests/scenarios/**`, `tests/eval/**`) |
| **Worktree** | Yes — task worktree |

**Skills:** base-agent, base-code-agent, test-architecture, scenario-testing, ptc-sandbox

**Return format:**
```json
{
  "delegation_type": "scenario_writing",
  "status": "completed|partial|failed",
  "scenarios_written": [{"file": "...", "scenario_name": "...", "tier": "critical|important|edge_case", "description": "..."}],
  "collect_only_result": {"exit_code": 0, "scenarios_discovered": 12},
  "decisions_made": [{"decision": "...", "reason": "..."}]
}
```

**INV-1 enforcement:** The scenario-writer is the most isolated sub-agent. It reads ONLY the design document and plan to understand what behaviors the system should exhibit. It never reads implementation source or unit tests. This ensures scenarios test the design intent, not implementation artifacts.

**Distinct from test-writer:** The test-writer creates unit/integration tests for specific plan chunks (task-scoped). The scenario-writer creates end-to-end behavioral scenarios for entire phases (phase-scoped). Different scope, different input, different output structure.

---

#### audit-checker

**Identity:** Adversarial plan adherence and quality auditor. Systematically verifies that implementation matches the design document, tests cover required behaviors, and code adheres to project conventions. Extremely granular and critical.

| Property | Value |
|----------|-------|
| **Model** | Sonnet |
| **Tools** | Read, Grep, Glob, Bash, PTC (**NO Write, NO Edit**) |
| **Authorized Dispatchers** | Auditor |
| **Reads** | Both implementation source AND test code, design doc, plan |
| **Writes** | Audit report file only (via returning structured JSON; the Auditor teammate writes the file) |
| **Worktree** | No (reads from task worktree or merged branch) |

**Skills:** base-agent, plan-adherence, code-review, ptc-sandbox

**Additional loadable skill (conditional):** `integration-verification` — loaded during phase-level audits to check cross-task interface wiring (exports consumed, APIs connected, data contracts matched).

**Return format:**
```json
{
  "delegation_type": "audit",
  "status": "completed|partial|failed",
  "verdict": "APPROVED|CRITIQUE|ESCALATED",
  "findings": [
    {
      "severity": "minor|moderate|major|critical",
      "category": "plan_adherence|test_coverage|code_quality|style|design_coherence|integration",
      "file": "path/to/file.py",
      "line": 142,
      "description": "...",
      "expected_behavior": "...",
      "actual_behavior": "...",
      "recommendation": "..."
    }
  ],
  "design_alignment_score": "high|medium|low",
  "test_coverage_assessment": "...",
  "style_conformance_notes": []
}
```

**Audit methodology (enforced in agent definition body):**
1. Read the original design document FIRST — before looking at any code
2. Check implementation against design intent: does the code do what was designed, not just "does it work"?
3. Check tests against required behaviors: are tests grounded in the design, not just testing what happens to be implemented?
4. Check results: all tests pass, quality gate clean
5. Check style and conventions: project-specific patterns, naming, error handling standards
6. Be adversarial: flag unnecessary abstractions, over-engineering, missing error handling, convention drift, dead code, unclear naming

**INV-1 compliance:** The audit-checker reads BOTH tests and source to understand coherence, but has NO Write/Edit tools. It can never modify what it audits. When fixes are needed, it reports findings and the Auditor teammate dispatches the appropriate isolated sub-agent (implementer for code, test-writer for tests).

---

#### debugger

**Identity:** Hypothesis-driven bug investigator. Diagnoses complex, hard-to-find bugs through systematic hypothesis formation and testing. Handles timing issues, race conditions, silent errors (memory leaks, architectural problems), synchronization bugs, and other reasoning-intensive failures that simple code fixes cannot resolve.

| Property | Value |
|----------|-------|
| **Model** | Sonnet |
| **Tools** | Read, Write, Edit, Bash, Glob, Grep, PTC |
| **Authorized Dispatchers** | Coder, Auditor |
| **Reads** | Source code, test RESULTS and error traces. **NEVER test code.** |
| **Writes** | Source code fixes (in task worktree) |
| **Worktree** | Yes — task worktree |

**Skills:** base-agent, base-code-agent, ptc-sandbox

**Return format:**
```json
{
  "delegation_type": "debugging",
  "status": "completed|partial|failed",
  "root_cause": {
    "description": "...",
    "category": "logic|timing|concurrency|memory|architecture|data_flow|configuration",
    "affected_files": [{"path": "...", "lines": "...", "role_in_bug": "..."}],
    "reproduction_steps": ["..."]
  },
  "fix_applied": {
    "files_modified": ["..."],
    "description": "...",
    "confidence": "high|medium|low"
  },
  "related_risks": ["Areas that may have similar issues"],
  "quality_gate_result": { ... }
}
```

**INV-1 enforcement:** The debugger reads source code and test RESULTS (pytest output, stack traces, error messages) but never the test source code. It investigates from the implementation side, using error symptoms to guide hypothesis formation. PreToolUse hook blocks Read/Grep on test file globs.

**When dispatched by Coder:** Implementation is stuck — tests keep failing and simple fixes aren't working. The debugger gets: error output, affected source files, and what has been tried so far.

**When dispatched by Auditor:** Audit found a failure whose root cause is unclear. The debugger gets: audit finding, affected source files, and the design intent from the audit report.

**Debugging methodology (enforced in agent definition body):**
1. Read the error symptoms and affected source files
2. Form 2-3 hypotheses about root cause
3. Test each hypothesis systematically (via PTC analysis, code tracing, targeted Bash commands)
4. Identify root cause with evidence
5. Apply surgical fix
6. Run quality gate to verify fix doesn't break anything
7. Report root cause, fix, and related risks

**Scope:** Handles both minute bugs (off-by-one, wrong variable) and broad architectural bugs (frame buffer timing causing model/user click desynchronization, race conditions in async pipelines, silent memory leaks in long-running processes).

---

#### plan-checker

**Identity:** Pre-execution plan verifier. Reads the design document and implementation plan with fresh eyes to verify alignment, completeness, and feasibility before any code is written. Acts as a "fresh pair of eyes" gate.

| Property | Value |
|----------|-------|
| **Model** | Sonnet |
| **Tools** | Read, Grep, Glob, PTC (**NO Write, NO Edit, NO Bash**) |
| **Authorized Dispatchers** | Planner |
| **Reads** | Plan, design document, codebase structure |
| **Writes** | Verification report only (returned as structured JSON) |
| **Worktree** | No |

**Skills:** base-agent, ptc-sandbox

**Return format:**
```json
{
  "delegation_type": "plan_verification",
  "status": "completed|partial|failed",
  "verdict": "PASS|REVISE",
  "dimensions": {
    "goal_coverage": {"pass": true, "notes": "..."},
    "task_completeness": {"pass": true, "notes": "..."},
    "dependency_accuracy": {"pass": true, "notes": "..."},
    "scope_boundaries": {"pass": true, "notes": "..."},
    "risk_identification": {"pass": false, "notes": "..."},
    "wave_feasibility": {"pass": true, "notes": "..."},
    "acceptance_criteria_clarity": {"pass": true, "notes": "..."},
    "technical_feasibility": {"pass": true, "notes": "..."}
  },
  "revision_suggestions": [{"dimension": "...", "issue": "...", "suggestion": "..."}]
}
```

**Verification dimensions:**
1. **Goal coverage** — does every design requirement have at least one task covering it?
2. **Task completeness** — does each task have clear inputs, outputs, acceptance criteria?
3. **Dependency accuracy** — are `depends_on` relationships correct? Any circular deps?
4. **Scope boundaries** — are tasks properly bounded? Any that try to do too much?
5. **Risk identification** — are risky tasks identified with fallback plans?
6. **Wave feasibility** — are parallel tasks truly independent (no shared files)?
7. **Acceptance criteria clarity** — can each criterion be mechanically verified?
8. **Technical feasibility** — do tasks assume APIs/patterns that don't exist in the codebase?

**Planner-Checker loop:** The Planner dispatches plan-checker → receives verification report → revises plan if REVISE → dispatches plan-checker again → loops until PASS → presents to user.

---

#### optimizer

**Identity:** Post-implementation code optimizer. Reviews source code for performance improvements, memory efficiency, algorithmic complexity, and architectural quality. Works in its own ephemeral worktree so optimizations can be compared against the baseline implementation.

| Property | Value |
|----------|-------|
| **Model** | Sonnet |
| **Tools** | Read, Write, Edit, Bash, Glob, Grep, PTC |
| **Authorized Dispatchers** | Coder |
| **Reads** | Source code only. **NEVER test code.** |
| **Writes** | Optimized source code (in own ephemeral worktree) |
| **Worktree** | Yes — own ephemeral worktree branched from task worktree |

**Skills:** base-agent, base-code-agent, quality-gate, ptc-sandbox

**Return format:**
```json
{
  "delegation_type": "optimization",
  "status": "completed|partial|failed",
  "optimizations_applied": [
    {
      "file": "...",
      "description": "...",
      "category": "algorithmic|memory|cache|io|concurrency|architecture",
      "impact_estimate": "high|medium|low",
      "before_after": {"before": "...", "after": "..."}
    }
  ],
  "quality_gate_result": { ... },
  "worktree_branch": "optimize/{task_id}",
  "diff_summary": "..."
}
```

**Worktree workflow:**
1. Optimizer receives a branch reference (the task's primary worktree)
2. Orchestrator creates an ephemeral worktree branched from it
3. Optimizer makes optimizations in the ephemeral worktree
4. Optimizer runs quality gate to ensure optimizations don't break anything
5. Returns: optimization report + worktree branch name + diff summary
6. Coder teammate (or Auditor) compares the two branches:
   - If optimizations are substantive and safe → merge optimization worktree into primary
   - If marginal or risky → cherry-pick specific improvements
   - If not worth it → discard ephemeral worktree, no harm done
7. The primary implementation is never at risk — the ephemeral worktree is disposable

**INV-1 enforcement:** The optimizer reads only source code, never test code. It optimizes based on algorithmic analysis, not test behavior. PreToolUse hook blocks Read/Grep on test file globs.

**Optimization focus areas:**
- Algorithmic complexity (O(n^2) → O(n log n) where applicable)
- Memory allocation patterns (unnecessary copies, large temporary allocations)
- Cache-friendly data access patterns
- I/O efficiency (batch operations, async where beneficial)
- Hot path identification and optimization (especially critical in robotics-cv: frame processing, detection loops, real-time constraints)
- Unnecessary abstractions that add overhead without value

---

## 5. Dispatch Rules

### 5.1 Authorized Dispatcher Table

Each sub-agent has a fixed set of teammates that are authorized to request its dispatch. The orchestrator enforces this — if a teammate requests a sub-agent type it is not authorized for, the request is rejected.

| Sub-Agent | Authorized Dispatchers | Model Selection |
|-----------|----------------------|-----------------|
| codebase-scout | Explorer | Teammate specifies Haiku or Sonnet per dispatch |
| research-scout | Researcher | Teammate specifies Haiku or Sonnet per dispatch |
| implementer | Coder | Sonnet (fixed) |
| test-writer | Coder, Tester | Sonnet (fixed) |
| scenario-writer | Tester | Sonnet (fixed) |
| audit-checker | Auditor | Sonnet (fixed) |
| debugger | Coder, Auditor | Sonnet (fixed) |
| plan-checker | Planner | Sonnet (fixed) |
| optimizer | Coder | Sonnet (fixed) |

### 5.2 Cross-Domain Communication

When a teammate needs work outside its domain, it messages the specialist teammate:

| Requester Needs | Specialist | Message Type |
|----------------|-----------|--------------|
| Codebase context for a module | Explorer | info_request |
| API/library documentation | Researcher | info_request |
| Code fix for audit finding | Coder | task_request (via Auditor → orchestrator) |
| Test fix for broken tests | Coder or Tester | task_request |
| Plan verification | Planner | (orchestrator mediates) |
| Audit of implementation | Auditor | review_request (via orchestrator) |
| Root cause investigation | Coder or Auditor | (dispatches debugger directly) |

### 5.3 Delegation Decision Framework

Before dispatching a sub-agent, the teammate MUST evaluate:

| Condition | Action |
|-----------|--------|
| Task requires < 3 tool calls and no judgment | Do it yourself |
| Task is a single Bash command or file read | Do it yourself |
| Task requires domain expertise you lack | Message the specialist teammate |
| Task requires parallel agents for depth/accuracy | Message specialist teammate (they know how to partition) |
| Task is substantial mechanical work in YOUR domain | Dispatch your sub-agent |
| Task requires judgment about delegation strategy | Use Think MCP, then dispatch |

### 5.4 Parallel Dispatch Rules

Teammates can request multiple sub-agents in parallel when:

1. **Tasks are file-independent** — no shared write targets
2. **Tasks don't depend on each other's output** — each is self-contained
3. **Order doesn't matter** — results can be synthesized in any order

When any of these conditions are NOT met, dispatch sequentially.

**Wave-based parallelism:** For phase execution, the plan pre-groups tasks into waves. All tasks in a wave are verified file-independent by the plan-checker. The orchestrator dispatches all tasks in a wave simultaneously.

### 5.5 Model Selection (Haiku vs Sonnet)

For sub-agents that support model selection (codebase-scout, research-scout), the teammate chooses:

| Task Characteristics | Model |
|---------------------|-------|
| Simple extraction, known file paths, pattern matching | Haiku |
| Structure analysis, dependency tracing, synthesis | Sonnet |
| Documentation lookup, API signature extraction | Haiku |
| Comparative analysis, best practice evaluation | Sonnet |

Sonnet is the default. Haiku is an optimization for tasks where the overhead savings justify the reduced capability.

---

## 6. Communication Architecture

### 6.1 Message Flow

```
User <──────────────> Orchestrator (main session)
                           │
                    ┌──────┼──────────────────────────┐
                    │      │                           │
              [TeamCreate] │                    [Sub-agent spawn]
                    │      │                           │
         ┌─────────┼──────┼─────────┐                 │
         │         │      │         │                  │
      Explorer  Researcher Planner  │              Sub-agents
         │         │      │         │             (ephemeral)
         │         │      │    ┌────┼────┐
         │         │      │  Coder Tester Auditor
         │         │      │    │    │      │
         └─────SendMessage─────┘    │      │
           (teammate ←→ teammate)   │      │
                                    └──────┘
```

### 6.2 Teammate-to-Teammate (Direct)

Teammates message each other directly via `SendMessage` for information exchange:
- Explorer ↔ Researcher: cross-domain queries
- Coder ↔ Explorer: context requests
- Coder ↔ Tester: implementation status, failure reports
- Auditor ↔ Coder: fix instructions, critique responses
- Auditor ↔ Tester: test fix instructions
- Planner ↔ Explorer: context grounding
- Planner ↔ Researcher: technical feasibility

### 6.3 Sub-Agent Spawn (Orchestrator-Mediated)

1. Teammate writes delegation JSON to `.claude/temp/delegation-{task_id}.json`
2. Teammate sends `info_request` to orchestrator with the file path
3. Orchestrator reads delegation JSON, validates authorized dispatcher
4. Orchestrator spawns sub-agent via Task tool with the delegation prompt
5. Sub-agent executes, returns structured JSON
6. Orchestrator routes result back to teammate via `info_ready` with output file path

### 6.4 User Interface

- User talks to orchestrator for workflow-level coordination
- User talks directly to Auditor in its tmux pane for audit discussions
- User approves scenarios via Tester interaction
- User approves plans via Planner interaction
- All major decisions and escalations surface through the orchestrator or directly in the relevant teammate's pane

---

## 7. Skill Assignments

### 7.1 Universal Skills

| Skill | Loaded By | Purpose |
|-------|-----------|---------|
| ptc-sandbox | ALL agents (teammates + sub-agents) | PTC MCP access, container routing, REPL management, fallback protocol |

### 7.2 Teammate Skills

| Teammate | Skills |
|----------|--------|
| Explorer | ptc-sandbox, context-packets, sub-agent-delegation/explorer-routes, result-synthesis/exploration, codebase-exploration |
| Researcher | ptc-sandbox, sub-agent-delegation/researcher-routes, result-synthesis/research, research-methodology |
| Planner | ptc-sandbox, implementation-plans, test-architecture, phase-planning, multi-perspective-analysis, sub-agent-delegation/planner-routes |
| Coder | ptc-sandbox, sub-agent-delegation/coder-routes, result-synthesis/implementation, task-execution, plan-adherence |
| Tester | ptc-sandbox, sub-agent-delegation/tester-routes, test-architecture, scenario-testing |
| Auditor | ptc-sandbox, sub-agent-delegation/auditor-routes, result-synthesis/audit, code-review, plan-adherence |

### 7.3 Sub-Agent Skills

| Sub-Agent | Level 1 | Level 2 | Specific Skills |
|-----------|---------|---------|-----------------|
| codebase-scout | base-agent | — | context-packets, ptc-sandbox |
| research-scout | base-agent | — | ptc-sandbox |
| implementer | base-agent | base-code-agent | plan-adherence, quality-gate, ptc-sandbox |
| test-writer | base-agent | base-code-agent | tdd-workflow, test-architecture, ptc-sandbox |
| scenario-writer | base-agent | base-code-agent | test-architecture, scenario-testing, ptc-sandbox |
| audit-checker | base-agent | — | plan-adherence, code-review, ptc-sandbox |
| debugger | base-agent | base-code-agent | ptc-sandbox |
| plan-checker | base-agent | — | ptc-sandbox |
| optimizer | base-agent | base-code-agent | quality-gate, ptc-sandbox |

### 7.4 Conditional Skills

| Skill | Loaded When | By |
|-------|-------------|-----|
| integration-verification | Phase-level audit (not task-level) | audit-checker |

### 7.5 Synthesis Skill Structure

```
new_claude/skills/result-synthesis/
├── SKILL.md              # Universal synthesis protocol
├── routes/
│   ├── exploration.md    # Merge codebase-scout results → context packets
│   ├── research.md       # Merge research-scout results → research files
│   ├── implementation.md # Evaluate implementer/test-writer outputs
│   └── audit.md          # Consolidate audit-checker reports
```

Each teammate loads the base synthesis skill + their route. PTC is the synthesis engine — raw sub-agent results stay in the container, the teammate gets a JSON summary.

### 7.6 Delegation Skill Structure

```
new_claude/skills/sub-agent-delegation/
├── SKILL.md                    # General delegation protocol
├── routes/
│   ├── explorer-routes.md      # How to dispatch codebase-scout
│   ├── researcher-routes.md    # How to dispatch research-scout
│   ├── planner-routes.md       # How to dispatch plan-checker
│   ├── coder-routes.md         # How to dispatch implementer, test-writer, debugger, optimizer
│   ├── tester-routes.md        # How to dispatch scenario-writer, test-writer
│   └── auditor-routes.md       # How to dispatch audit-checker, debugger
├── base-agent.md               # Level 1 inheritance template
├── base-code-agent.md          # Level 2 inheritance template
└── references/
    └── anti-patterns.md
```

---

## 8. File Locations

### 8.1 Agent Definitions

```
new_claude/agents/
├── teammates/
│   ├── orchestrator.md
│   ├── explorer.md
│   ├── researcher.md
│   ├── planner.md
│   ├── coder.md
│   ├── tester.md
│   └── auditor.md
├── sub-agents/
│   ├── codebase-scout.md
│   ├── research-scout.md
│   ├── implementer.md
│   ├── test-writer.md
│   ├── scenario-writer.md
│   ├── audit-checker.md
│   ├── debugger.md
│   ├── plan-checker.md
│   └── optimizer.md
└── inheritance/
    ├── base-agent.md
    └── base-code-agent.md
```

### 8.2 Related Files

| Purpose | Path |
|---------|------|
| This document | `new_claude/agents/AGENT-ARCHITECTURE.md` |
| V2 restructuring plan (superseded sections noted) | `.claude/plans/v2-subagent-restructuring.md` |
| State machines | `state-machines/*.json` |
| Delegation skills | `new_claude/skills/sub-agent-delegation/` |
| Synthesis skills | `new_claude/skills/result-synthesis/` |
| Delegation prompt schemas | `new_claude/skills/delegation-prompts/schemas/` |
| Return validation | `new_claude/skills/delegation-prompts/scripts/validate_return.py` |
| SubagentStop hook | `hooks/subagent_stop.py` |
| SubagentStart hook | `hooks/subagent_start.py` |
| Quality gate script | `scripts/gate.sh` |

---

## 9. Extension Protocol

To add a new sub-agent type:

1. **Create agent definition** at `new_claude/agents/sub-agents/{name}.md`
   - Extend base-agent (Level 1) and optionally base-code-agent (Level 2) via `skills:` field
   - Define tools, model, read/write permissions, return format
   - Include behavioral instructions specific to this type

2. **Add route entry** in the authorized teammate's delegation routes file
   - `new_claude/skills/sub-agent-delegation/routes/{teammate}-routes.md`
   - Define: when to dispatch, how to compose the prompt, what to verify on return

3. **Add return validation** in SubagentStop hook
   - `hooks/subagent_stop.py` — add validation case for the new `delegation_type`
   - Define required fields and structural checks

4. **Update authorized dispatcher table** in this document (Section 5.1)

5. **Add synthesis route** if the sub-agent returns results that need multi-agent synthesis
   - `new_claude/skills/result-synthesis/routes/{context}.md`

No other changes needed. The inheritance model, delegation protocol, and hook infrastructure handle everything else automatically.

---

## 10. Design Decisions Log

Decisions made during the Task #1 design session, preserved for traceability.

| # | Decision | Rationale | Alternatives Rejected |
|---|----------|-----------|----------------------|
| D1 | 9 sub-agent types, not 6 or 12 | Each serves a distinct purpose without being pigeonholed. 6 was too sparse (1:1 ratio). 12 was over-specific (conditional logic in definitions). | 6 generic types (too much conditional logic), 12 role-specific (too narrow) |
| D2 | Auditor is a persistent teammate, not a sub-agent | Needs SendMessage for arbitration, persistent context for ad-hoc user audits, and ability to reason across multiple audit reports | Per-phase teammate (too limited), on-demand sub-agent (can't arbitrate) |
| D3 | Strict domain separation — no sub-agent borrowing | Specialist teammates know how to partition, prompt, and verify their sub-agents. Non-specialists produce worse results. | Cross-domain borrowing (generalist drift, worse prompt quality) |
| D4 | Shared sub-agents (debugger, test-writer) have multiple authorized dispatchers | These agents legitimately serve multiple domains. Debugger serves both Coder and Auditor. Test-writer serves both Coder and Tester. | Separate per-domain variants (unnecessary duplication) |
| D5 | Test/code isolation as a first-class invariant | Prevents test overfitting. Enforced by hooks, not just conventions. | Advisory-only (insufficient enforcement) |
| D6 | Optimizer works in ephemeral worktree | Optimizations can be compared against baseline without risk. Discard if not worth it. | In-place optimization (risky, no comparison), read-only optimizer (can't demonstrate improvements) |
| D7 | Plan-checker as a dedicated sub-agent | Plan errors caught before implementation are 10-50x cheaper to fix. Fresh eyes on plan-design alignment. | Planner self-checks (same context bias), no verification (plan errors reach implementation) |
| D8 | Debugger can edit code | Distinguishes from implementer by input (symptoms vs plan) and method (hypothesis-driven vs plan-driven). Read-only debugger just duplicates audit-checker's role. | Read-only debugger (becomes redundant with audit-checker) |
| D9 | Level 1 + Level 2 inheritance, defer Level 3 grouping | Level 1 (base-agent) eliminates duplication across 9 agents. Level 2 (base-code-agent) justified by 5 code-writing agents. Analysis and exploration groups only have 2 agents each — not worth a Level 2 yet. | Flat (too much duplication), 3-level for all groups (premature for small groups) |
| D10 | Integration checking as a conditional skill, not a sub-agent | Cross-task wiring verification is valuable but can be loaded into audit-checker during phase audits. Separate agent not justified until scenario testing proves insufficient. | Dedicated sub-agent (too specialized for current needs) |
| D11 | Arbitration is root-cause focused, not adversarial | When Coder and Tester disagree, the problem is usually neither side's fault exclusively. Root-cause analysis from fresh context is more productive than blame assignment. | Adversarial arbitration (escalates conflict), blame-based (doesn't solve the underlying issue) |
| D12 | Universal result-synthesis skill with role-specific routes | Explorer, Researcher, Coder, and Auditor all need to synthesize parallel sub-agent results. Common protocol, different output formats per role. | Per-teammate synthesis (duplicated logic), no synthesis skill (ad-hoc, inconsistent) |
| D13 | Agent-specific return instructions, not universal return-protocol skill | Each sub-agent type has a different output shape. A universal skill wastes tokens teaching irrelevant return formats. Embedding return instructions per-agent is cheaper. | Universal return-protocol skill (~2K tokens per agent, 90% irrelevant content) |
| D14 | Teammate model selection for Haiku vs Sonnet | Teammates know the task complexity and choose the appropriate model. Haiku for extraction, Sonnet for analysis. Not hardcoded. | Fixed model per agent type (inflexible), always Sonnet (wasteful for simple tasks) |
| D15 | Do not design around 200K teammate context constraint | Token usage and context consumption should be optimized, but the 200K limit is a known bug (GitHub #34421) likely to be resolved. Design for efficiency, not for a specific context ceiling. | Hard 200K design limit (premature optimization around a bug) |
| D16 | Sequential phases, 1 Coder per phase | Task parallelism within waves, not phase parallelism. One Coder teammate directs all sub-agents for a phase's tasks. | Multiple Coders per phase (coordination overhead, merge conflicts) |
| D17 | Orchestrator role reduced to sub-agent spawning | Teammates message each other directly for information exchange. Orchestrator only handles spawn requests and user interface. | Orchestrator mediates all communication (bottleneck, token waste) |
| D18 | Orchestrator dashboard at `.claude/state/system-dashboard.json` | Tracks teammates, their sub-agents, task assignments, statuses, and message directory. Single source of truth for system state. | Distributed state (hard to reason about), no dashboard (no observability) |
| D19 | No hook-based sub-agent dispatch | Orchestrator retains spawn authority. Hooks handle worktree setup and daemon communication only. | Hook-triggered spawning (implicit, hard to debug) |
| D20 | Parallel sub-agents safe on same task worktree | Test-writer (test files), implementer (source files), and auditor (reads only) don't overlap in file writes. INV-1 enforces this. | Sequential-only sub-agents (slower, unnecessary) |
| D21 | Optimizer is task-level, dispatched by Coder post-green | Coder deliberates after green phase whether the task is performance-sensitive (hot path, real-time constraint). If yes, dispatches optimizer before sending to Auditor. Auditor reviews final optimized code in one pass — no re-audit loop. Most tasks skip optimization entirely. | Phase-level optimization by Auditor (delays phase completion, requires re-audit after code changes, Auditor manages 3 sub-agent types instead of 2) |
| D22 | Sub-agent cold-start is primary path, resume is optimization | Cold-start gets: delegation prompt + previous return (decisions_made, carry_forward). Crash recovery via ephemeral state file. Resume not required for reliability. | Resume-dependent architecture (fragile, session-bound) |

---

## Appendix A: Summary Tables

### A.1 Complete Teammate Table

| Teammate | Model | Persistence | PTC | Dispatches | Skills Count |
|----------|-------|-------------|-----|------------|--------------|
| Orchestrator | Opus | Full session | Yes | All types (sole spawner) | 3+ |
| Explorer | Sonnet | Persistent, /clear at pressure | Yes | codebase-scout | 5 |
| Researcher | Sonnet | Persistent, /clear at pressure | Yes | research-scout | 4 |
| Planner | Opus | Per-feature | Yes | plan-checker | 6 |
| Coder | Opus | Persistent, /clear between phases | Yes | implementer, test-writer, debugger, optimizer | 5 |
| Tester | Opus | Per-phase | Yes | scenario-writer, test-writer | 4 |
| Auditor | Opus | Persistent, full session | Yes | audit-checker, debugger | 5 |

### A.2 Complete Sub-Agent Table

| Sub-Agent | Model | Tools | Write Access | Worktree | Authorized By | INV-1 Status |
|-----------|-------|-------|-------------|----------|---------------|--------------|
| codebase-scout | S/H | Read,Grep,Glob,Bash,PTC | Context packets | No | Explorer | N/A (reads codebase, writes packets) |
| research-scout | S/H | Read,Grep,WS,WF,PTC | Research files | No | Researcher | N/A (reads web, writes research) |
| implementer | S | Read,Write,Edit,Bash,Glob,Grep,PTC | Source code | Task WT | Coder | Reads source, NEVER tests |
| test-writer | S | Read,Write,Edit,Bash,Glob,Grep,PTC | Test files | Task WT | Coder, Tester | Reads tests, NEVER source |
| scenario-writer | S | Read,Write,Edit,Bash,Glob,Grep,PTC | Scenario files | Task WT | Tester | Reads design, NEVER impl/tests |
| audit-checker | S | Read,Grep,Glob,Bash,PTC | Report only | No | Auditor | Reads BOTH, writes NEITHER |
| debugger | S | Read,Write,Edit,Bash,Glob,Grep,PTC | Source fixes | Task WT | Coder, Auditor | Reads source + test results, NEVER test code |
| plan-checker | S | Read,Grep,Glob,PTC | Report only | No | Planner | N/A (reads plan/design) |
| optimizer | S | Read,Write,Edit,Bash,Glob,Grep,PTC | Optimized source | Ephemeral WT | Coder | Reads source, NEVER tests |

### A.3 Inheritance Chain

```
base-agent (Level 1)
├── codebase-scout
├── research-scout
├── audit-checker
├── plan-checker
└── base-code-agent (Level 2)
    ├── implementer
    ├── test-writer
    ├── scenario-writer
    ├── debugger
    └── optimizer
```
