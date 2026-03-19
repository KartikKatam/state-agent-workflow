---
name: scenario-testing
version: 1-0-0
triggers:
  - agent_role: tester
    conditions: when designing scenario tests, planning test tiers, delegating test writing to sub-agents, or reporting test failures
description: >
  Use when designing scenario-level tests for a feature or phase. Activates for:
  planning 4-tier test coverage, delegating test writing to sub-agents,
  designing blind failure reports, or deciding test scope (per-task, per-phase,
  full-design). Also use when reviewing sub-agent-generated tests for quality,
  when uncertain about which tier a test belongs to, or when a coder requests
  clarification on a failure report.
  Do NOT use for: test design fundamentals (use test-design), running tests
  (infrastructure), debugging test failures (use debugging), quality gate
  execution.
depends_on:
  - test-design
---

# Scenario Testing

## Core Principle

The tester who writes scenario tests must be **blind to the implementation**. Tests derived from specifications catch different bugs than tests derived from code. When tester and coder share context, they share blind spots.
<!-- Research: AgentCoder (arXiv:2312.13010) validated this architecture — multi-agent (79.9% pass@1) vs single-agent (71.3%) on HumanEval -->

## Quick Reference

| Situation | Action |
|-----------|--------|
| Starting test design for a feature | Follow the Scenario Design Workflow below |
| Deciding test scope | Use Scope Decision Table |
| Choosing which tier for a test | Use Tier Classification Table |
| Delegating test writing to sub-agents | Follow Sub-Agent Delegation Protocol |
| A test fails — need to report to coder | Follow Blind Failure Report Format |
| Coder asks for more detail on failure | Use Progressive Disclosure (one data point per round) |
| Reviewing sub-agent-generated tests | Run Verification Pipeline |
| Need sub-agent prompt templates | Read `references/prompt-templates.md` |
| Need tier-specific patterns | Read `references/tier-patterns.md` |
| Need examples of bad test delegation | Read `references/anti-patterns.md` |
| Testing robotics/CV code | Read `specializations/robotics-cv.md` |

## Scenario Design Workflow

Each step produces output the next step depends on.

### Step 1: Determine Scope

Read the plan/task assignment. Identify what scope of testing is needed.

| Scope | When | What You Test |
|-------|------|---------------|
| **Per-task** | After a coder completes a single task | Only the functions/behavior changed in that task |
| **Per-phase** | After all tasks in a phase complete | Cross-task integration within the phase |
| **Full-design** | After all phases complete | End-to-end scenarios across the entire feature |

Output: scope declaration + list of behaviors to test.

### Step 2: Load Specifications

Gather your testing inputs. You receive ONLY:
- The plan (task descriptions, requirements, acceptance criteria)
- Public API signatures and docstrings
- Schema definitions
- Design doc behavioral requirements

You do NOT receive: implementation code, coder session logs, internal function names, or coder's design decisions.
<!-- Research: AugmenTest (arXiv:2501.17461) showed spec-only oracle generation at 30% success vs 8.2% for code-based (TOGA baseline) -->

Why this matters: seeing the implementation creates confirmation bias. You unconsciously test the code paths the developer tested during development, confirming "the code does what it does" rather than "the code does what it should do."

Output: organized spec material per behavior.

### Step 3: Design Tier Allocation

For each behavior identified in Step 1, allocate tests across the 4 tiers using the Tier Classification Table. Not every behavior needs all 4 tiers.

**Tier Classification Table**

| Tier | Question It Answers | Allocate When |
|------|---------------------|---------------|
| **T1: Golden Path** | Does it work under ideal conditions? | Always — every behavior gets at least one T1 test |
| **T2: Edge Cases** | Does it handle extremes correctly? | When inputs have boundaries, collections, or optional fields |
| **T3: Adversarial** | Can hostile input break this? | Only for external-facing surfaces (APIs, user input, file parsing) |
| **T4: Property-Based** | Do universal invariants hold? | When a statable invariant exists (roundtrip, idempotency, monotonicity) |

<!-- Research: Tier research showed technique classification is orthogonal to scope pyramid. A T1 test can be unit OR scenario. Tier describes WHAT verification, not HOW MUCH system. -->

**Allocation guidance:**
- T1: 40-50% of tests per behavior
- T2: 30-40%
- T3: 5-15% (only external surfaces)
- T4: 5-15% (high leverage — each PBT replaces many manual tests)

**Scope-based tier restriction:** For per-task scope, allocate only T1 + T2. T3 and T4 add real value at per-phase and full-design scope where cross-component interaction matters. See `references/scope-patterns.md` for full guidance.

Output: per-behavior tier allocation with specific test case descriptions.

### Step 4: Delegate to Sub-Agents

For each tier's tests, delegate writing to sub-agents using the Sub-Agent Delegation Protocol below. Design the prompt, specify the tier, provide spec material from Step 2.

Output: delegated test writing tasks.

### Step 5: Verify Sub-Agent Output

Run every sub-agent-generated test through the Verification Pipeline. Reject or request revision for tests that fail quality checks.

Output: verified test suite ready for execution.

## Sub-Agent Delegation Protocol

Sub-agents write the actual test code. You (tester) design the test architecture and verify quality. Model selection for sub-agents is an orchestration concern — this skill is model-agnostic.

### What Sub-Agents Receive

| Include | Exclude |
|---------|---------|
| Function signature with types | Implementation source code |
| Docstring / spec description | Coder session logs |
| Preconditions and postconditions | Internal function names the coder chose |
| Error contract (what exceptions, when) | Existing tests written by the coder |
| Schema definitions | Developer design rationale |

Why exclude implementation: spec-only prompting is empirically superior. When sub-agents see code, they generate tests that make the CURRENT code pass rather than tests that verify the SPEC is satisfied.
<!-- Research: AugmenTest (arXiv:2501.17461) — spec-only at 30% vs code-based at 8.2% -->

### Prompt Pattern Selection

| Tier | Prompt Pattern | When |
|------|---------------|------|
| T1 Golden Path | **Spec-Only** | Default for normal-case verification |
| T2 Edge Cases | **Adversarial** | "Break this function" — finds boundary bugs |
| T3 Adversarial | **Adversarial** | Explicitly hostile/security-focused |
| T4 Property-Based | **Invariant** | States invariants as Hypothesis properties |
| Any (high-priority) | **Mutation-Guided** | When you have a specific fault class to guard against |

For detailed prompt templates per pattern, read `references/prompt-templates.md`.

### Sub-Agent Constraints (add to every prompt)

Include these constraints verbatim in every sub-agent prompt:
- "Every test MUST have at least one assert statement."
- "Do NOT assert on mock.called or mock.return_value as the primary assertion. Assert on real return values, state changes, or exceptions."
- "Label each test with a comment explaining what behavior it verifies."

Why: AI agents systematically over-mock and write assertion-free tests without explicit prohibition.
<!-- Research: arXiv:2602.00409 — 1.2M commits showed systematic over-mocking in agent-generated tests -->

## Verification Pipeline

Run EVERY sub-agent-generated test through these checks, in order. Each step is cheap enough to run routinely.

| Step | Check | Action on Failure |
|------|-------|-------------------|
| 1. AST scan | Any test with zero `assert` statements? | Reject immediately |
| 2. Empty-stub check | Does test pass against `NotImplementedError` stub? | Reject — test is tautological. Skip for functions with complex external dependencies (DB, network); use for pure functions and data transforms. |
| 3. Mutation score | Mutation tool score on the tested module | < 40%: reject. 40-60%: request revision. >= 60%: accept |
| 4. Assertion audit | Are assertions over-concentrated on mocks? | Flag if mock assertions > real assertions |

Why this pipeline exists: LLM-generated tests can achieve 100% line/branch coverage at only 4% mutation score — executing every line while missing 96% of potential bugs.
<!-- Research: MutGen (arXiv:2506.02954) confirmed on HumanEval-Java; Trail of Bits mutation testing blog; CANDOR (arXiv:2506.02943) achieved 0.98 mutation score with dual-LLM pipeline -->

**Mutation score is the correct quality metric for AI-generated tests, not coverage.**

## Blind Failure Report Format

When tests fail, report to the coder using ONLY behavioral descriptions. Never reference implementation details you shouldn't know.

### Required Fields

```
TEST_ID:         Unique identifier
SCENARIO:        Given/When/Then behavioral description
EXPECTED:        Verbatim from spec
ACTUAL:          Verbatim from test output — no interpretation
DEVIATION_TYPE:  wrong_value | property_violated | unexpected_exception | timeout
REPRODUCTION:    Minimal input that triggers failure
SPEC_REFERENCE:  Which spec clause the test derives from (if no formal spec,
                 cite the closest source: PR description, ticket acceptance
                 criteria, design doc section, or API docstring)
```

### Forbidden in Reports

| Never Include | Why |
|---------------|-----|
| Implementation references (function names, line numbers) | You are blind to implementation |
| Developer intent guessing ("they probably forgot...") | Steers coder toward assumed cause, not root cause |
| Fix suggestions | You don't know the code; may be right symptom, wrong cause |
| Filtered findings | Report ALL deviations — filtering is the coder's job |

Why this format: NASA IV&V principle — findings must reach decision-makers without being filtered by the development team. The tester describes the GAP between spec and behavior. The coder uses their implementation knowledge to locate the root cause.
<!-- Research: NASA IV&V, DO-178C, AgentCoder all converge on this separation -->

### Progressive Disclosure

If the coder cannot locate the root cause from the initial report:

1. **Round 1** (always): Scenario + Expected + Actual
2. **Round 2** (if needed): Reproduction input + Spec reference
3. **Round 3** (if still stuck): Related passing tests + boundary conditions
4. **Round 4** (rare): Additional behavioral edge cases

Never provide more implementation insight — always more behavioral evidence. The coder's clarification requests may ONLY ask for additional behavioral observations.

## Critical Rules

- **Never read implementation code.** Your tests come from specs, not code. Seeing the implementation contaminates your test design with the developer's blind spots. Why: spec-only oracle generation achieves 30% success vs 8.2% for code-based approaches (AugmenTest).
- **Mutation score over coverage.** Coverage proves execution; mutation score proves correctness verification. A test suite at 100% coverage and 4% mutation score is nearly worthless. Why: LLM-generated tests routinely achieve full coverage while missing 96% of potential bugs (MutGen, HumanEval-Java). Target >= 60%, aspire to >= 80%.
- **Every sub-agent test gets verified.** Never trust sub-agent output without running the verification pipeline. Why: AI-generated tests have systematic quality issues — tautology, over-mocking (arXiv:2602.00409), and missing assertions.
- **Reports are behavioral, never implementational.** If you find yourself naming a function or suggesting a fix, STOP — you're contaminated. Why: implementation references steer the coder toward the assumed cause, not the actual root cause.
- **Fresh context per test round when feasible.** A tester that has seen a coder's fix description is contaminated. Why: message history creates unconscious bias toward testing the fix path only. Fallback: if fresh context is too expensive, at minimum do not include the coder's fix description in the tester's prompt.
- **Mutation survivors are test gaps, not code bugs.** Frame as "this behavior is unverified" not "this code is wrong." Why: the implementation may be correct — the test suite just can't prove it. Framing as "bugs" triggers defensive coder behavior instead of collaborative gap-filling.

## References

| Topic | File |
|-------|------|
| Sub-agent prompt templates (4 patterns) | `references/prompt-templates.md` |
| Tier-specific design patterns with code examples | `references/tier-patterns.md` |
| Verification pipeline implementation details | `references/verification-details.md` |
| Scope-specific patterns (per-task, per-phase, full-design) | `references/scope-patterns.md` |
| Failure report templates (behavioral, property, mutation gap) | `references/failure-templates.md` |
| Common mistakes in scenario testing and sub-agent delegation | `references/anti-patterns.md` |
| Robotics/CV-specific scenario testing patterns | `specializations/robotics-cv.md` |
| Test design fundamentals (loaded via depends_on) | `test-design/SKILL.md` |
