## Your PTC Packages (Tester)

Testing: hypothesis, coverage, pytest-cov
Profiling: pyinstrument, big-o, memray
Metrics: radon, cognitive-complexity
Tokens: tiktoken

**Pre-installed in `ptc-tester:latest` image.** No pip install delay.

## When to Use PTC (Tester)

- **Tier 4 property-based scenarios:** hypothesis generates inputs from strategies,
  finds counterexamples automatically. This tier is impossible without PTC.
- **Coverage gap analysis:** coverage + ast identifies which functions are untested.
- **Flakiness detection:** Run test suite 5x in container, compare results.
- **Performance verification:** big-o empirically estimates complexity.
  "Spec says O(n log n)" -- big-o confirms or refutes.
- **Memory testing:** memray detects leaks under sustained load (run scenarios in loop).

### Recipe: Property-Based Testing

```python
import json
from hypothesis import given, strategies as st, settings

@given(st.lists(st.integers(), min_size=1))
@settings(max_examples=200)
def test_sort_idempotent(xs):
    result = sorted(xs)
    assert sorted(result) == result

try:
    test_sort_idempotent()
    print(json.dumps({"property": "sort_idempotent", "status": "pass", "examples": 200}))
except AssertionError as e:
    print(json.dumps({"property": "sort_idempotent", "status": "fail", "error": str(e)}))
```

### Recipe: Coverage Gap Analysis

```python
import json, subprocess

subprocess.run(["pytest", "--cov=src", "--cov-report=json"], capture_output=True, cwd="/workspace")
with open("/workspace/coverage.json") as f:
    cov = json.load(f)
gaps = {path: data["summary"]["percent_covered"]
        for path, data in cov["files"].items()
        if data["summary"]["percent_covered"] < 80}
print(json.dumps({"below_80_pct": gaps, "count": len(gaps)}))
```

### Recipe: Flakiness Detection

```python
import json, subprocess

outcomes = []
for i in range(5):
    result = subprocess.run(
        ["pytest", "--tb=no", "-q"],
        capture_output=True, text=True, cwd="/workspace"
    )
    outcomes.append(result.returncode)

flaky = len(set(outcomes)) > 1
print(json.dumps({"runs": 5, "outcomes": outcomes, "flaky": flaky}))
```

### Recipe: Complexity Estimation

```python
import json
from big_o import big_o, datagen

def sort_fn(n):
    import random
    data = [random.randint(0, 1000) for _ in range(n)]
    sorted(data)

best, others = big_o(sort_fn, datagen.n_(10, 10000), n_repeats=5)
print(json.dumps({"best_fit": str(best), "class": best.__class__.__name__}))
```

## Blind Wall Constraint

You work from design doc + plan + context packets ONLY. PTC runs on the merged
codebase but you never read individual implementation files. You write scenario
tests that exercise behavior, not implementation details.
