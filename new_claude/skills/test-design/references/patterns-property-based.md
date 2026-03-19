# Property-Based Testing (Hypothesis)

For invariants that must hold for ALL valid inputs, not just hand-picked examples.

## Core Pattern

```python
from hypothesis import given
from hypothesis import strategies as st

@given(items=st.lists(st.integers(), min_size=1))
def test_sort_preserves_length(items):
    """Sorting never changes the number of elements."""
    assert len(sorted(items)) == len(items)

@given(items=st.lists(st.integers()))
def test_sort_idempotent(items):
    """Sorting twice gives the same result as sorting once."""
    assert sorted(items) == sorted(sorted(items))
```

## Data-Driven Strategies

```python
@given(data=st.data())
def test_filter_never_increases_count(data):
    """Filtering should never output more items than input."""
    n = data.draw(st.integers(1, 100))
    items = data.draw(st.lists(st.integers(), min_size=n, max_size=n))
    threshold = data.draw(st.integers())
    result = [x for x in items if x > threshold]
    assert len(result) <= len(items)
```

## When to Use

- Algorithmic invariants (sort, filter, transform)
- Data transformation contracts (input shape → output shape)
- Mathematical properties (commutativity, associativity, idempotency)
- Boundary exploration (Hypothesis finds edge cases you didn't think of)

## When NOT to Use

- Simple CRUD operations with known expected outputs
- Tests that need specific expected values (use golden examples instead)
- Performance-sensitive test suites (Hypothesis runs many iterations)
