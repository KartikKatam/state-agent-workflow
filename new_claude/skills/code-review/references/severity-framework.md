# Severity Classification Framework

Guide for classifying code review findings into Critical, Important, and Minor. Severity determines whether a finding blocks progress (Critical), should be addressed (Important), or is advisory (Minor).

## Severity Definitions

### Critical

**Cannot ship. Must fix before proceeding.**

The finding indicates a defect that:
- Breaks stated functionality (requirement not met)
- Creates a security vulnerability (injection, auth bypass, data exposure)
- Makes code untestable (no way to verify behavior)
- Violates a plan limitation (explicit "do NOT" boundary crossed)
- Represents a fraudulent test (test proves nothing)
- Causes data loss, corruption, or silent failure

**Mapping to verdict:** Any Critical finding → Fix verdict minimum. If the Critical finding indicates the fundamental approach is wrong → Scrap.

### Important

**Should not ship. Should fix before proceeding.**

The finding indicates an issue that:
- Affects maintainability (cognitive complexity > 15, god function, circular dependency)
- Misses an edge case from the plan's `considerations` field
- Creates tech debt that will compound (hard-wired dependency, implicit coupling)
- Has weak test assertions (test exists but doesn't verify meaningful behavior)
- Represents a scope violation (YAGNI — functionality added beyond requirements)
- Has missing error handling for external calls
- Has undocumented plan deviation

**Mapping to verdict:** Important findings without Critical findings → Fix verdict. Coder addresses each Important item with auditor's specific guidance.

### Minor

**Advisory. Nice to have. Does not block.**

The finding indicates:
- Naming could be improved (but is not misleading)
- Comment could be clearer (but code is understandable)
- Alternative approach would be slightly better (but current approach works)
- Documentation could be expanded (but behavior is testable)
- Performance could be marginally improved (but meets requirements)

**Mapping to verdict:** Minor findings alone → Pass verdict. Minor findings are noted for coder awareness but do not affect the verdict.

## Classification Decision Table

| Symptom | Severity | Reasoning |
|---------|----------|-----------|
| Requirement from plan not implemented | Critical | Core deliverable missing |
| Limitation from plan violated | Critical | Explicit boundary crossed |
| SQL/command injection vector | Critical | Security — OWASP Top 10 |
| No input validation at API boundary | Critical | Security — trust boundary violation |
| Test asserts `is not None` only | Critical | Fraudulent test — proves nothing |
| Test marked `@skip` | Important | Missing coverage, not a pass |
| Bare `except:` catching all exceptions | Critical | Catches SystemExit, KeyboardInterrupt |
| External call without timeout | Important | Will hang indefinitely on failure |
| External call without error handling | Critical if in hot path | Silent crash in production |
| External call without error handling | Important if in setup/init | Crash during initialization |
| Cognitive complexity > 15 | Important | SonarSource empirical threshold: defect density increases above 15 — the function is hard to modify without introducing bugs |
| Cognitive complexity > 25 | Critical | Function cannot be safely modified — any change risks cascading side effects. Empirical studies show steep defect correlation above this point |
| Function > 80 lines | Important | At 80+ lines, the function almost certainly has hidden responsibilities — multiple conceptual operations packed into one body that should be separate |
| Function > 40 lines | Minor | Borderline — check if it's one coherent operation or two operations disguised as one. Google guideline; state machines and data tables are legitimate exceptions |
| Nesting depth > 3 | Important | Chomsky/Weinberg finding: humans lose track of nested context beyond 3 levels. Guard clauses eliminate most nesting |
| File modified outside `target_files` (undocumented) | Critical | Scope violation — plan breach |
| Functionality added beyond requirements | Important | YAGNI — scope creep |
| Mutable default argument | Important | Shared state bug (Python-specific) |
| Hardcoded configuration value | Important if deployment-relevant | Should be externalized |
| Hardcoded configuration value | Minor if test-only | Acceptable in tests |
| Missing type annotation on public function | Minor | Helpful but not blocking |
| Debug print statement left in | Important | Noise in production output |
| TODO/FIXME for this task's requirement | Critical | Incomplete implementation |
| TODO/FIXME for future work | Minor | Acceptable if out of scope |

## Edge Cases

### When Severity Depends on Context

Some findings change severity based on where they occur:

**Error handling on external calls:**
- In the request-handling hot path → Critical (production crash per request)
- In startup initialization → Important (crash on deploy, but recoverable by restarting)
- In optional feature path → Important (feature degrades, but core works)

**Missing tests:**
- For a core requirement → Critical (unverified core behavior)
- For an edge case in `considerations` → Important (known gap)
- For an edge case NOT in the plan → Minor (nice to have, not required)

**Code complexity:**
- In code that will be modified by future tasks → Important (compounds cost)
- In code that's a standalone utility, unlikely to change → Minor (contained complexity)

### When Multiple Findings Compound

Individual findings may be Minor alone but compound into Important:
- 3+ Minor naming issues in the same module → consider Important (pattern of unclear code)
- Multiple "slightly weak" test assertions → consider Important (systematic test quality issue)

Document the compound reasoning: "Individually Minor, but the pattern across [files] suggests systematic [issue]. Elevated to Important."

### When to Override the Table

The table provides defaults. Override when you have specific evidence:

**Upgrade Minor → Important:**
- **When the finding is in a `review_focus` area** — the strategist specifically flagged this for extra scrutiny. Any finding in a review_focus area should be considered for one-level severity upgrade. The strategist has full-plan context you don't; if they flagged it, there's a reason.
- When the same Minor issue appears in 3+ locations (systematic problem)

**Downgrade Important → Minor:**
- When the plan explicitly accepts the tradeoff ("we know X is suboptimal, acceptable for v1")
- When fixing would require changes outside the task's scope (flag but don't block)

**Never downgrade Critical:**
- Critical means "cannot ship." If you're tempted to downgrade, re-examine whether it truly breaks functionality, creates a security issue, or violates a plan limitation. If yes — it stays Critical regardless of how inconvenient that is.

## Verdict Mapping Summary

| Findings | Verdict |
|----------|---------|
| 0 Critical, 0 Important, 0+ Minor | **Pass** |
| 0 Critical, 1+ Important, 0+ Minor | **Fix** |
| 1+ Critical (approach sound), 0+ Important | **Fix** |
| 1+ Critical (approach fundamentally wrong) | **Scrap** |

**The scrap boundary:** Scrap is not "many Critical findings." It's "the foundation is wrong." Ask: "If the coder fixes every Critical finding individually, will the code be good?" If yes → Fix. If no (because the fixes would conflict, require rewriting everything, or the architecture itself is the problem) → Scrap.

## Finding Output Format

See SKILL.md Step 5 for the required format: every finding must include file:line, what's wrong, why it matters, how to fix, and severity.
