# Workflow-Coordination Skill Plan

**Status:** Planning — pending state machine gap resolution before implementation
**Skill type:** Technique (guided judgment, context-dependent decisions)
**Primary agent:** Orchestrator
**Token budget:** ~3,000 tokens (orchestrator's one always-loaded skill body)

---

## 1. State Machine Analysis

### What the System SM (`system.json`) Automates

The system state machine has 20 states and 27 transitions. It handles:

| What | How | Orchestrator Involvement |
|------|-----|------------------------|
| Workflow phase progression | Guards: `context_packets_exist`, `plan_json_valid`, `all_coder_tasks_complete`, `remaining_phases_exist`, etc. | None — infrastructure blocks invalid transitions |
| Agent spawning triggers | Actions: `spawn_explorer`, `spawn_strategist`, `spawn_coders`, `spawn_tester`, `spawn_auditor_arbitration`, `spawn_auditor_hardening` | SM says WHEN to spawn. **Orchestrator decides HOW** — which tasks, what context, parallel vs sequential |
| User approval gates | `requires_user_approval: true` on plan review, phase approval, final audit | Infrastructure blocks. **Orchestrator presents the options** |
| Remediation cycling | `increment_remediation_cycle`, `remediation_cycle_under_ceiling` guard, max cycle cap | **Orchestrator decides** which coders get which failure reports |
| Arbitration trigger | `remediation_stalled_or_ceiling` → `spawn_auditor_arbitration` | Automated — but **orchestrator interprets** the ruling |
| Error recovery from exploration/strategy | ERROR state with `reset` or `retry_exploration` transitions | Automated — but **orchestrator decides** which path to offer user |

### What Per-Agent State Machines Automate

| Machine | Key Automation | What's NOT Automated (Orchestrator Judgment) |
|---------|---------------|---------------------------------------------|
| `coder.json` | Full TDD lifecycle, write restrictions (`write_globs`), red retry limits (max 2), green retry limits (max 5), auto-handoff on context pressure, quality gate, invariant check | Task selection, worktree isolation config, context provision |
| `tester.json` | Scenario lifecycle, progressive escalation (cycle 1=light info_request, cycle 2=structured bug_report, cycle 3+=peer coordination), user approval for scenarios | What spec context tester receives (must be blind to implementation) |
| `auditor-task.json` | Review lifecycle, max critique cycles (3) then ESCALATED | What to do when ESCALATED — orchestrator decides |
| `auditor-phase.json` | Full audit lifecycle (artifact review → independent exploration → ruling → report) | Nothing — fully self-contained |
| `explorer.json` | Cache check, scope analysis, sub-agent dispatch, schema validation, error retry (max 1) | Nothing — but orchestrator decides WHEN to trigger re-exploration |
| `researcher.json` | Cache check, additional search loop (max 2), confidence-based synthesis | Nothing — fully self-contained |
| `strategist.json` | Design ingestion, granular user review checkpoints (phase breakdown → task breakdown → task detailing → test planning), JSON conversion with bidirectional validation, max revision limits | Nothing — fully self-contained with user interaction |

### Gaps: NOT in Any State Machine (Pure Orchestrator Judgment)

These are the areas the skill MUST cover because no infrastructure handles them:

1. **Re-exploration between phases** — `PHASE_COMMITTED → PHASE_ACTIVE` has `init_phase` action but no re-exploration logic. Orchestrator must check file overlap and decide.
2. **Task prioritization within a phase** — `spawn_coders` is an action but doesn't select which tasks or determine order.
3. **Parallel vs. sequential coder dispatch** — SM doesn't model parallelism at all. Orchestrator reads the plan DAG and decides.
4. **What happens after coder `SCRAP_RETRY` / `HANDOFF` / auditor-task `ESCALATED`** — SM has terminal states but no "what next" logic for the orchestrator.
5. **Info-request routing** — no SM for this. Orchestrator classifies (codebase vs external) and translates into task_assign.
6. **Scribe lifecycle** — SM has `commit_phase` action but no persistent scribe management across the workflow.
7. **State save/resume across sessions** — not modeled in any SM.
8. **Consecutive failure tracking** — SM tracks remediation cycles per phase, but NOT how many times the same task has been scrapped across fresh coder spawns.
9. **Tester blindness enforcement** — SM's `spawn_tester` fires the spawn, but content filtering of what the tester receives is purely orchestrator judgment.

### Potential State Machine Amendments

Before writing the skill, these gaps should be evaluated — some may belong in the SM rather than the skill:

| Gap | Candidate for SM? | Rationale |
|-----|-------------------|-----------|
| Re-exploration between phases | **Maybe** — could add a `RE_EXPLORING` state between `PHASE_COMMITTED` and `PHASE_ACTIVE` with a `skip_if_no_overlap` guard | Pro: makes re-exploration a formal phase. Con: skip logic requires judgment (file overlap analysis) that guards can't easily encode |
| Task prioritization | **No** — requires reading the plan DAG and interpreting dependency graphs. Not guard-encodable. | Judgment, not enforcement |
| Parallel coder dispatch | **No** — requires analyzing target_file overlap and presenting options to user. | Judgment, not enforcement |
| Post-SCRAP_RETRY recovery | **Maybe** — could add transitions from system SM that model "task failed, what next" | Pro: formalizes the recovery path. Con: decision (re-explore vs respawn vs escalate) is context-dependent |
| Post-ESCALATED recovery | **Maybe** — same as above | Same trade-offs |
| Info-request routing | **No** — classification requires understanding the content of the request | Judgment, not enforcement |
| Scribe lifecycle | **No** — persistent agent management across multiple phases is coordination, not state | Could be a separate lightweight SM, but probably over-engineering |
| State save/resume | **No** — cross-session concern, SM is per-session | Not SM territory |
| Consecutive failure tracking | **Maybe** — could add a counter guard: `task_scrap_count < 2` | Pro: hard limit is enforceable. Con: the "what to do" at the limit is still judgment |
| Tester blindness | **No** — SM can't enforce what content is in a spawn's context | Judgment about what to include/exclude |

---

## 2. Summary

`workflow-coordination` teaches the orchestrator the judgment needed to **execute within the state machine framework**: the SM says WHEN things happen (via guards and transitions), this skill teaches HOW to make them happen well. It fills the gap between "the guard says `spawn_coders`" and "spawn 2 parallel coders for tasks 1+3, give each the right context, and handle it when one hits SCRAP_RETRY."

---

## 3. Justification

The V2 architecture shifts enforcement to infrastructure (state machines, hooks, write_globs). But at every action-bearing transition, the orchestrator must make judgment calls:

- **`spawn_coders`** → Which tasks? Parallel? What context per coder?
- **`init_phase`** → Re-explore first? Skip if no file overlap?
- **`increment_remediation_cycle`** → Route which failures to which coders?
- **Coder reaches terminal `SCRAP_RETRY`** → Re-explore affected files? Spawn fresh coder with what context? Escalate to user?
- **Auditor-task reaches `ESCALATED`** → Fresh coder? User involvement? Different approach?

Without this skill, the orchestrator knows WHERE it is in the workflow (the SM tells it) but not HOW to act well at each decision point.

---

## 4. Proposed Frontmatter

```yaml
---
name: workflow-coordination
version: 1-0-0
triggers:
  - agent_role: orchestrator
    conditions: when making coordination decisions during workflow execution
description: >
  Use when the orchestrator needs to make coordination decisions that the
  system state machine doesn't encode: task selection and prioritization,
  coder spawn configuration (parallel/sequential, context provision),
  failure recovery after coder SCRAP_RETRY/HANDOFF or auditor ESCALATED,
  re-exploration decisions between phases, info-request routing, remediation
  management, and scribe lifecycle. Activates at workflow start, stays
  active throughout.
  Do NOT use for: planning decisions (phase-planning), sub-agent dispatch
  mechanics (sub-agent-delegation), handoff document writing (handoff-protocol),
  debugging (debugging).
---
```

---

## 5. Body Structure (Section by Section)

### Section 1: Core Principle (~3 lines)

"The state machine controls WHEN things happen. You control HOW they happen well. Every spawn action, every failure recovery, every phase transition has a judgment component that infrastructure cannot encode."

### Section 2: Quick Reference Table (~30 lines)

Maps the 12 most common orchestrator decision points to actions, explicitly tied to SM states/transitions:

| SM State/Transition | Decision Point | Action |
|---------------------|---------------|--------|
| `PHASE_ACTIVE` → `spawn_coders` | Which tasks, parallel? | Read plan DAG → Task Prioritization |
| `PLAN_READY` → `spawn_tester` | What context for tester? | Specs only, blind to implementation |
| Coder reaches `SCRAP_RETRY` | What next? | Failure Recovery table |
| Coder reaches `HANDOFF` | Spawn replacement? | Resume with session log per handoff-protocol |
| Auditor-task reaches `ESCALATED` | What next? | Failure Recovery table |
| `PHASE_COMMITTED` → `init_phase` | Re-explore? | Re-Exploration Protocol |
| `PHASE_REMEDIATION` active | Which coder gets which failure? | Remediation Routing |
| Coder sends `info_request` | Explorer or researcher? | Info-Request Routing |
| `PHASE_REPORT` → user approval | What to present? | Phase summary + options |
| Context pressure on orchestrator | Save state? | State Management |
| Phase committed | Dispatch scribe? | Scribe Lifecycle |
| `PHASE_ARBITRATION` ruling received | How to apply? | Apply ruling per auditor direction |

### Section 3: Task Prioritization (~35 lines)

When the system SM fires `spawn_coders` at `PHASE_ACTIVE → PHASE_IMPLEMENTATION`:

- Read the plan's task list for the current phase
- Build the dependency subgraph (only this phase's tasks)
- Identify unblocked tasks (no incomplete predecessors)
- Check `target_files` overlap between unblocked tasks (overlap → sequential only)
- Present parallel option to user when 2+ tasks are file-independent
- Priority order: tasks with downstream dependents first > leaf tasks
- Default: sequential in plan order. Parallel only if user opts in.

### Section 4: Agent Spawn Configuration (~40 lines)

The SM defines WHEN to spawn (transition actions). This section teaches HOW to configure each spawn:

| SM Action | Agent | Model | Context to Provide | Key Constraint |
|-----------|-------|-------|-------------------|----------------|
| `spawn_explorer` | Explorer | Sonnet | Query scope, existing context paths | Ephemeral — fresh per query |
| `spawn_strategist` | Strategist | Opus | Design doc, context packets, research | Persistent within planning |
| `spawn_coders` | Coder(s) | Opus | Plan chunk, context packets, session log path | One task per coder, worktree-isolated |
| `spawn_tester` | Tester | Opus | Design doc, plan, API signatures — **NO implementation code** | Persistent within phase, blind to impl |
| `spawn_auditor_arbitration` | Auditor | Opus | Both perspectives + plan requirement | Single-pass ruling |
| `spawn_auditor_hardening` | Auditor | Opus | All phases, full codebase, scenario results | Hardening mode |

All teammates: `mode: "bypassPermissions"`. Models are fixed — no runtime scoring.

**Tester blindness**: The SM's `spawn_tester` fires at `init_phase`. Tester receives design doc, plan specs, and public API signatures ONLY. Never coder session logs, implementation code, or internal function names. This is the orchestrator's responsibility when configuring the spawn — the SM can't enforce content filtering.

### Section 5: Failure Recovery (~55 lines)

Decision table for terminal/escalated agent states:

| Agent Terminal State | SM Does | Orchestrator Judgment |
|---------------------|---------|----------------------|
| Coder `SCRAP_RETRY` (red retries exhausted) | Coder writes handoff to `.claude/handoffs/`, notifies orchestrator | Read handoff. If test design was fundamentally wrong: re-explore the task's target area, then spawn fresh coder with handoff + fresh context. If coder's approach was wrong but tests were fine: spawn fresh coder with handoff only. If 2 consecutive scraps on same task: escalate to user. |
| Coder `HANDOFF` (context pressure) | Coder writes handoff, notifies orchestrator | Spawn fresh coder with session log + handoff doc. No re-exploration needed (context is current, coder just ran out of window). |
| Auditor-task `ESCALATED` (3 critique cycles exhausted) | Auditor notifies orchestrator of deadlock | Read audit findings. If auditor's concerns are architectural (wrong abstraction, wrong pattern): scrap task, re-plan with strategist input. If concerns are localized (edge cases, error handling): spawn fresh coder with auditor's full critique as context. If unclear: present both options to user. |
| Explorer `ERROR` → `abort` | Explorer notifies requester failed | Retry with narrower scope. If retry also fails: inform requester (coder/strategist) of the gap. They proceed with explicit uncertainty. |
| Researcher low-confidence termination | Researcher writes low-confidence research | Forward to requester with the low-confidence flag. Requester decides whether to proceed or request deeper research. |

**Consecutive failure tracking**: Track how many times the same task has been scrapped/handed-off. After 2 consecutive scraps on the same task, escalate to user — the task spec may be wrong.

### Section 6: Re-Exploration Protocol (~30 lines)

NOT in the system SM — purely orchestrator judgment during `init_phase` action.

After `PHASE_COMMITTED → PHASE_ACTIVE` (next phase starts):

| Check | Action |
|-------|--------|
| Intersect previous phase's modified files with next phase's `target_files` and `reference_files` | If overlap exists → spawn incremental explorer before coders |
| Previous phase created new types/APIs that next phase consumes | Always re-explore — new constructs need to be in context packets |
| Previous phase only modified internal logic, next phase touches different modules | Skip re-exploration — context is still valid |
| After parallel coders complete (multiple tasks merged) | One re-exploration covering all changed files before next wave |
| After a SCRAP_RETRY where the issue was stale context | Always re-explore the affected area before respawning |

Use think tool to evaluate overlap — this is a mandatory `requires_think` decision point.

### Section 7: Remediation Routing (~30 lines)

During `PHASE_REMEDIATION` (SM manages cycle count and arbitration trigger):

The tester's SM handles progressive escalation automatically (cycle 1=light, cycle 2=structured, cycle 3+=peer coordination). The orchestrator's job:

- **Route failure reports to the right coder**: Match each scenario failure to the task whose `target_files` contain the likely source. If ambiguous, route to the coder whose task's `requirements` most closely match the failing scenario's spec reference.
- **After coder fixes and merges**: Trigger tester re-execution (SM guard `remediation_fixes_merged` handles this).
- **If remediation is converging** (fewer failures each cycle): Continue up to 5 cycles.
- **If remediation is stalled** (same failures persist): SM guard `remediation_stalled_or_ceiling` triggers arbitration. Orchestrator configures the arbitration auditor spawn with both perspectives.

### Section 8: Info-Request Routing (~25 lines)

No SM for this — purely orchestrator judgment.

When a coder sends an `info_request` during `PHASE_IMPLEMENTATION`:

| Question Type | Route To | How to Detect |
|---------------|----------|--------------|
| Codebase structure, types, patterns | Explorer | References files, modules, or patterns in the repo |
| External library API, docs, behavior | Researcher | References third-party libraries, APIs, or external services |
| Plan interpretation, requirement clarity | Orchestrator answers directly or asks user | References plan fields or design doc ambiguity |
| Domain knowledge | User | References domain-specific concepts not in codebase or docs |

Translation: Map rich `info_request` fields (`what_we_need`, `why_we_need_it`, `relevant_context`) into targeted `task_assign` instructions. Track dispatch→requester mapping for response routing.

### Section 9: Scribe Lifecycle (~20 lines)

SM has `commit_phase` action but no persistent scribe management.

- Spawn scribe at workflow start (immediately after TeamCreate), Haiku model
- Scribe goes idle until needed
- After EACH user approval (`PHASE_REPORT → PHASE_COMMITTED`, plus exploration/planning approvals): send `task_assign` to idle scribe via SendMessage
- Scribe commits, goes idle again
- On scribe context pressure: shut down old, spawn new with commit history summary
- What triggers scribe: user approval of phase/exploration/planning output
- What does NOT trigger scribe: intermediate steps, info_request cycles, partial progress

### Section 10: State Management (~15 lines)

Not in any SM — orchestrator-only concern.

- At 70% context pressure: save to `.claude/temp/orchestrator-state.json`
- Contents: feature name, current system SM state, completed phases, plan path, context paths, scribe commit history, active agent IDs, resume instructions
- On resume: new session reads state file, reads plan, checks session logs for in-progress work, presents summary and options
- Spawn fresh scribe on resume

### Section 11: Critical Rules (~20 lines)

- **Tester is blind.** When configuring `spawn_tester`, provide specs only. Never include coder logs, implementation code, or internal function names. The SM spawns the tester — you control what it sees.
- **Re-explore before respawning after scrap.** A scrapped task means the coder's context was wrong or the approach was wrong. If context was wrong, re-explore. If approach was wrong, include the handoff with what didn't work.
- **2 consecutive scraps = escalate.** After 2 scraps on the same task, the task spec may be flawed. Escalate to user, don't spawn a third coder.
- **Track remediation convergence.** Fewer failures each cycle = continue. Same failures = stalled. The SM caps cycles, but you decide whether convergence warrants extending.
- **Route info-requests by content, not by agent.** A coder asking about an API doesn't always need a researcher — if the API is internal, route to explorer.
- **Never configure parallel coders with overlapping `target_files`.** Check before spawning. Overlapping writes cause merge conflicts that waste entire coder sessions.

### Section 12: References Pointer (~3 lines)

Links to `references/patterns.md` and `references/anti-patterns.md`.

**Estimated total: ~330 lines body + ~20 lines frontmatter = ~350 lines (~2,700 tokens)**

---

## 6. V1 Content Disposition

| V1 Content | Action | Rationale |
|------------|--------|-----------|
| Prerequisite checks | **Drop** | System SM guards (`context_packets_exist`, `plan_json_valid`, etc.) handle this entirely |
| Model assignments | **Absorb** — one table in Section 4 | SM has `spawn_*` actions but not model selection. Fixed table. |
| Workflow phases overview | **Drop** | System SM IS the workflow phases. Redundant. |
| Decision tree | **Drop** | System SM transitions ARE the decision tree |
| Parallel teammate protocol | **Absorb** — Section 3 (task prioritization) | SM doesn't model parallelism — orchestrator judgment |
| Auto re-exploration | **Absorb** — Section 6 (SM gap, not automated) | `init_phase` action exists but has no re-exploration logic |
| Persistent scribe lifecycle | **Absorb** — Section 9 (SM has `commit_phase` but no scribe management) | Orchestrator manages the persistent instance |
| Message dispatch tracking | **Absorb** — Section 8 (info-request routing) | No SM for this |
| State save/resume | **Absorb** — Section 10 (not in any SM) | Cross-session concern |
| Code examples (Python/bash) | **Drop** | V2 skills teach judgment, not syntax |

---

## 7. New Content (Not in V1)

| New Content | Source | Why Needed |
|-------------|--------|-----------|
| Failure recovery decision tree | SM terminal states (`SCRAP_RETRY`, `HANDOFF`, `ESCALATED`) that have no "what next" | SM ends the agent's lifecycle but doesn't tell orchestrator what to do next |
| Remediation routing | System SM `PHASE_REMEDIATION` state — orchestrator decides coder-failure mapping | Tester SM auto-escalates reports, but orchestrator routes them to the right coders |
| Consecutive failure tracking | Not in any SM | Prevents infinite respawning on broken task specs |
| Tester blindness enforcement | `spawn_tester` is an SM action, but content filtering is orchestrator judgment | SM can't enforce what context is provided — only the orchestrator can |

---

## 8. Reference Files

| File | Contents | When Loaded |
|------|----------|-------------|
| `references/patterns.md` | Parallel coordination examples, info-request translation templates, state save template, re-exploration overlap check examples | When orchestrator needs a specific pattern |
| `references/anti-patterns.md` | WRONG/RIGHT pairs: spawning coder without re-exploration after scrap, giving tester implementation code, force-parallel on dependent tasks, ignoring remediation convergence | When orchestrator encounters a coordination failure |

No `scripts/` or `specializations/` — workflow coordination is domain-agnostic.

---

## 9. Boundaries

| Topic | Handled By |
|-------|-----------|
| Workflow state transitions, guards, phase progression | System state machine (`system.json`) |
| TDD enforcement, write restrictions, retry limits | Coder state machine (`coder.json`) |
| Scenario progressive escalation, user approval for scenarios | Tester state machine (`tester.json`) |
| Audit critique cycles, max retries, escalation | Auditor-task state machine (`auditor-task.json`) |
| Phase audit lifecycle | Auditor-phase state machine (`auditor-phase.json`) |
| Design-to-plan conversion, user review checkpoints | Strategist state machine (`strategist.json`) |
| Exploration lifecycle, cache checks, schema validation | Explorer state machine (`explorer.json`) |
| Research lifecycle, confidence-based synthesis | Researcher state machine (`researcher.json`) |
| Strategic planning decisions | `phase-planning` skill |
| Sub-agent dispatch mechanics | `sub-agent-delegation` skill |
| Handoff document writing/reading | `handoff-protocol` skill |
| Debugging within a coder session | `debugging` skill |
| Agent identity rules, communication hard rules | Orchestrator agent spec (`orchestrator.md`) |

---

## 10. Open Questions (Resolve Before Implementation)

### SM Gaps That May Need Amendments

1. **Re-exploration state**: Should the system SM add a `RE_EXPLORING` state between `PHASE_COMMITTED` and `PHASE_ACTIVE`, with a guard `context_stale_for_next_phase`? This would formalize re-exploration as a workflow phase rather than leaving it as orchestrator judgment during `init_phase`.

2. **Post-failure recovery transitions**: Should the system SM model what happens after a coder's `SCRAP_RETRY`? Currently the system SM doesn't react to individual agent terminal states — it only knows `all_coder_tasks_complete`. The gap: if one coder scraps while another succeeds, the system SM can't distinguish "task 1 complete, task 2 scrapped" from "both tasks complete."

3. **Consecutive failure counter**: Should a guard `task_scrap_count < 2` be added to whatever transition spawns a fresh coder for a scrapped task? This would enforce the "2 scraps = escalate" rule at the infrastructure level rather than trusting the skill.

4. **Info-request routing**: Is this purely orchestrator judgment, or should there be a lightweight SM or hook that classifies info_requests and suggests routing? The volume of info_requests in a parallel coder phase could be high.

5. **Scribe as a state machine**: Should the persistent scribe have its own SM? Currently it's the only agent without one. Its lifecycle (spawn → idle → task_assign → commit → idle → ... → shutdown) is simple but formalized.

### Skill Content Questions

1. **Tester model**: The spawn config table says Opus for tester. The V1 skill said tester model is flexible. Confirm: is tester always Opus?

2. **Parallel coder limit**: Is there a hard limit on concurrent coders? The system SM's `spawn_coders` doesn't encode one. Should the skill recommend a max (e.g., 3)?

3. **Remediation convergence tracking**: The SM has `remediation_cycle_under_ceiling` guard with a cap. What IS the ceiling? Is it configurable? The skill says "up to 5 cycles if converging" — is 5 the right number?
