# Research Patterns

Proven approaches for research decomposition, sub-agent prompting, and synthesis.

## Contents

- **Prompt Templates** — Ready-made sub-agent prompts for API, comparison, and troubleshooting research
- **Facet-Splitting Strategies** — How to decompose research questions into parallel sub-agent work
- **WebSearch Query Optimization** — Query construction techniques, site targeting by question type, common mistakes
- **Comparison Research Structure** — JSON output structure for multi-option comparisons
- **PTC-Enhanced Synthesis Patterns** — When to use PTC, what packages for what purpose
- **Research Output Adaptation** — Type-specific `findings.details` structures

## Research-Specific Prompt Templates

### API/Library Research Prompt

```
## Research Task
Find the official API for {library} {version}: {specific function/class/feature}.

## Search Strategy
- Search: "{library} {version} {feature} documentation"
- Search: "{library} {feature} example usage"
- Target sites: official docs domain, GitHub repo README
- If PTC available: use trafilatura to extract clean content from docs pages

## Return Format
- Function/class signatures with parameter types
- One usage example (minimal, working)
- Known gotchas or version-specific notes
- Source URLs

## Constraints
- Only {library} {version} — do not include older version syntax
- Do not research alternatives to {library}
```

### Comparison Research Prompt

```
## Research Task
Evaluate {option} for {use case}: strengths, weaknesses, production readiness.

## Search Strategy
- Search: "{option} {use case} production experience {year}"
- Search: "{option} vs alternatives {use case}"
- Search: "{option} limitations gotchas"
- If PTC available: extract and summarize lengthy comparison articles

## Return Format
- 3-5 strengths (with evidence, not just claims)
- 3-5 weaknesses or limitations
- Production adoption evidence (who uses it, at what scale)
- Maturity assessment: experimental / stable / battle-tested
- Source URLs

## Constraints
- Only evaluate {option} — another agent covers {other_options}
- Focus on {use case}, not general-purpose assessment
```

### Troubleshooting Research Prompt

```
## Research Task
Find solutions for: "{error message or problem description}"
Context: {language} {version}, using {relevant libraries/frameworks}.

## Search Strategy
- Search: "{exact error text}" site:stackoverflow.com
- Search: "{error text}" site:github.com/issues
- Search: "{library} {error keyword} fix {version}"
- If PTC available: extract solution code blocks from pages

## Return Format
- Top 3 solutions ranked by community validation (votes, accepted answers)
- For each: cause, fix, any caveats
- Version-specific notes if the fix differs by version
- Source URLs

## Constraints
- Solutions must be for {language} {version} — do not include fixes for other languages
- Include the cause, not just the fix — the requester needs to understand why
```

## Facet-Splitting Strategies

How to decompose research questions into parallel sub-agent work:

| Research Type | Facet 1 | Facet 2 | Facet 3 (if needed) |
|---------------|---------|---------|---------------------|
| API/library | Official docs + signatures | Real-world usage + gotchas | — |
| Comparison (X vs Y) | X strengths + evidence | Y strengths + evidence | Direct comparison articles |
| Best practices | Official recommendations | Community production patterns | — |
| Troubleshooting | SO + GitHub issues | Version changelogs + migration guides | — |
| Architecture | Established patterns + papers | Production case studies | Alternative approaches |
| New technology | Official docs + getting started | Adoption + limitations + community | Alternatives comparison |

**Rules for good facet splits:**
- Each facet is independently searchable (parallel-safe)
- No overlap in search targets — "another agent handles X" prevents redundancy
- Each facet contributes a distinct dimension to the final answer
- 2-3 facets is the sweet spot. 4+ means your decomposition is too fine-grained

## WebSearch Query Optimization

### Query Construction

| Technique | Example | When |
|-----------|---------|------|
| Include year | "FastAPI middleware patterns 2026" | Current practices, evolving topics |
| Include version | "SQLAlchemy 2.0 async session" | Version-specific features |
| Site filter | "site:stackoverflow.com pytest fixture scope" | Known best source for question type |
| Exact error | `"ImportError: cannot import name 'X'"` | Troubleshooting specific errors |
| Exclude noise | "pytorch dataloader -tutorial -beginner" | Filtering out basic content |

### Site Targeting by Question Type

| Question Type | Best Sources |
|---------------|-------------|
| API reference | Official docs, GitHub repo |
| Troubleshooting | stackoverflow.com, github.com issues |
| Best practices | Official blogs, conference talks |
| Comparisons | Blog posts, benchmarks, HN discussions |
| Architecture | martinfowler.com, research papers, tech company blogs |
| Security | OWASP, vendor security advisories |

### Common Query Mistakes

```
WRONG: "how do fixtures work"
RIGHT: "pytest fixture scope session function module conftest 2026"

WRONG: "database best practices"
RIGHT: "SQLAlchemy 2.0 async session factory pattern production"

WRONG: "fix import error"
RIGHT: "ImportError cannot import SAM2Predictor segment-anything-2 python 3.11"
```

## Comparison Research Structure

When synthesizing comparison results from multiple sub-agents:

```json
{
  "findings": {
    "details": {
      "options": [
        {
          "name": "Option A",
          "strengths": ["strength 1 (source)", "strength 2 (source)"],
          "weaknesses": ["weakness 1 (source)"],
          "maturity": "stable",
          "adoption": "Used by X, Y at scale"
        },
        {
          "name": "Option B",
          "strengths": ["..."],
          "weaknesses": ["..."],
          "maturity": "experimental",
          "adoption": "Early adopters only"
        }
      ],
      "recommendation": "Option A for {use case} because {reason}",
      "recommendation_confidence": 0.8,
      "key_differences": [
        {"aspect": "Performance", "a": "X approach", "b": "Y approach"},
        {"aspect": "Ecosystem", "a": "Mature plugins", "b": "Limited"}
      ]
    }
  }
}
```

## PTC-Enhanced Synthesis Patterns

### When to Use PTC for Synthesis

| Situation | Use PTC? | Why |
|-----------|----------|-----|
| 2-3 sources, simple merge | No | Manual synthesis is faster than PTC overhead |
| 5+ sources, overlap likely | Yes | rapidfuzz dedup catches near-duplicates you'd miss manually |
| Comparison across 3+ options | Yes | pandas comparison matrix is more structured than manual |
| Large research output | Yes | tiktoken measures size before you deliver — prevents oversized output |
| Web content extraction | Yes | trafilatura/readability-lxml strip 90%+ noise from web pages |
| PDF document analysis | Yes | pypdf/pdfplumber extract text without loading full PDF into context |

### What to Use PTC For

**Deduplication (rapidfuzz):**
When sub-agents return overlapping findings, fuzzy matching in PTC identifies near-duplicate information. Useful because different sources describe the same pattern with slightly different wording — exact matching misses these.

**Comparison tables (pandas):**
When comparing 3+ options across multiple dimensions, build a DataFrame in PTC and print the formatted comparison. Cleaner than manually assembling a table in context.

**Token measurement (tiktoken):**
Before delivering research output, measure its token count in PTC. Research that exceeds the consumer's context budget is waste — compress or split if too large.

**Content extraction (trafilatura, readability-lxml):**
Sub-agents can use these in PTC to strip web pages to clean content before analyzing. Raw HTML in context wastes 80-90% of tokens on navigation, ads, and boilerplate.

**PDF processing (pypdf, pdfplumber):**
For research papers, specs, or technical documentation in PDF format. Content stays in PTC container; only extracted findings print to context.

For concrete PTC code recipes, load ptc-sandbox skill and read `references/researcher.md`.

## Research Output Adaptation

The `findings.details` structure adapts to research type:

| Research Type | `details` Contains |
|---------------|-------------------|
| API reference | `endpoints` (signatures, params, returns), `classes` |
| Comparison | `options` (strengths, weaknesses, adoption), `recommendation` |
| Best practices | `patterns` (name, when_to_use, example), `anti_patterns` |
| Troubleshooting | `causes` (description, fix, caveats), `prevention` |
| Architecture | `patterns` (name, trade_offs, when_to_use), `decision_matrix` |

Keep only the fields relevant to the research type. Empty sections are noise.
