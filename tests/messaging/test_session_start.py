"""Tests for SessionStart team resume detection (Chunk 9)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestSessionStartTeamScan:
    """Test that _check_workflow_resumption detects active messaging teams."""

    def test_detects_active_team(self, tmp_path, monkeypatch):
        """Active team in registry → shows in resumption context."""
        # Set up messages directory
        messages_dir = tmp_path / "messages"
        messages_dir.mkdir()
        team_dir = messages_dir / "test-team"
        team_dir.mkdir()

        # Create registry
        registry = {
            "teams": {
                "test-team": {
                    "workflow_id": "test-team-abc12345",
                    "created_at": "2024-01-01T00:00:00Z",
                    "status": "active",
                }
            }
        }
        (messages_dir / "_registry.json").write_text(json.dumps(registry))

        # Create manifest
        manifest = {
            "team_id": "test-team",
            "workflow_id": "test-team-abc12345",
            "created_at": "2024-01-01T00:00:00Z",
            "created_by": "orchestrator-01",
            "agents": {
                "orchestrator-01": {
                    "role": "orchestrator",
                    "status": "active",
                    "joined_at": "2024-01-01T00:00:00Z",
                    "inbox_path": str(team_dir / "orchestrator-01.inbox.json"),
                },
                "coder-01": {
                    "role": "coder",
                    "status": "active",
                    "joined_at": "2024-01-01T00:00:00Z",
                    "inbox_path": str(team_dir / "coder-01.inbox.json"),
                },
            },
        }
        (team_dir / "_manifest.json").write_text(json.dumps(manifest))

        # Create a workflow state so _check_workflow_resumption doesn't bail early
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        agents_dir = state_dir / "agents"
        agents_dir.mkdir()

        # Monkeypatch the messages directory path in the function
        # We need to patch expanduser to redirect ~/.claude/messages
        original_expanduser = os.path.expanduser
        def mock_expanduser(path):
            if "~/.claude/messages" in str(path):
                return str(messages_dir)
            if "~/.claude" in str(path):
                return str(tmp_path)
            return original_expanduser(path)

        monkeypatch.setattr(os.path, "expanduser", mock_expanduser)

        # Also need a workflow state file for the function to not return early
        import hooks.session_start as ss
        monkeypatch.setattr(ss, "WORKFLOW_STATE_FILE", state_dir / "system.json")
        workflow_state = {
            "workflow_id": "test-wf",
            "feature": "test-feature",
            "current_state": "EXPLORATION",
            "base_branch": "main",
            "started_at": "2024-01-01T00:00:00Z",
            "last_updated": "2024-01-01T00:00:00Z",
        }
        (state_dir / "system.json").write_text(json.dumps(workflow_state))
        monkeypatch.setattr(ss, "AGENT_STATE_DIR", agents_dir)

        result = ss._check_workflow_resumption()
        assert "test-team" in result
        assert "orchestrator-01" in result
        assert "coder-01" in result

    def test_no_crash_when_messages_dir_missing(self, tmp_path, monkeypatch):
        """Missing messages directory should not crash."""
        original_expanduser = os.path.expanduser
        def mock_expanduser(path):
            if "~/.claude/messages" in str(path):
                return str(tmp_path / "nonexistent" / "messages")
            if "~/.claude" in str(path):
                return str(tmp_path)
            return original_expanduser(path)

        monkeypatch.setattr(os.path, "expanduser", mock_expanduser)

        import hooks.session_start as ss
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        agents_dir = state_dir / "agents"
        agents_dir.mkdir()
        monkeypatch.setattr(ss, "WORKFLOW_STATE_FILE", state_dir / "system.json")
        workflow_state = {
            "workflow_id": "test-wf",
            "feature": "test-feature",
            "current_state": "EXPLORATION",
            "base_branch": "main",
        }
        (state_dir / "system.json").write_text(json.dumps(workflow_state))
        monkeypatch.setattr(ss, "AGENT_STATE_DIR", agents_dir)

        # Should not raise
        result = ss._check_workflow_resumption()
        assert isinstance(result, str)

    def test_ignores_archived_teams(self, tmp_path, monkeypatch):
        """Archived teams should not appear in resumption context."""
        messages_dir = tmp_path / "messages"
        messages_dir.mkdir()

        registry = {
            "teams": {
                "old-team": {
                    "workflow_id": "old-team-xyz",
                    "created_at": "2024-01-01T00:00:00Z",
                    "status": "archived",
                }
            }
        }
        (messages_dir / "_registry.json").write_text(json.dumps(registry))

        original_expanduser = os.path.expanduser
        def mock_expanduser(path):
            if "~/.claude/messages" in str(path):
                return str(messages_dir)
            if "~/.claude" in str(path):
                return str(tmp_path)
            return original_expanduser(path)

        monkeypatch.setattr(os.path, "expanduser", mock_expanduser)

        import hooks.session_start as ss
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        agents_dir = state_dir / "agents"
        agents_dir.mkdir()
        monkeypatch.setattr(ss, "WORKFLOW_STATE_FILE", state_dir / "system.json")
        workflow_state = {
            "workflow_id": "test-wf",
            "feature": "test-feature",
            "current_state": "EXPLORATION",
            "base_branch": "main",
        }
        (state_dir / "system.json").write_text(json.dumps(workflow_state))
        monkeypatch.setattr(ss, "AGENT_STATE_DIR", agents_dir)

        result = ss._check_workflow_resumption()
        assert "old-team" not in result
