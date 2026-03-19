---
name: task-handling
version: 1-0-0
triggers:
  - agent_role: "*"
    conditions: when receiving any assignment from orchestrator, before beginning domain-specific work
description: >
  Use when a director teammate receives an assignment from the orchestrator
  and needs to comprehend it before acting. Activates for: parsing task/phase
  assignments, declaring uncertainty, resolving unknowns by contacting
  Explorer/Researcher/orchestrator/user, and deciding readiness to proceed.
  Also use when an assignment seems simple (especially then — simple tasks
  hide assumptions). Do NOT use for: composing delegation prompts (use
  delegation skill), sub-agent dispatch mechanics (use delegation skill),
  domain-specific work (use code-design, scenario-testing, etc.), or
  quality gate execution (infrastructure).
---

# Task Handling

## Core Principle

**Understand before you act. Declare what you don't know before you act on what you do.** A director who delegates with unresolved uncertainties produces sub-agents that drift from requirements. Gaps discovered after delegation cost 10x more than gaps discovered before — the sub-agent's work gets thrown away.

**Violating the letter of the rules is violating the spirit of the rules.**

<HARD-GATE>
Do NOT compose any delegation prompt, design any test strategy, or
dispatch any sub-agent until you have:
1. Read your FULL assignment (all fields, not just the headline)
2. Produced a written uncertainty declaration (explicit know/don't-know)
3. Resolved all blocking uncertainties and explicit queries

If ANY condition is unmet, STOP and request what you need.
This applies to EVERY assignment regardless of perceived simplicity.
</HARD-GATE>

## Quick Reference

| Situation | Action |
|-----------|--------|
| Assignment has `queries` field with entries | Resolve ALL before proceeding — contact Explorer/Researcher/orchestrator as needed |
| You're unsure about a requirement | Add to uncertainty declaration, resolve before proceeding |
| Requirement seems wrong or impossible | Report to orchestrator with evidence — do NOT silently reinterpret |
| You want to skip uncertainty declaration | **STOP** — this is the most common failure mode. Simple tasks hide the worst assumptions |
| Mid-work: discover something contradicts your understanding | Pause. Update declaration. Resolve before continuing |
| Assignment references files/modules you haven't seen | Add to "don't know" — contact Explorer for context before acting |
| Resolution reveals assignment is based on wrong assumptions | Report to orchestrator with evidence — do NOT work around it |
| Orchestrator updates assignment mid-comprehension | Restart from Step 1 with updated assignment, re-validate declaration |
| Waiting for Explorer/Researcher response | Continue parsing and reading reference files. Do NOT compose delegation prompts or design strategy |

## Core Workflow

### Step 1: Parse Assignment

Read the full assignment from the orchestrator. Extract every field — not just the headline. Key fields vary by role (see `references/patterns.md` for role-specific field tables).

**Universal fields to look for in any assignment:**
- **Scope/context** — what larger goal this assignment serves
- **Requirements** — what must be done (becomes sub-agent success criteria)
- **Limitations** — what must NOT be done (pass through to delegation prompts verbatim)
- **Queries** — questions flagged by the planner (resolve before proceeding)
- **Reference material** — files to read for context (read BEFORE composing delegation prompts)

Step 1 produces: your mental model of the assignment. Step 2 tests this model for gaps.

### Step 2: Declare Uncertainty

Before any action, produce a written declaration with two columns:

**I KNOW (with source):**
- What you understand and where the understanding comes from
- Source must be: assignment field, context packet, reference file, or domain knowledge (state which)

**I DON'T KNOW / I'M UNSURE:**
- Anything unclear, ambiguous, or below "certain" confidence
- Each entry must be specific to THIS assignment and actionable

**An empty "don't know" list is a red flag, not competence.** Every assignment has unknowns. If you can't find any, you haven't examined deeply enough.

**Unknowns must be assignment-specific.** "Edge cases might exist" or "codebase may have changed" apply to every assignment and drive no resolution action — they're performative compliance. Each unknown should name a specific aspect of THIS assignment that you cannot verify from available context.

**Role-specific uncertainty patterns:**

| Role | Common Genuine Unknowns |
|------|------------------------|
| Coder | API signatures for modules I'll delegate work on? Existing code patterns the implementer should follow? Dependencies between this task and prior/parallel chunks? Are test_expectations complete or will the test-writer need to infer gaps? |
| Tester | Public API contracts for the module under test? Design doc behavioral specs detailed enough for scenario derivation? Which capabilities from the plan are already implemented and testable? Existing test infrastructure (conftest, fixtures) I should account for? |

Step 2 produces: a written declaration. Step 3 resolves the "don't know" entries.

### Step 3: Resolve Before Acting

For each "don't know" entry AND each assignment `queries` entry:

| Unknown Type | Resolution Path |
|-------------|----------------|
| Codebase structure, existing patterns, API signatures | Message **Explorer** — request context for specific modules |
| External library APIs, design patterns, compatibility | Message **Researcher** — request targeted research |
| Plan interpretation, requirement clarification | Message **orchestrator** directly |
| Ambiguous acceptance criteria, conflicting requirements | Escalate to **user** via orchestrator |
| File-level questions about known paths | Read the files yourself — no need to message anyone |

**Do NOT proceed until every query and blocking uncertainty is resolved.**

**Default: uncertainties are blocking** unless they ONLY affect naming, formatting, or documentation. If the uncertainty could change function signatures, control flow, error handling, data structures, test strategy, or scenario design — it's blocking.

WHY this default: Agents under pressure reclassify architectural uncertainties as "non-blocking" to proceed faster. "I can delegate with my best guess and the implementer will figure it out" sounds pragmatic but means the sub-agent builds on an unverified assumption. The rework when the assumption is wrong costs more than the resolution delay.

**While waiting for responses:** You may continue parsing assignment
fields and reading reference files. You may NOT compose delegation
prompts, design test strategy, or begin any domain-specific work
until all blocking unknowns are resolved.

**If resolution invalidates the assignment:** When a response reveals
the assignment is based on incorrect assumptions (e.g., Explorer
reports the referenced API was deprecated, or Researcher confirms
the library doesn't support the required feature), report to
orchestrator with evidence. Do NOT work around it — the plan may
need revision.

**If the orchestrator updates your assignment mid-comprehension:**
Restart from Step 1 with the updated assignment. Your existing
declaration is a useful starting point but must be re-validated
against the new fields.

Step 3 produces: all queries resolved, declaration updated. You are now ready to proceed to your domain-specific work (delegation, strategy design, etc.).

### Handoff to Delegation

Your resolved declaration maps directly to delegation prompt fields:

| Declaration Content | Delegation Prompt Field |
|--------------------|------------------------|
| "I KNOW" entries with file sources | `known_context.file_coordinates` |
| "I KNOW" entries with behavioral facts | `known_context.findings` |
| `limitations` from assignment | `scope_boundary.do_not` (pass through verbatim) |
| `test_expectations` from assignment | `test_specifications` in `tdd_chunk` prompts |
| Resolved queries that rejected an approach | `scope_boundary.rejected_approaches` |
| `requirements` mapped to success criteria | `success_criteria` |

This mapping is mechanical — don't re-derive it. Your comprehension work directly feeds the delegation skill's prompt composition.

### Time Budget

| Assignment Complexity | Comprehension Time |
|----------------------|-------------------|
| Simple (≤3 requirements, no queries) | ~2-3 minutes |
| Moderate (4-6 requirements, 1-2 queries) | ~5-8 minutes |
| Complex (7+ requirements, 3+ queries, cross-phase dependencies) | ~10-15 minutes |

If comprehension takes significantly longer, you're likely stuck on an unresolvable uncertainty — escalate rather than spin.

## Critical Rules

- **Uncertainty declaration is mandatory.** An empty "don't know" column means you haven't examined deeply enough. Every assignment has unknowns.
- **Queries block all action.** If the planner put it in `queries`, they flagged it deliberately. Don't self-answer — resolve through the right channel.
- **Limitations are hard boundaries.** Pass them through to sub-agents verbatim. Don't reinterpret, soften, or work around them.
- **Evidence, not confidence.** "I'm fairly sure the API supports this" is not resolution. Read the file, message the Explorer, or ask the Researcher. Then proceed with evidence.

## Anti-Rationalization

| Excuse | Reality |
|--------|---------|
| "This assignment is simple, no need to declare uncertainty" | Simple assignments are where assumptions hide. The declaration takes 30 seconds. Do it. |
| "I know this codebase, no unknowns" | Then "I know" is easy to fill. "Don't know" still needs entries — things you haven't verified from available context. |
| "The query is obvious, I can answer it myself" | If the planner put it in `queries`, they flagged it deliberately. Resolve through proper channels. |
| "I'll resolve this uncertainty once the sub-agent hits it" | The sub-agent will build on the assumption, produce wrong work, and you'll re-dispatch. Resolve now. |
| "The limitation doesn't really apply to my approach" | Limitations are bright lines. "Doesn't apply" is rationalization. Report to orchestrator if you genuinely believe it's inapplicable. |
| "I can start delegating while waiting for the Explorer response" | You'll compose the delegation prompt with incomplete context. Wait for the response, THEN compose. |

**Red Flags — STOP and re-check your process:**
- Composing a delegation prompt before completing uncertainty declaration
- Designing test strategy before resolving queries
- Self-answering queries without external verification
- Saying "should be fine" or "probably" about an unverified assumption
- Starting domain-specific work with an empty "don't know" column

## References

For uncertainty declaration templates and quality checks (both Coder and Tester examples), read `references/patterns.md`.

For common comprehension failures with WRONG/RIGHT examples (skipping declaration, self-answering queries, misclassifying blocking uncertainties), read `references/anti-patterns.md`.
