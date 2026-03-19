# Code Review Anti-Patterns

Common auditor failure modes. Each is a pattern observed in LLM agent reviews that produces incorrect or useless verdicts.

## Anti-Pattern 1: Trusting the Report

The most common failure. The auditor reads the coder's summary and verifies claims against the summary instead of against the code.

**WRONG:**
```
Coder report: "Implemented detection pipeline with confidence thresholds.
All 5 test cases pass. Edge cases for low-confidence detections handled."

Auditor review: "Coder reports all tests pass and edge cases handled.
Implementation appears complete. Verdict: Pass."
```

**RIGHT:**
```
Auditor reads src/detection/pipeline.py:
- Lines 45-67: detect() function — traces logic for confidence thresholds
- Line 52: threshold hardcoded to 0.5, but plan says configurable ← Finding

Auditor reads tests/test_detection.py:
- 5 test functions present, but test_low_confidence (line 78) asserts only
  `result is not None` — fraudulent test ← Finding
- test_edge_empty_frame (line 92) not present despite plan requirement ← Finding

Auditor reads test output:
- 4 tests pass, 1 skipped (test_batch_detection marked @skip) ← Finding

Verdict: Fix (1 Critical: fraudulent test, 2 Important: missing configurable
threshold, missing edge case test, 1 Important: skipped test)
```

**Why this matters:** The coder's report said "all 5 pass" — actually 4 pass, 1 skipped. The report said "edge cases handled" — actually one edge case test is missing and another is fraudulent. Without reading code, the auditor would have passed this.

## Anti-Pattern 2: Sycophantic Approval

The auditor praises the implementation and produces a Pass verdict with no evidence, or pads critical findings with unnecessary praise.

**WRONG:**
```
"Great implementation! The code is well-structured and the tests are
comprehensive. Really nice work on the error handling. I noticed one
small thing — the input validation could be improved on line 45, but
overall excellent job. Verdict: Pass."
```

**RIGHT:**
```
Finding: Critical — Missing input validation
File: src/api/handler.py:45
What: User input passed directly to SQL query without sanitization
Why: SQL injection vulnerability — attacker can execute arbitrary queries
Fix: Add parameterized query: `cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))`

Verdict: Fix (1 Critical finding)
```

**Why this matters:** The sycophantic version buries a Critical security vulnerability in praise and downgrades the verdict to Pass. The factual version correctly identifies the severity and blocks progress until fixed.

## Anti-Pattern 3: Severity Deflation

The auditor identifies real issues but systematically downgrades severity to avoid blocking progress.

**WRONG:**
```
Minor: Function `handle_upload()` at line 34 is 95 lines with cognitive
complexity 22. Could be refactored for readability.

Minor: No error handling for the S3 upload call at line 78.
If S3 is down, the API returns a 500 with no useful message.
```

**RIGHT:**
```
Important: Function `handle_upload()` at line 34 — cognitive complexity 22
(threshold: 15). 95 lines with 4 levels of nesting. Extract guard clauses
and split by responsibility (validation, upload, response formatting).

Critical: No error handling for S3 upload at line 78. If boto3.upload_file()
raises, the request handler crashes with a generic 500. External calls in the
request hot path must have timeout + try/except + meaningful error response.
```

**Why this matters:** A missing error handler on an external call in a request path is not "minor." Every request hits this code — it's the difference between a graceful error response and a 500 crash. Severity must reflect impact, not auditor discomfort.

## Anti-Pattern 4: Rubber-Stamping Under Pressure

When the auditor is reviewing Task 5 of 5 and "just wants to finish," review quality drops to a surface scan.

**WRONG:**
```
Task 5/5 review:
"Implementation follows the plan. Tests pass. Code quality looks good.
Verdict: Pass."
(Review completed in 45 seconds. No file:line references. No requirements traced.)
```

**RIGHT:**
```
Task 5/5 review:
Requirements verified:
- R1 (CLI argument parsing): src/cli/parser.py:12-45. Traced: --input, --output,
  --format flags all handled. Missing: --verbose flag from plan requirement ← Finding
- R2 (output formatting): src/cli/formatter.py:23-67. JSON and CSV paths traced.
  CSV path has no header row despite plan requiring headers ← Finding
Tests: 6/6 pass, but test_csv_output (line 34) asserts only on file existence,
  not content ← Finding

Verdict: Fix (1 Important: missing --verbose, 1 Important: CSV headers,
1 Critical: fraudulent test on CSV output)
```

**Signals rubber-stamping is happening:**
- Review completed suspiciously fast (under 2 minutes for task-level)
- No file:line references in the verdict
- Verdict says "implementation looks correct" without citing which requirements were verified
- No findings at all — statistically unlikely for any non-trivial implementation

**Counter:** The checklist is not optional. Every task gets the full checklist regardless of sequence position. If you catch yourself skipping steps because "this is the last one," that's the red flag — go back and start the checklist from Step 1.

## Anti-Pattern 5: Reviewing the Report Instead of the Diff

The auditor focuses on what the coder says they changed instead of what actually changed. Misses additions, deletions, and modifications the coder didn't mention.

**WRONG:**
```
Coder says they modified src/etl/transform.py to add data normalization.
Auditor reviews only src/etl/transform.py.
```

**RIGHT:**
```
Auditor checks all files in target_files from the task spec.
Finds: coder also modified src/etl/schema.py (not in target_files —
added a new column to the data model) and added src/etl/helpers.py (not in plan).
Both are undocumented deviations → findings.
The schema change is Critical (behavioral scope violation affecting downstream tasks).
The helpers file is Important (unplanned code addition).
```

**Why this matters:** Coders may modify files outside their scope without realizing it's a deviation. A schema change in a data pipeline affects every downstream consumer — this can't be caught by reviewing only the file the coder mentioned.

## Anti-Pattern 6: Testing Theater

The auditor confirms "tests pass" without examining what the tests actually verify. A test suite can have 100% pass rate and 0% meaningful coverage.

**WRONG:**
```
"All 12 tests pass. Test coverage looks good. Verdict: Pass."
```

**RIGHT:**
```
12 tests pass. Examining test quality:
- 8 tests have meaningful behavioral assertions ✓
- 2 tests assert only on return type (`isinstance(result, dict)`) — weak assertions
- 1 test mocks the database AND the query AND the result — tests nothing real
- 1 test is `test_placeholder` with `assert True` — fraudulent

Finding: Important — 2 tests need stronger assertions (file:line for each)
Finding: Critical — 1 test is completely fraudulent (tests/test_db.py:45)
Finding: Important — 1 placeholder test should be implemented (tests/test_db.py:89)
```

## Anti-Pattern 7: Design Review Avoidance

The auditor skips API/Design review (Priority 1) and jumps straight to implementation details (Priority 2). This catches surface bugs but misses structural problems that are expensive to fix later.

**WRONG:**
```
Review focuses on: variable names, loop efficiency, string formatting,
missing docstrings. All findings are Minor or Important.
Misses: the module has three classes that should be one, with circular
dependencies and a god function that handles detection + tracking + rendering.
```

**RIGHT:**
```
Review starts with API/Design:
- DetectionTracker class (line 12) handles detection, tracking, AND rendering
  → Single Responsibility violation. Three concerns, three classes.
- DetectionTracker depends on Renderer which depends on DetectionTracker
  → Circular dependency. Break by extracting shared interface.
- process_all() (line 145) is 200 lines with 6 responsibilities
  → God function. Decompose into pipeline stages.

These are structural findings (Important/Critical) that must be resolved
before line-level review is meaningful.
```

**Why this matters:** 75% of code review defects are maintainability issues (IEEE study). Fixing a naming issue takes seconds; fixing a circular dependency after 5 more tasks build on it takes hours.

## Anti-Pattern 8: Accepting Skipped Tests

Tests marked `@skip`, `@pytest.mark.skip`, or `@unittest.skip` are not passing tests — they're deferred work. The auditor must not count them as coverage.

**WRONG:**
```
"All tests pass (2 skipped). Coverage looks complete."
```

**RIGHT:**
```
Finding: Important — 2 tests skipped
File: tests/test_pipeline.py:34 — @pytest.mark.skip("TODO: needs GPU")
File: tests/test_pipeline.py:67 — @pytest.mark.skip("flaky")

What: Skipped tests count as missing coverage, not passing coverage.
Why: "needs GPU" may indicate untestable design. "flaky" indicates unreliable test.
Fix: For GPU test — mock the GPU call or use CPU fallback in tests.
     For flaky test — fix root cause (likely timing or state pollution).
```
