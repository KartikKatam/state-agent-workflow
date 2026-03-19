# Research Workflow

> **Purpose**: Provides the researcher agent with the complete research lifecycle — from checking existing research through tool selection, query execution, synthesis, file writing, and index updates.
> **Consumers**: researcher
> **Schemas**: `persistent-research/schemas/persistent.schema.json`, `ephemeral-research/schemas/ephemeral.schema.json`, `persistent-research/schemas/index.schema.json`
> **Depends on**: `session-lifecycle` (for context pressure and handoff protocols)

## What You Learn From This Skill
- How to check existing research before making external queries
- Research tool routing: Context7 (primary) vs WebSearch (fallback)
- Query strategy by request type (API reference, usage pattern, comparison, troubleshooting)
- Result synthesis with confidence scoring
- Research file writing and index updates
- Persistent vs ephemeral research classification

## Contract
- ALWAYS check `.claude/research/_index.json` before starting new research
- ALWAYS include confidence scores in output
- ALWAYS update index after writing research files
- Signal low confidence with explicit recommendations for how the requester should proceed
- Send task_complete to lead with output file paths and summary

---

## Research Tools

### Context7 (Primary)

Context7 MCP provides direct access to library documentation with high accuracy.

**Use Context7 for:**
- Library API documentation and reference
- Function/class signatures and parameters
- Version-specific features and configuration
- Code examples from official docs

**Workflow:**

```python
# Step 1: Resolve library ID
result = mcp__context7__resolve-library-id(
    libraryName="pytest",
    query="how to use fixtures"
)
# Returns: /pytest-dev/pytest

# Step 2: Query docs
docs = mcp__context7__query-docs(
    libraryId="/pytest-dev/pytest",
    query="fixture scope and conftest.py usage"
)
```

### WebSearch (Fallback)

Use when Context7 doesn't cover the library or the question needs broader sources.

**Use WebSearch for:**
- Comparisons and opinions ("X vs Y")
- Stack Overflow troubleshooting
- GitHub issues and discussions
- When Context7 doesn't cover the library or result is incomplete

**Tips:**
- Include year (2026) for current information
- Use `site:` filter for specific sources
- Include version numbers when relevant
- Be specific: "async session factory" not "database stuff"

### Routing Guidelines

```
Question Type                           -> Tool Strategy
--------------------------------------------------------------------
"How do I use {library} API?"           -> Context7 first, WebSearch if gaps
"What version of {lib} has {feature}?"  -> Context7 (changelog/versions)
"Show me examples of {pattern}"         -> Context7 first, WebSearch for real-world
"Best practices for {pattern}?"         -> Context7 + WebSearch for opinions
"Compare {lib_a} vs {lib_b}"            -> WebSearch (needs opinions)
"Error: {specific error message}"       -> WebSearch (Stack Overflow, GitHub issues)
"How should I approach {design}?"       -> WebSearch (architecture patterns)
```

**Key principle**: Start with Context7 for anything library-specific. Fall back to WebSearch for opinions, comparisons, troubleshooting, or when Context7 lacks coverage.

---

## Research Phases

### Phase 0: Check Saved Research (ALWAYS FIRST)

```bash
cat .claude/research/_index.json 2>/dev/null
```

```
Found existing research?
+-- Fully answers query -> Return existing (no new queries needed)
+-- Partially answers   -> Identify gaps, research only gaps
+-- Not found           -> Proceed to Phase 1
```

### Phase 1: Receive Request

Research requests come via SendMessage from lead:

```json
{
  "research_id": "res-001",
  "requester": "chunk-coder",
  "topic": "pytest fixtures with async support",
  "context": "Implementing async database tests",
  "urgency": "normal",
  "output_path": ".claude/research/pytest-async-fixtures.json"
}
```

| Urgency | Meaning | Action |
|---------|---------|--------|
| `blocking` | Requester is waiting | Prioritize, minimal synthesis |
| `normal` | Needed soon | Standard depth |
| `background` | Nice to have | Deep research, extra examples |

### Phase 2: Analyze Request Type

| Type | Description | Approach |
|------|-------------|----------|
| `api_reference` | Specific function/class docs | Context7 |
| `usage_pattern` | How to use a feature | Context7, WebSearch if incomplete |
| `comparison` | Which approach is better | WebSearch (needs opinions) |
| `troubleshooting` | Debug an issue | WebSearch (Stack Overflow, GitHub issues) |
| `best_practices` | Current recommendations | Context7 first, WebSearch for opinions |

### Phase 3: Execute Research

Research execution has two tracks: **MCP calls** (you do these yourself) and **WebSearch delegation** (sub-agents do these). This split exists because sub-agents cannot access MCP tools in background mode.

#### Track A: MCP Calls (You Do This)

**For all request types**, start with Context7 when the topic involves a known library:

```
1. Context7: resolve-library-id -> query-docs
2. Assess completeness of Context7 results
3. If complete for api_reference -> skip Track B, go to Phase 4
4. If incomplete or needs broader sources -> continue to Track B
```

#### Track B: WebSearch Delegation (Sub-Agents)

When Context7 results are incomplete or the request type needs broader sources (comparisons, troubleshooting, best practices), spawn 2-3 WebSearch sub-agents in parallel. Each sub-agent targets a different **facet** of the question:

**Facet splitting by request type:**

| Request Type | Facet 1 | Facet 2 | Facet 3 (if needed) |
|-------------|---------|---------|---------------------|
| `usage_pattern` | "Official examples + tutorials" | "Community patterns + real-world usage" | — |
| `comparison` | "{lib_a} strengths + use cases" | "{lib_b} strengths + use cases" | "Direct comparison articles" |
| `troubleshooting` | "Stack Overflow solutions" | "GitHub issues + discussions" | "Version-specific changes" |
| `best_practices` | "Official recommendations" | "Community patterns 2026" | — |

**Sub-agent prompt template:**
```
Search for: {facet description}
Query: "{specific search query}"
Return: A structured summary with:
- Key findings (bullet points)
- Source URLs
- Confidence notes (official doc vs blog vs forum)
Do NOT search for {other facets} — another agent handles those.
```

**Constraints:**
- Sub-agents CANNOT use MCP (Claude Code background limitation) — WebSearch tool only
- Raw search noise stays in sub-agent context; only the summary returns to you
- 2-3 sub-agents max per research request
- Include year (2026) and version numbers in search queries

#### Track C: Synthesis

After both tracks complete, follow the Post-Delegation Synthesis Protocol in `session-lifecycle/SKILL.md`:
1. Merge Context7 findings (Track A) with WebSearch summaries (Track B)
2. De-duplicate: if Context7 and WebSearch found the same information, keep the Context7 version (higher confidence)
3. Detect conflicts: if WebSearch contradicts Context7, trust Context7 for API facts but WebSearch for community patterns/opinions
4. Resolve and write the final research file

**Error handling fallback:**
```python
result = context7.query_docs(library_id, query)
if result is None or result.incomplete:
    # Spawn WebSearch sub-agents for broader coverage
    # (see Track B above)
if no_sub_agents_needed:
    result = WebSearch(f"{library} {query} documentation")
```

### Phase 4: Synthesize Results

Structure findings:

```markdown
## Research: {topic}

### Summary
{2-3 sentence answer}

### API Reference
{Key functions/classes with signatures}

### Usage Examples
{Code examples}

### Gotchas
{Common mistakes}

### Sources
{Source URLs}
```

### Phase 5: Write Files and Notify Lead

**Write research file:**
```json
{
  "meta": {
    "research_id": "res-001",
    "topic": "pytest async fixtures",
    "requested_by": "chunk-coder",
    "completed_at": "2026-01-15T10:31:45Z",
    "sources_used": ["context7", "websearch"],
    "confidence": "high",
    "confidence_reason": "Context7 returned complete API reference",
    "gaps": [],
    "from_cache": false
  },
  "summary": "...",
  "findings": { "api_reference": {}, "examples": [], "patterns": [], "gotchas": [] },
  "sources": [{ "title": "...", "url": "..." }]
}
```

**Update index** (`_index.json`):
```json
{
  "id": "pytest-async-fixtures",
  "path": "persistent/pytest-async-fixtures.json",
  "library": "pytest-asyncio",
  "version": "0.23",
  "type": "api_reference",
  "topics": ["pytest", "async", "fixtures"],
  "confidence": 0.9,
  "source_count": 3
}
```

**Send task_complete:**
```python
SendMessage(to="lead", message={
  "type": "task_complete",
  "payload": {
    "status": "success",
    "output_files": [".claude/research/{topic-slug}.json"],
    "summary": "Research complete: {topic}. Confidence: {level}. Key finding: {takeaway}"
  }
})
```

---

## Confidence Signaling

Every response MUST include confidence indicators.

| Level | Score | When |
|-------|-------|------|
| **High** | 0.9–1.0 | Context7 returned complete API reference from official docs |
| **Medium** | 0.7–0.8 | Multiple sources agree, some gaps |
| **Low** | 0.6 | WebSearch only, conflicting sources |
| **Uncertain** | <0.6 | Neither source had good answers |

**Source quality**: Official docs (0.9–1.0) > GitHub README (0.8) > Tutorials (0.7) > Stack Overflow (0.6)

When confidence is low, **include recommendations** for how the requester should proceed (e.g., "Test against actual library behavior").

---

## Persistent vs Ephemeral Research

**Persistent** (`.claude/research/persistent/{library}-{topic}.json`):
- API/library documentation
- Integration guides
- Best practices
- Troubleshooting guides

**Ephemeral** (`.claude/research/ephemeral/query-{num}-{slug}.json`):
- Version checks (time-sensitive)
- One-off troubleshooting
- Comparisons (opinions change)
- Quick questions

```
Is this about a library API?
+-- Yes -> Persistent
+-- No  -> Will it be reused across features?
           +-- Yes -> Persistent
           +-- No  -> Ephemeral
```

Ephemeral queries with `access_count >= 3` should be promoted to persistent.

---

## Parallel Research (Different Topics)

When multiple researchers run simultaneously on different topics, no coordination is needed — each writes to separate files and sends task_complete independently. Each researcher updates `_index.json` after writing. If concurrent index writes conflict, lead resolves.

---

## Efficiency Guidelines

- **Context7 first** — direct docs are cheaper than search
- **Specific queries** — "pytest fixture scope" not "how do pytest fixtures work"
- **Include version** — "SQLAlchemy 2.0" not "SQLAlchemy"
- **Cache results** — check `.claude/research/` before querying
- **Don't over-research** — for `api_reference`: get docs + one example, done. For `comparison`: key differences + recommendation, done.

---

## Examples

### API Reference

Request: `"pydantic v2 model_validator decorator"` (migrating from v1)
1. Context7: `resolve-library-id("pydantic")` → `query-docs("model_validator v2")`
2. Write research file with API reference and migration notes

### Usage Pattern

Request: `"SQLAlchemy 2.0 async session patterns"` (building async repo)
1. Context7: `query-docs("async session 2.0")`
2. WebSearch: "SQLAlchemy 2.0 async repository pattern example"
3. Synthesize and write

### Troubleshooting

Request: `"pytest fixture not found in conftest.py"`
1. WebSearch: "pytest fixture not found conftest site:stackoverflow.com"
2. Context7: `query-docs("fixture discovery conftest")`
3. Compile common causes, write research file
