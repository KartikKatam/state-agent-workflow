# Plan Verification Patterns

PTC code recipes and verification patterns for the 8 dimensions.

---

## PTC Recipe: Full Mechanical Verification Suite

Run all mechanical checks in a single PTC call. This covers dimensions 3 (dependencies), 4 (scope), 6 (waves), and parts of 1 (coverage), 2 (vagueness), 7 (testability).

```python
import json
from collections import defaultdict, deque

# Load plan
with open("/workspace/.claude/plans/{feature}-plan.json") as f:
    plan = json.load(f)

results = {}

# === Dimension 3: Dependency Accuracy (Kahn's BFS) ===

graph = defaultdict(list)
in_degree = defaultdict(int)
all_tasks = {}

for phase in plan["phases"]:
    for task in phase["tasks"]:
        tid = task["id"]
        all_tasks[tid] = task
        for dep in task.get("dependencies", []):
            graph[dep].append(tid)
            in_degree[tid] += 1

# Check for dangling references
dangling = []
for tid, task in all_tasks.items():
    for dep in task.get("dependencies", []):
        if dep not in all_tasks:
            dangling.append({"task": tid, "missing_dep": dep})

# Kahn's algorithm — cycle detection + wave extraction
queue = deque(t for t in all_tasks if in_degree[t] == 0)
waves = []
processed = set()

while queue:
    wave = list(queue)
    waves.append(wave)
    next_queue = deque()
    for t in wave:
        processed.add(t)
        for s in graph[t]:
            in_degree[s] -= 1
            if in_degree[s] == 0:
                next_queue.append(s)
    queue = next_queue

cycle_members = set(all_tasks.keys()) - processed

results["dependency_accuracy"] = {
    "has_cycle": len(cycle_members) > 0,
    "cycle_members": sorted(cycle_members),
    "dangling_refs": dangling,
    "execution_order": [t for wave in waves for t in wave],
    "wave_count": len(waves)
}

# === Dimension 6: Wave Feasibility (file conflict detection) ===

wave_conflicts = []
for wave_idx, wave in enumerate(waves):
    wave_tasks = [all_tasks[tid] for tid in wave]
    for i, a in enumerate(wave_tasks):
        a_files = set(a.get("target_files", []))
        for b in wave_tasks[i+1:]:
            b_files = set(b.get("target_files", []))
            overlap = a_files & b_files
            if overlap:
                wave_conflicts.append({
                    "wave": wave_idx,
                    "task_a": a["id"],
                    "task_b": b["id"],
                    "shared_files": sorted(overlap)
                })

results["wave_feasibility"] = {
    "waves": [{"wave": i, "tasks": w} for i, w in enumerate(waves)],
    "conflicts": wave_conflicts
}

# === Dimension 4: Scope Boundaries ===

scope_issues = []
for tid, task in all_tasks.items():
    targets = task.get("target_files", [])
    reqs = task.get("requirements", [])
    if len(targets) > 5:
        scope_issues.append({
            "task": tid,
            "issue": f"too_many_targets ({len(targets)})",
            "files": targets
        })
    if len(reqs) > 3:
        scope_issues.append({
            "task": tid,
            "issue": f"too_many_requirements ({len(reqs)})",
            "count": len(reqs)
        })

results["scope_boundaries"] = {"issues": scope_issues}

# === Dimension 2 (partial): Vagueness Scanner ===

VAGUE_WORDS = {
    "fast", "good", "easy", "appropriate", "satisfactory",
    "reasonable", "properly", "correctly", "efficient",
    "robust", "secure", "scalable", "clean", "simple",
    "adequate", "sufficient", "minimal", "optimal",
    "performant", "stable", "reliable"
}

vague_findings = []
for tid, task in all_tasks.items():
    for i, req in enumerate(task.get("requirements", [])):
        words = set(req.lower().split())
        found = words & VAGUE_WORDS
        if found:
            vague_findings.append({
                "task": tid,
                "requirement_idx": i,
                "text": req[:80],
                "vague_words": sorted(found)
            })

results["vagueness_check"] = {"findings": vague_findings}

# === Dimension 7 (partial): Test Coverage Gaps ===

coverage_gaps = []
for tid, task in all_tasks.items():
    reqs = task.get("requirements", [])
    test_exps = task.get("test_expectations", [])

    # Collect all requirement indices covered by test expectations
    covered_indices = set()
    for te in test_exps:
        for ref in te.get("requirement_refs", []):
            # requirement_refs may be indices or keywords
            if isinstance(ref, int):
                covered_indices.add(ref)
            elif ref.isdigit():
                covered_indices.add(int(ref))

    for i, req in enumerate(reqs):
        if i not in covered_indices:
            coverage_gaps.append({
                "task": tid,
                "requirement_idx": i,
                "text": req[:80],
                "issue": "no test expectation covers this requirement"
            })

results["test_coverage_gaps"] = {"gaps": coverage_gaps}

# === Dimension 1 (partial): Design Doc Section Coverage ===

all_design_sections = set()
for phase in plan["phases"]:
    for section in phase.get("design_doc_sections", []):
        all_design_sections.add(section)

results["design_sections_covered"] = sorted(all_design_sections)

print(json.dumps(results, indent=2))
```

**Usage:** Replace `{feature}` with the plan filename. The script outputs all mechanical findings. You then apply judgment to the results — the script detects issues, you evaluate severity.

---

## PTC Recipe: Goal Coverage Matrix

When you need to verify design requirements against plan tasks:

```python
import json

# Load design doc requirements (extracted by you into a list)
requirements = [
    "Batch selection with quality scoring",
    "Configurable batch size",
    "Deterministic tie-breaking",
    # ... extracted from design doc
]

with open("/workspace/.claude/plans/{feature}-plan.json") as f:
    plan = json.load(f)

# Build coverage matrix
matrix = []
for req in requirements:
    req_lower = req.lower()
    covering_tasks = []
    for phase in plan["phases"]:
        for task in phase["tasks"]:
            # Check if any task requirement mentions this design requirement
            for task_req in task.get("requirements", []):
                if any(word in task_req.lower()
                       for word in req_lower.split()
                       if len(word) > 3):
                    covering_tasks.append(task["id"])
                    break
    matrix.append({
        "requirement": req,
        "covered_by": covering_tasks,
        "covered": len(covering_tasks) > 0
    })

uncovered = [m for m in matrix if not m["covered"]]
print(json.dumps({
    "total_requirements": len(requirements),
    "covered": len(requirements) - len(uncovered),
    "uncovered": uncovered
}, indent=2))
```

**Note:** This is fuzzy matching — you must review the results. False negatives (requirement is covered but matching didn't catch it) are common. Use this as a starting point, then manually verify uncovered items.

---

## PTC Recipe: File Existence Checker (Dimension 8)

```python
import json, os

with open("/workspace/.claude/plans/{feature}-plan.json") as f:
    plan = json.load(f)

missing = []
for phase in plan["phases"]:
    for task in phase["tasks"]:
        for f_path in task.get("reference_files", []):
            # Strip line ranges (e.g., "src/foo.py:45-67" → "src/foo.py")
            clean = f_path.split(":")[0]
            full = os.path.join("/workspace", clean)
            if not os.path.exists(full):
                missing.append({
                    "task": task["id"],
                    "file": f_path,
                    "type": "reference_file"
                })

        for f_path in task.get("target_files", []):
            clean = f_path.split(":")[0]
            full = os.path.join("/workspace", clean)
            # target_files that don't exist are OK if they'll be created
            # But flag them so the checker can verify intent
            if not os.path.exists(full):
                missing.append({
                    "task": task["id"],
                    "file": f_path,
                    "type": "target_file_new"
                })

print(json.dumps({"missing_files": missing}, indent=2))
```

---

## Verification Order Pattern

The 8 dimensions have a natural verification order based on dependencies:

```
1. Goal Coverage         ← needs: design doc + plan phases
2. Task Completeness     ← needs: plan tasks
3. Dependency Accuracy   ← needs: plan tasks (PTC: Kahn's)
4. Scope Boundaries      ← needs: plan tasks (PTC: threshold check)
5. Risk Identification   ← needs: plan tasks + context
6. Wave Feasibility      ← needs: dimension 3 output (waves from Kahn's)
7. Acceptance Criteria   ← needs: plan tasks + test expectations
8. Technical Feasibility ← needs: plan + context packets + codebase
```

Dimensions 1-4 and 7 can be checked from the plan alone. Dimensions 5 and 8 require codebase context. Dimension 6 requires dimension 3's output (wave extraction from Kahn's).

Run the full PTC mechanical suite first (covers 1-4, 6, 7 partially). Then apply judgment to dimensions 5, 7, 8 with context.

---

## Revision Suggestion Patterns

Good revision suggestions follow a template:

```
[dimension]: [task/phase ID] — [specific issue].
Suggestion: [exact change with IDs and file paths].
```

### WRONG/RIGHT Examples

```
WRONG: "Fix the dependency cycle."
RIGHT: "dependency_accuracy: task-01-03 and task-01-05 form a cycle.
Suggestion: Extract the shared TypeDefinitions into a new task-01-02a
that both depend on. Remove task-01-03 → task-01-05 dependency."

WRONG: "Some tasks are too broad."
RIGHT: "scope_boundaries: task-02-01 has 7 target_files spanning
producer/ and consumer/. Suggestion: Split into task-02-01a
(producer/ops_batch.py, producer/config.py) and task-02-01b
(consumer/adapters.py, consumer/pipeline.py, consumer/config.py).
Add task-02-01a as dependency of task-02-01b."

WRONG: "Acceptance criteria need to be clearer."
RIGHT: "acceptance_criteria_clarity: task-01-02 requirement
'handles errors properly' is not testable. Suggestion: Replace
with 'Raises ValueError when input list is empty. Raises
TypeError when candidate lacks quality_metrics attribute. Logs
warning and skips candidate when individual score computation
fails.'"

WRONG: "Need more test coverage."
RIGHT: "acceptance_criteria_clarity: task-02-03 requirement 2
('Deterministic tie-breaking by candidate ID') has no test
expectation covering it. Suggestion: Add test expectation
te-02-03-03 with description 'Two candidates with equal scores
are ordered by ID' and requirement_refs ['1']."
```
