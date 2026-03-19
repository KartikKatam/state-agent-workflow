# MCP Research Skill

> **Purpose**: Reference guide for MCP research tools (Context7, WebSearch) with decision matrix, query optimization, and analytical comparison framework.
> **Consumers**: researcher
> **Schemas**: None (reference skill — no data output schema)
> **Depends on**: None

## Contract

- ALWAYS use Context7 as primary tool for API/library documentation
- Use WebSearch as fallback for opinions, comparisons, troubleshooting, and community patterns
- Follow the decision matrix for tool routing by question type
- NEVER use GitHub MCP — that is assigned to codebase-explorer only

High-leverage documentation and API research using Context7 MCP with WebSearch fallback.

## Target Tools

| Tool | Type | Purpose | API Key Required |
|------|------|---------|------------------|
| **Context7** | MCP | Library documentation (version-specific, official) | Optional (rate limits) |
| **WebSearch** | Built-in | Opinions, comparisons, troubleshooting, how-to | No |

**Note**: GitHub MCP is available to codebase-explorer for git history analysis, not for general research.

## Installation

```bash
# Context7 - Library documentation (primary research tool)
claude mcp add context7 -- npx -y @upstash/context7-mcp

# With API key for higher rate limits:
claude mcp add context7 -e CONTEXT7_API_KEY=your_key -- npx -y @upstash/context7-mcp
```

WebSearch is a built-in Claude tool — no installation needed.

## MCP Tool Reference

### Context7 (Primary)

**Purpose:** Fetch up-to-date, version-specific library documentation from official sources.

**Tools:**
| Tool | Description |
|------|-------------|
| `resolve-library-id` | Convert library name → Context7 ID (call first) |
| `query-docs` | Fetch documentation for a library ID |

**Usage Patterns:**
```python
# Step 1: Resolve the library ID
result = mcp__context7__resolve-library-id(
    libraryName="pytest",
    query="fixtures and conftest"
)
# Returns: /pytest-dev/pytest

# Step 2: Query docs with the ID
docs = mcp__context7__query-docs(
    libraryId="/pytest-dev/pytest",
    query="fixture scope session function module"
)
```

**Best For:**
- Official API documentation
- Version-specific function signatures
- Configuration options
- Parameter descriptions
- Code examples from official docs

**When to Trust Context7 Fully:**
- API reference (function signatures, parameters)
- Configuration options
- Version compatibility info
- Official code examples

**When to Supplement with WebSearch:**
- "Best practices" questions (docs may not cover real-world patterns)
- Troubleshooting specific errors
- Comparisons between libraries
- "How should I" design questions

**Limitations:**
- Free tier has rate limits (use API key for heavy usage)
- Not all libraries indexed
- May not have newest releases immediately
- Doesn't cover opinions or community patterns

---

### WebSearch (Fallback)

**Purpose:** Opinions, comparisons, troubleshooting, and community patterns.

WebSearch is a built-in Claude tool, not an MCP. Use it when Context7 can't answer.

**Usage Patterns:**
```python
# Troubleshooting
WebSearch(query="pytest fixture not found conftest site:stackoverflow.com")

# Comparisons (see Analytical Comparison section below)
WebSearch(query="pytest vs unittest comparison 2026")

# Best practices
WebSearch(query="FastAPI dependency injection patterns production")

# Recent changes
WebSearch(query="SQLAlchemy 2.0 migration guide breaking changes")
```

**Best For:**
- Stack Overflow solutions
- GitHub issue discussions
- Blog posts and tutorials
- Community opinions and patterns
- Troubleshooting specific errors
- Library comparisons

**Limitations:**
- Results may be outdated
- Quality varies by source
- Need to verify information against official docs

---

### GitHub MCP (Codebase-Explorer Only)

**Note:** GitHub MCP is assigned to **codebase-explorer** for git history analysis, not to researcher.

If you need GitHub information (issues, PRs, code search), request it through the lead who will spawn an explorer with the `git-history` skill.

**Researcher should NOT use GitHub MCP directly.**

---

## Decision Matrix

| Need | Primary | Fallback | Notes |
|------|---------|----------|-------|
| "How do I use X API?" | Context7 | WebSearch | Official docs first |
| "Show me examples of X" | Context7 | WebSearch | Try official examples first |
| "What's the best way to X?" | Context7 → WebSearch | — | Docs for correct way, web for patterns |
| "Why isn't X working?" | WebSearch | Context7 | Stack Overflow has troubleshooting |
| "Compare X vs Y" | WebSearch → Context7 both | — | See Analytical Comparison below |
| "What changed in X v2?" | Context7 | WebSearch | Version-specific docs |
| "Is X compatible with Y?" | WebSearch | Context7 | Community experience matters |
| "How should I design X?" | WebSearch | — | Needs opinions, not just docs |

---

## Analytical Comparison (For "Compare X vs Y" Queries)

When comparing libraries or approaches, provide **analytical comparison**, not just opinions.

### Comparison Structure

```markdown
## Comparison: {Library A} vs {Library B} for {Use Case}

### Overview
- **{Library A}**: {1-sentence description, primary purpose}
- **{Library B}**: {1-sentence description, primary purpose}

### Similarities
| Aspect | Both Libraries |
|--------|----------------|
| {aspect} | {what they share} |

### Key Differences
| Aspect | {Library A} | {Library B} |
|--------|-------------|-------------|
| {aspect 1} | {A's approach} | {B's approach} |
| {aspect 2} | {A's approach} | {B's approach} |

### When to Use Each

**Use {Library A} when:**
- {specific scenario where A is better}
- {why it's better for this scenario}

**Use {Library B} when:**
- {specific scenario where B is better}
- {why it's better for this scenario}

### For Your Use Case: {stated topic}

**Recommendation**: {A or B}
**Reasoning**: {specific reasons tied to the use case}
```

### Example: pytest vs unittest for async database tests

```markdown
## Comparison: pytest vs unittest for async database tests

### Overview
- **pytest**: Third-party testing framework with plugin ecosystem and fixtures
- **unittest**: Python stdlib testing framework with class-based tests

### Similarities
| Aspect | Both Libraries |
|--------|----------------|
| Test discovery | Automatic test discovery |
| Assertions | Support for assertions (pytest uses plain assert) |
| Setup/teardown | Support for test setup and cleanup |

### Key Differences
| Aspect | pytest | unittest |
|--------|--------|----------|
| Async support | Native via pytest-asyncio plugin | Requires subclassing AsyncTestCase |
| Fixtures | Function-scoped, dependency injection | Class methods (setUp/tearDown) |
| Syntax | Plain functions + assert | Classes + self.assertEqual() |
| Database fixtures | Easy session-scoped async fixtures | Complex with async context managers |

### For async database tests

**Recommendation**: pytest with pytest-asyncio
**Reasoning**:
- Session-scoped async fixtures allow database connection reuse
- `@pytest_asyncio.fixture` handles async setup/teardown cleanly
- Plugin ecosystem (pytest-asyncio, pytest-aiohttp) mature
```

### Research Process for Comparisons

1. **Context7**: Get official docs for BOTH libraries (API, features)
2. **WebSearch**: Get community opinions and real-world experiences
3. **Synthesize**: Create analytical comparison with the structure above
4. **Recommend**: Tie recommendation to the specific use case

---

## Query Optimization

### Context7 Tips
- **Include library name**: "pytest fixtures" not just "fixtures"
- **Specify version**: "SQLAlchemy 2.0 async" not "SQLAlchemy async"
- **Use library ID when known**: `/fastapi/fastapi` is faster than resolving
- **Specific queries**: "fixture scope session module" not "how do fixtures work"
- **Check if indexed**: Not all libraries are in Context7

### WebSearch Tips
- **Add year**: "FastAPI best practices 2026" for current info
- **Site filter**: "site:stackoverflow.com" for troubleshooting
- **Specific error messages**: Include exact error text in quotes
- **Language qualifier**: Add "python" for ambiguous terms
- **Comparison format**: "{lib_a} vs {lib_b} comparison" for comparisons

---

## Rate Limits & Costs

| Tool | Free Tier | Notes |
|------|-----------|-------|
| Context7 | Limited requests/day | Add API key for higher limits |
| WebSearch | Built-in, no limits | Part of Claude, no extra cost |

### Cost-Efficient Strategy

1. **Check cache first** — `.claude/research/` may already have the answer
2. **Context7 first** — Free tier is sufficient for most lookups
3. **WebSearch for gaps** — When Context7 doesn't have the answer
4. **Batch related queries** — Don't make 5 Context7 calls for one topic
5. **Save results** — Always save to `.claude/research/` for reuse

---

## Output Schema

Research results should follow this structure:

```json
{
  "meta": {
    "research_id": "string",
    "topic": "string",
    "sources_used": ["context7", "websearch"],
    "completed_at": "ISO timestamp",
    "confidence": "high|medium|low|uncertain",
    "confidence_reason": "Why this confidence level",
    "gaps": ["Any areas not fully covered"],
    "from_cache": false
  },
  "summary": "2-3 sentence answer",
  "findings": {
    "api_reference": {
      "function_name": {
        "signature": "def func(a: int) -> str",
        "description": "What it does",
        "parameters": []
      }
    },
    "examples": [
      {
        "description": "What this shows",
        "code": "actual code",
        "source": "where from"
      }
    ],
    "patterns": [
      {
        "name": "Pattern name",
        "description": "When to use",
        "code": "example"
      }
    ],
    "gotchas": [
      "Common mistake 1",
      "Common mistake 2"
    ]
  },
  "sources": [
    {"title": "Source name", "url": "https://..."}
  ]
}
```

---

## Integration Notes

### With Chunk-Coder

Chunk-coder reads research results directly from `.claude/research/`:
```bash
cat .claude/research/pytest-async-fixtures.json
```

If needed research doesn't exist, chunk-coder sends `info_request` to lead, which spawns researcher.

### With Plan-Architect

During planning, lead can spawn researcher for unfamiliar libraries:
```
Lead: "Research SAM2 API before planning"
→ Spawns researcher teammate
→ Waits for task_complete
→ Plan-architect reads .claude/research/sam2-api.json
```

### Communication

Researcher sends `task_complete` to lead via SendMessage when research is written to disk. Always include confidence level so the lead knows whether to proceed or request more research.
