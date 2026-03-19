---
name: test-design
version: 1-0-0
triggers:
  - agent_role: strategist
    conditions: when designing test specifications or test architecture during planning
  - agent_role: coder
    conditions: when writing tests during TDD red phase or evaluating test quality
  - agent_role: tester
    conditions: when designing scenario tests, evaluating test coverage, or reviewing test quality
description: >
  Use when designing tests, writing test specifications, choosing assertion
  strategies, or evaluating whether tests are meaningful. Activates for: test
  writing during TDD, test architecture planning, test quality review, edge
  case identification, fixture design, assertion strength decisions. Also use
  when a test passes immediately on first run, when uncertain whether a test
  actually proves correctness, when reviewing tests written by another agent,
  or when an agent considers tests "complete" but has not verified them against
  fraudulent test patterns. If tests exist but you have not checked for mock
  echo, tautology, or happy-path-only coverage, activate this skill.
  Do NOT use for: running tests, debugging test failures (use debugging skill),
  TDD sequence enforcement (infrastructure handles that), quality gate execution.
---

# Test Design

## Core Principle

A test proves correctness only when it could have caught the bug. Tests that pass by construction, assert on mocks instead of behavior, or skip edge cases are **fraudulent** — they provide false confidence while proving nothing.

## Quick Reference

| Situation | Action |
|-----------|--------|
| Designing tests for a new function | Follow the Test Design Workflow below |
| Test passes on first run | Suspect fraudulent — run the Fraudulent Test Detector |
| Choosing assertion type for a scoring/ranking test | Use Assertion Strength Selection — never ordering alone |
| Unsure what edge cases to cover | Use Edge Case Reasoning — 5 systematic categories |
| Designing fixtures for complex objects | Read `references/patterns-factories.md` |
| Grouping edge cases with parametrize | Read `references/patterns-parametrize.md` |
| Writing tests for probabilistic/CV code | Read `specializations/robotics-cv.md` |
| Reviewing someone else's tests | Run each test through the Fraudulent Test Detector |
| Tests seem "done" but not fraud-checked | Run every test through the Fraudulent Test Detector |
| Choosing mock strategy | Mock at lowest level necessary — see Critical Rules |
| Planning test architecture (Pass A/B/C) | Read `references/patterns-test-tiers.md` |
| Asserting on logs, traces, decisions | Read `references/patterns-observability.md` |
| Testing complex output structures | Read `references/patterns-snapshots.md` |
| Testing algorithmic invariants | Read `references/patterns-property-based.md` |
| Performance benchmarks / leak detection | Read `references/patterns-performance.md` |
| Test naming, directory layout, markers | Read `references/patterns-organization.md` |

## Test Design Workflow

Follow these steps when designing any test. Each step produces output the next step depends on.

### Step 1: Identify the Behavior Under Test

State in one sentence what the function/component SHOULD DO — its contract with callers. Not what the code does internally, but what observable behavior the caller depends on.

Output: A behavior statement. Example: "select_batch returns the top N candidates above quality threshold, ordered by score."

### Step 2: Derive Test Cases from the Contract

From the behavior statement, systematically derive cases using Edge Case Reasoning (below). Each case tests one aspect of the contract.

Output: A list of test cases with names following `test_{function}_{scenario}_{expected}` convention.

### Step 3: Choose Assertion Strategy Per Case

For each test case, select the appropriate assertion strength (see Assertion Strength Selection below). The assertion must verify the contract, not the implementation.

Output: Each test case annotated with assertion type and expected values.

### Step 4: Design Test Data

For each case, determine the minimal input that exercises the scenario. Three strategies:

| Strategy | When | Example |
|----------|------|---------|
| **L1 Golden** | Deterministic, hand-crafted values | `input=[0.9, 0.3, 0.8], threshold=0.5, expected=[0.9, 0.8]` |
| **L2 Config-Adaptive** | Values derived from config | `make_candidates(n=5, quality=cfg.min_quality + 0.1)` |
| **L3 Property-Based** | Algorithmic invariants | `@given(items=st.lists(st.floats(0,1)))` → `len(output) <= len(input)` |

**L1 rule**: Never rely on config defaults. If a test needs `min_quality=0.5`, construct a config with that value explicitly. Tests break silently when defaults change.

Output: Test data approach per case, with specific values for L1 cases.

### Step 5: Validate Test Meaningfulness

Before finalizing, run each test through the Fraudulent Test Detector. If any test fails the detector, redesign it.

Output: Final test design with all cases validated as meaningful.

## The Fraudulent Test Detector

A test is fraudulent if any of these are true. Check EVERY test against this list.

| Check | Fraudulent Signal | What To Do Instead |
|-------|-------------------|-------------------|
| **Tautology** | Test asserts what the code trivially guarantees (e.g., `assert isinstance(result, list)` when return type is `list`) | Assert on content, length, ordering, or values — properties that could actually be wrong |
| **Mock echo** | Test asserts on mock return values it configured itself | Assert on how the system USES the mock's output, not the output itself |
| **Implementation mirror** | Test reimplements the production logic and compares | Test against independently derived expected values, not a copy of the algorithm |
| **No-fail design** | Test literally cannot fail (e.g., `assert result is not None` for a function that never returns None) | Identify a scenario where the function COULD produce wrong output and test that |
| **Assertion-free** | Test runs code but never asserts anything meaningful | Add assertions on return value, side effects, or logged output |
| **Happy-path-only** | Test only covers the normal case, skips all edges | Add edge cases from Edge Case Reasoning |
| **Over-mocked** | So much is mocked that the test exercises zero real logic | Mock at the lowest level necessary — test real code paths |

**The first-run check**: If a test passes on its very first run during TDD, it is suspect. A well-designed test SHOULD fail before the implementation exists. If it passes immediately, either:
- The implementation was written first (TDD violation — infrastructure catches this)
- The test is fraudulent (this skill catches this)
- The test covers already-implemented behavior (acceptable only for regression tests)

## Edge Case Reasoning

Systematically generate edge cases from five categories. For each function, ask these questions:

### Category 1: Boundary Values
- What happens at the minimum valid input? (0, empty list, empty string)
- What happens at the maximum? (MAX_INT, huge list, very long string)
- What happens at exactly the threshold? (e.g., `quality == min_quality`)
- What about just above and just below the threshold?

### Category 2: Degenerate Inputs
- Empty collections: `[]`, `{}`, `""`
- Single-element collections: `[x]`
- All-same elements: `[x, x, x]`
- None/null where valid input expected
- Zero where positive expected

### Category 3: Defensive Paths
- What does the code silently skip? (e.g., `if not valid: continue`) Test: verify item absent AND reason logged.
- What exceptions does the code catch? Test: trigger the exception, verify fallback behavior.
- What guard clauses return early? Test: verify exact return value and type, not just "doesn't crash."
- What fallback paths exist? Test: trigger the fallback, verify correct output AND logged indication.

### Category 4: Ordering and Combination
- Does ordering of inputs matter? Test both orders.
- Do duplicate inputs cause issues? Test with duplicates.
- Do inputs interact? (e.g., two config params that affect each other)

### Category 5: State and Timing
- Does calling the function twice produce the same result? (Idempotency)
- Does calling with side effects leave the system in expected state?
- For async: does concurrent access cause issues?

Not all categories apply to every function. Use the ones relevant to the contract identified in Step 1.

## Assertion Strength Selection

Choose the weakest assertion that proves the contract. Stronger than necessary = brittle. Weaker than necessary = meaningless.

| Type | Syntax Example | Use When |
|------|---------------|----------|
| **Exact** | `== 0.5` | Deterministic computation, golden examples, guard clause returns |
| **Magnitude** | `>= 0.3` | Scores, weights — prevents collapse to zero |
| **Ordering** | `score_a > score_b` | Rankings — but NEVER alone for scoring tests |
| **Range** | `0.2 <= x <= 0.8` | Bounded outputs, normalized values |
| **Property** | `len(out) <= len(inp)` | Invariants over all valid inputs |
| **Spread** | `a - b >= 0.05` | Discrimination — prevents degenerate ties |
| **Structural** | `isinstance(r, Result) and r.items` | Output shape verification |

**Mandatory rule for scoring/ranking tests**: Never use ordering alone (`a > b`). Always pair with at least one of:
- Magnitude floor (`a >= 0.3 AND a > b`) — prevents both collapsing to zero
- Spread minimum (`a - b >= 0.05`) — prevents degenerate ties where `a = 0.001 > b = 0.0`

Why: Ordering-only assertions pass even when both values are meaninglessly small or identical up to floating-point noise.

## Critical Rules

- **Test behavior, not implementation.** Assert on what the function returns or logs, not how it computes internally. Implementation changes shouldn't break tests unless behavior changes. Why: implementation-coupled tests create false failures on valid refactors.
- **Mock at the lowest level necessary.** Mock external services, not internal functions. If you mock an internal function, you're testing that the code calls internal functions — not that it produces correct results. Why: over-mocking makes tests pass even when production code is broken.
- **Never add test-only methods to production code.** No `reset()`, `destroy()`, or `_get_internal_state()` in production classes for test access. Move test helpers to test utilities. Why: test-specific API surface couples production design to test needs.
- **Every negative path gets a log assertion.** When code silently skips, catches, or falls back, the test must assert the reason was logged. `assert "skipping" in caplog.text`. Why: silent failures are invisible without log verification — the test proves the code handled the case, not just survived it.
- **Config-independent test data.** Never hand-tune test values to match config defaults. Either construct config explicitly in the test, or use config-adaptive factory helpers. Why: tests break silently when defaults change, and the failure looks like a logic bug rather than a stale test.
- **One assertion concept per test.** A test can have multiple `assert` statements, but they should verify one logical concept. Don't combine "returns correct value" with "logs correctly" in one test. Why: when a multi-concept test fails, you can't tell which concept broke without reading the assertion.
- **Precondition assertions for upstream dependencies.** When a test depends on another function's output, assert the precondition with a descriptive failure message before the main assertion. Why: precondition failure = upstream bug, not function-under-test bug — the message tells you where to look.

## Domain Specializations

If your project's domain is declared in CLAUDE.md, load the matching specialization for domain-specific test patterns:

```
specializations/{domain}.md
```

**robotics-cv**: Probabilistic algorithm testing, IoU/AP metrics, tracking evaluation, sensor simulation, hardware mocking, timing constraints.

## References

Each reference file covers one topic — load only what you need:

| Topic | File |
|-------|------|
| Test tiers (Pass A/B/C) | `references/patterns-test-tiers.md` |
| Factory fixtures, scoping, preconditions | `references/patterns-factories.md` |
| Parametrized edge cases, config sensitivity | `references/patterns-parametrize.md` |
| Log assertions, structlog, caplog | `references/patterns-observability.md` |
| Snapshot testing (syrupy, inline-snapshot) | `references/patterns-snapshots.md` |
| Property-based testing (Hypothesis) | `references/patterns-property-based.md` |
| Benchmarks, memory/file-handle leaks | `references/patterns-performance.md` |
| Naming, directory structure, markers, CI | `references/patterns-organization.md` |
| Anti-patterns with WRONG/RIGHT examples | `references/anti-patterns.md` |
