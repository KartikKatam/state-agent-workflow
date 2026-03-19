# Parametrized Edge Cases

Group related edge cases to reduce test boilerplate and make coverage visible at a glance.

## Basic Parametrization

```python
@pytest.mark.parametrize("input_val,expected", [
    ([], []),                    # Empty input
    ([1], [1]),                  # Single item
    ([1, 2, 3], [1, 2, 3]),     # Normal case
    ([1] * 1000, [...]),         # Large input
])
def test_function_various_sizes(input_val, expected):
    assert function(input_val) == expected
```

## Config-Sensitivity Parametrization

Same data, different config values:

```python
@pytest.mark.parametrize("diversity_weight,expected_winner", [
    (0.0, "high_score_candidate"),    # Pure score → highest scorer wins
    (0.5, "balanced_candidate"),       # Balanced → neither extreme wins
    (1.0, "most_diverse_candidate"),   # Pure diversity → most unique wins
])
def test_scoring_responds_to_diversity_weight(candidates, diversity_weight, expected_winner):
    cfg = make_config(diversity_weight=diversity_weight)
    result = select_batch(candidates, cfg)
    assert result[0].id == expected_winner
```

## Exception Testing

```python
def test_function_raises_on_invalid_input():
    with pytest.raises(ValueError, match="must be positive"):
        function(invalid_input=-1)
```
