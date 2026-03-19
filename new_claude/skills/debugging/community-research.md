# Community Research: Debugging Skills Across Popular Agentic Workflow Repos

**Research date:** 2026-02-28
**Researcher:** researcher agent
**Confidence:** Medium (0.72) — Content extracted via WebSearch from documentation caches, skill marketplaces, and GitHub metadata. Direct file reads were not possible; findings synthesized from skills.sh, antigravity.codes, deepwiki, and search snippet aggregation.

---

## Overview

This report surveys debugging-related skills, agents, and methodologies across 7 notable agentic workflow repositories. For each repo, it documents exact file paths, methodology details, and techniques that are **not already covered** by our debugging skill. The final sections synthesize the gaps and recommend specific additions.

### Our Existing Coverage (Baseline)

Before reading per-repo findings, note what we already cover:
- 4-phase workflow: Reproduce → Compare Working vs Broken → Hypothesize & Test → Fix & Verify
- 3-Strikes Rule with structured escalation (3 options: scrap, explore, escalate)
- Hypothesis templates ("I think X because Y")
- Quick Reference decision table (10 rows covering common situations)
- Critical Rules with Why explanations
- Boundary logging at component boundaries
- 5-Whys technique
- Git bisect for regressions
- Flaky test isolation (3 root causes + detection approach)
- Defense-in-depth (4 layers post-fix)
- 7 anti-patterns with WRONG/RIGHT examples

---

## Per-Repo Findings

### 1. obra/superpowers

**Repo:** https://github.com/obra/superpowers

**Debugging skill file paths:**
- `skills/systematic-debugging/SKILL.md` — Main skill
- `skills/debugging/root-cause-tracing/SKILL.md` — Sub-skill for tracing backwards through call stack
- `skills/systematic-debugging/` — Directory also contains techniques for condition-based-waiting and defense-in-depth

**Methodology:**
4-phase root cause process similar in structure to ours, with the same phase-completion gate (must complete each phase before moving to the next). The core slogan is "Systematic debugging is FASTER than guess-and-check thrashing."

The phases:
1. Reproduce (boundary logging at every component in the pipeline)
2. Isolate (identify the specific failing component from the boundary logs)
3. Hypothesize and test (one variable at a time)
4. Fix at the source (fix the root cause, not the symptom)
4.5. Architecture escalation (triggered when 3+ fixes fail — question the architecture)

**Techniques we DON'T already cover:**

**A. Root-Cause Tracing as an explicit backward-chaining discipline**

obra frames root-cause tracing as a named, loadable sub-skill with a specific mental model: "trace bugs *backward* through the call chain until you find the original trigger, then fix at the source." The explicit backward-chaining framing is distinct from our Phase 2 (compare working vs broken). Our Phase 2 looks for differences in a list; obra's root-cause-tracing follows the call chain backward from where the error surfaces to where bad data or behavior was introduced.

The technique: when you can't trace manually, add `console.error()` (or equivalent) calls in tests and library code to capture the full stack trace and execution context at the point of failure, then trace each frame backward until you find the frame that first operated on bad data.

**B. Condition-Based-Waiting (replacement for arbitrary sleeps)**

Documented as a named technique specifically for async/timing-related bugs. The pattern: replace `sleep(N)` with condition-polling loops (poll until a condition is true or a timeout fires). This is particularly called out for async test patterns where tests intermittently fail because they wait a fixed time but the condition resolves at variable times.

This is different from our flaky test isolation section, which identifies timing dependency as a *root cause category* and says "replace sleep() with condition-based waiting" as the fix. obra makes this a standalone named pattern with implementation guidance. This elevation from "mentioned fix" to "named pattern" is worth considering — it signals to agents that this is a distinct, learnable technique.

**C. "95% of no-root-cause cases are incomplete investigation"**

obra explicitly states this heuristic as a law: "95% of 'no root cause' cases are incomplete investigation." This is an important behavioral correction for agents who give up on finding root cause and escalate prematurely. We have the 3-Strikes Rule which prevents this, but we don't have this framing as a motivational heuristic.

**Anti-patterns documented (not in our list):**
- None identified that differ meaningfully from our 7 — obra's anti-patterns align closely with ours.

**Escalation strategy:**
"Phase 4.5" — triggers when 3+ fixes have failed. The agent must explicitly question the architecture before continuing. Similar to our 3-Strikes Rule but framed as a numbered phase rather than a named rule.

---

### 2. glittercowboy/taches (get-shit-done)

**Repo:** https://github.com/glittercowboy/get-shit-done (note: not "taches-cc-resources" — the debugger is in the GSD main repo)

**Debugging skill/agent file paths:**
- `agents/gsd-debugger.md` — The debug agent (990 lines). The primary debugging artifact is an *agent*, not a skill.
- `taches-cc-resources/skills/` — Separate lighter-weight skills including "Debug Like Expert"

**Methodology:**

The GSD debugger takes a scientific method framing: Observe → Hypothesize → Experiment → Analyze → Repeat. But it adds several layers we don't have:

**A. Isolated Debug Subagent with Persistent Debug Session File**

The core architectural novelty: when a bug is detected during a main workflow, the GSD system spawns an *isolated debug subagent* that operates with its own context window (separate from the main agent). This subagent:
- Has its own hypothesis-evidence-resolution workflow
- Maintains a **persistent debug session file** (not just in-context state) that tracks: current hypotheses, evidence gathered, experiments run, results, and resolution
- Is shut down after the bug is resolved, so debug investigation doesn't pollute the main agent's context

This is genuinely novel relative to our skill, which treats debugging as an in-context activity. The isolation pattern prevents debug noise (intermediate print statements, exploratory logs, failed hypotheses) from filling the main agent's context window.

The debug file format tracks:
```
status: investigating | solved | blocked
hypotheses: [{id, statement, confidence, status, evidence}]
experiments: [{id, hypothesis_id, change_made, result}]
resolution: {root_cause, fix_applied, verification}
```

**B. Seeking Disconfirming Evidence**

The GSD debugger explicitly instructs the agent to **seek evidence that disproves the current hypothesis** — asking "What would prove me wrong?" before committing to an experiment. This is the scientific principle of falsifiability applied operationally.

Our skill has hypothesis templates that produce falsifiable hypotheses, but we don't explicitly prompt the agent to seek disconfirming evidence. There is a difference: a falsifiable hypothesis can still be tested by only looking for confirming evidence (confirmation bias). Actively seeking disconfirming evidence counters this.

**C. Strong Inference (Competing Experiment Design)**

The GSD debugger includes "Strong Inference" as a named technique: design experiments that differentiate between **two or more competing hypotheses simultaneously**. Rather than testing hypothesis A, then testing hypothesis B sequentially, design a single experiment whose result rules one out.

Example: if you have two hypotheses (cache is stale vs. API returns wrong data), add logging that captures both the cache key AND the raw API response in the same call. One log line proves which is wrong, instead of two separate experiments.

**D. Precision in Observation**

The GSD debugger requires "precise observations" before forming hypotheses: not "it's broken" but "counter shows 3 when clicking once, should show 1." This forces quantification before hypothesis formation.

Our skill says "read the COMPLETE error" which is related but different — GSD's precision requirement applies to behavioral observations (not just error text) and requires quantification ("should show 1" not just "shows wrong value").

**Anti-patterns documented:**
- Confirmation bias (testing only evidence that supports your hypothesis)
- Vague observations ("it's broken") used as hypothesis inputs

**Escalation strategy:**
After investigation, the debug subagent either resolves the bug or reports `status: blocked` with a structured summary of all hypotheses tried and evidence gathered. The main workflow agent decides whether to retry, escalate, or abandon.

---

### 3. wshobson/agents

**Repo:** https://github.com/wshobson/agents

**Debugging skill file paths:**
- `plugins/developer-essentials/skills/debugging-strategies/SKILL.md` — Primary debugging skill
- `plugins/developer-essentials/skills/error-handling-patterns/SKILL.md` — Companion skill on resilient error handling

**Methodology:**
Scientific method loop: Observe → Hypothesize → Experiment → Analyze → Repeat. This aligns closely with our 4-phase approach but uses cleaner scientific method terminology.

**Techniques we don't already cover:**

**A. Rubber Duck Debugging**

Explicitly documented: "Explain your code and problem out loud (to a rubber duck, colleague, or yourself) — this often reveals the issue." This is a named technique absent from our skill.

This technique works because the act of formulating a verbal explanation of the problem forces the agent to adopt the perspective of an *explainer* rather than an *investigator*. The shift in cognitive mode often exposes incorrect assumptions.

For AI agents, the practical application is: before forming a hypothesis, write out a plain-language description of what the code is supposed to do, what it actually does, and where the gap is. The act of writing this often reveals the root cause.

**B. Binary Search Debugging**

Explicitly documented: "Comment out half the code, narrow down the problematic section, repeat until found." This is distinct from git bisect — binary search debugging applies within a single file or function, not across commits.

This is a complementary technique to git bisect (which searches across time) — binary search applies within the current state of the code. Our skill has git bisect but not code-level binary search.

**C. Production Debugging Protocol**

The wshobson skill includes an explicit production debugging section with specific rules not in our skill:
1. "Gather evidence through error tracking and logs — don't guess"
2. "Reproduce locally using anonymized production data and matching environments" — create a local environment that mirrors production constraints
3. "Safe investigation — don't change production; use feature flags and test fixes in staging"

This is a production-specific context that our skill doesn't address at all. Our skill assumes a development environment where you can freely run code and add logging. Production has constraints: you can't freely change code, data is sensitive, and failures affect real users.

**D. Error-Handling-Patterns as a Debugging Complement**

The companion skill `error-handling-patterns` documents how to design error messages that make future debugging easier (errorful error messages with context vs. "an error occurred"). This is a preventive debugging pattern — design your code so that when bugs occur, the errors point directly to the cause.

Our skill focuses on debugging after a bug occurs. This preventive angle (make errors debuggable at design time) is absent from our skill.

**Anti-patterns documented:**
The wshobson skill documents that "binary search" is the correct form of code narrowing, implicitly flagging the anti-pattern of trying to guess-and-check random subsections.

**Escalation strategy:**
The skill does not explicitly document an escalation strategy beyond "repeat until root cause found."

---

### 4. dsifry/metaswarm

**Repo:** https://github.com/dsifry/metaswarm

**Debugging skill file paths:**
- Metaswarm builds on obra/superpowers skills — it does not have a standalone debugging SKILL.md distinct from obra's
- Debugging is integrated into the multi-agent orchestration flow rather than a single dedicated skill file

**Methodology:**
Metaswarm uses systematic debugging as one of its 13 foundational skills, importing from the superpowers ecosystem. It adds a production multi-tenant SaaS context where debugging must be:
1. Non-disruptive to other tenants
2. Logged for audit trails (compliance requirement)
3. Coordinated across the agent swarm

**Unique techniques relative to our skill:**

**A. Audit Trail Logging During Debugging**

In production SaaS systems, all debugging activity (not just the final fix) must be logged to an audit trail. This means: every hypothesis tested, every change made and reverted, every log statement added and then removed, is recorded. The audit log serves two purposes: (1) compliance evidence, (2) a replayable debugging session that other team members can review.

This is the "debugging session as an artifact" concept. Our skill produces a fixed bug; metaswarm's approach produces a fixed bug PLUS a complete record of the investigation.

**B. No unique debugging technique not already covered by obra or wshobson.** Metaswarm inherits rather than innovates on debugging methodology.

**Escalation strategy:**
Multi-agent coordination: when one agent hits 3-strikes, it sends a structured message to the swarm orchestrator, which can spawn a second investigator to look at the problem with fresh context.

---

### 5. parcadei/Continuous-Claude-v3

**Repo:** https://github.com/parcadei/Continuous-Claude-v3

**Debugging skill file paths:**
- 109 skills total, but no dedicated debugging SKILL.md appears in the skills directory listing
- Debugging support is distributed across the hook debugging workflow and the TLDR code analysis sub-system
- `llm-tldr` (separate repo): https://github.com/parcadei/llm-tldr — the code analysis tool

**Methodology:**
Continuous-Claude's debugging approach is architecturally different: it focuses on making code *readable by agents at low token cost* so that debugging requires less exploration. The TLDR system provides 5-layer code analysis:

- L1: AST (~500 tokens) — structural overview
- L2: Call Graph (+440 tokens) — dependency relationships
- L3: Control Flow Graph (+110 tokens) — execution paths
- L4: Data Flow Graph (+130 tokens) — data movement
- L5: Program Dependence Graph (+150 tokens) — combined slicing

**Unique techniques relative to our skill:**

**A. Structural Code Analysis Before Debugging**

Before starting phase 1 (reproduce), Continuous-Claude recommends running the TLDR analysis on the file/function containing the bug to understand: (1) what the function calls, (2) what calls the function, (3) how data flows through the function. This structural pre-analysis replaces manual code reading and is faster for large codebases.

This is different from our "check what changed" step because it's about understanding structure, not changes. It's particularly useful when debugging unfamiliar code you didn't write.

**B. Hook Debugging Workflow**

A specific sub-workflow for debugging hooks (event handlers, middleware, interceptors) that aren't firing correctly:
1. Verify the hook is registered (check the registration code, not just the handler)
2. Verify the event that should trigger the hook is actually firing (add logging upstream)
3. Verify the hook receives the correct context/payload
4. Verify the hook's output is being consumed correctly downstream

This pattern applies to any event-driven system (not just Claude Code hooks) and is more specific than our boundary logging pattern. Our boundary logging is generic; this is a structured checklist for a specific architectural pattern (event hooks).

**Escalation strategy:**
When a hook debugging workflow fails to resolve the issue, Continuous-Claude creates a detailed handoff document (ledger entry) and routes to a fresh agent with the full investigation record, preventing context window loss of debug findings.

---

### 6. NeoLabHQ/context-engineering-kit

**Repo:** https://github.com/NeoLabHQ/context-engineering-kit

**Debugging skill file paths:**
- The First Principles Framework (FPF) plugin — applies to debugging via the ADI cycle
- The Kaizen skill — root cause analysis commands

**Methodology:**
NeoLabHQ's approach to debugging is through structured reasoning frameworks rather than a dedicated debugging workflow. The FPF framework applies to debugging as follows:

**The ADI Cycle:**
1. **Abduction** — Generate 3-5 competing hypotheses simultaneously (don't anchor on the first idea)
2. **Deduction** — Verify logic and constraints for each hypothesis (does it make sense mechanically?)
3. **Induction** — Gather evidence through targeted experiments (does it work in reality?)

**Unique techniques relative to our skill:**

**A. Mandated Multi-Hypothesis Generation**

FPF explicitly requires generating 3-5 competing hypotheses BEFORE testing any of them. Our skill says "form one hypothesis" in Phase 3. This is an intentional design difference: FPF argues that generating multiple hypotheses first prevents anchoring on the first plausible explanation.

The practical application: before running any experiment, write out 3-5 specific, falsifiable hypotheses ranked by plausibility. Then design experiments that can rule out multiple hypotheses simultaneously (which overlaps with Strong Inference from GSD).

**B. Auditable Reasoning Trail**

FPF produces an audit trail from hypothesis to decision: every hypothesis considered, how it was tested, what evidence was gathered, and the final decision with rationale. The goal is to make debugging reasoning transparent and reviewable.

Our skill produces a root cause + regression test. FPF produces that PLUS a structured record of all competing hypotheses evaluated. This is similar to metaswarm's audit trail logging but focused on reasoning transparency rather than compliance.

**C. Kaizen: Cause and Effect Analysis**

The Kaizen skill includes Cause and Effect Analysis (also called Ishikawa/fishbone diagrams) for root cause analysis, supplementing the standard 5-Whys. Cause and Effect maps multiple contributing factors to a single effect, useful when the bug has multiple contributing causes (not just a single linear chain).

Our 5-Whys technique assumes a linear causal chain. Cause and Effect Analysis handles cases where multiple independent factors combine to produce a failure — for example, a bug that only appears when (1) a specific user role is active AND (2) a feature flag is enabled AND (3) the cache is warm.

**Anti-patterns documented:**
- Anchoring on the first plausible hypothesis without considering alternatives
- Testing hypotheses sequentially without first eliminating implausible ones via deduction

**Escalation strategy:**
FPF presents multiple hypothesis options to the user when the evidence doesn't conclusively support one. The user selects the direction — it's a human-in-the-loop decision point by design.

---

### 7. EveryInc/compound-engineering-plugin

**Repo:** https://github.com/EveryInc/compound-engineering-plugin

**Debugging skill file paths:**
- `/reproduce-bug` command in the plugin
- `/report-bug` command for bug reporting
- Skills directory: `plugins/compound-engineering/skills/`

**Methodology:**
Compound engineering's philosophy is "make each unit of work compound into the next." For debugging specifically, this means:

**A. Bug Knowledge Persistence (Compound Debugging)**

The `/reproduce-bug` command captures the full bug reproduction, investigation, and fix as a **compound artifact** — a record stored in the project's knowledge base so that (1) the same bug doesn't require re-investigation if it recurs, (2) the investigation pattern is available as a template for similar bugs, and (3) new agents on the project can reference past debug sessions.

This is the most ambitious formulation of "debug as learning" — not just writing a regression test, but creating a searchable, reusable record of the entire investigation process.

Our skill ends with "regression test every bug." Compound engineering adds a layer: record the WHY of the bug and the investigation path in a persistent knowledge base.

**Anti-patterns documented:**
- Debugging the same bug twice without building a record (debugging debt)

**Escalation strategy:**
Not explicitly documented beyond the standard re-investigation flow.

---

## Unique Techniques: Synthesis Across All Repos

The following techniques appear in the community and are **genuinely absent from our skill**:

### T1: Explicit Backward Call-Chain Tracing (obra)

**What it is:** Start at the error surface. Trace backward through each call frame to find the frame that first operated on bad data or triggered incorrect behavior. Fix at that origin point, not at the surface.

**Why it's different from our Phase 2:** Our Phase 2 looks for *differences* between working and broken. Backward tracing follows the *execution path* from the symptom to the cause. These are complementary, not identical — backward tracing works when the code hasn't changed but the data or state has changed.

**Confidence:** High (0.9) — documented as a named sub-skill with explicit technique description in obra/superpowers.

---

### T2: Isolated Debug Subagent with Persistent Session File (GSD)

**What it is:** When debugging begins, spawn a dedicated debug subagent with its own context window and a persistent file tracking hypotheses, experiments, and evidence. The main agent's context is not polluted by debug investigation noise.

**Why it's different from our skill:** Our skill treats debugging as an in-context activity. The isolation pattern matters for long debugging sessions where the investigation itself (failed hypotheses, exploratory logs, intermediate states) could consume the main agent's context window.

**Confidence:** Medium (0.75) — documented via GitHub snippet aggregation and DeepWiki.

---

### T3: Seeking Disconfirming Evidence / Strong Inference (GSD)

**What it is:**
- *Disconfirming evidence:* Before committing to an experiment, ask "What would prove my hypothesis wrong?" Then actively look for that evidence.
- *Strong inference:* Design a single experiment whose result rules out two or more competing hypotheses simultaneously.

**Why it's different from our skill:** We have hypothesis templates that produce falsifiable hypotheses. We do NOT explicitly prompt the agent to seek disconfirming evidence or design multi-ruling experiments. A falsifiable hypothesis can still be tested with confirmation bias.

**Confidence:** High (0.85) — described in detail in GSD debugger content and supported by scientific method literature.

---

### T4: Mandated Multi-Hypothesis Generation (NeoLabHQ FPF)

**What it is:** Before testing any hypothesis, generate 3-5 competing hypotheses. Eliminate implausible ones via logical deduction before running any experiments.

**Why it's different from our skill:** Our Phase 3 says "form one hypothesis." FPF's approach prevents anchoring on the first plausible explanation by mandating alternatives up front.

**When to use vs. our approach:** Single-hypothesis works fine for simple, localized bugs (wrong key name, off-by-one). Multi-hypothesis generation is better for complex bugs where the root cause is non-obvious and multiple explanations are plausible.

**Confidence:** Medium (0.75) — documented via NeoLabHQ content aggregation.

---

### T5: Production Debugging Protocol (wshobson)

**What it is:** A specific set of constraints for debugging in production:
1. Never change production code directly
2. Reproduce the issue locally using anonymized production data in a production-matching environment
3. Test fixes in staging with feature flags before production deployment
4. Gather evidence from error tracking and logs before forming hypotheses

**Why it's different from our skill:** Our skill assumes a development environment. Production debugging has hard constraints (no direct access, sensitive data, live user impact) that require a different approach.

**Confidence:** Medium (0.72) — documented in wshobson/agents skill content.

---

### T6: Rubber Duck Debugging (wshobson)

**What it is:** Write out (or "say out loud") a plain-language description of what the code is supposed to do, what it actually does, and where the gap is before forming any hypothesis.

**Why it's different from our skill:** Not in our skill at all. This technique's value is the cognitive mode shift from investigator to explainer, which surfaces incorrect assumptions.

**For AI agents specifically:** The rubber duck technique translates to: before Phase 3, write a 3-5 sentence narrative explaining the system behavior as if explaining to someone unfamiliar. The act of writing often reveals where the mental model breaks.

**Confidence:** High (0.88) — documented in wshobson/agents and widely known in software engineering.

---

### T7: Binary Search Debugging (wshobson)

**What it is:** Comment out / disable half the suspect code, test, and narrow iteratively. Distinct from git bisect — this operates on the current code state, not across commit history.

**Why it's different from our skill:** We have git bisect (which operates across time) but not in-code binary search (which operates within the current state of a file or function).

**When to use:** When the bug is definitely in a specific function/block but you can't identify which statement causes it. Faster than reading every line when functions are long.

**Confidence:** High (0.88) — documented in wshobson/agents.

---

### T8: Cause and Effect Analysis (NeoLabHQ Kaizen)

**What it is:** Map multiple contributing factors to a single failure using an Ishikawa/fishbone structure. Useful when the bug requires multiple conditions to be simultaneously true.

**Why it's different from our 5-Whys:** 5-Whys follows a single causal chain. Cause and Effect Analysis handles multi-factor failures where no single cause is sufficient — the bug only appears when conditions A AND B AND C are all true.

**Confidence:** Medium (0.70) — documented via NeoLabHQ content aggregation.

---

### T9: Bug Knowledge Persistence (EveryInc compound-engineering)

**What it is:** After resolving a bug, record not just a regression test but the full investigation record (hypotheses tested, evidence gathered, root cause, fix reasoning) in a searchable project knowledge base.

**Why it's different from our skill:** Our skill ends with "regression test every bug." This extends that to a reusable investigation artifact that prevents future agents from re-investigating the same class of bug.

**Confidence:** Medium (0.68) — inferred from compound-engineering philosophy and `/reproduce-bug` command description.

---

### T10: Condition-Based-Waiting as Named Pattern (obra — elevates existing mention)

**What it is:** Replace all `sleep(N)` / `time.sleep()` / `setTimeout(N)` calls in tests with condition-polling loops that wait until a condition becomes true (or a safety timeout fires).

**Our current coverage:** Our flaky test isolation section mentions "replace sleep() with condition-based waiting" as the fix for timing-dependent flaky tests. obra elevates this to a named, loadable pattern with specific implementation guidance.

**Why it matters to elevate:** A named pattern is more actionable for agents than a parenthetical mention. When an agent sees a timing flakiness, it can invoke the named pattern rather than having to reconstruct the approach from the mention.

**Confidence:** High (0.88) — documented as a specific sub-technique in obra/superpowers.

---

## Gap Analysis: What Our Skill Is Missing

| Gap | Severity | Source | Description |
|-----|----------|--------|-------------|
| No disconfirming evidence prompt | High | GSD | Agents with falsifiable hypotheses can still test with confirmation bias |
| No production debugging protocol | High | wshobson | Skill assumes dev environment; production has hard constraints |
| No multi-hypothesis generation | Medium | NeoLabHQ | Single hypothesis risks anchoring; need explicit alternative-generation step |
| No rubber duck technique | Medium | wshobson | Cognitive mode shift that surfaces incorrect assumptions |
| No binary search debugging | Medium | wshobson | In-code narrowing (not git bisect) for locating bug within a function |
| No debug context isolation | Medium | GSD | Long debugging sessions can pollute main agent context |
| No cause and effect analysis | Low-Medium | NeoLabHQ | Multi-factor bug analysis beyond linear 5-Whys |
| No bug knowledge persistence | Low | EveryInc | Investigation artifacts prevent re-investigation of same bug class |
| Backward call-chain tracing not named | Low | obra | Technique exists implicitly in Phase 2 but not as a named, invokable pattern |
| Condition-based-waiting not elevated | Low | obra | Mentioned in flaky test section but not elevated to named pattern |

### What We Already Cover Well

The following were found in multiple community repos and ARE in our skill:
- Hypothesis-driven debugging (all repos)
- 5-Whys (NeoLabHQ, our skill)
- Boundary logging / component isolation (obra, our skill)
- One-variable-at-a-time testing (all repos)
- Git bisect (our skill — not in most other repos)
- Flaky test categorization (our skill — not in most other repos)
- 3-Strikes escalation (our skill is more comprehensive than any other repo on escalation)
- Defense-in-depth post-fix (obra, our skill)
- Regression testing every bug (all repos)

Our skill is notably STRONGER than the community on: git bisect, flaky test isolation specifics, 3-Strikes escalation structure, and anti-patterns documentation.

---

## Recommended Additions

Listed in priority order (highest value-to-effort first):

### Priority 1 — Add to SKILL.md (Core Workflow)

**Rec 1: Disconfirming Evidence Prompt in Phase 3**

Add to Phase 3 (Hypothesize and Test), after "State your hypothesis explicitly":
> Before testing: ask "What would prove this hypothesis wrong?" Actively look for that evidence before running your experiment. If you find disconfirming evidence, update or discard the hypothesis before investing in an experiment.

This is a one-sentence addition that closes a meaningful gap. It counters confirmation bias without adding workflow overhead.

**Rec 2: Production Debugging Protocol — New Quick Reference Row**

Add to the Quick Reference table:
| Debugging in production (can't freely add logs or change code) | Use production debugging protocol: gather from logs first, reproduce locally with anonymized data, test fix in staging with feature flag |

And add a new "Production Debugging" subsection explaining the constraints and the safe investigation approach.

### Priority 2 — Add to references/patterns.md

**Rec 3: Rubber Duck Debugging**

Add a short pattern: "Before forming hypotheses, write a 3-5 sentence plain-language narrative explaining what the code should do, what it does, and where the gap is. The act of formulating the explanation often surfaces the root cause."

Note the agent-specific translation: writing a narrative is more practical than speaking out loud.

**Rec 4: Binary Search Debugging**

Add to patterns.md alongside the git bisect section:
```
## Binary Search Within Code (Current State)

When you can narrow the bug to a function or module but can't identify the specific line:

1. Comment out / mock half of the function's body
2. Test: if bug disappears, bug is in the commented half; if it persists, bug is in the remaining half
3. Restore and repeat with the offending half
4. Continue until the minimal bad statement is identified

When to use: The bug is definitely in THIS function/module, but reading every line hasn't identified it.
When NOT to use: You need to find which commit introduced the bug — use git bisect instead.
```

**Rec 5: Multi-Hypothesis Generation (for complex bugs)**

Add to patterns.md as an optional Phase 3 variant:
```
## Multi-Hypothesis Generation (For Complex Bugs)

When a bug has no obvious single cause, generate 3-5 competing hypotheses BEFORE testing any of them:

1. List all plausible root causes (aim for 3-5, at least)
2. Eliminate implausible ones via logical deduction (does the mechanism make sense?)
3. Rank remaining hypotheses by plausibility
4. Design experiments that can rule out multiple hypotheses simultaneously (Strong Inference)

When to use: After Phase 2 produces multiple suspicious differences with no clear winner.
When to use single-hypothesis (standard): When the error message or boundary log clearly points to one specific cause.
```

### Priority 3 — Add to references/anti-patterns.md

**Rec 6: New Anti-Pattern — Confirmation Bias Testing**

```
## Anti-Pattern 8: Confirmation Bias Testing

Designing experiments that can only confirm your hypothesis, not disprove it.

WRONG:
Hypothesis: "The cache is returning stale data"
Test: Check the cache value → yes, it's stale → "hypothesis confirmed"
Problem: You didn't check whether the API ALSO returns the stale value, which would implicate the upstream source, not the cache.

RIGHT:
Hypothesis: "The cache is returning stale data"
Disconfirming test: Check both the cache value AND the raw API response.
If API is fresh → cache is the bug.
If API is also stale → cache is not the bug; look upstream.
One test, both confirmed and disconfirmed.
```

### Priority 4 — Architecture Consideration (Not urgent)

**Rec 7: Debug Context Isolation Pattern**

Consider adding a note to the 3-Strikes escalation section or as a separate pattern:
> For long debugging sessions (3+ hypothesis cycles), consider isolating debug investigation into a dedicated context (separate session or file) to prevent debug noise from consuming the main working context. The isolation pattern: open a scratch debug file, document each hypothesis and its evidence in that file, maintain the main context for implementation work.

This is lower priority because our 3-Strikes rule already limits how long debugging consumes the main context.

**Rec 8: Bug Knowledge Persistence (if you have a memory system)**

If the project uses the coding-memory skill (which we do), add a note to Phase 4: "After resolving a complex bug (one that required multiple hypothesis cycles), record the bug pattern and investigation path as a memory entry so future agents can recognize and resolve the same class of bug faster."

---

## Source URLs

- [obra/superpowers systematic-debugging skill](https://github.com/obra/superpowers/blob/main/skills/systematic-debugging/SKILL.md)
- [obra/superpowers skills directory](https://github.com/obra/superpowers/tree/main/skills/systematic-debugging)
- [obra/superpowers root-cause-tracing sub-skill](https://github.com/obra/superpowers/blob/main/skills/debugging/root-cause-tracing/SKILL.md)
- [wshobson/agents debugging-strategies skill](https://github.com/wshobson/agents/blob/main/plugins/developer-essentials/skills/debugging-strategies/SKILL.md)
- [wshobson/agents error-handling-patterns skill](https://github.com/wshobson/agents/blob/main/plugins/developer-essentials/skills/error-handling-patterns/SKILL.md)
- [glittercowboy/get-shit-done gsd-debugger agent](https://github.com/glittercowboy/get-shit-done/blob/main/agents/gsd-debugger.md)
- [glittercowboy taches-cc-resources skills](https://github.com/glittercowboy/taches-cc-resources/tree/main/skills)
- [dsifry/metaswarm](https://github.com/dsifry/metaswarm)
- [parcadei/Continuous-Claude-v3](https://github.com/parcadei/Continuous-Claude-v3)
- [parcadei/llm-tldr](https://github.com/parcadei/llm-tldr)
- [NeoLabHQ/context-engineering-kit](https://github.com/NeoLabHQ/context-engineering-kit)
- [EveryInc/compound-engineering-plugin](https://github.com/EveryInc/compound-engineering-plugin)
- [skills.sh systematic-debugging listing](https://skills.sh/obra/superpowers/systematic-debugging)
- [antigravity.codes debugging-strategies](https://antigravity.codes/agent-skills/debugging/debugging-strategies)
- [Debug2Fix paper (Microsoft Research)](https://arxiv.org/abs/2602.18571)
- [GSD Troubleshooting DeepWiki](https://deepwiki.com/glittercowboy/get-shit-done/16-troubleshooting)
