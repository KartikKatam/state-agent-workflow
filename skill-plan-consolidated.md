# Consolidated Skill Plan

**Status:** Consolidated from `00-catalog.md` (skill list) + `03-skill-architecture.md` (architecture) + `ptc-packages-per-agent.md` (PTC packages)
**Supersedes:** `03-skill-architecture.md` Section 6 (old catalog), `00-catalog.md` (standalone catalog)
**Date:** 2026-02-28

---

## 1. Summary

**14 skills total:** 5 shared + 8 agent-primary + 1 meta

| Type | Count | Skills |
|------|-------|--------|
| Shared | 5 | `test-design`, `sub-agent-delegation`, `debugging`, `handoff-protocol`, `ptc-sandbox` |
| Agent-primary | 8 | `phase-planning`, `task-execution`, `code-design`, `codebase-exploration`, `research-methodology`, `scenario-testing`, `code-review`, `workflow-coordination` |
| Meta | 1 | `writing-skills` (already built) |

The addition from the previous 13-skill catalog: **`ptc-sandbox`** — a shared skill that teaches all agents how to leverage Programmatic Tool Calling for surgical context operations, with agent-specific package routing.

---

## 2. Skill Catalog

### 2.1 Shared Skills (loaded by multiple agents)

| # | Skill | Loaded By | What It Teaches |
|---|-------|-----------|-----------------|
| 1 | `test-design` | strategist, coder, tester | How to design good tests. Edge case reasoning, assertion design, fixture strategy, what makes a test meaningful vs fraudulent. Domain specializations: ML training, CV, robotics, benchmarking. |
| 2 | `sub-agent-delegation` | all agents | Full delegation lifecycle: scoping tasks for Haiku/Sonnet, parallel vs sequential, context sizing, error recovery, AND synthesis of results. Agent-specific routes (explorer → context packets, researcher → research entries, coder → reviewed implementation, tester → scenario code). |
| 3 | `debugging` | any agent (trigger-based) | 4-phase: reproduce → isolate → identify → fix. 3-strikes rule. When to scrap and handoff vs keep trying. Works for TDD green loops, gate failures, auditor fix cycles, scenario failures. |
| 4 | `handoff-protocol` | all agents | How for ALL cases: context pressure, scrap/retry, user pause, session end. What to preserve, how to structure handoff file. Infra forces WHEN; this teaches HOW. |
| 5 | `ptc-sandbox` | all agents (except orchestrator) | How to use PTC containers for surgical context operations. Core patterns (write code → tools execute in container → only print() returns). Agent-specific package routes. Graceful degradation when PTC unavailable. |

### 2.2 Agent-Primary Skills

| # | Skill | Primary Agent | What It Teaches |
|---|-------|--------------|-----------------|
| 6 | `phase-planning` | strategist | **Highest-leverage skill.** Design ingestion with gap identification, user interview. Evidence-based reasoning (all decisions backed by context, research, design doc, or verified Opus knowledge). Phase breakdown with rationale and verification. Task breakdown: parallelizable, individually verifiable, instructable with exact requirements/limitations/considerations/suggestions (NOT vague, NOT code templates). Task metadata: blocking dependencies, review_level (1/2/3), queries. Phase+task-level tests. JSON conversion with diff feedback. Write-then-discuss for handoff resilience. |
| 7 | `task-execution` | coder | **Forced uncertainty declaration**: must explicitly state know/don't-know. Any uncertainty → query explorer/researcher. Scope discipline. Plan adherence (absorbed from standalone). Handling auditor feedback at 3 levels (pass/fix/scrap). |
| 8 | `code-design` | coder | Design principles and style. Robust, modular, upgradeable, readable code. Test description logging before red-phase complete (what, how, why, considerations). |
| 9 | `codebase-exploration` | explorer | How to explore, how many sub-agents, what to keep in mind. Git-diff staleness (reference `files_analyzed`, check diffs — NOT timestamp-based). PTC-aware synthesis into context packets. |
| 10 | `research-methodology` | researcher | Source evaluation, MCP routing (Context7 primary, WebSearch fallback). Confidence scoring. All research always persists. PTC-aware synthesis. |
| 11 | `scenario-testing` | tester | Planning: behavior testing + edge cases + real data reasoning. 4 tiers (golden path, edge cases, adversarial, property-based). Per-phase AND full-design scope. Implementation via Sonnet sub-agents. Goes idle after implementation, receives execution triggers. Progressive blind failure reporting (describes behavior, coder stays blind to test implementation). Loads `test-design`. |
| 12 | `code-review` | auditor | **Task-level**: plan adherence, test robustness (discern fraudulent auto-passes from real tests), code quality, 3-level response (pass / fix-with-guidance / complete-scrap — scrap triggers handoff). **Phase-level**: independent exploration, check coder decisions/errors/scraps/retries in logs, thorough report (how code works, what tests test, all audit happenings). **Arbitration**: tester-coder disagreement resolution. **Final audit**: production readiness, full scenario suite, summary. Context loading: plan + PTC on design + context packets + query logs. |
| 13 | `workflow-coordination` | orchestrator | Picks highest-priority non-blocked tasks (done in planning step). Assigns coders (default Opus, only Sonnet for easy boilerplate). Spawns tester at phase start. Failure recovery: coder handoff/scrap handling, spawn fresh coders. Remediation management. NOT strategic decisions — those were made during planning. |

### 2.3 Meta Skill

| # | Skill | Purpose |
|---|-------|---------|
| 14 | `writing-skills` | Working tool for planning and creating skills. Already built at `skills/writing-skills/SKILL.md`. |

---

## 3. PTC Sandbox Skill Design

### 3.1 Purpose

`ptc-sandbox` is a shared skill that centralizes all PTC knowledge. Other skills remain PTC-agnostic — they teach *what* to accomplish (explore code, review changes, run tests). `ptc-sandbox` teaches *how* to leverage the container tooling to accomplish those goals with minimal context cost.

This separation means:
- Skills work without PTC (agents use Read/Grep/Glob/Bash directly, higher context cost)
- Skills work *better* with PTC (agents write Python in containers, only print() output enters context)
- No skill needs PTC-specific code patterns embedded in it
- Package lists and usage patterns update in one place

### 3.2 Directory Structure

```
skills/ptc-sandbox/
├── SKILL.md                    # Core PTC patterns, when to use PTC vs direct tools,
│                               # graceful degradation, ptc_execute usage
├── references/
│   ├── explorer.md             # 16 packages: AST parsing, metrics, graphs, git mining
│   ├── coder.md                # 12 packages: profiling, coverage, security, diffs
│   ├── auditor.md              # 13 packages + 3 hardening: metrics, coverage, blast radius
│   ├── tester.md               # 9 packages: hypothesis, coverage, profiling, complexity
│   ├── researcher.md           # 10 packages: extraction, documents, matching, data
│   ├── strategist.md           # 4 packages: graphs, metrics, tokens
│   ├── common-patterns.md      # Patterns shared across roles (tiktoken, radon, etc.)
│   └── domain-packages.md      # Domain-specific package overlays (robotics-cv, etc.)
├── scripts/
│   └── validate-packages.py    # Verify packages install cleanly, no conflicts
└── specializations/
    └── robotics-cv.md          # Domain-specific PTC patterns (OpenCV, torch, etc.)
```

### 3.3 SKILL.md Body — Core Content

The SKILL.md body (~2,500 tokens estimated) covers:

**1. When to Use PTC vs Direct Tools**
Decision table:
| Situation | Use PTC? | Why |
|-----------|----------|-----|
| Read 1-2 specific files | No | Direct Read is cheaper than container overhead |
| Analyze 5+ files, synthesize | Yes | 93% context savings (22KB → 1.6KB) |
| Run one test | No | Direct Bash is simpler |
| Run tests + coverage + analyze gaps | Yes | Multi-step pipeline, intermediate results stay in container |
| Simple grep for a function | No | Direct Grep is instant |
| Structural code search (callers, types) | Yes | tree-sitter/jedi provide precision Grep can't |

**2. Core Pattern**
```
ptc_execute(agent_id, role, code)
→ Code runs in persistent container
→ await tool_name(params) calls pause/resume
→ Variables persist across calls
→ Only print() output returns to agent context
```

**3. Graceful Degradation**
When PTC is unavailable (container fails, Docker not running, MCP not connected):
- Agent falls back to direct tool use (Read, Grep, Glob, Bash)
- Higher context cost but same functional outcome
- Skills don't change behavior — only the tooling layer differs

**4. Role Routing**
"For your role-specific packages and patterns, read `references/{your-role}.md`"

### 3.4 Agent-Specific Reference Files

Each `references/{role}.md` file contains:

1. **Package inventory** — what's installed, what category, what it does for this role
2. **Key patterns** — 3-5 code recipes showing the highest-value PTC operations for this role
3. **Anti-patterns** — common mistakes (e.g., printing raw DataFrames instead of summaries)
4. **Integration with role's skills** — how PTC enhances the judgment skills this role loads

#### Package Summary Per Role

| Role | Packages | Install Size | Key Capabilities |
|------|----------|-------------|------------------|
| Explorer | 16 | ~25 MB | AST parsing (tree-sitter, jedi), metrics (radon, vulture, cohesion), graphs (networkx), git mining (pydriller, gitpython) |
| Coder | 12 | ~15 MB | Profiling (pyinstrument, pympler, line-profiler), coverage (coverage, diff-cover), security (bandit), diffs (unidiff) |
| Auditor | 13 (+3 hardening) | ~20 MB | Metrics regression (radon, wily), coverage (diff-cover), blast radius (networkx), security (bandit, semgrep on-demand) |
| Tester | 9 | ~35 MB | Property-based testing (hypothesis), profiling (pyinstrument, big-o, memray), coverage gap analysis |
| Researcher | 10 | ~8 MB | Web extraction (trafilatura, readability-lxml), documents (pypdf, pdfplumber), matching (rapidfuzz) |
| Strategist | 4 | ~5 MB | Task DAG (networkx), complexity estimation (radon, cognitive-complexity), token measurement (tiktoken) |
| Orchestrator | — | — | No PTC. Coordinates agents via messages only. |

#### Hardening Packages (Auditor On-Demand)

| Package | Size | When Installed |
|---------|------|----------------|
| `semgrep` | ~55-60 MB | Final security/correctness audit (5000+ rules) |
| `pip-audit` | ~200 KB | Dependency vulnerability check |
| `scalene` | ~5-10 MB | Combined CPU+memory profiler for integration tests |

#### Domain-Specific Packages

```python
DOMAIN_PACKAGES = {
    "robotics-cv": ["opencv-python-headless", "scikit-image", "scipy",
                     "torch --index-url .../cpu", "torchvision --index-url .../cpu"],
    "data-engineering": ["polars", "duckdb", "sqlalchemy", "pandera"],
    "web-dev": ["flask", "fastapi", "cssutils", "html5lib"],
}
```

### 3.5 YAML Frontmatter

```yaml
---
name: ptc-sandbox
version: 1-0-0
triggers:
  - agent_role: explorer
    conditions: when performing multi-file analysis or context generation
  - agent_role: coder
    conditions: when running profiling, coverage analysis, or scope verification
  - agent_role: auditor
    conditions: when performing metrics analysis, blast radius, or security scanning
  - agent_role: tester
    conditions: when running property-based tests, coverage analysis, or profiling
  - agent_role: researcher
    conditions: when extracting content from web pages, PDFs, or synthesizing sources
  - agent_role: strategist
    conditions: when analyzing task dependencies or estimating complexity
description: >
  Use when an agent needs to perform multi-step analysis where intermediate
  results should NOT enter the context window. Use when packages like
  tree-sitter, hypothesis, trafilatura, or networkx would provide better
  results than raw file reads. Do NOT use for simple single-file reads,
  one-off greps, or running a single test command.
---
```

---

## 4. Three-Layer Loading Model

Every skill has three layers, each loaded at a different time with different size constraints.

### 4.1 Layer Overview

| Layer | When Loaded | Target Size | Contents | Loaded By |
|---|---|---|---|---|
| **1: Metadata** | Always (at agent spawn) | <100 words / ~150 tokens | YAML frontmatter in SKILL.md: name, version, trigger conditions | `subagent_start.py` collects all frontmatter |
| **2: Body** | On trigger match | <500 lines / ~3,000 tokens (soft limit) | SKILL.md markdown body: core workflow instructions, decision tables, critical rules | Agent reads SKILL.md via Read tool |
| **3: References** | On explicit demand | Unlimited | Patterns, edge cases, anti-patterns, examples in `references/` subdirectory | Agent reads specific file via Read tool |

### 4.2 Token Comparison: V1 vs V2

| Scenario | V1 Tokens | V2 Tokens | Reduction |
|---|---|---|---|
| Coder loads 3 skills (typical) | ~14,700 | metadata (150) + 1 body (3,000) = **3,150** | **78%** |
| Coder loads 2 skill bodies | ~14,700 | metadata (150) + 2 bodies (6,000) = **6,150** | **58%** |
| Explorer loads 0 skill bodies (reference-heavy) | ~4,900 | metadata (150) + 0 bodies = **150** | **97%** |
| Orchestrator loads 1 skill body | ~4,900 | metadata (150) + 1 body (3,000) = **3,150** | **36%** |

### 4.3 Why Three Layers, Not Two

Two layers (metadata + everything-else) would still front-load thousands of tokens of edge-case documentation. The three-layer model matches how agents actually use information:

- **Metadata:** "Should I care about this skill?" (every turn, implicitly)
- **Body:** "How do I execute this workflow?" (once at activation, stays in context)
- **References:** "What about this specific edge case?" (rarely, only when needed)

---

## 5. Skill Directory Structure

```
skills/{skill-name}/
├── SKILL.md              # Layers 1+2: YAML frontmatter + markdown body
├── references/           # Layer 3: detailed knowledge base
│   ├── patterns.md       # Proven approaches, code examples
│   ├── anti-patterns.md  # Known failure modes with explanations
│   └── edge-cases.md     # Boundary conditions, rare scenarios
├── scripts/              # Deterministic automation (enforcement level 6)
│   └── validate.py       # Validation, linting, gating scripts
├── schemas/              # Skill-local Pydantic schemas
│   └── *.schema.json     # JSON Schema exports (from Pydantic models)
└── specializations/      # Domain-specific extensions
    └── {domain}.md       # e.g., robotics-cv.md
```

### 5.1 Directory Conventions

| Directory | Loaded into context? | Purpose |
|---|---|---|
| `references/` | Only when agent reads a specific file | Deep knowledge that SKILL.md references but does not inline |
| `scripts/` | Never loaded into context | Executed by the agent via Bash tool; removes discretion (enforcement level 6) |
| `schemas/` | Never loaded into context | Consumed by hooks and validators; agents interact via structured output |
| `specializations/` | Only when domain matches | Domain-specific overlays loaded based on `Project Domain` in CLAUDE.md |

### 5.2 Why SKILL.md Combines Layers 1 and 2

YAML frontmatter and markdown body live in the same file because:
1. Single-file skill creation — contributors don't need to coordinate across files
2. Frontmatter extraction is trivial — Python YAML parsing splits frontmatter in a single pass
3. SKILL.md is the canonical entry point — agents always read SKILL.md to activate

---

## 6. YAML Frontmatter Specification

### 6.1 Schema

```yaml
---
name: string           # Skill identifier, matches directory name
version: string        # MODEL-REVISION-ADDITION format
triggers:              # When this skill should activate
  - agent_role: string # Which agent role (coder, explorer, orchestrator, etc.)
    states: [string]   # State machine states that activate this skill (optional)
    conditions: string # Free-text conditions beyond state (optional)
description: string    # ONLY trigger conditions. NEVER workflow summaries.
depends_on: [string]   # Other skills that should be loaded first (optional)
---
```

### 6.2 The CSO Rule: Descriptions Must Not Summarize Workflows

From `superpowers-synthesis.md` Section 15:

> Descriptions that summarize the skill's workflow cause agents to follow the description instead of reading the full skill.

**Descriptions contain ONLY:**
- When to activate ("Use when...")
- Trigger phrases and conditions
- Negative boundaries ("Do NOT use for...")

**Descriptions NEVER contain:**
- How the workflow operates
- Step sequences or process summaries
- Outcome descriptions

---

## 7. Enforcement Hierarchy

Six levels from weakest to strongest. Push enforcement to levels 5-6 wherever possible.

| Level | Type | Agent Can Override? | Example |
|---|---|---|---|
| 1 | Prose instructions | Yes, easily | "Please remember to validate output" |
| 2 | Imperative commands | Yes, under pressure | "Run validation before returning" |
| 3 | Explained reasoning | Reluctantly | "Validate because corruption is silent and undetectable" |
| 4 | Anti-pattern examples | Rarely | "WRONG: skip validation / CORRECT: always validate" |
| 5 | Structural dependencies | Cannot without breaking workflow | "Step 2 reads Step 1's output file" |
| 6 | Scripted automation | Cannot — no discretion | `python scripts/validate.py` |

### 7.1 Mapping to Skill Files

| Level | Where It Lives |
|---|---|
| 1-2 | SKILL.md body: prose and imperative instructions |
| 3 | SKILL.md body: "why" explanations paired with non-obvious instructions |
| 4 | `references/anti-patterns.md`: wrong/right pairs, loaded on demand |
| 5 | SKILL.md body + state machine: step sequencing with explicit dependencies |
| 6 | `scripts/` directory: validation scripts, quality gate runners |

### 7.2 Design Principle

Every critical rule must be backed by at least level 5 or 6. If a rule exists only at levels 1-3, it will eventually be rationalized away under pressure.

---

## 8. Why V2 Skills Are Ground-Up, Not Migrated

V1 skills enforced behavior through prose because that was the only mechanism. V2 has infrastructure:

| What V1 skills enforced | Where it lives in V2 |
|---|---|
| Write permissions per state | State machine `write_globs` + PreToolUse hook |
| TDD transition ordering | State machine transitions + guards |
| Session lifecycle (load, handoff, compact) | SessionStart/PreCompact/Stop hooks + context_monitor.py |
| Logging and commit timing | State machine commit actions + async hooks |
| Schema validation | PostToolUse write_validate handler + schema_validator.py |
| Context pressure management | PostToolUse context_pressure handler + context_monitor.py |

**V2 skills focus on what infrastructure can't enforce:** judgment, decision heuristics, anti-rationalization, workflow knowledge that requires understanding "why" not just "what."

### 8.1 Skill Design Process

V2 skills are written ground-up using:

1. **V1 skills as reference material** — extract judgment/knowledge, discard enforcement-via-prose
2. **Superpowers patterns** (`superpowers-synthesis.md`) — empirically tested through 6+ iterations of agent behavior testing, rationalization capture, and hardening
3. **Writing-skills meta-skill** — enforces structure, progressive loading, enforcement hierarchy
4. **TDD for documentation** — write skill → test against agent behavior → capture rationalizations → harden → re-test
5. **The question filter:** For every rule, ask: "Can infrastructure enforce this?" If yes, it's a hook/guard/state machine concern, not a skill.

---

## 9. Domain Specializations

### 9.1 Location

```
skills/{skill-name}/specializations/{domain}.md
```

### 9.2 Loading Trigger

The `Project Domain` declaration in CLAUDE.md controls which specializations load:

```markdown
## Project Domain
domain: robotics-cv
```

When an agent activates a skill (reads SKILL.md), the body checks for a matching specialization file. If `skills/{skill-name}/specializations/robotics-cv.md` exists, the body directs the agent to read it.

### 9.3 Current Specializations

| Specialization File | Parent Skill | Domain |
|---|---|---|
| `test-design/specializations/robotics-cv.md` | test-design | robotics-cv |
| `ptc-sandbox/specializations/robotics-cv.md` | ptc-sandbox | robotics-cv |

### 9.4 Adding a New Domain

Only requires:
1. Create `specializations/{domain}.md` files in relevant skill directories
2. Update `Project Domain` in CLAUDE.md

No core skill changes, no hook changes, no schema changes.

---

## 10. Loading Mechanism

### 10.1 Decision: Agent-Initiated Loading

The agent reads SKILL.md when triggered — not hook-injected.

| Factor | Agent-Initiated | Hook-Injected (rejected) |
|---|---|---|
| Context control | Agent decides when to load | Hook injects unconditionally |
| Latency | No additional hook latency | Adds file I/O to every hook call |
| Agent autonomy | Agent can skip if not needed | Hook cannot know agent's actual need |
| Size limits | Read tool has no size limit | Competes for MAX_CONTEXT_CHARS budget |

### 10.2 Runtime Flow

```
1. Agent spawned
   └── subagent_start.py extracts YAML frontmatter from all SKILL.md files
   └── Injects compact metadata catalog (~150 tokens):
       "SKILLS: ptc-sandbox(explorer+coder+auditor+...), debugging(trigger), ..."

2. Agent reads task assignment
   └── Checks metadata catalog against its role and current state
   └── Determines which skills match

3. Agent reads matching SKILL.md files
   └── post_tool_use.py handle_read_skill detects the Read
   └── Skill body is now in agent's context

4. During work, agent encounters edge case or needs packages
   └── SKILL.md body says: "Read references/{role}.md for package patterns"
   └── Agent reads the reference file (Layer 3, on demand)
```

### 10.3 Reference Implementation: Skill Metadata Injection

New section in `subagent_start.py` for skill metadata injection:

```python
def _build_skill_metadata_section(agent_type: str) -> str:
    """Extract YAML frontmatter from all SKILL.md files, format as compact catalog."""
    skills_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")) / ".claude" / "skills"
    if not skills_dir.exists():
        return ""

    entries = []
    for skill_dir in sorted(skills_dir.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        frontmatter = _extract_frontmatter(skill_md)
        if not frontmatter:
            continue

        name = frontmatter.get("name", skill_dir.name)
        triggers = frontmatter.get("triggers", [])
        relevant = any(
            t.get("agent_role") in (agent_type, "*")
            for t in triggers
        )
        if relevant:
            states = []
            for t in triggers:
                if t.get("agent_role") in (agent_type, "*"):
                    states.extend(t.get("states", []))
            state_str = "+".join(states[:3]) + ("+" if len(states) > 3 else "")
            entries.append(f"{name}({state_str})" if states else name)

    if not entries:
        return ""

    return f"=== SKILLS ===\n{', '.join(entries)}"
```

### 10.4 Trigger Matching

Intentionally simple — the agent does it:
1. Agent receives skill metadata catalog in spawn context
2. Agent knows its own role and current state
3. Agent compares: "Am I a coder? ptc-sandbox triggers for coder. I should read it."
4. Agent reads `skills/ptc-sandbox/SKILL.md`

---

## 11. Per-Role Skill Loading Summary

### 11.1 Which Skills Each Role Loads

| Role | Always Load (Layer 2) | On-Trigger (Layer 2) | PTC Reference (Layer 3) |
|---|---|---|---|
| **Orchestrator** | workflow-coordination | debugging | — (no PTC) |
| **Coder** | task-execution, code-design, ptc-sandbox | debugging, test-design | `references/coder.md` |
| **Explorer** | codebase-exploration, ptc-sandbox | sub-agent-delegation | `references/explorer.md` |
| **Researcher** | research-methodology, ptc-sandbox | sub-agent-delegation | `references/researcher.md` |
| **Strategist** | phase-planning, ptc-sandbox | test-design | `references/strategist.md` |
| **Tester** | scenario-testing, test-design, ptc-sandbox | debugging | `references/tester.md` |
| **Auditor** | code-review, ptc-sandbox | debugging | `references/auditor.md` |

Cross-cutting skills (`handoff-protocol`, `sub-agent-delegation`, `debugging`) load on demand when triggered — not upfront.

### 11.2 Token Budget Per Role

| Role | Skill Meta | Skill Bodies | PTC Ref (on-demand) | Estimated Total |
|---|---|---|---|---|
| Orchestrator | ~150 | 3,000 | — | **~3,150** |
| Coder | ~150 | 3,000 + 2,500 + 2,500 = 8,000 | ~1,500 | **~8,150** (+1,500 on demand) |
| Explorer | ~150 | 2,500 + 2,500 = 5,000 | ~2,000 | **~5,150** (+2,000 on demand) |
| Researcher | ~150 | 2,500 + 2,500 = 5,000 | ~1,500 | **~5,150** (+1,500 on demand) |
| Strategist | ~150 | 3,000 + 2,500 = 5,500 | ~800 | **~5,650** (+800 on demand) |
| Tester | ~150 | 3,000 + 3,000 + 2,500 = 8,500 | ~1,500 | **~8,650** (+1,500 on demand) |
| Auditor | ~150 | 2,500 + 2,500 = 5,000 | ~2,000 | **~5,150** (+2,000 on demand) |

PTC reference files are Layer 3 — loaded only when the agent actually uses PTC, not at skill activation.

---

## 12. Dissolved Skills (from previous catalogs)

| Original Skill | Where It Went |
|---|---|
| `tdd-discipline` | Infrastructure (state machine enforces TDD sequence, write_globs enforce file separation). Judgment distributed across: `test-design`, `code-design`, `debugging`, `task-execution` |
| `anti-rationalization` | Each discipline skill builds its own rationalization table. Pattern taught by `writing-skills`. |
| `plan-adherence` | Absorbed into `task-execution` (coder) and `code-review` (auditor) |
| `verification-before-completion` | Infrastructure forces gate (hooks block "done" without evidence). Each skill teaches what to verify. |
| `structured-synthesis` | Merged into `sub-agent-delegation` (delegation + synthesis = one skill) |
| `scenario-generation` | Merged into `scenario-testing` |
| `session-lifecycle` | Hooks (SessionStart, PreCompact, Stop) + context_monitor.py |
| `logging-and-commit` | State machine commit actions + async hooks |
| `push-workflow` | Simple enough for agent spec instructions |
| `MCP-research` | Merged into `research-methodology` |
| `persistent-research` / `ephemeral-research` | Merged into `research-methodology` — all research always persists |
| `git-history-analysis` | PTC handles git analysis; `codebase-exploration` covers when/why |
| `coding-memory` | Infrastructure (scribe writes to memory.db via hook) |
| `context-packets` | Merged into `codebase-exploration` — packet format is schema, exploration strategy is judgment |
| `implementation-plans` | Merged into `phase-planning` (now called `strategic-planning` → `phase-planning`) |
| `multi-perspective-analysis` | Merged into `phase-planning` — one technique within planning |

---

## 13. Infrastructure Changes Needed

Noted during workflow walkthrough — for when we build/update state machines and hooks:

| Change | Where | What |
|--------|-------|------|
| Git-diff staleness check | Explorer state machine + hook | Context packets reference `files_analyzed`; explorer checks git diffs instead of timestamps |
| Remove orchestrator context verification | System state machine | Infra validates context sufficiency, not orchestrator judgment |
| Forced uncertainty declaration | Coder state machine | CONTEXT_LOADED → TEST_DESIGN transition requires think output with explicit know/don't-know |
| Test description logging | Coder hooks + infra | Before TDD_RED, coder must log test descriptions (what, how, why, considerations) |
| Coder → Sonnet delegation pattern | Coder state machine + sub-agent-delegation skill | All coders are Opus, delegate code writing to Sonnet sub-agents, review before completing |
| 3-level auditor response | Auditor-task state machine | Pass / fix / scrap (scrap triggers handoff) |
| Tester spawned at phase start | System state machine | Confirm timing of `spawn_tester` in PHASE_ACTIVE → PHASE_IMPLEMENTATION |
| Handoff blocking annotation | Hooks | Infra forces handoff at threshold, doesn't just warn |
| Coder decision logging | Hooks | Coder must log decision-making processes |
| PTC availability detection | subagent_start.py or hook | Detect whether PTC MCP is connected; inform agent so it knows whether to load ptc-sandbox body |

---

## 14. Key Design Principles

1. **Skills encode judgment only** — infrastructure (state machine, hooks, PTC) handles enforcement
2. **Phases, not chunks** — milestone-based phases with tasks inside
3. **Audit levels per task** — strategist rates: 1 (no auditor/boilerplate), 2 (phase-end audit), 3 (full task auditor)
4. **Research always persists** — no ephemeral/persistent decision
5. **All coders are Opus** — delegate code writing to Sonnet sub-agents via PTC
6. **Forced uncertainty declaration** — coders must explicitly state what they know/don't know
7. **Git-diff staleness** — context packets reference `files_analyzed`, not timestamps
8. **Anti-rationalization is not standalone** — each discipline skill builds its own
9. **Verification before completion is infrastructure** — hooks enforce, skills teach what to verify
10. **Delegation and synthesis are one skill** — sub-agent-delegation covers both
11. **Test-design is shared** — strategist, coder, and tester all load the same skill
12. **Debugging is trigger-based** — any agent can use it when encountering failures
13. **TDD cycle is infrastructure** — state machine enforces sequence, not a skill
14. **Domain specializations augment skills** — `{skill}/specializations/{domain}.md`
15. **Write-then-discuss pattern** — for handoff resilience during planning
16. **Evidence-based reasoning** — all strategist decisions must be backed by verifiable sources
17. **For every rule, ask: "Can infrastructure enforce this?"** — if yes, NOT in the skill
18. **PTC is infrastructure, skill teaches judgment** — `ptc-sandbox` teaches WHEN and WHY to use PTC; the container runtime is infrastructure. Other skills remain PTC-agnostic.
19. **Graceful degradation** — every PTC-enhanced workflow must have a direct-tools fallback. Skills never assume PTC is available.

---

## 15. Key Decisions

### 15.1 Agent-Initiated vs Hook-Injected Loading

**Decision:** Agent-initiated (see Section 10.1).

Decisive factors:
- Hook injection competes for the MAX_CONTEXT_CHARS budget (hard cap at 3,000 chars)
- Agent-initiated loading uses the Read tool's natural channel — no size limit
- `handle_read_skill` handler already provides observability for agent-initiated reads
- Agent autonomy allows skipping irrelevant skills even when metadata suggests a match

### 15.2 500-Line Limit: Advisory with Linter Warning

**Decision:** Advisory, enforced by `writing-skills/scripts/validate.py` as a warning, not a blocking error.

Blocking on line count would create pressure to omit important instructions — the opposite of the intended effect.

`validate.py` reports:
- **INFO** at 400+ lines: "Consider moving detailed examples to references/"
- **WARN** at 500+ lines: "Exceeds guideline — review for content that can be deferred"
- No ERROR level for line count

### 15.3 Skill Metadata: YAML Frontmatter in SKILL.md, Not Separate Index

**Decision:** Frontmatter in SKILL.md. No separate index file.

- Single-file authoring — creating a skill requires only one file (SKILL.md)
- Frontmatter extraction is solved (read lines between `---` delimiters, parse as YAML)
- No synchronization problem between an index file and skill content
- `subagent_start.py` iterates `skills/*/SKILL.md` at spawn time — one glob, one pass

**Trade-off:** If skills directory grows to 30+, the glob-and-parse loop adds latency. Mitigation: cache parsed frontmatter in a generated JSON index (pre-commit hook or CI). Future optimization, not needed for 14 skills.

### 15.4 Skill Versioning

**Decision:** MODEL-REVISION-ADDITION format (same as Doc 0 schema versioning).

Version field in frontmatter: `version: 1-0-0`
- **MODEL** increments: Breaking changes to skill structure (sections removed, workflow rewritten)
- **REVISION** increments: Behavioral changes (new required step, modified enforcement)
- **ADDITION** increments: Additive changes (new reference file, new anti-pattern example)

Agents do not check skill versions at runtime — for human tracking and CI validation only.

---

## 16. Integration Points

| File | Action | Purpose |
|---|---|---|
| `hooks/post_tool_use.py` | No change | `handle_read_skill` already detects skill reads and emits `skill_loaded` events |
| `hooks/utils/event_logger.py` | No change | `emit_skill_loaded` already validates path pattern |
| `hooks/subagent_start.py` | Modify | Add `_build_skill_metadata_section()` to extract frontmatter and inject compact skill catalog |
| `scripts/token_budget_check.py` | Modify | Add skill body line counting and token estimation |
| `skills/writing-skills/scripts/validate.py` | Exists | Skill quality validation: frontmatter schema, line count, CSO compliance, reference integrity |
| PTC MCP server | Reference | `ptc-sandbox` skill references PTC container capabilities; server provides the runtime |

---

## 17. Deferred Schemas

Skill-local Pydantic schemas in `skills/{skill-name}/schemas/`. Implementation deferred to skill authoring phase.

| Skill | Schema File | Purpose |
|---|---|---|
| `codebase-exploration` | `codebase-context.schema.json` | Codebase-level context packet |
| `codebase-exploration` | `feature-context.schema.json` | Feature-specific context |
| `codebase-exploration` | `query-result.schema.json` | Explorer query result |
| `phase-planning` | `implementation-plan.schema.json` | Chunked plan: phases, tasks, dependencies |
| `test-design` | `test-plan.schema.json` | Test plan: tier design, scenario structure |
| `scenario-testing` | `test-result.schema.json` | Test execution result: pass/fail, coverage |
| `code-review` | `deviation-report.schema.json` | Plan deviation: what changed, why, approval |
| `research-methodology` | `research-entry.schema.json` | Research finding: content, confidence, sources |
| `research-methodology` | `research-index.schema.json` | Research index: topic registry, freshness |
| `handoff-protocol` | `handoff-state.schema.json` | Handoff file: preserved state, resume instructions |
| `ptc-sandbox` | `ptc-status.schema.json` | Container status: role, packages, execution count |

### 17.1 Schema Conventions

All skill-local schemas follow Doc 0 conventions:
- Pydantic models with `ConfigDict(extra="forbid")`
- `schema_version` field using MODEL-REVISION-ADDITION format
- `.model_dump(exclude_defaults=True, exclude_none=True)` for LLM injection
- JSON Schema exports generated from Pydantic models via `model_json_schema()`
- Stored as `.schema.json` files for hook-based validation

### 17.2 Schema Ownership

Each schema is owned by the skill that produces the data. Consuming skills reference the schema but do not modify it. Example: `codebase-exploration` owns `codebase-context.schema.json`. The `phase-planning` skill consumes context packets but validates against `codebase-exploration`'s schema, not a local copy.

---

## 18. Build Order

1. `writing-skills` — our tool (DONE)
2. `phase-planning` + `sub-agent-delegation` + `test-design` — highest-leverage, cross-cutting, shared by 3 agents
3. `ptc-sandbox` — enables all other skills to leverage PTC (can be built in parallel with #2)
4. Remaining 9 skills, each planned collaboratively with user

---

## 19. Rejected Skills

| Idea | Where It Lives Instead |
|------|----------------------|
| Merge conflict resolution | State machine + Doc 1 |
| Think-tool quality | Embedded in each judgment skill |
| Anti-sycophancy standalone | Sections within code-review and test-design |
| Problem-solving suite | Future v3 strategist enhancement |
| when-stuck | Fold into debugging |
| testing-anti-patterns | references/ inside test-design |
| defense-in-depth | references/ inside debugging |
| Scribe skill | Infrastructure (hooks, state machine) |

---

## 20. Community Research Per Skill

| Skill | Key Community Sources |
|-------|----------------------|
| `test-design` | obra/superpowers TDD, nizos/tdd-guard, trailofbits/property-based-testing, trailofbits/testing-handbook-skills |
| `sub-agent-delegation` | obra/superpowers subagent-driven-development + dispatching-parallel-agents, NeoLabHQ/context-engineering-kit SADD, dsifry/metaswarm, parcadei/Continuous-Claude-v3 (32 agents) |
| `debugging` | obra/superpowers systematic-debugging, glittercowboy/taches (Debug Like Expert, 5-Whys), wshobson/agents (hypothesis-driven) |
| `handoff-protocol` | parcadei/Continuous-Claude-v3 (YAML state transfers, 5-layer compression 95% token reduction, Ledger system), ZENG3LD/claude-session-restore |
| `phase-planning` | obra/superpowers writing-plans (2-5 min task granularity), dsifry/metaswarm (9-phase workflow), NeoLabHQ/context-engineering-kit SDD (Arc42-based specs) |
| `task-execution` | obra/superpowers executing-plans, EveryInc/compound-engineering-plugin, dsifry/metaswarm (4-phase execution) |
| `code-design` | ramziddin/solid-skills (SOLID, Law of Demeter), NeoLabHQ/context-engineering-kit (First Principles Framework, Reflexion Plugin) |
| `codebase-exploration` | parcadei/Continuous-Claude-v3 (TLDR 5-layer analysis ~1200 tokens from ~23000), kingbootoshi/cartographer, NeoLabHQ/context-engineering-kit (MAKER pattern) |
| `research-methodology` | parcadei/Continuous-Claude-v3 (oracle agent), wshobson/agents (parallel investigation), K-Dense-AI/claude-scientific-skills |
| `scenario-testing` | trailofbits/property-based-testing, trailofbits/testing-handbook-skills (fuzzing), affaan-m/everything-claude-code (AgentShield red/blue team) |
| `code-review` | sanyuan0704/code-review-expert (7-stage, P0-P3 severity), obra/superpowers requesting/receiving-code-review, NeoLabHQ/context-engineering-kit (6 specialized reviewer roles), trailofbits/differential-review |
| `workflow-coordination` | dsifry/metaswarm (9-phase, most mature), EveryInc/compound-engineering-plugin (compound learning), NeoLabHQ/context-engineering-kit (LLM-as-Judge) |
| `ptc-sandbox` | Anthropic PTC documentation, ipybox (Gradion AI), local `ptc-rewrite-plan.md`, `ptc-packages-per-agent.md` |

### 20.1 Potential New Skills from Research (not yet decided)

| Potential Skill | Source | Notes |
|----------------|--------|-------|
| Compound-Learning / Mistake-to-Pattern | EveryInc, parcadei ("Compound Don't Compact") | After each work cycle, extract learnings as reusable patterns |
| Security-Review | trailofbits (22 security skills), affaan-m (AgentShield) | Could be a code-review specialization |
| Context-Compression | parcadei (5-layer AST, ~1200 from ~23000 tokens) | Could enhance codebase-exploration and handoff-protocol |

### 20.2 Highest-Value Repositories for Deep Study

1. **obra/superpowers** — Battle-tested, most directly comparable
2. **dsifry/metaswarm** — Production multi-tenant SaaS, 18 agents, 100% test coverage
3. **parcadei/Continuous-Claude-v3** — Most advanced handoff/continuity, 5-layer compression, 109 skills
4. **NeoLabHQ/context-engineering-kit** — SADD, First Principles, Reflexion, LLM-as-Judge
5. **EveryInc/compound-engineering-plugin** — 80/20 rule, compound learning philosophy
6. **nizos/tdd-guard** — Hook-based TDD enforcement mechanism
7. **sanyuan0704/code-review-expert** — 7-stage review, P0-P3 severity
8. **wshobson/agents** — 112 agents, 146 skills, 4-tier model dispatching

---

## 21. Verification Criteria

- [ ] Every skill directory contains a valid SKILL.md with parseable YAML frontmatter
- [ ] Frontmatter `name` matches directory name for all 14 skills
- [ ] Frontmatter `description` contains only trigger conditions, no workflow summaries (CSO rule)
- [ ] `subagent_start.py` extracts and injects skill metadata catalog at spawn (<150 tokens)
- [ ] `handle_read_skill` emits `skill_loaded` event for every SKILL.md read
- [ ] No skill body exceeds 500 lines without documented justification
- [ ] `writing-skills/scripts/validate.py` passes on all 14 skills
- [ ] Per-role overhead measured by `token_budget_check.py`
- [ ] Coder overhead (skill meta + bodies) under 9,000 tokens
- [ ] Domain specializations load correctly when `Project Domain` is set
- [ ] All deferred schemas cataloged with owner skill and consumers identified
- [ ] Layer 3 reference files are reachable from SKILL.md body (no broken pointers)
- [ ] `ptc-sandbox` references/{role}.md exists for all 6 PTC-enabled roles
- [ ] `ptc-sandbox` graceful degradation path documented and testable
- [ ] `ptc-sandbox` package lists match `ptc-packages-per-agent.md` ROLE_PACKAGES dict
- [ ] Each skill's PTC integration (if any) routes through `ptc-sandbox`, not inline PTC instructions

---

## 22. PTC Production Readiness — Benchmark Results & Remaining Gaps

**Date:** 2026-03-07
**Source:** 27-task benchmark (`ptc-benchmark/`) comparing PTC agent vs Traditional agent (Read/Grep/Glob) on a 157-file Python codebase (58K LOC).

### 22.1 Benchmark Results Summary

**Overall:** PTC won 17/27 tasks, Traditional won 10/27.
**Token reduction:** 49.1% (PTC: 424K tokens vs Traditional: 833K).
**Tool call reduction:** 55% (PTC: 107 calls vs Traditional: 238).

9 of 10 Traditional wins are solvable with agent routing, skill patterns, and one infrastructure fix. 1 is irreducible container overhead on trivial tasks.

Full analysis: `ptc-benchmark/report.md` and `skills/ptc-sandbox/agentic_workflow/agent-routing-benchmark-findings.md`.

### 22.2 What Has Been Validated (27 tasks)

All tasks ran in real Docker containers (`ptc-sandbox:latest`, 277MB) via Unix socket IPC. Container mount verified at `/workspace`. Explorer role packages (16 packages) installed and functional.

| Category | Tasks | Validated Behavior |
|---|---|---|
| Single-file parsing | 1, 8, 12 | AST parsing, line counting, function/class extraction |
| Multi-directory aggregation | 2, 7, 11, 24 | os.walk across 157 files, accumulating dicts, LOC/class/function counts |
| Cross-file search | 3, 14, 16, 22 | Import tracking, symbol references, numpy usage mapping |
| Filtered extraction | 4, 23 | Substring filtering, prefix grouping, category assignment |
| Cross-reference correlation | 5, 17 | Matching ops modules to test files, checking test coverage |
| Deep comprehension | 6, 18, 20 | Pipeline data flow tracing, None-risk analysis, call graph construction |
| Large file handling | 13 | 5000-line file parsed for classes, fixtures, function lengths |
| Namespace persistence | 2, 24 | Variables surviving across ptc_execute calls within same agent_id |
| Large output handling | 25 | Output exceeding max_output_bytes cap (73KB) — exposed the limit |
| Multi-metric single-pass | 26 | 5 unrelated analyses in one code block |
| Error recovery | 27 | FileNotFoundError + missing binary files, graceful continuation |
| Container crash + recovery | 15 | SIGKILL (exit 137) after 14 tasks, agent_id switch recovery |
| Complexity computation | 21 | Branch counting via AST (if/for/while/try nodes) |
| API surface extraction | 19 | Public symbol enumeration, __init__.py export checking |
| Function signature analysis | 10 | Parameter extraction, type annotations, cross-file caller search |
| Config comparison | 15 | Set operations on dataclass fields across two files |

### 22.3 What Has NOT Been Tested — Must Validate Before Production

#### Container Lifecycle (Critical)

- [ ] **Long-running sessions (>30 min):** Container died at 6.5 min / 14 tasks (SIGKILL, exit 137). Need to validate 1-2 hour sessions with 50+ tasks. Is the OOM kill from namespace accumulation, Python process leaks, or Docker memory limits?
- [ ] **`execute_code_with_recovery()` wiring:** This method EXISTS in `container_manager.py:212` but `server.py:135` calls `execute_code()` instead. The health-check-before-execute path is completely dead code in production. Must wire it up and test.
- [ ] **Concurrent agents on shared container:** Two agent_ids using one container's REPL pool. Does per-agent locking work? Does one agent's crash corrupt the other's namespace?
- [ ] **Graceful shutdown on session exit:** Are containers orphaned when Claude Code terminates? Is cleanup reliable?
- [ ] **Namespace memory growth:** Measure pickle size after 10, 20, 50 calls. Is there a ceiling? Does `ptc_reset_namespace` actually free memory in the container process?

#### Write Operations (Blocking for coder/tester roles)

- [ ] **File writes inside container:** All 27 benchmark tasks were read-only. No task tested writing files to `/workspace`. Coder role needs this.
- [ ] **Code generation + execution loop:** Agent writes Python → PTC runs it → agent verifies output. The full coder workflow is untested.
- [ ] **`run_tests()` tool:** Exists in tool registry but never exercised. Can PTC run pytest inside the container and return structured pass/fail?
- [ ] **`run_linter()` tool:** Never called. Does it work with installed packages (radon, bandit, etc.)?

#### Role Routing (Blocking for all non-explorer agents)

- [ ] **Non-explorer roles:** All 27 tasks used `role="explorer"`. The coder, tester, researcher, strategist, and auditor roles were never exercised. Do their packages install correctly? Do their tool permissions work?
- [ ] **Role-specific tool restrictions:** Does a tester get blocked from write operations? Does an auditor get semgrep?
- [ ] **Package installation per role:** Explorer's 16 packages were verified. Coder's 12, auditor's 13+3, tester's 9, researcher's 10, and strategist's 4 are untested.

#### Security & Isolation (Blocking for production)

- [ ] **Network isolation verification:** `_isolate_network()` is called in container setup but never verified. Can code inside the container make outbound HTTP requests?
- [ ] **Filesystem escape:** `/workspace` is read-only mount, but are there writable paths code could abuse (`/tmp`, `/ptc_ipc`, etc.)?
- [ ] **Resource limits tuning:** The OOM kill suggests CPU/memory limits in `config.json` need review. What are the actual limits? Are they appropriate?
- [ ] **Malicious code integration test:** Fork bombs, infinite loops, disk fills through the full MCP path. Unit tests exist (`test_fork_bomb_blocked`, `test_disk_fill_tmpfs`) but integration tests through MCP are missing.

#### Edge Cases

- [ ] **Binary file handling through MCP:** What happens when PTC code `open()`s a binary file without error handling?
- [ ] **Unicode/encoding:** No task tested files with non-ASCII content, BOM markers, or mixed encodings.
- [ ] **Very large single files (>10K LOC):** Largest tested was ~5K lines. What about 50K-line generated files?
- [ ] **Timeout behavior end-to-end:** No task hit the execution timeout. Does the CancelMsg work? Does the namespace survive?
- [ ] **Empty workspace:** What happens when `/workspace` has no Python files?

#### Performance Baselines (Needed for routing decisions)

- [ ] **Container cold start time:** Measured ~15s (pip install) but not isolated from code generation latency. Need clean measurement.
- [ ] **IPC latency floor:** Estimated ~1.4s from Task 8 benchmark. Need isolated ping-pong measurement.
- [ ] **Namespace serialization cost:** How much does pickle save/load add per call as namespace grows?
- [ ] **Parallel `asyncio.gather()` throughput:** Mentioned in skill docs but never used in benchmark. What's the actual speedup?

### 22.4 Infrastructure Fixes Required (from benchmark)

| Fix | File | Effort | Impact |
|---|---|---|---|
| Wire `execute_code_with_recovery()` into `server.py` | `~/.claude/mcp/ptc-server/server.py:135` | 1 line change | Eliminates container crash retry cascades |
| Auto-reconnect on dead IPC socket | `container_manager.py:170-173` | ~20 lines | Same agent_id survives container restarts |
| Output size pre-check in skill docs | `skills/ptc-sandbox/references/anti-patterns.md` | Documentation | Prevents 73KB output → retry loops |
| Routing heuristic in SKILL.md | `skills/ptc-sandbox/SKILL.md` | Documentation | Prevents PTC usage on trivial grep tasks |

### 22.5 Verification Criteria (PTC-specific, extends Section 21)

- [ ] `execute_code_with_recovery()` is the default path in `server.py`
- [ ] Container survives 50+ consecutive `ptc_execute` calls without crash
- [ ] Each of the 6 PTC-enabled roles can install packages and execute code
- [ ] `run_tests()` and `run_linter()` tools return structured results
- [ ] Network isolation blocks outbound connections from container
- [ ] Container cleanup runs on session exit (no orphaned containers)
- [ ] IPC latency measured and documented (<2s for empty print)
- [ ] Namespace memory stays under container limit after 50 calls
- [ ] Agent routing benchmark findings integrated into skill references
