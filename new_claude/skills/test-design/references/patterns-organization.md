# Test Organization

Naming conventions, directory structure, markers, and selective running.

## Naming Convention

```
test_{function}_{scenario}_{expected_outcome}
```

Examples:
- `test_select_batch_empty_input_returns_empty`
- `test_process_valid_input_logs_result`
- `test_detect_blurry_frame_filters_low_confidence`

## Directory Structure

```
tests/
  conftest.py          # Shared markers, session fixtures
  fixtures/
    factories.py       # Factory fixtures
    resources.py       # Session-scoped resources
  unit/
    test_{module}.py
  integration/
    test_{integration}.py
  system/              # Pass C tests
    test_{e2e}.py
    test_{benchmark}.py
  fixtures/data/
    valid_input.json
    edge_case.json
    production_samples/  # Stored production data (Pass C)
```

## Markers

```python
def pytest_configure(config):
    for marker in [
        "smoke: Quick sanity checks (< 5s each)",
        "unit: Isolated unit tests",
        "integration: Multi-component integration tests",
        "golden: Golden example tests",
        "benchmark: Performance benchmark tests",
        "system: System-level tests",
        "slow: Tests taking > 30 seconds",
        "nightly: Run only in nightly CI",
    ]:
        config.addinivalue_line("markers", marker)
```

## Selective Running

```bash
pytest -m "unit" -x --timeout=60          # Fast dev feedback
pytest -m "not nightly" --timeout=300      # PR CI
pytest --timeout=3600                       # Nightly: everything
pytest -n 4 --dist loadscope -m "unit"     # Parallel
```
