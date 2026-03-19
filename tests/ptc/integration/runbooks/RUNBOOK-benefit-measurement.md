# Benefit Measurement Runbook

## Prerequisites

- [ ] All phase runbooks (1–4) completed successfully
- [ ] Resource monitoring data collected (see RUNBOOK-resource-monitoring.md)
- [ ] Python 3.11+ with `tiktoken` installed for token counting (`pip install tiktoken`)
- [ ] MCP tool-use baseline data available (or ability to run MCP comparison tests)
- [ ] Spreadsheet or notebook ready for calculations

## Scenario 1: Token Reduction

Measures how PTC reduces the number of tokens exchanged between the LLM and the execution environment compared to MCP tool-use.

### 1A: Single File Operation

Compare tokens for: "Read a CSV file, compute statistics, return summary."

**MCP Baseline (tool-use):**
```python
#!/usr/bin/env python3
"""Count tokens in MCP tool-use flow for single file operation."""
import tiktoken

enc = tiktoken.encoding_for_model("gpt-4")  # cl100k_base, close to Claude

# MCP flow: tool_use request + tool_result response (repeated per step)
mcp_messages = [
    # Step 1: Read file
    {"role": "assistant", "content": '{"tool": "read_file", "args": {"path": "data.csv"}}'},
    {"role": "tool", "content": "<entire CSV file content returned as string>..."},
    # Step 2: Parse and compute (assistant writes code description)
    {"role": "assistant", "content": "Now I'll compute the mean and std of each column..."},
    # Step 3: Another tool call to write results
    {"role": "assistant", "content": '{"tool": "write_file", "args": {"path": "stats.json", "content": "..."}}'},
    {"role": "tool", "content": "File written successfully."},
]

# Simulate with realistic content sizes
csv_content = "col1,col2,col3\n" + "\n".join([f"{i},{i*2},{i*3}" for i in range(500)])
mcp_messages[1]["content"] = csv_content

mcp_tokens = sum(len(enc.encode(m["content"])) for m in mcp_messages)
print(f"MCP tokens (single file): {mcp_tokens}")
```

**PTC Flow:**
```python
# PTC flow: one code block sent, one result returned
ptc_messages = [
    # Step 1: Send code (all logic in one block)
    {"role": "assistant", "content": """
import pandas as pd
import json

df = pd.read_csv('data.csv')
stats = df.describe().to_dict()
with open('stats.json', 'w') as f:
    json.dump(stats, f)
print(json.dumps({"rows": len(df), "columns": list(df.columns), "summary": "computed"}))
"""},
    # Step 2: Receive compact result
    {"role": "tool", "content": '{"rows": 500, "columns": ["col1","col2","col3"], "summary": "computed"}'},
]

ptc_tokens = sum(len(enc.encode(m["content"])) for m in ptc_messages)
print(f"PTC tokens (single file): {ptc_tokens}")
```

**Calculate reduction:**
```python
token_reduction_pct = (mcp_tokens - ptc_tokens) / mcp_tokens * 100
print(f"Token reduction: {token_reduction_pct:.1f}%")
```

### 1B: Multi-File Operation

Compare tokens for: "Read 5 Python files, find all function definitions, write a summary."

**MCP approach:** 5 read_file tool calls (each returns full file content) + parsing logic in assistant messages + 1 write_file call. Each file content traverses the context window.

**PTC approach:** 1 code block that uses `glob` + `ast` to parse locally, returns only the summary.

```python
# MCP: 5 files * ~200 lines * ~10 tokens/line = ~10,000 tokens for file content alone
# Plus tool call overhead: ~5 * 50 = 250 tokens
# Plus assistant reasoning: ~500 tokens
mcp_tokens_multi = 10000 + 250 + 500  # ≈ 10,750

# PTC: 1 code block (~30 lines * 10 tokens) + compact result (~200 tokens)
# Code: ~300 tokens
# Result: ~200 tokens
ptc_tokens_multi = 300 + 200  # ≈ 500

token_reduction_multi = (mcp_tokens_multi - ptc_tokens_multi) / mcp_tokens_multi * 100
print(f"Multi-file token reduction: {token_reduction_multi:.1f}%")
```

### 1C: Aggregation

| Operation | MCP Tokens | PTC Tokens | Reduction % |
|-----------|-----------|-----------|-------------|
| Single file read + stats | ___ | ___ | ___ % |
| Multi-file analysis (5 files) | ___ | ___ | ___ % |
| pip install + import + compute | ___ | ___ | ___ % |
| Iterative debugging (3 rounds) | ___ | ___ | ___ % |
| **Weighted average** | ___ | ___ | **___ %** |

**Weighted average formula:**
```python
operations = [
    {"name": "single_file", "mcp": ___, "ptc": ___, "frequency": 0.4},
    {"name": "multi_file", "mcp": ___, "ptc": ___, "frequency": 0.2},
    {"name": "pip_compute", "mcp": ___, "ptc": ___, "frequency": 0.2},
    {"name": "debug_loop", "mcp": ___, "ptc": ___, "frequency": 0.2},
]

weighted_mcp = sum(op["mcp"] * op["frequency"] for op in operations)
weighted_ptc = sum(op["ptc"] * op["frequency"] for op in operations)
weighted_reduction = (weighted_mcp - weighted_ptc) / weighted_mcp * 100
print(f"Weighted token reduction: {weighted_reduction:.1f}%")
```

## Scenario 2: Latency Comparison

Compare wall-clock time for PTC vs MCP on identical operations.

### 2A: Single Operation Latency

```bash
# PTC: Execute code in container
time pytest tests/ptc/integration/test_phase1_code_execution.py::test_simple_computation -v --timeout=30 2>&1 | tail -5

# MCP: Equivalent via tool-use (if MCP baseline tests exist)
time pytest tests/mcp/test_tool_use_baseline.py::test_simple_computation -v --timeout=30 2>&1 | tail -5
```

**Manual timing script:**
```python
#!/usr/bin/env python3
"""Compare PTC vs MCP latency for identical tasks."""
import time
import statistics

# Run each operation 10 times, collect timings
ptc_times = []
mcp_times = []

for i in range(10):
    # PTC timing
    start = time.perf_counter()
    # ... execute via PTC client ...
    ptc_times.append(time.perf_counter() - start)

    # MCP timing
    start = time.perf_counter()
    # ... execute via MCP tool-use ...
    mcp_times.append(time.perf_counter() - start)

print(f"PTC - mean: {statistics.mean(ptc_times)*1000:.0f} ms, "
      f"p50: {statistics.median(ptc_times)*1000:.0f} ms, "
      f"p95: {sorted(ptc_times)[8]*1000:.0f} ms")
print(f"MCP - mean: {statistics.mean(mcp_times)*1000:.0f} ms, "
      f"p50: {statistics.median(mcp_times)*1000:.0f} ms, "
      f"p95: {sorted(mcp_times)[8]*1000:.0f} ms")
print(f"Speedup: {statistics.mean(mcp_times)/statistics.mean(ptc_times):.1f}x")
```

### 2B: Parallel Operations Latency

```python
# PTC: 4 operations in parallel (4 containers)
import asyncio

async def ptc_parallel():
    start = time.perf_counter()
    tasks = [execute_in_container(f"agent-{i}", code) for i in range(4)]
    results = await asyncio.gather(*tasks)
    return time.perf_counter() - start

# MCP: 4 operations sequential (tool-use is inherently sequential per conversation)
def mcp_sequential():
    start = time.perf_counter()
    for i in range(4):
        execute_via_mcp(code)
    return time.perf_counter() - start

ptc_parallel_time = asyncio.run(ptc_parallel())
mcp_sequential_time = mcp_sequential()

print(f"PTC parallel (4 ops): {ptc_parallel_time*1000:.0f} ms")
print(f"MCP sequential (4 ops): {mcp_sequential_time*1000:.0f} ms")
print(f"Speedup: {mcp_sequential_time/ptc_parallel_time:.1f}x")
```

### Latency Results Table

| Operation | PTC (ms) | MCP (ms) | Speedup |
|-----------|---------|---------|---------|
| Simple print | ___ | ___ | ___ x |
| File read + parse | ___ | ___ | ___ x |
| pip install + import | ___ | ___ | ___ x |
| 4 parallel tasks | ___ | ___ | ___ x |
| Handoff (PTC-only) | ___ | N/A | N/A |

## Scenario 3: Qualitative Assessment

Score each dimension 1–5 using the rubric below. Compare PTC implementation vs MCP baseline.

### Scoring Rubric

| Score | Label | Description |
|-------|-------|-------------|
| 1 | Poor | Doesn't work or requires extensive workarounds |
| 2 | Below Average | Works but with significant limitations |
| 3 | Average | Functional, meets basic requirements |
| 4 | Good | Works well, minor limitations |
| 5 | Excellent | Exceeds expectations, elegant solution |

### Assessment Dimensions

| Dimension | PTC Score | MCP Score | Notes |
|-----------|-----------|-----------|-------|
| **Code sophistication** | ___ / 5 | ___ / 5 | Can agent write multi-step, stateful code? |
| **Library usage** | ___ / 5 | ___ / 5 | Can agent install and use any pip package? |
| **Data persistence** | ___ / 5 | ___ / 5 | Can agent build on previous results? |
| **Error recovery** | ___ / 5 | ___ / 5 | Can agent debug and fix its own code? |
| **Multi-agent collaboration** | ___ / 5 | ___ / 5 | Can multiple agents share work? |
| **Handoff continuity** | ___ / 5 | ___ / 5 | Can a new agent continue where another left off? |
| **Resource efficiency** | ___ / 5 | ___ / 5 | Memory/CPU usage per capability unit |
| **Isolation** | ___ / 5 | ___ / 5 | Are agents truly sandboxed? |
| **Scalability** | ___ / 5 | ___ / 5 | How well does it handle 4+ concurrent agents? |
| **Developer experience** | ___ / 5 | ___ / 5 | Ease of setup, debugging, monitoring |
| **Total** | ___ / 50 | ___ / 50 | |

### Qualitative Scoring Guide

**Code sophistication (PTC advantage expected):**
- 5: Agent writes multi-file projects, uses design patterns, handles edge cases
- 3: Agent writes functional single-file scripts
- 1: Agent can only execute trivial one-liners

**Library usage (PTC advantage expected):**
- 5: Agent installs and uses any pip package, handles version conflicts
- 3: Agent uses pre-installed packages only
- 1: No package management capability

**Multi-agent collaboration (PTC advantage expected):**
- 5: Agents share filesystem, coordinate via files, no interference
- 3: Agents work independently, no sharing
- 1: Agents interfere with each other

### Final Summary

```
Token Reduction:     ___% (weighted average across operation types)
Latency Improvement: ___x (geometric mean of speedups)
Qualitative Score:   PTC ___/50 vs MCP ___/50 (delta: +___)
```

## Pass/Fail Criteria

| Criterion | Pass | Fail |
|-----------|------|------|
| Token reduction measured | Data for all 4 operation types | Missing measurements |
| Token reduction > 50% | Weighted average > 50% | Below 50% |
| Latency comparison complete | All 5 operation types measured | Missing measurements |
| PTC not slower than MCP | PTC latency <= 2x MCP for single ops | PTC > 2x slower |
| PTC parallel advantage | 4-parallel PTC < 2x single MCP | No parallel benefit |
| Qualitative PTC >= MCP | PTC total >= MCP total | PTC scores lower |
| All data reproducible | Scripts provided, results consistent | Ad-hoc measurements only |
