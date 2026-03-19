# Multi-Perspective Analysis

> **Purpose**: Provides a protocol for exploring multiple valid approaches in parallel using sub-agents, presenting structured comparisons, and iterating with the user to lock a decision.
> **Consumers**: plan-architect, chunk-coder
> **Schemas**: None (sub-agents return findings as text directly to the parent agent)
> **Depends on**: `context-packets` (sub-agents need codebase context to evaluate approaches)

## What You Learn From This Skill
- When to activate multi-perspective analysis (and when NOT to)
- Two scopes: planner perspectives (architecture) vs coder perspectives (code taste)
- How to spawn parallel sub-agents to explore each approach to meaningful depth
- Sub-agent model selection (Haiku, Sonnet, or Opus based on complexity)
- How to synthesize and present structured comparisons with recommendations
- How to iterate with the user (combine, tweak, follow-up, deeper dives)
- Application to test architecture decisions

## Contract
- NEVER present a single approach as the only option when multiple valid approaches exist
- ALWAYS include a clear recommendation with reasoning — don't just list options
- Sub-agents explore to MEANINGFUL depth (read source files, check patterns) — not surface-level summaries
- If sub-agents surface unknowns, gather additional context BEFORE presenting to user
- Iterate with the user — let them combine, tweak, ask follow-ups, request deeper dives
- Maximum 4 approaches per decision — if more exist, pre-filter to the most viable 3-4
- Sub-agents may use up to Opus without user approval; no extended thinking available for sub-agents

---

## Two Scopes of Perspective Analysis

### Planner Perspectives (Architecture)

Used by **plan-architect** for approach and architecture decisions:
- "Should we use composition or inheritance for this component?"
- "Event-driven or polling for state sync?"
- "Vertical slice with inline types or shared type module?"

The planner explores each approach at the architectural level:
- How it shapes the chunk structure
- What files it touches
- How it integrates with existing patterns
- What it implies for future chunks

The user can combine approaches, tweak boundaries, ask follow-up questions, or request deeper exploration of one branch.

### Coder Perspectives (Code Taste)

Used by **chunk-coder** for implementation mechanics decisions:
- "Generator pattern or list-building for this data pipeline?"
- "Nested conditionals or early-return guard clauses?"
- "Single function with branches or dispatch to helper functions?"
- "Class with methods or module-level functions with shared state?"

The coder explores each at the code level:
- Readability
- Behavioral nuances
- Structural organization
- How it affects test ergonomics

The scope is narrower and faster than planner perspectives — these are implementation decisions within an already-locked architectural approach.

---

## When to Activate

### Plan-Architect Activates When

- A chunk has 2+ viable architectural approaches
- The design doc specifies *what* but leaves *how* open, and codebase context suggests multiple valid patterns
- The user asks "what are our options?" or "explore alternatives"
- Test architecture (Phase 5) identifies 2+ viable test strategies

### Chunk-Coder Activates When

- The finalized plan leaves implementation details open and 2+ valid coding approaches exist
- Code taste decisions affect structure, readability, or test ergonomics meaningfully
- A function or module can be organized in genuinely different ways that affect behavior or maintainability

### Do NOT Activate (Either Agent)

- There is one clear approach consistent with existing codebase patterns
- The design doc or plan specifies the approach explicitly
- The choice is trivial (naming, file location, import order)
- The plan already locked the decision (coder does not re-debate planner decisions)

---

## Sub-Agent Model Selection

Sub-agents may use **up to Opus** without requiring user approval. These are scoped, short-lived explorations — the "always ask before Opus" rule applies to long-lived teammates, not Task-tool sub-agents.

**Important**: Task-tool sub-agents do NOT have extended thinking. Opus without thinking is still a significantly stronger reasoner than Sonnet for architectural analysis — the base model's reasoning capability handles cross-file impact, coupling detection, and tradeoff analysis well in its default mode.

| Sub-agent task | Model | When |
|---|---|---|
| Check if a pattern exists in codebase | Haiku | Factual lookup, yes/no answer |
| Evaluate approach by reading 2-3 files, assessing pattern compatibility | Sonnet | Most approach evaluations |
| Evaluate approach requiring cross-file architectural reasoning, subtle tradeoff analysis, system-wide impact | **Opus** | Complex approaches with coupling/failure mode/scalability implications |

The planner or coder makes the model selection call based on the complexity of the specific approach being evaluated.

---

## Divergent Exploration Protocol

### Step 1: Identify Decision Axes

- What are the distinct approaches? (minimum 2, maximum 4)
- What are the evaluation criteria? (feasibility, integration cost, test complexity, pattern consistency, performance)

### Step 2: Spawn Parallel Sub-Agents (One Per Approach)

Spawn **Explore** sub-agents (read-only, fast). Each receives:
- The specific approach to explore ("Evaluate approach A: composition pattern")
- Relevant context: feature context packet path, codebase patterns, design doc section
- The evaluation checklist (see below)

Each sub-agent returns its findings as text directly to the parent agent. No files are written — the parent synthesizes from the responses in its context.

**Sub-agent prompt template:**
```
Evaluate approach: {approach name} for {chunk/decision}.

Read these files for context:
- {feature context packet path}
- {relevant source files}

Evaluation checklist — cover ALL of these:
1. Feasibility: high/medium/low + reasoning + any blockers
2. Files affected: which files would need changes
3. Pattern compatibility: does this match existing codebase patterns? Which ones?
4. Friction points: where does this approach create friction with existing code
5. Test impact: how many tests needed, how complex, what fixtures required
6. Pros and cons: concrete advantages and disadvantages
7. Unknowns: anything you discovered that wasn't in the original context
8. Code references: file:line locations that informed your analysis
```

### Step 3: Check If Sub-Agents Surfaced Unknowns

If a sub-agent's findings reveal context the planner/coder didn't have (e.g., "found a registry pattern at pipeline.py:142 that affects Approach B's feasibility"), the planner/coder SHOULD:
- Request an additional context packet from the orchestrator (info_request)
- Or spawn a follow-up sub-agent to investigate the specific unknown

BEFORE synthesizing and presenting to the user.

### Step 4: Synthesize and Present

Read all sub-agent responses from context. Compare across evaluation criteria. Present to user:

```
"I explored {N} approaches for {chunk/decision}:

**Approach A: {name}**
- How it works: {1-2 sentences}
- Pros: {from sub-agent findings}
- Cons: {from sub-agent findings}
- Integration cost: {assessment}
- Pattern consistency: {matches/conflicts with existing patterns}

**Approach B: {name}**
- How it works: {1-2 sentences}
- Pros: {from sub-agent findings}
- Cons: {from sub-agent findings}
- Integration cost: {assessment}
- Pattern consistency: {assessment}

**My recommendation**: {which and why}

You can pick one, combine elements (e.g., {specific suggestion}),
tweak the approach, or ask me to explore further."
```

### Step 5: Iterate with User

- User may ask follow-up questions about a specific approach
- User may propose a hybrid — planner/coder evaluates feasibility
- User may request deeper exploration of one branch (spawn another sub-agent)
- Continue until user locks the decision

---

## Application to Test Architecture (Phase 5 Extension)

When the test-architecture phase identifies multiple viable test strategies, the same protocol applies:

### Test Strategy Dimensions

For each dimension with multiple valid options:

1. **Testing approach**: unit-heavy vs integration-heavy
2. **Mock strategy**: mock externals vs real fixtures
3. **Coverage scope**: minimal (happy path + critical) vs exhaustive
4. **Organization**: per-function vs per-behavior vs per-scenario

### Protocol

```
1. Identify test strategy options (from the dimensions above)

2. Spawn Explore sub-agents per option (same protocol as above)
   Evaluation checklist adds test-specific criteria:
   - Coverage: what percentage of behavior is tested?
   - Runtime: how long will the test suite take?
   - Maintenance: how fragile are the tests to refactoring?
   - Fixture cost: how much test infrastructure is needed?
   - Confidence: how much confidence do the tests provide?

3. Check for surfaced unknowns

4. Present comparison with recommendation

5. User picks, combines, or refines
```
