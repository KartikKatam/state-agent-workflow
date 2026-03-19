# Doc 1: Git Management

**Status:** Draft v2
**Depends on:** Doc 0 (Token Efficiency Standards)
**Unblocks:** Doc 2 (Context Packets), Doc 4 (Hook Architecture), Doc 5 (Agent Specifications)
**Updated:** 2026-02-28 — Aligned with Claude Code v2.1.49+ native worktree support, added tiered worktree strategy, corrected hook contracts, added crash recovery.

## Problem

Up to 4 coders work in parallel, each needing isolated git state. Without structured worktree management, branch naming, commit strategy, and merge ordering, parallel coders will collide on files, produce unreadable commit histories, and waste tokens on bloated handoff payloads. The current `merge-worktree.sh` handles the merge step but has no concept of allocation tracking, merge queues, or checkpoint-based rollback. Handoff between a dying coder and its replacement currently requires writing the full diff to a file (~2,000-8,000 tokens). With micro-commits, the replacement coder reads `git log --oneline` (~200 tokens) instead.

---

## 1. Tiered Worktree Strategy

Claude Code v2.1.49+ provides native worktree support via `isolation: worktree` in subagent frontmatter and the `--worktree` CLI flag. Native worktrees are created at `<repo>/.claude/worktrees/<name>/` with branch `worktree-<name>`, branching from the default remote branch. Auto-cleanup occurs when no changes are made.

Not all agents need the same level of worktree control. The system uses three tiers:

### 1.1 Tier 1: Custom Worktrees (Chunk-Coders Only)

Chunk-coders **always** use worktrees with custom `WorktreeCreate`/`WorktreeRemove` hooks that replace Claude Code's default git behavior. This provides:

- **Base branch control** — branch from `WorkflowState.base_branch` (e.g., `feature/lpr-tracking`), not `origin/main`
- **Capacity enforcement** — hard limit of 4 concurrent coder worktrees
- **Port allocation** — PTC sandbox port per coder
- **git rerere** — enabled automatically for conflict resolution reuse
- **Agent state tracking** — worktree path and base branch recorded in agent state
- **Lifecycle logging** — JSONL events for observability
- **Handoff continuity** — replacement coders inherit the worktree/branch

The `isolation: worktree` field in the chunk-coder agent definition triggers these hooks. The hooks take over the entire lifecycle — Claude Code does not run its default `git worktree add`.

### 1.2 Tier 2: Native Worktrees (Planners — On Demand)

Planners use Claude Code's native worktree support **only when the orchestrator decides to spawn competing approaches**. This is a spawn-time decision, not hardcoded in the agent definition.

```python
# Normal single planner — no worktree
Task(subagent_type="plan-architect", name="planner", ...)

# Competing approaches — add isolation at spawn time
Task(subagent_type="plan-architect", name="planner-approach-a", isolation="worktree", ...)
Task(subagent_type="plan-architect", name="planner-approach-b", isolation="worktree", ...)
```

Native worktrees branch from the default remote branch, which is acceptable for planners since they produce plan artifacts (not source code that needs a specific base). The orchestrator passes absolute main-repo paths for any context packets the planners need to read (see Section 3.2).

### 1.3 Tier 3: No Worktrees (Explorers, Researchers, Auditors, Scribe)

These agents write directly to the main repo:

- **Explorers** write `.claude/context/` packets that must be immediately visible to subsequent agents.
- **Researchers** write `.claude/research/` files consumed by planners and the orchestrator.
- **Auditors** read coder worktrees via `git diff`/`git show` or absolute paths — they don't need their own worktree.
- **Scribe** commits to the integration branch in the main repo.

Worktrees would isolate their output files, breaking the artifact pipeline where each agent reads the previous agent's output from repo-relative paths.

---

## 2. Coder Worktree Allocation & Lifecycle

### 2.1 Capacity

Maximum 4 concurrent worktrees for coders. This is a hard limit enforced by the WorktreeCreate hook. Non-coder agents using native worktrees (Tier 2) do not count toward this limit.

### 2.2 Directory Convention

```
/tmp/wt-{agent-id}/
```

Example: `/tmp/wt-coder-p1-t3-a7f2/`

The agent ID (per `AGENT_ID_PATTERN`: `^[a-z]+-[a-z0-9]+-[a-z0-9]+-[a-f0-9]{4}$`) is the sole namespace. No collisions are possible between concurrent coders because each has a unique 4-hex suffix.

This differs from Claude Code's default path (`.claude/worktrees/<name>/`) because our custom WorktreeCreate hook controls the path. Using `/tmp/` avoids polluting the repo directory and makes cleanup simpler.

### 2.3 Lifecycle Tied to Coder State Machine

The worktree lifecycle maps directly to the coder state machine (`state-machines/coder.json`):

| Coder state | Worktree action | Hook/script |
|---|---|---|
| TASK_CLAIMED -> WORKTREE_CREATED | **Allocate**: create worktree, register in agent state, allocate port | `hooks/worktree_create.py` |
| MERGE_RESOLVED -> TASK_COMPLETE | **Release**: cleanup worktree, delete branch, release port | `hooks/worktree_remove.py` |
| -> SCRAP_RETRY | **Release**: cleanup worktree, preserve branch for debugging | `hooks/worktree_remove.py` |
| -> HANDOFF | **Preserve**: worktree stays, replacement coder inherits it | No remove hook; replacement coder reuses path |

### 2.4 WorktreeCreate Hook (`hooks/worktree_create.py`)

Triggered by the `WorktreeCreate` event in `.claude/settings.json`. Runs as a command hook. **This hook replaces Claude Code's default `git worktree add` behavior entirely** — when configured, Claude Code does not create the worktree itself.

**Input (stdin JSON from Claude Code):**
```json
{
  "session_id": "abc123",
  "transcript_path": "/home/user/.claude/projects/.../session.jsonl",
  "cwd": "/home/user/project",
  "hook_event_name": "WorktreeCreate",
  "name": "coder-p1-t3-a7f2"
}
```

The `name` field is the only identifier. The orchestrator encodes the agent ID as the worktree name when spawning:
```python
Task(
    subagent_type="chunk-coder",
    name="coder-p1-t3-a7f2",
    isolation="worktree",  # triggers WorktreeCreate hook
    ...
)
```

**Actions (in order):**
1. **Derive identifiers.** Extract agent ID from `name`. Compute branch: `agent/{name}`, path: `/tmp/wt-{name}/`.
2. **Check capacity.** Scan `~/.claude/state/agents/` for active agents with non-null `worktree` fields. If count >= 4, exit with non-zero code (fails worktree creation).
3. **Create worktree.** Run `git worktree add --lock <path> -b <branch> <base_branch>` where `base_branch` comes from the system state (`WorkflowState.base_branch`). The `--lock` flag prevents race conditions between creation and lock (prevents `git gc` from pruning an in-use worktree).
4. **Configure git.** Run:
   - `git -C <path> config rerere.enabled true` (reuse recorded conflict resolutions)
   - `git -C <path> config gc.worktreePruneExpire never` (prevent gc from racing with active worktrees)
5. **Update agent state.** Set `worktree` and `base_branch` fields on the agent's state file via `workflow_state.py register` or `locked_read_modify_write`.
6. **Allocate port.** Write `~/.claude/state/ports/{agent-id}.json` with `{"port": <allocated>, "worktree": "<path>"}`. Port range: 8100-8103 (one per worktree slot).
7. **Log event.** Append to `~/.claude/logs/worktree-lifecycle.jsonl`:
   ```json
   {"timestamp": "...", "event": "created", "agent_id": "coder-p1-t3-a7f2", "path": "/tmp/wt-coder-p1-t3-a7f2", "branch": "agent/coder-p1-t3-a7f2", "base_branch": "feature/lpr-tracking"}
   ```

**Output (stdout — absolute path only):**
```
/tmp/wt-coder-p1-t3-a7f2
```

Claude Code reads the printed path and uses it as the subagent's working directory. All other output must go to stderr to avoid interfering with the path. A non-zero exit code causes worktree creation to fail entirely.

### 2.5 WorktreeRemove Hook (`hooks/worktree_remove.py`)

Triggered by the `WorktreeRemove` event at session exit or when a subagent finishes.

**Input (stdin JSON from Claude Code):**
```json
{
  "session_id": "abc123",
  "transcript_path": "/home/user/.claude/projects/.../session.jsonl",
  "cwd": "/home/user/project",
  "hook_event_name": "WorktreeRemove",
  "worktree_path": "/tmp/wt-coder-p1-t3-a7f2"
}
```

**Important:** WorktreeRemove hooks have **no decision control** — they cannot block removal. They run for side effects only (cleanup, logging). Failures are logged in debug mode only.

**Actions (in order):**
1. **Derive agent ID.** Extract from `worktree_path` (strip `/tmp/wt-` prefix).
2. **Check handoff flag.** Read agent state — if `handoff_in_progress: true`, skip worktree/branch removal (replacement coder will inherit). Only release port and log.
3. **Unlock and remove worktree.** Run `git worktree unlock <path>` then `git worktree remove <path> --force`.
4. **Delete branch** (unless agent state has `preserve_branch: true` for SCRAP_RETRY debugging). Run `git branch -D <branch>`.
5. **Release port.** Delete `~/.claude/state/ports/{agent-id}.json`.
6. **Clear agent state.** Set `worktree: null` on agent state.
7. **Log event.** Append to `~/.claude/logs/worktree-lifecycle.jsonl`:
   ```json
   {"timestamp": "...", "event": "removed", "agent_id": "coder-p1-t3-a7f2", "reason": "task_complete"}
   ```

### 2.6 Crash Recovery & Startup Reconciliation

On orchestrator startup (SessionStart hook or workflow initialization), reconcile the worktree registry against actual filesystem state:

```bash
# Get actual worktrees from git
git worktree list --porcelain

# Compare against agent state files in ~/.claude/state/agents/
# For each agent with non-null worktree field:
#   - If worktree path doesn't exist on disk → clear agent state (stale entry)
#   - If port file exists but worktree doesn't → release port
# For each /tmp/wt-* directory on disk:
#   - If no corresponding agent state file → remove worktree (orphan from crash)
```

This handles:
- Crashes where the WorktreeRemove hook never fired
- Interrupted workflows that left worktrees behind
- Agent state that got out of sync with filesystem reality

### 2.7 Cleanup on Workflow Termination

When the system state machine reaches `COMPLETE` or the orchestrator shuts down, run the same reconciliation sweep as Section 2.6. Additionally, remove any remaining locked worktrees:

```bash
git worktree remove <path> --force --force  # two --force flags needed for locked worktrees
```

---

## 3. Competing Planner Worktrees

When the orchestrator decides to spawn competing approaches (e.g., two planners exploring different architectures), it uses Claude Code's native worktree support.

### 3.1 Spawning Competing Planners

```python
Task(
    subagent_type="plan-architect",
    name="planner-approach-a",
    team_name="feature-workflow",
    isolation="worktree",
    prompt="""Design approach A: pipeline architecture.
    Context packets (read from main repo): {main_repo_path}/.claude/context/_codebase.json
    Write your plan to: .claude/plans/lpr-tracking-plan.json"""
)

Task(
    subagent_type="plan-architect",
    name="planner-approach-b",
    team_name="feature-workflow",
    isolation="worktree",
    prompt="""Design approach B: event-driven architecture.
    Context packets (read from main repo): {main_repo_path}/.claude/context/_codebase.json
    Write your plan to: .claude/plans/lpr-tracking-plan.json"""
)
```

Both planners write to the same relative path but in separate worktrees — no collision. Native worktrees are used (no custom hooks) because planners don't need base branch control, ports, or merge queues.

### 3.2 Reading Context Across Worktrees

Worktrees are directories on the same machine — any agent can read any absolute path. The orchestrator passes **absolute main-repo paths** for context packets that planners need:

```
Context packet: /home/kartik/personal/project/.claude/context/_codebase.json
```

This works because explorers write to the main repo (Tier 3 — no worktree), so context packets are always at the main repo's path. Planners read from there and write their plans locally.

### 3.3 Resolution After Competing Planners Finish

Three options — the orchestrator offers the user a choice:

**Option A: Pick one.** Copy the winning plan to the main repo:
```bash
cp .claude/worktrees/planner-approach-a/.claude/plans/lpr-tracking-plan.json \
   .claude/plans/lpr-tracking-plan.json
```

**Option B: Synthesize.** Send one planner the other's plan via absolute path:
```
"Read the competing plan at:
 .claude/worktrees/planner-approach-b/.claude/plans/lpr-tracking-plan.json
 Synthesize the best ideas from both into your plan."
```

**Option C: Arbitrate.** Spawn an auditor (no worktree) that reads both plans and writes a unified version directly to the main repo.

In all cases, both planner worktrees are cleaned up afterward (Claude Code auto-removes worktrees with no committed changes, or prompts to keep/remove if changes exist).

### 3.4 Auditor Cross-Worktree Access

Auditors do NOT use worktrees. They access coder work via git commands or absolute paths from the main repo:

```bash
# Read coder's branch history
git log --oneline agent/coder-p1-t1-a7f2 --not feature/lpr-tracking

# Diff coder's changes against base
git diff feature/lpr-tracking...agent/coder-p1-t1-a7f2

# Read a specific file from the coder's branch
git show agent/coder-p1-t1-a7f2:src/detection/pipeline.py

# Or read directly from the worktree filesystem
cat /tmp/wt-coder-p1-t1-a7f2/src/detection/pipeline.py
```

The orchestrator passes the coder's branch name and worktree path in the auditor's task assignment message.

---

## 4. Branch Naming

### 4.1 Pattern

```
agent/{agent-id}
```

Examples:
- `agent/coder-p1-t3-a7f2`
- `agent/coder-p1-t5-b3e1`
- `agent/coder-p2-t1-c4d9`

The agent ID pattern (`AGENT_ID_PATTERN` in `schemas/_constants.py`: `^[a-z]+-[a-z0-9]+-[a-z0-9]+-[a-f0-9]{4}$`) guarantees uniqueness. The branch name is derived deterministically from the agent ID -- zero additional context injection needed (per Doc 0 Section 1, minimizing system overhead).

### 4.2 Integration Branch

The base branch is configurable per workflow, stored in `WorkflowState.base_branch` (`schemas/system_state.py`). It is NOT hardcoded to `main`.

Examples:
- `feature/lpr-tracking` (feature branch workflow)
- `develop` (gitflow)
- `main` (direct integration)

All worktrees branch from and merge back to this configured base.

### 4.3 Branch-to-Worktree Mapping

One branch per worktree. One worktree per coder. The mapping is:

```
agent/{agent-id}  <-->  /tmp/wt-{agent-id}/
```

The agent state file (`~/.claude/state/agents/{agent-id}.json`) is the authoritative record. It holds both the `worktree` path and the `base_branch`. The branch name is not stored separately because it is derivable from the agent ID.

---

## 5. Micro-Commit Strategy

### 5.1 Commit Rule: Every State Transition with Work Product

Rather than listing specific transitions, the rule is simple:

**Commit at every state transition where the departing state has non-empty `write_globs`.**

If a state allowed the coder to write files, there may be work product to commit when leaving that state. If the staging area is clean (no changes since last commit), the commit is skipped silently — no empty commits.

This is simpler to reason about than an explicit list, and it automatically adapts as the state machine evolves. Add a new writable state? Commits happen automatically.

### 5.2 Commit Points Derived from Coder State Machine

From `state-machines/coder.json`, the states with `write_globs` (and thus commit points on exit) are:

| Departing state | `write_globs` | Typical commit content | Message prefix |
|---|---|---|---|
| TEST_DESIGN | `tests/**/*.py` | Test files | `test: {task-id} {description}` |
| IMPLEMENTATION | `src/**/*.py` | Source code | `feat: {task-id} {description}` |
| QUALITY_GATE | `src/**/*.py`, `tests/**/*.py` | Format/lint/type fixes | `fix: {task-id} quality gate fixes` |
| FIXES | `src/**/*.py`, `tests/**/*.py` | Auditor-requested fixes | `fix: {task-id} address auditor feedback` |

States without `write_globs` (SPAWNED, TASK_CLAIMED, WORKTREE_CREATED, CONTEXT_REQUESTED, CONTEXT_LOADED, TDD_RED, RED_VERIFIED, etc.) have no work product to commit — the coder was reading, reasoning, or waiting, not writing.

### 5.3 Commit Action in the State Machine

The coder state machine gains a generic `auto_commit` action added to every transition that exits a writable state. The commit script:

1. Checks the staging area — if clean, skip silently.
2. Stages files matching the departing state's `write_globs`.
3. Determines the message prefix from the departing state (TEST_DESIGN → `test:`, IMPLEMENTATION → `feat:`, QUALITY_GATE/FIXES → `fix:`).
4. Runs `git commit` with the conventional message.
5. Creates a checkpoint ref (Section 7.2).

### 5.4 Commit Message Convention

```
{type}: {task-id} {description}

Agent: {agent-id}
State: {from_state} -> {to_state}
```

Where `type` is one of: `test`, `feat`, `fix`, `refactor`.

Example:
```
test: t3 add detection pipeline unit tests

Agent: coder-p1-t3-a7f2
State: TEST_DESIGN -> TESTS_WRITTEN
```

### 5.5 Why Micro-Commits Help Token Efficiency

When a coder hits context pressure and hands off to a replacement, the current system writes a handoff file containing the full diff and progress summary. With micro-commits:

**Before (handoff file):**
```
# Handoff for task t3
## Progress: IMPLEMENTATION (tests pass, implementing detect_plate())
## Files modified:
- src/detection/pipeline.py (full diff: +127 lines)
- tests/test_pipeline.py (full diff: +89 lines)
## What's done: ...
## What's left: ...
```
Estimated: ~2,000-8,000 tokens depending on diff size.

**After (replacement coder reads git log):**
```bash
git log --oneline agent/coder-p1-t3-a7f2 --not feature/lpr-tracking
```
```
a7f2c3d test: t3 add detection pipeline unit tests
b8e4f1a feat: t3 implement detect_plate core logic
```
The replacement coder sees exactly what was done (via commit messages and diffs on demand) without a handoff file. Estimated: ~200 tokens for the log, plus targeted `git diff` on specific commits if needed.

**Savings:** 1,800-7,800 tokens per handoff event. For a workflow with 8-12 handoffs across parallel coders, this is 14,400-93,600 tokens saved.

---

## 6. Integration & Merge Strategy

### 6.1 Squash-Merge by Default

The existing `scripts/merge-worktree.sh` already performs squash-merge. This collapses the micro-commits into a single clean commit on the integration branch. The micro-commit history is preserved in the backup ref (Section 7.1) for debugging if needed.

Squash-merge is the default. Regular merge is available via `--no-squash` for cases where preserving granular history on the integration branch is desired (e.g., complex multi-file refactors where the commit sequence aids review).

### 6.2 Merge Queue with FIFO + Priority

When multiple coders complete tasks simultaneously, merges must be ordered to minimize conflicts. The orchestrator maintains a merge queue.

**Queue structure:**

```json
{
  "merge_queue": [
    {"agent_id": "coder-p1-t3-a7f2", "task_id": "t3", "priority": 1, "enqueued_at": "2026-02-26T10:00:00Z", "status": "pending"},
    {"agent_id": "coder-p1-t5-b3e1", "task_id": "t5", "priority": 2, "enqueued_at": "2026-02-26T10:00:05Z", "status": "pending"}
  ]
}
```

Stored in: `~/.claude/state/merge-queue.json`

**Ordering rules:**
1. **Priority first.** Lower number = higher priority. Priority comes from the plan JSON's `priority` field per task.
2. **FIFO tiebreaker.** Equal-priority tasks merge in the order their coders signaled `TASK_REVIEW_REQUESTED` (approximated by `enqueued_at`).
3. **Dependency respect.** If task B depends on task A (from plan JSON `dependencies` field), task A must merge first regardless of priority.

**Orchestrator merge flow:**
1. Coder enters `MERGE` state.
2. Orchestrator checks merge queue. If this coder is next, proceed.
3. If not next, coder waits in `MERGE` state. Orchestrator sends a `peer_notify` message when its turn arrives.
4. Coder calls `scripts/merge-worktree.sh`.
5. On success, transition to `MERGE_RESOLVED` -> `TASK_COMPLETE`.
6. On conflict, transition to conflict resolution (Section 9).

### 6.3 Extending `merge-worktree.sh`

The existing script needs these additions:

```bash
# New options:
#   --priority N        Task priority for merge queue ordering
#   --task-id ID        Task ID for merge queue tracking
#   --dependencies IDs  Comma-separated dependency task IDs

# New pre-merge check:
#   Query merge queue via workflow_state.py to verify this agent is next
#   Block if dependencies haven't merged yet
```

The script already handles backup refs, quality gate, squash-merge, and cleanup. The additions are queue-awareness and dependency checking.

### 6.4 Configurable Base Branch

The base branch is read from `WorkflowState.base_branch` at worktree creation time and stored in `AgentState.base_branch`. The merge script receives it as the `<base-branch>` argument. No hardcoding.

---

## 7. Checkpoint & Reversion

### 7.1 Backup Refs Before Merge

Already implemented in `merge-worktree.sh`:

```bash
BACKUP_REF="refs/backups/${BASE_BRANCH//\//-}-$TIMESTAMP"
git update-ref "$BACKUP_REF" "$BASE_SHA"
```

This captures the integration branch state before the merge, enabling `git reset --hard $BACKUP_REF` to undo a bad merge.

### 7.2 Checkpoint Refs at TDD States

Within a coder's worktree, checkpoint refs mark known-good states during the TDD cycle. These enable rollback if implementation breaks previously passing tests.

**Checkpoint ref pattern:**
```
refs/checkpoints/{agent-id}/{state}
```

**Checkpoint creation points:**

| After transition to | Ref name | Purpose |
|---|---|---|
| TESTS_WRITTEN | `refs/checkpoints/{id}/tests-written` | Rollback target if tests become unfixable |
| TDD_GREEN (first pass) | `refs/checkpoints/{id}/tdd-green` | Last known state where all tests pass |
| QUALITY_GATE pass | `refs/checkpoints/{id}/gate-passed` | Last known state where all quality checks pass |

**Creation (in commit action):**
```bash
git -C /tmp/wt-{agent-id} update-ref refs/checkpoints/{agent-id}/{state} HEAD
```

**Rollback scenario:**
If a coder is in IMPLEMENTATION and the TDD_GREEN -> IMPLEMENTATION loop has fired 3+ times (tests still failing), the coder can:
1. `git reset --hard refs/checkpoints/{id}/tdd-green` to return to the last green state.
2. Re-read the test expectations.
3. Try a different implementation approach.

This avoids wasting context tokens on increasingly divergent attempts. The state machine's `max_occurrences: 5` on the TDD_GREEN -> IMPLEMENTATION transition provides the hard limit; checkpoints provide the escape hatch before that limit is hit.

### 7.3 Recovery Procedures

**Merge rollback (orchestrator-initiated):**
```bash
# 1. Find backup ref
git for-each-ref refs/backups/ --format='%(refname) %(creatordate:short)' | sort -k2 -r | head -5

# 2. Reset integration branch
git reset --hard refs/backups/{base-branch}-{timestamp}

# 3. Re-queue the failed merge
python3 scripts/workflow_state.py transition --agent-id {id} --to-state MERGE --trigger merge_retry
```

**TDD rollback (coder-initiated):**
```bash
# 1. Check available checkpoints
git -C /tmp/wt-{agent-id} for-each-ref refs/checkpoints/{agent-id}/

# 2. Reset to last green
git -C /tmp/wt-{agent-id} reset --hard refs/checkpoints/{agent-id}/tdd-green

# 3. Re-enter IMPLEMENTATION state
python3 scripts/workflow_state.py transition --agent-id {id} --to-state IMPLEMENTATION --trigger checkpoint_rollback
```

---

## 8. Conflict Prevention

### 8.1 Function Overlap Detection

The strategist populates `touched_functions` on each task in the plan JSON (per `rules/plans.md`). Before dispatching parallel coders, the orchestrator validates that no two concurrent tasks share functions.

**Validation script:** `scripts/validate_function_overlap.py`

**Input:** The plan JSON file.

**Algorithm:**
1. For each pair of tasks assigned to the same phase, compute the intersection of their `touched_functions` lists.
2. If any intersection is non-empty, flag it as a conflict.
3. Return the conflict list.

**Example plan JSON task entries:**
```json
[
  {"task_id": "t3", "touched_functions": ["detect_plate", "preprocess_frame"], "phase": "p1"},
  {"task_id": "t5", "touched_functions": ["track_vehicle", "associate_detection"], "phase": "p1"},
  {"task_id": "t6", "touched_functions": ["detect_plate", "crop_region"], "phase": "p1"}
]
```

Result: t3 and t6 overlap on `detect_plate` -- they cannot run in parallel. The orchestrator must serialize them (t3 first, then t6) or reassign t6 to a later phase.

### 8.2 Assignment-Time Validation

The orchestrator runs overlap validation BEFORE dispatching coders:

```python
# In orchestrator dispatch logic:
active_tasks = get_active_coder_tasks()  # tasks currently being worked on
new_task = plan.tasks[next_task_id]
for active in active_tasks:
    overlap = set(active.touched_functions) & set(new_task.touched_functions)
    if overlap:
        # Cannot dispatch — queue until active task completes
        defer_task(new_task, reason=f"function overlap with {active.task_id}: {overlap}")
        break
```

This is a pre-dispatch check, not a hook. It runs in the orchestrator's decision logic when selecting which task to assign next.

### 8.3 File-Level Conflict Detection

As a coarser-grained fallback, track which files each coder has modified:

```bash
git -C /tmp/wt-{agent-id} diff --name-only {base_branch}...HEAD
```

The orchestrator can query this for any active coder's worktree. If a new task's expected file set (derived from `write_globs` and `touched_functions`) overlaps with files already modified in an active worktree, the orchestrator defers the task.

---

## 9. Conflict Resolution

Despite prevention measures, merge conflicts can occur when:
- `touched_functions` was incomplete (strategist missed a dependency).
- Two tasks modify the same file in non-overlapping functions but git cannot auto-merge the hunks.
- Refactoring changes import ordering or shared utility functions.

### 9.1 git rerere for Recurring Patterns

Enabled per-worktree by the WorktreeCreate hook (Section 2.4, step 4):

```bash
git -C /tmp/wt-{agent-id} config rerere.enabled true
```

git rerere ("reuse recorded resolution") records conflict resolutions and auto-applies them when the same conflict recurs. This is especially valuable in the TDD cycle where a coder may merge, encounter a conflict, resolve it, then later need to re-merge after additional commits.

The rerere database is per-repository (shared across worktrees via the common `.git/rr-cache` directory), so a resolution recorded by one coder benefits subsequent coders hitting the same conflict.

### 9.2 Coder Think Tool for Simple Conflicts

When `merge-worktree.sh` exits with a merge conflict (exit code 1), the coder:

1. Transitions to `MERGE` state (already there).
2. Reads the conflict markers via `git diff --name-only --diff-filter=U`.
3. Uses the Think tool (required by `requires_think: true` on the `conflicts_resolved` transition in `coder.json`) to reason about the resolution.
4. Edits the conflicting files to resolve.
5. Runs `git add` on resolved files, then `git commit`.
6. Transitions to `MERGE_RESOLVED` via the `conflicts_resolved` trigger with the `rerere_applied_or_manual_resolve` guard.

**Simple conflict criteria (coder handles):**
- Fewer than 3 conflicting files.
- Conflicts are in code the coder wrote or reviewed in this task.
- No structural disagreements (just line-level overlaps).

### 9.3 Auditor Arbitration for Complex Conflicts

If the coder cannot resolve the conflict (more than 3 files, or conflicts in code outside its task scope), the orchestrator escalates to auditor arbitration mode.

**Flow:**
1. Coder sends `status_update` to orchestrator: `"merge_conflict_complex"`.
2. Orchestrator spawns an auditor in `ARBITRATION` mode (auditor state machine supports this).
3. Auditor reads both branches, the plan JSON for both tasks, and the conflict markers.
4. Auditor resolves conflicts, commits, and signals completion.
5. Coder proceeds to `MERGE_RESOLVED`.

Arbitration is a last resort. The function overlap prevention (Section 8) should eliminate most conflicts. The Think tool resolution (Section 9.2) handles the remainder. Auditor arbitration handles edge cases.

---

## 10. Token Budget

Per Doc 0 Section 1, all git management data injected into agent context must minimize overhead.

### 10.1 Micro-Commits Reduce Handoff Payload

| Scenario | Tokens | Source |
|---|---|---|
| **Before:** Handoff file with full diff + progress | 2,000-8,000 | Written to `.claude/handoffs/`, loaded by replacement coder |
| **After:** `git log --oneline` of micro-commits | ~200 | Replacement coder runs git command, reads output |
| **After:** Targeted `git diff {commit}` on demand | ~300-500 per file | Only fetched for files the replacement needs to understand |

**Per-handoff savings:** 1,500-7,500 tokens.
**Per-workflow savings (est. 8-12 handoffs):** 12,000-90,000 tokens.

### 10.2 Branch Naming Derives from Agent ID

The branch name `agent/{agent-id}` is derived from `AGENT_ID_PATTERN`. The agent already knows its own ID (injected at spawn via `CLAUDE_CODE_AGENT_NAME` env var, per `state_helpers.py`). Therefore:

- **Zero additional context injection** for branch name. The coder computes it: `f"agent/{self.agent_id}"`.
- **Zero additional context injection** for worktree path. The coder computes it: `f"/tmp/wt-{self.agent_id}/"`.

Per Doc 0 Section 2.3, information derivable from existing context should not be redundantly injected.

### 10.3 Merge Queue in Compact Format

When the orchestrator inspects the merge queue (per Doc 0 Section 2.2, TOON format for arrays of >3 objects):

Before (~280 tokens):
```json
[
  {"agent_id": "coder-p1-t3-a7f2", "task_id": "t3", "priority": 1, "enqueued_at": "2026-02-26T10:00:00Z", "status": "pending"},
  {"agent_id": "coder-p1-t5-b3e1", "task_id": "t5", "priority": 2, "enqueued_at": "2026-02-26T10:00:05Z", "status": "pending"},
  {"agent_id": "coder-p1-t6-c4d9", "task_id": "t6", "priority": 2, "enqueued_at": "2026-02-26T10:00:10Z", "status": "blocked"},
  {"agent_id": "coder-p1-t7-d5e0", "task_id": "t7", "priority": 3, "enqueued_at": "2026-02-26T10:01:00Z", "status": "pending"}
]
```

After (~120 tokens, TOON):
```
# MERGE_QUEUE
agent|task|pri|enqueued|status
coder-p1-t3-a7f2|t3|1|10:00|pending
coder-p1-t5-b3e1|t5|2|10:00|pending
coder-p1-t6-c4d9|t6|2|10:00|blocked
coder-p1-t7-d5e0|t7|3|10:01|pending
```

Savings: ~57%.

---

## 11. Integration Points

| File | Action | Changes |
|---|---|---|
| `state-machines/coder.json` | **Modify** | Add commit actions (`commit_tests`, `commit_implementation`, `commit_gate_fixes`, `commit_auditor_fixes`) to transitions at TEST_DESIGN->TESTS_WRITTEN, TDD_GREEN->INVARIANT_CHECK, QUALITY_GATE->TASK_REVIEW_REQUESTED, FIXES->TASK_REVIEW_REQUESTED |
| `scripts/merge-worktree.sh` | **Modify** | Add `--priority`, `--task-id`, `--dependencies` options; add merge queue check before merge; add dependency validation |
| `schemas/agent_state.py` | **No change** | `worktree` and `base_branch` fields already exist |
| `schemas/system_state.py` | **No change** | `base_branch` field already exists on `WorkflowState` |
| `schemas/_constants.py` | **No change** | `AGENT_ID_PATTERN` already defines the naming convention |
| `hooks/worktree_create.py` | **New** | WorktreeCreate command hook: capacity check, worktree creation, agent state update, port allocation, rerere + gc config, lifecycle logging. Prints absolute worktree path to stdout. |
| `hooks/worktree_remove.py` | **New** | WorktreeRemove command hook: worktree removal, branch deletion, port release, agent state clear, lifecycle logging. No decision control (side effects only). |
| `hooks/utils/state_helpers.py` | **Modify** | Add `WORKTREE_LOG` path constant, add `log_worktree_event()` helper, add `reconcile_worktrees()` for crash recovery |
| `scripts/validate_function_overlap.py` | **New** | Pre-dispatch overlap validation for parallel coder safety |
| `scripts/workflow_state.py` | **Modify** | Add `enqueue-merge`, `dequeue-merge`, `next-merge` commands for merge queue management; add `worktree_path_exists` and `port_allocated` guard implementations |
| `.claude/settings.json` | **Modify** | Register WorktreeCreate and WorktreeRemove hook entries |
| `.claude/agents/chunk-coder.md` | **Modify** | Add `isolation: worktree` to frontmatter |
| `.gitignore` | **Modify** | Add `.claude/worktrees/` (for native Tier 2 worktrees) |

### Settings.json Hook Registration

Add to `.claude/settings.json`. Note: `WorktreeCreate` and `WorktreeRemove` do not support matchers — they always fire on every occurrence. The `matcher` field is omitted (not set to empty string).

```json
{
  "hooks": {
    "WorktreeCreate": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 hooks/worktree_create.py",
            "timeout": 15000
          }
        ]
      }
    ],
    "WorktreeRemove": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 hooks/worktree_remove.py",
            "timeout": 10000
          }
        ]
      }
    ]
  }
}
```

**Important caveat:** These hooks fire for ALL `isolation: worktree` subagents, including Tier 2 native planners. The WorktreeCreate hook must detect whether the requesting agent is a coder (needs custom path, capacity check, ports) or a non-coder (should fall through to native behavior).

**Detection strategy:** Check if the `name` field matches the coder agent ID pattern (`coder-*`). If yes, run full custom logic. If no, fall through — create a native-style worktree at `.claude/worktrees/<name>/` using `git worktree add` with default branch, and print that path. This way both tiers work through the same hook registration:

```python
# In hooks/worktree_create.py
name = input_json["name"]
if name.startswith("coder-"):
    # Tier 1: full custom logic (capacity, base branch, ports, rerere)
    path = create_coder_worktree(name)
else:
    # Tier 2: native-equivalent behavior
    path = create_native_worktree(name)
print(path)
```

### New Guards for `workflow_state.py`

```python
_GUARD_REGISTRY.update({
    # Worktree guards (used in TASK_CLAIMED -> WORKTREE_CREATED transition)
    "worktree_path_exists": _guard_worktree_path_exists,
    "port_allocated": _guard_port_allocated,
    # Merge guards (used in MERGE -> MERGE_RESOLVED transition)
    "merge_exit_zero": _guard_merge_exit_zero,
    "rerere_applied_or_manual_resolve": _guard_rerere_applied_or_manual_resolve,
})
```

---

## 12. Verification Criteria

### Tier 1: Coder Worktrees (Custom Hooks)
- [ ] WorktreeCreate hook enforces max 4 concurrent coder worktrees
- [ ] WorktreeCreate hook creates worktree at `/tmp/wt-{agent-id}/` branching from `WorkflowState.base_branch`
- [ ] WorktreeCreate hook uses `--lock` flag at creation to prevent race conditions
- [ ] WorktreeCreate hook sets `rerere.enabled true` and `gc.worktreePruneExpire never`
- [ ] WorktreeCreate hook updates agent state with `worktree` and `base_branch` fields
- [ ] WorktreeCreate hook allocates port in `~/.claude/state/ports/{agent-id}.json`
- [ ] WorktreeCreate hook prints absolute path to stdout (only output on stdout)
- [ ] WorktreeCreate hook exits non-zero on capacity limit (blocks creation)
- [ ] WorktreeRemove hook cleans up worktree, branch, port, and agent state
- [ ] WorktreeRemove hook skips worktree/branch removal when `handoff_in_progress: true`
- [ ] WorktreeRemove hook unlocks worktree before removal (`git worktree unlock`)
- [ ] Worktree lifecycle events logged to `~/.claude/logs/worktree-lifecycle.jsonl`

### Tier 2: Native Worktrees (Planners on Demand)
- [ ] WorktreeCreate hook detects non-coder agents and falls through to native-style behavior
- [ ] Competing planners can read context packets from main-repo absolute paths
- [ ] Competing planners can read each other's worktrees via absolute paths
- [ ] Orchestrator offers pick-one / synthesize / arbitrate options after competing planners finish

### Tier 3: No Worktrees (Explorers, Researchers, Auditors, Scribe)
- [ ] Auditor can read coder branches via `git diff`, `git show`, and absolute worktree paths

### Crash Recovery
- [ ] Startup reconciliation compares agent state registry against `git worktree list` output
- [ ] Stale agent state entries (worktree path doesn't exist) are cleared
- [ ] Orphan worktrees (no matching agent state) are removed
- [ ] Stale port allocations (no matching worktree) are released
- [ ] Workflow termination cleanup handles locked worktrees (`--force --force`)

### Git Operations (Unchanged from v1)
- [ ] Branch naming follows `agent/{agent-id}` pattern with no manual configuration
- [ ] Micro-commits fire at all 4 TDD transition points (tests written, implementation green, gate fixes, auditor fixes)
- [ ] Commit messages follow `{type}: {task-id} {description}` convention
- [ ] Checkpoint refs created at TESTS_WRITTEN, TDD_GREEN, and QUALITY_GATE states
- [ ] `merge-worktree.sh` extended with merge queue awareness and dependency checking
- [ ] Merge queue uses FIFO + priority ordering with dependency respect
- [ ] `validate_function_overlap.py` detects overlapping `touched_functions` between parallel tasks
- [ ] Orchestrator defers task dispatch when function overlap exists with active coders
- [ ] git rerere enabled per-worktree; resolutions shared across coders via common `.git/rr-cache`
- [ ] Coder handles simple merge conflicts (fewer than 3 files) via Think tool
- [ ] Auditor arbitration mode available for complex conflicts
- [ ] Replacement coder reads `git log` (~200 tokens) instead of handoff file (~2,000-8,000 tokens)
- [ ] Merge queue serialized in TOON format when injected into orchestrator context (per Doc 0 Section 2.2)
