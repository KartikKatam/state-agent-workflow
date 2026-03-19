# Anthropic Official Best Practices for Skills

Condensed from Anthropic's skill authoring documentation.

## Core Principles

### Conciseness
Context window is shared. Only add what Claude doesn't already know.

**Challenge each piece:**
- "Does Claude really need this explanation?"
- "Can I assume Claude knows this?"
- "Does this paragraph justify its token cost?"

### Degrees of Freedom

Match specificity to task fragility:

| Freedom | When to Use | Example |
|---------|-------------|---------|
| **High** (text instructions) | Multiple valid approaches, context-dependent | Code review process |
| **Medium** (pseudocode/params) | Preferred pattern exists, some variation OK | Report generation template |
| **Low** (exact scripts) | Fragile operations, consistency critical | Database migrations |

**Analogy:** Narrow bridge with cliffs = low freedom (exact instructions). Open field = high freedom (general direction).

### Test with Target Models

What works for Opus may need more detail for Haiku. If skill serves multiple models, aim for instructions that work with all.

## Structure Patterns

### Progressive Disclosure
SKILL.md is a table of contents. Point to detailed files as needed.

```markdown
# SKILL.md
## Quick start
[Core instructions]

## Advanced features
**Form filling**: See FORMS.md
**API reference**: See REFERENCE.md
```

Claude loads reference files only when needed. Keep references one level deep.

### Workflow Pattern
Break complex operations into sequential steps with checklists:

```markdown
Task Progress:
- [ ] Step 1: Analyze input
- [ ] Step 2: Process data
- [ ] Step 3: Validate output
- [ ] Step 4: Verify results
```

### Feedback Loop Pattern
Run validator → fix errors → repeat. Greatly improves output quality.

### Template Pattern
Provide output templates. Match strictness to requirements:
- **Strict:** "ALWAYS use this exact template structure"
- **Flexible:** "Sensible default, adapt as needed"

### Conditional Workflow
Guide through decision points:
```markdown
Creating new? → Follow Creation workflow
Editing existing? → Follow Editing workflow
```

## Content Guidelines

- **Avoid time-sensitive info** — no "if before August 2025..."
- **Use consistent terminology** — pick one term, use throughout
- **Provide examples** — input/output pairs for quality-dependent skills

## Evaluation-Driven Development

1. Run Claude on representative tasks WITHOUT skill — document failures
2. Build 3 scenarios testing those gaps
3. Measure baseline performance
4. Write minimal instructions addressing gaps
5. Iterate: run evaluations, compare to baseline, refine

Ensures you're solving actual problems, not imagined requirements.

## Naming

Use gerund form (verb + -ing): "Processing PDFs", "Testing code", "Writing documentation."

Avoid vague names: "Helper", "Utils", "Tools."
