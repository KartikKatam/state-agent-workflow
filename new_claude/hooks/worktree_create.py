#!/usr/bin/env python3
"""WorktreeCreate Hook — Allocates and configures worktrees for coder agents.

Fires when: Claude Code creates a subagent with `isolation: worktree`
Configured in: .claude/settings.json (WorktreeCreate hook)

Two tiers handled:
  Tier 1 (coders): Full custom logic — capacity check, base branch control,
    rerere, port allocation, state tracking, lifecycle logging.
  Tier 2 (planners): Native-equivalent — simple worktree, no extras.

Input (stdin JSON):
  {
    "session_id": "abc123",
    "hook_event_name": "WorktreeCreate",
    "name": "coder-p1-t3-a7f2",
    "cwd": "/home/user/project",
    "transcript_path": "..."
  }

Output: Absolute path to the created worktree (ONLY thing on stdout).
Exit code: Non-zero blocks worktree creation (used for capacity enforcement).

Design doc ref: 01-git-management.md, Section 2.4
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — ensure project root is importable
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from hooks.utils.error_logger import log_hook_error
from hooks.utils.state_helpers import (
    AGENT_STATE_DIR,
    ensure_dirs,
    locked_read_modify_write,
    read_json_safe,
    read_stdin,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CLAUDE_HOME = Path(os.path.expanduser("~/.claude"))
WORKFLOW_STATE_FILE = _CLAUDE_HOME / "state" / "workflow.json"
WORKTREE_LOG = _CLAUDE_HOME / "logs" / "worktree-lifecycle.jsonl"
MAX_CODER_WORKTREES = 4
DEFAULT_BASE_BRANCH = "main"


def _log_lifecycle(event: str, **kwargs: object) -> None:
    """Append a worktree lifecycle event to the JSONL log. Fire-and-forget."""
    try:
        WORKTREE_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        entry.update(kwargs)
        with WORKTREE_LOG.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


def _stderr(msg: str) -> None:
    """Write diagnostic message to stderr (never stdout)."""
    print(msg, file=sys.stderr)


def _get_base_branch() -> str:
    """Read the workflow base branch from WorkflowState, falling back to DEFAULT."""
    state = read_json_safe(WORKFLOW_STATE_FILE)
    if state:
        return state.get("base_branch", DEFAULT_BASE_BRANCH)
    return DEFAULT_BASE_BRANCH


def _count_active_coder_worktrees() -> int:
    """Count agents with a non-null worktree field whose ID starts with 'coder-'."""
    count = 0
    if not AGENT_STATE_DIR.exists():
        return 0
    for state_file in AGENT_STATE_DIR.glob("coder-*.json"):
        data = read_json_safe(state_file)
        if data and data.get("worktree"):
            count += 1
    return count


def _run_git(
    args: list[str], cwd: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a git command, raising on failure."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _allocate_port(agent_id: str, project_root: str) -> None:
    """Allocate ports for the agent via the existing allocate-ports.sh script."""
    try:
        script = Path(project_root) / "scripts" / "allocate-ports.sh"
        if script.exists():
            subprocess.run(
                ["bash", str(script), "allocate", agent_id],
                capture_output=True,
                text=True,
                check=True,
            )
            _stderr(f"Allocated ports for {agent_id}")
        else:
            _stderr(f"Port allocation script not found at {script}")
    except subprocess.CalledProcessError as e:
        _stderr(f"Port allocation failed: {e.stderr}")
        log_hook_error("worktree_create", "_allocate_port", Exception(e.stderr))


def _create_coder_worktree(name: str, cwd: str) -> str:
    """Tier 1: Full custom worktree for coder agents.

    1. Check capacity (max 4 concurrent coder worktrees)
    2. Create worktree with --lock, branching from workflow base branch
    3. Configure rerere and gc
    4. Update agent state
    5. Allocate port
    6. Log lifecycle event
    7. Return absolute path
    """
    agent_id = name

    # --- Capacity check ---
    active_count = _count_active_coder_worktrees()
    if active_count >= MAX_CODER_WORKTREES:
        _stderr(
            f"CAPACITY LIMIT: {active_count}/{MAX_CODER_WORKTREES} coder worktrees active. "
            f"Cannot create worktree for {agent_id}."
        )
        _log_lifecycle(
            "capacity_rejected",
            agent_id=agent_id,
            active_count=active_count,
            limit=MAX_CODER_WORKTREES,
        )
        sys.exit(1)

    # --- Derive paths ---
    base_branch = _get_base_branch()
    branch_name = f"agent/{name}"
    worktree_path = f"/tmp/wt-{name}"

    # --- Create worktree ---
    try:
        _run_git(
            [
                "worktree",
                "add",
                "--lock",
                worktree_path,
                "-b",
                branch_name,
                base_branch,
            ],
            cwd=cwd,
        )
    except subprocess.CalledProcessError as e:
        _stderr(f"git worktree add failed: {e.stderr}")
        _log_lifecycle(
            "create_failed",
            agent_id=agent_id,
            branch=branch_name,
            base_branch=base_branch,
            error=e.stderr.strip(),
        )
        sys.exit(1)

    # --- Configure git in worktree ---
    try:
        _run_git(["-C", worktree_path, "config", "rerere.enabled", "true"])
        _run_git(["-C", worktree_path, "config", "gc.worktreePruneExpire", "never"])
    except subprocess.CalledProcessError as e:
        _stderr(f"git config failed (non-fatal): {e.stderr}")

    # --- Update agent state ---
    state_file = AGENT_STATE_DIR / f"{agent_id}.json"
    if state_file.exists():
        locked_read_modify_write(
            state_file,
            lambda data: {
                **data,
                "worktree": worktree_path,
                "base_branch": base_branch,
            },
        )
    else:
        _stderr(f"Agent state file not found: {state_file} (worktree still created)")

    # --- Allocate port ---
    _allocate_port(agent_id, cwd)

    # --- Log lifecycle ---
    _log_lifecycle(
        "created",
        agent_id=agent_id,
        path=worktree_path,
        branch=branch_name,
        base_branch=base_branch,
    )

    _stderr(
        f"Worktree created: {worktree_path} (branch: {branch_name}, base: {base_branch})"
    )
    return worktree_path


def _create_native_worktree(name: str, cwd: str) -> str:
    """Tier 2: Native-equivalent worktree for non-coder agents (e.g. planners).

    Simple worktree creation without capacity limits, ports, or rerere.
    """
    worktree_path = f"/tmp/wt-{name}"
    branch_name = f"worktree-{name}"

    try:
        _run_git(
            ["worktree", "add", worktree_path, "-b", branch_name, "HEAD"],
            cwd=cwd,
        )
    except subprocess.CalledProcessError as e:
        _stderr(f"git worktree add failed: {e.stderr}")
        _log_lifecycle("create_failed", agent_id=name, error=e.stderr.strip())
        sys.exit(1)

    _log_lifecycle(
        "created", agent_id=name, path=worktree_path, branch=branch_name, tier="native"
    )
    _stderr(f"Native worktree created: {worktree_path} (branch: {branch_name})")
    return worktree_path


def main() -> None:
    """Entry point. Reads stdin, routes to tier 1 or tier 2, prints path."""
    try:
        ensure_dirs()
        data = read_stdin()
        name = data.get("name", "")
        cwd = data.get("cwd", os.getcwd())

        if not name:
            _stderr("WorktreeCreate: no 'name' in input, cannot create worktree")
            sys.exit(1)

        # Route based on agent name prefix
        if name.startswith("coder-"):
            path = _create_coder_worktree(name, cwd)
        else:
            path = _create_native_worktree(name, cwd)

        # Output ONLY the absolute path to stdout
        print(path)

    except SystemExit:
        raise
    except Exception as e:
        log_hook_error("worktree_create", "main", e)
        _stderr(f"WorktreeCreate hook failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
