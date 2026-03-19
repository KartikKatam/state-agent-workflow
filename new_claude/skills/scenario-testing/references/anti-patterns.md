# Scenario Testing Anti-Patterns

Common mistakes in scenario testing, blind testing, and sub-agent delegation.
Each anti-pattern shows WRONG and RIGHT approaches.

---

## Anti-Pattern 1: Tester Reads Implementation Code

The most damaging failure mode. Once the tester sees the code, their tests
unconsciously confirm "the code does what it does" instead of "the code does
what the spec says."

```python
# WRONG — tester looked at the implementation and saw it uses a dict
def test_select_batch_uses_dict_lookup():
    """Tests that internal dict is populated correctly."""
    result = select_batch(candidates, threshold=0.5, n=2)
    # This tests an implementation detail, not behavior
    assert hasattr(result, '_internal_dict')
    assert 'a' in result._internal_dict

# RIGHT — tester tests behavioral contract from spec only
def test_select_batch_returns_top_candidates():
    """Spec: returns top N above threshold, ordered by score."""
    result = select_batch(candidates, threshold=0.5, n=2)
    assert len(result) == 2
    assert result[0].score >= result[1].score
    assert all(c.score >= 0.5 for c in result)
```

**Why this matters:** AugmenTest research (arXiv:2501.17461) showed spec-only
oracle generation achieves 30% success vs 8.2% for code-based approaches. The
LLM generates tests that make the current code pass rather than tests that
verify the spec.

---

## Anti-Pattern 2: Failure Reports Reference Implementation

```
# WRONG — tester names internal functions and suggests fixes
TEST_ID:     TC-AUTH-007
SCENARIO:    Unauthenticated request
FINDING:     The authenticate() method in auth_service.py returns None
             instead of raising AuthError. Line 42 is missing the token check.
SUGGESTION:  Add `if not token: raise AuthError()` at line 42.

# RIGHT — tester describes behavioral gap only
TEST_ID:     TC-AUTH-007
SCENARIO:    Given no Authorization header, When GET /api/orders,
             Then response status MUST be 401
EXPECTED:    HTTP 401, body: {"error": "unauthorized"}
ACTUAL:      HTTP 200, body: {"orders": []}
DEVIATION:   wrong_status
SPEC_REF:    spec.md §4.2: All unauthenticated /api/* requests return 401
```

**Why this matters:** Implementation references steer the coder toward the
assumed cause, not the actual root cause. The tester may have the right symptom
but the wrong cause. Let the coder use their implementation knowledge.

---

## Anti-Pattern 3: Sub-Agent Receives Implementation Code

```python
# WRONG — sub-agent prompt includes the code
prompt = f"""
Write tests for this function:

```python
def select_batch(candidates, threshold, n):
    filtered = [c for c in candidates if c.score > threshold]  # Bug: > not >=
    return sorted(filtered, key=lambda x: x.score, reverse=True)[:n]
```

Write pytest tests.
"""
# Sub-agent will write tests that match the buggy > behavior

# RIGHT — sub-agent receives only the spec
prompt = f"""
Write pytest tests for the following function.
Do NOT look at any implementation.

Function: select_batch(candidates: list[Candidate], threshold: float, n: int) -> list[Candidate]
Spec: Returns the top N candidates whose score is >= threshold, ordered by
      score descending. Returns empty list if no candidates meet threshold.
Preconditions: threshold in [0, 1], n >= 0
Error contract: Raises ValueError if n < 0 or threshold outside [0, 1]
"""
# Sub-agent will write tests that check >= (correct per spec), catching the > bug
```

---

## Anti-Pattern 4: Trusting Coverage as Quality Metric

```
# WRONG — "100% coverage, we're done!"
$ pytest --cov=src/batch tests/
Name                  Stmts   Miss  Cover
src/batch.py             45      0   100%
TOTAL                    45      0   100%

# Tests execute every line but assert nothing meaningful:
def test_batch():
    result = select_batch(candidates, 0.5, 2)  # No assertions!

# RIGHT — mutation score reveals test quality
$ mutmut run --paths-to-mutate src/batch.py
Killed: 3/60 (5%)  ← 100% coverage, 5% mutation score = tests prove nothing
```

**Critical finding:** LLM-generated tests on HumanEval-Java achieved 100%
line/branch coverage at only 4% mutation score — executing every line while
missing 96% of potential bugs including leap year handling.

---

## Anti-Pattern 5: Over-Mocked Tests from Sub-Agents

```python
# WRONG — sub-agent asserts on mock state, not real behavior
def test_process_order():
    mock_db = MagicMock()
    mock_db.save.return_value = True
    processor = OrderProcessor(db=mock_db)
    processor.process(order)
    mock_db.save.assert_called_once_with(order)  # Only assertion!
    # This tests that process() calls db.save — not that the order is
    # correctly processed, validated, or transformed

# RIGHT — mock the dependency, assert on real behavior
def test_process_order_returns_confirmation():
    mock_db = MagicMock()
    mock_db.save.return_value = True
    processor = OrderProcessor(db=mock_db)
    result = processor.process(order)
    assert result.status == "confirmed"
    assert result.order_id == order.id
    assert result.total == order.calculate_total()
```

**Why this happens:** AI agents have a default cooperative bias — they want
tests to pass. Mocking everything and asserting on mock state is the easiest
way to write a "passing" test that proves nothing.

**Prevention:** Add to every sub-agent prompt: "Assert ONLY on real return
values, real state changes, or real exceptions. Do NOT assert on mock.called
or mock.return_value as the primary assertion."

---

## Anti-Pattern 6: Tester Filters Findings

```
# WRONG — tester decides a finding is "probably intentional"
FINDINGS: 1 failure found (2 others suppressed — likely intentional behavior)

# RIGHT — tester reports ALL deviations from spec
FINDINGS: 3 deviations from spec:
  1. GET /api/orders without auth returns 200 (spec: 401)
  2. Response includes undocumented 'debug' field (not in schema §3.1)
  3. Error response uses 'msg' key (spec §5.2 says 'message')
```

**Why this matters:** NASA IV&V principle — filtered findings that turn out to
be real bugs are accountability failures. The tester does not decide what is
"minor" or "probably intentional." Report everything; let the coder triage.

---

## Anti-Pattern 7: Tautological Tests (Pass Against Empty Stub)

```python
# WRONG — test passes even if function is completely unimplemented
def test_select_batch_returns_something():
    result = select_batch([], 0.5, 2)
    assert result is not None or result is None  # Always true!

# WRONG — existence check, not behavior check
def test_select_batch_callable():
    assert callable(select_batch)  # Passes with any function definition

# RIGHT — test that actually fails without correct implementation
def test_select_batch_filters_below_threshold():
    candidates = [Candidate(score=0.3), Candidate(score=0.8)]
    result = select_batch(candidates, threshold=0.5, n=10)
    assert len(result) == 1
    assert result[0].score == 0.8
```

**Detection:** Run tests against a stub that raises `NotImplementedError`.
Any test that passes is tautological — reject it.

---

## Anti-Pattern 8: Homogenized Input Space

LLMs have blind spots in test data generation — they tend toward "medium"
values and miss extremes.

```python
# WRONG — all inputs are medium-sized, medium-valued
def test_case_1(): select_batch([C(0.5), C(0.6), C(0.7)], 0.5, 2)
def test_case_2(): select_batch([C(0.4), C(0.8), C(0.6)], 0.5, 3)
def test_case_3(): select_batch([C(0.3), C(0.9), C(0.5)], 0.5, 2)
# Same pattern: 3 elements, scores in [0.3, 0.9], n in [2, 3]

# RIGHT — systematic boundary coverage
def test_empty():          select_batch([], 0.5, 2)
def test_single():         select_batch([C(0.8)], 0.5, 1)
def test_all_below():      select_batch([C(0.1), C(0.2)], 0.5, 2)
def test_all_above():      select_batch([C(0.9), C(0.8)], 0.5, 2)
def test_at_threshold():   select_batch([C(0.5)], 0.5, 1)
def test_n_zero():         select_batch([C(0.9)], 0.5, 0)
def test_n_exceeds_list():  select_batch([C(0.9)], 0.5, 100)
```

**Prevention:** Mandatory use of adversarial prompt pattern for Tier 2 tests.
Hypothesis property tests (Tier 4) automatically explore the input space beyond
LLM comfort zones.

---

## Anti-Pattern 9: Progressive Disclosure Violation

```
# WRONG — tester dumps everything in first report including implementation hints
Round 1: "The authenticate() function at auth_service.py:42 doesn't check
          the token. You should add a token validation step. Also the
          middleware ordering in app.py might be wrong."

# RIGHT — progressive behavioral disclosure
Round 1: "GET /api/orders without Authorization header returns 200.
          Spec §4.2 requires 401."
Round 2: (only if coder asks) "Same failure with any missing header type,
          not just Authorization. Also fails with expired tokens."
Round 3: (only if coder still stuck) "Related passing test: authenticated
          GET /api/orders returns 200 correctly."
```

---

## Anti-Pattern 10: Skipping Tier 4 Property-Based Tests

```
# WRONG — "We have good coverage with T1-T3, T4 is optional"
# Result: misses bugs that only manifest with specific input combinations

# RIGHT — T4 catches what T1-T3 miss
@given(scores=st.lists(st.floats(0, 1, allow_nan=False), min_size=2))
def test_output_always_sorted(scores):
    candidates = [Candidate(score=s) for s in scores]
    result = select_batch(candidates, threshold=0.0, n=len(candidates))
    for i in range(len(result) - 1):
        assert result[i].score >= result[i + 1].score
# Hypothesis found: scores=[0.5, 0.5000000000000001] — floating point
# comparison breaks sort stability. T1-T3 never tested this.
```

**Why T4 matters:** Each property-based test finds approximately 50x as many
mutations as the average unit test (OOPSLA 2025 peer-reviewed study).
