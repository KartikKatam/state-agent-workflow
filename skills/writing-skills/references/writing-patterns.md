# Writing Patterns for Skill Content

Techniques for writing skill content that agents actually follow.

## Explain the "Why" — Don't Just List Steps

Today's LLMs generalize from reasoning but skip arbitrary-seeming commands. Pair every non-obvious instruction with its consequence.

```markdown
# BAD — arbitrary command, agent will skip under pressure
ALWAYS validate the output file before returning it to the user.

# GOOD — explained reasoning, agent internalizes
After generating the file, validate with `python scripts/validate.py output.docx`.
Validation catches silent corruption (malformed XML, missing relationships) that
would cause the file to fail when opened. Skipping = user gets a broken file
with no error message.
```

Pattern: **instruction** + **what breaks if skipped**.

## Decision Tables Over Prose

Agents parse tables more reliably than paragraphs. Convert any "if X then Y" logic into a table:

```markdown
# BAD — buried in prose
For simple tasks, follow the plan exactly. For moderate tasks, the tests
should be prescriptive but the implementation can vary. For complex tasks,
research first before starting TDD.

# GOOD — scannable table
| Situation | Action |
|-----------|--------|
| Simple/mechanical task | Follow prescriptive plan exactly |
| Moderate complexity | Tests are prescriptive, implementation is open |
| High complexity | Research first, then TDD |
```

Tables also prevent agents from cherry-picking one option — they see all options together.

## Show Wrong Then Right

Anti-patterns with explicit markers teach boundaries more effectively than positive examples alone:

```markdown
// WRONG — agent sees what NOT to do
new Paragraph({ children: [new TextRun("• Item")] })

// RIGHT — agent sees the correct approach
const doc = new Document({
  numbering: { config: [{ levels: [{ format: LevelFormat.BULLET }] }] }
});
```

### Rules for WRONG/RIGHT Pairs

1. **WRONG first** — agent sees the trap before the solution
2. **Explain why it's wrong** — comment or inline annotation
3. **RIGHT immediately after** — no other content between them
4. **Real code, not pseudocode** — agents learn better from concrete examples
5. **One concept per pair** — don't combine multiple lessons

## Conciseness Is Key

The context window is a shared resource. Every token in a skill competes with the agent's actual work.

- **Default assumption:** Claude is already very smart. Only add context it doesn't have.
- **Challenge each paragraph:** "Does this justify its token cost?"
- **Move details to tool help:** `scripts/validate.py --help` instead of documenting all flags in the skill
- **Use cross-references:** Don't repeat content that lives in another skill
- **One excellent example beats many mediocre ones**

### Token Budget Awareness

| Content Type | Typical Token Cost | Justification Threshold |
|-------------|-------------------|------------------------|
| Decision table | 50-100 tokens | Almost always justified — high info density |
| WRONG/RIGHT pair | 100-200 tokens | Justified if the mistake is common |
| Prose paragraph | 50-150 tokens | Must teach something Claude doesn't already know |
| Full code example | 200-500 tokens | Justified for complex patterns only |
| Rationalization table entry | 20-40 tokens | Always justified for discipline skills |

## Keep References One Level Deep

Claude may partially read nested references. All reference files link directly from SKILL.md — never chain `references/a.md` → `references/b.md`.

```markdown
# GOOD — flat structure
SKILL.md → references/patterns.md
SKILL.md → references/anti-patterns.md
SKILL.md → references/edge-cases.md

# BAD — nested references
SKILL.md → references/patterns.md → references/advanced-patterns.md
```

## Structural Dependencies for Step Sequencing

The strongest non-automated enforcement (level 5) is making steps depend on outputs from previous steps:

```markdown
### Step 1: Analyze the form
Run: `python scripts/analyze_form.py input.pdf`
This creates `fields.json` that Step 2 operates on.

### Step 2: Create field mapping
Edit `fields.json` — Step 1 must complete first, the file doesn't exist yet.

### Step 3: Generate output
Run: `python scripts/generate.py fields.json`
Uses the mapping from Step 2. Cannot run without it.
```

The agent literally cannot skip to Step 3 — the input file doesn't exist until Step 1 runs. This is enforcement through structure, not instruction.

## Collected Rules vs Scattered Rules

**Never scatter critical rules throughout the document.** Agents miss them.

```markdown
# BAD — rules scattered in different sections
## Formatting
When formatting output, never use \n...

## Data Processing
Always validate before returning...

## Edge Cases
Never use unicode bullets...

# GOOD — collected in one section
## Critical Rules
- **Never use `\n`** — use separate Paragraph elements (silent XML corruption)
- **Never use unicode bullets** — use `LevelFormat.BULLET` (Word crashes)
- **Always validate before returning** — `python scripts/validate.py` (corruption is invisible)
```

Use visual markers (bold, bullet points, consistent pattern) so the section is scannable.

## Degrees of Freedom

Match instruction specificity to task fragility:

| Freedom | When to Use | Example |
|---------|-------------|---------|
| **High** (text instructions) | Multiple valid approaches, context-dependent | Code review process |
| **Medium** (pseudocode/params) | Preferred pattern exists, some variation OK | Report generation |
| **Low** (exact scripts) | Fragile operations, consistency critical | Database migrations |

**Analogy:** Narrow bridge with cliffs on both sides = low freedom (exact instructions). Open field = high freedom (general direction only).
