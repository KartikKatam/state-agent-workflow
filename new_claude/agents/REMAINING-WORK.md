# Sub-Agent Overhaul — Remaining Work

**Date:** 2026-03-16
**Status:** Design phase 100% complete. Implementation phase starting.
**Blocked on:** Nothing — all design documents complete (6/6 teammates, 9/9 sub-agents)
**Next step:** Write agent definition .md files. See `IMPLEMENTATION-HANDOFF.md` for full instructions.

---

## Design Docs Completed

| Document | Lines | Content | Status |
|----------|-------|---------|--------|
| `AGENT-ARCHITECTURE.md` | 1,072+ | 7 teammates, 9 sub-agents, 6 invariants, inheritance model, dispatch rules, 22 design decisions | **Complete** |
| `BASE-DIRECTOR-PATTERN.md` | 1,157 | 5 lifecycle phases, 14 states, 17 extension points for all teammates | **Complete** |
| `TEAMMATE-DIRECTOR-PATTERNS.md` | 2,628 | Per-teammate extension points: all 6 teammates (Explorer, Researcher, Planner, Coder, Tester, Auditor) | **6/6 complete** |
| `SUB-AGENT-SPECS.md` | 1,486 | All 9 sub-agents: 8 dimensions each + 3 appendices | **Complete** |
| `DELEGATION-RETURN-SCHEMAS.md` | 699 | 11 shared sub-models, 7 new return types, migration path | **Complete** |

**Design complete.** All 6 teammate patterns and 9 sub-agent specs are finished. Review fixes applied: plan-checker cold-start fallback, partial return handling, test-writer multi-parent clarification, stall threshold principle, INV-1 execution results clarification, general sub-agent restricted to orchestrator, orchestrator-mediated spawning documented as temporary constraint.

---

## 1. Agent Definition .md Files (16 files)

Each file = frontmatter (tools, skills, model, disallowedTools) + markdown body (behavioral instructions, return format, scope boundaries). Format reference: `.claude/agents/codebase-explorer.md` (V1).

### Teammate Definitions (7 files → `new_claude/agents/teammates/`)

| File | Source Design | Automatable | Notes |
|------|-------------|-------------|-------|
| `orchestrator.md` | AGENT-ARCHITECTURE.md §3.1 | **Low** | Behavioral mode of main session, not a standard teammate. Custom writing needed. |
| `explorer.md` | TEAMMATE-DIRECTOR-PATTERNS.md §1 | **High** | 17 extension points fully specified, V1 state mapping done |
| `researcher.md` | TEAMMATE-DIRECTOR-PATTERNS.md §2 | **High** | Same structure as Explorer |
| `planner.md` | TEAMMATE-DIRECTOR-PATTERNS.md §3 | **High** | Includes plan-checker dispatch cycle |
| `coder.md` | TEAMMATE-DIRECTOR-PATTERNS.md §4 | **High** | Most detailed section, director pattern fully specified |
| `tester.md` | TEAMMATE-DIRECTOR-PATTERNS.md §5 | **High** | Blocked on design completion |
| `auditor.md` | TEAMMATE-DIRECTOR-PATTERNS.md §6 | **High** | Blocked on design completion |

### Sub-Agent Definitions (9 files → `new_claude/agents/sub-agents/`)

| File | Source Design | Automatable | Notes |
|------|-------------|-------------|-------|
| `codebase-scout.md` | SUB-AGENT-SPECS.md §1 | **Very high** | All 8 dimensions defined |
| `research-scout.md` | SUB-AGENT-SPECS.md §2 | **Very high** | Broad read access (updated) |
| `plan-checker.md` | SUB-AGENT-SPECS.md §3 | **Very high** | Read-only, broad read access (updated) |
| `audit-checker.md` | SUB-AGENT-SPECS.md §4 | **Very high** | No Write/Edit tools, adversarial |
| `test-writer.md` | SUB-AGENT-SPECS.md §5 | **Very high** | INV-1: blind to source |
| `implementer.md` | SUB-AGENT-SPECS.md §6 | **Very high** | INV-1: blind to tests |
| `scenario-writer.md` | SUB-AGENT-SPECS.md §7 | **Very high** | Most isolated: blind to source AND unit tests |
| `debugger.md` | SUB-AGENT-SPECS.md §8 | **Very high** | Hypothesis-driven, ≥2 hypothesis log entries |
| `optimizer.md` | SUB-AGENT-SPECS.md §9 | **Very high** | Ephemeral worktree, compare-and-discard |

**Implementation approach:** Write one file at a time. User reviews each before proceeding. Sub-agent files are more mechanical (translate spec → frontmatter + prose). Teammate files require more judgment (integrate base pattern + extension points into coherent behavioral instructions).

---

## 2. State Machine JSON Updates (7-8 files in `state-machines/`)

| File | Change Scope | Source | Automatable | Blocked |
|------|-------------|--------|-------------|---------|
| `system.json` | Add wave-based execution: WAVE_DISPATCHED → WAVE_AWAITING → WAVE_REVIEW → WAVE_MERGE cycle within PHASE_IMPLEMENTATION | v2-subagent-restructuring.md §2.1, BASE-DIRECTOR-PATTERN.md | **Medium** | No |
| `coder.json` | **Major rewrite.** Replace direct TDD states with director states: DELEGATION_THINK → TEST_WRITER_DISPATCHED → TEST_REVIEW → IMPLEMENTER_DISPATCHED → AUDIT_REQUESTED, etc. | TEAMMATE-DIRECTOR-PATTERNS.md §4 | **Medium** | No |
| `explorer.json` | Replace direct exploration with delegation cycle. DELIBERATION replaces SCOPE_ANALYSIS. V1→V2 state mapping table provided. | TEAMMATE-DIRECTOR-PATTERNS.md §1 (V1 alignment table) | **High** | No |
| `researcher.json` | Same pattern as explorer — add delegation cycle, search strategy fallback preserved. | TEAMMATE-DIRECTOR-PATTERNS.md §2 | **High** | No |
| `strategist.json` | Add wave decomposition requirement. Add plan-checker dispatch states. | TEAMMATE-DIRECTOR-PATTERNS.md §3 | **High** | No |
| `tester.json` | Add scenario-writer dispatch. Add delegation think-gate. | TEAMMATE-DIRECTOR-PATTERNS.md §5 | **High** | **Yes — design pending** |
| `auditor-task.json` | Simplify for audit-checker sub-agent dispatch. | TEAMMATE-DIRECTOR-PATTERNS.md §6 | **High** | **Yes — design pending** |
| `auditor-phase.json` | Same simplification pattern as auditor-task. | TEAMMATE-DIRECTOR-PATTERNS.md §6 | **High** | **Yes — design pending** |

**Key principle:** Sub-agents do NOT have their own state machines. They are ephemeral — spawned, execute, return. Their lifecycle is tracked as states within the dispatching teammate's state machine (e.g., `TEST_WRITER_DISPATCHED` is a Coder state, not a test-writer state).

**Micro-commits at TDD boundaries:** Not separate scripts. Daemon triggers `git add + git commit` at specific state transitions: entering RED_VERIFIED, QUALITY_GATE_PASSED, AUDIT_APPROVED. Three natural checkpoints.

---

## 3. Schema Changes (Pydantic, in `schemas/`)

| File | Changes | Source | Automatable |
|------|---------|--------|-------------|
| `delegation_return.py` | Add 7 new return models (PlanVerificationReturn, AuditReturn, TestWritingReturn, ImplementationReturn, ScenarioWritingReturn, DebuggingReturn, OptimizationReturn). Add 11 shared sub-models. Update discriminated union from 5→12 types. | DELEGATION-RETURN-SCHEMAS.md (has literal Python code) | **Very high** |
| `agent_state.py` | Add `active_subagents: list[dict] = []`, `subagent_history: list[dict] = []`, `pending_delegation: str | None = None` | v2-subagent-restructuring.md §5.2 | **Very high** |
| `delegation_prompt.py` | Verify existing 5 types cover all sub-agent input contracts. May need sub-agent routing field. | SUB-AGENT-SPECS.md input contracts, Appendix C | **Medium** |

---

## 4. Skill Changes (in `new_claude/skills/`)

### New Skills (3)

| Skill | Content Source | Automatable | Notes |
|-------|--------------|-------------|-------|
| `base-agent/SKILL.md` | AGENT-ARCHITECTURE.md §4.1 Level 1 | **High** | PTC access, return protocol, INV-1, logging, escalation, ephemeral state file, cold-start |
| `base-code-agent/SKILL.md` | AGENT-ARCHITECTURE.md §4.1 Level 2 | **High** | Quality gate, write permissions, worktree context, iteration protocol |
| `workflow-coordination/SKILL.md` | Existing PLAN.md (24.6K) | **Medium** | Content exists, needs restructuring into skill format with frontmatter |

### Skill Updates (3)

| Skill | Changes | Automatable | Notes |
|-------|---------|-------------|-------|
| `delegation-prompts/SKILL.md` | Add per-teammate route tables mapping delegation types → sub-agent types. Add think MCP integration requirement. Add orchestrator-mediated spawn protocol. | **Medium** | Route tables defined in AGENT-ARCHITECTURE.md §5, but integrating into existing 24.7K skill needs judgment |
| `sub-agent-delegation/SKILL.md` | Add orchestrator-mediated spawn protocol. Add PTC container routing (register_parent for shared, skip for own). Add resume vs fresh-spawn decision tree. | **Medium** | Patterns defined, needs integration |
| `ptc-sandbox/SKILL.md` | Minor updates for sub-agent PTC patterns (container sharing). May not need changes — existing skill in `skills/ptc-sandbox/` may already cover this. | **Low** | Check before changing |

---

## 5. Daemon Guards (in `scripts/daemon/guards/mechanical.py`)

### New Guards (~6)

| Guard | What It Checks | Source | Automatable |
|-------|---------------|--------|-------------|
| `all_wave_subagents_complete` | All sub-agents for current wave have terminated (any status) | v2-subagent-restructuring.md §6.2 | **High** — reads `active_subagents[]` in agent state |
| `subagent_quality_gate_passed` | Last completed sub-agent's return has `quality_gate_passed: true` | SUB-AGENT-SPECS.md validation rules | **High** |
| `delegation_prompt_valid` | Delegation JSON at `pending_delegation` path validates against schema | SUB-AGENT-SPECS.md input contracts | **Medium** |
| `wave_has_remaining_tasks` | Current wave has undispatched tasks | System state fields | **High** |
| `all_waves_complete` | All waves in current phase are done | System state fields | **High** |
| `current_wave_merged` | All worktrees for current wave merged successfully | System state fields | **High** |

### Existing Unregistered Guards (7 — implement to unblock transitions)

| Guard | State Machine | Automatable |
|-------|--------------|-------------|
| `clarification_resolves_ambiguity` | explorer, researcher | **High** — check clarification response in agent state |
| `consecutive_stall_count_gte_5` | tester | **High** — read counter from agent state |
| `consecutive_stall_count_lt_5` | tester | **High** |
| `exploration_progress_exists` | explorer | **High** — check for partial results |
| `no_partial_results_exist` | explorer | **High** |
| `partial_results_meet_minimum_coverage` | explorer, researcher | **Medium** — needs coverage % threshold |
| `research_progress_exists` | researcher | **High** |

---

## 6. Hook Updates (in `hooks/`)

| Hook | Changes | Source | Automatable |
|------|---------|--------|-------------|
| `subagent_start.py` | Register sub-agent in parent's `active_subagents[]`. Inject PTC context (container ID, role image). Set up `ptc_register_parent` for shared containers. | AGENT-ARCHITECTURE.md §1, SUB-AGENT-SPECS.md common conventions | **Medium** |
| `subagent_stop.py` | Per-type validation dispatch: 9 sub-agent types × validation rules from SUB-AGENT-SPECS.md Appendix A. Layered architecture: structural validation always runs (portable), daemon notification if available (workflow mode). Quality gate result logging to `~/.claude/logs/quality-gates.jsonl`. | SUB-AGENT-SPECS.md Appendix A (full validation table) | **Medium** — rules are fully specified but ~65 rules across 9 types is substantial |

**Hook architecture decision (D-HOOK-1):** Option C — layered. Hooks validate independently (works outside workflow). If daemon is running, hooks ALSO ping daemon with results. The `/orchestrate` skill wires up daemon connection at workflow start. Matches existing pattern in `pre_tool_use.py` (try daemon, fall back to direct).

---

## 7. Not In Scope (deferred)

These are NOT part of the sub-agent overhaul:

- TUI viewers (message + state transition)
- Merge queue for coder ordering
- Checkpoint refs for rollback
- TOON token efficiency conversions
- Embedding model integration
- V2/V3 observability
- PTC ipybox rewrite (current PTC is complete and running)
- init-workflow.sh setup script
- 5 validation scripts (design coverage, plan conversion, etc.)
- Domain-specific schemas (context packets, scenarios, research entries)

---

## Execution Order

### Phase A: Can Start Now (no blockers)

1. Schema changes — `delegation_return.py` (from DELEGATION-RETURN-SCHEMAS.md)
2. Schema changes — `agent_state.py` (3 field additions)
3. Sub-agent .md files — all 9 (from SUB-AGENT-SPECS.md)
4. Teammate .md files — Explorer, Researcher, Planner, Coder (from TEAMMATE-DIRECTOR-PATTERNS.md)
5. Orchestrator .md file (custom, from AGENT-ARCHITECTURE.md §3.1)
6. New skills — `base-agent/SKILL.md`, `base-code-agent/SKILL.md`
7. New skill — `workflow-coordination/SKILL.md` (from PLAN.md)
8. State machines — explorer.json, researcher.json, strategist.json (from patterns §1-§3)
9. Implement 7 unregistered guards
10. State machine — coder.json rewrite (from patterns §4)
11. State machine — system.json wave additions

### Phase B: Blocked on Architect (Tester + Auditor patterns)

12. Teammate .md files — Tester, Auditor
13. State machines — tester.json, auditor-task.json, auditor-phase.json

### Phase C: After State Machines Land

14. New daemon guards (~6)
15. Skill updates — delegation-prompts, sub-agent-delegation
16. Hook updates — subagent_start.py, subagent_stop.py

### Phase D: Integration

17. Wire v2 hooks into settings.json
18. Populate think_prompt fields in all state machines (29 states)
19. Fix shell injection in session_start.py
20. End-to-end test: single task through coder director flow

---

## Automation Assessment

| Category | Files | Highly Automatable | Needs Judgment |
|----------|-------|--------------------|----------------|
| Agent .md files | 16 | 15 | 1 (orchestrator) |
| State machines | 7-8 | 5 | 2-3 (coder, system, auditor) |
| Schemas | 2-3 | 2-3 | 0 |
| Skills | 6 | 3 new | 3 updates |
| Guards | ~13 | ~11 | 2 |
| Hooks | 2-3 | 0 | 2-3 |
| **Total** | **~47 files** | **~36 (~75%)** | **~11 (~25%)** |

~75% of remaining work is mechanical translation from existing design documents into implementation files. The design docs contain enough detail (input/output contracts, validation rules, state mappings, Pydantic code) that a well-prompted agent can produce correct implementations with minimal iteration.

The 25% requiring judgment: orchestrator definition, coder/system state machine rewrites, delegation-prompts skill integration, sub-agent-delegation skill update, and the subagent_stop hook (65 validation rules need careful implementation).
