"""Tests for daemon session registry."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.daemon.state_manager as _sm


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Isolate state and clear registry between tests."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    monkeypatch.setattr(_sm, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(_sm, "MACHINES_DIR", tmp_path / "machines")
    (tmp_path / "machines").mkdir()
    _sm._session_registry.clear()
    _sm._agent_state_cache.clear()
    _sm._machine_cache.clear()
    yield
    _sm._session_registry.clear()
    _sm._agent_state_cache.clear()
    _sm._machine_cache.clear()


class TestRegisterSession:
    def test_basic_registration(self):
        _sm.register_session("coder-a7f2", "Corner-CNN", "coder", "Corner-CNN-abc12345")
        entry = _sm.get_session("coder-a7f2")
        assert entry is not None
        assert entry["team_id"] == "Corner-CNN"
        assert entry["role"] == "coder"
        assert entry["status"] == "active"

    def test_register_multiple_agents(self):
        _sm.register_session("coder-a7f2", "Corner-CNN", "coder", "wf-1")
        _sm.register_session("explorer-b3e1", "Corner-CNN", "explorer", "wf-1")
        assert len(_sm.get_team_sessions("Corner-CNN")) == 2

    def test_register_agent_also_populates_registry(self, tmp_path):
        """register_agent with team_id should populate session registry."""
        result = _sm.register_agent(
            agent_id="coder-p1-t1-a7f2",
            role="coder",
            model="opus-4-6",
            team_id="Corner-CNN",
            workflow_id="wf-123",
        )
        assert result["ok"] is True
        entry = _sm.get_session("coder-p1-t1-a7f2")
        assert entry is not None
        assert entry["team_id"] == "Corner-CNN"

    def test_register_agent_without_team_skips_registry(self):
        """register_agent without team_id should NOT populate registry."""
        _sm.register_agent(
            agent_id="coder-p1-t1-a7f2",
            role="coder",
            model="opus-4-6",
        )
        assert _sm.get_session("coder-p1-t1-a7f2") is None


class TestTouchSession:
    def test_updates_last_seen(self):
        _sm.register_session("coder-a7f2", "Corner-CNN", "coder", "wf-1")
        first = _sm.get_session("coder-a7f2")["last_seen"]
        time.sleep(0.01)
        _sm.touch_session("coder-a7f2")
        second = _sm.get_session("coder-a7f2")["last_seen"]
        assert second >= first

    def test_noop_for_unknown_agent(self):
        _sm.touch_session("nonexistent")  # should not crash


class TestDepartSession:
    def test_marks_departed(self):
        _sm.register_session("coder-a7f2", "Corner-CNN", "coder", "wf-1")
        _sm.depart_session("coder-a7f2")
        assert _sm.get_session("coder-a7f2")["status"] == "departed"

    def test_departed_excluded_from_team_sessions(self):
        _sm.register_session("coder-a7f2", "Corner-CNN", "coder", "wf-1")
        _sm.register_session("explorer-b3e1", "Corner-CNN", "explorer", "wf-1")
        _sm.depart_session("coder-a7f2")
        members = _sm.get_team_sessions("Corner-CNN")
        assert len(members) == 1
        assert members[0]["role"] == "explorer"


class TestResolveRole:
    def test_finds_agent_by_role(self):
        _sm.register_session("coder-a7f2", "Corner-CNN", "coder", "wf-1")
        ids = _sm.resolve_role_in_team("Corner-CNN", "coder")
        assert ids == ["coder-a7f2"]

    def test_finds_multiple_agents_same_role(self):
        _sm.register_session("coder-a7f2", "Corner-CNN", "coder", "wf-1")
        _sm.register_session("coder-c4d9", "Corner-CNN", "coder", "wf-1")
        ids = _sm.resolve_role_in_team("Corner-CNN", "coder")
        assert len(ids) == 2

    def test_empty_for_missing_role(self):
        _sm.register_session("coder-a7f2", "Corner-CNN", "coder", "wf-1")
        ids = _sm.resolve_role_in_team("Corner-CNN", "tester")
        assert ids == []

    def test_excludes_departed(self):
        _sm.register_session("coder-a7f2", "Corner-CNN", "coder", "wf-1")
        _sm.depart_session("coder-a7f2")
        ids = _sm.resolve_role_in_team("Corner-CNN", "coder")
        assert ids == []


class TestStaleDetection:
    def test_fresh_session_not_stale(self):
        _sm.register_session("coder-a7f2", "Corner-CNN", "coder", "wf-1")
        assert _sm.get_stale_sessions(timeout_seconds=300) == []

    def test_old_session_is_stale(self):
        _sm.register_session("coder-a7f2", "Corner-CNN", "coder", "wf-1")
        # Manually backdate last_seen
        _sm._session_registry["coder-a7f2"]["last_seen"] = (
            datetime.now(timezone.utc) - timedelta(seconds=600)
        ).isoformat()
        stale = _sm.get_stale_sessions(timeout_seconds=300)
        assert len(stale) == 1
        assert stale[0]["agent_id"] == "coder-a7f2"

    def test_departed_excluded_from_stale(self):
        _sm.register_session("coder-a7f2", "Corner-CNN", "coder", "wf-1")
        _sm._session_registry["coder-a7f2"]["last_seen"] = (
            datetime.now(timezone.utc) - timedelta(seconds=600)
        ).isoformat()
        _sm.depart_session("coder-a7f2")
        assert _sm.get_stale_sessions(timeout_seconds=300) == []


class TestGetTeamSessions:
    def test_filters_by_team(self):
        _sm.register_session("coder-a7f2", "Corner-CNN", "coder", "wf-1")
        _sm.register_session("coder-x1y2", "Other-Team", "coder", "wf-2")
        members = _sm.get_team_sessions("Corner-CNN")
        assert len(members) == 1
        assert members[0]["agent_id"] == "coder-a7f2"

    def test_empty_for_unknown_team(self):
        assert _sm.get_team_sessions("nonexistent") == []
