---
name: git-history-analysis
description: Analyzes git history to answer specific questions for plan-architect or chunk-coder. Haiku-optimized with decision trees and consumer-specific output.
triggers:
  - mode: "git-history" in lead's request
load_condition: ONLY when lead explicitly sets mode="git-history"
---

# Git History Analysis Skill

> **Purpose**: Analyzes git history to answer specific questions, producing consumer-tailored (plan-architect or chunk-coder) context packets.
> **Consumers**: codebase-explorer (conditional: lead sets mode="git-history")
> **Schemas**: `git-history-analysis/schemas/history-context.schema.json`
> **Depends on**: None

## Contract

- ONLY load when lead's request contains `mode: "git-history"`
- ALWAYS follow consumer-specific output format (plan-architect vs chunk-coder)
- NEVER include raw command output in packets — synthesize findings
- Respect token budget: max 5 items per category, answers < 50 words
- Use targeted commands only — don't run all commands, pick based on questions

**Model**: Haiku (fast, limited context)
**Output**: Consumer-tailored, token-efficient packets

---

## CRITICAL: When to Load This Skill

**LOAD** when lead's request contains:
```json
{ "mode": "git-history", ... }
```

**DO NOT LOAD** for:
- Regular codebase exploration
- Feature context generation
- Query responses about current code

---

## Input Format

Lead provides:
```json
{
  "mode": "git-history",
  "feature": "batch-selection",
  "scope": "producer/",
  "consumer": "plan-architect | chunk-coder",
  "questions": [
    "What approaches were tried before?",
    "Any edge cases that caused issues?"
  ]
}
```

**Key fields:**
- `consumer`: WHO needs this (determines output format)
- `questions`: WHAT to find (focus your analysis)

---

## Decision Tree (Follow This)

```
START
  │
  ├─► Read input: scope, consumer, questions
  │
  ├─► Run targeted commands (see Command Menu below)
  │   Only run commands that help answer the questions
  │
  ├─► Check consumer type:
  │   │
  │   ├─► plan-architect
  │   │   Output: design_decisions, failed_approaches, constraints
  │   │   Focus: WHY things are the way they are
  │   │
  │   └─► chunk-coder
  │       Output: gotchas, edge_cases, patterns_to_follow
  │       Focus: WHAT to watch out for during implementation
  │
  └─► Write consumer-specific packet
      Return brief summary (< 50 words)
```

---

## Command Menu (Pick What You Need)

**Don't run all commands.** Pick based on questions asked.

| Question Type | Commands to Run |
|--------------|-----------------|
| "What changed?" | `git log --oneline -15 -- {scope}` |
| "Why was X done?" | `git log --grep="X" --oneline`, then `gh pr view {num}` |
| "Past issues?" | `gh issue list --search "{keyword}" --state all -L 5` |
| "Failed approaches?" | `git log --grep="revert\|fix\|bug" --oneline -- {scope}` |
| "Design decisions?" | `gh pr list --search "{feature}" -L 5`, then `gh pr view {num} --comments` |
| "Who worked on this?" | `git log --format="%an" -- {scope} \| sort \| uniq -c \| sort -rn` |

**Command syntax:**
```bash
# Recent commits (always start here)
git log --oneline -15 -- {scope}

# Find related PRs
gh pr list --search "{keyword}" --state all -L 5

# PR details with discussion
gh pr view {number} --comments

# Related issues
gh issue list --search "{keyword}" --state all -L 5

# Issue details
gh issue view {number}
```

---

## Consumer-Specific Output Formats

### For plan-architect

**Focus**: Design decisions, constraints, what NOT to do

**Output file**: `.claude/context/{feature}-history-{scope}.json`

```json
{
  "meta": {
    "type": "git-history",
    "consumer": "plan-architect",
    "feature": "{feature}",
    "scope": "{scope}",
    "created": "{timestamp}"
  },
  "answers": {
    "{question_1}": "{direct answer}",
    "{question_2}": "{direct answer}"
  },
  "for_planning": {
    "design_decisions": [
      {
        "decision": "Use quality-first sorting",
        "rationale": "PR #45: diversity without quality baseline = poor results",
        "source": "PR #45"
      }
    ],
    "failed_approaches": [
      {
        "approach": "Greedy selection",
        "why_failed": "Didn't handle duplicate scores",
        "evidence": "Reverted in commit abc123"
      }
    ],
    "constraints": [
      "Must handle empty input (caused bug #12)",
      "Batch size cannot exceed config limit"
    ],
    "related_prs": [45, 52],
    "related_issues": [12, 18]
  }
}
```

**Return to orchestrator:**
```
History analysis for plan-architect complete.

Key findings:
- 2 design decisions documented
- 1 failed approach to avoid
- 2 constraints from past bugs

Output: .claude/context/{feature}-history-{scope}.json
```

---

### For chunk-coder

**Focus**: Gotchas, edge cases, patterns to follow

**Output file**: `.claude/context/{feature}-history-{scope}.json`

```json
{
  "meta": {
    "type": "git-history",
    "consumer": "chunk-coder",
    "feature": "{feature}",
    "scope": "{scope}",
    "created": "{timestamp}"
  },
  "answers": {
    "{question_1}": "{direct answer}",
    "{question_2}": "{direct answer}"
  },
  "for_implementation": {
    "gotchas": [
      {
        "issue": "Empty input causes crash",
        "solution": "Add early return check",
        "evidence": "Fixed in PR #52"
      }
    ],
    "edge_cases_to_test": [
      "Empty candidate list",
      "Duplicate quality scores",
      "Batch size = 1"
    ],
    "patterns_to_follow": [
      {
        "pattern": "Use early return for empty collections",
        "example_file": "producer/ops_detection.py:45"
      }
    ],
    "avoid": [
      "Don't use greedy selection (tried and reverted)"
    ]
  }
}
```

**Return to orchestrator:**
```
History analysis for chunk-coder complete.

Key findings:
- 1 gotcha to handle
- 3 edge cases to test
- 1 pattern to follow

Output: .claude/context/{feature}-history-{scope}.json
```

---

## Workflow Example

**Input from orchestrator:**
```json
{
  "mode": "git-history",
  "feature": "batch-selection",
  "scope": "producer/",
  "consumer": "chunk-coder",
  "questions": [
    "Any edge cases that caused bugs?",
    "What patterns should I follow?"
  ]
}
```

**Step 1: Targeted commands**
```bash
# Find bug-related commits
git log --grep="fix\|bug" --oneline -10 -- producer/

# Find related issues
gh issue list --search "batch" --state closed -L 5
```

**Step 2: Follow up on findings**
```bash
# If found issue #12 about edge case
gh issue view 12

# If found PR #52 with fix
gh pr view 52 --comments
```

**Step 3: Write chunk-coder specific output**
- Focus on gotchas and edge_cases_to_test
- Don't include design_decisions (that's for planner)

**Step 4: Return brief summary**
```
History analysis for chunk-coder complete.
Key findings: 1 gotcha (empty input), 3 edge cases to test.
Output: .claude/context/batch-selection-history-producer.json
```

---

## Token Budget Rules (Haiku Limits)

1. **Max 5 significant items** per category
2. **Answers < 50 words each**
3. **Return summary < 50 words**
4. **Don't include raw command output** in packet
5. **Skip categories with no findings** (don't include empty arrays)

---

## No History Found

If scope has minimal/no relevant history:

```
No significant history found for {scope}.

Checked:
- Git log: {N} commits, none matching questions
- PRs: None found for "{feature}"
- Issues: None found

Recommendation: Proceed without history context.
```

**Don't create an output file** if no useful history found.

---

## Integration with Info Requests

Chunk-coder can request history mid-implementation by sending `info_request` to lead:

```python
SendMessage(to="lead", message={
  "type": "info_request",
  "payload": {
    "need": "codebase",
    "query": "What edge cases caused issues in producer/ops_batch.py?",
    "context": "Writing tests for chunk-02, want to cover past problem areas",
    "priority": "normal"
  }
})
```

Lead spawns codebase-explorer with git-history mode.

---

## Quick Reference: Consumer Needs

| Consumer | Needs | Doesn't Need |
|----------|-------|--------------|
| plan-architect | design_decisions, failed_approaches, constraints | implementation gotchas |
| chunk-coder | gotchas, edge_cases, patterns | high-level design rationale |

---

## Tool Selection: CLI vs GitHub MCP

**Default to CLI.** Most git history analysis is local operations where CLI is faster and doesn't need the network. Use GitHub MCP only when it provides a real benefit.

### Use CLI (`git` / `gh`) for:

| Task | Command | Why CLI |
|------|---------|---------|
| Commit history | `git log --oneline -15 -- {scope}` | Local, instant, no API calls |
| File changes | `git diff --stat HEAD~10..HEAD` | Local |
| Blame / authorship | `git blame {file}` | Local, no MCP equivalent |
| Branch comparison | `git log main..feature --oneline` | Local |
| Keyword search in commits | `git log --grep="fix" --oneline` | Local, fast |
| Search code on GitHub | `gh search code "pattern" --repo owner/repo` | Full search syntax |
| PR listing with filters | `gh pr list --search "keyword" --state all` | Richer search syntax than MCP |

### Use GitHub MCP for:

| Task | MCP call | Why MCP |
|------|----------|---------|
| PR details + review comments | `get_pull_request` + `get_pull_request_comments` | Structured JSON, avoids Bash tool overhead |
| Issue details | `get_issue` | Clean structured data |
| PR review threads | `get_pull_request_reviews` | No good CLI equivalent for structured review data |
| PR files changed | `get_pull_request_files` | Returns structured list with patch info |

**Rule of thumb:** Use `git` for anything local. Use `gh` CLI for search and listing. Use GitHub MCP when you need detailed structured data about a specific PR/issue you've already identified.
