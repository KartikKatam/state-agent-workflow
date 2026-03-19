# Test Architecture

> **Purpose**: Enables the Test Architecture Phase in plan-architect — designing comprehensive test skeletons (Pass A: chunk-wise, Pass B: holistic, Pass C: system-level) before implementation begins.
> **Consumers**: plan-architect
> **Schemas**: `implementation-plans/schemas/implementation-plan.schema.json` (test_architecture section)
> **Depends on**: `multi-perspective-analysis` (for test strategy option exploration when 2+ viable strategies exist)
> **Reference**: `patterns.md` in this directory (load on demand for test pattern details, conftest templates, log capture patterns)

## What You Learn From This Skill

- When to enable/skip the Test Architecture Phase
- Pass A: chunk-wise tests (invariant, golden example, quality gate)
- Pass B: holistic tests (integration, functionality, cross-module)
- Pass C: system-level validation (real sensor data, HIL, e2e pipeline, performance)
- Log-based assertion design for tests that verify internal behavior
- How to present test architecture summary for approval
- When to activate multi-perspective analysis for test strategy decisions

## Contract

- Test Architecture Phase comes AFTER all chunks are detailed
- Ask user before enabling (it's a switch, respect "no")
- When 2+ viable test strategies exist for a dimension, activate multi-perspective analysis
- Present Pass A, Pass B, and Pass C (if applicable) separately for approval
- Include test architecture in plan JSON when enabled
- Design log-based assertions for behaviors not visible in return values

---

## Output Format

**CRITICAL**: The test architecture output is a **markdown specification document**, NOT a `.py` test file. The plan-architect is a planner — it NEVER writes code, including test code.

For each chunk, the specification includes:
- Test class and method names
- Docstrings describing what each test verifies
- Input descriptions (what to arrange, with specific values)
- Expected output descriptions (what to assert, with exact expected values)
- Assertion criteria (exact numbers, orderings, types, log substrings)

The code examples in Pass A/B/C below are **reference patterns for the chunk-coder** showing the test style to follow. They are NOT the output format for Gate 2. The plan-architect writes specs in markdown; the chunk-coder writes `.py` test code.

**Per-chunk delivery**: Test specs are organized per-chunk in the markdown document. Each chunk-coder reads only its chunk's section, writes those tests into the test file, then implements production code to make them pass. The test file grows incrementally per chunk — it is NEVER pre-populated with all chunks' tests upfront.

**Example Gate 2 output** (per-chunk section):

```markdown
### Chunk-01 Tests

#### TestNormalizeMetric (7 tests)
| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_midpoint | value=50, low=0, high=100 | normalize_metric(50, 0, 100) | == 0.5 |
| test_at_low | value=0, low=0, high=100 | normalize_metric(0, 0, 100) | == 0.0 |
| test_clamps_below | value=-10, low=0, high=100 | normalize_metric(-10, 0, 100) | == 0.0 |

#### TestChunk01Golden (1 test)
| Test | Input | Expected |
|------|-------|----------|
| test_golden_mixed_bin | 6 ROIs: A(top8=T,h=80,ten=3000), B(...), ... | 4 pass gate; OCR ordering E > A > B > F |
```

---

## When to Enable Test Architecture Phase

Enable if ANY of these are true:
- Feature has >3 chunks
- Feature involves algorithm design
- Feature has complex state management
- Feature integrates with external systems
- Feature has >5 edge cases identified
- User explicitly requests "detailed test planning"

Skip if ALL of these are true:
- Feature is simple (1-2 chunks)
- Tests are straightforward CRUD operations
- No complex state or algorithms
- User says "skip test architecture" or "simple tests are fine"

### Asking the User

After ALL chunk details are agreed:

```
All {N} chunks are scoped. Before final approval, I recommend a
**Test Architecture Phase** to design comprehensive tests.

With the complete picture, I can now design:

**Pass A - Chunk-wise Tests:**
- Invariant verification tests (existence, behavioral)
- Golden example tests (expected inputs → outputs)
- Linting/style compliance per chunk

**Pass B - Holistic Tests:**
- Module-level integration tests
- File-wise complete functionality tests
- Cross-chunk interaction tests

{if feature touches pipeline/hardware:}
**Pass C - System-Level Validation:**
- Real sensor data tests
- End-to-end pipeline tests
- Performance benchmarks
{endif}

This feature has {reason why it might benefit}:
- {signal 1}
- {signal 2}

Enable Test Architecture Phase?
  [yes] - Design comprehensive tests now (recommended)
  [no] - Skip, handle tests per-chunk during implementation
  [partial] - Just Pass A (chunk-wise), skip holistic tests

Your choice:
```

---

## Test Data Strategy

Annotate each `test_spec` case with `data_strategy`. Three layers:

| Layer | `data_strategy` | Description |
|-------|----------------|-------------|
| **L1 Golden** | `"L1 golden"` | Deterministic, config-independent values. Test constructs its own config explicitly — NEVER rely on defaults. See A.2 for value selection rules, A.7 for assertion strength. |
| **L2 Config-Adaptive** | `"L2 config-adaptive"` | Factory helpers that derive data FROM config. Test bench stays the same; config varies; data adjusts. See `patterns.md` Pattern 2b. |
| **L3 Property-Based** | `"L3 property-based"` | Hypothesis — random valid inputs, verify invariants. Architect specifies: properties, input domain, settings overrides. |

Most chunks use a mix of L1 and L2. L3 is for algorithmic invariants.

---

## Pass A: Chunk-wise Tests (Invariant & Golden Examples)

Design tests for EACH chunk that verify:

### A.1 Invariant Tests

```
## Chunk-01 Invariant Tests

### Existence Invariants
- test_batch_candidate_importable: Import BatchCandidate succeeds
- test_config_fields_exist: batch_size, min_quality in config

### Behavioral Invariants
- test_select_batch_empty_returns_empty: Empty input → empty output
- test_select_batch_respects_max_size: Never exceeds batch_size
- test_select_batch_filters_below_min: Drops candidates < min_quality

### Constraint Invariants
- test_batch_size_positive: batch_size > 0 enforced
- test_quality_in_range: min_quality ∈ [0, 1]
```

### A.2 Golden Example Tests

```python
@pytest.mark.golden
def test_select_batch_golden_example_1():
    """
    Golden Example: Standard selection

    Input:
      candidates = [quality: 0.9, 0.7, 0.3, 0.8]
      batch_size = 2
      min_quality = 0.5

    Expected Output:
      selected = [quality: 0.9, 0.8]  # Top 2 above threshold
    """
    pass  # chunk-coder implements
```

**Golden Example Design Rules:**
- Config-independent by construction. If a test needs `min_quality=0.5`, construct a config with that value — don't assume it's the default.
- Choose values with semantic meaning: boundary (0, 1), midpoint (0.5), just-above/below-threshold, degenerate (empty, single).
- For assertion strength on scoring/ranking tests, see A.7.

### A.3 Log-Based Assertions (chunk-wise)

For behaviors not visible in return values, design tests that assert on log output:

```python
def test_select_batch_logs_rejection_reasons(caplog):
    """Verify that rejected candidates are logged with reasons."""
    with caplog.at_level(logging.DEBUG):
        result = select_batch(candidates, config)

    # Assert internal decision-making is logged
    assert "rejected candidate" in caplog.text
    assert "quality=0.3 < threshold=0.5" in caplog.text
```

**When to design log assertions:**
- Decision points where the code chooses between paths (selection, filtering, routing)
- Performance-sensitive operations where timing should be logged
- Error recovery paths where the recovery strategy should be visible
- Pipeline stage transitions where entry/exit should be traceable

### A.4 Linting & Style Tests

Per-chunk, verify:
- `ruff format --check {chunk_files}` passes
- `ruff check {chunk_files}` passes
- `pyright {chunk_files}` passes

This is baked into chunk-coder's quality gate, but listed here for completeness.

### A.5 Negative/Defensive Path Coverage

Identify and test these defensive paths per chunk:

- **Silent-skip paths** — code silently drops data (e.g., `if not valid: continue`). Test: verify item absent from output AND logging occurs explaining why.
- **Error-swallowing paths** — broad `try/except` that catches and continues. Test: trigger the exception, verify fallback behavior produces correct output.
- **Fallback paths** — primary logic fails, code falls back to alternative. Test: trigger the fallback condition, verify valid result AND log indicating fallback was used.
- **Guard clause paths** — early return on edge condition (empty input, None). Test: verify exact return value and type, not just "doesn't crash."

Each defensive path → a `test_spec` case with category `negative_path` or `edge_case`. "No exception raised" is never sufficient — assert on exact output AND logged reason.

### A.6 Precondition Verification

When a test depends on upstream behavior, populate the `preconditions` array with natural-language assertions. Chunk-coder implements each as an `assert` with descriptive `f"Precondition failed: ..."` message. See `patterns.md` Pattern 6.

**When to add:**
- Test arranges data expecting a specific intermediate result
- Test depends on another function's output (scoring → selection)
- Test exercises stage interaction (stage N output feeds stage N+1)

### A.7 Assertion Strength

Choose the appropriate assertion type for each test. The architect specifies assertion strength in the `tests` description field.

| Type | Example | Use When |
|------|---------|----------|
| **Exact** | `== 0.5` | Deterministic computation, golden examples |
| **Magnitude** | `>= 0.3` | Scores, weights — prevents collapse to zero |
| **Ordering** | `A > B` | Ranking — but NEVER alone for scoring tests |
| **Range** | `0.2 <= x <= 0.8` | Bounded outputs, normalized values |
| **Property** | `len(out) <= len(inp)` | Invariants that hold for all valid inputs |
| **Spread** | `A - B >= 0.05` | Discrimination — prevents degenerate ties |

**Mandatory rule**: Scoring/ranking tests NEVER use ordering alone. Always pair with magnitude floor. When discrimination matters, add spread. Specify assertion types in the `tests` description field.

---

## Pass B: Holistic Integration & Functionality Tests

Design tests that span multiple chunks or verify complete module functionality.

### B.1 Module Integration Tests

```python
class TestBatchPipelineIntegration:
    """Tests spanning chunk-01 and chunk-02."""

    def test_selection_with_diversity(self, pipeline_fixture):
        """Full batch selection with diversity scoring."""
        pass

    def test_batch_respects_both_quality_and_diversity(self):
        """Quality threshold + diversity balance work together."""
        pass
```

### B.2 File-wise Complete Functionality Tests

```python
class TestOpsBatchComplete:
    """Comprehensive tests for ops_batch module."""

    def test_full_batch_workflow(self):
        """End-to-end: raw candidates → final batch."""
        pass

    def test_module_handles_all_edge_cases(self):
        """Combined edge cases that span functions."""
        pass
```

### B.3 Cross-Module Tests

```python
class TestPipelineIntegration:
    """Tests batch selection within full pipeline."""

    def test_pipeline_uses_batch_selection(self, full_pipeline):
        """Pipeline correctly invokes batch selection."""
        pass

    def test_pipeline_handles_empty_batch(self, full_pipeline):
        """Pipeline gracefully handles no candidates."""
        pass
```

### B.4 Log-Based Integration Assertions

For integration tests, log assertions verify cross-component communication:

```python
def test_pipeline_stage_transitions_logged(caplog, full_pipeline):
    """Verify pipeline logs stage entry/exit with timing."""
    with caplog.at_level(logging.INFO):
        full_pipeline.run(test_input)

    # Verify stage transitions are traceable
    log_messages = [r.message for r in caplog.records]
    assert any("entering stage: batch_selection" in m for m in log_messages)
    assert any("exiting stage: batch_selection" in m for m in log_messages)
    # Verify timing is logged for performance tracking
    assert any("duration_ms" in m for m in log_messages)
```

### B.5 Config-Sensitivity Testing

Same data, different config values. Three categories:

1. **Breaking configs** — values that SHOULD cause rejection/error/empty output.
2. **Normal variations** — different valid configs that change behavior. Parametrize.
3. **Config interactions** — two+ dimensions interact. Most valuable, hardest to design.

Identify config dimensions, rank by impact (high/medium/low), design tests for high-impact ones. Document in `module_tests.config_sensitivity`. See `patterns.md` Config-Sensitivity Parametrized Tests for templates.

---

## Pass C: System-Level Validation (CONDITIONAL)

**Activate when**: Feature touches a multi-stage processing pipeline, has external system dependencies, has performance requirements under real-world conditions, or involves end-to-end data flow through the system.

**Do NOT activate when**: Feature is pure business logic, configuration, or internal tooling with no pipeline/external-system interaction.

> **Domain specializations** provide concrete Pass C examples. Load `specializations/robotics-cv.md` for sensor data, SITL, and inference pipeline tests.

### C.1 Production Sample Tests

Tests using stored data from real production environments:

```python
@pytest.mark.system
@pytest.mark.slow
class TestWithProductionData:
    """Tests using stored production samples."""

    @pytest.fixture(scope="module")
    def production_samples(self):
        """Load stored production data from test fixtures."""
        return load_test_data("tests/fixtures/data/production_samples/")

    def test_processing_on_real_data(self, production_samples):
        """System processes real production data without errors."""
        for sample in production_samples:
            result = system.process(sample.input)
            assert isinstance(result, ExpectedOutputType)

    def test_quality_on_labeled_data(self, production_samples):
        """Output quality meets threshold on labeled test set."""
        labeled = [s for s in production_samples if s.has_labels]
        quality = evaluate_quality(labeled)
        assert quality >= MINIMUM_QUALITY_THRESHOLD
```

### C.2 End-to-End Pipeline Tests

```python
@pytest.mark.system
class TestEndToEndPipeline:
    """Full pipeline tests: input → all stages → output."""

    def test_full_pipeline_with_synthetic_input(self):
        """Pipeline processes synthetic input end-to-end."""
        pass

    def test_full_pipeline_with_production_sample(self, sample):
        """Pipeline processes a production sample end-to-end."""
        pass

    def test_pipeline_error_propagation(self):
        """Errors in one stage propagate correctly."""
        pass
```

### C.3 Performance Benchmarks

```python
@pytest.mark.benchmark
@pytest.mark.system
class TestPerformanceBenchmarks:
    """Verify performance meets requirements."""

    def test_processing_latency(self, benchmark_input):
        """Single-item processing within latency budget."""
        import time
        start = time.perf_counter()
        result = system.process(benchmark_input)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < MAX_LATENCY_MS

    def test_throughput(self, benchmark_batch):
        """Batch processing meets throughput requirements."""
        pass

    def test_memory_usage(self, benchmark_input):
        """Processing stays within memory budget."""
        pass
```

### C.4 Log-Based System Assertions

System tests heavily leverage logs to verify pipeline behavior:

```python
def test_pipeline_logs_complete_trace(caplog, full_pipeline, production_sample):
    """Verify complete processing trace is logged for debugging."""
    with caplog.at_level(logging.DEBUG):
        full_pipeline.run(production_sample)

    records = caplog.records
    # Verify all stages logged
    stages_logged = [r for r in records if "stage:" in r.message]
    assert len(stages_logged) >= EXPECTED_STAGE_COUNT
    # Verify no ERROR level logs (clean processing)
    errors = [r for r in records if r.levelno >= logging.ERROR]
    assert len(errors) == 0, f"Unexpected errors: {errors}"
```

---

## Test Architecture Summary (Present to User)

```
## Test Architecture Summary

### Pass A: Chunk-wise Tests

| Chunk | Invariant | Golden | Negative | Precond | Log-Assert | Quality | Total |
|-------|-----------|--------|----------|---------|------------|---------|-------|
| chunk-01 | {n} | {n} | {n} | {n} | {n} | 3 | {n} |
| chunk-02 | {n} | {n} | {n} | {n} | {n} | 3 | {n} |
| **Total** | {n} | {n} | {n} | {n} | {n} | {n} | **{N}** |

### Pass B: Holistic Tests

| Scope | Integration | Functionality | Config-Sensitivity | Log-Assert | Total |
|-------|-------------|---------------|-------------------|------------|-------|
| producer/ops_batch.py | {n} | {n} | {n} | {n} | {n} |
| Pipeline integration | {n} | {n} | {n} | {n} | {n} |
| **Total** | {n} | {n} | {n} | {n} | **{N}** |

{if Pass C enabled:}
### Pass C: System-Level Validation

| Category | Tests | Marks |
|----------|-------|-------|
| Real sensor data | {n} | @system, @slow |
| E2E pipeline | {n} | @system |
| Performance | {n} | @benchmark, @system |
| **Total** | **{N}** | |
{endif}

### Test Infrastructure

**Fixtures (conftest.py):**
- {fixture_1}: {purpose}
- {fixture_2}: {purpose}

**Factories:**
- make_{object}: Creates {object} with defaults

**Marks:**
- @pytest.mark.golden: Golden example tests
- @pytest.mark.integration: Cross-chunk tests
- @pytest.mark.system: System-level tests
- @pytest.mark.benchmark: Performance benchmarks
- @pytest.mark.slow: Long-running tests

### Quality Metrics

**Assertion strength**: {n} exact, {n} magnitude, {n} ordering+magnitude, {n} property
**Data strategy**: {n} L1 golden, {n} L2 config-adaptive, {n} L3 property-based
**Defensive coverage**: {n} negative paths tested, {n} precondition assertions

---

**Test Architecture Ready**

Say **"test architecture approved"** to continue to final review,
or request changes.
```

---

## Saving Test Architecture (Two Gates)

The test architecture follows the same plain-text-first discipline as planning:

### Gate A: Plain-Text Test Specs

Write the complete test architecture as human-readable markdown to `.claude/plans/{feature}-test-specs.md`. This document contains per-chunk test tables, module-level integration scenarios, golden examples, and test infrastructure decisions. Send the file path via message — the user reads it directly and provides feedback or approval.

Do NOT touch the plan JSON until the user approves the plain-text specs.

### Gate B: Append to Plan JSON

After the user approves the plain-text specs, APPEND two things to the existing plan JSON at `.claude/plans/{feature}-plan.json`:

1. **`test_spec`** on each chunk — what tests the chunk-coder writes
2. **`module_tests`** at plan level — holistic/integration tests for the hardening chunk

Do NOT overwrite any existing plan fields. Only populate these previously-empty sections.

### Per-Chunk `test_spec`

Appended to each chunk. The chunk-coder reads this to know what tests to write.

```json
{
  "id": "chunk-01",
  "test_spec": {
    "test_file": "tests/test_batch.py",
    "test_type": "unit",
    "cases": [
      {
        "name": "test_select_batch_empty_input",
        "tests": "returns empty list for empty input",
        "category": "edge_case"
      },
      {
        "name": "test_select_batch_quality_filtering",
        "tests": "filters candidates below min_quality threshold",
        "category": "core_logic"
      },
      {
        "name": "test_golden_standard_selection",
        "tests": "4 candidates -> selects top 2 above threshold",
        "category": "golden"
      },
      {
        "name": "test_logs_rejection_reasons",
        "tests": "caplog contains 'rejected' with reason for each filtered candidate",
        "category": "log_assertion"
      },
      {
        "name": "test_scoring_magnitude_and_spread",
        "tests": "top candidate score >= 0.3 AND spread >= 0.05 between rank 1 and rank 2",
        "category": "core_logic",
        "data_strategy": "L1 golden",
        "preconditions": ["all candidates pass quality gate (quality >= min_quality)"]
      },
      {
        "name": "test_silent_skip_logs_reason",
        "tests": "candidate with None keypoints is skipped; caplog contains skip reason",
        "category": "negative_path",
        "data_strategy": "L2 config-adaptive"
      }
    ]
  }
}
```

**Test Categories** (from Pass A/B/C mapping):

| Category | Source | Description |
|----------|--------|-------------|
| `edge_case` | Pass A.1 | Empty input, null, boundary conditions |
| `core_logic` | Pass A.1 | Main functionality, behavioral invariants |
| `constraint` | Pass A.1 | Config limits, validation, range enforcement |
| `golden` | Pass A.2 | Known input/output pairs with exact expected values |
| `log_assertion` | Pass A.3 | caplog-based assertions on internal decisions |
| `error` | Pass A.1 | Exception handling, error recovery |
| `negative_path` | Pass A.5 | Silent skips, fallbacks, guard clauses |
| `config_sensitivity` | Pass B.5 | Same data, different config values |
| `property` | Hypothesis | Invariants verified over random valid inputs |
| `performance` | Pass C.3 | Latency, throughput, memory benchmarks |
| `integration` | Pass B | Cross-chunk, cross-module interaction |

### Plan-Level `module_tests`

Appended at the plan's top level. The hardening chunk implements these.

```json
{
  "module_tests": {
    "integration_scenarios": [
      {
        "name": "full_batch_selection",
        "description": "Select batch from candidates with quality and diversity scoring",
        "covers": ["chunk-01", "chunk-02", "chunk-03"],
        "setup": "Load test fixtures with 100 candidates"
      }
    ],
    "golden_examples": [
      {
        "name": "diverse_selection",
        "type": "normal",
        "input_description": "100 candidates, 20 high-quality, 10 duplicates",
        "expected_behavior": "Returns 32 candidates, no duplicates, quality > 0.5"
      }
    ],
    "error_recovery": [
      {
        "trigger": "All candidates have None keypoints",
        "expected_handling": "Pose distance falls back to bbox, selection proceeds"
      }
    ],
    "performance_criteria": {
      "latency": "< 5ms for 32 candidates",
      "memory": "< 1MB additional"
    },
    "config_sensitivity": {
      "dimensions": [
        {"name": "diversity_weight", "impact": "high", "test_approach": "parametrize 0.0/0.5/1.0, verify winner changes"},
        {"name": "min_quality", "impact": "medium", "test_approach": "parametrize 0.1/0.5/0.9, verify output count changes"}
      ],
      "breaking_configs": [
        {"config_change": "min_quality=1.0", "expected_behavior": "all candidates rejected, empty output"},
        {"config_change": "batch_size=0", "expected_behavior": "raises ValueError"}
      ],
      "interaction_tests": [
        {"dimensions": ["diversity_weight", "min_quality"], "description": "high diversity + low quality → diverse low-quality batch"}
      ]
    }
  }
}
```

### Hardening Chunk Requirement

Every plan must have at least one chunk with:
- `purpose: "hardening"`
- At least one `system` level invariant
- Dependencies on `core_logic` chunks
- The hardening chunk implements the `module_tests` integration scenarios and golden examples

---

## Multi-Perspective Analysis for Test Strategies

When the test architecture phase identifies 2+ viable test strategies for a dimension, activate the `multi-perspective-analysis` protocol.

### When to Activate

- **Testing approach**: unit-heavy vs integration-heavy for a module
- **Mock strategy**: mock externals vs real fixtures for specific dependencies
- **Coverage scope**: minimal (happy path + critical) vs exhaustive
- **Organization**: per-function vs per-behavior vs per-scenario
- **Log assertion scope**: minimal (key decisions only) vs comprehensive (full trace)

### How to Activate

Use the divergent exploration protocol from the `multi-perspective-analysis` skill:

1. Identify the test strategy dimension with multiple valid options
2. Spawn Explore sub-agents per option, each evaluating:
   - Test count and runtime
   - Fixture/mock requirements
   - Coverage gaps
   - Maintenance burden
   - Confidence provided
3. Synthesize and present comparison with recommendation
4. User picks, combines, or refines

### When NOT to Activate

- The codebase already has an established test pattern for this type of code
- The test strategy is obvious given the code structure
- Only one viable approach exists
