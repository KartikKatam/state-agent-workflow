# Ephemeral Research Skill

> **Purpose**: Guidelines for handling one-off research queries with expiration, access tracking, and promotion to persistent when frequently accessed.
> **Consumers**: researcher
> **Schemas**: `ephemeral-research/schemas/ephemeral.schema.json`
> **Depends on**: `MCP-research` (for tool reference), `persistent-research` (for promotion target)

## Contract

- ALWAYS check index and persistent research before creating new ephemeral queries
- Keep answers concise — 2-3 paragraphs max
- ALWAYS set `expires_at` based on query type
- Flag for promotion when `access_count >= 3`
- NEVER create ephemeral research for API/library docs — use persistent instead

Guidelines for handling one-off research queries that don't need long-term storage.

---

## When to Use Ephemeral Research

Create ephemeral research when the query is:
- **Time-sensitive** (current versions, recent trends)
- **One-off troubleshooting** (fixing specific errors)
- **Comparison/opinion** (best practices that change)
- **Context-specific** (specific to current feature, unlikely to reuse)

### Examples of Ephemeral Research

✅ **Good candidates:**
- "What's the latest PyTorch version compatible with CUDA 11.8?"
- "How to fix 'ImportError: cannot import SAM2Predictor'?"
- "Best Python testing framework in 2026"
- "How to optimize this specific batch processing loop?"
- "Should we use asyncio or threading for this use case?"

❌ **Poor candidates** (use persistent instead):
- "SAM2 batch inference API reference" (reusable docs)
- "PyTorch DataLoader configuration" (core library docs)
- "OpenCV image transformation functions" (API reference)

---

## Schema Location

```
.claude/skills/ephemeral-research/schemas/ephemeral.schema.json
```

---

## Output Location

```
.claude/research/ephemeral/query-{num}-{slug}.json
```

**Naming Convention:**
- `query-{sequential-num}-{topic-slug}.json`
- Example: `query-001-pytorch-version-check.json`
- Example: `query-002-fix-sam2-import-error.json`

---

## Structure

### Top-Level Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Matches filename without `.json` |
| `query` | Yes | The original question |
| `answer` | Yes | Synthesized answer (2-3 paragraphs max) |
| `answer_type` | Yes | One of: `factual`, `opinion`, `comparison`, `troubleshooting`, `how_to` |
| `key_points` | No | Bullet-point summary of main insights |
| `sources` | Yes | URLs with relevance scores |
| `related_persistent` | No | Links to persistent research that partially answered this |
| `should_promote` | Yes | Flag for promotion (default: false) |
| `meta` | Yes | Agent ID, timestamps, access count, confidence, context |

### Answer Types

| Type | Description | Example |
|------|-------------|---------|
| `factual` | Verifiable fact | "PyTorch 2.1.0 requires CUDA >= 11.8" |
| `opinion` | Best practices, recommendations | "Pytest is preferred over unittest in 2026" |
| `comparison` | Comparing options | "Asyncio vs threading for I/O" |
| `troubleshooting` | Fixing errors | "Fix ImportError by updating sam2" |
| `how_to` | Step-by-step instructions | "How to profile GPU memory usage" |

---

## Workflow

### 1. Check for Existing Answer

**Before creating new ephemeral query**, check index:

```bash
# Search ephemeral queries
cat .claude/research/_index.json | jq '.ephemeral[] | select(.query | contains("PyTorch version"))'
```

If similar query found:
- Read it
- Check `expires_at` - still valid?
- Increment `access_count`
- If access_count >= 3 → Flag for promotion

### 2. Check Persistent Research First

**Many ephemeral queries can be answered from existing persistent research:**

```bash
# Search persistent research
cat .claude/research/_index.json | jq '.search_index["pytorch"]'
# Returns: ["torch-api", "torch-dataloader", ...]
```

If persistent research exists and partially answers the query:
- Read the persistent research
- Note gaps in knowledge
- Only research the gaps
- Link to persistent research in `related_persistent`

**Example:**
```
Query: "How to optimize PyTorch DataLoader for small images?"
Persistent: "torch-dataloader" (has DataLoader docs)
Gap: Optimization tips for small images
Action: Use Perplexity to research gap, link to "torch-dataloader"
```

### 3. Create Ephemeral Query

Use MCP tools (see `mcp-research` skill):
- **WebSearch** for synthesized answers, recent info (preferred for ephemeral)
- **Context7** if docs might help

Keep answer concise (2-3 paragraphs max).

### 4. Set Expiration

Default: 7 days from creation

```json
{
  "meta": {
    "created_at": "2026-01-29T18:00:00Z",
    "expires_at": "2026-02-05T18:00:00Z"
  }
}
```

**Adjust expiration based on query type:**
- Version checks: 30 days (versions don't change daily)
- Troubleshooting: 7 days (may be outdated quickly)
- Best practices: 90 days (trends change slowly)
- Comparisons: 180 days (architectural choices stable)

### 5. Update Index

Add to `.claude/research/_index.json`:

```json
{
  "ephemeral": [
    {
      "id": "query-001-pytorch-version-check",
      "path": "ephemeral/query-001-pytorch-version-check.json",
      "query": "What's the latest PyTorch version compatible with CUDA 11.8?",
      "keywords": ["pytorch", "cuda", "version", "compatibility"],
      "created_at": "2026-01-29T18:00:00Z",
      "expires_at": "2026-02-28T18:00:00Z",
      "access_count": 0,
      "should_promote": false
    }
  ]
}
```

Update counts:
```json
{
  "meta": {
    "total_ephemeral": 3  // Increment
  }
}
```

---

## Auto-Cleanup

Researcher agent should cleanup expired queries on startup:

```python
def cleanup_expired_queries():
    index = read_index()
    now = datetime.now()

    expired = [q for q in index['ephemeral']
               if datetime.fromisoformat(q['expires_at']) < now]

    for query in expired:
        # Delete file
        os.remove(f".claude/research/{query['path']}")
        # Remove from index
        index['ephemeral'].remove(query)

    # Update meta
    index['meta']['last_cleanup'] = now.isoformat()
    index['meta']['total_ephemeral'] = len(index['ephemeral'])

    write_index(index)
```

---

## Promotion to Persistent

**When to promote:**
- `access_count >= 3` (queried 3+ times)
- User explicitly requests promotion
- Query is actually about API/library docs (misclassified initially)

**Promotion process:**
1. Read ephemeral query
2. Expand answer into structured persistent format
3. Create persistent research file
4. Update index (remove from ephemeral, add to persistent)
5. Delete ephemeral file

**Example:**
```
Ephemeral: "How do I use PyTorch DataLoader with custom collate_fn?"
Access count: 4 → PROMOTE

Action:
1. Read query-005-pytorch-dataloader-collate.json
2. Expand into full persistent research with:
   - DataLoader API reference
   - collate_fn parameter docs
   - Examples
   - Common patterns
3. Write persistent/torch-dataloader.json
4. Update index
5. Delete query-005-pytorch-dataloader-collate.json
```

---

## Best Practices

### DO ✅

- **Check index first** - Avoid duplicate queries
- **Link to persistent research** - Reuse existing knowledge
- **Set appropriate expiration** - Based on query type
- **Keep answers concise** - 2-3 paragraphs max
- **Extract key points** - Bullet list of main insights
- **Track access count** - For promotion decisions

### DON'T ❌

- **Don't create ephemeral for API docs** - Use persistent instead
- **Don't write long essays** - Keep it brief
- **Don't skip expiration** - Always set expires_at
- **Don't ignore persistent research** - Check it first
- **Don't forget sources** - Always cite URLs

---

## Example: PyTorch Version Check

```json
{
  "id": "query-001-pytorch-version-check",
  "query": "What's the latest PyTorch version compatible with CUDA 11.8?",
  "answer": "As of January 2026, PyTorch 2.1.2 is the latest stable version compatible with CUDA 11.8. PyTorch 2.1.x requires CUDA >= 11.8 or CUDA 12.1. For production use, PyTorch 2.1.2 with CUDA 11.8 is recommended over the 2.2.x series which is still in preview.",
  "answer_type": "factual",
  "key_points": [
    "PyTorch 2.1.2 is latest stable for CUDA 11.8",
    "Requires CUDA >= 11.8 or CUDA 12.1",
    "2.2.x series still in preview (not recommended for production)"
  ],
  "sources": [
    {
      "url": "https://pytorch.org/get-started/locally/",
      "title": "PyTorch Installation Guide",
      "snippet": "CUDA 11.8: pip install torch==2.1.2",
      "relevance": 0.95
    },
    {
      "url": "https://github.com/pytorch/pytorch/releases",
      "title": "PyTorch Releases",
      "relevance": 0.85
    }
  ],
  "related_persistent": [],
  "should_promote": false,
  "meta": {
    "agent_id": "res-001",
    "created_at": "2026-01-29T18:00:00Z",
    "expires_at": "2026-02-28T18:00:00Z",
    "access_count": 0,
    "confidence": 0.9,
    "context": {
      "feature": "batch-selection",
      "chunk": "chunk-01",
      "requestor": "chunk-coder-abc123"
    }
  }
}
```

---

## Integration with Researcher Agent

Researcher agent loads this skill when lead spawns it for ephemeral queries.

**Agent flow:**
1. Load `ephemeral-research` skill
2. Check index for similar queries
3. Check persistent research for partial answers
4. Use MCP tools to fill gaps (WebSearch primary, Context7 if docs help)
5. Write concise answer
6. Write to `.claude/research/ephemeral/query-{num}-{slug}.json`
7. Update `.claude/research/_index.json`
8. Send `task_complete` to lead with answer summary
