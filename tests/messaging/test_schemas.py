"""Tests for file-based messaging schema models."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from schemas.messaging import (
    AgentRegistration,
    MessageEnvelope,
    TeamManifest,
    TeamRegistry,
    TeamRegistryEntry,
)


NOW = datetime.now(tz=timezone.utc)


class TestMessageEnvelope:
    """Tests for MessageEnvelope model."""

    def _make_envelope(self, **overrides) -> MessageEnvelope:
        defaults = {
            "message_id": "abc-123",
            "sequence": 1,
            "from_agent": "orchestrator-p1-init-a1b2",
            "to_agent": "coder-p1-t1-c3d4",
            "team_id": "team-001",
            "message_type": "task_assign",
            "priority": "blocking",
            "timestamp": NOW,
            "body": {"task": "implement feature X"},
        }
        defaults.update(overrides)
        return MessageEnvelope(**defaults)

    def test_round_trip(self):
        env = self._make_envelope()
        dumped = env.model_dump_json()
        restored = MessageEnvelope.model_validate_json(dumped)
        assert restored.message_id == env.message_id
        assert restored.sequence == env.sequence
        assert restored.from_agent == env.from_agent
        assert restored.to_agent == env.to_agent
        assert restored.team_id == env.team_id
        assert restored.message_type == env.message_type
        assert restored.priority == env.priority
        assert restored.read is False
        assert restored.body == {"task": "implement feature X"}

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            self._make_envelope(unknown_field="bad")

    def test_rejects_sequence_below_1(self):
        with pytest.raises(ValidationError, match="greater_than_equal"):
            self._make_envelope(sequence=0)

    def test_optional_reply_to(self):
        env = self._make_envelope(reply_to="prev-msg-id")
        assert env.reply_to == "prev-msg-id"

    def test_reply_to_defaults_none(self):
        env = self._make_envelope()
        assert env.reply_to is None

    def test_read_defaults_false(self):
        env = self._make_envelope()
        assert env.read is False


class TestAgentRegistration:
    """Tests for AgentRegistration model."""

    def test_round_trip(self):
        reg = AgentRegistration(
            role="coder",
            pid=12345,
            session_id="sess-abc",
            status="active",
            joined_at=NOW,
            inbox_path="/tmp/messages/team-001/coder.inbox.json",
        )
        dumped = reg.model_dump_json()
        restored = AgentRegistration.model_validate_json(dumped)
        assert restored.role == "coder"
        assert restored.pid == 12345
        assert restored.status == "active"
        assert restored.inbox_path == "/tmp/messages/team-001/coder.inbox.json"

    def test_optional_pid_and_session(self):
        reg = AgentRegistration(
            role="explorer",
            status="idle",
            joined_at=NOW,
            inbox_path="/tmp/inbox.json",
        )
        assert reg.pid is None
        assert reg.session_id is None

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            AgentRegistration(
                role="coder",
                status="active",
                joined_at=NOW,
                inbox_path="/tmp/inbox.json",
                extra_field="bad",  # pyright: ignore[reportCallIssue]
            )


class TestTeamManifest:
    """Tests for TeamManifest model."""

    def test_with_agents(self):
        reg = AgentRegistration(
            role="coder",
            status="active",
            joined_at=NOW,
            inbox_path="/tmp/inbox.json",
        )
        manifest = TeamManifest(
            team_id="team-001",
            workflow_id="wf-abc",
            created_at=NOW,
            created_by="orchestrator-p1-init-a1b2",
            agents={"coder-1": reg},
        )
        dumped = manifest.model_dump_json()
        restored = TeamManifest.model_validate_json(dumped)
        assert "coder-1" in restored.agents
        assert restored.agents["coder-1"].role == "coder"

    def test_empty_agents_default(self):
        manifest = TeamManifest(
            team_id="team-002",
            workflow_id="wf-def",
            created_at=NOW,
            created_by="orchestrator-p1-init-a1b2",
        )
        assert manifest.agents == {}


class TestTeamRegistry:
    """Tests for TeamRegistry model."""

    def test_with_teams(self):
        entry = TeamRegistryEntry(
            workflow_id="wf-abc",
            created_at=NOW,
            status="active",
        )
        registry = TeamRegistry(teams={"team-001": entry})
        dumped = registry.model_dump_json()
        restored = TeamRegistry.model_validate_json(dumped)
        assert "team-001" in restored.teams
        assert restored.teams["team-001"].status == "active"

    def test_empty_teams_default(self):
        registry = TeamRegistry()
        assert registry.teams == {}


class TestAgentStateMessagingFields:
    """Tests that AgentState has the new messaging fields."""

    def test_inbox_blocked_and_team_id_exist(self):
        from schemas.agent_state import AgentState

        state = AgentState(
            id="coder-p1-t1-a1b2",
            role="coder",
            model="opus-4-6",
            spawned_at=NOW,
            status="active",
            current_state="TASK_CLAIMED",
        )
        assert state.inbox_blocked is None
        assert state.team_id is None

    def test_inbox_blocked_with_value(self):
        from schemas.agent_state import AgentState

        state = AgentState(
            id="coder-p1-t1-a1b2",
            role="coder",
            model="opus-4-6",
            spawned_at=NOW,
            status="active",
            current_state="TASK_CLAIMED",
            inbox_blocked={
                "blocked_by_message_id": "msg-xyz",
                "from_agent": "orchestrator-p1-init-c3d4",
            },
            team_id="team-001",
        )
        assert state.inbox_blocked is not None
        assert state.inbox_blocked["blocked_by_message_id"] == "msg-xyz"
        assert state.team_id == "team-001"
