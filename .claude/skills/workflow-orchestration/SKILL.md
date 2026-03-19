# Workflow Orchestration

> **Purpose**: Provides the orchestrator with workflow phase management, parallel coordination, prerequisite checks, auto re-exploration, persistent scribe lifecycle, and info-request translation.
> **Consumers**: orchestrator
> **Schemas**: None (references existing schemas via other skills)
> **Depends on**: `session-lifecycle` (for context pressure and handoff protocols)

## What You Learn From This Skill
- Workflow phases: explore → plan → implement → commit with prerequisites per phase
- Parallel coordination: when and how to run multiple teammates simultaneously
- Prerequisite checklist: what files must exist before each phase
- Auto re-exploration protocol after each chunk commit
- Persistent scribe lifecycle management
- Info-request translation: turning rich info_requests into targeted task_assign instructions
- Orchestrator state save/resume across sessions

## Contract
- Check prerequisites BEFORE every teammate spawn (see prerequisite checklist)
- Track which phases are complete via session logs and plan status
- After each chunk commit, trigger incremental re-exploration BEFORE spawning next chunk-coder
- Spawn scribe ONCE at workflow start; reuse for all commits; replace only on context pressure
- Translate rich info_request fields into correspondingly rich task_assign instructions for explorer/researcher
- Save orchestrator state to disk when context pressure hits or session ends

---

## Prerequisite Checks (CRITICAL)

Before spawning ANY teammate, verify prerequisites exist:

### Before Exploration
```
Required: Design document (if feature exploration)
Check: Does .claude/designs/{feature}.md exist?
  |-- Yes -> Proceed with exploration
  +-- No  -> Ask user for design doc path or create one
```

### Before Planning
```
Required:
  1. Codebase context: .claude/context/_codebase.json
  2. Feature context: .claude/context/{feature}-context.json
  3. Design document: .claude/designs/{feature}.md

Check each:
  |-- All exist -> Proceed with planning
  +-- Missing   -> Spawn explorer first, then plan
```

### Before Implementation
```
Required:
  1. Plan exists: .claude/plans/{feature}-plan.json
  2. Plan status: "approved"
  3. Previous chunk (if not chunk-01): status "completed"
     (unless chunks are independent — see parallel coders below)

Check:
  |-- Plan missing          -> Tell user to create plan first
  |-- Plan not approved     -> Tell user to approve plan first
  |-- Previous chunk incomplete -> Complete it first or ask user to skip
  +-- All good              -> Proceed with implementation
```

### Before Dispatching Scribe (Any Phase)
```
Required:
  1. User approval (explicit "approved", "looks good", etc.)
  2. Files were actually written by the completing teammate
  3. For implementation: session log exists at .claude/logs/{feature}-{chunk}-log.json

Check:
  |-- No approval -> Wait for user approval
  |-- No files written (intermediate step) -> Do NOT dispatch scribe
  +-- Approved + files written -> Send task_assign to persistent scribe via SendMessage
```

**What triggers scribe dispatch:**
- Explorer writes context packets → user approves → scribe commits
- Planner writes plan files → user approves → scribe commits
- Chunk-coder writes code + tests → user approves → scribe commits

**What does NOT trigger scribe dispatch:**
- info_request/info_ready cycles (no user-facing output)
- User asking questions to agents
- Partial progress updates
- Test architecture review rounds (until final approval)

---

## Model Assignments

Models are fixed per agent type. No runtime complexity scoring needed.

| Agent | Model | Thinking | Rationale |
|-------|-------|----------|-----------|
| orchestrator | Sonnet | Enabled | Coordination decisions, not deep reasoning |
| plan-architect | **Opus 4.6** | Enabled | Chunk boundaries, dependency graphs, test strategy require deep reasoning |
| chunk-coder | **Opus 4.6** | Enabled | Implementation, edge cases, cross-file consistency require deep reasoning |
| codebase-explorer | Sonnet | Disabled | File reading and structure mapping — straightforward |
| researcher | Sonnet | Disabled | Doc lookup and synthesis — well-defined tasks |
| scribe | Haiku | Disabled | Mechanical commit tasks |

**Planner and coder are always Opus 4.6. No ask required.** Both have sub-agent access to Haiku, Sonnet, and Opus for delegation.

**Explorer and researcher are Sonnet by default.** In rare cases where the orchestrator identifies a need for Opus-level analysis (e.g., failure root-cause analysis in explorer), it MAY ask the user before escalating. This is uncommon.

---

## Workflow Phases

### Standard Workflow

#### 1. Feature Request

User: "Add batch selection to the LPR module"

**Check prerequisites:**
```bash
ls .claude/context/_codebase.json 2>/dev/null && echo "CODEBASE_EXISTS"
ls .claude/context/{feature}-context.json 2>/dev/null && echo "FEATURE_EXISTS"
ls .claude/designs/{feature}.md 2>/dev/null && echo "DESIGN_EXISTS"
```

**Respond based on state:**
- No codebase context -> "I'll explore the codebase first"
- No design doc -> "Do you have a design document? I can help create one"
- No feature context -> "I'll explore for this specific feature"
- All exist -> "Context ready. Want to create an implementation plan?"

#### 2. Exploration

Spawn ONE explorer for the codebase scope. If external docs are also needed, spawn ONE researcher in parallel. Explorer and researcher are ephemeral — spawned fresh per task, shut down after task_complete.

```python
# Explorer handles codebase (internally parallelizes with sub-agents)
SendMessage(to="codebase-explorer", message={
  "type": "task_assign",
  "payload": {
    "task_type": "explore",
    "instructions": "Explore {scope} for {feature}. Design doc: {path}",
    "output": { "write_to": ".claude/context/{feature}-context.json" }
  }
})

# Researcher handles external docs (if needed, in parallel)
SendMessage(to="researcher", message={
  "type": "task_assign",
  "payload": {
    "task_type": "research",
    "instructions": "Research: {topic}. Context: {why needed}.",
    "output": { "write_to": ".claude/research/{topic-slug}.json" }
  }
})
```

After all task_complete messages received: "Exploration complete. Here's what was found: {brief summary}."

**On user approval** ("looks good", "approved"): Send task_assign to persistent scribe to commit context files. Scribe commits directly — no approval round-trip.

#### 3. Planning (Interactive)

Spawn plan-architect (Opus 4.6). User talks to plan-architect directly.

```python
SendMessage(to="plan-architect", message={
  "type": "task_assign",
  "payload": {
    "task_type": "plan",
    "instructions": "Create implementation plan for {feature}.",
    "inputs": {
      "files_to_read": [
        ".claude/designs/{feature}.md",
        ".claude/context/_codebase.json",
        ".claude/context/{feature}-context.json"
      ]
    },
    "output": { "write_to": ".claude/plans/{feature}-plan.json" }
  }
})
```

When task_complete received: "Plan ready with {N} chunks. Review it and let me know."

**On user approval** ("approved"): Send task_assign to persistent scribe to commit plan files. Scribe commits directly.

#### 4. Implementation (Interactive)

Spawn chunk-coder (Opus 4.6). User talks to chunk-coder directly.

For each chunk:
1. **Check for existing session** — If session log exists with status "in_progress", offer resume
2. **Spawn chunk-coder** with plan, context, and session log paths
3. User works with chunk-coder through TDD cycle
4. On approval → send to persistent scribe

#### 5. Approval and Commit

When user says "approved" after reviewing chunk implementation:
- Send task_assign to the **persistent scribe** (not a new spawn — use SendMessage to the existing idle scribe)
- Scribe commits directly, sends task_complete
- "Chunk committed. Ready for next chunk?"

**Key rule**: The scribe receives task_assign for ALL phase completions that produce files — exploration, planning, AND implementation. The pattern is always: teammate writes files → lead presents to user → user approves → lead dispatches scribe. Intermediate steps (info_request/info_ready, partial progress, user questions to agents) do NOT trigger scribe commits.

#### 6. Auto Re-Exploration (Between Chunks)

See dedicated section below.

#### 7. Repeat

Continue with remaining chunks until feature complete.

---

## Parallel Teammates

### Explorer and Researcher: One Per Domain

Explorer and researcher each write to shared output files. Only one of each runs at a time to avoid write conflicts. They are spawned fresh per task and shut down after completion.

### Planners and Coders: Multiple Allowed

The user may want to work on multiple features or chunks simultaneously. The orchestrator supports:

**Parallel planners** — Plan multiple features at the same time:
```
plan-architect-1: Planning feature-A → .claude/plans/feature-a-plan.json
plan-architect-2: Planning feature-B → .claude/plans/feature-b-plan.json
```
No conflict — each writes to a different plan file.

**Parallel coders** — Implement independent chunks simultaneously:
```
chunk-coder-1: Implementing chunk-01 → .claude/logs/{feature}-chunk-01-log.json
chunk-coder-2: Implementing chunk-03 → .claude/logs/{feature}-chunk-03-log.json
```

**Parallel coder rules:**
1. Only chunks WITHOUT dependency edges between them can run in parallel
2. Each coder gets a distinct session log path
3. Check the plan's dependency graph:
   ```
   chunk-01 → chunk-02 → chunk-04
                       ↗
   chunk-03 ──────────
   ```
   In this graph: chunk-01 and chunk-03 CAN run in parallel. chunk-02 must wait for chunk-01. chunk-04 must wait for chunk-02 and chunk-03.
4. After parallel chunks complete, run auto re-exploration ONCE before the next wave

**Presenting parallel options to user:**
```
Chunks ready for implementation:
  - chunk-01 (no dependencies)
  - chunk-03 (no dependencies)

chunk-02 depends on chunk-01, chunk-04 depends on chunk-02 + chunk-03.

Options:
  [sequential] - Implement one at a time (chunk-01 first)
  [parallel]   - Implement chunk-01 and chunk-03 simultaneously

Your choice:
```

---

## Auto Re-Exploration After Chunk Completion

After each chunk-coder completes and the scribe commits, the orchestrator MUST trigger an incremental re-exploration of the feature directory before spawning the next chunk-coder.

### Why

Chunk-01 may have created new files, new types, new config keys, new function signatures, or new API surface that chunk-02 depends on. If chunk-02 loads stale context packets, it may duplicate work, use wrong names, or miss integration points.

### Protocol

```
After chunk-N approved + committed:
  1. Orchestrator sends task_assign to a fresh codebase-explorer:
     - task_type: "explore"
     - mode: "incremental"  (update existing, don't rebuild from scratch)
     - instructions: "Re-explore {feature directory} after chunk-{N} implementation.
       Focus on: new files created, new types/classes added, new function signatures,
       new config values, changed API surface, new test fixtures.
       Preserve manual_notes from existing context."
     - inputs: { files_to_read: [".claude/context/{feature}-context.json",
                                  ".claude/logs/{feature}-chunk-{N}-log.json"] }
     - output: { write_to: ".claude/context/{feature}-context.json" }
  2. Explorer reads the session log's files_modified to know exactly what changed
  3. Explorer updates the feature context packet with:
     - New entries in structure.blocks for new files
     - New entries in types.shared for new types/classes
     - Updated function signatures in touchpoints
     - New config values
     - Updated name_bank (names now in use, names still available)
  4. Explorer sends task_complete
  5. Orchestrator proceeds to spawn chunk-{N+1} coder
```

### Key Rules

- The re-exploration is **lightweight** — reads only files that changed (from session log) plus their immediate dependents. Does NOT re-read the entire codebase. Fast (~30 seconds) vs full exploration (~2-3 minutes).
- Explorer is spawned fresh each time — no benefit to persistence since output is on disk.
- After parallel chunks complete, run ONE re-exploration pass covering all changed files.

### Skip Condition

If the next chunk touches entirely different files (no overlap in `scope.touched_files` between chunk-N and chunk-N+1), the orchestrator MAY skip re-exploration. Use Sequential Thinking to evaluate overlap.

---

## Persistent Scribe Lifecycle

Instead of spawning a new scribe for each commit, the orchestrator spawns ONE scribe at workflow start and keeps it idle between commits.

### Protocol

```
Workflow start (immediately after TeamCreate):
  1. Spawn scribe teammate (Haiku, bypassPermissions, into team)
  2. Scribe reads its skills and goes idle — no task_assign yet

After ANY phase approved by user (exploration, planning, or chunk implementation):
  1. Send task_assign via SendMessage to existing idle scribe (NOT spawn new scribe)
     Include: agent type that completed, feature name, files to commit, log path
  2. Scribe stages files, commits directly (no approval round-trip), sends task_complete
  3. Scribe goes idle again — remains alive for the next commit

Session end or scribe context pressure:
  1. Scribe writes session-wide summary (optional)
  2. Send shutdown_request to scribe
  3. If context pressure: shut down, spawn replacement scribe with commit history summary
```

### Benefits

- Richer commit messages that reference previous chunks' changes
- Cross-chunk learning signal correlation
- On-demand progress summaries ("scribe, what have we done so far?")
- Session-wide narrative in final summary

---

## Message Dispatch and Request Tracking

The orchestrator is the central message router. When multiple teammates run in parallel, the orchestrator may receive multiple messages in the same turn. It MUST handle all of them without losing track.

### How Messages Arrive

Claude Code's Agent Teams delivers teammate messages to the orchestrator automatically. When the orchestrator's turn comes, it may see:
- One message (common: sequential workflow)
- Multiple messages (parallel coders both finishing, or both sending info_requests)

The `from` field on each message identifies the sender. The orchestrator MUST use this to route responses back correctly.

### Dispatch Tracking

When multiple info_requests arrive, the orchestrator tracks pending dispatches:

```
Incoming (same turn):
  1. info_request from chunk-coder-1: "pipeline stage contract" (codebase question)
  2. info_request from chunk-coder-2: "SAM2 batch API" (docs question)

Orchestrator actions:
  1. Spawn explorer-A for request 1 → output: .claude/context/queries/pipeline-stages.json
  2. Spawn researcher-B for request 2 → output: .claude/research/sam2-batch-api.json
  3. Track: { explorer-A → chunk-coder-1, researcher-B → chunk-coder-2 }

As responses arrive:
  explorer-A sends task_complete → orchestrator sends info_ready to chunk-coder-1
  researcher-B sends task_complete → orchestrator sends info_ready to chunk-coder-2
  Shut down explorer-A and researcher-B after their respective task_complete
```

### Key Rules

1. **One agent per dispatch topic** — each info_request spawns its own fresh explorer or researcher. The agent itself decides how to parallelize internally (sub-agents, etc.)
2. **Multiple dispatch agents CAN run simultaneously** — if two coders need info at the same time, two helper agents run in parallel
3. **Route responses to the correct requester** — use the `from` field from the original info_request to address the `info_ready` response
4. **Priority handling** — `blocking` requests get dispatched immediately. `normal` requests can be batched if the orchestrator is busy.
5. **Don't block on one request** — if explorer-A is slow but researcher-B is done, send researcher-B's response immediately. Don't wait for both.

### Example: Parallel Coders with Concurrent Info Requests

```
State: chunk-coder-1 and chunk-coder-2 both running

Turn 1: chunk-coder-1 sends info_request (blocking: pipeline contract)
  → Spawn explorer-A
  → chunk-coder-1 waits

Turn 2: chunk-coder-2 sends info_request (normal: SAM2 docs)
  → Spawn researcher-B
  → chunk-coder-2 continues working

Turn 3: explorer-A sends task_complete
  → Send info_ready to chunk-coder-1 (with file path)
  → Shut down explorer-A
  → chunk-coder-1 resumes

Turn 4: researcher-B sends task_complete
  → Send info_ready to chunk-coder-2 (with file path)
  → Shut down researcher-B
```

### Translating Rich Info Requests

When an info_request contains structured fields (`what_we_need`, `why_we_need_it`, `relevant_context`), translate them into correspondingly rich task_assign instructions for the dispatched agent:

```
info_request from chunk-coder-1:
  query: "How does the pipeline orchestrate stage transitions?"
  what_we_need: "Function signatures, data formats, error propagation"
  relevant_context:
    related_files: ["src/producer/pipeline.py", "src/producer/stages/"]
    specific_questions: ["What does a stage return?", "How do errors propagate?"]

→ Translated task_assign to explorer-A:
  instructions: "Answer specific questions about pipeline stage transitions.
    Start from: src/producer/pipeline.py, src/producer/stages/
    Questions to answer:
    1. What does a stage's return type look like?
    2. How does error from one stage propagate to the next?
    Context: Chunk-03 integrates batch selection as a new pipeline stage.
    Write findings to .claude/context/queries/pipeline-stages.json"
```

Simple info_requests (just a `query` field) get simple task_assign instructions.

---

## Orchestrator State Save and Resume

The orchestrator IS the main session. When context pressure hits or the user ends a session, the orchestrator saves its state to disk so a new session can resume.

### Save State

```python
Write(".claude/temp/orchestrator-state.json", {
  "feature": "{feature}",
  "workflow_status": "in_progress",
  "completed_phases": ["exploration", "planning", "chunk-01", "chunk-02"],
  "current_phase": "implementation",
  "current_chunk": "chunk-03",
  "plan_path": ".claude/plans/{feature}-plan.json",
  "context_paths": [
    ".claude/context/_codebase.json",
    ".claude/context/{feature}-context.json"
  ],
  "scribe_commit_history": [
    { "chunk": "chunk-01", "hash": "abc123" },
    { "chunk": "chunk-02", "hash": "def456" }
  ],
  "active_teammates": [],
  "saved_at": "{ISO timestamp}",
  "resume_instructions": "Resume from chunk-03 implementation. Chunks 01-02 committed."
})
```

### Resume

When user says "resume workflow {feature}" or "continue session":

1. Read `.claude/temp/orchestrator-state.json`
2. Read the plan file to understand remaining chunks
3. Check session logs for any in-progress chunks
4. Present resume summary:
   ```
   Resuming workflow: {feature}

   Completed:
   - Exploration (context at .claude/context/{feature}-context.json)
   - Planning (4 chunks, plan at .claude/plans/{feature}-plan.json)
   - chunk-01 committed (abc123)
   - chunk-02 committed (def456)

   Next: chunk-03 — {chunk.name}

   Options:
   [continue] - Start chunk-03
   [status]   - Show detailed progress
   [skip]     - Jump to a different chunk
   ```
5. Spawn fresh persistent scribe for this session
6. Continue from the next pending chunk

---

## Session Log Awareness

### On Startup: Check for Active Sessions

```bash
find .claude/logs -name "*-log.json" -exec grep -l '"status": "in_progress"' {} \;
```

If found, offer to resume:
```
I found an in-progress session:
  Feature: {feature}
  Chunk: {chunk}
  Last state: {resume_point}

Options:
  [resume]  - Continue from where we left off
  [restart] - Start this chunk fresh
  [skip]    - Move to a different task

Your choice:
```

### Loading Session Context

When resuming or starting a chunk, load relevant context:

```bash
cat .claude/logs/{feature}-{chunk}-log.json 2>/dev/null
cat .claude/plans/{feature}-plan.json
cat .claude/context/_codebase.json
cat .claude/context/{feature}-context.json
```

Pass relevant summary to spawned teammate in the task_assign message.

---

## Decision Tree

```
User request
    |
    |-- "start workflow" / "@orchestrator"
    |   +-- Check for orchestrator-state.json -> offer resume or new
    |   +-- Check for in-progress sessions -> offer resume or new
    |
    |-- "explore" / "analyze codebase"
    |   |-- Check: codebase context exists?
    |   |   +-- Present exploration options -> spawn explorer (fresh)
    |   |-- External docs needed? -> spawn researcher in parallel (fresh)
    |   +-- Return context file paths
    |
    |-- "research" / "look up docs for" / "how does X work"
    |   +-- Spawn researcher (fresh) -> shut down after task_complete
    |
    |-- "plan" / "break down" / "chunk [feature]"
    |   |-- Check: design doc exists?
    |   |   +-- No -> Ask for design doc
    |   |-- Check: context exists?
    |   |   +-- No -> Spawn explorer first
    |   |-- Unfamiliar libraries? -> Spawn researcher in parallel
    |   +-- Spawn plan-architect (Opus 4.6)
    |
    |-- "implement" / "code" / "start chunk-XX"
    |   |-- Check: plan exists and approved?
    |   |   +-- No -> Tell user to create/approve plan
    |   |-- Check: dependencies satisfied?
    |   |   +-- No -> Complete dependencies first or offer parallel if independent
    |   +-- Spawn chunk-coder (Opus 4.6)
    |
    |-- "implement chunk-01 and chunk-03" (parallel request)
    |   |-- Check: no dependency edge between them?
    |   |   +-- Yes -> Spawn two chunk-coders simultaneously
    |   |   +-- No  -> "chunk-03 depends on chunk-01. Must be sequential."
    |
    |-- "approved" / "looks good" / "commit it"
    |   |-- Which phase just completed?
    |   |   |-- Exploration -> Send task_assign to scribe (context files)
    |   |   |-- Planning    -> Send task_assign to scribe (plan files)
    |   |   +-- Implementation -> Send task_assign to scribe (code + tests)
    |   +-- After scribe task_complete -> offer next step
    |
    |-- "continue" / "next chunk"
    |   +-- Auto re-explore -> spawn chunk-coder for next chunk
    |
    |-- "resume" / "continue session"
    |   +-- Load orchestrator-state.json -> resume from saved state
    |
    +-- Other requests
        +-- Handle directly or clarify
```
