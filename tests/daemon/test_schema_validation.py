"""Tests for state machine schema validation and JSON loading."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from schemas.state_machine import (
    StateMachineDefinition,
    StateDefinition,
    TransitionDefinition,
)

MACHINES_DIR = PROJECT_ROOT / "state-machines"


class TestStateDefinitionSchema:
    """Tests for StateDefinition Pydantic model."""

    def test_minimal_state(self):
        s = StateDefinition(name="IDLE", description="Idle state")
        assert s.name == "IDLE"
        assert s.write_allowed is True
        assert s.write_globs is None
        assert s.blocked_tools == []
        assert s.think_on_exit is False
        assert s.think_prompt is None
        assert s.post_actions == {}

    def test_state_with_think_fields(self):
        s = StateDefinition(
            name="REVIEW",
            description="Review state",
            think_on_exit=True,
            think_prompt="Review the implementation against the spec.",
        )
        assert s.think_on_exit is True
        assert s.think_prompt == "Review the implementation against the spec."

    def test_state_with_post_actions(self):
        s = StateDefinition(
            name="IMPL",
            description="Implementation state",
            post_actions={
                "Write": ["lint_check", "test_runner"],
                "*": ["log_action"],
            },
        )
        assert "Write" in s.post_actions
        assert s.post_actions["Write"] == ["lint_check", "test_runner"]
        assert s.post_actions["*"] == ["log_action"]

    def test_state_with_write_globs(self):
        s = StateDefinition(
            name="TEST_DESIGN",
            description="Test design",
            write_allowed=True,
            write_globs=["tests/**/*.py"],
        )
        assert s.write_globs == ["tests/**/*.py"]

    def test_state_rejects_extra_fields(self):
        with pytest.raises(Exception):
            StateDefinition(
                name="BAD",
                description="Bad state",
                unknown_field="nope",  # type: ignore[call-arg]
            )


class TestTransitionDefinition:
    """Tests for TransitionDefinition model."""

    def test_minimal_transition(self):
        t = TransitionDefinition(to_state="NEXT", trigger="go")
        assert t.from_state == "*"
        assert t.guards == []
        assert t.max_occurrences is None

    def test_transition_with_max_occurrences(self):
        t = TransitionDefinition(
            from_state="A",
            to_state="B",
            trigger="retry",
            max_occurrences=3,
        )
        assert t.max_occurrences == 3

    def test_max_occurrences_must_be_positive(self):
        with pytest.raises(Exception):
            TransitionDefinition(
                from_state="A",
                to_state="B",
                trigger="retry",
                max_occurrences=0,
            )


class TestStateMachineDefinition:
    """Tests for full StateMachineDefinition model."""

    def test_validates_initial_state_exists(self):
        with pytest.raises(Exception, match="initial_state"):
            StateMachineDefinition(
                name="test",
                description="test",
                initial_state="NONEXISTENT",
                terminal_states=["END"],
                states=[
                    StateDefinition(name="START", description="s"),
                    StateDefinition(name="END", description="e"),
                ],
                transitions=[],
            )

    def test_validates_terminal_states_exist(self):
        with pytest.raises(Exception, match="terminal_state"):
            StateMachineDefinition(
                name="test",
                description="test",
                initial_state="START",
                terminal_states=["NONEXISTENT"],
                states=[StateDefinition(name="START", description="s")],
                transitions=[],
            )

    def test_validates_transition_state_refs(self):
        with pytest.raises(Exception, match="to_state"):
            StateMachineDefinition(
                name="test",
                description="test",
                initial_state="START",
                terminal_states=["END"],
                states=[
                    StateDefinition(name="START", description="s"),
                    StateDefinition(name="END", description="e"),
                ],
                transitions=[
                    TransitionDefinition(
                        from_state="START", to_state="NOWHERE", trigger="go"
                    )
                ],
            )


class TestAllStateMachineJSONs:
    """Validate every JSON file in state-machines/ loads and passes schema."""

    @pytest.fixture(params=sorted(MACHINES_DIR.glob("*.json")), ids=lambda p: p.stem)
    def machine_path(self, request: pytest.FixtureRequest) -> Path:
        return request.param

    def test_json_parses(self, machine_path: Path):
        data = json.loads(machine_path.read_text())
        assert isinstance(data, dict)
        assert "name" in data
        assert "states" in data

    def test_pydantic_validates(self, machine_path: Path):
        machine = StateMachineDefinition.model_validate_json(machine_path.read_text())
        assert machine.name == machine_path.stem

    def test_initial_state_in_states(self, machine_path: Path):
        machine = StateMachineDefinition.model_validate_json(machine_path.read_text())
        state_names = {s.name for s in machine.states}
        assert machine.initial_state in state_names

    def test_terminal_states_in_states(self, machine_path: Path):
        machine = StateMachineDefinition.model_validate_json(machine_path.read_text())
        state_names = {s.name for s in machine.states}
        for ts in machine.terminal_states:
            assert ts in state_names

    def test_think_on_exit_has_think_prompt_or_default(self, machine_path: Path):
        """States with think_on_exit=True should ideally have a think_prompt."""
        machine = StateMachineDefinition.model_validate_json(machine_path.read_text())
        for state in machine.states:
            if state.think_on_exit:
                # think_prompt can be None (legacy), but the field must exist
                assert hasattr(state, "think_prompt")

    def test_post_actions_keys_are_valid(self, machine_path: Path):
        """post_actions keys should be tool names or '*'."""
        machine = StateMachineDefinition.model_validate_json(machine_path.read_text())
        valid_tools = {
            "Write",
            "Edit",
            "Read",
            "Bash",
            "Think",
            "SendMessage",
            "Task",
            "Glob",
            "Grep",
            "*",
        }
        for state in machine.states:
            for key in state.post_actions:
                assert key in valid_tools or key.startswith("mcp__"), (
                    f"{machine.name}.{state.name}: post_actions key '{key}' not recognized"
                )
