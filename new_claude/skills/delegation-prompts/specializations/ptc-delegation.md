# PTC Delegation Specialization

Extends the base delegation prompt schema with a `ptc_hints` field for delegates that have PTC (Programmatic Tool Calling) access. Tells the delegate which files to load into the PTC namespace and what to extract, preventing full-file reads into the agent's context window.

## When to Add `ptc_hints`

| Condition | Add? |
|-----------|------|
| Delegate has PTC access AND files > 200 lines | Yes |
| Delegate needs specific functions/classes from large files | Yes |
| Delegate needs to compare data across multiple files | Yes |
| Simple reads of short files (< 200 lines) | No -- Read tool is simpler |
| Delegate needs most of a file (> 75%) | No -- Read tool is simpler |
| Task is pure writing (no analysis) | No -- PTC not needed |

## `ptc_hints` Schema Field

Add to the delegation prompt JSON alongside the base fields:

```json
{
  "task": "...",
  "known_context": { "..." },
  "output_contract": { "..." },

  "ptc_hints": {
    "files_to_load": [
      {
        "path": "/workspace/src/pipeline.py",
        "extract": "Pipeline class (lines 45-120), methods: run, validate, process_frame"
      },
      {
        "path": "/workspace/data/config.json",
        "extract": "Top-level keys: model_params, stage_config"
      }
    ],
    "namespace_variables": ["source", "tree"]
  },

  "environment": {
    "ptc_available": true,
    "tools": ["mcp__local-ptc__ptc_execute"]
  }
}
```

## Surgical Read Pattern

When the spawner has line coordinates from explorer context, include them in `ptc_hints.files_to_load` with specific line ranges. This keeps only the relevant lines in the agent's context instead of the full file.

**Include when:** Line range known, file > 200 lines, delegate needs < 25% of file.

```json
{
  "ptc_hints": {
    "files_to_load": [
      {
        "path": "/workspace/src/pipeline.py",
        "extract": "Lines 340-370 only (process_frame method). Use: lines = open(path).readlines(); print(''.join(lines[339:371]))"
      }
    ]
  }
}
```

The `lines` variable persists in the PTC namespace for follow-up reads from the same file.

## Surgical Edit Pattern

When the delegate needs to modify a specific function in a large file, include AST extraction guidance in the `extract` field:

```json
{
  "ptc_hints": {
    "files_to_load": [
      {
        "path": "/workspace/src/pipeline.py",
        "extract": "process_frame function at lines 340-370. Use ast.parse + ast.walk to extract FunctionDef node, then ast.unparse to see current implementation"
      }
    ]
  }
}
```

**Include when:** Modifying a known function/class in a file > 300 lines, edit is mechanical.

## Explorer Context to PTC Translation

When populating `ptc_hints` from `.claude/context/*.json`, translate structure blocks directly:

| Explorer Context Field | `ptc_hints` Mapping |
|-----------------------|---------------------|
| `structure.blocks[].file` | `files_to_load[].path` (make absolute) |
| `structure.blocks[].class` | `files_to_load[].extract` = "class {class}" |
| `structure.blocks[].line` | `files_to_load[].extract` += " at line {line}" |
| `structure.blocks[].methods` | `files_to_load[].extract` += ", methods: {methods}" |
| `touchpoints[].file` + `touchpoints[].line` | Surgical read entry |

**PTC composition example** (use when spawner has context packets):

```python
import json

with open("/workspace/.claude/context/pipeline-context.json") as f:
    ctx = json.load(f)

ptc_hints = {
    "files_to_load": [
        {
            "path": f"/workspace/{block['file']}",
            "extract": f"class {block.get('class', '?')} at line {block.get('line', '?')}, "
                       f"methods: {', '.join(block.get('methods', []))}"
        }
        for block in ctx.get("structure", {}).get("blocks", [])
        if block.get("class")  # only class-level blocks
    ],
    "namespace_variables": []
}

print(json.dumps(ptc_hints, indent=2))
```

## What NOT to Include in `ptc_hints`

**Do NOT tell the delegate HOW to structure PTC calls.** Evidence from the V1/V4 skill experiment: prescriptive PTC call structure reduced accuracy by -0.779 (V1) and -0.818 (V4). Give WHAT to extract, not HOW to orchestrate.

| Include | Do NOT Include |
|---------|---------------|
| Which files to load (`files_to_load`) | Step-by-step PTC call sequence |
| What to extract (`extract` field) | "In your first PTC call, do X. In your second, do Y." |
| Namespace variables to reuse | "Always use namespace continuity" |
| Line ranges for surgical reads | "Never re-read a file" |

The delegate knows PTC patterns from its own skill loading. Your job is to give it targets, not micromanage its tool usage.
