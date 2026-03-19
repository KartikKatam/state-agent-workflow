# Design Doc → Teammate .md Translation Walkthrough

Step-by-step process for translating architecture specs and director pattern specs into an operational teammate .md file. Includes concrete before/after examples showing what gets extracted, what gets dropped, and why.

---

## Table of Contents

1. [The Translation Pipeline](#pipeline)
2. [Step 1: Inventory the Inputs](#inventory)
3. [Step 2: Separate Agent-Relevant from Infrastructure](#separate)
4. [Step 3: Check Skill Coverage](#skills)
5. [Step 4: Write Frontmatter](#frontmatter)
6. [Step 5: Write the Body Section by Section](#body)
7. [Step 6: Audit for Leakage](#audit)
8. [Common Translation Mistakes](#mistakes)

---

## The Translation Pipeline {#pipeline}

```
Design Documents                    Agent .md File
─────────────────                   ──────────────

Architecture Spec ─┐               ┌─ Frontmatter
  §Role definition │               │  (name, description, tools,
  §Sub-agents      │               │   model, skills, hooks)
  §Skills          ├── Extract ──► │
  §Cross-domain    │               ├─ Body
  §Tools           │               │  §1 Identity & Mission
                   │               │  §2 Decision Frameworks
Director Pattern ──┤               │  §3 Sub-Agent Delegation
  §Extension pts   │               │  §4 Coordination Protocol
  §Think prompts   ├── Extract ──► │  §5 Output Contracts
  §Delegation comp │               │  §6 Self-Execute Boundaries
  §Self-execute    │               │  §7 Skill Routing
  §Domain verify   │               │
                   │               └─────────────────────
State Machine ─────┤
  §States          ├── DROP (infrastructure)
  §Transitions     │
  §Guards          │
                   │
Hook Definitions ──┤
  §PreToolUse      ├── DROP (infrastructure, explain WHY in body)
  §PostToolUse     │
  §SubagentStop    │
                   │
Validation Scripts ┤
  §validate_*.py   ├── DROP (infrastructure)
  §gate.sh         │
```

---

## Step 1: Inventory the Inputs {#inventory}

Before writing anything, create a checklist of source material:

```markdown
## Translation Inventory for: [Teammate Name]

### Architecture Spec
- [ ] Role definition (§3.X): read and noted
- [ ] Authorized sub-agents (§4.2): listed with models
- [ ] Skills (§3.X): listed, will read each
- [ ] Cross-domain relationships (§3.X, §6.2): noted
- [ ] Tool access (§3.X): noted

### Director Pattern Spec
- [ ] Extension points: all 17 reviewed
- [ ] Ingress context loading: noted what agent loads
- [ ] Deliberation think prompt / domain questions: captured
- [ ] Authorized sub-agents: confirmed matches architecture spec
- [ ] Delegation composition: captured per sub-agent type
- [ ] Model selection heuristic: captured
- [ ] Synthesis output type: captured
- [ ] Domain verification: captured
- [ ] Self-execute scope: captured
- [ ] Cross-domain partners: confirmed matches architecture spec
- [ ] Persistence model: noted
- [ ] Domain states: noted (for understanding, NOT for body)
- [ ] Domain error paths: noted (for understanding, NOT for body)

### Skills This Agent Loads
- [ ] [skill-1]: read SKILL.md, noted what it covers
- [ ] [skill-2]: read SKILL.md, noted what it covers
- [ ] ...

### Infrastructure Constraints
- [ ] Hook enforcement rules: listed (will explain WHY, not WHAT)
- [ ] State machine states: listed (for understanding, NOT for body)
- [ ] Validation scripts: listed (NOT for body)
```

**Why this step matters:** Skipping the inventory leads to either bloated agents (including infrastructure) or incomplete agents (missing coordination protocols). The inventory is the prerequisite check that prevents both.

---

## Step 2: Separate Agent-Relevant from Infrastructure {#separate}

Go through each extension point and classify it:

### Example: Explorer Director Pattern

| Extension Point | Classification | Reasoning |
|----------------|---------------|-----------|
| `INGRESS_CONTEXT_LOADING` — scan .claude/context/ for existing packets, load project structure into PTC | **Agent-relevant** (partial) | The agent needs to know WHAT to check on startup. But "load into PTC" is a mechanism detail — the agent just needs "check existing packets for reuse." |
| `DELIBERATION_THINK_PROMPT` — Q1-Q7 | **Agent-relevant** (transform) | The questions become decision frameworks. But the daemon injects think prompts — don't copy format, extract reasoning patterns. |
| `AUTHORIZED_SUB_AGENTS` — codebase-scout (Haiku or Sonnet) | **Agent-relevant** (direct) | Agent needs to know it dispatches scouts and how to choose models. |
| `DELEGATION_COMPOSITION` — partition, depth, questions, format, exclusions | **Agent-relevant** (direct) | Agent needs to know WHAT to include in delegation prompts. |
| `MODEL_SELECTION_HEURISTIC` — extraction=Haiku, analysis=Sonnet | **Agent-relevant** (direct) | Agent makes this decision per-dispatch. |
| `SYNTHESIS_OUTPUT_TYPE` — context packets to .claude/context/ | **Agent-relevant** (direct) | Agent needs to know what it produces. |
| `DOMAIN_VERIFICATION` — schema validation, partition coverage, duplicate detection, confidence floor, staleness guard | **Split** | Schema validation is hook-enforced (drop). Confidence floor, staleness awareness, and duplicate awareness are judgment calls (keep — agent decides thresholds). |
| `OUTPUT_WRITE_GLOBS` — .claude/context/** | **Infrastructure** (drop) | Hooks enforce file boundaries. |
| `POST_ACTIONS` — validate_context_packet_schema | **Infrastructure** (drop) | Runs automatically. |
| `PERSISTENCE_MODEL` — persistent, IDLE after delivery | **Infrastructure** (drop) | Daemon manages lifecycle. |
| `DOMAIN_STATES` — none beyond base | **Infrastructure** (drop) | State machine is infrastructure. |
| `DOMAIN_ERROR_PATHS` — base 4-path recovery | **Infrastructure** (partially) | Error recovery paths are daemon-managed, but the REASONING behind when to retry vs partial-synthesize is agent judgment. Extract the decision criteria, drop the state transitions. |

### What to Do With "Split" Items

When an extension point is partially agent-relevant:

1. **Extract the judgment** — what does the agent need to decide?
2. **Drop the mechanism** — how is the decision enforced/executed?
3. **Explain the WHY** — why does this mechanism exist?

Example for DOMAIN_VERIFICATION:

```markdown
# FROM design doc (infrastructure + judgment mixed)
1. Schema validation — packet validates against schema
2. Partition coverage — every assigned partition has findings
3. Duplicate detection — merge if equivalent exists
4. Confidence floor — below 0.3 gets needs_verification
5. Staleness guard — flag sections where files changed during exploration

# INTO agent .md (judgment only)
## Quality Awareness

Before delivering a context packet:
- Verify every partition you assigned to scouts has findings
  (a missing partition means a scout failed silently — re-dispatch)
- If a finding's confidence is below 0.3, mark it as needing
  verification — don't present uncertain findings as established
- Check if files changed during your exploration (git commits
  since you started) — flag affected sections as potentially stale
- Check if an equivalent packet already exists — merge rather
  than duplicating (deduplicate across overlapping boundaries)

Schema validation runs automatically on write (hook-enforced).
```

---

## Step 3: Check Skill Coverage {#skills}

For each piece of agent-relevant content from Step 2, check whether a loaded skill already covers it.

### Example: Explorer Loading `codebase-exploration` Skill

The `codebase-exploration` skill covers:
- Exploration modes (Full/Incremental/Query/Feature)
- Epistemic standards and confidence scoring
- 6-point checklist for scout returns
- Sub-agent count heuristic
- Staleness detection patterns

**Check each body section against skill coverage:**

| Planned Body Content | Skill Covers It? | Decision |
|---------------------|-------------------|----------|
| How to score confidence | Yes — epistemic standards section | **DROP from body** — skill teaches this |
| When to reuse vs re-explore | Partially — staleness detection, but not the full reuse decision | **KEEP in body** — add reuse decision table |
| Haiku vs Sonnet selection | No — skill doesn't cover model selection | **KEEP in body** — this is teammate-level judgment |
| What to include in scout delegation prompts | Partially — skill has delegation section, but not Explorer-specific context | **KEEP in body** — skill teaches HOW to delegate, body teaches WHAT to include |
| 6-point checklist for returns | Yes — skill's patterns.md | **DROP from body** — reference the skill |
| Partition overlap handling | No | **KEEP in body** — Explorer-specific concern |

**Result:** The body only contains what the skill DOESN'T cover. No duplication.

---

## Step 4: Write Frontmatter {#frontmatter}

With the inventory, classification, and skill audit done, frontmatter writes itself:

```yaml
---
# name: from architecture spec §3.X
name: explorer

# description: synthesize trigger conditions from architecture spec's
# role definition. Apply CSO Rule — no workflow details.
description: >
  [Write WHEN to delegate, not HOW it works]

# tools: from architecture spec §3.X tool access
tools: Read, Grep, Glob

# model: teammates use opus
model: opus

# skills: from Step 3 — only skills needed 80%+ of the time
skills:
  - codebase-exploration
  - sub-agent-delegation
  - context-packets

# memory: from director pattern PERSISTENCE_MODEL
memory: user

# hooks: from infrastructure constraints inventory
hooks:
  PostToolUse:
    - matcher: "Write|Edit"
      hooks:
        - type: command
          command: "./scripts/validate_context_packet_schema.sh"
---
```

---

## Step 5: Write the Body Section by Section {#body}

For each section, pull from the classified items in Step 2, filtered by skill coverage from Step 3.

### Section 1: Identity

**Source:** Architecture spec role definition + your judgment about behavioral tone.

**Process:** Read the role definition. Identify the core mission. Write a backstory that IMPLIES the behavioral standards rather than listing them.

### Section 2: Decision Frameworks

**Source:** Director pattern think prompt questions (transformed, not copied).

**Process:**
1. List all think prompt questions from DELIBERATION_THINK_PROMPT and domain questions
2. Group by decision type (reuse, partitioning, model selection, self-execute)
3. Convert each group into a decision table with conditions → actions → WHY
4. Remove questions that the skill already covers (Step 3 filter)
5. Remove questions about infrastructure routing (daemon handles those)

**Example transformation:**

```
# Think prompt question (design doc)
Q6: Are existing context packets fresh enough to reuse? Check packet
    timestamps against recent git activity for the files they reference.
    If stale or insufficient coverage, what gaps need new exploration?

Q7: For each partition identified in Q4 — Haiku or Sonnet? Haiku for
    extraction tasks. Sonnet for analysis tasks.

# Becomes decision tables (agent body)
## Context Reuse Decision
| Existing Packets | Freshness | Action | Why |
|...table rows...|

## Model Selection per Scout
| Task Characteristics | Model | Why |
|...table rows...|
```

### Section 3: Sub-Agent Delegation

**Source:** Director pattern AUTHORIZED_SUB_AGENTS + DELEGATION_COMPOSITION.

**Process:**
1. For each authorized sub-agent, create a delegation template
2. The template specifies WHAT context to include (from DELEGATION_COMPOSITION)
3. Add semantic review criteria (what to check that hooks can't)
4. Add partial return handling
5. Do NOT include the sub-agent-delegation skill's lifecycle (that's loaded separately)

### Section 4: Coordination

**Source:** Director pattern CROSS_DOMAIN_PARTNERS + architecture spec §6.2.

**Process:** Create the partner table from the design doc. Include message content descriptions. Reference the team messaging skill for format details.

### Section 5: Output Contracts

**Source:** Director pattern SYNTHESIS_OUTPUT_TYPE + DOMAIN_VERIFICATION (judgment parts only).

**Process:** List deliverables per task type, write Definition of Done checklist, include termination log spec.

### Section 6: Self-Execute Boundaries

**Source:** Director pattern SELF_EXECUTE_SCOPE.

**Process:** Extract the heuristic. The design doc has exhaustive lists of self-executable vs not-self-executable items — condense these into the boundary rule.

### Section 7: Skill Routing

**Source:** Step 3 results — skills NOT in frontmatter that the agent occasionally needs.

---

## Step 6: Audit for Leakage {#audit}

After writing the complete .md, do a final pass:

### Infrastructure Leakage Check

Read every line and ask: "Is this something the daemon/hook/state machine handles?"

**Red flags:**
- State names (SPAWNED, DELIBERATION, SYNTHESIS, DELIVERY)
- Transition language ("move to X state", "transition when")
- Guard conditions ("when X is true, proceed")
- Hook enforcement ("PreToolUse blocks", "SubagentStop validates")
- Validation script references ("scripts/validate_*.py runs")

### Skill Duplication Check

For every paragraph of domain knowledge, ask: "Is this in a loaded skill?"

**Red flags:**
- Confidence scoring methodology (→ codebase-exploration skill)
- Delegation lifecycle steps (→ sub-agent-delegation skill)
- Context packet schemas (→ context-packets skill)
- Research depth calibration (→ research-methodology skill)
- Test design principles (→ test-design skill)

### Token Audit

Count lines per section. Compare to targets:

```
Identity:          ___ / 5-15 lines
Decision Frames:   ___ / 30-80 lines
Delegation:        ___ / 20-50 lines
Coordination:      ___ / 20-40 lines
Output Contracts:  ___ / 15-30 lines
Self-Execute:      ___ / 10-20 lines
Skill Routing:     ___ / 5-15 lines
────────────────────────────
Total:             ___ / 200-350 lines
```

If total exceeds 350, audit the heaviest section. Usually Decision Frameworks or Delegation is the culprit — check if domain knowledge is leaking from skills.

---

## Common Translation Mistakes {#mistakes}

### Mistake 1: The Faithful Transcription

**What happens:** Writer converts every extension point into body content, including infrastructure details. Result: 600+ line agent body that conflicts with hooks and state machine.

**Fix:** Run the classification step (Step 2) rigorously. If it's in the "infrastructure" column, it doesn't go in the body.

### Mistake 2: The Bare Minimum

**What happens:** Writer drops too much. Agent body has identity and output contract but no decision frameworks or delegation templates. Result: agent with a personality but no judgment.

**Fix:** Decision frameworks (Section 2) are the highest-value section. If in doubt, spend your line budget there.

### Mistake 3: The Skill Copier

**What happens:** Writer reads the skills, finds them excellent, and copies key sections into the body "for emphasis." Result: same content in two places, divergence risk when skills are updated.

**Fix:** Trust the skill loading mechanism. The body references skills for domain knowledge, never copies them.

### Mistake 4: The Think Prompt Copier

**What happens:** Writer copies think prompt questions verbatim from the design doc into the body. Result: format collides with daemon-injected think prompts (daemon adds its own formatting).

**Fix:** Extract REASONING PATTERNS from think prompts, not the questions themselves. "When evaluating freshness, compare packet timestamps to git commits" — not "Q6: Are existing context packets fresh enough to reuse?"

### Mistake 5: The State Machine Narrator

**What happens:** Writer includes "After completing exploration, you move to SYNTHESIS state where you..." Result: agent tries to self-manage state transitions, conflicting with daemon routing.

**Fix:** Remove all state names and transition language. The agent doesn't need to know it's "in SYNTHESIS" — it needs to know WHAT to do when it has scout results to combine.
