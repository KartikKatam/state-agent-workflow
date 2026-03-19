"""Tests for PreToolUse inbox blocking (Chunk 4)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.daemon.state_manager as _sm
from scripts.daemon.permissions import handle_pre_tool


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """Isolate agent state to tmp_path."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    monkeypatch.setattr(_sm, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(_sm, "MACHINES_DIR", tmp_path / "machines")
    (tmp_path / "machines").mkdir()
    _sm._agent_state_cache.clear()
    _sm._machine_cache.clear()
    yield
    _sm._agent_state_cache.clear()
    _sm._machine_cache.clear()


def _create_blocked_agent(agents_dir: Path, agent_id: str = "coder-p1-t1-a1b2") -> None:
    """Create an agent state file with inbox_blocked set."""
    state = {
        "id": agent_id,
        "role": "coder",
        "model": "opus-4-6",
        "spawned_at": "2024-01-01T00:00:00Z",
        "status": "active",
        "current_state": "IMPLEMENTATION",
        "inbox_blocked": {
            "blocked_by_message_id": "msg-abc-123",
            "from_agent": "orchestrator-p1-init-x1y2",
        },
    }
    (agents_dir / f"{agent_id}.json").write_text(json.dumps(state))


class TestInboxBlocking:
    def test_blocks_write(self, tmp_path):
        _create_blocked_agent(tmp_path / "agents")
        result = handle_pre_tool("coder-p1-t1-a1b2", "Write", {"file_path": "src/main.py"})
        assert result["allowed"] is False
        assert "Inbox blocked" in result["reason"]
        assert "msg-abc-123" in result["reason"]

    def test_blocks_edit(self, tmp_path):
        _create_blocked_agent(tmp_path / "agents")
        result = handle_pre_tool("coder-p1-t1-a1b2", "Edit", {"file_path": "src/main.py"})
        assert result["allowed"] is False

    def test_blocks_bash_non_sendmsg(self, tmp_path):
        _create_blocked_agent(tmp_path / "agents")
        result = handle_pre_tool("coder-p1-t1-a1b2", "Bash", {"command": "git status"})
        assert result["allowed"] is False

    def test_allows_bash_sendmsg(self, tmp_path):
        _create_blocked_agent(tmp_path / "agents")
        result = handle_pre_tool("coder-p1-t1-a1b2", "Bash", {"command": "send-msg ack --message-id msg-abc-123"})
        assert result.get("allowed") is True or "allowed" not in result or result.get("allowed") is not False
        # The function may return allowed=True or fall through to other checks

    def test_allows_ptc(self, tmp_path):
        _create_blocked_agent(tmp_path / "agents")
        result = handle_pre_tool("coder-p1-t1-a1b2", "mcp__local-ptc__ptc_execute", {})
        assert result.get("allowed", True) is True

    def test_allows_read(self, tmp_path):
        _create_blocked_agent(tmp_path / "agents")
        result = handle_pre_tool("coder-p1-t1-a1b2", "Read", {"file_path": "src/main.py"})
        assert result.get("allowed", True) is True

    def test_allows_think(self, tmp_path):
        _create_blocked_agent(tmp_path / "agents")
        result = handle_pre_tool("coder-p1-t1-a1b2", "Think", {})
        assert result.get("allowed", True) is True

    def test_allows_mcp_think(self, tmp_path):
        _create_blocked_agent(tmp_path / "agents")
        result = handle_pre_tool("coder-p1-t1-a1b2", "mcp__think-tool__think", {})
        assert result.get("allowed", True) is True

    def test_denial_includes_from_agent(self, tmp_path):
        _create_blocked_agent(tmp_path / "agents")
        result = handle_pre_tool("coder-p1-t1-a1b2", "Write", {"file_path": "x.py"})
        assert "orchestrator-p1-init-x1y2" in result["reason"]

    def test_no_block_when_inbox_blocked_is_none(self, tmp_path):
        """Agent without inbox_blocked should not be blocked."""
        state = {
            "id": "coder-p1-t1-a1b2",
            "role": "coder",
            "model": "opus-4-6",
            "spawned_at": "2024-01-01T00:00:00Z",
            "status": "active",
            "current_state": "IMPLEMENTATION",
            "inbox_blocked": None,
        }
        (tmp_path / "agents" / "coder-p1-t1-a1b2.json").write_text(json.dumps(state))
        result = handle_pre_tool("coder-p1-t1-a1b2", "Write", {"file_path": "x.py"})
        # Should pass through inbox check (may still be blocked by SM gating, but not by inbox)
        if not result.get("allowed", True):
            assert "Inbox blocked" not in result.get("reason", "")
