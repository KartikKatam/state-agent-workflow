"""Tests for daemon pre-tool checks (handle_pre_tool + check_tool_allowed).

Tests cover: hard block patterns, write gating, blocked_tools, handoff blocking,
annotation blocking, ask-user, and permissive fallback.
"""

from __future__ import annotations

from pathlib import Path


import scripts.workflow_state as daemon
from schemas.agent_state import PendingAnnotation

from .conftest import make_agent_in_state


# ---------------------------------------------------------------------------
# 1-3: Hard block patterns (dangerous commands)
# ---------------------------------------------------------------------------


class TestHardBlocks:
    """Verify that hard-blocked patterns are denied via handle_pre_tool."""

    def test_rm_rf_root_hard_blocked(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """rm -rf / → hard_block=True."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "UNRESTRICTED")
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2", "Bash", {"command": "rm -rf /"}
        )
        assert not result["allowed"]
        assert result.get("hard_block") is True

    def test_mkfs_hard_blocked(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """mkfs → hard_block=True."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "UNRESTRICTED")
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2", "Bash", {"command": "mkfs.ext4 /dev/sda"}
        )
        assert not result["allowed"]
        assert result.get("hard_block") is True

    def test_fork_bomb_hard_blocked(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """Fork bomb pattern → hard_block=True."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "UNRESTRICTED")
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2", "Bash", {"command": ":(){ :|:& }"}
        )
        assert not result["allowed"]
        assert result.get("hard_block") is True

    def test_harmless_bash_not_hard_blocked(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """Harmless Bash → allowed."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "UNRESTRICTED")
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2", "Bash", {"command": "ls -la"}
        )
        assert result["allowed"]


# ---------------------------------------------------------------------------
# 4-6: Write gating (via check_tool_allowed, invoked by handle_pre_tool)
# ---------------------------------------------------------------------------


class TestWriteGating:
    """Test Write/Edit tool gating based on state definitions."""

    def test_write_to_allowed_path(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """Write to a path matching write_globs → allowed."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "WRITING")
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {"file_path": "src/utils/helper.py"},
        )
        assert result["allowed"]

    def test_write_to_wrong_path(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """Write to a path NOT in write_globs → denied."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "WRITING")
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {"file_path": "tests/test_foo.py"},
        )
        assert not result["allowed"]
        assert "not in allowed globs" in result["reason"]

    def test_write_when_write_not_allowed(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """Write in a state where write_allowed=false → denied."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {"file_path": "src/main.py"},
        )
        assert not result["allowed"]
        assert "writes not allowed" in result["reason"]

    def test_edit_same_as_write(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """Edit follows the same restrictions as Write."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2",
            "Edit",
            {"file_path": "src/main.py"},
        )
        assert not result["allowed"]

    def test_write_no_file_path_with_globs(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """Write with write_globs but no file_path → allowed (nothing to check)."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "WRITING")
        result = daemon.handle_pre_tool("coder-p1-t1-a1b2", "Write", {})
        assert result["allowed"]

    def test_write_in_unrestricted_state(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """Write in unrestricted state → allowed anywhere."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "UNRESTRICTED")
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {"file_path": "/any/path/file.py"},
        )
        assert result["allowed"]


# ---------------------------------------------------------------------------
# 7: Blocked tools
# ---------------------------------------------------------------------------


class TestBlockedTools:
    """Test blocked_tools list enforcement."""

    def test_tool_in_blocked_list(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """Tool explicitly in blocked_tools → denied."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "BLOCKED_BASH")
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2", "Bash", {"command": "echo hi"}
        )
        assert not result["allowed"]

    def test_tool_not_in_blocked_list(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """Tool NOT in blocked_tools → allowed."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "BLOCKED_BASH")
        result = daemon.handle_pre_tool("coder-p1-t1-a1b2", "Read", {})
        assert result["allowed"]


# ---------------------------------------------------------------------------
# 8: Handoff blocking
# ---------------------------------------------------------------------------


class TestHandoffBlocking:
    """Test behavior when pending_handoff is set."""

    def test_handoff_blocks_write_to_non_handoff_path(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """pending_handoff=True, Write to non-handoff path → denied."""
        make_agent_in_state(
            "coder-p1-t1-a1b2", "coder", "UNRESTRICTED", pending_handoff=True
        )
        daemon._agent_state_cache.clear()
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {"file_path": "src/main.py"},
        )
        assert not result["allowed"]
        assert "handoff" in result["reason"].lower()

    def test_handoff_allows_handoff_path(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """pending_handoff=True, Write to .claude/handoffs/ → allowed."""
        make_agent_in_state(
            "coder-p1-t1-a1b2", "coder", "UNRESTRICTED", pending_handoff=True
        )
        daemon._agent_state_cache.clear()
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {"file_path": ".claude/handoffs/summary.md"},
        )
        assert result["allowed"]

    def test_handoff_allows_read(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """pending_handoff=True → Read still allowed."""
        make_agent_in_state(
            "coder-p1-t1-a1b2", "coder", "UNRESTRICTED", pending_handoff=True
        )
        daemon._agent_state_cache.clear()
        result = daemon.handle_pre_tool("coder-p1-t1-a1b2", "Read", {})
        assert result["allowed"]


# ---------------------------------------------------------------------------
# 9: Annotation blocking
# ---------------------------------------------------------------------------


class TestAnnotationBlocking:
    """Test behavior when pending_critical_annotation is set."""

    def test_annotation_blocks_write(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """pending_critical_annotation → Write denied."""
        make_agent_in_state(
            "coder-p1-t1-a1b2",
            "coder",
            "UNRESTRICTED",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-001",
                message="Think about your approach",
            ),
        )
        daemon._agent_state_cache.clear()
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {"file_path": "src/main.py"},
        )
        assert not result["allowed"]
        assert "annotation" in result["reason"].lower()

    def test_annotation_blocks_bash(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """pending_critical_annotation → Bash denied."""
        make_agent_in_state(
            "coder-p1-t1-a1b2",
            "coder",
            "UNRESTRICTED",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-001",
                message="Think about your approach",
            ),
        )
        daemon._agent_state_cache.clear()
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2",
            "Bash",
            {"command": "echo hi"},
        )
        assert not result["allowed"]

    def test_annotation_allows_read(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """pending_critical_annotation → Read still allowed."""
        make_agent_in_state(
            "coder-p1-t1-a1b2",
            "coder",
            "UNRESTRICTED",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-001",
                message="Think about your approach",
            ),
        )
        daemon._agent_state_cache.clear()
        result = daemon.handle_pre_tool("coder-p1-t1-a1b2", "Read", {})
        assert result["allowed"]


# ---------------------------------------------------------------------------
# 10: Ask-user
# ---------------------------------------------------------------------------


class TestAskUser:
    """Test ask_user behavior for risky operations."""

    def test_git_push_asks_user(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """Bash with git push → ask_user=True."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "UNRESTRICTED")
        daemon._agent_state_cache.clear()
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2",
            "Bash",
            {"command": "git push origin main"},
        )
        assert result.get("ask_user") is True

    def test_generalist_exempt_from_ask(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """Generalist role is exempt from ask-user checks."""
        make_agent_in_state("generalist-p1-t1-a1b2", "generalist", "IDLE")
        daemon._agent_state_cache.clear()
        result = daemon.handle_pre_tool(
            "generalist-p1-t1-a1b2",
            "Bash",
            {"command": "git push origin main"},
        )
        assert result["allowed"]
        assert result.get("ask_user") is not True


# ---------------------------------------------------------------------------
# 11: Permissive fallback
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 11: Validation error blocking
# ---------------------------------------------------------------------------


class TestValidationErrorBlocking:
    """Test behavior when pending_validation_error is set."""

    def test_validation_error_blocks_write(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """pending_validation_error → Write denied."""
        make_agent_in_state(
            "coder-p1-t1-a1b2",
            "coder",
            "UNRESTRICTED",
            pending_validation_error={
                "message": "ruff: F821 undefined name",
                "validator_errors": ["F821: undefined name 'foo'"],
            },
        )
        daemon._agent_state_cache.clear()
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {"file_path": "src/main.py"},
        )
        assert not result["allowed"]
        assert "validation error" in result["reason"].lower()

    def test_validation_error_blocks_edit(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """pending_validation_error → Edit denied."""
        make_agent_in_state(
            "coder-p1-t1-a1b2",
            "coder",
            "UNRESTRICTED",
            pending_validation_error={
                "message": "schema error",
                "validator_errors": ["Schema validation failed"],
            },
        )
        daemon._agent_state_cache.clear()
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2",
            "Edit",
            {"file_path": "src/main.py"},
        )
        assert not result["allowed"]

    def test_validation_error_blocks_bash(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """pending_validation_error → Bash denied."""
        make_agent_in_state(
            "coder-p1-t1-a1b2",
            "coder",
            "UNRESTRICTED",
            pending_validation_error={"message": "fix required"},
        )
        daemon._agent_state_cache.clear()
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2",
            "Bash",
            {"command": "echo hi"},
        )
        assert not result["allowed"]

    def test_validation_error_allows_read(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """pending_validation_error → Read still allowed."""
        make_agent_in_state(
            "coder-p1-t1-a1b2",
            "coder",
            "UNRESTRICTED",
            pending_validation_error={"message": "fix required"},
        )
        daemon._agent_state_cache.clear()
        result = daemon.handle_pre_tool("coder-p1-t1-a1b2", "Read", {})
        assert result["allowed"]

    def test_validation_error_allows_think(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """pending_validation_error → Think still allowed (agents need to deliberate)."""
        make_agent_in_state(
            "coder-p1-t1-a1b2",
            "coder",
            "UNRESTRICTED",
            pending_validation_error={"message": "fix required"},
        )
        daemon._agent_state_cache.clear()
        result = daemon.handle_pre_tool("coder-p1-t1-a1b2", "Think", {})
        assert result["allowed"]


# ---------------------------------------------------------------------------
# 12: Permissive fallback
# ---------------------------------------------------------------------------


class TestPermissiveFallback:
    """Verify permissive fallback when data is missing."""

    def test_unknown_agent_allowed(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """Unknown agent_id → allowed."""
        daemon._agent_state_cache.clear()
        result = daemon.handle_pre_tool(
            "unknown-x1-y2-z3z3", "Write", {"file_path": "anything.py"}
        )
        assert result["allowed"]

    def test_state_not_in_machine_allowed(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """Agent in a state not defined in the machine → allowed with warning."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "NONEXISTENT_STATE")
        daemon._agent_state_cache.clear()
        result = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2", "Write", {"file_path": "foo.py"}
        )
        assert result["allowed"]

    def test_no_machine_for_role(
        self, temp_state_dir: Path, sample_machine: None
    ) -> None:
        """Generalist role has no machine → allowed."""
        make_agent_in_state("generalist-p1-t1-a1b2", "generalist", "IDLE")
        daemon._agent_state_cache.clear()
        result = daemon.handle_pre_tool(
            "generalist-p1-t1-a1b2", "Write", {"file_path": "foo.py"}
        )
        assert result["allowed"]
