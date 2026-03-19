# Scope-Specific Testing Patterns

Guidance for testing at per-task, per-phase, and full-design scope.

## Per-Task Testing

After a single task completes. Fastest, most focused.

- Test ONLY the functions/behavior changed in that task
- Reference the task spec, not the full design
- **Tier 1 + Tier 2 only** — skip T3/T4 for per-task scope. Why: adversarial
  and property-based tests add value at integration boundaries, not isolated
  function changes.
- Sub-agent delegation: 1-2 sub-agents sufficient
- Verification pipeline: Steps 1 + 2 (AST scan + empty-stub). Skip mutation
  testing — too expensive for per-task iteration speed.

## Per-Phase Testing

After all tasks in a phase complete. Tests cross-task integration.

- Test interactions BETWEEN tasks within the phase
- Reference the phase requirements from the plan
- **All 4 tiers where applicable** — this is where T3/T4 start adding value
- Focus on: data flows between components, state consistency, error propagation
- Sub-agent delegation: 3-5 sub-agents (one per integration boundary)
- Verification pipeline: All 4 steps. Mutation testing is justified at this
  scope — phase boundaries are where bugs hide.

## Full-Design Testing

After all phases complete. End-to-end scenario coverage.

- Test complete user journeys across the entire feature
- Reference the original design doc acceptance criteria
- **All 4 tiers, emphasis on T1 scenarios and T4 properties** — T1 scenarios
  verify business outcomes; T4 properties catch invariant violations across
  the full feature surface.
- Focus on: business outcomes, cross-phase integration, performance SLAs
- Sub-agent delegation: 5-10 sub-agents (organized by user journey)
- Verification pipeline: All 4 steps with mutation score >= 80% target for
  critical paths.

## Scope Selection Decision Table

| Signal | Scope |
|--------|-------|
| Coder completed one task, more tasks pending | Per-task |
| All tasks in current phase done, more phases pending | Per-phase |
| All phases complete, feature ready for final review | Full-design |
| Auditor requests independent test verification | Per-phase or full-design |
| Bug found in production or integration | Per-task (focused regression) |
