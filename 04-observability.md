# Doc 4: Observability

**Status:** Draft v1
**Depends on:** Doc 0 (Token Efficiency Standards), Doc 1 (Git Management)
**Unblocks:** Doc 5 (Agent Specifications)

## Problem

The workflow system generates events across 8 agent types, 8 state machines, inter-agent messages, decision logs, context pressure signals, and worktree lifecycles. Without structured observability, the human operator has no visibility into what agents are doing, why they made decisions, or where token budget is going. The existing event logging infrastructure (`event_logger.py`, `trace_context.py`, `context_monitor.py`, `error_logger.py`) is partially built but disconnected — `trace_context.py` (269 lines of W3C-compliant trace propagation) has never been called. There is no unified query layer, no real-time viewer, and no cross-session correlation.

**Key constraint: Zero token impact on agents.** All observability data flows are fire-and-forget. Agents never read observability data. No `additionalContext` injection for observability purposes. This system is purely for the human operator.

---

## 1. Three-Surface Taxonomy

Adapted from the AgentTrace research (arxiv 2602.10133). Every observable event belongs to exactly one of three surfaces. Each surface has its own data format, storage location, and query patterns.

### 1.1 Operational Surface

What the system is *doing*: state transitions, tool calls, hook executions, timing, agent lifecycle events.

| Event type | Source | Log file |
|---|---|---|
| `state_transition` | `workflow_state.py` `log_transition` (line 186) | `~/.claude/logs/state-transitions.jsonl` |
| `message_sent` | `post_tool_use.py` `handle_send_message` (line 203) | `~/.claude/logs/message-bus.jsonl` |
| `agent_registered` | `event_logger.py` `emit_agent_registered` (line 128) | `~/.claude/temp/workflow-events.jsonl` |
| `agent_terminated` | `event_logger.py` `emit_agent_terminated` (line 133) | `~/.claude/temp/workflow-events.jsonl` |
| `subagent_started` | `subagent_start.py` via `emit_subagent_started` (line 78) | `~/.claude/temp/workflow-events.jsonl` |
| `subagent_completed` | `post_tool_use.py` `handle_task_completion` (line 324) | `~/.claude/temp/workflow-events.jsonl` |
| `subagent_validated` | `event_logger.py` `emit_subagent_validated` (line 158) | `~/.claude/temp/workflow-events.jsonl` |
| `pre_compact` | `event_logger.py` `emit_pre_compact` (line 138) | `~/.claude/temp/workflow-events.jsonl` |
| `hook_error` | `error_logger.py` `log_hook_error` (line 24) | `~/.claude/logs/hook-errors.jsonl` |
| `worktree_created` | `worktree_create.py` (Doc 1, Section 1.4) | `~/.claude/logs/worktree-lifecycle.jsonl` |
| `worktree_removed` | `worktree_remove.py` (Doc 1, Section 1.5) | `~/.claude/logs/worktree-lifecycle.jsonl` |

**Data format:** Each entry is a single JSON object on one JSONL line. Minimum fields: `ts` (UTC ISO), `event` (string), `trace_id` (32-hex, added by Section 4), `span_id` (16-hex), `workflow_id` (string).

### 1.2 Cognitive Surface

What agents are *thinking*: Think tool outputs, decision reasoning, options considered, choices made.

| Event type | Source | Log file |
|---|---|---|
| `decision_logged` | `post_tool_use.py` `handle_think` (line 166) | `~/.claude/logs/decisions/{agent-id}.jsonl` |

**Data format:** Each entry includes `agent_id`, `decision_point`, `thought`, `considered`, `chosen`, `agent_role`, `current_state`. The structured THOUGHT/CONSIDERED/CHOSEN format is parsed by `_parse_decision_output` (line 548 of `post_tool_use.py`).

Cognitive events are the most valuable for post-hoc analysis — they reveal *why* an agent chose a particular implementation approach, test strategy, or conflict resolution path.

### 1.3 Contextual Surface

What resources agents are *consuming*: context window usage, skill loads, handoffs, token consumption, file I/O patterns.

| Event type | Source | Log file |
|---|---|---|
| `context_pressure` | `context_monitor.py` via `post_tool_use.py` `handle_context_pressure` (line 375) | `~/.claude/temp/workflow-events.jsonl` |
| `skill_loaded` | `post_tool_use.py` `handle_read_skill` (line 318) | `~/.claude/temp/workflow-events.jsonl` |
| `file_written` | `post_tool_use.py` `handle_write_event` (line 312) | `~/.claude/temp/workflow-events.jsonl` |
| `context_check` | `context_monitor.py` `log_context_check` (line 144) | `~/.claude/logs/context-checks.jsonl` |

**Data format:** Same JSONL envelope. Context pressure events include `percentage` (0-100 scale) and `level` (normal/warning/critical). Skill load events include `skill` name and `file` path.

### 1.4 Common Envelope

All events across all three surfaces share a common envelope for correlation:

```json
{
  "ts": "2026-02-26T10:00:00.123456+00:00",
  "event": "state_transition",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "workflow_id": "wf-lpr-20260226-a3b4",
  "surface": "operational",
  ...event-specific fields...
}
```

The `surface` field is optional on disk (derivable from event type) but required in the SQLite aggregation layer (Section 7.3) for cross-surface queries.

---

## 2. JSONL Dual-Output Architecture

Following the AgentTrace pattern: JSONL files as the primary store (always available, zero dependencies), with an optional OTel bridge for external tools.

### 2.1 Always-On Local JSONL

Already implemented in `hooks/utils/event_logger.py` (line 22, `emit()` function). Current output: `~/.claude/temp/workflow-events.jsonl`.

**Current behavior:**
- Fire-and-forget: all exceptions caught silently (line 41-42)
- Append-only: one JSON object per line
- File rotation at session start: `rotate_events_file()` (line 176) archives current file, keeps 5 most recent

**Changes needed:**
1. Add `trace_id`, `span_id`, `workflow_id` fields to every `emit()` call (Section 4)
2. Move general events from `~/.claude/temp/workflow-events.jsonl` to `~/.claude/logs/workflow-events.jsonl` (aligns with other logs)
3. State transitions stay in their dedicated file (`~/.claude/logs/state-transitions.jsonl`)
4. Decisions stay in their per-agent files (`~/.claude/logs/decisions/{agent-id}.jsonl`)
5. Messages stay in their dedicated file (`~/.claude/logs/message-bus.jsonl`)

### 2.2 Optional OTel Export

A separate process that reads JSONL files and converts them to OpenTelemetry spans. Never runs inside hooks — zero latency impact on the agent pipeline.

Detailed in Section 6.

### 2.3 Why JSONL First

- Zero infrastructure: works on any machine with a filesystem
- `grep`/`jq` queryable: `jq 'select(.event=="state_transition")' workflow-events.jsonl`
- Append-friendly: `fcntl` file locking already in `state_helpers.py` (line 147, `locked_read_modify_write`)
- Streamable: TUI dashboard tails files in real time
- Portable: copy JSONL files to any machine for offline analysis

---

## 3. Data Sources

Complete mapping of every existing hook/utility to its observability surface:

| Source file | Function/handler | Surface | Events generated | Current status |
|---|---|---|---|---|
| `scripts/workflow_state.py` (line 186) | `log_transition` | Operational | `state_transition` | Implemented, writes to `state-transitions.jsonl` |
| `hooks/post_tool_use.py` (line 166) | `handle_think` | Cognitive | `decision_logged` | Implemented, writes to `decisions/{agent-id}.jsonl` |
| `hooks/post_tool_use.py` (line 203) | `handle_send_message` | Operational | `message_sent` | Implemented, writes to `message-bus.jsonl` |
| `hooks/utils/event_logger.py` (line 22) | `emit` (all emitters) | All | various | Implemented, writes to `workflow-events.jsonl` |
| `hooks/utils/context_monitor.py` (line 43) | `check_context_usage` | Contextual | `context_pressure` | Implemented, emitted via `handle_context_pressure` |
| `hooks/utils/context_monitor.py` (line 144) | `log_context_check` | Contextual | context check entry | Implemented, writes to `context-checks.jsonl` |
| `hooks/utils/trace_context.py` | `TraceContext` class | Cross-cutting | trace propagation | **Implemented but UNUSED** |
| `hooks/subagent_start.py` (line 78) | via `emit_subagent_started` | Contextual | `subagent_started` | Implemented |
| `hooks/post_tool_use.py` (line 318) | `handle_read_skill` | Contextual | `skill_loaded` | Implemented |
| `hooks/post_tool_use.py` (line 312) | `handle_write_event` | Contextual | `file_written` | Implemented |
| `hooks/post_tool_use.py` (line 324) | `handle_task_completion` | Operational | `subagent_completed` | Implemented |
| `hooks/utils/error_logger.py` (line 24) | `log_hook_error` | Operational | `hook_error` | Implemented, writes to `hook-errors.jsonl` |
| `hooks/session_start.py` (line 130) | via `emit_agent_registered` | Operational | `agent_registered` | Implemented |

---

## 4. Activating trace_context.py

`hooks/utils/trace_context.py` (269 lines) is a fully implemented W3C Trace Context propagation system that has never been called. It supports all four propagation channels: environment variables, dict serialization, file-based (agent to hooks), and handoff. This section designs its activation.

### 4.1 Trace Initialization at Workflow Start

When the orchestrator starts a workflow (system state machine transitions from `IDLE` to `DESIGN_LOADED`), create a root trace:

```python
# In session_start.py, when handling orchestrator startup with active workflow
from hooks.utils.trace_context import TraceContext

root = TraceContext.new_trace()
root.save("orchestrator")
# Also write trace_id to workflow state for persistence
workflow_state["trace_id"] = root.trace_id
```

The `trace_id` is written to `WorkflowState` (in `~/.claude/state/system.json`) so it survives orchestrator compaction. A new `trace_id` field on `WorkflowState` (schema addition, per Doc 0 Section 7.3 versioning: `1-0-1` addition).

### 4.2 Child Spans at Agent Spawn

When the orchestrator dispatches a teammate, create a child span and propagate via environment variables:

```python
# In orchestrator dispatch logic (spawning a coder, explorer, etc.)
parent_ctx = TraceContext.load("orchestrator")
if parent_ctx:
    child = parent_ctx.child_span()
    spawn_env = child.to_env()  # {CLAUDE_TRACE_ID, CLAUDE_SPAN_ID, CLAUDE_PARENT_SPAN_ID}
    # Pass spawn_env to agent spawn command
```

The spawned agent reads inherited context at startup:

```python
# In session_start.py, during _handle_agent_session
inherited = TraceContext.from_env()
if inherited:
    my_span = inherited.child_span()
    my_span.save(agent_id)
else:
    # Not in a traced workflow — generate standalone trace
    standalone = TraceContext.new_trace()
    standalone.save(agent_id)
```

### 4.3 Trace Context in Log Entries

Every log entry gets `trace_id` and `span_id` by reading the agent's saved trace file:

```python
# In event_logger.py emit() function
from hooks.utils.trace_context import TraceContext

def emit(event: str, **kwargs: object) -> None:
    entry = {"ts": ..., "event": event}

    # Add trace context if available
    agent_id = os.environ.get("CLAUDE_CODE_AGENT_NAME", "unknown")
    ctx = TraceContext.load(agent_id) if agent_id != "unknown" else None
    if ctx:
        entry["trace_id"] = ctx.trace_id
        entry["span_id"] = ctx.span_id

    # Add workflow_id from env (set at workflow start)
    entry["workflow_id"] = os.environ.get("WORKFLOW_ID", "")

    entry.update(kwargs)
    ...
```

### 4.4 workflow_id Propagation

The `workflow_id` is a human-readable identifier (e.g., `wf-lpr-20260226-a3b4`) distinct from `trace_id` (opaque 128-bit hex). It serves a different purpose:

- `trace_id`: W3C-standard correlation across distributed spans, used by OTel backends
- `workflow_id`: Human-readable session identifier for filtering logs ("show me everything from this afternoon's workflow run")

Propagation path:
1. Orchestrator generates `workflow_id` at workflow start (or reads from `WorkflowState` on resume)
2. Written to `WorkflowState.workflow_id` in `~/.claude/state/system.json`
3. Passed to spawned agents via `WORKFLOW_ID` environment variable
4. All hooks read `WORKFLOW_ID` from env and include in log entries

### 4.5 Trace Propagation Summary

```
Orchestrator starts workflow
    └── TraceContext.new_trace() → root trace (trace_id=abc..., span_id=111...)
        ├── root.save("orchestrator")
        └── root.child_span() → child for coder-p1-t3
            ├── child.to_env() → passed to agent spawn
            ├── Coder reads TraceContext.from_env() at startup
            ├── coder_span = inherited.child_span()
            ├── coder_span.save("coder-p1-t3-a7f2")
            └── All coder's log entries include trace_id=abc..., span_id=222...
```

All trace state files: `~/.claude/state/traces/{agent-id}.json` (already defined in `trace_context.py` line 49).

---

## 5. State Snapshot Writing

Periodic snapshots capture the full system state at a point in time. They are the primary debugging tool when something goes wrong — "what was every agent doing at 10:15am?"

### 5.1 Trigger Points

1. **On every system state transition:** When `workflow_state.py` `log_transition` fires for the system state machine (machine_name is `"system"`), a snapshot is written alongside the transition log entry.
2. **Periodic:** Every 5 minutes during active work (when system state is not `IDLE` or `COMPLETE`). Implemented as a timer in the `workflow_state.py` daemon's serve loop.

### 5.2 Snapshot Contents

```json
{
  "timestamp": "2026-02-26T10:15:00+00:00",
  "workflow_id": "wf-lpr-20260226-a3b4",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "system_state": "PHASE_IMPLEMENTATION",
  "trigger": "all_coders_dispatched",
  "agents": [
    {
      "id": "coder-p1-t3-a7f2",
      "role": "coder",
      "state": "IMPLEMENTATION",
      "context_pct": 45.2,
      "worktree": "/tmp/wt-coder-p1-t3-a7f2",
      "task": "t3",
      "phase": "p1"
    },
    {
      "id": "coder-p1-t5-b3e1",
      "role": "coder",
      "state": "TEST_DESIGN",
      "context_pct": 12.0,
      "worktree": "/tmp/wt-coder-p1-t5-b3e1",
      "task": "t5",
      "phase": "p1"
    }
  ],
  "merge_queue": [
    {"agent_id": "coder-p1-t3-a7f2", "task_id": "t3", "priority": 1, "status": "pending"}
  ],
  "active_worktrees": [
    "/tmp/wt-coder-p1-t3-a7f2",
    "/tmp/wt-coder-p1-t5-b3e1"
  ],
  "feature": "lpr-tracking",
  "base_branch": "feature/lpr-tracking",
  "phase": "p1"
}
```

### 5.3 Storage

- **Directory:** `~/.claude/logs/snapshots/`  (already defined in `state_helpers.py` line 28: `SNAPSHOTS_DIR`)
- **File naming:** `{timestamp}-{system_state}.json` (e.g., `20260226-101500-PHASE_IMPLEMENTATION.json`)
- **Retention:** Keep snapshots from the last 3 workflow runs. Older snapshots are pruned at workflow start (same pattern as `rotate_events_file` in `event_logger.py`).

### 5.4 Snapshot Writer

```python
# In workflow_state.py, called from log_transition when machine == "system"
def write_snapshot(system_state: str, trigger: str) -> None:
    """Write a point-in-time snapshot of all agent and system state."""
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "system_state": system_state,
        "trigger": trigger,
    }

    # Gather agent states
    agents = []
    for state_file in AGENTS_DIR.glob("*.json"):
        agent = json.loads(state_file.read_text())
        if agent.get("status") == "active":
            agents.append({
                "id": agent["id"],
                "role": agent["role"],
                "state": agent.get("current_state"),
                "context_pct": agent.get("context_usage_pct", 0),
                "worktree": agent.get("worktree"),
                "task": agent.get("task"),
                "phase": agent.get("phase"),
            })
    snapshot["agents"] = agents

    # Gather workflow state
    wf = load_system_state()
    if wf:
        snapshot["workflow_id"] = wf.workflow_id
        snapshot["trace_id"] = getattr(wf, "trace_id", "")
        snapshot["feature"] = wf.feature
        snapshot["base_branch"] = wf.base_branch
        snapshot["phase"] = wf.current_phase
        snapshot["merge_queue"] = wf.merge_queue or []

    # Gather active worktrees
    snapshot["active_worktrees"] = [a["worktree"] for a in agents if a.get("worktree")]

    # Write
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"{ts}-{system_state}.json"
    path = SNAPSHOTS_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(snapshot, indent=2))
```

---

## 6. OTel Bridge

A separate process that converts JSONL log data into OpenTelemetry spans. It runs outside the hook pipeline — zero latency impact on agents.

### 6.1 Architecture

```
JSONL files (primary store)          OTel Bridge (separate process)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
state-transitions.jsonl  ──┐
workflow-events.jsonl    ──┼──→  scripts/otel_bridge.py  ──→  OTel Collector / Backend
message-bus.jsonl        ──┤         (tail + convert)         (Jaeger, SigNoz, Langfuse)
decisions/*.jsonl        ──┘
```

### 6.2 Span Mapping

JSONL events map to OTel spans following the GenAI semantic conventions (opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/):

| JSONL event | OTel span kind | OTel span name | Attributes |
|---|---|---|---|
| `agent_registered` | `create_agent` | `create_agent {role}` | `gen_ai.agent.id`, `gen_ai.agent.name`, `gen_ai.request.model` |
| `state_transition` | INTERNAL | `transition {from}→{to}` | `machine`, `trigger`, `entity_id` |
| `message_sent` | CLIENT | `message {from}→{to}` | `message_type`, `from_agent`, `to_agent` |
| `decision_logged` | INTERNAL | `decision {point}` | `agent_role`, `current_state`, `decision_point` |
| `context_pressure` | INTERNAL | `context_pressure {level}` | `percentage`, `level`, `agent` |

### 6.3 W3C Traceparent Generation

The bridge uses `trace_context.py`'s `to_traceparent()` method (line 240) to generate W3C headers for OTel spans:

```python
from hooks.utils.trace_context import TraceContext

ctx = TraceContext(trace_id=entry["trace_id"], span_id=entry["span_id"])
traceparent = ctx.to_traceparent()
# Result: "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
```

This ensures that spans exported by the bridge correlate with Claude Code's native OTel telemetry (enabled via `CLAUDE_CODE_ENABLE_TELEMETRY=1`, `OTEL_METRICS_EXPORTER=otlp`).

### 6.4 Bridge Operation

```bash
# Start the bridge (runs as a long-lived process)
python3 scripts/otel_bridge.py --endpoint http://localhost:4317

# Or with Claude Code's native OTel env vars
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 python3 scripts/otel_bridge.py
```

The bridge:
1. Tails all JSONL files using `inotify` (Linux) or `kqueue` (macOS)
2. Parses each new line as JSON
3. Maps to OTel span using the table above
4. Batches spans (100 spans or 5 seconds, whichever comes first)
5. Exports via OTLP gRPC to the configured endpoint
6. Tracks file offsets in `~/.claude/temp/otel-bridge-offsets.json` for crash recovery

### 6.5 When NOT to Run the Bridge

The bridge is entirely optional. The JSONL files are the primary store. The bridge adds value only when:
- An OTel backend (Jaeger, SigNoz, Langfuse) is running
- The operator wants timeline/waterfall visualization
- Cross-tool correlation with Claude Code's native telemetry is desired

If no backend is configured, the bridge does not start. No data is lost — it can replay from JSONL files at any time.

---

## 7. TUI Dashboard

A terminal-native dashboard built with Textual (Python TUI framework) for real-time workflow monitoring. Runs in a dedicated tmux pane alongside the agent team.

### 7.1 Two Viewers

From `agentic_workflow_design.md` (Section "TUI Viewers"):

**Message Viewer** — Real-time inter-agent message stream:
- Columns: time, from, to, type, state, task, summary
- Detail pane: full message content on selection
- Hybrid search: FTS5 keywords + optional nomic embedding similarity (768d)
- Session scoping: current session / previous sessions / all sessions
- Agent focus mode: filter to messages involving a specific agent
- Vim-style navigation, live pulse bar, cost ticker, token sparklines
- Data source: tails `~/.claude/logs/message-bus.jsonl`

**State Transition Viewer** — State machine visualization:
- Columns: agent, time, from state, to state, trigger, duration
- Clickable transitions: shows the full snapshot from `~/.claude/logs/snapshots/`
- Filterable by agent, phase, state
- Export to JSONL
- Data source: tails `~/.claude/logs/state-transitions.jsonl`

### 7.2 Dashboard Layout

```
┌─────────────────────────────────────────────────────┐
│  Workflow: wf-lpr-20260226 │ State: PHASE_IMPL     │
│  Agents: 4 active │ Phase: p1 │ Elapsed: 1h 23m    │
├─────────────────────────────────────────────────────┤
│  [Messages]  [State Transitions]  [Decisions]       │
├─────────────────────────────────────────────────────┤
│  10:15:02  orchestrator → coder-p1-t3  task_assign  │
│  10:15:05  coder-p1-t3 → explorer     ctx_request   │
│  10:15:12  explorer → coder-p1-t3     ctx_response  │
│  10:15:30  coder-p1-t3: TEST_DESIGN → TESTS_WRITTEN│
│  10:16:01  coder-p1-t3: TESTS_WRITTEN → TDD_RED    │
│  ...                                                │
├─────────────────────────────────────────────────────┤
│  Search: [________________]  Filter: [all agents ▼] │
└─────────────────────────────────────────────────────┘
```

### 7.3 SQLite Aggregation Layer

The TUI does not query JSONL files directly for search — that would be O(n) per query. Instead, a background ingestion thread periodically loads new JSONL entries into a SQLite database.

**Database:** `~/.claude/logs/observability.db`

**Tables:**

```sql
-- Unified event table with FTS5 for keyword search
CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    event TEXT NOT NULL,
    surface TEXT NOT NULL,     -- operational, cognitive, contextual
    agent_id TEXT,
    workflow_id TEXT,
    trace_id TEXT,
    span_id TEXT,
    raw_json TEXT NOT NULL     -- full JSONL line for detail view
);

CREATE INDEX idx_events_workflow ON events(workflow_id);
CREATE INDEX idx_events_agent ON events(agent_id);
CREATE INDEX idx_events_timestamp ON events(timestamp);
CREATE INDEX idx_events_surface ON events(surface);

-- FTS5 for keyword search across all event data
CREATE VIRTUAL TABLE events_fts USING fts5(
    event, agent_id, raw_json,
    content=events,
    content_rowid=id
);
```

**Ingestion:** A background thread in the TUI process reads new lines from all JSONL files every 2 seconds, inserts into SQLite, and updates FTS5. File offsets are tracked to avoid re-processing.

**Cross-session querying:** Because all JSONL files include `workflow_id`, the SQLite layer can filter by workflow session. Previous sessions are available via archived JSONL files (from `rotate_events_file`).

### 7.4 Optional Semantic Search

If `nomic-ai/nomic-embed-text-v1.5` is available (already specified in `agentic_workflow_design.md`), the TUI adds embedding-based semantic search:

- Decision log entries are embedded at ingestion time (768d vectors)
- Message summaries are embedded at ingestion time
- User types a natural language query, it is embedded, and cosine similarity finds relevant entries
- Fallback: if nomic is not installed, only FTS5 keyword search is available

This is a P2 feature — the TUI works without it.

### 7.5 Dashboard Script

Location: `scripts/tui_dashboard.py`

```bash
# Launch the dashboard
python3 scripts/tui_dashboard.py

# Launch with specific workflow filter
python3 scripts/tui_dashboard.py --workflow-id wf-lpr-20260226-a3b4

# Launch in a tmux pane (designed for this)
tmux split-window -h 'python3 scripts/tui_dashboard.py'
```

---

## 8. Async Hook Integration

Per Doc 0 Section 4.4, PostToolUse handlers are split into synchronous (must-return) and asynchronous (fire-and-forget).

### 8.1 What Moves to Async

| Handler | Current location | Target | Rationale |
|---|---|---|---|
| `handle_think` (decision logging) | `post_tool_use.py` line 166 | `post_tool_use_async.py` | JSONL append is fire-and-forget, no feedback needed |
| `handle_send_message` (message bus) | `post_tool_use.py` line 203 | `post_tool_use_async.py` | JSONL append is fire-and-forget, no feedback needed |
| `handle_write_event` (file written event) | `post_tool_use.py` line 312 | `post_tool_use_async.py` | Event emission is fire-and-forget |
| `handle_read_skill` (skill loaded event) | `post_tool_use.py` line 318 | `post_tool_use_async.py` | Event emission is fire-and-forget |
| `handle_task_completion` (subagent event) | `post_tool_use.py` line 324 | `post_tool_use_async.py` | Event emission is fire-and-forget |

### 8.2 What Stays Synchronous

| Handler | Location | Rationale |
|---|---|---|
| `handle_write_validate` | `post_tool_use.py` line 237 | Returns feedback to agent on validation failure |
| `handle_write_lint` | `post_tool_use.py` line 263 | Returns feedback to agent on critical lint errors |
| `handle_write_track` | `post_tool_use.py` line 294 | Feeds `handle_user_correction` (needs tracking data available immediately) |
| `handle_update_context` | `post_tool_use.py` line 350 | State file update needed for subsequent context pressure check |
| `handle_context_pressure` | `post_tool_use.py` line 375 | Returns feedback to agent on context warnings |
| `handle_annotations` | `post_tool_use.py` line 413 | Returns `additionalContext` to agent |
| `handle_user_correction` | `post_tool_use.py` line 456 | Returns feedback to agent |

### 8.3 State Transition Logging Stays Synchronous

`workflow_state.py` `log_transition` (line 186) stays synchronous. It runs inside the state machine daemon (not in PostToolUse hooks), and state transition accuracy requires synchronous writes. The daemon already handles its own I/O — moving it to async would add complexity with no benefit, since the daemon is not in the hook latency path.

### 8.4 Hook Configuration

From Doc 0 Section 4.4:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "type": "command",
        "command": "python3 hooks/post_tool_use.py",
        "timeout": 3,
        "statusMessage": "Validating..."
      },
      {
        "type": "command",
        "command": "python3 hooks/post_tool_use_async.py",
        "async": true
      }
    ]
  }
}
```

---

## 9. Multi-Session Log Correlation

### 9.1 workflow_id on Every Log Entry

Every JSONL event includes a `workflow_id` field. This is the primary correlation key for human operators:

- "Show me all events from this afternoon's run" = filter by `workflow_id`
- "Show me all events across all runs" = no filter
- "Show me what happened to coder-p1-t3 in run X" = filter by `workflow_id` + `agent_id`

### 9.2 Generating workflow_id

Format: `wf-{feature-slug}-{date}-{short-random}` (e.g., `wf-lpr-tracking-20260226-a3b4`)

Generated by the orchestrator when starting a new workflow. Stored in `WorkflowState.workflow_id`. Propagated to agents via the `WORKFLOW_ID` environment variable (set at spawn time, alongside trace context env vars from Section 4.2).

### 9.3 Cross-Session Unified View

The TUI dashboard (Section 7) provides unified views via the SQLite aggregation layer:

1. **Current session:** Filter by the active `workflow_id`
2. **Previous session:** Select from a dropdown of known `workflow_id` values in the database
3. **All sessions:** No filter — shows timeline across all workflow runs
4. **Comparison view:** Side-by-side two sessions (same feature, different runs) for debugging regressions

### 9.4 Concurrent Write Safety

Multiple agents may write to the same JSONL files concurrently (e.g., two coders both triggering `handle_write_event` simultaneously). Safety mechanisms:

- `event_logger.py` uses Python's built-in file append mode (`open(f, "a")`) — on Linux, appends to regular files are atomic up to `PIPE_BUF` (4096 bytes). Single JSONL lines are well under this limit.
- `locked_read_modify_write` in `state_helpers.py` (line 147) uses `fcntl.LOCK_EX` for files that need read-modify-write (not applicable to append-only JSONL, but used for state files).
- The `workflow_state.py` daemon serializes all state transitions through a single Unix domain socket, so `state-transitions.jsonl` is never written concurrently.

---

## 10. Token Budget

### 10.1 Zero Token Impact on Agents

Per the key constraint stated in the Problem section:

| Component | Token injection | Rationale |
|---|---|---|
| JSONL event logging | 0 tokens | Fire-and-forget writes, no `additionalContext` |
| State snapshots | 0 tokens | Written by daemon, never read by agents |
| OTel bridge | 0 tokens | Separate process, no agent interaction |
| TUI dashboard | 0 tokens | Separate process, reads JSONL files only |
| SQLite aggregation | 0 tokens | Background thread in TUI, no agent interaction |
| Trace context propagation | 0 tokens | Environment variables set at spawn, trace files read by hooks only |
| Decision log writes | 0 tokens | Async hook, no feedback return |

The only agent-visible observability data is the `trace_id`/`span_id` fields on log entries — but agents never read those entries, so the tokens are zero.

### 10.2 JSONL File Sizes

Estimated per-session sizes (based on typical workflow: 4 coders, 10 tasks, 2 phases):

| File | Est. entries/session | Est. bytes/entry | Est. file size |
|---|---|---|---|
| `workflow-events.jsonl` | ~500 | ~200 bytes | ~100 KB |
| `state-transitions.jsonl` | ~200 | ~250 bytes | ~50 KB |
| `message-bus.jsonl` | ~150 | ~500 bytes (includes content) | ~75 KB |
| `decisions/{agent}.jsonl` | ~20/agent, ~80 total | ~800 bytes | ~64 KB |
| `context-checks.jsonl` | ~100 | ~150 bytes | ~15 KB |
| `hook-errors.jsonl` | ~5 (ideally 0) | ~500 bytes | ~2.5 KB |
| **Total per session** | | | **~310 KB** |

### 10.3 Rotation Strategy

Already implemented in `event_logger.py` `rotate_events_file()` (line 176):
- Rotates on session start
- Keeps 5 most recent archives
- Naming: `workflow-events-{YYYYMMDD-HHMMSS}.jsonl`

Extend the same pattern to other JSONL files. Each file gets its own rotation function following the same logic. Total disk usage with 5 archived sessions: ~1.5 MB. Negligible.

### 10.4 OTel Bridge Overhead

The bridge is a separate Python process:
- Memory: ~50 MB (Python + OTLP client)
- CPU: negligible (event-driven, idle between batches)
- Network: ~1 KB per span exported
- No agent overhead: runs independently, reads files only

---

## 11. Integration Points

| File | Action | Changes | Line refs |
|---|---|---|---|
| `hooks/utils/event_logger.py` | **Modify** | Add `trace_id`, `span_id`, `workflow_id` to `emit()`; move output to `~/.claude/logs/` | Line 22 (`emit`), line 19 (`EVENTS_FILE`) |
| `hooks/utils/trace_context.py` | **No change** | Already complete; activated by callers | All 269 lines |
| `hooks/session_start.py` | **Modify** | Initialize `TraceContext.new_trace()` for orchestrator; read `TraceContext.from_env()` for agents; save trace file | Lines 89-94 (`_handle_agent_session`), lines 206-229 (`_handle_orchestrator_session`) |
| `hooks/subagent_start.py` | **Modify** | Propagate trace context env vars to spawned agent; add `workflow_id` to spawn env | Line 78 (after `emit_subagent_started`) |
| `hooks/post_tool_use.py` | **Modify** | Remove handlers moving to async (Section 8.1); keep sync handlers | Lines 488-508 (dispatch tables) |
| `hooks/post_tool_use_async.py` | **New** | Async PostToolUse handler: decision logging, message bus, event emission | — |
| `hooks/utils/error_logger.py` | **Modify** | Add `trace_id`, `span_id`, `workflow_id` to error entries | Line 37 (entry dict construction) |
| `hooks/utils/context_monitor.py` | **Modify** | Add `trace_id`, `workflow_id` to context check entries | Line 154 (entry dict in `log_context_check`) |
| `scripts/workflow_state.py` | **Modify** | Add `write_snapshot()` call in `log_transition` for system transitions; add periodic snapshot timer in daemon serve loop; add `trace_id`/`workflow_id` to transition log entries | Line 186 (`log_transition`), daemon serve function |
| `schemas/system_state.py` | **Modify** | Add `trace_id: str | None` and `workflow_id: str | None` fields to `WorkflowState` | Schema addition (version `1-0-1`) |
| `scripts/otel_bridge.py` | **New** | JSONL-to-OTel span converter; tail + batch + export via OTLP gRPC | — |
| `scripts/tui_dashboard.py` | **New** | Textual TUI with message viewer, state transition viewer, SQLite search | — |
| `.claude/settings.json` | **Modify** | Add async PostToolUse hook entry (per Doc 0 Section 4.4) | Hook configuration section |

---

## 12. Verification Criteria

- [ ] `trace_context.py` is activated: `TraceContext.new_trace()` called at workflow start in `session_start.py`
- [ ] `TraceContext.from_env()` called at agent startup; child span saved to `~/.claude/state/traces/{agent-id}.json`
- [ ] Every JSONL log entry across all files includes `trace_id`, `span_id`, and `workflow_id` fields
- [ ] `workflow_id` generated at workflow start and stored in `WorkflowState`
- [ ] `workflow_id` propagated to all agents via `WORKFLOW_ID` environment variable
- [ ] State snapshots written to `~/.claude/logs/snapshots/` on every system state transition
- [ ] State snapshots written periodically (every 5 minutes) during active workflow
- [ ] Snapshot files contain all agent states, system state, merge queue, and active worktrees
- [ ] `post_tool_use_async.py` created with decision, message, and event handlers moved from sync
- [ ] `post_tool_use.py` retains only sync handlers (validation, lint, context pressure, annotations)
- [ ] Async hook registered in `.claude/settings.json` with `"async": true`
- [ ] `event_logger.py` writes to `~/.claude/logs/workflow-events.jsonl` (moved from `temp/`)
- [ ] `otel_bridge.py` tails all JSONL files and exports OTel spans with correct W3C traceparent
- [ ] OTel bridge maps events to GenAI semantic convention span types
- [ ] OTel bridge tracks file offsets for crash recovery
- [ ] `tui_dashboard.py` launches and displays message viewer and state transition viewer
- [ ] TUI SQLite aggregation layer ingests JSONL files into searchable database
- [ ] TUI FTS5 keyword search returns relevant results across all event surfaces
- [ ] Multi-session log correlation: filtering by `workflow_id` isolates a single session's events
- [ ] Concurrent JSONL appends from multiple agents do not corrupt log files
- [ ] Zero token impact: no observability data injected into agent context via `additionalContext`
- [ ] JSONL file rotation extended to all log files (not just `workflow-events.jsonl`)
- [ ] Snapshot retention: only last 3 workflow runs preserved, older pruned at workflow start
