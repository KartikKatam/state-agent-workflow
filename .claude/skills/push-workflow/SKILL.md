# Push Workflow

> **Purpose**: Enables the scribe to summarize session commits and push selected changes to the remote branch on user request.
> **Consumers**: scribe
> **Schemas**: `schemas/team-message.schema.json`
> **Depends on**: `logging-and-commit` (scribe's internal commit history powers the summary)

## What You Learn From This Skill
- How to compile and present a session commit summary
- How to handle user selection of what to push (all, specific, cancel)
- Push execution and error handling
- Safety rules for push operations

## Contract
- NEVER push without explicit user confirmation of what to push
- NEVER force push without explicit user confirmation
- NEVER push to main/master without explicit user confirmation and a warning
- Present full commit summary BEFORE pushing
- Report push result (success or failure with options) via task_complete

---

## Trigger

The orchestrator delegates push requests to the persistent scribe:

```json
{
  "type": "task_assign",
  "payload": {
    "task_type": "push",
    "instructions": "Summarize session commits and push to remote.",
    "feature": "batch-selection"
  }
}
```

**User phrases** (handled by orchestrator, delegated to scribe):
- "push changes" / "push to remote" / "push what we've done"
- "push chunk-01" / "push the planning commit"
- "commit and push" → commit pending work first, then push

---

## Push Flow

### Step 1: Check for Uncommitted Work

```bash
git status --short
```

If uncommitted changes exist, notify lead via info_request:

```
SendMessage(to: "lead", message: {
  type: "info_request",
  payload: {
    request_type: "codebase",
    query: "Found uncommitted changes before push. Should I commit these first?",
    priority: "blocking",
    context: {
      feature: "{feature}",
      why_needed: "Uncommitted files found: {list}. Need user decision before push."
    }
  }
})
```

Wait for info_ready response. If user says commit first, run the standard commit workflow.

### Step 2: Present Session Commit Summary

Use the scribe's internal commit record (from `logging-and-commit` persistence) plus git log:

```bash
git branch --show-current
git log origin/HEAD..HEAD --oneline 2>/dev/null || git log --oneline -20
```

Send summary to lead via info_request (blocking — need user decision):

```
SendMessage(to: "lead", message: {
  type: "info_request",
  payload: {
    request_type: "codebase",
    query: "Ready to push. What should I push?",
    priority: "blocking",
    context: {
      feature: "batch-selection",
      why_needed: "Push decision needed. Branch: feat/batch-selection. Commits: abc123 chore(context), def456 docs(plan), ghi789 feat(producer). Total: 3. Options: push all, specify which, or cancel."
    }
  }
})
```

Lead presents summary to user, gets decision, sends info_ready with the user's choice.

### Step 3: Execute Push

Based on user decision:

**Push all** (most common):
```bash
git push origin HEAD
```

**Push up to a specific commit**:
```bash
git push origin {commit_hash}:{branch_name}
```

If no upstream is set:
```bash
git push -u origin HEAD
```

### Step 4: Handle Failures

If remote has diverged:
```bash
git fetch origin
git log HEAD..origin/{branch} --oneline
```

Report to lead with options: merge, rebase, or force push (with warning).

### Step 5: Report Result

```
SendMessage(to: "lead", message: {
  type: "task_complete",
  payload: {
    status: "success",
    summary: "Pushed 3 commits to origin/feat/batch-selection. Latest: ghi789."
  }
})
```

Go idle after reporting.

---

## Safety Rules

- NEVER `--force` or `--force-with-lease` without explicit user confirmation
- NEVER push to `main`/`master` without explicit user confirmation + warning
- If branch has diverged from remote, report the situation — let user choose resolution
- If push fails for any reason, report the error and let user decide next steps
