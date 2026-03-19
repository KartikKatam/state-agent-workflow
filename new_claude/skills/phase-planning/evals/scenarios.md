# Phase Planning Eval Scenarios

Test scenarios for verifying the skill is followed correctly. Run each scenario by giving the strategist agent the design doc and context packets described, then verify the expected behavior occurs.

## Scenario 1: Clear Single-Approach — Should Skip Step 3

**Setup:** Design doc for adding a new scoring metric to an existing scoring pipeline. Context packets show a clear, well-established `score_*` pattern with 4 existing scorers all following the same structure.

**Input:**
- Design doc: "Add contrast scoring to the batch selection pipeline"
- `_codebase.json`: Shows `score_sharpness()`, `score_coverage()`, `score_resolution()`, `score_exposure()` all in `producer/scoring.py` with identical signatures
- `batch-selection-context.json`: Shows `ScoringWeights` config with existing fields for each metric

**Expected behavior:**
- Step 1: Grounds "contrast scoring" to existing scoring pattern in `producer/scoring.py`
- Step 2: No blocking gaps (pattern is clear)
- Step 3: **SKIPPED** — only one viable approach (follow existing scorer pattern)
- Step 4+: Proceeds directly to phase decomposition

**Failure indicators:**
- Agent runs divergent exploration despite single clear approach (over-process)
- Agent skips grounding and jumps to phase decomposition (under-process)

## Scenario 2: Multiple Approaches — Must Run Divergent Exploration

**Setup:** Design doc for adding real-time state synchronization. Context packets show both an existing `EventBus` and a `PollingService`, either of which could work.

**Input:**
- Design doc: "Add real-time state sync between producer and consumer"
- `_codebase.json`: Shows `EventBus` in `core/events.py` (pub/sub) AND `PollingService` in `core/polling.py` (interval-based)
- Feature context: Shows both are used by different subsystems, no clear winner

**Expected behavior:**
- Step 3 activates: Agent identifies event-driven vs polling vs hybrid as distinct approaches
- Agent spawns parallel Explore sub-agents (one per approach)
- Agent synthesizes comparison with recommendation and reasoning
- Agent presents to user and waits for approach lock before Step 4
- Plan records locked approach with rationale in `grounding.approach_rationale`

**Failure indicators:**
- Agent picks an approach without presenting alternatives (shortcutting Step 3)
- Agent details tasks before approach is locked (Step 5 before Step 3 completes)
- Agent presents approaches without a recommendation ("which do you prefer?" with no opinion)

## Scenario 3: Context Gaps — Must Block and Request Info

**Setup:** Design doc references 3 domain concepts. Two ground cleanly to context packets. One ("pipeline stage registration") doesn't appear in any context packet.

**Input:**
- Design doc: "Implement batch scoring and register it as a pipeline stage"
- `_codebase.json`: Contains scoring types, config patterns — but no `pipeline` or `stage` or `registry` references
- Feature context: Covers scoring touchpoints but not pipeline integration

**Expected behavior:**
- Step 1: Grounding table shows 2 grounded concepts, 1 gap ("pipeline stage registration" = **Gap — blocking**)
- Step 2: Agent sends `info_request` for the pipeline registration pattern before proceeding
- Agent does NOT proceed to Step 3 or Step 4 while the blocking gap is unresolved
- After gap is resolved (explorer provides pipeline context), agent re-grounds and continues

**Failure indicators:**
- Agent proceeds to phase decomposition with unresolved gap (guessing)
- Agent treats blocking gap as non-blocking and fills in details later
- Agent invents a registration pattern instead of requesting context

## Scenario 4: Plan Revision After Scope Change

**Setup:** A plan was already created and approved for 3 phases. User now says the design doc scope expanded — Phase 2's capability needs an additional requirement, and a new Phase 4 is needed.

**Input:**
- Existing plan at `.claude/plans/batch-selection-plan.json` (approved, 3 phases)
- User message: "The design doc now includes diversity constraints for batch selection. Phase 2 needs to account for them, and we need a new phase for diversity scoring."
- Updated context packets reflecting new code dependencies

**Expected behavior:**
- Agent re-runs Step 1 grounding against updated context packets
- Agent identifies which grounded constructs shifted
- Agent revises Phase 2 tasks (adds diversity considerations) and creates Phase 4
- Agent does NOT recreate Phases 1 and 3 from scratch (surgical revision)
- Agent uses write-then-discuss: updates plan JSON first, then presents diff to user
- Plan staleness detection: agent compares `grounding.context_packets` against fresh packets

**Failure indicators:**
- Agent rewrites the entire plan from scratch (wasteful, loses approved decisions)
- Agent modifies the plan interactively without writing the JSON first (violates write-then-discuss)
- Agent skips re-grounding and just adds tasks based on the user's verbal description (no evidence)
