---
name: handoff-protocol
version: 1-0-0
triggers:
  - agent_role: "*"
    conditions: when context pressure triggers, task completes with successor needed, auditor scraps, chunk boundary reached, session ends, or when spawned as a continuation/successor agent
description: >
  Use when an agent must transfer work to a successor or persist state
  for later resumption, OR when spawned as a continuation agent that
  must load and apply predecessor state. Activates for: context pressure
  handoff, task completion with successor, auditor scrap, chunk boundary,
  session end, continuation agent startup, chunk-N startup (N > 1).
  Do NOT use for: normal task communication (team messaging), status
  updates without agent replacement, saving intermediate results during
  active work (that's normal file writing).
---

# Handoff Protocol

## Core Principle

**The receiving agent must be able to continue without asking questions.** Every handoff is a one-way information transfer — the predecessor will be terminated. If the successor re-discovers decisions, re-reads files, or guesses the resume point, the handoff failed.

**Violating the letter of the rules is violating the spirit of the rules.**

<HARD-GATE>
**Sender:** Do NOT signal ready-for-shutdown or send `needs_replacement: true`
until you have written a handoff document to the designated file path.

**Receiver:** Do NOT begin new work until you have loaded the handoff document,
applied predecessor decisions as LOCKED, and acknowledged carry-forward state
to the user/orchestrator.
</HARD-GATE>

## Quick Reference

**Route to your section — read ONLY what applies to your role:**

| You are... | Read... |
|-----------|---------|
| **Handing off** (outgoing agent under context pressure, completing task, scrapped) | Sender Protocol → Critical Rules → Anti-Rationalization |
| **Successor** (continuation agent, chunk-N where N>1) | Receiver Protocol → Critical Rules |

| Handoff Type | File Location | Key Priority |
|-------------|---------------|--------------|
| Context pressure | Session log handoff fields | Resume point + in-flight state |
| Task completion | Session log | Decisions, preferences, deviations |
| Auditor scrap | Session log + scrap notes | What failed and WHY — not how to "fix" |
| Chunk boundary | Session log | Decisions that carry forward |
| Session end | `.claude/temp/{agent}-state.json` | Everything for cold resume |

---

## Sender Protocol

Complete in-flight operations before writing. Abandoning running sub-agents, partial file writes, or half-implemented functions wastes tokens already spent and leaves the successor with ambiguous state. Finish what's in progress, don't start anything new.

### What to Capture

The successor has: the plan, context packets, and your handoff document. They do NOT have: your conversation history, your mental model, or what you tried and rejected.

| Category | What to Record | WHY |
|----------|---------------|-----|
| **Resume point** | Exact phase, step, and what's next | Successor skips completed work |
| **Decisions made** | Each decision + rationale + source (user / plan / evidence) + locked? | Prevents re-deciding and decision drift |
| **User preferences** | Style/approach preferences the user expressed | User shouldn't repeat themselves |
| **Pending decisions** | Unresolved questions that were in progress | Successor presents these instead of guessing |
| **Files modified** | Every file changed, with what was done and current state | Successor knows what exists |
| **Key files read** | Context files, reference files, plan sections consumed | Successor loads same context without guessing |
| **What didn't work** | Approaches tried and abandoned, with why (2 sentences each) | Successor avoids repeating failed paths |
| **In-flight state** | Partially complete operations and their current state | Successor completes rather than duplicates |

**"What didn't work" is not optional.** Every omitted failed approach costs the successor 5-30 minutes of re-discovery. This is the highest-ROI field in any handoff document.

### Information Priority

Write top-to-bottom in this order — successors under context pressure need actionable information first:

1. **Where to resume** — phase, step, what's next
2. **What's done** — completed work, files created/modified with state
3. **Active decisions** — locked choices the successor must follow
4. **What to avoid** — failed approaches, known dead ends
5. **Context loaded** — files and sections already consumed
6. **Background** — anything else useful but not critical

### Type-Specific Rules

| Type | Special requirement | WHY |
|------|-------------------|-----|
| **Context pressure** | Document in-flight state explicitly — no "I was about to..." | Either finish it or record it as not-started. Ambiguous state wastes successor time. |
| **Auditor scrap** | Preserve auditor assessment **verbatim**. Do NOT include fix suggestions. | Paraphrasing loses the specific critique. Fix suggestions carry the flawed mental model that caused the scrap. |
| **Chunk boundary** | Include ALL decisions, not just "important" ones. Capture HOW user wants things done. | Minor decisions compound. "Unimportant" naming choices become patterns the successor must follow. |

### Write → Validate → Signal

Infrastructure validates handoff documents automatically — the `Handoff` Pydantic model (`schemas/handoff.py`) is validated by `hooks/utils/schema_validator.py` when you write to `.claude/handoffs/{agent-id}.json`. Structural field presence is enforced by Pydantic. The skill teaches what infrastructure cannot validate: semantic quality of resume instructions, usefulness of failed_approaches entries, and whether work_summary actually helps the successor.

After writing and validating: signal `needs_replacement`. Never signal before writing — if you crash after signaling but before writing, the successor gets nothing.

---

## Receiver Protocol

Infrastructure handles the mechanical parts: session log parsing, status→state routing, injecting the handoff document path at startup. This section teaches the judgment that infrastructure cannot enforce.

### Apply Predecessor State

1. **Load `key_files_read`** from the handoff document FIRST — don't burn context re-discovering what's already documented. The predecessor listed exactly which files matter.

2. **Treat `decisions_made` as LOCKED.** These are not suggestions. If the source is `user_preference` or `plan`, the decision is non-negotiable unless the user explicitly overrides. Even if you'd do it differently — the predecessor's decision was made with context you don't have.

3. **Follow `user_preferences`** — the user already told a previous agent how they want things done. Repeating the conversation wastes their time and yours.

4. **Present `pending_decisions` to the user** — these are unresolved questions. Do NOT answer them yourself. The predecessor left them pending because they require user input.

5. **Read `what_didnt_work`** — each entry is a dead end the predecessor already explored. Do not retry these approaches unless you have specific new evidence that changes the outcome.

### Acknowledge to User

The user talks to multiple agents across a feature. They need to know what YOU know:

```
Resuming {task} from {resume_point}.

Carrying forward:
- {decision_1} (source: {user/plan/evidence})
- {preference_1}

Pending decisions for your input:
- {pending_1}

Continuing with: {next step}
```

WHY acknowledge: Without this, the user doesn't know if you loaded the handoff correctly. They'll repeat instructions or worry about decision drift.

### Document Overrides

If the user overrides a predecessor's decision, record it immediately:

```json
{
  "decision": "switched from composition to inheritance for BatchProcessor",
  "reason": "user override — inheritance better for new requirement",
  "overrides": "chunk-01 decision",
  "source": "user_preference"
}
```

The `overrides` field creates a traceable chain. Future agents see both the original decision and the override with rationale.

### Don't Redo, Don't "Improve"

Do not re-implement completed work. Do not refactor predecessor code that works. The successor's job is to continue from the resume point, not to rewrite what exists. If predecessor code has issues, document them as deviations — don't silently fix.

---

## Critical Rules

- **Specific resume points, not vague summaries.**
  "Continue implementing BatchProcessor" is useless. "Implemented `select_batch()` and `score_diversity()` with passing tests. Next: `apply_filters()` per requirement R-04" gives a starting line.

- **Decisions record source and lock status.**
  "Used composition — user preferred it in chunk-01 — LOCKED" vs "I chose composition." The successor needs to know if a decision is negotiable.

- **Scrap handoffs describe the problem, not the solution.**
  The predecessor's mental model was rejected. Suggesting fixes perpetuates it. Include the auditor's exact words and your root cause analysis. The fresh coder forms their own approach.

- **Handoff before signaling, always.**
  Write → validate → signal. Never the reverse.

- **Receiver: LOCKED means LOCKED.**
  "The predecessor's approach doesn't match how I'd do it" is not grounds for overriding. Only the user can unlock a locked decision.

## Anti-Rationalization

| Excuse | Reality |
|--------|---------|
| "The session log already has enough info" | Session logs track status. Handoff documents explain WHY and WHAT'S NEXT. Different purposes. |
| "My failed approaches aren't worth documenting" | You spent tokens discovering they don't work. The successor WILL try them again without this. |
| "I'm almost done, I'll skip the handoff and just finish" | If you were almost done, you wouldn't be handing off. Context pressure is non-linear — the last 30% fills faster. Write the handoff NOW. |
| "I'll write a brief handoff and the successor can ask questions" | Successors can't ask you — you'll be terminated. One-way transfer. Make it complete. |
| "The scrap notes should include my suggested fix" | Your fix was scrapped. Including it biases the fresh coder toward the same wrong approach. |
| "The predecessor's decisions don't apply to my approach" | They're LOCKED. User or plan decided, not predecessor opinion. Override requires user consent. |
| "I'll re-read the codebase myself for a fresh perspective" | Load `key_files_read` from handoff first. Re-discovering documented context wastes your context window. |
| "I can skip the acknowledgment, the user knows what's going on" | User talks to multiple agents. They need to know what YOU know. Acknowledge. |

**Red Flags — STOP:**
- Sending `needs_replacement` before writing the handoff file
- Resume point says "continue where I left off" (non-specific)
- Handoff omits failed approaches
- Scrap handoff includes implementation suggestions
- Decisions listed without rationale or lock status
- Successor re-deciding a LOCKED decision without user approval
- Successor skipping the acknowledgment message
- Successor retrying an approach listed in "what didn't work"

## References

For handoff document templates per type (context pressure, scrap, chunk boundary, session end) and compression techniques (reference-by-path, key-value decisions, diff-from-plan), read `references/patterns.md`.

For common handoff failures with WRONG/RIGHT examples (12 patterns), read `references/anti-patterns.md`.
