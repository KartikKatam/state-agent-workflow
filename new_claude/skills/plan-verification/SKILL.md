---
name: plan-verification
version: 1-0-0
triggers:
  - agent_role: plan-checker
    conditions: when verifying an implementation plan against a design document
description: >
  Use when verifying an implementation plan before execution begins.
  Activates for: plan-checker sub-agent dispatched by the Planner to
  verify plan quality across 8 dimensions. Also activates for re-verification
  after plan revision. Also use when any agent needs to assess plan quality
  before committing to execution, even outside the formal plan-checker flow.
  Do NOT use for: creating plans (use phase-planning), reviewing implemented
  code (use code-review), executing tasks (use task-execution).
depends_on: []
---

# Plan Verification

## Core Principle

**Read the design BEFORE the plan.** Build your mental model of what SHOULD exist from the design document, then evaluate whether the plan achieves it. If you read the plan first, you anchor on what IS planned and lose the ability to notice what's MISSING.

**Violating the letter of the rules is violating the spirit of the rules.**

<HARD-GATE>
Do NOT produce any verdict until you have:
1. Read the design document and built a requirements checklist
2. Read the full implementation plan (all phases, tasks, dependencies)
3. Read the codebase context packets referenced in the plan's grounding
4. Verified ALL 8 dimensions — skipping a dimension invalidates the verdict

If the design document, plan, or context packets are missing, return
`failed` — you cannot verify without all three inputs.
</HARD-GATE>

## Quick Reference

| Situation | Action |
|-----------|--------|
| First-time verification (no prior report) | Full 8-dimension check |
| Re-verification after revision | Focus on previously-failed dimensions, spot-check passing ones |
| Previous verification report provided | Read it first — gives you accumulated context. Re-verify failed dimensions, confirm fixes |
| Dimension clearly passes | Still document evidence — "passes" without evidence is not a finding |
| Dimension is ambiguous | Flag as fail with specific concerns — err toward thoroughness |
| Plan doesn't follow expected schema | Return `partial` — document structural issues as revision suggestions |
| Context packets don't cover plan's modules | Fail `technical_feasibility` — you can't verify what you can't see |
| Verifying a partial plan (only some phases ready) | Run all 8 dimensions on available phases. Note in verdict that cross-phase dependencies and full coverage cannot be verified yet |

## The 8 Verification Dimensions

Every dimension produces a `pass` (bool) and `notes` (evidence). A dimension without evidence is not verified — it's skipped.

### 1. Goal Coverage

**Question:** Does every design requirement map to at least one task?

**How to check:**
1. Extract requirements from the design document (each MUST, SHOULD, acceptance criterion)
2. For each requirement, find the task(s) that implement it
3. Check via `design_doc_sections` on phases AND `requirements` on tasks

**Pass:** Every design requirement maps to ≥1 task. No orphan requirements.
**Fail signals:** Requirement with no implementing task. Design doc section not referenced by any phase's `design_doc_sections`.

Use PTC to build the coverage matrix — requirements as rows, tasks as columns. Any all-zero row is an uncovered requirement.

**If the design document itself contains vague requirements** (e.g., "handle errors gracefully"), flag them as `goal_coverage` issues. You cannot verify plan coverage of a requirement you can't define — report the vagueness and suggest the Planner request design doc clarification.

### 2. Task Completeness

**Question:** Does each task have everything a context-free coder needs?

**Required per task:** `phase_context`, `target_files` (non-empty), `requirements` (non-empty, each testable), `limitations`, `considerations`, `dependencies`, `review_level`, `test_expectations` (non-empty).

**Pass:** All tasks have all required fields populated with substantive content.
**Fail signals:** Empty `requirements`, missing `target_files`, `test_expectations` with no entries, `phase_context` missing or generic.

**Vagueness check on requirements:** Flag any requirement containing unquantified adjectives without a measurable threshold. The full blocklist is in the PTC vagueness scanner in `references/patterns.md` — use it rather than checking manually. Exception: if the vague word is immediately followed by a specific threshold ("scalable to 10K users"), it's not vague. Requirements with unquantified adjectives cannot be tested.

### 3. Dependency Accuracy

**Question:** Are task dependencies valid with no cycles?

**How to check:** Use PTC with Kahn's algorithm on the task dependency graph:

```python
from collections import deque, defaultdict

graph = defaultdict(list)
in_degree = defaultdict(int)
all_tasks = set()

for task in tasks:
    all_tasks.add(task["id"])
    for dep in task["dependencies"]:
        graph[dep].append(task["id"])
        in_degree[task["id"]] += 1

queue = deque(t for t in all_tasks if in_degree[t] == 0)
processed = []
while queue:
    t = queue.popleft()
    processed.append(t)
    for s in graph[t]:
        in_degree[s] -= 1
        if in_degree[s] == 0:
            queue.append(s)

if len(processed) != len(all_tasks):
    cycle_members = all_tasks - set(processed)
    print(json.dumps({"cycle": True, "members": sorted(cycle_members)}))
else:
    print(json.dumps({"cycle": False, "order": processed}))
```

Also check: every `depends_on` ID actually exists as a task ID. Dangling references are silent failures.

**Pass:** No cycles. All dependency references resolve to existing tasks. Topological order exists.
**Fail signals:** Cycle detected (report members). Dangling dependency reference. Task depends on itself.

### 4. Scope Boundaries

**Question:** Is each task reasonably scoped?

**Thresholds** (defaults — calibrate for your codebase's file granularity):
- > 5 `target_files` per task → likely too broad, should split
- > 3 distinct concerns in `requirements` → mixing responsibilities
- Task spans multiple modules with no shared interface → coupling risk

**Pass:** All tasks within scope thresholds. Each task has a single clear responsibility.
**Fail signals:** Task exceeds file target threshold. Task requirements mix unrelated concerns (e.g., "implement scoring AND update config schema AND add logging").

### 5. Risk Identification

**Question:** Do high-risk tasks have mitigation?

**Risk indicators:** Task touches concurrency, external APIs, complex state machines, shared mutable state, performance-critical paths, or security boundaries.

**Pass:** Every task with risk indicators has `considerations` addressing the risk, appropriate `review_level` (2-3), and `queries` for unknowns.
**Fail signals:** Task touching concurrency with `review_level: 1`. Task calling external API with no error handling in `considerations`. Performance-critical task with no performance consideration.

### 6. Wave Feasibility

**Question:** Can tasks in the same wave execute in parallel without conflicts?

A "wave" is a set of tasks with no inter-dependencies — they could run simultaneously. Use PTC to extract waves from the topological sort (Kahn's level-by-level output), then check for resource conflicts:

```python
# After Kahn's produces waves:
for wave_idx, wave_tasks in enumerate(waves):
    for i, a in enumerate(wave_tasks):
        for b in wave_tasks[i+1:]:
            overlap = set(a["target_files"]) & set(b["target_files"])
            if overlap:
                print(f"CONFLICT wave {wave_idx}: "
                      f"{a['id']} and {b['id']} "
                      f"share targets: {overlap}")
```

**Pass:** No two tasks in the same wave share any `target_files`.
**Fail signals:** Write-write conflict (both modify same file). Write-read conflict (task reads file another task modifies, but no dependency declared).

### 7. Acceptance Criteria Clarity

**Question:** Can each acceptance criterion be mechanically verified?

**Testability test per criterion:**
1. Can it be expressed as Given-When-Then with a binary outcome?
2. Does it specify an observable outcome (not internal state)?
3. Does it avoid vague qualifiers (see dimension 2 blocklist)?

**Also check `test_expectations`:** Every requirement in a task should have ≥1 test expectation covering it. Any requirement with no `requirement_refs` pointing to it is untested.

**Pass:** All criteria are mechanically testable. All requirements have test coverage.
**Fail signals:** Criterion uses vague language. Criterion describes internal state ("variable X is set to Y") instead of observable behavior. Requirement with no test expectation.

### 8. Technical Feasibility

**Question:** Do referenced APIs, patterns, and files actually exist?

**How to check:** Cross-reference the plan against codebase context packets:
- `target_files` — do the referenced existing files appear in the context?
- `reference_files` — do they exist at the specified paths?
- `suggestions` referencing code patterns — does the pattern exist?
- `grounding.concept_map` — are all entries `grounded` or `resolved`?

**Pass:** All references resolve. No `gap_blocking` entries in concept map. Referenced patterns exist in codebase.
**Fail signals:** `target_files` reference non-existent path not marked as "create". `reference_files` path doesn't exist. `concept_map` has `gap_blocking` entries. Plan references API that doesn't exist in context.

## Verdict Rules

| Condition | Verdict |
|-----------|---------|
| All 8 dimensions pass | **PASS** |
| Any dimension fails | **REVISE** — with specific revision suggestions |

Every REVISE verdict must include `revision_suggestions` — specific, actionable changes. "Fix the dependencies" is not a suggestion. "Task task-01-03 depends on task-01-05 but task-01-05 depends on task-01-03 — break the cycle by extracting the shared type into a new task-01-02a" is a suggestion.

## Multi-Gate Awareness

The plan-checker may be dispatched multiple times across review gates (PHASE_REVIEW → TASK_REVIEW → DETAIL_REVIEW → TEST_PLAN_REVIEW). If `previous_verification_report` is provided in your delegation prompt:

1. Read it first — it gives you accumulated context
2. Re-verify dimensions that previously failed (the Planner should have addressed them)
3. Spot-check dimensions that previously passed (plans change during revision)
4. Your report is cumulative — include all 8 dimensions, not just the ones you re-checked

## Stall Detection

If you're re-verifying after revision and the same dimensions still fail with the same issues, note this in your return. The Planner has a 3-iteration stall threshold — your evidence of no-progress helps the system escalate appropriately.

**Time budget heuristic:** Expect ~2-3 minutes per task for mechanical PTC checks, plus ~1-2 minutes per task for judgment-based dimensions (risk, feasibility, criteria clarity). A 10-task plan takes ~30-40 minutes for a thorough check. Under 10 minutes total is a red flag that dimensions were skipped.

## Critical Rules

- **Design before plan.** Read the design document first. Build your "what should exist" checklist before seeing the plan. This is the single most important rule — it prevents you from anchoring on the plan's framing.
- **Evidence in every dimension.** "Pass" without evidence is "skipped." Document what you checked and what you found.
- **Use PTC for mechanical checks.** Cycle detection, wave extraction, coverage matrices, vagueness scanning — these are algorithmic, not judgment calls. Run them in PTC.
- **Err toward REVISE.** An unnecessary revision round costs one iteration. A missed issue costs implementation rework. When in doubt, flag it.
- **Revision suggestions must be specific.** File paths, task IDs, exact changes. The Planner works from your suggestions — vague suggestions produce vague revisions.
- **Never modify the plan.** You are read-only. Your job is to verify and report, not to fix.

## Anti-Rationalization

| Excuse | Reality |
|--------|---------|
| "The plan looks well-structured, I'll just spot-check" | Well-structured plans can have missing requirements, hidden cycles, and untestable criteria. Full 8-dimension check. |
| "Cycle detection is overkill for a small plan" | Small plans have the most insidious dependency errors — they look obvious and aren't verified. Run the algorithm. |
| "This dimension clearly passes, no need to document evidence" | Undocumented passes are indistinguishable from skipped dimensions. Write the evidence. |
| "The vagueness check is too strict — 'properly handles errors' is clear enough" | "Properly" means different things to different coders. If the criterion can't be stated as Given-When-Then with a binary outcome, it's not testable. |
| "I'll PASS with notes about minor issues" | If the issues affect any dimension, it's REVISE. Notes on a PASS get ignored — revision suggestions on a REVISE get addressed. |
| "The Planner already checked dependencies" | The Planner created the dependencies. Fresh-eyes verification exists precisely because creators miss their own errors. |

**Red Flags — STOP:**
- Producing a verdict without reading the design document first
- Skipping any of the 8 dimensions
- PASS verdict with any dimension lacking evidence
- REVISE verdict with no `revision_suggestions`
- Running mechanical checks (cycles, waves, coverage) by hand instead of PTC

## References

For PTC code recipes (Kahn's algorithm, wave extraction, coverage matrix, vagueness scanner, file existence checker), read `references/patterns.md`.

For common plan verification failures with WRONG/RIGHT examples, read `references/anti-patterns.md`.
