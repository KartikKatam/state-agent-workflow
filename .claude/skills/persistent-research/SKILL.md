# Persistent Research Skill

> **Purpose**: Guidelines for creating long-lived, reusable research artifacts about APIs, libraries, and frameworks.
> **Consumers**: researcher
> **Schemas**: `persistent-research/schemas/persistent.schema.json`, `persistent-research/schemas/index.schema.json`
> **Depends on**: `MCP-research` (for tool reference and query optimization)

## Contract

- ALWAYS check `.claude/research/_index.json` before creating new research
- Structure over prose — use arrays of objects, not paragraphs
- ALWAYS add integration points linking research to actual codebase usage
- Set confidence scores based on source quality
- ALWAYS update the index after writing any research file

Guidelines for creating long-lived, reusable research artifacts about APIs, libraries, and frameworks.

---

## When to Use Persistent Research

Create persistent research when the information:
- **Will be reused** across multiple features or sessions
- **Is about APIs/libraries** used in the codebase
- **Has lasting value** (not time-sensitive)
- **Is version-specific** documentation

### Examples of Persistent Research

✅ **Good candidates:**
- SAM2 batch inference API
- PyTorch DataLoader configuration options
- OpenCV transformation functions
- Pydantic validator patterns
- FastAPI dependency injection

❌ **Poor candidates** (use ephemeral instead):
- "What's the current PyTorch version?" (time-sensitive)
- "How to fix ImportError?" (troubleshooting)
- "Best Python testing framework 2026" (opinion/comparison)

---

## Schema Location

```
.claude/skills/persistent-research/schemas/persistent.schema.json
```

---

## Output Location

```
.claude/research/persistent/{library-name}.json
```

**Naming Convention:**
- `{library}-{topic}.json` for specific topics (e.g., `sam2-batch-api.json`)
- `{library}-api.json` for general API reference (e.g., `torch-api.json`)
- `{library}-integration.json` for integration guides

---

## Structure

### Top-Level Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique identifier matching filename (no `.json`) |
| `library` | Yes | Library name (e.g., "sam2", "torch") |
| `version` | No | Version or range (e.g., "2.1.0", ">=2.0") |
| `type` | Yes | One of: `api_reference`, `integration_guide`, `best_practices`, `troubleshooting` |
| `documentation` | Yes | Structured content (see below) |
| `sources` | Yes | URLs with types (official_docs, github, tutorial, etc.) |
| `meta` | Yes | Metadata including contributors, timestamps, confidence |

### Documentation Sections

**All sections are optional** - only include what's relevant:

```json
{
  "documentation": {
    "summary": "2-3 sentence overview",
    "installation": {...},
    "endpoints": [...],      // Functions/methods
    "classes": [...],
    "examples": [...],       // Code examples
    "common_patterns": [...],
    "gotchas": [...],        // Common pitfalls
    "integration_points": [...]  // How it's used in LPR codebase
  }
}
```

**Key principle:** Structure over prose. Use arrays of objects, not paragraphs.

---

## Workflow

### 1. Check Index First

**Before creating new research**, check if it already exists:

```bash
# Read the index
cat .claude/research/_index.json | jq '.persistent[] | select(.library == "sam2")'
```

If found:
- Read the existing research file
- Check `meta.last_updated` - is it recent?
- Check `meta.confidence` - is it high?
- Decide: **Reuse**, **Supplement**, or **Replace**

### 2. Create Research Document

Use MCP tools (see `mcp-research` skill):
- **Context7** for official docs (preferred)
- **WebSearch** for examples, opinions, troubleshooting

Gather information in structured format per schema.

### 3. Set Confidence Score

Base confidence on source quality:

| Source Type | Confidence |
|-------------|-----------|
| Official docs (recent) | 0.9-1.0 |
| GitHub (official repo) | 0.8-0.9 |
| Tutorial (reputable source) | 0.7-0.8 |
| Stack Overflow | 0.6-0.7 |
| Blog post | 0.5-0.6 |

**Adjust down if:**
- Documentation is outdated
- Multiple conflicting sources
- Version mismatch

### 4. Add Integration Points

**Critical:** Link research to actual codebase usage.

```json
{
  "integration_points": [
    {
      "location": "producer/loader.py:45-80",
      "usage": "DataLoader with custom collate_fn for batch processing",
      "notes": "Using num_workers=4, prefetch_factor=2"
    }
  ]
}
```

This makes research **actionable** - developers know where/how it's used.

### 5. Update Index

After creating research file, update `.claude/research/_index.json`:

```json
{
  "persistent": [
    {
      "id": "sam2-batch-api",
      "path": "persistent/sam2-batch-api.json",
      "library": "sam2",
      "version": "2.1.0",
      "type": "api_reference",
      "topics": ["batch processing", "inference", "SAM2"],
      "last_updated": "2026-01-29T18:00:00Z",
      "confidence": 0.9,
      "source_count": 3
    }
  ]
}
```

Update `meta` section:
```json
{
  "meta": {
    "total_persistent": 6,  // Increment
    "libraries_covered": ["sam2", "torch", "cv2", ...]  // Add if new
  }
}
```

---

## Parallel Research Mode

When multiple researchers work on the same library:

### Section Ownership

Each researcher owns specific sections:

```json
{
  "meta": {
    "contributors": [
      {
        "agent_id": "res-001",
        "sections": ["endpoints", "examples"],
        "timestamp": "2026-01-29T18:00:00Z"
      },
      {
        "agent_id": "res-002",
        "sections": ["common_patterns", "gotchas"],
        "timestamp": "2026-01-29T18:05:00Z"
      }
    ],
    "merge_strategy": "section_ownership"
  }
}
```

**Rules:**
- Each agent writes ONLY to owned sections
- Read-only access to other sections (observable state)
- Last agent to finish runs consolidation pass

### Consolidation Pass

**If you're the last researcher to finish:**

1. **Deduplicate examples** - Remove duplicate code examples
2. **Link patterns to examples** - Reference example IDs in common_patterns
3. **Merge related sections** - Combine similar endpoints if needed
4. **Update meta:**
   ```json
   {
     "meta": {
       "merge_strategy": "consolidation",
       "last_consolidated": "2026-01-29T18:10:00Z"
     }
   }
   ```

---

## Related Research Links

Use `meta.related_research` to create a knowledge graph:

```json
{
  "meta": {
    "related_research": [
      "torch-dataloader",  // SAM2 uses PyTorch DataLoader
      "cv2-transforms"     // Preprocessing before SAM2
    ]
  }
}
```

**When to link:**
- Library A depends on Library B
- Library A is commonly used with Library B
- Similar concepts in both libraries

This helps researchers discover related context.

---

## Maintenance

### Updating Existing Research

When updating (new version, new features):

1. **Increment `meta.last_updated`**
2. **Preserve old version info** if breaking changes
3. **Add to `contributors` array** (don't replace)
4. **Adjust confidence** if source quality changed

### Deprecation

If library is no longer used in codebase:
- Don't delete (may be useful for reference)
- Add note to `manual_notes` in index
- Consider archiving to `.claude/research/archive/`

---

## Best Practices

### DO ✅

- **Check index first** - Avoid duplicate research
- **Structure over prose** - Use objects/arrays, not paragraphs
- **Add integration points** - Link to actual codebase usage
- **Set accurate confidence** - Based on source quality
- **Update index** - Keep index in sync with research files
- **Link related research** - Build knowledge graph

### DON'T ❌

- **Don't copy-paste docs** - Summarize and structure
- **Don't skip version info** - Always note version researched
- **Don't mix libraries** - One library per research file
- **Don't forget sources** - Always cite URLs
- **Don't write prose** - Use structured fields

---

## Example: SAM2 Batch API Research

```json
{
  "id": "sam2-batch-api",
  "library": "sam2",
  "version": "2.1.0",
  "type": "api_reference",
  "documentation": {
    "summary": "SAM2 provides batch inference API for processing multiple images efficiently. Supports CUDA batching with automatic memory management.",
    "endpoints": [
      {
        "name": "batch_predict",
        "signature": "batch_predict(images: List[np.ndarray], batch_size: int = 4) -> List[Mask]",
        "description": "Process multiple images in batches",
        "parameters": [
          {
            "name": "images",
            "type": "List[np.ndarray]",
            "required": true,
            "description": "List of input images in RGB format"
          },
          {
            "name": "batch_size",
            "type": "int",
            "required": false,
            "default": "4",
            "description": "Number of images to process simultaneously"
          }
        ],
        "returns": "List[Mask] - One mask per input image"
      }
    ],
    "examples": [
      {
        "title": "Basic batch inference",
        "code": "predictor = SAM2Predictor(...)\nresults = predictor.batch_predict(images, batch_size=8)",
        "description": "Process 8 images at a time for optimal GPU utilization"
      }
    ],
    "gotchas": [
      {
        "issue": "OOM errors with large batch_size",
        "solution": "Start with batch_size=4, increase gradually based on GPU memory",
        "example": "For 24GB GPU: batch_size=8 works well"
      }
    ],
    "integration_points": [
      {
        "location": "producer/ops_segment.py:120-150",
        "usage": "Batch segmentation of quality-filtered candidates",
        "notes": "Using batch_size=4 for P100 GPUs"
      }
    ]
  },
  "sources": [
    {
      "url": "https://github.com/facebookresearch/sam2",
      "type": "github",
      "title": "SAM2 Official Repository"
    }
  ],
  "meta": {
    "created_at": "2026-01-29T18:00:00Z",
    "last_updated": "2026-01-29T18:00:00Z",
    "confidence": 0.9,
    "related_research": ["torch-cuda", "cv2-transforms"]
  }
}
```

---

## Integration with Researcher Agent

Researcher agent loads this skill when lead spawns it for persistent research tasks.

**Agent flow:**
1. Load `persistent-research` skill
2. Check index for existing research
3. Use MCP tools to gather info (Context7 primary, WebSearch fallback)
4. Structure per schema
5. Write to `.claude/research/persistent/{id}.json`
6. Update `.claude/research/_index.json`
7. Send `task_complete` to lead with research ID
