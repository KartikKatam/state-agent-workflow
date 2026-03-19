---
name: code-design
version: 1-0-0
triggers:
  - agent_role: coder
    conditions: when writing production code, designing module structure, or logging test descriptions before TDD red-phase
description: >
  Use when writing or modifying production code, designing module boundaries,
  or preparing for TDD red-phase. Activates when choosing how to split
  responsibilities across functions, deciding what to inject vs. hard-wire,
  structuring interfaces from test descriptions, reducing cognitive complexity,
  resolving deep nesting, making immutability decisions, or deciding whether
  to extract a helper or add an abstraction layer. Also use when restructuring
  existing code for testability, adding error resilience patterns, or choosing
  concurrency models (async vs threads vs processes).
  Do NOT use for: TDD cycle sequencing (see infrastructure), test assertion
  design (see test-design), plan adherence (see task-execution), or
  code review (see code-review).
---

# Code Design

## Core Principle

**Design for the next change, not the current requirement.** Code is written once but read, tested, and modified many times. Every structural decision should make the next modification cheaper — through testability, clear boundaries, and minimal coupling. Write every function as if the next person to read it has never seen the codebase.

## When Not to Use

- TDD cycle sequencing (red/green/refactor order) → infrastructure
- Test assertion quality or mock design → test-design skill
- Plan adherence or scope management → task-execution skill
- Post-implementation code review → code-review skill

## Quick Reference

| Situation | Action |
|-----------|--------|
| About to enter TDD red-phase | Log test descriptions first (Step 1) |
| Designing a new function/class | Identify concerns, then design interfaces (Step 2) |
| Naming a function | Verb phrase describing its effect: `filter_active_users()` not `process()` |
| Naming a boolean | Use `is_`/`has_`/`can_`: `is_valid()` not `check_valid()` |
| Adding a dependency | Inject it as a parameter — never hard-wire internally |
| Considering extracting a helper | Apply Rule of Three — don't extract until third use |
| Function needs "and" to describe it | Split — "and" signals multiple responsibilities |
| Nesting depth >= 3 | Guard clauses to flatten — early return for preconditions |
| Long if/elif chain on same value | Dict lookup — eliminates branching complexity entirely |
| Function mixes high-level and low-level operations | Extract the low-level into helpers (SLAP) |
| Writing a comment explaining WHAT code does | Extract the code into a function named after the comment |
| Implementing multiple similar operations | Use the same structure for all of them — consistency is readability |
| Wrapping a third-party library | Isolate behind an adapter — volatile dependencies get interfaces |
| Adding an abstraction layer | Ask: "Will this change independently?" If no, don't abstract |
| Creating a data class | Use `frozen=True` by default — opt into mutability only when needed |
| Passing a collection between functions | Prefer tuple/frozenset if mutation isn't required |
| Writing a public function | Annotate all parameters and return type — types are machine-checked docs |
| Accepting external input (API, file, user) | Validate at the trust boundary before processing |
| Function over ~40 lines | Re-examine — likely has hidden responsibilities. Exception: state machines, data tables, pipeline configs |
| Calling an external service (API, DB, model) | Add timeout + retry with backoff. See `references/resilience-patterns.md` |
| Adding concurrency (async, threads, processes) | Async for I/O-bound, processes for CPU-bound, never fire-and-forget. See `references/concurrency-patterns.md` |
| Designing a multi-stage processing pipeline | Typed stages, bounded queues, per-item error boundaries. See `references/pipeline-patterns.md` routing guide |
| Deciding what/where to log | Log at architectural boundaries, not inside functions. See `references/logging-patterns.md` |
| About to mark task complete | Run the Design Verification checklist (Step 5) |

## Core Workflow

### Step 1: Log Test Descriptions (Before Red-Phase)

Before writing any test code, document what you intend to test. This forces design thinking before implementation — test descriptions expose the interfaces, contracts, and edge cases your production code must satisfy.

For each test you plan to write, log:

| Field | What to Write | Why |
|-------|--------------|-----|
| **What** | The behavior being tested (one sentence) | Clarifies the contract — if you can't state the behavior, you don't understand it yet |
| **How** | The approach — inputs, action, assertion (one sentence) | Reveals the interface — what goes in, what comes out |
| **Why** | What breaks if this behavior is wrong (one sentence) | Confirms the test has value — if nothing breaks, the test is fraudulent |
| **Considerations** | Edge cases, dependencies, risks (brief list) | Surfaces unknowns before they become bugs |

Test descriptions from Step 1 reveal what interfaces and boundaries your production code needs. Step 2 designs those interfaces.

**Why this matters:** Descriptions written before code expose design gaps. If describing the test is hard, the behavior is unclear — stop and clarify before coding. Skipping this produces tests that mirror implementation rather than define contracts.

### Step 2: Decompose Concerns and Design Interfaces

Before designing individual interfaces, identify the distinct responsibilities this code must handle. Group the test descriptions from Step 1 by the concern they exercise — each cluster reveals a separate responsibility that should become its own function, class, or module.

**Identify concerns first:**
List the distinct responsibilities. Example: "This feature involves: (1) parsing raw sensor input, (2) validating against config thresholds, (3) computing aggregated metrics, (4) formatting output for the API." Each numbered item is a separate concern — it gets its own function or module. If two concerns always change together, they might be one concern. If they change independently, they must be separate.

**Then design interfaces for each concern:**

For each function or component:

1. **Inputs**: What does the caller provide? Derive from test setup.
2. **Outputs**: What does the caller receive? Derive from test assertions.
3. **Dependencies**: What external resources does this need? Each becomes an injected parameter.
4. **Boundary**: What is inside this component vs. outside? Draw the line at the responsibility boundary.
5. **Name**: Name each function as a verb phrase describing its effect on the caller. The name IS the documentation — if the name is unclear, the interface is unclear. If you can't name it without "and", it has multiple responsibilities.

Step 2 produces concern decomposition, interface signatures, and dependency lists. Step 3 implements within these boundaries.

**Why concern-first:** Without explicit decomposition, you'll write one function per test instead of one function per responsibility. Tests map to behaviors, not necessarily to functions. A single concern may have 5 tests. Five concerns with one function each is better than one function that passes all 5 tests.

**Why interface-first:** Interfaces designed from test descriptions are inherently testable — the tests already exist in description form. Interfaces designed from implementation are shaped by internal convenience, not caller needs.

### Step 3: Structure Code for Change

During implementation (TDD green-phase), apply these design principles. Each reduces the cost of the next modification.

**Write at one level of abstraction per function (SLAP).**
A function either orchestrates (calls other functions) or operates (does the work) — never both. If `generate_report` contains both `for byte in buffer` and `write_summary(results)`, you're mixing levels. Extract the low-level work into a helper. The orchestrating function should read like a table of contents — each line describes WHAT happens, not HOW.

**Name with intent, not mechanism.**
Names are the primary channel through which your code communicates.

| Entity | Convention | Example |
|--------|-----------|---------|
| Function | Verb phrase describing effect | `filter_active_users()` not `process()` |
| Boolean function | `is_`/`has_`/`can_` prefix | `is_expired()` not `check_expiry()` |
| Variable | Noun describing content | `active_user_count` not `n` (module-level); `i` is fine for loop counters |
| Class | Noun describing what it is | `DetectionPipeline` not `DetectionManager` |
| Constant | `UPPER_SNAKE` describing the value | `MAX_RETRY_COUNT` not `NUM` |

Avoid vague names: `handle()`, `process()`, `do_stuff()`, `data`, `result`, `manager`. If the name doesn't tell you what it does without reading the body, rename it.

**Inject dependencies, never hard-wire.**
Pass collaborators in as parameters rather than creating them internally. A function that instantiates its own database connection can only be tested with a real database. A function that receives a connection can be tested with a stub in milliseconds.

**Isolate what changes, leave stable code concrete.**
Which parts are likely to change? Those get Protocol interfaces. Stable parts stay concrete. Example: detection model will be swapped → Protocol; coordinate math won't change → direct calls. Don't abstract stable code.

**Prefer composition over inheritance.**
Build behavior by combining small components rather than extending deep class hierarchies. Inheritance couples parent and child — changing a parent breaks children across the codebase. Composition lets you swap one part without touching others.

**Default to immutable data structures.**
Use `frozen=True` dataclasses, tuples over lists, `Final` for constants. Mutable shared state creates invisible coupling — any holder can mutate it, breaking others silently. Opt into mutability only when mutation is the purpose (builders, accumulators, caches).

**Type-annotate public interfaces.**
Annotations are machine-checked docs — they tell the reader what goes in and out without reading the body, and let pyright catch mismatches before tests run. Annotate all public function signatures and return types; skip only for trivial internal helpers. Abstract parameter types (`Sequence`, `Mapping`) accept wide input; concrete return types (`list`, `dict`) tell callers exactly what they get.

**Separate decisions from actions.**
Functions that both decide AND act are hard to test — you need to trigger the decision to observe the action. Split into: a pure function that decides (easy to test) and a function that acts on the decision (easy to mock).

**Make code self-documenting.**
If you write a comment explaining what code does, extract that block into a function named after the comment. Comments explain WHY (context, tradeoffs, non-obvious constraints). Code explains WHAT. Never comment out code — delete it; version control preserves history.

**Use consistent patterns for similar operations.**
When implementing multiple similar things (endpoints, handlers, test cases), give them all the same structure. If one endpoint does validate → process → respond, all endpoints do too. Consistency means understanding one instance gives you all of them.

**Design for resilience in external calls.**
Every call to an external service (API, database, model inference) needs a timeout and a plan for failure. At minimum: set a timeout on every external call, retry transient failures (429, 500-504) with exponential backoff and jitter, and define what happens when a dependency is down (crash, partial results, or fallback). AI agents consistently produce code that works in the happy path but crashes on the first transient 503. See `references/resilience-patterns.md` for retry, circuit breaker, timeout, and graceful degradation patterns.

**Treat logging as a design decision.**
Log at architectural boundaries (service entry/exit, external calls, error recovery), not inside internal functions. Use `%s`-style formatting, not f-strings — f-strings evaluate even when the log level is disabled. Never log credentials, tokens, or PII. See `references/logging-patterns.md` for level selection, structured logging, and observability patterns.

**Use structured concurrency for concurrent work.**
Every concurrent task must be owned, awaited, and have its exceptions handled. Use `asyncio.TaskGroup` (Python 3.11+) — it guarantees all tasks complete or cancel before the block exits. Never fire-and-forget with bare `asyncio.create_task`. Choose the right model: async for I/O-bound (network, DB), `ProcessPoolExecutor` for CPU-bound (GIL prevents threads from parallelizing CPU work). Default to no shared mutable state; when unavoidable, protect with `Lock` or `Semaphore`. See `references/concurrency-patterns.md` for model selection, synchronization primitives, and anti-patterns.

### Step 4: Reduce Complexity

When the quality gate flags high complexity, or when code is hard to follow, apply these techniques — listed from highest to lowest impact.

**Guard clauses for preconditions.**
Check failure conditions at the top and return early. The happy path stays at base indentation. This eliminates nesting penalties entirely — a function at nesting depth 3 can often drop to depth 0. See `references/patterns.md` for before/after examples with complexity scores.

**Extract method at responsibility boundaries.**
Move nested logic into well-named helper functions. Function calls are free in cognitive complexity scoring — they reduce measured complexity while improving readability. Extract when: nesting >= 3, a comment explains a block, or the function mixes abstraction levels.

**Dictionary dispatch for value-based branching.**
Replace if/elif chains that branch on the same value with a dict lookup. Reduces complexity to near zero. Use the function dispatch variant for command/handler patterns. Don't use for trivial 2-3 branch cases where if/elif is clearer.

**Watch function length as a complexity signal.**
Functions over ~40 lines usually have hidden responsibilities — re-examine by looking for abstraction level mixing, deep nesting, or multiple concerns. The real metric is cognitive complexity (target: under 15), not line count. A 60-line state machine with no nesting is fine; a 20-line function with 4 nesting levels is not. Exceptions: state machines, data tables, and pipeline configs are legitimately long when their logic is linear.

| Symptom | Technique | Typical Complexity Reduction |
|---------|-----------|------------------------------|
| Nesting depth >= 3 | Guard clauses | 40-70% |
| Function > 40 lines | Extract method (SLAP) | 30-60% |
| 5+ if/elif on same value | Dict dispatch | 60-90% |
| State-dependent branching | Transition table (dict) | 70-90% |
| Complex boolean expression | Split into guard clauses | 10-30% |

### Step 5: Verify Your Design

Before marking implementation complete, scan your code against these checks. This is the "senior engineer re-reads their own PR" step. Catching design problems here is 10x cheaper than catching them in auditor review.

- [ ] **Each function does one thing.** Can you describe it without "and"? If not, split.
- [ ] **Names reveal intent.** Read only the function names in your module — do they tell the story of what the code does? Could a new team member understand the flow from names alone?
- [ ] **Each function stays at one abstraction level.** No mixing `for byte in buffer` with `generate_report(users)` in the same function.
- [ ] **Similar operations follow the same structure.** If you wrote 3 handlers, do they all have the same shape?
- [ ] **Each file contains one concept.** If a file has two unrelated classes or two unrelated groups of functions, split it.
- [ ] **Dependencies are injected.** Grep for hard-coded instantiation of collaborators inside business logic.
- [ ] **External input is validated at the boundary.** Anything from outside (API, file, user) hits a validation layer before business logic.
- [ ] **No implicit return paths.** Every function explicitly returns on all branches.
- [ ] **Data is immutable by default.** Dataclasses use `frozen=True`. Collections passed between functions are tuples/frozensets unless mutation is the purpose.
- [ ] **Public functions are type-annotated.** Every public function has parameter and return type annotations. Parameters use abstract types (`Sequence`), returns use concrete types (`list`).
- [ ] **Comments explain WHY, not WHAT.** Any "what" comment should be a function name instead.
- [ ] **External calls have timeouts and failure handling.** Every API call, DB query, or model inference has a timeout. Transient failures are retried with backoff.
- [ ] **Logging is at boundaries, not everywhere.** Log calls are at service entry/exit points, not inside internal helpers. No f-strings in log calls. No credentials in logs.
- [ ] **Concurrent tasks are structured.** All async tasks created within a `TaskGroup` or explicitly tracked. No fire-and-forget `create_task`. Shared mutable state protected by locks. CPU-bound work uses processes, not threads.

## Critical Rules

### Must-Follow (highest impact — violations cause bugs, untestable code, or safety issues)

- **Write at one abstraction level per function** — the #1 predictor of cognitive complexity. Each function either coordinates or computes, never both. _Exception: performance-critical inner loops where function call overhead matters._
- **Decompose concerns before designing interfaces** — without this, you write one function per test instead of one function per responsibility. _No exceptions._
- **Inject dependencies, never hard-wire** — the single highest-impact testability decision. _Exception: pure functions and trivial utilities with no external state._
- **Validate at trust boundaries** — validate all external input before processing. This eliminates the majority of injection vulnerabilities. _No exceptions for external input._
- **Catch specific exceptions, never bare `except`** — bare `except:` catches system exits and memory errors. Default to deny (fail-closed) for security paths. _No exceptions._
- **Design for resilience** — every external call needs a timeout and a failure plan. Retry transient failures with backoff, degrade gracefully when dependencies fail. _Exception: local-only pure computations._
- **Watch function length** — functions over ~40 lines usually have hidden responsibilities. The real metric is cognitive complexity (under 15), not line count. _Exception: state machines, data tables, pipeline configs with linear logic._

### Should-Follow (default yes — documented exceptions exist)

- **Log test descriptions before writing test code** — forces design thinking before implementation. _Exception: trivial one-line tests._
- **Name with intent** — vague names (`process`, `handle`, `data`) force readers to read the body. Every function name: verb phrase. Every boolean: `is_`/`has_`/`can_`. _Exception: standard loop variables (`i`, `x`, `y`)._
- **Immutable by default** — `frozen=True` dataclasses, tuples, `Final`. Mutable shared state creates invisible coupling. _Exception: builders, accumulators, performance hot paths (frozen is ~2.4x slower to instantiate)._
- **Type-annotate public interfaces** — machine-checked docs that let pyright catch mismatches. Abstract params (`Sequence`), concrete returns (`list`). _Exception: test files, trivial lambdas, prototype code._
- **No "and" in function purpose** — multi-purpose functions require combinatorial test coverage. _Exception: simple scripts where splitting adds more complexity than it removes._
- **Rule of Three for extraction** — don't extract until third use. _Exception: extract earlier for security-critical duplication. Don't extract even at 3 if copies have diverged._
- **Consistent structure for similar operations** — understanding one gives you all of them.
- **Minimal public surface** — every public method is a contract you must maintain and test.
- **Abstraction earns its place** — "Will this change independently?" If no, don't abstract. Abstract the volatile, leave the stable concrete.
- **Explicit return paths** — never rely on implicit `None` return. If a function returns `None` intentionally, say `return None`.
- **Log at architectural boundaries** — log service entry/exit and external calls, not inside internal functions. Use `%s` formatting, never f-strings. Never log credentials or PII.

For detailed exceptions and when to deliberately break each rule, read `references/rule-exceptions.md`.

## References

For proven design patterns with code examples (dependency injection, guard clauses, dict dispatch, extract method, error handling, composition), read `references/patterns.md`.

For common design failures with WRONG/RIGHT pairs (god functions, hidden dependencies, premature abstraction, deep nesting, AI performance anti-patterns), read `references/anti-patterns.md`.

For resilience patterns in production code (retry with backoff, circuit breaker, timeouts, graceful degradation, bulkhead), read `references/resilience-patterns.md`.

For logging as a design concern (level selection, structured logging with structlog, what NOT to log, observability), read `references/logging-patterns.md`.

For when to deliberately break each design rule (function length exceptions, DI exceptions, immutability exceptions, extraction boundary signals), read `references/rule-exceptions.md`.

For concurrency design decisions (async vs threads vs processes, structured concurrency, shared state, synchronization, testing concurrent code), read `references/concurrency-patterns.md`.

For multi-stage pipeline design (stage isolation, backpressure, error handling, batching, testing), see the routing guide at `references/pipeline-patterns.md`. Domain-specific extensions (buffer pools, GPU batching, verification checks) are in `specializations/`.
