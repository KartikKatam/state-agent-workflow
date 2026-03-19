# Snapshot Testing

For complex output structures where hand-writing expected values is impractical.

## File-Based (syrupy)

```python
def test_output_structure(snapshot, system, input):
    result = system.process(input)
    assert result == snapshot
```

Snapshots stored under `__snapshots__/` directory, committed to git. Update with `pytest --snapshot-update`.

## Inline (inline-snapshot)

```python
from inline_snapshot import snapshot

def test_config(default_config):
    assert default_config.to_dict() == snapshot({"batch_size": 32, "min_quality": 0.5})
```

Use `dirty_equals` within inline-snapshots for fields that vary between runs:

```python
from dirty_equals import IsPositiveInt, IsDatetime

def test_result_structure(system, test_input):
    result = system.process(test_input)
    assert result.to_dict() == snapshot({
        "id": IsPositiveInt,
        "timestamp": IsDatetime,
        "items": IsNonEmptyList,
    })
```

## When to Use

**Use snapshots for**: complex structures, serialization formats, API responses.
**Use explicit assertions for**: numerical properties, invariants, business rules.
