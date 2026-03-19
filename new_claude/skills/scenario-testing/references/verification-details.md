# Verification Pipeline Implementation Details

Technical details for each step of the sub-agent test verification pipeline.

## Step 1: AST Scan

```python
import ast

def check_test_assertions(test_file_path: str) -> list[str]:
    """Flag tests with zero assertions."""
    tree = ast.parse(open(test_file_path).read())
    flagged = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            assertion_count = sum(
                1 for n in ast.walk(node) if isinstance(n, ast.Assert)
            )
            if assertion_count == 0:
                flagged.append(f"{node.name}: zero assertions — REJECT")
    return flagged
```

Also flag trivially-true assertions: `assert True`, `assert result is not None`
as the sole assertion. These pass against any implementation.

## Step 2: Empty-Stub Tautology Check

Replace the implementation with a stub that raises `NotImplementedError` for
every function. Run the generated tests. Any test that PASSES is tautological.

This is the TDD red-green check applied to AI-generated tests: if the test
doesn't fail when no implementation exists, it proves nothing.

**When to use vs skip:**

| Function Type | Use Stub Check? | Why |
|---------------|-----------------|-----|
| Pure functions (no side effects) | Always | Easy to stub, high value |
| Data transforms | Always | Easy to stub, catches tautological tests reliably |
| Functions with DB/network deps | Skip | Requires same mocking infrastructure the tests use |
| Functions with complex init | Consider | May need partial stub — stub the function, not the class |

## Step 3: Mutation Score Gate

**Language-specific mutation tools:**

| Language | Tool | Command |
|----------|------|---------|
| Python | mutmut | `mutmut run --paths-to-mutate src/module.py --tests-dir tests/` |
| Java/Kotlin | PiTest | `mvn pitest:mutationCoverage` |
| TypeScript/JS | Stryker | `npx stryker run` |
| Go | go-mutesting | `go-mutesting ./...` |
| Rust | cargo-mutants | `cargo mutants` |

```bash
# Python example
mutmut run --paths-to-mutate src/module.py --tests-dir tests/
mutmut results
# Score = (killed / total) * 100
```

| Score | Verdict |
|-------|---------|
| < 40% | Reject — tests are essentially tautological |
| 40-60% | Request revision — major gaps |
| 60-80% | Acceptable for most code |
| >= 80% | Production-grade (target for critical code) |

Note: MutGen (arXiv:2506.02954) achieved 89.5% mutation score with iterative
mutation feedback loops. CANDOR (arXiv:2506.02943) reached 0.98 using dual-LLM
consensus. Standard single-pass sub-agent prompts realistically land in
40-70% without mutation feedback.

## Step 4: Assertion Audit

Check the distribution of assertion types. A healthy AI-generated test suite:
- ~40% Exact assertions (assertEqual, ==)
- ~35% Property assertions (assertIn, assertTrue with conditions)
- Some Exception assertions (assertRaises, pytest.raises)
- Low Mock-only assertions

Red flags:
- Zero exact assertions: sub-agent didn't derive expected values
- Zero exception assertions: error contract ignored
- Mock assertions dominant: over-mocking (most common AI failure, confirmed
  by arXiv:2602.00409 across 1.2M commits in 2,168 TypeScript repos)
