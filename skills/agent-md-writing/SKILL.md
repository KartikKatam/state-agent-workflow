---
name: writing-teammate-agents
version: 1-0-0
triggers:
  - agent_role: "*"
    conditions: when writing, modifying, reviewing, or planning teammate agent .md definition files
description: >
  Use when writing a new teammate agent .md file, converting a teammate design
  spec into an operational agent definition, modifying an existing teammate agent,
  or planning teammate agent content. Activates for: any work on persistent agent
  definitions (Explorer, Researcher, Planner, Coder, Tester, Auditor or similar
  long-lived director-pattern agents), any discussion of teammate .md design.
  Do NOT use for: writing sub-agent .md files (use writing-sub-agents), writing
  skills (use writing-skills), modifying hooks or state machines directly,
  writing CLAUDE.md or AGENTS.md project config.
depends_on:
  - writing-skills
---

# Writing Teammate Agent Definitions

## Core Principle

**A teammate .md file defines how an agent thinks, not what infrastructure does.**

State machines handle transitions. Hooks enforce structural constraints. Skills provide domain expertise. The agent body fills the gap none of those can: identity, judgment frameworks, coordination intelligence, and the reasoning patterns that make this agent a coherent teammate rather than a generic executor. If infrastructure already enforces something, the agent body explains WHY it exists — never restates the rule.

**Violating the letter of the rules is violating the spirit of the rules.**

<HARD-GATE>
Do NOT write any teammate agent .md content until you have:
1. Read the design documents that define this teammate (architecture spec,
   director pattern spec, extension points)
2. Listed which skills this teammate loads — read each skill to understand
   what domain knowledge is ALREADY covered (avoids duplication)
3. Listed which constraints are enforced by hooks and state machines
   (the body references WHY, never restates WHAT)
4. Identified this teammate's sub-agents, cross-domain partners, and
   coordination protocols from the design docs

If any input is missing, ask before proceeding. An agent definition written
without understanding the surrounding infrastructure will conflict with it.
</HARD-GATE>

## Quick Reference

| Decision | Route |
|----------|-------|
| Is this a persistent, event-driven agent with cross-domain messaging? | This skill (teammate) |
| Is this a bounded, fire-and-forget agent with input/output contract? | Use `writing-sub-agents` skill |
| Does this constraint get enforced by a hook or state machine? | Don't restate it — explain WHY in the body |
| Is this domain expertise the agent needs for most tasks? | Put it in a skill listed in `skills:` frontmatter |
| Is this domain expertise needed occasionally? | Add a routing pointer in the body |
| Does the description summarize the agent's workflow? | Rewrite — CSO Rule: WHEN to delegate, not HOW it works |
| Is the body exceeding 400 lines? | Move domain content into skills or references |
| Could a narrative backstory replace 5 explicit instructions? | Use backstory — agents internalize identity better than checklists |

## The Two Documents You're Translating

Teammate .md files translate two source documents into an operational agent definition:

1. **Architecture spec** — defines WHAT the agent is: role, sub-agents, skills, tools, model, cross-domain relationships. This becomes the **frontmatter** and the **structural sections** of the body.

2. **Director pattern spec** — defines HOW the agent specializes universal behavior: extension points, delegation composition, synthesis output, domain verification, self-execute boundaries. This becomes the **behavioral sections** of the body.

The translation is lossy by design. The design docs contain implementation detail for hooks, state machines, and daemon infrastructure. The agent .md extracts only what the agent needs to reason about. Everything else stays in infrastructure.

### What Gets Translated vs What Gets Dropped

| Design Doc Content | Goes Into Agent .md? | Why |
|-------------------|---------------------|-----|
| Role identity, mission, backstory | **Yes** — body section 1 | Agent needs to know who it is |
| Decision frameworks (think prompt questions) | **Yes** — body section 2 | Agent needs to know how to reason |
| Sub-agent delegation composition | **Yes** — body section 3 | Agent needs to know what context to include when delegating |
| Cross-domain coordination protocols | **Yes** — body section 4 | Agent needs to know who to message and when |
| Output contracts and Definition of Done | **Yes** — body section 5 | Agent needs to know what "done" looks like |
| Self-execute vs delegate boundaries | **Yes** — body section 6 | Agent needs to know when NOT to spawn a sub-agent |
| State machine transitions | **No** — infrastructure | Daemon handles state routing |
| Hook enforcement rules | **No** — infrastructure | Hooks enforce structurally; body explains WHY |
| Guard conditions | **No** — infrastructure | State machine guards, not prose |
| Validation scripts | **No** — infrastructure | Scripts run mechanically |
| Token budgets, model costs | **No** — frontmatter only | `model:` field handles this |
| File access globs | **No** — hooks | PreToolUse hooks enforce file boundaries |
| Think prompt formatting | **No** — daemon injects | Critical annotations inject think prompts |

## Teammate .md Canonical Structure

### Frontmatter

```yaml
---
name: teammate-identifier
description: >
  Delegation routing description. 2-3 sentences describing WHEN to
  send work here. Include trigger phrases. Never describe HOW the
  agent works internally. Apply CSO Rule.
tools: [tool list — minimum viable set]
model: opus
skills:
  - skill-1    # Domain expertise this agent needs for most tasks
  - skill-2    # Only always-needed skills — occasional skills load on-demand
memory: user
hooks:
  PreToolUse:
    - matcher: "relevant-pattern"
      hooks:
        - type: command
          command: "./scripts/relevant-enforcement.sh"
---
```

#### Frontmatter Field Rules

**`name`** — Descriptive, hyphenated. The name becomes invocable. Good: `codebase-explorer`, `security-auditor`. Bad: `agent-1`, `helper`.

**`description`** — The most consequential field. This is the ONLY thing the orchestrator or lead sees before deciding to delegate. Rules:

| Do | Don't |
|----|-------|
| Describe WHEN to send work here | Describe HOW the agent processes work |
| Include 3-5 natural trigger phrases | List internal workflow steps |
| Be action-oriented ("Analyzes codebase structure...") | Be passive ("A helper that...") |
| Include negative boundaries ("Do NOT use for...") | Omit competing agent discrimination |
| Make it "pushy" — agents undertrigger by default | Make it conservative |

CSO Rule: If the description says "dispatches scouts, synthesizes findings, writes context packets" the orchestrator mentally checks those boxes and skips reading the body. The description should say "Use when codebase context is needed for planning, implementation, or debugging."

**`tools`** — Principle of Least Privilege. This is enforcement level 1 in the hierarchy.

| Teammate Type | Typical Tools |
|--------------|---------------|
| Read-heavy (Explorer, Researcher) | Read, Grep, Glob, WebSearch, WebFetch |
| Planning (Planner) | Read, Grep, Glob, Write, Edit |
| Implementation-directing (Coder) | Read, Write, Edit, Bash, Glob, Grep, Agent |
| Testing (Tester) | Read, Write, Edit, Bash, Glob, Grep, Agent |
| Audit (Auditor) | Read, Grep, Glob, Bash, Agent |

Teammates that dispatch sub-agents need the `Agent` tool. Teammates that only message peers do NOT need `Agent` — messaging uses a different mechanism.

**`model`** — Teammates run on `opus` (complex reasoning, synthesis, multi-turn coordination). Sub-agents run on `sonnet` or `haiku`. Don't use `inherit` for teammates — they need consistent reasoning quality.

**`skills`** — Only list skills the teammate needs for the MAJORITY of its tasks. Skills loaded here persist through `/clear` and cost tokens on every LLM call. Use this test: "If this teammate received 10 random tasks in its domain, would it need this skill for 8+ of them?" If yes, list it. If no, add a routing pointer in the body instead.

**`memory`** — `user` is the recommended default (cross-project knowledge). Use `project` when knowledge is codebase-specific. Memory gives the agent a persistent directory it can read/write across sessions.

**`hooks`** — Structural enforcement scoped to this agent's lifecycle. The body should NOT restate what hooks enforce. Instead, the body explains the purpose:

```markdown
# In the body — explains WHY, doesn't restate the rule
Your tools are scoped to read-only operations. Your job is to
observe and synthesize, not to modify. This constraint exists
because your findings inform other agents' work — modifying code
while auditing it would compromise independence.
```

### Body Structure

The body becomes the agent's system prompt. Every token persists for the agent's entire lifetime. Target: **200-350 lines**.

The body has 7 sections. Order matters — agents read top-to-bottom and attention degrades. Front-load identity and mission. Put reference-heavy content last.

#### Section 1: Identity & Mission (5-15 lines)

Who you are, what you optimize for, and a narrative backstory that anchors behavioral tone.

The identity section does three things:
1. **States the role** in 1-2 sentences
2. **States the mission** — what this agent optimizes for (quality? speed? coverage? independence?)
3. **Provides backstory** — a narrative anchor that implies behavioral standards

Backstory is not flavor text. Research shows agents with narrative identity ("With a reputation for finding flaws everyone else misses") produce more consistent behavior than agents given equivalent instruction lists. The backstory acts as an implicit constraint — an adversarial auditor naturally produces more thorough output than one told "be thorough."

```markdown
# Example identity section

You are the quality authority for this project. Your purpose is to
independently verify that implementation matches design intent and
that code meets production standards.

You have reviewed thousands of codebases and have a reputation for
catching the subtle issues — the race condition hiding behind a
clean API, the edge case that passes unit tests but fails in
production. You don't trust reports; you verify artifacts directly.
```

**What NOT to put here:** Tool lists, skill inventories, state machine descriptions. Those belong in frontmatter or infrastructure.

#### Section 2: Decision Frameworks (30-80 lines)

How the agent reasons through its core decisions. This is the highest-value section — it defines the agent's judgment.

Translate think prompt questions from the design docs into decision frameworks. Don't copy think prompts verbatim — the daemon injects those as critical annotations. Instead, encode the *reasoning patterns* the agent should follow.

Structure decision frameworks as tables or decision trees, not prose paragraphs. Agents parse structured formats more reliably (writing-patterns principle: "Decision Tables Over Prose").

**Types of decision frameworks by teammate role:**

| Teammate | Core Decisions | Framework Style |
|----------|---------------|-----------------|
| Explorer | Reuse vs explore? Haiku vs Sonnet per scout? Self-execute vs delegate? | Conditional routing table |
| Researcher | Cache vs fresh research? Depth calibration? Strategy fallback? | Depth-to-action table |
| Planner | Grounding complete? Phase boundaries? Task granularity? | Question-driven checklist |
| Coder | Which sub-agent phase next? Debugger vs re-dispatch? Accept partial? | State-action decision tree |
| Tester | Coverage strategy? Tier allocation? Progress vs stall? | Threshold-based routing |
| Auditor | Self-investigate vs dispatch? Severity classification? Arbitration? | Evidence-to-verdict matrix |

#### Section 3: Sub-Agent Delegation (20-50 lines)

What sub-agents this teammate dispatches, when to dispatch each, and what context to include in delegation prompts.

This section answers three questions:
1. **Which sub-agents can I dispatch?** (from `AUTHORIZED_SUB_AGENTS`)
2. **What context goes into each delegation prompt?** (from `DELEGATION_COMPOSITION`)
3. **What do I check when the sub-agent returns?** (semantic review — hooks handle structural validation)

Structure as a per-sub-agent template:

```markdown
## Delegating to [sub-agent-type]

**When:** [trigger condition]
**Model:** [haiku/sonnet and why]

**Include in delegation prompt:**
1. [Specific context item with source]
2. [File paths — be explicit, don't make the sub-agent search]
3. [Return format expectation]

**On return, verify:**
- [Semantic check the hook can't do]

**If partial return:**
- [Decision framework for accept/re-dispatch/self-investigate]
```

Don't duplicate the sub-agent-delegation skill's dispatch lifecycle. That skill teaches HOW to delegate generally. This section teaches WHAT specific context each sub-agent type needs from THIS teammate.

#### Section 4: Coordination Protocol (20-40 lines)

Who this teammate messages, what types, and when.

Structure as a partner table:

```markdown
## Cross-Domain Partners

| Partner | I Send | I Receive | When |
|---------|--------|-----------|------|
| Explorer | Context request | Context packet path | Need codebase context |
| Coder | Audit findings | Audit request | Task completion |
```

Include message schemas only if not already in a loaded skill. Otherwise, point to the skill.

#### Section 5: Output Contracts (15-30 lines)

What this teammate produces and Definition of Done.

Every teammate must have explicit completion criteria — the single most effective anti-rationalization measure.

Structure as a deliverable checklist with Definition of Done per output type.

#### Section 6: Self-Execute Boundaries (10-20 lines)

When to work directly vs delegate. Express as a simple heuristic:

```markdown
**Self-execute** when you know the question AND PTC answers it
in 1-2 commands.

**Delegate** when the task requires reading 50+ lines across
multiple files or multi-step analysis to synthesize understanding.
```

#### Section 7: Skill Routing Pointers (5-15 lines)

Conditional loading for skills not in frontmatter:

```markdown
## Additional Skills (Load On Demand)
- For handoff preparation → read `handoff-protocol` skill
- For multi-perspective analysis → read `multi-perspective-analysis` skill
```

## The Enforcement Hierarchy for Agent Bodies

| Level | Mechanism | Agent Body's Role |
|-------|-----------|-------------------|
| 6 | Scripts (validation, quality gate) | Don't mention — runs automatically |
| 5 | State machine, structural dependencies | Don't mention — daemon handles routing |
| 5 | Hook enforcement (file globs, tool blocking) | Explain WHY the constraint exists |
| 4 | Anti-pattern examples (in skills) | Point to skill references |
| 3 | Explained reasoning | **Primary level for agent body** |
| 2 | Imperative commands | Minimize — weak enforcement |
| 1 | Conventions | Avoid — no enforcement power |

**Litmus test:** "If I deleted this line, would behavior change?" If no — because a hook/state machine/skill covers it — delete it.

## Token Budget

| Section | Target Lines | Notes |
|---------|-------------|-------|
| Identity & Mission | 5-15 | Front-loaded, always relevant |
| Decision Frameworks | 30-80 | Highest value per token |
| Sub-Agent Delegation | 20-50 | Critical for delegation quality |
| Coordination Protocol | 20-40 | Tables are compact |
| Output Contracts | 15-30 | Completion criteria |
| Self-Execute Boundaries | 10-20 | Simple heuristic |
| Skill Routing | 5-15 | Only if needed |
| **Total** | **105-250** | **Target: 200 typical, 350 max** |

## Agent Description CSO Rule

**CSO Violation Test:** Read the description aloud. If you can mentally check off "task done" from the description alone — without reading the body — it leaks workflow.

```yaml
# WRONG — workflow leak
description: >
  Dispatches codebase-scout sub-agents to explore partitions of the
  codebase, synthesizes their findings into structured context packets,
  and writes them to .claude/context/.

# RIGHT — trigger conditions only
description: >
  Use when codebase context is needed for planning, implementation,
  debugging, or research grounding. Provides structured codebase analysis.
  Do NOT use for: external research (Researcher), plan creation (Planner).
```

## Teammate Writing Checklist

**Frontmatter:**
- [ ] Description follows CSO Rule — no workflow leaks
- [ ] Description includes trigger phrases and negative boundaries
- [ ] Tools are minimum viable set
- [ ] Model is `opus`
- [ ] Skills are needed for 80%+ of tasks
- [ ] Hooks enforce structural constraints (not duplicated in body)

**Body:**
- [ ] Identity states role, mission, and behavioral backstory
- [ ] Decision frameworks are tables/trees, not prose paragraphs
- [ ] Each sub-agent has a delegation template (WHAT context, not HOW to delegate)
- [ ] Partner table covers all cross-domain relationships
- [ ] Output contracts have Definition of Done checklist
- [ ] Self-execute boundary expressed as clear heuristic
- [ ] Under 350 lines total
- [ ] Every constraint has a WHY
- [ ] No infrastructure restated (hooks, state machine, scripts)
- [ ] No skill content duplicated

## Anti-Rationalization

| Excuse | Reality |
|--------|---------|
| "The design doc is so detailed, I'll just convert it directly" | Design docs contain infrastructure specs. Direct conversion produces bloated agents. Extract only what the agent reasons about. |
| "I'll include state machine transitions so the agent understands flow" | The daemon manages state. Including transitions makes the agent try to self-manage instead of responding to routing. |
| "The delegation section needs the full prompt template" | The sub-agent-delegation skill teaches prompt composition. The body says WHAT context, not HOW to format it. |
| "I'll add this constraint as prose since the hook might not catch it" | If the hook doesn't catch it, fix the hook. Prose is level 2 — rationalized away under pressure. |
| "This skill content is so important I'll repeat it in the body" | Duplication wastes tokens and creates divergence risk. The agent loads the skill — trust it. |
| "The body is 500 lines but all content is essential" | If 500 lines are essential for every task, your skill decomposition is wrong. Move domain knowledge into skills. |

**Red Flags — STOP:**
- Body exceeding 350 lines without explicit justification
- State machine transitions, guard conditions, or validation scripts in the body
- Same rule in both the body AND a hook/skill
- Description containing workflow language ("then", "after", "dispatches", "synthesizes")
- Decision framework written as prose paragraph instead of table
- No Definition of Done in output contracts

## References

**Within this skill's references/:**
- For reusable structural patterns (backstory templates, decision framework types, delegation composition templates, coordination archetypes, output contract patterns, self-execute heuristics, skill coverage audit, infrastructure boundary detection) → read `references/patterns.md`
- For 12 expanded failure modes with diagnostic criteria and concrete WRONG/RIGHT fixes (Design Doc Converter, State Machine Narrator, Skill Duplicator, Hook Restater, Prose Decision Maker, Kitchen Sink, and more) → read `references/anti-patterns.md`
- For full WRONG/RIGHT examples per body section (frontmatter, identity, decision frameworks, delegation, coordination, output contracts, self-execute, skill routing) plus a complete annotated teammate .md → read `references/complete-examples.md`
- For the step-by-step design-doc-to-.md translation pipeline (inventory inputs, separate agent-relevant from infrastructure, check skill coverage, write frontmatter, write body, audit for leakage) → read `references/translation-walkthrough.md`

**Cross-skill references:**
- For writing patterns (WHY-driven, tables over prose, WRONG/RIGHT pairs, conciseness) → read `writing-skills` references
- For anti-rationalization architecture (3-layer defense) → read `writing-skills` references/anti-rationalization-architecture.md

**External references:**
- For teammate critique and review methodology → see standalone `agent-critique-guide.md` (used in chat sessions, not loaded by agents)
- For sub-agent .md files → use the `writing-sub-agents` skill
