# Sub-Agent .md Anti-Patterns

Reference file for `writing-sub-agents`. Failure modes specific to ephemeral, bounded agent definitions.

---

## Table of Contents

1. [The Full Schema Dumper](#1)
2. [The Context Assumer](#2)
3. [The Parent's Shadow](#3)
4. [The Leaky Barrier](#4)
5. [The Rigid Step List](#5)
6. [The Over-Qualified Worker](#6)
7. [The Scope Creeper](#7)
8. [The Silent Failer](#8)
9. [The Hardcoded Path Agent](#9)
10. [The Single-Parent Specialist](#10)

---

## 1. The Full Schema Dumper {#1}

**Symptom:** 30+ lines of JSON schema in the body showing every field, type, and validation rule.

**Why it fails:** SubagentStop hook validates schema. Body tokens repeat on every invocation. 200 schema tokens × 50 invocations = 10,000 wasted tokens.

```markdown
// WRONG — full schema in body (30+ lines of JSON structure)

// RIGHT — semantic quality guidance
Return structured JSON as your final message. The hook validates
required fields — focus on content quality:
- Every test in tests_written should map to a plan requirement
- decisions_made should explain WHY, not just WHAT
- carry_forward must list specific gaps for a replacement to continue
```

**Fix:** Delete schema. Teach what makes GOOD content. Hook catches structural violations.

---

## 2. The Context Assumer {#2}

**Symptom:** References "the plan", "the error", "the current task" without specifying these arrive via delegation prompt.

```markdown
// WRONG — assumes context exists
Read the plan to understand requirements. Check the test results.

// RIGHT — traces context to delegation prompt
Read the plan chunk included in your delegation prompt. Everything
you need arrives in the delegation prompt — don't search for files
unless the prompt directs you to read specific paths.
```

---

## 3. The Parent's Shadow {#3}

**Symptom:** Parent's think prompt questions, workflow routing decisions, or "what happens after you return" in the sub-agent body.

```markdown
// WRONG — parent's workflow awareness
After you return, the Coder will evaluate your results and decide
whether to dispatch the implementer or re-dispatch you.

Think about:
Q1: Are there dependencies between this chunk and others?
Q2: Should I suggest wave ordering changes?

// RIGHT — sub-agent focuses on own job
Your tests will be used as acceptance criteria for implementation.
Write them to verify BEHAVIOR, not implementation detail.
Document cross-chunk dependencies in carry_forward so the parent
can account for them.
```

**Fix:** Delete all "what happens after" content. Keep only what affects the sub-agent's own work quality.

---

## 4. The Leaky Barrier {#4}

**Symptom:** Information barrier stated without concrete consequences.

```markdown
// WRONG
Never read source files. This is important for separation of concerns.

// RIGHT
You are blind to implementation source by design. Tests that couple
to implementation are worthless — when implementation changes
(refactoring, optimization), coupled tests break even though behavior
is preserved. Your tests must remain valid across ANY correct
implementation.
```

**Fix:** Every barrier needs a concrete consequence of violation.

---

## 5. The Rigid Step List {#5}

**Symptom:** Flat numbered list with no decision points, iterations, or conditional branches.

```markdown
// WRONG
1. Read plan chunk  2. Write tests  3. Run tests  4. Return

// RIGHT — phases with decision points
1. **Parse and understand** — extract requirements
2. **Design before writing** — what behavior, what assertions
3. **Write and validate** — create tests, run them:
   - Behavioral failures → proceed to gate
   - SyntaxError → fix your code first
   - Tests PASS → RED FLAG: rethink assertion
4. **Quality gate and return** — fix issues yourself
```

---

## 6. The Over-Qualified Worker {#6}

**Symptom:** Opus model for a read-only extraction task. 5 skills loaded for a task needing 0-1.

| Task Type | Right Model | Right Skill Count |
|-----------|------------|-------------------|
| Read-only extraction | Haiku | 0 |
| Analysis / code writing | Sonnet | 1-2 |
| Complex multi-step reasoning | Sonnet (Opus is for teammates) | 1-2 |

---

## 7. The Scope Creeper {#7}

**Symptom:** "If you notice other improvements...", "Feel free to also fix...", "While you're here..."

```markdown
// WRONG — encourages scope creep
If you notice code quality issues beyond the audit findings,
feel free to fix those too.

// RIGHT — strict scope, flag without acting
Fix the specific issues from audit findings. If you notice other
issues, document in decisions_made as "OBSERVED: [issue]" but
do NOT fix them. The parent decides on additional work.
```

---

## 8. The Silent Failer {#8}

**Symptom:** "Return failed" for everything with no diagnostic information.

```markdown
// WRONG
If anything goes wrong, return status: "failed".

// RIGHT
Return with diagnostic detail:
- decisions_made: what you tried and what went wrong
- carry_forward: specific error or blocker
- hypothesis about root cause (even if uncertain)
```

---

## 9. The Hardcoded Path Agent {#9}

**Symptom:** File paths, worktree locations, or branch names hardcoded in .md body.

```markdown
// WRONG
Write to src/pipeline/processor.py in /workspace/phase-01/task-03

// RIGHT
Write to source paths from your delegation prompt. Work exclusively
in the provided worktree path.
```

---

## 10. The Single-Parent Specialist {#10}

**Symptom:** Multi-parent sub-agent only handles one parent's use case.

```markdown
// WRONG — only handles Coder dispatch
Write tests that MUST fail against unimplemented behavior.

// RIGHT — adapts to dispatch type
**`tdd_chunk`:** Write NEW tests that MUST fail. Verify behavioral
failure types.
**`infrastructure`:** Fix EXISTING test infrastructure. Verify
collect-only passes.
Both modes: run quality gate before returning.
```

**Fix:** Check design spec for all parents. Ensure body handles each use case via delegation type field.
