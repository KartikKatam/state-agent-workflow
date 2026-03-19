# Session 2 Handoff — Agent Definition .md Files (Continued)

**Date:** 2026-03-17
**Status:** Phase A in progress. 4 of 9 sub-agent .md files complete. 1 new skill created.
**Previous handoff:** `IMPLEMENTATION-HANDOFF.md` (read that FIRST for full project context)

---

## Required Skills — Load Before Writing Any .md File

Load these three skills. They contain the rules, patterns, anti-patterns, and examples for writing agent .md files. Do NOT write any .md files without loading these.

### 1. Agent MD Writing Skill (for teammate .md files — Phase B)
```
Read: /home/kartik/personal/agentic_workflow/skills/agent-md-writing/SKILL.md
Read: /home/kartik/personal/agentic_workflow/skills/agent-md-writing/references/patterns.md
Read: /home/kartik/personal/agentic_workflow/skills/agent-md-writing/references/anti-patterns.md
Read: /home/kartik/personal/agentic_workflow/skills/agent-md-writing/references/complete-examples.md
Read: /home/kartik/personal/agentic_workflow/skills/agent-md-writing/references/translation-walkthrough.md
```

### 2. Sub-Agent MD Writing Skill (for sub-agent .md files — Phase A)
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

### Primary Design Documents (in `new_claude/agents/`)

| Document | Content | Read When |
|----------|---------|-----------|
| `AGENT-ARCHITECTURE.md` | Source of truth: 7 teammates, 9 sub-agents, 6 invariants, dispatch rules | Before writing ANY .md file |
| `SUB-AGENT-SPECS.md` | All 9 sub-agent specs: behavioral steps, contracts, validation, tool access, failure modes | Primary source for each sub-agent .md file |
| `TEAMMATE-DIRECTOR-PATTERNS.md` | Per-teammate extension points for all 6 teammates | Primary source for each teammate .md file (Phase B) |
| `DELEGATION-RETURN-SCHEMAS.md` | Return types, shared sub-models | Reference for output contract sections |
| `BASE-DIRECTOR-PATTERN.md` | Universal teammate lifecycle (design reference, NOT runtime) | Before writing teammate .md files (Phase B) |

### V2 Skills (in `new_claude/skills/` — these are what agents load at runtime)

The old V1 skills in `.claude/skills/` are **deprecated**. Only use skills from:
- `skills/` (top-level) — contains `ptc-sandbox` (universal) and the writing skills
- `new_claude/skills/` — all V2 operational skills

**Key V2 skills to know about:**

| Skill | Path | Used By Sub-Agents | Notes |
|-------|------|-------------------|-------|
| `ptc-sandbox` | `skills/ptc-sandbox/SKILL.md` | ALL agents | Universal PTC access. Has role-specs per agent type. |
| `codebase-exploration` | `new_claude/skills/codebase-exploration/SKILL.md` | codebase-scout | Exploration methodology, 6-point checklist, confidence scoring, token conventions |
| `research-methodology` | `new_claude/skills/research-methodology/SKILL.md` | research-scout | Confidence scoring, source quality, decomposition (teammate-perspective but scout uses relevant parts) |
| `plan-verification` | `new_claude/skills/plan-verification/SKILL.md` | plan-checker | **NEW — created this session.** 8 verification dimensions, PTC recipes, verdict rules |
| `code-review` | `new_claude/skills/code-review/SKILL.md` | audit-checker | Severity framework, fraudulent test detection, anti-sycophancy, review checklists |
| `delegation-prompts` | `new_claude/skills/delegation-prompts/SKILL.md` | Parents (not sub-agents) | Return protocol — parents include `return_schema` in delegation prompts. Sub-agents do NOT load this. |
| `code-design` | `new_claude/skills/code-design/SKILL.md` | implementer, optimizer | Code design patterns, pipeline patterns |
| `debugging` | `new_claude/skills/debugging/SKILL.md` | debugger | Hypothesis-driven debugging methodology |
| `test-design` | `new_claude/skills/test-design/SKILL.md` | test-writer, scenario-writer | Test design patterns, tier patterns |
| `scenario-testing` | `new_claude/skills/scenario-testing/SKILL.md` | scenario-writer | Scenario generation, verification |
| `task-execution` | `new_claude/skills/task-execution/SKILL.md` | implementer | Task execution patterns |

**Skill assignment principle:** Sub-agents load 1-2 skills max (cold-start token cost). Every skill must be needed for EVERY invocation. The skill carries domain knowledge; the .md body carries agent-specific judgment the skill doesn't cover.

---

## Progress — What's Done

### Phase A: Sub-Agent .md Files (4 of 9 complete)

| # | File | Status | Skills Loaded | Key Design Notes |
|---|------|--------|---------------|-----------------|
| 1 | `sub-agents/codebase-scout.md` | ✅ Done | ptc-sandbox, codebase-exploration | ~110 lines. Depth routing table, partition boundaries, confidence as skill reference |
| 2 | `sub-agents/research-scout.md` | ✅ Done | ptc-sandbox, research-methodology | ~120 lines. Search strategy by depth, premise contradiction as first finding, citation quality |
| 3 | `sub-agents/plan-checker.md` | ✅ Done | ptc-sandbox, plan-verification | ~110 lines. Design-first reading order, PTC mechanical checks prioritized, stall detection |
| 4 | `sub-agents/audit-checker.md` | ✅ Done | ptc-sandbox, code-review | ~135 lines. Adversarial backstory, 4-level severity, fraudulent test detection, YAGNI enforcement |
| 5 | `sub-agents/test-writer.md` | ❌ Not started | ptc-sandbox, test-design (?) | Multi-parent: Coder (TDD red) vs Tester (infra repair). INV-1: blind to source. |
| 6 | `sub-agents/implementer.md` | ❌ Not started | ptc-sandbox, code-design, task-execution (?) | INV-1: blind to tests. Stall threshold 5. Multi-parent: Coder (TDD green) + Coder (audit fix). |
| 7 | `sub-agents/scenario-writer.md` | ❌ Not started | ptc-sandbox, scenario-testing (?) | Most isolated: blind to source AND unit tests. Collect-only validation. |
| 8 | `sub-agents/debugger.md` | ❌ Not started | ptc-sandbox, debugging (?) | Hypothesis-driven, ≥2 hypothesis log. Stall threshold 3. Multi-parent. |
| 9 | `sub-agents/optimizer.md` | ❌ Not started | ptc-sandbox, code-design (?) | Ephemeral worktree, compare-and-discard. Multi-parent. |

### New Skill Created

**`new_claude/skills/plan-verification/`** — Created for plan-checker sub-agent.
- `SKILL.md` (~250 lines) — 8 verification dimensions, HARD-GATE, PTC recipes inline, verdict rules, multi-gate awareness, anti-rationalization
- `references/patterns.md` (~200 lines) — Full PTC mechanical verification suite, coverage matrix, file checker, revision suggestion templates
- `references/anti-patterns.md` (~220 lines) — 9 failure modes with WRONG/RIGHT examples

Scored 95/100 (A) by user. Fixes applied: expanded vagueness blocklist, vague-design-doc handling, partial plan handling, time budget heuristic, TOC on anti-patterns, scope threshold documented as calibratable.

### Phase B & C: Not Started

Phase B (6 teammate .md files) and Phase C (orchestrator .md) are not started. Phase A should complete first.

---

## Patterns Learned — Apply to ALL Remaining .md Files

These patterns were developed iteratively during Session 2 through user critique. They are mandatory for every .md file going forward.

### Skill Assignment

1. **V1 skills are deprecated.** Only use skills from `skills/` (ptc-sandbox) and `new_claude/skills/`. Never reference `.claude/skills/`.
2. **Read each candidate skill's SKILL.md** before assigning it. Understand what it covers so you don't duplicate content in the body.
3. **ptc-sandbox is universal.** Every sub-agent that uses PTC loads it.
4. **delegation-prompts is parent-only.** Sub-agents do NOT load it. They receive the return schema via the delegation prompt. The parent includes `return_schema` in the prompt per the skill's Return Protocol.
5. **1-2 skills per sub-agent.** Cold-start token cost. Every skill must be needed every invocation.
6. **After assigning skills, audit the body for duplication.** If the skill covers confidence scoring, the body says "per your skill's epistemic standards" — it doesn't reproduce the confidence scale.

### Workflow Design

7. **Decision tables, not step lists.** The workflow section must surface judgment calls as routing tables. A flat numbered list without decision points is an anti-pattern.
8. **The primary routing decision goes in step 2.** Step 1 is always "parse and orient." Step 2 is the big decision that shapes everything (depth for scouts, search strategy for researchers, verification mode for checkers, dispatch type for multi-parent agents).
9. **Contradictions and edge cases are decision points in the workflow, not just failure table entries.** If something happens DURING the work (contradictory findings, scope edges), it should appear in the workflow with a decision table — not only in "When Things Go Wrong."

### Output Contracts

10. **Teach semantic quality, not schema.** The hook validates fields. The body teaches what makes GOOD content. "Every finding needs file:line, expected vs actual, and a specific recommendation" — not "findings is an array of objects with severity, category, file, line."
11. **Two outputs distinguished when applicable.** Some sub-agents write a file AND return JSON. These are separate outputs with different quality standards. Not all sub-agents have this (audit-checker is return-only).
12. **Anti-patterns inline in the output section.** One or two "never do X" lines where the sub-agent would be tempted to take shortcuts. "Never claim findings about files you didn't open." "Never soften findings with praise."

### Boundaries

13. **Write scope explicit in one sentence.** Even if hooks enforce it: "Your Write tool is scoped to context output paths only" or "You have no Write or Edit tools." Explains WHAT, then the next sentence explains WHY.
14. **Define ambiguous terms.** If a boundary uses a judgment term like "blocking," define it: "blocking means you cannot answer an essential_output item above medium confidence without it."
15. **Scope violations are findings, not just rules.** For audit-type agents: "Scope violations are major findings" is more actionable than "stay in scope."

### Identity

16. **Backstory for adversarial/thorough roles.** The audit-checker's "reputation for catching subtle issues" backstory implies thoroughness more effectively than "be thorough." Use narrative when the behavioral standard is hard to specify as rules.
17. **Optimization target in 1 sentence.** "You optimize for accuracy over completeness." "You optimize for cited accuracy." Every sub-agent states what it optimizes for.

### Multi-Parent Sub-Agents (upcoming: test-writer, debugger, optimizer)

18. **Dispatch type as mode selector.** The delegation prompt's `type` field differentiates behavior. Body has a "mode-specific behavior" table.
19. **Shared workflow first, mode-specific differences second.** Don't duplicate the core process for each mode. One workflow, then a table of mode-specific variations.

### Information Barriers (upcoming: test-writer, implementer, scenario-writer)

20. **Three-layer barrier.** Layer 1: hook blocks file access (don't restate). Layer 2: body explains WHY. Layer 3: body teaches how to work WITHOUT the information.
21. **Error messages are not source code.** Execution output (pytest failures, stack traces) is behavioral evidence the agent CAN use. Explain how to use errors as behavioral specs without reverse-engineering the implementation.

### Context Pressure & Budget

22. **Budget-tight row in depth/strategy tables.** Every decision table should have a row for "budget is tight" with prioritization guidance.
23. **PTC mechanical checks prioritized under pressure.** When context pressure forces partial work, PTC algorithmic checks give the most value per token. Judgment dimensions second.

### Stall & Failure

24. **Stall thresholds stated.** 3 for verification/analysis sub-agents. 5 for implementation sub-agents.
25. **Partial vs failed distinction.** Partial = produced SOME useful work (parent builds on it). Failed = couldn't produce ANY meaningful output (parent retries from scratch).
26. **Re-audit/re-verification pattern.** When a previous finding was fixed at the reported location but the same pattern exists elsewhere, report as NEW finding — the original is resolved, the pattern is separate.

---

## What Still Needs to Be Done

### Phase A Remaining (5 sub-agent .md files)

Write one file at a time. Present for user review before proceeding.

| # | File | Source | Key Concerns | Likely Skills |
|---|------|--------|-------------|---------------|
| 5 | `sub-agents/test-writer.md` | SUB-AGENT-SPECS §5 | **Multi-parent**: Coder (TDD red) vs Tester (infra repair). INV-1: blind to source. Red verification mandatory for Coder dispatch. | ptc-sandbox, test-design |
| 6 | `sub-agents/implementer.md` | SUB-AGENT-SPECS §6 | INV-1: blind to tests. Stall threshold 5. Quality gate mandatory. Multi-parent: Coder (TDD green) + Coder (audit fix). | ptc-sandbox, code-design + task-execution (?) |
| 7 | `sub-agents/scenario-writer.md` | SUB-AGENT-SPECS §7 | Most isolated: blind to source AND unit tests. Reads design+plan only. Collect-only validation. | ptc-sandbox, scenario-testing |
| 8 | `sub-agents/debugger.md` | SUB-AGENT-SPECS §8 | Hypothesis-driven, ≥2 hypothesis log. Stall threshold 3. Multi-parent: Coder (workflow) + Auditor (ad-hoc). | ptc-sandbox, debugging |
| 9 | `sub-agents/optimizer.md` | SUB-AGENT-SPECS §9 | Ephemeral worktree, compare-and-discard. Multi-parent: Coder + Auditor + Orchestrator. | ptc-sandbox, code-design |

**For each remaining file:**
1. Read SUB-AGENT-SPECS §N for the sub-agent's full design spec
2. Read candidate V2 skills to determine what they cover (avoid body duplication)
3. Decide if a custom skill is needed (as we did for plan-verification) — likely NOT for the remaining 5, the existing V2 skills should cover them
4. Write the .md applying all patterns above
5. Present to user for review, apply feedback, then proceed

### Phase B (6 teammate .md files) — After Phase A

See `IMPLEMENTATION-HANDOFF.md` for the full list. Requires reading `TEAMMATE-DIRECTOR-PATTERNS.md` and `BASE-DIRECTOR-PATTERN.md`. Uses the agent-md-writing skill (not sub-agent-md-writing).

### Phase C (orchestrator .md) — After Phase B

Custom writing, not mechanical. See `IMPLEMENTATION-HANDOFF.md`.

---

## User Interaction Preferences (confirmed this session)

- **Write one .md file at a time.** Present for user review before proceeding to the next.
- **Stop after every file and wait for review.** Do not batch-write.
- **Stop using the think tool excessively.** Use it for genuinely complex decisions, not as a default before every action.
- **Eval scenarios for skills will be done in a batch later** — do not add them inline.
- **The user provides structured critique** with specific fixes. Apply fixes surgically — don't rewrite the whole file.
- **When the user asks "does this need a custom skill?" — investigate before assuming.** Read existing V2 skills first. Only create a new skill when existing skills genuinely don't cover the domain.
