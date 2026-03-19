---
name: codebase-exploration
version: 1-0-0
description: >
  Use when analyzing, exploring, or refreshing understanding of a codebase.
  Activates for: full codebase analysis (first encounter or missing context),
  incremental updates after commits, answering targeted code questions,
  feature-scoped exploration before implementation, and staleness checks
  on existing context. Also use when deciding whether to delegate exploration
  to sub-agents. Works alongside ptc-sandbox when PTC is available.
  Do NOT use for: session lifecycle or handoff (use handoff-protocol),
  sub-agent dispatch mechanics (use sub-agent-delegation). Context packet
  schemas are in infrastructure, not this skill.
---

# Codebase Exploration

## Core Principle

Produce **complete, concise context** that gives other agents the full picture in the fewest tokens. Every relevant fact should be captured — but expressed as structured fields, `path:line` references, and short descriptions rather than prose. The goal is zero information gaps with zero wasted tokens. For every field, ask: **"Would a planner or coder need this to avoid a mistake?"** If yes, include it — concisely. If it's already expressed in fewer tokens elsewhere, reference it instead of repeating it.

## Epistemic Standards

Downstream agents trust your output to make decisions. Overconfident or fabricated findings cause worse outcomes than honest gaps.

- **Ask before guessing.** When a query is ambiguous about scope, depth, intent, or assumptions — ask the lead or user for clarification before exploring. The cost of a clarifying question is one message. The cost of exploring the wrong thing is a wasted exploration and a misleading packet. Default to asking whenever you aren't completely sure what's being asked. Ambiguity includes: which module ("the pipeline" — which one?), what depth (public API vs. implementation internals), why they're asking (planning to modify vs. trying to understand), and implicit assumptions ("how A talks to B" when they might not communicate directly).
- **Flag uncertainty, don't fill it with plausible stories.** If you can't determine a module's purpose from the code, report `"purpose": "unknown"` with `"confidence": 0.2` and `"needs_verification": true`. A planner can work around a gap. A planner can't detect a confident-sounding wrong answer.
- **Use confidence scores consistently.** Direct code evidence: 0.8-1.0. Strong inference from patterns: 0.6-0.8. Indirect evidence (naming, docs, partial matches): 0.3-0.6. Speculation: 0.0-0.3. Anything below 0.6 gets `"needs_verification": true`.
- **Correct the lead's framing when the code disagrees.** If asked to "explore the auth microservice" but auth is a monolithic module, say so. Don't force-fit findings into an incorrect mental model.
- **Report what you couldn't analyze and why.** Unparseable files, languages without available tooling, obfuscated code, heavy macro usage — state the limitation rather than silently skipping or guessing.
- **Know when to stop and report.** If you've explored the scope and have findings at mixed confidence levels, report what you have with appropriate confidence scores. Don't silently go down rabbit holes chasing completeness, and don't silently skip hard-to-analyze areas. Let the lead decide if deeper exploration is warranted.

## When NOT to Use

- Session lifecycle or agent handoff → use handoff-protocol
- Sub-agent dispatch mechanics (spawning, verification, collection) → use sub-agent-delegation
- Context packet schema definitions → see infrastructure
- Simple single-file reads or greps that don't require analysis → use Read/Grep tools directly

## Quick Reference

| Situation | Action |
|-----------|--------|
| First time on project / no `_codebase.json` | Full codebase analysis (Mode 1) |
| After chunk commit / lead says "refresh" | Incremental update — only re-read changed files (Mode 2) |
| Lead sends specific question | Query response — targeted search, structured answer (Mode 3) |
| Design doc exists, need feature context | Feature exploration — map touchpoints, deps, patterns (Mode 4) |
| Existing context, unsure if stale | Git-diff staleness check before deciding (see Step 2) |
| Scope covers 3+ independent modules | Sub-agent delegation — see Sub-Agent Count |
| Scope is 1-2 modules | Explore yourself — sub-agent overhead exceeds benefit |
| Incremental update, few changed files | Explore yourself — changes are targeted, not broad |

## Core Workflow

### Step 1: Determine Exploration Mode

Match the lead's task assignment:

| Signal in Task | Mode | Output File |
|----------------|------|-------------|
| "analyze codebase", no existing context | Full (1) | `.claude/context/_codebase.json` |
| "refresh", "update", after commit | Incremental (2) | Updated `_codebase.json` |
| Specific question, `info_request` | Query (3) | `.claude/context/queries/{topic}.json` |
| "explore for feature X", design doc ref | Feature (4) | `.claude/context/{feature}-context.json` |

If the signal is ambiguous, ask the lead before proceeding. This applies to mode selection but also to scope ("which pipeline module?"), depth ("public API or internals?"), and intent ("modifying or understanding?"). Wrong assumptions waste the entire exploration — a clarifying question costs one message.

Step 2 uses the mode determined here to decide whether re-exploration is needed.

### Step 2: Check Staleness via Git-Diff

**Applies to Modes 2 and 4 when existing context exists.** Skip for Mode 1 (no existing context) and Mode 3 (always fresh query).

**Process:**
1. Read the existing context packet's `files_analyzed` field
2. Run `git diff --name-only <commit-at-last-update>..HEAD`
3. Intersect: which files from `files_analyzed` appear in the diff?
4. If no intersection: report "context is current" to lead — do not re-explore
5. If intersection exists: those files + their immediate dependents (importers) are your scope

**Why git-diff, not timestamps:** Timestamps tell you WHEN something changed. A formatting-only commit or unrelated refactor triggers false re-exploration. Git diffs show actual content changes to files the packet covers — the only signal that matters for staleness.

If no commit ref is stored in the packet, fall back to `git diff --name-only HEAD~5..HEAD` and intersect against `files_analyzed`.

Step 3 operates only on the scope identified here (changed files + dependents), not the entire codebase.

### Step 3: Explore

**Execution path:** If PTC is available, use it for batch file analysis, AST parsing, dependency graphing, and metrics — your role-spec has recipes and packages for this. If PTC is not available, follow the manual step-by-step processes in `patterns.md`. Either path produces the same output: raw findings for synthesis.

**All modes share these priorities (in order):**

1. **Structure** — directory layout, module boundaries, key entry points
2. **Types** — public types, dataclasses, shared models. Record: `name`, `file:line`, `kind`, `usage` (max 50 chars)
3. **Patterns** — error handling, logging, config access. One example location per pattern, no code snippets
4. **Implementation status** — mark each module: `implemented` / `stub` / `planned` / `empty`. Base this on actual code content (real logic vs placeholder bodies), not docs
5. **Touchpoints** (feature mode only) — files to create/modify, integration points, dependencies

**Sub-agent delegation** (when scope covers 3+ independent modules):

See Sub-Agent Count section for the dispatch heuristic, then follow the sub-agent-delegation skill for the full dispatch → verify → synthesize lifecycle. Each sub-agent gets disjoint module ownership and must cover all 6 points:

| # | Point | Schema Target |
|---|-------|---------------|
| 1 | File catalog: path, line count, purpose ≤10 words | `ModuleBlock.files` |
| 2 | Internal architecture: types, inheritance, call flow | `TypeEntry` list |
| 3 | Patterns: error handling, logging, config, naming | `PatternEntry` list |
| 4 | External interfaces: imports from outside scope, exports | `DependencyEdge` |
| 5 | Implicit contracts: ordering, init, env vars, singletons | `ImplicitContract` list |
| 6 | Unknowns: ambiguous or undocumented (confidence <0.6) | `needs_verification: true` |

Sub-agent responses missing any point will fail schema validation at Step 5. For the full sub-agent prompt template, see `references/patterns.md`.

Step 4 operates on the raw findings from this step.

### Step 4: Synthesize into Context Packet

Transform raw findings into a schema-compliant packet. The schemas define required fields; this step is about judgment — how to express findings concisely while preserving completeness.

**Synthesis rules:**
1. **Merge** sub-agent results into the target schema structure
2. **Deduplicate** — keep the most detailed version of repeated findings
3. **Resolve conflicts** — if sub-agents disagree, read the source file yourself and decide
4. **Compress** — replace prose with structured fields. Every file reference uses `path:line` format. Descriptions stay under 50 characters.
5. **Cross-reference** — link to related context via `related_context` field

**The compression test:** After writing a field, check two things: (1) Could a planner understand this without reading the source file? If not, you haven't included enough. (2) Have you included the source code itself? If so, you've included too much — compress to structured fields and references.

**Preserve `manual_notes`** during incremental updates — copy the existing section verbatim. These are human-added observations that must survive every update.

Step 5 validates what this step produced.

### Step 5: Validate and Deliver

1. Check the packet against its type's schema (codebase, query, or feature)
2. Verify: no prose descriptions >100 characters, all file references use `path:line`
3. Write the packet to disk
4. Send `task_complete` to lead with `output_files` paths and summary (<100 tokens)

## Sub-Agent Count

| Codebase Signal | Sub-Agents | Rationale |
|-----------------|------------|-----------|
| 1-2 modules, <15 files total | 0 (explore yourself) | Dispatch overhead exceeds parallelism benefit |
| 3-4 independent modules | 2 | Group related modules per agent |
| 5+ independent modules | 3-4 | Maximum practical parallelism |
| Modules with heavy cross-dependencies | Fewer agents, larger scopes | Cross-dep modules in same scope capture interactions |
| Incremental update (few changed files) | 0 (explore yourself) | Changes are targeted, sub-agents are overkill |

**Memory awareness:** Sub-agents share your container's 1024MB memory limit. Each sub-agent's REPL consumes memory independently — heavy AST parsing or graph building across 3-4 concurrent sub-agents can trigger eviction at 512MB. For large codebases with heavy analysis, consider staggering sub-agent dispatch (2 at a time) rather than full parallel.

**Model selection per sub-agent:**
- **Haiku** — simple modules (<5 files, flat structure, no cross-module imports)
- **Sonnet** — complex modules (>5 files, pipelines, middleware, cross-dependencies)

**Hard limit: 2-4 sub-agents.** Fewer than 2 means you should do it yourself. More than 4 adds coordination overhead that exceeds parallelism gains. Sub-agents cannot spawn their own sub-agents (Claude Code limitation).

## Token Efficiency Conventions

Context packets are consumed by planners and coders every session. Token cost compounds across agents and turns.

| Field Type | Format | Example |
|------------|--------|---------|
| File reference | `path:line` or `path:start-end` | `"producer/config.py:45"` |
| Type reference | `module.TypeName` | `"common.models.RoiImage"` |
| Description | ≤50 characters | `"ROI quality scoring"` |
| List of names | Array of strings | `["TrackId", "RoiImage"]` |
| Conditional read pointer | `read_if` field | `"read_if": "implementing OCR"` |
| Confidence | `confidence` 0-1 | `"confidence": 0.9` |

## Staleness Detection

Use **git-diff staleness**, never timestamp staleness.

| Approach | Method | Problem |
|----------|--------|---------|
| Timestamp (WRONG) | Check `meta.updated_at`, flag if old | Formatting-only commits trigger false refresh; real changes to untracked files get missed |
| Git-diff (RIGHT) | Intersect `meta.files_analyzed` with `git diff --name-only <meta.commit_ref>..HEAD` | Detects actual content changes to files the packet covers |

```bash
# Files changed since context was last generated
git diff --name-only <meta.commit_ref>..HEAD
# Intersect with meta.files_analyzed — only re-explore the overlap + their importers
```

Age is irrelevant. Content changes to analyzed files are what matter.

## Critical Rules

- **Git-diff for staleness, never timestamps** — see Staleness Detection above. `files_analyzed` + `git diff` is the only reliable signal.
- **Complete coverage, compressed representation** — capture every relevant fact, but express it as structured fields, references, and short descriptions. `path:line` over paragraphs, structured fields over prose. For every field: "Would a planner need this to avoid a mistake?" If yes, include it — concisely.
- **Preserve `manual_notes` on incremental updates** — human observations must survive every update cycle. Copy the section verbatim.
- **Synthesis is YOUR job** — sub-agents return raw findings. You merge, deduplicate, resolve conflicts, and produce the final packet. Output must read as if one agent explored everything.
- **Disjoint sub-agent scopes** — no two sub-agents explore the same files. Scope overlap causes write conflicts and wasted tokens.
- **Implementation status must be observable** — mark modules as `implemented`/`stub`/`planned`/`empty` based on actual code (real logic vs TODO bodies), never from docs or intentions alone.
- **Schema compliance is non-negotiable** — packets follow their type's schema. Validate before writing.
- **Never rationalize past uncertainty** — if you aren't sure, use low confidence scores and `needs_verification`. A planner can request deeper exploration. A planner cannot detect a confident-sounding fabrication.

## References

For mode-specific step-by-step exploration processes (manual path) and the 6-point sub-agent checklist, read `references/patterns.md`.
For common exploration failures with WRONG/RIGHT examples, read `references/anti-patterns.md`.
For context packet field definitions and validation rules, see `schemas/context_packets.py`.
