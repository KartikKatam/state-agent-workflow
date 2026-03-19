# Agentic Coding Tools: Git Worktree & Isolation Patterns

**Research date:** 2026-03-01
**Confidence:** See per-section ratings
**Scope:** How the most popular agentic coding tools handle git isolation, worktrees, parallel agents, and merge strategies

---

## 1. Executive Summary

The agentic coding ecosystem has converged on **two distinct isolation patterns** depending on deployment model:

1. **Hosted/cloud tools** (Devin, GitHub Copilot Coding Agent, OpenHands SaaS) use **VM/container-per-task** isolation. The container IS the isolation unit — no worktrees needed.

2. **Local/IDE tools** (Claude Code, Cursor, VS Code Background Agents, ccswarm) use **git worktree-per-agent** isolation. Worktrees are cheap, fast, and share the `.git` object database.

**Key finding for this project:** Claude Code (CLI v2.1.49-2.1.50, shipped February 2026) now has **native first-class worktree support** with `--worktree` flag AND `isolation: worktree` in subagent frontmatter. Our multi-agent TDD workflow can directly leverage this.

**Merge strategies** are consistently PR-based and human-reviewed across the ecosystem. Auto-merge is not trusted for agent output. The rare exception is Cursor (Best-of-N parallel agents with automatic clean-merge attempt).

**Critical gap in current workflow:** Our `chunk-coder` agents do NOT use worktree isolation — they share the same filesystem. This is the primary gap compared to best-in-class tools.

---

## 2. Claude Code Native Worktree Support

**Confidence: HIGH** — from official documentation at code.claude.com/docs/en/common-workflows and hooks reference

### 2.1 The `--worktree` / `-w` CLI Flag

Introduced ~v2.1.49-2.1.50 (February 2026):

```bash
# Named worktree — creates .claude/worktrees/feature-auth/ with branch worktree-feature-auth
claude --worktree feature-auth
claude -w feature-auth

# Auto-generated name (e.g., "bright-running-fox")
claude --worktree

# Combined with tmux for a separate terminal session
claude --worktree feature-auth --tmux
```

- Worktrees created at `<repo>/.claude/worktrees/<name>/`
- Branch named `worktree-<name>`, branched from default remote branch
- Can also be triggered mid-session: "work in a worktree" or "start a worktree"

### 2.2 Subagent Worktree Isolation

Two mechanisms to give subagents their own worktrees:

**a) Conversational request:**
> "use worktrees for your agents"

**b) Subagent frontmatter:**
```yaml
---
name: chunk-coder-01
description: Implements chunk 01 in isolation
isolation: worktree
---
```

Each subagent with `isolation: worktree` gets a dedicated branch + working directory created on spawn, auto-cleaned on finish (if no changes made).

### 2.3 Cleanup Behavior

- **No changes:** Worktree and branch auto-removed on session exit
- **Changes/commits exist:** Claude prompts to keep or remove
- Manual: `git worktree remove .claude/worktrees/<name>`
- Recommendation: add `.claude/worktrees/` to `.gitignore`

### 2.4 EnterWorktree Internal Tool

`EnterWorktree` is Claude Code's internal tool that switches a session's CWD into a worktree. Key behaviors:
- Appears in the permission system — can be added to deny list via `/permissions`
- No corresponding `ExitWorktree` tool yet (Issue #29436, open)
- CWD is locked to worktree for the session; `cd` via Bash reverts after each command

### 2.5 WorktreeCreate and WorktreeRemove Hooks

Shipped in v2.1.50. Replaces default `git worktree` behavior for non-git VCS (SVN, Perforce, Mercurial):

**WorktreeCreate** — fires on `--worktree` or `isolation: worktree` spawn:
```json
{
  "session_id": "abc123",
  "hook_event_name": "WorktreeCreate",
  "name": "feature-auth"
}
```
Hook MUST print absolute path of created directory to stdout.

**WorktreeRemove** — fires on session exit:
```json
{
  "session_id": "abc123",
  "hook_event_name": "WorktreeRemove",
  "worktree_path": "/path/to/.claude/worktrees/feature-auth"
}
```

Only `type: "command"` hooks supported (not HTTP/prompt/agent).

### 2.6 Agent Teams + Worktrees: The Gap

**Agent Teams** (TeamCreate + Task with team_name) do **NOT** provide automatic worktree isolation. Teammates share the same filesystem. Parallelism is achieved through non-overlapping file paths and message coordination.

To get worktree isolation with Agent Teams, each teammate's custom agent definition would need `isolation: worktree` in its frontmatter — this would give each teammate its own branch+directory.

Sources:
- [Common workflows — Claude Code Docs](https://code.claude.com/docs/en/common-workflows)
- [Hooks reference — Claude Code Docs](https://code.claude.com/docs/en/hooks)
- [Issue #29436 — Add ExitWorktree tool](https://github.com/anthropics/claude-code/issues/29436)
- [Boris Cherny announcement — Threads](https://www.threads.com/@boris_cherny/post/DVAAnexgRUj/introducing-built-in-git-worktree-support-for-claude-code-now-agents-can-run-in)

---

## 3. Per-Tool Breakdown

### 3.1 Aider (paul-gauthier/Aider-AI/aider)

**Confidence: HIGH** — from official docs and issue tracker

| Dimension | Behavior |
|-----------|----------|
| Isolation | None — operates directly in current directory |
| Worktree support | None built-in; users DIY by launching aider in worktree dirs |
| Parallel agents | Not supported (log overwrite, stale files, diff mixing — Issue #302) |
| Commit strategy | **Auto-commit after every LLM edit** (Conventional Commits via weak model) |
| Branch creation | None — operates on current branch only |
| Rollback | `/undo` = `git reset --hard HEAD^` (only undoes aider's last commit) |
| PR creation | None built-in |

Key behaviors:
- Commits dirty files first (pre-AI-edit), then auto-commits AI changes — keeps human/AI commits separated
- Attribution: `(aider)` appended to author/committer name
- `--no-auto-commits` flag disables but known bug (Issue #101) still commits on new file creation
- One repo at a time; `/read` for read-only files from a second repo

Sources:
- [Aider Git Integration](https://aider.chat/docs/git.html)
- [Aider Options Reference](https://aider.chat/docs/config/options.html)
- [Issue #302 — Multiple instances](https://github.com/Aider-AI/aider/issues/302)

---

### 3.2 SWE-agent (princeton-nlp/SWE-agent)

**Confidence: HIGH** — from source code (sweagent/environment/repo.py) and docs

| Dimension | Behavior |
|-----------|----------|
| Isolation | **Docker container** per run (`--rm` flag destroys on exit) |
| Worktree support | None — Docker IS the isolation |
| Parallel agents | Via separate containers |
| Commit strategy | **No commits** — patch-only output via `git diff --cached` |
| Branch creation | None during agent run |
| Rollback | Discard container and re-run from `base_commit` |
| PR creation | `--actions.open_pr` flag |

Repo setup methods inside container:
- **GitHub repos:** `git fetch --depth 1` (shallow clone to `base_commit`)
- **Local repos:** `deployment.runtime.upload()` transfer into container
- **Pre-existing:** Use repo already present in deployment

Submit tool flow:
```bash
git add -A
git diff --cached  # generates the patch
```
Patch saved to host filesystem. Known issue: `git add -A` includes config file changes (pyproject.toml etc.) causing spurious diffs — fix is selective exclusion.

Sources:
- [SWE-agent GitHub](https://github.com/SWE-agent/SWE-agent)
- [mini-swe-agent Issue #528](https://github.com/SWE-agent/mini-swe-agent/issues/528)

---

### 3.3 OpenHands (All-Hands-AI/OpenHands, formerly OpenDevin)

**Confidence: HIGH** — from Docker sandbox docs and SDK paper (arxiv 2511.03690)

| Dimension | Behavior |
|-----------|----------|
| Isolation | **Docker container** per session (OH runtime image built on user-provided base) |
| Worktree support | None — container IS the isolation boundary |
| Parallel agents | Multiple containers side-by-side (SaaS multi-tenancy) |
| Commit strategy | Agent commits and pushes from within container |
| Workspace abstraction | LocalWorkspace (host FS thin wrapper) or DockerWorkspace/RemoteWorkspace |

Sources:
- [Docker Sandbox — OpenHands Docs](https://docs.openhands.dev/openhands/usage/sandboxes/docker)
- [OpenHands Software Agent SDK (arxiv)](https://arxiv.org/html/2511.03690v1)
- [Docker Runtime — OpenHands Docs](https://docs.openhands.dev/modules/usage/architecture/runtime)

---

### 3.4 Devin (Cognition AI)

**Confidence: MEDIUM** — from blog posts; internal implementation proprietary

| Dimension | Behavior |
|-----------|----------|
| Isolation | **Ephemeral cloud VM/sandbox** per task ("cloud laptop") |
| Worktree support | None — sandbox boundary IS the isolation |
| Parallel agents | **MultiDevin** (Devin 2.0, Sept 2024): manager Devin spawns team of parallel Devins, each in its own sandbox |
| Commit strategy | Feature branch + PR; human reviews |
| Git integration | GitHub/GitLab OAuth; can open branches/PRs, push/pull, observe CI |
| Memory | Vectorized codebase snapshot + full replay timeline per session |

Sources:
- [Devin 2.0 — Cognition AI](https://cognition.ai/blog/devin-2)
- [MultiDevin announcement](https://x.com/i/status/1836866702182863286)

---

### 3.5 Sweep (sweepai/sweep)

**Confidence: MEDIUM** — from source code references and HN discussion

| Dimension | Behavior |
|-----------|----------|
| Isolation | **Server-side clone** per task + ephemeral process |
| Worktree support | None |
| Parallel agents | Independent processes per concurrent issue |
| Commit strategy | All changes to a named branch, then PR |
| Trigger model | GitHub webhook (issue labels, PR comments) |
| Validation | "Sandbox" runs formatters/linters/tests after each file edit |

Current status: Sweep pivoted to JetBrains AI assistant; original GitHub bot architecture described above.

Sources:
- [GitHub — sweepai/sweep](https://github.com/sweepai/sweep)
- [Launch HN: Sweep (YC S23)](https://news.ycombinator.com/item?id=36987454)

---

### 3.6 GitHub Copilot (Coding Agent + VS Code Background Agents)

**Confidence: HIGH** — from official Microsoft/GitHub docs

Two distinct products with different isolation strategies:

**Copilot Coding Agent (GA since Sept 2025):**
| Dimension | Behavior |
|-----------|----------|
| Isolation | **GitHub Actions ephemeral runner** per task |
| Branch restriction | `copilot/` prefix enforced at GitHub API layer |
| Parallel agents | Each issue gets its own runner + `copilot/` branch |
| Commit strategy | Draft PR created immediately with empty commit; incremental commits as agent works |
| Merge | Human approval required (separation of duties enforced) |

**VS Code Background Agents (v1.107, Nov 2025):**
| Dimension | Behavior |
|-----------|----------|
| Isolation | **Git worktree** per session (`/project/.worktrees/session-abc123`) |
| Parallel agents | Multiple concurrent agents in separate worktrees |
| Commit strategy | Commit per turn |
| Merge | User applies worktree changes to main workspace; VS Code auto-merges; conflicts via native UI |
| Known issues | Data loss bug (#289973), worktree proliferation (#296194) |

Sources:
- [Copilot coding agent — GitHub Docs](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent)
- [VS Code Background agents](https://code.visualstudio.com/docs/copilot/agents/background-agents)
- [VS Code November 2025 release notes](https://code.visualstudio.com/updates/v1_107)

---

### 3.7 Cursor Parallel Agents (v2.0, October 2025)

**Confidence: HIGH**

| Dimension | Behavior |
|-----------|----------|
| Isolation | **Git worktree per agent** (1:1 mapping) |
| Worktree location | `<repo>/.worktrees/<session-id>` |
| Inheritance | Worktree inherits all new/edited files from primary working tree at creation |
| Parallel limit | Hard limit: 20 worktrees per workspace |
| Merge strategy | Automatic clean merge attempted first; for Best-of-N: user chooses "Full Overwrite" or "Merge" via conflict UI |
| Commit strategy | Agent commits to worktree branch |

Sources:
- [Cursor Parallel Agents Docs](https://cursor.com/docs/configuration/worktrees)
- [Dev.to: Git Worktrees — Power Behind Cursor's Parallel Agents](https://dev.to/arifszn/git-worktrees-the-power-behind-cursors-parallel-agents-19j1)

---

### 3.8 Cline (formerly claude-dev, VS Code extension)

**Confidence: HIGH** — from official Cline docs and DeepWiki code analysis

| Dimension | Behavior |
|-----------|----------|
| Isolation | **Shadow git repository** using `core.worktree` config (NOT `git worktree add`) |
| Git integration | Checkpoints: commits to shadow repo after every single tool use |
| Commit strategy | Shadow repo only — never touches user's git history |
| Worktree support | Uses `core.worktree` config trick, not linked worktrees |
| Auto-push | Never |

**Key innovation: Shadow Git Repository**

Cline maintains a **hidden shadow git repository** in VS Code's global extension storage, completely separate from the project's `.git`:
- Storage: `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\checkpoints\<workspace-id>\.git`
- Uses Git's `core.worktree` config so the shadow repo tracks files in the actual workspace directory **without requiring file copies**
- Commit format: `checkpoint-<workspace-id>-<task-id>`
- Commits every tool use (edit 3 files → 3 checkpoint commits)
- **Temporarily renames nested `.git` dirs to `.git_disabled`** during commits (handles submodules)
- All checkpointing is local only; cleaned up manually

This is architecturally distinct from `git worktree` — it's a single repo configured with `core.worktree` pointing to a different directory.

Sources:
- [Cline Checkpoints Docs](https://docs.cline.bot/core-workflows/checkpoints)
- [DeepWiki: Cline Checkpoint System](https://deepwiki.com/hyhfish/cline/7.1-checkpoint-system)
- [Cline GitHub wiki](https://github.com/cline/cline/wiki)

---

### 3.9 Continue (continue-dev/continue)

**Confidence: MEDIUM** — from docs and search results

| Dimension | Behavior |
|-----------|----------|
| Isolation | None built-in; agents operate on current branch |
| Git integration | Agents defined in `.continue/checks/` (version-controlled markdown files) |
| CI integration | Runs agents on PRs as GitHub status checks |
| Worktree support | None native |

Sources:
- [Continue Docs — Agent mode](https://docs.continue.dev/ide-extensions/agent/quick-start)
- [Continue GitHub](https://github.com/continuedev/continue)

---

### 3.10 Mentat (mentat-ai/mentat)

**Confidence: LOW** — sparse documentation on git strategy

| Dimension | Behavior |
|-----------|----------|
| Git requirement | Requires git repo to be initialized |
| Worktree support | None documented |
| Multi-file editing | Cross-file coordination without manual copy-paste |
| Parallel support | Not documented |

---

## 4. Community Projects: Real Implementations

### 4.1 `ccswarm` (nwiizo) — Rust orchestrator

**Repo:** [github.com/nwiizo/ccswarm](https://github.com/nwiizo/ccswarm)
**Pattern:** Channel-based orchestration, zero shared state

```
ProactiveMaster (Type-State) → Agent Pool → Git Worktrees → Results
       ↓
Channel-Based Coordination (zero locks, message passing via Rust channels)
```

Agent types: Frontend, Backend, DevOps, QA — each in its own worktree.

**Known limitation:** ParallelExecutor exists but not wired in v0.4.3 — tasks run sequentially in the current implementation.

---

### 4.2 `parallel-code` (johannesjo) — Electron multi-agent GUI

**Repo:** [github.com/johannesjo/parallel-code](https://github.com/johannesjo/parallel-code) (287 stars)
**Motto:** "Five agents, five features, one repo. Merge back to main when you're done."

On task creation:
1. New git branch from main
2. `git worktree add` creates separate directory
3. `node_modules` and gitignored dirs **symlinked** (avoids disk multiplication)
4. Agent (Claude Code, Codex, or Gemini) runs in complete isolation

Keyboard-centric: `Ctrl+N` (new task), `Ctrl+Shift+M` (merge). Includes a `CLAUDE.md` in the repo.

---

### 4.3 `ccpm` (automazeio) — GitHub Issues-driven orchestration

**Repo:** [github.com/automazeio/ccpm](https://github.com/automazeio/ccpm)

Most architecturally sophisticated community implementation. Uses GitHub Issues as source of truth.

**Parallel execution strategy** — a single issue "explodes" into 4-5 parallel work streams:
- Agent 1: Database schema and migrations
- Agent 2: Service layer and business logic
- Agent 3: API endpoints and middleware
- Agent 4: UI components
- Agent 5: Test suites and documentation

Tasks marked `parallel: true` designed to modify non-overlapping file regions. Issue comments enable agent-to-agent communication asynchronously.

```
.claude/
├── CLAUDE.md            # always-on instructions
├── agents/              # task-oriented agents
├── commands/pm/         # slash commands
├── context/             # project-wide context
├── epics/               # local workspace (gitignored)
│   └── [epic-name]/
│       ├── epic.md
│       └── [#].md       # individual task files
└── prds/                # product requirement docs
```

---

### 4.4 `parallel-cc` (frankbria) — SQLite-coordinated worktrees

**Repo:** [github.com/frankbria/parallel-cc](https://github.com/frankbria/parallel-cc)

Novel coordination mechanisms:
- **SQLite session tracking** with heartbeat monitoring
- **File Claims**: Agents declare exclusive or shared access to files before modifying
- **AST-based conflict detection** with AI-generated resolution suggestions + confidence scores
- **Stale session cleanup**: Sessions inactive 10+ min auto-detected and cleaned

---

### 4.5 `agent-orchestrator` (ComposioHQ) — Plugin-based autonomous loop

**Repo:** [github.com/ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator)

Six-stage flow: workspace isolation → runtime launch → agent init → execution → reaction handlers → human notification

Plugin-based with swappable slots:
```
Runtime:    tmux | Docker | Kubernetes | process
Agent:      Claude Code | Codex | Aider
Workspace:  worktree | clone
Tracker:    GitHub | Linear
Notifier:   desktop | Slack | webhooks
```

**Autonomous feedback loop:** CI failure logs feed back to agent automatically — no human in loop until merge-readiness.

---

### 4.6 `parallel-worktrees` skill (spillwavesolutions) — Claude Code skill

**Repo:** [github.com/spillwavesolutions/parallel-worktrees](https://github.com/spillwavesolutions/parallel-worktrees)

Six named patterns:

| Pattern | Description |
|---------|-------------|
| **Competitive Implementation** | N agents tackle same task; pick best solution |
| **Divide and Conquer** | Split feature into independent parallel tracks |
| **Redundant Safety Net** | Multiple agents back up critical changes |
| **Exploration Sprint** | Competing architectural approaches |
| **Test-First Parallel** | One agent writes tests while others implement |
| **Review Pipeline** | Separate implementation from fresh-eyes review |

Scripts: `spawn-parallel.sh`, `cleanup-worktrees.sh`, `sync-worktrees.sh`

Two execution modes:
1. **Interactive Parallel**: Multiple terminals, each running Claude independently
2. **Background Orchestration**: Main agent delegates via `Task` tool while continuing its own work

---

### 4.7 `agenttools/worktree` — Agent archetype model

**Repo:** [github.com/agenttools/worktree](https://github.com/agenttools/worktree)

Uses **specialized agent archetypes** per worktree:
- **Architect**: High-level design decisions
- **Detective**: Debugging and investigation
- **Craftsman**: Implementation quality
- **Explorer**: Alternative approaches
- **Aesthete**: Code style and readability

Generates `WORKTREE_COORDINATION.md` alongside `CLAUDE.md` for multi-agent spawns — lists role assignments and file ownership. Both auto-added to `.gitignore`.

---

### 4.8 Notable HN Projects

- **Wtx** ([HN #47156674](https://news.ycombinator.com/item?id=47156674)): `wtx checkout mybranch` auto-creates worktree and opens Claude
- **Gwt-Claude** ([HN #46384500](https://news.ycombinator.com/item?id=46384500)): Shell scripts `gwt-create` / `gwt-switch`
- **Branchlet** ([HN #45054144](https://news.ycombinator.com/item?id=45054144)): TUI for managing Claude Code/Cursor/Codex worktrees
- **Agentastic.dev** ([HN #46501758](https://news.ycombinator.com/item?id=46501758)): Ghostty + git worktrees = one task = one worktree = one terminal session
- **AppleScript orchestrator** ([HN #46578028](https://news.ycombinator.com/item?id=46578028)): State machine `NEEDS_INIT → WORKING → PR_OPEN → MERGED`, spawns iTerm2 tabs

---

## 5. Common Patterns (Ecosystem Convergence)

| Pattern | Prevalence | How Used |
|---------|------------|----------|
| `git worktree add` per agent | **Universal** for local tools | Every project uses this as isolation primitive |
| tmux for terminal multiplexing | **Very common** | ccswarm, parallel-cc, agent-orchestrator, agenttools/worktree |
| `CLAUDE.md` in each worktree | **Common** | Usually shared via symlink or copy from repo root |
| PR-based merge, human review | **Universal** | Auto-merge not trusted for agent output |
| Branch per agent/task | **Universal** | Even container-based tools create dedicated branches |
| VM/container for hosted tools | **Universal** (hosted) | Devin, Copilot Coding Agent, OpenHands |
| Git worktrees for local tools | **Universal** (local) | Claude Code, Cursor, VS Code Background Agents |
| Auto-commit per change | **Aider-specific** | Aider commits every LLM edit; most others don't auto-commit |
| Patch-based output (no commit) | **SWE-agent-specific** | Docker container → git diff → patch file |
| `isolation: worktree` frontmatter | **Official (Feb 2026)** | Claude Code's built-in declarative worktree isolation |

---

## 6. Unique Innovations Worth Adopting

### 6.1 `isolation: worktree` in Agent Frontmatter (Anthropic — ADOPT NOW)

The cleanest DX in the ecosystem. Declarative worktree isolation that auto-cleans on completion:
```yaml
---
isolation: worktree
---
```
**Recommendation:** Add to `chunk-coder` agent definition to give each parallel coder its own worktree.

### 6.2 File Claims System (`parallel-cc` — WORTH IMPLEMENTING)

Agents declare exclusive/shared file access before modifying. AST-based conflict detection. Prevents the "both agents rewrote auth.py" problem. Could be implemented as a simple JSON registry:
```json
{
  "src/core/processor.py": { "owner": "chunk-coder-01", "mode": "exclusive" },
  "tests/test_processor.py": { "owner": "chunk-coder-01", "mode": "exclusive" }
}
```

### 6.3 Agent Archetypes per Worktree (`agenttools/worktree` — CONSIDER FOR EXPLORATION)

Specialized personas (Detective for debugging, Explorer for alternatives). Useful for the exploration phase where different cognitive approaches matter.

### 6.4 `WORKTREE_COORDINATION.md` (`agenttools/worktree` — USEFUL FOR PLAN PHASE)

Coordination file auto-generated for multi-agent spawns listing role assignments and file ownership. Complements `CLAUDE.md`.

### 6.5 Competitive Implementation (`parallel-worktrees` skill — USEFUL FOR HIGH-STAKES CHUNKS)

Deliberately exploit LLM non-determinism: N agents write the same chunk differently, pick the best. High cost, high quality. Appropriate for critical/complex chunks.

### 6.6 Beat Model (`Claude-Code-Workflow` repo — ADOPT FOR ORCHESTRATION)

Coordinator wakes only on callbacks, not polling. Workers execute multi-phase pipelines autonomously. Simple successors advance without coordinator roundtrips. Minimizes orchestration overhead.

### 6.7 Node_modules Symlinking (`parallel-code` — ADOPT FOR JS PROJECTS)

For JavaScript projects: symlink `node_modules` and other gitignored build dirs across worktrees to avoid disk multiplication. Huge disk savings.

### 6.8 Autonomous CI Feedback Loop (`agent-orchestrator` — ASPIRE TO)

CI failure logs feed back to agent automatically. No human in the loop until merge-readiness. Currently requires infrastructure (GitHub Actions integration).

---

## 7. Detailed Claude Code Native Support Breakdown

### What's Built-In (No Code Required)

| Feature | Status | How to Use |
|---------|--------|-----------|
| `--worktree <name>` CLI flag | ✅ Shipped v2.1.49 | `claude --worktree chunk-01` |
| Auto-named worktrees | ✅ | `claude --worktree` |
| tmux integration | ✅ | `claude --worktree chunk-01 --tmux` |
| `isolation: worktree` in agent frontmatter | ✅ Shipped v2.1.49 | Add to agent YAML front matter |
| Auto-cleanup (no changes) | ✅ | Automatic on session exit |
| WorktreeCreate hook (non-git VCS) | ✅ Shipped v2.1.50 | Configure in .claude/settings.json |
| WorktreeRemove hook | ✅ Shipped v2.1.50 | Configure in .claude/settings.json |
| Mid-session worktree creation | ✅ | Ask Claude "start a worktree" |

### What We Need to Build

| Feature | Gap | Recommended Approach |
|---------|-----|---------------------|
| File ownership registry | Not built-in | Simple JSON file per-feature tracking which chunk-coder owns which files |
| Post-merge synthesis | Not built-in | Orchestrator reads all worktree branches after chunk-coders complete, dispatches merge |
| Conflict detection across active worktrees | Not built-in | Pre-spawn analysis of file overlap across chunks |
| Worktree lifecycle in session logs | Not built-in | Add `worktree_path` and `worktree_branch` fields to session-log.schema.json |

### What's NOT Built-In (By Design)

- **Agent Teams do NOT automatically use worktrees** — they share the filesystem
- **No ExitWorktree tool** — once in a worktree, you stay there (Issue #29436)
- **No automatic merge back to main** — user/orchestrator must manage merging

---

## 8. Recommendations for This System

### Priority 1: Add `isolation: worktree` to chunk-coder agents

The single highest-impact change. Gives each parallel `chunk-coder` its own branch + working directory. Zero configuration overhead.

In the chunk-coder agent file (`.claude/agents/chunk-coder.md`):
```yaml
---
name: chunk-coder
isolation: worktree
---
```

Or, when spawning via `Task` tool:
```python
Task(
  subagent_type="chunk-coder",
  isolation="worktree",
  team_name="feature-workflow",
  ...
)
```

### Priority 2: Add worktree fields to session logs

Extend `session-log.schema.json` with:
```json
{
  "worktree": {
    "path": ".claude/worktrees/chunk-01",
    "branch": "worktree-chunk-01",
    "base_commit": "abc123"
  }
}
```

### Priority 3: Implement file ownership registry

Before spawning parallel chunk-coders, the plan-architect should document which files each chunk touches. Orchestrator writes a registry:
```json
{
  "chunk-01": {
    "owns": ["src/processor.py", "tests/test_processor.py"],
    "reads": ["src/base.py"]
  },
  "chunk-02": {
    "owns": ["src/selector.py", "tests/test_selector.py"],
    "reads": ["src/base.py", "src/processor.py"]
  }
}
```
Chunks that `reads` from another chunk's `owns` must wait until that chunk is committed.

### Priority 4: Post-chunk merge protocol

After all parallel chunk-coders complete, orchestrator should:
1. Check each worktree branch for conflicts with main
2. If no conflicts: auto-merge all worktree branches to main sequentially
3. If conflicts: present to user with diff for each conflict
4. Run quality gate on merged result before scribe commits

### Priority 5: Add `.claude/worktrees/` to `.gitignore`

Add immediately to prevent worktree contents appearing as untracked files.

---

## 8b. Additional Tools: Windsurf, Kiro, Amazon Q

### Windsurf (formerly Codeium) — Wave 13 (December 2025)

**Confidence: MEDIUM** — from changelog and news coverage

| Dimension | Behavior |
|-----------|----------|
| Isolation | **Native git worktrees** added in Wave 13 |
| Parallel agents | Multi-pane Cascade UI with separate worktrees per agent |
| Worktree support | Yes — added Dec 2025 |
| Auto-commit timing | Not documented |

Prior to Wave 13, Cascade agent worked directly in the workspace with no isolation.

Sources:
- [Windsurf Wave 13 Blog](https://windsurf.com/blog/windsurf-wave-13)
- [Neowin coverage](https://www.neowin.net/news/windsurf-wave-13-introduces-the-new-swe-15-model-and-git-worktrees/)

---

### Kiro (Amazon — successor to Amazon Q Developer CLI)

**Confidence: MEDIUM-HIGH** — from official Kiro docs

| Dimension | Behavior |
|-----------|----------|
| Isolation | **Shadow bare git repository** for checkpointing |
| Worktree support | None — uses bare repo approach |
| Auto-commit | To shadow repo only; never touches user's git |
| Restore modes | Soft (preserve new files) or Hard (delete new files) |
| Session scope | Shadow repo auto-cleaned when chat session ends |

Architecture: bare repo + per-turn and per-tool-level snapshots. Similar conceptually to Cline's shadow git but implemented as a bare repo rather than using `core.worktree`.

Sources:
- [Kiro Checkpointing Docs](https://kiro.dev/docs/cli/experimental/checkpointing/)

---

### Taxonomy of Git Isolation Strategies (2024-2026)

| Strategy | Tools | How It Works | Uses `git worktree`? |
|----------|-------|--------------|---------------------|
| **Shadow repo** (core.worktree) | Cline | Hidden `.git` in ext storage; commits every tool call | No |
| **Shadow bare repo** | Kiro | Bare repo for per-turn snapshots | No |
| **Native git worktrees** (auto-managed) | Cursor, VS Code Copilot BG agents, Windsurf Wave 13, Claude Code, ccswarm | IDE/tool creates `git worktree add` per session | Yes |
| **PR-as-isolation** | GitHub Copilot Coding Agent, Continue cloud | `copilot/` branch + PR; human reviews | No |
| **VM/container-per-task** | Devin, OpenHands, Copilot Coding Agent (runner) | Full OS isolation | No |
| **Patch-only output** | SWE-agent | `git diff --cached` → patch file; no commits | No |
| **Direct branch commits** | Aider | Auto-commits to current branch every LLM edit | No |
| **No git management** | Mentat, Aide | User handles all git | No |

---

## 9. What Was NOT Found (Honest Gaps)

- **No public `chunk-coder` repos** with worktree patterns exist. This project's agent design is novel.
- **No public repos with the exact CLAUDE.md pattern** in this repo (orchestrator as behavioral mode, TeamCreate/SendMessage JSON protocol, five message types). The closest analogues are `ccpm` (GitHub Issues coordination) and `agenttools/worktree` (WORKTREE_COORDINATION.md).
- **No Reddit threads** on this exact combination of topics. HN is the primary community venue.
- **Cline, Continue, Mentat, Aide** have no worktree-native support. They rely on users manually setting up worktrees or VS Code extensions.

---

## 10. Summary Comparison Table

| Tool | Isolation Mechanism | Worktrees? | Parallel Agents | Commit Strategy | Confidence |
|------|--------------------|----|----|----|---|
| **Claude Code CLI** | Git worktree per session | ✅ Native (v2.1.49) | Via `isolation: worktree` | Per-worktree commits | HIGH |
| **Cursor** | Git worktree per agent | ✅ Native | Up to 8 agents | Per-worktree commits | HIGH |
| **VS Code Background Agents** | Git worktree per session | ✅ Native | Multiple concurrent | Commit per turn | HIGH |
| **GitHub Copilot Coding Agent** | GitHub Actions runner per task | ❌ | Multiple runners | `copilot/` branch + draft PR | HIGH |
| **OpenHands** | Docker container per session | ❌ | Multiple containers | Agent commits in container | HIGH |
| **Devin** | Ephemeral cloud VM per task | ❌ | MultiDevin (separate VMs) | Feature branch + PR | MEDIUM |
| **Sweep** | Server-side clone per task | ❌ | Independent processes | Branch + PR per task | MEDIUM |
| **SWE-agent** | Docker container per run | ❌ | Separate containers | No commits; patch file only | HIGH |
| **Aider** | None (direct repo) | ❌ | Not supported | Auto-commit every LLM edit | HIGH |
| **Cline** | None | ❌ | Not supported | User-directed | MEDIUM |
| **Continue** | None | ❌ | Not supported | N/A (PR-based checks) | MEDIUM |
| **ccswarm** | Git worktree per agent role | ✅ | Specialized pools (sequential in practice) | Per-role branch commits | MEDIUM |
| **parallel-code** | Git worktree per task | ✅ | 5 agents via Electron GUI | Branch per task, manual merge | HIGH |
| **ccpm** | Git worktree per stream | ✅ | 4-5 parallel streams per issue | Branch per stream, PR | HIGH |
| **parallel-cc** | Git worktree + SQLite | ✅ | Multiple concurrent | Branch → PR or direct merge | HIGH |

---

## Sources

**Official Documentation:**
- [Claude Code Common Workflows](https://code.claude.com/docs/en/common-workflows)
- [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks)
- [Aider Git Integration](https://aider.chat/docs/git.html)
- [SWE-agent Repository](https://github.com/SWE-agent/SWE-agent)
- [OpenHands Docker Sandbox](https://docs.openhands.dev/openhands/usage/sandboxes/docker)
- [OpenHands Software Agent SDK (arxiv)](https://arxiv.org/html/2511.03690v1)
- [GitHub Copilot Coding Agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent)
- [VS Code Background Agents](https://code.visualstudio.com/docs/copilot/agents/background-agents)
- [Cursor Parallel Agents](https://cursor.com/docs/configuration/worktrees)
- [Devin 2.0 Announcement](https://cognition.ai/blog/devin-2)

**Community Projects:**
- [nwiizo/ccswarm](https://github.com/nwiizo/ccswarm)
- [johannesjo/parallel-code](https://github.com/johannesjo/parallel-code)
- [automazeio/ccpm](https://github.com/automazeio/ccpm)
- [frankbria/parallel-cc](https://github.com/frankbria/parallel-cc)
- [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator)
- [spillwavesolutions/parallel-worktrees](https://github.com/spillwavesolutions/parallel-worktrees)
- [agenttools/worktree](https://github.com/agenttools/worktree)
- [stravu/crystal](https://github.com/stravu/crystal) (deprecated → Nimbalyst)
- [coplane/par](https://github.com/coplane/par)
- [coderabbitai/git-worktree-runner](https://github.com/coderabbitai/git-worktree-runner)

**Community Articles:**
- [Upsun: Git worktrees for parallel AI coding agents](https://devcenter.upsun.com/posts/git-worktrees-for-parallel-ai-coding-agents/)
- [Nx Blog: How Git Worktrees Changed My AI Agent Workflow](https://nx.dev/blog/git-worktrees-ai-agents)
- [Agent Interviews: Parallel AI Coding with Git Worktrees](https://docs.agentinterviews.com/blog/parallel-ai-coding-with-gitworktrees/)
- [incident.io: Shipping faster with Claude Code and Git Worktrees](https://incident.io/blog/shipping-faster-with-claude-code-and-git-worktrees)
- [Nick Mitchinson: Using Git Worktrees for Multi-Feature Development with AI Agents](https://www.nrmitchi.com/2025/10/using-git-worktrees-for-multi-feature-development-with-ai-agents/)
- [Boris Cherny: Claude Code worktree announcement](https://www.threads.com/@boris_cherny/post/DVAAnexgRUj/)

**GitHub Issues:**
- [Claude Code Issue #29436 — Add ExitWorktree tool](https://github.com/anthropics/claude-code/issues/29436)
- [Claude Code Issue #27590 — Multi-repo worktree-aware collaboration](https://github.com/anthropics/claude-code/issues/27590)
- [Aider Issue #302 — Multiple instances parallel workflow](https://github.com/Aider-AI/aider/issues/302)

**HN Discussions:**
- [HN #46591395 — Claude skill for managing worktrees](https://news.ycombinator.com/item?id=46591395)
- [HN #46578028 — Claude Code Orchestrator parallel AI](https://news.ycombinator.com/item?id=46578028)
- [HN #44178216 — Multiple Claude Code agents via Git worktrees](https://news.ycombinator.com/item?id=44178216)
- [HN #47156674 — Wtx: Git worktrees for parallel AI agents](https://news.ycombinator.com/item?id=47156674)
