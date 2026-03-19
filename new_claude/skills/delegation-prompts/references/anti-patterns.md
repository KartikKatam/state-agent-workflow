# Delegation Anti-Patterns

Seven before/after examples. Each shows WRONG (what agents default to), RIGHT (typed JSON prompt), and WHY (the cost difference).

## Contents

1. ["Go explore and find the problem"](#1-go-explore-and-find-the-problem) — Delegating discovery when you have coordinates
2. ["Read everything and write a summary"](#2-read-everything-and-write-a-summary) — Unbounded report instead of targeted extraction
3. ["Explore the codebase" when context exists](#3-explore-the-codebase-when-context-already-exists) — Re-exploration over valid context packets
4. ["Spawning when you could do it yourself"](#4-spawning-when-you-could-do-it-yourself) — Spawn overhead exceeds task cost
5. ["No output format specified"](#5-no-output-format-specified) — Delegate produces unusable output
6. ["Missing scope boundary"](#6-missing-scope-boundary) — Delegate expands to unrelated code
7. ["Vague discovery verbs with no targets"](#7-vague-discovery-verbs-with-no-targets) — "Investigate why X" when files are known

---

## 1. "Go explore and find the problem"

The spawner already knows the problem but delegates discovery instead of execution.

**WRONG:**
```
Read the scoring files and figure out why tasks 35-38 are broken.
```

**RIGHT:**
```json
{
  "type": "targeted",
  "task": "Fix field name mismatches in scorers for tasks 35, 36, 38",
  "known_context": {
    "file_coordinates": [
      {"path": "/home/kartik/personal/ptc-benchmark/scripts/score_benchmark.py",
       "lines": "683-715", "description": "Task 35: checks top_level_keys but result uses keys"},
      {"path": "/home/kartik/personal/ptc-benchmark/scripts/score_benchmark.py",
       "lines": "732-748", "description": "Task 36: checks ipv4_address but result uses addresses"},
      {"path": "/home/kartik/personal/ptc-benchmark/scripts/score_benchmark.py",
       "lines": "763-775", "description": "Task 38: reads functions but GT uses docstrings"}
    ],
    "findings": [
      "All three are field name mismatches between scorer and data",
      "Both agents scored 0.000 despite correct underlying data"
    ]
  },
  "output_contract": {
    "path": "/home/kartik/personal/ptc-benchmark/scripts/score_benchmark.py",
    "format": "Python source file with fixed elif branches"
  },
  "scope_boundary": {
    "do_not": ["Modify tasks other than 35, 36, 38", "Re-run full benchmark"],
    "tool_budget": 10
  },
  "success_criteria": ["All three scorers produce non-zero scores", "Re-score runs without errors"]
}
```

**WHY:** WRONG cost 37 calls / ~175K tokens. RIGHT gives exact coordinates — estimated 8-10 calls / ~30K tokens. 4x reduction in calls, 6x in tokens.

---

## 2. "Read everything and write a summary"

The spawner needs specific data points but asks for a broad report.

**WRONG:**
```
Read all the experiment results and write a summary of what we found.
```

**RIGHT:**
```json
{
  "type": "targeted",
  "task": "Extract variant comparison data into a summary table",
  "known_context": {
    "file_coordinates": [
      {"path": "/home/kartik/personal/ptc-benchmark/experiment/results/comparison-report.md",
       "description": "V0-V5 variant results for tasks 37-45"}
    ],
    "findings": ["V0 baseline optimal at 0.842 CoT accuracy", "V1 and V4 caused severe drops"]
  },
  "output_contract": {
    "path": "/home/kartik/personal/ptc-benchmark/experiment/results/summary.md",
    "format": "Markdown table: Variant, CoT Accuracy, Delta vs V0, IL+R Rate, Recommendation"
  },
  "scope_boundary": {
    "do_not": ["Analyze raw experiment logs", "Re-run experiments"],
    "tool_budget": 5
  }
}
```

**WHY:** "Write a summary" = unbounded. Delegate reads every file, produces 2000 words. The typed prompt specifies source file, extraction targets, and output format. One file in, one table out.

---

## 3. "Explore the codebase" when context already exists

Re-exploration when context packets are valid.

**WRONG:**
```
Explore the ptc-server codebase to understand how containers are managed.
```

**RIGHT:**
```json
{
  "type": "guided",
  "task": "Verify and extend container management context for PTC server",
  "known_context": {
    "file_coordinates": [
      {"path": "/home/kartik/.claude/mcp/ptc-server/container_manager.py",
       "lines": "249", "description": "Container lifecycle, volume mounts"},
      {"path": "/home/kartik/.claude/mcp/ptc-server/pulse_cleanup.py",
       "lines": "87-92", "description": "Session-based cleanup"}
    ],
    "context_packets": [
      {"path": ".claude/context/ptc-server-context.json", "covers": "Full PTC server structure"}
    ]
  },
  "unknowns": [
    "Read context packet first -- identify gaps",
    "Only re-explore if missing info about container volume lifecycle"
  ],
  "output_contract": {
    "path": "/home/kartik/personal/agentic_workflow/.claude/context/ptc-server-context.json"
  },
  "scope_boundary": {
    "do_not": ["Re-explore files already covered in context packet"]
  }
}
```

**WHY:** Context packet already has coordinates. WRONG re-does all the explorer's work. RIGHT starts from existing context, limits new work to specific gaps.

---

## 4. "Spawning when you could do it yourself"

Task costs fewer tool calls than spawn overhead.

**WRONG:**
```python
Task(prompt='{"type":"targeted","task":"Check if session file exists",...}',
     subagent_type="general-purpose")
```

**RIGHT:**
```python
Bash("ls -la ~/.claude/.ptc-session-id 2>/dev/null || echo 'NOT FOUND'")
```

**WHY:** Spawn overhead (init, skill loading) exceeds a 1-call task by 5-10x. If < 3 calls and no judgment, do it yourself.

---

## 5. "No output format specified"

Delegate produces unusable output format.

**WRONG:**
```
Analyze the benchmark results and tell me what you find.
```

**RIGHT:**
```json
{
  "type": "targeted",
  "task": "Extract win counts and token reduction from benchmark results",
  "known_context": {
    "file_coordinates": [
      {"path": "/home/kartik/personal/ptc-benchmark/report.json",
       "description": "45-task benchmark results"}
    ]
  },
  "output_contract": {
    "path": "/home/kartik/personal/ptc-benchmark/analysis.json",
    "format": "JSON",
    "schema_example": {"ptc_wins": 30, "traditional_wins": 15, "token_reduction_pct": 49.1}
  },
  "scope_boundary": {"do_not": ["Modify the benchmark report"]}
}
```

**WHY:** "Tell me what you find" = freeform prose when you needed 3 structured fields. `schema_example` eliminates ambiguity. 24 documented instances of this failure.

---

## 6. "Missing scope boundary"

Delegate expands scope to "improve" unrelated code.

**WRONG:**
```json
{
  "type": "targeted",
  "task": "Add input validation to process_frame",
  "known_context": {
    "file_coordinates": [
      {"path": "/home/user/project/src/pipeline.py", "lines": "340",
       "description": "process_frame method"}
    ]
  },
  "output_contract": {"path": "/home/user/project/src/pipeline.py", "format": "Python"}
}
```

**RIGHT:**
```json
{
  "type": "targeted",
  "task": "Add input validation to process_frame method",
  "known_context": {
    "file_coordinates": [
      {"path": "/home/user/project/src/pipeline.py", "lines": "340-370",
       "description": "def process_frame(self, frame: np.ndarray) -> ProcessResult"}
    ],
    "findings": ["Callers sometimes pass None or wrong dtype"]
  },
  "output_contract": {
    "path": "/home/user/project/src/pipeline.py",
    "format": "Python, only process_frame body modified (lines 340-370)"
  },
  "scope_boundary": {
    "do_not": ["Modify other methods", "Refactor surrounding code", "Add tests (separate task)"],
    "tool_budget": 5,
    "file_set": ["/home/user/project/src/pipeline.py"]
  },
  "success_criteria": ["Raises ValueError on None input", "Existing tests still pass"]
}
```

**WHY:** Without scope_boundary, delegate refactors __init__, adds type hints to 5 methods, creates a test file. Each expansion = 5-15 wasted calls.

---

## 7. "Vague discovery verbs with no targets"

Using "investigate" when file coordinates are known.

**WRONG:**
```
Investigate why the session cleanup is failing. Something about the session ID.
```

**RIGHT:**
```json
{
  "type": "guided",
  "task": "Diagnose root cause of session cleanup failure when session ID file is missing",
  "known_context": {
    "file_coordinates": [
      {"path": "/home/kartik/.claude/mcp/ptc-server/pulse_cleanup.py",
       "lines": "87-92", "description": "Reads session ID from file that may not exist"},
      {"path": "/home/kartik/.claude/mcp/ptc-server/container_manager.py",
       "lines": "228-231", "description": "Creates session ID file on container start"},
      {"path": "/home/kartik/.claude/mcp/ptc-server/session_start.py",
       "description": "Session initialization"}
    ],
    "findings": ["File deleted on session end, cleanup fires after → race condition suspected"]
  },
  "unknowns": [
    "Does session_start.py write or read the session ID file?",
    "Does cleanup fire before or after session end handler?",
    "Would a file-existence check in pulse_cleanup.py:87 fix the race?"
  ],
  "output_contract": {
    "path": "/home/kartik/personal/agentic_workflow/.claude/context/queries/cleanup-diagnosis.json",
    "format": "JSON",
    "schema_example": {"root_cause": "...", "fix_location": "file:line", "confidence": "high|medium|low"}
  },
  "scope_boundary": {
    "do_not": ["Fix the bug -- diagnosis only"],
    "tool_budget": 8,
    "file_set": [
      "/home/kartik/.claude/mcp/ptc-server/pulse_cleanup.py",
      "/home/kartik/.claude/mcp/ptc-server/container_manager.py",
      "/home/kartik/.claude/mcp/ptc-server/session_start.py"
    ]
  },
  "rejected_approaches": [
    {"approach": "Grep all session ID references across codebase", "reason": "Only 3 files involved"}
  ]
}
```

**WHY:** "Investigate why X" = 15-20 calls to find files. Spawner knew the 3 files and the race condition. RIGHT turns a 20-call investigation into an 8-call targeted verification.
