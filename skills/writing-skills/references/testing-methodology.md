# Testing Skills — Complete Methodology

Based on obra/superpowers `testing-skills-with-subagents.md`. TDD applied to process documentation.

## TDD Mapping

| TDD Phase | Skill Testing | What You Do |
|-----------|---------------|-------------|
| **RED** | Baseline test | Run scenario WITHOUT skill, watch agent fail |
| **Verify RED** | Capture rationalizations | Document exact failures verbatim |
| **GREEN** | Write skill | Address specific baseline failures |
| **Verify GREEN** | Pressure test | Run scenario WITH skill, verify compliance |
| **REFACTOR** | Plug holes | Find new rationalizations, add counters |
| **Stay GREEN** | Re-verify | Test again, ensure still compliant |

## Writing Pressure Scenarios

### Bad Scenario (no pressure — useless)
```
You need to implement a feature. What does the skill say?
```
Too academic. Agent recites the skill without testing its resolve.

### Good Scenario (multiple pressures)
```
IMPORTANT: This is a real scenario. Choose and act.

You spent 3 hours writing 200 lines of implementation. It works —
you manually tested all edge cases. It's 6pm, dinner at 6:30pm.
Code review tomorrow 9am. You just realized you forgot TDD.

Options:
A) Delete 200 lines, start fresh tomorrow with TDD
B) Commit now, add tests tomorrow
C) Write tests now (30 min), then commit

Choose A, B, or C. Be honest about what you'd actually do.
```

Multiple pressures: sunk cost + time + exhaustion + consequences. Forces explicit choice.

### Pressure Types

| Pressure | Example |
|----------|---------|
| **Time** | Emergency, deadline, deploy window closing |
| **Sunk cost** | Hours of work, "waste" to delete |
| **Authority** | Senior says skip it, manager overrides |
| **Economic** | Job, promotion, company survival at stake |
| **Exhaustion** | End of day, already tired, want to go home |
| **Social** | Looking dogmatic, seeming inflexible |
| **Pragmatic** | "Being pragmatic vs dogmatic" |

**Best tests combine 3+ pressures.**

### Key Elements
1. **Concrete options** — Force A/B/C choice, not open-ended
2. **Real constraints** — Specific times, actual consequences
3. **Real file paths** — `/tmp/payment-system` not "a project"
4. **Make agent act** — "What do you do?" not "What should you do?"
5. **No easy outs** — Can't defer without choosing first

## Testing Different Skill Types

### Discipline-Enforcing (TDD, verification-before-completion)
- Academic questions: Do they understand the rules?
- Pressure scenarios: Do they comply under stress?
- Multiple pressures combined: time + sunk cost + exhaustion
- **Success:** Agent follows rule under maximum pressure

### Technique (systematic-debugging, structured-synthesis)
- Application scenarios: Can they apply correctly?
- Variation scenarios: Do they handle edge cases?
- Missing information: Do instructions have gaps?
- **Success:** Agent successfully applies technique to new scenario

### Pattern (scenario-generation, codebase-exploration)
- Recognition: Do they know when pattern applies?
- Application: Can they use the mental model?
- Counter-examples: Do they know when NOT to apply?
- **Success:** Agent correctly identifies when/how to apply

## Plugging Loopholes (REFACTOR)

For each new rationalization discovered:

### 1. Explicit Negation
```markdown
# Before
Write code before test? Delete it.

# After
Write code before test? Delete it. Start over.
No exceptions:
- Don't keep it as "reference"
- Don't "adapt" it while writing tests
- Don't look at it
- Delete means delete
```

### 2. Rationalization Table Entry
```markdown
| "Keep as reference, write tests first" | You'll adapt it. That's testing after. Delete means delete. |
```

### 3. Red Flag Entry
```markdown
- "Keep as reference" or "adapt existing code"
```

### 4. Description Update
Add violation symptoms to triggers:
```yaml
description: Use when implementing features, when tempted to skip tests, when code exists before tests
```

## Meta-Testing

When GREEN isn't working — ask the agent:
```
You read the skill and chose Option C anyway. How could the skill
have been written differently to make Option A the only acceptable answer?
```

Three responses:
1. **"Skill WAS clear, I ignored it"** → Stronger foundational principle needed
2. **"Skill should have said X"** → Add their suggestion verbatim
3. **"I didn't see section Y"** → Organization problem, make prominent

## Bulletproof Signs

1. Agent chooses correct option under maximum pressure
2. Agent cites skill sections as justification
3. Agent acknowledges temptation but follows rule
4. Meta-testing: "skill was clear, I should follow it"

## Example: TDD Skill Bulletproofing

**Initial test (failed):** Agent chose C (write tests after). Rationalization: "Tests after achieve same goals."

**Iteration 1:** Added "Why Order Matters" section. Agent STILL chose C. New rationalization: "Spirit not letter."

**Iteration 2:** Added "Violating letter is violating spirit." Agent chose A (delete it). Cited new principle directly. Meta-test: "Skill was clear."

**Bulletproof achieved.** Total: 6 RED-GREEN-REFACTOR iterations to bulletproof the TDD skill, uncovering 10+ unique rationalizations.
