# File-Based Agent Messaging System — Implementation Handoff

## Session Summary

Implemented Phase 0 of a custom file-based messaging layer that replaces Claude Code's experimental Agent Teams (TeamCreate, SendMessage, Task) for inter-agent communication. The system provides typed messages, guaranteed delivery via file persistence, priority-based blocking enforcement, and full observability — integrated with the existing daemon, hooks, and state machine infrastructure.

---

## Architecture Overview

```
┌──────────────┐   Bash tool    ┌──────────────┐  flock+RMW   ┌────────────────────┐
│ Sender Agent │ ──────────────→│ send_msg.py  │ ───────────→ │ recipient.inbox    │
│ (any session)│  send-msg CLI  │ validates,   │              │ .json (envelopes)  │
│              │ ←──────────────│ stores,      │              └────────────────────┘
│              │ "sent: seq=3"  │ notifies     │                      │
└──────────────┘                │              │              ┌────────────────────┐
                                │              │ ───────────→ │ payloads/          │
                                │              │  atomic copy │ {msg_id}.json      │
                                └──────┬───────┘              │ (full content)     │
                                       │                      └────────────────────┘
                                       │ socket notify
                                       ▼
                                ┌──────────────┐
                                │    Daemon     │
                                │ (in-memory    │
                                │  session      │──→ writes annotation for recipient
                                │  registry)    │──→ sets inbox_blocked on agent state
                                └──────┬───────┘──→ registers escalation for non-blocking
                                       │
                     ┌─────────────────┬┘
                     │                 │
               (blocking)        (non-blocking)
                     │                 │
                     ▼                 ▼
             ┌──────────────┐  ┌──────────────────┐
             │ PreToolUse:  │  │ PostToolUse:      │
             │ DENY Write/  │  │ inject annotation │
             │ Edit/Bash    │  │ into context      │
             │              │  │                   │
             │ ALLOW PTC,   │  │ On state          │
             │ send-msg,    │  │ transition →      │
             │ Read, Think  │  │ escalate to       │
             └──────────────┘  │ blocking          │
                               └──────────────────┘
```

### Key design decisions made during this session:

1. **Draft file pattern** — Agents write payloads to `{agent}.draft.json` (overwritten each time, zero temp file proliferation), `send-msg send` reads from it by default. Full validated payload stored permanently at `payloads/{msg_id}.json`. Envelope in inbox is lightweight with `content_path` reference.

2. **Agent identity = readable string, not opaque token** — We explored a token-based identity system and rejected it. The `agent_id` string (e.g., `coder-p1-t3-a7f2`) flows through everything — file paths, daemon protocol, annotations, messaging. It's debuggable and already consistent across 8+ file path locations, 6+ daemon endpoints, and 4 annotation writers. Adding a session registry on top (not replacing the string) gives the daemon liveness awareness.

3. **Session registry is in-memory, not persisted** — The daemon IS the liveness authority. If it restarts, agents re-register via their next hook call. No disk persistence needed.

4. **Role-based routing** — Agents send by role (`--to-role coder`) instead of needing to know exact agent_ids. Daemon resolves via session registry, falls back to team manifest.

5. **Slash commands per V2 teammate role** — NOT per V1 sub-agent type. The user explicitly corrected this: `/orchestrator`, `/coder`, `/explorer`, `/researcher`, `/planner`, `/tester`, `/auditor` map to the V2 teammate architecture from `new_claude/agents/AGENT-ARCHITECTURE.md`, not the old `.claude/agents/` sub-agent specs.

6. **Payload validation uses existing schemas** — The `schemas/message_protocol.py` payload models (TaskAssignPayload, etc.) are reused. No schema changes needed. The `content_path` pattern handles rich content that doesn't fit the structured fields.

---

## Files Created

### Core Infrastructure

| File | Purpose | Lines |
|------|---------|-------|
| `schemas/messaging.py` | MessageEnvelope, TeamManifest, TeamRegistry, AgentRegistration models | ~85 |
| `scripts/send_msg.py` | CLI: send (with draft/--body/--payload-file), ack, list, count, team | ~540 |
| `scripts/daemon/messaging.py` | Daemon-side: handle_message_notification, handle_message_ack, escalation engine | ~220 |
| `scripts/inbox_watch.sh` | inotifywait-based zero-token idle wake-up (chmod +x) | ~25 |

### Slash Commands

| File | Purpose |
|------|---------|
| `commands/orchestrator/SKILL.md` | `/orchestrator {team}` — create or join team, load orchestrator spec, start daemon |
| `commands/explorer/SKILL.md` | `/explorer {team} [--no-team]` — join team as explorer (Sonnet model) |
| `commands/researcher/SKILL.md` | `/researcher {team} [--no-team]` — join team as researcher (Sonnet model) |
| `commands/planner/SKILL.md` | `/planner {team} [--no-team]` — join team as planner (Opus model) |
| `commands/coder/SKILL.md` | `/coder {team} [--no-team]` — join team as coder (Opus model) |
| `commands/tester/SKILL.md` | `/tester {team} [--no-team]` — join team as tester (Opus model) |
| `commands/auditor/SKILL.md` | `/auditor {team} [--no-team]` — join team as auditor (Opus model) |
| `commands/leaveteam/SKILL.md` | `/leaveteam` — graceful departure, preserve inbox |
| `commands/deleteteam/SKILL.md` | `/deleteteam {team}` — orchestrator-only teardown |

### Tests (76 passing)

| File | Tests | Coverage |
|------|-------|----------|
| `tests/messaging/test_schemas.py` | 15 | Pydantic round-trip, extra field rejection, sequence validation |
| `tests/messaging/test_send_msg.py` | 10 | send/ack/list/count/concurrent/role-routing/team |
| `tests/messaging/test_daemon_messaging.py` | 8 | notification, blocking, escalation registration |
| `tests/messaging/test_inbox_blocking.py` | 11 | PreToolUse blocking matrix (Write/Edit/Bash denied, PTC/Read/Think allowed) |
| `tests/messaging/test_escalation.py` | 7 | state-triggered escalation, escalate_to_blocking |
| `tests/messaging/test_observability.py` | 7 | event emitter output verification |
| `tests/messaging/test_session_start.py` | 3 | team scan in resumption detection |
| `tests/messaging/test_session_registry.py` | 15 | register/touch/depart/resolve_role/stale/team_sessions |

---

## Files Modified

| File | Change | Location |
|------|--------|----------|
| `schemas/agent_state.py` | Added `inbox_blocked: dict \| None` and `team_id: str \| None` fields | After line 104 (after `pending_validation_error`) |
| `schemas/__init__.py` | Added messaging model imports and `__all__` entries | After line 89 (message_protocol imports) |
| `scripts/daemon/server.py` | Added import of messaging handlers (line 30), 5 new command routes in `process_request()`: `message_notification`, `message_ack`, `team_members`, `resolve_role`, `depart_session`. Fixed `message_ack` field name mismatch (accepts both `agent_id` and `from_agent`). Updated `register` to pass `team_id`/`workflow_id`. | Lines 30, 99-130 |
| `scripts/daemon/permissions.py` | Added step 2.5 (inbox blocking) in `handle_pre_tool()` between handoff blocking and validation error blocking | Between lines 213-215 |
| `scripts/daemon/transitions.py` | Added `touch_session()` call in `handle_post_tool()` (after agent load). Added step 3.5 (non-blocking escalation) after auto-transition. | Lines ~375, ~505 |
| `scripts/daemon/state_manager.py` | Added session registry: `_session_registry` dict, 7 functions (`register_session`, `touch_session`, `depart_session`, `get_session`, `get_team_sessions`, `resolve_role_in_team`, `get_stale_sessions`). Extended `register_agent()` with `team_id`/`workflow_id` params. | After line 61 (registry), lines 335-380 (register_agent) |
| `hooks/utils/event_logger.py` | Added 7 messaging emitters after line 168: `emit_message_delivered`, `emit_message_acked`, `emit_inbox_blocked`, `emit_inbox_unblocked`, `emit_message_escalated`, `emit_team_created`, `emit_team_joined` | After `emit_subagent_validated`, before PTC emitters |
| `hooks/session_start.py` | Added team scan in `_check_workflow_resumption()`: reads `~/.claude/messages/_registry.json`, enumerates active teams and members | After line 316 (after handoff scanning) |

---

## Runtime File Structure

```
~/.claude/
├── messages/                           # Messaging root
│   ├── _registry.json                  # {teams: {name: {workflow_id, status}}}
│   └── {team_name}/
│       ├── _manifest.json              # {team_id, workflow_id, agents: {...}}
│       ├── {agent_id}.inbox.json       # [{envelope}, ...] — lightweight, has content_path
│       ├── {agent_id}.draft.json       # Agent's scratchpad (overwritten each send)
│       └── payloads/
│           └── {message_id}.json       # Full validated payload (permanent)
│
├── state/
│   ├── agents/{agent_id}.json          # Agent state (inbox_blocked, team_id, current_state, ...)
│   └── sessions/                       # (not yet created — daemon registry is in-memory)
│
├── annotations/
│   └── {agent_id}.jsonl                # Daemon-written annotations (message alerts)
│
└── temp/
    └── workflow-events.jsonl           # Observability events
```

---

## How Communication Works End-to-End

### Send flow:
1. Agent writes payload to `{agent}.draft.json` (or uses `--body`/`--payload-file`)
2. `send-msg send --to-role coder --type task_assign --priority blocking`
3. `send_msg.py` resolves role → agent_id via daemon registry (fallback: manifest)
4. Validates payload against Pydantic model from `schemas/message_protocol.py`
5. Stores validated content to `payloads/{msg_id}.json`
6. Appends lightweight envelope to recipient's inbox (flock + atomic write)
7. Notifies daemon via socket → daemon writes annotation + sets `inbox_blocked` (if blocking)

### Receive flow:
1. Recipient's next tool call triggers hooks
2. **PreToolUse** (`permissions.py` step 2.5): if `inbox_blocked` set → deny Write/Edit/Bash, allow PTC/Read/Think/send-msg
3. **PostToolUse** (`transitions.py` step 4): reads annotations → injects "[CRITICAL] New task_assign from orchestrator. Read inbox..." into context
4. Recipient reads inbox via `send-msg list` or PTC
5. Recipient reads `content_path` for full payload
6. Recipient acks: `send-msg ack --message-id {id}` → daemon clears `inbox_blocked`

### Non-blocking escalation:
1. Non-blocking messages register in `_pending_escalations` (in-memory)
2. On recipient's next state transition to a "ready" state (IDLE, TASK_COMPLETE, SYNTHESIS, etc.), `check_escalation()` fires
3. First pending message gets promoted to blocking via `escalate_to_blocking()`

### Idle wake-up:
1. Agent finishes work, runs `send-msg count` → no unread
2. Starts background watcher: `bash scripts/inbox_watch.sh {inbox_path}` (run_in_background: true)
3. inotifywait blocks until inbox file is modified → script exits
4. Claude Code auto-notifies agent → agent reads inbox, processes, repeats

---

## Session Registry

The daemon maintains an in-memory `_session_registry` dict mapping `agent_id → {team_id, role, workflow_id, registered_at, last_seen, status}`.

- **Populated**: during `register_agent()` when `team_id` is provided (slash command init)
- **Updated**: `touch_session()` called on every PostToolUse hook
- **Queried**: `team_members` command, `resolve_role` command, stale detection
- **Not persisted**: intentionally in-memory — daemon restart = fresh, agents re-register

### Daemon commands for registry:
- `{"command": "team_members", "team_id": "..."}` → `{"ok": true, "members": [...]}`
- `{"command": "resolve_role", "team_id": "...", "role": "coder"}` → `{"ok": true, "agent_ids": [...]}`
- `{"command": "depart_session", "agent_id": "..."}` → `{"ok": true}`

---

## User Preferences Expressed During Session

1. **Use subagents for implementation** — the user explicitly asked me to delegate implementation to subagents rather than doing it myself. Assign specific tasks with full context, don't do the implementation and context retrieval in the main session.

2. **V2 teammate architecture, not V1 sub-agents** — the slash commands must map to the V2 teammate roles from `new_claude/agents/AGENT-ARCHITECTURE.md` (Explorer, Researcher, Planner, Coder, Tester, Auditor), NOT the old V1 sub-agent specs in `.claude/agents/` (chunk-coder, codebase-explorer, etc.). The user corrected this explicitly.

3. **Agent spec files referenced as if they exist** — `new_claude/agents/teammates/{role}.md` files don't exist yet but will be built soon. Slash commands reference them and warn gracefully if missing.

4. **Custom agent names, not auto-naming** — the user rejected auto-naming (coder-01, coder-02) in favor of generated-but-unique names (coder-a7f2) that include a hex suffix.

5. **Draft file pattern for message payloads** — agents write to one reusable draft file (overwritten each time) instead of spawning infinite temp files. Validation runs on the draft, then content is copied to permanent storage.

6. **Readable agent_ids, not opaque tokens** — we explored and rejected token-based identity. The `agent_id` string (e.g., `coder-p1-t3-a7f2`) is the identity token. Added session registry on top for liveness, not as a replacement.

7. **Full initialization in slash commands** — not just messaging plumbing. Each `/{role}` command should load the agent behavioral spec, load skills, handle team join/create, check handoffs, register with daemon, create/load state files.

---

## Known Bugs Fixed

1. **`message_ack` field name mismatch** — `send_msg.py` sent `{"from_agent": ...}` but `server.py` expected `request["agent_id"]`. Fixed in `server.py` line 112: `request.get("agent_id") or request.get("from_agent", "")`.

2. **Concurrent sends race condition** — `flock()` is per-process, not per-thread. Added `_inbox_thread_lock = threading.Lock()` in `send_msg.py` to serialize in-process concurrent writes.

---

## What's Remaining

### Tier 1: Required for end-to-end workflow

1. **Teammate agent spec files** — `new_claude/agents/teammates/{orchestrator,explorer,researcher,planner,coder,tester,auditor}.md` don't exist. These define the behavioral contract each teammate follows. The design is in `new_claude/agents/AGENT-ARCHITECTURE.md` (§3.1-3.7) and `new_claude/agents/TEAMMATE-DIRECTOR-PATTERNS.md` (extension points per teammate). The `teammates/` directory exists but is empty.

2. **V2 state machines** — Current machines in `state-machines/` are V1. V2 needs machines based on `new_claude/agents/BASE-DIRECTOR-PATTERN.md` (14 states: SPAWNED → CONTEXT_LOADING → DELIBERATION → SUB_AGENT_DISPATCH → SYNTHESIS → OUTPUT_VALIDATION → DELIVERY → IDLE, etc.). Without these, the daemon either has no machine for the V2 roles or uses V1 machines with wrong states/transitions.

3. **Sub-agent spawn mediation** — `AGENT-ARCHITECTURE.md` says teammates can't spawn sub-agents directly (Claude Code limitation). Pattern is: teammate writes delegation JSON to disk → sends `info_request` to orchestrator → orchestrator reads delegation JSON, spawns sub-agent via Agent tool → sub-agent returns → orchestrator sends `info_ready` back. The messaging infrastructure supports `info_request`/`info_ready` types, but there's no orchestrator-side logic to parse spawn requests and act on them.

4. **Payload model flexibility** — All payload models use `extra="forbid"` in `schemas/message_protocol.py`. V2 teammates need to attach delegation JSON paths, sub-agent params, context references. Options: (a) add `content: dict | None = None` field to each payload, (b) switch to `extra="allow"` on agent-facing payloads, or (c) rely on `content_path` pattern (already built — rich content goes to payload file, not inline). Option (c) may be sufficient.

### Tier 2: Operational quality

5. **Payload cleanup on ack** — `payloads/{msg_id}.json` files accumulate. `cmd_ack` should delete the payload file after marking read.

6. **Inbox rotation** — Inbox files grow with every message. Need archival of read messages.

7. **`team_id` validation in messaging** — `handle_message_notification` and `handle_message_ack` accept but ignore `team_id`. Should validate sender/recipient are in the same team.

8. **Daemon auto-start alignment** — Slash commands set `WORKFLOW_ID` and try to start daemon, but if daemon is already running with a different workflow_id, agents connect to different daemons. Need convention: one daemon per team, or one global daemon.

9. **Stale session reaping** — `get_stale_sessions()` exists but nothing calls it automatically. Need a periodic check (timer thread in daemon) that marks stale sessions as departed and optionally notifies the orchestrator.

### Tier 3: Future phases

10. **Cross-team messaging** — Current design is single-team only.
11. **Delivery confirmation** — Sender doesn't know if recipient read the message.
12. **New message types** — `spawn_request`/`spawn_result` for sub-agent mediation, `audit_request` for coder→auditor flow.

---

## Key File References for Continuation

| Need | File |
|------|------|
| Message envelope schema | `schemas/messaging.py` |
| Payload validation models | `schemas/message_protocol.py` |
| Agent state with inbox_blocked | `schemas/agent_state.py` (lines 110-119) |
| CLI send/ack/list/count/team | `scripts/send_msg.py` |
| Daemon message handlers | `scripts/daemon/messaging.py` |
| Session registry | `scripts/daemon/state_manager.py` (lines 62-130) |
| PreToolUse inbox blocking | `scripts/daemon/permissions.py` (step 2.5, ~line 215) |
| PostToolUse escalation | `scripts/daemon/transitions.py` (step 3.5, ~line 505) |
| Observability emitters | `hooks/utils/event_logger.py` (messaging section) |
| V2 architecture design | `new_claude/agents/AGENT-ARCHITECTURE.md` |
| V2 teammate patterns | `new_claude/agents/TEAMMATE-DIRECTOR-PATTERNS.md` |
| V2 base director pattern | `new_claude/agents/BASE-DIRECTOR-PATTERN.md` |
| Existing V1 state machines | `state-machines/*.json` |
| Original implementation plan | Read the transcript at the path in this handoff's parent directory |
| All messaging tests | `tests/messaging/` (76 tests) |

---

## Test Verification

```bash
# Run all messaging tests (should be 76 passing)
python -m pytest tests/messaging/ -v

# Run daemon tests to verify no regressions
python -m pytest tests/daemon/test_pre_tool.py tests/daemon/test_post_tool.py tests/daemon/test_core_logic.py -v

# One pre-existing failure (unrelated to messaging):
# tests/daemon/test_integration.py::TestThinkEnforcement::test_think_enforcement_end_to_end
# KeyError: 'acknowledged' — existed before this work
```
