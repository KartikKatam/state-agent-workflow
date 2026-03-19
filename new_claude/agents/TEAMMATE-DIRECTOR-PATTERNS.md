# Teammate Director Patterns — Per-Teammate Extension Point Design

## Purpose

This document fills in every extension point from `BASE-DIRECTOR-PATTERN.md` for each of the 6 V2 teammates. It is a **design reference** — consulted when writing the actual teammate `.md` agent definition files in `new_claude/agents/teammates/`.

**Relationship to other documents:**
- `AGENT-ARCHITECTURE.md` — defines WHAT each agent is (structural spec)
- `BASE-DIRECTOR-PATTERN.md` — defines HOW agents operate universally (behavioral spec)
- **This document** — defines HOW each specific teammate customizes the universal behavior (specialization spec)

**How to read:** Each teammate section follows the same structure — the 17 extension points from the base pattern, filled in with values, rationale, and source references. Extension points that use base pattern defaults without modification are noted as "Base default" with a brief explanation of why no customization is needed.

**Universal self-execute heuristic (applies to ALL teammates):**
If the teammate knows the question AND PTC can answer it in 1-2 commands → self-execute. If the teammate needs to explore/discover to find the answer, or the task requires multi-file analysis to synthesize new understanding → delegate to sub-agent. PTC-based lookups are not "reading source code yourself" — they are targeted queries that return concise structured results without polluting the teammate's context window.

**General sub-agents:** The BASE-DIRECTOR-PATTERN.md defines a general sub-agent mechanism (`sub_agent_type: "general"` + `skills_to_load`). This is available to the orchestrator only — for user-initiated ad-hoc spawns where no specialized sub-agent fits. Teammates do NOT spawn general sub-agents; they use their authorized specialized sub-agents, self-execute via PTC, or escalate to the user. This prevents the general sub-agent from becoming a bypass around specialized validation rules.

**Orchestrator-mediated sub-agent spawning (temporary constraint):** Teammates write delegation JSON to disk and message the orchestrator to spawn sub-agents on their behalf. This is a workaround for a current Claude Code limitation where teammates cannot use the Task tool directly. When this is resolved upstream, teammates will spawn sub-agents directly — hooks and the dashboard file provide observability independently of the orchestrator relay.

**Sub-agent resume and cold-start:** When dispatching a sub-agent that was previously active (e.g., plan-checker across multiple review gates), teammates attempt resume first (Agent tool `resume` parameter). If resume fails, they cold-start a fresh instance with the original delegation prompt + the previous sub-agent's structured return (including `previous_verification_report` for plan-checker). Both paths produce equivalent results — resume is faster, cold-start is reliable. See SUB-AGENT-SPECS Common Conventions for details.

---

## 1. Explorer

**Role summary:** Codebase context generator. Dispatches codebase-scout sub-agents to read and analyze source code, synthesizes their findings into structured context packets consumed by other teammates. Persistent across session.

**Sources consulted:**
- `AGENT-ARCHITECTURE.md` §3.2 (Explorer definition, behavior, skills, cross-domain)
- `AGENT-ARCHITECTURE.md` §4.2 (codebase-scout sub-agent definition)
- `AGENT-ARCHITECTURE.md` §5.1, §5.5 (dispatch rules, model selection)
- `BASE-DIRECTOR-PATTERN.md` §2-§7 (lifecycle phases, extension point API)
- `state-machines/explorer.json` (V1 Explorer — 13 states, 20 transitions)
- `new_claude/skills/codebase-exploration/SKILL.md` (exploration modes, epistemic standards, confidence scoring)
- `new_claude/skills/sub-agent-delegation/SKILL.md` (delegation lifecycle, dispatch modes, context sizing)
- `new_claude/skills/context-packets/` (context packet schemas)

### V1 State Machine Alignment

The V1 Explorer (`state-machines/explorer.json`, v1.3.0) has 13 states. The base director pattern has 14. Here is how V1 states map to the base pattern, and what changes for the sub-agent-heavy V2 approach.

| V1 State | Base Pattern State | Change |
|----------|-------------------|--------|
| `SPAWNED` | `SPAWNED` | No change |
| `EXISTING_CONTEXT_CHECK` | `CONTEXT_LOADING` | **Folded.** V1 combined loading + reuse decision in one state. V2 splits: CONTEXT_LOADING loads existing packets, DELIBERATION decides reuse vs explore. The reuse decision moves to DELIBERATION think prompt Q6. |
| `SCOPE_ANALYSIS` | `DELIBERATION` | **Folded.** V1's scope analysis (partitioning, sub-agent count, model selection) IS the deliberation. Q4 (universal) handles partitioning, Q7 (domain) handles model selection. |
| `AWAITING_CLARIFICATION` | `AWAITING_CLARIFICATION` | No change. Max 2 rounds preserved. |
| `SUB_AGENT_DISPATCH` | `SUB_AGENT_DISPATCH` | **Changed mechanism.** V1 dispatched sub-agents directly via Task tool. V2 uses orchestrator-mediated spawn: Explorer writes delegation JSON → messages orchestrator → orchestrator spawns. State behavior (waiting for results, think-on-exit assessment) is the same. |
| `SYNTHESIS` | `SYNTHESIS` | **Enhanced.** V2 adds PTC as synthesis engine — raw sub-agent results stay in PTC container, Explorer synthesizes there and writes the final context packet. |
| `PACKET_VALIDATION` | `OUTPUT_VALIDATION` | **Renamed.** Same function: schema validation, duplicate detection, merge logic. |
| `PACKET_WRITTEN` | `DELIVERY` | **Renamed.** Same function: notify requester, confirm receipt. |
| `IDLE` | `IDLE` | No change |
| `QUERY_RELATEDNESS_ASSESSMENT` | `RELATEDNESS_ASSESSMENT` | No change. Think prompt preserved. |
| `TERMINATED` | `TERMINATED` | No change |
| `ERROR` | `ERROR` | No change. V1 already implemented all 4 recovery paths (RETRY, PARTIAL_SYNTHESIS, HANDOFF, ABORT) with the same guards and think prompt structure the base pattern formalizes. |
| `HANDOFF` | `HANDOFF` | No change |

**States eliminated by folding:** 2 (EXISTING_CONTEXT_CHECK and SCOPE_ANALYSIS merged into base CONTEXT_LOADING and DELIBERATION)

**States added:** 1 (SELF_EXECUTING — base pattern state, needed because V2 Explorer can self-execute PTC lookups)

**Net state count:** V1 had 13 states. V2 base provides 14. Explorer adds 0 domain states. Explorer uses 14 states total (the base pattern as-is).

**Why folding doesn't reduce effectiveness:** The V1 split EXISTING_CONTEXT_CHECK and SCOPE_ANALYSIS into two states primarily to get two separate think invocations. In V2, the deliberation think prompt covers both decisions (reuse? + partition?) in 7 questions total — a single think invocation with the same decision quality at lower token cost. The two decisions are logically coupled: you can't decide how to partition until you know what existing coverage you can reuse.

### Extension Points

#### `INGRESS_CONTEXT_LOADING`

**What the Explorer loads on spawn:**
1. Scan `.claude/context/` for existing context packet filenames and their `meta.timestamp` fields (read metadata only, not full packet content — token efficiency)
2. Project directory structure (top-level `ls` or tree) loaded into PTC persistent container
3. The query/task assignment from the orchestrator message
4. If this is a replacement spawn (after HANDOFF), read the handoff summary from `.claude/handoffs/`

**What it does NOT load:**
- Full content of existing context packets (deferred to DELIBERATION if reuse is being considered)
- Source code files (that's the scouts' job)
- Research files or plans (not Explorer's domain)

**Rationale:** The V1 EXISTING_CONTEXT_CHECK state tried to determine freshness by reading packets AND checking git log in one pass. V2 separates: CONTEXT_LOADING scans what exists (cheap), DELIBERATION decides what to do about it (including freshness checks via PTC git commands). The PTC persistent container from AGENT-ARCHITECTURE.md §3.2 is initialized here and persists across multiple queries for this Explorer instance.

#### `DELIBERATION_THINK_PROMPT` / `DOMAIN_THINK_QUESTIONS`

Universal questions Q1-Q5 are inherited unchanged. Domain questions appended:

```
Q6: Are existing context packets fresh enough to reuse? Check packet timestamps
    against recent git activity for the files they reference. If stale or
    insufficient coverage, what gaps need new exploration?
Q7: For each partition identified in Q4 — Haiku or Sonnet? Haiku for extraction
    tasks (file listing, signature extraction, known-path reads). Sonnet for
    analysis tasks (dependency tracing, pattern recognition, architectural
    assessment). Only evaluate if Q4 chose multiple scouts.
```

**Total: 7 questions.** Q4 (universal) handles partitioning strategy. Q3 (universal) handles the self-execute decision using the PTC heuristic. Q6-Q7 are Explorer-specific knowledge the universals cannot cover.

**What was intentionally excluded:**
- Exploration mode selection (Full/Incremental/Query/Feature). These modes from the codebase-exploration skill describe prompt composition strategies, not state transitions. The mode is implicit in the query content and affects the delegation prompt, not the state machine. The skill teaches prompt composition; the state machine doesn't need to name modes.

#### `AUTHORIZED_SUB_AGENTS`

| Sub-Agent Type | Model | Notes |
|---------------|-------|-------|
| `codebase-scout` | Haiku or Sonnet (per dispatch, see Q7) | Primary and only authorized sub-agent |



**Rationale:** AGENT-ARCHITECTURE.md §3.2 and §5.1 authorized dispatcher table. Explorer dispatches only codebase-scout. The architecture intentionally keeps Explorer single-sub-agent — exploration partitioning achieves parallelism by dispatching N scouts simultaneously, not by having different sub-agent types.

#### `DELEGATION_COMPOSITION`

**Primary delegation type:** `exploration`

**Delegation prompt structure per codebase-scout:**
1. **Partition assignment** — specific directories, modules, or file patterns this scout covers
2. **Depth requirement** — surface structure vs deep implementation details
3. **Specific questions** — what the scout should answer about its partition
4. **Output format reference** — pointer to codebase-scout return format (delegation_type: "exploration")
5. **Exclusions** — what NOT to explore (prevents scouts from wandering into each other's partitions)

**Context included in delegation prompt:**
- Project structure overview (from PTC persistent container)
- Relevant file paths within the partition
- Any existing partial context to avoid re-exploring (if Q6 found partial coverage)

**Context NOT included:**
- Full content of existing context packets (too large, scouts explore fresh)
- Other scouts' partition assignments (scouts are independent)
- Cross-domain information (research, plans)

**Rationale:** Sub-agent-delegation skill Step 2 (context sizing — 2-Read test, 30% test). AGENT-ARCHITECTURE.md §4.2 codebase-scout return format. Each scout gets a self-contained prompt with exactly what it needs for its partition.

#### `MODEL_SELECTION_HEURISTIC`

| Task Characteristics | Model | Examples |
|---------------------|-------|---------|
| Known file paths, extraction, pattern matching, structure listing | **Haiku** | "List all public functions in `src/pipeline.py`", "Extract class hierarchy from `src/agents/`", "Find all files importing `BaseModel`" |
| Dependency tracing, pattern analysis, architectural assessment, unknown structure | **Sonnet** | "Analyze the data flow through the processing module", "Map the error handling patterns in `src/core/`", "Trace how config propagates from entry point to subsystems" |
| Mixed or uncertain | **Sonnet** | Default when task characteristics don't clearly favor Haiku |

**Rationale:** AGENT-ARCHITECTURE.md §5.5. Sonnet is the safe default. Haiku is an optimization for tasks where the scout's job is mechanical extraction, not reasoning. The Explorer teammate makes this decision per-scout during DELIBERATION (Q7).

#### `SYNTHESIS_OUTPUT_TYPE`

**Produces:** Structured context packets written to `.claude/context/`

| Packet Type | Schema | When Produced |
|-------------|--------|---------------|
| Codebase overview | `context-packets/schemas/codebase.schema.json` | Full codebase exploration |
| Feature context | `context-packets/schemas/feature-context.schema.json` | Feature-scoped exploration |
| Query result | `context-packets/schemas/query-result.schema.json` | Targeted question answer |

**Synthesis process:**
1. Collect all codebase-scout returns in PTC container
2. Deduplicate findings across scout partitions (overlap at boundaries is expected)
3. Resolve contradictions (if scout A says function exists but scout B implies it doesn't — re-check via PTC)
4. Aggregate confidence scores using codebase-exploration skill epistemic standards (direct evidence: 0.8-1.0, inference: 0.6-0.8, indirect: 0.3-0.6)
5. Structure into appropriate packet type
6. Write to `.claude/context/`

**Rationale:** AGENT-ARCHITECTURE.md §3.2 ("writes structured context packet"). Codebase-exploration skill epistemic standards. Context-packets skill schemas.

#### `DOMAIN_VERIFICATION`

Checks applied during SYNTHESIS / OUTPUT_VALIDATION:

1. **Schema validation** — packet validates against the appropriate context-packet schema
2. **Partition coverage** — every partition assigned to a scout in DELIBERATION has corresponding findings in the synthesized packet. Missing partitions flagged.
3. **Duplicate detection** — check if an equivalent packet already exists at the target path. If so, merge (V1 had this as a guard: `duplicate_packet_exists` → `merge_into_existing_packet`).
4. **Confidence floor** — any finding below 0.3 confidence gets `needs_verification: true`. Packet-level confidence is the weighted average of finding-level scores.
5. **Staleness guard** — if the packet references files that changed during exploration (git commits between CONTEXT_LOADING and SYNTHESIS), flag those sections as potentially stale.

**Rationale:** V1 PACKET_VALIDATION guards (`packet_schema_valid`, `no_duplicate_packet_exists`, merge logic). Codebase-exploration skill epistemic standards. The staleness guard is new for V2 — V1 didn't check for concurrent modifications.

#### `OUTPUT_WRITE_GLOBS`

```
.claude/context/**
```

Only context packets. Explorer never writes source code, test code, plans, research, or logs.

**Rationale:** AGENT-ARCHITECTURE.md §3.2. V1 SYNTHESIS write_globs.

#### `POST_ACTIONS`

```
Write → validate_context_packet_schema
Edit  → validate_context_packet_schema
```

Every write to `.claude/context/` triggers schema validation. This is mechanical (hook-enforced), not self-review.

**Rationale:** V1 SYNTHESIS post_actions, preserved unchanged.

#### `PERSISTENCE_MODEL`

**Persistent.** Explorer goes to IDLE after delivery, stays alive for subsequent queries. Terminated when:
- RELATEDNESS_ASSESSMENT determines new query is unrelated → TERMINATED (orchestrator spawns fresh Explorer)
- No more pending queries → TERMINATED
- Context pressure → HANDOFF (orchestrator spawns replacement)

**Rationale:** AGENT-ARCHITECTURE.md §3.2: "Persistent across session, /clear at context pressure." Persistent because the Explorer's PTC container caches project structure — discarding it between queries wastes the setup cost.

#### `DOMAIN_STATES`

**None added.** The Explorer's domain maps entirely to the base pattern lifecycle:

- Context loading: base `CONTEXT_LOADING`
- Reuse/partition decision: base `DELIBERATION`
- Scope clarification: base `AWAITING_CLARIFICATION`
- PTC self-lookups: base `SELF_EXECUTING`
- Scout dispatch: base `SUB_AGENT_DISPATCH`
- Result synthesis: base `SYNTHESIS`
- Packet validation: base `OUTPUT_VALIDATION`
- Delivery + confirmation: base `DELIVERY`
- Idle between queries: base `IDLE`
- Relatedness check: base `RELATEDNESS_ASSESSMENT`
- Error + 4-path recovery: base `ERROR`
- Context pressure: base `HANDOFF`

The Explorer is the simplest teammate because its domain — exploring code and producing context packets — IS the base lifecycle of "receive work → deliberate → delegate → synthesize → deliver." No domain-specific state clusters are needed.

#### `DOMAIN_ERROR_PATHS`

**None added.** The base 4-path recovery (RETRY, PARTIAL_SYNTHESIS, HANDOFF, ABORT) covers all Explorer failure modes:

| Failure | Recovery Path | Rationale |
|---------|--------------|-----------|
| Scout timeout/crash (transient) | RETRY | Re-dispatch to SCOPE_ANALYSIS with adjusted scope. Max 1 retry. |
| Scout returned empty/wrong results (structural) | PARTIAL_SYNTHESIS | Synthesize whatever scouts succeeded. Mark packet as partial with `meta.completeness: "partial"` and `meta.missing_sections` populated. |
| Explorer context pressure during synthesis | HANDOFF | Write handoff summary with partial findings. Replacement Explorer continues. |
| Target paths don't exist / query fundamentally malformed | ABORT | No salvageable work. Notify requester of failure. |

**Rationale:** V1 ERROR state already implemented exactly these 4 paths with identical guards and think prompt structure. No Explorer-specific failure modes exist beyond what the base pattern covers.

#### `SELF_EXECUTE_SCOPE`

**Self-executable (teammate does directly):**
- **PTC targeted queries** — any lookup where the Explorer knows what it's looking for and PTC returns a concise answer in 1-2 commands. Examples:
  - `ls -la src/` to check directory structure
  - `git log --oneline -5 -- src/module.py` to check recent changes for staleness
  - `grep -c "class " src/models.py` to count classes in a known file
  - `head -20 .claude/context/existing-packet.json` to read packet metadata
- **Reading existing context packets** — full or partial, for freshness evaluation or synthesis input
- **Writing synthesized context packets** — the synthesis output (this is the Explorer's primary write)
- **Sending messages** — to orchestrator, requesting teammates, or cross-domain partners

**NOT self-executable (must delegate to codebase-scout):**
- **Broad exploration** — reading multiple source files to build understanding ("map the architecture of src/agents/")
- **Multi-file analysis** — synthesizing across files where the answer isn't known in advance ("how does data flow from input to output?")
- **Pattern discovery** — finding patterns across the codebase when you don't know what you're looking for

**The boundary:** Known question + PTC answers in 1-2 commands → self-execute. Unknown answer + requires reading/analyzing source files → delegate. The test is whether the Explorer can articulate the exact command to run BEFORE running it. If yes, PTC. If it needs to explore to figure out what to ask, that's a scout's job.

#### `CROSS_DOMAIN_PARTNERS`

| Partner | Direction | Message Type | Purpose |
|---------|-----------|-------------|---------|
| Researcher | Explorer → Researcher | `info_request` | API/library documentation needed to complement codebase context (e.g., "what's the expected API for this external dependency?") |
| All teammates | Any → Explorer | `info_request` | Context requests ("I need to understand module X before I can plan/implement/test") |
| Orchestrator | Explorer ↔ Orchestrator | `info_request` / `info_ready` / `task_complete` | Clarification requests, sub-agent spawn mediation, delivery confirmation |

**Rationale:** AGENT-ARCHITECTURE.md §3.2 (cross-domain), §6.2 (communication diagram). Explorer is the most-messaged teammate — every other teammate needs codebase context at some point.

---

## 2. Researcher

**Role summary:** External information gatherer. Dispatches research-scout sub-agents to search documentation, web sources, and APIs, synthesizes their findings into structured research files consumed by other teammates. Persistent across session.

**Sources consulted:**
- `AGENT-ARCHITECTURE.md` §3.3 (Researcher definition, behavior, skills, cross-domain)
- `AGENT-ARCHITECTURE.md` §4.2 (research-scout sub-agent definition)
- `AGENT-ARCHITECTURE.md` §5.1, §5.5 (dispatch rules, model selection)
- `BASE-DIRECTOR-PATTERN.md` §2-§7 (lifecycle phases, extension point API)
- `state-machines/researcher.json` (V1 Researcher — 14 states, 24 transitions)
- `new_claude/skills/research-methodology/SKILL.md` (research depth calibration, decomposition, cache/staleness checks)
- `new_claude/skills/sub-agent-delegation/SKILL.md` (delegation lifecycle, dispatch modes, context sizing)

### V1 State Machine Alignment

The V1 Researcher (`state-machines/researcher.json`, v1.2.0) has 14 states. It mirrors the Explorer structurally but adds two domain-specific states: `SEARCH_STRATEGY_FALLBACK` and `ADDITIONAL_SEARCH`.

| V1 State | Base Pattern State | Change |
|----------|-------------------|--------|
| `SPAWNED` | `SPAWNED` | No change |
| `EXISTING_RESEARCH_CHECK` | `CONTEXT_LOADING` | **Folded.** V1 combined loading research index + reuse decision. V2 splits: CONTEXT_LOADING scans research index, DELIBERATION decides reuse vs new research. Same rationale as Explorer. |
| `QUERY_ANALYSIS` | `DELIBERATION` | **Folded.** V1's search strategy analysis (MCP vs WebSearch, sub-agent count, confidence requirements) IS the deliberation. |
| `AWAITING_CLARIFICATION` | `AWAITING_CLARIFICATION` | No change. Same pattern as Explorer. |
| `SUB_AGENT_DISPATCH` | `SUB_AGENT_DISPATCH` | Changed to orchestrator-mediated spawn. |
| `SEARCH_STRATEGY_FALLBACK` | **Domain state — kept** | Researcher-specific recovery: MCP fails → switch to WebSearch (or vice versa). Not an error — the research goal is unchanged, only the tool changes. See Domain States section for full rationale. |
| `ADDITIONAL_SEARCH` | **Eliminated** | Folded into the base SYNTHESIS → SUB_AGENT_DISPATCH retry loop. V1 used this state to track "how many additional search rounds." V2 expresses this as `max_occurrences` on the SYNTHESIS → SUB_AGENT_DISPATCH retry transition. The SYNTHESIS think prompt already evaluates "results sufficient?" and can choose RETRY to dispatch additional scouts with refined prompts. No separate state needed. |
| `SYNTHESIS` | `SYNTHESIS` | Enhanced with PTC as synthesis engine. |
| `RESEARCH_WRITTEN` | `DELIVERY` | **Renamed.** Same function: update research index, notify requester. |
| `IDLE` | `IDLE` | No change |
| `QUERY_RELATEDNESS_ASSESSMENT` | `RELATEDNESS_ASSESSMENT` | No change |
| `ERROR` | `ERROR` | Same 4-path recovery. V1 already implemented RETRY, PARTIAL_SYNTHESIS, HANDOFF, ABORT with identical structure. |
| `TERMINATED` | `TERMINATED` | No change |
| `HANDOFF` | `HANDOFF` | No change |

**States eliminated by folding:** 3 (EXISTING_RESEARCH_CHECK, QUERY_ANALYSIS → base states; ADDITIONAL_SEARCH → eliminated)

**States added:** 0 new, 1 retained domain state (SEARCH_STRATEGY_FALLBACK), 1 base state now used (SELF_EXECUTING)

**Net state count:** V1 had 14 states. V2 base provides 14. Researcher adds 1 domain state (SEARCH_STRATEGY_FALLBACK). Researcher uses 15 states total.

**Why ADDITIONAL_SEARCH elimination doesn't reduce effectiveness:** The V1 `ADDITIONAL_SEARCH` state existed to track retry count via `max_occurrences: 2` on its self-loop and to represent "dispatching more scouts for deeper coverage." In V2, SYNTHESIS already evaluates result quality and can choose RETRY when coverage is insufficient. The retry dispatches scouts with refined prompts (narrower scope, different search terms, adjusted sources) based on what the first pass revealed. The max 2 additional rounds cap moves to a `max_occurrences` guard on the SYNTHESIS → SUB_AGENT_DISPATCH transition. Same behavior, one less state.

### Extension Points

#### `INGRESS_CONTEXT_LOADING`

**What the Researcher loads on spawn:**
1. Scan `.claude/research/_index.json` for existing research topics, keywords, and `completed_at` timestamps (metadata only — not full research content)
2. The research query/task assignment from the orchestrator message
3. If this is a replacement spawn (after HANDOFF), read the handoff summary from `.claude/handoffs/`

**What it does NOT load:**
- Full content of existing research files (deferred to DELIBERATION if reuse is being considered)
- Codebase context (not Researcher's domain — if needed, message Explorer)
- Plans or implementation details

**Rationale:** Mirrors Explorer's approach: CONTEXT_LOADING scans what exists (cheap), DELIBERATION decides what to do. The research index is the Researcher's equivalent of Explorer's context packet scan. PTC persistent container is initialized here for synthesis work later.

#### `DELIBERATION_THINK_PROMPT` / `DOMAIN_THINK_QUESTIONS`

Universal questions Q1-Q5 are inherited unchanged. Domain questions appended:

```
Q6: Does existing research cover this query? Check cache staleness per
    research-methodology rules: 30-day window for actively-evolving topics
    (libraries in active development), 90-day for stable topics (established
    patterns, mature libraries). If cached research is stale or has insufficient
    confidence, what gaps need new research?
Q7: What research depth is needed? Match to the depth calibration table:
    - Simple factual (yes/no, one signature) → self-execute via PTC, 0 scouts
    - Focused lookup (API pattern, gotchas) → self-execute or 1 scout
    - Multi-facet (comparison, best practices) → 2-3 scouts, parallel by facet
    - Deep evaluation (new tech, architecture) → 3-4 scouts, structured avenues
```

**Total: 7 questions.** Q4 (universal) handles sub-agent count and decomposition into research avenues. Q3 (universal) handles the self-execute decision (simple factuals via PTC). Q6-Q7 are Researcher-specific: cache policy and depth calibration.

**What was intentionally excluded:**
- MCP vs WebSearch tool selection. This is a delegation prompt concern, not a state machine concern. The Researcher specifies source preferences in the scout's delegation prompt. If the primary tool fails at runtime, the SEARCH_STRATEGY_FALLBACK domain state handles the switch.

#### `AUTHORIZED_SUB_AGENTS`

| Sub-Agent Type | Model | Notes |
|---------------|-------|-------|
| `research-scout` | Haiku or Sonnet (per dispatch, see model heuristic) | Primary and only authorized sub-agent |



**Rationale:** AGENT-ARCHITECTURE.md §3.3 and §5.1 authorized dispatcher table. Researcher dispatches only research-scout. Parallelism comes from dispatching N scouts on different research avenues simultaneously.

#### `DELEGATION_COMPOSITION`

**Primary delegation type:** `research`

**Delegation prompt structure per research-scout:**
1. **Research avenue** — the specific sub-question this scout investigates (one facet of the decomposed query)
2. **Source directives** — preferred sources (official docs, web, specific libraries), tool preferences (Context7 MCP for library docs, WebSearch for broader topics)
3. **Depth and format** — what to return (key findings, code examples, comparison points, API signatures)
4. **Version constraints** — specific library versions, framework compatibility requirements
5. **Confidence expectations** — what constitutes a confident answer for this avenue

**Context included:**
- The sub-question with full context of why it matters to the overall research goal
- Any partial findings from previous scouts that inform this avenue
- Version and constraint information from the original query

**Context NOT included:**
- Other scouts' avenue assignments (independent)
- Codebase context (not their domain)
- Full research index (unnecessary — they don't check cache, the Researcher already did)

**Rationale:** Research-methodology skill decomposition rules ("each sub-question should be answerable with 1-3 searches, sub-questions should be independent"). Sub-agent-delegation skill context sizing.

#### `MODEL_SELECTION_HEURISTIC`

| Task Characteristics | Model | Examples |
|---------------------|-------|---------|
| Documentation extraction, API signature lookup, known-source retrieval | **Haiku** | "Extract the ArUco detection API from OpenCV 4.8 docs", "Get the pytest fixture signature" |
| Comparative analysis, best practice evaluation, multi-source synthesis | **Sonnet** | "Compare ArUco vs AprilTag for indoor robotics use", "Evaluate event sourcing patterns for real-time pipelines" |
| Mixed or uncertain | **Sonnet** | Default when not clearly extraction-only |

**Rationale:** AGENT-ARCHITECTURE.md §5.5. Same structure as Explorer. Haiku is the optimization for mechanical extraction from known sources.

#### `SYNTHESIS_OUTPUT_TYPE`

**Produces:** Structured research files written to `.claude/research/`

**Output structure per research file:**
- Topic, keywords, requester context
- Findings organized by research avenue (one section per scout's contribution)
- Per-finding confidence scores
- Citations with source URLs, retrieval dates
- Gaps and limitations explicitly flagged
- Overall confidence assessment

**Synthesis process:**
1. Collect all research-scout returns in PTC container
2. Deduplicate findings across avenues (scouts exploring adjacent topics may find overlapping information)
3. Resolve contradictions between sources (flag if unresolvable)
4. Score confidence per finding using research-methodology standards
5. Structure into research file format
6. Update `.claude/research/_index.json` with topic, keywords, confidence, timestamp
7. Write to `.claude/research/`

**Rationale:** AGENT-ARCHITECTURE.md §3.3 ("writes structured research file with citations"). Research-methodology skill synthesis protocol.

#### `DOMAIN_VERIFICATION`

Checks applied during SYNTHESIS / OUTPUT_VALIDATION:

1. **Schema validation** — research file validates against research schema
2. **Citation completeness** — every factual claim has at least one citation with URL and source name. Uncitable claims get `needs_verification: true`.
3. **Confidence scoring** — per-finding scores follow the standard scale (direct evidence 0.8-1.0, inference 0.6-0.8, indirect 0.3-0.6, speculation 0.0-0.3). Anything below 0.6 flagged as uncertain.
4. **Avenue coverage** — every research avenue assigned during DELIBERATION has corresponding findings in the output. Missing avenues flagged.
5. **Duplicate detection + merge** — if research on the same topic already exists, merge new findings into existing file rather than creating duplicate. Update index.
6. **Staleness metadata** — research file includes `completed_at` timestamp for future staleness checks by other Researcher instances.

**Rationale:** V1 SYNTHESIS guards (`research_file_written`, `confidence_scores_assigned`, `no_duplicate_research_exists`, merge logic). Research-methodology skill epistemic standards. Citation completeness is new for V2 — V1 didn't enforce citation coverage.

#### `OUTPUT_WRITE_GLOBS`

```
.claude/research/**
```

Only research files and the research index. Researcher never writes source code, test code, plans, or context packets.

**Rationale:** AGENT-ARCHITECTURE.md §3.3. V1 SYNTHESIS write_globs.

#### `POST_ACTIONS`

```
Write → validate_research_schema
Edit  → validate_research_schema
```

Every write to `.claude/research/` triggers schema validation. Mechanical, not self-review.

**Rationale:** V1 SYNTHESIS post_actions, preserved unchanged.

#### `PERSISTENCE_MODEL`

**Persistent.** Researcher goes to IDLE after delivery, stays alive for subsequent queries. Terminated when:
- RELATEDNESS_ASSESSMENT determines new query is unrelated domain → TERMINATED
- No more pending queries → TERMINATED
- Context pressure → HANDOFF

**Rationale:** AGENT-ARCHITECTURE.md §3.3: "Persistent across session, /clear at context pressure." Persistent because the PTC container caches research index and the Researcher builds domain familiarity across related queries.

#### `DOMAIN_STATES`

**One addition: `SEARCH_STRATEGY_FALLBACK`**

```
SUB_AGENT_DISPATCH ──[primary strategy failed + alternative exists]──► SEARCH_STRATEGY_FALLBACK
                                                                            │
SEARCH_STRATEGY_FALLBACK ──[think_chosen:FALLBACK]──► SUB_AGENT_DISPATCH    │
                         ──[think_chosen:ABORT]──► ERROR ◄──────────────────┘
                                                      (no alternative exists)
```

**Why this is a domain state, not part of ERROR:**
- ERROR models "the work failed — diagnose and choose a recovery path." It's a terminal assessment.
- SEARCH_STRATEGY_FALLBACK models "the tool failed, but I have another tool." It's a tactical substitution. The research goal, decomposition, and scout prompts are unchanged — only the source directive switches (e.g., Context7 MCP → WebSearch, or WebSearch → a different search approach).
- Going to ERROR for a tool switch would trigger the full 4-path recovery think prompt (RETRY/PARTIAL_SYNTHESIS/HANDOFF/ABORT), which is overkill for "Context7 is down, try WebSearch."

**SEARCH_STRATEGY_FALLBACK state definition:**
- `write_allowed`: false
- `think_on_exit`: true
- Think prompt: "1) Which search strategy failed and why (MCP unavailable, timeout, rate-limited, empty results)? 2) What alternative strategy exists (MCP→WebSearch, WebSearch→different terms, specific documentation site)? 3) Should scout prompts be adjusted for the fallback tool (e.g., MCP-specific queries won't work in WebSearch)? CHOSEN: FALLBACK (re-dispatch with alternative) or ABORT (no viable alternative, go to ERROR)."
- `max_occurrences`: 1 on the FALLBACK → SUB_AGENT_DISPATCH transition (prevents infinite tool-switching)

**Entry points:**
- From SUB_AGENT_DISPATCH when `sub_agent_error_reported AND alternative_strategy_exists`

**Exit points:**
- To SUB_AGENT_DISPATCH with adjusted strategy (FALLBACK)
- To ERROR when no alternative exists or fallback already tried (ABORT)

#### `DOMAIN_ERROR_PATHS`

**None beyond base 4.** The strategy fallback is handled by the domain state before reaching ERROR. If a Researcher reaches ERROR, it's a genuine failure where the 4 standard recovery paths apply:

| Failure | Recovery Path | Rationale |
|---------|--------------|-----------|
| All search strategies exhausted (primary + fallback both failed) | RETRY | Only if transient (both MCP and WebSearch were temporarily down). Rare. |
| Scouts returned but results are contradictory/unusable | PARTIAL_SYNTHESIS | Synthesize the non-contradictory findings, flag gaps. |
| Researcher context pressure during multi-avenue research | HANDOFF | Write handoff with completed avenues, replacement continues remaining. |
| Query fundamentally unanswerable (topic doesn't exist, wrong domain) | ABORT | Notify requester. |

#### `SELF_EXECUTE_SCOPE`

**Self-executable (teammate does directly):**
- **PTC targeted queries** — any research question answerable in 1-2 commands:
  - Simple factual lookups ("Does library X support feature Y?" → WebSearch via PTC)
  - API signature retrieval when the source URL is known
  - Checking if a cached research file answers the question
  - Version checking ("What is the latest release of library X?")
- **Reading existing research files** — full or partial, for cache evaluation or synthesis input
- **Writing synthesized research output** — the research file and index update
- **Sending messages** — to orchestrator, requesting teammates, cross-domain partners
- **Research-methodology skill depth calibration:** "Simple factual (yes/no, one signature) → 0 sub-agents (direct)" and "Focused lookup → 0-1 sub-agents" — these low-depth rows are PTC self-execute candidates

**NOT self-executable (must delegate to research-scout):**
- **Multi-facet research** — any query decomposed into 2+ independent sub-questions
- **Deep comparative analysis** — comparing libraries, patterns, approaches across multiple sources
- **Broad documentation extraction** — gathering comprehensive API docs for a library
- **Any research requiring multiple searches** to build a complete picture

**The boundary:** Known question + PTC answers in 1-2 commands → self-execute. Unknown answer + requires exploring multiple sources → delegate. The research-methodology skill's depth calibration table is the authoritative guide: rows 1-2 (simple factual, focused lookup) are self-execute candidates; rows 3-4 (multi-facet, deep evaluation) always delegate.

#### `CROSS_DOMAIN_PARTNERS`

| Partner | Direction | Message Type | Purpose |
|---------|-----------|-------------|---------|
| Explorer | Researcher → Explorer | `info_request` | Codebase context when research needs code examples or implementation reference ("What pattern does our codebase use for X so I can research compatible approaches?") |
| All teammates | Any → Researcher | `info_request` | Research requests ("I need API docs for library X", "What are best practices for Y?") |
| Orchestrator | Researcher ↔ Orchestrator | `info_request` / `info_ready` / `task_complete` | Clarification, sub-agent spawn mediation, delivery confirmation |

**Rationale:** AGENT-ARCHITECTURE.md §3.3 (cross-domain: "Messages Explorer for codebase context"), §6.2 (communication diagram).

---

## 3. Planner

**Role summary:** Design-to-plan converter. The most self-reliant teammate — does planning reasoning directly, only delegates to plan-checker for independent verification. Receives design reference from orchestrator, decomposes into phases → tasks → test architecture through a granular user-review cycle, then converts the approved plain-text plan to JSON for agent consumption with semantic validation. Per-feature lifecycle (terminates after plan approval).

**Sources consulted:**
- `AGENT-ARCHITECTURE.md` §3.4 (Planner definition, behavior, skills, cross-domain)
- `AGENT-ARCHITECTURE.md` §4.2 (plan-checker sub-agent definition, 8 verification dimensions)
- `AGENT-ARCHITECTURE.md` §5.1, §5.5 (dispatch rules, model selection)
- `BASE-DIRECTOR-PATTERN.md` §2-§7 (lifecycle phases, extension point API)
- `state-machines/strategist.json` (V1 Strategist — 14 states, 17 transitions, 5 user review gates)
- `new_claude/skills/phase-planning/SKILL.md` (grounding, gap resolution, decomposition, test architecture)
- `new_claude/skills/multi-perspective-analysis/` (divergent exploration for approach decisions)
- `new_claude/skills/sub-agent-delegation/SKILL.md` (delegation lifecycle for plan-checker)

### V1 State Machine Alignment

The V1 Strategist (`state-machines/strategist.json`, v1.1.0) has 14 states with 5 user review gates and think prompts at every work state. The Planner's V1 cycle is its domain — the review cycle stays intact. V2 enhancements are surgical: context-awareness in DESIGN_INGESTION, plan-checker at each review gate, and self-reliance throughout.

| V1 State | V2 State | Change |
|----------|----------|--------|
| `SPAWNED` | `SPAWNED` | No change. Orchestrator sends design file path + task assignment. |
| `DESIGN_INGESTION` | `DESIGN_INGESTION` | **Enhanced.** V1: read design, think, proceed. V2: read design, check if context packet exists for grounding, if missing → message Explorer with design file path so Explorer can read the design and build the right context. Wait for context, read it, send follow-up queries if needed. Once grounded → proceed. Think prompt expanded (see below). |
| `PHASE_BREAKDOWN` | `PHASE_BREAKDOWN` | **Same.** Write to plan-plaintext. Think prompt preserved. Planner does this directly — phase decomposition is judgment work, uses PTC for targeted codebase lookups when grounding boundaries. |
| `PHASE_REVIEW` | `PHASE_REVIEW` | **Enhanced.** Plan-checker spawned (Sonnet) and runs before user sees it. User reviews with checker report alongside. Revision loop uses progress-based tracking (see below). |
| `TASK_BREAKDOWN` | `TASK_BREAKDOWN` | **Same.** Think prompt preserved. Planner does this directly. |
| `TASK_REVIEW` | `TASK_REVIEW` | **Enhanced.** Plan-checker resumes (not re-spawned) — already has phase context. Progress-based revision tracking. |
| `TASK_DETAILING` | `TASK_DETAILING` | **Enhanced.** Same think prompt. V2 addition: if the Planner discovers a task touches unknown code, it can message Explorer/Researcher for the specific context needed rather than just adding a query and hoping the coder resolves it. Structured request with what's needed, why, and what design concept it maps to. |
| `DETAIL_REVIEW` | `DETAIL_REVIEW` | **Enhanced.** Plan-checker resumes. Progress-based revision tracking. |
| `TEST_PLANNING` | `TEST_PLANNING` | **Same.** Think prompt preserved. Pass A/B/C test architecture — Planner does this directly. |
| `TEST_PLAN_REVIEW` | `TEST_PLAN_REVIEW` | **Enhanced.** Plan-checker resumes. Progress-based revision tracking. |
| `PLAN_TEXT_COMPLETE` | `PLAN_TEXT_COMPLETE` | **Same.** Final user approval gate. Major rework can loop back to PHASE_BREAKDOWN (progress-based). |
| `PLAN_TEXT_APPROVED` | `PLAN_TEXT_APPROVED` | **Same.** User approved plain-text plan. |
| `JSON_CONVERSION` | `JSON_CONVERSION` | **Changed mechanism.** V1: spawned extraction sub-agents. V2: Planner does it itself — it wrote the plan, it knows the content, conversion is mechanical. Uses PTC for conversion + validation scripts. |
| `JSON_VALIDATION` | `JSON_VALIDATION` | **Enhanced.** Schema validation + **semantic bidirectional diff** between plain-text and JSON. Validation scripts check structural fidelity (all fields present, no data loss) AND semantic fidelity (intent preserved, no lossy translation). This is the trust gate — the user approved plain-text, agents consume JSON, validation ensures they match. |
| `PLAN_COMPLETE` | `PLAN_COMPLETE` | **Enhanced.** Write termination log capturing full planning context (decisions, reasonings, grounding outcomes, plan-checker findings, user feedback applied, implementation notes), then notify orchestrator and terminate. Log is the Planner's institutional memory — a respawned Planner reads it to recover the reasoning behind every decision in the plan. |
| `HANDOFF` | `HANDOFF` | No change. Context pressure → write handoff with planning progress, current state, decisions made. |

**States eliminated:** 0
**States added:** 0
**Domain states modified:** 5 (DESIGN_INGESTION enhanced, 4 *_REVIEW states get plan-checker)
**Net state count:** 14 states (same as V1) + ERROR from base = **15 states**

The V2 Planner has the same state count as V1. The enhancements are behavioral (what happens within each state), not structural (adding/removing states).

### DESIGN_INGESTION — V2 Enhancement Detail

This is the biggest behavioral change from V1. V1 assumed context was pre-loaded. V2 makes the Planner responsible for ensuring it has what it needs — but naturally, as part of reading the design, not as a separate prerequisite gate.

**Flow within DESIGN_INGESTION:**

1. Read the design document (path provided by orchestrator in task assignment)
2. Ground every design concept to codebase constructs per phase-planning skill Step 1
3. Check: does a context packet exist that covers this design's codebase surface area?
   - **Yes, sufficient** → proceed to think prompt, then PHASE_BREAKDOWN
   - **Yes, partial** → identify specific gaps. Simple gaps (known file path, single lookup) → PTC self-execute. Complex gaps → step 4
   - **No context packet** → step 4
4. Message Explorer with structured request:
   ```
   { design_file_path, concepts_needing_context, why_needed, grounding_gaps }
   ```
   Explorer reads the design doc itself, deliberates on what codebase context is needed, dispatches scouts, builds the context packet.
5. Read the returned context packet. Re-ground concepts.
6. If still gaps → send follow-up queries to Explorer/Researcher with specifics
7. Once all blocking gaps resolved → think prompt → proceed

**V2 think prompt for DESIGN_INGESTION:**

```
Q1: What are the core requirements vs nice-to-haves in the design doc?
Q2: What existing codebase patterns and conventions must the plan respect?
Q3: Are there ambiguities in the design doc that need user clarification?
Q4: What are the highest-risk areas that need careful phasing?
Q5: Are there external dependencies or integration points that constrain ordering?
Q6: Is all grounding resolved? Any concepts still unmapped to codebase constructs?
    If gaps remain, are they blocking (affects phase boundaries) or non-blocking
    (can resolve during task detailing)?
CHOSEN: PROCEED | CLARIFY | NEED_CONTEXT
```

PROCEED → PHASE_BREAKDOWN. CLARIFY → message orchestrator for user clarification. NEED_CONTEXT → message Explorer/Researcher (loops within DESIGN_INGESTION until resolved).

**Why this isn't a separate state:** Checking for context is part of understanding the design. You can't analyze requirements (Q1) without grounding them. You can't assess risk (Q4) without knowing what codebase constructs are involved. Context checking is inherent to design ingestion, not a prerequisite for it.

### Plan-Checker Integration

**Spawn and resume pattern:**

| Review Gate | Plan-Checker Action | What It Verifies |
|-------------|-------------------|-----------------|
| `PHASE_REVIEW` | **Spawn** (first invocation, Sonnet) | Phase boundaries, dependency ordering, testability per phase, goal coverage |
| `TASK_REVIEW` | **Resume** (has phase context) | Task completeness, dependency accuracy, scope boundaries, wave feasibility |
| `DETAIL_REVIEW` | **Resume** (has phase + task context) | Query specificity, review_level justification, metadata completeness |
| `TEST_PLAN_REVIEW` | **Resume** (full plan context) | Test coverage, invariant specificity, acceptance criteria clarity, technical feasibility |

**Why resume, not re-spawn:** The plan-checker accumulates understanding across gates. At PHASE_REVIEW it knows phases. At TASK_REVIEW it knows phases + tasks. By TEST_PLAN_REVIEW it has the full picture. This means:
- Later checks are more informed (checker catches cross-cutting issues earlier checks couldn't see)
- No re-reading cost (checker doesn't re-read the plan from scratch at each gate)
- Checker can verify consistency across stages (e.g., "task T3 claims to implement phase P2's capability but P2's verification criteria don't cover T3's output")

**Cold-start fallback:** If resume fails (sub-agent crashed, context lost), the Planner cold-starts a fresh plan-checker with: the original delegation prompt + `previous_verification_report` containing the prior gate's full structured return. The fresh checker reads the prior report and has equivalent context for the current gate's verification. See SUB-AGENT-SPECS Common Conventions § "Cold-Start as Primary Path."

**Plan-checker dispatch is orchestrator-mediated:** Planner writes delegation JSON, messages orchestrator, orchestrator spawns (or resumes) the plan-checker. Planner waits for the verification report before presenting to user.

**Revision after plan-checker REVISE verdict:** If plan-checker returns REVISE, the Planner reads the revision suggestions, applies fixes, and re-dispatches the checker (resumed) before going back to user review. The user never sees a plan that the checker flagged — Planner fixes it first. User sees the clean version with the checker's PASS report.

### Revision Tracking — Progress-Based, Not Hard Caps

V1 used raw `max_occurrences` on revision transitions (3, 3, 2, 3). V2 follows the progress-based pattern already established in the coder (`GREEN_PROGRESS_CHECK`, `INVARIANT_PROGRESS_CHECK`) and tester (`reset_stall_counter` on `progress_detected`) state machines.

**How it works:** The daemon tracks a consecutive stall counter per review gate. Each revision loop, the daemon evaluates whether the Planner made meaningful progress:

- **Progress detected** (plan content changed substantively — phases added/removed/restructured, tasks revised, test expectations updated) → stall counter resets to 0, revision continues
- **No progress** (same issues, cosmetic changes only, same plan-checker dimensions failing) → stall counter increments

**Stall thresholds per gate:**

| Review Gate | Stall Threshold | On Stall | Rationale |
|-------------|----------------|----------|-----------|
| PHASE_REVIEW | 3 consecutive no-progress | Escalate to user with stuck diagnosis | Phase boundaries are high-impact — user arbitrates |
| TASK_REVIEW | 3 consecutive no-progress | Escalate to user | Task structure affects all downstream work |
| DETAIL_REVIEW | 2 consecutive no-progress | Escalate to user | Details are lower-risk but still need resolution |
| TEST_PLAN_REVIEW | 3 consecutive no-progress | Escalate to user | Test architecture is critical to implementation quality |
| PLAN_TEXT_COMPLETE | 1 consecutive no-progress on major rework | HANDOFF | If a full rework pass didn't change things, fresh eyes needed |

**Why progress-based:** The revision caps exist to prevent infinite loops where the Planner is stuck, not to limit productive iteration. If the user gives feedback, the Planner revises meaningfully, the checker finds a new issue, and the user refines again — that's productive work. A hard cap of 3 would cut that off arbitrarily. The stall counter only fires when the same problems keep recurring with no forward motion.

**Consistency with existing patterns:** The coder's `progress_confirmed` / `stuck_detected` transitions and the tester's `reset_stall_counter` action use the same logic. The daemon already implements progress evaluation — the Planner reuses that mechanism rather than inventing a new one.

### Extension Points

#### `INGRESS_CONTEXT_LOADING`

**What the Planner loads on spawn:**
1. The design document (path from orchestrator's task assignment message)
2. Any existing context packets in `.claude/context/` that cover the design's codebase surface area
3. Any existing research files in `.claude/research/` relevant to the design's technical domain
4. If this is a respawn (plan revision after implementation): the **termination log** from `.claude/logs/{feature}-planning-log.json` — this is the prior Planner's institutional memory containing all decisions, reasonings, grounding outcomes, and rejected alternatives. Also loads existing plan state in `.claude/plans/plan-plaintext/` and `.claude/plans/{feature}-plan.json`
5. If this is a replacement spawn (after HANDOFF): the handoff summary from `.claude/handoffs/` plus any existing plan state

**What it does NOT load:**
- Source code files directly (uses context packets for codebase understanding, PTC for targeted lookups)
- Other teams' plans or session logs
- Test code or implementation details

**Rationale:** The Planner works from design docs + context packets + research, not from raw source code. When context packets are insufficient, it messages Explorer/Researcher for what it needs rather than reading source files itself (domain separation).

#### `DELIBERATION_THINK_PROMPT` / `DOMAIN_THINK_QUESTIONS`

The Planner's deliberation is distributed across its domain states — each work state has its own think prompt from V1, preserved and enhanced. The base pattern's DELIBERATION state is not used as a separate state; instead, the Planner's first think point is the DESIGN_INGESTION think prompt (see above).

**PHASE_BREAKDOWN think prompt** (V1 preserved):
```
Q1: Are phases ordered so each builds on the previous without requiring rework?
Q2: Is each phase independently testable and commitable?
Q3: Are phase boundaries at natural integration points where the system is in a consistent state?
Q4: Is the first phase small enough to validate the approach early?
Q5: Are there phases that could be parallelized?
CHOSEN: DRAFT_COMPLETE | NEED_CONTEXT
```

**TASK_BREAKDOWN think prompt** (V1 preserved):
```
Q1: Is each task small enough for a single coder in one session?
Q2: Are task dependencies explicit — does task B truly need task A's output?
Q3: Are touched_functions accurate and non-overlapping between concurrent tasks?
Q4: Is the priority ordering correct — critical-path tasks before optional enhancements?
Q5: Are model_recommendations appropriate (Opus for complex logic, Sonnet for boilerplate)?
CHOSEN: DRAFT_COMPLETE | NEED_CONTEXT
```

**TASK_DETAILING think prompt** (V1 preserved + V2 addition):
```
Q1: Are exploration_queries specific enough to produce useful context packets?
Q2: Are research_queries targeting the right APIs, libraries, or patterns?
Q3: Is the review_level justified — are 'light' tasks truly mechanical with no downstream deps?
Q4: Are requires_research and requires_exploration flags set correctly?
Q5: Would any task benefit from additional context not currently requested?
Q6: Did detailing reveal any concepts I can't ground? If so, what specific context
    do I need from Explorer/Researcher to resolve them?
CHOSEN: DRAFT_COMPLETE | NEED_CONTEXT
```

NEED_CONTEXT routes to messaging Explorer/Researcher with a structured request, then re-entering the work state with new context.

**TEST_PLANNING think prompt** (V1 preserved):
```
Q1: Does every task have Pass A (unit), B (integration), C (contract) coverage where applicable?
Q2: Are invariants specific and machine-verifiable, not vague assertions?
Q3: Are golden examples representative of real usage patterns?
Q4: Do negative path tests cover the most likely failure modes?
Q5: Are test dependencies on external services properly mocked or isolated?
CHOSEN: DRAFT_COMPLETE
```

#### `AUTHORIZED_SUB_AGENTS`

| Sub-Agent Type | Model | Notes |
|---------------|-------|-------|
| `plan-checker` | Sonnet (fixed) | Only authorized sub-agent. Spawned once, resumed at each review gate. |



**Rationale:** AGENT-ARCHITECTURE.md §3.4 and §5.1. The Planner dispatches only plan-checker. All other work is done by the Planner directly (most self-reliant teammate) or requested cross-domain from Explorer/Researcher.

#### `DELEGATION_COMPOSITION`

**Primary delegation type:** `plan_verification`

**Delegation prompt structure for plan-checker:**
1. **What changed since last check** — which planning stage just completed, what was added/modified
2. **Current plan state** — file path to plan-plaintext, which sections are complete
3. **Verification focus** — which of the 8 dimensions are most relevant for this stage (e.g., PHASE_REVIEW emphasizes goal_coverage and dependency_accuracy; TEST_PLAN_REVIEW emphasizes acceptance_criteria_clarity)
4. **Previous findings** — summary of prior PASS/REVISE verdicts and resolved issues (available because of resume)
5. **Design doc reference** — path to design document for alignment checking

**Context included:**
- Plan-plaintext content (or diff since last check, for resumed invocations)
- Design document path
- Previous verification report (checker has this from resume, but explicit reference prevents drift)

**Context NOT included:**
- Source code (checker reads codebase via Glob/Grep/PTC for feasibility checks)
- Other teammates' outputs
- Session history

#### `MODEL_SELECTION_HEURISTIC`

**Not applicable.** Planner dispatches only plan-checker, which is always Sonnet (fixed). The Planner itself runs on Opus. No model selection decisions.

**Rationale:** AGENT-ARCHITECTURE.md §5.5: plan-checker is Sonnet (fixed). The Planner doesn't dispatch scouts with variable model selection like Explorer/Researcher do.

#### `SYNTHESIS_OUTPUT_TYPE`

**Produces:** Two artifacts:
1. **Plain-text plan** in `.claude/plans/plan-plaintext/` — human-readable, user-reviewed and approved
2. **JSON plan** in `.claude/plans/{feature}-plan.json` — agent-consumable, semantically validated against plain-text

The plain-text plan is the user interface. The JSON plan is the agent interface. The validation scripts in JSON_VALIDATION ensure they match — structural fidelity (all fields present, no data loss) AND semantic fidelity (intent preserved, no lossy translation). This is the trust gate: the user approved plain-text, agents consume JSON, validation ensures no divergence.

#### `DOMAIN_VERIFICATION`

Checks applied at each stage:

**During review gates (before user sees it):**
1. **Plan-checker verification** — 8 dimensions per AGENT-ARCHITECTURE.md §4.2 (goal coverage, task completeness, dependency accuracy, scope boundaries, risk identification, wave feasibility, acceptance criteria clarity, technical feasibility)
2. **Revision application** — if checker returns REVISE, Planner fixes and re-checks before user review

**During JSON_VALIDATION:**
3. **Schema validation** — JSON plan validates against `implementation-plan.schema.json`
4. **Bidirectional diff** — every element in plain-text has a corresponding JSON entry and vice versa
5. **Semantic fidelity** — validation scripts check that requirements, limitations, considerations text is preserved without lossy summarization
6. **Cross-reference integrity** — all `depends_on` references resolve to valid task IDs, all `design_doc_sections` map to actual sections, all `test_expectations` reference valid `requirement_refs`

#### `OUTPUT_WRITE_GLOBS`

```
.claude/plans/plan-plaintext/**
.claude/plans/*-plan.json
.claude/logs/*-planning-log.json
```

Plain-text plan files during the review cycle, JSON plan file after conversion, termination log at PLAN_COMPLETE. Planner never writes source code, test code, context packets, or research.

#### `POST_ACTIONS`

```
Write → validate_plan_schema       (for JSON plan files)
Edit  → validate_plan_schema       (for JSON plan files)
```

Schema validation triggers on writes to `.claude/plans/*-plan.json`. Plain-text files don't have schema validation — they're free-form until JSON conversion.

#### `PERSISTENCE_MODEL`

**Per-feature.** Planner terminates after PLAN_COMPLETE. Before terminating, it writes a **termination log** to `.claude/logs/{feature}-planning-log.json` that captures the full planning context:

**Termination log contents:**
- **Key decisions** — every design-to-plan decision with rationale (approach selection, phase boundaries, task granularity choices, review_level assignments)
- **Grounding outcomes** — the final concept map showing how each design concept maps to codebase constructs, which gaps were resolved and how
- **Context interactions** — what was requested from Explorer/Researcher, what was returned, how it influenced the plan
- **Plan-checker findings** — summary of each verification pass (which dimensions passed/failed, what was revised)
- **User feedback applied** — what the user changed at each review gate, why, and how the plan adapted
- **Implementation notes** — anything the Planner learned during planning that would help a coder or a respawned Planner (gotchas, non-obvious constraints, areas of uncertainty)
- **Approach alternatives** — rejected approaches with rejection rationale (from phase-planning skill Step 3), so a respawned Planner doesn't re-explore dead ends

This log is the Planner's institutional memory. If a plan needs revision after implementation starts, the orchestrator spawns a new Planner that reads the termination log + existing plan state + the coder's feedback. The respawned Planner understands WHY the plan looks the way it does, not just WHAT it says.

**Rationale:** AGENT-ARCHITECTURE.md §3.4: "Per-feature (terminates after plan is approved)." The termination log addresses the V1 gap where respawned Planners had no access to prior reasoning and would re-derive or contradict earlier decisions.

#### `DOMAIN_STATES`

The Planner's domain IS its state cycle. All 14 V1 states are domain states — the base pattern's generic lifecycle (CONTEXT_LOADING → DELIBERATION → SUB_AGENT_DISPATCH → SYNTHESIS) doesn't apply because the Planner doesn't follow that flow. The Planner's flow is:

```
SPAWNED → DESIGN_INGESTION → PHASE_BREAKDOWN ⇄ PHASE_REVIEW →
TASK_BREAKDOWN ⇄ TASK_REVIEW → TASK_DETAILING ⇄ DETAIL_REVIEW →
TEST_PLANNING ⇄ TEST_PLAN_REVIEW → PLAN_TEXT_COMPLETE →
PLAN_TEXT_APPROVED → JSON_CONVERSION → JSON_VALIDATION → PLAN_COMPLETE
```

With revision loops (⇄) at each review gate and HANDOFF/ERROR as orthogonal escapes.

The base pattern's extension mechanism used here is **OVERRIDE** — the Planner replaces the generic lifecycle with its own domain-specific cycle. The base pattern's orthogonal states (ERROR, HANDOFF, TERMINATED) still apply.

#### `DOMAIN_ERROR_PATHS`

**One addition: context request failure.**

| Failure | Recovery Path | Rationale |
|---------|--------------|-----------|
| Explorer/Researcher didn't respond or returned insufficient context | **RETRY with specifics** | Re-message with narrower, more specific request. If still no response, proceed with partial grounding and flag gaps in plan. |
| Plan-checker stuck in REVISE loop (3+ iterations on same dimension) | **Escalate to user** | Planner and checker disagree — user arbitrates. |
| JSON validation fails after 3 conversion attempts | **HANDOFF** | Conversion bug, not a planning problem. Write handoff for replacement Planner or manual intervention. |

Base 4-path recovery applies for all other failures (sub-agent crash, PTC unavailable, etc.).

#### `SELF_EXECUTE_SCOPE`

**The Planner is the most self-reliant teammate.** Self-execute is the default, delegation is the exception.

**Self-executable (Planner does directly):**
- **All planning reasoning** — phase decomposition, task breakdown, detailing, test architecture. This is judgment work — the Planner's core competency.
- **PTC targeted lookups** — checking file existence, reading specific functions, validating that a codebase construct referenced in the plan actually exists. Simple queries where the Planner knows what to ask.
- **Reading context packets and research files** — for grounding decisions
- **Writing plain-text plan** — all plan-plaintext content
- **JSON conversion** — mechanical translation of approved plan content
- **Sending structured messages** — to Explorer, Researcher, orchestrator
- **Applying plan-checker revision suggestions** — reading the checker's REVISE report and fixing the plan

**NOT self-executable (delegates or requests cross-domain):**
- **Independent plan verification** — delegates to plan-checker (fresh eyes, no confirmation bias)
- **Broad codebase exploration** — messages Explorer when context packets don't cover what's needed
- **Research lookups** — messages Researcher when design references external libraries/patterns the Planner doesn't know
- **Source code reading beyond targeted PTC lookups** — if the Planner needs to understand a module's architecture, that's Explorer's domain

**The boundary:** The Planner does everything that requires planning judgment. It delegates only for independent verification (plan-checker) and context it doesn't have (Explorer/Researcher). The delegation cost heuristic from the base pattern (< 3 tool calls → self-execute) doesn't apply — the Planner's self-execute threshold is much higher because planning reasoning IS its direct work, regardless of tool call count.

#### `CROSS_DOMAIN_PARTNERS`

| Partner | Direction | Message Type | When | Request Schema |
|---------|-----------|-------------|------|---------------|
| Explorer | Planner → Explorer | `info_request` | Context packet missing or insufficient for design grounding | `{ design_file_path, concepts_needing_context, grounding_gaps[], why_needed }` |
| Explorer | Planner → Explorer | `info_request` | Mid-cycle gap discovered during TASK_DETAILING | `{ specific_query, affected_tasks[], codebase_area, why_blocking }` |
| Researcher | Planner → Researcher | `info_request` | Design references external library/pattern Planner can't ground | `{ research_query, design_concept, why_needed, urgency }` |
| Orchestrator | Planner ↔ Orchestrator | `info_request` / `info_ready` | User clarification, plan-checker spawn/resume mediation, delivery | Standard protocol |
| Coder | Coder → Planner (via orchestrator) | `info_request` | Post-approval: coder found plan issue during implementation | Orchestrator may spawn new Planner |

**Structured request emphasis:** When the Planner messages Explorer or Researcher, the request includes exactly what's needed, why, and what design concept it maps to. This lets Explorer read the design doc and build targeted context rather than guessing what the Planner wants. The Planner's requests are grounded in the concept map from phase-planning skill Step 1 — every gap references a specific design concept and codebase area.

**Rationale:** AGENT-ARCHITECTURE.md §3.4 (cross-domain: "Messages Explorer for context, Researcher for technical feasibility"), §6.2 (communication diagram). The Planner is the primary consumer of both Explorer and Researcher output.

---

## 4. Coder

**Role summary:** Phase-level task coordinator. The Coder is a director — it does not write code. It ingests all tasks for a phase, identifies parallel execution groups, dispatches test-writer and implementer sub-agents to task worktrees, monitors their progress, relays audit findings, manages merge flow, and escalates to the user only when progress stalls. Persistent across all phases with /clear between phases.

**Sources consulted:**
- `AGENT-ARCHITECTURE.md` §3.5 (Coder definition, behavior, skills, cross-domain)
- `AGENT-ARCHITECTURE.md` §4.2 (implementer, test-writer, debugger sub-agent definitions)
- `AGENT-ARCHITECTURE.md` §5.1, §5.5 (dispatch rules, model selection)
- `BASE-DIRECTOR-PATTERN.md` §2-§7 (lifecycle phases, extension point API)
- `state-machines/coder.json` (V1 Coder — 24 states, per-task TDD lifecycle)
- `new_claude/skills/phase-planning/SKILL.md` (wave-based parallelism, task dependencies)
- `new_claude/skills/sub-agent-delegation/SKILL.md` (delegation lifecycle, dispatch modes)
- `new_claude/skills/plan-adherence/SKILL.md` (scope checking, deviation documentation)

### V1 → V2 Architectural Shift

The V1 Coder is fundamentally different from the V2 Coder. This is not an incremental enhancement like Explorer/Researcher/Planner — it is a role redefinition.

**V1 Coder:** Per-task, single-threaded. Spawned for one task, does TDD work itself (writes tests, writes implementation), terminates when task is done. The state machine encodes the TDD cycle because the Coder IS the executor.

**V2 Coder:** Phase-level director. Persistent across the phase, manages multiple task pipelines in parallel through sub-agents. Does not write code — designs delegation prompts, reviews results, coordinates with Auditor, manages merges. The TDD cycle moves entirely to sub-agents.

**What V1 states become:**

| V1 State(s) | V2 Equivalent | Why |
|---|---|---|
| SPAWNED, TASK_CLAIMED, WORKTREE_CREATED, CONTEXT_REQUESTED, CONTEXT_LOADED | **SPAWNED, PHASE_INGESTION** | V1 loaded one task. V2 loads the full phase — all tasks, dependency graph, parallel groups. |
| TEST_DESIGN, TESTS_WRITTEN, TDD_RED, RED_VERIFIED, RED_VERIFIED_COMPLETE, RED_FAILED | **test-writer sub-agent** | The entire red phase is delegated. Test-writer writes tests, runs them, verifies they fail, runs quality gate, returns. |
| IMPLEMENTATION, TDD_GREEN, GREEN_PROGRESS_CHECK, INVARIANT_CHECK, INVARIANT_PROGRESS_CHECK | **implementer sub-agent** | The entire green phase is delegated. Implementer writes code, runs tests, verifies they pass, runs quality gate, returns. |
| APPROACH_ASSESSMENT | **Coder RESULT_REVIEW think prompt + debugger dispatch** | V1 Coder assessed its own approach. V2 Coder dispatches debugger for stuck situations, then escalates to user. |
| QUALITY_GATE | **Sub-agent responsibility** | Sub-agents run quality gates before returning. The Coder doesn't run gates — it reviews gate results in the return. |
| TASK_REVIEW_REQUESTED, FIXES | **Auditor teammate interaction** | V1 Coder waited for audit and applied fixes itself. V2 Coder messages Auditor, receives findings, resumes sub-agents to fix. |
| MERGE, MERGE_CONFLICT, MERGE_RESOLVED, TASK_COMPLETE | **MERGE_MANAGEMENT, TASK_COMPLETE** | Merge flow preserved but now per-task-branch into Coder's phase branch. |
| SCRAP_RETRY, PLAN_ERROR | **STUCK_ESCALATION, PLAN_ERROR** | Escalation paths preserved. |
| HANDOFF | **HANDOFF** | Context pressure handling preserved. |

### Worktree Hierarchy

```
primary branch (main or feature)
    └── worktree: phase-{N}/task-{01}  (sub-agents for task 01 work here)
    └── worktree: phase-{N}/task-{02}  (sub-agents for task 02 work here)
    └── worktree: phase-{N}/task-{03}  (sub-agents for task 03 work here)
```

**Sequential phases, parallel tasks within waves.** One Coder per phase. Each task gets its own worktree branched off the primary branch. Multiple sub-agents (test-writer, implementer, auditor) can work on the SAME task worktree simultaneously because INV-1 ensures they don't overlap in file writes: test-writer writes test files, implementer writes source files, audit-checker is read-only.

**Hook-managed infrastructure:**
- **On sub-agent dispatch:** Hooks create the task worktree (if not already created for this task), inject worktree path into the sub-agent's prompt, and ping the daemon
- **On task merge:** Task branch merges into primary branch. Conflicts should be rare if the plan's parallel groups are correct (no shared `target_files` between parallel tasks)
- **Hooks are shared across worktrees** — git worktrees share the same `.git/hooks/` directory, so PreToolUse/PostToolUse hooks work identically in any worktree. Hooks ping the daemon for state tracking.

### Orchestrator Role and Inter-Teammate Messaging

**The orchestrator's role is reduced to sub-agent spawning and system-level decisions.** Teammates message each other directly for all cross-domain communication — no orchestrator mediation needed for Coder ↔ Auditor audit cycles, Coder ↔ Explorer context requests, etc.

**Orchestrator dashboard** at `.claude/state/system-dashboard.json`:
```json
{
  "phase": "phase-02",
  "teammates": {
    "coder": {
      "status": "active",
      "sub_agents": [
        { "id": "...", "type": "implementer", "task": "task-03", "status": "active" },
        { "id": "...", "type": "test-writer", "task": "task-04", "status": "idle" }
      ]
    },
    "auditor": {
      "status": "active",
      "sub_agents": [
        { "id": "...", "type": "audit-checker", "task": "task-02", "status": "active" }
      ]
    },
    "explorer": { "status": "idle", "sub_agents": [] }
  },
  "message_log": [
    { "from": "coder", "to": "auditor", "type": "info_request", "task": "task-02", "timestamp": "..." },
    { "from": "auditor", "to": "coder", "type": "info_ready", "task": "task-02", "timestamp": "..." }
  ]
}
```

The orchestrator updates this dashboard on every sub-agent spawn/terminate and can be /cleared as needed to manage context pressure — the dashboard file preserves system state across clears.

**When the orchestrator is involved:**
- Sub-agent spawn requests (only the orchestrator can use the Task tool to spawn)
- Phase transitions
- System-level escalations (PLAN_ERROR, stuck across multiple teammates)
- /clear coordination

**When the orchestrator is NOT involved:**
- Coder ↔ Auditor audit cycle (direct messaging)
- Coder ↔ Explorer context requests (direct messaging)
- Coder ↔ Researcher lookups (direct messaging)
- Sub-agent result processing (Coder reads returns directly)

### V2 State Machine

```
SPAWNED
    │
    ▼
PHASE_INGESTION ◄─── (/clear, next phase assignment)
    │  (load plan, tasks, dependency graph, parallel groups)
    │  (think: execution strategy, wave ordering)
    │
    ▼
DISPATCH_READY ◄────────────────────────────────────┐
    │  (think per task: delegation prompt design)    │
    │  hooks auto-create worktrees                   │
    │  dispatch test-writers for ready tasks          │
    │                                                │
    ▼                                                │
COORDINATING ──────────────────────────────────────┐│
    │  (event loop: process returns, make decisions) ││
    │                                                ││
    │  Events:                                       ││
    │  ┌─ test-writer returned ──► RESULT_REVIEW     ││
    │  ├─ implementer returned ──► RESULT_REVIEW     ││
    │  ├─ debugger returned ──► RESULT_REVIEW        ││
    │  ├─ audit findings received ──► AUDIT_RESPONSE ││
    │  ├─ dependencies cleared ──► DISPATCH_READY ───┘│
    │  └─ all tasks merged ──► PHASE_COMPLETE        │
    │                                                │
    ▼                                                │
RESULT_REVIEW                                        │
    │  (think: assess return, decide next action)    │
    │                                                │
    │  ┌─ red verified ──► dispatch implementer      │
    │  ├─ green verified ──► message Auditor         │
    │  ├─ tests still failing ──► dispatch debugger  │
    │  ├─ debugger resolved ──► resume implementer   │
    │  ├─ debugger stuck ──► STUCK_ESCALATION        │
    │  └─ quality gate failed ──► resume sub-agent   │
    │                                                │
    └──► back to COORDINATING ───────────────────────┘

AUDIT_RESPONSE
    │  (think: triage findings, plan fixes)
    │
    │  ┌─ approved ──► MERGE_MANAGEMENT
    │  ├─ improvements needed ──► resume sub-agents to fix
    │  │     (sub-agents re-run tests + quality gate after fixes,
    │  │      then Coder messages Auditor to re-check)
    │  └─ critical issues ──► STUCK_ESCALATION
    │
    └──► back to COORDINATING

MERGE_MANAGEMENT
    │  (merge task branch into phase branch)
    │
    │  ┌─ clean merge ──► task complete, back to COORDINATING
    │  ├─ conflict ──► Coder resolves (this IS direct work, not delegated)
    │  └─ post-merge test verification ──► run tests on phase branch
    │
    └──► back to COORDINATING

STUCK_ESCALATION
    │  (user involvement needed)
    │  (think: what's stuck, what was tried, what options remain)
    │
    │  ┌─ user provides guidance ──► resume relevant sub-agent
    │  ├─ user says scrap task ──► SCRAP_RETRY
    │  └─ user identifies plan error ──► PLAN_ERROR
    │
    └──► back to COORDINATING

PHASE_COMPLETE
    │  (all tasks merged into phase branch)
    │  (write termination log)
    │  (merge phase branch into primary)
    │
    │  ┌─ more phases ──► /clear, back to PHASE_INGESTION
    │  └─ last phase ──► TERMINATED

SCRAP_RETRY ──► write handoff, notify orchestrator ──► TERMINATED (for this task)
                (Coder continues with other tasks)

PLAN_ERROR ──► write error report, notify orchestrator ──► depends on severity
               (task-level: skip task, continue phase)
               (phase-level: TERMINATED, orchestrator decides)

HANDOFF ──► write handoff summary ──► TERMINATED
            (context pressure, orchestrator spawns replacement)
```

**State count:** 10 states (SPAWNED, PHASE_INGESTION, DISPATCH_READY, COORDINATING, RESULT_REVIEW, AUDIT_RESPONSE, MERGE_MANAGEMENT, STUCK_ESCALATION, PHASE_COMPLETE, HANDOFF) + 2 terminal outcomes (SCRAP_RETRY, PLAN_ERROR) = **12 states**

### Deliberation Model

Think prompts fire at natural decision points, not at every action.

**Where think prompts fire:**

| State | Think Trigger | What It Decides |
|-------|--------------|-----------------|
| PHASE_INGESTION | After loading plan | Execution strategy: wave ordering, parallel groups, which tasks to dispatch first, any upfront context needs |
| DISPATCH_READY | Before each dispatch batch | Per-task delegation prompt design: what context to include, what the test-writer/implementer needs to know, worktree setup |
| RESULT_REVIEW | On each sub-agent return | Assess return quality, decide next action (dispatch next sub-agent, dispatch debugger, message Auditor, escalate) |
| AUDIT_RESPONSE | On audit findings | Triage findings: which sub-agent to resume with what instructions, severity assessment, whether to escalate |
| STUCK_ESCALATION | On entering stuck state | Diagnosis for user: what's stuck, what was tried, what options remain |

**Where think prompts do NOT fire:**
- Sending structured messages to Auditor — follows team-messaging protocol schema
- Resuming a sub-agent with audit fixes — the delegation prompt update is the deliberation
- Creating worktrees — hooks handle this
- Running merges — mechanical git operations
- Quality gate results — pass/fail routing is mechanical

### Per-Task Pipeline — Sub-Agent Lifecycle

Each task goes through this pipeline, managed by the Coder but executed by sub-agents:

```
1. RED PHASE
   ├─ Coder dispatches test-writer (think: delegation prompt)
   ├─ Hook creates task worktree, injects path
   ├─ test-writer: writes tests → runs them → verifies failure → quality gate
   ├─ test-writer returns with red_verification result
   └─ Coder reviews (RESULT_REVIEW): tests fail correctly? quality gate clean?

2. GREEN PHASE
   ├─ Coder dispatches implementer (think: delegation prompt with test results)
   ├─ Hook ensures same task worktree
   ├─ implementer: implements → runs tests → verifies passing → quality gate
   ├─ If stuck: Coder dispatches debugger to same worktree
   ├─ implementer/debugger returns with green_verification result
   └─ Coder reviews (RESULT_REVIEW): all tests pass? quality gate clean?

3. AUDIT
   ├─ Coder messages Auditor teammate (structured protocol, no think needed)
   ├─ Auditor spawns audit-checker on the task branch
   ├─ Audit-checker: checks plan adherence, test design, code quality
   ├─ Auditor relays findings to Coder
   ├─ If improvements needed:
   │     Coder resumes implementer or test-writer to fix (think: what to fix)
   │     Sub-agent fixes → re-runs tests → verifies green → quality gate
   │     Coder messages Auditor to re-check (resumes audit-checker)
   │     Loop until approved
   └─ Audit approved → proceed to merge

4. MERGE
   ├─ Coder merges task branch into phase branch
   ├─ Conflict resolution if needed (Coder does this directly)
   ├─ Post-merge test run on phase branch to verify nothing broke
   └─ Task complete — all sub-agents for this task terminate

5. SUB-AGENT LIFECYCLE
   Sub-agents are designed for cold-start as the primary path. Resume is an
   optimization when available, never a requirement. Each sub-agent returns
   structured JSON with decisions_made and carry_forward fields — a fresh
   replacement gets the original delegation prompt + previous return and can
   pick up where the last one left off.

   ├─ test-writer: spawned at red phase, fresh spawn or resume for audit fixes
   ├─ implementer: spawned at green phase, fresh spawn or resume for audit fixes
   ├─ debugger: spawned when stuck, terminates when issue resolved or escalated
   └─ audit-checker: spawned by Auditor, fresh spawn or resume for re-audits
```

### Parallel Task Management

The plan pre-groups tasks into waves (from phase-planning skill). All tasks in a wave are verified file-independent by the plan-checker.

**Wave execution:**
1. Coder reads wave grouping from plan
2. DISPATCH_READY: dispatches test-writers for all tasks in the current wave simultaneously
3. COORDINATING: processes returns as they come in — each task advances independently through its pipeline
4. When all tasks in a wave are merged, next wave's tasks become ready → back to DISPATCH_READY
5. Cross-wave dependencies are respected: task T5 (wave 2) that depends on T2 (wave 1) is not dispatched until T2 merges

**Task state tracking (metadata, not Coder states):**

Each task is tracked with:
```
{
  task_id: "task-01",
  wave: 1,
  status: "red_in_progress | red_complete | green_in_progress | green_complete |
           audit_requested | audit_fixes | audit_approved | merging | merged | stuck | scrapped",
  worktree_branch: "phase-01/task-01",
  sub_agents: {
    test_writer: { id: "...", status: "active | idle | terminated" },
    implementer: { id: "...", status: "active | idle | terminated" },
    debugger: { id: "...", status: "active | terminated" }
  },
  audit_rounds: 0,
  stuck_escalations: 0
}
```

The Coder maintains this tracking table in its PTC container or in a state file. It's the Coder's operational dashboard.

### Extension Points

#### `INGRESS_CONTEXT_LOADING`

**What the Coder loads on spawn (per phase):**
1. The plan JSON — full phase tasks, dependency graph, wave groupings, test expectations
2. Codebase context packets relevant to this phase's tasks
3. Design document (for plan adherence reference)
4. If this is a replacement spawn (after HANDOFF): handoff summary + task tracking state
5. If this is a subsequent phase (after /clear): prior phase termination log for continuity

**What it does NOT load:**
- Source code (sub-agents read source in their worktrees)
- Test code (sub-agents write and read tests)
- Other phases' task details (out of scope)

#### `DELIBERATION_THINK_PROMPT` / `DOMAIN_THINK_QUESTIONS`

The Coder's deliberation is distributed across its event-driven states. Each has a focused think prompt.

**PHASE_INGESTION think prompt:**
```
Q1: What tasks are in this phase? What are the dependency chains?
Q2: What waves can execute in parallel? Are the wave groupings from the plan correct
    (no shared target_files between parallel tasks)?
Q3: Do any tasks require upfront context that isn't in existing packets?
    If so, which — and should I message Explorer/Researcher before dispatching?
Q4: What's the critical path? Which tasks block the most downstream work?
Q5: Are there any tasks flagged as high-risk in the plan that need special attention
    (review_level 3, queries to resolve)?
CHOSEN: READY_TO_DISPATCH | NEED_CONTEXT
```

**DISPATCH_READY think prompt (per task being dispatched):**
```
Q1: What does the test-writer need to know to write correct tests for this task?
    (requirements, limitations, considerations, test_expectations from plan)
Q2: What context files should be included in the delegation prompt?
    (plan chunk, relevant context packets, design doc sections)
Q3: Are there any resolved queries or implementation notes from the plan that
    affect how tests should be designed?
Q4: Does this task have dependencies on already-merged tasks whose output
    the test-writer needs to be aware of?
CHOSEN: DISPATCH
```

**RESULT_REVIEW think prompt (on sub-agent return):**
```
Q1: What did the sub-agent return? Status, files modified, quality gate result.
Q2: For red phase returns: do all tests fail with the expected failure types
    (AssertionError, ImportError — not syntax errors)? Is coverage sufficient
    per the plan's test_expectations?
Q3: For green phase returns: do all tests pass? Did the implementer's decisions
    stay within plan scope (check decisions_made against task limitations)?
Q4: For debugger returns: was the root cause identified? Is the fix confidence
    high enough to proceed, or does this need user eyes?
Q5: For optimizer returns: did optimizations maintain correctness (quality gate)?
    Are the changes substantive enough to merge, or discard the ephemeral worktree?
Q6: If green phase just passed (all tests pass, quality gate clean): is this task
    performance-sensitive? (hot path, frame processing loop, batch operation,
    real-time constraint) If yes → optimize before audit. If no → audit directly.
Q7: What's the next action for this task?
CHOSEN: DISPATCH_IMPLEMENTER | DISPATCH_OPTIMIZER | MESSAGE_AUDITOR |
        DISPATCH_DEBUGGER | RESUME_SUB_AGENT | ESCALATE_USER
```

**AUDIT_RESPONSE think prompt (on audit findings):**
```
Q1: What did the audit find? Severity breakdown (minor/moderate/major/critical).
Q2: Which findings are code issues (resume implementer) vs test issues
    (resume test-writer)? Separate clearly.
Q3: Are any findings actually plan contradictions rather than implementation bugs?
    If so, this is a PLAN_ERROR, not a fix.
Q4: What specific instructions should each resumed sub-agent receive?
    (file:line references, expected behavior, fix guidance from audit)
CHOSEN: RESUME_IMPLEMENTER | RESUME_TEST_WRITER | APPROVED | PLAN_ERROR | ESCALATE
```

**STUCK_ESCALATION think prompt:**
```
Q1: Which task is stuck? What phase was it in?
Q2: What was tried? (implementation attempts, debugger findings, audit cycles)
Q3: What evidence shows the approach cannot succeed?
Q4: What options remain? (scrap and retry with fresh approach, plan modification,
    user guidance on specific technical decision)
CHOSEN: PRESENT_TO_USER
```

#### `AUTHORIZED_SUB_AGENTS`

| Sub-Agent Type | Model | Notes |
|---|---|---|
| `test-writer` | Sonnet | Red phase. Writes tests, verifies failure, runs quality gate. Resumed for audit-driven test fixes. |
| `implementer` | Sonnet | Green phase. Implements code, verifies passing, runs quality gate. Resumed for audit-driven code fixes. |
| `debugger` | Sonnet | Dispatched when implementer is stuck. Investigates, applies fix, runs tests. |
| `optimizer` | Sonnet | Post-green, pre-audit. Dispatched only when RESULT_REVIEW deliberation identifies performance-sensitive code. Works in ephemeral worktree. Most tasks skip this. |



**Not dispatched by Coder:** `audit-checker` (dispatched by Auditor teammate, not Coder).

#### `DELEGATION_COMPOSITION`

**For test-writer (red phase):**
1. Task requirements, limitations, considerations from plan
2. Test expectations (test IDs, descriptions, requirement_refs)
3. Relevant context packet sections (file paths, type signatures, existing patterns)
4. Design doc sections this task implements
5. Worktree path (injected by hook)
6. Return instruction: write tests, run them, verify failure (AssertionError/ImportError not syntax), run quality gate, return structured result

**For implementer (green phase):**
1. Task requirements, limitations, considerations, suggestions from plan
2. Test RESULTS — pytest output showing which tests fail and with what errors (NOT test source code — INV-1)
3. Relevant context packet sections
4. Plan adherence checklist (what "done" looks like)
5. Worktree path (injected by hook)
6. Return instruction: implement, run tests, verify passing, run quality gate, return structured result with decisions_made

**For implementer (audit fix — fresh spawn or resume):**
1. Specific audit findings with file:line references and expected behavior
2. Previous sub-agent's return (decisions_made, carry_forward, files_modified) — provides full context for a cold-starting replacement
3. Original delegation prompt (task requirements, plan chunk)
4. Instruction: fix the specific issues, re-run ALL tests (not just affected), verify green, run quality gate

**For test-writer (audit fix — fresh spawn or resume):**
1. Specific audit findings about test issues
2. Previous sub-agent's return (decisions_made, carry_forward, tests_written)
3. Original delegation prompt
4. Instruction: fix test issues, re-run tests, verify they still fail against unimplemented behavior OR pass if implementation is complete, run quality gate

**For debugger:**
1. Error output — pytest failures, stack traces, error messages
2. Source files involved (from implementer's return)
3. What the implementer tried (from implementer's decisions_made)
4. Plan context for what the code should do
5. Worktree path
6. Return instruction: diagnose, fix, run tests, return with root_cause and fix_applied

#### `MODEL_SELECTION_HEURISTIC`

**Not applicable.** All Coder sub-agents are Sonnet (fixed). The Coder itself runs on Opus.

#### `SYNTHESIS_OUTPUT_TYPE`

**Produces per task:**
- Merged task branch in the phase worktree (the actual code + tests)
- Task completion record in the tracking table

**Produces per phase:**
- Merged phase branch ready for primary branch integration
- **Phase termination log** at `.claude/logs/{feature}-phase-{N}-coder-log.json`:
  - Tasks completed, scrapped, and stuck-escalated
  - Sub-agent dispatch history (who was dispatched for what, how many audit rounds)
  - Merge conflict resolutions (what conflicted, how resolved)
  - Decisions made at each RESULT_REVIEW and AUDIT_RESPONSE
  - Issues encountered and how they were resolved
  - Cross-task observations (patterns, risks for next phase)

#### `DOMAIN_VERIFICATION`

Verification is distributed across the pipeline:

1. **Sub-agent level:** test-writer and implementer run quality gates (ruff format, ruff check, pyright, pytest) before returning. A sub-agent cannot return without a clean gate.
2. **Coder level (RESULT_REVIEW):** Coder verifies return structure, checks quality gate results, validates that test failures/passes match expectations.
3. **Auditor level:** audit-checker verifies plan adherence, test design, code quality, design coherence. This is the independent verification gate.
4. **Post-merge:** After merging a task branch into the phase branch, run the full test suite on the phase branch to catch integration issues between parallel tasks.

#### `OUTPUT_WRITE_GLOBS`

```
.claude/logs/*-coder-log.json
.claude/handoffs/**
```

The Coder itself writes only logs and handoffs. All source code and test code is written by sub-agents in their worktrees. The Coder touches code only during merge conflict resolution (which is in the phase worktree, not the primary branch).

#### `POST_ACTIONS`

The Coder has no file-write post-actions (it doesn't write code). Sub-agents have their own post-actions (ruff_lint_critical on every Write/Edit).

#### `PERSISTENCE_MODEL`

**Persistent across ALL phases, /clear between phases.** The Coder is the longest-lived teammate.

Between phases:
1. Write phase termination log
2. Merge phase branch into primary
3. /clear to reset context
4. Orchestrator re-assigns next phase
5. Coder loads new phase in PHASE_INGESTION

The termination log preserves cross-phase continuity — the Coder reads prior phase logs on /clear recovery to understand what was already built.

#### `DOMAIN_STATES`

The Coder's domain is event-driven coordination. Its domain states replace the base pattern's linear lifecycle:

- **PHASE_INGESTION** — replaces CONTEXT_LOADING + DELIBERATION for the phase-level view
- **DISPATCH_READY** — replaces SUB_AGENT_DISPATCH, but recurring (dispatches waves, not one batch)
- **COORDINATING** — the event loop hub, no base pattern equivalent
- **RESULT_REVIEW** — replaces SYNTHESIS for individual sub-agent returns
- **AUDIT_RESPONSE** — Coder-specific, handles cross-domain audit flow
- **MERGE_MANAGEMENT** — Coder-specific, handles worktree merges
- **STUCK_ESCALATION** — replaces ERROR for user-facing escalation

The base pattern's extension mechanism used here is **OVERRIDE** (same as Planner). The Coder replaces the linear lifecycle with an event-driven loop. Base orthogonal states (HANDOFF, TERMINATED) still apply.

#### `DOMAIN_ERROR_PATHS`

| Failure | Recovery | Rationale |
|---|---|---|
| Sub-agent crash/timeout | Retry dispatch once (fresh sub-agent, same worktree). If retry fails → STUCK_ESCALATION | Transient failures (container issues, PTC timeout) are common enough to warrant one auto-retry |
| Debugger returns `stuck` | STUCK_ESCALATION → user | Debugger has its own internal progress loop (stall threshold: 3). By the time it returns `stuck`, it has exhausted multiple hypotheses and fix attempts. After debugger, it's a human problem. |
| Audit loop > 3 rounds on same task | STUCK_ESCALATION → user | Coder and Auditor disagree on what's correct. User arbitrates. |
| Merge conflict on phase branch | Coder resolves directly. If resolution breaks tests → STUCK_ESCALATION | Merge conflicts are the Coder's direct responsibility (one of the few things it does hands-on) |
| Plan contradiction discovered during implementation | PLAN_ERROR → orchestrator → may respawn Planner | Implementer or Auditor found that the task's requirements are impossible or contradictory |
| Multiple tasks in a wave all fail | Reassess wave grouping. Message Explorer for updated context. If systemic → STUCK_ESCALATION | Could indicate stale context packets or incorrect dependency analysis |

Progress-based tracking (same pattern as Planner, from coder/tester V1): stall counters on audit cycles and implementation retries. Progress resets the counter. Stall increments it. Threshold triggers STUCK_ESCALATION.

#### `SELF_EXECUTE_SCOPE`

**The Coder is a director. Its self-execute scope is coordination, not coding.**

**Self-executable:**
- Reading plan chunks, context packets, sub-agent returns
- Designing delegation prompts (this is the Coder's primary intellectual work)
- Tracking task state (maintaining the task tracking table)
- Merge conflict resolution (the only time the Coder touches code directly)
- Post-merge test verification (running pytest on the phase branch via PTC)
- Sending structured messages to Auditor, Explorer, orchestrator
- Writing termination logs

**NOT self-executable (delegated):**
- Writing test code → test-writer sub-agent
- Writing source code → implementer sub-agent
- Debugging stuck implementations → debugger sub-agent
- Auditing code quality → Auditor teammate (which dispatches audit-checker)
- Optimization → Auditor teammate (design-level, post all phases)

**The boundary:** The Coder writes delegation prompts and coordination decisions. Sub-agents write code. The Coder's value is in understanding what needs to be done and designing precise instructions — not in executing those instructions itself.

#### `CROSS_DOMAIN_PARTNERS`

| Partner | Direction | Message Type | When | Request Schema |
|---|---|---|---|---|
| Auditor | Coder → Auditor | `info_request` | Green phase complete, task ready for audit | `{ task_id, worktree_branch, plan_chunk_ref, test_results_summary }` |
| Auditor | Auditor → Coder | `info_ready` | Audit findings ready | `{ task_id, verdict, findings[], severity_summary }` |
| Auditor | Coder → Auditor | `info_request` | Fixes applied, re-audit requested | `{ task_id, fixes_applied[], sub_agent_rerun_results }` |
| Explorer | Coder → Explorer | `info_request` | Task needs context not in existing packets | `{ query, affected_tasks[], codebase_area, why_needed }` |
| Researcher | Coder → Researcher | `info_request` | Task needs external API/library info | `{ research_query, affected_tasks[], urgency }` |
| Orchestrator | Coder ↔ Orchestrator | Various | Sub-agent spawn mediation, phase completion, plan error reporting | Standard protocol |
| Planner | Coder → Planner (via orchestrator) | `info_request` | Plan contradiction found | Orchestrator may spawn new Planner |

**Rationale:** AGENT-ARCHITECTURE.md §3.5 (cross-domain: "Messages Explorer for context, Auditor for review requests"). The Coder's primary cross-domain relationship is with the Auditor — every task goes through the audit cycle.

---

## 5. Tester

**Role summary:** Scenario test planner and execution monitor. The Tester is a hybrid — a planner for the test domain (designs eval scenarios with the user through a granular review cycle) and a persistent monitor for the execution domain (runs scenarios after coders merge, reports results, triggers re-execution after remediation). Designs Pass D evaluation scenarios blind to implementation (INV-1), dispatches scenario-writer and test-writer sub-agents for mechanical code work, runs pytest directly for scenario execution, and messages Coder/Auditor with structured failure reports. Per-phase persistence.

**Sources consulted:**
- `AGENT-ARCHITECTURE.md` §3.6 (Tester definition, behavior, skills, cross-domain)
- `AGENT-ARCHITECTURE.md` §4.2 (scenario-writer, test-writer sub-agent definitions)
- `AGENT-ARCHITECTURE.md` §5.1, §5.5 (dispatch rules, model selection)
- `AGENT-ARCHITECTURE.md` §6.1 (INV-1: test/code isolation)
- `BASE-DIRECTOR-PATTERN.md` §2-§7 (lifecycle phases, extension point API)
- `state-machines/tester.json` (V1 Tester — 12 states, 17 transitions)
- `SUB-AGENT-SPECS.md` §5 (test-writer), §7 (scenario-writer)
- `new_claude/skills/test-architecture/` (scenario tier design, Pass D eval patterns)
- `new_claude/skills/sub-agent-delegation/SKILL.md` (delegation lifecycle, dispatch modes)

### V1 State Machine Alignment

The V1 Tester (`state-machines/tester.json`, v2.0.0) has 12 states with 2 think prompts (SCENARIO_PLANNING, SCENARIO_REPORTING), 1 user approval gate (SCENARIOS_APPROVED), daemon-mediated re-execution via `remediation_fixes_merged`, and progress-based stall tracking on the execution cycle.

**V1 → V2 Assessment: Incremental Enhancement (Not Redefinition).** The Tester's V1 role IS its V2 role — a director that designs scenarios as direct work and delegates code writing. Unlike the Coder (full redefinition), the V1 state machine is the foundation. The key structural change is expanding the single `SCENARIO_PLANNING` state into a multi-state planning sub-cycle with user review gates, mirroring the Planner's granular review cycle. The execution cycle is preserved with surgical enhancements.

| V1 State | V2 State | Change |
|----------|----------|--------|
| `SPAWNED` | `SPAWNED` | No change. Orchestrator sends design file path + task assignment. |
| `SCENARIO_PLANNING` | `DESIGN_INGESTION → SCENARIO_STRATEGY ⇄ STRATEGY_REVIEW → SCENARIO_SPECIFICATION ⇄ SPEC_REVIEW` | **Expanded.** V1 single state becomes V2 5-state planning sub-cycle with 2 user review gates and 3 plan-checker dispatch points. This is the Planner-level granularity enhancement. See Planning Sub-Cycle section below. |
| `SCENARIOS_APPROVED` | `SCENARIOS_APPROVED` | No change. User approved scenario list. Ready to build. |
| `SCENARIO_BUILDING` | `SCENARIO_BUILDING` | **Enhanced.** V1: Tester wrote code directly (`model: sonnet`, `write_allowed: true`). V2: dispatches scenario-writer sub-agent to scenario worktree. Tester stays Opus, delegates mechanical code writing. SubagentStop hook enforces INV-1. |
| `SCENARIO_VALIDATION` | `SCENARIO_VALIDATION` | **Enhanced.** V2: plan-checker resumed (Dimension A — code vs Tester's spec). Can dispatch test-writer for infrastructure fixes, not just loop back to scenario-writer. |
| `SCENARIOS_READY` | `SCENARIOS_READY` | **Adjusted.** Plan revision routes to DESIGN_INGESTION (was → SCENARIO_PLANNING). Tester re-grounds from design understanding, not just scenario list. |
| — | `MERGE_MANAGEMENT` | **Added.** Merge scenario branch into phase branch after coders merge. Tester resolves conflicts directly (same pattern as Coder). No V1 equivalent — V1 didn't use worktrees. |
| `SCENARIO_EXECUTION` | `SCENARIO_EXECUTION` | No change. Tester runs pytest directly via PTC. blocked_tools: [Write, Edit]. |
| `SCENARIO_REPORTING` | `SCENARIO_REPORTING` | **Enhanced.** V2: messages Coder directly (not through orchestrator). Structured failure report with design requirement mapping. Can message Auditor when failure pattern suggests plan misalignment. |
| `ERROR` | `ERROR` | No change. Same RETRY/ESCALATE/HANDOFF paths with identical think prompt. |
| `IDLE` | `IDLE` | No change. Daemon-mediated re-execution via `remediation_fixes_merged`. |
| `PHASE_COMPLETE` | `PHASE_COMPLETE` | **Enhanced.** Writes termination log capturing scenario decisions, coverage map, execution history (same pattern as Planner/Coder). |
| `HANDOFF` | `HANDOFF` | No change. Context pressure → write handoff with scenario state. |

**States eliminated:** 1 (SCENARIO_PLANNING split into 5)
**States added:** 6 (DESIGN_INGESTION, SCENARIO_STRATEGY, STRATEGY_REVIEW, SCENARIO_SPECIFICATION, SPEC_REVIEW, MERGE_MANAGEMENT)
**Net state count:** V1 had 12 states. V2 has **17 states.**

The increase is justified by the Tester's dual-mode nature: a Planner-like granular review cycle for scenario design (5 new states replacing 1) plus a Coder-like merge management state for worktree integration. The execution cycle (SCENARIO_EXECUTION → SCENARIO_REPORTING → IDLE) is unchanged.

### Planning Sub-Cycle — V2 Enhancement Detail

This is the biggest structural change from V1. V1's single SCENARIO_PLANNING state tried to do too much — understand the design, plan what to test, specify how to test it, and interact with the user, all with one think prompt. V2 breaks this into a granular review cycle matching the Planner's approach.

**Flow:**

```
DESIGN_INGESTION
    │  [think on entry — daemon critical annotation]
    │  think → understand design, ground capabilities, check context
    │
    │  ┌─ NEED_CONTEXT ──► message Explorer/Researcher ──► loop back
    │  ├─ CLARIFY ──► message orchestrator for user ──► loop back
    │  └─ PROCEED ▼
    │
SCENARIO_STRATEGY
    │  [think on entry — daemon critical annotation]
    │  think → design coverage map, tier allocation, cross-task scenarios
    │  write to scenario plan directory
    │
STRATEGY_REVIEW
    │  plan-checker SPAWNED (Dimension B: coverage vs design/plan)
    │  user reviews strategy + checker report
    │  ┌─ approved ──► SCENARIO_SPECIFICATION
    │  └─ changes ──► SCENARIO_STRATEGY (progress-tracked)
    │
SCENARIO_SPECIFICATION
    │  [think on entry — daemon critical annotation]
    │  think → write per-scenario details, assertions, infra needs
    │  write to scenario plan directory
    │
SPEC_REVIEW
    │  plan-checker RESUMED (Dimension B: feasibility + value vs design)
    │  user reviews specs + checker report
    │  ┌─ approved ──► SCENARIOS_APPROVED
    │  └─ changes ──► SCENARIO_SPECIFICATION (progress-tracked)
```

**Why 5 states, not 1:** Each state has a distinct think prompt, distinct output, and a distinct review concern. DESIGN_INGESTION is about understanding (may loop for context). SCENARIO_STRATEGY is about coverage decisions (user reviews what to test). SCENARIO_SPECIFICATION is about concrete details (user reviews how to test). Collapsing these would force a single think prompt to cover understanding, strategy, AND specification — the same problem V1 had.

**Why 2 user review gates (not 5 like the Planner):** The Planner decomposes across 5 dimensions (phases, tasks, details, tests, final). Scenario planning has 2 natural levels: strategy (what to test, at what tiers) and specification (detailed per-scenario specs). More gates would be artificial. Fewer would collapse the planning into a rubber-stamp.

**DESIGN_INGESTION self-loop:** After the think prompt, the Tester deliberates on whether it has everything needed. If context is missing (public API specs, behavioral contracts not clear from design doc), it messages Explorer/Researcher with a structured request and loops back. If the design has ambiguities that affect scenario design, it messages the orchestrator for user clarification. This mirrors the Planner's DESIGN_INGESTION flow exactly.

**What makes the Tester's DESIGN_INGESTION different from the Planner's:** The Planner grounds design concepts to codebase constructs. The Tester grounds design concepts to testable behaviors — it reads the design to understand WHAT to test, not HOW the code implements it. The Tester is blind to implementation (INV-1), so its grounding is design-focused: acceptance criteria, public API contracts, behavioral specifications, cross-task interaction points.

### Plan-Checker Integration

The Tester dispatches plan-checker for two verification dimensions across three gates:

**Dimension A — Scenario code vs Tester's spec:** After the scenario-writer implements test code, did it build what the Tester designed? The Tester's scenario specification IS a plan — plan-checker verifies the implementation against it. Same role plan-checker plays for the Planner, applied to the test domain.

**Dimension B — Scenario coverage vs design/plan:** Do the scenarios (as designed by the Tester) adequately cover the original design document and implementation plan? Are there critical behavioral gaps? Is anything missing that would let a broken implementation slip through?

**Spawn and resume pattern:**

| Gate | Dimension | What It Verifies | Plan-Checker Action |
|------|-----------|-----------------|-------------------|
| `STRATEGY_REVIEW` | **B** (coverage vs design) | Does the coverage map hit every acceptance criterion in the design? Are critical user-facing behaviors all covered? Any gaps that would make the scenario suite ineffective? | **Spawn** (first invocation, Sonnet) |
| `SPEC_REVIEW` | **B** (feasibility + value vs design) | Are scenarios realistic and valuable? Do they test what matters, not what's easy? Are cross-task integration scenarios covering the real integration points from the plan? | **Resume** (has strategy context) |
| `SCENARIO_VALIDATION` | **A** (code vs Tester's spec) | Did the scenario-writer build what the Tester specified? Every scenario in the spec implemented? Assertions match? Tiers correct? | **Resume** (has full spec context — knows exactly what should have been built) |

**Why resume, not re-spawn:** By SCENARIO_VALIDATION, the plan-checker knows the full picture: the design, the strategy, the detailed specs, AND now the code. It catches drift at every level. Re-spawning would lose the accumulated understanding of what the Tester intended.

**Cold-start fallback:** If resume fails, the Tester cold-starts a fresh plan-checker with: the original delegation prompt + `previous_verification_report` containing the prior gate's full structured return. Same pattern as the Planner — see SUB-AGENT-SPECS Common Conventions.

**Plan-checker dispatch is orchestrator-mediated:** Tester writes delegation JSON, messages orchestrator, orchestrator spawns (or resumes) the plan-checker. Tester waits for the verification report before presenting to user (at review gates) or before proceeding (at SCENARIO_VALIDATION). (Note: orchestrator mediation is a temporary constraint — see document preamble.)

**Revision after plan-checker REVISE verdict:** If plan-checker returns REVISE at a review gate, the Tester reads the revision suggestions, applies fixes, and re-dispatches the checker (resumed) before going back to user review. The user sees the clean version with the checker's PASS report.

### Revision Tracking — Progress-Based, Not Hard Caps

Following the Planner's established pattern. The daemon tracks a consecutive stall counter per review gate. Each revision loop, the daemon evaluates whether the Tester made meaningful progress:

- **Progress detected** (coverage map changed substantively — scenarios added/removed/re-tiered, new cross-task scenarios identified, gaps filled) → stall counter resets to 0, revision continues
- **No progress** (same issues, cosmetic changes only, same plan-checker dimensions failing) → stall counter increments

**Stall thresholds per gate:**

| Review Gate | Stall Threshold | On Stall | Rationale |
|-------------|----------------|----------|-----------|
| STRATEGY_REVIEW | 3 consecutive no-progress | Escalate to user with stuck diagnosis | Strategy is high-level — user arbitrates coverage decisions |
| SPEC_REVIEW | 2 consecutive no-progress | Escalate to user | Specs are more granular, should converge faster |

The execution cycle preserves V1's progress-based tracking on SCENARIO_REPORTING (stall threshold 5 on consecutive cycles with no progress, reset on progress).

### Merge Management

After coders merge all tasks in the phase, the Tester merges its scenario branch into the phase branch before executing scenarios against the merged code.

**Placement:** Between SCENARIOS_READY and SCENARIO_EXECUTION. Rationale: scenarios test merged code, so coders merge first → Tester merges scenario branch on top → then executes.

**Flow within MERGE_MANAGEMENT:**

1. Merge scenario branch into phase branch (git merge)
2. If clean merge → run collect-only to verify scenarios still valid after merge
3. If merge conflict → Tester resolves directly (same as Coder — this IS direct work, not delegated). Conflicts arise when scenario conftest/fixtures collide with coder-written test infrastructure.
4. Post-merge collect-only: if scenarios broken by merge → dispatch test-writer to fix infrastructure
5. Once collect-only passes → proceed to SCENARIO_EXECUTION

**Why the Tester resolves conflicts directly:** Merge conflict resolution is a coordination decision (which version to keep, how to reconcile), not mechanical code writing. The Tester understands the scenario intent and can make the right resolution. This matches the Coder's MERGE_MANAGEMENT pattern.

### INV-1 Enforcement

The Tester is blind to implementation. It reads:
- Design documents, implementation plan (phase-level), public API specs (from design/plan, not source)
- Context packets (for behavioral specs — public API info requested from Explorer)
- Scenario test code (its own sub-agents' output — for validation and review)
- Pytest output (for result analysis in SCENARIO_REPORTING)

It does NOT read:
- Source code (`src/**`, `lib/**`)
- Unit tests (`tests/unit/**`, `tests/test_*.py`)
- Implementation details of any kind

**Pytest output and INV-1:** During SCENARIO_EXECUTION and SCENARIO_REPORTING, the Tester reads pytest output which includes stack traces with source code snippets. This does NOT violate INV-1 — the Tester sees failure information (what went wrong), not implementation details (how the code works). The Tester's job is "scenario X failed because the expected behavior didn't occur" and routing that to the Coder. It does not diagnose implementation bugs.

### V2 State Machine

```
SPAWNED
    │
    ▼
DESIGN_INGESTION  ◄──────────────────────────── (plan revision from SCENARIOS_READY)
    │  [think on entry — daemon critical annotation]
    │  think → read design, understand capabilities, check context
    │
    │  ┌─ NEED_CONTEXT ──► message Explorer/Researcher ──► loop
    │  ├─ CLARIFY ──► message orchestrator for user ──► loop
    │  └─ PROCEED ▼
    │
    ▼
SCENARIO_STRATEGY
    │  [think on entry — daemon critical annotation]
    │  think → write coverage map, tier allocation, cross-task scenarios
    │
    ▼
STRATEGY_REVIEW
    │  plan-checker SPAWNED (Dimension B: coverage vs design/plan)
    │  user reviews strategy + checker report
    │  ┌─ approved ──► SCENARIO_SPECIFICATION
    │  └─ changes requested ──► SCENARIO_STRATEGY (progress-tracked, threshold 3)
    │
    ▼
SCENARIO_SPECIFICATION
    │  [think on entry — daemon critical annotation]
    │  think → write per-scenario details, assertions, infra needs
    │
    ▼
SPEC_REVIEW
    │  plan-checker RESUMED (Dimension B: feasibility + value vs design)
    │  user reviews specs + checker report
    │  ┌─ approved ──► SCENARIOS_APPROVED
    │  └─ changes requested ──► SCENARIO_SPECIFICATION (progress-tracked, threshold 2)
    │
    ▼
SCENARIOS_APPROVED
    │
    ▼
SCENARIO_BUILDING ◄──────────────┐
    │  (dispatch scenario-writer    │
    │   to scenario worktree)       │
    │                               │
    ▼                               │
SCENARIO_VALIDATION                 │
    │  plan-checker RESUMED         │
    │  (Dimension A: code vs        │
    │   Tester's spec)              │
    │  + collect-only check         │
    │                               │
    │  ┌─ validation passed ──► SCENARIOS_READY
    │  ├─ code doesn't match spec ──┘ (re-dispatch scenario-writer)
    │  └─ infra broken ──► dispatch test-writer, then ──┘
    │
    ▼
SCENARIOS_READY
    │  (waiting for coders to merge all tasks in phase)
    │  ┌─ plan revision flag set ──► DESIGN_INGESTION
    │  └─ coders merged ▼
    │
    ▼
MERGE_MANAGEMENT
    │  (merge scenario branch into phase branch)
    │  (resolve conflicts directly)
    │  (post-merge collect-only to verify scenarios still valid)
    │  (if broken → dispatch test-writer)
    │
    ▼
SCENARIO_EXECUTION
    │  Tester runs pytest directly via PTC
    │  blocked_tools: [Write, Edit]
    │
    ├─► SCENARIO_REPORTING
    │       [think on entry — daemon critical annotation]
    │       ├─ ALL_PASSED ──► IDLE (send pass report)
    │       ├─ PROGRESS ──► IDLE (message Coder, reset stall counter)
    │       ├─ STALLED < 5 ──► IDLE (detailed report to Coder)
    │       └─ STALLED ≥ 5 ──► IDLE (escalate to orchestrator +
    │                                 message Auditor if plan misalignment suspected)
    │
    └─► ERROR
            [think on entry — daemon critical annotation]
            ├─ RETRY ──► SCENARIO_EXECUTION (once)
            ├─ ESCALATE ──► IDLE
            └─ HANDOFF

IDLE
    ├─ remediation_fixes_merged ──► SCENARIO_EXECUTION
    └─ all_scenarios_passed_or_arbitrated ──► PHASE_COMPLETE

PHASE_COMPLETE  (writes termination log, terminal)
HANDOFF  (context pressure, terminal)
```

**State count:** 17 states (SPAWNED, DESIGN_INGESTION, SCENARIO_STRATEGY, STRATEGY_REVIEW, SCENARIO_SPECIFICATION, SPEC_REVIEW, SCENARIOS_APPROVED, SCENARIO_BUILDING, SCENARIO_VALIDATION, SCENARIOS_READY, MERGE_MANAGEMENT, SCENARIO_EXECUTION, SCENARIO_REPORTING, ERROR, IDLE, PHASE_COMPLETE, HANDOFF)

### Deliberation Model

All think prompts fire **on state entry** as daemon critical annotations. The agent addresses each via Think MCP before doing the state's work. This ensures the daemon can set the annotation when the state is entered, and the agent's reasoning is captured before action.

**States with think-on-entry:**

| State | Think Trigger | What It Decides |
|-------|--------------|-----------------|
| DESIGN_INGESTION | On entry (initial or plan-revision loop) | What capabilities this phase delivers, what context is available, whether to proceed or request more |
| SCENARIO_STRATEGY | On entry (initial or revision loop) | Coverage categories, tier allocation, cross-task scenarios, coverage-to-requirement mapping |
| SCENARIO_SPECIFICATION | On entry (initial or revision loop) | Per-scenario details, assertion concreteness, infrastructure requirements |
| SCENARIO_REPORTING | On entry (after execution completes) | Pass/fail analysis, progress assessment vs previous cycle, routing decision |
| ERROR | On entry (after infrastructure failure) | Failure diagnosis, recovery path selection |

**States WITHOUT think prompts:**
- STRATEGY_REVIEW, SPEC_REVIEW — user review states, Tester waits for input
- SCENARIOS_APPROVED — transition state
- SCENARIO_BUILDING — sub-agent dispatch, delegation prompt is the deliberation
- SCENARIO_VALIDATION — plan-checker + collect-only check, mechanical verification
- SCENARIOS_READY — waiting state
- MERGE_MANAGEMENT — mechanical git operations (think only if conflict resolution requires judgment, handled ad-hoc)
- SCENARIO_EXECUTION — mechanical pytest execution via PTC
- IDLE — waiting state

**Think prompts:**

**DESIGN_INGESTION** (on entry):
```
Q1: What capabilities does this phase deliver? List from design doc and plan.
Q2: What are the user-facing behaviors and acceptance criteria?
Q3: What public API contracts are specified? (function signatures, input/output
    types, error contracts — from design/plan, NOT source code)
Q4: Are there cross-task interactions within this phase that create emergent
    behaviors individual task tests would miss?
Q5: Is all context loaded? Do I have design doc, plan, and relevant API specs?
    If missing, what specific context do I need from Explorer?
Q6: If returning after plan revision: what changed in the plan? How does that
    affect which capabilities are delivered and which scenarios are needed?
CHOSEN: PROCEED | NEED_CONTEXT | CLARIFY
```

PROCEED → SCENARIO_STRATEGY. NEED_CONTEXT → message Explorer/Researcher with structured request, loop back to DESIGN_INGESTION with new context. CLARIFY → message orchestrator for user clarification, loop back.

**SCENARIO_STRATEGY** (on entry):
```
Q1: What categories of scenarios are needed?
    - Per-task behavioral (individual task capabilities)
    - Cross-task integration (interactions between tasks in this phase)
    - Error/boundary (edge cases, invalid inputs, failure modes)
Q2: Tier breakdown — for each scenario category:
    - Critical: core user-facing behaviors that MUST work
    - Important: expected behaviors, common paths
    - Edge_case: boundary conditions, error recovery
Q3: Coverage map: which design requirements map to which scenarios?
    Every acceptance criterion should have at least one scenario.
    Flag any requirements with no clear scenario.
Q4: Cross-task scenarios — what integration points exist between tasks
    in this phase? These are highest-value scenarios.
Q5: Is the scenario count achievable? Prioritize by tier if too many.
CHOSEN: STRATEGY_COMPLETE
```

**SCENARIO_SPECIFICATION** (on entry):
```
Q1: For each scenario: is the behavior under test clearly specified in the
    design doc? Flag ambiguous scenarios that need user clarification.
Q2: Are inputs and expected outputs concrete enough for a scenario-writer
    to implement without guessing?
Q3: Are assertion criteria machine-verifiable? No vague "should work correctly."
    Every assertion must be a concrete check.
Q4: Do cross-task scenarios test actual integration points, not just
    re-test individual task behaviors from a different angle?
Q5: Are there infrastructure requirements (fixtures, test data, external
    service stubs) that need to be specified for the scenario-writer?
CHOSEN: SPEC_COMPLETE
```

**SCENARIO_REPORTING** (on entry — V1 preserved, unchanged):
```
Q1: How many scenarios passed vs failed? List the counts.
Q2: If this is the first execution cycle (no previous results to compare),
    any failures are an informational baseline — declare PROGRESS.
Q3: Compare current failures with the previous cycle: which specific failures
    were resolved? Which persist unchanged? Which are new?
Q4: Overall assessment: has the coder made measurable progress (fewer total
    failures, different failure patterns, resolved some previous issues) or
    is the coder stuck on the same issues (identical or worse failure set)?
CHOSEN: ALL_PASSED | PROGRESS | STALLED
```

**ERROR** (on entry — V1 preserved, unchanged):
```
Q1: What type of failure occurred (pytest crash, OOM, container error,
    dependency missing)? State the error type and pytest exit code.
Q2: Is the failure transient (container restart needed, temporary resource
    exhaustion) or structural (critical dependency broken, environment
    corrupted)? State your evidence.
Q3: Did any test suites complete successfully before the failure? List what
    ran and results if available.
Q4: RETRY: Is the failure transient AND has retry not been used? What would
    you verify before retrying?
Q5: ESCALATE: Is the infrastructure broken in a way the tester cannot resolve?
    What should the orchestrator know?
Q6: HANDOFF: Is this a context pressure issue rather than an infrastructure
    failure?
CHOSEN: RETRY | ESCALATE | HANDOFF
```

### Extension Points

#### `INGRESS_CONTEXT_LOADING`

**What the Tester loads on spawn:**
1. The design document (path from orchestrator's task assignment message)
2. The implementation plan — phase-level tasks, acceptance criteria, behavioral requirements
3. Context packets in `.claude/context/` that contain public API specs for this phase's codebase surface area (behavioral contracts, function signatures, input/output types — NOT implementation details)
4. If this is a respawn (plan revision after implementation): the **termination log** from `.claude/logs/{feature}-phase-{N}-tester-log.json` — prior Tester's scenario decisions, coverage map, execution history
5. If this is a replacement spawn (after HANDOFF): the handoff summary from `.claude/handoffs/` plus any existing scenario plan state and scenario test files

**What it does NOT load:**
- Source code (INV-1 — blind to implementation)
- Unit tests / Pass A/B/C tests (INV-1 — blind to unit tests)
- Implementation details of any kind
- Other phases' scenario state (out of scope)

**Rationale:** The Tester works from design docs + plan + public API specs, never from source code or unit tests. When context packets don't contain sufficient public API information, the Tester messages Explorer with a structured request specifying what behavioral contracts it needs — Explorer provides the information without exposing implementation details.

#### `DELIBERATION_THINK_PROMPT` / `DOMAIN_THINK_QUESTIONS`

The Tester's deliberation is distributed across its domain states — each work state has its own think prompt that fires on state entry as a daemon critical annotation. The base pattern's DELIBERATION state is not used as a separate state; instead, the Tester's first think point is the DESIGN_INGESTION think prompt (see Deliberation Model section above for all 5 think prompts).

#### `AUTHORIZED_SUB_AGENTS`

| Sub-Agent Type | Model | Dispatched When | Notes |
|---|---|---|---|
| `scenario-writer` | Sonnet (fixed) | SCENARIO_BUILDING | Writes Pass D scenario test code in scenario worktree. Blind to implementation AND unit tests (INV-1). |
| `test-writer` | Sonnet (fixed) | SCENARIO_VALIDATION (infra fixes), MERGE_MANAGEMENT (post-merge fixes) | Repairs broken test infrastructure — fixtures, conftest, import issues. NOT the same as Coder dispatching test-writer for red phase. |
| `plan-checker` | Sonnet (fixed) | STRATEGY_REVIEW (spawn), SPEC_REVIEW (resume), SCENARIO_VALIDATION (resume) | 3-gate verification: coverage vs design, feasibility vs design, code vs Tester's spec. |



**Rationale:** AGENT-ARCHITECTURE.md §3.6 and §5.1. The Tester dispatches scenario-writer for mechanical code writing, test-writer for infrastructure repairs, and plan-checker for independent verification. All work that requires scenario design reasoning (strategy, specification, result interpretation) is done by the Tester directly.

#### `DELEGATION_COMPOSITION`

**For scenario-writer (SCENARIO_BUILDING):**
1. **Scenario specifications** — the Tester's approved scenario list with per-scenario details (behavior tested, tier, inputs, expected outputs, assertions, design requirement reference)
2. **Design document sections** — the acceptance criteria and behavioral requirements each scenario maps to
3. **Plan (phase-level)** — what capabilities this phase delivers, for scenario grounding
4. **Public API specs** — function signatures, input/output types, error contracts (from context packets, NOT source code)
5. **Worktree path** — injected by hook (scenario worktree branched off phase branch)
6. **Return instruction:** write scenario test files, run collect-only to verify discoverability, run quality gate on scenario files, return structured result with scenarios_written and collect_only_result

**For test-writer (infrastructure fixes — SCENARIO_VALIDATION or MERGE_MANAGEMENT):**
1. **Error output** — collect-only failures, import errors, fixture issues
2. **Scenario files affected** — which scenario test files have infrastructure problems
3. **Previous scenario-writer return** — decisions_made, carry_forward (for cold-start replacement context)
4. **Worktree path** — same scenario worktree
5. **Return instruction:** fix infrastructure issues (fixtures, conftest, imports), re-run collect-only to verify, run quality gate, return structured result

**For plan-checker (3 gates):**

At STRATEGY_REVIEW (Dimension B — coverage vs design):
1. **Scenario strategy document** — coverage map, tier allocation, cross-task scenarios
2. **Design document** — for alignment checking
3. **Implementation plan (phase-level)** — acceptance criteria, task structure
4. **Verification focus:** goal_coverage (every acceptance criterion has scenarios), risk_identification (critical behaviors have critical-tier scenarios)

At SPEC_REVIEW (Dimension B — feasibility + value vs design):
1. **Scenario specifications** — detailed per-scenario specs
2. **Previous verification report** (from resume — has strategy context)
3. **Verification focus:** acceptance_criteria_clarity (assertions are concrete), technical_feasibility (scenarios can be implemented), scope_boundaries (scenarios test design intent, not implementation details)

At SCENARIO_VALIDATION (Dimension A — code vs Tester's spec):
1. **Scenario test files** — the scenario-writer's output
2. **Tester's scenario specifications** — what should have been built
3. **Previous verification reports** (from resume — has full context)
4. **Verification focus:** task_completeness (every specified scenario implemented), dependency_accuracy (assertions match specs), goal_coverage (no scenarios lost or changed in translation)

**Context NOT included in any delegation:**
- Source code (INV-1)
- Unit tests (INV-1)
- Other teammates' outputs
- Session history

#### `MODEL_SELECTION_HEURISTIC`

**Not applicable.** All Tester sub-agents are Sonnet (fixed). The Tester itself runs on Opus. No model selection decisions.

**Rationale:** AGENT-ARCHITECTURE.md §5.5: scenario-writer, test-writer, and plan-checker are all Sonnet (fixed). The Tester's sub-agent roster doesn't include variable-model scouts like Explorer/Researcher have.

#### `SYNTHESIS_OUTPUT_TYPE`

**Produces per execution cycle:**
- **Scenario test report** at `.claude/reports/{feature}-phase-{N}-scenario-report.json`:
  - Scenarios run, pass/fail counts
  - Per-scenario results with failure output
  - Design requirement mapping (which requirements passed/failed)
  - Progress assessment vs previous cycle
  - Stall counter state
- **Failure messages** to Coder (structured `info_ready` with failure details, design requirement mapping, reproduction guidance)

**Produces per phase:**
- Scenario test files in `tests/scenarios/**` and `tests/eval/**` (written by sub-agents, merged into phase branch)
- **Phase termination log** at `.claude/logs/{feature}-phase-{N}-tester-log.json`:
  - Scenario strategy decisions and rationale (coverage map, tier allocation)
  - Per-scenario specifications and design requirement traces
  - Plan-checker findings at each gate (coverage verification, code-vs-spec verification)
  - User feedback applied at each review gate
  - Execution history (how many cycles, progress/stall pattern, what was reported to Coder)
  - Auditor interactions (if any plan misalignment escalations)
  - Infrastructure issues encountered and how resolved
  - Cross-phase observations (patterns, risks for next phase's scenarios)

#### `DOMAIN_VERIFICATION`

Verification is distributed across the pipeline:

1. **Plan-checker level (STRATEGY_REVIEW, SPEC_REVIEW):** Independent verification that scenarios cover the design and plan requirements. Catches coverage gaps before code is written.
2. **Plan-checker level (SCENARIO_VALIDATION):** Independent verification that scenario-writer's code matches the Tester's specification. Catches implementation drift.
3. **Collect-only check (SCENARIO_VALIDATION, MERGE_MANAGEMENT):** Mechanical verification that scenarios are importable and discoverable. `pytest --collect-only` exit code 0.
4. **Sub-agent level:** scenario-writer and test-writer run quality gates (ruff format, ruff check, pyright) on scenario files before returning. Universal PostToolUse hook on Write/Edit.
5. **Tester level (SCENARIO_REPORTING):** Tester interprets execution results per phase — maps failures to design requirements, assesses progress vs stall, decides routing.
6. **Post-merge (MERGE_MANAGEMENT):** After merging scenario branch into phase branch, collect-only re-run to verify scenarios still valid after merge.

#### `OUTPUT_WRITE_GLOBS`

```
.claude/reports/**
.claude/logs/*-tester-log.json
.claude/handoffs/**
.claude/plans/scenario-plan/**
```

The Tester itself writes scenario plans (strategy document, specification document), reports, logs, and handoffs. All scenario test code is written by sub-agents (scenario-writer, test-writer) in their worktrees. The Tester touches scenario code only during merge conflict resolution in MERGE_MANAGEMENT (which is in the phase worktree, not the primary branch).

#### `POST_ACTIONS`

The Tester has no file-write post-actions on its own writes (reports, logs, and plans don't have schema validation hooks). Sub-agents have their own post-actions (ruff_lint_critical on every Write/Edit to scenario test files).

#### `PERSISTENCE_MODEL`

**Per-phase.** Tester persists for the duration of the phase — through the planning sub-cycle, scenario building, and all execution cycles until all scenarios pass or are arbitrated. Terminated when:
- PHASE_COMPLETE: all scenarios passed or arbitrated
- HANDOFF: context pressure

Before terminating, the Tester writes a **termination log** to `.claude/logs/{feature}-phase-{N}-tester-log.json` (see SYNTHESIS_OUTPUT_TYPE for full contents).

This log is the Tester's institutional memory. If a new Tester is spawned for the same phase (after HANDOFF or plan revision), it reads the termination log + existing scenario plan state + scenario test files. The respawned Tester understands WHY the scenarios look the way they do — coverage decisions, tier rationale, user feedback applied, plan-checker findings addressed.

**Rationale:** AGENT-ARCHITECTURE.md §3.6: "Per-phase persistence." The Tester needs to persist across the entire execution cycle (which may span many coder remediation rounds) to maintain the stall counter and compare results across cycles.

#### `DOMAIN_STATES`

The Tester's domain IS its state cycle. The planning sub-cycle + execution cycle replaces the base pattern's linear lifecycle. The base pattern's extension mechanism used here is **OVERRIDE** (same as Planner and Coder).

**Planning sub-cycle (replaces base CONTEXT_LOADING → DELIBERATION → SUB_AGENT_DISPATCH → SYNTHESIS):**
- DESIGN_INGESTION — understanding + context acquisition
- SCENARIO_STRATEGY + STRATEGY_REVIEW — coverage strategy + user review
- SCENARIO_SPECIFICATION + SPEC_REVIEW — detailed specs + user review

**Building + validation (maps partially to base DELEGATION → SYNTHESIS):**
- SCENARIO_BUILDING — sub-agent dispatch (scenario-writer)
- SCENARIO_VALIDATION — plan-checker + collect-only verification

**Execution + monitoring (no base pattern equivalent):**
- SCENARIOS_READY — waiting for coders (daemon-mediated)
- MERGE_MANAGEMENT — worktree merge into phase branch
- SCENARIO_EXECUTION — Tester runs pytest directly
- SCENARIO_REPORTING — result analysis + routing
- IDLE — between execution cycles (daemon-mediated re-execution)

Base orthogonal states (ERROR, HANDOFF, PHASE_COMPLETE) still apply.

#### `DOMAIN_ERROR_PATHS`

| Failure | Recovery Path | Rationale |
|---------|--------------|-----------|
| Scenario-writer crash/timeout | Re-dispatch once (fresh scenario-writer, same worktree, previous return as context). If retry fails → escalate to user | Transient failures warrant one auto-retry |
| Test-writer can't fix infrastructure | Escalate to user with diagnosis. May indicate missing dependency or environment issue | Infrastructure is foundational — can't proceed without it |
| Plan-checker stuck in REVISE loop (3+ iterations on same dimension) | Escalate to user | Tester and checker disagree on coverage/completeness — user arbitrates |
| Merge conflict in MERGE_MANAGEMENT that breaks scenarios | Tester resolves directly. If resolution breaks collect-only → dispatch test-writer. If still broken → escalate to user | Merge conflicts are the Tester's direct responsibility |
| Scenario failures suggest plan misalignment (STALLED ≥ 5) | Message Auditor with failure evidence + plan requirement mapping. Auditor spawns audit-checker to verify implementation against plan | Persistent scenario failures may indicate the implementation doesn't match the design, not just code bugs |
| All search strategies for context exhausted (Explorer/Researcher didn't respond) | Proceed with partial grounding in DESIGN_INGESTION, flag gaps in scenario strategy. User can fill gaps during STRATEGY_REVIEW | Same pattern as Planner — partial grounding is better than blocking |

Base 4-path recovery (RETRY, PARTIAL_SYNTHESIS, HANDOFF, ABORT) applies for infrastructure failures during SCENARIO_EXECUTION via the ERROR state. Progress-based tracking (same pattern as Planner, Coder, V1 Tester): stall counters on review gate revisions and execution cycles. Progress resets the counter. Stall increments it. Threshold triggers escalation.

#### `SELF_EXECUTE_SCOPE`

**Self-executable (Tester does directly):**
- **All scenario design reasoning** — coverage strategy, tier allocation, scenario specification, cross-task integration analysis. This is judgment work — the Tester's core competency.
- **Running scenario tests** — `pytest tests/scenarios/ tests/eval/` via PTC. 1-2 commands, mechanical execution. The Tester needs the raw output directly for analysis in SCENARIO_REPORTING.
- **Interpreting test results** — analyzing pass/fail patterns, comparing cycles, assessing progress vs stall, mapping failures to design requirements. This is the Tester's primary intellectual work in the execution phase.
- **PTC targeted queries** — checking scenario file existence, reading conftest.py, verifying worktree state. Simple queries where the Tester knows what to ask.
- **Reading design docs, plans, context packets, scenario test files** — for scenario design and result analysis
- **Merge conflict resolution** — the only time the Tester touches scenario code directly (MERGE_MANAGEMENT)
- **Writing scenario plans, reports, termination logs** — the Tester's primary write outputs
- **Sending structured messages** — to Coder (failure reports), Auditor (plan misalignment), Explorer/Researcher (context requests), orchestrator

**NOT self-executable (delegates or requests cross-domain):**
- **Writing scenario test code** → scenario-writer sub-agent
- **Fixing test infrastructure** → test-writer sub-agent
- **Independent scenario-to-plan verification** → plan-checker sub-agent (fresh eyes, no confirmation bias)
- **Broad codebase exploration** → messages Explorer when context packets don't contain needed public API specs
- **Research lookups** → messages Researcher when design references external libraries/patterns
- **Implementation-vs-plan auditing** → messages Auditor when persistent failures suggest design misalignment

**The boundary:** The Tester does everything that requires scenario design judgment or result interpretation. It delegates mechanical code writing (scenario-writer, test-writer) and independent verification (plan-checker). It runs tests directly (PTC) because the value is in the interpretation, not the execution. The delegation cost heuristic from the base pattern (< 3 tool calls → self-execute) applies — running pytest is 1-2 calls, well within self-execute scope.

#### `CROSS_DOMAIN_PARTNERS`

| Partner | Direction | Message Type | When | Request Schema |
|---|---|---|---|---|
| Coder | Tester → Coder | `info_ready` | SCENARIO_REPORTING — failures detected (PROGRESS or STALLED) | `{ phase_id, scenario_report_path, failures_summary, design_requirements_failing[], progress_assessment, cycle_number }` |
| Auditor | Tester → Auditor | `info_request` | SCENARIO_REPORTING — STALLED ≥ threshold AND failure pattern suggests plan misalignment | `{ phase_id, scenario_failures[], plan_requirements_violated[], evidence_of_misalignment, request: "verify implementation against plan" }` |
| Auditor | Auditor → Tester | `info_ready` | Audit findings on implementation-vs-plan alignment | `{ phase_id, verdict, findings[], plan_adherence_issues[] }` |
| Explorer | Tester → Explorer | `info_request` | DESIGN_INGESTION — needs public API specs or behavioral contracts | `{ design_file_path, api_contracts_needed[], behavioral_specs_needed[], why_needed }` |
| Researcher | Tester → Researcher | `info_request` | DESIGN_INGESTION — design references external library/pattern | `{ research_query, design_concept, why_needed_for_scenarios }` |
| Orchestrator | Tester ↔ Orchestrator | Various | Sub-agent spawn mediation (plan-checker, scenario-writer, test-writer), phase completion, escalation | Standard protocol |

**Structured request emphasis:** When the Tester messages Explorer, the request specifies what public API information is needed (function signatures, input/output types, error contracts) and why (which scenarios need this information). The request explicitly notes INV-1 — the Tester needs behavioral contracts, not implementation details.

**Rationale:** AGENT-ARCHITECTURE.md §3.6 (cross-domain: "Messages Coder directly for failure reports, escalates to Auditor"). The Tester's primary cross-domain relationships are with the Coder (failure reports every execution cycle) and the Auditor (escalation when failures suggest systemic issues beyond code bugs).

---

## 6. Auditor

**Role summary:** Session-persistent quality gatekeeper and arbitrator. The Auditor is event-driven — it sits in READY, receives requests (task audit, phase audit, project audit, arbitration, ad-hoc), processes them through a unified investigation cycle, and returns to READY. Dispatches audit-checker sub-agents for evidence gathering, synthesizes findings into verdicts and reports, and messages Coder/Tester with actionable fix instructions. Three audit scopes: task-level (lightweight, think-logged, no written review), phase-level (written review after scenario testing passes), and project-level (comprehensive multi-agent audit with final verdict: APPROVED / CHANGES_REQUIRED / REDESIGN_REQUIRED). Handles arbitration between Coder and Tester when they're stuck — hybrid approach where the Auditor self-investigates for simple disputes and dispatches audit-checker for complex ones. Accepts ad-hoc user requests with access to audit-checker, debugger, and optimizer sub-agents. Session-persistent with context pressure handoff protocol and user-initiated /clear support.

**Sources consulted:**
- `AGENT-ARCHITECTURE.md` §3.7 (Auditor definition, behavior, skills, cross-domain)
- `AGENT-ARCHITECTURE.md` §4.2 (audit-checker, debugger, optimizer sub-agent definitions)
- `AGENT-ARCHITECTURE.md` §5.1, §5.5 (dispatch rules, model selection)
- `AGENT-ARCHITECTURE.md` §2 (INV-4: adversarial audit independence)
- `BASE-DIRECTOR-PATTERN.md` §2-§7 (lifecycle phases, extension point API)
- `state-machines/auditor-task.json` (V1 Task Auditor — 6 states, 7 transitions)
- `state-machines/auditor-phase.json` (V1 Phase Auditor — 9 states, 12 transitions)
- `SUB-AGENT-SPECS.md` §4 (audit-checker), §8 (debugger), §9 (optimizer)
- `new_claude/skills/sub-agent-delegation/SKILL.md` (delegation lifecycle, dispatch modes)
- `new_claude/skills/plan-adherence/` (plan adherence checking patterns)

### V1 State Machine Alignment

The Auditor is unique among teammates: it has TWO V1 state machines (auditor-task.json v1.3.0, 6 states; auditor-phase.json v1.4.0, 9 states) that consolidate into ONE V2 state machine. V1 used separate ephemeral auditors — one per task (linear critique cycle) and one per phase (linear investigation flow). V2 replaces both with a single session-persistent teammate that handles all audit scopes through a unified event-driven investigation cycle.

**V2 Assessment: Structural Consolidation.** The V1 auditor behaviors are preserved — task-level critique, phase-level investigation with independent exploration and evidence-based rulings. The structural change is unification: one persistent Auditor replaces two ephemeral auditor types, gains arbitration and ad-hoc modes (no V1 equivalent), and delegates the mechanical code-reading work to audit-checker sub-agents. The V1 task auditor's critique-fix cycle (CRITIQUE_SENT ⇄ FIXES_VERIFIED) moves to the Coder's state machine — the Auditor delivers findings and returns to READY, the Coder handles fix-and-re-request.

**From auditor-task.json (V1):**

| V1 State | V2 State | Change |
|----------|----------|--------|
| `SPAWNED` | `SPAWNED` | **Shared.** One-time session initialization, not per-task. |
| `CODE_REVIEW` | `INVESTIGATION` | **Delegated.** V1: Auditor read code directly with think prompt. V2: audit-checker sub-agent reads code, returns structured findings. The V1 think prompt's 5 questions (plan match, edge cases, patterns, security, simplicity) become dimensions in the audit-checker's delegation prompt. Auditor stays lean. |
| `CRITIQUE_SENT` | `DELIVERY` | **Simplified.** V1: Auditor waited in CRITIQUE_SENT for Coder fixes. V2: Auditor delivers findings via message, returns to READY. No waiting state — Coder requests re-audit when ready. |
| `FIXES_VERIFIED` | Eliminated | **Moved to Coder.** The fix-verify cycle lives in the Coder's state machine (AUDIT_RESPONSE state). The Auditor treats a re-audit request as a new task audit with `previous_audit` context in the delegation prompt — same investigation cycle, fresh entry. |
| `APPROVED` | `DELIVERY` (APPROVED outcome) | **Folded.** Approval is a delivery outcome, not a separate state. |
| `ESCALATED` | `DELIVERY` (ESCALATED outcome) | **Folded.** Escalation is a delivery outcome routed to user. |

**From auditor-phase.json (V1):**

| V1 State | V2 State | Change |
|----------|----------|--------|
| `SPAWNED` | Shared | |
| `CONTEXT_LOADING` | `CONTEXT_LOADING` | **Preserved.** Now session-level (once on spawn), not per-phase. Phase-specific context is loaded during REQUEST_ANALYSIS. |
| `ARTIFACT_REVIEW` | `INVESTIGATION` | **Enhanced.** V1: Auditor reviewed artifacts directly. V2: dispatches N parallel audit-checkers (one per task) for systematic review. Auditor synthesizes their returns. |
| `INDEPENDENT_EXPLORATION` | `INVESTIGATION` (self-investigation pass) | **Preserved as behavior.** V1's independent exploration is the Auditor's self-investigation via PTC/Read within the INVESTIGATION ⇄ EVIDENCE_REVIEW loop. The Auditor can alternate between sub-agent dispatch and self-investigation across loop iterations. |
| `RULING` | `RULING` | **Preserved.** Same function: form evidence-based verdicts. V2 adds project-level verdict (APPROVED / CHANGES_REQUIRED / REDESIGN_REQUIRED). V1's INSUFFICIENT_EVIDENCE flagging and evidence citation requirements preserved in RULING think prompt. |
| `REPORT_WRITING` | `REPORT_WRITING` | **Preserved.** Phase and project audits produce written reviews. Task audits log via think MCP only — no written review. |
| `COMPLETE` | `DELIVERY` → `READY` | **Replaced.** V1 COMPLETE was terminal (ephemeral auditor terminated). V2: Auditor delivers and returns to READY for the next request. |
| `ERROR` | Part of `EVIDENCE_REVIEW` + `INVESTIGATION` retry | **Folded.** V1 ERROR with RETRY/HANDOFF/ABORT paths maps to the investigation loop's progress tracking and stall handling. If artifact loading fails, the Auditor can retry within INVESTIGATION or escalate via EVIDENCE_REVIEW. The V1 error recovery intelligence is preserved in the investigation loop's think prompts. |
| `HANDOFF` | `HANDOFF` | **Preserved.** Context pressure → write handoff, terminate. V2 adds user-initiated /clear with same handoff protocol. |
| `TERMINATED` | `TERMINATED` | **Preserved.** Only after unrecoverable failure. |

**States eliminated by consolidation:** 8 (CODE_REVIEW, CRITIQUE_SENT, FIXES_VERIFIED, APPROVED, ESCALATED, ARTIFACT_REVIEW, INDEPENDENT_EXPLORATION, COMPLETE — folded into unified cycle)

**States added:** 4 (READY, REQUEST_ANALYSIS, INVESTIGATION, EVIDENCE_REVIEW — the event-driven investigation cycle)

**Net state count:** V1 had 15 states across 2 machines (6 task + 9 phase). V2 has **11 states** in 1 machine.

**Why consolidation doesn't lose V1 nuance:** V1's task auditor critique cycle is preserved semantically — the Auditor still sends findings to Coder and receives re-audit requests, but the waiting (CRITIQUE_SENT) and verification (FIXES_VERIFIED) states move to the Coder where they belong. V1's phase auditor investigation flow is preserved — the INVESTIGATION loop supports both sub-agent dispatch (V1 ARTIFACT_REVIEW equivalent) and self-investigation via PTC (V1 INDEPENDENT_EXPLORATION equivalent), with the EVIDENCE_REVIEW loop replacing the linear pipeline with an iterative process that can go deeper when needed. V1's evidence-based ruling with INSUFFICIENT_EVIDENCE flagging and re-investigation (capped at 2 in V1) is generalized into the progress-tracked investigation loop (stall threshold 3).

### Three Audit Scopes

The Auditor handles three audit scopes with different depth, output, and verdict semantics:

| Scope | Trigger | Sub-Agents | Investigation Depth | Output | Verdict |
|-------|---------|------------|--------------------|---------|---------|
| **Task** | Coder completes green phase, requests audit | 1 audit-checker | 1 round typical | Think MCP log. No written review. Findings messaged to Coder. | APPROVED / CRITIQUE / ESCALATED |
| **Phase** | Orchestrator pings after scenario testing passes (or directly if no scenarios) | N parallel audit-checkers (one per task) + self-investigation | Multiple rounds | **Written review** at `.claude/reports/` | Assessment in review |
| **Project** | Orchestrator pings after all phases complete + full scenario testing passes | N × M audit-checkers (per phase × per task) + cross-phase integration checkers | Extensive, multi-round | **Full report** + verdict at `.claude/reports/` | APPROVED / CHANGES_REQUIRED / REDESIGN_REQUIRED |

**Task audit is lightweight by design.** The per-task audit is a fast feedback loop — the Coder needs APPROVED or CRITIQUE quickly to continue working. Findings are logged via think MCP for auditability and messaged to the Coder with file:line references. No written report, no user involvement unless ESCALATED.

**Phase audit is comprehensive.** After all tasks pass and scenario testing passes, the Auditor dispatches parallel audit-checkers, self-investigates cross-task integration, and writes a review document the user can read. This is the V1 phase auditor's flow, enhanced with sub-agent delegation and iterative investigation.

**Project audit is the final gate.** The highest-stakes judgment in the workflow. The Auditor dispatches audit-checkers across all phases and tasks, synthesizes findings into a comprehensive report, and delivers one of three verdicts:
- **APPROVED** — Implementation satisfies the design. Ready to ship.
- **CHANGES_REQUIRED** — Specific issues must be addressed. Report includes file:line references and priority for each required change.
- **REDESIGN_REQUIRED** — Fundamental misalignment with design intent. The implementation has deviated from the original goal, or tests are validating the wrong behaviors. This is a judgment call that requires the Auditor's Opus-level reasoning — the verdict quality comes from the Auditor's synthesis of all evidence, not from the Sonnet sub-agents' individual findings.

**Audit scope flow within the workflow:**

```
Per task:
    Coder green phase ──► task audit request ──► 1 audit-checker
    ──► findings to Coder ──► (Coder fixes if CRITIQUE, re-requests)

Per phase:
    All tasks done ──► scenario testing ──► scenarios pass
    ──► phase audit request ──► N parallel audit-checkers
    ──► written review to user

    (If no scenario tests for this phase:
    All tasks done ──► phase audit directly)

Project-level:
    All phases done ──► full scenario testing passes
    ──► project audit request ──► comprehensive multi-agent audit
    ──► full report + verdict (APPROVED | CHANGES_REQUIRED | REDESIGN_REQUIRED)
```

### Unified Investigation Cycle

The core of the V2 Auditor. All request types (task audit, phase audit, project audit, arbitration, ad-hoc) flow through the same investigation cycle with different parameters.

**Cycle:**

```
REQUEST_ANALYSIS → INVESTIGATION ⇄ EVIDENCE_REVIEW → RULING → [REPORT_WRITING] → DELIVERY
```

**INVESTIGATION ⇄ EVIDENCE_REVIEW** is the progress loop. Each iteration, the Auditor can:

1. **Dispatch audit-checker(s)** — primary investigation tool for all audit modes
2. **Dispatch optimizer** — for optimization-focused ad-hoc audits (user-initiated only)
3. **Dispatch debugger** — for root cause investigation (user-initiated ad-hoc only)
4. **Self-investigate via PTC** — targeted code queries, test result parsing, specific file checks
5. **Read files directly** — when PTC overhead isn't justified for a quick check
6. **Message Explorer/Researcher** — for codebase context or external API info

After each investigation pass, EVIDENCE_REVIEW assesses: is the evidence sufficient to form a ruling? If yes → RULING. If no → back to INVESTIGATION with a refined strategy. Progress-tracked with stall threshold of 3 consecutive passes with no new evidence.

**Why unified, not per-mode branches:** Every mode follows the same structural pattern — analyze request, investigate, review evidence, rule, deliver. The differences are parameterized by the request type, not encoded in separate state paths:

| Parameter | Task Audit | Phase Audit | Project Audit | Arbitration | Ad-Hoc |
|-----------|-----------|-------------|---------------|-------------|--------|
| Sub-agents dispatched | 1 audit-checker | N parallel audit-checkers | N × M audit-checkers | 0–1 audit-checker (hybrid) | audit-checker, debugger, optimizer (as needed) |
| Investigation depth | 1 round typical | Multiple rounds | Extensive | Varies by complexity | Varies by request |
| RULING output | Think MCP log | Written review | Written report + verdict | Ruling message | User-facing answer or report |
| DELIVERY target | Coder | User + orchestrator | User + orchestrator | Coder + Tester | User |

### Arbitration — Hybrid Investigation

Arbitration follows the same investigation cycle but with mode-specific behavior at each stage. The hybrid approach: Auditor self-investigates for simple disputes, dispatches audit-checker for complex ones, and can iterate between self-investigation and sub-agent dispatch.

**REQUEST_ANALYSIS:** Read the dispute context — Coder's claim, Tester's claim, the disputed code/tests, the plan requirement in question. Determine whether this is resolvable from available context or needs deeper investigation.

**INVESTIGATION (first pass — Auditor self-investigates):**
- Read the disputed area via PTC — the specific files, the specific test assertions
- Compare against the plan/design document
- Check if one side is clearly correct based on the design intent

**EVIDENCE_REVIEW decision:**
- If the dispute is clear (one side doesn't match the design) → SUFFICIENT → RULING directly
- If ambiguous (both sides have valid points, or the evidence is incomplete) → MORE_NEEDED → dispatch audit-checker for targeted investigation with specific questions about the disputed area, loop back

**INVESTIGATION (subsequent passes — if needed):**
- Audit-checker returns → Auditor reviews findings
- Auditor may self-investigate further via PTC to verify specific sub-agent claims
- Auditor may dispatch another audit-checker with refined questions
- Loop continues until EVIDENCE_REVIEW declares SUFFICIENT

**RULING for arbitration:**
- Root cause analysis: what is the actual problem?
- Determine which side needs to change and why
- Severity check: function/module-level fix → Auditor resolves autonomously (messages both parties with instructions). Task-level rewrite → requires user approval (escalate with findings).

**DELIVERY for arbitration:**
- Message Coder with: what needs to change in the implementation, why, file:line references
- Message Tester with: confirmation that tests are correct (or what needs to change in scenarios, why)
- If user approval required: present findings to user in tmux pane

**The Auditor is not adversarial during arbitration.** INV-4 (adversarial audit independence) applies to audit-checker sub-agents — they are critical and granular about code quality. The Auditor during arbitration is diagnostic: "what is the actual problem and what solves it?" — root-cause analyst, not blame assigner.

### Ad-Hoc Mode — Flexible Entry

The user can request anything via the Auditor's tmux pane. REQUEST_ANALYSIS determines the response strategy:

| User Request Type | Sub-Agents Dispatched | Example |
|---|---|---|
| "Audit module X" | audit-checker | Targeted audit of specific area |
| "Why is this failing?" | debugger | Root cause investigation |
| "Is this performant?" / "Audit performance" | audit-checker + optimizer | Static analysis + prototype improvements |
| "Does this match the design?" | audit-checker | Plan adherence check |
| "Quick question about X" | None (self-execute via PTC) | Simple lookup |

For combined dispatches (e.g., audit-checker + optimizer for performance audit), the Auditor dispatches both in the same INVESTIGATION pass. Both sub-agents work in parallel — audit-checker reads code and identifies hotspots/issues, optimizer prototypes improvements in an ephemeral worktree. The Auditor synthesizes both returns in EVIDENCE_REVIEW: "here's what's slow (audit-checker's analysis), here's what an optimization pass achieved (optimizer's branch with benchmarks), here's whether it's worth merging." The optimizer's branch is left intact for the Coder or orchestrator to merge if the user decides it's worthwhile.

**Debugger and optimizer are ad-hoc only.** In the normal workflow (task/phase/project audit, arbitration), the Auditor dispatches only audit-checker. Debugger and optimizer are available because the user may ask the Auditor to investigate or optimize anything — the Auditor needs the tools to fulfill ad-hoc requests. The REQUEST_ANALYSIS think prompt enforces this: Q4 explicitly asks which sub-agents are needed, and the behavioral instruction restricts debugger/optimizer to ad-hoc mode.

### V2 State Machine

```
SPAWNED
    │
    ▼
CONTEXT_LOADING
    │  load: design doc, plan, project conventions,
    │  existing audit reports (if respawn), context packets
    │
    ▼
READY  ◄──────────────────────────────────────────────────────────┐
    │  (event loop — waits for requests)                          │
    │                                                             │
    │  receives: task_audit | phase_audit | project_audit         │
    │            | arbitration | ad_hoc                            │
    │                                                             │
    ▼                                                             │
REQUEST_ANALYSIS                                                  │
    │  [think on entry — daemon critical annotation]              │
    │  determine: mode, scope, investigation strategy,            │
    │  sub-agent dispatch plan                                    │
    │                                                             │
    │  ┌─ DIRECT_ANSWER (simple ad-hoc) ──────────► DELIVERY ─────┤
    │  └─ INVESTIGATE ▼                                           │
    │                                                             │
    ▼                                                             │
INVESTIGATION  ◄──────────────────────┐                           │
    │  Actions (one or more per pass): │                           │
    │  • dispatch audit-checker(s)     │                           │
    │  • dispatch optimizer (ad-hoc)   │                           │
    │  • dispatch debugger (ad-hoc)    │                           │
    │  • self-investigate via PTC      │                           │
    │  • read specific files directly  │                           │
    │  • message Explorer/Researcher   │                           │
    │                                  │                           │
    ▼                                  │                           │
EVIDENCE_REVIEW                        │                           │
    │  [think on entry — daemon        │                           │
    │   critical annotation]           │                           │
    │                                  │                           │
    │  ┌─ MORE_NEEDED ─────────────────┘                           │
    │  │  (progress-tracked,                                      │
    │  │   stall threshold 3)                                     │
    │  └─ SUFFICIENT ▼                                            │
    │                                                             │
    ▼                                                             │
RULING                                                            │
    │  [think on entry — daemon critical annotation]              │
    │                                                             │
    │  ┌─ task audit ──► log via think MCP ──────► DELIVERY ──────┤
    │  ├─ phase audit ──────────────► REPORT_WRITING              │
    │  ├─ project audit ────────────► REPORT_WRITING              │
    │  ├─ arbitration ──────────────────────────► DELIVERY ──────┤
    │  └─ ad-hoc ──► DELIVERY or REPORT_WRITING (depends on      │
    │                 complexity)                                  │
    │                                                             │
    ▼ (when report needed)                                        │
REPORT_WRITING                                                    │
    │  write to .claude/reports/**                                │
    │  project audit: include verdict                             │
    │  (APPROVED | CHANGES_REQUIRED | REDESIGN_REQUIRED)          │
    │                                                             │
    ▼                                                             │
DELIVERY                                                          │
    │  route by request type:                                     │
    │  • task: message Coder (APPROVED / CRITIQUE / ESCALATED)    │
    │  • phase: report path to user + orchestrator                │
    │  • project: report + verdict to user + orchestrator         │
    │  • arbitration: message Coder + Tester with resolution      │
    │  • ad-hoc: present to user                                  │
    │                                                             │
    └─────────────────────────────────────────────────────────────┘

HANDOFF  (context pressure or user /clear — write handoff, terminate)
TERMINATED  (unrecoverable failure only)
```

**State count:** 11 (SPAWNED, CONTEXT_LOADING, READY, REQUEST_ANALYSIS, INVESTIGATION, EVIDENCE_REVIEW, RULING, REPORT_WRITING, DELIVERY, HANDOFF, TERMINATED)

### Deliberation Model

All think prompts fire **on state entry** as daemon critical annotations. The Auditor has 3 think-on-entry states (REQUEST_ANALYSIS, EVIDENCE_REVIEW, RULING) — fewer than other teammates because the Auditor's investigation cycle is iterative (EVIDENCE_REVIEW fires multiple times per request) rather than having many distinct think-gated states.

**States with think-on-entry:**

| State | Think Trigger | What It Decides |
|-------|--------------|-----------------|
| REQUEST_ANALYSIS | On entry (each new request from READY) | Mode, scope, investigation strategy, sub-agent dispatch plan |
| EVIDENCE_REVIEW | On entry (each investigation pass) | Evidence sufficiency, gaps, progress assessment, next investigation action |
| RULING | On entry (after evidence sufficient) | Verdict, severity classification, routing decision, report contents |

**States WITHOUT think prompts:**
- SPAWNED, CONTEXT_LOADING — mechanical initialization
- READY — waiting state
- INVESTIGATION — sub-agent dispatch / self-investigation. The delegation prompt and investigation action ARE the deliberation for this state.
- REPORT_WRITING — mechanical writing based on RULING decisions
- DELIVERY — mechanical message routing based on RULING decisions
- HANDOFF — mechanical handoff writing

**Think prompts:**

**REQUEST_ANALYSIS** (on entry):
```
Q1: What type of request is this? (task_audit | phase_audit |
    project_audit | arbitration | ad_hoc)
Q2: What is the scope? For audit: which task/phase, what files,
    what plan requirements. For arbitration: what is the dispute,
    who are the parties, what does each claim. For ad-hoc: what is
    the user asking.
Q3: What evidence do I need to form a ruling? List specific artifacts,
    files, code areas, and audit dimensions to investigate.
Q4: Which sub-agents should I dispatch? How many? In parallel?
    For task audit: 1 audit-checker with task scope.
    For phase audit: N parallel audit-checkers (one per task) with
    phase scope (integration dimension enabled).
    For project audit: per-phase dispatch strategy, cross-phase
    integration checkers.
    For arbitration: assess whether self-investigation is sufficient
    first — dispatch audit-checker only if deeper evidence needed.
    For ad-hoc: depends on request type. debugger and optimizer
    available for user-initiated investigation/optimization requests.
Q5: Can I answer this directly without sub-agent dispatch?
    (Simple ad-hoc questions, PTC lookup sufficient)
Q6: Is there prior audit context for this scope? Previous task audit
    findings that inform this phase audit? Prior arbitration rulings?
    Pattern observations from earlier audits in this session?
CHOSEN: INVESTIGATE | DIRECT_ANSWER
```

**EVIDENCE_REVIEW** (on entry — fires each investigation pass):
```
Q1: What did this investigation pass produce? Summarize: sub-agent
    findings (audit-checker verdicts, optimizer benchmarks, debugger
    root causes), self-investigation results (PTC queries, file reads),
    or cross-domain responses (Explorer context, Researcher findings).
Q2: Do the findings answer the original request? Map evidence to the
    questions identified in REQUEST_ANALYSIS Q3. Which questions are
    answered? Which remain open?
Q3: Are there evidence gaps? Specific dimensions not yet covered,
    ambiguous findings that need clarification, areas where sub-agent
    findings conflict or are incomplete.
Q4: Am I making progress? Compare this pass to the previous: new
    evidence found, gaps narrowed, or spinning on the same questions
    with no new information?
Q5: If more investigation is needed: what specific action would close
    the gap? Options: dispatch another audit-checker with refined
    questions, self-investigate a specific area via PTC, request
    context from Explorer, read a specific file directly.
CHOSEN: SUFFICIENT | MORE_NEEDED
```

**RULING** (on entry):
```
Q1: Based on all evidence gathered, what is my verdict?
    For task audit: APPROVED (no major/critical findings) |
      CRITIQUE (major findings, specific fixes needed) |
      ESCALATED (critical findings, user decision needed)
    For phase audit: overall quality assessment, plan adherence,
      test coverage, cross-task integration findings.
    For project audit: APPROVED | CHANGES_REQUIRED |
      REDESIGN_REQUIRED. This is the highest-stakes judgment —
      REDESIGN_REQUIRED means the implementation fundamentally
      deviates from design intent or tests validate wrong behaviors.
    For arbitration: which side needs to change and why? Is this
      function/module-level (autonomous resolution) or task-level
      (requires user approval)?
Q2: Is every finding backed by specific evidence (file:line)?
    Flag any claims without concrete citations — these are
    INSUFFICIENT_EVIDENCE and must be explicitly noted.
Q3: Severity classification for each finding:
    minor | moderate | major | critical.
    Apply INV-4 — be adversarial and granular about quality.
    The highest standard is the only standard.
Q4: Are there patterns across audits in this session?
    (Session-persistent context advantage — flag recurring issues
    that individual task audits surfaced independently.)
Q5: For phase/project audit: what goes in the written review?
    Structure: executive summary, per-task/phase findings,
    cross-cutting issues, verdict with rationale.
Q6: For project audit specifically: does the implementation satisfy
    the design? Not "does it work" but "does it do what the design
    intended." If CHANGES_REQUIRED, list each change with priority.
    If REDESIGN_REQUIRED, articulate what fundamentally doesn't match.
CHOSEN: task → THINK_LOG | phase/project/ad-hoc → REPORT |
        arbitration → MESSAGE
```

### Extension Points

#### `INGRESS_CONTEXT_LOADING`

**What the Auditor loads on spawn (session start):**
1. The design document — the authoritative reference for all audit judgments
2. The implementation plan — phase structure, task decomposition, acceptance criteria
3. Project conventions — coding standards, patterns, naming conventions (from context packets or project config)
4. Existing audit reports from `.claude/reports/` — if this is a respawn after HANDOFF or user /clear
5. Context packets in `.claude/context/` — codebase structure, module interfaces
6. If replacement spawn: handoff summary from `.claude/handoffs/` with prior audit state and cross-audit observations

**What it does NOT load:**
- Source code or test code (that's audit-checker's job — keeps the Auditor lean)
- Other teammates' internal state (the Auditor works from structured messages and sub-agent reports)
- Session history beyond audit artifacts (the Auditor's context is its own audit trail)

**Rationale:** The Auditor needs design and plan to form judgments, but does NOT need to read source code itself for standard audit operations. Audit-checkers read code and return structured findings. The Auditor's context stays lean — design + plan + audit reports — allowing it to persist across the full session without context pressure from accumulated source code reading. When the Auditor does need to check specific code (arbitration self-investigation, ad-hoc PTC queries), it uses targeted reads that don't accumulate.

#### `DELIBERATION_THINK_PROMPT` / `DOMAIN_THINK_QUESTIONS`

The Auditor's deliberation is distributed across its investigation cycle — REQUEST_ANALYSIS, EVIDENCE_REVIEW, and RULING each have their own think prompt that fires on state entry as a daemon critical annotation. The base pattern's DELIBERATION state is not used as a separate state; instead, REQUEST_ANALYSIS serves as the entry-point deliberation for each request. EVIDENCE_REVIEW provides iterative deliberation within the investigation loop. See Deliberation Model section above for all 3 think prompts.

#### `AUTHORIZED_SUB_AGENTS`

| Sub-Agent Type | Model | Dispatched When | Mode Restriction | Notes |
|---|---|---|---|---|
| `audit-checker` | Sonnet (fixed) | INVESTIGATION — all modes | None (all modes) | Primary investigation tool. Read-only (no Write/Edit). Returns structured findings with verdict + file:line references. INV-1 compliant: reads BOTH source and tests, writes NEITHER. |
| `debugger` | Sonnet (fixed) | INVESTIGATION — ad-hoc only | Ad-hoc (user-initiated) | Root cause investigation and fix. Dispatched when user asks "why is this broken?" Not used in workflow audit/arbitration — in the normal workflow, debugger is dispatched by Coder. |
| `optimizer` | Sonnet (fixed) | INVESTIGATION — ad-hoc only | Ad-hoc (user-initiated) | Optimization analysis in ephemeral worktree. Dispatched when user asks about performance. Branch left for Coder/orchestrator to merge if worthwhile. Not used in workflow audit. |

 | |

**Dispatch authorization rationale:** Audit-checker is the Auditor's core sub-agent — every audit request dispatches at least one. Debugger and optimizer are available for ad-hoc flexibility because the user may ask the Auditor to investigate or optimize anything. In the normal workflow, debugger is dispatched by Coder (for code fixes) and optimizer is dispatched by Coder (post-green optimization). The Auditor's access to debugger and optimizer is for user-initiated requests only — the REQUEST_ANALYSIS think prompt (Q4) enforces this behaviorally.

#### `DELEGATION_COMPOSITION`

**For audit-checker (task audit — INVESTIGATION):**
1. **Design document** — the behavioral requirements checklist source
2. **Plan chunk** — task scope, acceptance criteria, expected behavior
3. **Source files** — implementation to audit (file paths from Coder's audit request)
4. **Test files** — test code to verify coverage (from same worktree)
5. **Audit scope** — `"task"` (integration dimension not applicable)
6. **Previous audit** (if re-audit after fixes) — prior findings to verify resolution, Coder's `fixes_applied[]` list
7. **Return instruction:** audit 6 dimensions (plan adherence, design coherence, test coverage, code quality, style conformance — integration not applicable for task scope), classify findings by severity, return structured JSON with verdict

**For audit-checker (phase audit — INVESTIGATION, N parallel):**
1. **Design document** — full design
2. **Implementation plan** — full plan with all phase tasks
3. **Source files per task** — each checker gets its assigned task's files
4. **Test files per task** — each checker gets its assigned task's tests
5. **Audit scope** — `"phase"` (integration dimension enabled — cross-task wiring)
6. **Cross-task interface context** — other tasks' public interfaces (for integration checking by each checker)
7. **Return instruction:** audit all 6 dimensions including integration. Flag cross-task issues: exports consumed correctly, APIs connected, data contracts matched between tasks

**For audit-checker (project audit — INVESTIGATION, comprehensive):**
1. **Design document** — full design
2. **Full implementation plan** — all phases, all tasks
3. **All source files per assigned scope** — each checker audits a phase or cross-phase dimension
4. **All test files per assigned scope** — unit + integration + scenario
5. **Scenario test results** — all scenario reports from Tester across phases
6. **Prior phase audit reports** — findings from each phase audit (the Auditor's accumulated audit trail)
7. **Audit scope** — `"project"` (full integration + cross-phase coherence)
8. **Return instruction:** comprehensive audit of assigned scope, flag cross-phase issues, assess design adherence at the project level

**For audit-checker (arbitration — INVESTIGATION, when dispatched):**
1. **Dispute context** — Coder's claim, Tester's claim, the specific disagreement
2. **Design document sections** — the requirements relevant to the dispute
3. **Plan chunk** — the task in question
4. **Source files** — the disputed implementation
5. **Test files** — the disputed tests
6. **Specific questions** (as `unknowns[]`) — what the Auditor needs investigated (targeted)
7. **Return instruction:** verify plan adherence of disputed area, determine which side aligns with design intent, provide file:line evidence for conclusions

**For debugger (ad-hoc only — INVESTIGATION):**
1. **User's question** — what they want investigated
2. **Error output or symptoms** — what's failing and how
3. **Source files involved** — affected code area
4. **Return instruction:** diagnose root cause, apply fix if appropriate, run tests, return with root_cause analysis and fix_applied details

**For optimizer (ad-hoc only — INVESTIGATION):**
1. **User's request** — what to optimize and why
2. **Source files** — performance-sensitive code
3. **Benchmark context** — current performance characteristics if known
4. **Return instruction:** analyze optimization opportunities, prototype improvements in ephemeral worktree, benchmark before/after, return comparison with branch name for potential merge

**Context NOT included in any delegation:**
- Other teammates' internal state or conversation history
- Session-level orchestrator state
- The Auditor's own prior rulings (the Auditor synthesizes across audits at the RULING stage, not the sub-agents — sub-agents see only their assigned scope)

#### `MODEL_SELECTION_HEURISTIC`

**Not applicable.** All Auditor sub-agents are Sonnet (fixed). The Auditor itself runs on Opus. No model selection decisions.

**Rationale:** The judgment quality comes from the Auditor's Opus-level synthesis in RULING, not from the sub-agents' evidence gathering. Sonnet is sufficient for systematically reading code and identifying issues — audit-checker follows a structured methodology (design → plan → implementation → tests → quality → style) that doesn't require Opus reasoning. The Opus value is applied at the synthesis and verdict layer where it matters most: cross-audit pattern recognition, severity classification, and the project-level APPROVED / CHANGES_REQUIRED / REDESIGN_REQUIRED verdict.

#### `SYNTHESIS_OUTPUT_TYPE`

**Produces per task audit:**
- Think MCP log entry with findings, verdict, and severity classifications (for auditability/observability)
- Structured message to Coder with verdict and actionable findings (file:line references, fix instructions)

**Produces per phase audit:**
- **Phase audit review** at `.claude/reports/{feature}-phase-{N}-audit-review.json`:
  - Executive summary: overall phase quality assessment
  - Per-task findings: aggregated from audit-checker returns, with Auditor's severity reclassifications and cross-audit observations
  - Cross-task integration findings: issues between tasks that individual task audits missed
  - Plan adherence assessment: how well the phase implementation matches the plan
  - Test coverage assessment: adequacy of unit tests and scenario coverage for this phase
  - Recommendations: prioritized list of improvements
- Structured notification to orchestrator + user with report path

**Produces per project audit:**
- **Project audit report** at `.claude/reports/{feature}-project-audit-report.json`:
  - Full design adherence assessment across all phases
  - Cross-phase integration analysis: do phases compose correctly?
  - Overall code quality and convention adherence
  - Test coverage completeness (unit + scenario across all phases)
  - Pattern analysis: recurring issues across phases/tasks
  - **Verdict: APPROVED | CHANGES_REQUIRED | REDESIGN_REQUIRED**
  - If CHANGES_REQUIRED: specific changes needed with file:line references, priority, and affected phase/task
  - If REDESIGN_REQUIRED: what fundamentally doesn't match the design intent, why, and what the gap is between implementation and design
- Structured notification to orchestrator + user with verdict and report path

**Produces per arbitration:**
- Ruling message to Coder: what needs to change in the implementation, why, file:line references
- Ruling message to Tester: confirmation that tests are correct (or what needs to change in scenarios, why)
- If user approval required (task-level rewrite): findings presented to user in tmux pane

**Produces per ad-hoc:**
- Direct answer to user (simple queries — DIRECT_ANSWER path)
- Report at `.claude/reports/` (complex investigations — written review of findings)
- For optimizer+audit-checker combined: synthesis of both analyses with the optimizer's branch reference for potential merge

#### `DOMAIN_VERIFICATION`

Verification is multi-layered:

1. **Sub-agent level:** Audit-checker returns structured JSON validated by SubagentStop hook (9 validation rules from SUB-AGENT-SPECS.md §4.4). Verdict-findings consistency enforced mechanically (CRITIQUE requires ≥1 major finding, APPROVED requires no major/critical findings).
2. **Auditor level (EVIDENCE_REVIEW):** Auditor verifies sub-agent findings are evidence-backed. Flags any findings without file:line citations. Assesses consistency across parallel checkers (do multiple checkers flag the same cross-task issue? do individual findings contradict?).
3. **Auditor level (RULING):** Cross-referencing findings against design document. The Auditor applies INV-4 (adversarial, granular, critical) at the synthesis level — ensuring the final verdict reflects the full picture, not just individual checker reports. INSUFFICIENT_EVIDENCE claims explicitly flagged (preserved from V1 phase auditor's RULING think prompt).
4. **Report level (REPORT_WRITING):** Written review must include file:line citations for every claim. Any findings without concrete citations must be flagged as INSUFFICIENT_EVIDENCE so the user can see gaps.

#### `OUTPUT_WRITE_GLOBS`

```
.claude/reports/**
.claude/logs/*-audit*.json
.claude/handoffs/**
```

The Auditor writes reports, audit logs, and handoffs. It NEVER writes source code or test code (INV-3: controller never implements, INV-4: audit-checker has no Write/Edit). Debugger and optimizer sub-agents write in their own worktrees when dispatched for ad-hoc requests — those changes are managed by the sub-agents, not the Auditor.

#### `POST_ACTIONS`

The Auditor has no file-write post-actions on its own writes (reports and logs don't need linting or schema validation hooks — the structured JSON format is enforced by the Auditor's own RULING think prompt and the report writing logic). Sub-agents have their own post-actions where applicable (debugger and optimizer have `ruff_lint_critical` on Write/Edit; audit-checker has no writes and therefore no post-actions).

#### `PERSISTENCE_MODEL`

**Session-persistent.** The Auditor persists across the full session — through all task audits, phase audits, arbitrations, and ad-hoc requests. This is the longest persistence of any teammate (shared with the Orchestrator).

**Why session-persistent:** The Auditor accumulates cross-audit context that directly improves verdict quality:
- Patterns that emerge across multiple task audits ("this module keeps having missing validation") inform later rulings and the phase audit
- Arbitration rulings reference prior audit findings for consistency
- The project-level final audit synthesizes all prior phase audits — the Auditor has seen the full progression
- Ad-hoc user requests benefit from the Auditor's accumulated understanding of the codebase's quality profile

**Context pressure handling:** Despite session persistence, the Auditor may hit context pressure during long sessions with many phases. The handoff protocol applies:
1. Write handoff summary to `.claude/handoffs/` with: current investigation state (if mid-request), cross-audit observations, pending requests, pattern notes
2. The user can also trigger this manually via /clear when they want to free up context
3. A replacement Auditor loads: design doc, plan, all prior audit reports from `.claude/reports/` (already on disk), and the handoff summary
4. Prior audit reports on disk serve as the Auditor's institutional memory — they externalize knowledge that would otherwise be lost on context reset

Written reports are the key to handoff resilience. Every phase audit and project audit produces a durable artifact. Task audit findings are logged via think MCP (daemon-side persistence). A replacement Auditor reads these artifacts and reconstructs the audit history without needing the original session's context.

#### `DOMAIN_STATES`

The Auditor's domain IS its event-driven investigation cycle. The base pattern's linear lifecycle (CONTEXT_LOADING → DELIBERATION → SUB_AGENT_DISPATCH → SYNTHESIS → DELIVERY) is replaced by the event loop with a unified investigation cycle. The base pattern's extension mechanism used here is **OVERRIDE** (same as Coder and Tester).

**Event loop (replaces base linear lifecycle):**
- **READY** — event loop hub, no base pattern equivalent. The Auditor's resting state between requests.
- **REQUEST_ANALYSIS** — replaces DELIBERATION, but recurring (fires for each request, not once per session)
- **INVESTIGATION** — replaces SUB_AGENT_DISPATCH, but iterative (may dispatch multiple rounds of sub-agents, interspersed with self-investigation)
- **EVIDENCE_REVIEW** — no base pattern equivalent. Assessment gate in the investigation loop.
- **RULING** — replaces SYNTHESIS for the judgment layer. The Auditor's core intellectual contribution.
- **REPORT_WRITING** — domain-specific, only for phase/project audits and complex ad-hoc
- **DELIVERY** — replaces base DELIVERY, but returns to READY (not terminal)

Base orthogonal states (HANDOFF, TERMINATED) still apply.

#### `DOMAIN_ERROR_PATHS`

| Failure | Recovery Path | Rationale |
|---|---|---|
| Audit-checker crash/timeout | Re-dispatch once (fresh checker, same scope and delegation prompt). If retry fails → skip that checker's scope, note gap in EVIDENCE_REVIEW, proceed with available evidence. | One retry for transient failures. Don't block entire phase/project audit for one failed checker. |
| Audit-checker returns `partial` (context pressure) | Accept partial findings. EVIDENCE_REVIEW decides whether gaps need another dispatch or are acceptable for ruling. | Context pressure in sub-agent. Partial evidence is better than none — the Auditor can dispatch a fresh checker for remaining dimensions. |
| All audit-checkers return APPROVED but Auditor's self-investigation finds issues | Auditor's findings override sub-agent verdicts. The Auditor is the authority, not the checkers. | The Auditor applies judgment; checkers provide evidence. The Auditor can see cross-audit patterns and context that individual checkers cannot. |
| Arbitration dispute unresolvable (design is genuinely ambiguous) | Escalate to user: "The design is ambiguous about X. Both implementation and tests are defensible interpretations. User must clarify design intent." | The Auditor arbitrates code vs design, not ambiguous design vs ambiguous design. Design ambiguity is the user's call. |
| Investigation stall (3 consecutive passes with no new evidence) | Force RULING with available evidence. Flag gaps explicitly — INSUFFICIENT_EVIDENCE findings in report/message. | Progress-based — if 3 rounds produce nothing new, more investigation won't help. Better to deliver an honest ruling with flagged gaps than to loop indefinitely. |
| Context pressure during phase/project audit | HANDOFF with audit state. Written reports for completed scopes are already on disk. Replacement Auditor continues from where this one stopped, loading prior reports. | Session persistence means the Auditor may accumulate a lot of context over many audits. Handoff preserves the external artifact trail. |
| Debugger/optimizer crash during ad-hoc | Report to user: "investigation of X failed due to [reason]. Here's what I found from audit-checker." Fall back to available evidence. | Ad-hoc requests should degrade gracefully, not block. The user can retry or redirect. |

#### `SELF_EXECUTE_SCOPE`

**Self-executable (Auditor does directly):**
- **All ruling and verdict decisions** — severity classification, plan adherence judgment, arbitration rulings, project-level verdicts. This is the Auditor's core competency — Opus-level reasoning applied to synthesized evidence.
- **Arbitration initial assessment** — reading dispute messages, comparing claims against plan/design via PTC, forming preliminary judgment before deciding whether sub-agent investigation is needed
- **PTC targeted queries** — reading specific code sections, checking test results, verifying file existence, parsing quality gate output. Quick lookups where the Auditor knows exactly what to look for.
- **Simple ad-hoc answers** — user asks "does module X follow our naming convention?" → PTC lookup → answer. No sub-agent needed.
- **Reading sub-agent returns** — parsing audit-checker findings, optimizer reports, debugger analysis. The structured JSON format makes this mechanical.
- **Cross-audit pattern recognition** — "this is the third time this module has missing validation" — leveraging session-persistent context
- **Writing reports, logs, handoffs** — the Auditor's primary write outputs
- **Sending structured messages** — to Coder (findings), Tester (test guidance), Explorer (context request), Researcher (API info), orchestrator (phase/project completion)

**NOT self-executable (delegates or requests cross-domain):**
- **Systematic code reading across multiple files** → audit-checker sub-agent (keeps Auditor context lean — the checker reads hundreds of lines so the Auditor doesn't have to)
- **Root cause debugging** → debugger sub-agent (ad-hoc only)
- **Optimization prototyping** → optimizer sub-agent (ad-hoc only)
- **Broad codebase exploration** → messages Explorer when context packets don't contain needed information
- **External API/library research** → messages Researcher when audit involves external conventions or standards

**The boundary:** The Auditor does everything that requires judgment, synthesis, and cross-audit reasoning. It delegates systematic evidence gathering (audit-checker reads code so the Auditor doesn't accumulate source in its context). The delegation cost heuristic: if investigating requires reading more than ~50 lines of source across multiple files → delegate to audit-checker. If it's a targeted PTC query (check this function's return type, read this test's assertion, verify this import exists) → self-execute.

#### `CROSS_DOMAIN_PARTNERS`

| Partner | Direction | Message Type | When | Request Schema |
|---|---|---|---|---|
| Coder | Auditor → Coder | `info_ready` | DELIVERY — task audit findings | `{ task_id, verdict: "APPROVED"|"CRITIQUE"|"ESCALATED", findings[], severity_summary, fix_instructions[] }` |
| Coder | Coder → Auditor | `info_request` | Coder requests task audit after green phase | `{ task_id, worktree_branch, plan_chunk_ref, test_results_summary }` |
| Coder | Coder → Auditor | `info_request` | Coder requests re-audit after fixes applied | `{ task_id, fixes_applied[], previous_audit_ref }` |
| Tester | Auditor → Tester | `info_ready` | DELIVERY — arbitration ruling affecting tests | `{ phase_id, ruling, test_changes_needed[], rationale }` |
| Tester | Tester → Auditor | `info_request` | Tester STALLED ≥ threshold, suspects plan misalignment | `{ phase_id, scenario_failures[], plan_requirements_violated[], evidence_of_misalignment }` |
| Explorer | Auditor → Explorer | `info_request` | INVESTIGATION — needs codebase context not in packets | `{ query, codebase_area, why_needed }` |
| Researcher | Auditor → Researcher | `info_request` | INVESTIGATION — needs external API/convention info | `{ research_query, why_needed }` |
| Orchestrator | Auditor ↔ Orchestrator | Various | Sub-agent spawn mediation (audit-checker, debugger, optimizer), phase/project audit triggers, escalation, project verdict delivery | Standard protocol |
| User | Auditor → User | Direct | DELIVERY — phase/project reports, arbitration escalation (task-level rewrites needing approval), ad-hoc answers | In tmux pane |

**Primary relationships:** The Auditor's most active cross-domain relationship is with the Coder — every task audit results in a message to the Coder, and the Coder may re-request audit after fixes. The Tester relationship activates during persistent scenario failures (Tester → Auditor escalation) and arbitration (Auditor → both Coder and Tester). The user is the Auditor's primary audience for phase/project reports and the authority for escalated findings and project-level verdicts.

**Rationale:** AGENT-ARCHITECTURE.md §3.7 (cross-domain: "Messages Coder for code fixes, Tester for test fixes, Explorer for codebase context"). The Auditor is the quality authority — its messages carry the weight of independent adversarial review (via audit-checker sub-agents, INV-4) and synthesized judgment (via Opus-level RULING).
