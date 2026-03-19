# Anti-Rationalization Architecture

Discipline-enforcing skills need three interlocking defense layers. Built from real agent failures, not hypothetical scenarios.

## Layer 1 — Rationalization Table

Two-column table mapping every observed excuse to a reality check. Each row is captured **verbatim** from agents under pressure testing — not invented.

```markdown
| Excuse | Reality |
|--------|---------|
| "Too simple to test" | Simple code breaks. Test takes 30 seconds. No exceptions. |
| "I'll test after" | Tests passing immediately prove nothing. Test-first forces you to see the failure. |
| "Keep as reference, write tests first" | You'll adapt it. That's testing after. Delete means delete. |
```

### Building the Table

1. Run pressure scenarios WITHOUT the skill (RED phase)
2. Capture exact rationalizations the agent produces — verbatim, not paraphrased
3. For each rationalization, write a reality check that closes the loophole
4. Use concrete consequences: "You'll adapt it" not "This may lead to issues"

### Effective Reality Checks

- **Short and absolute** — no hedging ("No exceptions" not "Generally avoid")
- **Consequence-focused** — what breaks, not what's theoretically wrong
- **Close the specific loophole** — each reality check addresses exactly one excuse

## Layer 2 — Red Flags List

Behavioral warning signs that mean the agent is about to violate the rule. These are observable ACTIONS, not just words:

```markdown
## Red Flags — STOP and Start Over
- Code written before test
- Test passes immediately on first run
- "I already manually tested it"
- "This is different because..."
- "probably", "should work", "assuming"
- "Done!" without verification evidence

**ALL of these mean: STOP. Return to the process.**
```

### Red Flag Categories

| Category | Examples |
|----------|----------|
| **Premature completion** | "Done!" without evidence, skipping verification |
| **Hedge words** | "probably", "should work", "assuming", "I think" |
| **Exception-seeking** | "This is different because...", "In this case..." |
| **Order violations** | Code before test, output before validation |
| **Appeal to efficiency** | "This would be faster if...", "To save time..." |

### Placement

Red flags go AFTER the rationalization table — the agent sees the excuses first (pre-emptive), then the behavioral signs (real-time detection).

## Layer 3 — Foundational Principle

A single sentence placed early in the skill (typically right after Core Principle) that pre-emptively closes the "spirit vs letter" escape hatch:

```markdown
**Violating the letter of the rules is violating the spirit of the rules.**
```

### Why This Works

This was added after pressure testing showed agents saying "I'm following the spirit of TDD." It cuts off an entire category of rationalization — any attempt to reinterpret or partially follow the rules.

Without this principle, agents commonly rationalize:
- "The spirit of TDD is testing, so testing after is fine"
- "The spirit of verification is confidence, and I'm confident"
- "I'm following the intent, just not the exact steps"

With the principle, the agent cannot separate "intent" from "process" — following the process IS the intent.

## How the Three Layers Interlock

```
Foundational Principle (early in skill)
  → Pre-emptively closes "spirit vs letter" escape hatch
  → Agent reads this before encountering any scenario

Rationalization Table (in Anti-Rationalization section)
  → Pre-emptive defense against specific known excuses
  → Agent recognizes its own rationalization in the table

Red Flags (after rationalization table)
  → Real-time detection of violation behavior
  → Agent checks its own actions against the list
```

Each layer catches what the previous one misses:
- Foundational Principle catches category-level evasion ("spirit vs letter")
- Rationalization Table catches specific excuse patterns
- Red Flags catch behavioral drift even when the agent doesn't explicitly rationalize

## Building Anti-Rationalization for a New Skill

1. **Run RED phase** — pressure test without the skill
2. **Capture verbatim** — exact rationalizations the agent produced
3. **Write reality checks** — concrete, absolute, consequence-focused
4. **Identify behavioral patterns** — what did the agent DO (not just say)?
5. **Add foundational principle** — "Violating the letter IS violating the spirit"
6. **Re-test** — run same scenario WITH the three layers
7. **Iterate** — new rationalizations → new table entries → re-test

Typical: 4-6 RED-GREEN-REFACTOR iterations to bulletproof a discipline skill, uncovering 8-12 unique rationalizations.
