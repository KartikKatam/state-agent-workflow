# Failure Report Templates

Concrete templates for the three types of blind failure reports.

## Behavioral Test Failure

The most common report type. One per failing test.

```
TEST_ID:         TC-BATCH-003
SCENARIO:        Given candidates with scores [0.9, 0.3, 0.8],
                 When select_batch is called with threshold=0.5 and n=2,
                 Then result contains exactly 2 candidates ordered by score
EXPECTED:        [Candidate(id="a", score=0.9), Candidate(id="c", score=0.8)]
ACTUAL:          [Candidate(id="a", score=0.9), Candidate(id="b", score=0.3)]
DEVIATION_TYPE:  wrong_value
REPRODUCTION:    select_batch([C(0.9), C(0.3), C(0.8)], threshold=0.5, n=2)
SPEC_REFERENCE:  plan.md task-3 §2: "returns top N above threshold, ordered by score"
REPRODUCIBLE:    true
```

## Property Violation

For Hypothesis/property-based test failures. Includes shrinking information.

```
TEST_ID:         TC-BATCH-PBT-001
SCENARIO:        For all valid score lists, output is sorted descending
PROPERTY:        For any result of select_batch: result[i].score >= result[i+1].score
COUNTEREXAMPLE:  scores=[0.5, 0.5000000000000001] — output order is reversed
SHRUNK_FROM:     Original: 47-element list. Shrunk to 2 elements.
SPEC_REFERENCE:  plan.md task-3 §2: "ordered by score"
REPRODUCIBLE:    pytest tests/test_batch.py::test_sorted --hypothesis-seed=42
```

## Mutation Gap Report

For mutation testing survivors. These are TEST GAPS, not code bugs.

```
REPORT_TYPE:     test_gap_report
MUTATION_SCORE:  45/60 = 75%

SURVIVORS:
  [1] BEHAVIOR:   Threshold boundary handling
      MEANING:    No test verifies behavior when score == threshold exactly
      GAP:        Add boundary test at score == threshold

  [2] BEHAVIOR:   Empty input handling
      MEANING:    No test verifies return value for empty candidate list
      GAP:        Add test: select_batch([], threshold=0.5, n=10) → expected []

INTERPRETATION:  Survivors are TEST GAPS, not code bugs. Implementation may be
                 correct — tests cannot prove it.
```

## Spec-Absent Reporting

When no formal spec exists, adapt the SPEC_REFERENCE field:

| Source Available | SPEC_REFERENCE Format |
|------------------|----------------------|
| Formal spec document | `spec.md §4.2: "All unauthenticated requests return 401"` |
| Design doc | `design.md §3.1: "Batch selector filters below threshold"` |
| PR description | `PR #42: "Add authentication to all /api/* endpoints"` |
| Ticket/issue | `JIRA-123 AC-2: "Unauthenticated users see 401"` |
| API docstring | `select_batch docstring: "Returns top N above threshold"` |
| Conversation/Slack | `"Per discussion 2026-02-28: threshold is inclusive (>=)"` |

Always cite the most authoritative source available. When multiple sources
exist, cite the most specific one.
