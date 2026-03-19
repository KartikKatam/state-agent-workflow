## Your PTC Packages (Coder)

Profiling: pyinstrument, pympler, line-profiler
Quality: perflint, radon, cognitive-complexity
Testing: coverage, pytest-cov, diff-cover
Security: bandit
Diffs: unidiff
Tokens: tiktoken

**Pre-installed in `ptc-coder:latest` image.** No pip install delay.

## When to Use PTC (Coder)

- **Pre-implementation analysis:** ast-parse the target module to understand existing
  signatures, patterns, and complexity before writing code.
- **Resource efficiency check (after green phase, before quality gate):**
  pyinstrument for hot spots, pympler for memory, cognitive-complexity for readability.
- **Scope verification:** unidiff parses git diff, compare modified files/functions
  against plan's touched_files. Automated plan adherence.
- **Quality gate batching:** Run ruff format + ruff check + pyright in one ptc_execute,
  return structured pass/fail summary instead of raw output.
- **Simple structural queries:** "What are the function signatures in auth.py?" --
  ast-parse directly instead of round-tripping through orchestrator and explorer.

### Recipe: Scope Verification

```python
import json, subprocess
from unidiff import PatchSet

diff = subprocess.run(["git", "diff", "--cached"], capture_output=True, text=True, cwd="/workspace")
patch = PatchSet(diff.stdout)
modified = [f.path for f in patch]
allowed = ["src/auth.py", "src/models.py", "tests/test_auth.py"]
violations = [f for f in modified if f not in allowed]
print(json.dumps({"modified": modified, "violations": violations, "clean": len(violations) == 0}))
```

### Recipe: Complexity Check

```python
import json
from radon.complexity import cc_visit
from cognitive_complexity.api import get_cognitive_complexity
import ast

with open("/workspace/src/auth.py") as f:
    code = f.read()
cc = cc_visit(code)
tree = ast.parse(code)
results = []
for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
    cog = get_cognitive_complexity(func)
    results.append({"name": func.name, "cognitive": cog})
print(json.dumps({"functions": results}))
```

### Recipe: Quality Gate Batch

```python
import json, subprocess

checks = {}
for name, cmd in [
    ("format", ["ruff", "format", "--check", "."]),
    ("lint", ["ruff", "check", "."]),
    ("typecheck", ["pyright"]),
]:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd="/workspace")
    checks[name] = {
        "pass": result.returncode == 0,
        "summary": result.stdout[:300] if result.returncode != 0 else "ok",
    }
print(json.dumps(checks))
```

## When NOT to Use PTC (Coder)

- Writing code -- use Write/Edit tools (host filesystem)
- Complex codebase questions -- message orchestrator, who dispatches explorer
