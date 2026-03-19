---
name: sub-agent-delegation
description: >
  Use when dispatching work to Haiku or Sonnet sub-agents via the Task tool.
  Activates for any sub-agent lifecycle: task scoping, dispatch, result
  verification, and multi-agent synthesis. Also use when a sub-agent fails
  or returns suspicious results. Do NOT use for: direct tool calls you
  perform yourself, teammate-to-teammate messaging via SendMessage, or
  orchestrator-level workflow coordination (see workflow-coordination skill).
---

# Sub-Agent Delegation

## Core Principle

Delegation is a complete lifecycle: **scope → context → dispatch → verify → synthesize**. The most common failure is treating dispatch as the final step. Sub-agents are optimistic reporters who claim success without evidence — verification is not optional.

## Quick Reference

| Situation | Action |
|-----------|--------|
| Task has one clear deliverable | Single sub-agent, scoped to that deliverable |
| Task has multiple independent parts | Parallel dispatch — one sub-agent per part |
| Tasks share files or depend on order | Sequential dispatch — wait for each to finish |
| Task is extraction, formatting, or schema-following | Haiku — pattern execution, no design decisions needed |
| Task requires judgment, design decisions, or novel code | Sonnet — needs reasoning about trade-offs |
| Sub-agent reports "done" | Verify: artifacts exist, content correct, tests pass |
| Sub-agent fails | Retry with clarified prompt first, escalate only after retry fails |
| Multiple sub-agents returned results | Synthesis pass — combine, deduplicate, resolve conflicts |
| Unsure what context to include | Minimum that makes the task self-contained (see Step 2) |

## Core Workflow

### Step 1: Scope the Task

Define exactly ONE deliverable per sub-agent. Write it as a completion sentence: "This sub-agent is done when ___."

**Scoping checklist:**
- What specific artifact will the sub-agent produce? (file, analysis, answer)
- What files/directories does it need to read?
- What files/directories may it modify? (be explicit)
- What should it NOT touch?

If you cannot fill this checklist, the task is not scoped enough to delegate. Split further or gather more information first.

### Step 2: Size the Context

Include in the sub-agent prompt:
- **Always:** The specific task description with completion criteria
- **Always:** Exact file paths the sub-agent needs (not "the source directory")
- **If modifying code:** Current content of files to modify, or relevant sections
- **If following patterns:** One concrete example of the desired pattern
- **Never:** Unrelated project context, full codebase overviews, or "nice to have" information

**Why context sizing matters:** Too little → sub-agent searches blindly, wastes turns, produces wrong output. Too much → sub-agent loses focus, misses the task in the noise, starts "improving" unrelated code. Both waste tokens and time.

**The 2-Read test:** If a sub-agent would need more than 2 Read/Grep calls to understand its task, your context is too small.

**The 30% test:** If more than 30% of context is unrelated to the specific deliverable, your context is too large.

### Step 3: Choose Dispatch Mode

Use the dispatch decision from Step 1's scope to choose:

**Parallel dispatch** — ALL of these must hold:
- Tasks are file-independent (no shared write targets)
- Tasks don't depend on each other's output
- Order doesn't matter for correctness

**Sequential dispatch** — when ANY of these hold:
- Task B needs Task A's output
- Tasks modify the same files
- Later tasks depend on earlier results

When unsure, default to sequential. Parallel dispatch with hidden dependencies causes silent corruption that is expensive to debug.

### Step 4: Dispatch with Structured Prompt

Every sub-agent prompt must contain these four sections:

```
## Task
[One sentence: what to produce]

## Context
[Files to read, patterns to follow, constraints]

## Deliverable
[Exact output: file path, format, content expectations]

## Constraints
[What NOT to do, scope boundaries]
```

Step 4 produces dispatched sub-agents whose results Step 5 will verify.

### Step 5: Verify Results

**Do not trust the report.** After every sub-agent returns:

1. **Artifacts exist** — Does the file/output they claimed to produce actually exist?
2. **Content matches** — Read the actual output. Does it match what was requested?
3. **Checks pass** — If they claimed tests pass, run the tests. If they claimed code works, verify it.
4. **Scope respected** — Did they stay within bounds? Any files modified that shouldn't have been?

If verification fails: return to Step 4 with corrections in the prompt. If a corrected retry also fails, escalate to the lead with:

1. **Original task prompt** — what you asked the sub-agent to do
2. **Sub-agent output** — what it returned (verbatim, not summarized)
3. **Verification findings** — what specifically failed (artifact missing, test failures, scope violation)
4. **Retry changes** — what you clarified in the second attempt
5. **Why retry failed** — same failure or a different one?

Without this evidence, the lead must re-investigate from scratch, wasting the time your attempts already spent.

### Step 6: Synthesize (multi-sub-agent only)

When multiple sub-agents return verified results from Step 5:

1. **Collect** all deliverables
2. **Deduplicate** overlapping content
3. **Resolve conflicts** — if two sub-agents produced contradictory outputs, investigate both and pick the substantiated one (or escalate)
4. **Find cross-cutting connections** — individual sub-agents see their partition, not the whole. Dependencies between parts that no individual sub-agent could see are YOUR responsibility.
5. **Verify the synthesis** — the combined result must be checked as a whole, not just the parts

Step 6 produces the final integrated deliverable.

## Agent-Specific Routes

| Your Role | Common Delegation | Sub-Agent Produces | Model |
|-----------|-------------------|--------------------|-------|
| Explorer | File analysis, dependency mapping | Context packet sections, query results | Haiku |
| Researcher | Source extraction, document parsing | Research entries with citations | Haiku |
| Coder | Code writing, test implementation | Code + passing tests | Sonnet |
| Tester | Scenario implementation | Test files, execution results | Sonnet |
| Strategist | Complexity estimation, dep analysis | Analysis reports | Haiku |
| Auditor | Targeted code inspection | Review findings with file:line refs | Sonnet |

For detailed patterns per role, read `references/patterns.md`.

## PTC Delegation

When sub-agents need to run analysis code (not just read files), they can use `ptc_execute`. This is an extension of the delegation workflow above — all 6 steps still apply.

### When to Delegate PTC Work

Delegate PTC tasks when the work has multiple independent parts that each require LLM reasoning at intermediate steps. If the task is purely mechanical (no reasoning between steps), write one `ptc_execute` call with a loop instead — sub-agents are unnecessary overhead.

| Situation | Action |
|-----------|--------|
| Multiple independent analyses needing judgment | Delegate — one sub-agent per analysis |
| Read 50 files and extract class names | Don't delegate — one ptc_execute with `os.walk` |
| Analyze structure, coverage, AND dependencies | Delegate — 3 independent analyses, each may need "dig deeper" judgment |
| Run a batch of metrics on one module | Don't delegate — one ptc_execute with all metrics |

### How Sub-Agents Get PTC Access

Sub-agents share YOUR container via the REPL pool. When a sub-agent calls `ptc_execute`, a new REPL process starts in your container (~100ms). No new container, no pip install. The sub-agent gets the same packages you have.

This means: sub-agent namespaces are isolated from yours, but they share your container's packages and resource limits.

### PTC Context in Delegation Prompts

When delegating a PTC task, include these in the sub-agent's prompt (Step 4):

1. **PTC availability:** "You have access to `ptc_execute` for running analysis code."
2. **Available packages:** List only the 3-5 packages this sub-agent needs — not your full list.
3. **Print discipline:** "Only `print()` output returns to your context. Process data in the container, print a JSON summary."
4. **Specific task:** Exactly what to analyze and what structure to output.
5. **Output location:** Where to write results (file path).

The print discipline point is critical — without it, sub-agents dump raw data into context, defeating PTC's token savings. Explain WHY: "400KB of file content stays in the container; only your 200-byte JSON summary enters context."

### PTC Delegation Template

```
Task(
    prompt="""Analyze test coverage for the payment module.

    You have access to ptc_execute for running analysis code.
    Available packages: coverage, pytest-cov, ast, json (stdlib)
    Project is mounted at /workspace.

    Write a ptc_execute call that:
    1. Runs pytest with coverage on tests/test_payment*.py
    2. Parses the coverage report
    3. Identifies uncovered functions in src/payment/
    4. Prints a JSON summary: { total_coverage_pct, uncovered_functions }

    Write results to: .claude/context/queries/payment-coverage.json
    """,
    subagent_type="general-purpose",
    model="haiku",
    team_name="my-team"
)
```

For PTC-specific delegation patterns by role, read `references/patterns.md`.
For PTC-specific anti-patterns, read `references/anti-patterns.md`.

## Critical Rules

- **One deliverable per sub-agent** — Multi-deliverable tasks get split. A sub-agent trying to do three things does none well.
- **Context is self-contained** — Sub-agent completes the task from the prompt alone. If it needs to search the codebase, you under-scoped the context.
- **Verify artifacts, not reports** — "I created the file" means nothing until you confirm the file exists and contains correct content. This is the single most documented delegation failure (24 instances in empirical testing).
- **Parallel requires file-independence** — Two sub-agents writing the same file produces corruption. Verify write targets before parallel dispatch.
- **Specify output format** — "Return your findings" → unusable. "Write to `path/file.json` following `schema.json`" → structured, verifiable output.
- **Retry before escalating** — First failures are usually prompt clarity issues. Retry with better context before escalating. Only escalate after a clarified retry also fails.

## References

For proven delegation patterns by agent role, read `references/patterns.md`.
For common delegation failures with WRONG/RIGHT examples, read `references/anti-patterns.md`.
