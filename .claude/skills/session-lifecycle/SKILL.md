# Session Lifecycle

> **Purpose**: Manages session loading, resumption, context pressure save-state, handoff, and continuation across all agents.
> **Consumers**: orchestrator, plan-architect, chunk-coder, codebase-explorer, researcher, scribe
> **Schemas**: `schemas/session-log.schema.json`
> **Depends on**: None (foundational skill)

## What You Learn From This Skill
- How to load and parse existing session logs on startup
- How to offer resume vs restart when an in-progress session exists
- How to save state when context pressure hits (populate all handoff fields)
- How to send `status_update` with `needs_replacement: true`
- What a continuation (replacement) agent does on startup
- How sub-agent delegation works across all agent types
- Chunk continuity: how decisions and preferences carry forward between chunks

## Contract
- On startup: check for existing session log, parse status, offer resume
- Before context-pressure stop: populate ALL handoff fields in session log
- After context-pressure stop: send status_update to lead with session_log path
- When implementing chunk-N (N > 1): read previous chunk's session log for decisions_made and user_preferences

---

## Session Log Loading

### On Startup: Check for Existing Session

```bash
# Check if session log exists
SESSION_LOG=".claude/logs/{feature}-{chunk}-log.json"
if [ -f "$SESSION_LOG" ]; then
    cat "$SESSION_LOG"
fi
```

### If Session Exists

Parse the session log and check `status`:

| Status | Action |
|--------|--------|
| `in_progress` | Resume from `resume_point` |
| `tests_written` | Skip to implementation |
| `implementation_done` | Skip to invariant verification |
| `verified` | Skip to quality gate |
| `approved` | Already done, tell orchestrator |
| `blocked` | Show blocker, ask user how to proceed |
| `failed` | Show failure, ask user how to proceed |

### Resume Flow

```
I found an existing session for {chunk.name}:

**Status**: {status}
**Last phase**: {phase}
**Resume point**: {resume_point}

Files modified so far:
{list files_modified}

Tests written:
{list tests with status}

Options:
  [continue] - Resume from where we left off
  [restart] - Start fresh (keeps existing tests)
  [reset] - Start completely fresh (deletes all changes)

Your choice:
```

If `[continue]`:
- Load all context from session log
- Skip completed phases
- Pick up at `resume_point`

### New Session Initialization

If no session log exists, create one. **Use the Claude Code session ID** provided at session start (injected via `additionalContext`) as `meta.session_id`. This ID is used by the completion validation hook to match your session log.

```bash
mkdir -p .claude/logs

cat > .claude/logs/{feature}-{chunk}-log.json << 'EOF'
{
  "meta": {
    "feature": "{feature}",
    "chunk": "{chunk}",
    "started_at": "{ISO timestamp}",
    "last_updated": "{ISO timestamp}",
    "session_id": "{your Claude Code session ID from additionalContext}",
    "session_label": "{feature}-{chunk}-{date}-{time}",
    "agent_model": "{sonnet|opus}"
  },
  "status": "in_progress",
  "phase": "context_loading",
  "resume_point": "Starting context loading",
  "files_modified": [],
  "tests_written": [],
  "invariants_status": {},
  "quality_gate": {
    "format": "not_run",
    "lint": "not_run",
    "typecheck": "not_run",
    "tests": "not_run"
  },
  "learning_signals": [],
  "deviations": [],
  "user_interactions": [],
  "decisions_made": [],
  "user_preferences": [],
  "context_usage": {},
  "timing": {
    "iteration_count": 0
  }
}
EOF
```

### Update Session Log at Each Phase

After each phase transition, update the log:

```bash
python3 << 'EOF'
import json
from datetime import datetime

with open('.claude/logs/{feature}-{chunk}-log.json', 'r') as f:
    log = json.load(f)

log['phase'] = '{new_phase}'
log['status'] = '{new_status}'
log['resume_point'] = '{description of current state}'
log['meta']['last_updated'] = datetime.utcnow().isoformat() + 'Z'

with open('.claude/logs/{feature}-{chunk}-log.json', 'w') as f:
    json.dump(log, f, indent=2)
EOF
```

---

## Context Pressure Protocol

All agents must monitor their context window and act proactively when approaching limits.

### Thresholds by Agent Mode

| Agent Mode | Warning | Critical | Action at Critical |
|------------|---------|----------|-------------------|
| Foreground (plan-architect, chunk-coder) | 50% | 70% | Prompt user for decision |
| Background (explorer, researcher, scribe) | — | 70% | Finish in-flight work, then auto-handoff |
| Team Lead (orchestrator) | 50% | 70% | Offer state save |

### Foreground Agent Protocol (plan-architect, chunk-coder)

**At 50% Context:**
- Inform user of context usage
- Offer to save partial work and continue with fresh agent
- Continue if user says so

**At 70% Context:**
- Strongly recommend saving state
- Present options:
  ```
  Context at 70%.

  Options:
  [A] Save state + continue with fresh teammate (Recommended)
  [B] Keep working (may hit limits)
  ```

**If user selects [A] or auto-triggered:**
1. Finish current safe checkpoint (complete the current test or function, not mid-edit)
2. Update session log with current state
3. Populate ALL handoff fields (see below)
4. Send status_update to lead:
   ```python
   SendMessage(to="lead", message={
     "type": "status_update",
     "payload": {
       "context_pressure": true,
       "needs_replacement": true,
       "session_log": ".claude/logs/{feature}-{chunk}-log.json",
       "phase": "{current_phase}",
       "completed": ["{list completed work}"],
       "pending": ["{list remaining work}"],
       "resume_instructions": "Continue {chunk} from {specific step}."
     }
   })
   ```
5. Stop and wait for lead to shut you down and spawn a replacement

### Background Agent Protocol (explorer, researcher, scribe)

**At 70% Context — FINISH IN-FLIGHT WORK, THEN HANDOFF:**

Background agents do NOT abandon work mid-task. At 70%, they enter **drain mode**: complete all currently in-flight work, then stop cleanly.

```
Context hits 70%:
  │
  ├── Sub-agents currently running?
  │   ├── Yes → Wait for ALL sub-agents to return results
  │   │         Aggregate their findings into the output file
  │   │         Then proceed to handoff
  │   └── No  → Proceed to handoff immediately
  │
  ├── Currently mid-exploration of a file/module (no sub-agents)?
  │   ├── Yes → Finish the current file/module analysis
  │   │         Write partial results to output file
  │   │         Then proceed to handoff
  │   └── No  → Proceed to handoff immediately
  │
  └── HANDOFF:
      1. Write all gathered results to output file(s)
      2. Do NOT start any NEW exploration, research queries, or sub-agents
      3. Populate handoff fields (see below)
      4. Send status_update to lead with needs_replacement: true
```

**Why finish in-flight work**: Sub-agents have already consumed tokens and API calls. Abandoning them wastes that work and forces the replacement agent to redo it. Waiting for them to complete (usually seconds) and writing their results means the replacement agent picks up where you left off with no duplicated effort.

**What "finish" means per agent type**:

| Agent | What to finish | What NOT to start |
|-------|---------------|-------------------|
| Explorer | Wait for running sub-agents; write their results into context packet | No new directory scans, no new sub-agent spawns |
| Researcher | Wait for running Context7/WebSearch queries; write findings to research file | No new library lookups, no new sub-agent spawns |
| Scribe | Finish current file write; complete current `git add` | No new memory extraction, no new file reads |

**Handoff steps (after draining)**:
1. Save state to session log or temp file
2. Populate handoff fields:
   - `resume_from_phase`
   - `resume_from_step`
   - `key_files_read`
   - `decisions_made`
   - `user_preferences`
   - `pending_decisions`
3. Send status_update to lead:
   ```python
   SendMessage(to="lead", message={
     "type": "status_update",
     "payload": {
       "context_pressure": true,
       "needs_replacement": true,
       "session_log": ".claude/temp/{agent}-session.json",
       "phase": "{current_phase}",
       "completed": ["{sections analyzed}"],
       "pending": ["{sections remaining}"],
       "resume_instructions": "Continue from {specific next section}. Completed sections already written to {output_file}."
     }
   })
   ```
4. Stop — do NOT start new work after sending status_update

Lead will:
1. Shut down exhausted agent
2. Spawn fresh agent of same type
3. Send task_assign with session_log path and resume_instructions

### Team Lead Protocol (orchestrator)

**At 50% Context:**
```
Orchestrator at 50% context.
Work summary:
  - Exploration: {status}
  - Planning: {status}
  - Implementation: {status}
```

**At 70% Context:**
```
Orchestrator context at 70%.

Current state:
  Feature: {feature}
  Progress: {details}

Options:
[A] Save state to .claude/temp/orchestrator-state.json, resume later
[B] Continue (may hit limits)

State can be resumed with: "resume workflow {feature}"
```

---

## Required Handoff Fields

Before any context-pressure stop, populate ALL of these in the session log:

```json
{
  "resume_from_phase": "implementation",
  "resume_from_step": "Implementing select_batch — 2 of 4 functions done",
  "key_files_read": [
    ".claude/plans/{feature}-plan.json",
    ".claude/context/{feature}-context.json"
  ],
  "decisions_made": [
    {
      "decision": "use composition for BatchProcessor",
      "reason": "user preference + matches existing pattern"
    }
  ],
  "user_preferences": [
    {
      "preference": "early returns over nested conditionals",
      "context": "user corrected nested if/else in select_batch"
    }
  ],
  "pending_decisions": [
    "diversity scoring algorithm — user was considering two approaches"
  ]
}
```

---

## Continuation Mode

When spawned as a replacement/continuation agent:

1. Read the session log at the path provided by lead
2. Parse `resume_from_phase` and `resume_from_step`
3. Load `key_files_read` — these are the critical context files
4. Apply `decisions_made` — these are LOCKED unless user overrides
5. Apply `user_preferences` — follow these patterns
6. Address `pending_decisions` — present to user if still unresolved
7. Acknowledge to user what's already done:
   ```
   Resuming {chunk.name} from {resume_from_step}.

   Previous session completed: {list completed work}
   Carrying forward:
   - {decision_1}
   - {preference_1}

   Continuing with: {next step}
   ```
8. Don't re-do completed work

---

## Chunk Continuity Protocol

When starting chunk-N where N > 1, the coder MUST read the previous chunk's session log to maintain consistency across the feature implementation.

### Fields That Carry Forward

```
decisions_made[]:
  Architectural and implementation decisions locked during a chunk.
  Example: { "decision": "use composition for BatchProcessor",
             "reason": "user preference + matches existing pattern" }

user_preferences[]:
  Coding style and approach preferences expressed by the user.
  Example: { "preference": "early returns over nested conditionals",
             "context": "user corrected nested if/else in select_batch" }

user_interactions[]:
  All user feedback applied during implementation.
  Relevant items carry forward to subsequent chunks.
```

### Continuity Flow

```
When starting chunk-N (N > 1):
  1. Read chunk-{N-1} session log:
     .claude/logs/{feature}-chunk-{N-1}-log.json

  2. Extract decisions_made → these are LOCKED unless user overrides

  3. Extract user_preferences → follow these patterns

  4. Acknowledge to user:
     "Carrying forward from chunk-{N-1}:
       - {decision_1}
       - {preference_1}
     I'll follow these unless you tell me otherwise."

  5. If user overrides a previous decision, document it:
     {
       "decision": "switched from composition to inheritance for BatchProcessor",
       "reason": "user override — inheritance better for new requirement",
       "overrides": "chunk-01 decision"
     }
```

---

## Sub-Agent Delegation (General Protocol)

All agents with the Task tool can spawn sub-agents for independent, well-scoped subtasks. The main agent does the core reasoning; sub-agents handle mechanical work.

### General Rules

1. **Sub-agents are cheap** — use Haiku for mechanical tasks, Sonnet for tasks needing synthesis
2. **You aggregate** — sub-agents return data to you, you synthesize the final output
3. **Section ownership** — each sub-agent gets a disjoint scope to prevent conflicts
4. **Don't over-subdivide** — 2-4 sub-agents is typical; more adds coordination overhead
5. **Sub-agents don't have conversation context** — never delegate tasks that require knowledge of user interactions

### When to Delegate (Any Agent)

| Subtask Type | Model | Example |
|-------------|-------|---------|
| Broad file search / catalog | Haiku | "List all dataclass names in producer/" |
| Name conflict check | Haiku | "Is BatchResult used anywhere?" |
| Simple validation | Haiku | "Verify import paths work" |
| Focused research | Sonnet | "Summarize SAM2 batch API" |

### When NOT to Delegate (Any Agent)

- Core reasoning (chunk boundaries, invariant design, algorithm implementation)
- Debugging failures (requires full context of what you wrote)
- Design decisions (requires plan awareness)
- Anything depending on user conversation context
- Multi-file refactoring requiring coherent view of all changes

### Delegation Pattern

```python
# Good: mechanical search
Task(
  subagent_type="Explore",
  prompt="List all dataclass names and their file:line locations in producer/.",
  model="haiku"
)

# Good: background validation
Task(
  subagent_type="Bash",
  prompt="Run: cd /path/to/project && ruff check producer/ops_batch.py",
  model="haiku",
  run_in_background=True
)

# Bad: don't delegate core logic
# Task(prompt="Implement the select_batch algorithm")  # NO
```

### Post-Delegation Synthesis Protocol

When you spawn 2+ sub-agents and receive their results, follow this protocol before writing the final output.

**Step 1: Merge** — Combine all sub-agent results into a single working structure grouped by section/topic.

**Step 2: De-duplicate** — Apply these rules:
- **Identical findings**: Discard duplicates, keep one instance
- **Complementary findings** (same topic, different details): Merge into a single richer entry
- **Different detail levels** (same fact, one more detailed): Keep the most detailed version only

**Step 3: Detect conflicts** — Flag any cases where sub-agents disagree:
- **Contradictory claims** (agent A says X uses inheritance, agent B says X uses composition): Read the source file yourself to verify. Keep the verified answer.
- **Orthogonal perspectives** (agent A focuses on performance, agent B on maintainability): Keep both, mark as complementary trade-offs.
- **Low-confidence disagreement** (both uncertain): Trust the sub-agent with higher-confidence evidence. If equal, read source yourself.

**Step 4: Resolve** — For each conflict detected in Step 3, document the resolution in the output's `manual_notes` or equivalent section (1 line per resolution).

**Step 5: Write** — Produce the final schema-compliant output file. The output should read as if a single agent explored everything — no seams between sub-agent contributions.

**Key rule**: Sub-agents return raw text summaries. YOU do all synthesis, de-duplication, and conflict resolution. Never pass sub-agent output through to the final file unprocessed.
