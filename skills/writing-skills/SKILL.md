---
name: writing-skills
version: 1-0-0
triggers:
  - agent_role: "*"
    conditions: when creating, modifying, reviewing, or planning skill files
description: >
  Use when writing a new SKILL.md, planning skill content, modifying an existing
  skill, or reviewing skill quality. Activates for: any work on files under
  skills/, any discussion of skill design, any skill planning session.
  Do NOT use for: using skills (that's the skill's own trigger), modifying
  hooks or state machines, writing agent specs.
---

# Writing Skills

## Core Principle

**Writing skills is TDD applied to process documentation.**

If you didn't watch an agent fail without the skill, you don't know if the skill teaches the right thing. Skills encode **judgment** — if infrastructure (state machine, hooks, PTC) can enforce a rule, it does NOT belong in a skill.

**Violating the letter of the rules is violating the spirit of the rules.**

<HARD-GATE>
Do NOT write any SKILL.md content, create any skill directory, or draft any
skill body until you have:
1. Identified the skill type (discipline / technique / pattern)
2. Run at least one baseline pressure scenario WITHOUT the skill
3. Documented exact agent failures and rationalizations verbatim

If creating a discipline-enforcing skill, you need 3+ pressure scenarios.
This applies regardless of how "obvious" the skill content seems.
</HARD-GATE>

## Quick Reference

| Decision | Route |
|----------|-------|
| Can infrastructure enforce this rule? | Don't put it in the skill — use hook, guard, or state machine |
| Is this a judgment call requiring reasoning? | Put it in the skill |
| Is the skill body over 500 lines? | Move content to `references/` |
| Does the description summarize workflow? | Rewrite — trigger conditions ONLY (CSO Rule) |
| Does a rule exist only as prose (level 1-2)? | Escalate to level 5-6 or flag as advisory |
| Is this a discipline-enforcing skill? | Add rationalization table + red flags + HARD-GATE |
| Are you writing without testing first? | STOP — run baseline scenario first |
| One-off solution? | Don't create a skill — put in task context |
| Standard practice Claude already knows? | Don't create — wasted tokens |
| Project-specific convention? | Put in CLAUDE.md, not a skill |

## Workflow Selection

| Situation | Workflow |
|-----------|----------|
| **Creating new skill** | Full RED-GREEN-REFACTOR from scratch (see Skill Creation Checklist) |
| **Editing existing skill** | Targeted cycle: RED on the specific gap, GREEN with edit, REFACTOR running both new AND existing scenarios (regression check) |
| **Reviewing skill quality** | Run validation script + spot-check pressure scenarios |

## Step 0: Understand the Intent

Before creating any test scenarios:
- What behavior does this skill enforce or teach?
- Which agents will use it? (coder, orchestrator, all?)
- Is this discipline (rigid), technique (guided), or pattern (flexible)?
- What does failure look like without this skill?

If any of these are unclear, **ask before proceeding**. Don't guess at skill intent.

## Three-Layer Loading Model

Every skill has three layers, loaded at different times:

| Layer | When | Size Target | Contains |
|-------|------|-------------|----------|
| **1: Metadata** | Always (at spawn) | <100 words | YAML frontmatter: name, version, triggers, description |
| **2: Body** | On trigger match | <500 lines / ~3,000 tokens | SKILL.md body: workflow, decision tables, critical rules |
| **3: References** | On explicit demand | Unlimited | `references/` files: patterns, anti-patterns, edge cases, examples |

**Design for this flow.** Agents see Layer 1 every turn. They read Layer 2 once at activation. They read Layer 3 rarely, only for specific edge cases. Put the 20% of content that covers 80% of cases in the body. Put the rest in references.

**Token estimation:** ~0.75 tokens per word for English prose, ~1.5 tokens per word for code blocks. `wc -w SKILL.md` x 0.75 = approximate token count.

## Directory Structure

```
skills/{skill-name}/
├── SKILL.md              # Layers 1+2: YAML frontmatter + body
├── references/           # Layer 3: on-demand knowledge
│   ├── patterns.md       # Proven approaches, code examples
│   ├── anti-patterns.md  # Known failure modes (WRONG/RIGHT pairs)
│   └── edge-cases.md     # Boundary conditions, rare scenarios
├── scripts/              # Enforcement level 6 (removes discretion)
│   └── validate.py       # Validation, quality gates
└── specializations/      # Domain-specific extensions
    └── {domain}.md       # e.g., robotics-cv.md
```

### Specializations

Domain-specific extensions that ADD context without modifying the core skill. Example: `specializations/robotics-cv.md` adds CV-specific testing patterns to `tdd-discipline` without changing the core TDD workflow.

**Create a specialization when:** The core skill applies universally but a specific domain needs additional patterns, anti-patterns, or examples.
**Create a separate skill when:** The domain requires a fundamentally different workflow.

## YAML Frontmatter Specification

**Standard fields** (compatible with Claude Code native skill system and Superpowers):
```yaml
---
name: string           # Must match directory name
description: string    # ONLY trigger conditions — see CSO Rule below
---
```

**Extended fields** (custom to this multi-agent system — not portable to vanilla Claude Code or Superpowers):
```yaml
---
name: string           # Must match directory name
version: string        # MODEL-REVISION-ADDITION (see below)
triggers:
  - agent_role: string # Which role: coder, explorer, orchestrator, etc. ("*" = all)
    states: [string]   # State machine states that activate (optional)
    conditions: string # Free-text conditions beyond state (optional)
description: string    # ONLY trigger conditions — see CSO Rule below
depends_on: [string]   # Skills agent MUST read before this one activates (optional)
---
```

**Version semantics** (`MODEL-REVISION-ADDITION`, e.g., `1-0-0`):
- **MODEL:** Breaking changes to skill structure or workflow
- **REVISION:** Behavioral changes (new rules, modified enforcement)
- **ADDITION:** Content additions that don't change behavior (examples, references)

**`depends_on` loading:** Agent MUST read listed skills before this one activates. If unavailable, warn the human but proceed with reduced effectiveness.

### The CSO Rule — Descriptions MUST NOT Summarize Workflows

The single most important rule for skill metadata. Empirically validated: descriptions that summarize workflow cause agents to follow the description shortcut instead of reading the full skill body.

**A description saying "dispatches subagent per task with code review between tasks" caused the agent to do ONE review, even though the skill specified TWO reviews.**

**Descriptions contain ONLY:**
- When to activate ("Use when...")
- Trigger phrases and conditions
- Negative boundaries ("Do NOT use for...")

**Descriptions NEVER contain:**
- How the workflow operates
- Step sequences or process summaries
- Outcome descriptions

```yaml
# WRONG — summarizes workflow, agent will shortcut
description: >
  TDD cycle enforcement: test -> fail -> implement -> pass -> gate.
  Writes tests first, verifies they fail, then implements production code.

# RIGHT — trigger conditions only
description: >
  Use when a coder enters any test or implementation state.
  Activates for: test writing, red-green cycles, TDD enforcement.
  Do NOT use for: quality gate checks, merge operations, auditor review.
```

**Make descriptions pushy.** The system under-triggers by default. Include:
1. What situations activate it ("Use when...")
2. Specific trigger phrases and contexts
3. Edge cases that SHOULD trigger (non-obvious activations)
4. Negative boundaries ("Do NOT use for...")

Write in third person. Keep under 500 characters. Start with "Use when."

## SKILL.md Body Structure

Every SKILL.md body follows this structure. Order matters — agents read top-to-bottom and attention degrades.

### 1. Core Principle (1-2 sentences)
Single foundational statement. Include "Violating the letter of the rules is violating the spirit of the rules" for discipline-enforcing skills.

### 2. HARD-GATE (discipline-enforcing skills)
Structural blocker before any work begins. Upgrades the most critical requirement from prose (level 2) to structural dependency (level 5).

### 3. Quick Reference Table
Decision routing table. Agents parse tables faster than prose. Map common situations to actions.

### 4. Core Workflow (numbered steps with structural dependencies)
Each step explicitly references what the previous step produced. Step sequencing with explicit dependencies is enforcement level 5 — the agent **cannot skip steps** without breaking the chain.

### 5. Critical Rules (collected, not scattered)
All non-negotiable rules in one visually distinct section. Pattern: **what to do/never do** + **why / what breaks**. Never scatter rules throughout the document.

### 6. Anti-Rationalization (discipline-enforcing skills only)
Three interlocking defense layers: rationalization table, red flags list, foundational principle. For detailed architecture with examples, read `references/anti-rationalization-architecture.md`.

### 7. Pointers to References
Link to `references/` for deep-dives. One level deep only — never chain references.

## The Enforcement Hierarchy

Six levels from weakest to strongest. **Push every critical rule to level 5 or 6.** If a rule exists only at levels 1-2, it WILL be rationalized away under pressure.

| Level | Type | Agent can override? | Where it lives |
|-------|------|---------------------|----------------|
| 1 | Prose instructions | Yes, easily | SKILL.md body |
| 2 | Imperative commands | Yes, under pressure | SKILL.md body |
| 3 | Explained reasoning | Reluctantly | SKILL.md body (paired "why" explanations) |
| 4 | Anti-pattern examples | Rarely | `references/anti-patterns.md` |
| 5 | Structural dependencies | Cannot without breaking flow | SKILL.md body (step sequencing) + state machine |
| 6 | Scripted automation | Cannot — no discretion | `scripts/` directory |

### Escalation Pattern

How to take a prose rule and progressively harden it:

```
Level 2 (prose):     "Always validate output"
  ↓ Add why
Level 3 (reasoning): "Validate with scripts/validate.py — silent corruption is invisible"
  ↓ Add anti-pattern
Level 4 (example):   WRONG: return file without validating / RIGHT: validate then return
  ↓ Add structural dependency
Level 5 (structure): Step 3 produces validated.docx → Step 4 operates on validated.docx
  ↓ Add script
Level 6 (automation): scripts/validate.py runs automatically, blocks return on failure
```

**The question filter for every rule:** "Can infrastructure enforce this?" If yes → hook, guard, or state machine. If no → skill (judgment required).

## Skill Types and Enforcement Style

| Type | Examples | Enforcement | Language |
|------|----------|-------------|----------|
| **Discipline** | tdd-discipline, verification-before-completion | Rigid — follow exactly, no adaptation | Authority: "YOU MUST", "No exceptions" |
| **Technique** | systematic-debugging, structured-synthesis | Guided — adapt principles to context | Moderate: "Consider", "When appropriate" |
| **Pattern** | scenario-generation, codebase-exploration | Flexible — adapt to context | Guidance: "Proven approach", "Effective when" |

**Discipline skills** get all three anti-rationalization layers + HARD-GATE constraints.
**Technique/Pattern skills** get explained reasoning (level 3) + anti-patterns (level 4).

For persuasion principle details (Authority, Commitment, Social Proof by skill type), read `references/persuasion-principles.md`.

## Skill Creation Checklist

**Step 0 — Understand Intent:**
- [ ] What behavior does this enforce or teach?
- [ ] Which agents use it?
- [ ] Skill type: discipline / technique / pattern
- [ ] What does failure look like? If unclear, ask before proceeding.

**RED Phase — Baseline Test:**
- [ ] Create 3+ pressure scenarios (discipline) or application scenarios (technique/pattern)
- [ ] Run WITHOUT skill — document baseline failures verbatim
- [ ] Identify rationalization patterns

**GREEN Phase — Write Minimal Skill:**
- [ ] YAML frontmatter: name matches directory, version set, triggers complete
- [ ] Description: starts with "Use when", trigger conditions ONLY, no workflow summary
- [ ] Core principle (1-2 sentences)
- [ ] HARD-GATE (discipline skills)
- [ ] Quick Reference table
- [ ] Core workflow with structural dependencies between steps
- [ ] Critical Rules section (collected, not scattered)
- [ ] Anti-rationalization layers (discipline skills): table + red flags + foundational principle
- [ ] References for deep-dives (patterns, anti-patterns, edge cases)
- [ ] Run WITH skill — verify agent now complies

**REFACTOR Phase — Close Loopholes:**
- [ ] Identify NEW rationalizations from testing
- [ ] Add explicit counters for each loophole
- [ ] Update rationalization table and red flags
- [ ] Re-test — agent still complies under maximum pressure
- [ ] Meta-test — ask agent how skill could be clearer

**Quality Checks:**
- [ ] Body under 500 lines (move excess to references/)
- [ ] Every critical rule at enforcement level 5-6 (or flagged as advisory)
- [ ] "Why" explained for every non-obvious instruction
- [ ] Anti-patterns shown with WRONG/RIGHT markers
- [ ] Validation script passes (or spec written if script not yet implemented)
- [ ] Token estimate under ~3,000 (`wc -w SKILL.md` x 0.75)

## Critical Rules

- **No skill content before baseline test** — The HARD-GATE is not optional. Reveals what ACTUALLY fails, not what you THINK fails.
- **Descriptions are trigger conditions only** — CSO Rule. Workflow summaries cause agents to shortcut the full body.
- **Collect rules, don't scatter** — Agents miss scattered rules. One section, strong visual markers.
- **Infrastructure first, skill second** — If a hook, state machine, or guard can enforce it, it does NOT belong in the skill.
- **Tables over prose, examples over explanations** — Agent attention degrades on uniform text walls.
- **Rationalizations from tests, not imagination** — Capture verbatim from pressure tests. Hypothetical counters miss the actual excuses.
- **References one level deep** — Never chain `references/a.md` → `references/b.md`. All links from SKILL.md directly.

## Skill-Writing Anti-Rationalization

| Excuse | Reality |
|--------|---------|
| "This skill is obvious, doesn't need baseline testing" | The obvious ones are where agents surprise you. Test anyway. |
| "I'll add the rationalization table later" | Later never comes. Capture failures during RED phase or lose them. |
| "The description is fine, it's close enough" | "Close enough" is how the CSO Rule gets violated. Trigger conditions ONLY. |
| "500 lines is a soft limit, 600 is fine" | Every token competes with the agent's actual work. Move to references/. |
| "I can write the skill from domain knowledge alone" | You're encoding YOUR judgment, not preventing ACTUAL agent failures. Run RED first. |
| "I'll just add one more section to the body" | Progressive disclosure exists for a reason. Will the agent need this 80% of the time? If not, references/. |

**Red flags — STOP:**
- Drafting SKILL.md body before running any baseline scenario
- Rationalization table entries you invented rather than observed
- Description containing "then", "after", "before" (workflow language)
- Skill body growing past 400 lines without moving content to references/

## Validation Script Specification

**Status: To be implemented** — create `scripts/validate.py` for each skill.

Required checks:
- Frontmatter parses as valid YAML
- `name` matches directory name
- `version` follows MODEL-REVISION-ADDITION format
- Description does not contain workflow summary phrases (flag "then", "after", "before" sequences)
- Description starts with "Use when" or similar trigger language
- Body line count (warn >500, not block)
- Required sections present: Quick Reference, Critical Rules (for discipline skills)
- `references/` files referenced from body actually exist
- No broken cross-references

## References

For anti-rationalization architecture (3 defense layers with examples), read `references/anti-rationalization-architecture.md`
For writing patterns (explain why, tables over prose, WRONG/RIGHT examples, conciseness), read `references/writing-patterns.md`
For TDD testing methodology (pressure scenarios, meta-testing, loophole plugging), read `references/testing-methodology.md`
For persuasion principles by skill type (Authority, Commitment, Social Proof), read `references/persuasion-principles.md`
For Anthropic's official best practices (progressive disclosure, degrees of freedom), read `references/anthropic-best-practices.md`
