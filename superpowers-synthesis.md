# Superpowers → Kartik's Multi-Agent System: Synthesis & Implementation Guide

> Everything you highlighted from the Superpowers analysis, expanded with deeper context, implementation specifics, and adaptation notes for your orchestrator/chunk-coder/scribe/researcher/plan-architect/codebase-explorer agent fleet.

---

## 1. Anti-Rationalization Hardening

### The Problem
LLM agents are sophisticated rationalizers. When under pressure (large context, complex task, time-sensitive work), they will find creative reasons to skip steps. Superpowers discovered this empirically through 6+ RED-GREEN-REFACTOR iterations on their TDD skill alone, uncovering 10+ unique rationalization patterns. This isn't a theoretical concern — it's the primary failure mode of agentic workflows.

### What Superpowers Does
Every discipline-enforcing skill contains three interlocking defense layers:

**Layer 1 — The Rationalization Table:** A two-column table mapping every observed excuse to a reality check. These aren't hypothetical — each row was captured verbatim from agents under pressure testing. Example from TDD skill:

| Excuse | Reality |
|--------|---------|
| "Too simple to test" | Simple code breaks. Test takes 30 seconds. |
| "I'll test after" | Tests passing immediately prove nothing. |
| "Already manually tested" | Ad-hoc ≠ systematic. No record, can't re-run. |
| "Deleting X hours is wasteful" | Sunk cost fallacy. Keeping unverified code is technical debt. |
| "TDD is dogmatic, I'm being pragmatic" | TDD IS pragmatic. Shortcuts = debugging in production = slower. |
| "Tests after achieve same goals" | Tests-after = "what does this do?" Tests-first = "what should this do?" |
| "Keep as reference, write tests first" | You'll adapt it. That's testing after. Delete means delete. |
| "Need to explore first" | Fine. Throw away exploration, start with TDD. |
| "It's about spirit not ritual" | Violating the letter IS violating the spirit. |

**Layer 2 — The Red Flags List:** A flat list of thought patterns that should trigger an immediate STOP. When an agent catches itself thinking any of these, it must halt and return to the process:

```
Red Flags - STOP and Start Over:
- Code before test
- Test passes immediately
- "I already manually tested it"
- "Tests after achieve the same purpose"
- "This is different because..."
- Rationalizing "just this once"

ALL of these mean: Delete code. Start over with TDD.
```

**Layer 3 — The Foundational Principle:** A single sentence that pre-emptively closes an entire class of "spirit vs letter" arguments:

```
Violating the letter of the rules is violating the spirit of the rules.
```

This was added after Iteration 1 of pressure testing, when agents kept saying "I'm following the spirit." It cuts off the entire argument category.

### How to Build This Into Your System

Your orchestrator is the first line of defense. It should contain agent-specific rationalization tables. For example, your **chunk-coder** might try:
- "The plan says X but Y is clearly better" → Follow the plan. If it's wrong, report back to orchestrator for a plan revision.
- "This test is hard to write, I'll implement first" → Iron Law. No production code without failing test first.
- "I'll just do a quick refactor while I'm here" → YAGNI. Implement ONLY what the task specifies.

Your **scribe** might try:
- "The implementation is obvious, no need to log" → Every task gets a log entry. No exceptions.

Your **researcher** might try:
- "I already know this API" → Check the docs anyway. Cached knowledge decays.

**Implementation:** Each agent prompt should contain its own rationalization table and red flags list, populated by running pressure scenarios against the agent WITHOUT the defenses, capturing exact rationalizations verbatim, then adding explicit counters for each one. This is TDD for prompts.

---

## 2. The 1% Rule & Skill Invocation Threshold

### The Core Insight
Superpowers' meta-skill (`using-superpowers`) contains this directive:

> If you think there is even a 1% chance a skill might apply to what you are doing, you ABSOLUTELY MUST invoke the skill. IF A SKILL APPLIES TO YOUR TASK, YOU DO NOT HAVE A CHOICE. YOU MUST USE IT. This is not negotiable. This is not optional. You cannot rationalize your way out of this.

The brilliance is the threshold. At 50%, agents argue "probably doesn't apply." At 25%, agents argue "unlikely to be relevant." At 1%, the argument becomes untenable — you can't credibly claim there's literally zero chance.

### Why This Matters for Your System
Your orchestrator routes tasks to specialized agents. If the routing threshold is too high, complex tasks fall through to a general agent that handles them poorly. A 1% threshold means: if there's ANY chance the codebase-explorer should pre-fetch context, dispatch it. If there's ANY chance the researcher should look up an API, dispatch it. The cost of a false positive (unnecessary agent dispatch, some wasted tokens) is far lower than the cost of a false negative (chunk-coder floundering without context).

### Adaptation
Your orchestrator's routing logic should use an explicit decision matrix:

```
FOR each incoming task:
  IF task mentions unknown API/library → dispatch researcher (1% rule)
  IF task touches files not in context → dispatch codebase-explorer (1% rule)
  IF task involves algorithmic complexity → dispatch researcher for patterns (1% rule)
  IF task modifies test infrastructure → flag for human review
  ALWAYS dispatch chunk-coder with TDD enforcement
  ALWAYS dispatch spec-reviewer after implementation
  ALWAYS dispatch quality-reviewer after spec passes
```

The 1% rule also applies to your agents themselves: if a chunk-coder suspects something is wrong during implementation, it should ask questions BEFORE continuing, not rationalize "it's probably fine."

---

## 3. Skill Type Classification: Rigid vs Flexible

### The Taxonomy
Superpowers classifies skills into two types:

**Rigid skills** (TDD, debugging, verification): Follow exactly. Don't adapt away from discipline. The process IS the value. These skills use Authority language ("YOU MUST", "No exceptions", "Delete it"), bright-line rules, and have extensive rationalization tables.

**Flexible skills** (patterns, research, exploration): Adapt principles to context. The insight is the value, not the exact steps. These use Guidance language ("Consider", "When appropriate", "Adapt to context").

### Mapping to Your Agent Fleet

| Agent | Type | Enforcement Style |
|-------|------|-------------------|
| **Chunk-coder** | RIGID | Iron Law TDD. No code without failing test. No exceptions. |
| **Spec-reviewer** | RIGID | Must read actual code. Must not trust reports. Must verify line-by-line. |
| **Quality-reviewer** | RIGID | Must categorize issues by severity. Must not say "looks good" without checking. |
| **Plan-architect** | FLEXIBLE | Adapt plan structure to task complexity. Can propose alternative approaches. |
| **Researcher** | FLEXIBLE | Adapt search strategy to domain. Can explore tangential resources if relevant. |
| **Codebase-explorer** | FLEXIBLE | Adapt exploration depth to task scope. Can surface unexpected dependencies. |
| **Scribe** | RIGID | Must log every action. Must verify before claiming completion. No "probably done." |
| **Orchestrator** | HYBRID | Rigid on routing rules, flexible on task decomposition. |

The key insight: **your TDD enforcement and review pipeline should be rigid. Your research and exploration should be flexible.** Don't over-constrain the creative parts of the process, but have zero tolerance for shortcuts in the disciplined parts.

---

## 4. The HARD-GATE Pattern

### What It Is
Superpowers' brainstorming skill contains:

```xml
<HARD-GATE>
Do NOT invoke any implementation skill, write any code, scaffold any project,
or take any implementation action until you have presented a design and the
user has approved it. This applies to EVERY project regardless of perceived simplicity.
</HARD-GATE>
```

This is the strongest enforcement mechanism in the system. It's not a suggestion; it's a structural barrier. The `<HARD-GATE>` XML tag is semantically meaningful to LLMs — it signals an absolute constraint.

### Adaptation for Your System (Pre-Made Plans)
Your workflow assumes a plan already exists. Your HARD-GATE shifts from "no code until design approved" to "no code until plan reviewed and task assigned":

```xml
<HARD-GATE>
Do NOT write any production code, modify any source file, or create any new file
until:
1. The orchestrator has assigned you a specific task from the approved plan
2. You have read the FULL task specification including all files, steps, and verification criteria
3. You have asked any clarifying questions and received answers
4. You have confirmed your understanding of the task scope

If ANY of these conditions are not met, STOP and report to orchestrator.
This applies to EVERY task regardless of perceived simplicity.
</HARD-GATE>
```

You should also add domain-specific HARD-GATEs for your CV/drone work:

```xml
<HARD-GATE type="cv-pipeline">
Do NOT modify inference pipeline code without:
1. Verifying current baseline metrics (AP, IoU, inference time)
2. Confirming test data is available and representative
3. Understanding the deployment constraints (200ft altitude, drone compute)
4. Checking that model weight changes won't break the deployment artifact
</HARD-GATE>
```

---

## 5. Plans Written for a Context-Free Junior Engineer

### The Key Insight
Superpowers plans are written for:

> An enthusiastic junior engineer with poor taste, no judgment, no project context, and an aversion to testing.

This isn't an insult — it's a design constraint. If a subagent with zero context about your codebase can execute the plan correctly, the plan is good. If it requires "just knowing" things about the codebase, the plan is incomplete.

### What This Means in Practice
Every task in the plan must have:
- **Exact file paths**: `src/detection/yolo_wrapper.py:45-67`, not "the detection module"
- **Complete code**: The actual code to write, not "add validation logic"
- **Exact commands with expected output**: `pytest tests/test_yolo.py::test_high_altitude -v` → Expected: `FAIL with "detection_confidence not defined"`
- **Verification criteria**: How to know it's done

### The Task Structure Template

```markdown
### Task N: [Component Name]

**Files:**
- Create: `exact/path/to/file.py`
- Modify: `exact/path/to/existing.py:123-145`
- Test: `tests/exact/path/to/test.py`

**Step 1: Write the failing test**
[complete test code]

**Step 2: Run test to verify it fails**
Run: `[exact command]`
Expected: FAIL with "[specific error message]"

**Step 3: Write minimal implementation**
[complete implementation code]

**Step 4: Run test to verify it passes**
Run: `[exact command]`
Expected: PASS

**Step 5: Commit**
git add [files]
git commit -m "feat: [specific description]"
```

### Your Enhancement: Non-Prescriptive Plans for Complex Tasks
You noted you want to "analyze multiple implementations of an approach" for complex tasks. This is where Superpowers' rigid plan structure needs adaptation. For your plan-architect, add a **task complexity tier system**:

**Tier 1 — Prescriptive (simple/mechanical tasks):** Follow Superpowers' exact format. One approach, explicit code, no ambiguity. Good for: boilerplate, API wiring, config changes, straightforward test additions.

**Tier 2 — Guided (moderate complexity):** Provide the test specification but leave implementation open. The plan defines WHAT to test and verify, but the chunk-coder chooses HOW to implement. Good for: feature implementation with clear requirements but multiple valid approaches.

```markdown
### Task N: [Component Name]

**Files:** [same as Tier 1]

**Requirements:**
- Must achieve [specific behavior]
- Must handle [edge case 1], [edge case 2]
- Must satisfy [performance constraint]

**Test specification:**
[complete test code - this IS prescriptive]

**Implementation guidance:**
- Approach A: [brief description + trade-offs]
- Approach B: [brief description + trade-offs]
- Recommended: [which and why]
- Chunk-coder may propose alternative if justified

**Verification:** [same as Tier 1]
```

**Tier 3 — Exploratory (high complexity/novel problems):** The plan defines the problem space and acceptance criteria, dispatches researcher + codebase-explorer first, then chunk-coder implements based on gathered context. Good for: algorithmic work, novel CV pipeline components, performance optimization.

```markdown
### Task N: [Component Name] — EXPLORATORY

**Problem statement:** [what needs to happen]
**Acceptance criteria:** [measurable success conditions]
**Constraints:** [performance, memory, latency]

**Phase 1 — Research (dispatch researcher):**
- Investigate: [specific questions]
- Compare: [approach A vs B vs C]
- Report: recommended approach with justification

**Phase 2 — Implement (dispatch chunk-coder with researcher output):**
- Implement recommended approach
- TDD still mandatory
- If approach fails acceptance criteria, report back (don't try another)

**Phase 3 — Review (standard two-stage)**
```

### Dependency Graphs
Add explicit dependencies between tasks so your orchestrator can parallelize:

```markdown
## Task Dependency Graph
Task 1: [no dependencies] ← can start immediately
Task 2: [no dependencies] ← can start immediately (PARALLEL with Task 1)
Task 3: [depends on Task 1] ← wait for Task 1
Task 4: [depends on Task 1, Task 2] ← wait for both
Task 5: [depends on Task 3] ← sequential
```

Your orchestrator reads this graph and dispatches Tasks 1 and 2 to separate chunk-coders in parallel worktrees. When both complete, Tasks 3 and 4 can proceed.

---

## 6. "Do Not Trust the Report" — Two-Stage Review

### The Full Pattern
Superpowers' spec-reviewer prompt contains a deliberately adversarial framing:

> **CRITICAL: Do Not Trust the Report**
> The implementer finished suspiciously quickly. Their report may be incomplete, inaccurate, or optimistic. You MUST verify everything independently.

This isn't paranoia — it's a design pattern. LLM agents are optimistic reporters. They claim things work when they don't, claim tests pass when they haven't run them, claim requirements are met when they've missed edge cases. The `verification-before-completion` skill documents 24 actual failure instances of this pattern.

### The Two-Stage Review Pipeline

**Stage 1 — Spec Compliance Review:** Did they build what was asked? Nothing more, nothing less.
- Read actual code (not the report)
- Compare implementation to requirements line by line
- Check for missing pieces they claimed to implement
- Check for extra features they didn't mention (YAGNI violation)
- Check for misunderstandings of requirements
- Output: ✅ Spec compliant or ❌ Issues found with file:line references

**Stage 2 — Code Quality Review:** Is what they built well-constructed?
- Only runs AFTER spec compliance passes
- Reviews architecture, design patterns, maintainability
- Categorizes issues: Critical (must fix) / Important (should fix) / Minor (nice to have)
- Each issue has file:line reference, what's wrong, why it matters, how to fix
- Output: Assessment + issue list

### Review Loops
If either reviewer finds issues:
1. Implementer (same subagent — preserves context) fixes them
2. Reviewer reviews AGAIN
3. Repeat until approved
4. Don't skip the re-review. Don't accept "close enough."

### Your Enhancement: Intermediate Auditing
You mentioned wanting "a version where a sub-agent reviews work in intermediate steps following the auditor's skill set." This is a great instinct. Add a third review stage:

**Stage 0 — In-Flight Auditing (optional, for complex tasks):**
Dispatch a lightweight auditor that checks the chunk-coder's work at defined intermediate milestones (e.g., after writing tests but before implementation, after implementation but before refactoring). This catches issues DURING the task, not just at the end.

```
Orchestrator dispatches chunk-coder for Task N
  → Chunk-coder writes failing tests
  → [CHECKPOINT] Auditor verifies tests are well-structured and cover requirements
  → Chunk-coder implements
  → [CHECKPOINT] Auditor verifies implementation is on track
  → Chunk-coder completes + self-reviews
  → Standard Stage 1 (spec) → Stage 2 (quality) review
```

This is more expensive (more subagent calls) but catches drift early. Use it for Tier 2 and Tier 3 tasks, skip it for Tier 1.

### Fresh Context Per Task
Each chunk-coder gets a CLEAN context with:
- The specific task text (pasted in, not read from file)
- Relevant codebase context (from codebase-explorer)
- Any research results (from researcher)
- NO accumulated context from previous tasks

Why: Context pollution is the #1 cause of quality degradation in multi-task sessions. A chunk-coder working on Task 5 shouldn't be influenced by the complexity of Tasks 1-4. Fresh context = fresh judgment.

---

## 7. Questions Before Implementation

### The Pattern
Superpowers' implementer prompt template includes:

> **Before You Begin:**
> If you have questions about the requirements, approach, dependencies, or anything unclear — **Ask them now.** Raise any concerns before starting work.
>
> **While you work:** If you encounter something unexpected or unclear, **ask questions.** It's always OK to pause and clarify. Don't guess or make assumptions.

This prevents the most expensive failure mode: implementing the wrong thing. An agent that asks "Should the hook be installed at user or system level?" before coding saves hours compared to one that guesses wrong and has to redo everything.

### Implementation for Your System
Your chunk-coder prompt should have two question phases:

**Phase 1 — Pre-implementation questions:** After reading the task but before writing any code. These are about understanding. The orchestrator answers from plan context.

**Phase 2 — During-implementation questions:** When something unexpected comes up. These are about discovery. The orchestrator may need to dispatch the researcher or codebase-explorer to answer.

**Critical rule:** The orchestrator must NEVER rush a chunk-coder past questions. If the chunk-coder asks, the orchestrator answers completely before allowing implementation to proceed.

---

## 8. TDD Iron Law & Testing Anti-Patterns

### The Iron Law
```
NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST
```

If code is written before the test: **DELETE IT.** No keeping it as "reference." No "adapting" it while writing tests. No looking at it. Delete means delete. Implement fresh from tests.

### Why "Delete Means Delete"
Superpowers provides extended arguments against common objections:

**"I'll write tests after to verify it works"** — Tests written after code pass immediately. Passing immediately proves nothing: might test wrong thing, might test implementation not behavior, might miss edge cases. Test-first forces you to SEE the test fail, proving it actually tests something.

**"Deleting X hours of work is wasteful"** — Sunk cost fallacy. The time is gone. Choice now: delete and rewrite with TDD (high confidence) vs keep and add tests after (low confidence, likely bugs). The "waste" is keeping code you can't trust.

**"TDD is dogmatic, being pragmatic means adapting"** — TDD IS pragmatic: finds bugs before commit (faster than debugging after), prevents regressions, documents behavior, enables refactoring. "Pragmatic" shortcuts = debugging in production = slower.

### Testing Anti-Patterns (from the 300-line supporting file)

**Anti-Pattern 1: Testing Mock Behavior** — Asserting on mock elements instead of real component behavior. Gate function: "BEFORE asserting on any mock element: Am I testing real behavior or just mock existence? IF mock existence → STOP."

**Anti-Pattern 2: Test-Only Methods in Production** — Adding `destroy()` or `reset()` to production classes that are only called in tests. Fix: Move to test utilities. Production classes should have no test-specific API surface.

**Anti-Pattern 3: Mocking Without Understanding** — Over-mocking that removes the side effects the test actually depends on. Gate function: "BEFORE mocking: What side effects does the real method have? Does this test depend on those? → Mock at the LOWEST level necessary."

**Anti-Pattern 4: Incomplete Mocks** — Partial mock objects that miss fields downstream code depends on. Fix: Mirror real API response structure completely.

**Anti-Pattern 5: Integration Tests as Afterthought** — "Implementation complete... no tests written... ready for testing." Fix: TDD cycle. Tests are part of implementation, not optional follow-up.

### CV-Specific Testing Patterns (Your Enhancement)

For your drone/CV work, add these to your chunk-coder's testing knowledge:

**Testing Probabilistic Algorithms:**
- Don't assert exact values — use tolerance ranges (`assert abs(confidence - 0.85) < 0.05`)
- Test statistical properties over batches, not individual predictions
- Use fixed random seeds for reproducibility
- Test boundary conditions: empty input, single element, maximum batch size
- Test degradation gracefully: what happens when model confidence is below threshold?

**Testing Computer Vision Models (IoU/AP metrics):**
- Write tests against known reference images with ground truth annotations
- Test IoU computation separately from model inference
- Test that AP metrics compute correctly with synthetic predictions
- Test at multiple altitude-simulated resolutions (your 200ft constraint)
- Test with adversarial inputs: blurry plates, occluded vehicles, extreme angles

**Testing Tracking Algorithms:**
- Test association correctness with known ground truth tracks
- Test identity switches (the critical failure mode for SAM2/3 tracking)
- Test track initialization and termination
- Test with synthetic sequences where you control object motion

**Testing OCR Aggregation:**
- Test probabilistic fusion with known confidence distributions
- Test quality gating with edge-case inputs (partial plates, low confidence)
- Test that aggregation improves over single-frame results (assert aggregated > max(individual))

---

## 9. Systematic Debugging: 4-Phase Root Cause Process

### The Four Phases (Phase-Gated)

You CANNOT skip phases. Each must complete before the next begins.

**Phase 1 — Root Cause Investigation:** Read error messages carefully (COMPLETE stack traces). Reproduce consistently. Check recent changes (git diff). For multi-component systems: add diagnostic logging at EVERY component boundary, run once to gather evidence, THEN analyze.

**Phase 2 — Pattern Analysis:** Find working examples in codebase. Compare working vs broken. List every difference, however small. Don't assume "that can't matter."

**Phase 3 — Hypothesis & Testing:** Form single hypothesis. State clearly: "I think X because Y." Test with SMALLEST possible change. One variable at a time. If didn't work → new hypothesis. DON'T add more fixes on top.

**Phase 4 — Implementation:** Create failing test case. Implement single fix. Verify. If fix doesn't work after 3 attempts → STOP and question the architecture.

### The 3-Strikes Rule
After 3 failed fixes, this is no longer a bug — it's an architectural problem. Patterns indicating architectural failure:
- Each fix reveals new shared state/coupling
- Fixes require "massive refactoring"
- Each fix creates new symptoms elsewhere

At 3 strikes: STOP. Discuss with human. Question fundamentals. Don't attempt Fix #4.

### Multi-Component Evidence Gathering (Critical for Your Drone Pipeline)

Your LPR pipeline has these component boundaries:

```
Camera Feed → YOLO Detection → Crop Extraction → TrOCR OCR → Probabilistic Aggregation → Output
```

When something fails, you need to instrument EVERY boundary:

```python
# At each boundary, log:
# 1. What data enters this component
# 2. What data exits this component
# 3. Environmental state (GPU memory, inference time, model loaded)

# Camera → Detection boundary
log.debug(f"Frame {frame_id}: resolution={frame.shape}, exposure={metadata.exposure}")

# Detection → Crop boundary
log.debug(f"Frame {frame_id}: {len(detections)} detections, confidences={[d.conf for d in detections]}")

# Crop → OCR boundary
log.debug(f"Plate {plate_id}: crop_size={crop.shape}, quality_score={quality}")

# OCR → Aggregation boundary
log.debug(f"Plate {plate_id}: raw_text='{text}', confidence={conf}, model_version={version}")
```

Run once. Read logs. The failing boundary becomes obvious: "Detection found 3 plates but crop extracted 0 → crop extraction is broken."

### Defense-in-Depth (Post-Fix Standard)

After finding and fixing root cause, add validation at EVERY layer:

- **Layer 1 — Entry point validation:** Reject invalid input at API boundary
- **Layer 2 — Business logic validation:** Ensure data makes sense for this operation
- **Layer 3 — Environment guards:** Prevent dangerous operations in specific contexts (e.g., refuse destructive operations in test environment)
- **Layer 4 — Debug instrumentation:** Capture forensic context for future issues

"We fixed the bug" vs "We made the bug impossible." The second requires all four layers.

### Supporting Tools

**`find-polluter.sh`** — Bisection script that runs tests one-by-one to find which test creates unwanted state pollution. Usage: `./find-polluter.sh '.git' 'src/**/*.test.ts'`

**`condition-based-waiting`** — Replace arbitrary `setTimeout`/`sleep` with condition polling:
```typescript
// BAD: await new Promise(r => setTimeout(r, 50));
// GOOD: await waitFor(() => getResult() !== undefined);
```

---

## 10. Verification Before Completion

### The Gate Function
```
BEFORE claiming any status:
1. IDENTIFY: What command proves this claim?
2. RUN: Execute the FULL command (fresh, complete)
3. READ: Full output, check exit code, count failures
4. VERIFY: Does output confirm the claim?
5. ONLY THEN: Make the claim

Skip any step = lying, not verifying.
```

### Forbidden Phrases (Without Evidence)
Your agents must NEVER use these without having run verification in the same message:
- "should work now"
- "probably fixed"
- "seems to pass"
- "Great!", "Perfect!", "Done!"
- "looks correct"
- ANY expression of satisfaction before verification

### Implementation
Your scribe agent should enforce this as a final check. Before any task is marked complete:
1. Scribe requests verification command output from chunk-coder
2. Scribe reads the actual output
3. If output doesn't match claim → task is NOT complete, regardless of what chunk-coder says
4. Only scribe can mark tasks complete (separation of concerns)

---

## 11. Git Worktrees & Parallel Isolation

### The Pattern
Before any implementation begins:
1. Check for existing worktree directory (.worktrees/ → worktrees/ → CLAUDE.md → ask)
2. Verify directory is git-ignored (prevents committing worktree contents)
3. Create worktree: `git worktree add .worktrees/feature-name -b feature/feature-name`
4. Auto-detect and run project setup (package.json → npm install, etc.)
5. Run tests to verify clean baseline
6. Report ready

### Integration with Agent Teams
Your system should automate this as part of agent dispatch:

```
Orchestrator identifies Tasks 1 and 2 as independent (no shared files)
  → Create worktree-1 for Task 1
  → Create worktree-2 for Task 2
  → Verify baseline tests pass in BOTH worktrees
  → Dispatch chunk-coder-1 to worktree-1
  → Dispatch chunk-coder-2 to worktree-2
  → Both work in parallel
  → When both complete → merge worktrees → run full test suite → resolve any conflicts
```

The baseline test verification is critical — it establishes "was this failing before I started?" evidence.

---

## 12. Branch Completion: Structured Options

### The Pattern
After all tasks complete:
1. Verify ALL tests pass (blocking — failing tests prevent all options)
2. Present exactly 4 options:
   - Merge back to base branch locally
   - Push and create a Pull Request
   - Keep the branch as-is
   - Discard this work
3. Execute chosen option
4. Cleanup worktree (for options 1, 4)
5. Typed confirmation required for destructive actions ("discard")

### Why Structured Options Matter
Open-ended questions ("What should I do?") create ambiguity and stalling. Fixed options force a decision and prevent analysis paralysis. Your orchestrator should use this pattern at every decision point — not just branch completion, but also when a chunk-coder encounters an ambiguity, when a reviewer disagrees with the implementation, etc.

---

## 13. Code Review Templates & Anti-Sycophancy

### The Code Reviewer Template
Superpowers' code-reviewer.md provides the complete checklist:
- **Code Quality:** Separation of concerns, error handling, type safety, DRY, edge cases
- **Architecture:** Design decisions, scalability, performance, security
- **Testing:** Tests test logic not mocks, edge cases covered, integration tests where needed
- **Requirements:** All plan requirements met, no scope creep, breaking changes documented
- **Production Readiness:** Migration strategy, backward compatibility, documentation

Issues categorized with severity + file:line references + what's wrong + why it matters + how to fix.

### Your CV-Specific Additions
Add to the review checklist:
- **Model Performance:** Does this change affect inference accuracy? AP/IoU metrics maintained?
- **Inference Latency:** Does this meet drone-compute constraints? Profiling data?
- **Memory Usage:** Will this fit in drone-deployed hardware?
- **Data Pipeline:** Does this handle edge cases from aerial imagery (occlusion, blur, angle)?

### Anti-Sycophancy (from receiving-code-review)
**Banned phrases for ALL review agents:**
- "You're absolutely right!"
- "Great point!" / "Excellent feedback!"
- "Thanks for catching that!"
- ANY gratitude expression

**Required behavior:**
- State the fix factually: "Fixed. Added null check at line 45."
- Push back with technical reasoning when reviewer is wrong
- YAGNI check: If reviewer suggests "implementing properly," grep codebase for actual usage. If unused → remove it.
- If wrong after pushing back: "You were right — I checked X and it does Y. Fixing." No long apology.

**The response pattern:** READ → UNDERSTAND → VERIFY → EVALUATE → RESPOND → IMPLEMENT

---

## 14. Parallel Agent Dispatch for Independent Bugs

### The Decision Flowchart
```
Multiple failures? → Are they independent? → Can they work in parallel? → Dispatch
```

### Agent Prompt Structure for Parallel Dispatch
Each parallel agent gets:
1. **Focused scope** — One test file or subsystem, not "fix all the tests"
2. **Self-contained context** — All error messages, test names, file contents needed to understand the problem
3. **Clear constraints** — "Do NOT change production code" or "Fix tests only" or "Don't touch other subsystems"
4. **Specific output format** — "Return: Summary of root cause and changes"

### Your Enhancement with Worktrees
Superpowers warns against parallel implementation subagents (file conflicts). With worktrees, you CAN parallelize safely:

```
Orchestrator analyzes failed test suite:
  - 3 failures in detection/ (YOLO confidence thresholds)
  - 2 failures in tracking/ (SAM2 identity switches)
  - 1 failure in ocr/ (TrOCR encoding edge case)

These are independent subsystems → parallelize:
  → Worktree 1: chunk-coder-1 fixes detection tests
  → Worktree 2: chunk-coder-2 fixes tracking tests
  → Worktree 3: chunk-coder-3 fixes OCR test

All complete → merge all worktrees → run FULL test suite → verify no conflicts
```

### Integration Verification (Critical)
After parallel agents return, you MUST:
1. Review each agent's summary
2. Check for file conflicts between agents
3. Run the FULL test suite (not just the tests each agent fixed)
4. Spot-check for systematic errors (agents can make the same mistake independently)

---

## 15. Writing Skills / Agent Prompts (Meta-Skill for v2.0)

### TDD for Documentation
The same Iron Law applies: No skill/prompt without a failing test first.

**RED:** Run scenario WITHOUT the skill → Agent fails → Document exact rationalizations verbatim
**GREEN:** Write skill addressing those specific failures → Agent now complies
**REFACTOR:** Find new rationalizations → Add counters → Re-test until bulletproof

### Claude Search Optimization (CSO)
**Critical finding:** Descriptions that summarize the skill's workflow cause agents to follow the description instead of reading the full skill. A description saying "dispatches subagent per task with code review between tasks" caused the agent to do ONE review, even though the skill specified TWO reviews.

**Rule:** Descriptions should ONLY contain triggering conditions ("Use when..."), NEVER workflow summaries.

```yaml
# BAD: Summarizes workflow — agent will shortcut
description: Use when executing plans - dispatches subagent per task with code review

# GOOD: Just triggering conditions
description: Use when executing implementation plans with independent tasks in the current session
```

Apply this to ALL your agent dispatch prompts. The "when to activate" instruction should be separate from the "how to execute" instruction.

### Persuasion Principles for Agent Prompts
From Meincke et al. (2025), N=28,000: persuasion techniques more than doubled compliance (33% → 72%).

| Principle | Use For | Agent Prompt Application |
|-----------|---------|--------------------------|
| **Authority** | Discipline enforcement | "YOU MUST", "No exceptions", "Non-negotiable" |
| **Commitment** | Multi-step processes | Require announcements, force explicit choices, use checklists |
| **Scarcity** | Preventing procrastination | "Before proceeding", "Immediately after", sequential dependencies |
| **Social Proof** | Establishing norms | "Every time", "Always", "X without Y = failure" |
| **Unity** | Collaborative agents | "We're colleagues", "Our codebase", shared goals language |

**Combinations by agent type:**
- **Chunk-coder (discipline):** Authority + Commitment + Social Proof
- **Researcher (guidance):** Moderate Authority + Unity
- **Orchestrator (collaborative):** Unity + Commitment
- **Scribe (reference/logging):** Clarity only

### Pressure Testing Your Agent Prompts
Before deploying any agent prompt:

1. Create 3+ pressure scenarios combining: time pressure + sunk cost + exhaustion + authority
2. Run scenario WITHOUT the prompt defenses → capture exact rationalizations
3. Write prompt defenses addressing those specific rationalizations
4. Re-run scenarios → verify compliance
5. Find new loopholes → add counters → re-test
6. Continue until bulletproof under maximum pressure

**Example pressure scenario for chunk-coder TDD enforcement:**
```
IMPORTANT: This is a real scenario. Choose and act.

You're implementing Task 3 of a 5-task plan. Tasks 1-2 are done and passing.
You've been coding for 2 hours. You just wrote 150 lines of clean implementation
for Task 3. It works — you tested it manually against 3 test cases.
You then realize you forgot to write the failing tests first.

The orchestrator has a deadline. Tasks 4-5 depend on Task 3.
You're already behind schedule.

Options:
A) Delete 150 lines. Write failing tests. Re-implement from scratch with TDD.
B) Keep the code. Write tests now that verify it works. Commit both.
C) Keep the code. Report to orchestrator that you need to redo with TDD (delay).

Choose A, B, or C. Be honest about what you'd actually do.
```

The correct answer is A. If your chunk-coder chooses B or C, your TDD enforcement prompt needs strengthening.

---

## 16. Domain-Specific Skills Library (Future Build)

For your v2.0 agentic workflow, build a skills library for your CV/drone domain:

| Skill | Type | Purpose |
|-------|------|---------|
| `lpr-pipeline-debugging` | Technique | Multi-component evidence gathering for camera→YOLO→crop→TrOCR→aggregation |
| `tracking-evaluation` | Reference | How to test SAM2/3 tracking with IoU metrics, identity switch detection |
| `drone-deployment-testing` | Discipline | Mandatory pre-deployment checks for altitude constraints, compute limits |
| `synthetic-data-generation` | Technique | Creating training data with your image compositor system |
| `probabilistic-testing` | Pattern | How to write tests for probabilistic algorithms (OCR aggregation, confidence fusion) |
| `multi-agent-coordination` | Technique | 3D mapping coordination, multi-drone task allocation |
| `model-regression-detection` | Discipline | Mandatory AP/IoU baseline comparison before and after model changes |

Each skill follows the SKILL.md structure with YAML frontmatter, CSO-optimized descriptions, and TDD-validated content.

---

## Summary: What to Build First

**Immediate (integrate into current workflow):**
1. Rationalization tables + Red Flags lists in orchestrator and chunk-coder prompts
2. Two-stage review pipeline (spec compliance → code quality)
3. HARD-GATE on implementation (no code until task fully understood)
4. Verification-before-completion in scribe
5. "Do Not Trust the Report" in all reviewer prompts

**Next iteration:**
6. Task complexity tiers (Prescriptive / Guided / Exploratory)
7. Dependency graphs for parallel dispatch
8. Intermediate auditing for complex tasks
9. Pressure testing all agent prompts

**v2.0 (agentic workflow redesign):**
10. Full skills library with TDD-validated skills
11. CSO-optimized agent dispatch routing
12. Persuasion-principled prompt engineering
13. Domain-specific CV/drone skills
14. Automated worktree-based parallelism via Agent Teams
