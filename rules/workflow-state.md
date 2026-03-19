---
globs: ["scripts/workflow_state.py", "state-machines/**/*.json"]
---

# Workflow State Machine Rules

## Architecture

`workflow_state.py` is a daemon that validates state transitions and tool permissions. PreToolUse hooks query it via Unix domain socket (~2ms latency). It is the single source of truth for agent state.

## State Machine Definitions

State machine JSON files in `state-machines/` define:
- States with write permissions (`write_allowed`, `write_globs`, `blocked_tools`)
- Transitions with guards, actions, and optional `max_occurrences`
- Initial and terminal states

## Modification Rules

- Every state MUST have a `description` explaining what it represents
- Every transition MUST have a `trigger` explaining what causes it
- Transitions with side effects MUST list them in `actions`
- Loop transitions (same from/to pair) MUST have `max_occurrences` to prevent infinite loops
- Never remove a state that existing agents might be in — deprecate by making it terminal
- The `generalist` role has NO state machine — it is unrestricted by design

## Permissive Fallback Principle

The state machine is **permissive by default**:
- Unmatched tool calls are ALLOWED (only explicitly blocked tools are denied)
- If daemon is down, hooks allow through with a warning
- If agent state is missing, allow through with a warning

This prevents the state machine from becoming a blocker during development.

## Schema Validation

State machine JSON files are validated against `schemas/state_machine.py` (Pydantic models). Run validation:
```bash
python3 -c "from schemas.state_machine import StateMachineDefinition; import json; StateMachineDefinition.model_validate(json.load(open('state-machines/coder.json')))"
```

## Daemon Operations

- `workflow_state.py serve` — Start daemon (called by orchestrator at workflow init)
- `workflow_state.py stop` — Stop daemon (called at workflow end)
- `workflow_state.py status` — Check if daemon is running
- Hook commands auto-route through daemon if running, fall back to direct file access

## Logging

Every state transition is logged to `~/.claude/logs/state-transitions.jsonl`. This is mandatory and cannot be disabled. Transition logs include trace_id and span_id for distributed tracing.
