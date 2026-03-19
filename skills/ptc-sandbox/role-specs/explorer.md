## Your PTC Packages (Explorer)

Code analysis: ast (stdlib), tree-sitter, tree-sitter-python, tree-sitter-typescript, ast-grep-py, jedi, pyan3
Metrics: radon, vulture, cognitive-complexity, lizard, cohesion
Graphs: networkx
Data: pandas
Git: gitpython, pydriller
Tokens: tiktoken
HTTP: requests

**Pre-installed in `ptc-explorer:latest` image.** No pip install delay.

## When to Use PTC (Explorer)

- **Full codebase analysis:** One ptc_execute reads ALL files with `open()` + `os.walk()`,
  parses with ast/tree-sitter, builds dependency graph with networkx, computes metrics
  with radon, prints structured JSON. Replaces 50+ individual Read calls.
- **Surgical query:** "What does function X call?" -- jedi for type inference, pyan3 for
  call graph, ast for signature extraction.
- **Git history:** pydriller for churn analysis, gitpython for blame-based ownership.
- **Token budget:** tiktoken to measure context packet size before delivery.
- **Web data:** requests for fetching remote resources (bridge networking enabled).

### Recipe: Full Module Analysis

```python
import ast, json, os

modules = {}
for root, dirs, files in os.walk("/workspace/src"):
    for f in files:
        if f.endswith(".py"):
            path = os.path.join(root, f)
            with open(path) as fh:
                tree = ast.parse(fh.read())
            classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
            funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
            modules[path] = {"classes": classes, "functions": funcs}
print(json.dumps({"modules": len(modules), "summary": modules}))
```

### Recipe: Dependency Graph

```python
import ast, json, networkx as nx, os

G = nx.DiGraph()
for root, _, files in os.walk("/workspace/src"):
    for f in files:
        if f.endswith(".py"):
            path = os.path.join(root, f)
            with open(path) as fh:
                tree = ast.parse(fh.read())
            mod_name = os.path.relpath(path, "/workspace").replace("/", ".").removesuffix(".py")
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        G.add_edge(mod_name, alias.name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    G.add_edge(mod_name, node.module)
cycles = list(nx.simple_cycles(G))
central = nx.pagerank(G)
top5 = sorted(central, key=central.get, reverse=True)[:5]
print(json.dumps({"cycles": len(cycles), "central_modules": top5}))
```

### Recipe: Git Churn Analysis

```python
import json
from pydriller import Repository

churn = {}
for commit in Repository("/workspace", since_date="2025-01-01").traverse_commits():
    for mod in commit.modified_files:
        if mod.filename.endswith(".py"):
            churn[mod.filename] = churn.get(mod.filename, 0) + 1

top_churn = sorted(churn.items(), key=lambda x: x[1], reverse=True)[:10]
print(json.dumps({"top_churn": top_churn}))
```

## When NOT to Use PTC (Explorer)

- Single file read -- use Read tool
- Simple grep -- use Grep tool
- Listing directory -- use Glob tool

## Self-Serve vs Delegation

You can answer most queries yourself in one ptc_execute call. Sub-agents are needed
only for very large codebases (10K+ files) where parallel analysis is faster.
When delegating: sub-agents share YOUR container (REPL pool).
