# Claude Code Sub-Agent Architecture: Comprehensive Research & Best Practices

## Table of Contents

1. [The Execution Hierarchy](#1-the-execution-hierarchy)
2. [Sub-Agent Anatomy: What Goes Into a Definition](#2-sub-agent-anatomy)
3. [Skills + Sub-Agents: The Composition Model](#3-skills--sub-agents)
4. [Granularity: Where Diminishing Returns Hit](#4-granularity-diminishing-returns)
5. [Custom Agents vs. Custom Sub-Agents](#5-custom-agents-vs-custom-sub-agents)
6. [Design Patterns From the Community](#6-design-patterns)
7. [Token Economics & Cost Traps](#7-token-economics)
8. [Invocation Protocol: The 4 Essential Components](#8-invocation-protocol)
9. [Reference Architectures](#9-reference-architectures)
10. [Key Resources & Links](#10-key-resources)

---

## 1. The Execution Hierarchy

Claude Code has three distinct delegation mechanisms, each solving a different coordination problem:

### Single Session (Main Agent)
- You talk to Claude directly. All context accumulates in one window.
- Best for: sequential work, simple tasks, anything under ~30 min of focused effort.

### Sub-Agents (Task Tool)
- One-shot workers spawned by the main agent (or lead). Fresh context, do a job, return a summary.
- The parent receives the final message verbatim — intermediate tool calls stay inside the sub-agent.
- **Cannot spawn other sub-agents.** The Agent/Task tool is stripped from every spawned context.
- Can be resumed by agent ID to continue where they left off (retains full conversation history).

### Agent Teams (Experimental)
- Multiple full Claude Code instances coordinated by a lead. Each teammate has its own context window.
- Teammates can message each other directly — no bottleneck through the lead.
- Shared task list with dependency tracking and self-claiming.
- ~7x token cost vs. single session. ~3-4x for a 3-teammate team.
- Best for: cross-layer coordination, debate/consensus, large-scale parallel work where workers need to communicate.

### Decision Framework

| Need | Use |
|------|-----|
| Quick focused work, no coordination needed | Sub-agent |
| Workers need to share findings mid-task | Agent Team |
| Stateful multi-turn collaboration | Agent Team (teammate) |
| Bounded task with clear input/output contract | Sub-agent |
| Same-file edits, tight dependencies | Single session |
| Independent parallel analysis | Sub-agents (parallel) |
| Cross-layer feature implementation | Agent Team |

**Practical rule from the community:** Sub-agents cover 90% of delegation needs. Only reach for Agent Teams when you hit the coordination bottleneck — workers genuinely need to talk to each other.

---

## 2. Sub-Agent Anatomy

### The File Format

Sub-agents are markdown files in `.claude/agents/` (project) or `~/.claude/agents/` (user-global). Project-level takes precedence on name collision.

```yaml
---
name: code-reviewer
description: Reviews code for quality, security, and best practices. Use after writing or modifying code.
tools: Read, Grep, Glob, Bash
model: sonnet
skills:
  - api-conventions
  - error-handling-patterns
memory: user
---

You are a senior code reviewer.

When invoked:
1. Run git diff to see recent changes
2. Focus on modified files
3. Review for readability, error handling, security, and test coverage

Provide feedback organized by priority:
- Critical (must fix)
- Warnings (should fix)
- Suggestions (nice to have)
```

### Frontmatter Fields Reference

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Identifier. Becomes invocable by name. |
| `description` | Yes | **The most important field.** Claude uses this to decide when to auto-delegate. Include specific trigger phrases. |
| `tools` | No | Comma-separated tool list. Omit = inherit all. Explicitly restrict for safety. |
| `model` | No | `sonnet`, `opus`, `haiku`, or `inherit`. Haiku is 2x faster, 3x cheaper than Sonnet. |
| `skills` | No | List of skill names. Full content is injected into context at startup. Sub-agent doesn't discover them — they're pre-loaded. |
| `memory` | No | `user`, `project`, or `local`. Gives persistent directory across sessions. |
| `hooks` | No | PreToolUse/PostToolUse/Stop hooks scoped to this agent's lifecycle. |
| `background` | No | `true` to always run as background task. |

### What Sub-Agents Inherit vs. Don't

| Receives | Does NOT Receive |
|----------|------------------|
| Its own system prompt (the markdown body) | Parent's conversation history |
| The Agent tool's prompt string | Parent's system prompt |
| Project CLAUDE.md | Skills (unless listed in `skills:` field) |
| Tool definitions (inherited or subset from `tools:`) | Parent's tool results |

**Critical implication:** The ONLY channel from parent to sub-agent is the prompt string. Include file paths, error messages, decisions, and all necessary context directly in that prompt.

### The Description Field: Make or Break

The description is the primary signal Claude uses for auto-delegation. Community consensus:

- Include 3-5 specific trigger phrases users might naturally say
- Be action-oriented: "Processes Excel files and generates reports" not "An Excel helper"
- Write in third person (consistency with system prompt point-of-view)
- Test activation with variations to ensure reliable matching
- If Claude rarely auto-delegates, add explicit instructions in CLAUDE.md:

```markdown
During implementation, delegate tasks to subagents based on expertise:
- Use `code-reviewer` agent for code quality review
- Use `test-runner` agent for test execution and analysis
- Use `db-expert` agent for migration and schema work
```

---

## 3. Skills + Sub-Agents: The Composition Model

Skills and sub-agents aren't separate systems. They compose in two directions:

### Direction 1: Skills INTO Sub-Agents

Use the `skills:` frontmatter field to inject domain expertise into a sub-agent.

```yaml
---
name: api-developer
description: Implement API endpoints following team conventions
skills:
  - api-conventions
  - error-handling-patterns
---

Implement API endpoints. Follow the conventions and patterns from the preloaded skills.
```

The full content of each listed skill is injected at startup. The sub-agent has the knowledge immediately — no discovery step, no extra tool calls to load it. This is how you guarantee a sub-agent has specific domain knowledge.

**When to use this:** When you want a sub-agent that reliably follows specific conventions, patterns, or workflows without needing to read them from disk each invocation.

### Direction 2: Sub-Agents FROM Skills (context: fork)

Use `context: fork` in a skill's frontmatter to run it in an isolated sub-agent context.

```yaml
---
name: deep-analysis
description: Comprehensive codebase analysis
context: fork
agent: Explore
---

Analyze how authentication is implemented across the codebase.
Summarize patterns and identify inconsistencies.
```

When invoked, the skill executes in a separate context. The parent never sees internal reasoning, tool calls, or intermediate steps — only the final summary.

**When to use this:** Skills that generate extensive output, require many file reads, or would pollute the main context. Research tasks, large-scale audits, dependency mapping.

**Gotcha:** `context: fork` only makes sense for skills that contain an actual task. If your skill is just guidelines/conventions, forking it means the agent receives guidelines with nothing to do and returns nothing.

### Rule of Thumb

| Pattern | Who owns system prompt | Role of skills |
|---------|----------------------|----------------|
| `skills:` on sub-agent | Sub-agent | Skills are injected reference knowledge |
| `context: fork` on skill | Skill is the task | Agent type is the execution environment |

If building something new, just make a sub-agent with skills. Use `context: fork` when you already have a skill and want to run it in isolation without duplicating it into a separate agent file.

---

## 4. Granularity: Diminishing Returns

### The Overhead Tax

Every sub-agent invocation has a cost:
- Fresh context initialization (system prompt + CLAUDE.md + tool definitions)
- Context gathering — the sub-agent starts cold and may need to re-read files the parent already read
- The parent pays tokens for the prompt it sends AND the result it receives back
- One GitHub issue reported a task that cost 2-3K tokens in the main session consuming 160K tokens as a sub-agent due to context initialization overhead

### Where Granularity Helps

**Good granularity — bounded, domain-specific roles:**
- Read-only reviewer (Read, Grep, Glob only)
- Test runner (Bash, Read, Grep)
- Security auditor (Read, Grep, Glob, WebSearch)
- Database migration specialist (Read, Write, Edit, Bash)
- Documentation writer (Read, Write, Edit, Glob, WebSearch)

These work because they have clear tool restrictions, a bounded domain, and produce a well-defined output.

**Good granularity — task-shaped, not role-shaped:**
- "Analyze the dependency graph for this module" — bounded, returns a summary
- "Run the test suite and report failures with root cause analysis" — clear output
- "Search for all usages of this deprecated function" — finite, parallelizable

### Where Granularity Hurts

**Too many agents (>5-8 custom definitions):**
- Claude's auto-delegation becomes unreliable — too many descriptions to match against
- You end up needing explicit CLAUDE.md instructions to force routing, defeating the auto-delegation benefit
- Each agent definition adds tokens to the system prompt (name + description for discovery)

**Too narrow agents:**
- A "semicolon-linter" agent that only checks semicolons — the overhead of spawning a fresh context exceeds just having the main agent do it
- Agents that finish in <5 tool calls — the initialization cost dominates

**Agents that need too much context:**
- If the sub-agent needs to read 20 files to understand the task, it's going to burn tokens on context gathering that the parent already had
- Better to keep this in the main session or use a teammate

### The Sweet Spot

Based on community patterns and cost analysis:

- **3-6 custom sub-agents per project** is the practical sweet spot
- Each should save at least 10-15 minutes of manual work per invocation to justify the token overhead
- Each should have a clear tool restriction (not "inherit all")
- Each should produce a bounded output (summary, report, diff — not ongoing work)
- Use Haiku for lightweight read-only agents (90% of Sonnet's capability, 2x speed, 3x cheaper)
- Reserve Sonnet/Opus for agents doing complex reasoning or code generation

### Model Selection Per Agent Type

| Agent Role | Recommended Model | Rationale |
|-----------|-------------------|-----------|
| Code exploration/search | haiku | Read-only, speed matters |
| Code review | sonnet | Needs reasoning about quality |
| Implementation/complex coding | opus or sonnet | Quality-critical |
| Test execution & analysis | sonnet | Needs to interpret failures |
| Documentation | sonnet | Needs good writing |
| Security audit | opus | Needs deep reasoning about vulnerabilities |

---

## 5. Custom Agents vs. Custom Sub-Agents

A common confusion: the file format for agents and sub-agents is identical. Both are markdown files with YAML frontmatter in `.claude/agents/`. The distinction is behavioral — it's about the role the instance plays at runtime.

### As a Top-Level Agent

When you invoke an agent directly (e.g., `--agent code-reviewer` or explicitly asking "Use code-reviewer"), it becomes the primary agent you interact with. It has its own system prompt, tools, and context. You can talk to it directly.

### As a Sub-Agent

When the main agent (or lead) delegates to it via the Task/Agent tool, it runs as a sub-agent: fresh context, does its job, returns a result. Same file, different execution context.

### Custom Agents ON TOP of Sub-Agents

This is the powerful composition pattern for your daemon-based orchestration:

**Layer 1 — Custom Agent as Lead:**
```yaml
---
name: orchestrator
description: Coordinates complex multi-step workflows
tools: Read, Write, Edit, Bash, Glob, Grep, Agent
skills:
  - handoff-protocol
  - task-decomposition
---

You are the orchestration lead. Decompose tasks, delegate to specialist sub-agents,
synthesize results. Never do implementation work directly.
```

**Layer 2 — Custom Sub-Agents as Workers:**
```yaml
---
name: implementation-coder
description: Implements code changes following TDD. Use when code needs to be written or modified.
tools: Read, Write, Edit, Bash, Glob, Grep
model: sonnet
skills:
  - code-review
  - testing-patterns
---

You are an implementation specialist. Write production code that passes the provided tests.
Follow the coding conventions from preloaded skills.
```

The lead agent has the `Agent` tool and can spawn the implementation-coder sub-agent. The sub-agent gets its own context with the `code-review` and `testing-patterns` skills pre-loaded.

### When to Use Each

| Scenario | Use |
|----------|-----|
| You want to talk to the agent directly | Top-level custom agent |
| The lead should auto-delegate based on task type | Custom sub-agent (description-based routing) |
| You need persistent multi-turn interaction | Agent Team teammate |
| Bounded task, clear input/output | Sub-agent |
| Complex workflow orchestration | Custom agent as lead + custom sub-agents as workers |

---

## 6. Design Patterns

### Pattern 1: The PubNub Pipeline (Sequential Handoff)

Established pattern from PubNub's production usage:

1. **pm-spec** — reads enhancement, writes spec, asks clarifying questions
2. **architect-review** — validates design, produces ADR
3. **implementer-tester** — builds and tests
4. **release** — deployment

Each stage uses a SubagentStop hook that reads a queue file and prints the next command. Human-in-the-loop approves each handoff.

**Key insight:** Scope tools per agent. PM & Architect are read-heavy. Implementer gets Edit/Write/Bash. Release gets only what it needs.

### Pattern 2: Parallel Research Fan-Out

```markdown
# Research: $ARGUMENTS

Launch parallel subagents:
1. **Web Documentation Agent** — search official docs, best practices, GitHub issues
2. **Stack Overflow Agent** — similar problems, highly-voted answers, pitfalls
3. **Codebase Explorer** — find existing patterns, related implementations
```

Each sub-agent works independently, returns a summary. Parent synthesizes.

### Pattern 3: Domain-Based Routing

Define in CLAUDE.md:
```markdown
## Domain Parallel Patterns
When implementing features across domains, spawn parallel agents:
- **Frontend agent**: React components, UI state, forms
- **Backend agent**: API routes, server actions, business logic
- **Database agent**: Schema, migrations, queries

Each agent owns their domain. No cross-domain writes.
```

### Pattern 4: Skills as Portable Behavioral Units

With Claude Code 2.1, skills can carry their operational semantics:

```yaml
---
name: guarded-shell
description: Shell with safety checks
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "~/.claude/hooks/validate-shell.sh"
---
```

A skill is no longer just instructions — it's a governed behavioral unit. Distribute it and it carries its safety checks with it.

### Pattern 5: The Progressive Disclosure Stack

From Anthropic's agent design patterns research:

1. **System prompt:** Only skill names + descriptions (minimal tokens)
2. **On relevance match:** Load full SKILL.md (comprehensive but focused)
3. **During execution:** Load helper files, reference docs, scripts as needed

This prevents context bloat. One implementation (ClaudeFast) recovered ~15,000 tokens per session (82% improvement) over loading everything into CLAUDE.md upfront.

---

## 7. Token Economics

### Cost Reality

- Average Claude Code cost: ~$6/developer/day, <$12 for 90% of users
- Agent teams: ~7x tokens vs. single session
- Each sub-agent spawn: variable initialization cost based on tool count and config complexity
- Parallel sub-agents: per-file token cost is unchanged, but you pay for each session simultaneously

### Common Cost Traps

**Over-parallelizing:** Launching 10 parallel agents for a simple feature wastes tokens. Group related micro-tasks into one agent.

**Under-restricting tools:** If you omit the `tools` field, the sub-agent inherits ALL tools. Each tool definition costs tokens. A read-only agent with Read/Grep/Glob has a much smaller context tax than one inheriting 20+ tools.

**Vague invocations:** Sending "implement the feature" instead of specific scope, file references, and expected outputs causes the sub-agent to spend tokens on exploration the parent already did.

**Not using Haiku where appropriate:** Haiku 4.5 delivers 90% of Sonnet's agentic capability at 2x speed and 3x cost savings. Use it for read-only analysis, search, and exploration agents.

### Token-Saving Strategies

1. **Restrict tools per agent** — read-only agents don't need Write/Edit/Bash
2. **Use Haiku for lightweight work** — exploration, search, documentation lookup
3. **Include specific file paths in prompts** — don't make sub-agents search for what you already know
4. **Use skills for progressive disclosure** — don't dump everything into CLAUDE.md
5. **Clear between tasks** — `/clear` prevents stale context from burning tokens
6. **Delegate verbose operations** — test runs, log analysis, file exploration to sub-agents so output stays in their context

---

## 8. Invocation Protocol: The 4 Essential Components

Professional sub-agent setups include a structured invocation protocol. Every Task/Agent dispatch should include:

### 1. Comprehensive Context
What the sub-agent needs to know about the current state. File paths, error messages, decisions made so far, relevant constraints.

### 2. Explicit Instructions
Exactly what the sub-agent should do. Not "review this code" but "review src/auth/ for SQL injection vulnerabilities, check that all user inputs are parameterized, and verify session token rotation follows the pattern in SECURITY.md."

### 3. Relevant File References
Specific paths the sub-agent should read. Don't make it search — you already know where things are.

### 4. Clear Success Criteria
What the output should look like. "Return a JSON summary with: files_reviewed, issues_found (severity, file, line, description), and a boolean all_clear."

### Anti-Patterns

- Sending "implement the feature" with no context
- Spawning 10 agents when 3 would cover the work
- Running 4 independent analyses sequentially when they could parallelize
- Not including file paths, forcing the sub-agent to glob/grep for them

---

## 9. Reference Architectures

### For Your Daemon-Based Orchestration

Given your multi-agent TDD system with orchestrator, explorer, planner, coder, blind tester, and auditor:

**Lead (Custom Agent):**
- Has `Agent` tool + full orchestration skills
- Routes work based on state machine transitions
- Synthesizes results, manages handoffs

**Explorer (Custom Sub-Agent):**
```yaml
---
name: codebase-explorer
description: Deep codebase exploration and dependency analysis. Use for understanding code structure, finding patterns, and mapping dependencies.
tools: Read, Grep, Glob
model: haiku
skills:
  - codebase-exploration
---
```

**Coder (Custom Sub-Agent):**
```yaml
---
name: implementation-coder
description: Implements code following TDD. Writes production code to pass failing tests. Use when implementation work is needed.
tools: Read, Write, Edit, Bash, Glob, Grep
model: sonnet
skills:
  - code-review
  - coding-conventions
memory: project
---
```

**Blind Tester (Custom Sub-Agent):**
```yaml
---
name: blind-tester
description: Writes and runs tests without seeing implementation. Use for TDD test design and verification.
tools: Read, Write, Edit, Bash, Glob, Grep
model: sonnet
skills:
  - testing-patterns
---
```

**Auditor (Custom Sub-Agent):**
```yaml
---
name: code-auditor
description: Reviews code quality, invariant compliance, and test coverage. Use for quality gates and final review.
tools: Read, Grep, Glob, Bash
model: sonnet
skills:
  - code-review
  - quality-standards
---
```

**Key architectural decisions:**
- Explorer uses Haiku (read-only, speed matters)
- Coder has `memory: project` for accumulating codebase patterns
- Auditor has no Write/Edit — can only read and report
- Each has specific skills pre-loaded, not discovered at runtime
- All routing goes through the lead's state machine

### For Simpler Projects

If you don't need the full daemon architecture, 3 agents cover most needs:

1. **Explorer** (haiku, read-only) — research, codebase mapping, dependency analysis
2. **Implementer** (sonnet, full write) — code changes, feature implementation
3. **Reviewer** (sonnet, read-only + bash for tests) — quality review, test execution

---

## 10. Key Resources

### Official Documentation
- **Sub-agents docs:** https://code.claude.com/docs/en/sub-agents
- **Agent SDK sub-agents:** https://platform.claude.com/docs/en/agent-sdk/subagents
- **Skills docs:** https://code.claude.com/docs/en/skills
- **Skills best practices:** https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- **Agent teams:** https://code.claude.com/docs/en/agent-teams
- **Cost management:** https://code.claude.com/docs/en/costs
- **Changelog:** https://code.claude.com/docs/en/changelog

### Community Resources
- **VoltAgent/awesome-claude-code-subagents** — 100+ sub-agent definitions organized by category: https://github.com/VoltAgent/awesome-claude-code-subagents
- **Anthropic's skills repo** — Official skill examples including document creation skills: https://github.com/anthropics/skills
- **ClaudeLog custom agents guide:** https://claudelog.com/mechanics/custom-agents/
- **ClaudeFast orchestration kit:** https://claudefa.st/blog/guide/agents/sub-agent-best-practices

### Deep Dives & Analysis
- **PubNub best practices** (production pipeline with hooks): https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/
- **Agent design patterns** (Lance Martin, Manus/Claude Code/Cursor analysis): https://rlancemartin.github.io/2026/01/09/agent_design/
- **Skills first-principles deep dive** (Lee Han Chung): https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/
- **Mental model for skills/subagents/plugins** (Dean Blank): https://levelup.gitconnected.com/a-mental-model-for-claude-code-skills-subagents-and-plugins-3dea9924bf05
- **Claude Code 2.1 context:fork analysis** (Rick Hightower): https://medium.com/@richardhightower/build-agent-skills-faster-with-claude-code-2-1-release-6d821d5b8179
- **Common mistakes & best practices** (ClaudeKit): https://claudekit.cc/blog/vc-04-subagents-from-basic-to-deep-dive-i-misunderstood
- **Customization guide** (alexop.dev): https://alexop.dev/posts/claude-code-customization-guide-claudemd-skills-subagents/

### GitHub Issues Worth Tracking
- **Nested sub-agent spawning request** (#4182): https://github.com/anthropics/claude-code/issues/4182
- **Sub-agent token usage concerns** (#4911): https://github.com/anthropics/claude-code/issues/4911
- **Agent token usage API request** (#10388): https://github.com/anthropics/claude-code/issues/10388
- **context:fork + agent: frontmatter** (#17283): https://github.com/anthropics/claude-code/issues/17283
