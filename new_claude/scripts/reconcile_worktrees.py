#!/usr/bin/env python3
"""Startup Crash Recovery — Reconcile agent state vs actual git worktrees.

Compares agent state files (which track worktree assignments) against
`git worktree list --porcelain` (ground truth) to detect and fix:

1. Stale state: Agent state references a worktree that no longer exists on disk.
   → Clear the worktree/base_branch fields, release stale port allocation.

2. Orphan worktrees: A worktree exists on disk (matching our /tmp/wt-* pattern)
   but no agent state file references it.
   → Remove the orphan worktree and its branch.

3. Stale ports: Port allocation exists for an agent with no active worktree.
   → Release the port allocation.

Usage:
  python3 scripts/reconcile_worktrees.py [--dry-run]

Options:
  --dry-run  Report what would be done without making changes.

Design doc ref: 01-git-management.md, Section 2.6
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from hooks.utils.state_helpers import (
    AGENT_STATE_DIR,
    PORTS_DIR,
    ensure_dirs,
    locked_read_modify_write,
    read_json_safe,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CLAUDE_HOME = Path(os.path.expanduser("~/.claude"))
WORKTREE_LOG = _CLAUDE_HOME / "logs" / "worktree-lifecycle.jsonl"
WORKTREE_PATH_PREFIX = "/tmp/wt-"


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


def _get_actual_worktrees() -> dict[str, str]:
    """Parse `git worktree list --porcelain` to get path→branch mapping.

    Returns only worktrees matching our /tmp/wt-* pattern.
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}

    worktrees: dict[str, str] = {}
    current_path = ""
    current_branch = ""

    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = line[len("worktree ") :]
            current_branch = ""
        elif line.startswith("branch "):
            current_branch = line[len("branch ") :]
        elif line == "":
            # End of entry
            if current_path.startswith(WORKTREE_PATH_PREFIX):
                worktrees[current_path] = current_branch
            current_path = ""
            current_branch = ""

    # Handle last entry (no trailing blank line)
    if current_path.startswith(WORKTREE_PATH_PREFIX):
        worktrees[current_path] = current_branch

    return worktrees


def _get_state_worktrees() -> dict[str, str]:
    """Read all agent state files and return agent_id→worktree_path mapping.

    Only includes agents with a non-null worktree field.
    """
    state_map: dict[str, str] = {}
    if not AGENT_STATE_DIR.exists():
        return state_map

    for state_file in AGENT_STATE_DIR.glob("*.json"):
        data = read_json_safe(state_file)
        if data and data.get("worktree"):
            agent_id = data.get("id", state_file.stem)
            state_map[agent_id] = data["worktree"]

    return state_map


def _derive_agent_id_from_path(worktree_path: str) -> str:
    """Extract agent ID from worktree path (/tmp/wt-{agent-id} → agent-id)."""
    basename = Path(worktree_path).name
    if basename.startswith("wt-"):
        return basename[3:]
    return basename


def reconcile(dry_run: bool = False) -> dict[str, int]:
    """Run the reconciliation. Returns counts of actions taken.

    Compares:
    - State files (what agents think exists)
    - git worktree list (what actually exists)
    - Port allocations (what ports are reserved)

    Fixes:
    - Stale state entries (worktree gone from disk)
    - Orphan worktrees (no state file references them)
    - Stale port allocations (no active worktree)
    """
    ensure_dirs()
    stats = {
        "stale_state_cleared": 0,
        "orphan_worktrees_removed": 0,
        "stale_ports_released": 0,
    }

    actual_worktrees = _get_actual_worktrees()
    state_worktrees = _get_state_worktrees()

    actual_paths = set(actual_worktrees.keys())
    state_paths = set(state_worktrees.values())

    # --- 1. Stale state entries: state references a path not in actual ---
    for agent_id, state_path in state_worktrees.items():
        if state_path not in actual_paths:
            action = "would clear" if dry_run else "clearing"
            print(f"  Stale state: {agent_id} → {state_path} ({action})")

            if not dry_run:
                state_file = AGENT_STATE_DIR / f"{agent_id}.json"
                if state_file.exists():
                    locked_read_modify_write(
                        state_file,
                        lambda data: {**data, "worktree": None, "base_branch": None},
                    )
                # Also release any port for this agent
                port_file = PORTS_DIR / f"{agent_id}.json"
                if port_file.exists():
                    port_file.unlink()
                    stats["stale_ports_released"] += 1

                _log_lifecycle(
                    "stale_state_cleared", agent_id=agent_id, stale_path=state_path
                )

            stats["stale_state_cleared"] += 1

    # --- 2. Orphan worktrees: actual worktree not in any state file ---
    for actual_path, branch in actual_worktrees.items():
        if actual_path not in state_paths:
            agent_id = _derive_agent_id_from_path(actual_path)
            action = "would remove" if dry_run else "removing"
            print(f"  Orphan worktree: {actual_path} ({action})")

            if not dry_run:
                # Unlock + remove
                subprocess.run(
                    ["git", "worktree", "unlock", actual_path],
                    capture_output=True,
                    check=False,
                )
                result = subprocess.run(
                    ["git", "worktree", "remove", actual_path, "--force"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    print(f"    Warning: removal failed: {result.stderr.strip()}")

                # Delete branch if it looks like ours
                if branch:
                    branch_short = branch.removeprefix("refs/heads/")
                    subprocess.run(
                        ["git", "branch", "-D", branch_short],
                        capture_output=True,
                        check=False,
                    )

                # Release port if exists
                port_file = PORTS_DIR / f"{agent_id}.json"
                if port_file.exists():
                    port_file.unlink()
                    stats["stale_ports_released"] += 1

                _log_lifecycle(
                    "orphan_removed", agent_id=agent_id, path=actual_path, branch=branch
                )

            stats["orphan_worktrees_removed"] += 1

    # --- 3. Stale port allocations: port file exists but agent has no active worktree ---
    if PORTS_DIR.exists():
        active_agents_with_worktrees = {
            aid for aid, path in state_worktrees.items() if path in actual_paths
        }
        for port_file in PORTS_DIR.glob("*.json"):
            port_agent_id = port_file.stem
            if port_agent_id not in active_agents_with_worktrees:
                # Check if this port was already handled above
                if (
                    port_agent_id not in state_worktrees
                    or state_worktrees.get(port_agent_id) not in actual_paths
                ):
                    # Avoid double-counting if already released above
                    if port_file.exists():
                        action = "would release" if dry_run else "releasing"
                        print(f"  Stale port: {port_agent_id} ({action})")
                        if not dry_run:
                            port_file.unlink()
                            _log_lifecycle(
                                "stale_port_released", agent_id=port_agent_id
                            )
                        stats["stale_ports_released"] += 1

    return stats


def main() -> None:
    """Entry point for the reconciliation script."""
    dry_run = "--dry-run" in sys.argv

    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"Worktree reconciliation ({mode}):")

    stats = reconcile(dry_run=dry_run)

    total = sum(stats.values())
    if total == 0:
        print("  All clean — no stale state or orphan worktrees found.")
    else:
        print("\nSummary:")
        print(f"  Stale state entries cleared: {stats['stale_state_cleared']}")
        print(f"  Orphan worktrees removed: {stats['orphan_worktrees_removed']}")
        print(f"  Stale ports released: {stats['stale_ports_released']}")


if __name__ == "__main__":
    main()
