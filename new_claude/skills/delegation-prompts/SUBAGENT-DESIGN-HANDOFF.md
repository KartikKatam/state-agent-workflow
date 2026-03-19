# Handoff: Custom Sub-Agent Design for Delegation Workflow

**Session:** 2026-03-11
**Status:** Research complete, design decisions needed before implementation
**Depends on:** Completed delegation-prompts skill (v4-0-0), return protocol

---

## What Was Accomplished This Session

### 1. Delegation-prompts skill completed (v4-0-0)

The `composing-delegation-prompts` skill is fully implemented with 5 delegation types, return protocol, validation hooks, and critique-driven improvements.

**Files implemented — read these to understand the current state:**

| File | Purpose | Read priority |
|------|---------|---------------|
| `SKILL.md` | Main skill doc (490 lines, v4-0-0) | HIGH — read first |
| `CONFIRMED-CHANGES.md` | All confirmed design decisions | HIGH — status reference |
| `RETURN-PROTOCOL-HANDOFF.md` | Return protocol design + resolved questions | MEDIUM |
| `schemas/targeted.schema.json` | Delegation schema pattern (read one to understand all) | MEDIUM |
| `schemas/return-targeted.schema.json` | Return schema pattern (read one to understand all) | MEDIUM |
| `scripts/validate_delegation_prompt.py` | PreToolUse hook — validates all 5 types | MEDIUM |
| `scripts/validate_return.py` | Return validation utility (importable + CLI) | MEDIUM |
| `specializations/ptc-delegation.md` | PTC patterns for delegates (134 lines) | HIGH — sub-agent route goes here |
| `references/anti-patterns.md` | 7 WRONG/RIGHT examples with token costs | LOW |

All paths relative to: `agentic_workflow/new_claude/skills/delegation-prompts/`

Hook file: `agentic_workflow/new_claude/hooks/subagent_stop_return_validation.py`

**Test results:** 19/19 validation tests pass (11 delegation prompt + 8 return validation including JSON-in-prose extraction).

### 2. Discovery: Claude Code custom sub-agents with skill injection

We discovered that Claude Code supports custom sub-agent definitions in `.claude/agents/*.md` with a `skills` field that **injects full skill content into the sub-agent's context at startup**.

**Key facts verified via official documentation:**

1. **Custom agents are strict upgrades over general-purpose.** Default tool access is identical (inherits all tools including MCP). Skills ADD domain knowledge without replacing capabilities.

2. **The `skills` field in frontmatter pre-loads skill content.** Unlike normal sessions where skill descriptions are loaded but content loads on-demand, sub-agents get **full skill content injected at startup**. No Read tool call needed.

3. **Sub-agents CAN use PTC MCP.** Empirically verified in this session — `mcp__local-ptc__ptc_execute` works from sub-agents, namespace persists across calls.

4. **Body + spawner prompt combine.** The agent definition's markdown body becomes the system prompt. The spawner's `prompt` parameter becomes the task instruction. Both are received. CLAUDE.md is also loaded automatically.

5. **Sub-agents cannot spawn sub-agents.** The Agent/Task tool is stripped from all spawned contexts. Architecture is strictly two levels: main session → sub-agents/teammates.

6. **Injected skills consume context at startup.** Full content loaded, not just descriptions. Must keep injected skills compact.

**Sources consulted:**
- https://code.claude.com/docs/en/sub-agents (official)
- https://code.claude.com/docs/en/skills (official)
- https://code.claude.com/docs/en/agent-teams (official)
- https://platform.claude.com/docs/en/agent-sdk/subagents (SDK docs)
- GitHub issues #32731, #19077, #4182

### 3. Critical discrepancy to verify empirically

**Existing teammate definitions list `Task` in their tools field:**

```yaml
# .claude/agents/chunk-coder.md
tools: Read, Write, Edit, Bash, Glob, Grep, Task, SendMessage
```

```yaml
# .claude/agents/codebase-explorer.md
tools: Read, Write, Bash, Glob, Grep, Task, SendMessage
```

But official docs and GitHub issues say the Agent/Task tool is stripped from spawned contexts. **This must be verified empirically before designing the relay protocol.** If teammates DO have the Task tool, they can spawn sub-agents directly (no relay through the lead needed). If they don't, the listed `Task` tool in the frontmatter is ineffective.

**Test to run:** Spawn a teammate and have it attempt `Task(subagent_type="general-purpose", prompt="print hello")`. Check if it succeeds or fails.

---

## The Design Space

### What custom sub-agents enable

Sub-agents spawned via `Task(subagent_type="my-custom-agent")` get:
- A tailored system prompt (the agent definition's markdown body)
- Pre-loaded skills (via `skills` field — full content injected)
- All tools including MCP (by default, unless restricted via `tools` or `disallowedTools`)
- Model selection (`model: haiku` for cheap tasks, `opus` for complex ones)
- CLAUDE.md loaded automatically

This means we can create specialized sub-agent types that:
1. Know how to use PTC (via ptc-delegation skill)
2. Know how to structure their return (via a return-protocol skill)
3. Have type-specific behavioral instructions (exploration vs research vs targeted)
4. Can have hooks attached (needs verification — do custom agent hooks work?)

### Proposed custom sub-agent types

| Agent Definition | Pre-loaded Skills | Model | Use Case |
|-----------------|-------------------|-------|----------|
| `targeted-delegate` | ptc-delegation, return-protocol | inherit | Surgical file edits, known coordinates |
| `guided-delegate` | ptc-delegation, return-protocol | inherit | Diagnosis, judgment-required tasks |
| `exploration-delegate` | ptc-delegation, return-protocol, context-packets | sonnet | Codebase mapping, structure discovery |
| `research-delegate` | ptc-delegation, return-protocol, research-workflow | sonnet | API research, doc lookups |
| `tdd-delegate` | ptc-delegation, return-protocol, tdd-workflow | inherit | Test-driven implementation |
| `audit-delegate` | ptc-delegation, return-protocol, plan-adherence | haiku | Code review, scope checking |

### The relay pattern (if teammates can't spawn)

```
Teammate composes delegation prompt JSON (using composing-delegation-prompts skill)
  → Teammate sends to lead via SendMessage: {type: "spawn_request", delegation: <JSON>}
  → Lead maps delegation type to custom agent type
  → Lead spawns: Task(subagent_type="exploration-delegate", prompt=<delegation JSON>)
  → Sub-agent executes with pre-loaded skills
  → Sub-agent returns structured JSON (validated by SubagentStop hook)
  → Lead receives return, forwards to requesting teammate via SendMessage
```

### The direct pattern (if teammates CAN spawn)

```
Teammate composes delegation prompt JSON
  → Teammate spawns: Task(subagent_type="exploration-delegate", prompt=<delegation JSON>)
  → Sub-agent executes with pre-loaded skills
  → Sub-agent returns structured JSON
  → Teammate receives return directly
```

This is simpler but requires the empirical verification above.

### New skill: return-protocol (for sub-agents to load)

Currently, the return schema is included in the delegation prompt via `return_schema` + `return_instruction`. But if sub-agents can load skills, a dedicated `return-protocol` skill could teach them:

1. How to structure their return JSON by delegation type
2. What `delegation_type` and `status` mean
3. How to populate `decisions_made`, `essential_output_confidence`, etc.
4. When to use optional fields (`unexpected_findings`, `cross_scope_findings`, `gaps`)
5. The quality bar for each confidence level (high/medium/low)

This would be a compact skill (~100-150 lines) loaded via the `skills` field on each custom agent definition. Much more effective than a single `return_instruction` sentence.

---

## Open Questions for Next Session

### Architecture decisions

1. **Can teammates spawn sub-agents?** Empirical test needed. This determines relay vs direct pattern.

2. **Can custom sub-agents have hooks?** If a custom agent definition can specify hooks, the SubagentStop return validation hook could be attached per-agent-type rather than globally. Needs verification.

3. **Should each delegation type have its own custom agent, or use fewer generic agents?** 6 agent definitions is more maintenance but more tailored. 2-3 generic agents (e.g., `ptc-delegate`, `research-delegate`) is simpler but less specialized.

4. **How does the lead map delegation type to agent type?** Options:
   - Explicit in the delegation prompt (`"subagent_type": "exploration-delegate"`)
   - Automatic mapping in the orchestrator skill (`type: "exploration"` → `exploration-delegate`)
   - Convention: agent name = `{type}-delegate`

5. **Context budget per agent type.** Each injected skill consumes context at startup. Need to measure:
   - `ptc-delegation.md`: 134 lines (~2-3K tokens)
   - `return-protocol` (proposed): ~100-150 lines (~2K tokens)
   - `context-packets/SKILL.md`: how many lines?
   - `research-workflow/SKILL.md`: how many lines?
   - Total per agent type must leave enough working context

### Delegation skill completion

6. **PTC sub-agent route in ptc-delegation.md.** Needs a section teaching sub-agents how to use PTC, branching by delegation type. Blocked on decision #3 (agent types).

7. **Validation hook enforcement for agent types.** Should the delegation prompt validation hook check that the spawner is using the right custom agent type for the delegation type? (e.g., warn if `type: "research"` but `subagent_type: "general-purpose"`)

8. **`return_schema` field still needed?** If sub-agents get return knowledge from a pre-loaded skill, the `return_schema` field in the delegation prompt may be redundant. Keep for sub-agents that don't load the skill (fallback), or remove to save tokens?

### Workflow integration

9. **Spawn request message type.** If relay pattern is needed, the v2 message protocol needs a `spawn_request` type. Where does this get defined? In `schemas/message_protocol.py` or in the delegation skill?

10. **Return forwarding.** When the lead receives a sub-agent return, how does it forward to the requesting teammate? Direct SendMessage with the full JSON? Or does the lead summarize/extract first?

11. **Orchestrator skill update.** `workflow-orchestration/SKILL.md` needs to know about the sub-agent relay pattern (if relay) or about the custom agent types (either way).

---

## Existing Agent Definitions to Reference

Current teammates in `.claude/agents/` — read these to understand the existing agent definition pattern:

| File | Purpose | Key patterns to note |
|------|---------|---------------------|
| `.claude/agents/orchestrator.md` | Lead behavioral spec | Workflow phases, model dispatching |
| `.claude/agents/codebase-explorer.md` | Explorer teammate | Operating modes, tool list includes Task |
| `.claude/agents/researcher.md` | Research teammate | MCP usage, source routing |
| `.claude/agents/plan-architect.md` | Planner teammate | Conditional skill loading |
| `.claude/agents/chunk-coder.md` | Coder teammate | Tiered file reading, tool list includes Task |
| `.claude/agents/scribe.md` | Commit teammate | Conditional coding-memory loading |

These are TEAMMATES (full Claude Code sessions). The new custom agents would be SUB-AGENTS (lighter, skill-injected, no conversation history). Different category, similar definition format.

---

## Recommended Session Plan

### Phase 1: Verify constraints (15 min)
- Test: Can teammates spawn sub-agents? (spawn a teammate, have it try Task)
- Test: Can custom agent definitions specify hooks?
- Measure: Context size of candidate skills for injection

### Phase 2: Design custom agent types (30 min)
- Decide: How many agent types? Per-delegation-type vs generic?
- Decide: Which skills per type? (context budget matters)
- Decide: Relay vs direct pattern (depends on Phase 1 verification)
- Draft: Agent definition format for sub-agents

### Phase 3: Build return-protocol skill (30 min)
- Create: `new_claude/skills/return-protocol/SKILL.md` — compact sub-agent skill
- Include: Type-specific return instructions, confidence level guidance
- Include: PTC sub-agent route (from ptc-delegation.md, restructured)

### Phase 4: Build custom agent definitions (30 min)
- Create: `.claude/agents/{type}-delegate.md` for each agreed type
- Wire: Skills field, model selection, system prompt
- Test: Spawn each type, verify skill injection works

### Phase 5: Update delegation skill (15 min)
- Update: SKILL.md with custom agent type guidance
- Update: Validation hook with agent type checks (if decided)
- Update: CONFIRMED-CHANGES.md with new decisions

---

## Files Modified This Session (for diff review)

**Created:**
- `schemas/exploration.schema.json`
- `schemas/research.schema.json`
- `schemas/return-targeted.schema.json`
- `schemas/return-guided.schema.json`
- `schemas/return-exploration.schema.json`
- `schemas/return-research.schema.json`
- `schemas/return-tdd-chunk.schema.json`
- `scripts/validate_return.py`
- `hooks/subagent_stop_return_validation.py` (at `new_claude/hooks/`)

**Modified:**
- `scripts/validate_delegation_prompt.py` — added exploration + research validators
- `schemas/targeted.schema.json` — added return_schema, return_instruction fields
- `schemas/guided.schema.json` — added return_schema, return_instruction fields
- `schemas/tdd-chunk.schema.json` — added return_schema, return_instruction fields
- `SKILL.md` — v3→v4: new types, return protocol, eval scenarios, cost heuristic, edge cases
- `CONFIRMED-CHANGES.md` — updated to reflect all implemented work
- `RETURN-PROTOCOL-HANDOFF.md` — marked decisions as resolved
- `references/anti-patterns.md` — added TOC
