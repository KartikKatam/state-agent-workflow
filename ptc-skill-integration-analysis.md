# PTC Package Integration: Per-Agent, Per-Skill Analysis

## How To Read This Document

For each agent, I show:
1. **What they do today** (current tool-calling pattern, token cost)
2. **What PTC changes** (specific packages, specific code patterns)
3. **Which skills need updating** (with concrete before/after examples)
4. **Impact rating** (token savings + accuracy improvement)

---

## Agent 1: CODEBASE-EXPLORER (Sonnet)

### Current Behavior
Explorer reads files one-by-one via the Read tool. Every file's content enters the agent's context window. For a 200-file codebase, that's ~400KB of raw text (~100K tokens) consumed just to produce a 5KB context packet.

**Current tool call pattern (from exploration-modes skill):**
```
Read("src/main.py")           → 3KB in context
Read("src/auth.py")           → 4KB in context
Read("src/models.py")         → 5KB in context
... × 50 files ...
Grep("class ", "**/*.py")     → 2KB in context
Grep("import ", "**/*.py")    → 3KB in context
```
Total: ~150KB in context. Agent reasons about all of it, writes JSON context packet.

### With PTC
Explorer sends ONE `ptc_execute` call containing a Python script that:
1. Reads all files (raw content stays in container)
2. Parses each with `ast` or `tree-sitter`
3. Builds dependency graph with `networkx`
4. Computes quality metrics with `radon`
5. Detects dead code with `vulture`
6. Tabulates results with `pandas`
7. Prints structured JSON summary (~2KB)

**PTC code pattern:**
```python
import ast, json, os
import networkx as nx
from radon.complexity import cc_visit
from radon.metrics import mi_visit

# Read all Python files (stays in container memory)
files = {}
for root, _, fnames in os.walk("/workspace/src"):
    for f in fnames:
        if f.endswith(".py"):
            path = os.path.join(root, f)
            with open(path) as fh:
                files[path] = fh.read()

# Build dependency graph
G = nx.DiGraph()
for path, content in files.items():
    tree = ast.parse(content)
    module = path.replace("/workspace/", "").replace("/", ".").replace(".py", "")
    G.add_node(module)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            G.add_edge(module, node.module)

# Compute metrics
metrics = {}
for path, content in files.items():
    module = path.replace("/workspace/", "")
    complexity = cc_visit(content)
    avg_cc = sum(b.complexity for b in complexity) / max(len(complexity), 1)
    metrics[module] = {
        "classes": len([n for n in ast.walk(ast.parse(content)) if isinstance(n, ast.ClassDef)]),
        "functions": len([n for n in ast.walk(ast.parse(content)) if isinstance(n, ast.FunctionDef)]),
        "lines": len(content.split("\n")),
        "avg_complexity": round(avg_cc, 1),
        "maintainability": round(mi_visit(content, True), 1),
    }

# Find central modules
central = nx.pagerank(G)
top_modules = sorted(central.items(), key=lambda x: -x[1])[:10]

# Circular dependencies
cycles = list(nx.simple_cycles(G))

print(json.dumps({
    "files_analyzed": len(files),
    "total_lines": sum(m["lines"] for m in metrics.values()),
    "top_modules": [{"module": m, "centrality": round(c, 3)} for m, c in top_modules],
    "circular_deps": cycles[:5],
    "highest_complexity": sorted(metrics.items(), key=lambda x: -x[1]["avg_complexity"])[:10],
    "summary_metrics": metrics,
}, indent=2))
```

**Token comparison:**
| Metric | Without PTC | With PTC |
|--------|-------------|----------|
| Context consumed | ~100K tokens | ~500 tokens |
| Round trips | 50+ | 1 |
| Quality of output | String matching | AST-parsed, graph-analyzed, metrics-computed |

### Skills To Update

**context-packets** (currently ~160 lines) — Add PTC patterns section:
- "When PTC is available, use `ptc_execute` for batch file analysis"
- Replace the current "read files individually" patterns with ptc_execute code templates
- Add package-specific recipes: ast for structure, networkx for dependencies, radon for quality, vulture for dead code

**exploration-modes** (currently ~380 lines) — Major rewrite of all 4 modes:
- **Mode 1 (Full Analysis)**: Single ptc_execute call replaces 7-step sequential process. Packages: ast, networkx, radon, vulture, pandas
- **Mode 2 (Incremental)**: ptc_execute reads only changed files + dependents (networkx neighbor lookup), recomputes affected metrics
- **Mode 3 (Query Response)**: ptc_execute with targeted analysis (e.g., "find all callers of function X" via ast.walk + networkx)
- **Mode 4 (Feature Exploration)**: ptc_execute traces all touchpoints from design doc entities through dependency graph

**git-history-analysis** (currently ~370 lines) — Add PTC patterns:
- Parse `git log --format=json` output with pandas (commit frequency, contributor heatmap, file churn)
- Build contributor-file matrix to identify domain experts
- Currently runs many `git log` commands and reads output into context

### Impact: CRITICAL (93% token reduction, significantly richer output)

---

## Agent 2: CHUNK-CODER (Opus)

### Current Behavior
Coder follows a strict TDD cycle. Each phase is a separate tool call:
1. Read plan JSON (Read tool) — plan enters context
2. Write test file (Write tool)
3. Run tests to verify failure (Bash → pytest) — full output enters context
4. Write implementation (Write tool)
5. Run tests to verify passing (Bash → pytest) — full output enters context again
6. Run quality gate (Bash → gate.sh) — ruff + pyright + pytest output enters context
7. Check invariants (Bash)

Each test run dumps full pytest output (~3K tokens). Quality gate dumps ruff + pyright output (~2K tokens). Over a typical chunk with 3-4 RED/GREEN cycles, that's ~20K tokens of tool output.

### With PTC
The entire TDD cycle can run as batched operations within ptc_execute:

```python
import json, subprocess

# Run tests and extract structured results
def run_tests(test_path):
    proc = subprocess.run(
        ["python3", "-m", "pytest", test_path, "--tb=short", "-q"],
        capture_output=True, text=True, cwd="/workspace", timeout=30
    )
    output = proc.stdout + proc.stderr
    # Parse structured results (only summary returns to agent)
    import re
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", output)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", output)) else 0
    failures = []
    for fm in re.finditer(r"FAILED (\S+)::(\S+)", output):
        failures.append({"file": fm.group(1), "test": fm.group(2)})
    return {"passed": passed, "failed": failed, "failures": failures}

# RED phase: verify tests fail
red = run_tests("tests/test_new_feature.py")
assert red["failed"] > 0, "Tests must fail before implementation"

# GREEN phase: after implementation, verify tests pass
green = run_tests("tests/test_new_feature.py")

# Quality gate: format + lint + typecheck
fmt = subprocess.run(["ruff", "format", "--check", "."], capture_output=True, text=True, cwd="/workspace")
lint = subprocess.run(["ruff", "check", "."], capture_output=True, text=True, cwd="/workspace")

print(json.dumps({
    "red_phase": {"status": "pass" if red["failed"] > 0 else "FAIL", "failures": red["failures"]},
    "green_phase": {"status": "pass" if green["failed"] == 0 else "FAIL", "passed": green["passed"], "failures": green["failures"]},
    "format": {"clean": fmt.returncode == 0},
    "lint": {"clean": lint.returncode == 0, "issues": lint.stdout[:500] if lint.returncode != 0 else ""},
}))
```

**But note:** The coder still needs to WRITE the test and implementation code via Write/Edit tools (those go to the host filesystem). PTC helps most with the **verification steps** (running tests, quality gate, invariant checks) — not the code writing itself.

**Where PTC really shines for the coder:**

1. **Pre-implementation analysis** — Before writing code, analyze the target module:
```python
import ast, json
data = await read_file(path="src/auth.py")
tree = ast.parse(data["content"])
# Extract all function signatures, class hierarchies, existing patterns
functions = []
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef):
        args = [a.arg for a in node.args.args]
        functions.append({"name": node.name, "args": args, "line": node.lineno})
print(json.dumps({"functions": functions}))
```

2. **Test result analysis** — Parse test output for patterns:
```python
import json
result = await run_tests(test_path="tests/")
# Analyze coverage if available
cov = await coverage_summary(test_path="tests/", source_dirs="src/")
print(json.dumps({
    "tests": result,
    "coverage": cov,
    "uncovered_lines": cov.get("uncovered", [])[:20]
}))
```

3. **Plan adherence verification** — Check scope violations automatically:
```python
import ast, json
# Get plan scope
plan = await read_file(path=".claude/plans/feature-plan.json")
plan_data = json.loads(plan["content"])
allowed_files = set()
for chunk in plan_data["chunks"]:
    allowed_files.update(chunk.get("scope", {}).get("touched_files", []))

# Check what we actually modified
diff = await git_diff_summary(base_branch="main")
modified = set(c["file"] for c in diff.get("changes", []))

scope_violations = modified - allowed_files
print(json.dumps({
    "scope_check": "pass" if not scope_violations else "FAIL",
    "violations": list(scope_violations),
    "allowed": list(allowed_files),
    "modified": list(modified),
}))
```

### Skills To Update

**tdd-workflow** (currently ~580 lines) — Add PTC verification patterns:
- Replace "Run pytest via Bash, read full output" with ptc_execute patterns that return structured results
- Add pre-implementation analysis pattern (ast-parse target module before writing)
- Add quality gate batching (format + lint + typecheck in one call)
- Add coverage analysis pattern (identify untested code paths)

**plan-adherence** (currently ~230 lines) — Add PTC scope verification:
- Automated scope checking via ptc_execute (compare git diff against plan scope)
- Function-level change detection via ast comparison
- Deviation quantification (lines added outside scope)

### Impact: HIGH (42-60% token reduction on verification steps, significantly better scope checking)

---

## Agent 3: PLAN-ARCHITECT (Opus)

### Current Behavior
Planner reads context packets + design docs, reasons about chunking. Most of the work is reasoning, not data processing. But there are data-heavy operations:
- Reading and cross-referencing multiple files to validate chunk boundaries
- Verifying that every design doc concept maps to codebase constructs
- Computing dependency graphs to determine chunk ordering

### With PTC
PTC helps the planner with **validation and analysis**, not with the planning itself:

1. **Design grounding verification** — Check that design doc entities exist in codebase:
```python
import ast, json

# Read design doc entities (passed as param)
design_entities = ["AuthService", "TokenValidator", "RateLimiter"]

# Search codebase for each entity
found = {}
missing = []
for root, _, files in __import__("os").walk("/workspace/src"):
    for f in files:
        if not f.endswith(".py"): continue
        path = __import__("os").path.join(root, f)
        with open(path) as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                if node.name in design_entities:
                    found[node.name] = f"{path}:{node.lineno}"

missing = [e for e in design_entities if e not in found]
print(json.dumps({"found": found, "missing": missing, "coverage": f"{len(found)}/{len(design_entities)}"}))
```

2. **Chunk dependency analysis** — Verify chunk ordering is valid:
```python
import ast, json
import networkx as nx

# Build import graph from files in each chunk
chunks = {"chunk-01": ["src/auth.py", "src/tokens.py"], "chunk-02": ["src/api.py", "src/routes.py"]}

G = nx.DiGraph()
file_to_chunk = {}
for chunk_id, files in chunks.items():
    for f in files:
        file_to_chunk[f] = chunk_id

for f, content_data in [(f, open(f"/workspace/{f}").read()) for chunk_files in chunks.values() for f in chunk_files]:
    tree = ast.parse(content_data)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            # Map import to file
            import_file = node.module.replace(".", "/") + ".py"
            if import_file in file_to_chunk and f in file_to_chunk:
                if file_to_chunk[f] != file_to_chunk[import_file]:
                    G.add_edge(file_to_chunk[f], file_to_chunk[import_file])

# Check for cycles (invalid ordering)
cycles = list(nx.simple_cycles(G))
topo_order = list(nx.topological_sort(G)) if not cycles else []
print(json.dumps({"valid_ordering": len(cycles) == 0, "cycles": cycles, "suggested_order": topo_order}))
```

3. **Complexity estimation** — Estimate effort per chunk:
```python
import json
from radon.complexity import cc_visit
from radon.metrics import mi_visit

chunks = {"chunk-01": ["src/auth.py"], "chunk-02": ["src/api.py"]}
estimates = {}
for chunk_id, files in chunks.items():
    total_cc = 0
    total_lines = 0
    for f in files:
        with open(f"/workspace/{f}") as fh:
            content = fh.read()
        blocks = cc_visit(content)
        total_cc += sum(b.complexity for b in blocks)
        total_lines += len(content.split("\n"))
    estimates[chunk_id] = {
        "total_complexity": total_cc,
        "total_lines": total_lines,
        "estimated_difficulty": "high" if total_cc > 20 else "medium" if total_cc > 10 else "low"
    }
print(json.dumps(estimates))
```

### Skills To Update

**implementation-plans** (currently ~375 lines) — Add PTC validation patterns:
- Design grounding: automated entity-to-codebase mapping
- Chunk dependency validation: networkx cycle detection
- Complexity estimation: radon metrics per chunk
- Scope completeness: verify all files mentioned in design doc are covered

**test-architecture** (currently ~670 lines) — Add PTC analysis for test design:
- Identify highest-complexity functions (radon) → prioritize for testing
- Find uncovered code paths (coverage + ast)
- Compute call graph to identify integration test boundaries (networkx)

### Impact: MODERATE (30% token reduction, much better validation accuracy)

---

## Agent 4: RESEARCHER (Sonnet)

### Current Behavior
Researcher uses Context7 MCP (primary) and WebSearch (fallback). MCP results enter context directly. The researcher synthesizes and writes to `.claude/research/`.

### With PTC
Research is the **least impacted** by PTC because:
- Context7 already returns structured summaries
- WebSearch already returns structured results
- The main work is reasoning/synthesis, not data processing

**Where PTC helps:**

1. **Deduplication and comparison** — When researching multiple libraries:
```python
import json, pandas as pd

# Compare findings across sources (passed as tool results)
sources = [
    await read_file(path=".claude/research/library-a.json"),
    await read_file(path=".claude/research/library-b.json"),
]

# Tabulate comparison
rows = []
for s in sources:
    data = json.loads(s["content"])
    for entry in data.get("entries", []):
        rows.append({"library": data["name"], "feature": entry["feature"], "supported": entry["supported"]})

df = pd.DataFrame(rows)
comparison = df.pivot_table(index="feature", columns="library", values="supported", aggfunc="first")
print(comparison.to_markdown())
```

2. **Existing research analysis** — Check what research already exists:
```python
import json, os

index_data = await read_file(path=".claude/research/_index.json")
index = json.loads(index_data["content"])

# Find all research related to a topic
topic = "authentication"
matches = [e for e in index["entries"] if topic.lower() in e.get("topic", "").lower()]
print(json.dumps({"existing_research": len(matches), "entries": matches[:5]}))
```

### Skills To Update

**research-workflow** (currently ~350 lines) — Minor additions:
- Add PTC deduplication pattern for multi-source research
- Add PTC pattern for analyzing existing research index

**persistent-research** and **ephemeral-research** — No changes needed (these are about research methodology, not data processing)

### Impact: LOW (10-15% token reduction, marginal accuracy improvement)

---

## Agent 5: SCRIBE (Haiku)

### Current Behavior
Scribe reads output files, writes session logs, runs git commands, extracts learning memories. Operations are relatively simple and sequential.

### With PTC
Scribe benefits from PTC for **log analysis and commit preparation**:

1. **Session log analysis** — Summarize a session for commit message:
```python
import json

log = await read_file(path=".claude/logs/feature-chunk01-log.json")
log_data = json.loads(log["content"])

# Extract key information
phases = log_data.get("phase_history", [])
tests = log_data.get("test_results", {})
deviations = log_data.get("deviations", [])
learning = log_data.get("learning_signals", [])

print(json.dumps({
    "phases_completed": len(phases),
    "tests_passed": tests.get("passed", 0),
    "tests_failed": tests.get("failed", 0),
    "deviations": len(deviations),
    "learning_signals": len(learning),
    "files_modified": log_data.get("files_modified", []),
    "summary": f"Implemented {log_data.get('chunk_id', '?')}: {len(phases)} phases, {tests.get('passed', 0)} tests passing"
}))
```

2. **Git diff analysis for commit** — Generate structured change summary:
```python
import json
diff = await git_diff_summary(base_branch="main")
changes = await list_changes(since_ref="HEAD~1")
print(json.dumps({
    "files_changed": diff.get("files_changed", 0),
    "insertions": diff.get("insertions", 0),
    "deletions": diff.get("deletions", 0),
    "change_summary": diff.get("changes", [])[:10],
}))
```

3. **Memory pattern extraction** — Analyze learning signals:
```python
import json

# Read all session logs with learning signals
logs = []
for f in await list_dir(path=".claude/logs/"):
    if "log.json" in f.get("name", ""):
        data = await read_file(path=f".claude/logs/{f['name']}")
        log = json.loads(data["content"])
        if log.get("learning_signals"):
            logs.append(log)

# Extract and deduplicate patterns
patterns = {}
for log in logs:
    for signal in log["learning_signals"]:
        key = signal.get("type", "") + ":" + signal.get("description", "")[:50]
        if key not in patterns:
            patterns[key] = {"count": 0, "examples": []}
        patterns[key]["count"] += 1
        patterns[key]["examples"].append(signal.get("context", "")[:100])

# Only patterns with frequency >= 3
significant = {k: v for k, v in patterns.items() if v["count"] >= 3}
print(json.dumps({"total_signals": sum(p["count"] for p in patterns.values()), "significant_patterns": significant}))
```

### Skills To Update

**logging-and-commit** (currently ~190 lines) — Add PTC patterns:
- Session log summarization for commit messages
- Git diff analysis for structured change descriptions
- Batch log + stage + verify in one call

**coding-memory** (currently ~380 lines) — Add PTC patterns:
- Batch pattern extraction across all session logs
- Deduplication and confidence computation
- Promotion threshold checking

### Impact: MODERATE (30-40% token reduction, better pattern extraction)

---

## Agent 6: TESTER (Planned — v2)

### Current Behavior
Not yet implemented. Design doc says: blind scenario tester, writes tests from specs without seeing implementation.

### With PTC
PTC is **transformative** for the tester:

1. **Comprehensive test suite analysis**:
```python
import json, subprocess, coverage
import pandas as pd

# Run full test suite with coverage
result = await run_tests(test_path="tests/")
cov = await coverage_summary(test_path="tests/", source_dirs="src/")

# Analyze which functions are untested
import ast
for f in cov.get("uncovered_files", []):
    data = await read_file(path=f)
    tree = ast.parse(data["content"])
    uncovered_funcs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.lineno in cov.get("uncovered_lines", {}).get(f, []):
                uncovered_funcs.append({"name": node.name, "line": node.lineno})
    if uncovered_funcs:
        print(f"Uncovered in {f}: {json.dumps(uncovered_funcs)}")
```

2. **Property-based test generation**:
```python
from hypothesis import given, strategies as st
import json

# Agent can USE hypothesis to generate edge cases
# and report which ones fail
@given(st.integers(min_value=-1000, max_value=1000))
def test_validate_input(x):
    # Test the validation function
    assert validate_input(x) == (x >= 0)

# Run and collect failures
try:
    test_validate_input()
    print(json.dumps({"property_test": "passed"}))
except AssertionError as e:
    print(json.dumps({"property_test": "failed", "counterexample": str(e)}))
```

3. **Test flakiness detection**:
```python
import json, subprocess

# Run tests 5 times, detect flaky ones
results = []
for i in range(5):
    r = await run_tests(test_path="tests/")
    results.append(r)

# Find tests that sometimes pass, sometimes fail
all_tests = set()
for r in results:
    for f in r.get("failures", []):
        all_tests.add(f["test"])

flaky = []
for test in all_tests:
    fail_count = sum(1 for r in results if any(f["test"] == test for f in r.get("failures", [])))
    if 0 < fail_count < 5:
        flaky.append({"test": test, "fail_rate": f"{fail_count}/5"})

print(json.dumps({"flaky_tests": flaky, "total_runs": 5}))
```

### Skills To Create

**scenario-testing** (new v2 skill) — Built with PTC from the start:
- Coverage gap analysis via ptc_execute
- Property-based test generation patterns (hypothesis)
- Flakiness detection via repeated runs
- Test performance profiling

### Impact: TRANSFORMATIVE (tester barely functions without PTC; with PTC it becomes a powerful analysis engine)

---

## Agent 7: AUDITOR (Planned — v2)

### Current Behavior
Not yet implemented. Design doc says: adversarial reviewer with 4 modes (task, phase, hardening, arbitration).

### With PTC
PTC makes the auditor **genuinely adversarial** rather than just reading diffs:

1. **Scope violation detection** (task audit):
```python
import ast, json
import networkx as nx

plan = json.loads((await read_file(path=".claude/plans/feature-plan.json"))["content"])
diff = await git_diff_summary(base_branch="main")

# Check every modified function against plan scope
violations = []
for change in diff.get("changes", []):
    if change["file"].endswith(".py"):
        data = await read_file(path=change["file"])
        tree = ast.parse(data["content"])
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                # Check if this function is in the plan's scope
                in_scope = any(
                    change["file"] in chunk.get("scope", {}).get("touched_files", [])
                    for chunk in plan.get("chunks", [])
                )
                if not in_scope:
                    violations.append({"file": change["file"], "function": node.name, "line": node.lineno})

print(json.dumps({"scope_violations": violations, "clean": len(violations) == 0}))
```

2. **Complexity regression detection** (phase audit):
```python
import json
from radon.complexity import cc_visit

# Compare complexity before and after changes
diff = await git_diff_summary(base_branch="main")
regressions = []
for change in diff.get("changes", []):
    if not change["file"].endswith(".py"): continue

    current = await read_file(path=change["file"])
    current_cc = cc_visit(current["content"])
    avg_current = sum(b.complexity for b in current_cc) / max(len(current_cc), 1)

    # Compare against threshold
    if avg_current > 10:
        regressions.append({
            "file": change["file"],
            "avg_complexity": round(avg_current, 1),
            "high_complexity_functions": [
                {"name": b.name, "complexity": b.complexity}
                for b in current_cc if b.complexity > 10
            ]
        })

print(json.dumps({"complexity_regressions": regressions}))
```

3. **Dead code detection** (hardening audit):
```python
import json, subprocess

result = subprocess.run(
    ["python3", "-m", "vulture", "/workspace/src/", "--min-confidence", "80"],
    capture_output=True, text=True, timeout=30
)

dead_code = []
for line in result.stdout.strip().split("\n"):
    if line.strip():
        dead_code.append(line.strip())

print(json.dumps({"dead_code_items": len(dead_code), "items": dead_code[:20]}))
```

### Skills To Create

**code-review** (new v2 skill) — Built with PTC from the start:
- Automated scope verification via ast + plan comparison
- Complexity regression detection via radon before/after
- Dead code detection via vulture
- Dependency impact analysis via networkx blast radius
- Test coverage gap detection for changed code

### Impact: TRANSFORMATIVE (auditor without PTC is a glorified diff reader; with PTC it's a real static analysis engine)

---

## Summary: Package-to-Skill Mapping

### Tier 1 Packages (Every Agent Benefits)

| Package | Primary Agent | Skill | Specific Use |
|---------|--------------|-------|-------------|
| `ast` | Explorer, Coder, Auditor | context-packets, plan-adherence, code-review | Parse Python code structure, extract signatures, detect scope violations |
| `json` (stdlib) | All | All | Already used everywhere |

### Tier 2 Packages (Role-Specific, High Impact)

| Package | Primary Agent | Skill | Specific Use |
|---------|--------------|-------|-------------|
| `networkx` | Explorer, Planner, Auditor | context-packets, implementation-plans, code-review | Dependency graphs, cycle detection, centrality, blast radius |
| `radon` | Explorer, Planner, Auditor | context-packets, implementation-plans, code-review | Complexity metrics, maintainability index, effort estimation |
| `vulture` | Explorer, Auditor | context-packets, code-review | Dead code detection |
| `pandas` | Explorer, Researcher, Scribe | context-packets, research-workflow, coding-memory | Tabulation, comparison tables, log analysis |
| `tree-sitter` | Explorer | context-packets | Multi-language AST parsing |
| `hypothesis` | Tester | scenario-testing | Property-based test generation |
| `coverage` | Coder, Tester | tdd-workflow, scenario-testing | Coverage gap analysis |
| `pygments` | Explorer | context-packets | Syntax-aware tokenization |

### Tier 3 Packages (Domain-Specific)

| Package | Domain | Skill Specialization |
|---------|--------|---------------------|
| `opencv-python-headless` | robotics-cv | test-architecture/specializations/robotics-cv |
| `scikit-image` | robotics-cv | test-architecture/specializations/robotics-cv |
| `scipy` | robotics-cv | test-architecture/specializations/robotics-cv |
| `torch`, `torchvision` | robotics-cv | test-architecture/specializations/robotics-cv |

### Tier 4 Packages (Utility, All Agents)

| Package | Use |
|---------|-----|
| `black`, `ruff` | In-container code formatting/linting verification |
| `pyyaml`, `toml` | Config file parsing |
| `jinja2` | Template-driven code generation |
| `pillow` | Image metadata for robotics-cv |

---

## Implementation Plan for Skill Updates

### Phase 1: Core PTC Skill (new)
Create `skills/ptc-sandbox/SKILL.md` — the foundation all other skills reference.
- Available tools (await functions)
- Available packages (per-tier listing)
- Code patterns (single tool, parallel, batch analysis)
- Anti-patterns
- Domain specializations

### Phase 2: Explorer Skills (highest impact)
Update `context-packets` and `exploration-modes`:
- Add PTC code templates for all 4 exploration modes
- Replace "read files individually" with batch analysis
- Add package-specific recipes (ast, networkx, radon, vulture)

### Phase 3: Coder Skills (second highest impact)
Update `tdd-workflow` and `plan-adherence`:
- Add PTC verification patterns (structured test results, scope checking)
- Add pre-implementation analysis patterns
- Add coverage analysis patterns

### Phase 4: New Agent Skills (v2)
Create `scenario-testing` (tester) and `code-review` (auditor):
- Built with PTC from the start
- hypothesis for property testing
- radon for complexity auditing
- vulture for dead code detection

### Phase 5: Supporting Skills
Update `logging-and-commit`, `coding-memory`, `research-workflow`:
- Add PTC patterns where beneficial
- Lower priority — these are already efficient

---

## Token Impact Summary

| Agent | Current Overhead | With PTC | Reduction | Accuracy Impact |
|-------|-----------------|----------|-----------|-----------------|
| Explorer | ~100K tokens/exploration | ~5K tokens | **95%** | AST-parsed vs string-matched |
| Coder | ~20K tokens/chunk (verification) | ~3K tokens | **85%** | Structured results vs raw output |
| Planner | ~15K tokens/planning | ~10K tokens | **33%** | Validated dependencies vs assumed |
| Researcher | ~8K tokens/research | ~7K tokens | **12%** | Marginally better dedup |
| Scribe | ~5K tokens/commit | ~3K tokens | **40%** | Better pattern extraction |
| Tester (v2) | N/A (new) | ~3K tokens | **N/A** | Wouldn't exist without PTC |
| Auditor (v2) | N/A (new) | ~4K tokens | **N/A** | Wouldn't exist without PTC |

**Aggregate session savings** (typical: 1 explorer + 1 planner + 2 coders + 1 scribe):
- Without PTC: ~160K tokens on raw reads + verification
- With PTC: ~24K tokens
- **85% reduction across a full workflow session**
