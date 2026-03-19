#!/usr/bin/env python3
"""Benchmark RTK + LSP token savings.

Runs a set of tasks twice via `claude -p --output-format json`:
  - BASELINE: no user settings (no RTK hook, no LSP plugins)
  - ENHANCED: full user settings (RTK hook + pyright LSP)

Compares input/output tokens, cost, and quality of responses.

Usage:
    python scripts/benchmark_rtk_lsp.py [--runs N] [--tasks TASK_IDS] [--dry-run]

Examples:
    python scripts/benchmark_rtk_lsp.py                # Run all tasks once
    python scripts/benchmark_rtk_lsp.py --runs 3        # 3 runs per task (avg)
    python scripts/benchmark_rtk_lsp.py --tasks 1,3,5   # Only tasks 1, 3, 5
    python scripts/benchmark_rtk_lsp.py --dry-run       # Print commands without executing
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Task Definitions ─────────────────────────────────────────────────────────
# Each task targets different tool usage patterns to isolate RTK vs LSP impact.

TASKS: list[dict[str, str]] = [
    # ── RTK tasks: force Bash usage to measure compression ──────────────
    {
        "id": "1",
        "name": "bash_git_log",
        "category": "RTK",
        "prompt": (
            "You MUST use the Bash tool (not Read, Glob, or Grep) for ALL "
            "operations. Run: git log --oneline -30, then git diff --stat HEAD~1, "
            "then git status. Summarize the results."
        ),
    },
    {
        "id": "2",
        "name": "bash_find_files",
        "category": "RTK",
        "prompt": (
            "You MUST use the Bash tool (not Read, Glob, or Grep) for ALL "
            "operations. Run: find . -name '*.py' -type f | head -60, then "
            "wc -l schemas/*.py, then ls -la hooks/utils/. "
            "Summarize what you found."
        ),
    },
    {
        "id": "3",
        "name": "bash_grep_search",
        "category": "RTK",
        "prompt": (
            "You MUST use the Bash tool (not Read, Glob, or Grep) for ALL "
            "operations. Run: grep -rn 'ConfigDict' schemas/, then "
            "grep -rn 'class.*BaseModel' schemas/, then "
            "grep -rn 'def ' hooks/utils/event_logger.py. Summarize the results."
        ),
    },
    # ── LSP tasks: exercise code navigation ─────────────────────────────
    {
        "id": "4",
        "name": "lsp_find_definition",
        "category": "LSP",
        "prompt": (
            "Find where the class 'SystemState' is defined in this project. "
            "Then find all files that import or reference SystemState. "
            "Show the class fields and their types. "
            "Prefer using LSP tools (goToDefinition, findReferences, hover) "
            "when available."
        ),
    },
    {
        "id": "5",
        "name": "lsp_type_analysis",
        "category": "LSP",
        "prompt": (
            "Analyze schemas/hook_output.py using LSP tools when available. "
            "List all symbols (documentSymbol), get type info (hover) for "
            "key classes, and find references to HookOutput. "
            "If LSP is not available, fall back to Read/Grep."
        ),
    },
    {
        "id": "6",
        "name": "lsp_call_hierarchy",
        "category": "LSP",
        "prompt": (
            "Analyze hooks/utils/event_logger.py. Find all functions defined "
            "in it using documentSymbol or LSP if available. Then for each "
            "public function, find all callers using findReferences or "
            "incomingCalls. If LSP is unavailable, use Grep. "
            "Summarize the call graph."
        ),
    },
    # ── MIXED tasks: natural usage patterns ─────────────────────────────
    {
        "id": "7",
        "name": "mixed_exploration",
        "category": "MIXED",
        "prompt": (
            "Find all Pydantic models in schemas/ that use ConfigDict. "
            "For each, list the model name, ConfigDict settings, and field count. "
            "Use whatever tools you think are most efficient."
        ),
    },
    {
        "id": "8",
        "name": "mixed_refactor",
        "category": "MIXED",
        "prompt": (
            "Compare hooks/utils/event_logger.py and hooks/utils/error_logger.py. "
            "What's similar, what's different? Could they be consolidated? "
            "List specific overlapping functions. Use whatever tools are most "
            "efficient."
        ),
    },
]

# ── Configuration ─────────────────────────────────────────────────────────────

WORKDIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = WORKDIR / "reports" / "benchmark-rtk-lsp"


@dataclass
class RunResult:
    task_id: str
    task_name: str
    category: str
    mode: str  # "baseline" or "enhanced"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    response_text: str = ""
    raw_json: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def build_claude_cmd(
    mode: str,
    category: str,
) -> list[str]:
    """Build the claude CLI command for a benchmark run.

    Prompt is passed via stdin to avoid variadic option parsing issues.
    """
    cmd = [
        "claude",
        "-p",
        "--output-format", "json",
        "--dangerously-skip-permissions",
        "--no-session-persistence",
        "--model", "sonnet",
    ]

    if mode == "baseline":
        # Skip user settings → no RTK hook, no LSP plugins
        cmd.extend(["--setting-sources", "project,local"])
    else:
        # Enhanced: all settings (RTK hook + LSP from user config)
        cmd.extend(["--setting-sources", "user,project,local"])

    # For LSP category in baseline: ensure LSP tools are not used
    # (they won't be since user settings with enabledPlugins are skipped)
    # For RTK category: the hook rewrite is in user settings

    return cmd


def run_task(task: dict[str, str], mode: str, run_idx: int) -> RunResult:
    """Execute a single benchmark task and parse results."""
    result = RunResult(
        task_id=task["id"],
        task_name=task["name"],
        category=task["category"],
        mode=mode,
    )

    cmd = build_claude_cmd(mode, task["category"])
    label = f"[{mode.upper():>8}] Task {task['id']} ({task['name']}) run {run_idx}"
    print(f"  {label} ... ", end="", flush=True)

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            input=task["prompt"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(WORKDIR),
        )
        elapsed = time.monotonic() - start

        if proc.returncode != 0:
            result.error = f"exit {proc.returncode}: {proc.stderr[:200]}"
            print(f"FAIL ({result.error})")
            return result

        data = json.loads(proc.stdout)
        result.raw_json = data
        result.response_text = data.get("result", "")
        result.duration_ms = data.get("duration_ms", int(elapsed * 1000))
        result.num_turns = data.get("num_turns", 0)
        result.total_cost_usd = data.get("total_cost_usd", 0.0)

        usage = data.get("usage", {})
        result.input_tokens = usage.get("input_tokens", 0)
        result.output_tokens = usage.get("output_tokens", 0)
        result.cache_creation_tokens = usage.get(
            "cache_creation_input_tokens", 0
        )
        result.cache_read_tokens = usage.get("cache_read_input_tokens", 0)

        total_in = (
            result.input_tokens
            + result.cache_creation_tokens
            + result.cache_read_tokens
        )
        print(
            f"OK  in={total_in:,}  out={result.output_tokens:,}  "
            f"${result.total_cost_usd:.4f}  {result.duration_ms}ms"
        )

    except subprocess.TimeoutExpired:
        result.error = "timeout (120s)"
        print(f"TIMEOUT")
    except json.JSONDecodeError as e:
        result.error = f"json parse: {e}"
        print(f"JSON ERROR")
    except Exception as e:
        result.error = str(e)
        print(f"ERROR: {e}")

    return result


def aggregate_results(
    results: list[RunResult],
) -> dict[str, dict[str, Any]]:
    """Aggregate results by task and mode, averaging across runs."""
    agg: dict[str, dict[str, list[RunResult]]] = {}
    for r in results:
        key = r.task_id
        if key not in agg:
            agg[key] = {"baseline": [], "enhanced": []}
        agg[key][r.mode].append(r)

    summary: dict[str, dict[str, Any]] = {}
    for task_id, modes in sorted(agg.items()):
        for mode, runs in modes.items():
            valid = [r for r in runs if r.error is None]
            if not valid:
                continue
            n = len(valid)
            key = f"{task_id}_{mode}"
            summary[key] = {
                "task_id": task_id,
                "task_name": valid[0].task_name,
                "category": valid[0].category,
                "mode": mode,
                "runs": n,
                "avg_input_tokens": sum(r.input_tokens for r in valid) // n,
                "avg_output_tokens": sum(r.output_tokens for r in valid) // n,
                "avg_cache_creation": sum(
                    r.cache_creation_tokens for r in valid
                ) // n,
                "avg_cache_read": sum(r.cache_read_tokens for r in valid) // n,
                "avg_total_tokens": sum(
                    r.input_tokens
                    + r.output_tokens
                    + r.cache_creation_tokens
                    + r.cache_read_tokens
                    for r in valid
                ) // n,
                "avg_cost_usd": sum(r.total_cost_usd for r in valid) / n,
                "avg_duration_ms": sum(r.duration_ms for r in valid) // n,
                "avg_turns": sum(r.num_turns for r in valid) / n,
                # Work tokens: conversation content excluding base system
                # prompt. Approximated as output + non-creation input.
                # This isolates tool result size differences.
                "avg_work_tokens": sum(
                    r.input_tokens + r.output_tokens + r.cache_read_tokens
                    for r in valid
                ) // n,
            }
    return summary


def print_comparison(summary: dict[str, dict[str, Any]]) -> str:
    """Print side-by-side comparison and return as string."""
    lines: list[str] = []

    def p(s: str = "") -> None:
        lines.append(s)
        print(s)

    p("=" * 90)
    p("RTK + LSP BENCHMARK RESULTS")
    p("=" * 90)
    p()

    # Header
    p(
        f"{'Task':<22} {'Cat':<5} {'Mode':<9} "
        f"{'Turns':>5} {'Output':>7} {'Work':>8} "
        f"{'Total':>8} {'Cost':>8} {'Time':>8}"
    )
    p("-" * 90)

    # Group by task
    task_ids = sorted(set(v["task_id"] for v in summary.values()))
    total_baseline = 0
    total_enhanced = 0
    work_baseline = 0
    work_enhanced = 0
    cost_baseline = 0.0
    cost_enhanced = 0.0

    for tid in task_ids:
        bkey = f"{tid}_baseline"
        ekey = f"{tid}_enhanced"
        b = summary.get(bkey)
        e = summary.get(ekey)

        for data, mode_label in [(b, "baseline"), (e, "enhanced")]:
            if data is None:
                continue
            total = data["avg_total_tokens"]
            work = data["avg_work_tokens"]
            if mode_label == "baseline":
                total_baseline += total
                work_baseline += work
                cost_baseline += data["avg_cost_usd"]
            else:
                total_enhanced += total
                work_enhanced += work
                cost_enhanced += data["avg_cost_usd"]

            p(
                f"{data['task_name']:<22} {data['category']:<5} "
                f"{mode_label:<9} "
                f"{data['avg_turns']:>5.0f} "
                f"{data['avg_output_tokens']:>7,} "
                f"{data['avg_work_tokens']:>8,} "
                f"{data['avg_total_tokens']:>8,} "
                f"${data['avg_cost_usd']:>7.4f} "
                f"{data['avg_duration_ms']:>7,}ms"
            )

        # Delta row (based on work tokens — isolates tool result differences)
        if b and e:
            b_work = b["avg_work_tokens"]
            e_work = e["avg_work_tokens"]
            b_total = b["avg_total_tokens"]
            e_total = e["avg_total_tokens"]
            cost_saved = b["avg_cost_usd"] - e["avg_cost_usd"]

            work_diff = b_work - e_work
            work_pct = ((work_diff) / b_work * 100) if b_work else 0
            total_diff = b_total - e_total
            direction = "SAVED" if work_pct > 0 else "MORE"
            p(
                f"{'':>22} {'':>5} {'DELTA':<9} "
                f"{'':>5} {'':>7} "
                f"{work_diff:>+8,} "
                f"{total_diff:>+8,} "
                f"${cost_saved:>+7.4f} "
                f"  {abs(work_pct):.1f}% {direction}"
            )
        p()

    # Grand totals
    p("=" * 90)
    p("GRAND TOTALS")
    p()
    p("  Work tokens (excl. system prompt):")
    p(f"    Baseline: {work_baseline:>12,}")
    p(f"    Enhanced: {work_enhanced:>12,}")
    if work_baseline > 0:
        saved = work_baseline - work_enhanced
        pct = (saved / work_baseline) * 100
        p(f"    Delta:    {saved:>+12,} ({pct:+.1f}%)")
    p()
    p("  Total tokens (incl. system prompt):")
    p(f"    Baseline: {total_baseline:>12,}")
    p(f"    Enhanced: {total_enhanced:>12,}")
    if total_baseline > 0:
        saved = total_baseline - total_enhanced
        pct = (saved / total_baseline) * 100
        p(f"    Delta:    {saved:>+12,} ({pct:+.1f}%)")
    p()
    p("  Cost:")
    p(f"    Baseline: ${cost_baseline:>11.4f}")
    p(f"    Enhanced: ${cost_enhanced:>11.4f}")
    if cost_baseline > 0:
        cost_saved = cost_baseline - cost_enhanced
        cost_pct = (cost_saved / cost_baseline) * 100
        p(f"    Delta:    ${cost_saved:>+11.4f} ({cost_pct:+.1f}%)")
    p()

    # Category breakdown (work tokens)
    p("BY CATEGORY (work tokens)")
    for cat in ["RTK", "LSP", "MIXED"]:
        cat_b = sum(
            v["avg_work_tokens"]
            for v in summary.values()
            if v["category"] == cat and v["mode"] == "baseline"
        )
        cat_e = sum(
            v["avg_work_tokens"]
            for v in summary.values()
            if v["category"] == cat and v["mode"] == "enhanced"
        )
        if cat_b > 0:
            diff = cat_b - cat_e
            pct = (diff / cat_b) * 100
            p(
                f"  {cat:<8} baseline={cat_b:>8,}  enhanced={cat_e:>8,}  "
                f"delta={diff:>+8,} ({pct:+.1f}%)"
            )
    p("=" * 90)

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark RTK + LSP token savings"
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of runs per task per mode (default: 1)",
    )
    parser.add_argument(
        "--tasks",
        type=str,
        default=None,
        help="Comma-separated task IDs to run (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing",
    )
    args = parser.parse_args()

    # Filter tasks
    tasks = TASKS
    if args.tasks:
        task_ids = set(args.tasks.split(","))
        tasks = [t for t in TASKS if t["id"] in task_ids]

    if not tasks:
        print("No matching tasks found.")
        sys.exit(1)

    print(f"Benchmark: {len(tasks)} tasks x {args.runs} runs x 2 modes")
    print(f"Working directory: {WORKDIR}")
    print()

    if args.dry_run:
        for task in tasks:
            for mode in ["baseline", "enhanced"]:
                cmd = build_claude_cmd(mode, task["category"])
                print(f"Task {task['id']} ({task['name']}) [{mode}]:")
                print(f"  {' '.join(cmd[:8])} ...")
                print()
        return

    # Run benchmark
    all_results: list[RunResult] = []

    for run_idx in range(1, args.runs + 1):
        if args.runs > 1:
            print(f"\n{'─' * 40} Run {run_idx}/{args.runs} {'─' * 40}")

        for task in tasks:
            # Run baseline first, then enhanced
            for mode in ["baseline", "enhanced"]:
                result = run_task(task, mode, run_idx)
                all_results.append(result)
                # Brief pause between runs to avoid rate limiting
                time.sleep(2)

    # Aggregate and report
    print()
    summary = aggregate_results(all_results)
    report = print_comparison(summary)

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")

    # Save raw results
    raw_path = RESULTS_DIR / f"raw-{timestamp}.json"
    raw_data = []
    for r in all_results:
        raw_data.append({
            "task_id": r.task_id,
            "task_name": r.task_name,
            "category": r.category,
            "mode": r.mode,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "cache_creation_tokens": r.cache_creation_tokens,
            "cache_read_tokens": r.cache_read_tokens,
            "total_cost_usd": r.total_cost_usd,
            "duration_ms": r.duration_ms,
            "num_turns": r.num_turns,
            "error": r.error,
            "response_length": len(r.response_text),
        })
    raw_path.write_text(json.dumps(raw_data, indent=2))

    # Save report
    report_path = RESULTS_DIR / f"report-{timestamp}.txt"
    report_path.write_text(report)

    # Save summary
    summary_path = RESULTS_DIR / f"summary-{timestamp}.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    print(f"\nResults saved to: {RESULTS_DIR}/")
    print(f"  Raw data:  {raw_path.name}")
    print(f"  Report:    {report_path.name}")
    print(f"  Summary:   {summary_path.name}")


if __name__ == "__main__":
    main()
