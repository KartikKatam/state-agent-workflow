"""Tests for worktree create/remove hooks and reconciliation script.

Tests mock subprocess.run (git commands) and filesystem to verify:
- Capacity checking (max 4 coder worktrees)
- Correct git worktree add invocations (--lock, branch names, base branch)
- Git config (rerere, gc.worktreePruneExpire)
- Agent state updates (worktree, base_branch fields)
- Port allocation delegation
- Lifecycle logging
- Handoff flag checking (skip removal when pending_handoff=True)
- Crash recovery reconciliation
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — ensure project root is importable
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def state_dirs(tmp_path: Path) -> dict[str, Path]:
    """Create temporary state directories mimicking ~/.claude/state/."""
    agents_dir = tmp_path / "state" / "agents"
    ports_dir = tmp_path / "state" / "ports"
    logs_dir = tmp_path / "logs"
    for d in [agents_dir, ports_dir, logs_dir]:
        d.mkdir(parents=True)
    return {
        "agents": agents_dir,
        "ports": ports_dir,
        "logs": logs_dir,
        "root": tmp_path,
    }


@pytest.fixture()
def _patch_dirs(state_dirs: dict[str, Path]):
    """Patch state directory constants to use tmp_path."""
    with (
        patch("new_claude.hooks.worktree_create.AGENT_STATE_DIR", state_dirs["agents"]),
        patch(
            "new_claude.hooks.worktree_create.WORKTREE_LOG",
            state_dirs["logs"] / "worktree-lifecycle.jsonl",
        ),
        patch(
            "new_claude.hooks.worktree_create.WORKFLOW_STATE_FILE",
            state_dirs["root"] / "workflow.json",
        ),
        patch("new_claude.hooks.worktree_create.ensure_dirs"),
    ):
        yield


@pytest.fixture()
def _patch_remove_dirs(state_dirs: dict[str, Path]):
    """Patch state directory constants for remove hook."""
    with (
        patch("new_claude.hooks.worktree_remove.AGENT_STATE_DIR", state_dirs["agents"]),
        patch("new_claude.hooks.worktree_remove.PORTS_DIR", state_dirs["ports"]),
        patch(
            "new_claude.hooks.worktree_remove.WORKTREE_LOG",
            state_dirs["logs"] / "worktree-lifecycle.jsonl",
        ),
        patch("new_claude.hooks.worktree_remove.ensure_dirs"),
    ):
        yield


@pytest.fixture()
def _patch_reconcile_dirs(state_dirs: dict[str, Path]):
    """Patch state directory constants for reconciliation script."""
    with (
        patch("new_claude.scripts.reconcile_worktrees.AGENT_STATE_DIR", state_dirs["agents"]),
        patch("new_claude.scripts.reconcile_worktrees.PORTS_DIR", state_dirs["ports"]),
        patch(
            "new_claude.scripts.reconcile_worktrees.WORKTREE_LOG",
            state_dirs["logs"] / "worktree-lifecycle.jsonl",
        ),
        patch("new_claude.scripts.reconcile_worktrees.ensure_dirs"),
    ):
        yield


def _write_agent_state(agents_dir: Path, agent_id: str, **overrides: object) -> Path:
    """Write a minimal agent state file and return its path."""
    state = {
        "id": agent_id,
        "role": "coder",
        "model": "sonnet-4-6",
        "spawned_at": "2026-03-01T00:00:00Z",
        "status": "active",
        "current_state": "TASK_CLAIMED",
        "worktree": None,
        "base_branch": None,
        **overrides,
    }
    path = agents_dir / f"{agent_id}.json"
    path.write_text(json.dumps(state, indent=2))
    return path


def _write_workflow_state(root: Path, base_branch: str = "feature/test") -> Path:
    """Write a minimal workflow state file."""
    state = {
        "workflow_id": "wf-test-001",
        "feature": "test",
        "base_branch": base_branch,
        "current_state": "PHASE_IMPLEMENTATION",
        "started_at": "2026-03-01T00:00:00Z",
    }
    path = root / "workflow.json"
    path.write_text(json.dumps(state, indent=2))
    return path


# ===========================================================================
# WorktreeCreate tests
# ===========================================================================


class TestWorktreeCreate:
    """Tests for hooks/worktree_create.py."""

    @pytest.mark.usefixtures("_patch_dirs")
    def test_coder_worktree_creation(self, state_dirs: dict[str, Path]) -> None:
        """Coder worktree: creates with --lock, correct branch, base branch from workflow."""
        from new_claude.hooks.worktree_create import _create_coder_worktree

        _write_agent_state(state_dirs["agents"], "coder-p1-t3-a7f2")
        _write_workflow_state(state_dirs["root"], base_branch="feature/lpr-tracking")

        with (
            patch("new_claude.hooks.worktree_create._run_git") as mock_git,
            patch("new_claude.hooks.worktree_create._allocate_port"),
        ):
            mock_git.return_value = MagicMock(returncode=0)
            path = _create_coder_worktree("coder-p1-t3-a7f2", "/home/user/project")

        assert path == "/tmp/wt-coder-p1-t3-a7f2"

        # Verify git worktree add was called with correct args
        create_call = mock_git.call_args_list[0]
        args = create_call[0][0]
        assert args == [
            "worktree",
            "add",
            "--lock",
            "/tmp/wt-coder-p1-t3-a7f2",
            "-b",
            "agent/coder-p1-t3-a7f2",
            "feature/lpr-tracking",
        ]
        assert create_call[1]["cwd"] == "/home/user/project"

    @pytest.mark.usefixtures("_patch_dirs")
    def test_coder_worktree_rerere_config(self, state_dirs: dict[str, Path]) -> None:
        """Coder worktree: sets rerere.enabled and gc.worktreePruneExpire."""
        from new_claude.hooks.worktree_create import _create_coder_worktree

        _write_agent_state(state_dirs["agents"], "coder-p1-t3-a7f2")
        _write_workflow_state(state_dirs["root"])

        with (
            patch("new_claude.hooks.worktree_create._run_git") as mock_git,
            patch("new_claude.hooks.worktree_create._allocate_port"),
        ):
            mock_git.return_value = MagicMock(returncode=0)
            _create_coder_worktree("coder-p1-t3-a7f2", "/home/user/project")

        # Second call: rerere config
        rerere_call = mock_git.call_args_list[1]
        assert rerere_call[0][0] == [
            "-C",
            "/tmp/wt-coder-p1-t3-a7f2",
            "config",
            "rerere.enabled",
            "true",
        ]

        # Third call: gc config
        gc_call = mock_git.call_args_list[2]
        assert gc_call[0][0] == [
            "-C",
            "/tmp/wt-coder-p1-t3-a7f2",
            "config",
            "gc.worktreePruneExpire",
            "never",
        ]

    @pytest.mark.usefixtures("_patch_dirs")
    def test_coder_worktree_updates_agent_state(
        self, state_dirs: dict[str, Path]
    ) -> None:
        """Coder worktree: updates agent state with worktree path and base_branch."""
        from new_claude.hooks.worktree_create import _create_coder_worktree

        _write_agent_state(state_dirs["agents"], "coder-p1-t3-a7f2")
        _write_workflow_state(state_dirs["root"], base_branch="feature/lpr-tracking")

        with (
            patch("new_claude.hooks.worktree_create._run_git") as mock_git,
            patch("new_claude.hooks.worktree_create._allocate_port"),
        ):
            mock_git.return_value = MagicMock(returncode=0)
            _create_coder_worktree("coder-p1-t3-a7f2", "/home/user/project")

        # Read updated state
        state = json.loads((state_dirs["agents"] / "coder-p1-t3-a7f2.json").read_text())
        assert state["worktree"] == "/tmp/wt-coder-p1-t3-a7f2"
        assert state["base_branch"] == "feature/lpr-tracking"

    @pytest.mark.usefixtures("_patch_dirs")
    def test_capacity_limit_blocks_creation(self, state_dirs: dict[str, Path]) -> None:
        """Coder worktree: exits non-zero when 4 coders already have worktrees."""
        from new_claude.hooks.worktree_create import _create_coder_worktree

        # Create 4 existing coder states with active worktrees
        for i in range(4):
            _write_agent_state(
                state_dirs["agents"],
                f"coder-p1-t{i}-{i:04x}",
                worktree=f"/tmp/wt-coder-p1-t{i}-{i:04x}",
            )

        with pytest.raises(SystemExit) as exc_info:
            _create_coder_worktree("coder-p1-t5-a7f2", "/home/user/project")

        assert exc_info.value.code == 1

    @pytest.mark.usefixtures("_patch_dirs")
    def test_capacity_check_counts_only_coders(
        self, state_dirs: dict[str, Path]
    ) -> None:
        """Capacity check only counts coder-* agents, not explorers or researchers."""
        from new_claude.hooks.worktree_create import _count_active_coder_worktrees

        # Non-coder agents with worktrees should not count
        _write_agent_state(
            state_dirs["agents"],
            "explorer-p1-init-c4d9",
            role="explorer",
            worktree="/tmp/wt-explorer",
        )
        # 2 coders with worktrees
        _write_agent_state(
            state_dirs["agents"],
            "coder-p1-t1-0001",
            worktree="/tmp/wt-coder-p1-t1-0001",
        )
        _write_agent_state(
            state_dirs["agents"],
            "coder-p1-t2-0002",
            worktree="/tmp/wt-coder-p1-t2-0002",
        )
        # 1 coder without worktree (terminated)
        _write_agent_state(state_dirs["agents"], "coder-p1-t3-0003")

        assert _count_active_coder_worktrees() == 2

    @pytest.mark.usefixtures("_patch_dirs")
    def test_base_branch_defaults_to_main(self, state_dirs: dict[str, Path]) -> None:
        """When no workflow state exists, base_branch defaults to 'main'."""
        from new_claude.hooks.worktree_create import _get_base_branch

        assert _get_base_branch() == "main"

    @pytest.mark.usefixtures("_patch_dirs")
    def test_base_branch_from_workflow_state(self, state_dirs: dict[str, Path]) -> None:
        """Base branch comes from WorkflowState when available."""
        from new_claude.hooks.worktree_create import _get_base_branch

        _write_workflow_state(state_dirs["root"], base_branch="feature/custom")
        assert _get_base_branch() == "feature/custom"

    @pytest.mark.usefixtures("_patch_dirs")
    def test_native_worktree_for_planner(self, state_dirs: dict[str, Path]) -> None:
        """Tier 2: Non-coder agents get simple worktrees without extras."""
        from new_claude.hooks.worktree_create import _create_native_worktree

        with patch("new_claude.hooks.worktree_create._run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0)
            path = _create_native_worktree(
                "planner-p1-design-b3e1", "/home/user/project"
            )

        assert path == "/tmp/wt-planner-p1-design-b3e1"

        create_call = mock_git.call_args_list[0]
        args = create_call[0][0]
        assert args == [
            "worktree",
            "add",
            "/tmp/wt-planner-p1-design-b3e1",
            "-b",
            "worktree-planner-p1-design-b3e1",
            "HEAD",
        ]

    @pytest.mark.usefixtures("_patch_dirs")
    def test_lifecycle_log_written(self, state_dirs: dict[str, Path]) -> None:
        """Lifecycle events are logged to the JSONL log file."""
        from new_claude.hooks.worktree_create import _create_coder_worktree

        _write_agent_state(state_dirs["agents"], "coder-p1-t3-a7f2")
        _write_workflow_state(state_dirs["root"])

        with (
            patch("new_claude.hooks.worktree_create._run_git") as mock_git,
            patch("new_claude.hooks.worktree_create._allocate_port"),
        ):
            mock_git.return_value = MagicMock(returncode=0)
            _create_coder_worktree("coder-p1-t3-a7f2", "/home/user/project")

        log_file = state_dirs["logs"] / "worktree-lifecycle.jsonl"
        assert log_file.exists()
        entries = [
            json.loads(line) for line in log_file.read_text().strip().splitlines()
        ]
        assert len(entries) >= 1
        assert entries[-1]["event"] == "created"
        assert entries[-1]["agent_id"] == "coder-p1-t3-a7f2"

    @pytest.mark.usefixtures("_patch_dirs")
    def test_port_allocation_called(self, state_dirs: dict[str, Path]) -> None:
        """Port allocation is invoked for coder worktrees."""
        from new_claude.hooks.worktree_create import _create_coder_worktree

        _write_agent_state(state_dirs["agents"], "coder-p1-t3-a7f2")
        _write_workflow_state(state_dirs["root"])

        with (
            patch("new_claude.hooks.worktree_create._run_git") as mock_git,
            patch("new_claude.hooks.worktree_create._allocate_port") as mock_port,
        ):
            mock_git.return_value = MagicMock(returncode=0)
            _create_coder_worktree("coder-p1-t3-a7f2", "/home/user/project")

        mock_port.assert_called_once_with("coder-p1-t3-a7f2", "/home/user/project")

    @pytest.mark.usefixtures("_patch_dirs")
    def test_git_failure_exits_nonzero(self, state_dirs: dict[str, Path]) -> None:
        """When git worktree add fails, hook exits non-zero."""
        from new_claude.hooks.worktree_create import _create_coder_worktree

        _write_agent_state(state_dirs["agents"], "coder-p1-t3-a7f2")
        _write_workflow_state(state_dirs["root"])

        with patch("new_claude.hooks.worktree_create._run_git") as mock_git:
            mock_git.side_effect = subprocess.CalledProcessError(
                1, "git", stderr="fatal: already exists"
            )
            with pytest.raises(SystemExit) as exc_info:
                _create_coder_worktree("coder-p1-t3-a7f2", "/home/user/project")

        assert exc_info.value.code == 1


# ===========================================================================
# WorktreeRemove tests
# ===========================================================================


class TestWorktreeRemove:
    """Tests for hooks/worktree_remove.py."""

    @pytest.mark.usefixtures("_patch_remove_dirs")
    def test_coder_cleanup_full(self, state_dirs: dict[str, Path]) -> None:
        """Full cleanup: unlock, remove worktree, delete branch, release port, clear state."""
        from new_claude.hooks.worktree_remove import _cleanup_coder_worktree

        state_path = _write_agent_state(
            state_dirs["agents"],
            "coder-p1-t3-a7f2",
            worktree="/tmp/wt-coder-p1-t3-a7f2",
        )
        # Create a port file
        port_file = state_dirs["ports"] / "coder-p1-t3-a7f2.json"
        port_file.write_text(
            json.dumps({"agent_id": "coder-p1-t3-a7f2", "ports": [30000]})
        )

        with (
            patch("new_claude.hooks.worktree_remove._run_git") as mock_git,
            patch("new_claude.hooks.worktree_remove._release_port") as mock_port,
        ):
            mock_git.return_value = MagicMock(returncode=0)
            _cleanup_coder_worktree(
                "coder-p1-t3-a7f2", "/tmp/wt-coder-p1-t3-a7f2", "/home/user/project"
            )

        # Verify unlock was called
        unlock_call = mock_git.call_args_list[0]
        assert unlock_call[0][0] == ["worktree", "unlock", "/tmp/wt-coder-p1-t3-a7f2"]

        # Verify remove was called
        remove_call = mock_git.call_args_list[1]
        assert remove_call[0][0] == [
            "worktree",
            "remove",
            "/tmp/wt-coder-p1-t3-a7f2",
            "--force",
        ]

        # Verify branch delete
        branch_call = mock_git.call_args_list[2]
        assert branch_call[0][0] == ["branch", "-D", "agent/coder-p1-t3-a7f2"]

        # Verify port release
        mock_port.assert_called_once()

        # Verify state cleared
        state = json.loads(state_path.read_text())
        assert state["worktree"] is None

    @pytest.mark.usefixtures("_patch_remove_dirs")
    def test_handoff_skips_removal(self, state_dirs: dict[str, Path]) -> None:
        """When pending_handoff is True, worktree/branch removal is skipped."""
        from new_claude.hooks.worktree_remove import _cleanup_coder_worktree

        _write_agent_state(
            state_dirs["agents"],
            "coder-p1-t3-a7f2",
            worktree="/tmp/wt-coder-p1-t3-a7f2",
            pending_handoff=True,
        )

        with patch("new_claude.hooks.worktree_remove._run_git") as mock_git:
            _cleanup_coder_worktree(
                "coder-p1-t3-a7f2", "/tmp/wt-coder-p1-t3-a7f2", "/home/user/project"
            )

        # Git commands should NOT be called
        mock_git.assert_not_called()

    @pytest.mark.usefixtures("_patch_remove_dirs")
    def test_derive_agent_id(self) -> None:
        """Agent ID is correctly derived from worktree path."""
        from new_claude.hooks.worktree_remove import _derive_agent_id

        assert _derive_agent_id("/tmp/wt-coder-p1-t3-a7f2") == "coder-p1-t3-a7f2"
        assert (
            _derive_agent_id("/tmp/wt-planner-p1-design-b3e1")
            == "planner-p1-design-b3e1"
        )

    @pytest.mark.usefixtures("_patch_remove_dirs")
    def test_native_cleanup(self, state_dirs: dict[str, Path]) -> None:
        """Tier 2 cleanup: unlock, remove, delete branch."""
        from new_claude.hooks.worktree_remove import _cleanup_native_worktree

        with patch("new_claude.hooks.worktree_remove._run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0)
            _cleanup_native_worktree(
                "planner-p1-design-b3e1", "/tmp/wt-planner-p1-design-b3e1"
            )

        # Should have 3 calls: unlock, remove, branch -D
        assert len(mock_git.call_args_list) == 3

    @pytest.mark.usefixtures("_patch_remove_dirs")
    def test_lifecycle_log_on_remove(self, state_dirs: dict[str, Path]) -> None:
        """Lifecycle event is logged when a worktree is removed."""
        from new_claude.hooks.worktree_remove import _cleanup_coder_worktree

        _write_agent_state(
            state_dirs["agents"],
            "coder-p1-t3-a7f2",
            worktree="/tmp/wt-coder-p1-t3-a7f2",
        )

        with (
            patch("new_claude.hooks.worktree_remove._run_git") as mock_git,
            patch("new_claude.hooks.worktree_remove._release_port"),
        ):
            mock_git.return_value = MagicMock(returncode=0)
            _cleanup_coder_worktree(
                "coder-p1-t3-a7f2", "/tmp/wt-coder-p1-t3-a7f2", "/home/user/project"
            )

        log_file = state_dirs["logs"] / "worktree-lifecycle.jsonl"
        assert log_file.exists()
        entries = [
            json.loads(line) for line in log_file.read_text().strip().splitlines()
        ]
        assert any(e["event"] == "removed" for e in entries)

    @pytest.mark.usefixtures("_patch_remove_dirs")
    def test_remove_never_exits_nonzero(self, state_dirs: dict[str, Path]) -> None:
        """WorktreeRemove hook never raises or exits non-zero (no decision control)."""
        from new_claude.hooks.worktree_remove import main

        with patch("new_claude.hooks.worktree_remove.read_stdin") as mock_stdin:
            mock_stdin.return_value = {
                "worktree_path": "/tmp/wt-coder-p1-t3-a7f2",
            }
            # Even if cleanup methods fail, main should not exit non-zero
            with patch("new_claude.hooks.worktree_remove._cleanup_coder_worktree") as mock_cleanup:
                mock_cleanup.side_effect = Exception("unexpected error")
                # Should not raise
                main()


# ===========================================================================
# Reconciliation tests
# ===========================================================================


class TestReconcileWorktrees:
    """Tests for scripts/reconcile_worktrees.py."""

    @pytest.mark.usefixtures("_patch_reconcile_dirs")
    def test_stale_state_cleared(self, state_dirs: dict[str, Path]) -> None:
        """State referencing a non-existent worktree is cleared."""
        from new_claude.scripts.reconcile_worktrees import reconcile

        # Agent state says worktree exists, but git says it doesn't
        _write_agent_state(
            state_dirs["agents"],
            "coder-p1-t3-a7f2",
            worktree="/tmp/wt-coder-p1-t3-a7f2",
            base_branch="feature/test",
        )

        with patch("new_claude.scripts.reconcile_worktrees._get_actual_worktrees") as mock_actual:
            mock_actual.return_value = {}  # No worktrees exist on disk
            stats = reconcile(dry_run=False)

        assert stats["stale_state_cleared"] == 1

        # Verify state was cleared
        state = json.loads((state_dirs["agents"] / "coder-p1-t3-a7f2.json").read_text())
        assert state["worktree"] is None
        assert state["base_branch"] is None

    @pytest.mark.usefixtures("_patch_reconcile_dirs")
    def test_orphan_worktree_removed(self, state_dirs: dict[str, Path]) -> None:
        """Worktree on disk with no corresponding state is removed."""
        from new_claude.scripts.reconcile_worktrees import reconcile

        with (
            patch("new_claude.scripts.reconcile_worktrees._get_actual_worktrees") as mock_actual,
            patch("subprocess.run") as mock_run,
        ):
            mock_actual.return_value = {
                "/tmp/wt-coder-p1-t5-dead": "refs/heads/agent/coder-p1-t5-dead"
            }
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            stats = reconcile(dry_run=False)

        assert stats["orphan_worktrees_removed"] == 1

    @pytest.mark.usefixtures("_patch_reconcile_dirs")
    def test_stale_port_released(self, state_dirs: dict[str, Path]) -> None:
        """Port allocation for agent with no active worktree is released."""
        from new_claude.scripts.reconcile_worktrees import reconcile

        # Port file exists but no agent state references a worktree
        port_file = state_dirs["ports"] / "coder-p1-t3-a7f2.json"
        port_file.write_text(
            json.dumps({"agent_id": "coder-p1-t3-a7f2", "ports": [30000]})
        )

        with patch("new_claude.scripts.reconcile_worktrees._get_actual_worktrees") as mock_actual:
            mock_actual.return_value = {}
            stats = reconcile(dry_run=False)

        assert stats["stale_ports_released"] >= 1
        assert not port_file.exists()

    @pytest.mark.usefixtures("_patch_reconcile_dirs")
    def test_dry_run_no_changes(self, state_dirs: dict[str, Path]) -> None:
        """Dry run reports but does not modify anything."""
        from new_claude.scripts.reconcile_worktrees import reconcile

        state_path = _write_agent_state(
            state_dirs["agents"],
            "coder-p1-t3-a7f2",
            worktree="/tmp/wt-coder-p1-t3-a7f2",
        )

        with patch("new_claude.scripts.reconcile_worktrees._get_actual_worktrees") as mock_actual:
            mock_actual.return_value = {}
            stats = reconcile(dry_run=True)

        assert stats["stale_state_cleared"] == 1
        # But state should NOT be modified
        state = json.loads(state_path.read_text())
        assert state["worktree"] == "/tmp/wt-coder-p1-t3-a7f2"

    @pytest.mark.usefixtures("_patch_reconcile_dirs")
    def test_clean_state_no_actions(self, state_dirs: dict[str, Path]) -> None:
        """When state matches reality, no actions are taken."""
        from new_claude.scripts.reconcile_worktrees import reconcile

        _write_agent_state(
            state_dirs["agents"],
            "coder-p1-t3-a7f2",
            worktree="/tmp/wt-coder-p1-t3-a7f2",
        )

        with patch("new_claude.scripts.reconcile_worktrees._get_actual_worktrees") as mock_actual:
            mock_actual.return_value = {
                "/tmp/wt-coder-p1-t3-a7f2": "refs/heads/agent/coder-p1-t3-a7f2"
            }
            stats = reconcile(dry_run=False)

        assert sum(stats.values()) == 0

    @pytest.mark.usefixtures("_patch_reconcile_dirs")
    def test_porcelain_parsing(self) -> None:
        """git worktree list --porcelain output is correctly parsed."""
        from new_claude.scripts.reconcile_worktrees import _get_actual_worktrees

        porcelain_output = (
            "worktree /home/user/project\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /tmp/wt-coder-p1-t3-a7f2\n"
            "HEAD def456\n"
            "branch refs/heads/agent/coder-p1-t3-a7f2\n"
            "\n"
            "worktree /tmp/wt-planner-p1-design-b3e1\n"
            "HEAD ghi789\n"
            "branch refs/heads/worktree-planner-p1-design-b3e1\n"
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=porcelain_output, stderr=""
            )
            result = _get_actual_worktrees()

        # Main repo worktree should be excluded (not /tmp/wt-* pattern)
        assert "/home/user/project" not in result
        # Our worktrees should be included
        assert "/tmp/wt-coder-p1-t3-a7f2" in result
        assert result["/tmp/wt-coder-p1-t3-a7f2"] == "refs/heads/agent/coder-p1-t3-a7f2"
        assert "/tmp/wt-planner-p1-design-b3e1" in result
