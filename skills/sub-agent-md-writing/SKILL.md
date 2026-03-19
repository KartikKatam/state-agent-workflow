---
name: writing-sub-agents
version: 1-0-0
triggers:
  - agent_role: "*"
    conditions: when writing, modifying, reviewing, or planning sub-agent .md definition files
description: >
  Use when writing a new sub-agent .md file, converting a sub-agent design spec
  into an operational agent definition, modifying an existing sub-agent, or
  planning sub-agent content. Activates for: any work on ephemeral, bounded
  agent definitions (codebase-scout, research-scout, plan-checker, audit-checker,
  test-writer, implementer, scenario-writer, debugger, optimizer or similar
  fire-and-forget agents). Do NOT use for: writing teammate agent .md files
  (use writing-teammate-agents), writing skills (use writing-skills), modifying
  hooks or state machines, writing CLAUDE.md or AGENTS.md project config.
depends_on:
  - writing-skills
---

# Writing Sub-Agent Definitions

## Core Principle

**A sub-agent .md file is a self-contained contract for a bounded task.**

Sub-agents start cold, do one job, return structured results, and terminate. They receive NOTHING from their parent except the delegation prompt string and their own system prompt. They don't know what phase the workflow is in, what happened before them, or what other agents exist — unless the delegation prompt explicitly tells them.

The sub-agent .md defines what the agent IS and how it reasons. The delegation prompt (composed at runtime by the parent) defines what the agent DOES for a specific task. The .md is stable across invocations; the delegation prompt changes every time.

**Violating the letter of the rules is violating the spirit of the rules.**

<HARD-GATE>
Do NOT write any sub-agent .md content until you have:
1. Read the sub-agent's design spec to understand: behavioral steps,
   input contract, output contract, validation rules, tool access,
   file access globs, failure modes, and parent integration
2. Identified what the sub-agent RECEIVES (delegation prompt fields)
   vs what it knows INHERENTLY (its .md system prompt)
3. Listed which hooks enforce structural constraints on this sub-agent
   (SubagentStop validation, PreToolUse file restrictions, PostToolUse
   quality gates) — these are NOT restated in the body
4. Confirmed which skills this sub-agent loads (if any) and what
   those skills already cover

If any input is missing, ask before proceeding.
</HARD-GATE>

## Quick Reference

| Decision | Route |
|----------|-------|
| Is this a bounded, fire-and-forget agent? | This skill (sub-agent) |
| Is this a persistent, event-driven agent with cross-domain messaging? | Use `writing-teammate-agents` skill |
| Does this constraint get enforced by SubagentStop hook? | Don't restate — explain WHY in the body |
| Does PreToolUse block certain file access? | Don't restate — explain WHY |
| Is this domain expertise the sub-agent always needs? | Put in `skills:` frontmatter |
| Does the description summarize the sub-agent's internal workflow? | Rewrite — CSO Rule applies |
| Could the parent compose this context at delegation time? | Don't hardcode it in the .md — let the delegation prompt carry it |

## How Sub-Agents Differ From Teammates

| Property | Teammate | Sub-Agent |
|----------|----------|-----------|
| Lifetime | Persistent across session | Single task, then terminates |
| Context | Accumulates over session | Fresh each invocation |
| Communication | Messages peers directly | Returns result to parent only |
| State | Managed by daemon state machine | No state machine — linear execution |
| Model | Opus (complex reasoning) | Sonnet or Haiku (task-appropriate) |
| Delegation | Can spawn sub-agents | Cannot spawn sub-agents |
| Identity | Rich backstory, coordination protocols | Focused role, input/output contract |
| Body size | 200-350 lines | 80-200 lines |

The fundamental design difference: a teammate's .md defines how it *thinks across many situations*. A sub-agent's .md defines how it *executes one category of task reliably*.

## The .md vs The Delegation Prompt

This is the most important distinction for sub-agent design. Two documents shape a sub-agent's behavior:

**The .md file (stable — same every invocation):**
- Who the sub-agent IS (role, purpose, behavioral standards)
- How it reasons about its category of work (quality criteria, decision heuristics)
- What it produces (output contract schema)
- What it must NOT do (boundary awareness with WHY)
- What skills it has loaded (domain expertise)

**The delegation prompt (dynamic — different every invocation):**
- What specific task to do right now
- Specific file paths, error messages, context
- Which plan chunk, test specs, or findings to work from
- Worktree path and branch
- Previous sub-agent's return (for resume/cold-start scenarios)

**Design rule:** If information changes between invocations, it belongs in the delegation prompt, not the .md. If information is the same regardless of what task the sub-agent works on, it belongs in the .md.

```markdown
# WRONG — task-specific detail hardcoded in .md
You work in the task worktree at `.claude/worktrees/phase-01/task-03`.
Your plan chunk is in `.claude/plans/feature-plan.json` chunk 3.

# RIGHT — stable identity and reasoning in .md
You work in the task worktree provided in your delegation prompt.
Read the plan chunk provided to understand what behavior to implement.
```

## Sub-Agent .md Canonical Structure

### Frontmatter

```yaml
---
name: sub-agent-identifier
description: >
  What this sub-agent does and WHEN to delegate to it. Include trigger
  phrases the parent would naturally use. Apply CSO Rule.
tools: [restricted tool list — principle of least privilege]
model: sonnet  # or haiku for read-only/extraction tasks
skills:
  - relevant-skill  # Only if the sub-agent needs it for EVERY task
---
```

#### Frontmatter Field Rules

**`name`** — Descriptive, matches the `sub_agent_type` field used in delegation prompts. Examples: `codebase-scout`, `test-writer`, `audit-checker`.

**`description`** — Routing signal for the parent. The parent uses this to decide whether to auto-delegate. Same CSO Rule as teammates: describe WHEN to use, not HOW it works internally.

**`tools`** — This is the strongest behavioral enforcement. A sub-agent without Write/Edit tools literally cannot modify files, regardless of what its prompt says.

| Sub-Agent Type | Tools | Rationale |
|---------------|-------|-----------|
| Read-only analysis (scouts, checkers) | Read, Grep, Glob, Bash | Observe and report, never modify |
| Read-only + web (research scouts) | Read, Grep, Glob, WebSearch, WebFetch | Research needs web access |
| Implementation (writers, implementers) | Read, Write, Edit, Bash, Glob, Grep | Must create/modify code |
| Debugging | Read, Write, Edit, Bash, Glob, Grep | Must apply fixes |

**Key:** If a sub-agent should never write files, OMIT Write/Edit from tools. Don't rely on prose instructions — tool restriction is enforcement level 1 (infrastructure), prose is level 2 (easily overridden).

**`model`** — Match to task complexity:

| Model | Use For | Examples |
|-------|---------|---------|
| `haiku` | Fast extraction, known-path reads, structure listing | File listing, signature extraction, simple search |
| `sonnet` | Reasoning, analysis, code generation, review | Test writing, implementation, auditing, debugging |

Default to `sonnet`. Use `haiku` only when the sub-agent's job is mechanical extraction, not reasoning.

**`skills`** — Sub-agents should load FEW skills (0-2). Every loaded skill adds to the cold-start context cost, and sub-agents start fresh each time. Only list skills the sub-agent needs for literally every invocation. Domain knowledge that varies by task should arrive via the delegation prompt.

### Body Structure

Sub-agent bodies are shorter and more focused than teammate bodies. Target: **80-200 lines**.

The body has 5 sections:

#### Section 1: Identity & Purpose (3-8 lines)

Who you are and what you optimize for. Shorter than teammate identity — no elaborate backstory needed. Sub-agents run briefly; a 1-2 sentence role statement is sufficient.

```markdown
You are a test suite designer. You write tests from behavioral
specifications that verify design intent without coupling to
implementation details.

You optimize for tests that fail for the right reasons — missing
behavior, not missing imports or syntax errors.
```

Include the information barrier if this sub-agent has one (INV-1 blind wall):

```markdown
## Information Barrier
You must NEVER read, request, or infer implementation source code.
This isolation exists because tests that couple to implementation
are worthless — they test HOW code works, not WHETHER it works.
Your tests must be derivable from behavioral specs alone.
```

The information barrier explanation includes WHY (level 3 enforcement). The actual file access restriction is enforced by a PreToolUse hook (level 5) — the body doesn't need to say "you cannot read files matching `src/**`."

#### Section 2: Core Workflow (15-40 lines)

The decision framework for how this sub-agent approaches its work. NOT a rigid step-by-step checklist — a decision-oriented process that handles variations.

Structure as numbered phases with decision points, not a flat list. Each phase should reference what it operates on (from the delegation prompt) and what it produces (for the next phase or the return).

```markdown
## How You Work

1. **Parse delegation prompt** — extract task requirements, file paths,
   success criteria, and worktree path

2. **Understand the task** — read the plan chunk and behavioral specs
   to understand WHAT to build, not just WHERE to put it

3. **Design before writing** — for each test/function/component, decide:
   - What behavior does this verify?
   - What inputs exercise the interesting cases?
   - What assertion proves correctness?

4. **Write and validate** — create files, then run quality checks:
   - Tests should fail with behavior-related errors (AssertionError,
     ImportError) not infrastructure errors (SyntaxError, CollectionError)
   - If infrastructure errors → fix them before proceeding

5. **Quality gate** — run the full gate (format, lint, typecheck)
   before returning. Gate failures are your problem, not the parent's.

6. **Return structured result** — include everything the parent
   needs to evaluate your work and route the next step
```

**What to include in the workflow:**
- Decision points where the sub-agent exercises judgment
- Quality criteria that require interpretation (not mechanical checks)
- Iteration loops with exit conditions

**What NOT to include:**
- State machine transitions (infrastructure handles this)
- Hook-enforced validation (SubagentStop catches missing fields)
- Tool usage instructions Claude already knows (how to use Read, Write)

#### Section 3: Output Contract (15-30 lines)

What the sub-agent returns. This is structurally validated by the SubagentStop hook, but the body teaches the sub-agent what GOOD output looks like — the semantic quality the hook can't check.

```markdown
## What You Return

Return a structured JSON result as your final message:

**Always include:**
- `delegation_type` — must match what you received
- `status` — "completed", "partial", or "failed"
- `files_modified` — every file you created or changed
- `quality_gate_result` — format, lint, typecheck results
- `decisions_made` — every judgment call with reasoning
- `carry_forward` — anything the next agent needs to know

**Quality standard for `decisions_made`:**
Each decision should explain WHAT you chose, WHY, and what
alternative you considered. "Used dataclass instead of dict for
type safety" — not just "used dataclass."

**When to return `partial`:**
If you hit context pressure or can't complete all work, return
what you've done. Include `carry_forward` with specific gaps
so a replacement can continue without re-doing your work.
```

Don't list the full JSON schema in the body — the SubagentStop hook validates schema compliance. The body teaches judgment about what makes a GOOD return, not what fields are required.

#### Section 4: Boundaries (10-25 lines)

What the sub-agent must NOT do, with WHY for each boundary.

These are the boundaries that require the sub-agent to understand their purpose (level 3 enforcement). Structural boundaries (file access, tool restrictions) are enforced by hooks and don't need prose.

```markdown
## Boundaries

**Stay in your worktree.** All file operations happen in the worktree
path from your delegation prompt. Other worktrees belong to parallel
sub-agents — cross-boundary edits cause merge conflicts that break
the worktree isolation model.

**Don't interpret beyond your scope.** If the plan chunk is ambiguous,
make a reasonable decision and document it in `decisions_made`. Don't
redesign the architecture or second-guess upstream choices — flag
concerns for the parent to evaluate.

**Don't optimize prematurely.** Write correct code first. If the parent
wants optimization, it will dispatch an optimizer sub-agent. Your job
is correctness, not performance.
```

#### Section 5: Failure Handling (10-20 lines)

How the sub-agent handles problems it encounters.

```markdown
## When Things Go Wrong

**Tests won't pass after 5 iterations:** Stop iterating. Return
`partial` with what you've tried in `decisions_made`. The parent
will dispatch a debugger or provide guidance.

**Missing dependency from another chunk:** Document in `carry_forward`
and return `partial`. Don't try to implement the dependency yourself.

**Quality gate fails (non-test):** Auto-fix what's fixable (format,
simple lint). Report unfixable issues in the return.

**Ambiguous plan chunk:** Make the best decision you can, document
your reasoning in `decisions_made`, and proceed. Don't block on
ambiguity — the parent reviews your decisions.
```

## Writing Principles for Sub-Agents

### Conciseness Is Critical

Sub-agent bodies pay their token cost on EVERY invocation. A teammate's body cost is amortized over a long session. A sub-agent's body cost is paid fresh each time it's spawned. This makes conciseness even more important than for teammates.

**Target token budgets:**

| Sub-Agent Type | Body Lines | Approximate Tokens |
|---------------|------------|-------------------|
| Read-only scouts | 80-120 | ~800-1200 |
| Checkers/validators | 100-150 | ~1000-1500 |
| Writers/implementers | 120-200 | ~1200-2000 |
| Complex (debugger) | 150-200 | ~1500-2000 |

### Self-Contained Design

Sub-agents receive NOTHING from their parent except the delegation prompt. They don't inherit the parent's conversation history, system prompt, skills (unless listed in `skills:`), or tool results.

This means the .md must be self-contained for its domain. But "self-contained" doesn't mean "exhaustive" — it means the sub-agent has enough context to reason about any task in its category, with task-specific details arriving via delegation prompt.

**Test for self-containment:** Could a fresh sub-agent, reading only this .md and a well-composed delegation prompt, complete any task in its domain? If the answer requires "and also it needs to know about X from the parent's context" — that X either goes in the .md (if stable) or the delegation prompt (if task-specific).

### Information Barriers (INV-1)

Some sub-agents are intentionally blind to certain information. The test-writer can't see implementation source. The implementer can't see test source. The scenario-writer can't see either.

These barriers have two enforcement layers:
1. **Structural (hook):** PreToolUse blocks Read/Grep on forbidden file globs. The sub-agent literally cannot access the files.
2. **Conceptual (body):** The body explains WHY the barrier exists so the sub-agent doesn't try to circumvent it or request the information through other channels.

Both layers are needed. The hook prevents access. The body prevents the sub-agent from working around the restriction (asking the parent for file contents, inferring implementation from error messages, etc.).

```markdown
# Pattern for information barrier in body

## Information Barrier
You are blind to [what] by design. This exists because [consequence
of seeing it — what goes wrong, not just "it's the rules"].
Work exclusively from [what you DO have access to].

If you encounter errors that suggest [forbidden information] would
help, use the error messages themselves as your guide — they tell
you WHAT failed without revealing HOW the other side is structured.
```

### Multi-Parent Sub-Agents

Some sub-agents serve multiple parents for different purposes (e.g., test-writer serves Coder for TDD red phase and Tester for infrastructure repair). The .md must handle both use cases without the sub-agent needing to know which parent dispatched it.

Design pattern: use the delegation prompt's `type` field to differentiate behavior:

```markdown
## Adapting to Your Task

Your delegation prompt's `type` field determines your mode:

**`tdd_chunk`** — Write new tests from behavioral specs. All tests
MUST fail (you're writing tests for behavior that doesn't exist yet).
Verify failure types are behavioral (AssertionError, ImportError),
not infrastructure (SyntaxError).

**`targeted`** — Fix specific test infrastructure issues. You receive
error output and existing test files. Fix the plumbing — don't
rewrite test logic. Verify with collect-only, not full execution.
```

## Sub-Agent Description CSO Rule

Same rule as teammates, but the audience is different. Teammate descriptions are read by the orchestrator. Sub-agent descriptions are read by the dispatching teammate.

```yaml
# WRONG — workflow leak
description: >
  Reads source files across a partition, traces dependencies, extracts
  type signatures, identifies implicit contracts, and returns a
  structured exploration result.

# RIGHT — trigger conditions only
description: >
  Codebase exploration and analysis within an assigned partition.
  Use when structured understanding of code architecture, dependencies,
  patterns, or conventions is needed. Returns confidence-scored findings.
  Do NOT use for: web research (use research-scout), plan verification
  (use plan-checker), code modification (use implementer).
```

## Sub-Agent Writing Checklist

**Frontmatter:**
- [ ] `name` matches `sub_agent_type` used in delegation prompts
- [ ] Description follows CSO Rule
- [ ] Tools are minimum viable — read-only sub-agents have NO Write/Edit
- [ ] Model matches task complexity (haiku for extraction, sonnet for reasoning)
- [ ] Skills are only those needed for every invocation (0-2 typical)

**Body — Identity (Section 1):**
- [ ] Role stated in 1-2 sentences
- [ ] Optimization target clear (correctness? coverage? independence?)
- [ ] Information barrier explained with WHY (if applicable)
- [ ] No elaborate backstory — sub-agents are focused workers, not personas

**Body — Workflow (Section 2):**
- [ ] Decision-oriented phases, not rigid step checklist
- [ ] Each phase references what it operates on (from delegation prompt)
- [ ] Quality criteria require interpretation (not mechanical checks)
- [ ] Iteration loops have explicit exit conditions

**Body — Output Contract (Section 3):**
- [ ] Teaches what GOOD output looks like (semantic quality)
- [ ] Doesn't list full JSON schema (hook validates that)
- [ ] `decisions_made` quality standard defined
- [ ] `partial` return criteria specified

**Body — Boundaries (Section 4):**
- [ ] Each boundary has a WHY explanation
- [ ] No structural constraints restated (hooks handle those)
- [ ] Scope boundaries prevent the sub-agent from over-reaching

**Body — Failure Handling (Section 5):**
- [ ] Each failure mode has a specific response (not generic "escalate")
- [ ] Stall threshold specified (3 for verification, 5 for implementation)
- [ ] `partial` return is a valid failure response (not just "failed")

**Body — Overall:**
- [ ] Under 200 lines (scouts/checkers under 150)
- [ ] Self-contained — doesn't assume parent context
- [ ] Task-specific content deferred to delegation prompt, not hardcoded
- [ ] No infrastructure restated (SubagentStop validation, PreToolUse blocks)
- [ ] Multi-parent scenarios handled via delegation type field

## Anti-Rationalization

| Excuse | Reality |
|--------|---------|
| "I'll include the full output schema so the sub-agent knows the format" | SubagentStop hook validates schema. Body teaches semantic quality, not field names. Full schemas waste ~200 tokens per invocation. |
| "I need to explain the state machine so the sub-agent knows what happens after" | Sub-agents don't need to know what happens next. They do their job and return. The parent handles routing. |
| "I'll add all 8 audit dimensions to the audit-checker body" | The delegation prompt specifies which dimensions to check. The body teaches how to CHECK dimensions, not which ones exist. |
| "The body needs to explain how to use PTC" | If the sub-agent loads the ptc-sandbox skill, PTC usage is covered. If it doesn't need PTC for every task, PTC instructions don't belong in the body. |
| "I'll include the parent's think prompt questions so the sub-agent thinks the same way" | Think prompts are for the parent's deliberation. The sub-agent has its own workflow. Don't transplant the parent's reasoning framework. |
| "200 lines is too short for a complex sub-agent like debugger" | If the debugger needs 300 lines, domain knowledge is leaking from skills into the body. The debugging skill teaches debugging methodology; the body teaches this specific debugger's role and constraints. |

**Red Flags — STOP:**
- Sub-agent body exceeding 200 lines
- Full JSON schema in the body (hook validates this)
- State machine transitions or "what happens next" language
- Parent's think prompt questions copied into sub-agent body
- File access restrictions stated as prose (hook enforces these)
- Task-specific details hardcoded instead of arriving via delegation prompt

## References

**Within this skill's references/:**
- For reusable structural patterns (.md vs delegation prompt split with detailed examples, INV-1 information barrier patterns for all 3 archetypes, multi-parent sub-agent patterns, output contract semantic quality, failure handling patterns, workflow section patterns, worked annotated skeleton, delegation prompt composition guide for parents) → read `references/patterns.md`
- For 10 sub-agent failure modes with diagnostic criteria and WRONG/RIGHT fixes (Full Schema Dumper, Context Assumer, Parent's Shadow, Leaky Barrier, Rigid Step List, Over-Qualified Worker, Scope Creeper, Silent Failer, Hardcoded Path Agent, Single-Parent Specialist) → read `references/anti-patterns.md`
- For full WRONG/RIGHT examples per body section (frontmatter, identity, workflow, output contract, boundaries, failure handling), complete sub-agent examples (read-only, writer, multi-parent, information-barrier), and INV-1 barrier patterns → read `references/complete-examples.md`
- For delegation prompt design from the parent's perspective (the .md↔prompt split, 4-component prompts, context sizing, per-sub-agent templates, resume vs cold-start prompts) → read `references/delegation-prompt-design.md`

**Cross-skill references:**
- For writing patterns (WHY-driven, tables over prose, WRONG/RIGHT pairs, conciseness) → read `writing-skills` references
- For sub-agent delegation lifecycle (dispatch, verify, synthesize), context sizing heuristics (2-Read test, 30% test), escalation protocol → read `sub-agent-delegation` skill
- For anti-rationalization architecture → read `writing-skills` references/anti-rationalization-architecture.md

**External references:**
- For sub-agent critique and review methodology → see standalone `agent-critique-guide.md` (used in chat sessions, not loaded by agents)
- For teammate .md files → use the `writing-teammate-agents` skill
