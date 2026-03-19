## Your PTC Packages (Strategist)

Graphs: networkx
Metrics: radon, cognitive-complexity
Tokens: tiktoken

**Pre-installed in `ptc-strategist:latest` image.** No pip install delay.

## When to Use PTC (Strategist)

- **Plan validation (before shipping):**
  1. networkx checks task dependency DAG for cycles, suggests topological order
  2. ast verifies touched_functions actually exist in the codebase
  3. radon estimates per-task complexity from the files each task touches
  4. tiktoken measures token cost of the plan (it loads into every coder's context)

### Recipe: Plan Validation

```python
import ast, json, networkx as nx, tiktoken, os

# 1. Dependency DAG validation
G = nx.DiGraph()
tasks = [...]  # task list from plan
for t in tasks:
    for dep in t.get("dependencies", []):
        G.add_edge(dep, t["id"])
cycles = list(nx.simple_cycles(G))
order = list(nx.topological_sort(G)) if not cycles else []

# 2. Verify touched files exist
missing = []
for t in tasks:
    for f in t.get("touched_files", []):
        if not os.path.exists(f"/workspace/{f}"):
            missing.append({"task": t["id"], "file": f})

# 3. Token cost
enc = tiktoken.encoding_for_model("gpt-4")
plan_text = json.dumps(tasks)
tokens = len(enc.encode(plan_text))

print(json.dumps({
    "cycles": cycles,
    "topological_order": order,
    "missing_files": missing,
    "plan_tokens": tokens,
    "valid": len(cycles) == 0 and len(missing) == 0,
}))
```

### Recipe: Complexity Estimation per Task

```python
import json, os
from radon.complexity import cc_visit

tasks = [...]  # task list from plan
task_complexity = {}
for t in tasks:
    total_cc = 0
    for f in t.get("touched_files", []):
        path = f"/workspace/{f}"
        if os.path.exists(path) and f.endswith(".py"):
            with open(path) as fh:
                blocks = cc_visit(fh.read())
            total_cc += sum(b.complexity for b in blocks)
    task_complexity[t["id"]] = total_cc

print(json.dumps({"task_complexity": task_complexity}))
```

## The strategist uses PTC the LEAST of any agent.

Planning is reasoning, not computing. PTC helps with validation only.
One ptc_execute call at plan completion. That's typically it.
