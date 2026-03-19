---
name: ptc-sandbox
description: Execute Python code in persistent sandboxed containers with direct filesystem, network, and shell access
triggers:
  - agent needs to analyze multiple files
  - agent needs to process data and return summaries
  - agent needs to run computations in isolation
loads_role_spec: true
---

# PTC Sandbox

## ptc_execute API

```
ptc_execute(agent_id: str, role: str, code: str, timeout: int = 0) -> str
```

- `code`: Python code to execute. Top-level `await` is supported.
- Returns: JSON with `stdout`, `stderr`, `return_code`, `namespace_keys`.
- Only `print()` output enters your context window. Everything else stays in the container.
- This is the 88.5% token reduction: process data in the container, print only what you need.

## Direct Python (No Internal Tools)

PTC V2 containers have bridge networking and read-write `/workspace` mount. Write plain Python — no `await tool()` pattern.

### Files: open() directly

```python
# Read
with open("/workspace/src/main.py") as f:
    content = f.read()

# Write
with open("/workspace/output.json", "w") as f:
    json.dump(results, f)

# Walk directory tree
import os
for root, dirs, files in os.walk("/workspace/src"):
    for f in files:
        if f.endswith(".py"):
            path = os.path.join(root, f)
            with open(path) as fh:
                code = fh.read()
```

### HTTP: requests library

```python
import requests
resp = requests.get("https://api.example.com/data")
data = resp.json()
print(json.dumps({"status": resp.status_code, "items": len(data)}))
```

### Shell: subprocess

```python
import subprocess
result = subprocess.run(
    ["git", "log", "--oneline", "-20"],
    capture_output=True, text=True, cwd="/workspace"
)
print(result.stdout)
```

### Analysis: installed packages

```python
import ast, networkx as nx, json

# Parse and analyze — packages are pre-installed per role
tree = ast.parse(code)
classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
print(json.dumps({"classes": classes}))
```

## Print Discipline

THE critical skill for token savings:

```python
# GOOD: 400KB stays in container, 200 bytes in context
import ast, json
with open("/workspace/src/models.py") as f:
    tree = ast.parse(f.read())
classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
print(json.dumps({"classes": classes, "count": len(classes)}))

# BAD: 400KB enters context
with open("/workspace/src/models.py") as f:
    print(f.read())  # defeats the purpose of PTC
```

## State Persistence

Variables from previous `ptc_execute` calls survive in the same namespace:

```python
# Call 1:
with open("/workspace/src/main.py") as f:
    data = f.read()

# Call 2:
print(len(data))  # works — same namespace
```

## Error Handling

```python
import json, os

path = "/workspace/nonexistent.py"
if os.path.exists(path):
    with open(path) as f:
        content = f.read()
else:
    print(json.dumps({"error": f"File not found: {path}"}))
```

## Anti-Patterns

- Don't print raw file contents — process first, print summaries
- Don't make separate ptc_execute calls per file — batch with loops
- Don't ignore available packages — use ast, networkx, pandas, etc.
- Don't use PTC for simple single-file reads — use Read tool (faster)
- Don't exceed timeout — break into smaller ptc_execute calls
- Don't try to use Claude Code tools (Read, Grep, Glob) from inside PTC — write plain Python instead

## Packages

Your container has role-specific packages pre-installed in the image.
See your role-spec (auto-loaded from `role-specs/{role}.md`) for the exact list.
