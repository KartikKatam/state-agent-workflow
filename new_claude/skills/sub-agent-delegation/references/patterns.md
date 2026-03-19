# Delegation Patterns by Agent Role

Proven approaches for sub-agent delegation, organized by role.

## Contents
- [Explorer Delegation](#explorer-delegation) — Parallel file analysis, git history mining
- [Researcher Delegation](#researcher-delegation) — Multi-source parallel lookup
- [Coder Delegation](#coder-delegation) — Sonnet code writer, parallel modules
- [Tester Delegation](#tester-delegation) — Parallel tier implementation
- [Context Sizing Heuristics](#context-sizing-heuristics) — Token budgets by complexity
- [Synthesis Patterns](#synthesis-patterns) — Merge-deduplicate, sequential, map-reduce
- [PTC Delegation Patterns](#ptc-delegation-patterns) — In-container analysis via ptc_execute

## Explorer Delegation

### Pattern: Parallel File Analysis
Dispatch multiple Haiku sub-agents to analyze independent file groups simultaneously.

```
Sub-agent 1: Analyze src/detection/*.py → produce detection-module summary
Sub-agent 2: Analyze src/tracking/*.py → produce tracking-module summary
Sub-agent 3: Analyze src/ocr/*.py → produce ocr-module summary
```

**Context per sub-agent:** File list + output schema + one example output.
**Verify:** Each summary follows schema, covers all files in scope, no fabricated function/class names.

### Pattern: Git History Mining
Single Sonnet sub-agent for deep git analysis (requires judgment about significance).

**Context:** Repository path, date range, specific questions to answer.
**Verify:** Commit hashes referenced actually exist (`git log --oneline` spot-check).

## Researcher Delegation

### Pattern: Multi-Source Parallel Lookup
Dispatch Haiku sub-agents to different sources simultaneously.

```
Sub-agent 1: Search Context7 for {library} API docs
Sub-agent 2: WebSearch for {library} best practices
Sub-agent 3: Read local docs at docs/{library}/
```

**Synthesize:** Cross-reference findings. If sources disagree, flag the conflict rather than silently picking one.
**Verify:** URLs/citations actually resolve. Code examples are syntactically valid.

## Coder Delegation

### Pattern: Sonnet Code Writer with Review
Dispatch Sonnet for code implementation. Review the output yourself before accepting.

**Context:** Exact files to modify, test files to pass, one example of project coding style.
**Verify:** Tests actually pass (run them yourself). Code follows project patterns. No out-of-scope changes.

### Pattern: Parallel Independent Module Implementation
When changes span independent modules with no shared files.

```
Sub-agent 1: Implement in src/moduleA/ — tests in tests/test_moduleA/
Sub-agent 2: Implement in src/moduleB/ — tests in tests/test_moduleB/
```

**Pre-dispatch check:** Verify no shared files between sub-agents. Check imports don't cross.
**After both complete:** Run full test suite — not just individual module tests.

## Tester Delegation

### Pattern: Parallel Tier Implementation
Dispatch sub-agents for independent test tiers.

```
Sub-agent 1: Golden path scenarios (tests/scenarios/golden_path/)
Sub-agent 2: Edge case scenarios (tests/scenarios/edge_cases/)
Sub-agent 3: Adversarial scenarios (tests/scenarios/adversarial/)
```

**Context per sub-agent:** Test plan for that tier, fixture strategy, project test conventions.
**Verify:** Tests are not trivially passing. If possible, run against intentionally broken code to confirm they catch failures.

## Context Sizing Heuristics

| Task Complexity | Context Budget | What to Include |
|----------------|---------------|-----------------|
| Simple/mechanical (Haiku) | 500-1500 tokens | Task + 1-2 file contents + output format |
| Moderate (Sonnet) | 1500-4000 tokens | Task + relevant files + pattern example + constraints |
| Complex analysis (Sonnet) | 4000-8000 tokens | Task + comprehensive file set + examples + schema |

## Synthesis Patterns

### Merge-and-Deduplicate
For combining factual outputs (research, analysis).

1. Collect all results into one working space
2. Identify overlapping claims
3. For duplicates: keep version with better evidence/citations
4. For conflicts: investigate both, pick the substantiated one, note the discrepancy
5. Structure merged output following the target schema

### Sequential Accumulation
For building outputs that depend on each other.

1. Sub-agent 1 produces base artifact
2. Verify base artifact
3. Sub-agent 2 receives verified artifact + extension task
4. Verify extended artifact
5. Continue chain as needed

### Map-Reduce Analysis
For large-scale codebase analysis.

1. **Map:** Dispatch N sub-agents, each analyzing a partition
2. **Verify:** Check each partition result independently
3. **Reduce:** Combine partition results into aggregate analysis
4. **Verify aggregate:** Cross-check that totals/summaries are consistent with parts

## PTC Delegation Patterns

When sub-agents use `ptc_execute` for in-container analysis. Sub-agents share your container via REPL pool — same packages, ~100ms startup, isolated namespaces.

### Pattern: Parallel PTC Analysis
Dispatch sub-agents that each use `ptc_execute` for independent analyses.

```
Sub-agent 1 (Haiku): ptc_execute → coverage analysis on src/auth/
Sub-agent 2 (Haiku): ptc_execute → complexity metrics on src/models/
Sub-agent 3 (Haiku): ptc_execute → dependency graph on src/api/
```

**Context per sub-agent:** Task + relevant packages (3-5 only) + print discipline reminder + output path.
**Verify:** Output file exists, JSON is valid, values are plausible (not fabricated numbers).

### Pattern: PTC + Read Tool Hybrid
Some delegation tasks need both PTC analysis and direct file reads.

```
Sub-agent prompt:
"Analyze the auth module for security issues.
1. Use ptc_execute with bandit to scan src/auth/ → print JSON summary
2. Read src/auth/config.py with Read tool to check for hardcoded secrets
3. Write combined findings to .claude/context/queries/auth-security.json"
```

**Why hybrid:** bandit needs a Python runtime (PTC), but checking for hardcoded strings is faster with Read + pattern matching. Don't force PTC when simpler tools suffice.

### Pattern: Explorer PTC Delegation
Explorer dispatches sub-agents for parallel codebase analysis partitions.

```
Sub-agent prompt:
"You have access to ptc_execute. Available packages: ast, radon, networkx.
Analyze all Python files in src/detection/:
1. Parse with ast → extract classes, functions, imports
2. Compute cyclomatic complexity with radon
3. Print JSON: {files, classes, functions, avg_complexity}
Write to .claude/context/queries/detection-analysis.json"
```

**Model:** Haiku — mechanical extraction with clear instructions.
**Verify:** Function/class names match actual source (spot-check 2-3 against Read tool).

### Pattern: Auditor PTC Delegation
Auditor dispatches sub-agents for independent audit checks.

```
Sub-agent 1: ptc_execute with diff-cover → coverage on changed lines
Sub-agent 2: ptc_execute with cognitive-complexity → regression check
Sub-agent 3: ptc_execute with ast → test assertion count validation
```

**Model:** Haiku for all — these are formulaic checks with clear outputs.
**Synthesize:** Combine into audit verdict. Any check failing → audit fails.
