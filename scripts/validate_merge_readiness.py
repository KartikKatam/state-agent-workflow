#!/usr/bin/env python3
"""Pre-merge readiness validator.

Runs 4 checks before a coder worktree is merged into the base branch:
1. Quality gate (gate.sh) passes
2. No merge conflicts with base branch (dry-run merge)
3. Session log is complete (status, tests, quality gate)
4. No uncommitted changes in worktree

Usage:
    python scripts/validate_merge_readiness.py <worktree-path> <base-branch> [--session-log PATH]

Exit codes: 0 = all checks pass, 1 = one or more failed.

Design doc ref: "Validation Scripts" section.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Add project root to path for schema imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schemas.session_log import SessionLog

# ---------------------------------------------------------------------------
# Colors (matches gate.sh style)
# ---------------------------------------------------------------------------
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
BOLD = "\033[1m"
RESET = "\033[0m"

# ---------------------------------------------------------------------------
# Check runner
# ---------------------------------------------------------------------------

_failures = 0


def run_check(name: str, passed: bool, detail: str | None = None) -> bool:
    """Print check result and track failures."""
    global _failures  # noqa: PLW0603
    status = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
    print(f"{BOLD}▸ {name:<25}{RESET} {status}")
    if not passed:
        _failures += 1
        if detail:
            for line in detail.splitlines()[:30]:
                print(f"    {line}")
    return passed


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_quality_gate(worktree: Path) -> bool:
    """Run gate.sh in the worktree directory."""
    gate_script = Path(__file__).resolve().parent / "gate.sh"
    if not gate_script.exists():
        return run_check("quality-gate", False, "gate.sh not found")

    result = subprocess.run(
        [str(gate_script)],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        timeout=300,
    )
    return run_check(
        "quality-gate",
        result.returncode == 0,
        result.stdout + result.stderr if result.returncode != 0 else None,
    )


def check_merge_conflicts(worktree: Path, base_branch: str) -> bool:
    """Dry-run merge to detect conflicts."""
    # Fetch latest base branch
    fetch = subprocess.run(
        ["git", "fetch", "origin", base_branch],
        cwd=str(worktree),
        capture_output=True,
        text=True,
    )
    # Use local base_branch if fetch fails (e.g., no remote)
    merge_target = f"origin/{base_branch}" if fetch.returncode == 0 else base_branch

    result = subprocess.run(
        ["git", "merge", "--no-commit", "--no-ff", merge_target],
        cwd=str(worktree),
        capture_output=True,
        text=True,
    )

    # Always abort the merge attempt
    subprocess.run(
        ["git", "merge", "--abort"],
        cwd=str(worktree),
        capture_output=True,
    )

    if result.returncode != 0:
        detail = result.stdout + result.stderr
        # Distinguish merge conflicts from other errors
        if "CONFLICT" in detail:
            return run_check("merge-conflicts", False, detail)
        # Merge failed for another reason (e.g., already up to date is fine)
        if "Already up to date" in detail:
            return run_check("merge-conflicts", True)
        return run_check("merge-conflicts", False, detail)

    return run_check("merge-conflicts", True)


def check_session_log(session_log_path: Path | None, worktree: Path) -> bool:
    """Validate session log completeness."""
    # Auto-discover session log if not provided
    if session_log_path is None:
        logs_dir = worktree / ".claude" / "logs"
        if logs_dir.exists():
            candidates = sorted(
                logs_dir.glob("*-log.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            # Filter out planning logs
            candidates = [c for c in candidates if "-planning-log.json" not in c.name]
            if candidates:
                session_log_path = candidates[0]

    if session_log_path is None or not session_log_path.exists():
        return run_check("session-log", False, "No session log found")

    try:
        content = session_log_path.read_text()
        log = SessionLog.model_validate_json(content)
    except Exception as e:
        return run_check("session-log", False, f"Failed to parse session log: {e}")

    issues: list[str] = []

    # Status should indicate completion
    mergeable_statuses = {"verified", "approved", "committed"}
    if log.status not in mergeable_statuses:
        issues.append(
            f"status is '{log.status}', expected one of: {', '.join(sorted(mergeable_statuses))}"
        )

    # Tests should have been written
    if not log.tests_written:
        issues.append("tests_written is empty — no tests recorded")

    # Quality gate should have passed
    if log.quality_gate is None:
        issues.append("quality_gate is null — gate was never run")
    elif not log.quality_gate.passed:
        issues.append("quality_gate.passed is false")

    if issues:
        return run_check("session-log", False, "\n".join(f"- {i}" for i in issues))

    return run_check("session-log", True)


def check_clean_worktree(worktree: Path) -> bool:
    """Check for uncommitted changes."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(worktree),
        capture_output=True,
        text=True,
    )
    output = result.stdout.strip()
    if output:
        return run_check("clean-worktree", False, output)
    return run_check("clean-worktree", True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pre-merge readiness validator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("worktree_path", type=Path, help="Path to the coder worktree")
    parser.add_argument("base_branch", help="Base branch to merge into")
    parser.add_argument(
        "--session-log",
        type=Path,
        default=None,
        help="Path to session log JSON (auto-discovered if omitted)",
    )
    args = parser.parse_args()

    worktree = args.worktree_path.resolve()
    if not worktree.is_dir():
        print(
            f"{RED}ERROR: Worktree path does not exist: {worktree}{RESET}",
            file=sys.stderr,
        )
        return 1

    if not (worktree / ".git").exists() and not (worktree / ".git").is_file():
        print(f"{RED}ERROR: Not a git directory: {worktree}{RESET}", file=sys.stderr)
        return 1

    print()
    print(f"{BOLD}═══ Merge Readiness Check ═══{RESET}")
    print(f"    worktree: {worktree}")
    print(f"    base:     {args.base_branch}")
    print()

    check_quality_gate(worktree)
    check_merge_conflicts(worktree, args.base_branch)
    check_session_log(args.session_log, worktree)
    check_clean_worktree(worktree)

    print()
    if _failures > 0:
        print(
            f"{RED}{BOLD}✗ Merge readiness FAILED — {_failures} check(s) failed{RESET}"
        )
        return 1
    else:
        print(f"{GREEN}{BOLD}✓ Merge readiness PASSED — ready to merge{RESET}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
