---
name: code-review
version: 1-0-0
triggers:
  - agent_role: auditor
    conditions: when reviewing coder implementation, conducting phase-end audit, arbitrating tester-coder disputes, or performing final production readiness assessment
description: >
  Use when reviewing implementation work at any scope: task-level code review,
  phase-level audit, tester-coder arbitration, or final production readiness
  assessment. Activates for: any review_level 2-3 task completion, phase
  completion, tester-coder dispute resolution, feature completion audit, or when
  asked to review code quality. Do NOT use for: TDD cycle mechanics
  (infrastructure), writing tests (test-design skill), writing production code
  (code-design skill), task execution (task-execution skill), or code style
  and formatting issues (defer to linters).
---

# Code Review

## Core Principle

**Do not trust the report. Verify everything independently.** The implementer's report may be incomplete, inaccurate, or optimistic. LLM agents are optimistic reporters — they claim things work when they don't, claim tests pass when they haven't run them, claim requirements are met when they've missed edge cases. Every claim must be verified by reading actual code, not by reading summaries.

**Violating the letter of the rules is violating the spirit of the rules.**

## When Not to Use

- TDD cycle mechanics (that's infrastructure)
- Writing tests (test-design skill)
- Writing production code (code-design skill)
- Code style and formatting issues (defer to linters)

<HARD-GATE>
Do NOT produce any review verdict (pass, fix, or scrap) until you have:
1. Read the ACTUAL source code files — not the coder's report or summary
2. Read the ACTUAL test files — not the coder's description of what tests do
3. Loaded the task or phase specification from the plan
4. Verified test results by reading command output — not the coder's claim of "all tests pass"

If ANY of these conditions are unmet, STOP and request the missing information.
If test output doesn't exist because tests were never run, that is itself a Critical finding — report it.
This applies to EVERY review regardless of perceived simplicity.
</HARD-GATE>

## Quick Reference

| Situation | Action |
|-----------|--------|
| Spawned for task review (review_level=3) | Load task spec + code + tests. Execute Step 1-6. |
| Spawned for phase-end audit (review_level=2) | Load all phase tasks + code + tests + coder logs. Execute Step 1-6 at phase scope. |
| Spawned for tester-coder arbitration | Load both perspectives + code + tests + plan requirement. Execute Arbitration Protocol. |
| Spawned for final audit | Load entire feature: all phases, all code, all tests, scenario suite results. Execute Step 1-6 at feature scope. |
| Coder's report says "all tests pass" | Ignore. Read actual test output. |
| Coder's report says "requirement X is met" | Ignore. Read actual code, verify requirement X line by line. |
| Code looks good at first glance | Not a verdict. Run the full checklist. |
| Unsure about a finding | Flag as Important with your uncertainty stated — don't suppress it. |
| Test passes but you suspect it's fraudulent | Investigate: does it test real behavior or mock existence? See fraudulent test detection. |
| Coder added functionality not in requirements | Flag as scope violation regardless of quality (YAGNI). |
| Issue is about code formatting or style | Skip — linters handle this. Focus on design and behavior. |
| Finding would require changing the plan itself | Out of scope for review. Report to orchestrator. |
| Coder disagrees with your Fix verdict | Evaluate their counter-evidence once. If valid, revise. If not, maintain with reasoning. |
| Review scope feels too large to assess thoroughly | Chunk it. Review in focused passes by concern (API, then implementation, then tests). |

## Core Workflow

### Step 1: Load Context

Load context based on review scope. Do NOT start reviewing until context is complete. **WHY load before reviewing:** If you start reading code before loading the plan, you'll evaluate what you see rather than what was required — anchoring on the implementation instead of the specification.

| Review Scope | Load |
|-------------|------|
| **Task-level** | Task spec from plan + target files (code) + test files + test output |
| **Phase-level** | All phase task specs + all code + all tests + coder decision logs + session logs (errors, scraps, retries) |
| **Final audit** | All phases + full codebase context + scenario test suite results + all session logs |

For phase-level and final audit, use PTC for targeted analysis: metrics (cognitive complexity, coverage), blast radius (what else does this code touch), and security scanning. Direct Read for code comprehension.

Step 1 produces: full context loaded. Step 2 determines what to verify.

### Step 2: Identify Verification Targets

Before reviewing code, build a verification checklist from the plan. **WHY build the checklist before reading code:** This prevents confirmation bias. If you read code first, you verify what you see — not what was required. Requirements you never look for are requirements you never find missing.

1. **List every requirement** from the task/phase spec — each becomes a verification target
2. **List every limitation** — each becomes a negative verification ("code does NOT do X")
3. **List every test_expectation** — each must have a corresponding test
4. **List review_focus items** — the strategist flagged these for extra scrutiny
5. **Note deviations** documented by the coder — verify each is justified and properly scoped

Step 2 produces: a concrete checklist of what to verify. Step 3 verifies independently.

### Step 3: Independent Verification

Verify each target from Step 2 by reading actual code. **Reading order for unfamiliar code:** Start with public API signatures to understand what the code promises (function names, parameters, return types). Then trace the main path to verify it delivers. Then check error paths and edge cases. This order matches how the code will be used — callers see the API first, then hit the happy path, then encounter errors.

For each requirement:

1. **Find the implementation** — locate the code that implements this requirement
2. **Trace the logic** — follow the code path end-to-end, not just the function signature:
   - **Happy path first**: trace the main success path from entry to return. Does it produce the right output for valid input?
   - **Error path next**: what happens when input is invalid, when an external call fails, when data is empty or null? Follow each conditional branch — does the `else` case handle what it should?
   - **Check implicit assumptions**: does the code assume a list is non-empty, a value is positive, a file exists? Are those assumptions validated or will they cause silent failures?
   - **Boundary conditions**: what happens at zero, one, max values? What about empty strings, empty collections, None?
3. **Check the test** — does a test verify this specific requirement? Does the test assert on real behavior?
4. **Check the test output** — did the test actually run and pass? Read the output.
5. **Mark the target** — Verified / Issue Found / Cannot Verify

**"Cannot Verify" is a finding, not a skip.** If you can't verify a requirement from the available code and tests, that's a gap — report it.

Step 3 produces: annotated verification checklist. Step 4 assesses code quality.

### Step 4: Assess Against Review Checklists

Review the code against the priority-ordered checklist. Spend most time on the top of the pyramid — design issues are hardest to fix later.

**Priority 1 — API/Design (highest impact — spend the most time here):**

75% of code review defects are maintainability issues, not functional bugs (IEEE). Design problems caught now save hours of rework; design problems caught after 5 more tasks build on them cost days.

- Public interfaces match what the plan specified
- No unplanned API surface (YAGNI — flag additions not in requirements)
- Separation of concerns — each function/class has one responsibility
- Dependencies are injected, not hard-wired
- Abstraction boundaries match the plan's architectural intent

**HOW to evaluate design:** Compare the plan's component decomposition to the actual code structure. The plan says "detection module and tracking module" — does the code have two modules or one `DetectionTracker` class handling both? Count the responsibilities of each class: if you need "and" to describe what it does, it has too many. Check import graphs: if A imports B and B imports A, that's a circular dependency regardless of how clean the code is. If a function orchestrates (calls other functions) AND operates (does the work), it's mixing abstraction levels.

**Priority 2 — Implementation:**
- Correctness — logic handles the stated requirements
- Error handling — external calls have timeouts, failures are handled explicitly
- Security — input validated at trust boundaries, no injection vectors
- Cognitive complexity — functions under 15, nesting depth under 3
- Edge cases from `considerations` field addressed

**Priority 3 — Tests:**
- Tests verify real behavior, not mock existence (see Fraudulent Test Detection below)
- Tests have meaningful assertions — not `assert True` or `assert result is not None`
- Edge cases from `test_expectations` are covered
- Test descriptions were logged before implementation (code-design Step 1)

**Priority 4 — Plan Adherence:**
- All `requirements` implemented — check each one
- All `limitations` respected — verify no violations
- No scope creep — no code beyond what's required
- Deviations documented with type, reason, impact
- `review_focus` items addressed proactively

**Priority 5 — Code Style:**
- Defer entirely to linters/formatters. Do NOT waste review time on style.

**Scope-specific thinking:** Different review scopes require different mindsets — don't just run the task-level checklist N times for a phase review:
- **Task-level:** Is this single implementation correct and well-built?
- **Phase-level:** Do the pieces fit together? Are conventions consistent across tasks? Did different coders make conflicting assumptions? Look for cross-cutting concerns task-level reviews miss.
- **Final audit:** Is this production-ready? What are the failure modes? What happens when each dependency is down?

For detailed checklists with examples, read `references/review-checklists.md`.

Step 4 produces: findings list with severity. Step 5 produces the verdict.

### Step 5: Produce Verdict

Classify each finding by severity, then determine the overall verdict.

**Severity Classification:**

| Severity | Criteria | Review Action |
|----------|----------|---------------|
| **Critical** | Breaks functionality, violates security, contradicts requirements, or makes code untestable. Cannot ship. | Must be fixed before proceeding. |
| **Important** | Affects maintainability, misses edge cases, has design issues, or creates tech debt. Should not ship. | Should be fixed. Auditor includes specific guidance. |
| **Minor** | Advisory improvements. Nice to have. Does not block. | Noted for coder's awareness. Does not affect verdict. |

For severity classification examples and edge cases, read `references/severity-framework.md`.

**Verdict Rules:**

| Condition | Verdict |
|-----------|---------|
| Zero Critical, zero Important findings | **Pass** — task/phase proceeds |
| Zero Critical, one or more Important findings | **Fix** — coder addresses Important items |
| One or more Critical findings, but approach is sound | **Fix** — coder addresses all Critical + Important items |
| Critical findings indicate fundamental approach is wrong | **Scrap** — delete implementation, prepare handoff |

**Verdict Output Format:**

Every finding must include:
- **File and line**: `src/detection/yolo_wrapper.py:45`
- **What's wrong**: Factual description of the issue
- **Why it matters**: Consequence if left unfixed
- **How to fix**: Specific guidance (not vague "improve this")
- **Severity**: Critical / Important / Minor

**Pass verdict** still requires evidence — not just "looks good":

```
Verdict: Pass

Requirements verified:
- R1 (configurable threshold): Implemented at src/detection/pipeline.py:45,
  threshold read from config.yaml. Verified with test_configurable_threshold (PASS).
- R2 (batch processing): Implemented at src/detection/pipeline.py:78-95,
  processes frames in configurable batch sizes. Verified with test_batch_detect (PASS).
- R3 (empty input handling): Guard clause at src/detection/pipeline.py:42
  returns empty list. Verified with test_empty_frame (PASS).

Scope: All modifications within target_files. No undocumented deviations.
Tests: 8/8 pass, 0 skipped, 0 fraudulent. All test_expectations covered.
Minor: Variable `d` at line 23 could be more descriptive. Advisory only.
```

Step 5 produces: verdict with findings. Step 6 handles follow-up.

### Step 6: Handle Follow-up

| Verdict | Follow-up |
|---------|-----------|
| **Pass** | Report to orchestrator. Task/phase proceeds. |
| **Fix** | Send findings to coder. Coder applies ONLY requested fixes, re-submits. Re-review ONLY the changed code — don't re-review the entire codebase. If coder pushes back with technical evidence, evaluate once: if valid, revise finding; if not, maintain with reasoning. |
| **Scrap** | Document: what's fundamentally wrong, why patching fails, what a fresh approach should consider. Send to orchestrator for handoff. Coder writes the scrap handoff per handoff-protocol. Do NOT engage in extended discussion about salvageability. |

**Fix cycle discipline:**
- Re-review is scoped to the fix, not the whole codebase
- If a fix introduces NEW issues, those are new findings — don't escalate to scrap unless the pattern indicates fundamental problems
- Maximum 2 fix cycles for the same finding. If not resolved after 2 attempts, escalate to orchestrator.
- Coder pushes back once with evidence → evaluate. If disagreement persists → maintain verdict. No extended debate.

**Time budget guidance:** A thorough task-level review takes 5-15 minutes and covers 3-10 files. A phase-level review takes 15-45 minutes. A final audit takes 30-90 minutes. Under 2 minutes (task) or under 10 minutes (phase) is a red flag that steps were skipped.

## Fraudulent Test Detection

Tests that pass trivially prove nothing. Before accepting any test as valid evidence:

| Red Flag | What It Means |
|----------|---------------|
| Test asserts on mock existence, not behavior | Testing that a mock was called, not that real logic works |
| Test has no meaningful assertion | `assert True`, `assert result is not None`, `assert len(items) > 0` without checking content |
| Test mocks the exact thing being tested | Circular — testing the mock, not the code |
| Test tests implementation details | Asserts on internal state or method calls, not observable behavior |
| Test would pass with empty implementation | The assertion is too weak to catch real failures |
| Test name doesn't match what it tests | Misleading — test may be cargo-culted from another test |

**Gate question before accepting any test:** "If I replaced the implementation with `pass` or `return None`, would this test still pass?" If yes — the test is fraudulent.

## Anti-Sycophancy Protocol

Auditor communication must be factual and direct. No praise padding, no gratitude theater, no softening.

**Banned phrases — using ANY of these in a review indicates process failure:**
- "Great work!", "Excellent!", "Nice job!", "Well done!"
- "You're absolutely right!", "Great point!"
- "Thanks for catching that!", "Good catch!"
- Any gratitude or praise expression in a review context

**Required communication pattern:**
- State findings factually: "Line 45: missing null check on `user_id`. Add validation before dict lookup."
- If coder disagrees and is correct: "I was incorrect — X works because Y. Removing this finding." No apology ceremony.
- If coder disagrees and is wrong: "X fails because Y. Evidence: [cite code/test/plan]. Finding stands."
- Never pad negative findings with positive observations to soften them.

**YAGNI enforcement:**
- If coder added functionality not in requirements, flag as scope violation
- Quality of the extra code is irrelevant — scope discipline is the issue
- Check: grep codebase for actual usage of added APIs. If unused → flag for removal.

## Arbitration Protocol

When spawned to resolve a tester-coder disagreement, follow this protocol instead of the standard review workflow.

### Arbitration Input

You receive:
1. **Tester's report**: What failed, expected vs actual behavior, test code
2. **Coder's response**: Why their implementation is correct, counter-evidence
3. **Plan requirement**: The original specification both should satisfy

### Arbitration Process

1. **Read all evidence**: Tester report, coder response, actual code, actual tests, plan requirement
2. **Determine dispute type**:

| Signal | Dispute Type | Resolution |
|--------|-------------|------------|
| Dispute about verifiable facts (code does/doesn't do X) | Factual | Rule with cited evidence |
| Dispute about implementation quality/approach within plan scope | Bounded judgment | Rule with reasoned analysis |
| Dispute questions plan decisions (requirements, architecture, test strategy) | Architectural | Escalate to orchestrator/user |
| Resolution requires changes spanning multiple tasks | Cross-scope | Escalate to orchestrator |
| Cannot determine who is right from available evidence | Uncertain | Escalate with recommendation |

3. **Issue ruling** — one of four outcomes:

| Ruling | When | Consequence |
|--------|------|-------------|
| **Coder is wrong** | Implementation doesn't satisfy the requirement as specified | Coder receives Fix or Scrap verdict. Coder modifies implementation. |
| **Tester is wrong** | Test doesn't correctly verify the requirement. Implementation satisfies it. | Ruling sent to orchestrator → orchestrator directs tester to revise test. Coder's implementation stands unchanged. |
| **Requirement ambiguity** | Both interpreted the requirement differently; both interpretations are defensible | Escalate to orchestrator/user to clarify the requirement. Both may need changes after clarification. |
| **Escalate** | Architectural dispute, cross-scope impact, or insufficient evidence to determine | Escalate to orchestrator with auditor's analysis and recommendation. |

### Arbitration Output

The ruling must specify:
- **Who must act**: Coder, tester, or orchestrator
- **What they must do**: Specific changes with file:line references
- **Why**: Evidence and reasoning supporting the ruling
- **What the other party should know**: Context for the party who "lost"

Arbitration is single-pass. Neither party responds to the ruling — the orchestrator enforces it. If either party believes the ruling is wrong, they report to the orchestrator, who may override.

## Critical Rules

- **Read code, not reports.** Every verification must come from reading actual source files. Coder summaries are starting points for investigation, not evidence.
- **Verify test quality, not just test existence.** A test file existing does not mean the behavior is tested. Read the assertions. Apply the fraudulent test gate question.
- **No sycophancy.** Factual findings only. Praise in a review context is a process violation, not collegiality.
- **Severity determines verdict, not volume.** One Critical finding outweighs ten Minor findings. Classify precisely.
- **Scrap means the approach is wrong.** Not "many issues." A hundred Important findings is still a Fix, not a Scrap. Scrap is reserved for fundamentally wrong architecture, assumptions, or patterns that cannot be patched.
- **Style is automated.** Never comment on formatting, naming conventions, or style that linters handle. Focus on design and behavior — the top of the Code Review Pyramid.
- **Scope violations are Critical.** Code outside `target_files` modified without documented deviation = Critical finding. Functionality added beyond requirements = Important finding (YAGNI).
- **Evidence in every verdict.** Pass requires "verified at [file:line]." Fix requires "issue at [file:line], fix by [specific guidance]." Scrap requires "approach fails because [specific reason], fresh approach should [specific direction]."

## Anti-Rationalization

| Excuse | Reality |
|--------|---------|
| "The code looks clean, no need to trace every requirement" | Clean code can still miss requirements. Verify each one against the plan. |
| "Tests exist for this, so the behavior is covered" | Tests existing ≠ tests working. Read the assertions. Apply the fraudulent test gate. |
| "The coder's report is thorough, I can trust it" | The report IS the thing you're verifying. Trusting it defeats the purpose of review. |
| "This is a simple task, a quick look is sufficient" | Simple tasks have the most insidious bugs — the ones everyone assumes are handled. Full checklist. |
| "I'll flag this as Minor to avoid blocking progress" | Severity is based on impact, not convenience. If it breaks functionality, it's Critical. |
| "The coder already tested this manually" | Manual testing is not systematic. No record, can't re-run, proves nothing. |
| "Scrap seems too harsh, I'll say Fix with major changes" | If the approach is fundamentally wrong, Fix produces Frankenstein code. Scrap is the honest assessment. |
| "The coder will be frustrated if I scrap their work" | Your job is accurate assessment, not emotional management. A wrong Pass is worse than a correct Scrap. |
| "I don't want to seem adversarial" | Adversarial verification is the design intent. You are the last line of defense before code ships. |
| "This deviation is obviously fine, no need to flag it" | Undocumented deviations are plan violations. Flag it — let the coder justify it formally. |

**Red Flags — STOP and re-examine your review:**
- Producing a verdict without reading all source files
- Writing "looks good" or "well implemented" without specific evidence
- Skipping the test quality check because "tests pass"
- Downgrading severity to avoid blocking progress
- Accepting the coder's report as evidence instead of reading code
- Using any banned sycophantic phrase
- Completing a review in under 2 minutes (for task-level) or under 10 minutes (for phase-level)
- Producing a Pass verdict with no file:line references

## References

For detailed per-level checklists with WRONG/RIGHT review examples, read `references/review-checklists.md`.

For common auditor failure modes with WRONG/RIGHT pairs, read `references/anti-patterns.md`.

For severity classification guide with examples and edge cases, read `references/severity-framework.md`.
