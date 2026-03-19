# Delegation Prompt Design for Sub-Agents

How to design delegation prompts that complement the sub-agent .md file. The .md defines the stable identity and reasoning patterns. The delegation prompt provides the task-specific context. This reference teaches how to compose effective delegation prompts and how the .md should be designed to receive them.

---

## Table of Contents

1. [The .md ↔ Delegation Prompt Split](#split)
2. [The 4-Component Delegation Prompt](#components)
3. [Context Sizing](#sizing)
4. [WRONG/RIGHT Delegation Prompt Examples](#examples)
5. [Designing the .md to Receive Delegation Prompts](#receiving)
6. [Per-Sub-Agent Delegation Templates](#templates)
7. [Resume vs Cold-Start Prompts](#resume)

---

## The .md ↔ Delegation Prompt Split {#split}

Two documents shape a sub-agent's behavior. Getting the split right is the most important design decision.

### The Split Rule

**Stable across invocations → .md file:**
- Agent identity and purpose
- Decision-making heuristics
- Output quality standards
- Boundary awareness with WHY
- Failure response patterns

**Changes every invocation → delegation prompt:**
- Specific task to perform
- File paths and worktree location
- Plan chunk or task specification
- Previous sub-agent's return (for resume/cold-start)
- Test results, error messages, audit findings
- Parent's decisions and constraints for this specific dispatch

### Common Split Mistakes

| Content | Wrong Location | Right Location | Why |
|---------|---------------|----------------|-----|
| "Work in worktree at /phase-01/task-03" | .md (hardcoded) | delegation prompt | Path changes every dispatch |
| "Your plan chunk is in feature-plan.json chunk 3" | .md (hardcoded) | delegation prompt | Different chunk each time |
| "When tests fail, iterate up to 5 times" | delegation prompt (task-specific) | .md (stable heuristic) | Same iteration limit every invocation |
| "You optimize for correctness" | delegation prompt | .md (stable identity) | Same optimization target every invocation |
| "The previous implementer tried X and it didn't work" | .md | delegation prompt (resume context) | Specific to this dispatch |
| "Red verification means all tests must fail" | delegation prompt | .md (stable process) | Same verification every TDD dispatch |

---

## The 4-Component Delegation Prompt {#components}

Every well-composed delegation prompt includes four components. This structure comes from the sub-agent-delegation skill — this reference expands on each component with sub-agent-specific guidance.

### Component 1: Comprehensive Context

What the sub-agent needs to know about the CURRENT STATE. Not its role (that's in the .md) — the specific situation.

```markdown
## Context for this dispatch:
- Task: Implement batch selection pipeline (task-03, phase-01)
- Worktree: /workspace/.claude/worktrees/phase-01/task-03
- Plan chunk: Phase 1, Task 3 from feature-plan.json
- Prior chunks completed: task-01 (data models), task-02 (config)
- Dependencies available: BatchCandidate class from task-01,
  ProducerConfig.batch_size from task-02
```

**Include:**
- Task identity (which task, which phase)
- Worktree path (where to work)
- What exists from prior work (dependencies, completed outputs)
- Any parent decisions affecting this task ("use dataclass not dict")

**Omit:**
- General instructions the .md already covers
- Agent identity reminders ("you are an implementer")
- Process instructions ("run tests after implementing")

### Component 2: Explicit Instructions

What SPECIFICALLY to do, beyond the general workflow in the .md.

```markdown
## Your task:
Implement the batch selection pipeline that:
1. Accepts a list of detection candidates from the tracking module
2. Scores each candidate using the scoring criteria from the plan
3. Selects the top-N candidates (N from ProducerConfig.batch_size)
4. Returns a BatchResult with selected candidates and scoring metadata

Key requirements from the plan:
- Scoring must be deterministic (same input → same output)
- Selection must handle ties by using candidate.timestamp as tiebreaker
- Empty candidate list should return empty BatchResult, not error
```

**Include:**
- Specific behavioral requirements from the plan chunk
- Edge cases explicitly called out in the plan
- Constraints the plan imposes ("deterministic scoring")
- Acceptance criteria

**Omit:**
- HOW to implement (the sub-agent decides approach)
- Code snippets (unless the plan specifies exact API signatures)

### Component 3: Relevant File References

Specific paths the sub-agent should read. Don't make it search — you already know where things are.

```markdown
## Files to read:
- Plan chunk: .claude/plans/batch-selection-plan.json → phases[0].tasks[2]
- Existing code: src/producer/models.py (BatchCandidate, BatchResult)
- Existing code: src/producer/config.py (ProducerConfig.batch_size)
- Context packet: .claude/context/producer-module-context.json

## Files to create/modify:
- src/producer/ops_batch.py (new file — batch selection pipeline)
- src/producer/__init__.py (add export for select_batch)
```

**Include:**
- Full paths to everything the sub-agent needs to read
- Full paths for files to create or modify
- Which section of a large file matters (e.g., "phases[0].tasks[2]")

**Omit:**
- Files the sub-agent shouldn't read (INV-1 restricted files)
- Every file in the project (only relevant ones)

### Component 4: Clear Success Criteria

What the parent will check when the sub-agent returns.

```markdown
## Success criteria:
- All tests pass (test results in delegation context below)
- Quality gate clean (format, lint, typecheck)
- select_batch function handles: normal case, empty list, ties
- No modifications to files outside src/producer/

## Test results (from test-writer phase):
- test_select_batch_normal: FAILED (ImportError: no module 'ops_batch')
- test_select_batch_empty: FAILED (ImportError: no module 'ops_batch')
- test_select_batch_ties: FAILED (ImportError: no module 'ops_batch')
- test_select_batch_scoring: FAILED (ImportError: no module 'ops_batch')

All tests fail with ImportError because ops_batch.py doesn't exist yet.
After implementation, all should pass.
```

**Include:**
- Measurable criteria ("all tests pass," "quality gate clean")
- Test results from prior phase (for implementer)
- Expected behavioral outcomes

**Omit:**
- Test source code (INV-1 for implementer)
- Vague criteria ("should work well")

---

## Context Sizing {#sizing}

From the sub-agent-delegation skill — two heuristics for right-sizing context:

### The 2-Read Test

If the sub-agent would need to Read the same file TWICE (once to understand context, once to do the work), include the relevant content directly in the delegation prompt. Saves a tool call.

### The 30% Test

Delegation prompt + sub-agent .md + loaded skills should not exceed 30% of the sub-agent's context window. This leaves 70% for the sub-agent's own work (reading files, tool calls, reasoning).

For Sonnet (200K window): delegation prompt should be < ~40K tokens when combined with .md + skills.
For Haiku (200K window): same math, but aim smaller since Haiku's effective reasoning capacity is lower.

**In practice:** Most delegation prompts are 500-2000 tokens. If your prompt exceeds 5000 tokens, you're probably including content the sub-agent should read from disk instead.

---

## WRONG/RIGHT Delegation Prompt Examples {#examples}

### The Vague Dispatch

```markdown
# WRONG
Explore the auth module.

# WHY IT FAILS: No specific questions to answer. No file paths.
# No output format guidance. The scout will do a generic sweep
# and return whatever it finds — probably not what you needed.

# RIGHT
Explore the authentication module at src/auth/.

Questions to answer:
1. What authentication strategies are implemented? (JWT, session, OAuth?)
2. How is the auth middleware applied to routes? (decorator, middleware chain?)
3. What is the session token lifecycle? (creation, validation, refresh, expiry)
4. Are there any implicit contracts between auth and the user model?

Depth: Implementation detail — I need to understand the auth flow
well enough to design a new SSO integration.

Exclusions: Don't explore src/auth/migrations/ (schema history, not relevant).

Known context: The user model is in src/models/user.py with fields
id, email, password_hash, last_login, is_active.
```

### The Context Dump

```markdown
# WRONG — includes everything the parent knows
Here is the full plan:
[500 lines of plan JSON]

Here is the full design doc:
[300 lines of design]

Here is all the context I have:
[200 lines of prior exploration results]

Now implement task 3.

# WHY IT FAILS: 1000 tokens of context, 90% irrelevant. The
# sub-agent wastes attention parsing irrelevant plan phases and
# design sections. It might even get confused by conflicting
# guidance from other phases.

# RIGHT — only relevant slice
## Plan chunk for task 3:
[20 lines — just this task's requirements, dependencies, and constraints]

## Relevant design excerpt:
[10 lines — the acceptance criteria this task satisfies]

## Relevant context:
[15 lines — specific functions and types this task depends on]

Implement task 3. Full plan at .claude/plans/feature-plan.json
if you need additional context.
```

### The Identity Reminder

```markdown
# WRONG — restates what the .md already says
You are an implementer. Your job is to write production code.
You should follow the code-design skill and optimize for correctness.
Never read test files. Return structured JSON with decisions_made.

Now implement the batch selector.

# WHY IT FAILS: Everything before "Now implement" is already in
# the sub-agent's .md and loaded skills. You're burning 60 tokens
# repeating what the sub-agent already knows.

# RIGHT — task-specific only
Implement the batch selection pipeline.
[context, instructions, files, criteria as above]
```

---

## Designing the .md to Receive Delegation Prompts {#receiving}

The .md should be designed to work with well-composed delegation prompts. This means:

### 1. Reference delegation prompt content, don't hardcode

```markdown
# IN THE .md — references delegation prompt dynamically
Read the plan chunk from your delegation prompt to understand
what behavior to implement.

Work in the worktree path provided in your delegation prompt.

# NOT THIS — hardcodes specific values
Read the plan chunk from .claude/plans/feature-plan.json chunk 3.
Work in /workspace/.claude/worktrees/phase-01/task-03.
```

### 2. Define what the delegation prompt should contain

The .md's workflow section implicitly defines what the delegation prompt must include by referencing it:

```markdown
## How You Work

1. **Parse delegation prompt** — Extract:
   - Task requirements and behavioral specs
   - Source file targets to create/modify
   - Test RESULTS (not test source — what tests expect)
   - Success criteria
   - Worktree path
   - Previous sub-agent's return (if this is a resume/fix dispatch)
```

This tells the parent: "Your delegation prompt must include these items." The parent uses this as a checklist when composing the prompt.

### 3. Handle missing context gracefully

```markdown
## If delegation prompt is incomplete

If your delegation prompt is missing critical context:
- Missing worktree path → cannot proceed, return `failed`
- Missing plan chunk → cannot proceed, return `failed`
- Missing test results → proceed without (first implementation attempt)
- Missing prior chunk decisions → check existing source for evidence,
  document assumptions in `decisions_made`
```

---

## Per-Sub-Agent Delegation Templates {#templates}

### codebase-scout Delegation Template

```markdown
Explore [partition description] in the codebase.

## Partition scope:
- Directories: [specific dirs]
- File patterns: [if applicable]
- Exclusions: [dirs/files to skip]

## Questions to answer:
1. [Specific question about this partition]
2. [Specific question]
3. [Specific question]

## Depth: [surface structure | implementation detail]

## Known context:
[Any prior knowledge the scout should account for,
e.g., "The Pipeline class was already explored — skip it,
focus on the Processor subclasses"]

## Model: [haiku | sonnet] — [why this model for this task]
```

### test-writer Delegation Template (TDD Red Phase)

```markdown
Write tests for [task description].

## Plan chunk:
[Task requirements, acceptance criteria, behavioral specs]

## Test specifications:
### Pass A (unit tests):
- [test_name]: [what behavior to verify]
- [test_name]: [what behavior to verify]
### Pass B (integration tests):
- [test_name]: [what behavior to verify]

## Design context:
[Relevant design document sections — what the user intended]

## Codebase context:
[Public API specs from context packets — function signatures,
types, existing patterns for test style]

## Worktree: [path]

## Success criteria:
- All tests fail (red verification — behavior doesn't exist yet)
- Failures are behavioral (AssertionError, ImportError), not
  infrastructure (SyntaxError, CollectionError)
- Quality gate clean on test files
```

### implementer Delegation Template (Green Phase)

```markdown
Implement [task description].

## Plan chunk:
[Task requirements, approach, constraints, limitations]

## Test results (what your code must satisfy):
[pytest output showing which tests fail and error messages
— NOT test source code]

## Source files to create/modify:
[Specific file paths]

## Files to read for context:
[Existing source, context packets, design doc sections]

## Dependencies from prior tasks:
[What was built, where to import from, decisions that affect this task]

## Worktree: [path]

## Success criteria:
- All tests pass
- Quality gate clean
- Implementation stays within plan scope
```

### implementer Delegation Template (Audit Fix)

```markdown
Fix specific issues from audit review.

## Audit findings to address:
[Finding 1: file:line, severity, description, recommended fix]
[Finding 2: file:line, severity, description, recommended fix]

## Previous implementation context:
[Previous sub-agent's return — decisions_made, carry_forward,
files_modified — so you understand what was built and why]

## Original plan chunk:
[For reference — what was the intended behavior]

## Worktree: [path]

## Success criteria:
- Specific findings addressed
- ALL tests still pass (not just affected — watch for regressions)
- Quality gate clean
- ONLY fix specified issues — no refactoring beyond fix scope
```

---

## Resume vs Cold-Start Prompts {#resume}

Sub-agents can be resumed (continuing existing context) or cold-started (fresh context with prior return as context). The delegation prompt differs:

### Resume Prompt (Continuing Same Sub-Agent)

```markdown
Continue your work with these additional findings.

## New audit findings:
[Findings from the audit-checker review]

## What to fix:
[Specific issues with file:line references]

## Constraint:
Re-run ALL tests after fixes, not just affected ones.
```

Resume prompts are shorter — the sub-agent already has full context from its prior work.

### Cold-Start Prompt (Fresh Sub-Agent Replacing Previous)

```markdown
Continue work started by a previous implementer.

## Previous implementer's return:
- Files modified: [list]
- Decisions made: [list with reasoning]
- Carry forward: [important notes]
- Quality gate: [result]
- Status: partial — tests 9-12 still failing

## Original delegation context:
[Full original delegation prompt — the fresh sub-agent needs
everything the original received]

## Additional context:
[Why the previous sub-agent returned partial — timeout? stuck?]

## Your task:
Pick up where the previous implementer left off. Their files are
in the worktree. Focus on the remaining failing tests.
```

Cold-start prompts are longer — the fresh sub-agent needs the original context PLUS the previous return to have equivalent knowledge.
