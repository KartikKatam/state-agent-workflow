# V2 Agent Architecture — Handoff Document

**Last updated:** 2026-03-16
**Status:** Task #2b in progress — Explorer, Researcher, Planner, Coder complete. Tester next, then Auditor.

---

## How to Use This Handoff

You are picking up an interactive design session. Read the files listed in the **Required Reading** section below BEFORE engaging the user. Then continue with the Tester section of `TEAMMATE-DIRECTOR-PATTERNS.md`, following the design method and style described in this document.

### Required Reading (in order)

| # | File | Why |
|---|------|-----|
| 1 | `new_claude/agents/AGENT-ARCHITECTURE.md` | Source of truth — agent roster, invariants, dispatch rules, sub-agent definitions. §3.6 (Tester), §3.7 (Auditor), §4.2 (scenario-writer, test-writer sub-agents), §6.1 (invariants) |
| 2 | `new_claude/agents/BASE-DIRECTOR-PATTERN.md` | Universal lifecycle, 17 extension points you'll fill in. §7 is the extension point API. §3.4 has the OVERRIDE mechanism (used by Planner and Coder). |
| 3 | `new_claude/agents/TEAMMATE-DIRECTOR-PATTERNS.md` | **The output file.** Sections 1-4 (Explorer, Researcher, Planner, Coder) are complete. Study Planner (§3) and Coder (§4) closely — they're the most relevant precedents for the Tester's complexity level. |
| 4 | `state-machines/tester.json` | V1 Tester state machine — 11 states, 17 transitions. This is the foundation for the V2 Tester, NOT something to replace wholesale. |
| 5 | `new_claude/agents/SUB-AGENT-SPECS.md` | Sub-agent detailed specs. §5 (test-writer), §7 (scenario-writer) are the Tester's sub-agents. Also read the Common Conventions section (validation layers, quality gate hooks, daemon state tracking). These were updated this session. |
| 6 | `state-machines/strategist.json` | V1 Planner state machine — reference for how V1 review cycles were preserved in V2. The Planner section used this as its foundation. |

### Files Modified This Session

| File | What changed |
|------|-------------|
| `TEAMMATE-DIRECTOR-PATTERNS.md` | Added Planner (§3) and Coder (§4) sections — ~900 lines |
| `SUB-AGENT-SPECS.md` | Rewrote validation layers (structural hook vs semantic parent review), added quality gate hooks section, added daemon state tracking note, updated debugger with progress loops |

---

## Completed Work

### Task #1: Agent Architecture (Complete)

**Output:** `new_claude/agents/AGENT-ARCHITECTURE.md`

Defined the complete agent roster through interactive design session:
- **7 Teammates:** Orchestrator, Explorer, Researcher, Planner, Coder, Tester, Auditor
- **9 Sub-Agents:** codebase-scout, research-scout, implementer, test-writer, scenario-writer, audit-checker, debugger, plan-checker, optimizer
- **6 Architectural Invariants** enforced by hooks and state machines
- **2-Level Inheritance Model** for sub-agent definitions (base-agent → base-code-agent → specific)
- **Dispatch rules, skill assignments, communication architecture, extension protocol**
- **15 design decisions** with rationale and rejected alternatives

### Task #2a: Base Director Pattern (Complete)

**Output:** `new_claude/agents/BASE-DIRECTOR-PATTERN.md`

Defined the universal lifecycle that ALL teammates follow:
- **5 lifecycle phases:** Ingress → Deliberation → Delegation → Synthesis → Egress
- **14 base states** with transitions, guards, think prompts, write restrictions
- **Orchestrator-mediated spawn protocol** (teammate writes delegation JSON → orchestrator spawns)
- **General sub-agent mechanism** (`sub_agent_type: "general"` + `skills_to_load`)
- **4-path error recovery:** RETRY, PARTIAL_SYNTHESIS, HANDOFF, ABORT
- **17 extension points** marked with `[EXT: ...]` for per-teammate customization
- **Delegation JSON template** and spawn request message schema
- **Complete base state machine JSON** in Appendix A

**Key decision: Option A (design reference, not runtime inheritance).** BASE-DIRECTOR-PATTERN.md is consulted when WRITING teammate definitions. It is NOT loaded by agents at runtime. Each teammate .md file is self-contained.

### Task #2b: Per-Teammate Director Patterns (In Progress)

**Output:** `new_claude/agents/TEAMMATE-DIRECTOR-PATTERNS.md`

| Teammate | Status | Complexity | Notes |
|----------|--------|------------|-------|
| Explorer (§1) | **Complete** | Moderate | Incremental V2 enhancement over V1. Parallel scout dispatch. |
| Researcher (§2) | **Complete** | Moderate | Similar — incremental enhancement. MCP routing. |
| Planner (§3) | **Complete** | High | V1 cycle preserved, V2 enhancements layered on. OVERRIDE extension. 3 iterations to get right. |
| Coder (§4) | **Complete** | Very High | Radical role redefinition — 24-state executor → 12-state event-loop coordinator. Most design discussion of any teammate. |
| Tester (§5) | **Next** | High | See "Tester Starting Point" below |
| Auditor (§6) | Pending | High | |

---

## Design Method — How the User Works

**This is critical. The user has rejected 5+ proposals across sessions for not following these principles.**

### Interaction Style

1. **Present the design back with reasoning.** Don't just write the section — explain what you designed and WHY. Walk through the V1→V2 mapping, the key decisions, the nuances. The user wants to understand the design, not just approve it.

2. **Visual state machines.** Every teammate gets an ASCII state diagram with annotations showing decision points, think prompts, and flow. The user consistently asks for visual representation.

3. **V1 is the foundation, not the enemy.** The V1 state machines (`state-machines/*.json`) encode real operational knowledge. Don't throw them away or "consolidate" them into abstract frameworks. Map V1 states to V2 equivalents. Enhance behaviorally, not structurally, unless the role fundamentally changed (only the Coder was a full redefinition).

4. **Incremental enhancement vs role redefinition.** For each teammate, the first question is: "how much changes from V1 to V2?" Explorer, Researcher = incremental. Planner = mostly incremental (V1 cycle preserved, V2 behavioral enhancements). Coder = radical redefinition. Assess this upfront.

5. **Iterate, don't assume.** The user will push back on proposals that:
   - Over-consolidate (merging distinct review stages into generic loops)
   - Add artificial states (prepending CONTEXT_LOADING when the orchestrator already provides context)
   - Make sub-agents unreasonably autonomous (they should be directed by precise delegation prompts)
   - Ignore the existing state machine's nuance

### Design Principles Established This Session

6. **Progress-based stall tracking, not hard caps.** V1 used `max_occurrences` on revision transitions. V2 uses stall counters that reset on progress. "If progress was made, the count should reset. This is only to stop infinite loops where progress isn't happening."

7. **Cold-start primary, resume as optimization.** Sub-agents write `decisions_made` and `carry_forward` in returns. A fresh replacement gets the original delegation prompt + previous return. Resume is a bonus, never a requirement.

8. **Distributed deliberation.** Think prompts at natural decision points only. No frontloaded "think about everything" phase. No think wrapping structured messages that already have protocol schemas.

9. **Orchestrator role reduced.** Teammates message each other directly for cross-domain communication. Orchestrator only involved for sub-agent spawning and system-level decisions. Dashboard at `.claude/state/system-dashboard.json`.

10. **Two-layer validation for sub-agents.** SubagentStop hook = structural (schema, required fields, gate results present, task_id match). Parent semantic review = "did it actually do the right thing?" Hooks can't assess semantic correctness.

11. **Quality gate hooks: Option B.** Universal lint/type PostToolUse hook on Write/Edit (phase-independent). Pytest is NOT a hook — sub-agent interprets results per phase (red = must fail, green = must pass).

12. **Sub-agents do NOT report state to daemon.** Auditability via structured returns + ephemeral state files + parent tracking. Live granular state tracking is not worth the overhead for short-lived sub-agents.

13. **Debugger makes changes directly + progress loops.** Iterative fix cycle, stall threshold of 3 (shorter leash — it's already second line of defense).

14. **Termination logs.** Every teammate writes a termination log before exiting that captures key decisions, reasonings, implementation changes, and context for continuity.

15. **Worktree hierarchy.** Task worktrees branch off primary. Multiple sub-agents share same task worktree (safe via INV-1). Sequential phases, parallel tasks within waves.

### Anti-Patterns That Got Proposals Rejected

| What NOT to do | What happened when it was tried |
|----------------|-------------------------------|
| Consolidate V1 review stages into abstract loops | "No, I hate this, this is really wrong. The planner was going to use the currently existing cycle." |
| Prepend artificial ingress states before V1's cycle | "Why are we trying to change the relationship at the beginning? Doesn't the orchestrator send that in its delegation prompt?" Created a 23-state monster. |
| Propose hook-based sub-agent dispatch | "I don't like hook based subagent dispatch it mechanises the freeform of the multiple state machines." |
| Design around sub-agent resume reliability | "I don't want to rely on it at this scale. If a new sub-agent needs to be spawned to continue where the old one left off it should be easy to do so." |
| Force deliberation on structured messages | "I don't want forced deliberation when communication already has its own schema, validation, and protocol defined by a skill." |
| Over-complicate worktree hierarchy (3 levels) | User simplified to task worktrees off primary branch. |

---

## Tester Starting Point

### What We Know

**From AGENT-ARCHITECTURE.md §3.6:**
- Opus teammate, per-phase persistence
- Dispatches: scenario-writer, test-writer
- Blind to implementation (INV-1 — never reads source code, only public API specs and design docs)
- Designs eval scenarios WITH the user (direct work, not delegated — this reasoning is the Tester's core value)
- After user approval: dispatches scenario-writer for scenario test code
- Validates scenarios (dry-run) → dispatches test-writer if broken
- Executes scenarios against merged code
- Reports results → messages Coder directly or escalates to Auditor
- Skills: ptc-sandbox, sub-agent-delegation/tester-routes, test-architecture, scenario-testing

**From V1 state machine (`state-machines/tester.json`):**
- 11 states, 17 transitions — already well-structured
- User-approval gate for scenarios (SCENARIO_PLANNING → SCENARIOS_APPROVED)
- Build → validate → re-build loop for scenario code
- Plan revision handling (SCENARIOS_READY → back to SCENARIO_PLANNING if plan changed)
- Infrastructure error handling with RETRY/ESCALATE/HANDOFF
- Daemon-mediated re-execution triggers (IDLE → SCENARIO_EXECUTION when `remediation_fixes_merged`)
- Progress-based stall tracking on execution cycle (PROGRESS resets counter, STALLED increments, threshold 5)
- Think prompts at SCENARIO_PLANNING and SCENARIO_REPORTING

**From SUB-AGENT-SPECS.md §5 (test-writer) and §7 (scenario-writer):**
- scenario-writer: writes Pass D eval scenarios from design docs, blind to implementation AND unit tests
- test-writer: when dispatched by Tester, repairs broken test infrastructure (not same as Coder dispatching for red phase)
- Both follow cold-start/resume protocol, SubagentStop validation

### Why the Tester Needs Planner-Level Granularity

The user explicitly stated: "The tester should be as granular as the planner as it is in charge of planning and writing the scenario tests for each phase and the whole design document."

This means:
- The Tester's SCENARIO_PLANNING phase is analogous to the Planner's review cycle — it involves real intellectual work with the user, not just approving a list
- Scenario design requires understanding the entire design document, cross-task interactions, and behavioral contracts
- The Tester IS a planner for the test domain — it plans what to test, how to test it, at what tiers
- Sub-agent delegation (scenario-writer, test-writer) follows after the planning is done, similar to how Planner dispatches plan-checker after review gates

### V2 Assessment: Incremental Enhancement (Not Redefinition)

Unlike the Coder, the Tester's V1 role IS its V2 role. It was already a director that designs scenarios as direct work and delegates code writing. The V2 changes are:

1. **Sub-agent lifecycle alignment** — scenario-writer and test-writer follow cold-start/resume with SubagentStop validation
2. **Direct inter-teammate messaging** — Tester messages Coder directly for failure reports (not through orchestrator)
3. **Quality gate hooks** — universal lint/type hook on sub-agent writes (Option B)
4. **Worktree integration** — scenarios execute in the phase branch after Coder merges
5. **Termination log** — same pattern as Planner and Coder
6. **Richer scenario planning** — the SCENARIO_PLANNING phase should be as detailed as the Planner's review cycle, with think prompts, user interaction, tier analysis

### Extension Points to Fill In

Same 15 extension points as every teammate (see the template in `TEAMMATE-DIRECTOR-PATTERNS.md` — study how §3 Planner and §4 Coder filled them in). The Tester's V1 state machine maps directly to most of these — the work is fitting it into the extension point framework and adding V2 enhancements.

---

## Full Roadmap

| # | Task | Status | Output |
|---|------|--------|--------|
| 1 | Agent architecture | **Complete** | `AGENT-ARCHITECTURE.md` |
| 2a | Base director pattern | **Complete** | `BASE-DIRECTOR-PATTERN.md` |
| 2b | Per-teammate director patterns | **In Progress** (4/6) | `TEAMMATE-DIRECTOR-PATTERNS.md` |
| 3 | State machine alignment | Pending | Updated `state-machines/*.json` |
| 4 | Sub-agent delegation skill rework | Pending | Reworked `new_claude/skills/sub-agent-delegation/` |
| 5 | Delegation prompting + return schemas | Pending | Reworked `new_claude/skills/delegation-prompts/` |

---

## User Design Preferences (All Sessions — Cumulative)

### From Task #1 (agent architecture)

1. **Practical, not pigeonholed** — Sub-agents are versatile within their domain. Specialization comes from the delegation prompt, not narrow definitions.
2. **Directors, not dogmatically** — Teammates do minor work directly (< 3 tool calls, no judgment). Don't spawn sub-agents for trivial work.
3. **Test/code isolation is non-negotiable (INV-1)** — Read both sides → can't edit either. Prevents test overfitting.
4. **Strict domain separation (INV-2)** — Need work outside your domain → message the specialist. No borrowing sub-agents.
5. **Auditor: root-cause analyst, not blame assigner** — Arbitration is diagnostic, not adversarial.
6. **Debugger edits code** — Investigates from symptoms, applies surgical fixes. Never reads test code.
7. **Optimizer in ephemeral worktree** — Compare against baseline, merge if worthwhile, discard if not.
8. **Don't design around 200K bug** — Optimize for token efficiency, but expect the context limit to be fixed.
9. **Conditional agents deserve own identity** — Led to scenario-writer as distinct from test-writer.
10. **Eliminate redundant agents** — "How is this different enough to justify its own definition?"

### From Task #2a (base director pattern)

11. **Option A over B** — Design reference doc, not runtime inheritance.
12. **Enforcement via hooks + state machines** — Markdown guides reasoning; hooks enforce constraints.
13. **General sub-agents are a pressure valve** — Any teammate can request one for edge cases, not a primary delegation path.
14. **Skills are capability modules, not lifecycle definitions** — Skills teach HOW. The base director pattern defines WHEN and in WHAT ORDER.

### From Task #2b (per-teammate patterns — this session)

15. **V1 state machines are the foundation** — Enhance behaviorally, not structurally. Map V1→V2, don't replace.
16. **Progress-based stall tracking** — Counters reset on progress, increment on stall. Replaces hard `max_occurrences`.
17. **Cold-start primary** — Sub-agents designed for fresh spawn. Resume is optimization, never requirement.
18. **Distributed deliberation** — Think prompts at natural decision points, not frontloaded or forced on structured messages.
19. **Orchestrator role reduced** — Direct teammate messaging. Orchestrator for spawning + system decisions only.
20. **Two-layer sub-agent validation** — Hook = structural, parent = semantic.
21. **Quality gates: Option B** — Universal lint/type hook. Pytest interpretation is sub-agent's job (phase-dependent).
22. **No daemon state for sub-agents** — Returns + ephemeral state files + parent tracking = sufficient auditability.
23. **Debugger has progress loops** — Stall threshold 3. Makes direct changes.
24. **Termination logs** — Every teammate writes one before exiting.
25. **Tester = Planner-level granularity** — It plans scenario tests for the whole design doc. Not a lightweight approval gate.

---

## Files Created/Modified Across Sessions

| File | Session | Purpose |
|------|---------|---------|
| `new_claude/agents/AGENT-ARCHITECTURE.md` | Task #1 | Source of truth for agent roster |
| `new_claude/agents/BASE-DIRECTOR-PATTERN.md` | Task #2a | Universal teammate lifecycle design reference |
| `new_claude/agents/TEAMMATE-DIRECTOR-PATTERNS.md` | Task #2b | Per-teammate extension point designs (output file) |
| `new_claude/agents/SUB-AGENT-SPECS.md` | Task #2b | Detailed sub-agent specs (written by another agent, updated this session) |
| `new_claude/agents/DELEGATION-RETURN-SCHEMAS.md` | Task #2b | Return schema definitions |
| `new_claude/agents/sub-agents/*.md` | Task #2b | Per-sub-agent definition files |
| `state-machines/tester.json` | V1 | V1 Tester state machine — foundation for V2 |
| `state-machines/strategist.json` | V1 | V1 Planner state machine — reference for review cycle preservation |
| `state-machines/coder.json` | V1 | V1 Coder state machine — reference for V1→V2 mapping |
| `new_claude/agents/TASK1-HANDOFF.md` | All | This handoff file |
