# Exploration Patterns

Step-by-step processes for each exploration mode. Load when you need detailed guidance beyond the core workflow in SKILL.md.

If PTC is available, prefer it for batch file analysis, AST parsing, and metrics (see your role-spec for recipes). These patterns work with or without PTC.

---

## Mode 1: Full Codebase Analysis

**Output:** `.claude/context/_codebase.json` (CodebaseContext schema)

### Step-by-Step

1. **Setup** — `mkdir -p .claude/context`, check if `_codebase.json` already exists
2. **Map structure** — list all top-level directories, find modules (`__init__.py`, `package.json`), identify key files (config, models, pipeline, main). Record each as a `ModuleBlock` with `purpose` ≤50 chars.
3. **Read dependency manifests** — `requirements*.txt`, `pyproject.toml`, `package.json`, `Cargo.toml`. Record as `DependencyEntry` with version constraints. Never infer deps from imports — imports may reference uninstalled or optional packages.
4. **Detect languages** — record in `meta.languages_detected`. This informs which PTC analysis tools are available for future exploration.
5. **Assess implementation status** — for each module, check whether files contain real logic (`implemented`), placeholder bodies (`stub`), only init files (`empty`), or are referenced in docs but don't exist (`planned`). Record evidence in `status_evidence`.
6. **Extract types** — find public types (`class`, `@dataclass`, `interface`, `struct`, type aliases). Record as `TypeEntry`: `name`, `file:line`, `kind`, `usage` ≤50 chars. Skip internal helpers (prefixed with `_`).
7. **Extract configs** — find config classes/dataclasses. Record as `ConfigEntry` with fields and defaults.
8. **Identify patterns** — sample error handling, logging, config access, test conventions. One `PatternEntry` per pattern with `example_location` and `read_if`. Don't catalog every instance.
9. **Map dependencies between modules** — record as `DependencyEdge` entries. Source: import statements (or PTC dependency graph if available).
10. **Identify implicit contracts** — ordering assumptions, required initialization, shared state, env vars. Record as `ImplicitContract` with `confidence` and `needs_verification` when uncertain.
11. **Record test conventions** — from one sample test file: framework, directory, naming pattern.
12. **Build name bank** — collect existing type/function/module names for collision avoidance.
13. **Write packet** — validate against CodebaseContext schema. Set `meta.update_type: "full"`, record `meta.commit_ref` and `meta.files_analyzed`.

### Include vs Exclude

| Include | Exclude |
|---------|---------|
| Module boundaries and purposes (≤50 chars) | Individual function implementations |
| Public/exported types with `file:line` | Internal helper types (prefixed `_`) |
| Config classes with fields and defaults | Runtime config values |
| One example per coding pattern | Every instance of a pattern |
| `path:line` references | Source code content |
| Test convention from one sample | Full test inventory |
| Dependencies from manifest files | Transitive dependencies |
| Implicit contracts with confidence scores | Speculation without evidence |
| `status_evidence` for non-obvious statuses | Status without evidence |

---

## Mode 2: Incremental Update

**Output:** Updated `.claude/context/_codebase.json`

### Process

1. Read existing `_codebase.json`
2. Identify changed scope via git-diff (SKILL.md Step 2)
3. Re-read only changed files + immediate dependents (importers)
4. Update affected fields: `structure.blocks`, `types`, `configs`, `patterns`, `dependency_edges`
5. Add entries for newly created files/modules
6. Remove entries for deleted files/modules
7. **Preserve `manual_notes` verbatim** — copy existing list unchanged
8. Update `meta`: set `update_type: "incremental"`, update `updated_at`, `commit_ref`, `update_summary`, append to `files_analyzed`
9. Update `name_bank` with any new names

### After Chunk Completion (Orchestrator-Triggered)

When the lead triggers incremental refresh after a chunk commit:

1. Read the session log's `files_modified` — this is your exact changed-file list
2. Re-read only those files + files that import them
3. Focus on: new `ModuleBlock` entries, new `TypeEntry` items, updated function signatures, new config values, names to add to `name_bank`
4. Keep it fast — incremental refresh should complete in ~30 seconds, not minutes

---

## Mode 3: Query Response

**Output:** `.claude/context/queries/{topic-slug}.json` (QueryResult schema)

### Process

1. **Clarify before exploring.** Parse the lead's question, scope, and context. If ambiguous about what's being asked, ask for clarification (see Epistemic Standards in SKILL.md). Check existing query packets — if a recent, relevant one exists and the files haven't changed, return a pointer instead of re-exploring.
2. Search within specified scope using appropriate tools
3. For each `specific_questions` entry, gather targeted evidence
4. Assess confidence based on evidence quality (see scale below)
5. Write query packet with structured answer, sources, and confidence

### Confidence Scoring

| Evidence Level | Confidence | Action |
|---------------|-----------|--------|
| Direct code evidence (saw the implementation) | 0.8-1.0 | Report with confidence |
| Strong inference (consistent patterns, clear imports) | 0.6-0.8 | Report, note it's inferred |
| Indirect evidence (docs, naming, partial matches) | 0.3-0.6 | Report with `needs_verification: true` |
| Speculation (no direct evidence found) | 0.0-0.3 | Report with `needs_verification: true` and add to `limitations` |

Flag low-confidence answers so downstream agents know to double-check. Include `follow_up_suggested` when the findings raise new questions.

---

## Mode 4: Feature Exploration

**Output:** `.claude/context/{feature}-context.json` (FeatureContext schema)

### Process

1. Read the design document (path provided by lead)
2. Read existing `_codebase.json` for project context
3. **Clarify scope if needed.** If the design doc is ambiguous about what parts of the codebase are affected, ask before exploring broadly.
4. Identify touchpoints: files to create, modify, or read. Record as `Touchpoint` with action, reason, and lines of interest.
5. Map dependencies: types, configs, functions the feature needs. Record in `FeatureDependencies`.
6. Identify new items the feature introduces. Record as `NewItem`.
7. Find patterns to follow for consistency. Record as `PatternRef` with example locations.
8. Check for naming conflicts against `name_bank` from codebase context. Record as `NamingConstraint`.
9. Identify risks and unknowns. Record as `FeatureRisk` with severity and confidence.
10. Write feature-context packet. Set `meta.confidence` reflecting overall analysis confidence.

### Touchpoint Actions

| Action | Meaning | What to Record |
|--------|---------|----------------|
| `create` | New file needed | Path, purpose, suggested module placement |
| `modify` | Changes to existing file | Path, lines of interest, what changes, confidence |
| `read` | Reference only | Path, what information the coder needs from it |

---

## The 6-Point Sub-Agent Checklist

Give each sub-agent this checklist for its assigned scope. Every point must be covered in its response.

| # | Point | What to Capture | Schema Target |
|---|-------|-----------------|---------------|
| 1 | **File catalog** | Every file: path, line count, purpose ≤10 words | `ModuleBlock.files` |
| 2 | **Internal architecture** | Key classes/functions, inheritance, call flow | `TypeEntry` list, `DependencyEdge` |
| 3 | **Patterns** | Error handling, logging, config, naming conventions | `PatternEntry` list |
| 4 | **External interfaces** | Imports from outside scope, exports used by other modules | `DependencyEdge`, `TypeEntry.exported` |
| 5 | **Implicit contracts** | Assumed ordering, required init, env vars, singletons | `ImplicitContract` list |
| 6 | **Unknowns** | Ambiguous, undocumented, or needing deeper investigation | Items with `confidence < 0.6`, `needs_verification: true` |

### Sub-Agent Prompt Template

```
## Task
Explore the {module_name} module and produce a structured analysis.

## Scope
Directory: {path}
Files: {file_list}
You are responsible ONLY for files within this scope.

## Deliverable
Return JSON covering all 6 points of the exploration checklist:
1. File catalog (path, line_count, purpose ≤10 words per file)
2. Internal architecture (types with name, file:line, kind, usage ≤50 chars)
3. Patterns (name, style ≤50 chars, example_location as path:line, read_if)
4. External interfaces (imports from outside scope, exported types)
5. Implicit contracts (description ≤100 chars, between modules, evidence path:line, confidence 0-1)
6. Unknowns (items with confidence < 0.6, needs_verification: true)

## Constraints
- Do NOT explore files outside your assigned scope
- Use structured fields, not prose paragraphs
- File references use path:line format
- Descriptions ≤50 characters
- Include confidence scores for any non-obvious findings
- Flag anything uncertain with needs_verification: true
```

---

## Synthesis After Sub-Agent Returns

After sub-agents return verified results:

1. **Collect** all 6-point analyses
2. **Merge** into target schema — file catalogs to `structure.blocks`, types to `types` dict by module, patterns to `patterns` list
3. **Deduplicate** — if two sub-agents found the same shared type, keep the more detailed entry
4. **Resolve conflicts** — sub-agents may report different patterns for the same concern. Read the source yourself to determine which is accurate. Don't average — pick the correct one.
5. **Fill cross-cutting gaps** — connections between modules that no single sub-agent could see: shared types, cross-module call chains, dependency directions, implicit contracts that span scopes
6. **Compress** — final packet must be smaller than the sum of sub-agent outputs. Remove redundant entries, shorten descriptions to ≤50 chars, eliminate anything a planner wouldn't need
7. **Validate** — check the complete packet against its schema before writing

The synthesized packet should read as if one agent explored the entire codebase — no seams between sub-agent contributions.
