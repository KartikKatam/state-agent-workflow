# Review Checklists

Detailed checklists for each review scope, organized by the Code Review Pyramid priority (design → implementation → tests → adherence). Style is excluded — defer to linters.

## Task-Level Review Checklist

Used when review_level=3. Scope: single task's implementation.

### API/Design (Priority 1)

- [ ] Public function signatures match what the plan specified (parameter types, return types)
- [ ] No new public API surface that isn't in requirements (YAGNI)
- [ ] Each function has a single responsibility — can describe without "and"
- [ ] Dependencies injected as parameters, not instantiated internally
- [ ] Volatile dependencies (external services, models) behind Protocol interfaces
- [ ] Data flows in one direction through the module — no circular dependencies

**WRONG — accepting unplanned API surface:**
```
# Coder added a `reset()` method not in requirements
# Review says: "Nice addition, could be useful later"
# WRONG — this is scope creep. Flag as Important (YAGNI).
```

**RIGHT — flagging unplanned API surface:**
```
# Finding: Important — YAGNI violation
# File: src/tracker.py:89
# What: `reset()` method added but not in task requirements
# Why: Unplanned API surface creates maintenance burden and untested contract
# Fix: Remove `reset()`. If needed later, add it as a planned requirement with tests.
```

### Implementation (Priority 2)

- [ ] Logic correctly handles all stated requirements (trace each one)
- [ ] Error handling: external calls have timeouts, failures handled explicitly
- [ ] No bare `except:` — specific exception types caught
- [ ] Input validated at trust boundaries (API endpoints, file reads, user input)
- [ ] Cognitive complexity per function ≤ 15
- [ ] Nesting depth ≤ 3 (guard clauses used for preconditions)
- [ ] Function length ≤ 40 lines (exceptions: state machines, data tables)
- [ ] Function parameters ≤ 4 (group into dataclass/dict if more)
- [ ] No mutable default arguments
- [ ] External calls: timeout set, retry with backoff for transient failures

### Tests (Priority 3)

- [ ] Each `test_expectation` from the plan has a corresponding test
- [ ] Tests assert on behavior, not implementation details
- [ ] Tests have meaningful assertions (not `assert True`, not `assert x is not None`)
- [ ] Apply fraudulent test gate: "Would this pass with `return None`?" If yes → fraudulent
- [ ] Tests cover edge cases from `considerations` field
- [ ] Test descriptions were logged before implementation (code-design Step 1)
- [ ] No test-only methods in production code (`destroy()`, `reset()` only called from tests)

**WRONG — accepting a fraudulent test:**
```python
# Test file:
def test_detection_pipeline():
    pipeline = DetectionPipeline(model=mock_model)
    result = pipeline.detect(test_frame)
    assert result is not None  # This passes even if detect() returns an empty list

# Review says: "Test exists for detection pipeline ✓"
# WRONG — this test proves nothing. An empty list is "not None."
```

**RIGHT — flagging a fraudulent test:**
```
# Finding: Critical — Fraudulent test
# File: tests/test_detection.py:23
# What: test_detection_pipeline asserts only `result is not None`
# Why: Passes with empty list, empty dict, or any non-None value. Does not verify
#      detection behavior. Would pass if detect() returned "hello".
# Fix: Assert on expected detection properties:
#      assert len(result.detections) > 0
#      assert all(d.confidence > 0.5 for d in result.detections)
#      assert all(d.bbox.area() > 0 for d in result.detections)
```

### Plan Adherence (Priority 4)

- [ ] Every requirement from task spec has verified implementation
- [ ] Every limitation is respected (negative verification)
- [ ] No files modified outside `target_files` without documented deviation
- [ ] Deviations documented with: type, description, reason, impact, approval_needed
- [ ] `review_focus` items explicitly addressed in implementation
- [ ] No TODO/FIXME/HACK comments for things that should be done in this task

---

## Phase-Level Review Checklist

Used at phase completion (review_level≥2). Scope: all tasks in the phase.

Everything from the task-level checklist, PLUS:

### Cross-Task Coherence

- [ ] Functions/classes across tasks follow consistent naming patterns
- [ ] Error handling strategy is consistent across the phase
- [ ] Data types used consistently (no `dict` in one task, `dataclass` in another for same concept)
- [ ] Import structure is clean — no circular imports between task outputs
- [ ] Test patterns are consistent across tasks

### Coder Decision Logs

- [ ] Read session logs for ALL tasks in the phase
- [ ] Check for patterns: did multiple coders make the same mistake? → systemic issue
- [ ] Check for scraps: what was scrapped and why? Is the replacement actually better?
- [ ] Check for retries: what failed and how was it fixed? Is the fix robust?
- [ ] Check uncertainty declarations: were unknowns identified early or discovered late?

### Phase Integration

- [ ] Components from different tasks integrate correctly
- [ ] No conflicting assumptions between tasks (e.g., Task 1 assumes sync, Task 3 assumes async)
- [ ] Shared interfaces match — what Task 2 exports is what Task 4 imports
- [ ] Full test suite passes (not just individual task tests)

### Phase-Level Report Structure

Phase-level reviews produce a comprehensive report:

```markdown
## Phase Review: [Phase Name]

### Summary
[1-2 paragraphs: what this phase delivers, overall assessment]

### How the Code Works
[Brief architectural description: key components, data flow, integration points]

### What Tests Verify
[Summary of test coverage: what's tested, what's not, test quality assessment]

### Findings
[All findings with severity, file:line, guidance — same format as task-level]

### Coder Log Analysis
[Patterns from session logs: common issues, scraps, retries, uncertainty handling]

### Verdict: [Pass / Fix / Scrap]
[Evidence supporting the verdict]
```

---

## Final Audit Checklist

Used when all phases complete. Scope: entire feature.

Everything from phase-level checklist, PLUS:

### Production Readiness

- [ ] All scenario tests (from tester agent) pass
- [ ] No hardcoded test values in production code
- [ ] Configuration is externalized (not hardcoded paths, URLs, thresholds)
- [ ] Logging follows architectural boundary pattern (not scattered)
- [ ] No debug/development code left in (print statements, TODO hacks)
- [ ] Error messages are informative (not generic "something went wrong")

### Architectural Coherence

- [ ] Components compose cleanly — clear boundaries between modules
- [ ] No feature implemented across phases contradicts itself
- [ ] Performance-sensitive paths identified and validated
- [ ] Failure modes are understood — what happens when each dependency is down?

### Summary Report Structure

```markdown
## Final Audit: [Feature Name]

### Executive Summary
[Overall assessment: production-ready or not, key concerns]

### Architecture Review
[How components fit together, data flow, integration points]

### Test Coverage Assessment
[What's tested, quality of tests, gaps identified]

### Risk Assessment
[Remaining risks, known limitations, areas needing monitoring]

### Findings
[All findings across all phases, deduplicated, with severity]

### Verdict: [Pass / Fix / Scrap]
[Evidence and reasoning]
```

---

## Arbitration Review Checklist

Used when resolving tester-coder disputes.

### Evidence Gathering

- [ ] Read tester's report: what failed, expected vs actual, test code
- [ ] Read coder's response: why implementation is correct, counter-evidence
- [ ] Read plan requirement: the original specification
- [ ] Read actual code: the implementation in question
- [ ] Read actual test: the test in question
- [ ] Determine: does the implementation satisfy the requirement as written?
- [ ] Determine: does the test correctly verify the requirement as written?

### Ruling Criteria

| Question | If Yes | If No |
|----------|--------|-------|
| Does the implementation satisfy the plan requirement? | Coder may be right | Coder may be wrong |
| Does the test correctly verify the plan requirement? | Tester may be right | Tester may be wrong |
| Is the plan requirement unambiguous? | Ruling can be definitive | Requirement ambiguity — escalate |
| Does resolution stay within this task's scope? | Auditor can rule | Cross-scope — escalate |

### Worked Example

**Dispute:** Tester says `parse_input()` should return an empty list for invalid input. Coder says it should raise `ValueError`. Both cite the plan.

**Evidence trace:**

1. Read plan requirement: "Task 2, R3: parse_input() must handle invalid input gracefully."
2. Read implementation: `src/parser.py:34` — `raise ValueError(f"Invalid format: {raw}")` on malformed input.
3. Read test: `tests/test_parser.py:56` — `with pytest.raises(ValueError)` — test expects the exception.
4. Read tester's scenario test: `tests/scenarios/test_parser_edge.py:12` — `assert parse_input(bad_data) == []` — test expects empty list.
5. Coder's position: "ValueError is graceful — it gives the caller a specific error to handle."
6. Tester's position: "Graceful means don't crash the caller. Return a neutral value."

**Analysis:** The plan says "handle gracefully" without specifying the mechanism. Both interpretations are defensible: raising a specific exception IS graceful (the caller gets actionable information), and returning an empty list IS graceful (the caller doesn't need try/except). Neither interpretation is wrong — the requirement is ambiguous.

However: checking the plan's `considerations` field reveals "parse_input() is called in a batch loop over 1000+ items." This context favors empty-list return — raising in a batch loop forces the caller to wrap every call in try/except or lose the entire batch on one bad item.

**Ruling: Requirement ambiguity** — but with a recommendation.
- **Who must act:** Orchestrator/user to clarify the requirement.
- **Recommendation:** Empty list with logged warning. The batch-loop context makes exceptions costly for callers. But this is an architectural decision that affects the API contract for all consumers — it should be made explicitly, not by the auditor.
- **What changes after clarification:** If empty-list: coder modifies implementation, tester's test stands. If ValueError: tester revises test, coder's implementation stands.

### Ruling Output Template

```markdown
## Arbitration Ruling

### Dispute
[Brief description of the disagreement]

### Evidence Examined
- Plan requirement: [requirement text, source]
- Implementation: [file:line, what it does]
- Test: [file:line, what it verifies]
- Tester position: [summary]
- Coder position: [summary]

### Analysis
[Reasoning — cite specific evidence for each point]

### Ruling: [Coder is wrong / Tester is wrong / Requirement ambiguity / Escalate]

### Required Action
- **Who must act**: [coder / tester / orchestrator]
- **What to do**: [specific changes with file:line]
- **Why**: [one-sentence justification]

### Recommendation (for Requirement Ambiguity and Escalate rulings)
- **Auditor's recommendation**: [what the auditor believes the requirement SHOULD mean, and why]
- **Impact of each interpretation**: [what changes if interpretation A vs B is chosen]
(The final decision is the orchestrator's/user's, but the auditor's recommendation provides informed context.)
```
