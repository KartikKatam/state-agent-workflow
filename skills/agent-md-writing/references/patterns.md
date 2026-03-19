# Teammate Agent .md Design Patterns

Reusable structural patterns for teammate .md writing. For concrete WRONG/RIGHT examples per section, see `complete-examples.md`. For the design-doc-to-.md translation process, see `translation-walkthrough.md`.

---

## Table of Contents

1. [Backstory Patterns That Shape Behavior](#backstory)
2. [Decision Framework Structural Types](#framework-types)
3. [Delegation Composition Templates](#delegation-templates)
4. [Coordination Protocol Patterns](#coordination-patterns)
5. [Output Contract Patterns](#output-patterns)
6. [Self-Execute Boundary Heuristics](#self-execute-heuristics)
7. [Skill Coverage Audit Method](#skill-audit)
8. [Infrastructure Boundary Detection](#infra-boundary)

---

## 1. Backstory Patterns That Shape Behavior {#backstory}

Backstory is the most token-efficient behavioral enforcement. Two sentences of narrative can replace five explicit instructions by establishing implicit standards the agent internalizes.

### The Effectiveness Test

Ask: "Does removing this backstory change how the agent behaves?" If yes, it's earning its tokens. If no, it's flavor text — delete it.

### Backstory Templates by Behavioral Goal

| Goal | Backstory Pattern | What It Replaces |
|------|-------------------|------------------|
| **Thoroughness** | "You have a reputation for catching the subtle issues — the race condition hiding behind a clean API" | "Be thorough", "Check all edge cases", "Don't miss anything" |
| **Conservatism** | "You have a bias toward verified evidence over inference — if you can't read the code, you say 'unverified'" | "Always include confidence scores", "Mark uncertain findings" |
| **Independence** | "You don't trust reports; you verify artifacts directly" | "Always verify sub-agent results", "Don't take claims at face value" |
| **Adversarial** | "You assume the implementation is wrong until evidence proves otherwise" | "Look for bugs", "Be critical", "Don't be lenient" |
| **Precision** | "You produce plans that coders can execute without asking questions" | "Be specific", "Include all details", "Don't be vague" |
| **Pragmatism** | "You make decisions even with incomplete information, documenting uncertainty rather than blocking" | "Don't ask too many questions", "Make progress even when uncertain" |

### Combining Backstory Traits

Most teammates need 2-3 traits. Combine them in a single paragraph:

```markdown
You are the quality authority for this project. You have reviewed
thousands of codebases and catch the subtle issues — race conditions,
edge cases that pass unit tests but fail in production. You don't
trust reports; you verify artifacts directly. When evidence is
ambiguous, you flag it rather than guess.
```

This paragraph encodes: thoroughness (catch subtle issues), independence (verify directly), and conservatism (flag ambiguity). Three behavioral constraints in four sentences.

### Mission Statement Pattern

The mission statement is separate from backstory. It states WHAT the agent optimizes for:

| Teammate | Mission Statement |
|----------|-------------------|
| Explorer | "Your analysis is what teammates build decisions on" |
| Researcher | "You find the answer the team would find with unlimited time" |
| Planner | "You produce plans coders execute without asking questions" |
| Coder | "You orchestrate sub-agents to produce code that passes blind testing" |
| Tester | "You design tests that catch bugs the implementation team doesn't know exist" |
| Auditor | "You are the last line before code reaches production" |

---

## 2. Decision Framework Structural Types {#framework-types}

Design docs contain think prompt questions. The body needs decision frameworks. Translation pattern: think prompt questions → structural decision format.

### Type 1: Routing Table

Best for decisions with clear conditions and discrete actions. Each row is a complete if-then.

```markdown
| Condition | Action | Why |
|-----------|--------|-----|
| [observable] | [concrete action] | [consequence if wrong] |
```

**Use when:** The agent faces a categorical decision with 3-8 distinct paths. Most common framework type.

**Example domains:** Self-execute vs delegate, model selection per sub-agent, context reuse vs fresh exploration, audit severity routing.

### Type 2: Decision Tree

Best for sequential decisions where each narrows the next. Visual branching.

```markdown
Is X true?
├── Yes → Is Y also true?
│   ├── Yes → Action A
│   └── No → Action B
└── No → Action C
```

**Use when:** Decisions are nested — the second question depends on the first answer. Usually 2-3 levels deep.

**Example domains:** Audit finding triage (severity → scope → action), error classification (transient → structural → environmental), result review (pass/fail → failure type → next step).

### Type 3: Threshold Matrix

Best for continuous variables mapped to discrete actions. Numbers-based.

```markdown
| Metric | Threshold | Below | Above | Rationale |
|--------|-----------|-------|-------|-----------|
| [metric] | [value] | [action] | [action] | [why this threshold] |
```

**Use when:** The agent monitors a numeric signal (stall count, confidence score, iteration count, coverage percentage) and routes based on thresholds.

**Example domains:** Stall detection, confidence scoring, coverage assessment, iteration budgets.

### Type 4: Evidence-to-Verdict Matrix

Best for synthesis decisions where multiple signals combine into a judgment. Matrix intersection.

```markdown
| Signal A \ Signal B | B: Strong | B: Weak | B: Absent |
|---------------------|-----------|---------|-----------|
| **A: Strong** | High confidence → proceed | Mixed → investigate B | Partial → note gap |
| **A: Weak** | Mixed → investigate A | Low confidence → escalate | Insufficient → abort |
```

**Use when:** The agent synthesizes multiple evidence sources into a single judgment. Common for auditors and planners.

### Choosing the Right Type

| Design Doc Pattern | Framework Type |
|-------------------|----------------|
| "If X, do A. If Y, do B. If Z, do C." | Routing table |
| "Check X first. If yes, then check Y. Based on both..." | Decision tree |
| "When the counter reaches N..." or "If confidence is below X..." | Threshold matrix |
| "Weigh these factors together to decide..." | Evidence-to-verdict matrix |

---

## 3. Delegation Composition Templates {#delegation-templates}

The teammate body says WHAT context each sub-agent needs. The sub-agent-delegation skill says HOW to compose prompts. The template pattern bridges them.

### Per-Sub-Agent Template Structure

```markdown
## Delegating to [sub-agent-type]

**When:** [1-sentence trigger — matches a row in the decision framework]
**Model:** [haiku/sonnet with brief rationale]

**Include in delegation prompt:**
1. [Context item] — from [source: plan, packet, design doc, previous return]
2. [Context item] — from [source]
3. [Specific file paths] — don't make the sub-agent search
4. [Output format pointer] — what the return should contain

**On return, verify (semantic — hooks handle structural):**
- [Check 1: something requiring judgment]
- [Check 2: something about content quality]

**If partial return:**
- [Decision: accept / re-dispatch narrower / self-investigate]
```

### Semantic Review Patterns

What the parent checks that hooks can't:

| Sub-Agent Type | Hook Validates | Parent Validates |
|---------------|---------------|-----------------|
| codebase-scout | Schema, required fields, confidence present | Findings actually cover the partition, confidence not inflated |
| test-writer | Schema, files exist, quality gate | Tests fail for RIGHT reasons (behavioral, not infrastructure) |
| implementer | Schema, files exist, tests pass | Decisions stay within plan scope |
| audit-checker | Schema, verdict consistency | Findings match actual code (not hallucinated references) |
| plan-checker | Schema, all dimensions checked | Verification is substantive (not rubber-stamped) |

### Partial Return Decision Pattern

```markdown
On partial return, check carry_forward:
1. Essential outputs covered? → Accept partial, note gaps
2. Essential outputs missing, gap is small? → Self-investigate via PTC
3. Essential outputs missing, gap requires exploration? → Re-dispatch narrower scope
```

---

## 4. Coordination Protocol Patterns {#coordination-patterns}

### The Partner Table

Minimal complete representation of cross-domain relationships:

```markdown
| Partner | I Send | I Receive | When |
|---------|--------|-----------|------|
| [name] | [type]: [content summary] | [type]: [content summary] | [trigger] |
```

### Relationship Archetypes

| Archetype | Example | Protocol |
|-----------|---------|----------|
| **Provider-Consumer** | Explorer → Planner (context) | Provider sends `info_ready` when product available; consumer reads it |
| **Request-Response** | Planner → Explorer (context request) | Requester sends `info_request` with what/why; provider delivers |
| **Bidirectional Advisory** | Coder ↔ Auditor (audit cycle) | Request → findings → fixes → re-request, until approved |
| **Escalation** | Tester → Auditor (plan misalignment) | Escalator provides evidence; authority investigates and rules |
| **Parallel Notification** | Auditor → Coder + Tester (arbitration) | Authority sends ruling to both parties simultaneously |

### When NOT to Include Message Schemas

If the team-messaging protocol is in a loaded skill (e.g., `sub-agent-delegation`), don't duplicate schemas in the body. Point to the skill:

```markdown
For message format and delivery mechanics, see the team-messaging
protocol in the sub-agent-delegation skill.
```

Only include schemas in the body when they're agent-specific (not shared protocol).

---

## 5. Output Contract Patterns {#output-patterns}

### Definition of Done Template

```markdown
## Definition of Done for [output type]

- [ ] [Verifiable condition 1]
- [ ] [Verifiable condition 2]
- [ ] [Verifiable condition 3]
- [ ] [Artifact written to specific path]
- [ ] [Downstream consumer notified]
```

Every item must be independently verifiable — not "thorough analysis" but "all partitions have findings."

### Multi-Mode Output Pattern

When a teammate handles multiple request types:

```markdown
## What I Produce

| Mode | Output | Location | DoD |
|------|--------|----------|-----|
| Per query | Context packet | `.claude/context/` | Schema valid, all partitions covered |
| Per session | Termination log | `.claude/logs/` | Decisions, dispatches, versions recorded |
| On handoff | Handoff summary | `.claude/handoffs/` | State preserved for replacement |
```

---

## 6. Self-Execute Boundary Heuristics {#self-execute-heuristics}

### The Universal PTC Heuristic

Works for any teammate with PTC container access:

```
Self-execute: know the question + PTC answers in 1-2 commands
Delegate: requires reading 50+ lines across files OR multi-step synthesis
```

### Domain-Specific Boundaries

| Teammate | Self-Execute | Delegate |
|----------|-------------|---------|
| Explorer | File existence, specific signatures, import verification, git log | Multi-file analysis, dependency tracing, pattern discovery |
| Researcher | Simple factual lookups, API signature retrieval, cache checks | Multi-facet research, comparative analysis, broad docs |
| Planner | PTC codebase lookups for grounding, plan text writing, JSON conversion | Independent plan verification (plan-checker) |
| Coder | Evaluate returns, compose prompts, merge conflicts, task tracking | All code writing, debugging, optimization |
| Tester | Run tests, interpret results, design scenarios, merge conflicts | Scenario code writing, test infrastructure repair, plan checking |
| Auditor | All rulings/verdicts, PTC queries, reading returns, writing reports | Systematic code reading, debugging, optimization |

---

## 7. Skill Coverage Audit Method {#skill-audit}

Before writing the body, audit what loaded skills already cover:

```
For each loaded skill:
1. Read the skill's SKILL.md body
2. List the domain knowledge it provides
3. Mark any content you planned for the body as "COVERED BY SKILL"
4. Delete covered content from the body draft

What remains: agent-level judgment the skill doesn't provide
```

### Common Coverage Overlaps

| Body Content | Likely Covered By |
|-------------|-------------------|
| Exploration methodology, confidence scoring | codebase-exploration skill |
| Research decomposition, depth calibration | research-methodology skill |
| Plan decomposition, grounding, gap resolution | phase-planning skill |
| Delegation lifecycle, context sizing, escalation | sub-agent-delegation skill |
| Review checklists, severity framework | code-review skill |
| Test tier patterns, verification pipeline | scenario-testing skill |
| Handoff sender/receiver protocol | handoff-protocol skill |

---

## 8. Infrastructure Boundary Detection {#infra-boundary}

### Quick Tests for "Does This Belong in the Body?"

| Test | If Yes → | If No → |
|------|----------|---------|
| Does a hook enforce this? | Body explains WHY only | Body may include as decision framework |
| Does the state machine route this? | Don't mention it | Body may include as decision framework |
| Does a script validate this? | Don't mention it | Body may include as quality criterion |
| Is this in a loaded skill? | Don't duplicate | Body may add agent-level judgment |
| Does this change between tasks? | Delegation prompt, not body | Body (stable across tasks) |

### The Deletion Audit

After drafting the body, read each paragraph and ask:
1. "If I delete this, does agent behavior change?"
2. "If yes, is that because the body is the ONLY place this is enforced?"
3. "If no, something else enforces it — delete from body."

Target: every surviving paragraph passes all three questions.
