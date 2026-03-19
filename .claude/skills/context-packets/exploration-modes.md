# Exploration Mode Processes

> **Purpose**: Detailed step-by-step processes for each codebase exploration mode. Load this file when generating context packets.
> **Loaded by**: codebase-explorer (on demand, not required at startup for other consumers)

---

## Mode 1: Full Codebase Analysis

**Trigger**: "analyze this codebase", "explore the project", first time on a project
**Output**: `.claude/context/_codebase.json` (codebase schema)

### Step 1: Setup

```bash
mkdir -p .claude/context
cat .claude/context/_codebase.json 2>/dev/null && echo "EXISTS" || echo "NEW"
```

### Step 2: Map Structure

```bash
# ALL top-level directories, not just source code
find . -maxdepth 1 -type d -not -path "*/.*" -not -path "*/__pycache__/*" -not -path "*/.venv/*"

# Recurse into each
find . -type d -not -path "*/.*" -not -path "*/__pycache__/*" -not -path "*/.venv/*" | head -30

# Python modules
find . -name "__init__.py" -not -path "./.venv/*" | head -20

# Key files
find . -name "config.py" -o -name "models.py" -o -name "pipeline.py" | grep -v .venv
```

### Step 3: Sweep Scripts, Docs, and Manifests

```bash
# Scripts — read headers to capture purpose
ls scripts/ 2>/dev/null
head -10 scripts/*.sh scripts/*.py 2>/dev/null

# All project docs at root (.md, .txt, planning artifacts)
find . -maxdepth 1 \( -name "*.md" -o -name "*.txt" \) -not -path "*/.*"

# Dependency manifests — NEVER infer deps from imports alone
cat requirements*.txt 2>/dev/null
cat pyproject.toml 2>/dev/null | grep -A 50 "\[project.dependencies\]\|\[tool\."
```

Record scripts in `structure.blocks`. For each doc, note `{"file", "purpose", "relevance": "planner|coder|reference"}`. Record deps from manifests into `overview.key_dependencies`.

### Step 4: Assess Implementation Status

For each module, mark status in `structure.blocks`:
- `implemented` — real logic, tests, actively used
- `stub` — signatures present, placeholder/TODO bodies
- `planned` — referenced in docs, no code (or only `__init__.py`)
- `empty` — `__init__.py` only, no doc references

### Step 5: Extract Types (selective — only public types)

```bash
# Find type definitions
grep -rn "^class \|^@dataclass\|^[A-Z][a-zA-Z]*\s*=" --include="*.py" | grep -v .venv | grep -v test | head -50
```

For each type, record: `{"name": "X", "file": "path:line", "kind": "dataclass", "usage": "brief purpose"}`

### Step 6: Extract Configs

```bash
grep -rn "@dataclass" -A 15 --include="config.py" | head -80
```

Record each config class and its fields with defaults.

### Step 7: Identify Patterns (sample, don't exhaustively list)

```bash
# Error handling - just find the pattern, not every instance
grep -rn "class.*Error\|raise " --include="*.py" | head -10

# Logging
grep -rn "logger\." --include="*.py" | head -5

# One test file to understand convention
cat $(find . -name "test_*.py" | head -1) | head -40
```

### Step 8: Load Name Bank

```bash
cat docs/name-bank.md 2>/dev/null | head -100
```

### Step 9: Write Codebase Packet

Write the codebase packet directly following `codebase.schema.json`:

```json
{
  "meta": {
    "type": "codebase",
    "created": "2026-01-27T10:00:00Z",
    "updated": "2026-01-27T10:00:00Z",
    "schema_version": "1.0",
    "project_root": "/home/user/lpr-module",
    "project_name": "lpr-module",
    "update_type": "full"
  },
  "structure": {
    "blocks": [
      {
        "name": "producer",
        "path": "producer/",
        "purpose": "detection, tracking, ROI cropping, quality scoring",
        "key_files": {
          "config": "config.py",
          "models": "models.py",
          "pipeline": "pipeline.py",
          "ops": ["ops_detection.py", "ops_tracking.py"]
        }
      }
    ]
  },
  "types": {
    "shared": [
      {"name": "TrackId", "file": "common/models.py:5", "kind": "alias", "usage": "track identifier"}
    ],
    "by_block": {
      "producer": []
    }
  },
  "configs": [],
  "patterns": {
    "error_handling": {"style": "custom exceptions", "base_class": "common/errors.py:AppError"},
    "logging": {"library": "structlog", "import": "from common.log import logger"}
  },
  "name_bank": {},
  "manual_notes": {"_comment": "Add manual observations here", "notes": []}
}
```

Then send `task_complete` to lead with `output_files: [".claude/context/_codebase.json"]`.

---

## Mode 2: Incremental Update

**Trigger**: "refresh codebase context", "update the context", after chunk-N completion
**Output**: Updated `.claude/context/_codebase.json` (preserves `manual_notes`)

### Process

1. Load existing `.claude/context/_codebase.json`
2. **Preserve `manual_notes` entirely** — copy it verbatim to the updated file
3. Re-run exploration steps (2-5 from Mode 1), focusing on changed areas
4. Update changed sections only — don't rewrite unchanged blocks
5. Set `meta.update_type: "incremental"` and `meta.update_summary: "what changed"`
6. Set `meta.updated` to current timestamp
7. Write the updated file directly, then send `task_complete` to lead

### Incremental Update After Chunk Completion

When triggered by the orchestrator after a chunk commit (auto re-exploration):

```
Input from orchestrator:
  - mode: "incremental"
  - instructions: "Re-explore {feature directory} after chunk-{N}"
  - inputs: { files_to_read: [context path, session log path] }
```

**Process:**
1. Read the session log's `files_modified` to know exactly what changed
2. Read only the changed files + their immediate dependents
3. Update the feature context packet with:
   - New entries in `structure.blocks` for new files
   - New entries in `types.shared` for new types/classes
   - Updated function signatures in `touchpoints`
   - New config values
   - Updated `name_bank` (names now in use, names still available)
4. Preserve all `manual_notes`
5. Write updated packet, send `task_complete`

**Key rule**: Incremental re-exploration is lightweight — it reads only changed files + immediate dependents. Does NOT re-read the entire codebase. Keeps it fast (~30 seconds) vs full exploration (~2-3 minutes).

---

## Mode 3: Query Response

**Trigger**: Spawned by lead with a specific question (including BLOCKED info_requests)
**Output**: `.claude/context/queries/{topic-slug}.json` (query-result schema)

### Input Format

The lead provides:
- **question**: What they need to know
- **scope**: Where to look (e.g., "producer/", "entire codebase")
- **context**: Why they're asking (helps give relevant answer)
- **specific_questions**: (optional) Concrete sub-questions to answer

### Process

1. Parse the question and scope
2. Search within the specified scope using Glob, Grep, Read
3. For each `specific_questions` entry, gather targeted evidence
4. Assess confidence based on evidence quality
5. Produce a query-result packet

### Output Format

```json
{
  "meta": {
    "type": "query",
    "created": "2026-01-27T10:15:00Z",
    "schema_version": "1.0",
    "requester": "lead",
    "query_id": "error-handling-patterns"
  },
  "query": {
    "question": "How are errors handled in producer/?",
    "scope": "producer/",
    "context": "implementing new batch selection, need to know error patterns"
  },
  "answer": {
    "summary": "Producer uses OpsError exceptions caught in pipeline.py:run_frame(). Errors logged with structlog.",
    "confidence": 0.9,
    "details": [
      {"item": "OpsError base class", "location": "producer/errors.py:12", "relevance": "inherit from this"},
      {"item": "catch point", "location": "producer/pipeline.py:45-50", "relevance": "where errors surface"}
    ]
  },
  "sources": [
    {"file": "producer/errors.py", "lines_read": "1-30", "relevant": true},
    {"file": "producer/pipeline.py", "lines_read": "40-60", "relevant": true}
  ]
}
```

Then send `task_complete` to lead with output file path and summary.

---

## Sub-Agent Delegation for Deep Exploration

When the exploration scope covers 3+ independent modules or the codebase is large enough that single-agent analysis risks context pressure, use a two-phase sub-agent pattern.

### Phase A: Quick Map (You Do This)

1. Run directory listing + file counts per top-level module
2. Assess complexity per module:
   - **Simple** (<5 files, flat structure, no cross-module imports): assign to Haiku sub-agent
   - **Complex** (>5 files, pipeline/middleware patterns, cross-module dependencies): assign to Sonnet sub-agent
3. Define 2-4 disjoint scopes — each sub-agent gets exclusive ownership of its module(s)

### Phase B: Parallel Deep Dives (Sub-Agents)

Spawn sub-agents with the Task tool. Each sub-agent receives:
- **Scope**: Exact directory paths to explore (disjoint — no overlap)
- **Checklist**: The 6-point exploration checklist below
- **Output format**: "Return a structured summary following this template: ..."

**6-Point Exploration Checklist** (each sub-agent covers all 6 for its scope):

| # | Point | What to capture |
|---|-------|-----------------|
| 1 | File catalog | Every file with `path:line_count`, purpose in ≤10 words |
| 2 | Internal architecture | Key classes/functions, inheritance, call flow between files |
| 3 | Patterns | Error handling, logging, config access, naming conventions |
| 4 | External interfaces | Imports from outside scope, exports used by other modules |
| 5 | Implicit contracts | Assumed ordering, required initialization, env vars, singletons |
| 6 | Unknowns | Anything ambiguous, undocumented, or requiring deeper investigation |

**Model selection per sub-agent:**

```
Module complexity    -> Model
-----------------------------------------
Simple (<5 files)    -> Haiku (fast, cheap)
Complex (>5 files,   -> Sonnet (better at
 pipeline, middleware)   tracing call flows)
```

**Constraints:**
- Sub-agents CANNOT spawn their own sub-agents (Claude Code limitation)
- Sub-agents do NOT have MCP access in background mode — use CLI tools only
- 2-4 sub-agents is the sweet spot; more adds coordination overhead
- Each sub-agent returns text to you — raw findings stay in their context

### Phase C: Synthesis (You Do This)

Follow the Post-Delegation Synthesis Protocol in `session-lifecycle/SKILL.md`:
1. Merge all sub-agent results into the target schema structure
2. De-duplicate (keep most detailed version of repeated findings)
3. Detect and resolve conflicts (read source files yourself if sub-agents disagree)
4. Write the final schema-compliant context packet

The output should read as if a single explorer analyzed everything — no visible seams between sub-agent contributions.

---

## Mode 4: Feature Exploration

**Trigger**: "explore for feature X", given a design document
**Output**: `.claude/context/{feature}-context.json` (feature-context schema)

### Input

- Path to design document
- Feature name

### Process

1. Read the design document
2. Identify all touchpoints (files to read/modify/create)
3. Find dependencies (types, configs, functions needed)
4. Note patterns that apply (from existing codebase)
5. Check for naming conflicts (from `name_bank`)
6. Map integration points with existing code

### Output Format

```json
{
  "meta": {
    "type": "feature",
    "created": "2026-01-27T10:20:00Z",
    "updated": "2026-01-27T10:20:00Z",
    "schema_version": "1.0",
    "feature_name": "batch-selection",
    "design_doc": ".claude/designs/batch-selection.md",
    "status": "ready"
  },
  "feature": {
    "summary": "Select optimal batch of ROIs using quality + diversity scoring",
    "primary_block": "producer",
    "secondary_blocks": [],
    "goals": ["maximize quality", "ensure diversity", "respect batch size limits"]
  },
  "touchpoints": [
    {"file": "producer/ops_batch.py", "action": "create", "reason": "new batch selection logic"},
    {"file": "producer/pipeline.py", "action": "modify", "reason": "call batch selection", "lines_of_interest": "80-100"},
    {"file": "producer/config.py", "action": "modify", "reason": "add batch config fields"},
    {"file": "producer/__init__.py", "action": "modify", "reason": "export new functions"}
  ],
  "dependencies": {
    "types_needed": [
      {"name": "RoiImage", "from": "common/models.py", "used_for": "input candidates"}
    ],
    "configs_needed": [
      {"name": "ProducerConfig", "fields_used": ["max_batch_size", "min_quality"]}
    ]
  },
  "new_items": {
    "types": [
      {"name": "BatchCandidate", "file": "producer/models.py", "purpose": "ROI with diversity score"}
    ],
    "functions": [
      {"name": "select_batch", "file": "producer/ops_batch.py", "signature": "(candidates: list[RoiImage], cfg: ProducerConfig) -> list[RoiImage]"}
    ],
    "config_fields": [
      {"config": "ProducerConfig", "field": "diversity_weight", "type": "float", "default": 0.3}
    ]
  },
  "patterns_to_follow": [
    {"pattern": "ops_ prefix for operation files", "example_in": "producer/ops_detection.py", "apply_to": "new ops_batch.py"},
    {"pattern": "early returns for validation", "example_in": "producer/ops_tracking.py:45-60", "apply_to": "select_batch function"}
  ],
  "naming_constraints": [
    {"name": "BatchResult", "type": "class", "avoid_because": "already exists in consumer/models.py"}
  ],
  "related_context": {
    "codebase": ".claude/context/_codebase.json",
    "load_sections": ["types.shared", "configs", "patterns.error_handling"]
  }
}
```

Then send `task_complete` to lead with output file path and summary.
