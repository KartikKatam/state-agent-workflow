# Session Handoff: Git Worktree Management Implementation

**Created:** 2026-03-01
**Previous session:** Design research + design doc update for git/worktree management
**Target:** Implement the git worktree management system per `01-git-management.md` (v2)
**Implementation target:** `new.claude/` (V2 rewrite — NOT the existing `.claude/`)

---

## What Was Done This Session

1. **Spawned two researchers** to investigate worktree patterns:
   - General multi-agent worktree best practices (merge queues, conflict prevention, rollback)
   - Agentic coding repo patterns (Claude Code, aider, OpenHands, Cursor, etc.)

2. **Read Claude Code's actual documentation** for `--worktree`, `isolation: worktree`, WorktreeCreate/WorktreeRemove hooks, and subagent frontmatter — discovered critical differences from our original design assumptions.

3. **Updated the design doc** (`01-git-management.md`) from v1 to v2:
   - Added tiered worktree strategy (3 tiers)
   - Corrected hook contracts to match Claude Code's actual API
   - Added crash recovery / startup reconciliation
   - Added competing planner workflow
   - Added auditor cross-worktree access patterns
   - Added `--lock` and `gc.worktreePruneExpire` best practices

4. **Did NOT write any implementation code.** Only the design doc was updated.

---

## Key Design Decisions (Rationale Included)

### Three-Tier Worktree Strategy

| Tier | Agents | Mechanism | Why |
|------|--------|-----------|-----|
| **Tier 1: Custom hooks** | Chunk-coders | WorktreeCreate/Remove hooks replace Claude Code's default | Need base branch control, capacity limits, ports, rerere, micro-commits, merge queue, handoff continuity |
| **Tier 2: Native on-demand** | Planners (competing approaches only) | `isolation: worktree` at spawn time (orchestrator decides) | Competing planners need file isolation; native worktrees are sufficient since they don't need merge queues or base branch control |
| **Tier 3: No worktrees** | Explorers, researchers, auditors, scribe | Write directly to main repo | Their output files (context packets, research, plans) must be immediately visible to subsequent agents. Worktrees would isolate these files, breaking the artifact pipeline |

### WorktreeCreate Hook Contract (CRITICAL)

Claude Code's actual hook API differs from our v1 assumptions:

**Input** (stdin JSON):
```json
{
  "session_id": "abc123",
  "hook_event_name": "WorktreeCreate",
  "name": "coder-p1-t3-a7f2",
  "cwd": "/home/kartik/personal/project",
  "transcript_path": "..."
}
```

**Output**: Print ONLY the absolute path to stdout. Nothing else on stdout.

**Key behaviors:**
- Hook **replaces** default `git worktree add` entirely — Claude Code does NOT create the worktree
- Non-zero exit code **fails** worktree creation (this is how we enforce capacity)
- No matcher support — fires for ALL `isolation: worktree` subagents
- The hook must detect coder vs non-coder from the `name` field and branch accordingly

### WorktreeRemove Hook Contract

**Input** (stdin JSON):
```json
{
  "session_id": "abc123",
  "hook_event_name": "WorktreeRemove",
  "worktree_path": "/tmp/wt-coder-p1-t3-a7f2"
}
```

**Key behaviors:**
- **No decision control** — cannot block removal
- Runs for side effects only (cleanup, port release, state clear, logging)
- Failures are logged in debug mode only

### Why Not Native Worktrees for Coders

Native `isolation: worktree` creates worktrees at `.claude/worktrees/<name>/` branching from the **default remote branch** (e.g., `origin/main`). This is wrong for coders because:
- We work on feature branches (e.g., `feature/lpr-tracking`) — coders must branch from that
- No capacity enforcement (Claude Code has no worktree limit)
- No port allocation for PTC sandbox
- No rerere configuration
- No micro-commit infrastructure
- No merge queue integration
- Handoff breaks — replacement coder gets a NEW worktree on a NEW branch, losing the predecessor's work

### Hook Routing Strategy

Since WorktreeCreate/Remove fire for ALL `isolation: worktree` subagents (no matcher), the hook must route internally:

```python
name = input_json["name"]
if name.startswith("coder-"):
    # Tier 1: full custom logic
    path = create_coder_worktree(name)
else:
    # Tier 2: native-equivalent behavior
    path = create_native_worktree(name)
print(path)
```

---

## Files to Implement

All implementation goes into the `new.claude/` directory structure. The existing `.claude/` is V1 and should not be modified.

### New Files

| File | Purpose | Priority |
|------|---------|----------|
| `new.claude/hooks/worktree_create.py` | WorktreeCreate hook — capacity check, git worktree add, rerere, ports, state, logging | P0 |
| `new.claude/hooks/worktree_remove.py` | WorktreeRemove hook — cleanup worktree, branch, ports, state, logging | P0 |
| `new.claude/scripts/reconcile_worktrees.py` | Startup crash recovery — reconcile agent state vs `git worktree list` | P1 |

### Files to Modify

| File | Change | Priority |
|------|--------|----------|
| `new.claude/settings.json` | Add WorktreeCreate/WorktreeRemove hook entries | P0 |
| Chunk-coder agent definition (when it exists) | Add `isolation: worktree` to frontmatter | P1 (blocked on agent specs) |
| `.gitignore` | Add `.claude/worktrees/` | P0 |

### Files to Reference (Read Only)

| File | Why |
|------|-----|
| `01-git-management.md` | **THE** design spec — implement exactly what it says |
| `agentic_workflow_design.md` | V2 architecture — agent roles, state machines, 8 agents |
| `05-agent-specifications.md` | Agent spec format — how to structure agent definitions |
| `.claude/research/worktree-multi-agent-practices.md` | Research on merge queues, conflict prevention, rollback |
| `.claude/research/agentic-coding-worktree-patterns.md` | Research on how Claude Code, Cursor, aider, etc. handle worktrees |
| `schemas/agent_state.py` | AgentState model — has `worktree` and `base_branch` fields |
| `schemas/system_state.py` | WorkflowState model — has `base_branch` field |
| `schemas/_constants.py` | AGENT_ID_PATTERN and other shared constants |
| `hooks/utils/state_helpers.py` | Existing state helpers — add worktree helpers here |
| `hooks/utils/event_logger.py` | Existing event logger — use for lifecycle logging |
| `scripts/allocate-ports.sh` | Existing port allocation — reference for port logic |
| `scripts/merge-worktree.sh` | Existing merge script — needs queue-awareness additions |
| `scripts/workflow_state.py` | State machine daemon — needs merge queue commands |
| `state-machines/coder.json` | Coder state machine — worktree lifecycle maps to this |

---

## Implementation Notes

### V2 vs V1 Directory Structure

The V2 rewrite lives in `new.claude/`. The existing infrastructure at the repo root (`hooks/`, `scripts/`, `schemas/`, `state-machines/`) was built during Phase 1 and is part of the V2 system. The `new.claude/` directory contains V2-specific additions (skills, plans, research, settings, and eventually agents and CLAUDE.md).

**Hook location:** New hooks go in `new.claude/hooks/` or the existing `hooks/` directory depending on whether they're replacing V1 hooks or are net-new. The worktree hooks are net-new — place them wherever is consistent with the existing hook structure. Check `hooks/` to see the current convention.

### Existing Infrastructure to Leverage

The following already exists and should be used, not reimplemented:

- **`schemas/agent_state.py`** — AgentState with `worktree: Optional[str]` and `base_branch: Optional[str]`
- **`schemas/system_state.py`** — WorkflowState with `base_branch: str`
- **`hooks/utils/state_helpers.py`** — `locked_read_modify_write()`, `get_agent_state()`, etc.
- **`hooks/utils/event_logger.py`** — `log_event()` for structured JSONL logging
- **`hooks/utils/trace_context.py`** — W3C trace context propagation
- **`hooks/utils/__init__.py`** — Common imports
- **`scripts/allocate-ports.sh`** — Port allocation from range 30000-39999
- **`scripts/merge-worktree.sh`** — Squash-merge with backup refs

### What the Design Doc Says to Build (Section by Section)

**Section 2.4 — WorktreeCreate hook:**
1. Derive agent ID from `name` field
2. Check capacity (scan `~/.claude/state/agents/` for non-null `worktree` fields, fail if >= 4)
3. `git worktree add --lock <path> -b agent/<name> <base_branch>`
4. `git -C <path> config rerere.enabled true`
5. `git -C <path> config gc.worktreePruneExpire never`
6. Update agent state file (set `worktree` and `base_branch`)
7. Allocate port (write to `~/.claude/state/ports/<name>.json`)
8. Log to `~/.claude/logs/worktree-lifecycle.jsonl`
9. Print absolute path to stdout

**Section 2.5 — WorktreeRemove hook:**
1. Derive agent ID from `worktree_path`
2. Check `handoff_in_progress` flag — skip removal if true
3. `git worktree unlock <path>` then `git worktree remove <path> --force`
4. Delete branch (unless `preserve_branch: true`)
5. Release port
6. Clear agent state
7. Log lifecycle event

**Section 2.6 — Crash recovery:**
1. Run `git worktree list --porcelain`
2. Compare against agent state files in `~/.claude/state/agents/`
3. Clear stale state entries, remove orphan worktrees, release stale ports

**Section 11 — Settings.json:**
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

Note: No `matcher` field — WorktreeCreate/WorktreeRemove don't support matchers.

### What NOT to Implement (Out of Scope)

- **Merge queue** (Section 6.2) — Separate implementation task, depends on `scripts/workflow_state.py` changes
- **Micro-commit automation** (Section 5) — Implemented via PostToolUse hook, separate task
- **Checkpoint refs** (Section 7.2) — Created by the auto-commit action, separate task
- **Function overlap validation** (Section 8.1) — Separate script (`validate_function_overlap.py`)
- **Merge-worktree.sh extensions** (Section 6.3) — Separate task
- **Agent specs** — Phase 3 work, not part of this task
- **Competing planner workflow** — Orchestrator behavioral logic, not infrastructure

### Testing Strategy

- Unit test worktree_create.py: mock `subprocess.run` for git commands, verify stdout output, capacity checking, state file updates
- Unit test worktree_remove.py: mock git commands, verify handoff flag checking, cleanup sequence
- Unit test reconcile_worktrees.py: mock `git worktree list` output and filesystem state
- Integration test: create actual worktree on a test repo, verify path/branch/config, then remove and verify cleanup

---

## Context That Informed This Design

### Research Files (read these for background)

1. **`.claude/research/worktree-multi-agent-practices.md`** — 525 lines
   - Key findings: squash-merge validated, function-level conflict prevention is ahead of industry, speculative parallel CI is our biggest gap (acceptable for ≤4 coders), checkpoint refs are novel
   - Best practices adopted: `--lock` at creation, `gc.worktreePruneExpire=never`, startup reconciliation

2. **`.claude/research/agentic-coding-worktree-patterns.md`** — 789 lines
   - Key findings: Claude Code v2.1.49 has native `isolation: worktree`, ecosystem split (local=worktrees, cloud=containers), Agent Teams do NOT auto-isolate (teammates share filesystem)
   - Innovations worth noting: parallel-cc's file claims system, ccswarm's channel-based coordination, Cline's shadow git via core.worktree

### Claude Code Documentation Findings

- `isolation: worktree` in subagent frontmatter gives each subagent its own worktree
- WorktreeCreate/Remove hooks **replace** default git behavior (designed for non-git VCS)
- Hook input is minimal: `{session_id, name, cwd}` for create, `{session_id, worktree_path}` for remove
- No matcher support on these hooks — they fire for every worktree creation
- WorktreeRemove has no decision control (cannot block removal)
- Native worktree path: `<repo>/.claude/worktrees/<name>/`, branch: `worktree-<name>`
- Auto-cleanup on no changes; prompt to keep/remove on changes

### Conversation Decisions (not in design doc)

- **Competitive implementation** (multiple coders on same task) needs zero infrastructure changes — it's just the orchestrator spawning two coders instead of one
- **Auditors** access coder worktrees via `git diff`/`git show`/absolute paths — no worktree of their own needed
- **Explorer/researcher output** must stay in main repo because subsequent agents read those files from repo-relative paths
- **Planners in worktrees** need absolute main-repo paths for context packets since the worktree won't have uncommitted files from the main repo

---

## Verification Criteria (from design doc Section 12)

The implementation is complete when:

### Tier 1 (Custom Hooks)
- [ ] WorktreeCreate hook enforces max 4 concurrent coder worktrees
- [ ] WorktreeCreate hook creates worktree at `/tmp/wt-{agent-id}/` branching from `WorkflowState.base_branch`
- [ ] WorktreeCreate hook uses `--lock` flag at creation
- [ ] WorktreeCreate hook sets `rerere.enabled true` and `gc.worktreePruneExpire never`
- [ ] WorktreeCreate hook updates agent state with `worktree` and `base_branch` fields
- [ ] WorktreeCreate hook allocates port in `~/.claude/state/ports/{agent-id}.json`
- [ ] WorktreeCreate hook prints absolute path to stdout (only output on stdout)
- [ ] WorktreeCreate hook exits non-zero on capacity limit (blocks creation)
- [ ] WorktreeRemove hook cleans up worktree, branch, port, and agent state
- [ ] WorktreeRemove hook skips removal when `handoff_in_progress: true`
- [ ] WorktreeRemove hook unlocks worktree before removal
- [ ] Lifecycle events logged to `~/.claude/logs/worktree-lifecycle.jsonl`

### Tier 2 (Native Worktrees)
- [ ] WorktreeCreate hook detects non-coder agents and falls through to native-style behavior

### Crash Recovery
- [ ] Startup reconciliation compares agent state vs `git worktree list`
- [ ] Stale state entries cleared, orphan worktrees removed, stale ports released
