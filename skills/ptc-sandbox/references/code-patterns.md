# Code Patterns

## Reading Files

```python
# Single file
with open("/workspace/src/main.py") as f:
    content = f.read()
print(len(content.splitlines()))

# Multiple files
import os, json

file_data = {}
for root, _, files in os.walk("/workspace/src"):
    for f in files:
        if f.endswith(".py"):
            path = os.path.join(root, f)
            with open(path) as fh:
                file_data[path] = fh.read()
print(json.dumps({"files_read": len(file_data)}))
```

## Writing Files

```python
import json

results = {"analysis": "complete", "issues": []}
with open("/workspace/output/report.json", "w") as f:
    json.dump(results, f, indent=2)
print("Report written to /workspace/output/report.json")
```

## HTTP Requests

```python
import requests, json

resp = requests.get("https://api.example.com/data", timeout=10)
data = resp.json()
print(json.dumps({"status": resp.status_code, "count": len(data)}))
```

## Shell / Git Commands

```python
import subprocess, json

# Git log
result = subprocess.run(
    ["git", "log", "--oneline", "-20"],
    capture_output=True, text=True, cwd="/workspace"
)
print(result.stdout)

# Git diff
diff = subprocess.run(
    ["git", "diff", "--cached"],
    capture_output=True, text=True, cwd="/workspace"
)
print(json.dumps({"diff_lines": len(diff.stdout.splitlines())}))
```

## Parallel Operations

Use threads or asyncio for concurrent I/O:

```python
import json
from concurrent.futures import ThreadPoolExecutor

def analyze_file(path):
    import ast
    with open(path) as f:
        tree = ast.parse(f.read())
    return {
        "classes": len([n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]),
        "functions": len([n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]),
    }

import os
py_files = []
for root, _, files in os.walk("/workspace/src"):
    for f in files:
        if f.endswith(".py"):
            py_files.append(os.path.join(root, f))

with ThreadPoolExecutor(max_workers=4) as pool:
    results = dict(zip(py_files, pool.map(analyze_file, py_files)))

print(json.dumps({"files_analyzed": len(results), "results": results}))
```

## Error Handling

```python
import json, os

paths = ["src/a.py", "src/b.py", "missing.py"]
results = {}
errors = {}
for p in paths:
    full = f"/workspace/{p}"
    if os.path.exists(full):
        with open(full) as f:
            results[p] = len(f.read().splitlines())
    else:
        errors[p] = "File not found"
print(json.dumps({"results": results, "errors": errors}))
```

## Loops with Analysis

When processing many files, use a loop inside one ptc_execute call:

```python
import ast, json, os

summaries = {}
for root, dirs, files in os.walk("/workspace/src"):
    for f in files:
        if f.endswith(".py"):
            path = os.path.join(root, f)
            with open(path) as fh:
                tree = ast.parse(fh.read())
            classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
            summaries[path] = {"classes": classes, "lines": len(open(path).readlines())}

print(json.dumps({"files_analyzed": len(summaries), "summaries": summaries}))
```

## Timeout Awareness

Long-running operations should be broken into multiple ptc_execute calls:

```python
# BAD: One call that might timeout
# for i in range(10000): heavy_analysis(files[i])

# GOOD: Process in batches, each within timeout
batch = files[0:100]
results = {}
for f in batch:
    # ... process ...
    pass
print(json.dumps(results))
# Next ptc_execute call handles files[100:200], etc.
```

## State Persistence Across Calls

Variables persist in the container namespace between ptc_execute calls:

```python
# Call 1: Build data
import os
py_files = []
for root, _, files in os.walk("/workspace/src"):
    for f in files:
        if f.endswith(".py"):
            py_files.append(os.path.join(root, f))
print(f"Found {len(py_files)} files")

# Call 2: Use py_files from previous call
import ast, json
results = {}
for path in py_files[:50]:
    with open(path) as f:
        tree = ast.parse(f.read())
    results[path] = len([n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)])
print(json.dumps(results))
```
