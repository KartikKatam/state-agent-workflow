# Test Patterns Reference

> **Purpose**: State-of-the-art test design patterns — foundation patterns, observability, snapshot testing, performance, and resource management. Domain-specific patterns (CV/ML, robotics, etc.) are in `specializations/` subdirectories.
> **Loaded by**: plan-architect (on demand), chunk-coder (when plan references specific patterns)

---

## Foundation Patterns

### Pattern 1: Arrange-Act-Assert (AAA)

Every test follows AAA structure:

```python
def test_function_behavior(self, fixtures):
    # Arrange - Set up test data and preconditions
    input_data = make_test_data(...)
    expected = ExpectedResult(...)

    # Act - Execute the function under test
    result = function_under_test(input_data)

    # Assert - Verify the outcome
    assert result == expected
```

### Pattern 2: Factory Fixtures

For complex objects, use factory fixtures with sensible defaults:

```python
@pytest.fixture
def make_candidate():
    """Factory for creating test candidates with defaults."""
    def _make(
        id: str = "test-001",
        quality: float = 0.8,
        timestamp: datetime | None = None,
        **overrides
    ) -> Candidate:
        return Candidate(
            id=id,
            quality=quality,
            timestamp=timestamp or datetime.now(),
            **overrides
        )
    return _make
```

**For complex domain objects, consider Factory Boy** for richer factories with random but realistic data. See domain specializations for examples. For config-dependent test data, see Pattern 2b: Config-Adaptive Factory Helpers below.

### Pattern 2b: Config-Adaptive Factory Helpers

Factories that derive data FROM config parameters — tests never break when defaults change:

```python
def make_spread_candidates(n, cfg, *, margin=0.005):
    """Create n candidates spread >= cfg.min_distance + margin apart."""
    step = cfg.min_distance + margin
    return [make_candidate(position=i * step, quality=0.5 + i * 0.05) for i in range(n)]


def make_quality_candidates(n, cfg, *, above=True, offset=0.1):
    """Create n candidates above or below cfg.min_quality."""
    base = cfg.min_quality + offset if above else cfg.min_quality - offset
    return [make_candidate(quality=base + i * 0.01) for i in range(n)]
```

### Pattern 3: Parametrized Edge Cases

Group related edge cases with parametrize:

```python
@pytest.mark.parametrize("input_val,expected", [
    ([], []),                    # Empty input
    ([1], [1]),                  # Single item
    ([1, 2, 3], [1, 2, 3]),     # Normal case
    ([1] * 1000, [...]),        # Large input
])
def test_function_handles_various_sizes(self, input_val, expected):
    assert function(input_val) == expected
```

### Pattern 4: Exception Testing

```python
def test_function_raises_on_invalid_input(self):
    with pytest.raises(ValueError, match="must be positive"):
        function(invalid_input=-1)
```

### Pattern 5: Fixture Scopes and Composition

| Scope | Use For | Example |
|-------|---------|---------|
| `function` | Fresh state per test | Most fixtures |
| `class` | Shared within test class | Database setup |
| `module` | Shared within file | Expensive computation |
| `session` | Shared across all tests | Model loading, connection pools |

**Fixture composition for expensive resources** — separate expensive loading from per-test isolation:

```python
@pytest.fixture(scope="session")
def expensive_resource():
    """Session-scoped: load/connect ONCE, reuse across all tests."""
    resource = create_expensive_resource()  # DB connection, model load, etc.
    yield resource
    resource.cleanup()

@pytest.fixture
def fresh_resource(expensive_resource):
    """Function-scoped: fresh state per test, shared underlying resource."""
    instance = expensive_resource.create_instance()
    instance.reset_state()
    yield instance
    instance.cleanup()
```

### Pattern 6: Precondition Assertions

Assert upstream assumptions before the main assertion. Precondition failure → upstream bug, not function-under-test bug.

```python
def test_diversity_overrides_score_ordering(candidates, cfg):
    # Precondition: verify scoring produces expected ordering
    score_copy = compute_score(candidates[0], cfg)
    score_diverse = compute_score(candidates[1], cfg)
    assert score_copy > score_diverse, (
        f"Precondition failed: expected copy score {score_copy:.3f} > diverse score {score_diverse:.3f}"
    )

    # Main assertion: diversity overrides raw score ordering
    result = select_batch(candidates, cfg)
    assert candidates[1] in result
```

---

## Snapshot Testing

### Syrupy (File-Based Snapshots)

For complex outputs (JSON responses, pipeline state), use snapshot testing to catch unexpected changes:

```python
def test_pipeline_output_snapshot(snapshot, system, standard_input):
    """System output structure should match snapshot."""
    result = system.process(standard_input)
    assert result == snapshot
```

Snapshots stored under `__snapshots__/` directory, committed to git. Update with `pytest --snapshot-update`.

### Inline Snapshots

For smaller expected values, inline-snapshot keeps expected values in the test file itself:

```python
from inline_snapshot import snapshot

def test_config_serialization(default_config):
    assert default_config.to_dict() == snapshot({
        "batch_size": 32,
        "min_quality": 0.5,
        "diversity_weight": 0.3,
    })
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

**When to use snapshots vs explicit assertions:**
- Snapshot: complex output structures, serialization formats, API responses
- Explicit: numerical properties, invariants, business rules

---

## OpenTelemetry Trace-Based Testing

For instrumented systems, assert on spans and trace structure:

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory import InMemorySpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

@pytest.fixture
def span_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    exporter.clear()

def test_system_creates_expected_spans(span_exporter, system, test_input):
    system.process(test_input)
    spans = span_exporter.get_finished_spans()
    span_names = [s.name for s in spans]

    assert "validate" in span_names
    assert "process" in span_names
    assert "persist" in span_names

    process_span = next(s for s in spans if s.name == "process")
    assert process_span.attributes["component.name"] is not None
    duration_ms = (process_span.end_time - process_span.start_time) / 1e6
    assert duration_ms < 200
```

**When to use trace assertions vs log assertions:**
- Traces: timing, call hierarchy, cross-service communication, span attributes
- Logs: decision points, filtering reasons, configuration values, error details

---

## Performance Regression Testing

Use pytest-benchmark with CI regression detection:

```python
@pytest.mark.benchmark
def test_critical_path_latency(benchmark, system_under_test, standard_input):
    """Critical path latency must not regress."""
    result = benchmark(system_under_test.process, standard_input)
    assert result is not None

@pytest.mark.benchmark
def test_batch_throughput(benchmark, processor, batch_input):
    """Batch processing throughput benchmark."""
    result = benchmark(processor.batch_process, batch_input)
    assert len(result) == len(batch_input)
```

```bash
# Save baseline in CI
pytest tests/benchmarks/ --benchmark-save=baseline

# Compare in CI (fail if mean is 20% slower)
pytest tests/benchmarks/ --benchmark-compare=0001_baseline --benchmark-compare-fail=mean:20%
```

> **Domain specializations** add domain-specific benchmark examples (e.g., inference latency, preprocessing throughput).

---

## Property-Based Testing (Hypothesis)

For algorithmic invariants that must hold for ALL valid inputs:

```python
from hypothesis import given, settings
from hypothesis import strategies as st

@given(items=st.lists(st.integers(), min_size=1))
def test_sort_preserves_length(items):
    """Sorting never changes the number of elements."""
    assert len(sorted(items)) == len(items)

@given(items=st.lists(st.integers()))
def test_sort_is_idempotent(items):
    """Sorting twice gives the same result as sorting once."""
    assert sorted(items) == sorted(sorted(items))

@given(data=st.data())
def test_filter_never_increases_count(data):
    """Filtering should never output more items than input."""
    n = data.draw(st.integers(1, 100))
    items = data.draw(st.lists(st.integers(), min_size=n, max_size=n))
    threshold = data.draw(st.integers())
    result = [x for x in items if x > threshold]
    assert len(result) <= len(items)
```

> **Domain specializations** add domain-specific strategies (e.g., bounding box generators, frame arrays for CV/ML).

---

## Config-Sensitivity Parametrized Tests

### Function-Level: Different Config → Different Winner

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

### Pipeline-Level: Different Config → Different Output Count

```python
@pytest.mark.parametrize("min_quality,expected_count", [
    (0.1, 10),   # Permissive → most candidates pass
    (0.5, 6),    # Moderate → some filtered
    (0.9, 2),    # Strict → few pass
    (1.0, 0),    # Breaking → all rejected
])
def test_output_count_responds_to_min_quality(candidates, min_quality, expected_count):
    cfg = make_config(min_quality=min_quality)
    result = select_batch(candidates, cfg)
    assert len(result) == expected_count
```

---

## Negative/Defensive Path Testing

### Silent-Skip with Log Assertion

```python
def test_none_keypoints_skipped_with_log(caplog, make_candidate, cfg):
    candidates = [make_candidate(keypoints=None), make_candidate(keypoints=valid_kps)]
    with caplog.at_level(logging.DEBUG):
        result = select_batch(candidates, cfg)
    assert len(result) == 1  # Only valid candidate passes
    assert "skipping candidate" in caplog.text  # Skip reason logged
```

### Fallback Path with Valid-Result Assertion

```python
def test_pose_distance_falls_back_to_bbox(caplog, candidates_without_keypoints, cfg):
    with caplog.at_level(logging.WARNING):
        result = compute_diversity(candidates_without_keypoints, cfg)
    assert result >= 0.0  # Fallback still produces valid diversity score
    assert "falling back to bbox distance" in caplog.text
```

### Guard Clause with Exact-Default Assertion

```python
def test_empty_input_returns_empty_list(cfg):
    result = select_batch([], cfg)
    assert result == []  # Exact return value, not just "doesn't crash"
    assert isinstance(result, list)  # Exact type
```

---

## Observability Testing Patterns

### structlog Testing

**Using `capture_logs()` context manager:**

```python
from structlog.testing import capture_logs

def test_pipeline_logs_event_count():
    with capture_logs() as cap_logs:
        system.process(test_input)

    completed_logs = [l for l in cap_logs if l["event"] == "processing_complete"]
    assert len(completed_logs) == 1
    assert "count" in completed_logs[0]
    assert completed_logs[0]["log_level"] == "info"
```

**Using pytest-structlog plugin:**

```python
def test_with_structlog_plugin(log):  # 'log' fixture from pytest-structlog
    process_input(test_input)
    assert log.has("processing_complete", count=3)
    assert log.has("validation_complete")
    assert "pipeline_error" not in log.events
```

### caplog (stdlib logging)

```python
import logging

def test_function_logs_decision(caplog):
    with caplog.at_level(logging.DEBUG):
        result = function_under_test(input_data)
    assert "decision reason" in caplog.text
```

### Log Collector Fixture

```python
@pytest.fixture
def log_collector(caplog):
    """Structured log assertion helper."""
    caplog.set_level(logging.DEBUG)

    class LogCollector:
        @property
        def messages(self):
            return [r.message for r in caplog.records]

        def has_event(self, event_name: str) -> bool:
            return any(event_name in m for m in self.messages)

        def events_at_level(self, level: int) -> list[str]:
            return [r.message for r in caplog.records if r.levelno == level]

        @property
        def errors(self):
            return self.events_at_level(logging.ERROR)

    return LogCollector()
```

### Testing Logging Overhead

```python
@pytest.mark.benchmark
def test_logging_overhead(benchmark, system_under_test, standard_input):
    """Logging should add < 5% overhead to critical path latency."""
    import logging

    # With logging
    time_with = benchmark.pedantic(system_under_test.process, args=(standard_input,), rounds=50)

    # Without logging
    logging.disable(logging.CRITICAL)
    time_without = benchmark.pedantic(system_under_test.process, args=(standard_input,), rounds=50)
    logging.disable(logging.NOTSET)

    overhead_pct = (time_with - time_without) / time_without * 100
    assert overhead_pct < 5.0, f"Logging overhead: {overhead_pct:.1f}%"
```

---

## Resource Leak Detection

### Memory Leak Testing (pytest-memray)

```python
@pytest.mark.limit_leaks("512 KB")
def test_processing_no_memory_leak(system, test_input):
    """Processing should not leak more than 512KB per call."""
    for _ in range(100):
        _ = system.process(test_input)
```

### Manual Memory Tracking

```python
import tracemalloc, gc

@pytest.fixture
def track_memory():
    gc.collect()
    tracemalloc.start()
    baseline = tracemalloc.take_snapshot()
    yield
    gc.collect()
    current = tracemalloc.take_snapshot()
    stats = current.compare_to(baseline, "lineno")
    leaked = sum(s.size_diff for s in stats if s.size_diff > 0)
    tracemalloc.stop()
    assert leaked < 10 * 1024 * 1024, f"Memory leak: {leaked / 1024 / 1024:.1f} MB"
```

### File Handle Leak Detection

```python
@pytest.fixture
def track_file_handles():
    import psutil, os
    proc = psutil.Process(os.getpid())
    initial_fds = proc.num_fds()
    yield
    gc.collect()
    final_fds = proc.num_fds()
    assert final_fds <= initial_fds + 5, f"File handle leak: {initial_fds} -> {final_fds}"
```

---

## Async Testing

```python
import pytest, asyncio

@pytest.mark.asyncio
async def test_async_processing(service):
    inputs = [create_test_input(i) for i in range(5)]
    results = await asyncio.gather(*[service.aprocess(inp) for inp in inputs])
    assert len(results) == 5
    assert all(r is not None for r in results)

@pytest.mark.asyncio
async def test_async_timeout():
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(slow_operation(large_input), timeout=5.0)

@pytest.mark.asyncio
async def test_async_cancellation():
    task = asyncio.create_task(service.aprocess(test_input))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.resources_released()
```

---

## Test Organization

### Recommended Structure

```
tests/
├── conftest.py              # Shared: markers, session fixtures, factory imports
├── fixtures/
│   ├── __init__.py
│   ├── factories.py         # Factory fixtures for domain objects
│   └── resources.py         # Shared resource fixtures (session-scoped)
├── unit/
│   ├── conftest.py          # Unit-specific: mocks, stubs
│   ├── test_{module_a}.py
│   └── test_{module_b}.py
├── integration/
│   ├── conftest.py          # Integration: real resource loading
│   └── test_{integration}.py
├── regression/
│   ├── conftest.py          # Regression baselines
│   ├── test_accuracy.py
│   └── test_performance.py
├── system/
│   ├── test_{e2e}.py
│   └── test_{benchmark}.py
└── fixtures/data/
    ├── valid_input.json
    ├── edge_case.json
    └── production_samples/    # Stored production data (Pass C, if applicable)
```

### conftest.py Template

```python
"""Root conftest.py — shared test fixtures and configuration."""
import pytest
from typing import Generator

# Register fixture modules
pytest_plugins = [
    "tests.fixtures.factories",
    "tests.fixtures.resources",
]

# Add domain-specific markers from specializations as needed

# === Markers ===

def pytest_configure(config):
    for marker in [
        "smoke: Quick sanity checks (< 5s each)",
        "unit: Isolated unit tests",
        "integration: Multi-component integration tests",
        "regression: Regression tests against baselines",
        "benchmark: Performance benchmark tests",
        "hitl: Hardware/software in the loop tests",
        "system: System-level tests",
        "golden: Golden example tests",
        "slow: Tests taking > 30 seconds",
        "nightly: Run only in nightly CI",
    ]:
        config.addinivalue_line("markers", marker)

# Auto-mark slow tests
def pytest_collection_modifyitems(items):
    for item in items:
        if "benchmark" in item.nodeid or "regression" in item.nodeid:
            item.add_marker(pytest.mark.slow)
```

### Test Naming Conventions

```
test_{function}_{scenario}_{expected_outcome}
```

Examples:
- `test_select_batch_empty_input_returns_empty_list`
- `test_select_batch_exceeds_limit_truncates`
- `test_process_valid_input_returns_result`

### Selective Running

```bash
# Developer workflow: fast feedback
pytest -m "smoke or unit" -x --timeout=60

# PR CI: comprehensive but skip expensive
pytest -m "not nightly and not hitl" --timeout=300

# Nightly: everything including regression
pytest --timeout=3600

# Parallel execution (resource fixtures stay shared with loadscope)
pytest -n 4 --dist loadscope -m "unit or integration"
```

---

## Coverage Strategy

| Code Type | Target | Rationale |
|-----------|--------|-----------|
| Core algorithms | 95%+ | High risk, high value |
| Business logic | 90%+ | Critical paths |
| Integration points | 80%+ | Contract verification |
| Utilities | 70%+ | Support code |
| Configuration | 60%+ | Validation only |

### What NOT to Test

- Third-party library internals
- Simple property accessors
- Framework boilerplate
- Generated code

---

## Mock Strategy

### When to Mock

- External services (APIs, databases in unit tests)
- Time-dependent code (`datetime.now()`)
- Random behavior
- Expensive computations (in unit tests)
- File system (sometimes)

### When NOT to Mock

- The code under test
- Simple value objects
- Pure functions
- In integration tests (use real dependencies)

```python
# Patching
from unittest.mock import patch

@patch("module.external_service")
def test_function_calls_service(self, mock_service):
    mock_service.return_value = {"result": "success"}
    result = function_under_test()
    mock_service.assert_called_once_with(expected_args)

# Fixture-based mock
@pytest.fixture
def mock_service():
    with patch("module.external_service") as mock:
        mock.return_value = {"result": "success"}
        yield mock
```

---

## Checklist Before Approving Test Architecture

- [ ] All public functions have test coverage planned
- [ ] Edge cases identified for each function
- [ ] Factory fixtures designed for complex objects
- [ ] Mock strategy defined for external dependencies
- [ ] Test file organization matches codebase structure
- [ ] Naming conventions consistent with existing tests
- [ ] Integration test boundaries clear
- [ ] No redundant tests planned
- [ ] Log-based assertions designed for key decision points
- [ ] Pass C activation decision documented (enabled or reason_disabled)
- [ ] Production sample data strategy defined (if applicable)
- [ ] Performance baselines established for benchmark tests
- [ ] Hypothesis properties identified for algorithmic code
- [ ] Async test patterns applied where applicable
- [ ] Resource leak detection fixtures configured
- [ ] Test markers assigned for selective CI execution
- [ ] Test data is config-independent (no values hand-tuned to defaults)
- [ ] Config-adaptive helpers designed for config-dependent tests
- [ ] Precondition assertions specified for upstream-dependent tests
- [ ] Negative/defensive paths identified and tested per chunk
- [ ] Assertion strength appropriate: scoring uses magnitude+ordering, never ordering alone
- [ ] Config-sensitivity dimensions identified and covered in Pass B
