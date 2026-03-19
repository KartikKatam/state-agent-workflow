# Team Messaging Protocol

Replaces `agent-communication.md` (BLOCKED + inbox.json pattern). All communication uses `SendMessage` with structured JSON payloads defined in `team-message.schema.json`.

**Cardinal rule: never send file content in messages — only file paths.**

---

## Architecture

```
Lead (orchestrator)              Teammates
       │                              │
       ├──task_assign──────────────►  │  (spawn + assign work)
       │                              │
       │  ◄──────task_complete────────┤  (work done, files written)
       │                              │
       │  ◄──────info_request─────────┤  (need info, may keep working)
       ├──────────info_ready────────► │  (here's the file path)
       │                              │
       │  ◄──────status_update────────┤  (context pressure / progress)
       │                              │
```

---

## Message Types

### 1. task_assign (lead → teammate)

Replaces `Task()` spawn prompt.

```json
{
  "type": "task_assign",
  "from": "lead",
  "to": "codebase-explorer",
  "timestamp": "2026-02-05T10:00:00Z",
  "payload": {
    "task_type": "explore",
    "instructions": "Build codebase context for the batch-selection feature. Focus on src/producer/ and shared/models/.",
    "inputs": {
      "files_to_read": [".claude/context/_codebase.json"]
    },
    "output": {
      "write_to": ".claude/context/batch-selection-context.json"
    }
  }
}
```

### 2. task_complete (teammate → lead)

Replaces write-and-discard return. Teammate has already written files to disk.

```json
{
  "type": "task_complete",
  "from": "codebase-explorer",
  "to": "lead",
  "timestamp": "2026-02-05T10:03:00Z",
  "payload": {
    "status": "success",
    "output_files": [".claude/context/batch-selection-context.json"],
    "summary": "Mapped 12 files in src/producer/, identified BatchProcessor as primary entry point.",
    "context_pressure": false
  }
}
```

### 3. info_request (teammate → lead)

Replaces BLOCKED protocol. **Key difference**: teammate does NOT terminate.

- `priority: blocking` → teammate waits for `info_ready` before continuing
- `priority: normal` → teammate continues other work; reads info when `info_ready` arrives

```json
{
  "type": "info_request",
  "from": "chunk-coder-1",
  "to": "lead",
  "timestamp": "2026-02-05T10:15:00Z",
  "payload": {
    "request_type": "documentation",
    "query": "How does FastAPI dependency injection work with async database sessions?",
    "context": {
      "feature": "batch-selection",
      "chunk": "chunk-02",
      "why_needed": "Implementing DatabaseSession dependency for BatchProcessor"
    },
    "priority": "normal",
    "existing_checked": [
      ".claude/research/fastapi-async-di.json"
    ]
  }
}
```

### 3b. Rich info_request (teammate → lead)

For nuanced requests where the query alone isn't enough context, use the structured fields:

```json
{
  "type": "info_request",
  "from": "plan-architect",
  "to": "lead",
  "timestamp": "2026-02-05T10:30:00Z",
  "payload": {
    "request_type": "codebase",
    "query": "How does the pipeline orchestrate stage transitions?",
    "priority": "blocking",

    "what_we_need": "The exact mechanism by which pipeline stages hand off data — function signatures, data formats, and error propagation patterns.",
    "why_we_need_it": "Chunk-03 integrates batch selection as a new pipeline stage. We need to match the existing stage contract exactly.",
    "relevant_context": {
      "design_doc_section": "Section 3.2 — Pipeline Integration",
      "existing_knowledge": "We know BatchCandidate is the input type. We don't know what the pipeline expects as stage output.",
      "related_files": ["src/producer/pipeline.py", "src/producer/stages/"],
      "specific_questions": [
        "What does a stage's return type look like?",
        "How does error from one stage propagate to the next?",
        "Is there a stage registration mechanism?"
      ]
    },
    "existing_checked": [".claude/research/pipeline-overview.json"]
  }
}
```

**When to use rich vs simple info_request:**
- **Simple** (`query` only): "Does class X exist?", "What version of library Y?"
- **Rich** (structured fields): Integration patterns, API contracts, cross-module analysis — anything where the explorer/researcher needs nuance to return targeted results

The orchestrator translates rich info_request fields into correspondingly rich `task_assign` instructions, so the explorer/researcher (and their sub-agents) can target the exact information needed.

### 4. info_ready (lead → teammate)

Replaces inbox.json. Lead never reads the content — just routes the file path.

```json
{
  "type": "info_ready",
  "from": "lead",
  "to": "chunk-coder-1",
  "timestamp": "2026-02-05T10:16:00Z",
  "payload": {
    "file_paths": [".claude/research/fastapi-async-di-v2.json"],
    "summary": "FastAPI DI uses Depends() with async generators for session lifecycle.",
    "status": "fulfilled"
  }
}
```

### 5. status_update (teammate → lead)

Replaces context threshold auto-handoff and `handoff_writer.py`.

```json
{
  "type": "status_update",
  "from": "chunk-coder-1",
  "to": "lead",
  "timestamp": "2026-02-05T11:00:00Z",
  "payload": {
    "phase": "implementation",
    "progress": "4/7 tests passing, implementing batch validation",
    "context_pressure": true,
    "needs_replacement": true,
    "session_log": ".claude/logs/batch-selection-chunk-02-log.json",
    "resume_instructions": "Continue from Phase: implementation, step 5/7 — batch validation edge cases"
  }
}
```

---

## Flow Diagrams

### Info Request Flow (~110 tokens total messaging overhead)

```
chunk-coder-1                    lead                        researcher (helper)
     │                             │                              │
     ├─ info_request (~50 tok) ──►│                              │
     │  (continues working if      │                              │
     │   priority: normal)         ├── task_assign ────────────►  │
     │                             │                              ├─ writes file to disk
     │                             │                              │
     │                             │  ◄── task_complete (~30 tok)─┤
     │                             │                              │
     │                             ├── (shuts down helper)        │
     │                             │                              ✕
     │  ◄─ info_ready (~30 tok) ──┤
     │                             │
     ├─ reads file from disk       │
     └─ continues work             │
```

**Token efficiency**: Content only enters the requester's context when they read the file. The lead never sees content — just routes paths.

### Commit Flow (Approval-First)

```
teammate (e.g. explorer)         lead                        user              scribe
     │                             │                           │                  │ (idle)
     ├─ task_complete ────────────►│                           │                  │
     │                             ├── presents results ─────►│                  │
     ✕                             │                           │                  │
                                   │  ◄── "approved" ─────────┤                  │
                                   │                           │                  │
                                   ├── task_assign ──────────────────────────────►│
                                   │   (agent, feature,        │                  │
                                   │    files, log path)       │                  ├─ stages + commits
                                   │                           │                  │
                                   │  ◄───────────────── task_complete ──────────┤
                                   │   (commit hash)           │                  │ (goes idle)
```

User approval is the ONLY gate. The lead dispatches scribe only after user says "approved" / "looks good" / "ship it". The scribe commits directly — no second approval round-trip.

### Context Pressure / Replacement Flow

```
chunk-coder-1                    lead                        chunk-coder-2 (fresh)
     │                             │                              │
     ├─ (context at 70%+)         │                              │
     ├─ updates session log        │                              │
     ├─ status_update ───────────►│                              │
     │  (needs_replacement: true)  │                              │
     │                             ├── shuts down coder-1         │
     ✕                             │                              │
                                   ├── task_assign ────────────► │
                                   │   (with session_log path    │
                                   │    + resume_instructions)   │
                                   │                              ├─ reads session log
                                   │                              ├─ continues from step 5/7
                                   │                              │
```

---

## Token Efficiency Rules

1. **Paths not content**: Messages contain file paths, never file content. Content enters only the context of the agent that reads the file.
2. **No broadcast**: All messages are targeted (from → to). No fan-out.
3. **Summaries ≤ 200 chars**: The `summary` field in `task_complete` and `info_ready` exists for the lead to make routing decisions — not for full context transfer.
4. **Lead stays thin**: The lead coordinates. It does not read content packets, research files, or source code. This keeps lead context small and long-lived.
5. **Helper lifecycle**: Helpers spawned for `info_request` are shut down after `task_complete`. They don't persist.

---

## Messaging Discipline

These rules prevent token waste, duplicate messages, and slow round-trips. **All agents MUST follow them.**

### Lead Communication Limits (HARD RULES)

These rules apply to the orchestrator / team lead specifically. They exist because repeated violations caused user frustration.

1. **One message per topic.** The lead MUST NOT send multiple messages about the same question or request to a teammate. If a teammate does not respond, the lead MUST either wait, check output files silently, or inform the user — never re-ask.
2. **No idle-triggered messages.** The lead MUST NOT send messages in response to idle notifications. Idle notifications are automatic turn boundaries (every 10-20 seconds) and are NOT evidence of a problem. The lead MUST wait at least 3 minutes after dispatching work before checking on a teammate, and MUST check output files (Glob/Read) silently before sending any message.
3. **No shutdown without user approval.** The lead MUST NOT send `shutdown_request` to any teammate without first asking the user and receiving explicit confirmation. This applies even to teammates whose work is complete.

### Write First, Message Second

1. Finish ALL work for the current event (write files, run commands, update logs).
2. THEN send exactly ONE SendMessage with:
   - The correct message type (task_complete, info_request, etc.)
   - File paths to everything you wrote
   - A summary ≤ 200 chars
3. NEVER send content in the message body. The user reads your output files directly.

### One Message Per Event

- **ONE SendMessage per workflow event.** Don't send `status_update` AND `task_complete` for the same event — pick the right type.
- If work is done → `task_complete`. If you need info → `info_request`. If context is critical → `status_update`. Never combine.
- Don't send a plain-text explanation followed by a JSON payload. The structured JSON payload IS the message.

### Large Outputs Go to Disk

- Any output longer than ~50 lines MUST be written to a file first.
- The message contains only the file path and a short summary.
- This applies to: test specs, plans, code review notes, exploration results, research findings, session logs — everything.
- Short summaries and status updates (≤ 200 chars) go directly in the message `summary` field.

**Where to write** (route by content type):

| Content Type | Location |
|-------------|----------|
| Codebase context | `.claude/context/_codebase.json` |
| Feature context | `.claude/context/{feature}-context.json` |
| Query results | `.claude/context/queries/{topic}.json` |
| Research results | `.claude/research/{topic}.json` |
| Design docs | `.claude/designs/{feature}.md` |
| Plans (JSON) | `.claude/plans/{feature}-plan.json` |
| Plans (plain-text) | `.claude/plans/plan-plaintext/{feature}-plan-text.md` |
| Test architecture specs | `.claude/plans/{feature}-test-specs.md` |
| Session logs | `.claude/logs/{feature}-{chunk}-log.json` |
| Orchestrator state | `.claude/temp/orchestrator-state.json` |
| Other workflow artifacts | `.claude/temp/{descriptive-name}.{ext}` |

---

## Migration from Old Protocol

| Old Pattern | New Pattern |
|-------------|-------------|
| `BLOCKED: documentation` return | `info_request` message (teammate keeps running) |
| Orchestrator writes `inbox.json` | Lead sends `info_ready` message |
| Agent reads `inbox.json` on resume | Agent reads file at path in `info_ready` message |
| Scribe returns `{ git_commands: [...] }` | Lead sends `task_assign` to scribe after user approval, scribe commits directly |
| `handoff_writer.py` creates handoff packet | Teammate sends `status_update`, lead spawns replacement |
| `Task()` spawn with return | `task_assign` message to teammate |
| Background agent returns `{ file_path, summary }` | Teammate sends `task_complete` after writing files |
