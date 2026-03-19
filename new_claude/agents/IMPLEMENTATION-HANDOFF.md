# Implementation Handoff — Agent Definition .md Files

**Date:** 2026-03-16
**Status:** Design complete (6/6 teammates, 9/9 sub-agents). Implementation phase begins.
**Goal:** Convert design documents into operational .md agent definition files.

---

## Required Skills — Load Before Starting

Load these three skills FIRST. They contain the rules, patterns, anti-patterns, and examples for writing agent .md files. Do NOT write any .md files without loading these.

### 1. Agent MD Writing Skill (for teammate .md files)
```
Read: /home/kartik/personal/agentic_workflow/skills/agent-md-writing/SKILL.md
Read: /home/kartik/personal/agentic_workflow/skills/agent-md-writing/references/patterns.md
Read: /home/kartik/personal/agentic_workflow/skills/agent-md-writing/references/anti-patterns.md
Read: /home/kartik/personal/agentic_workflow/skills/agent-md-writing/references/complete-examples.md
Read: /home/kartik/personal/agentic_workflow/skills/agent-md-writing/references/translation-walkthrough.md
```

### 2. Sub-Agent MD Writing Skill (for sub-agent .md files)
```
Read: /home/kartik/personal/agentic_workflow/skills/sub-agent-md-writing/SKILL.md
Read: /home/kartik/personal/agentic_workflow/skills/sub-agent-md-writing/references/patterns.md
Read: /home/kartik/personal/agentic_workflow/skills/sub-agent-md-writing/references/anti-patterns.md
Read: /home/kartik/personal/agentic_workflow/skills/sub-agent-md-writing/references/complete-examples.md
Read: /home/kartik/personal/agentic_workflow/skills/sub-agent-md-writing/references/delegation-prompt-design.md
```

### 3. Writing Skills Skill (general writing methodology)
```
Read: /home/kartik/personal/agentic_workflow/skills/writing-skills/SKILL.md
Read: /home/kartik/personal/agentic_workflow/skills/writing-skills/references/writing-patterns.md
Read: /home/kartik/personal/agentic_workflow/skills/writing-skills/references/anti-rationalization-architecture.md
```

---

## Design Source Documents — Read Before Writing Each File

These are the authoritative design references. Every .md file is a TRANSLATION of these documents — not a copy, not a summary. The skills above teach you HOW to translate.

### Primary Design Documents (in `new_claude/agents/`)

| Document | Lines | Content | Read When |
|----------|-------|---------|-----------|
| `AGENT-ARCHITECTURE.md` | 1,072+ | Source of truth: 7 teammates, 9 sub-agents, 6 invariants, 2-level inheritance, dispatch rules, 22 design decisions | Before writing ANY .md file |
| `BASE-DIRECTOR-PATTERN.md` | 1,157 | Universal teammate lifecycle: 5 phases, 14 states, 17 extension points. Design reference, NOT runtime — do NOT copy this into .md files | Before writing teammate .md files |
| `TEAMMATE-DIRECTOR-PATTERNS.md` | 2,628 | Per-teammate extension points for all 6 teammates (Explorer §1, Researcher §2, Planner §3, Coder §4, Tester §5, Auditor §6). V1→V2 state alignment, think prompts, delegation composition, self-execute scope, cross-domain partners | Primary source for each teammate .md file |
| `SUB-AGENT-SPECS.md` | 1,519 | All 9 sub-agent specs: behavioral steps, input/output contracts, validation rules, tool access, file access, failure modes, state machine integration. Common conventions, 3 appendices | Primary source for each sub-agent .md file |
| `DELEGATION-RETURN-SCHEMAS.md` | 699 | 11 shared Pydantic sub-models, 7 new return types, migration path | Reference for output contract sections |

### Supporting References

| Document | Content | Read When |
|----------|---------|-----------|
| `state-machines/explorer.json` | V1 Explorer state machine (13 states) | Writing explorer.md |
| `state-machines/researcher.json` | V1 Researcher state machine | Writing researcher.md |
| `state-machines/strategist.json` | V1 Planner state machine | Writing planner.md |
| `state-machines/coder.json` | V1 Coder state machine | Writing coder.md |
| `state-machines/tester.json` | V1 Tester state machine (12 states) | Writing tester.md |
| `state-machines/auditor-task.json` | V1 Task Auditor state machine (6 states) | Writing auditor.md |
| `state-machines/auditor-phase.json` | V1 Phase Auditor state machine (9 states) | Writing auditor.md |
| `.claude/agents/codebase-explorer.md` | V1 agent definition — example of target format | Format reference |

### Existing V1 Skills (loaded by agents at runtime)

| Skill | Path | Loaded By |
|-------|------|-----------|
| context-packets | `.claude/skills/context-packets/` | Explorer, Planner, Coder |
| implementation-plans | `.claude/skills/implementation-plans/` | Planner, Coder |
| plan-adherence | `.claude/skills/plan-adherence/` | Coder, Auditor |
| test-architecture | `.claude/skills/test-architecture/` | Tester |
| session-lifecycle | `.claude/skills/session-lifecycle/` | All |
| tdd-workflow | `.claude/skills/tdd-workflow/` | Coder |
| research-workflow | `.claude/skills/research-workflow/` | Researcher |
| sub-agent-delegation | `.claude/skills/sub-agent-delegation/` | All teammates with sub-agents |
| ptc-sandbox | `skills/ptc-sandbox/` | All agents |
| coding-memory | `.claude/skills/coding-memory/` | Scribe |
| git-history-analysis | `.claude/skills/git-history-analysis/` | Explorer (conditional) |

---

## What Was Done in the Design Phase

### Session 1: Agent Architecture (Task #1)
- Defined complete agent roster: 7 teammates + 9 sub-agents
- Established 6 architectural invariants (INV-1 through INV-6)
- Defined 2-level inheritance model for sub-agents (base-agent → base-code-agent → specific)
- Established dispatch rules, skill assignments, communication protocol
- Made 15 design decisions with rationale and rejected alternatives
- Output: `AGENT-ARCHITECTURE.md`

### Session 2: Base Director Pattern (Task #2a)
- Designed universal lifecycle all teammates follow: 5 phases, 14 states
- Defined 17 extension points for per-teammate customization
- Established orchestrator-mediated spawn protocol (temporary — see below)
- Defined general sub-agent mechanism (orchestrator-only)
- Designed 4-path error recovery (RETRY, PARTIAL_SYNTHESIS, HANDOFF, ABORT)
- Key decision: design reference, NOT runtime inheritance. Each .md file is self-contained.
- Output: `BASE-DIRECTOR-PATTERN.md`

### Session 3: Per-Teammate Patterns (Task #2b)
- Completed all 6 teammate extension points:
  - **Explorer** (§1): Incremental enhancement. 14 states. Parallel scout dispatch, PTC synthesis.
  - **Researcher** (§2): Incremental enhancement. 14 states. MCP routing, search strategy fallback.
  - **Planner** (§3): Mostly incremental. 16 states. V1 review cycle preserved. OVERRIDE extension. 5-gate plan-checker integration.
  - **Coder** (§4): Full redefinition. 12 states. Event-driven coordinator, not executor. OVERRIDE extension. Sub-agent pipeline: test-writer → implementer → debugger → audit loop.
  - **Tester** (§5): Incremental enhancement. 17 states. SCENARIO_PLANNING expanded to 5-state planning sub-cycle with user review gates. 3-gate plan-checker integration. OVERRIDE extension.
  - **Auditor** (§6): Structural consolidation. 11 states. Two V1 machines → one V2 event-driven machine. Unified investigation cycle. Three audit scopes (task/phase/project). Hybrid arbitration. OVERRIDE extension.
- Output: `TEAMMATE-DIRECTOR-PATTERNS.md`

### Session 3 (continued): Sub-Agent Specs
- Completed all 9 sub-agent detailed specs (8 dimensions each)
- Established common conventions: status values, ephemeral state file, cold-start, validation layers, quality gate hooks, stall threshold principle, INV-1 execution results clarification, partial return handling
- 3 appendices: validation summary, INV-1 matrix, delegation type mapping
- Output: `SUB-AGENT-SPECS.md`

### Session 3 (continued): Delegation Return Schemas
- Designed 11 shared Pydantic sub-models
- Designed 7 new return types (PlanVerification, Audit, TestWriting, Implementation, ScenarioWriting, Debugging, Optimization)
- Output: `DELEGATION-RETURN-SCHEMAS.md`

### Review Fixes Applied This Session
- **Plan-checker resume → cold-start fallback**: Added `previous_verification_report` field to plan-checker input contract. Documented resume-first, cold-start-fallback pattern in common conventions and Planner/Tester sections.
- **Partial return handling**: Added deliberation-based decision framework to common conventions (accept/re-dispatch/self-investigate via existing think prompts).
- **Test-writer multi-parent**: Added "Dispatch Context" section to SUB-AGENT-SPECS §5 clarifying Coder vs Tester dispatch purposes.
- **Stall threshold principle**: Documented 3 vs 5 split in common conventions.
- **INV-1 execution results**: New common conventions section clarifying source file reads vs execution output.
- **General sub-agent**: Removed from all teammate authorized sub-agent tables. Available to orchestrator only.
- **Orchestrator-mediated spawning**: Documented as temporary constraint in TEAMMATE-DIRECTOR-PATTERNS preamble.
- **Debugger INV-1**: Added acknowledgment about pytest output containing assertion details.
- **Debugger dispatch context**: Clarified Coder (workflow) vs Auditor (ad-hoc only).

---

## Files to Write — Execution Order

### Output Directories (create if not exist)
```
new_claude/agents/teammates/     # 7 teammate .md files (including orchestrator)
new_claude/agents/sub-agents/    # 9 sub-agent .md files
```

### Phase A: Sub-Agent .md Files (9 files — most mechanical)

These are the easiest to write. SUB-AGENT-SPECS.md provides all 8 dimensions per sub-agent. The sub-agent-md-writing skill teaches the translation. Each file is 80-200 lines.

Write one file at a time. Present to user for review before proceeding.

| # | File | Source | Key Concerns |
|---|------|--------|-------------|
| 1 | `sub-agents/codebase-scout.md` | SUB-AGENT-SPECS §1 | Broad read access, partition-scoped, exploration return |
| 2 | `sub-agents/research-scout.md` | SUB-AGENT-SPECS §2 | WebSearch/WebFetch access, broad read, citation requirements |
| 3 | `sub-agents/plan-checker.md` | SUB-AGENT-SPECS §3 | Read-only, `previous_verification_report` for multi-gate cold-start, 8 verification dimensions |
| 4 | `sub-agents/audit-checker.md` | SUB-AGENT-SPECS §4 | Read-only, NO Write/Edit, reads BOTH source+tests (INV-1 compliant), adversarial methodology order |
| 5 | `sub-agents/test-writer.md` | SUB-AGENT-SPECS §5 | **Multi-parent**: Coder (TDD red phase) vs Tester (infrastructure repair). INV-1: blind to source. Red verification mandatory for Coder dispatch. |
| 6 | `sub-agents/implementer.md` | SUB-AGENT-SPECS §6 | INV-1: blind to tests. Receives test RESULTS not test code. Stall threshold 5. Quality gate mandatory. |
| 7 | `sub-agents/scenario-writer.md` | SUB-AGENT-SPECS §7 | Most isolated: blind to source AND unit tests. Reads design+plan only. Collect-only validation. |
| 8 | `sub-agents/debugger.md` | SUB-AGENT-SPECS §8 | Hypothesis-driven, ≥2 hypothesis log entries. Stall threshold 3. Sees test RESULTS in pytest output (acknowledged, not violation). Multi-parent: Coder (workflow) + Auditor (ad-hoc). |
| 9 | `sub-agents/optimizer.md` | SUB-AGENT-SPECS §9 | Ephemeral worktree, compare-and-discard. Multi-parent: Coder (post-green) + Auditor (ad-hoc) + Orchestrator (standalone). |

### Phase B: Teammate .md Files (6 files — requires judgment)

These require more judgment. TEAMMATE-DIRECTOR-PATTERNS provides the design; the agent-md-writing skill teaches how to translate extension points into behavioral instructions. Each file is 200-350 lines.

**Critical: strip design rationale, V1 alignment tables, and source citations. Only behavioral instructions, decision frameworks, contracts, and constraints go in the .md body.**

Write one file at a time. Present to user for review before proceeding.

| # | File | Source | Complexity | Key Concerns |
|---|------|--------|------------|-------------|
| 10 | `teammates/explorer.md` | TEAMMATE-DIRECTOR §1 | Moderate | Incremental V1 enhancement. Parallel scout dispatch. PTC synthesis engine. |
| 11 | `teammates/researcher.md` | TEAMMATE-DIRECTOR §2 | Moderate | MCP routing (Context7 primary, WebSearch fallback). Search strategy fallback. |
| 12 | `teammates/planner.md` | TEAMMATE-DIRECTOR §3 | High | V1 review cycle preserved. 5-gate plan-checker integration. OVERRIDE extension. Progress-based stall tracking. |
| 13 | `teammates/coder.md` | TEAMMATE-DIRECTOR §4 | Very High | Full redefinition — event-driven coordinator. Sub-agent pipeline. Wave coordination. Merge management. Audit response loop. |
| 14 | `teammates/tester.md` | TEAMMATE-DIRECTOR §5 | High | 5-state planning sub-cycle. 3-gate plan-checker integration. INV-1 blind to implementation. Merge management. Daemon-mediated re-execution. |
| 15 | `teammates/auditor.md` | TEAMMATE-DIRECTOR §6 | High | Event-driven unified investigation cycle. Three audit scopes (task/phase/project). Hybrid arbitration. Debugger+optimizer ad-hoc only. Three project verdicts. |

### Phase C: Orchestrator .md File (1 file — custom, not mechanical)

| # | File | Source | Notes |
|---|------|--------|-------|
| 16 | `teammates/orchestrator.md` | AGENT-ARCHITECTURE §3.1 + throughout | Not a standard teammate — behavioral mode of main session. Custom writing needed. Covers: team creation, teammate spawning, sub-agent spawn mediation (temporary), wave management, dashboard updates, phase transitions. |

---

## Key Design Decisions the Writer Must Know

These decisions were made across multiple sessions. They affect how .md files should be written.

### Architecture
1. **Teammates are directors, not doers.** They load context into PTC, reason about strategy, spawn sub-agents, synthesize results. They do NOT perform substantial mechanical work themselves. Self-execute heuristic: < 3 tool calls AND no judgment required → do it yourself.
2. **Sub-agents are versatile workers.** Specialization comes from the delegation prompt, not narrow definitions. The .md file defines stable capabilities; the delegation prompt provides task-specific context.
3. **INV-1 (test/code isolation):** Agents that read both sides MUST NOT edit either. Agents that write source NEVER read test code. Agents that write tests NEVER read source code. All agents may see execution RESULTS (stdout, stderr, pytest output).
4. **INV-4 (adversarial audit):** Audit-checker is critical and granular. Its job is to find problems, not approve. No Write/Edit tools.
5. **Cold-start is primary.** Resume is attempted first (fast path). Cold-start with previous return is the reliable fallback. Sub-agent .md files should NOT assume they will be resumed — they must be self-contained.
6. **Orchestrator-mediated spawning is temporary.** Teammates write delegation JSON, message orchestrator to spawn. This is a workaround for a current Claude Code bug. When resolved, teammates will spawn directly.

### Behavioral
7. **V1 state machines are the foundation.** The V1 state machines encode real operational knowledge. The .md files should reflect V2 enhancements layered on V1 foundations, not replacements.
8. **Progress-based stall tracking.** Counters reset on progress, increment on stall. Thresholds: 3 for verification/analysis, 5 for implementation/execution.
9. **Distributed deliberation.** Think prompts at natural decision points only. No frontloaded "think about everything" phase.
10. **Two-layer validation.** SubagentStop hook = structural (schema, fields). Parent semantic review = "did it actually do the right thing?"
11. **Quality gate hooks: universal lint/type on Write/Edit (PostToolUse).** Pytest interpretation is the sub-agent's job (phase-dependent meaning).
12. **General sub-agents: orchestrator only.** Teammates use their specialized sub-agents, self-execute via PTC, or escalate. No general sub-agent bypass.

### File Format
13. **Frontmatter:** `name`, `description`, `tools`, `model`, `mode` (teammate vs sub-agent), optionally `mcps`, `disallowedTools`. Description must NOT summarize the workflow (CSO Rule from the writing skills).
14. **Body target:** Teammates 200-350 lines, sub-agents 80-200 lines. Design rationale, V1 alignment tables, source citations stay in the design docs — they do NOT go in the .md files.
15. **Skills in startup check:** Each .md file lists skills to load at startup (REQUIRED vs CONDITIONAL). Skills teach HOW; the .md file defines WHEN and WHAT.
16. **The infrastructure boundary:** State machine transitions, hook validation rules, daemon interactions — these are infrastructure. The .md file should NOT restate them. The .md file tells the agent what decisions to make and how to reason; hooks and state machines enforce constraints mechanically.

---

## Anti-Patterns to Avoid (from the writing skills)

These are the most common failure modes when converting design docs to .md files:

1. **Design Doc Converter** — Copying design doc content verbatim instead of translating to behavioral instructions
2. **State Machine Narrator** — Restating state machine transitions ("when in STATE_X, transition to STATE_Y") instead of describing the decision framework
3. **Skill Duplicator** — Repeating content that's already in loaded skills
4. **Hook Restater** — Describing hook validation rules in the .md body (hooks enforce mechanically, the agent doesn't need to know the rules)
5. **Kitchen Sink Agent** — .md file > 400 lines, trying to cover every edge case
6. **Single-Parent Specialist** — Sub-agent .md that only describes one parent's dispatch context when it has multiple parents

---

## User Interaction Preferences

- **Write one .md file at a time.** Present for user review before proceeding to the next.
- **Do NOT batch-write files.** The user wants granular control over each file.
- **Explain what you designed and WHY.** Walk through key translation decisions — what was kept, what was stripped, what was reframed as behavioral instruction vs left to infrastructure.
- **Show the final .md file, not a diff.** The user reads the full file for coherence.
- **Start with sub-agents (Phase A).** They're more mechanical and build confidence in the translation approach before tackling the more nuanced teammate files.

---

## Verification Checklist Per File

Before presenting each .md file to the user:

1. [ ] Frontmatter complete and description follows CSO Rule
2. [ ] Startup check lists correct skills (REQUIRED vs CONDITIONAL)
3. [ ] Body is within token budget (sub-agents: 80-200 lines, teammates: 200-350 lines)
4. [ ] No design rationale, V1 alignment tables, or source citations in the body
5. [ ] No hook validation rules restated in the body
6. [ ] No state machine transitions narrated in the body
7. [ ] No skill content duplicated in the body
8. [ ] INV-1 enforcement described as identity/scope constraint, not as hook rules
9. [ ] Self-execute vs delegate boundary is clear
10. [ ] For multi-parent sub-agents: all dispatch contexts covered
11. [ ] For teammates: think prompt decision frameworks are behavioral, not procedural
12. [ ] Cross-domain communication partners and message schemas included
13. [ ] Output format and return contract clearly specified
14. [ ] Failure modes described as "what to do when X happens" not "hook catches X"
