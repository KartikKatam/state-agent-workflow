# Handoff Patterns

Proven handoff structures for each handoff type. Use these templates as starting points — adapt to your specific situation.

**Output path:** Write handoff documents to `.claude/handoffs/{agent-id}.json` — the Pydantic validator (`schemas/handoff.py`, enforced by `hooks/utils/schema_validator.py`) triggers on this path and validates structural field presence automatically.

## Context Pressure Handoff Template

The most common handoff. Infrastructure detects the threshold and forces the handoff; your job is to write a document the successor can act on immediately.

```
## Handoff: {feature} - {chunk/task}
**Type:** Context pressure
**Agent:** {role} ({model})
**Timestamp:** {ISO}

### Resume Point
Phase: {exact phase name}
Step: {exact step within phase}
Next action: {specific next thing to do — function name, test name, file to modify}

### Completed Work
- {file1}: {what was done — "created with select_batch() and score_diversity() implemented and tested"}
- {file2}: {what was done}

### In-Flight (Incomplete)
- {description of partially complete work and its current state}
- Sub-agents: {pending/none — if pending, what they were doing}

### Decisions Made
| Decision | Reason | Source | Locked? |
|----------|--------|--------|---------|
| {what was decided} | {why} | {user/plan/evidence} | {yes if user preference, no if judgment call} |

### User Preferences
- {preference}: {context where it was expressed}

### Pending Decisions
- {unresolved question}: {context and any partial discussion}

### Failed Approaches
- {approach}: {why it failed — 2 sentences max}

### Key Files Read
- {file path}: {what was extracted from it}
```

### Compression Techniques for Context Pressure

When context is tight, compress information without losing recoverability:

**1. Reference by path, don't inline content:**
```
WRONG: "The plan says to implement three functions: select_batch which takes
       a list of candidates and returns...(50 lines of plan content)"
RIGHT: "Plan at .claude/plans/lpr-plan.json, phase 2, task 03. Covers
        select_batch, score_diversity, apply_filters."
```

**2. Decisions as key-value pairs, not narratives:**
```
WRONG: "After considering several approaches and discussing with the user,
        we decided that composition would be better than inheritance because
        the user mentioned they prefer..."
RIGHT: "composition over inheritance | user preference chunk-01 | LOCKED"
```

**3. File state as diffs from plan, not full descriptions:**
```
WRONG: "Created producer/ops_batch.py with a class BatchProcessor that has
        methods select_batch and score_diversity. select_batch takes a list..."
RIGHT: "producer/ops_batch.py: BatchProcessor created. select_batch + score_diversity
        done per plan. apply_filters NOT started."
```

## Auditor Scrap Handoff Template

The predecessor's approach was fundamentally rejected. The fresh coder needs to understand WHAT went wrong — not HOW to fix it.

```
## Scrap Handoff: {feature} - {task}
**Type:** Auditor scrap
**Scrapped agent:** {role}
**Auditor:** {auditor identifier}

### Auditor Assessment (verbatim)
{Copy the auditor's full scrap assessment here — do not paraphrase.
 The fresh coder needs the auditor's exact words to understand
 what standard they're being held to.}

### Root Cause Analysis
{Your honest analysis of why the approach failed. Focus on the
 structural/architectural mistake, not surface-level code issues.}

### What Was Tried
- Approach: {what you implemented}
- Files created: {list}
- Tests written: {list — some may be salvageable if the test logic is correct}

### What NOT to Repeat
{Specific patterns or decisions that led to the scrap. The fresh
 coder should understand the dead ends WITHOUT being told where to go.}

### Task Context
Plan: {path to plan}
Task: {task ID and title}
Reference files: {from task spec}
```

**Key principle:** Do NOT include "my suggested approach for the next coder." Your approach was scrapped — your suggestions carry the same flawed assumptions. The fresh coder should read the task spec fresh and form their own approach informed by what NOT to do.

## Chunk Boundary Handoff Template

Chunk N is complete. Chunk N+1's coder needs continuity — decisions, preferences, and patterns established during earlier chunks.

```
## Chunk Continuity: {feature} chunk-{N} → chunk-{N+1}
**Completed by:** {agent}

### Decisions That Carry Forward
| Decision | Reason | Override? |
|----------|--------|-----------|
| {decision} | {reason — cite user preference, plan, or evidence} | User override only |

### User Preferences
| Preference | Context |
|-----------|---------|
| {e.g., early returns over nested conditionals} | {when user expressed this} |

### Patterns Established
- {naming convention established in chunk-01}
- {error handling pattern used throughout}
- {test structure pattern}

### Deviations from Plan
| Deviation | Type | Reason | Impact |
|-----------|------|--------|--------|
| {what deviated} | {scope_addition/modification} | {why} | {low/medium/high} |

### Files Created/Modified
{List with brief description of what each file contains}
```

## Session End Handoff Template

User pauses or session terminates. The handoff must support resumption from zero context — a fresh session with no conversation history.

```
## Session State: {feature}
**Saved at:** {timestamp}
**Resume with:** "resume workflow {feature}"

### Workflow Progress
| Phase | Status | Details |
|-------|--------|---------|
| Exploration | {complete/partial/not started} | {brief detail} |
| Planning | {complete/partial/not started} | {brief detail} |
| Implementation | {complete/partial/not started} | {which chunks done} |
| Review | {complete/partial/not started} | {brief detail} |

### Active State
- Current phase: {phase}
- Current task: {task ID and description}
- Agent status: {who's running, who's idle}

### Critical Context
- Plan: {path}
- Context packets: {paths}
- Research: {paths if any}

### Unfinished Business
- {anything in progress that needs attention on resume}

### User Preferences (session-wide)
- {preferences expressed during this session}
```

## When to Use Each Template

| Trigger | Template | Priority Fields |
|---------|----------|-----------------|
| Context at threshold | Context Pressure | Resume point, in-flight state, failed approaches |
| Task done with successor | Chunk Boundary (if same feature) or minimal "task complete" log entry | Decisions, preferences |
| Auditor says "scrap" | Auditor Scrap | Verbatim assessment, root cause, what NOT to repeat |
| Chunk complete | Chunk Boundary | All carry-forward fields |
| User says "stop" / session timeout | Session End | Full workflow state, everything for cold resume |
