# PTC Comprehensive Benchmark Suite — Session Prompt

You are benchmarking **PTC (Programmatic Tool Calling)**, a sandboxed Python execution MCP server that gives AI agents persistent REPL environments inside Docker containers. The goal is to produce a rigorous, publication-quality benchmark report that quantifies PTC's value across multiple dimensions.

## System Under Test

PTC is an MCP server (`mcp__local-ptc__ptc_execute`) that provides:
- **Persistent namespace**: Variables survive across calls within the same agent session
- **Container isolation**: Each agent role gets its own Docker container with role-specific packages
- **Sub-agent container sharing**: `ptc_register_parent(sub_id, parent_id)` lets child agents share parent's container
- **Crash recovery**: State persistence at `/tmp/ptc-server-state.json`, watchdog daemon, signal handlers
- **Rich libraries**: numpy, pandas, scipy, scikit-learn, opencv, networkx, tree-sitter, tiktoken, requests, beautifulsoup4, pdfplumber, radon, bandit, vulture, hypothesis, and more

The comparison baselines are:
1. **Native Claude Tools** (Read, Write, Grep, Glob, Bash, WebSearch, Agent)
2. **Bash + python3 -c** (ephemeral, no state persistence)
3. **Bash + python3 script.py** (write script to disk, execute, read output)

## Your Task

### Phase 1: Build the Benchmark Infrastructure

Create a benchmark harness that:

1. **Logs every tool call** with:
   - Tool name (mcp__local-ptc__ptc_execute, Bash, Read, Grep, WebSearch, etc.)
   - Wall-clock duration (ms)
   - Input size (chars sent to tool)
   - Output size (chars returned to context)
   - Success/failure status
   - Error type on failure (timeout, import error, connection error, etc.)

2. **Implements a runner** at `scripts/ptc-benchmark/runner.py` that:
   - Defines each benchmark task as a Python class with `name`, `category`, `complexity`, `description`
   - Runs each task N times (configurable, default 5) for statistical significance
   - Records results to `ptc-benchmark-results/results.jsonl` (append-only)
   - Computes mean, median, std, p5, p95 for all metrics per task
   - Tracks system resources: container memory usage, CPU time, Docker stats

3. **Produces a report** at `ptc-benchmark-results/report.md` with:
   - Per-task results tables
   - Category summaries
   - Comparison charts (PTC vs baseline)
   - Statistical significance tests (paired t-test or Wilcoxon for PTC vs Bash baseline)
   - Overall findings and recommendations

4. **Hook-based logging** (optional, if feasible): A PostToolUse hook variant at `scripts/ptc-benchmark/benchmark_hook.py` that intercepts tool calls during benchmark runs and logs timing/size data automatically.

### Phase 2: Run the Benchmark Tasks

Run every task below using BOTH PTC and the most appropriate baseline (Bash, Native Tools, or both). For each task, record:

| Metric | Description |
|--------|-------------|
| `wall_time_ms` | End-to-end execution time |
| `input_tokens_est` | Estimated tokens sent to tool (chars / 4) |
| `output_tokens_est` | Estimated tokens returned to context (chars / 4) |
| `context_pollution` | Total chars that entered the conversation context |
| `success` | Boolean — did the task produce correct output? |
| `error_type` | If failed: timeout, import_error, runtime_error, network_error, parse_error |
| `correctness_score` | 0.0-1.0 — for tasks with verifiable answers |
| `docker_mem_mb` | Container memory usage (PTC only) |
| `docker_cpu_pct` | Container CPU usage (PTC only) |

---

## Benchmark Tasks

### Category A: Static Analysis (Codebase Understanding)

**A1. Class extraction — tree-sitter AST**
- Task: Extract all Python classes with inheritance, methods, and line numbers from the workspace
- Complexity: Simple
- PTC approach: tree-sitter parsing in container, return structured summary
- Baseline: Grep for `class ` + Read each file
- Key metric: Token reduction ratio (raw source bytes vs clean output)

**A2. Cyclomatic complexity analysis — radon**
- Task: Find the top 15 most complex functions in the workspace, ranked by CC score
- Complexity: Simple
- PTC approach: `radon cc` in container, parse JSON, return sorted top-15
- Baseline: Bash `python3 -m radon cc` piped through jq/python
- Key metric: Output size comparison

**A3. Dead code detection — vulture**
- Task: Find all unused variables, functions, imports with ≥80% confidence
- Complexity: Simple
- PTC approach: `vulture` in container, categorize by type
- Baseline: Bash `vulture` command

**A4. Security audit — bandit**
- Task: Run full security audit, categorize by severity (HIGH/MEDIUM/LOW), return top issues per category
- Complexity: Medium
- PTC approach: `bandit -r -f json` in container, parse 3.4MB JSON, return ~1KB summary
- Baseline: Bash `bandit` + manual parsing in context
- Key metric: This is the extreme compression case — measure raw output vs summary ratio

**A5. Import dependency graph + cycle detection**
- Task: Build the full import graph of the workspace, compute PageRank centrality, detect circular dependencies
- Complexity: Medium
- PTC approach: AST parsing + networkx graph analysis in container
- Baseline: Grep for `import` + manual reasoning
- Key metric: Can baseline even complete this task?

**A6. Refactoring impact analysis**
- Task: Given a symbol name (e.g., `WorkflowState`), find all definitions, imports, and usages across the codebase via AST
- Complexity: Medium
- PTC approach: AST walk with node type classification
- Baseline: Grep for symbol name (text match, no semantic understanding)
- Key metric: Precision — PTC distinguishes definitions/imports/usages; Grep cannot

**A7. Token budget analysis**
- Task: Count tokens (tiktoken cl100k_base) for every file in the workspace, report top-20 most expensive files and totals by file type
- Complexity: Medium
- PTC approach: tiktoken encoding in container
- Baseline: No native equivalent (tiktoken not available outside PTC)
- Key metric: Is this task even possible without PTC?

### Category B: Research & Information Retrieval

**B1. arXiv API structured search**
- Task: Search arXiv for "real-time perception" papers in cs.CV and cs.RO, return 15 most recent with titles, authors, dates, abstracts, links
- Complexity: Simple
- PTC approach: requests + BeautifulSoup XML parsing of arXiv API
- Baseline: WebSearch for "arXiv real-time perception"
- Key metric: Structured metadata quality (does baseline return actual paper links with authors?)

**B2. arXiv RSS feed processing**
- Task: Fetch today's arXiv RSS feeds for cs.CV and cs.RO, extract all paper titles and links
- Complexity: Simple
- PTC approach: feedparser + structured extraction
- Baseline: WebSearch for "arXiv cs.CV new papers today"
- Key metric: Token reduction (raw RSS is ~1.4M chars)

**B3. PDF paper extraction**
- Task: Fetch a specific arXiv paper PDF, extract text from first 3 pages, return clean text
- Complexity: Medium
- PTC approach: requests + pdfplumber
- Baseline: Read tool (can read local PDFs but can't fetch remote ones)
- Key metric: Can baseline complete this task at all?

**B4. Wikipedia API structured query**
- Task: Fetch introductions for 4 related Wikipedia articles via the MediaWiki API
- Complexity: Simple
- PTC approach: requests to Wikipedia API, parse JSON, return clean extracts
- Baseline: WebSearch for each topic
- Key metric: Precision of returned content (API gives exact intro text; WebSearch gives summaries)

**B5. Multi-source research aggregation**
- Task: Search for "YOLO object detection" across arXiv API, DuckDuckGo, and Wikipedia API. Merge results into a unified summary.
- Complexity: Hard
- PTC approach: Three API calls in one PTC execution, merge and deduplicate
- Baseline: Three separate WebSearch calls + manual synthesis
- Key metric: Number of tool calls required, total tokens consumed

### Category C: Data Processing & Analysis

**C1. JSON log analytics**
- Task: Load all session logs from `.claude/logs/`, parse into a DataFrame, compute summary statistics (mean files modified, test count, quality gate pass rate)
- Complexity: Simple
- PTC approach: pandas DataFrame construction + stats
- Baseline: Read each file + manual computation in context
- Key metric: Number of Read calls saved, token reduction

**C2. Training sweep simulation and analysis**
- Task: Simulate 4 training sweeps (80 epochs each), detect anomalies (loss spikes, divergence, overfitting, plateaus), produce cross-sweep comparison
- Complexity: Hard
- PTC approach: numpy simulation + scipy stats + anomaly detection logic, all in persistent namespace
- Baseline: Cannot simulate training data without code execution
- Key metric: Is this task possible without PTC?

**C3. CSV/JSON large dataset processing**
- Task: Generate a 100K-row synthetic dataset in PTC, compute group-by aggregations, correlations, and outlier detection
- Complexity: Medium
- PTC approach: pandas + scipy in container
- Baseline: Write python script to disk, execute via Bash, read output
- Key metric: Wall time, token overhead of script-write-execute-read cycle

**C4. Deep diff between two complex data structures**
- Task: Load two JSON schema files from the workspace, compute structural diff (added/removed/changed fields)
- Complexity: Simple
- PTC approach: deepdiff library
- Baseline: Read both files + manual comparison in context
- Key metric: Accuracy of diff detection

### Category D: Robotics & Computer Vision (Domain-Specific)

**D1. Camera calibration math**
- Task: Given camera intrinsics (focal length, sensor size) and plate dimensions, compute detection range table for license plates at 5m-100m
- Complexity: Simple
- PTC approach: numpy projection math
- Baseline: Manual calculation in context (LLM does the math)
- Key metric: Correctness (verify against known formulas), speed

**D2. Extended Kalman Filter simulation**
- Task: Simulate 90 frames of noisy vehicle detections, run EKF, report error reduction statistics
- Complexity: Medium
- PTC approach: filterpy EKF + numpy
- Baseline: Cannot run iterative numerical simulation without code execution
- Key metric: Is this task possible without PTC?

**D3. Homography + pose estimation**
- Task: Given 4 plate corner points, compute homography via OpenCV, estimate 3D pose via solvePnP, analyze perspective distortion
- Complexity: Medium
- PTC approach: cv2.findHomography + cv2.solvePnP
- Baseline: Cannot call OpenCV without code execution
- Key metric: Correctness (verify reprojection error)

**D4. Image preprocessing pipeline comparison**
- Task: Simulate 5 lighting conditions, apply 5 preprocessing pipelines, measure contrast and sharpness metrics, recommend best pipeline per condition
- Complexity: Hard
- PTC approach: cv2 + numpy synthetic image generation and analysis
- Baseline: Not possible without code execution

**D5. Neural network architecture FLOP estimation**
- Task: Given a ParseQ-S architecture definition, estimate FLOPs, parameter count, memory usage, and latency on 5 hardware targets
- Complexity: Medium
- PTC approach: Pure numpy computation of FLOP formulas
- Baseline: LLM estimates from knowledge (no computation)
- Key metric: Correctness vs known FLOP counts, and whether LLM baseline gets close

**D6. Pipeline throughput Monte Carlo simulation**
- Task: Simulate a producer→queue→consumer pipeline at peak load for 300s, compute drop rate, latency percentiles, queue depth stats, capacity planning
- Complexity: Hard
- PTC approach: numpy discrete-event simulation
- Baseline: Not possible without code execution

**D7. Real-time scheduling analysis (Rate Monotonic + Response Time)**
- Task: Given 7 pipeline stages with WCET and periods, run RMA schedulability test, exact RTA, and jitter impact simulation
- Complexity: Hard
- PTC approach: numpy + scipy for scheduling math and Monte Carlo jitter sim
- Baseline: LLM can do RMA math manually but not Monte Carlo

### Category E: Multi-Turn Persistent State (PTC-Unique)

**E1. Load → Analyze → Fix → Verify pipeline (4-turn)**
- Task: Turn 1: Load 10K synthetic OCR results into PTC namespace. Turn 2: Train a DecisionTree to explain failures (using data from Turn 1). Turn 3: Simulate a fix and measure improvement (using data from Turn 1 + model from Turn 2). Turn 4: Regression check across subpopulations (using ALL previous state).
- Complexity: Hard
- PTC approach: 4 sequential PTC calls, each building on persistent state
- Baseline approach A: Write all data to a file, read it back each turn (measure token cost)
- Baseline approach B: Write a single script that does all 4 steps (measure flexibility)
- Key metric: **Cumulative token cost across all 4 turns** — this is the critical PTC differentiator

**E2. Bayesian hyperparameter optimization (iterative)**
- Task: Run 50-trial Bayesian optimization (optuna TPE sampler) over 5 augmentation parameters, report optimal params, parameter importance, convergence analysis
- Complexity: Hard
- PTC approach: optuna study lives in persistent namespace, all trials run in container
- Baseline: Write optuna script to disk, execute, read output
- Key metric: Interactivity — can the agent inspect intermediate results and adjust?

**E3. Incremental data pipeline**
- Task: Turn 1: Load base dataset. Turn 2: Add derived features. Turn 3: Train a model. Turn 4: Evaluate. Turn 5: Add more data, retrain, compare.
- Complexity: Extreme
- PTC approach: 5 sequential calls, each building on previous namespace state
- Baseline: Single monolithic script or re-read everything each turn
- Key metric: Token cost scaling — does PTC cost stay flat while baseline grows linearly?

### Category F: Code Generation & Validation

**F1. Generate code from schema, validate by execution**
- Task: Read a JSON schema from the workspace, generate a Pydantic model, execute it in PTC to verify it compiles and validates correctly
- Complexity: Medium
- PTC approach: Generate code + exec() in same PTC call
- Baseline: Generate code + Write to file + Bash python3 to test
- Key metric: Round-trip time, number of tool calls

**F2. Property-based test generation**
- Task: Given a `merge_configs()` function, generate and run 200 Hypothesis property-based tests, report any violations
- Complexity: Medium
- PTC approach: hypothesis library in container
- Baseline: Not possible without code execution
- Key metric: Can PTC catch edge cases that manual test writing misses?

**F3. Code formatting and linting in sandbox**
- Task: Take messy Python code, format with ruff, lint, report issues, return clean version
- Complexity: Simple
- PTC approach: ruff in container
- Baseline: Write to temp file, Bash ruff, read back
- Key metric: Number of tool calls, latency

---

## Ablation Studies

### Ablation 1: Persistent Namespace vs Fresh Container

**Hypothesis**: Persistent namespace saves tokens on multi-turn tasks but may accumulate stale state.

- Run E1, E2, E3 with persistent namespace (normal PTC behavior)
- Run E1, E2, E3 with forced namespace reset between calls (`del` all variables or restart REPL)
- Measure: Token cost difference, correctness difference, memory growth

### Ablation 2: Shared Container vs Isolated Containers (Multi-Agent)

**Hypothesis**: Container sharing (`ptc_register_parent`) reduces startup latency and memory but risks namespace collision.

- Spawn 3 sub-agents, each running A1, A2, A3 simultaneously
- Variant A: All share parent's container (via `ptc_register_parent`)
- Variant B: Each gets its own container (no registration)
- Measure: Total wall time, per-agent correctness, memory usage per container, namespace collision rate, container startup latency

### Ablation 3: PTC vs Bash Script Pipeline

**Hypothesis**: For single-shot tasks, writing a Python script to disk and running it via Bash is nearly equivalent to PTC.

- Run all Category A tasks (static analysis) via PTC and via write-script-execute-read
- Measure: Total tool calls, total tokens, wall time, code correctness
- This isolates PTC's value to multi-turn persistence vs single-shot convenience

### Ablation 4: Container Resource Scaling

**Hypothesis**: PTC container resource usage scales linearly with number of concurrent agents.

- Run D6 (Monte Carlo simulation) with 1, 2, 4, 8 concurrent PTC containers
- Measure: Per-container memory, total system memory, CPU contention, execution time degradation
- Report: At what concurrency does the host system start thrashing?

### Ablation 5: Library Availability Impact

**Hypothesis**: PTC's value comes primarily from library access, not from persistence or isolation.

- Identify the 5 tasks most dependent on specialized libraries (cv2, filterpy, optuna, tree-sitter, networkx)
- Attempt each using only stdlib (math, json, collections, itertools) in PTC
- Measure: Which tasks become impossible? Which degrade gracefully?

### Ablation 6: Token Scaling with Data Size

**Hypothesis**: PTC's token advantage grows with data size because intermediate data stays in container.

- Run C3 (large dataset processing) at 1K, 10K, 100K, 500K, 1M rows
- For each size, measure PTC output tokens vs baseline output tokens
- Plot: Token cost vs data size for PTC (should be flat) vs baseline (should grow)

---

## Additional Research-Quality Analyses

### Analysis 1: Failure Mode Taxonomy

For every failed PTC execution across all tasks and runs, categorize the failure:
- `import_error` — package not available or not importable
- `timeout` — execution exceeded limit
- `runtime_error` — code error (TypeError, ValueError, etc.)
- `network_error` — HTTP request failed
- `resource_error` — OOM, disk full, etc.
- `namespace_pollution` — previous call's state interfered
- `container_crash` — Docker container died

Report: Failure rate by category, most common failure modes, MTBF (mean time between failures).

### Analysis 2: Startup Latency Characterization

Measure cold-start vs warm-start latency:
- First PTC call in a session (container startup + REPL init)
- Subsequent calls (REPL already running)
- After container crash + recovery
- After idle period (5min, 10min, 30min)

### Analysis 3: Context Window Savings Projection

For each task, compute:
- `tokens_saved = baseline_context_tokens - ptc_context_tokens`
- `savings_pct = tokens_saved / baseline_context_tokens * 100`
- Project: "Over a typical 14-chunk implementation workflow, PTC saves X tokens total"
- Express in dollars at current API pricing

### Analysis 4: Correctness Verification

For tasks with deterministic answers (D1, D3, D5, D7):
- Compute ground-truth answers analytically
- Compare PTC output vs ground truth
- Compare LLM baseline output vs ground truth (when LLM attempts math directly)
- Report: accuracy rate by approach

### Analysis 5: pip Install Reliability

Systematically test package installation in a running REPL:
- Install 20 packages of varying sizes
- For each: does `import` work immediately? After `importlib.invalidate_caches()`? After REPL restart?
- Characterize the import cache bug quantitatively

---

## Output Format

### Primary deliverable: `ptc-benchmark-results/report.md`

Structure:
1. Executive Summary (1 page)
2. Methodology (benchmark harness, statistical approach, hardware specs)
3. Per-category results with tables and charts
4. Ablation study results
5. Failure mode analysis
6. Recommendations (when to use PTC vs native tools)
7. Raw data appendix (link to JSONL files)

### Data files:
- `ptc-benchmark-results/results.jsonl` — raw per-run data
- `ptc-benchmark-results/summary.json` — aggregated statistics
- `ptc-benchmark-results/ablations/` — per-ablation data
- `ptc-benchmark-results/failures.jsonl` — failure log with full tracebacks

### Visualization:
- Token savings bar chart (PTC vs baseline per task)
- Latency comparison scatter plot
- Token cost scaling curve (Ablation 6)
- Failure mode pie chart
- Concurrency scaling plot (Ablation 4)

Generate these as ASCII tables in the report. If matplotlib is available in PTC, generate PNG charts and embed them.

---

## Hardware & Environment

Record at the start of the benchmark:
- CPU model, core count, RAM
- Docker version, container resource limits
- Python version in container
- PTC server version/commit
- List of pre-installed packages with versions
- Host OS and kernel version
- Claude model and context window size

---

## Constraints

- Do NOT look at or use results from previous benchmarking sessions
- Run each task from scratch — no warm cache advantages unless explicitly testing warm-start
- For multi-run tasks (N=5 or N=10), discard the first run as warmup and report stats on the remaining runs
- All timing must use wall-clock time measured from outside the tool call (not inside the PTC code)
- Report raw numbers first, then derived statistics — never hide unfavorable results
- If a task fails consistently, report the failure rate honestly rather than excluding it
