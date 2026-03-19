# Factory Fixtures

For complex domain objects, use factory functions with sensible defaults.

## Basic Factory

```python
@pytest.fixture
def make_candidate():
    """Factory for creating test candidates with overridable defaults."""
    def _make(
        id: str = "test-001",
        quality: float = 0.8,
        **overrides
    ) -> Candidate:
        return Candidate(id=id, quality=quality, **overrides)
    return _make
```

## Config-Adaptive Factories

Derive test data FROM config parameters so tests never break when defaults change:

```python
def make_quality_candidates(n, cfg, *, above=True, offset=0.1):
    """Create n candidates above or below cfg.min_quality."""
    base = cfg.min_quality + offset if above else cfg.min_quality - offset
    return [make_candidate(quality=base + i * 0.01) for i in range(n)]
```

## Fixture Scoping

| Scope | Use For | Example |
|-------|---------|---------|
| `function` | Fresh state per test | Most fixtures (default) |
| `class` | Shared within test class | Database connection |
| `module` | Shared within file | Expensive computation |
| `session` | Shared across all tests | Model loading |

**Compose scopes** to share expensive resources while isolating state:

```python
@pytest.fixture(scope="session")
def model():
    """Load model ONCE for all tests."""
    return load_model("weights.pt")

@pytest.fixture
def fresh_predictor(model):
    """Per-test predictor with fresh state, shared model."""
    pred = Predictor(model)
    pred.reset()
    yield pred
```

## Precondition Assertions

When a test depends on upstream behavior, assert the precondition first with a descriptive message:

```python
def test_diversity_overrides_score(candidates, cfg):
    # Precondition: verify scoring produces expected ordering
    score_a = compute_score(candidates[0], cfg)
    score_b = compute_score(candidates[1], cfg)
    assert score_a > score_b, (
        f"Precondition failed: expected score {score_a:.3f} > {score_b:.3f}"
    )

    # Main assertion
    result = select_batch(candidates, cfg)
    assert candidates[1] in result  # Diversity overrides raw score
```

Precondition failure = upstream bug, not function-under-test bug.
