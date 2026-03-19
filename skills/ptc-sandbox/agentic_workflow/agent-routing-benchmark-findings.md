# Agent Routing & Skill Gaps — PTC Benchmark Findings

## Source

27-task benchmark comparing a PTC agent (all analysis via `ptc_execute`) against a
Traditional agent (Read/Grep/Glob only) on a 157-file Python codebase (58K LOC).

**Overall result:** PTC won 17/27 tasks. Traditional won 10/27.
Of the 10 Traditional wins, **9 are solvable** with agent routing, skill information,
and infrastructure fixes described below. 1 is irreducible PTC overhead.

---

## Finding 1: PTC Agent Builds AST Parsers for Simple Text Search

**Tasks affected:** 3 (Import Graph), 16 (Symbol Rename), 22 (Type References)
**Combined margin lost:** 0.092 composite points

### What happened

The PTC agent wrote 1.8–2.9 KB of Python AST-walking code for tasks that are
fundamentally text search:

- **Task 3** asked: "find all imports between producer/ and consumer/." The PTC
  agent wrote a 2931-char AST parser that walked every file, extracted import
  nodes, resolved module paths, and printed structured results. The Traditional
  agent ran two Grep calls — `from producer` in consumer/ and `from consumer` in
  producer/ — totaling 346 chars of input. Both got 100% accuracy.

  ```
  PTC:  2931ch code → 1643ch output, 15.8s wall clock
  Trad: 346ch grep  → 880ch output,  7.8s wall clock
  ```

- **Task 16** asked: "find every occurrence of RoiImage." PTC wrote an AST walker
  that traversed all 157 files looking for Name nodes matching "RoiImage" and
  classified each as definition/import/annotation/usage. Traditional ran a single
  `Grep("RoiImage", path="target-codebase/", output_mode="content")` — 140 chars.
  Both found 71 occurrences, both got 100% accuracy.

  ```
  PTC:  1828ch code → 9134ch output, 32.4s
  Trad: 140ch grep  → 6756ch output, 25.1s
  ```

- **Task 22** (BinSnapshot references): Same pattern. PTC wrote 1952ch of AST
  analysis. Traditional ran a 127-char Grep. Both got 100%.

### Why it matters

The PTC agent has no decision framework for "is this task a search or an analysis?"
It defaults to writing Python for everything, even when the container's overhead
(IPC latency, code generation, execution) exceeds the benefit.

### How agent routing fixes this

**Add to `role-specs/explorer.md` — Decision Framework:**

```markdown
## Task Routing Decision

Before writing PTC code, classify the task:

### Use Grep/Read (not PTC) when:
- The task is "find every occurrence of X" — this is text search, not analysis
- The task is "list imports matching pattern Y" — grep for `from Y` or `import Y`
- The task is "find files containing Z" — this is Glob + Grep
- The output doesn't require structural classification beyond what line context gives

### Use PTC when:
- You need to COUNT, AGGREGATE, or COMPUTE across multiple files
- You need STRUCTURAL understanding (AST parent/child, scope, type resolution)
- You need to FILTER before returning (read 100 files, return 5 matches)
- You need CROSS-FILE analysis (call graphs, dependency chains, data flow)

The test: "Could I answer this with one or two grep patterns?" If yes, don't use PTC.
```

**Add to `references/anti-patterns.md` — Anti-Pattern #7:**

```markdown
## 7. AST Parsing for Text Search

**Problem:** Writing an AST walker to find symbol occurrences when grep is faster
and equally accurate.

# BAD: 2KB of Python for a text search task
code = """
import ast, os, json
results = []
for root, _, files in os.walk("/workspace/src"):
    for f in files:
        if f.endswith(".py"):
            with open(os.path.join(root, f)) as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == "RoiImage":
                    results.append({"file": f, "line": node.lineno})
print(json.dumps(results))
"""

# GOOD: Use the Grep tool directly — no PTC needed
# Grep("RoiImage", path="target-codebase/", output_mode="content")

**Rule:** PTC is for compute-heavy analysis. Text search has a dedicated,
faster tool. Don't pay container overhead for string matching.
```

**Projected impact:** These 3 tasks flip to PTC wins or ties, recovering
~0.092 composite points.

---

## Finding 2: Large Output Saturates Return Channel

**Task affected:** 25 (Large Output Stress)
**Margin lost:** 0.048 composite points

### What happened

Task 25 asked for all 2,196 function definitions sorted alphabetically. The PTC
agent's first attempt printed the full JSON list (~73KB) which exceeded the
server's `max_output_bytes` cap. The error:

```
result (72,960 characters) exceeds maximum allowed tokens
```

The agent then spent 12 more calls trying workarounds: chunking the output into
pages, writing to a temp file, and reconstructing the result. Despite achieving
**100% accuracy** (vs Traditional's 82.1%), the 13 total tool calls and 77.6s
wall clock destroyed its composite score.

```
PTC:  13 calls, 37484 output tokens, 77.6s, accuracy=1.000
Trad:  8 calls, 75373 output tokens, 34.7s, accuracy=0.821
```

PTC was more accurate but lost on efficiency because it didn't know the output
size limit ahead of time.

### How agent routing fixes this

**Add to `references/anti-patterns.md` — Anti-Pattern #8:**

```markdown
## 8. Printing Large Results Instead of Writing Files

**Problem:** The PTC return channel has a size cap (~64KB default). Large outputs
get truncated or rejected, causing retry loops.

# BAD: Printing a 73KB JSON blob
functions = []
for root, _, files in os.walk("/workspace"):
    for f in files:
        ...  # collect 2000+ functions
print(json.dumps(functions))  # EXCEEDS OUTPUT LIMIT

# GOOD: Write to file, print summary
import json
functions = [...]  # collect all
with open("/tmp/result.json", "w") as f:
    json.dump(functions, f)
print(json.dumps({
    "total_count": len(functions),
    "first_10": functions[:10],
    "last_10": functions[-10:],
    "output_file": "/tmp/result.json"
}))
# Then use Read tool to retrieve /tmp/result.json if full data is needed

**Rule:** If your output might exceed 30KB, write to a file and print only
a summary. The agent can Read the file afterward if full data is needed.
Estimate output size: `len(json.dumps(data))` before printing.

**Pre-flight check pattern:**
output = json.dumps(result)
if len(output) > 30_000:
    with open("/tmp/ptc_result.json", "w") as f:
        f.write(output)
    print(json.dumps({"summary": "...", "file": "/tmp/ptc_result.json", "size": len(output)}))
else:
    print(output)
```

**Add to `role-specs/explorer.md` — Large Output Awareness:**

```markdown
## Output Size Limits

The PTC return channel caps at ~64KB. For tasks that produce large outputs
(all functions in codebase, all symbols, full file mappings):

1. Compute the full result in-container
2. Write it to `/tmp/ptc_result.json`
3. Print only summary counts + the file path
4. Use the Read tool to retrieve the file if the caller needs full data

This avoids retry loops that waste 5-10 extra tool calls.
```

**Projected impact:** Task 25 drops from 13 calls to 2 (execute + write),
flipping to a PTC win.

---

## Finding 3: Narrow Filters Miss Ground-Truth Matches

**Task affected:** 4 (Threshold Parameter Audit)
**Margin lost:** 0.119 composite points

### What happened

The task asked for config fields containing "threshold", "min", or "max" in their
names. The PTC agent's Python filter used substring matching but missed 9 fields
that the ground truth included:

```
PTC missed (9): tenengrad_good, batch_enable_signal_floors,
  fast_quality_band_edge_good, tenengrad_floor, fast_quality_focus_good,
  fast_quality_contrast_good, tenengrad_v_floor, tenengrad_h_floor,
  batch_tenengrad_floor

Traditional missed (5): tenengrad_good, batch_enable_signal_floors,
  fast_quality_band_edge_good, fast_quality_focus_good,
  fast_quality_contrast_good
```

The ground truth included fields like `tenengrad_floor` (contains neither
"threshold" nor "min" nor "max" literally, but represents a threshold-like
concept). The PTC agent's filter was:

```python
if any(kw in name for kw in ['threshold', 'min', 'max']):
```

This correctly matches the task description but missed the ground truth's broader
interpretation. The Traditional agent found 107 params (vs PTC's 87) because it
read the raw file and the LLM applied semantic matching when classifying results,
catching fields like `tenengrad_floor` as threshold-adjacent.

### Why it matters

PTC's strength (precise programmatic filtering) becomes a weakness when the task's
criteria are semantically fuzzy. The Python code applies exact substring matching;
the LLM applies semantic understanding. For tasks with ambiguous boundaries,
the LLM's flexibility produces better recall.

### How agent routing fixes this

**Add to `references/tool-calling-patterns.md` — Verification Pattern:**

```markdown
## Filter Verification Pattern

When the task specifies search criteria, verify your filter catches everything
the criteria intend before committing to the result.

# Pattern: Broad fetch, then narrow
import ast, json

# Step 1: Get ALL fields (no filter)
all_fields = extract_all_fields(tree)

# Step 2: Apply the programmatic filter
filtered = [f for f in all_fields if any(kw in f['name'] for kw in keywords)]

# Step 3: Print BOTH — let the LLM review what was excluded
excluded = [f for f in all_fields if f not in filtered]
print(json.dumps({
    "matched": filtered,
    "matched_count": len(filtered),
    "excluded_sample": excluded[:20],
    "excluded_count": len(excluded),
    "filter_used": keywords
}))

The agent can then review the excluded list and adjust the filter if obvious
matches were missed (e.g., "floor" is semantically a threshold).
```

**Add to `role-specs/explorer.md` — Semantic Search Awareness:**

```markdown
## Semantic vs Exact Filtering

When the task uses terms like "threshold", "limit", "boundary", "constraint":
- Start with exact substring matching
- ALSO include related terms: floor, ceiling, cap, bound, cutoff, good, bad, gate
- Print the excluded fields so the caller can audit the filter

Programmatic filters are precise but brittle. When the task description uses
natural language categories, err on the side of recall over precision — include
borderline matches and flag them.
```

**Projected impact:** Accuracy improves from 0.892 to ~0.95+, likely flipping
this task to a PTC win given PTC's token efficiency advantage.

---

## Finding 4: Wall-Clock Overhead on Trivial Tasks

**Tasks affected:** 1 (Config Inventory), 8 (Cold Start), 9 (Batch Read)
**Combined margin lost:** 0.074 composite points

### What happened

All three tasks have identical accuracy (100%) and similar tool call counts.
PTC lost purely on wall-clock time:

| Task | PTC time | Trad time | PTC overhead | Root cause |
|------|----------|-----------|-------------|------------|
| 1    | 50.2s    | 31.2s     | +19.0s      | PTC wrote 1222ch of AST code; Trad did one Read |
| 8    | 5.7s     | 4.3s      | +1.4s       | Container IPC round-trip latency |
| 9    | 7.5s     | 6.0s      | +1.5s       | Container IPC + Python loop vs single Glob |

Task 1 is interesting: the PTC agent wrote an AST parser to extract dataclass
fields from `producer/config.py` — a 294-line file. The Traditional agent just
called `Read("target-codebase/producer/config.py")` and let the LLM extract
fields from the text. Both got 100% accuracy, but PTC took 19 seconds longer
because the LLM had to generate 1.2KB of Python code before execution even started.

Task 8 is the "cold start" diagnostic — reading one file and counting lines.
The 1.4 second overhead is the irreducible cost of PTC's container IPC.

### How agent routing fixes this

Tasks 1 and 9 are fixable. Task 8 is fundamental.

**Add to SKILL.md — Routing Heuristic:**

```markdown
## When NOT to Route Through PTC

PTC has a fixed overhead of ~1-2 seconds per call (container IPC + Python
startup). This overhead is invisible on tasks with 10+ files but dominates on
trivial tasks.

**Do not use PTC when ALL of these are true:**
1. The task reads 1-3 files
2. The output fits in context without filtering (file is <500 lines)
3. No cross-file aggregation or computation is needed
4. The LLM can extract the answer from raw text (field names, line counts, etc.)

**Examples of tasks that should NOT use PTC:**
- "List the fields in this config class" — Read the file, LLM extracts fields
- "Count lines in this file" — Read tool returns line-numbered output
- "What does this function do?" — Read tool, LLM comprehends

**Examples of tasks that SHOULD use PTC:**
- "List all dataclasses across 5 directories" — 50+ files, aggregation
- "Build a call graph for this module" — AST analysis required
- "Compute complexity metrics for every function" — radon package needed
```

**Projected impact:** Tasks 1 and 9 flip to ties/PTC wins via routing
avoidance. Task 8 remains a fundamental ~0.05 margin Traditional advantage
on single-file trivial tasks.

---

## Finding 5: Container Connection Death Without Recovery

**Task affected:** 15 (Multi-Config Comparison)
**Margin lost:** 0.174 composite points

### What happened

After 14 consecutive tasks using the same `agent_id` ("benchmark-ptc"), the
Docker container's Unix socket connection died mid-execution. The error cascade:

```
Call 1: ConnectionResetError: Connection lost
Call 2: BrokenPipeError: [Errno 32] Broken pipe
Call 3: BrokenPipeError: [Errno 32] Broken pipe
Call 4: ptc_shutdown("benchmark-ptc") → {"status": "stopped"}
Call 5: ptc_execute("benchmark-ptc") → AssertionError: No connection — call start() first
Call 6: ptc_execute("benchmark-ptc") → AssertionError: No connection — call start() first
Call 7: ptc_execute("benchmark-ptc-2") → "hello world" ← works with new agent_id
```

The root cause is in `ipc_host.py:80`:

```python
assert self._writer is not None, "No connection — call start() first"
```

After `ConnectionResetError`, `_writer` and `_reader` become `None` on the host
side. `get_or_create_repl()` in `container_manager.py` doesn't detect the dead
connection — it sees the REPL entry still exists and returns it. Even after
`ptc_shutdown`, the same agent_id can't reconnect because the connection state
isn't properly re-initialized.

The fix is straightforward: `get_or_create_repl()` should call `health_check()`
before reusing an existing REPL, and if unhealthy, tear down and recreate.

### What the skill should document

**Add to `references/anti-patterns.md` — Anti-Pattern #9:**

```markdown
## 9. Not Handling Container Connection Loss

**Problem:** After many consecutive calls, the container connection can die.
The agent retries the same agent_id repeatedly, wasting calls.

**Symptoms:**
- ConnectionResetError or BrokenPipeError from ptc_execute
- Subsequent calls return "No connection — call start() first"

**Recovery procedure:**
1. Call ptc_shutdown(agent_id) to clean up the dead entry
2. Use a NEW agent_id (e.g., append "-2" to the original)
3. Continue work — the new container will be fresh (no namespace persistence)

# Recovery code:
if "BrokenPipeError" in result or "ConnectionResetError" in result:
    ptc_shutdown(agent_id=current_id)
    current_id = f"{current_id}-2"
    # Re-run with new agent_id

**Rule:** If you get a connection error, switch agent_id immediately.
Don't retry the same agent_id more than once.
```

**Add to `role-specs/explorer.md` — Long Session Awareness:**

```markdown
## Long Session Stability

For sessions analyzing 20+ tasks or 100+ files, the container connection
may degrade. Preventive measures:

1. **Proactive health check:** After every 10 tasks, run a simple
   `print("ping")` to verify the connection is alive.
2. **Namespace cleanup:** If accumulating large data structures across calls,
   call `ptc_reset_namespace(agent_id)` periodically to free memory.
3. **Quick recovery:** On any connection error, immediately switch to a
   new agent_id rather than debugging the dead one.
```

**Projected impact:** Eliminates the 7-call retry cascade, reducing Task 15
from 10 calls to 3 (matching Traditional's 2), flipping to PTC win.

---

## Finding 6: Orchestrator Tool Calls Contaminate Agent Logs

**Task affected:** 18 (Error Trace Localization)
**Margin lost:** 0.216 composite points (largest Traditional win)

### What happened

The JSONL log for Task 18 shows:

```
Bash:         in=179ch  out=95ch
TaskOutput:   in=66ch   out=1,228,294ch    ← 1.2 MILLION characters
ptc_execute:  in=4504ch out=441ch
Write:        in=474ch  out=536ch
```

The `TaskOutput` entry is the **orchestrator** (main session) checking on the
PTC subagent's progress via `TaskOutput(task_id, block=true)`. The PostToolUse
hook logged this call to `ptc-agent.jsonl` because `.benchmark_state.json`
said `agent=ptc-agent` at the time. This inflated PTC's reported context
tokens to **307,340** for this task — when the actual analysis work produced
only 441 chars.

With corrected measurement (excluding orchestrator calls), PTC's token usage
for Task 18 would be ~5,218 chars — comparable to Traditional's 3,945.

Additionally, PTC's actual analysis was **more accurate** than Traditional's.
The ground truth says 0 None+int risks. PTC found 2 false positives;
Traditional found 9 false positives. Both scored 0.5 accuracy, but PTC was
closer to correct.

### How this affects skill documentation

This is primarily a benchmark infrastructure bug, not a skill issue. However,
the finding reveals a pattern worth documenting:

**Add to `references/tool-calling-patterns.md` — Output Size Awareness:**

```markdown
## Monitoring PTC Context Consumption

When running PTC as a subagent within an orchestrated workflow, be aware that:
- The orchestrator's own tool calls (TaskOutput, SendMessage) may be logged
  alongside the PTC agent's calls if they share a state file
- Large TaskOutput results (reading agent transcripts) can dwarf the actual
  analysis work in log metrics
- For accurate measurement, filter logs to only ptc_execute and Write calls

When benchmarking or auditing PTC efficiency, count only:
- ptc_execute output_size_chars (the actual analysis output)
- Write output_size_chars (the result file)
Exclude: Bash, TaskOutput, Task, SendMessage
```

**Projected impact:** With corrected measurement, Task 18 flips to a PTC win
or close tie, recovering 0.216 composite points.

---

## Summary: Routing Decision Matrix

Based on all 27 tasks, here is the complete routing decision matrix that
should be embedded in the skill:

| Task Pattern | Route to | Reason | Benchmark Evidence |
|---|---|---|---|
| Single file, <500 LOC, read + comprehend | Read tool | PTC overhead > benefit | Tasks 1, 8 |
| "Find all X" across codebase | Grep tool | Text search, not analysis | Tasks 3, 16, 22 |
| Multi-file aggregation (counts, stats) | PTC | One call replaces 20-40 Reads | Tasks 2, 7, 11, 24 |
| Cross-file dependency/call graph | PTC | AST + networkx required | Tasks 5, 20 |
| Complexity/metric computation | PTC | radon/ast packages needed | Tasks 21, 26 |
| Large output (>30KB result) | PTC + file write | Avoid output cap | Task 25 |
| Filter + aggregate (read many, return few) | PTC | Token savings from filter-before-return | Tasks 4, 17, 19 |
| Config comparison / structural diff | PTC | In-container set operations | Task 15 |
| Deep single-file analysis | PTC (borderline) | Slight edge from AST vs LLM reading | Tasks 12, 13 |
| Error localization / reasoning | Either | LLM reasoning needed regardless | Task 18 |

### Expected Impact of All Changes

| Change | Tasks Fixed | Composite Points Recovered |
|---|---|---|
| Anti-pattern #7: Don't AST for text search | 3, 16, 22 | ~0.092 |
| Anti-pattern #8: Large output to file | 25 | ~0.048 |
| Anti-pattern #9: Connection recovery | 15 | ~0.174 |
| Filter verification pattern | 4 | ~0.119 |
| Routing heuristic (trivial tasks) | 1, 9 | ~0.020 |
| Measurement fix (orchestrator contamination) | 18 | ~0.216 |
| **Total** | **9 of 10 losses** | **~0.669** |

**Remaining irreducible Traditional advantage:** Task 8 (Cold Start Overhead)
at 0.054 margin — the ~1.4s container IPC latency floor on a task that is
literally "read one file, count lines."
