"""State machine definition model — defines the structure of any state machine.

Used by: workflow_state.py (enforcement), PreToolUse hooks (state gating)
Instances: state-machines/system.json, state-machines/coder.json, etc.

The Pydantic models here define the SCHEMA. Actual state machine definitions
are JSON files in state-machines/ validated against these models. Agents never
load these — enforcement runs in scripts/hooks outside agent context.

Design doc ref: "State Machines" section, lines 303-369.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent_state import AgentRole


class StateDefinition(BaseModel):
    """One state in a state machine."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="State name (e.g., 'IDLE', 'TASK_CLAIMED')")
    description: str = Field(description="What this state represents")

    # --- Write/Edit gating (PreToolUse hook checks these) ---
    write_allowed: bool = Field(
        default=True,
        description="Whether Write/Edit tools are permitted in this state",
    )
    write_globs: list[str] | None = Field(
        default=None,
        description="If write_allowed, restrict to these glob patterns. None = all files allowed.",
        examples=[["tests/**/*.py"], ["src/**/*.py", "tests/**/*.py"]],
    )

    # --- Read gating (prevents test contamination during implementation) ---
    read_globs_exclude: list[str] | None = Field(
        default=None,
        description="Glob patterns for files the agent cannot Read/Glob/Grep in this state. "
        "Used to prevent test contamination during implementation.",
        examples=[["tests/**/*.py", "**/test_*.py"]],
    )

    # --- Tool blocking (permissive fallback: unlisted tools are ALLOWED) ---
    blocked_tools: list[str] = Field(
        default_factory=list,
        description="Tools explicitly blocked in this state. Everything else is allowed.",
        examples=[["Write", "Edit"], ["Bash"]],
    )

    # --- Think tool requirements ---
    think_on_exit: bool = Field(
        default=False,
        description="Agent must use Think tool before leaving this state",
    )
    think_prompt: str | None = Field(
        default=None,
        description="Emitted as a critical annotation when entering this state "
        "(if think_on_exit is true). Tells the agent WHAT to think about.",
    )
    model: str | None = Field(
        default=None,
        description="Model to use for sub-agents spawned in this state. "
        "Overrides agent_model for delegation. None = use agent_model default.",
    )
    post_actions: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Validators to run after specific tool calls in this state. "
        "Key = tool name (or '*' for all), value = list of validator names.",
    )


class TransitionDefinition(BaseModel):
    """One valid transition between states."""

    model_config = ConfigDict(extra="forbid")

    from_state: str = Field(
        default="*",
        description="Source state. '*' means any state (used in universal_transitions).",
    )
    to_state: str
    trigger: str = Field(
        description="What causes this transition (event or condition)",
        examples=[
            "design_loaded",
            "explorer_complete",
            "user_approved",
            "all_tasks_complete",
        ],
    )
    guards: list[str] = Field(
        default_factory=list,
        description="Conditions that must be true for this transition (evaluated by workflow_state.py)",
        examples=[["context_packets_exist", "explorer_status == complete"]],
    )
    actions: list[str] = Field(
        default_factory=list,
        description="Side effects triggered by this transition",
        examples=[["spawn_strategist", "log_phase_entry", "notify_user"]],
    )
    requires_user_approval: bool = Field(
        default=False,
        description="Transition blocked until user explicitly approves",
    )
    description: str | None = Field(
        default=None,
        description="Human-readable description of this transition's purpose or rationale.",
    )
    max_occurrences: int | None = Field(
        default=None,
        ge=1,
        description="Max times this transition can fire (for loop limits, e.g., remediation max 2)",
    )


class StateMachineDefinition(BaseModel):
    """Complete state machine definition. Loaded from JSON by workflow_state.py."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        description="Machine identifier",
        examples=["system", "coder", "tester", "explorer"],
    )
    description: str
    version: str = Field(
        default="1.0.0",
        description="Schema version for backward compatibility",
    )
    agent_role: AgentRole | None = Field(
        default=None,
        description="Which agent role this machine applies to. None for system-level.",
    )
    agent_model: str | None = Field(
        default=None,
        description="Default model this agent runs as. Per-state 'model' overrides for sub-agent delegation.",
    )
    variables: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Named glob-list variables with defaults, referenced as '$name' in "
        "write_globs/read_globs_exclude. Resolved at load time by state_manager.",
        examples=[{"source_globs": ["src/**/*.py"], "test_globs": ["tests/**/*.py"]}],
    )
    initial_state: str = Field(description="State the machine starts in")
    terminal_states: list[str] = Field(
        description="States that end the machine's lifecycle",
        examples=[["COMPLETE"], ["TASK_COMPLETE"], ["APPROVED"]],
    )
    states: list[StateDefinition]
    universal_transitions: list[TransitionDefinition] = Field(
        default_factory=list,
        description="Transitions that can fire from ANY state (e.g., context_pressure_exceeded → HANDOFF). "
        "These are merged with per-state transitions during enforcement.",
    )
    transitions: list[TransitionDefinition]

    @model_validator(mode="after")
    def _validate_state_references(self) -> StateMachineDefinition:
        state_names = {s.name for s in self.states}
        if self.initial_state not in state_names:
            msg = f"initial_state {self.initial_state!r} not found in states"
            raise ValueError(msg)
        for ts in self.terminal_states:
            if ts not in state_names:
                msg = f"terminal_state {ts!r} not found in states"
                raise ValueError(msg)
        all_transitions = list(self.transitions) + list(self.universal_transitions)
        for t in all_transitions:
            if t.from_state != "*" and t.from_state not in state_names:
                msg = f"transition from_state {t.from_state!r} not found in states"
                raise ValueError(msg)
            if t.to_state not in state_names:
                msg = f"transition to_state {t.to_state!r} not found in states"
                raise ValueError(msg)
        return self
