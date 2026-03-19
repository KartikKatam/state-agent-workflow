---
globs: ["tests/**/*.py"]
---

# Testing Rules

## Test Architecture — 4 Passes

Tests are organized into 4 passes with strict separation of concerns:

- **Pass A** (unit): Per-task, written by coder. Invariants, golden examples, negative paths, log assertions.
- **Pass B** (integration): Per-phase, written by coder. Cross-task interactions, config sensitivity, API contracts.
- **Pass C** (system): Written by coder if applicable. E2E pipeline, performance, resource validation.
- **Pass D** (scenarios): Written by tester (blind to implementation). Eval scenarios from design doc — golden path, edge cases, adversarial, property-based.

Coders write A/B/C. Testers write D. **Never cross this boundary.**

## TDD Cycle (Coder)

1. Write test FIRST — test must fail (RED)
2. Verify the test actually fails for the right reason
3. Write minimum implementation to make it pass (GREEN)
4. Never modify test assertions during IMPLEMENTATION state — this is hook-enforced

## Test Data Levels

- **L1 Golden**: Deterministic fixtures, hardcoded expected values
- **L2 Config-Adaptive**: Factory helpers parameterized from config
- **L3 Property-Based**: Hypothesis random generation for invariant testing

Use Factory Boy patterns. Use `pytest.approx` for floating-point comparisons.

## File Naming

- Unit tests: `tests/unit/test_{module}.py`
- Integration tests: `tests/integration/test_{feature}.py`
- System tests: `tests/system/test_{pipeline}.py`
- Scenario tests: `tests/scenarios/test_{scenario_name}.py`

## Rules

- Every test must have a docstring explaining WHAT it tests and WHY
- No `assert True` or `assert something` without a message — use descriptive assertions
- No test should depend on execution order — tests must be independently runnable
- No sleeping in tests — use polling with timeout or mock time
- Test files must not import from other test files — shared fixtures go in `conftest.py`
- Mark slow tests with `@pytest.mark.slow`
