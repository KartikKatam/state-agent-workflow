# Session Handoff — Sub-Agent Overhaul

**Date:** 2026-03-16
**Session:** V2 sub-agent architecture restructuring
**Previous session ID:** 22ffec4e-93d3-4242-ae7b-8aa9d087041e

---

## Prompt for Next Session

Copy this as your first message to a new Claude Code session in this project directory:

---

We are in the middle of a major overhaul of the agentic workflow system — restructuring from an all-teammate architecture to a director-teammate + ephemeral sub-agent architecture. The design phase is ~85% complete and implementation has not started.

**Read these files first to understand the current state:**

1. `new_claude/agents/AGENT-ARCHITECTURE.md` — Source of truth. 7 teammates, 9 sub-agents, 6 invariants, 22 design decisions. This supersedes the old v2-subagent-restructuring.md for agent types.

2. `new_claude/agents/BASE-DIRECTOR-PATTERN.md` — Universal teammate lifecycle: 5 phases, 14 states, 17 extension points.

3. `new_claude/agents/TEAMMATE-DIRECTOR-PATTERNS.md` — Per-teammate extension points for Explorer, Researcher, Planner, Coder. **Tester and Auditor sections are NOT yet written** — this is the first thing to complete.

4. `new_claude/agents/SUB-AGENT-SPECS.md` — All 9 sub-agent detailed specs: behavioral steps, input/output contracts, validation rules, tool/file access, failure modes, state machine integration. 3 appendices (validation summary, INV-1 matrix, delegation type mapping).

5. `new_claude/agents/DELEGATION-RETURN-SCHEMAS.md` — 11 shared Pydantic sub-models, 7 new return types, migration path for `schemas/delegation_return.py`.

6. `new_claude/agents/REMAINING-WORK.md` — Complete breakdown of all remaining implementation work with automation assessment.

7. `.claude/plans/v2-subagent-restructuring.md` — Original restructuring plan (background context, some sections superseded by AGENT-ARCHITECTURE.md).

**What's done:**
- Full agent architecture designed (7 teammates + 9 sub-agents)
- Base director pattern designed (universal lifecycle for all teammates)
- Director patterns for Explorer, Researcher, Planner, Coder complete
- All 9 sub-agent specs complete (8 dimensions each)
- Delegation return schemas designed (7 new types + 11 sub-models)
- General PTC container created and tested (Dockerfile.general, fallback routing)
- Dead files cleaned up (31 files deleted)
- SessionStart hook disabled (was causing compaction hangs)

**What's NOT done:**
- Tester and Auditor director patterns (design)
- All 16 agent/sub-agent .md definition files (implementation)
- All 7-8 state machine JSON updates (implementation)
- Schema changes in delegation_return.py and agent_state.py (implementation)
- 3 new skills: base-agent, base-code-agent, workflow-coordination
- 3 skill updates: delegation-prompts, sub-agent-delegation, ptc-sandbox
- ~13 daemon guards (6 new + 7 unregistered)
- 2 hook updates: subagent_start.py, subagent_stop.py
- Wiring v2 hooks into settings.json

**Key design decisions made this session:**
- D16: Sequential phases, 1 Coder per phase
- D17: Orchestrator reduced to sub-agent spawning only
- D18: Orchestrator dashboard at .claude/state/system-dashboard.json
- D19: No hook-based sub-agent dispatch
- D20: Parallel sub-agents safe on same task worktree (INV-1 enforces file separation)
- D21: Optimizer is design-level (Auditor dispatches), not task-level
- D22: Cold-start is primary path, resume is optimization
- Research-scout and plan-checker have broad read access (read-only, INV-1 doesn't apply)
- QualityGateResult simplified to `quality_gate_passed: bool` (hook blocks until gate passes, detailed results go to logs)
- Hook architecture: Option C — layered (hooks validate independently for portability, daemon notification is optional for workflow mode)
- Micro-commits at 3 TDD boundaries (RED_VERIFIED, QUALITY_GATE_PASSED, AUDIT_APPROVED), not separate scripts
- Teammate context is 200K (bug, not design — don't hyper-optimize around it)
- Sub-agents inherit 1M context from parent (works correctly)

**How to proceed:**
1. First: Complete Tester and Auditor director patterns in TEAMMATE-DIRECTOR-PATTERNS.md
2. Then: Follow the execution order in REMAINING-WORK.md (Phase A → B → C → D)
3. Write .md files one at a time with user review before each
4. The user wants granular control over implementation — do NOT batch-write files without review

**Active teammates when session ended:**
- architect (blue) — was working on Tester/Auditor patterns, may have been interrupted by context pressure
- writer and sub-agent-architect were terminated

**Important behavioral notes:**
- Do NOT interject between user and teammates. Stay out of the way unless asked.
- Do NOT approve teammate plans automatically — let the user review.
- Do NOT batch-write implementation files without user review.
- Teammates should talk to the user directly, not message the team lead.
- When spawning teammates, include in their prompt: "Do NOT message the team lead. The user interfaces with you directly."

---

## File Inventory (what exists in `new_claude/agents/`)

```
new_claude/agents/
├── AGENT-ARCHITECTURE.md        # Source of truth (1,072+ lines)
├── BASE-DIRECTOR-PATTERN.md     # Universal teammate lifecycle (1,157 lines)
├── TEAMMATE-DIRECTOR-PATTERNS.md # 4/6 teammates done (1,418 lines)
├── SUB-AGENT-SPECS.md           # All 9 sub-agents (1,486 lines)
├── DELEGATION-RETURN-SCHEMAS.md  # Pydantic schema designs (699 lines)
├── REMAINING-WORK.md            # Implementation checklist
├── SESSION-HANDOFF.md           # This file
└── TASK1-HANDOFF.md             # Task #1 handoff (can be deleted)
```

No .md agent definition files exist yet — all were reverted. Implementation starts fresh.

## PTC MCP Status

PTC is globally available on port 8741 (SSE transport). General container added this session. 7 role images: explorer, coder, tester, researcher, auditor, strategist, general. The general container is the fallback for unknown roles.

## Known Issues

- SessionStart hook (`.claude/hooks/SessionStart.py`) disabled in settings — was causing compaction hangs due to 10s subprocess timeout inside a 5s hook timeout
- 7 guards in state machines are referenced but unregistered in `scripts/daemon/guards/mechanical.py` — transitions using them will permanently block
- `new_claude/hooks/subagent_stop_return_validation.py` exists but is not wired into settings
- Shell injection in `hooks/session_start.py` (v2) — needs `shlex.quote()` on agent_id
