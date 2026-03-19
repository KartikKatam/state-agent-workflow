# Tier-Specific Design Patterns

Code examples and design guidance for each of the 4 test tiers.

## Tier 1: Golden Path

**What belongs:** The primary use cases — the 80-90% of production traffic.

Design pattern:
1. Identify the function's primary contract (from docstring/spec)
2. Write one test per documented use case
3. Use explicit, readable fixtures with hand-crafted values
4. Assert on return value, not implementation internals

```python
# GOOD — tests the contract
def test_select_batch_returns_top_candidates():
    """Verifies: select_batch returns candidates above threshold, ordered by score."""
    candidates = [
        Candidate(id="a", score=0.9),
        Candidate(id="b", score=0.3),
        Candidate(id="c", score=0.8),
    ]
    result = select_batch(candidates, threshold=0.5, n=2)
    assert len(result) == 2
    assert result[0].id == "a"  # Highest score first
    assert result[1].id == "c"
```

## Tier 2: Edge Cases

**What belongs:** Boundary values, degenerate inputs, off-by-one conditions.

Use parametrize for systematic boundary coverage:

```python
@pytest.mark.parametrize("input_list,threshold,expected_count", [
    ([], 0.5, 0),              # Empty input
    ([Candidate(score=0.5)], 0.5, 1),  # Exactly at threshold
    ([Candidate(score=0.49)], 0.5, 0), # Just below threshold
    ([Candidate(score=0.51)], 0.5, 1), # Just above threshold
])
def test_select_batch_boundaries(input_list, threshold, expected_count):
    result = select_batch(input_list, threshold=threshold, n=10)
    assert len(result) == expected_count
```

## Tier 3: Adversarial

**What belongs:** Hostile inputs crafted to exploit. Only for external surfaces.

```python
def test_rejects_oversized_input():
    """Guards against memory exhaustion from crafted large input."""
    huge_input = [Candidate(score=0.9)] * 1_000_000
    with pytest.raises(ValueError, match="exceeds maximum"):
        select_batch(huge_input, threshold=0.5, n=10)

def test_handles_nan_scores():
    """Guards against NaN poisoning in score comparison."""
    candidates = [Candidate(score=float('nan')), Candidate(score=0.8)]
    result = select_batch(candidates, threshold=0.5, n=10)
    # NaN candidates must be excluded — NaN > threshold is always False
    assert len(result) == 1
    assert result[0].score == 0.8
```

## Tier 4: Property-Based

**What belongs:** Universal invariants that hold for ALL valid inputs.

Each property-based test finds approximately 50x as many mutations as the
average unit test (OOPSLA 2025 peer-reviewed study).

```python
from hypothesis import given, strategies as st

@given(scores=st.lists(st.floats(min_value=0, max_value=1), min_size=0, max_size=100))
def test_output_never_exceeds_input_size(scores):
    """Invariant: output length <= input length for any valid input."""
    candidates = [Candidate(score=s) for s in scores if not math.isnan(s)]
    result = select_batch(candidates, threshold=0.5, n=len(candidates))
    assert len(result) <= len(candidates)

@given(scores=st.lists(st.floats(min_value=0, max_value=1, allow_nan=False), min_size=2))
def test_output_is_sorted_descending(scores):
    """Invariant: output is always sorted by score descending."""
    candidates = [Candidate(score=s) for s in scores]
    result = select_batch(candidates, threshold=0.0, n=len(candidates))
    for i in range(len(result) - 1):
        assert result[i].score >= result[i + 1].score
```

Also consider AdverTest (arXiv:2602.08146) — an adversarial multi-agent
framework where a test agent and mutant agent compete, directly relevant
to combining T3 adversarial and T4 property-based approaches.
