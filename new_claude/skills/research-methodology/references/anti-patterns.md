# Research Anti-Patterns

Common research failures with WRONG/RIGHT examples.

## Contents

- **AP 1: Searching Instead of Thinking** — Opus doing retrieval instead of reasoning
- **AP 2: Over-Broad Sub-Agent Prompts** — Vague prompts produce vague results
- **AP 3: Skipping Cache Check** — Re-researching known information
- **AP 4: No Confidence Scoring** — Unscored findings mislead consumers
- **AP 5: Dumping Raw Content** — Noise in research output
- **AP 6: Single Source Dependency** — Uncorroborated claims reported as high confidence
- **AP 7: Ignoring the Consumer's Context** — Wrong depth for the audience
- **AP 8: Manual Processing When PTC Available** — Context waste on raw content
- **AP 9: Too Many Sub-Agents** — Coordination overhead exceeds value
- **AP 10: Re-Searching Sub-Agent Findings** — Duplicating work at 10x cost

## Anti-Pattern 1: Searching Instead of Thinking

```
WRONG: Receive research request → immediately start WebSearching
       (Opus tokens burned on retrieval, vague queries, unfocused results)

RIGHT: Receive research request → decompose into 3 precise sub-questions
       → craft targeted sub-agent prompts → dispatch Sonnet sub-agents
       → synthesize their findings
```

**Why this fails:** Opus doing unfocused searching is the most expensive way to get mediocre results. Your value is decomposition and synthesis — the judgment layer. Sub-agents are cheaper and can search in parallel.

## Anti-Pattern 2: Over-Broad Sub-Agent Prompts

```
WRONG: "Research SQLAlchemy async patterns"
       (Sub-agent doesn't know: which version? what use case? what depth?)

RIGHT: "Find SQLAlchemy 2.0 async session factory patterns for
        repository pattern with connection pooling. Search:
        'SQLAlchemy 2.0 async session factory repository pattern 2026'.
        Return: 2-3 code examples with session lifecycle management.
        Do NOT cover sync patterns or SQLAlchemy 1.x."
```

**Why this fails:** Vague prompts produce vague results. The sub-agent fills ambiguity with guesses. Your job is to remove ambiguity before dispatch.

## Anti-Pattern 3: Skipping Cache Check

```
WRONG: Research request arrives → decompose → dispatch sub-agents
       (Re-researches topic that was answered last week)

RIGHT: Research request arrives → check {research_dir}/_index.json
       → find existing research → verify it answers the question
       → return existing if sufficient, research only gaps
```

**Why this fails:** Re-research wastes sub-agent tokens AND produces potentially inconsistent findings with existing research. The 30-second cache check prevents minutes of unnecessary work.

## Anti-Pattern 4: No Confidence Scoring

```
WRONG: "SQLAlchemy 2.0 supports async sessions natively."
       (No source, no confidence level, consumer trusts it blindly)

RIGHT: "SQLAlchemy 2.0 supports async sessions via AsyncSession
        (confidence: 0.95, source: official docs). For the repository
        pattern specifically, async_scoped_session is recommended
        (confidence: 0.7, source: community patterns, not in official docs)."
```

**Why this fails:** Downstream agents (coders, planners) make implementation decisions based on your research. A wrong answer presented with false confidence causes bugs that are expensive to find — the coder trusts the research and doesn't verify.

## Anti-Pattern 5: Dumping Raw Content

```
WRONG: Copy-paste 3 pages of documentation into the research output
       (Consumer reads 2000 tokens of noise to find 50 tokens of answer)

RIGHT: Extract the 3-5 key points, include one minimal code example,
       link to the full docs for deep-dives. Research output under
       500 tokens for simple topics, under 2000 for complex ones.
```

**Why this fails:** Research output is consumed by other agents whose context windows are shared resources. Every unnecessary token in research competes with the agent's actual work.

## Anti-Pattern 6: Single Source Dependency

```
WRONG: Found one blog post that answers the question → report as high confidence
       (Blog may be outdated, wrong, or describing a non-standard approach)

RIGHT: Found one blog post → corroborate with at least one other source
       → if uncorroborated, report as low confidence (0.4-0.5)
       → include recommendation: "verify against actual library behavior"
```

**Why this fails:** Single sources have unknown reliability. A blog post from 2024 may describe patterns that changed in 2025. Official docs + community agreement = high confidence. One random source = low confidence, always.

## Anti-Pattern 7: Ignoring the Consumer's Context

```
WRONG: Strategist asks "how does X work?" →
       Return implementation-level API reference with code examples

RIGHT: Strategist asks "how does X work?" →
       Return architectural overview, trade-offs, integration complexity,
       comparison with alternatives. No code examples needed.
```

**Why this fails:** Research depth and focus must match the consumer. A coder needs implementation details. A strategist needs decision-support information. A 5000-token API reference when the strategist needed a 200-token trade-off summary is worse than useless — it wastes their context window.

## Anti-Pattern 8: Manual Processing When PTC Is Available

```
WRONG: WebSearch returns a long web page → agent reads entire page in context
       → manually extracts relevant sections (1500 tokens spent on noise)

RIGHT: WebSearch returns URL → ptc_execute with trafilatura to extract
       clean content → print() only the relevant findings
       (50 tokens in context, noise stays in container)
```

**Why this fails:** PTC exists precisely for this — processing raw content without polluting the context window. When PTC is available, always prefer it for content extraction. Direct processing is the fallback, not the default.

## Anti-Pattern 9: Too Many Sub-Agents

```
WRONG: Decompose into 6 sub-questions → dispatch 6 sub-agents
       (Coordination overhead exceeds value, results are fragmented)

RIGHT: Decompose into 2-3 independent facets → dispatch 2-3 sub-agents
       → synthesize into coherent output
```

**Why this fails:** Each sub-agent has coordination cost (prompt crafting, result verification, synthesis). Beyond 3-4 sub-agents, the overhead of managing them exceeds the parallelism benefit. If you need 6 facets, group related ones.

## Anti-Pattern 10: Re-Searching What Sub-Agents Already Found

```
WRONG: Sub-agents return findings → Opus does 5 more searches
       "just to be thorough" (duplicates sub-agent work at 10x cost)

RIGHT: Sub-agents return findings → Opus does 0-2 targeted searches
       to double-check specific claims or fill narrow gaps
       → focus on synthesis
```

**Why this fails:** If you're doing extensive searching after sub-agents return, you either under-scoped the sub-agents (fix the prompts) or you don't trust their results (verify specific claims, don't redo everything).
