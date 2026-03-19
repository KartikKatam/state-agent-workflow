# Plan Verification Anti-Patterns

Common verification failures with diagnostic criteria and WRONG/RIGHT fixes.

## Contents

1. [The Rubber Stamp](#1-the-rubber-stamp)
2. [The Plan-First Reader](#2-the-plan-first-reader)
3. [The Manual Calculator](#3-the-manual-calculator)
4. [The Scope Ignorer](#4-the-scope-ignorer)
5. [The Wave Optimist](#5-the-wave-optimist)
6. [The Vagueness Acceptor](#6-the-vagueness-acceptor)
7. [The Context-Free Verifier](#7-the-context-free-verifier)
8. [The Silent Passer](#8-the-silent-passer)
9. [The Re-Verification Skipper](#9-the-re-verification-skipper)

---

## 1. The Rubber Stamp

**Symptom:** All 8 dimensions pass with generic notes like "looks good" or "no issues found." Review completed in under 2 minutes.

**Why it fails:** Generic notes mean the checker didn't actually verify — it skimmed. "No issues found" without evidence of what was checked is indistinguishable from "didn't check."

```
WRONG:
  goal_coverage: {pass: true, notes: "All requirements covered"}
  dependency_accuracy: {pass: true, notes: "Dependencies look correct"}

RIGHT:
  goal_coverage: {pass: true, notes: "12 design requirements mapped:
    REQ-1 (batch selection) → task-01-01, task-01-02.
    REQ-2 (scoring) → task-01-03.
    [... all 12 listed]. Coverage matrix in PTC confirms no gaps."}
  dependency_accuracy: {pass: true, notes: "Kahn's BFS on 15 tasks:
    no cycles. 4 waves extracted. All 8 dependency references resolve
    to existing task IDs."}
```

---

## 2. The Plan-First Reader

**Symptom:** Verification notes reference plan structure but not design requirements. Goal coverage checks whether tasks exist, not whether they implement the right things.

**Why it fails:** Reading the plan first anchors on what's planned. You verify the plan is internally consistent but miss that it doesn't actually implement the design.

```
WRONG: (read plan first)
  goal_coverage: {pass: true, notes: "All 8 tasks have requirements
    and test expectations. Each phase has design_doc_sections."}
  — But design doc has 15 requirements and plan only covers 10.
    The checker never noticed because they never built the
    requirements list from the design doc independently.

RIGHT: (read design first)
  goal_coverage: {pass: false, notes: "Design doc has 15 requirements.
    Plan covers 10. Missing: REQ-11 (error recovery for external API
    timeout), REQ-12 (config hot-reload), REQ-13 (graceful degradation
    when scoring service unavailable), REQ-14 (audit logging for batch
    decisions), REQ-15 (batch size limits from runtime config)."}
```

---

## 3. The Manual Calculator

**Symptom:** Checker traces dependency chains by hand instead of running Kahn's algorithm in PTC. Misses a cycle in a 15-task plan because it "looked acyclic."

**Why it fails:** Humans are bad at detecting cycles in graphs with > 8 nodes. The algorithm is O(V+E) and catches every cycle. Manual tracing is O(error-prone).

```
WRONG:
  dependency_accuracy: {pass: true, notes: "Traced dependencies for
    all tasks. No cycles visible. Order: 1→2→3, 4→5→6, 3→7, 6→7."}
  — Missed: task-07 → task-04 (hidden in task-07's dependencies),
    creating cycle 4→5→6→7→4.

RIGHT:
  dependency_accuracy: {pass: false, notes: "Kahn's BFS in PTC:
    cycle detected. Members: task-04, task-05, task-06, task-07.
    Specifically: task-07 depends on task-04, but task-04 depends
    on task-05 which depends on task-06 which depends on task-07."}
```

---

## 4. The Scope Ignorer

**Symptom:** Tasks with 8+ target files and mixed concerns pass scope_boundaries without comment.

**Why it fails:** Oversized tasks are the #1 cause of implementation stalls. A coder receives a task touching 8 files across 3 modules and can't hold it all in context. They implement partially, miss interactions, and the auditor catches it later at 10x the cost.

```
WRONG:
  scope_boundaries: {pass: true, notes: "Tasks are appropriately scoped."}

RIGHT:
  scope_boundaries: {pass: false, notes: "task-02-01 has 8 target_files
    spanning producer/ (3 files), consumer/ (3 files), and common/
    (2 files). Requirements mix scoring logic, config schema changes,
    and integration wiring — three distinct concerns. Suggest splitting
    into: task-02-01a (scoring in producer/), task-02-01b (config
    changes in common/), task-02-01c (integration in consumer/,
    depends on 01a and 01b)."}
```

---

## 5. The Wave Optimist

**Symptom:** Wave feasibility passes without checking file overlaps. Checker assumes "if they're in different modules, they're independent."

**Why it fails:** Module boundaries don't guarantee file independence. Two tasks in different modules might both modify `__init__.py`, `config.py`, or a shared utility file. Only mechanical file-set intersection catches these.

```
WRONG:
  wave_feasibility: {pass: true, notes: "Tasks in each wave are
    in different modules — no conflicts expected."}

RIGHT:
  wave_feasibility: {pass: false, notes: "PTC wave extraction:
    4 waves. Wave 2 conflict: task-01-03 and task-01-04 both
    target producer/__init__.py. If run in parallel, merge
    conflicts are guaranteed. Suggest: add task-01-03 as
    dependency of task-01-04, moving task-01-04 to wave 3."}
```

---

## 6. The Vagueness Acceptor

**Symptom:** Requirements like "properly handle errors" and "efficient implementation" pass acceptance criteria clarity.

**Why it fails:** A coder receiving "properly handle errors" will implement whatever they think "properly" means. The auditor will verify against whatever they think "properly" means. When these disagree (they always do), rework follows.

```
WRONG:
  acceptance_criteria_clarity: {pass: true, notes: "All tasks
    have clear requirements."}
  — requirement "properly handle errors" passed unchallenged.

RIGHT:
  acceptance_criteria_clarity: {pass: false, notes: "task-01-02
    requirement 3 'properly handle errors' fails testability:
    'properly' is unquantified. Suggest replacing with:
    'Raises ValueError on empty input. Raises TypeError on
    non-numeric scores. Logs warning and returns partial result
    when individual candidate scoring fails. Never raises
    unhandled exceptions to the pipeline caller.'"}
```

---

## 7. The Context-Free Verifier

**Symptom:** Technical feasibility passes without checking context packets. Checker accepts `reference_files` paths without verifying they exist.

**Why it fails:** Plans written from memory reference files that were renamed, moved, or deleted. Coders receive tasks pointing to ghost files and waste time searching.

```
WRONG:
  technical_feasibility: {pass: true, notes: "Plan references
    reasonable file paths and patterns."}

RIGHT:
  technical_feasibility: {pass: false, notes: "PTC file existence
    check: task-01-03 reference_file 'producer/scoring.py:45-67'
    does not exist. Context packet _codebase.json shows scoring
    was moved to producer/ops_scoring.py in the last refactor.
    task-02-01 suggestion references 'consumer/selection.py' which
    also does not exist. Suggest updating all file references
    against current codebase context."}
```

---

## 8. The Silent Passer

**Symptom:** Dimensions pass without any notes at all — empty string or "N/A."

**Why it fails:** A pass without evidence cannot be distinguished from a skip. The Planner sees "pass" and moves forward, but the dimension was never actually checked.

```
WRONG:
  risk_identification: {pass: true, notes: ""}

RIGHT:
  risk_identification: {pass: true, notes: "3 risk indicators found:
    task-02-03 touches concurrency (async batch processing) —
    review_level: 3, considerations include thread safety. PASS.
    task-03-01 calls external scoring service — considerations
    include timeout handling and retry logic. PASS. task-03-02
    modifies shared config — review_level: 2, limitation says
    'do not change existing fields.' PASS."}
```

---

## 9. The Re-Verification Skipper

**Symptom:** On re-verification after revision, checker only looks at previously-failed dimensions and blindly passes previously-passing ones.

**Why it fails:** Plan revisions can break previously-passing dimensions. Adding a new task to fix a cycle might introduce a scope issue. Changing a requirement to be more specific might break test coverage.

```
WRONG: (re-verification)
  "Previously passed dimensions 1,2,4,5,7,8 — keeping as PASS."
  Only re-checked dimensions 3 (cycles) and 6 (waves).

RIGHT: (re-verification)
  Re-verified dimension 3 (previously failed): cycle resolved. PASS.
  Re-verified dimension 6 (previously failed): wave conflicts
    resolved after dependency change. PASS.
  Spot-checked dimension 4: new task-01-02a introduced in revision
    has 6 target_files — scope threshold exceeded. REVISE.
  Other dimensions: confirmed no regression from plan changes.
```
