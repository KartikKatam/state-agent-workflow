---
name: research-methodology
version: 1-0-0
description: >
  Use when conducting research on libraries, APIs, design patterns, architecture
  decisions, or any topic requiring external information gathering. Activates for:
  research requests from the lead, info_request messages from other agents,
  any task requiring WebSearch or document analysis, or when any agent needs to
  validate assumptions against external sources. Also use when research involves
  multiple facets, external sources, or cross-cutting analysis. Do NOT use for:
  codebase exploration (use codebase-exploration), sub-agent dispatch mechanics
  (use sub-agent-delegation), PTC container operations (use ptc-sandbox).
---

# Research Methodology

## Core Principle

Your job is to **think, not search.** Decompose research problems into precise sub-questions, craft targeted sub-agent prompts that make their work easy, and synthesize findings into confident, structured output. Sub-agents do the searching. You do the reasoning. You can search for double-checking and nuance-gathering, but the bulk of information retrieval is delegated.

## When NOT to Use

- **Quick single-fact lookup** — a coder checking one API signature mid-implementation doesn't need this skill. Just search directly.
- **Opinion/judgment questions** — "Should we use microservices?" is a design decision, not a research task. The strategist's judgment is the answer, not external sources.
- **Codebase questions** — "How does our auth module work?" is codebase exploration, not research. Use codebase-exploration.
- **Information already in CLAUDE.md or project docs** — check project-local knowledge before triggering external research.
- **Sub-agent dispatch** — if you just need to send a task to a sub-agent, use sub-agent-delegation directly. This skill is for research-specific decomposition and synthesis.

## Research Depth Calibration

Match output depth to request complexity. Over-researching wastes tokens for every downstream consumer.

| Request Complexity | Target Output | Sub-Agents | Example |
|--------------------|---------------|------------|---------|
| Simple factual (yes/no, one signature) | 100-200 tokens | 0 (direct) | "Does library X support feature Y?" |
| Focused lookup (API pattern, gotchas) | 300-500 tokens | 0-1 | "How to use pytest fixtures with async" |
| Multi-facet (comparison, best practices) | 500-1500 tokens | 2-3 | "Compare Alembic vs Django migrations for async" |
| Deep evaluation (new tech, architecture) | 1500-3000 tokens | 3-4 | "Evaluate event sourcing for our pipeline" |

## Quick Reference

| Situation | Action |
|-----------|--------|
| New research request arrives | Step 1: check cache, then decompose |
| Simple factual question (one search) | You can answer directly — sub-agents are overhead |
| Multi-faceted question (3+ aspects) | Decompose into sub-agent tasks, dispatch parallel |
| Comparison ("X vs Y") | One sub-agent per option + one for direct comparisons |
| Troubleshooting (error, debugging) | Sub-agent with error text + version context |
| Need API signatures or library docs | WebSearch with library name + version |
| Sub-agents returned results | Synthesize: merge, dedup, score confidence |
| Confidence is low across sources | Flag explicitly, recommend how requester should proceed |
| PTC available | Use PTC for content extraction, dedup, comparison tables |
| PTC unavailable | Direct tool calls — same flow, higher context cost |

## Core Workflow

### Step 1: Understand and Decompose

Read the research request. Before any searching, answer these questions:

1. **What exactly is being asked?** Restate the question in your own words. Vague requests produce vague research — if the request is ambiguous, ask the lead for clarification before proceeding. The cost of one clarifying message is far less than researching the wrong thing.
2. **What does the requester actually need?** A coder asking "how does X work?" needs implementation patterns. A strategist asking the same question needs architectural trade-offs. Tailor depth and focus to the consumer.
3. **What constraints matter?** Version requirements, language, framework compatibility, performance characteristics. These go into sub-agent prompts.
4. **What do we already know?** Check `{research_dir}/_index.json` and existing research files. `{research_dir}` is specified by the lead in the task assignment (typically `.claude/research/` or `new_claude/research/`). If existing research fully answers the question, return it with `from_cache: true` — don't re-research.

**Cache search:** Grep the index file for matching `keywords` or `topic` fields rather than loading the entire index into context. For large indexes, use PTC with rapidfuzz to fuzzy-match against the topic.

**Staleness check:** If cached research exists but `completed_at` is older than 30 days for a rapidly-evolving topic (framework in active development, new library), re-research. For stable topics (established patterns, mature libraries), cached research older than 90 days is still valid. When in doubt, check the topic's release cadence — if the library shipped 3 versions since the research was written, it's stale.

**Decompose into sub-questions** when the topic has multiple independent facets. Each sub-question becomes one sub-agent task.

Decomposition rules:
- Each sub-question should be answerable with 1-3 searches
- Sub-questions should be independent (parallel-safe)
- Include the *why* — what this facet contributes to the overall answer
- Specify what format to return (key findings, code examples, comparison points)

| Request Type | Typical Decomposition |
|--------------|----------------------|
| API/library usage | 1: Official docs + signatures, 2: Real-world usage patterns + gotchas |
| Comparison (X vs Y) | 1: X strengths + use cases, 2: Y strengths + use cases, 3: Direct comparison articles |
| Best practices | 1: Official recommendations, 2: Community patterns + production experience |
| Troubleshooting | 1: Stack Overflow + GitHub issues, 2: Version-specific changelogs |
| Architecture/design | 1: Established patterns + papers, 2: Production implementations + case studies |
| New technology eval | 1: Official docs + getting started, 2: Community adoption + limitations, 3: Alternatives |

Step 1 produces: a research plan with sub-questions and their assignments. Step 2 uses this plan.

### Step 2: Craft Sub-Agent Prompts

Each sub-agent needs a prompt that makes its job easy. The quality of your prompts determines the quality of research output. This is where your Opus reasoning earns its cost — turning a vague research topic into precise, targeted instructions.

**Every research sub-agent prompt must include:**

```
## Research Task
[One sentence: what specific question to answer]

## Search Strategy
[Specific queries to try, sites to target, version constraints]
[Whether to use PTC for content extraction — and which packages]

## Return Format
[Exact structure: key findings as bullet points, code examples, source URLs]
[What to include vs exclude]

## Constraints
[What NOT to research — prevents scope creep into other sub-agents' territory]
[Version requirements, language constraints]
```

**Making prompts sharp — the nuance that earns Opus cost:**

- Include version numbers: "SQLAlchemy 2.0" not "SQLAlchemy"
- Include year for currency: "FastAPI best practices 2026"
- Include the use case context: "for async database tests" not just "pytest fixtures"
- Include anti-targets: "Do NOT research unittest — another agent handles that"
- For WebSearch: suggest `site:` filters when you know the best sources (e.g., `site:stackoverflow.com` for troubleshooting)

**PTC guidance in prompts:** When PTC is available, tell sub-agents to use it for content extraction. This keeps raw web content out of their context window.

```
## PTC Usage
You have ptc_execute available. Use trafilatura to extract clean content
from web pages before analyzing. Only print() your structured findings —
raw HTML stays in the container.
```

If PTC is unavailable, omit this section. The sub-agent will use WebFetch/WebSearch directly — same results, higher context cost.

Step 2 produces: dispatch-ready sub-agent prompts. Step 3 dispatches them.

### Step 3: Dispatch and Verify

**Dispatch** using the sub-agent-delegation skill for mechanics:
- **Sonnet** for most research tasks — needs reasoning to evaluate sources, extract relevant information, and summarize accurately
- **Haiku** for basic data collection — simple web page content extraction, straightforward fact retrieval where no judgment is needed
- **Parallel** dispatch when sub-questions are independent (the common case)
- **Sequential** only when one sub-question's answer determines what to ask next

**Verify** results from each sub-agent (per sub-agent-delegation Step 5):
- Did the sub-agent answer the actual question asked?
- Are source URLs included?
- Is the return format correct?
- Are findings substantive (not just "I found that X exists")?

If a sub-agent returns thin or off-target results, retry with a clarified prompt before giving up. Common fixes: more specific search queries, different site filters, narrower scope.

**Your own searching:** After sub-agents return, you may search directly for:
- Double-checking a specific claim a sub-agent made
- Filling a narrow gap the sub-agents missed
- Finding the nuance that connects findings across sub-agents
- Verifying version compatibility that affects the synthesis

Keep your own searching targeted and brief. If you find yourself doing extensive searching, you under-scoped the sub-agents.

**If sub-agents are unavailable** (single-agent mode, resource constraints): handle all research directly using the same decomposition as your own search strategy. Work through each sub-question sequentially. Same quality target, higher context cost.

Step 3 produces: verified sub-agent results. Step 4 synthesizes them.

### Step 4: Synthesize and Score Confidence

This is your highest-value step. Sub-agents see their partition — you see the whole picture.

**Synthesis process:**

1. **Merge** findings from all sub-agents into a unified view
2. **Deduplicate** — when multiple sub-agents found the same information, keep the most detailed version
3. **Resolve conflicts** — when sub-agents contradict each other, investigate both claims. Prefer official docs over blog posts, recent over old, specific over general
4. **Find connections** — cross-cutting insights that no individual sub-agent could see. "Sub-agent 1 found X uses pattern A, sub-agent 2 found Y also uses pattern A — this is a common architectural pattern"
5. **Score confidence** per finding (see Confidence Scoring below)

**PTC-enhanced synthesis** (when PTC is available — prefer this path):

Use PTC when synthesis involves 5+ sources or structured comparison. The benefits compound:
- **rapidfuzz** for fuzzy-matching duplicate findings across sources — catches near-duplicates that exact string matching misses
- **pandas** for building comparison matrices when evaluating multiple options
- **tiktoken** to measure output size before delivering — research that exceeds the consumer's context budget is waste
- **markdown-it-py** for parsing structured markdown from sub-agent returns

Load the ptc-sandbox skill for agent-specific package patterns and code recipes. The ptc-sandbox `references/researcher.md` file has the concrete code patterns for these operations.

When PTC is unavailable, perform synthesis manually in context. Same judgment, higher token cost.

Step 4 produces: synthesized findings with confidence scores. Step 5 writes them.

### Step 5: Write and Deliver

**Write the research file** to `{research_dir}/{topic-slug}.json`:

```json
{
  "meta": {
    "research_id": "string",
    "topic": "string",
    "requested_by": "string",
    "completed_at": "ISO timestamp",
    "sources_used": ["websearch"],
    "confidence": "high|medium|low|uncertain",
    "confidence_reason": "Brief explanation",
    "gaps": ["Areas not fully covered"]
  },
  "summary": "2-3 sentence answer",
  "findings": {
    "key_points": ["Main insight 1", "Main insight 2"],
    "details": {},
    "examples": [],
    "gotchas": []
  },
  "sources": [
    {"title": "string", "url": "string", "type": "official_docs|github|tutorial|stackoverflow|blog"}
  ]
}
```

The `findings.details` structure adapts to the research type — API reference uses `endpoints`, comparisons use `options`, troubleshooting uses `causes_and_solutions`. Keep fields minimal — only populate what's relevant.

**Updating existing research vs. creating new:** When cached research partially answers the question but needs a new facet, modify the existing file — add to `findings`, set `updated_at`, update the index entry. When the new question is substantially different (different library, different use case), create a new file. Default to updating. Creating new files for every question leads to duplicate/overlapping entries that pollute the index.

**Update the index** at `{research_dir}/_index.json` with the new or updated entry.

**Send task_complete** to lead:
```json
{
  "type": "task_complete",
  "payload": {
    "status": "success",
    "output_files": ["{research_dir}/{topic-slug}.json"],
    "summary": "Research complete: {topic}. Confidence: {level}. Key: {one-sentence takeaway}"
  }
}
```

## Confidence Scoring

Every research output MUST include a confidence assessment. Consumers make decisions based on your confidence — overconfidence causes worse outcomes than honest uncertainty.

| Source Type | Base Confidence |
|-------------|----------------|
| Official documentation (recent) | 0.9-1.0 |
| GitHub repository README/docs | 0.8-0.9 |
| Reputable tutorial (major platform) | 0.7-0.8 |
| Stack Overflow (accepted, high-vote) | 0.6-0.7 |
| Blog post | 0.5-0.6 |
| Single uncorroborated source | 0.3-0.5 |

**Adjust down when:**
- Documentation is for an older version than what's needed
- Multiple sources conflict without clear resolution
- The topic is rapidly evolving (frameworks in active development)
- You're extrapolating from related but not identical information

**Adjust up when:**
- Multiple independent sources agree
- Official docs + community experience align
- You verified claims against actual library behavior

**When confidence is low (< 0.6):**
Always include explicit guidance for the requester: "Test against actual library behavior before relying on this" or "Consider prototyping both approaches — sources disagree on which performs better."

## When Research Reveals Bad Assumptions

If your findings show the original task premise is wrong — the library is deprecated, the architecture pattern has a fatal flaw, the version doesn't support the needed feature — report this immediately to the lead with:

1. **What was assumed** — the original request's premise
2. **What you found** — the contradicting evidence with sources
3. **Confidence in the finding** — how sure are you the assumption is wrong
4. **Impact** — what downstream work is affected
5. **Alternatives** — if you found viable alternatives, include them

Do not silently report findings that contradict the premise as if everything is fine. Catching bad assumptions early is one of research's highest-value contributions.

## Anti-Rationalization

| Rationalization | Why It's Wrong |
|----------------|----------------|
| "This is a simple question, I'll just search directly" | Even simple questions need a cache check. Sub-agent overhead is real but decomposition quality matters more than speed. |
| "The first result looks authoritative, no need to corroborate" | Single-source confidence cap is 0.5. One authoritative-looking result can be outdated or wrong. |
| "The sub-agent already found the answer, cancel the others" | Parallel sub-agents are already dispatched. Cancellation saves nothing. Corroboration from multiple facets increases confidence. |
| "Confidence scoring is overhead for this simple question" | Confidence is never optional. A 30-second assessment prevents downstream agents from treating speculation as fact. |
| "The cached research is close enough" | "Close enough" means the delta is unexamined. If the question is slightly different, the delta might be the most important part. |
| "I'll report findings now and add sources later" | Unsourced findings are unverifiable. Sources are part of the finding, not metadata added after. |

## Critical Rules

- **Think first, search second** — decomposition quality determines research quality. A well-decomposed question with Haiku sub-agents produces better results than an Opus agent doing unfocused searching.
- **Check cache before researching** — existing research may fully answer the request. Re-research is pure waste. But verify staleness for evolving topics (see Step 1).
- **Prefer PTC when available** — content extraction, deduplication, and structured comparison all benefit from PTC's context savings. Load ptc-sandbox for package patterns. Fall back to direct tools only when PTC is unavailable.
- **Confidence is not optional** — every finding needs a confidence score. Every low-confidence finding needs a recommendation. Consumers can work around gaps but cannot detect overconfident wrong answers.
- **Sub-agents do the searching, you do the reasoning** — if you find yourself doing extensive searching, you under-decomposed the problem. Your Opus tokens are for judgment, not retrieval.
- **Adapt output depth to consumer** — match the Research Depth Calibration table. A coder needs implementation examples. A strategist needs trade-off analysis. A 2000-token research file for a yes/no question is waste.
- **All research persists** — every research output is written to disk with structured metadata. No throwaway research. Future agents may reuse your findings.
- **Sources are mandatory** — every factual claim links to a source. Unsourced claims are unverifiable and erode trust in the research system.
- **Report bad assumptions immediately** — if research contradicts the original premise, tell the lead. Don't bury it in findings.

## References

- **Step 2 (prompts):** `references/patterns.md` → Prompt Templates for ready-made API, comparison, and troubleshooting templates
- **Step 2 (decomposition):** `references/patterns.md` → Facet-Splitting Strategies for decomposition by research type
- **Step 3 (queries):** `references/patterns.md` → WebSearch Query Optimization for query construction and site targeting
- **Step 4 (synthesis):** `references/patterns.md` → PTC-Enhanced Synthesis Patterns for dedup, comparison, and token measurement
- **Step 5 (output):** `references/patterns.md` → Research Output Adaptation for type-specific `details` structures
- **Failure modes:** `references/anti-patterns.md` for 10 common research failures with WRONG/RIGHT examples
- **Schema:** `schemas/research_entry.py` for Pydantic validation model
- **PTC recipes:** Load ptc-sandbox skill → `references/researcher.md` for concrete code patterns
