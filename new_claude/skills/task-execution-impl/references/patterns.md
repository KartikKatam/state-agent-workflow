# Task Execution Patterns — Implementer

Proven approaches for scope discipline, deviation documentation, and verification during delegated task execution.

## Contents

- [Delegation Prompt Assessment](#assessment)
- [Scope Checking During Implementation](#scope-checking)
- [Deviation Documentation Examples](#deviation-examples)
- [Verification Walkthrough](#verification)
- [Targeted Mode: Fix Scope](#targeted-scope)

## Delegation Prompt Assessment {#assessment}

### Complete delegation prompt (proceed immediately)

```
DELEGATION PROMPT:
- type: tdd_chunk
- task: Implement weighted scoring for batch selection
- plan_chunk: Phase 2, Task 3 — score_candidate function
- source_targets: [src/producer/ops_batch.py, src/producer/types.py]
- test_results: 4 tests fail with ImportError (ops_batch doesn't exist yet)
- success_criteria: all tests pass, quality gate clean
- limitations: ["Do not modify BatchConfig", "No async"]
- previous_chunk_decisions: BatchCandidate defined as frozen dataclass in task-01
- worktree: /workspace/.claude/worktrees/phase-02/task-03

Assessment: All critical context present. Proceeding.
```

### Incomplete delegation prompt (document and adapt)

```
DELEGATION PROMPT:
- type: tdd_chunk
- task: Implement scoring function
- source_targets: [src/producer/ops_batch.py]
- worktree: /workspace/.claude/worktrees/phase-02/task-03
- (no test_results, no previous_chunk_decisions)

Assessment:
- Missing test_results → proceed from plan requirements only.
  Documented in decisions_made: "No test results provided.
  Implementing from plan chunk behavioral specs."
- Missing previous_chunk_decisions → check existing source in
  worktree for evidence of prior decisions.
  Documented: "Inferred BatchCandidate is frozen dataclass from
  existing src/producer/types.py:12"
```

### Critically incomplete (return failed)

```
DELEGATION PROMPT:
- type: tdd_chunk
- task: Implement something for batch selection
- (no plan_chunk, no source_targets, no worktree)

Assessment: Cannot proceed.
Return: { status: "failed", reason: "Delegation prompt missing
  plan chunk, source targets, and worktree path. Cannot implement
  without knowing what to build, where to write, or where to work." }
```

## Scope Checking During Implementation {#scope-checking}

### When a file outside source targets needs a change

**Step 1:** Classify the change.

| Classification | Example | Action |
|---------------|---------|--------|
| Mechanical necessity | Adding export to `__init__.py` | Document deviation (low impact), proceed |
| Type system requirement | Adding type to shared types file | Document deviation (medium impact), proceed with caution |
| Behavioral change | Modifying a function in another module | Do NOT modify. Document as OBSERVED |
| Convenience improvement | "While I'm here, refactor X" | Do NOT do this |

**Step 2:** If proceeding, log the deviation immediately.

### When a plan requirement conflicts with existing code

DO: Implement the plan requirement and document the conflict.
```
decisions_made: "Plan requires ValueError on None input. Existing
pattern in scoring.py:78 returns empty list for invalid input.
Implemented ValueError per plan requirement. Plan may not have
accounted for the existing convention — flagging for parent review."
```

DO NOT: Silently follow the existing pattern instead of the plan.

## Deviation Documentation Examples {#deviation-examples}

### Low-impact mechanical deviation

```json
{
  "decision": "Added re-export of ScoreResult to producer/__init__.py",
  "reason": "Python import system requires explicit export for type to be accessible from package root. File not in source_targets but change is mechanical.",
  "impact": "Low — no behavioral change, import mechanics only"
}
```

### Medium-impact type change

```json
{
  "decision": "Used TypedDict instead of dataclass for ScoringResult",
  "reason": "JSON serialization required by downstream consumer (discovered reading existing source). Dataclass requires custom encoder.",
  "impact": "Medium — API surface changes: fields accessed via ['key'] not .key"
}
```

### OBSERVED (gap noticed, not acted on)

```json
{
  "decision": "OBSERVED: score_roi in consumer/scoring.py has no type annotations on parameters",
  "reason": "File is not in my scope (reference only). Noting for potential future task.",
  "impact": "None — observation only, no change made"
}
```

## Verification Walkthrough {#verification}

Work through systematically, not from memory:

1. **Re-read success criteria** from delegation prompt. For each criterion, identify the specific evidence it's met.
2. **Check modified files** — list every file you touched. Verify each is in source_targets (or has a documented deviation).
3. **Re-read limitations** — for each, confirm you didn't violate it. Check your actual changes, not your memory.
4. **Review decisions_made** — any medium/high impact deviations that the parent needs to evaluate?
5. **Run quality gate** — format, lint, typecheck, pytest. Read the full output.
6. **Only then** set status to `completed`.

### Verification for targeted mode

After fixing audit findings:

1. **Map each finding** to the change you made. Every finding should have a corresponding fix.
2. **Run ALL tests** — not just tests related to the findings. Audit fixes can introduce regressions.
3. **Confirm no unrelated changes** — diff your modifications against the finding list. Every change should trace to a finding.
4. **Quality gate** — full run, not just the files you touched.

## Partial Return: When and How {#partial-return}

### When to return `partial` vs `failed`

| Situation | Status | Why |
|-----------|--------|-----|
| 8 of 12 tests pass after 5 iterations | `partial` | Useful work exists — parent dispatches debugger for remaining 4 |
| Quality gate passes but 2 success criteria unmet | `partial` | Code is valid, parent evaluates missing criteria |
| Plan requirement literally cannot work as specified | `partial` | Evidence of infeasibility is useful output |
| Missing dependency blocks all progress | `partial` if some files written, `failed` if nothing done | Any written code is useful context for re-dispatch |
| Delegation prompt has no plan chunk | `failed` | Cannot produce any meaningful work without requirements |
| Worktree is corrupted or missing | `failed` | Infrastructure problem, not a work output |

### Well-formed partial return

```json
{
  "status": "partial",
  "files_modified": ["src/producer/ops_batch.py"],
  "quality_gate_passed": true,
  "test_results": { "total": 12, "passed": 8, "failed": 4 },
  "decisions_made": [
    {"decision": "Implemented scoring pipeline and batch assembly",
     "reason": "Core behavioral requirements from plan §3.1-3.2", "impact": "Low"},
    {"decision": "OBSERVED: constraint satisfaction (plan §3.3) may need async — tests 9-12 timeout",
     "reason": "Synchronous implementation hits 5s timeout on constraint solver",
     "impact": "High — may need architectural change"}
  ],
  "carry_forward": [
    "Tests 9-12 fail with timeout in constraint satisfaction (>5s). Synchronous approach may be fundamentally wrong for this section.",
    "Tests 1-8 pass — scoring and batch assembly are solid.",
    "ops_batch.py:89-120 is the constraint section — a debugger or fresh implementer should focus there.",
    "Used frozen dataclass for BatchResult (consistent with task-01 pattern)."
  ],
  "iterations": 5
}
```

**What makes this partial return good:**
- `carry_forward` tells the next agent EXACTLY where to focus (ops_batch.py:89-120)
- Error description is specific (timeout, not "didn't work")
- Hypothesis included (sync approach may be wrong — saves the debugger from re-discovering this)
- Working code is preserved (tests 1-8 pass — don't re-implement)

## Targeted Mode: Fix Scope {#targeted-scope}

In `targeted` mode, your scope is the audit findings — not the original plan requirements.

| Allowed | Not Allowed |
|---------|-------------|
| Fix the specific issue at the reported file:line | Refactor the function while you're there |
| Add missing validation the finding identified | Add validation the finding didn't mention |
| Fix the import the finding flagged | Reorganize the import structure |
| Adjust the return type the finding specified | "Improve" the return type beyond the fix |

**The test:** For every line you changed, can you point to the specific audit finding that required it? If not, revert that line.
