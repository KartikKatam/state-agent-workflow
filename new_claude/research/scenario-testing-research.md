# Scenario Testing Research Synthesis

**Date:** 2026-02-28
**Research rounds:** 2 (7 sub-agents total)
**Overall confidence:** High (0.84)

---

## 1. Executive Summary

Seven parallel research sub-agents investigated blind/independent testing across AI labs, industry, aerospace (NASA IV&V, DO-178C), and academic papers. The research validates a tester-implementer separation architecture where the tester designs and writes tests from specifications only, never seeing implementation code. Key finding: this separation improves implementation success by 66% (Cognition SWE-bench TDD data) while producing tests that catch 50x more mutations than standard unit tests (OOPSLA 2025 PBT study).

**Top 5 findings that directly shaped skill design:**

1. **100% coverage at 4% mutation score** — LLM-generated tests achieved full line/branch coverage while missing 96% of potential bugs. Coverage is not a valid quality metric for AI-generated tests. Mutation score is the correct metric.

2. **Specification-only prompting produces better tests** — AugmenTest (arXiv:2501.17461) showed oracle precision DROPS when LLMs see implementation code. Spec-only prompting is empirically superior, not just theoretically cleaner.

3. **AgentCoder validates tester-coder separation** — Published architecture (arXiv:2312.13010): programmer agent + separate test designer agent (no code access) + test executor agent. "Reduces confirmation bias and catches subtle errors missed in tightly coupled designs."

4. **Meta ACH: mutation as test specification** — 73% engineer acceptance rate. The mutant IS the test contract — guarantees non-tautology before review. Deployed at Facebook/Instagram/WhatsApp scale.

5. **4-tier is technique classification, not scope** — Tiers (golden path, edge cases, adversarial, property-based) are orthogonal to the test pyramid (unit, integration, E2E). A golden-path test can be unit OR scenario level.

---

## 2. Blind Testing Methodologies

### What Works

| Method | Blindness Mechanism | Proven Effectiveness |
|--------|---------------------|---------------------|
| NASA IV&V | Org separation (tech, managerial, financial) | DO-178C Level A: 16/31 objectives require independence |
| BDD/Gherkin | Scenarios in business language, separate from code | Martin Fowler canonical pattern; SAFe adopted |
| Property-based testing | Properties state what SHOULD hold; Hypothesis generates inputs | 50x mutation kills per test vs unit tests (OOPSLA 2025) |
| Mutation testing | Engine mutates code silently; tests don't know what changed | LLM tests: 100% coverage at 4% mutation score — exposes test fraud |
| Contract testing (Pact) | Consumer defines needs; provider verifies independently | Neither team sees the other's code |
| Generator-evaluator separation | Different model grades output, no shared context | Vercel v0: cleanest commercial implementation |

### What Doesn't Work

- **Tester seeing implementation** — creates confirmation bias; tests mirror code instead of verifying spec
- **Coverage as quality metric** — 100% coverage proves execution, not correctness
- **Self-testing** — agents writing tests for their OWN code during coding is mostly wasteful (arXiv:2602.07900: 49% less tokens, only 1.8-2.6% decrease in success). But a SEPARATE tester writing for a separate implementer improves success 66% (Cognition)
- **Over-mocking** — coding agents systematically over-mock (arXiv:2602.00409: 1.2M commits, 2168 TypeScript repos). Explicit prohibition required.

---

## 3. Test Tier Architecture

The 4-tier system describes WHAT KIND of verification, not how much system is exercised.

| Tier | Question Answered | Allocation | Execution |
|------|-------------------|------------|-----------|
| **1: Golden Path** | Does it work under ideal conditions? | 40-50% | Every commit |
| **2: Edge Cases** | Does it handle extremes correctly? | 30-40% | Every commit |
| **3: Adversarial** | Can a motivated attacker break this? | 5-15% | Nightly/pre-release |
| **4: Property-Based** | Are universal invariants satisfied? | 5-15% | Fast: every commit; full: nightly |

**Key distinctions:**
- Tier 2 vs 3: Edge cases are ACCIDENTAL extremes; adversarial inputs are CRAFTED attacks
- Tier 3 vs 4: Adversarial generates inputs OUTSIDE valid space; PBT generates inputs WITHIN valid space
- Tier 1+2: Together cover entire documented input domain
- Tier 2+4: PBT finds edge cases manual boundary analysis misses

**Tier completion criteria:**
- Tier 1: Every documented use case has a test
- Tier 2: BVA completed for every input with defined range
- Tier 3: OWASP Top 10 for external surfaces; auth paths adversarially tested
- Tier 4: Every statable invariant tested; mutation score >= 80%

---

## 4. Failure Reporting Patterns

### Include (derivable from spec + test output only)

| Field | Description |
|-------|-------------|
| `test_id` | Unique identifier |
| `scenario` | Given/When/Then behavioral description |
| `expected` | Verbatim from spec |
| `actual` | Verbatim from test output — no interpretation |
| `deviation_type` | Classification (wrong_value, property_violated, unexpected_exception, timeout) |
| `reproduction` | Minimal input that triggers failure |
| `spec_reference` | Which spec clause the test derives from |

### Exclude (would contaminate tester's blindness)

- Implementation details, function/method names, line numbers
- Developer intent guessing ("they probably meant to...")
- Fix suggestions
- Filtered findings (report ALL deviations)
- Internal stack traces

### Feedback Loop

```
Tester → Behavioral report → Coder (uses own implementation knowledge to locate root cause)
Coder → fix → re-trigger test run → Tester → pass/fail report
```

Tester NEVER suggests fixes. Coder NEVER shares implementation details back. Progressive disclosure if coder can't find issue: additional behavioral data points, not code insight.

---

## 5. AI-Specific Approaches

### Eval Frameworks

| Framework | Lab | Blind Mechanism |
|-----------|-----|-----------------|
| Bloom | Anthropic | Scenarios generated fresh per run; 100 rollouts x 3 |
| SWE-bench Verified | OpenAI | Private test split; model never sees tests |
| LiveCodeBench | ICLR 2025 | Rolling post-cutoff problems; detects contamination |
| CyberSecEval 2 | Meta | Real CVEs; deterministic static analysis grading |
| Terminal-Bench 2.0 | Cross-lab | Containerized; reproduced by multiple labs |

### AI-Native Company Patterns

| Company | Key Pattern |
|---------|-------------|
| Cognition (Devin) | Private golden benchmark; parallel isolated environments; TDD mode +66% |
| Vercel (v0) | Generator-evaluator separation; eval-driven CI/CD; 100% safety gate |
| SWE-bench ecosystem | Real GitHub issues; private test splits; 3-annotator validation |

---

## 6. Sub-Agent Test Implementation

### Prompt Patterns (4 types)

1. **Spec-Only** (primary): Give signature + docstring + error contract. No implementation code. Sub-agent writes normal, boundary, error, and property tests.

2. **Adversarial**: "Break this function." Sub-agent thinks like a malicious caller. Labels each test with the bug it would catch.

3. **Invariant**: "State ALL invariants." Roundtrip, idempotency, commutativity, monotonicity, size preservation. Each becomes a Hypothesis property test.

4. **Mutation-Guided** (Meta ACH): Give the mutant code + spec. Sub-agent writes tests that KILL the mutant. Guarantees non-tautology by construction.

### Verification Pipeline (5 steps, ordered by cost)

| Step | Method | Cost | Catches |
|------|--------|------|---------|
| 1 | AST analysis | Negligible | Zero-assertion tests, trivially-true assertions |
| 2 | Empty-stub tautology check | One pytest run | Tests that pass against NotImplementedError |
| 3 | Mutation score gate | 1-5 min | Tests that don't detect behavioral changes |
| 4 | Assertion category audit | No additional | Over-mocked, unbalanced assertion types |
| 5 | Spec cross-reference (Opus) | Optional | Tests verifying implementation details, not spec |

### Quality Gate Thresholds

| Mutation Score | Action |
|----------------|--------|
| < 40% | Reject — tests are essentially tautological |
| 40-60% | Request revision from sub-agent |
| 60-80% | Acceptable |
| >= 80% | Production-grade (target) |

---

## 7. Sources

### AI Lab Eval Frameworks
- Bloom: alignment.anthropic.com/2025/bloom-auto-evals/
- Anthropic Agent Evals: anthropic.com/engineering/demystifying-evals-for-ai-agents
- OpenAI Evals: github.com/openai/evals
- SWE-bench: swebench.com
- LiveCodeBench: ICLR 2025

### Blind Testing Methodologies
- NASA IV&V: nasa.gov/ivv-overview/
- DO-178C: Vector whitepaper
- OOPSLA 2025 PBT effectiveness study
- Trail of Bits: mutation testing (2025)
- Meta Engineering: ACH tool (FSE 2025, arXiv:2501.12862)

### AI-Native Testing
- Cognition: cognition.ai/blog/evaluating-coding-agents
- Vercel: vercel.com/blog/eval-driven-development
- AgentCoder: arXiv:2312.13010
- AugmenTest: arXiv:2501.17461
- Over-mocked tests: arXiv:2602.00409
- Agent self-tests: arXiv:2602.07900

### Test Architecture
- Google SRE Book: sre.google/sre-book/testing-reliability/
- Martin Fowler test shapes: martinfowler.com/articles/2021-test-shapes.html
- BDD: cucumber.io/docs/bdd/
- Pact: docs.pact.io/
- Hypothesis: hypothesis.readthedocs.io/
