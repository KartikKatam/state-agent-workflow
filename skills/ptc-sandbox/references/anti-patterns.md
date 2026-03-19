# PTC Anti-Patterns

## 1. Printing Raw Data

**Problem:** Dumping entire file contents or command outputs into context defeats PTC's purpose.

```python
# BAD: 400KB enters your context window
with open("/workspace/src/models.py") as f:
    print(f.read())

# GOOD: 200 bytes in context
import ast, json
with open("/workspace/src/models.py") as f:
    tree = ast.parse(f.read())
classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
print(json.dumps({"classes": classes, "count": len(classes)}))
```

**Rule:** Process data in the container. Only `print()` the summary you actually need.

## 2. Trying to Use Claude Code Tools Inside PTC

**Problem:** Attempting to call Read, Grep, Glob, or other Claude Code tools from inside the container. These tools don't exist in the PTC environment.

```python
# BAD: Claude Code tools are not available inside PTC
data = await read_file(path="src/main.py")  # NameError
results = await run_tests(test_path="tests/")  # NameError

# GOOD: Use plain Python
with open("/workspace/src/main.py") as f:
    data = f.read()

import subprocess
results = subprocess.run(["pytest", "tests/"], capture_output=True, text=True, cwd="/workspace")
```

**Rule:** Write standard Python. Use `open()` for files, `subprocess` for commands, `requests` for HTTP.

## 3. Repeated Single Calls

**Problem:** Making a separate `ptc_execute` for each file wastes container overhead.

```python
# BAD: 50 ptc_execute calls, 50 container round-trips
for f in files:
    result = ptc_execute(code=f"with open('/workspace/{f}') as fh: print(fh.read())")

# GOOD: One ptc_execute with a loop
code = """
import json, os
results = {}
for path in %s:
    with open(f"/workspace/{path}") as f:
        results[path] = len(f.read().splitlines())
print(json.dumps(results))
""" % repr(files)
```

**Rule:** Batch multiple operations into one ptc_execute call.

## 4. Ignoring Available Packages

**Problem:** Writing manual parsing when packages do it better.

```python
# BAD: Manual regex parsing of Python code
import re
classes = re.findall(r'class (\w+)', code)

# GOOD: Use ast (stdlib) for reliable parsing
import ast
tree = ast.parse(code)
classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
```

**Rule:** Check your role-spec for available packages. Use them.

## 5. Using PTC for Simple Reads

**Problem:** Container overhead for tasks the Read tool handles instantly.

```python
# BAD: Spinning up PTC to read one file and print it
ptc_execute(code="with open('/workspace/README.md') as f: print(f.read())")

# GOOD: Use the Read tool directly (no container, no overhead)
# Read("README.md")
```

**Rule:** PTC is for *compute* — analysis, transformation, aggregation.
Simple reads, greps, and globs have dedicated tools that are faster.

## 6. Exceeding Timeout

**Problem:** One massive ptc_execute call that times out and loses all work.

```python
# BAD: Process entire codebase in one call
for f in all_10000_files:
    heavy_analysis(f)  # timeout after 60s, all work lost

# GOOD: Process in batches
batch = all_10000_files[0:100]
for f in batch:
    results[f] = quick_analysis(f)
print(json.dumps(results))
# Next call: batch = all_10000_files[100:200]
```

**Rule:** Keep each ptc_execute call within the timeout. Break large tasks into batches.

## 7. Spawning Sub-Agents for Mechanical Work

**Problem:** Using sub-agents when a loop in ptc_execute suffices.

```python
# BAD: 5 sub-agents each reading 10 files
for i in range(5):
    Task(prompt=f"Read files {i*10} to {(i+1)*10}")

# GOOD: One ptc_execute with os.walk
code = """
import os, json
results = {}
for root, _, files in os.walk("/workspace/src"):
    for f in files:
        if f.endswith(".py"):
            with open(os.path.join(root, f)) as fh:
                results[f] = len(fh.readlines())
print(json.dumps(results))
"""
```

**Rule:** Delegate only when intermediate LLM reasoning is needed between steps.
