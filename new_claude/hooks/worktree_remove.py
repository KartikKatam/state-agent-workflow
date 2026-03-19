#!/usr/bin/env python3
"""WorktreeRemove Hook — Cleans up worktrees, branches, ports, and agent state.

Fires when: Claude Code removes a subagent's worktree (auto-cleanup or manual)
Configured in: .claude/settings.json (WorktreeRemove hook)

IMPORTANT: This hook has NO decision control — it cannot block removal.
It runs for side effects only: cleanup, port release, state clear, logging.

Input (stdin JSON):
  {
    "session_id": "abc123",
    "hook_event_name": "WorktreeRemove",
    "worktree_path": "/tmp/wt-coder-p1-t3-a7f2"
  }

Output: None required (side-effect only hook).
Exit code: Failures are logged but do not block removal.

Design doc ref: 01-git-management.md, Section 2.5
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

from hooks.utils.error_logger import log_hook_error
from hooks.utils.state_helpers import (
    AGENT_STATE_DIR,
    PORTS_DIR,
    ensure_dirs,
    locked_read_modify_write,
    read_json_safe,
    read_stdin,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CLAUDE_HOME = Path(os.path.expanduser("~/.claude"))
WORKTREE_LOG = _CLAUDE_HOME / "logs" / "worktree-lifecycle.jsonl"


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


def _derive_agent_id(worktree_path: str) -> str:
    """Extract agent ID from worktree path.

    Expected format: /tmp/wt-{agent-id}
    Example: /tmp/wt-coder-p1-t3-a7f2 → coder-p1-t3-a7f2
    """
    basename = Path(worktree_path).name
    if basename.startswith("wt-"):
        return basename[3:]  # Strip "wt-" prefix
    return basename


def _run_git(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command. When check=False, failures are silent."""
    return subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        check=check,
    )


def _release_port(agent_id: str, project_root: str | None) -> None:
    """Release ports for the agent via the existing allocate-ports.sh script."""
    try:
        port_file = PORTS_DIR / f"{agent_id}.json"
        if port_file.exists():
            # Try the script first for a clean release
            if project_root:
                script = Path(project_root) / "scripts" / "allocate-ports.sh"
                if script.exists():
                    subprocess.run(
                        ["bash", str(script), "release", agent_id],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    _stderr(f"Released ports for {agent_id}")
                    return
            # Fallback: just delete the state file
            port_file.unlink(missing_ok=True)
            _stderr(f"Released ports for {agent_id} (direct file removal)")
    except Exception as e:
        _stderr(f"Port release failed for {agent_id}: {e}")
        log_hook_error("worktree_remove", "_release_port", Exception(str(e)))


def _cleanup_coder_worktree(
    agent_id: str, worktree_path: str, project_root: str | None
) -> None:
    """Tier 1 cleanup: full cleanup for coder agents.

    1. Check handoff_in_progress flag — skip worktree/branch removal if true
    2. Unlock and remove worktree
    3. Delete branch
    4. Release port
    5. Clear agent state (worktree field)
    6. Log lifecycle event
    """
    state_file = AGENT_STATE_DIR / f"{agent_id}.json"
    agent_state = read_json_safe(state_file)

    # --- Check handoff flag ---
    if agent_state and agent_state.get("pending_handoff"):
        _stderr(
            f"Handoff in progress for {agent_id} — skipping worktree/branch removal. "
            f"Replacement coder will inherit {worktree_path}."
        )
        _log_lifecycle(
            "remove_skipped_handoff",
            agent_id=agent_id,
            path=worktree_path,
        )
        return

    branch_name = f"agent/{agent_id}"

    # --- Unlock worktree ---
    _run_git(["worktree", "unlock", worktree_path], check=False)

    # --- Remove worktree ---
    result = _run_git(["worktree", "remove", worktree_path, "--force"], check=False)
    if result.returncode != 0:
        _stderr(f"git worktree remove failed: {result.stderr.strip()}")
        # Try harder — clean up manually if the directory exists
        wt_path = Path(worktree_path)
        if wt_path.exists():
            _stderr(f"Attempting manual cleanup of {worktree_path}")
            try:
                import shutil

                shutil.rmtree(worktree_path)
                # Prune to clean git's worktree bookkeeping
                _run_git(["worktree", "prune"], check=False)
            except Exception as e:
                _stderr(f"Manual cleanup failed: {e}")

    # --- Delete branch ---
    preserve = agent_state.get("preserve_branch", False) if agent_state else False
    if not preserve:
        result = _run_git(["branch", "-D", branch_name], check=False)
        if result.returncode != 0:
            _stderr(f"Branch deletion failed (may not exist): {result.stderr.strip()}")

    # --- Release port ---
    _release_port(agent_id, project_root)

    # --- Clear agent state worktree field ---
    if state_file.exists():
        locked_read_modify_write(
            state_file,
            lambda data: {**data, "worktree": None},
        )

    # --- Log lifecycle ---
    _log_lifecycle(
        "removed",
        agent_id=agent_id,
        path=worktree_path,
        branch=branch_name,
        branch_preserved=preserve,
    )
    _stderr(
        f"Worktree removed: {worktree_path} (branch {'preserved' if preserve else 'deleted'})"
    )


def _cleanup_native_worktree(agent_id: str, worktree_path: str) -> None:
    """Tier 2 cleanup: simple cleanup for non-coder agents."""
    branch_name = f"worktree-{agent_id}"

    # Unlock + remove
    _run_git(["worktree", "unlock", worktree_path], check=False)
    _run_git(["worktree", "remove", worktree_path, "--force"], check=False)

    # Delete branch
    _run_git(["branch", "-D", branch_name], check=False)

    _log_lifecycle("removed", agent_id=agent_id, path=worktree_path, tier="native")
    _stderr(f"Native worktree removed: {worktree_path}")


def main() -> None:
    """Entry point. Reads stdin, derives agent ID, routes cleanup."""
    try:
        ensure_dirs()
        data = read_stdin()
        worktree_path = data.get("worktree_path", "")
        cwd = data.get("cwd")

        if not worktree_path:
            _stderr("WorktreeRemove: no 'worktree_path' in input, skipping cleanup")
            return

        agent_id = _derive_agent_id(worktree_path)
        _stderr(f"WorktreeRemove: cleaning up {agent_id} at {worktree_path}")

        if agent_id.startswith("coder-"):
            _cleanup_coder_worktree(agent_id, worktree_path, cwd)
        else:
            _cleanup_native_worktree(agent_id, worktree_path)

    except Exception as e:
        log_hook_error("worktree_remove", "main", e)
        _stderr(f"WorktreeRemove hook failed: {e}")
        # Do NOT sys.exit(1) — removal hooks have no decision control


if __name__ == "__main__":
    main()
