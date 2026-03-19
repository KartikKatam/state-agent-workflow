# Skill Engineering Guide: Writing Precise, Enforceable Agent Skills

## The Core Problem You're Facing

When an agent "follows the general gist but not the complete skill," the root cause is almost always one of these:

1. **The skill is telling, not explaining.** A list of steps without reasoning gives the agent permission to improvise.
2. **The skill is too long or flat.** The agent's attention degrades in long, uniform text — critical steps drown in noise.
3. **There are no structural guardrails.** Nothing in the skill architecture forces verification or gates progress.
4. **The description doesn't trigger properly.** The skill isn't even being consulted, or only partially loaded.

This guide addresses all four.

---

## Part 1: Skill Architecture

### The Three-Layer Loading Model

Skills use progressive disclosure. Understanding this is essential because it determines *what the agent actually sees*:

| Layer | What It Is | When It's Loaded | Size Target |
|-------|-----------|-------------------|-------------|
| **Metadata** | `name` + `description` in YAML frontmatter | Always in context (~100 words) | Keep tight |
| **SKILL.md body** | The main instructions | When the skill triggers | < 500 lines |
| **Bundled resources** | Scripts, references, assets in subdirectories | On-demand, when SKILL.md tells the agent to read them | Unlimited |

**Why this matters for your problem:** If your skill is 800 lines of flat instructions, the agent *will* lose track of steps in the middle. The solution is hierarchy — keep the SKILL.md as a routing document that points to detailed references.

### Recommended File Structure

```
my-skill/
├── SKILL.md                    # The entry point (< 500 lines)
│   ├── YAML frontmatter        # name, description (triggering)
│   └── Markdown body           # Core workflow + pointers
├── scripts/                    # Deterministic, repeatable operations
│   ├── validate.py             # Validation scripts the agent runs
│   └── build.py                # Build/transform scripts
├── references/                 # Detailed docs loaded as-needed
│   ├── api-patterns.md         # Deep-dive on specific subtopics
│   └── edge-cases.md           # Known gotchas
└── assets/                     # Templates, icons, fonts
    └── template.html
```

**The key insight:** Scripts execute without needing to be loaded into context. If a step can be automated (validation, formatting, file transforms), make it a script. The agent calls it; it doesn't need to "understand" 200 lines of validation logic.

---

## Part 2: Writing Skills That Get Followed Precisely

### Principle 1: Explain the "Why" — Don't Just List Steps

This is the single most impactful change you can make. Today's LLMs are smart enough to generalize from reasoning but bad at following arbitrary-seeming commands. Compare:

**Bad (arbitrary command — agent will skip this):**
```markdown
ALWAYS validate the output file before returning it to the user.
```

**Good (explained reasoning — agent will internalize this):**
```markdown
After generating the file, validate it with `python scripts/validate.py output.docx`.
Validation catches silent corruption (malformed XML, missing relationships) that
would cause the file to fail when the user opens it in Word. Skipping this step
means the user gets a broken file with no error message — they just see garbage.
```

When the agent understands *why* a step matters, it treats it as load-bearing rather than optional.

### Principle 2: Use Decision Tables, Not Paragraphs

Agents parse structured content more reliably than prose. For routing decisions:

```markdown
## Quick Reference

| Task | Approach |
|------|----------|
| Read/analyze content | `pandoc` or unpack for raw XML |
| Create new document | Use `docx-js` — see Creating New Documents below |
| Edit existing document | Unpack → edit XML → repack — see Editing Existing |
```

This eliminates ambiguity. The agent matches the user's request to a row and follows the pointer.

### Principle 3: Make Critical Rules Visually Distinct

The production skills use a consistent pattern for non-negotiable rules — they're collected into a dedicated section with strong visual markers:

```markdown
### Critical Rules
- **Never use `\n`** — use separate Paragraph elements
- **Never use unicode bullets** — use `LevelFormat.BULLET` with numbering config
- **PageBreak must be in Paragraph** — standalone creates invalid XML
- **Always set table `width` with DXA** — never use `WidthType.PERCENTAGE`
```

Each rule follows the pattern: **what to do/never do** + **why / what breaks**. This is far more effective than scattering rules throughout the document.

### Principle 4: Show the Wrong Way, Then the Right Way

Anti-patterns are extremely powerful for enforcement. The agent learns the boundary, not just the target:

```markdown
### Lists (NEVER use unicode bullets)

```javascript
// ❌ WRONG — never manually insert bullet characters
new Paragraph({ children: [new TextRun("• Item")] })  // BAD

// ✅ CORRECT — use numbering config with LevelFormat.BULLET
const doc = new Document({
  numbering: {
    config: [{
      reference: "bullets",
      levels: [{ level: 0, format: LevelFormat.BULLET, text: "•" }]
    }]
  }
});
```

This pattern — showing the tempting-but-wrong approach explicitly marked as wrong, followed by the correct approach — dramatically reduces the "close but not quite" failures you're seeing.

### Principle 5: Use Imperative Form

Write instructions as commands, not descriptions:

```markdown
# Bad (descriptive — easy to skip)
The validation step should be run after creating the file.
It's recommended to check the output before sharing.

# Good (imperative — clear expectation)
After creating the file, run validation:
```bash
python scripts/validate.py output.docx
```
If validation fails, unpack the file, fix the XML, and repack.
```

---

## Part 3: Structural Guardrails That Enforce Compliance

This is where you solve the "steps get skipped" problem architecturally rather than rhetorically.

### Guardrail 1: Validation Scripts as Gates

Instead of trusting the agent to "remember" quality checks, make validation a concrete step that produces pass/fail output:

```markdown
### Step 3: Validate (required before presenting to user)

Run validation — this catches the most common generation errors:
```bash
python scripts/validate.py output.docx
```

If validation fails, unpack, fix the XML, and repack. Do NOT skip validation
and present the file — the user will get a corrupted document.
```

The script is the guardrail. The agent runs it, sees output, and must respond to it. This is fundamentally more reliable than "remember to check X."

### Guardrail 2: Explicit Step Sequencing with Dependencies

When order matters, make each step's dependency on the previous step explicit:

```markdown
**Follow all 3 steps in order.**

### Step 1: Unpack
```bash
python scripts/unpack.py document.docx unpacked/
```
This creates the `unpacked/` directory that Step 2 operates on.

### Step 2: Edit XML
Edit files in `unpacked/word/`. (Step 1 must complete first — the files don't exist yet.)

### Step 3: Pack
```bash
python scripts/pack.py unpacked/ output.docx --original document.docx
```
Uses the edited files from Step 2. Validates automatically and auto-repairs common issues.
```

Note the phrasing: "Follow all 3 steps in order." + each step references what the previous step produced. This creates a logical chain the agent can't skip links in.

### Guardrail 3: Checklists for Complex Outputs

For multi-requirement outputs, include an explicit checklist the agent should verify:

```markdown
### Before Presenting the Final Output

Verify all of the following:
- [ ] Page size is set explicitly (not relying on defaults)
- [ ] All tables have dual widths (columnWidths + cell width)
- [ ] Validation script passes without errors
- [ ] File opens correctly (run conversion to PDF as a smoke test)

If any check fails, fix the issue before presenting the file.
```

### Guardrail 4: Template-Based Output Enforcement

When the output must follow a specific structure, provide a literal template:

```markdown
## Report Structure
ALWAYS use this exact template:

# [Title]
## Executive Summary
[2-3 paragraphs summarizing key findings]
## Key Findings
[Detailed analysis with supporting data]
## Recommendations
[Actionable next steps, numbered]
```

This is more enforceable than describing the structure in prose. The agent copies the template and fills it in rather than generating structure from scratch.

### Guardrail 5: Bundled Scripts for Deterministic Operations

Anything that can be automated should be. This is the strongest guardrail because it removes the agent's discretion entirely:

```markdown
## Generating the Chart

Don't write chart generation code from scratch. Use the bundled script:
```bash
python scripts/generate_chart.py --input data.csv --output chart.png --style corporate
```

This ensures consistent styling, correct axis labels, and proper resolution
every time, regardless of what the agent might otherwise improvise.
```

Every script you bundle is one fewer thing the agent can get wrong.

---

## Part 4: Writing Effective Descriptions (Triggering)

Your skill is useless if it doesn't trigger. The description in the YAML frontmatter is the *only* thing the agent sees when deciding whether to consult a skill.

### Make Descriptions "Pushy"

The system undertriggers by default — it errs toward handling things without skills. Combat this:

```yaml
# Bad — too passive, won't trigger on edge cases
description: "Helps create Word documents."

# Good — aggressive triggering with explicit examples
description: >
  Use this skill whenever the user wants to create, read, edit, or manipulate
  Word documents (.docx files). Triggers include: any mention of 'Word doc',
  'word document', '.docx', or requests to produce professional documents with
  formatting. Also use when extracting content from .docx files, performing
  find-and-replace, or converting content into a polished Word document.
  If the user asks for a 'report', 'memo', 'letter', or 'template' as a Word
  file, use this skill. Do NOT use for PDFs, spreadsheets, or Google Docs.
```

### Description Checklist

A good description includes:
1. **What the skill does** (create, read, edit Word documents)
2. **Trigger phrases** (specific words/contexts that should activate it)
3. **Edge cases that should trigger** (non-obvious uses)
4. **Negative boundaries** (what should NOT trigger this skill)

### Test Your Triggers

Create eval queries — both should-trigger and should-not-trigger — and test them. The skill-creator framework has tooling for this, but even manually verifying that your skill triggers on realistic prompts is valuable.

---

## Part 5: Advanced Techniques

### Technique 1: Progressive Reference Loading

For complex skills, don't dump everything in SKILL.md. Use it as a router:

```markdown
# My Complex Skill

## Quick Reference
| Task | Guide |
|------|-------|
| Simple creation | Follow instructions below |
| Editing existing files | Read [editing.md](editing.md) |
| Advanced formatting | Read [advanced.md](advanced.md) |

## Simple Creation Workflow
[core instructions here — under 200 lines]
```

The agent reads SKILL.md, determines which path applies, and loads only the relevant reference. This keeps context focused.

### Technique 2: Anti-Pattern Libraries

Maintain a section of known failure modes. Agents learn boundaries more effectively from counterexamples than from positive examples alone:

```markdown
### Common Mistakes

**❌ Don't write Python scripts for XML edits.**
Scripts introduce unnecessary complexity. The edit tool shows exactly what's
being replaced and is more reliable for string operations.

**❌ Don't use `WidthType.PERCENTAGE` for table widths.**
Percentages break in Google Docs. Always use `WidthType.DXA` with explicit
pixel values.

**❌ Don't skip validation "because the code looks right."**
Silent XML corruption is invisible in code review but breaks the file on open.
```

### Technique 3: Context-Aware Defaults

Encode sensible defaults directly into your skill so the agent doesn't have to decide:

```markdown
### Page Size
```javascript
// CRITICAL: docx-js defaults to A4, not US Letter
// Always set page size explicitly
sections: [{
  properties: {
    page: {
      size: { width: 12240, height: 15840 }  // US Letter in DXA
    }
  }
}]
```

The comment explains why the default is wrong and provides the correct value inline. The agent copies this rather than guessing.

### Technique 4: Tone and Framing

The skill-creator docs emphasize: "Try to explain to the model why things are important in lieu of heavy-handed musty MUSTs." This is counterintuitive but correct. Compare:

```markdown
# Heavy-handed (less effective)
You MUST ALWAYS use Arial font. NEVER use any other font. This is CRITICAL.

# Explanatory (more effective)
Use Arial as the default font — it's universally supported across Windows, Mac,
and Linux, so the document will render correctly regardless of the user's system.
Other fonts may display as fallbacks, breaking the intended formatting.
```

The second version gives the agent a mental model. It will apply this reasoning even in edge cases you didn't anticipate.

### Technique 5: Iteration-Driven Development

Don't try to write the perfect skill on the first attempt. The recommended workflow:

1. **Draft** the skill based on your intent
2. **Create 2-3 realistic test prompts** (the kind of thing a real user would say)
3. **Run them** and examine the outputs
4. **Identify gaps** — where did the agent deviate?
5. **Revise** — but generalize from the feedback, don't overfit to specific test cases
6. **Repeat** until the outputs are consistently correct
7. **Expand** the test set and verify at larger scale

The key insight from the skill-creator framework: "if you find yourself writing ALWAYS or NEVER in all caps, that's a yellow flag — reframe and explain the reasoning."

---

## Part 6: Quick Checklist for Any New Skill

Before deploying a skill, verify:

- [ ] **Description is pushy** — includes trigger phrases, edge cases, and negative boundaries
- [ ] **SKILL.md is under 500 lines** — excess is offloaded to references/
- [ ] **Critical rules are collected** in a dedicated section, not scattered
- [ ] **Anti-patterns are shown** with ❌ markers alongside ✅ correct approaches
- [ ] **Validation is scripted** — not relying on the agent to "remember" to check
- [ ] **Steps are sequenced** with explicit dependencies between them
- [ ] **The "why" is explained** for every non-obvious instruction
- [ ] **Templates are provided** for any structured output format
- [ ] **Decision tables route** complex branching logic
- [ ] **Test prompts have been run** and outputs verified

---

## Summary: The Hierarchy of Enforcement

From weakest to strongest:

1. 🔴 **Prose instructions** — "Please remember to validate" → easily skipped
2. 🟡 **Imperative commands** — "Run validation before returning" → better but still skippable
3. 🟡 **Explained reasoning** — "Validate because corruption is silent" → agent internalizes the why
4. 🟢 **Anti-pattern examples** — "❌ WRONG / ✅ CORRECT" → agent learns boundaries
5. 🟢 **Structural dependencies** — "Step 2 operates on Step 1's output" → can't skip without breaking
6. 🟢 **Scripted automation** — `python scripts/validate.py` → removes discretion entirely

The most robust skills combine levels 3-6. They explain reasoning, show boundaries, enforce ordering through dependencies, and automate everything that can be automated.

Your goal: minimize the surface area where the agent has to exercise judgment on *how* to follow the skill, and maximize the surface area where the agent exercises judgment on *what the user actually needs*.
