## Your PTC Packages (Auditor)

Metrics: radon, cognitive-complexity, vulture, cohesion
Coverage: coverage, pytest-cov, diff-cover
Security: bandit
Diffs: unidiff, pydriller, wily
Graphs: networkx
Tokens: tiktoken

**Pre-installed in `ptc-auditor:latest` image.** No pip install delay.

### Hardening Packages (On-Demand)

Installed via runtime pip install only in HARDENING mode:

| Package | Size | When |
|---------|------|------|
| semgrep | ~55-60 MB | Final security/correctness audit (5000+ rules) |
| pip-audit | ~200 KB | Dependency vulnerability check |
| scalene | ~5-10 MB | Combined CPU+memory profiler for integration tests |

## When to Use PTC (Auditor)

- **Task audit (after every coder claims done):**
  1. unidiff parses git diff -- scope violation check against plan
  2. diff-cover -- "what % of new code is tested?" (highest-value audit metric)
  3. cognitive-complexity -- complexity regression detection
  4. ast scan test files -- detect fraudulent tests (zero assertions)
  One ptc_execute, ~400 tokens returned, replaces 5-20K tokens of manual reading.

- **Phase audit:**
  Full coverage report + pydriller churn analysis + vulture dead code scan.

- **Hardening (final audit):**
  Runtime install semgrep + pip-audit. Comprehensive security + dependency scan.
  Takes ~30s install, runs once per project.

- **Arbitration:**
  coverage + hypothesis for independent property-based verification.
  Neither tester's nor coder's tests -- auditor's own.

### Recipe: Task Audit (Mandatory)

```python
import ast, json, subprocess
from unidiff import PatchSet
from cognitive_complexity.api import get_cognitive_complexity

# 1. Scope check
diff = subprocess.run(["git", "diff", "--cached"], capture_output=True, text=True, cwd="/workspace")
patch = PatchSet(diff.stdout)
modified = [f.path for f in patch]

# 2. Diff coverage
subprocess.run(["pytest", "--cov=src", "--cov-report=xml"], cwd="/workspace", capture_output=True)
dc = subprocess.run(["diff-cover", "coverage.xml", "--compare-branch=main"], capture_output=True, text=True, cwd="/workspace")

# 3. Complexity regression
complex_funcs = []
for f in patch:
    if f.path.endswith(".py"):
        try:
            with open(f"/workspace/{f.path}") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    cog = get_cognitive_complexity(node)
                    if cog > 10:
                        complex_funcs.append({"file": f.path, "func": node.name, "cognitive": cog})
        except Exception:
            pass

results = {"modified": modified, "diff_cover": dc.stdout[:500], "complex_functions": complex_funcs}
print(json.dumps(results))
```

### Recipe: Dead Code Detection

```python
import json, subprocess

result = subprocess.run(
    ["python", "-m", "vulture", "src/", "--min-confidence", "80"],
    capture_output=True, text=True, cwd="/workspace"
)
lines = result.stdout.strip().splitlines()
print(json.dumps({"dead_code_items": len(lines), "details": lines[:20]}))
```

## Mandatory PTC Checks (Always Run)

Task-level: scope check + diff-cover + complexity regression + test assertion count.
These are not optional. Data-backed verdicts, not opinions.
