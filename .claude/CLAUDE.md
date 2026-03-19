# Project Rules

## Multi-Agent TDD Workflow (Agent Teams)

This project uses a **multi-agent TDD workflow** where the main Claude Code session acts as **orchestrator/team lead** when workflow mode is activated. The workflow breaks features into testable chunks implemented via Test-Driven Development. The orchestrator is not a separate agent — it is a behavioral mode of the main session. It creates an Agent Team, spawns teammates, and coordinates via structured `SendMessage` JSON payloads.

### Activation

To activate the workflow, say:
- **"start workflow"** or **"@orchestrator"**
- **"plan [feature]"**, **"implement [feature]"**, **"explore codebase"**

When NOT in workflow mode, Claude operates normally without orchestration.

### Quick Reference

| Command | What Happens |
|---------|--------------|
| `start workflow` | Enter orchestrated mode, check for active sessions |
| `explore codebase` | Spawn codebase-explorer teammate(s) |
| `research {topic}` | Spawn researcher teammate for docs/API lookup |
| `plan {feature}` | Spawn plan-architect teammate |
| `implement chunk-XX` | Spawn chunk-coder teammate |
| `approved` | Spawn scribe teammate, offer next chunk |
| `stop workflow` | Return to normal Claude mode |

### Agent Teams

The workflow uses Agent Teams with **delegate mode**:
- **Lead** (main session as orchestrator): Creates team via TeamCreate, spawns teammates via Task with `team_name`. Coordinates via SendMessage. Never writes code or reads content files. User talks directly to the lead.
- **Teammates**: Write files directly, have native MCP access, communicate via structured messages.
- **tmux layout**: Teammates share one window — main pane (65%) on the left, others stacked right. Click a pane + `Ctrl-a m` to promote it to main. `Ctrl-a z` to zoom fullscreen.
- **Message protocol**: See `.claude/protocols/team-messaging.md` for all 5 message types.
- **Context pressure**: Teammates auto-replace via handoff protocol. The main session (orchestrator) uses context compression after saving state — see orchestrator.md for details.

### Workflow Phases

```
1. Exploration → Teammates write .claude/context/*.json directly
2. Planning    → Teammate writes .claude/plans/{feature}-plan.json
3. Implementation → TDD per chunk, teammates write session logs
4. Commit      → Lead dispatches scribe after user approval, scribe commits directly
```

### File Locations

| Purpose | Path |
|---------|------|
| Codebase context | `.claude/context/_codebase.json` |
| Feature context | `.claude/context/{feature}-context.json` |
| Query results | `.claude/context/queries/{topic}.json` |
| Research results | `.claude/research/{topic}.json` |
| Design docs | `.claude/designs/{feature}.md` |
| Plans | `.claude/plans/{feature}-plan.json` |
| Session logs | `.claude/logs/{feature}-{chunk}-log.json` |
| Planning logs | `.claude/logs/{feature}-planning-log.json` |
| Memory database | `.claude/memory/memory.db` |
| Schemas | `.claude/schemas/*.json` |
| Message protocol | `.claude/protocols/team-messaging.md` |

### Agents

| Agent | Purpose | Mode | Activation |
|-------|---------|------|------------|
| `orchestrator` | Workflow coordination | **Main session** (behavioral spec) | `@orchestrator`, `start workflow` |
| `codebase-explorer` | Context generation | Teammate | Via lead |
| `researcher` | Docs/API research | Teammate | Via lead, `research {topic}` |
| `plan-architect` | Plan creation | Teammate | Via lead |
| `chunk-coder` | TDD implementation | Teammate | Via lead |
| `scribe` | Commits & memory | Teammate | Via lead |

For full orchestrator documentation, see: `.claude/agents/orchestrator.md`

---

## MCP Integration

All agents have native MCP access. No background-mode limitations.

### Agent MCP Usage

| Agent | MCP Servers | Fallback |
|-------|-------------|----------|
| `orchestrator` | Sequential Thinking | — |
| `plan-architect` | Sequential Thinking | — |
| `researcher` | **Context7** (primary) | WebSearch |
| `codebase-explorer` | **GitHub MCP** (github-personal) | `gh` CLI, `git` CLI |
| `chunk-coder` | — | Requests info via lead |
| `scribe` | — | `gh` CLI |

### Sequential Thinking

Used by orchestrator and plan-architect for complex decisions:

**Mandatory decision points:**
- Exploration strategy (single vs parallel teammates)
- Teammate handoff/failure recovery
- Design completeness assessment
- Chunk boundary decisions

### Troubleshooting

**Check MCP status:**
```bash
claude mcp list
```

**Add missing MCP:**
```bash
# Sequential Thinking (required for full workflow)
claude mcp add sequential-thinking -- npx -y @modelcontextprotocol/server-sequential-thinking

# Context7 (primary research tool)
claude mcp add context7 -- npx -y @upstash/context7-mcp

# GitHub (for codebase-explorer history analysis)
claude mcp add github-personal -e GITHUB_TOKEN=xxx -- npx -y @modelcontextprotocol/server-github
```

**Session start shows MCP status** - The SessionStart hook reports which MCPs are connected.

---

## Agent Definitions

Agent files are located in `.claude/agents/`:
- `orchestrator.md` — Team lead, workflow coordinator with model dispatching
- `codebase-explorer.md` — Context packet generation, direct file writes
- `researcher.md` — Documentation/API research via Context7 MCP
- `plan-architect.md` — Chunked planning with test architecture
- `chunk-coder.md` — TDD implementation with parallel awareness
- `scribe.md` — Direct logging, approval-gated commits, memory extraction

---

## Skills

Skills are loaded by agents at startup for specialized workflow knowledge. Core skills are domain-agnostic; domain-specific patterns live in `specializations/` subdirectories within relevant skills.

### Core Skills (New — from migration)

| Skill | Location | Used By | Purpose |
|-------|----------|---------|---------|
| `session-lifecycle` | `.claude/skills/session-lifecycle/` | all agents | Session loading, context pressure, handoff, chunk continuity |
| `tdd-workflow` | `.claude/skills/tdd-workflow/` | chunk-coder | Full TDD cycle: test → fail → implement → pass → gate |
| `workflow-orchestration` | `.claude/skills/workflow-orchestration/` | orchestrator | Workflow phases, model dispatch, parallel coordination, re-exploration |
| `logging-and-commit` | `.claude/skills/logging-and-commit/` | scribe | Parameterized commit flow, persistent scribe lifecycle |
| `research-workflow` | `.claude/skills/research-workflow/` | researcher | Research phases, MCP/WebSearch routing, confidence scoring |
| `multi-perspective-analysis` | `.claude/skills/multi-perspective-analysis/` | plan-architect, chunk-coder | Divergent exploration for multi-approach decisions |
| `push-workflow` | `.claude/skills/push-workflow/` | scribe | User-initiated push with session summary |

### Existing Skills (Pre-migration)

| Skill | Location | Used By | Purpose |
|-------|----------|---------|---------|
| `context-packets` | `.claude/skills/context-packets/` | explorer, planner, coder | Context packet reading/writing, exploration modes |
| `implementation-plans` | `.claude/skills/implementation-plans/` | planner, coder | Chunked plan structure, aggressive context-seeking |
| `plan-adherence` | `.claude/skills/plan-adherence/` | coder | Scope checking, deviation documentation |
| `test-architecture` | `.claude/skills/test-architecture/` | planner | Pass A/B/C test design, patterns reference |
| `git-history-analysis` | `.claude/skills/git-history-analysis/` | explorer (conditional) | Git history for context generation |
| `coding-memory` | `.claude/skills/coding-memory/` | scribe (conditional) | Learning signal extraction, pattern storage |

### Research Skills

| Skill | Location | Used By |
|-------|----------|---------|
| `MCP-research` | `.claude/skills/MCP-research/` | researcher |
| `persistent-research` | `.claude/skills/persistent-research/` | researcher |
| `ephemeral-research` | `.claude/skills/ephemeral-research/` | researcher |

### Domain Specializations

Specialization files extend core skills with domain-specific patterns. They live inside the skill they extend:

```
.claude/skills/{skill-name}/specializations/{domain}.md
```

Agents load specializations based on the Project Domain declaration below. Adding a new domain requires only new specialization files — no core skill changes.

---

## Project Domain

<!-- Set your domain here. Agents load matching specializations/{domain}.md files. -->
<!-- Example: domain: web-app, domain: data-pipeline, domain: robotics-cv -->
domain: <!-- TODO: set your project domain -->

---

## Schemas

Central schemas in `.claude/schemas/`:
- `session-log.schema.json` — Tracks implementation progress, learning signals, and resume fields
- `planning-log.schema.json` — Tracks planning sessions: design doc conversion, user edits, design deviations
- `team-message.schema.json` — All 5 message types for Agent Teams communication
- `design-review.schema.json` — Design completeness review annotations

Skill-local schemas (in each skill's `schemas/` subdirectory):
- `context-packets/schemas/codebase.schema.json` — Codebase context structure
- `context-packets/schemas/feature-context.schema.json` — Feature-specific context
- `context-packets/schemas/query-result.schema.json` — Explorer query results
- `implementation-plans/schemas/implementation-plan.schema.json` — Chunked plan structure
- `coding-memory/schemas/memory.schema.json` — Coding memory patterns
- `persistent-research/schemas/persistent.schema.json` — Persistent research entries
- `ephemeral-research/schemas/ephemeral.schema.json` — Ephemeral research entries
- `persistent-research/schemas/index.schema.json` — Research index
- `git-history-analysis/schemas/history-context.schema.json` — Git history context

---

## Quality Gates

All code must pass before commit. Configure a quality gate script for your project:

```bash
./scripts/gate.sh
```

<!-- TODO: Define your project's quality gate components. Examples: -->
<!-- - Formatter (e.g., ruff format, prettier, gofmt) -->
<!-- - Linter (e.g., ruff check, eslint, golangci-lint) -->
<!-- - Type checker (e.g., pyright, tsc, mypy) -->
<!-- - Tests (e.g., pytest, jest, go test) -->

---

## Learned Rules

### Teammate Communication (HARD RULES — enforced after repeated violations)

1. **No shutdown without user approval.** NEVER send `shutdown_request` to any teammate without first asking the user and receiving explicit "yes". No exceptions — not for "completed", "idle", or "ephemeral" teammates.
2. **No idle-triggered messages.** Idle notifications are automatic turn boundaries (every 10-20 seconds). They are NOT evidence of a problem. Wait at least 3 minutes after dispatching work, then silently check output files before sending any message.
3. **One message per topic.** Never ask a teammate the same question twice. If they didn't respond, wait longer, check files, or tell the user.

### Mistakes to Avoid
<!-- Patterns with frequency >= 5 will be added here by scribe -->

### Coding Preferences
<!-- User preferences with high confidence will be added here -->

### Code Navigation (Token-Efficient)

Each LSP call is a full API round-trip that re-reads the conversation. Use LSP surgically, not by default.

**Always use LSP for:**
- Diagnostics after every edit — fix errors before moving on (automatic, no extra cost)
- Single `hover` for type info when you need it (1 call, small result)
- Single `goToDefinition` when you know the symbol and need the source location

**Prefer Grep/Glob over LSP for:**
- Finding where a symbol is used (Grep is 1 call; `findReferences` is equivalent but no cheaper)
- Bulk exploration (listing files, searching patterns across codebase)
- Text searches (comments, strings, config values, non-code files)

**Avoid multi-call LSP chains.** If a task would require 3+ LSP calls (e.g., `workspaceSymbol` → `goToDefinition` → `findReferences` → `hover`), prefer fewer Grep/Read calls that accomplish the same thing in fewer round-trips. Each saved turn avoids re-reading ~24K+ cached tokens.

**Rule of thumb:** 1 Grep + 1 Read < 3 LSP calls in token cost.

### CLI Output (RTK)

RTK is active globally. All Bash commands are automatically compressed.
- If output seems truncated, use `command <cmd>` to bypass RTK for raw output
- Use `rtk gain` to check token savings statistics
