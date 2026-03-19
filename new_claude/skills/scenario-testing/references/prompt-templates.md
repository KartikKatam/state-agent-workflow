# Sub-Agent Prompt Templates

Four prompt patterns for delegating test writing to sub-agents.

## Spec-Only Pattern (Tier 1: Golden Path)

```
You are a test engineer. Write pytest tests for the following function.

IMPORTANT: Do NOT look at any implementation. Write tests based ONLY on
the specification below.

Function signature: {signature}
Specification: {docstring_or_spec}
Preconditions: {preconditions}
Postconditions: {postconditions}
Error contract: {exceptions_raised}

Your task:
1. Write tests for the NORMAL case (valid inputs, expected outputs)
2. Write tests for BOUNDARY cases (minimum, maximum, empty, single-element)
3. Write tests for ERROR cases (invalid inputs that should raise exceptions)
4. Write at least one PROPERTY test using @given from hypothesis

Each test must have an assert statement. Tests that only call the function
without asserting anything are not acceptable.

Do NOT assert on mock.called or mock.return_value as the primary assertion.
Assert on real return values, state changes, or exceptions.
Label each test with a comment explaining what behavior it verifies.
```

## Adversarial Pattern (Tier 2: Edge Cases / Tier 3: Adversarial)

```
You are an adversarial tester. Your goal is to BREAK the following function.

Function: {signature}
Spec: {docstring}

Think like a malicious or careless caller. What inputs could this function
mishandle? Consider:
- Off-by-one errors (n-1, n+1 at boundaries)
- Empty collections vs None
- Integer overflow or underflow
- Floating point edge cases (NaN, Inf, -0.0)
- Unicode vs ASCII edge cases
- Invariant violations after the function returns

Write 3-5 tests, each targeting a SPECIFIC way the function could fail.
Each test should CLEARLY FAIL if the implementation has the bug you're
targeting. Label each test with a comment explaining what bug it would catch.

Each test must have an assert statement.
Do NOT assert on mock.called or mock.return_value as the primary assertion.
```

## Invariant Pattern (Tier 4: Property-Based)

```
You are a formal methods engineer analyzing a function.

Function: {signature}
Spec: {docstring}

List ALL invariants that must hold for this function. For each invariant:
1. State it in English: "For any input X, after calling f(X)..."
2. Write it as a @given Hypothesis property test

Common invariant patterns to check:
- Roundtrip: encode then decode returns original
- Idempotency: f(f(x)) == f(x)
- Commutativity: f(a,b) == f(b,a)
- Monotonicity: if a <= b then f(a) <= f(b)
- Size preservation: len(f(x)) == len(x)
- No-crash: any valid input should not raise unexpected exceptions

Each property test must use @given with appropriate strategies.
```

## Mutation-Guided Pattern (High-Priority)

Use when you have a specific fault class to guard against. Based on Meta's ACH
architecture (73% engineer acceptance, deployed at Facebook/Instagram/WhatsApp).
Also validated by MutGen (arXiv:2506.02954) achieving 89.5% mutation score on
HumanEval-Java, and CANDOR (arXiv:2506.02943) reaching 0.98 mutation score.

```
You are writing a test that MUST FAIL when a specific bug is present.

The bug we are guarding against:
{plain_english_bug_description}

Here is a mutated (BUGGY) version of the function:
{mutant_code}

Here is the CORRECT specification:
{specification}

Write a test that:
1. FAILS when run against the mutant above
2. PASSES when run against a correct implementation
3. Tests the specific behavior described in the bug

Prove your test kills this mutant by explaining what assertion fails and why.
```

The mutant guarantees non-tautology by construction — the test must distinguish
correct from incorrect behavior to pass review.
