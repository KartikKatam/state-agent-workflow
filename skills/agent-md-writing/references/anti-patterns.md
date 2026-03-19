# Teammate Agent .md Anti-Patterns

Reference file for `writing-teammate-agents`. Expanded failure modes observed in agent definition writing, with concrete WRONG/RIGHT examples and diagnostic criteria.

---

## Table of Contents

- [Teammate Agent .md Anti-Patterns](#teammate-agent-md-anti-patterns)
  - [Table of Contents](#table-of-contents)
  - [1. The Design Doc Converter {#1}](#1-the-design-doc-converter-1)
  - [2. The State Machine Narrator {#2}](#2-the-state-machine-narrator-2)
  - [3. The Skill Duplicator {#3}](#3-the-skill-duplicator-3)
  - [4. The Hook Restater {#4}](#4-the-hook-restater-4)
  - [5. The Prose Decision Maker {#5}](#5-the-prose-decision-maker-5)
  - [6. The Kitchen Sink Agent {#6}](#6-the-kitchen-sink-agent-6)
  - [7. The Personality Without Purpose {#7}](#7-the-personality-without-purpose-7)
  - [8. The Orphaned Coordinator {#8}](#8-the-orphaned-coordinator-8)
  - [9. The Underdefined Delegator {#9}](#9-the-underdefined-delegator-9)
  - [10. The Overconstrained Agent {#10}](#10-the-overconstrained-agent-10)
  - [11. The Workflow-Leaking Description {#11}](#11-the-workflow-leaking-description-11)
  - [12. The Missing Definition of Done {#12}](#12-the-missing-definition-of-done-12)

---

## 1. The Design Doc Converter {#1}

**Symptom:** Body > 400 lines. Contains "Validation Rules", "Guard Conditions", "File Access Globs", or "State Machine" sections.

**Cause:** Design doc converted to agent body without filtering out infrastructure.

```markdown
// WRONG — design doc content in body (excerpt)
## Validation Rules (SubagentStop Hook)
| # | Check | Rule | Severity |
|---|-------|------|----------|
| 1 | Structure | JSON with delegation_type: "exploration" | Block |
| 2 | Essential output | essential_output covered | Block |

## File Access Globs
| Direction | Glob | Purpose |
|-----------|------|---------|
| Write allow | .claude/context/** | Context packets |
| Write deny | src/** | Never writes source |

// RIGHT — agent focuses on judgment, not infrastructure
## On Scout Return
Verify findings cover the full assigned partition and that
confidence scores vary naturally. The hook validates schema
and required fields — your job is judging whether the CONTENT
is good, not whether the FORMAT is correct.
```

**Fix:** For each line: "Does the agent reason about this, or does infrastructure handle it?" Delete everything infrastructure handles. Typical reduction: 50-70%.

---

## 2. The State Machine Narrator {#2}

**Symptom:** ALL_CAPS state names, transition arrows (→), "when you enter", "transition to", "move to the next state."

**Cause:** State machine treated as agent knowledge instead of infrastructure routing.

```markdown
// WRONG
When you receive a query:
1. CONTEXT_LOADING: Load existing context packets
2. DELIBERATION: Decide reuse vs explore
3. SUB_AGENT_DISPATCH: Spawn scouts
4. SYNTHESIS: Consolidate results

// RIGHT
When you receive a query:
1. Check what context already exists
2. Decide: reuse, partial re-explore, or full explore
3. If exploring: partition, dispatch scouts, synthesize results
4. Validate and deliver the context packet
```

**Why RIGHT works:** Same logical sequence without state awareness. The daemon routes the agent through states anyway. Agent attention goes to DECISIONS, not flow management.

---

## 3. The Skill Duplicator {#3}

**Symptom:** Body paragraphs substantially similar to loaded skill content.

**Cause:** Distrust of skill loading — author duplicates "just in case."

```markdown
// WRONG — codebase-exploration skill duplicated
## Exploration Methodology
Follow the 6-point checklist:
1. Module responsibilities and boundaries
2. Type definitions and data structures
3. Dependency graph...
Use confidence scoring: direct evidence 0.8-1.0...

// RIGHT — reference skill, add agent-level judgment
Your domain expertise comes from the codebase-exploration skill.
Your unique contribution beyond the skill: cross-query pattern
recognition. If the same module appears across multiple queries,
proactively build comprehensive context for it.
```

**Fix:** Delete duplicated content. Reserve body for agent-level judgment the skill doesn't cover.

---

## 4. The Hook Restater {#4}

**Symptom:** "Never X" / "Always Y" where a hook already enforces X/Y.

```markdown
// WRONG
Never write to files outside .claude/context/.
Always run schema validation on context packets.
Never modify source code.

// RIGHT
Your tools are scoped to context packet writes only — touching
source while analyzing it would compromise separation between
understanding and modifying.

Packets are schema-validated on every write. If validation
fails, you'll get feedback on what's wrong.
```

**Fix:** Replace "Never X" with "Your tools are scoped to Y because Z." Hook enforces WHAT; body explains WHY.

---

## 5. The Prose Decision Maker {#5}

**Symptom:** Multi-sentence paragraphs with "if...then" logic, "when X do Y, but when Z do W instead."

```markdown
// WRONG
When you receive a result from a scout, evaluate whether findings
are complete. If the scout covered all files and confidence looks
reasonable, proceed to synthesis. However, if the scout missed some
files, or if confidence seems uniformly high, consider re-dispatching...

// RIGHT
| Return Condition | Action | Why |
|-----------------|--------|-----|
| Full coverage, varied confidence | Accept → synthesize | Good data |
| Uniformly high confidence | Re-dispatch with "verify [area]" | Inflation risk |
| Missing files from partition | Re-dispatch for missed files | Targeted gap fill |
| Partial, essentials covered | Accept partial, note gaps | Essential data present |
| Partial, essentials missing | Re-dispatch for essentials | Critical gaps |
| Failed | Check error, re-dispatch corrected | Transient failure likely |
```

**Why tables win:** All options visible together — prevents cherry-picking. Parse-reliable for the LLM. Each row has a WHY column.

---

## 6. The Kitchen Sink Agent {#6}

**Symptom:** 8+ skills loaded, all tools enabled, 400+ line body, decision frameworks with >10 rows for edge cases.

**Fix:** Apply 80/20 rule. Keep 2-3 core skills. Move edge cases to reference files or handle via escalation. Excellent at 80% of tasks beats adequate at 100%.

---

## 7. The Personality Without Purpose {#7}

**Symptom:** Identity > 15 lines. Decision frameworks < 20 lines or absent.

**Cause:** Elaborate backstory without operational guidance.

**Fix:** Compress backstory to 2-3 sentences that imply behavioral standards. Invest tokens in decision frameworks. Backstory should be ~10% of body, not 30%.

---

## 8. The Orphaned Coordinator {#8}

**Symptom:** Message schemas don't match partner agents. Delegation templates include fields sub-agents don't recognize.

**Fix:** Integration audit. For each partner table row, verify the other side expects that message type. For each delegation template, verify the sub-agent's input contract accepts those fields.

---

## 9. The Underdefined Delegator {#9}

**Symptom:** "Delegate to scout when needed" — no context guidance, no semantic review criteria, no partial return handling.

**Cause:** Assumes sub-agents are smart enough to figure out what they need.

**Fix:** For each sub-agent: (1) what context in delegation prompt, (2) what to verify beyond hook validation, (3) what to do with partial returns.

---

## 10. The Overconstrained Agent {#10}

**Symptom:** More "Never do X" than positive decision frameworks.

**Fix:** Convert prohibitions to decision framework rows. Hook-enforced prohibitions get deleted entirely — body explains WHY, not WHAT.

---

## 11. The Workflow-Leaking Description {#11}

**Symptom:** Description contains "dispatches", "synthesizes", "first...then...finally."

**Cause:** CSO Rule violation — describing HOW the agent works instead of WHEN to use it.

**Fix:** Replace workflow with trigger conditions. WHAT the agent provides, not HOW.

---

## 12. The Missing Definition of Done {#12}

**Symptom:** No verifiable checklist. Completion described as "when analysis is complete."

**Fix:** Add DoD where each item is independently verifiable. "All partitions have findings" is verifiable. "Thorough analysis completed" is not.
