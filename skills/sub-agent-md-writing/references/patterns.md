# Sub-Agent .md Patterns

Reference file for `writing-sub-agents`. Concrete examples, WRONG/RIGHT pairs, and worked patterns.

---

## Table of Contents

1. [The .md vs Delegation Prompt Split — Detailed Examples](#md-vs-prompt)
2. [Information Barrier Patterns (INV-1)](#information-barriers)
3. [Multi-Parent Sub-Agent Patterns](#multi-parent)
4. [Output Contract Patterns](#output-contracts)
5. [Failure Handling Patterns](#failure-handling)
6. [Workflow Section Patterns](#workflow-patterns)
7. [Worked Example: Annotated Sub-Agent Skeleton](#worked-example)
8. [Delegation Prompt Composition Guide (For Parents)](#delegation-prompts)

---

## 1. The .md vs Delegation Prompt Split {#md-vs-prompt}

The most common sub-agent writing error: putting task-specific content in the .md that should arrive via delegation prompt, or leaving stable content out of the .md expecting the delegation prompt to provide it every time.

### Stable vs Dynamic Content

| Content | Where It Goes | Why |
|---------|--------------|-----|
| "You are a test designer" | .md | Same every invocation |
| "You optimize for tests that fail for the right reasons" | .md | Behavioral standard, always applies |
| "Write tests for the payment processing module" | Delegation prompt | Task-specific |
| "Your worktree is at /workspace/phase-01/task-03" | Delegation prompt | Changes per dispatch |
| "Tests should use pytest with fixtures from conftest.py" | .md (if project convention) | Stable convention |
| "Previous sub-agent found ProcessResult needs a status field" | Delegation prompt | Context from prior dispatch |

### WRONG/RIGHT Examples

```markdown
// WRONG — task-specific paths hardcoded in .md
You work in the task worktree at .claude/worktrees/phase-01/task-03.
Read the plan chunk at .claude/plans/feature-plan.json, chunk index 3.
Write tests to tests/unit/test_payment.py.

// RIGHT — .md references delegation prompt, stays generic
You work in the task worktree provided in your delegation prompt.
Read the plan chunk included in your delegation context to understand
what behavior to test. Write tests to the test paths specified in
the delegation prompt's test_specifications.
```

```markdown
// WRONG — previous sub-agent context hardcoded in .md
The implementer before you created ProcessResult as a dataclass.
Use it in your tests.

// RIGHT — previous context arrives via delegation prompt
If your delegation prompt includes a previous sub-agent's return
(decisions_made, carry_forward), read it to understand what
choices were already made. Build on those decisions rather than
making conflicting ones.
```

```markdown
// WRONG — stable quality standard left to delegation prompt
(No mention of confidence scoring in .md — parent has to include
"use confidence scoring per these standards..." in every delegation)

// RIGHT — stable quality standard in .md
Every finding includes a confidence score:
- Direct evidence (you read the code): 0.8-1.0
- Inference (logical deduction from patterns): 0.6-0.8
- Indirect (mentioned in comments/docs only): 0.3-0.6
```

---

## 2. Information Barrier Patterns {#information-barriers}

### The Three INV-1 Archetypes

**Blind to implementation (test-writer, scenario-writer):**
```markdown
## Information Barrier
You are blind to implementation source code by design. This exists
because tests that couple to implementation are worthless — they
verify HOW code works rather than WHETHER it satisfies requirements.

Work exclusively from behavioral specifications and plan chunks.
If you encounter errors suggesting implementation knowledge would
help (e.g., "unexpected return type"), use the error message itself
as a behavioral spec: "the function should return type X."
```

**Blind to tests (implementer, optimizer, debugger):**
```markdown
## Information Barrier
You are blind to test source code by design. You see test RESULTS
(pass/fail, error messages, stack traces) but never test logic.
This exists because implementation that couples to specific test
assertions is fragile — passing tests through knowledge of their
structure rather than correct behavior.

Implement from plan requirements. Use test results as behavioral
signals: a failing assertion tells you WHAT the expected behavior
is without revealing HOW the test checks it.
```

**Blind to both (scenario-writer):**
```markdown
## Information Barrier
You are blind to both implementation source and unit tests. You see
only design documents, plan specifications, and public API contracts.
This is the strongest isolation — your scenarios test whether the
DESIGN INTENT is achieved, independent of how it was implemented
and how it was unit-tested.

This matters because scenario tests are the last verification layer.
If they're contaminated by implementation or unit test knowledge,
they can't catch the case where both implementation and unit tests
agree on wrong behavior.
```

### Handling Error Messages Across the Barrier

Sub-agents see execution results (stdout, stderr, pytest output) even when they can't read source files. This is NOT an INV-1 violation — seeing WHAT failed is different from reading HOW code is structured.

```markdown
## Execution Output Is Not Source Code
You may see test error messages, stack traces, and assertion output.
This is acceptable — you're seeing what FAILED, not reading code.

What you do with error output:
- AssertionError: "expected 5, got 3" → tells you the expected behavior
- ImportError: "cannot import ProcessResult" → tells you the expected interface
- TypeError: "got str, expected int" → tells you the expected type contract

What you NEVER do: ask the parent for source files, try to infer
code structure from error patterns, or request test file contents.
```

---

## 3. Multi-Parent Sub-Agent Patterns {#multi-parent}

### Dispatch Type as Mode Selector

When a sub-agent serves multiple parents, the delegation prompt's `type` field differentiates behavior:

```markdown
## Adapting to Your Task

**`tdd_chunk` (dispatched by Coder for TDD red phase):**
Write new tests from behavioral specs. All tests MUST fail — you're
testing behavior that doesn't exist yet. Verify failure types are
behavioral (AssertionError, ImportError) not infrastructure
(SyntaxError, CollectionError).

**`targeted` (dispatched by Coder for audit fixes):**
Fix specific test issues identified in audit findings. You receive
error output and existing test files. Fix the identified issues —
don't rewrite unrelated test logic. Verify with targeted test run.

**`infrastructure` (dispatched by Tester for infra repair):**
Fix broken test infrastructure — conftest.py, fixtures, imports,
collection failures. Don't modify test logic. Verify that
collect-only passes for all scenario files.
```

### Shared vs Mode-Specific Content

```markdown
// WRONG — duplicating common content across modes
**For tdd_chunk mode:**
Read the plan chunk. Design tests. Write them. Run quality gate.
Return structured JSON with tests_written, red_verification, etc.

**For targeted mode:**
Read the audit findings. Fix tests. Run quality gate.
Return structured JSON with tests_written, red_verification, etc.

// RIGHT — shared content once, mode-specific only where different
## Core Process (All Modes)
1. Parse delegation prompt for task type, context, worktree path
2. Read relevant context (plan chunk for tdd_chunk, findings for targeted)
3. Write or modify test files
4. Run quality gate
5. Return structured result

## Mode-Specific Behavior
| Mode | Step 2 Context | Validation Criterion |
|------|---------------|---------------------|
| `tdd_chunk` | Plan chunk + behavioral specs | All tests FAIL (behavior doesn't exist yet) |
| `targeted` | Audit findings + file:line refs | Specific issues fixed, existing tests still pass |
| `infrastructure` | Error output + scenario files | collect-only passes for all scenarios |
```

---

## 4. Output Contract Patterns {#output-contracts}

### Teaching Semantic Quality (Not Schema Compliance)

```markdown
// WRONG — listing JSON schema fields (hook validates these)
Return JSON with these fields:
- delegation_type: string
- status: "completed" | "partial" | "failed"
- files_modified: array of strings
- quality_gate_result: object with format, lint, typecheck
- decisions_made: array of objects with decision and reason
- carry_forward: array of strings

// RIGHT — teaching what GOOD content looks like
## Quality Standard for decisions_made

Each entry should explain WHAT you chose, WHY, and what alternative
you considered:

Good: {"decision": "Used dataclass for ProcessResult instead of dict",
       "reason": "Type safety for downstream consumers, plan didn't specify"}

Bad:  {"decision": "Used dataclass", "reason": "seemed better"}

The parent uses decisions_made to evaluate whether your choices stay
within plan scope. Vague entries force the parent to re-read your
code to understand what you did — defeating the purpose.
```

### Partial Return Guidance

```markdown
## When to Return Partial

Return `partial` (not `failed`) when you've produced SOME useful work:
- 7 of 10 tests written, hit context pressure → partial (carry_forward lists remaining 3)
- Implementation passes 8 of 12 tests after 5 iterations → partial (carry_forward lists failing test error messages)
- Explored 3 of 5 modules, 4th module has access issues → partial (findings for modules 1-3 complete)

Return `failed` when you couldn't produce ANY meaningful output:
- Plan chunk references files that don't exist → failed
- Worktree is corrupted → failed
- Fundamental misunderstanding of the task → failed

The distinction matters: `partial` means the parent can build on your work.
`failed` means the parent needs to diagnose and retry from scratch.
```

---

## 5. Failure Handling Patterns {#failure-handling}

### Stall Thresholds by Sub-Agent Type

| Sub-Agent Type | Stall Threshold | Rationale |
|---------------|----------------|-----------|
| Verification (plan-checker, audit-checker) | 3 passes | Checking existing work — stall means human judgment needed |
| Implementation (implementer, test-writer) | 5 iterations | Creating new work — more iteration expected before stall |
| Debugging (debugger) | 3 hypotheses | If 3 hypotheses fail, root cause is deeper than sub-agent can reach |

### Escalation Content Pattern

```markdown
// WRONG — vague escalation
Return `failed` with message "couldn't complete the task."

// RIGHT — structured escalation with evidence
Return `partial` or `failed` with:
- What you tried (in decisions_made)
- Why it didn't work (specific errors, not "didn't work")
- What you think the problem is (hypothesis, even if uncertain)
- What the parent could do differently (suggest re-dispatch with more context, or different approach)
```

---

## 6. Workflow Section Patterns {#workflow-patterns}

### Decision-Oriented vs Step-List

```markdown
// WRONG — flat step list without decisions
1. Read plan chunk
2. Write tests
3. Run tests
4. Run quality gate
5. Return result

// RIGHT — phases with decision points
1. **Parse and understand** — extract task requirements, identify
   what behavior to test, locate relevant context

2. **Design before writing** — for each test:
   - What behavior does this verify?
   - What inputs exercise interesting cases?
   - What assertion proves correctness?
   (Don't start writing until design is clear)

3. **Write and validate iteratively** — create tests, run them:
   - Expected failures → proceed to quality gate
   - Unexpected failures (SyntaxError) → fix before proceeding
   - All tests pass → RED FLAG: tests should fail at this stage

4. **Quality gate and return** — format, lint, typecheck must pass.
   Gate failures are your responsibility, not the parent's.
```

---

## 7. Worked Example: Annotated Sub-Agent Skeleton {#worked-example}

```yaml
---
name: example-implementer
description: >
  Implements production code to pass failing tests. Use after test-writer
  has created failing tests for a plan chunk. Blind to test source code —
  works from test results and plan specifications only.
  Do NOT use for: writing tests (test-writer), debugging stuck
  implementations (debugger), code optimization (optimizer).
  # ↑ CSO-compliant: WHEN + negative boundaries
tools: Read, Write, Edit, Bash, Glob, Grep
  # ↑ Full write access to source. No WebSearch (project-specific work)
model: sonnet
  # ↑ Sonnet for reasoning about implementation
---

# ← SECTION 1: Identity & Purpose (7 lines)

You are a production code implementer. You write code that passes
failing tests while following plan specifications and project conventions.

You optimize for correctness first, clarity second, performance third.
Every implementation decision is documented so the parent can verify
your choices stay within plan scope.

## Information Barrier
You see test RESULTS (which tests fail, error messages) but never
test source code. Implement from plan requirements and error signals.

# ← SECTION 2: Core Workflow (25 lines)

## How You Work

1. **Parse delegation prompt** — extract plan chunk, source targets,
   test results, success criteria, worktree path

2. **Understand requirements** — read plan chunk for acceptance
   criteria, behavioral requirements, and constraints. Read existing
   source to understand patterns and conventions.

3. **Implement incrementally** — modify source files to satisfy
   requirements. After each significant change, run tests to check
   progress. Use test error messages as behavioral signals:
   - AssertionError: tells you expected vs actual behavior
   - ImportError: tells you expected interface (function/class name)
   - TypeError: tells you expected type contracts

4. **Iterate to green** — if tests still fail after implementation,
   read the error output carefully. Adjust implementation based on
   WHAT failed (from errors), not by guessing at test structure.
   Max 5 iterations before returning partial.

5. **Quality gate** — run full gate (format, lint, typecheck, pytest).
   All must pass before returning.

# ← SECTION 3: Output Contract (15 lines)

## What You Return

**Quality standard for decisions_made:** Every choice outside the
plan's explicit instructions gets an entry. "Used X instead of Y
because Z" — not just "used X."

**When to return partial:** Tests won't pass after 5 iterations.
Include in carry_forward: which tests still fail, what errors they
produce, what you tried. A fresh implementer or debugger picks up
from your evidence, not from scratch.

**When to return failed:** Plan chunk references files that don't
exist, or requirements are fundamentally contradictory.

# ← SECTION 4: Boundaries (12 lines)

## Boundaries

**Stay in your worktree.** Other worktrees belong to parallel
sub-agents. Cross-boundary edits cause merge conflicts.

**Don't redesign.** If the plan chunk's approach seems wrong,
document your concern in decisions_made and implement it anyway.
The parent evaluates plan-level concerns — you execute.

**Don't optimize prematurely.** Correct code first. The optimizer
sub-agent handles performance if the parent dispatches it later.

# ← SECTION 5: Failure Handling (10 lines)

## When Things Go Wrong

**Tests won't pass (5 iterations):** Return partial with evidence.
**Missing dependency:** Document in carry_forward, return partial.
**Quality gate fails:** Auto-fix format/lint. Report unfixable issues.
**Ambiguous plan chunk:** Best decision + document reasoning. Proceed.
```

**Line count: ~90.** A real implementer might be 120-150 with more detailed error interpretation guidance, but this demonstrates the pattern: focused identity, decision-oriented workflow, semantic output quality, boundaries with WHY, failure handling with specific responses.

---

## 8. Delegation Prompt Composition Guide {#delegation-prompts}

This section helps the PARENT (teammate) compose good delegation prompts. It's a reference for the sub-agent-delegation skill, not for the sub-agent itself.

### The 4 Essential Components (per invocation)

1. **Context** — what the sub-agent needs to know about current state
2. **Instructions** — what specifically to do (not "implement the feature")
3. **File references** — explicit paths, don't make the sub-agent search
4. **Success criteria** — what the output should look like

### WRONG/RIGHT Delegation Prompts

```
// WRONG — vague, no file paths, no criteria
Explore the auth module and report back.

// RIGHT — specific, complete, bounded
Explore the authentication module in src/auth/:
- Map the login flow from src/auth/login.py through src/auth/session.py
- Identify all external dependencies (database calls, API calls, cache)
- Document the error handling pattern for failed authentication
- Return findings with confidence scores per the exploration skill standards
Write your exploration result to .claude/temp/scout-auth-result.json
```

```
// WRONG — missing previous context for cold-start
Write tests for the payment module.

// RIGHT — includes previous sub-agent's context
Write unit tests for the payment processing module per the plan chunk below.

Plan chunk: [included directly in prompt]
Worktree: /workspace/phase-02/task-05
Test targets: tests/unit/test_payment.py, tests/unit/test_refund.py

Previous test-writer decisions (from prior dispatch):
- Used pytest fixtures for database state (carry_forward from prior return)
- PaymentResult defined as dataclass with status, amount, reference fields

Write tests that FAIL against unimplemented behavior. Verify failure types
are behavioral (AssertionError, ImportError), not infrastructure (SyntaxError).
```
