---
globs: ["agents/**/*.md", ".claude/agents/**"]
---

# Agent Rules

## Communication Protocol

All inter-agent messages use the typed protocol defined in `schemas/message_protocol.py`. Messages are JSON payloads sent via `SendMessage` and logged to `~/.claude/logs/message-bus.jsonl`.

### Message Types

| Type | Direction | Purpose |
|------|-----------|---------|
| `task_assign` | Orchestrator → agent | Assign work with inputs and expected output |
| `task_complete` | Agent → orchestrator | Report task outcome |
| `info_request` | Any → any | Request information from another agent |
| `info_ready` | Any → any | Deliver requested information |
| `status_update` | Agent → orchestrator | Progress report and context pressure signals |
| `context_query` | Any → explorer/researcher | Direct peer request for context |
| `context_response` | Explorer/researcher → any | Deliver requested context |
| `handoff` | Agent → orchestrator | Transfer state before termination |
| `shutdown` | Orchestrator → agent | Graceful termination request |
| `peer_notify` | Any → any | Fire-and-forget alert via annotation channel |

## Hard Rules

These rules are non-negotiable. Violations have caused real problems.

1. **No shutdown without user approval.** NEVER send `shutdown_request` to any teammate without first asking the user and receiving explicit "yes". No exceptions — not for "completed", "idle", or "ephemeral" teammates.

2. **No idle-triggered messages.** Idle notifications are automatic turn boundaries (every 10-20 seconds). They are NOT evidence of a problem. Wait at least 3 minutes after dispatching work, then silently check output files before sending any message.

3. **One message per topic.** Never ask a teammate the same question twice. If they didn't respond, wait longer, check files, or tell the user.

## Spawning Conventions

- Agent IDs follow the pattern: `{role}-{phase}-{task}-{short_uuid}` (e.g., `coder-p1-t3-a7f2`)
- Agent ID regex: defined in `schemas/_constants.py` as `AGENT_ID_PATTERN`
- Role assignment is based on the task type, not the agent's capabilities
- Model dispatch: `opus-4-6` for complex/ambiguous work, `sonnet-4-6` for mechanical/well-defined, `haiku-4-5` for simple lookups

## Agent Lifecycle

1. **Spawn**: Orchestrator creates agent via `Task` tool with `team_name`
2. **Register**: `SessionStart` hook writes agent state to `~/.claude/state/agents/{agent-id}.json`
3. **Work**: Agent claims tasks, updates state, communicates via protocol
4. **Handoff**: If context pressure hits threshold, agent writes handoff file and notifies orchestrator
5. **Terminate**: Orchestrator sends `shutdown` message, agent confirms

## Context Pressure

- Agents monitor context usage via `PostToolUse` hook updates
- At 70%: agent sends `status_update` with `context_pressure: true`
- At 85%: agent writes handoff file and requests replacement
- Handoff files go to `.claude/handoffs/{agent-id}.json` — see `schemas/handoff.py`

## Roles

| Role | Writes Code | Writes Files | Key Constraint |
|------|-------------|--------------|----------------|
| `orchestrator` | No | No | Coordination only — never reads content files |
| `explorer` | No | Context packets | Read-only codebase access |
| `researcher` | No | Research results | External docs/API lookup |
| `coder` | Yes | Source + tests | Must follow TDD cycle, works in worktree |
| `tester` | Yes | Scenario tests | Blind to coder implementation |
| `auditor` | No | Audit reports | Reviews coder output against plan |
| `generalist` | Yes | Any | Unrestricted — no state machine |
