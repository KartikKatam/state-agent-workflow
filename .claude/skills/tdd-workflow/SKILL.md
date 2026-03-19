# TDD Workflow

> **Purpose**: Defines the complete Test-Driven Development cycle for implementing plan chunks — from test design through quality gate.
> **Consumers**: chunk-coder
> **Schemas**: `schemas/session-log.schema.json` (tests_written, invariants_status, quality_gate sections)
> **Depends on**: `session-lifecycle` (for session log updates at each phase), `plan-adherence` (for scope checking)

## What You Learn From This Skill
- The full TDD cycle: write tests → run (expect fail) → implement → run (expect pass) → invariants → quality gate
- How to present each phase to the user before executing
- How to track test status in the session log
- Quality gate components and execution
- Iteration protocol when tests fail
- Learning signal recording for memory extraction
- Observability logging: writing structured logs during implementation and asserting on them in tests

## Contract
- Tests MUST be written before implementation
- Tests MUST fail before implementation (verify they test something real)
- The red phase (test failure) MUST be recorded in `phase_history` with failure count and error types as evidence
- All tests MUST pass before moving to invariant check
- The green phase (all pass) MUST be recorded in `phase_history` with pass count as evidence
- Quality gate MUST pass before presenting for review
- Session log MUST be updated at each phase transition — both `phase` field AND `phase_history` array
- User MUST approve before marking chunk complete
- Implementation code MUST include structured logging at key decision points
- Logging MUST be non-blocking — NEVER add latency to the system

---

## Interactive Implementation Flow

```
1. Load context and session log
2. Show user what you'll do (or resume point)
3. Write tests (show them, get feedback)
4. Run tests (expect failure)
5. Implement code (show progress)
6. Run tests (iterate until pass)
7. Run invariants
8. Run quality gate
9. Present for review
10. User approves → Update session log → Done
```

---

## Phase 1: Load Context

### 1.1 Load Plan and Chunk Spec

```bash
cat .claude/plans/{feature}-plan.json
```

Extract and **share with user**:

```
## Starting Chunk: {chunk.id} - {chunk.name}

**Purpose**: {chunk.purpose}
**Delivers**: "{chunk.delivers}"

**Files I'll touch**:
- {primary_file} (create)
- {other_file} (modify)

**What I'll implement**:
1. {task 1}
2. {task 2}

**Tests I'll write** (from plan):
- {test_1}
- {test_2}

**Out of scope** (I won't touch):
- {out_of_scope_1}

Does this look right? Any questions before I start?
```

**Update session log**: `phase: "context_loading"`, `resume_point: "Context loaded, ready for test design"`

**Source file reads**: Do NOT read production code or test files during context loading. Wait until Phase 2 (test design) to read the test file — and only the sections you need (factory functions, target class). Wait until Phase 2.4 (implementation) to read production code — and only the function you're modifying. The plan JSON contains enough context for Phase 1. See chunk-coder agent definition for the full tiered reading strategy.

### 1.2 Load Context Packets

```bash
cat .claude/context/_codebase.json
cat .claude/context/{feature}-context.json
```

Note patterns, types, and naming constraints.

### 1.3 Check Memories

Memories are injected by session-start hook. Scan them:

```
I see relevant patterns from past sessions:
- "{pattern_1}"
- "{pattern_2}"

I'll apply these.
```

---

## Phase 2: TDD Cycle

### 2.1 Design Tests

**Update session log**: `phase: "test_design"`

**Show the user your tests before writing:**

```
Here are the tests I'll write for {chunk.name}:

```python
class Test{MainFunction}:
    """Tests for {main_function}."""

    def test_{function}_basic_case(self, {fixtures}):
        """Verifies basic behavior."""
        # Arrange
        ...
        # Act
        result = {function}(...)
        # Assert
        assert result == expected

    def test_{function}_edge_case(self):
        """Handles edge case."""
        ...
```

Do these tests cover the right cases? Should I add any?
```

**Wait for feedback**, then write the tests.

### 2.2 Write Tests

Write test files based on the plan's `test_spec` and user feedback.

**Update session log**: `phase: "test_writing"`, add tests to `tests_written[]`

### 2.3 Run Tests (Expect Failure — RED PHASE)

```bash
pytest {test_file} -v
```

**This step is CRITICAL for TDD integrity.** The tests MUST fail before you implement. If tests pass without implementation, they're not testing real behavior.

**Update session log**:
- `phase: "red_verified"`
- Append to `phase_history`:
  ```json
  {
    "phase": "red_verified",
    "timestamp": "{ISO}",
    "details": "{N} tests failed: {failure summary — e.g., '7 ModuleNotFoundError, 4 ImportError'}"
  }
  ```
- Update test statuses in `tests_written[]`

**Report:**
```
Tests written. As expected, they fail:

FAILED test_function_basic - ModuleNotFoundError
FAILED test_function_edge - ModuleNotFoundError

{N}/{M} tests failed (red phase verified).
Now I'll implement the code.
```

### 2.4 Implement Code

**Update session log**: `phase: "implementation"`, `resume_point: "Implementing {function}"`

**Show your approach:**

```
Implementing {function} in {file}.

My approach:
1. {step 1}
2. {step 2}
3. {step 3}

Here's the code:
```

```python
def {function}(...) -> ...:
    """..."""
    ...
```

**Check with user:**
```
Does this approach look right?
```

### 2.5 Scope Check

**Before modifying any file:**
```
Is {filename} in scope.touched_files?
├── Yes → Proceed
└── No → Ask user before proceeding
```

If out of scope:
```
I need to modify {file} which isn't in the plan's scope.
Reason: {why needed}

Options:
1. Proceed and document as deviation
2. Find another way
3. Stop and discuss

What would you prefer?
```

If proceeding, add to `deviations[]` in session log.

### 2.6 Run Tests (Iterate)

**Update session log**: `phase: "iteration"`, increment `timing.iteration_count`

```bash
pytest {test_file} -v
```

**If failing:**
```
{N} of {M} tests passing. Issues:

- {test_name}
   {error message}

   Fixing...
```

Track in session log: increment `failure_count` for failing tests.

**If passing (GREEN PHASE):**
```
All {N} tests passing!

- {test_1} passed
- {test_2} passed

Moving to invariants.
```

**Update session log**:
- `status: "implementation_done"`, `phase: "green_verified"`
- Append to `phase_history`:
  ```json
  {
    "phase": "green_verified",
    "timestamp": "{ISO}",
    "details": "{N}/{N} tests passing"
  }
  ```

### 2.7 Record Learning Signals

If a test failed 2+ times, add a learning signal to the session log:

```json
{
  "id": "sig-001",
  "type": "test_fix_cycle",
  "context": {
    "test_name": "{test}",
    "failure_count": 2,
    "failure_reason": "{what went wrong}",
    "resolution": "{how it was fixed}"
  },
  "timestamp": "{ISO}",
  "extracted": false
}
```

Learning signal types:
- `test_fix_cycle` — test failed multiple times before passing
- `user_correction` — user corrected the agent's approach
- `pattern_discovery` — agent discovered a useful pattern
- `api_gotcha` — unexpected library/API behavior

---

## Observability Logging During Implementation

### Principle: Log for Testability

When implementing code, proactively add structured logging at key decision points. This serves two purposes:
1. **Tests can assert on log output** to verify internal behavior not visible in return values
2. **Production debugging** — logs provide traceable pipeline execution without needing a debugger

### Zero-Latency Requirement

Logging MUST NOT add latency to the system. In latency-sensitive systems, every millisecond matters.

**Rules:**
- Use Python's `logging` module (stdlib) — it's fast and well-integrated with pytest's `caplog`
- Log at appropriate levels: `DEBUG` for verbose internals, `INFO` for key events, `WARNING` for recoverable issues, `ERROR` for failures
- NEVER do expensive computation inside log calls — use lazy formatting: `logger.debug("processed %d candidates", len(candidates))` not f-strings
- NEVER do I/O (file writes, network calls) synchronously inside log handlers in production code
- If the project uses `structlog`, follow its async-safe patterns

### What to Log

| Decision Point | Log Level | Example |
|---------------|-----------|---------|
| Filtering/selection decisions | DEBUG | `"rejected candidate id=%s: quality=%.2f < threshold=%.2f"` |
| Stage entry/exit with timing | INFO | `"entering stage: batch_selection"`, `"exiting stage: batch_selection duration_ms=%.1f"` |
| Configuration values used | DEBUG | `"using batch_size=%d, min_quality=%.2f"` |
| Error recovery | WARNING | `"retrying operation after %s, attempt %d/%d"` |
| Pipeline routing decisions | DEBUG | `"routing to fast path: input_size=%d < threshold=%d"` |
| Resource usage | DEBUG | `"memory_mb=%.1f after processing batch"` |

### Logger Setup Pattern

```python
import logging

logger = logging.getLogger(__name__)

def select_batch(candidates: list[Candidate], config: BatchConfig) -> list[Candidate]:
    logger.debug("select_batch called with %d candidates, batch_size=%d", len(candidates), config.batch_size)

    filtered = [c for c in candidates if c.quality >= config.min_quality]
    logger.debug("filtered to %d candidates (removed %d below threshold=%.2f)",
                 len(filtered), len(candidates) - len(filtered), config.min_quality)

    selected = sorted(filtered, key=lambda c: c.quality, reverse=True)[:config.batch_size]
    logger.info("selected %d candidates from %d", len(selected), len(candidates))

    return selected
```

### Writing Log-Based Tests

When the plan's `test_spec` includes `log_assertions`, write tests using `caplog`:

```python
import logging

def test_select_batch_logs_filtering_decisions(caplog):
    """Verify filtering decisions are logged for debugging."""
    candidates = [make_candidate(quality=0.9), make_candidate(quality=0.3)]
    config = BatchConfig(batch_size=2, min_quality=0.5)

    with caplog.at_level(logging.DEBUG):
        result = select_batch(candidates, config)

    # Assert that internal decision-making is traceable
    assert "filtered to 1 candidates" in caplog.text
    assert "removed 1 below threshold=0.50" in caplog.text

def test_pipeline_stage_timing_logged(caplog):
    """Verify stage timing is logged for performance tracking."""
    with caplog.at_level(logging.INFO):
        pipeline.run(test_input)

    assert any("duration_ms" in r.message for r in caplog.records)
```

### Log Assertions in the Testing Pyramid

| Test Level | Log Assertion Purpose | Example |
|-----------|----------------------|---------|
| Pass A (chunk-wise) | Verify individual function logs decisions | "rejected candidate" logged |
| Pass B (integration) | Verify cross-component log trace | Stage entry/exit sequence |
| Pass C (system) | Verify complete pipeline trace, no errors | All stages logged, zero ERROR records |

### Temporary Debug Logging (Development/Testing Only)

Some insights require logging that IS too expensive for production — serializing large objects, dumping intermediate state, recording detailed timing breakdowns, or writing to files for offline analysis. These are valuable during development and testing but MUST be removed before productionization.

**Rules for temporary debug logs:**

1. **Mark clearly** — Every temporary log MUST have a comment: `# TEMP-LOG: <reason> — remove before production`
2. **Use a dedicated marker** — Wrap in a guard so they're easy to find and disable:
   ```python
   TEMP_DEBUG = True  # TEMP-LOG: Set False or remove before production

   def process_item(item):
       if TEMP_DEBUG:
           # TEMP-LOG: Dump intermediate state sizes for debugging data flow
           logger.debug("state sizes: input=%s, after_transform=%s", len(item.data), len(transformed))
           # TEMP-LOG: Write state to disk for offline analysis
           Path(f"/tmp/debug_output/item_{item.id}.json").write_text(json.dumps(item.to_dict()))
   ```
3. **Never in hot loops without the guard** — Temp logs inside tight loops MUST be behind the `TEMP_DEBUG` flag
4. **Ask user permission** — Before adding temp logs that write to disk or add noticeable latency, ask:
   ```
   I need to add temporary debug logging here to diagnose [issue].
   This will [write frames to disk / add ~5ms per frame / serialize the full state].

   These are marked as TEMP-LOG and will be removed before production.
   OK to add?
   ```
5. **Track in session log** — Record temp logs in `deviations[]` so the scribe and future chunks know they exist:
   ```json
   {
     "type": "temp_debug_logging",
     "files": ["producer/ops_batch.py:45", "producer/pipeline.py:120"],
     "reason": "Diagnosing shape mismatch in resize stage",
     "latency_impact": "~5ms per frame when TEMP_DEBUG=True",
     "remove_by": "Before merge to main"
   }
   ```
6. **Grep for cleanup** — Before marking a chunk complete, run:
   ```bash
   grep -rn "TEMP-LOG\|TEMP_DEBUG" --include="*.py"
   ```
   Report any remaining temp logs to the user. They decide: keep for now (testing phase) or remove now.

**Key principle**: Temp logs are a tool, not a smell. The smell is temp logs that aren't marked and survive to production. The `TEMP-LOG` comment and `TEMP_DEBUG` guard make them visible, disableable, and grep-findable.

---

## Phase 3: Verification

### 3.1 Run Invariants

**Update session log**: `phase: "invariant_check"`

For each invariant in the chunk's plan:

```bash
{invariant.verify.command}
```

**Report:**
```
Checking invariants:

inv-01-01: {description}
   `{command}` → exit 0

inv-01-02: {description}
   `{command}` → exit 0
```

Update `invariants_status` in session log.

### 3.2 Run Quality Gate

**Update session log**: `phase: "quality_gate"`

```bash
./scripts/gate.sh
```

Components (also runnable individually for quick iteration):
- `ruff format .` — Code formatting
- `ruff check . --fix` — Linting
- `pyright` — Type checking
- `pytest` — Tests

**Report:**
```
Quality gate:
- Format (ruff format) passed
- Lint (ruff check) passed
- Typecheck (pyright) passed
- Tests (pytest) passed

All checks passing!
```

Update `quality_gate` in session log.

**Update session log**: `status: "verified"`

---

## Phase 4: Review and Approval

### 4.1 Present for Review

**Update session log**: `phase: "review"`, `resume_point: "Awaiting user approval"`

```
## Chunk Complete: {chunk.id} - {chunk.name}

**Delivers**: {chunk.delivers}

### Files Modified
{list from session log}

### Tests ({count} total, all passing)
{list from session log}

### Invariants Verified
{list from session log}

### Quality Gate
All passing

### Deviations
{list or "None - matches plan exactly"}

---

**Ready for your review.**

You can:
- Run `git diff` to see all changes
- Ask questions
- Request changes

Say **"approved"** to complete this chunk.
```

### 4.2 Handle Feedback

**If user requests changes:**
```
User: "Add a docstring to X"

You: "Good catch. Adding..."
     [make change]
     "Done. Re-running gate..."
     [verify]
     "Updated. Anything else?"
```

Add to `user_interactions[]` in session log.

### 4.3 On Approval

When user says "approved":

**Update session log**:
```json
{
  "status": "approved",
  "phase": "complete",
  "resume_point": "Chunk approved, ready for commit",
  "meta": {
    "completed_at": "{ISO timestamp}"
  }
}
```

**Report:**
```
Chunk approved!

Session log updated. Lead will handle the commit.

Summary:
- Chunk: {chunk.id} complete
- Files: {count} modified
- Tests: {count} passing
- Ready for commit
```

Send task_complete to lead:
```python
SendMessage(to="lead", message={
  "type": "task_complete",
  "payload": {
    "status": "success",
    "output_files": [".claude/logs/{feature}-{chunk}-log.json"],
    "summary": "{chunk.id} complete. {count} tests passing, {count} invariants verified."
  }
})
```
