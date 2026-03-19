# Handoff: v2 Skill Architecture Planning Session

## Session Goal
Plan and write the complete v2 skill architecture for the multi-agent TDD workflow system. Ground-up redesign, NOT migration of v1 skills.

## Current State: SKILL CATALOG FINALIZED — Ready to build skill-writer, then plan each skill

### What Was Decided

**17 skills total.** User approved this catalog:

#### Judgment Skills (10)
| # | Skill | Primary Agent | Key Content |
|---|-------|--------------|-------------|
| 1 | `tdd-discipline` | coder | Iron Law TDD, anti-rationalization, test design heuristics, when to scrap vs fix |
| 2 | `task-execution` | coder | Reading task specs, scope discipline, clarifying questions, YAGNI |
| 3 | `systematic-debugging` | coder | 4-phase root cause process, 3-strikes rule, evidence gathering at component boundaries. Based on superpowers systematic-debugging |
| 4 | `phase-planning` | strategist | Phase = milestone checkpoint with tasks inside (NOT chunks/vertical slices). Audit-level rating per task: 1=no auditor/boilerplate, 2=important/audit at phase end, 3=blocking/full auditor. touched_functions, complexity tiers, dependency graphs |
| 5 | `codebase-exploration` | explorer | Exploration strategy (targeted vs comprehensive), query scoping, when to re-explore. Does NOT cover synthesis/output — that's structured-synthesis |
| 6 | `research-methodology` | researcher | Source evaluation, confidence scoring, MCP routing. Research ALWAYS persists. Does NOT cover synthesis — that's structured-synthesis |
| 7 | `test-design` | tester | Pass D test design, blind testing discipline, spec-only derivation |
| 8 | `scenario-generation` | tester | How to create adversarial scenarios across 4 tiers (golden path, edge cases, adversarial, property-based), coverage assessment |
| 9 | `code-review` | auditor | Two-stage review (spec compliance → code quality), "Do Not Trust the Report", anti-sycophancy, severity categorization |
| 10 | `workflow-coordination` | orchestrator | Phase transitions, dispatch strategy, failure recovery, re-exploration triggers |

#### Cross-Cutting Skills (6)
| # | Skill | Primary Agents | Key Content |
|---|-------|---------------|-------------|
| 11 | `verification-before-completion` | all | Gate function (IDENTIFY→RUN→READ→VERIFY→CLAIM), forbidden phrases without evidence |
| 12 | `anti-rationalization` | coder, auditor, tester | Per-role rationalization tables, red flags, HARD-GATE, foundational principle |
| 13 | `handoff-protocol` | coder, explorer, researcher | When to hand off vs continue, what to preserve, handoff file structure |
| 14 | `plan-adherence` | coder, auditor | Recognizing deviation from plan, documenting it, never freelancing. Separate from task-execution because auditor also needs it |
| 15 | `sub-agent-delegation` | ALL agents | How to scope sub-agent tasks (Haiku/Sonnet), context sizing, parallel vs sequential, error recovery (retry vs reframe vs escalate). Main agents have PTC + MCP; sub-agents use PTC |
| 16 | `structured-synthesis` | explorer, researcher, tester, auditor | How to synthesize sub-agent findings into quality structured output. Explorer → context packets, researcher → research entries, tester → scenario results, auditor → review findings |

#### Meta Skill (1)
| # | Skill | Purpose |
|---|-------|---------|
| 17 | `skill-writer` | Working tool that teammates load when helping user plan/write skills. NOT an agent-loaded skill in the workflow |

### Key Design Decisions Made

1. **Skills encode JUDGMENT ONLY** — if infrastructure (state machine, hooks, PTC) can enforce it, it doesn't go in the skill
2. **Phases, not chunks** — strategist creates milestone-based phases with tasks inside, not vertical implementation slices
3. **Audit levels per task** — strategist rates each task: 1 (no auditor), 2 (phase-end audit), 3 (full task auditor)
4. **Research always persists** — no ephemeral vs persistent decision; all research is persistent
5. **Context-packet-writing + research synthesis = structured-synthesis** — combined into one cross-cutting skill
6. **Sub-agent delegation is universal** — all agents delegate to sub-agents, not just explorer/researcher
7. **Plan-adherence is separate from task-execution** — because auditor also needs it (cross-cutting)
8. **No scribe skill** — scribe behavior is infrastructure (hooks, state machine)
9. **Domain specializations augment skills** — robotics-cv specializations live as files within skill directories (e.g., `tdd-discipline/specializations/robotics-cv.md`)
10. **skill-writer is a working tool, not a planned skill** — we build it first from best-of superpowers + local docs, then use it to guide all other skill planning

### Skills Rejected (With Reasons)
- Merge conflict resolution → state machine + Doc 1 handles flow
- Think-tool quality → guidance embedded in each judgment skill
- Anti-sycophancy standalone → sections within code-review and test-design
- Problem-solving suite (simplification-cascades, etc.) → too abstract for v2, future enhancement
- when-stuck → fold "stuck" heuristics into systematic-debugging
- testing-anti-patterns standalone → references/ inside tdd-discipline
- defense-in-depth standalone → references/ inside systematic-debugging

## What To Do Next

### Step 1: Build skill-writer SKILL.md
Synthesize from:
- `obra/superpowers` `skills/writing-skills/SKILL.md` — structure, CSO rules, TDD for docs, anti-pattern libraries
- `obra/superpowers` `skills/writing-skills/persuasion-principles.md` — Authority+Commitment+Social Proof for discipline skills
- `obra/superpowers` `skills/writing-skills/testing-skills-with-subagents.md` — pressure scenarios, rationalization capture, meta-testing
- `obra/superpowers` `skills/writing-skills/anthropic-best-practices.md` — conciseness, progressive disclosure, degrees of freedom
- Local `skill-engineering-guide.md` — 5 principles, 5 guardrails, enforcement hierarchy (6 levels)
- Doc 3 Sections 3-5 — YAML frontmatter spec, enforcement hierarchy, skill-writer spec

**The extracted content from all 4 superpowers writing-skills files is saved at: `/tmp/superpowers-writing-skills.txt`** (82KB, all 4 files concatenated)

### Step 2: For each of the 16 remaining skills, spawn a teammate that:
1. Loads the skill-writer skill
2. Gets specific reference files for that skill's domain
3. Plans the skill WITH the user (not autonomously)
4. Writes a planning doc to `.claude/designs/skills/{skill-name}.md`

Each planning doc contains:
1. Complete YAML frontmatter (name, version, triggers with agent_role and states, CSO-compliant description)
2. SKILL.md body outline (section headers + key content per section)
3. References inventory (what goes in references/ subdirectory)
4. Validation criteria (what scripts/validate.py checks)
5. Token estimate (flag if > ~3,000 soft target)

### Step 3: Cross-reference superpowers patterns
Verify all 16 patterns from superpowers-synthesis.md are represented across the catalog.

## Files Read This Session (Reference Map)

### Design Documents (v2 Architecture)
| File | Key Content for Skills |
|------|----------------------|
| `.claude/designs/03-skill-architecture.md` | Three-layer loading, directory structure, YAML frontmatter spec, enforcement hierarchy, v2 skill catalog, loading mechanism |
| `.claude/designs/00-token-efficiency-standards.md` | 10/10/80 budget, ~3,000 token soft limit per skill body, TOON format, progressive disclosure |
| `.claude/designs/05-agent-specifications.md` | 8 agent behavioral specs, HARD-GATE constraints, mandatory Think points, per-role rationalization tables, handoff protocol |
| `.claude/designs/01-git-management.md` | Worktree lifecycle, micro-commits, merge queue, checkpoint refs — relevant for coder and orchestrator skills |
| `.claude/designs/02-ptc-sandbox.md` | PTC tools per role, kernel lifecycle, fallback transparency — skills reference PTC tools as regular MCP tools |

### Research & Patterns
| File | Key Content for Skills |
|------|----------------------|
| `superpowers-synthesis.md` | 16 empirically-derived patterns: anti-rationalization (3 layers), Iron Law TDD, HARD-GATE, verification-before-completion, "Do Not Trust the Report", task complexity tiers, TDD for documentation, CSO |
| `skill-engineering-guide.md` | 5 principles (explain why, decision tables, visual rules, anti-patterns, imperative form), 5 guardrails (validation scripts, step sequencing, checklists, templates, bundled scripts), enforcement hierarchy (6 levels) |

### Infrastructure (What Skills DON'T Enforce)
| File | What It Already Enforces |
|------|------------------------|
| `state-machines/coder.json` | Write-gating via write_globs per state, transition guards (pytest_exit_zero, etc.), think_on_exit flags, max_occurrences on loops |
| `hooks/post_tool_use.py` | Schema validation, lint checks, context pressure, annotation injection, skill-loaded detection, file write tracking |
| `hooks/pre_tool_use.py` | 3-tier permissions (hard block, ask user, state machine gating via daemon socket) |

### Rules (Governance)
| File | Content |
|------|---------|
| `rules/agents.md` | Communication protocol, hard rules (no shutdown without approval, no idle-triggered messages), spawning conventions |
| `rules/testing.md` | 4-pass test architecture, TDD cycle, test data levels, file naming |
| `rules/plans.md` | Plan lifecycle, JSON plan structure, touched_functions requirement, plan modification rules |
| `rules/workflow-state.md` | State machine architecture, modification rules, permissive fallback principle |
| `rules/context-packets.md` | Context packet locations, exploration modes, required fields |
| `rules/hooks.md` | Hook types, performance requirements, output schema |
| `rules/security.md` | Shell injection prevention, path validation, atomic file operations |

### External Reference (obra/superpowers)
Full content of 4 files extracted to `/tmp/superpowers-writing-skills.txt`:
- `writing-skills/SKILL.md` — TDD for docs, CSO, skill structure, rationalization defense, creation checklist
- `writing-skills/persuasion-principles.md` — 7 principles, combinations by skill type, psychology research (Meincke 2025 N=28K)
- `writing-skills/testing-skills-with-subagents.md` — Pressure scenarios, RED-GREEN-REFACTOR for skills, meta-testing
- `writing-skills/anthropic-best-practices.md` — Conciseness, degrees of freedom, progressive disclosure patterns, evaluation-driven development

Additional superpowers research (from agent): Complete catalog of 38 skills across obra/superpowers, obra/superpowers-skills, obra/superpowers-lab, anthropics/skills. Key findings about skill structure, trigger descriptions, enforcement patterns saved in agent research output.

## Task List State
- Task 1: "Create skill-writer SKILL.md as a working tool" — in_progress
- Task 2: "Write anti-rationalization cross-cutting skill planning doc" — pending
- Task 3: "Write verification-before-completion cross-cutting skill planning doc" — pending
- Task 5: "Write handoff-protocol cross-cutting skill planning doc" — pending
- Task 6: "Cross-reference superpowers patterns and validate coverage" — pending
- (Tasks 2,3,5,6 need updating to reflect new catalog; Task 4 was deleted)

## Process: How User Wants to Work
- User wants to BUILD EACH SKILL COLLABORATIVELY — not autonomous generation
- I (orchestrator) spawn teammates loaded with skill-writer + relevant references
- Each teammate helps the user plan one specific skill
- User steers decisions, teammate brings analysis and structure
- Output: planning docs at `.claude/designs/skills/{skill-name}.md`
