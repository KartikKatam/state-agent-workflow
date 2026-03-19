# Test Design Anti-Patterns

Known failure modes in test design. Each shown with WRONG/RIGHT pairs. Load on demand when reviewing test quality or when the Fraudulent Test Detector flags a suspicious test.

---

## Anti-Pattern 1: Testing Mock Behavior

The test configures a mock to return a value, then asserts the mock returned that value. This proves the mock framework works, not the production code.

```python
# WRONG — asserts on mock's own return value
@patch("module.external_api")
def test_process(mock_api):
    mock_api.return_value = {"status": "ok"}
    result = process_data()
    assert mock_api.return_value == {"status": "ok"}  # Tautology

# RIGHT — asserts on how production code USES the mock's output
@patch("module.external_api")
def test_process(mock_api):
    mock_api.return_value = {"status": "ok", "data": [1, 2, 3]}
    result = process_data()
    assert result.item_count == 3  # Tests production logic
    assert result.status == ProcessStatus.SUCCESS
```

**Gate question**: "Am I asserting on something I configured, or something the code computed?"

---

## Anti-Pattern 2: Test-Only Methods in Production

Adding methods to production classes solely for test access. This couples production design to test needs.

```python
# WRONG — production class has test-specific API
class Pipeline:
    def process(self, data): ...
    def _get_internal_state(self):  # Only called in tests
        return self._state
    def reset_for_testing(self):   # Only called in tests
        self._state = {}

# RIGHT — test accesses state through public API or test utilities
class Pipeline:
    def process(self, data): ...

# In test file:
def test_pipeline_state_after_processing(caplog):
    pipeline = Pipeline()
    with caplog.at_level(logging.DEBUG):
        pipeline.process(test_data)
    assert "state transition: INIT -> READY" in caplog.text
```

**Gate question**: "Would this method exist if tests didn't need it?"

---

## Anti-Pattern 3: Over-Mocking

Mocking so many dependencies that the test exercises zero real code paths. The test passes regardless of production logic correctness.

```python
# WRONG — everything is mocked, test proves nothing
@patch("module.validate")
@patch("module.transform")
@patch("module.persist")
def test_pipeline(mock_persist, mock_transform, mock_validate):
    mock_validate.return_value = True
    mock_transform.return_value = "transformed"
    mock_persist.return_value = True
    result = pipeline.run("input")
    assert result is True  # Only tests that pipeline calls 3 functions

# RIGHT — mock only the external boundary, test real logic
@patch("module.database.save")  # Only mock the external service
def test_pipeline(mock_save):
    mock_save.return_value = True
    result = pipeline.run("raw input")
    assert result.transformed_value == "EXPECTED"  # Tests real transform
    assert result.validated is True                  # Tests real validation
    mock_save.assert_called_once()                  # Verifies persistence attempted
```

**Gate question**: "What real code does this test actually execute? If the answer is 'just the orchestration', mock less."

---

## Anti-Pattern 4: Incomplete Mocks

Partial mock objects that miss fields downstream code depends on. Tests pass but production fails because the mock doesn't mirror the real API shape.

```python
# WRONG — mock missing fields that downstream code reads
mock_response = {"id": 1, "name": "test"}
# Production code also reads response["metadata"]["version"] → KeyError in production

# RIGHT — mirror real API response structure
mock_response = {
    "id": 1,
    "name": "test",
    "metadata": {"version": "2.1", "timestamp": "2024-01-01T00:00:00Z"},
    "items": [],
}
```

**Gate question**: "Does this mock match the FULL shape of the real object, or just the fields my test cares about?"

---

## Anti-Pattern 5: Implementation Mirror

The test reimplements the production algorithm and compares results. If both have the same bug, the test passes.

```python
# WRONG — test mirrors production logic
def test_calculate_score(candidates):
    for c in candidates:
        expected = c.quality * 0.7 + c.diversity * 0.3  # Same formula as production
        assert calculate_score(c) == expected

# RIGHT — test against independently derived expected values
def test_calculate_score():
    candidate = Candidate(quality=1.0, diversity=0.0)
    assert calculate_score(candidate) == 0.7  # Known: 1.0 * 0.7 + 0.0 * 0.3

    candidate = Candidate(quality=0.0, diversity=1.0)
    assert calculate_score(candidate) == 0.3  # Known: 0.0 * 0.7 + 1.0 * 0.3
```

**Gate question**: "Did I compute the expected value independently, or by running the same logic in my head?"

---

## Anti-Pattern 6: Assertion-Free Tests

Tests that execute code but never assert anything meaningful. They "pass" because they don't crash — but crashing and correctness are different things.

```python
# WRONG — no meaningful assertion
def test_process_data():
    result = process_data(test_input)
    assert result is not None  # Almost always true — proves nothing

# WRONG — assertion on type only
def test_process_data():
    result = process_data(test_input)
    assert isinstance(result, list)  # True for empty list too

# RIGHT — assert on content, length, and properties
def test_process_data():
    result = process_data(test_input)
    assert len(result) == 3
    assert result[0].quality >= 0.5
    assert all(r.processed for r in result)
```

**Gate question**: "Under what input would this assertion actually fail? If I can't name one, the assertion is meaningless."

---

## Anti-Pattern 7: Testing Implementation Details

Tests break on valid refactors because they assert on HOW the code works, not WHAT it produces.

```python
# WRONG — asserts on internal call order
def test_process(mock_logger, mock_cache):
    process(data)
    assert mock_cache.get.call_count == 2  # Breaks if caching strategy changes
    assert mock_logger.debug.call_args_list == [...]  # Breaks on log message change

# RIGHT — asserts on observable behavior
def test_process():
    result = process(data)
    assert result == expected_output  # Stable across refactors
    # If caching matters, test the performance property:
    # assert second_call_faster_than_first
```

**Gate question**: "Would a valid refactor (same behavior, different structure) break this test? If yes, you're testing implementation."

---

## Anti-Pattern 8: Happy-Path-Only Testing

Only testing the normal case. Edge cases, error paths, and boundary conditions are skipped.

```python
# WRONG — only tests the golden path
def test_select_batch():
    candidates = [good_candidate_1, good_candidate_2, good_candidate_3]
    result = select_batch(candidates, normal_config)
    assert len(result) == 2

# RIGHT — systematically covers edges
class TestSelectBatch:
    def test_normal_selection(self):
        ...  # The happy path

    def test_empty_input_returns_empty(self):
        assert select_batch([], cfg) == []

    def test_all_below_threshold_returns_empty(self):
        low_quality = [Candidate(quality=0.1) for _ in range(5)]
        assert select_batch(low_quality, cfg) == []

    def test_exactly_at_threshold_included(self):
        at_threshold = Candidate(quality=cfg.min_quality)
        result = select_batch([at_threshold], cfg)
        assert len(result) == 1

    def test_single_candidate_above_threshold(self):
        result = select_batch([good_candidate], cfg)
        assert len(result) == 1
```

**Gate question**: "What inputs could make this function produce wrong output? Am I testing those inputs?"

---

## Summary Checklist

Before approving any test (yours or a teammate's):

- [ ] No mock echo — assertions test real computation, not configured values
- [ ] No production test methods — all test helpers live in test files
- [ ] Mocking at lowest necessary level — real code paths execute
- [ ] Mocks mirror full API shape — no missing fields
- [ ] Expected values computed independently — not mirroring production logic
- [ ] Every test has meaningful assertions — "not None" doesn't count
- [ ] Tests verify behavior, not implementation — survives valid refactors
- [ ] Edge cases covered — empty, single, boundary, degenerate, error paths
