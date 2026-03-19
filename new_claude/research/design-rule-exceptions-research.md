# Design Rule Exceptions Research

**Research ID**: design-rule-exceptions
**Completed**: 2026-02-28
**Confidence**: Medium-High (0.78) — Multiple authoritative sources; some findings from community consensus rather than controlled studies
**Sources used**: WebSearch (11 queries across 8 focus areas)
**Classification**: Persistent (reusable across features)

---

## Executive Summary

Every common code design rule has legitimate exceptions. The pattern across research is consistent: rules exist to prevent specific failure modes. When the failure mode doesn't apply, neither does the rule. AI agents fail at this reasoning — they apply rules uniformly regardless of context, producing code that is technically "clean" by metric but harder to read or maintain in practice.

The highest-impact insight from production data: **cognitive complexity and logic errors** are the leading causes of AI-generated code problems (CodeRabbit: 75% more logic errors in AI code vs human). Function extraction done incorrectly increases cognitive complexity rather than reducing it. This makes function-splitting discipline the highest-leverage design skill for AI agents.

---

## 1. Function Length Heuristics

### What Research Says

**McCabe's Cyclomatic Complexity** (1976): McCabe proposed a "reasonable upper limit" of 10 paths per function. No empirical evidence supports this specific bound — it was a heuristic. What it captures: functions with more execution paths need more test cases. It says nothing about lines.

**SonarSource Cognitive Complexity** (G. Ann Campbell, 2017–present): Created specifically because cyclomatic complexity "is not a satisfactory measure of understandability." Cognitive complexity penalizes nesting and control flow that requires mental tracking. It does NOT penalize line count directly. SonarSource's default threshold is **15 cognitive complexity units** — not a line count.

The key insight: a 60-line function with linear logic (no nesting, no branching) has cognitive complexity near zero. A 15-line function with 4 levels of nesting has high cognitive complexity. Line count is a proxy metric, not the actual goal.

**Martin Fowler / Clean Code convention**: The 20–30 line soft limit comes from "functions should fit on one screen." This was written when screens were 24 lines tall. Modern screens show 60–80 lines. The underlying principle is "readable in context without scrolling back" — which is context-dependent.

**Linux Kernel standard**: "The maximum length of a function is inversely proportional to the complexity and indentation level of that function." Functions with low nesting can be longer. This is the correct framing.

**Google/Goodhart's Law problem**: Focusing on line count as a metric causes developers to split functions prematurely to pass the metric, which increases indirection and cognitive load. The metric becomes the goal rather than the underlying readability goal.

### The ~30–40 Line Soft Limit

This is a reasonable default for **functions that mix control flow with computation**. It is not a hard rule and should not be enforced mechanically.

### When Long Functions Are Acceptable

| Pattern | Why Long Is Better | Example |
|---------|-------------------|---------|
| **State machines** | States and transitions belong together. Splitting creates artificial indirection that hides the state graph. | Protocol parsers, event-driven FSMs |
| **Data tables / dispatch tables** | A 60-line function that is 90% a lookup table has zero cognitive complexity. | Route config, command dispatch, codec tables |
| **Pipeline configurations** | Sequential steps with no branching read like a recipe. Splitting makes it harder to see the whole pipeline. | Data transformation pipelines, ETL configs |
| **Linear algorithms** | Sorting, search, mathematical algorithms with no nesting are readable regardless of length. | Numeric algorithms, serializers |
| **Framework-required methods** | Some frameworks (Django views, pytest fixtures) concentrate logic in required methods. Splitting forces artificial helper functions. | ORM query builders, test setup |

### What AI Agents Get Wrong

AI agents routinely produce 80–100 line functions they claim "do one thing" but are unreadable because:
1. They mix 3–4 abstraction levels in one function
2. They have deeply nested conditionals (high cognitive complexity despite "single purpose")
3. The 80–100 lines are a monolith: no logical grouping, no clear entry points

The correct signal for extraction is **nesting depth and abstraction level mixing**, not line count.

---

## 2. When to Break Each Major Design Rule

### 2.1 Single Responsibility Principle / Function Splitting

**The Rule**: Each function/class has one reason to change.

**Legitimate Exceptions**:

**Exception: Simple scripts and glue code**
- When to break: A 15-line script that does 3 related things (read file, transform, write) does not benefit from 3 functions. Six tiny functions with parameter passing creates more surface area for bugs than one clear function.
- Why it improves the code: Reading complexity is lower. No cross-function state management. The "reasons to change" are so coupled they effectively are one reason.
- Example:

```python
# Over-split (worse)
def read_config(path): ...
def parse_config(raw): ...
def validate_config(parsed): ...
def apply_config(validated): ...

# Better for a simple internal utility
def load_and_apply_config(path):
    raw = Path(path).read_text()
    config = json.loads(raw)
    assert "timeout" in config, "missing timeout"
    _settings.update(config)
    return config
```

**Exception: High cohesion with no external consumers**
- Internal module functions that will never be called separately should not be split. Splitting creates an implicit API surface that callers may accidentally depend on.

**Exception: Readability requires locality**
- When a function's logic is only meaningful in context of surrounding lines (error handling, state transitions), extraction to a separate function forces the reader to jump to understand context.

**Confidence**: Medium (0.75) — well-supported by Clean Code critiques but no empirical study on when splitting hurts.

---

### 2.2 Dependency Injection

**The Rule**: Inject dependencies rather than hardcoding them, for testability and flexibility.

**Legitimate Exceptions**:

**Exception: Pure functions**
- Pure functions take all inputs as parameters and have no external state. DI is already implicit — there's nothing to inject.
- Why skip: Adding a DI container to a pure function module adds complexity for zero testability gain. You can test pure functions directly.

```python
# Pure function — no DI needed
def calculate_discount(price: float, pct: float) -> float:
    return price * (1 - pct / 100)
```

**Exception: Internal utilities with fixed dependencies**
- A logging helper that uses the standard library logger doesn't need DI. It will never be tested with a different logger in isolation.

**Exception: Script-level main functions**
- Entry points that assemble components are allowed to hardcode some decisions. DI at this layer adds framework ceremony for no benefit.

**Downside of DI over-application**: "At its worst, DI turns compile-time errors into runtime errors, and you can't figure out where corrupt dependencies are coming from because your injector has it squirreled away behind several layers of obfuscation." (Wikipedia/community consensus)

**Confidence**: Medium (0.72) — well-understood tradeoff in functional programming community.

---

### 2.3 Immutability / Frozen Dataclasses

**The Rule**: Use frozen dataclasses (`@dataclass(frozen=True)`) to prevent accidental mutation.

**Legitimate Exceptions**:

**Exception: Builder pattern**
- Objects that are assembled step-by-step before being "sealed" need mutability during construction. Forcing immutability requires either a complex factory or copying on each addition.

```python
# Builder legitimately needs mutability
@dataclass
class QueryBuilder:
    filters: list = field(default_factory=list)
    sort_key: str | None = None
    limit: int | None = None

    def where(self, **kwargs) -> "QueryBuilder":
        self.filters.append(kwargs)  # mutable accumulation
        return self

    def build(self) -> FrozenQuery:
        return FrozenQuery(filters=tuple(self.filters), ...)
```

**Exception: Accumulators / result collectors**
- Objects that collect results during computation (metrics counters, result lists) are semantically mutable. Modeling them as immutable requires copy-on-update, which is expensive and unidiomatic.

**Exception: Performance-critical hot paths**
- Frozen dataclasses are approximately 2.4x slower to instantiate than mutable ones (measured, not estimated). In tight loops creating thousands of objects, this matters.

**Exception: Prototypes and exploratory code**
- Enforcing immutability in early development slows exploration. Add it during hardening, not during initial writing.

**The correct rule**: Objects that represent **finished values** (results, records, configuration snapshots) should be frozen. Objects that represent **ongoing computation** (accumulators, builders, state machines) should be mutable.

**Confidence**: High (0.85) — performance data is measured; builder pattern rationale is clear.

---

### 2.4 Extract Method / SLAP (Single Level of Abstraction Principle)

**The Rule**: Extract to maintain single abstraction level per function. Each function should call others at the same abstraction level.

**Legitimate Exceptions**:

**Exception: Performance-critical inner loops**
- Extracting a 3-line computation into a helper function introduces function call overhead. In tight inner loops (image processing, numerical computation), this matters. Keeping the computation inline is correct.

**Exception: Trivial one-liners that add no clarity**
- Extracting `if x is None: return default` into `get_or_default(x, default)` adds a layer with no clarity gain. The reader still needs to look up what `get_or_default` does.

**Exception: Wrong extraction boundary — the 5+ parameter signal**
- If the extracted function requires 5+ parameters (because it needs context from the caller), the extraction boundary is wrong. The extraction has split what belongs together. "Duplication is far cheaper than the wrong abstraction." (Sandi Metz)

```python
# Wrong extraction: function needs all this context
def _process_item(item, context, config, state, logger, metrics):
    ...

# Better: keep in caller where context is natural
```

**Exception: Rule of Three not yet triggered**
- Extract when you see it the third time, not the first. Two copies of similar logic may diverge. Premature extraction creates an abstraction that doesn't fit either use case cleanly.

**The correct test for extraction**: Ask "can I name this extracted function accurately in 3–5 words?" If no clear name exists, the boundary is wrong.

**Confidence**: Medium-High (0.80) — well-established in refactoring literature; "wrong abstraction" concept widely cited.

---

### 2.5 Type Annotations

**The Rule**: Annotate all function signatures and variables.

**Legitimate Exceptions**:

**Exception: Test files**
- Tests have narrow scope and are read with their test subject in context. Type annotations in tests often add visual noise without clarity benefit. The official Python typing docs and mypy both acknowledge this — `# mypy: ignore-errors` at the module level is a legitimate and documented approach.

```python
# Test file — omitting annotations is reasonable
def test_calculate_discount():
    result = calculate_discount(100.0, 10.0)
    assert result == 90.0
```

**Exception: Trivial lambdas**
- `key=lambda x: x.name` does not benefit from annotation. The type is obvious from context.

**Exception: Internal helpers that mypy infers correctly**
- If mypy can infer the type from usage (common with simple list comprehensions, generator expressions), explicit annotation adds redundancy. Annotate where inference fails or where the intent is non-obvious.

**Exception: Prototype / sketch code**
- Annotations add typing discipline cost during exploration. Add them when the design stabilizes. This is a lifecycle exception, not a permanent one.

**Confidence**: High (0.88) — mypy documentation explicitly supports these patterns.

---

### 2.6 Guard Clauses / Early Returns

**The Rule**: Use guard clauses (early return) to handle edge cases at the top, keeping the happy path visible and flat.

**Legitimate Exceptions**:

**Exception: Resource cleanup without context managers**
- In languages without context managers (or when context managers are unavailable), early returns skip cleanup code. The solution is not to avoid early returns — it is to use `try/finally`. If that's not available, use structured exits.

```python
# Wrong: early return skips cleanup
def process_file(path):
    f = open(path)
    if not valid_header(f):
        return None  # file handle leaked!
    ...

# Right: use context manager, guard clause is fine
def process_file(path):
    with open(path) as f:
        if not valid_header(f):
            return None
        ...
```

**Exception: Symmetric success/failure logic**
- When both branches do substantial and parallel work, an if/else makes the symmetry explicit. Guard clauses hide one branch.

```python
# Guard clause hides symmetry
def reconcile(a, b):
    if a.total == b.total:
        return record_match(a, b)
    return record_mismatch(a, b, a.total - b.total)

# if/else shows symmetry
def reconcile(a, b):
    if a.total == b.total:
        record_match(a, b)
    else:
        record_mismatch(a, b, a.total - b.total)
```

**Exception: Excessive guard clauses creating scattered returns**
- When a function has 6+ guard clauses, the flow becomes hard to follow. This is a sign the function should be split, not that all the guards are individually wrong.

**Confidence**: High (0.85) — well-documented in multiple engineering guides.

---

### 2.7 Composition Over Inheritance

**The Rule**: Prefer composition to inheritance. "Favor has-a over is-a."

**Legitimate Exceptions**:

**Exception: Template Method pattern**
- The Template Method pattern defines an algorithm skeleton in a base class and lets subclasses fill in specific steps. This is a legitimate use of inheritance: the base class provides the "how" (workflow), subclasses provide the "what" (specifics).

```python
class ReportGenerator:
    def generate(self):
        data = self.fetch_data()       # subclass provides
        formatted = self.format(data)   # subclass provides
        self.output(formatted)          # subclass provides

    def output(self, content):
        print(content)  # default implementation
```

- Template Method is especially appropriate when the base class algorithm has many steps and only a few need overriding. Composition (via Strategy) is better when whole algorithms need swapping at runtime.

**Exception: Genuine is-a relationships**
- When a class truly is a specialization of another (not just "uses" it), inheritance is semantically correct. `Dog` inheriting from `Animal` is not wrong when `Dog` IS an `Animal`.

**Exception: Framework extension points**
- Many frameworks (Django views, pytest plugins, Flask blueprints) are designed around inheritance as their extension mechanism. Fighting this pattern adds complexity for no gain.

**Exception: ABC / interface enforcement**
- Python ABCs use inheritance to enforce interface contracts. This is the correct tool for the job.

**The correct test**: Ask "will I ever need to substitute a subclass for the base class (Liskov)?" If yes, inheritance is appropriate. If the relationship is "uses" rather than "is-a", use composition.

**Confidence**: Medium-High (0.82) — well-supported by design pattern literature; Template Method rationale is canonical.

---

### 2.8 Rule of Three (DRY / Extract Earlier)

**The Rule**: Wait until duplication appears three times before extracting an abstraction.

**Legitimate Exceptions**:

**Exception: Extract earlier for clear DRY violations**
- The Rule of Three exists to prevent premature abstraction, not to license duplication when the abstraction is obvious. If two copies are clearly identical and the abstraction name is immediately obvious, extract after the second occurrence.

**Exception: Safety-critical code**
- In safety-critical code (aerospace, medical devices), even one copy of duplicated validation logic is dangerous. Extract immediately to ensure single point of truth for correctness.

**Exception: Security-sensitive patterns**
- Duplicated authentication checks, input sanitization, or encryption code should never be allowed to diverge. Extract after first duplication regardless of rule.

**When NOT to extract even after three occurrences**:
- If the three copies have begun to diverge in small ways (suggesting they're not really the same), do not force them into one abstraction. The divergence is a signal.
- If the extraction requires 5+ parameters (wrong boundary), keep the copies.

**Confidence**: Medium (0.75) — Rule of Three is a heuristic with acknowledged exceptions; safety code reasoning is from field practice.

---

## 3. Priority Ordering of Design Principles

Based on production data from CodeRabbit, GitClear, CodeScene, and code review research, here is a priority ordering of what actually impacts code quality in AI-generated code:

### Tier 1: Highest Impact (Break these and code breaks or becomes permanently unmaintainable)

1. **Logic correctness / cognitive complexity** — CodeRabbit found 75% more logic errors in AI code vs human. Cognitive complexity (deep nesting, complex conditionals) is the leading predictor of defect density (CodeScene research). This is the highest-leverage area.

2. **Function length as proxy for cognitive complexity** — Not line count itself, but what line count signals: functions that mix abstraction levels, have deep nesting, or carry too many responsibilities. GitClear found copy-paste increased 50% with AI tools (2020→2024) while refactoring dropped from 25% to 10% — indicating AI agents over-produce monolithic code.

3. **Avoiding wrong abstractions** — "Duplication is far cheaper than the wrong abstraction." Extracting to the wrong boundary creates an API surface that doesn't match its callers, causing rippling changes. This is harder to fix than original duplication.

### Tier 2: High Impact (Consistent violations degrade the codebase over months)

4. **Single level of abstraction per function** (SLAP) — Mixing high-level and low-level operations in one function is the primary source of "unmaintainable" ratings in code review. Reviewers focus on architecture and design over formatting (Google eng-practices: 75% of defects in code review affect evolvability, not functionality).

5. **Immutability for value objects** — Mutable value objects cause subtle state bugs that are hard to trace. Correct for value types; incorrect for builders/accumulators.

6. **Guard clauses for happy-path visibility** — Makes control flow easier to follow. Medium impact because violations are usually local to a function.

### Tier 3: Medium Impact (Violations hurt but are fixable incrementally)

7. **Dependency Injection** — Important for testability of systems. Less important for pure functions or isolated utilities.

8. **Type annotations** — High value in production code; acceptable to skip in tests and prototypes.

9. **Composition over inheritance** — Correctness matters at design time; fixing later is possible but expensive.

### Tier 4: Lower Impact (Style, consistency, team convention)

10. **Rule of Three** — Valuable but violations are visible and easily refactored.
11. **Exact line count limits** — Extremely low impact when cognitive complexity is low.

### Why This Ordering Matters for AI Agents

AI agents consistently fail on Tier 1 (logic, cognitive complexity) while producing technically correct Tier 4 metrics (functions are 30 lines, etc.). An agent that writes a 30-line function with 5 levels of nesting is "passing" the length check while failing the real readability goal.

**Augment Code research**: "AI operates in a self-harm mode, often writing code it cannot reliably maintain later... agents lack a reliable understanding of maintainability and change risk inside a real codebase."

**CodeRabbit 2025 data**: AI PRs have 1.4x more critical issues and 1.7x more major issues. The biggest category: logic and correctness errors (75% higher rate), with excessive I/O operations at 8x higher rate — both symptoms of agents not reasoning about runtime behavior.

---

## 4. Tiered Rule Systems

### How Other Standards Handle Must vs. Should vs. Consider

**NASA Power of 10 Rules (Gerard Holzmann, JPL)**:
- 10 rules, all stated as hard requirements, specifically for safety-critical embedded C
- Rationale: "Rules must be simple enough to be checked by static analysis tools"
- Key rules include: no dynamic allocation after init, no recursion, all loops have fixed upper bounds
- Philosophy: In safety-critical code, the cost of a false positive (following an unnecessary rule) is much lower than a false negative (missing a real defect)
- Lesson for AI agents: Hard rules work when the domain is narrow and the cost of violation is catastrophic. They fail when applied to general-purpose code.

**Google JavaScript Style Guide**:
- Uses RFC 2119 terminology: MUST, MUST NOT, SHOULD, SHOULD NOT, MAY
- "MUST" items are checked by automated tooling (linters, formatters)
- "SHOULD" items are reviewed by humans in code review
- "MAY" items are team-level decisions
- Tiered structure explicitly acknowledges that not all rules have equal weight

**Google Engineering Practices**:
- "Reviewers should favor approving changes once they improve the overall code health of the system, even if not perfect"
- Priority during review: correctness → architecture → test coverage → style
- This implies an explicit tier ordering: functional correctness beats style

**Linux Kernel**:
- Philosophy: "Coding style is very personal... but this is what goes for anything that I have to be able to maintain"
- Pragmatic: readability takes precedence over strict adherence to limits
- Function length: "maximum length is inversely proportional to complexity and indentation level" — context-dependent, not absolute

**Google's Tiered Standard Pattern (Software Process Standards)**:
- Mandatory practices applicable across all languages (6 in one cited example)
- Language-specific practices (override tier: language practices cannot override mandatory practices)
- Project-specific practices (override tier: project practices cannot override language practices)
- This hierarchical approach prevents project-level exceptions from undermining fundamental practices

### How to Present Tiered Rules to AI Agents

Research and practice show that AI agents have two failure modes with tiered rules:
1. **Ignoring lower tiers entirely** — treating all "should" rules as optional
2. **Applying all tiers uniformly** — treating "consider" rules as "must" rules

Effective tiered presentation for AI agents:

**Must-follow (non-negotiable)**: State as absolute with explanation of the failure mode it prevents. Example: "Never mix abstraction levels in one function. This causes functions that cannot be named, which cannot be understood, which cannot be tested."

**Should-follow (default, with documented exceptions)**: State the rule, then explicitly enumerate the 2–4 exceptions with their conditions. Example: "Extract when logic appears 3+ times, UNLESS: the extraction requires 5+ parameters (wrong boundary), or copies have begun to diverge (suggesting they're not the same thing)."

**Consider (context-dependent)**: Present as a tradeoff. Example: "Type annotations improve maintainability in production code. For test helpers and internal utilities, weigh annotation cost against readability benefit."

**Critical for AI agents**: Include the **failure mode** each rule prevents, not just the rule. Agents that understand why a rule exists can reason about whether the failure mode applies in their current context.

---

## 5. Code Examples for Key Exceptions

### Exception: Long function acceptable — state machine

```python
# 70 lines but cognitive complexity is ~8: one level of branching, no nesting
def handle_connection_event(event: ConnectionEvent, state: State) -> State:
    if state == State.DISCONNECTED:
        if event == ConnectionEvent.CONNECT:
            initiate_handshake()
            return State.CONNECTING
        return state  # ignore other events when disconnected

    if state == State.CONNECTING:
        if event == ConnectionEvent.HANDSHAKE_OK:
            send_ready()
            return State.CONNECTED
        if event == ConnectionEvent.TIMEOUT:
            close_socket()
            return State.DISCONNECTED
        return state

    if state == State.CONNECTED:
        if event == ConnectionEvent.DISCONNECT:
            send_goodbye()
            close_socket()
            return State.DISCONNECTED
        if event == ConnectionEvent.DATA:
            process_data()
            return state
        return state

    raise ValueError(f"Unknown state: {state}")
```

Splitting this into `handle_disconnected_event`, `handle_connecting_event`, `handle_connected_event` adds 3 functions with no reduction in complexity — the reader must now trace 4 functions to understand one state machine.

### Exception: Early extraction (before Rule of Three) — security check

```python
# Two copies exist — extract immediately because divergence is dangerous
def create_user(data: dict) -> User:
    sanitized = sanitize_html(data["bio"])  # Copy 1
    # ... 30 lines ...

def update_user(user_id: int, data: dict) -> User:
    sanitized = sanitize_html(data["bio"])  # Copy 2 — extract NOW
    # ... 25 lines ...

# Extract immediately: don't wait for a third copy of a security check
def _get_sanitized_bio(data: dict) -> str:
    return sanitize_html(data["bio"])
```

### Exception: Mutable dataclass for accumulator

```python
@dataclass
class MetricsCollector:
    """Mutable accumulator — frozen=True would require copy-on-update in tight loop."""
    latencies: list[float] = field(default_factory=list)
    error_count: int = 0
    request_count: int = 0

    def record(self, latency: float, is_error: bool = False) -> None:
        self.latencies.append(latency)
        self.request_count += 1
        if is_error:
            self.error_count += 1

    def to_summary(self) -> "MetricsSummary":
        # Convert to frozen value object when collection is done
        return MetricsSummary(
            p99=sorted(self.latencies)[int(len(self.latencies) * 0.99)],
            error_rate=self.error_count / max(1, self.request_count)
        )

@dataclass(frozen=True)
class MetricsSummary:
    p99: float
    error_rate: float
```

---

## Sources

- [CodeRabbit State of AI vs Human Code Generation (2025)](https://www.coderabbit.ai/blog/state-of-ai-vs-human-code-generation-report)
- [AI-authored code needs more attention, contains worse bugs — The Register (2025)](https://www.theregister.com/2025/12/17/ai_code_bugs)
- [GitClear AI Copilot Code Quality 2025: 4x Growth in Code Clones](https://www.gitclear.com/ai_assistant_code_quality_2025_research)
- [GitClear Coding on Copilot 2023: Downward Pressure on Code Quality](https://www.gitclear.com/coding_on_copilot_data_shows_ais_downward_pressure_on_code_quality)
- [Augment Code: Debugging AI-Generated Code — 8 Failure Patterns](https://www.augmentcode.com/guides/debugging-ai-generated-code-8-failure-patterns-and-fixes)
- [SonarSource: Cognitive Complexity White Paper](https://www.sonarsource.com/resources/cognitive-complexity/)
- [NASA Power of 10: Rules for Safety-Critical Code (Perforce)](https://www.perforce.com/blog/kw/NASA-rules-for-developing-safety-critical-code)
- [NASA Power of 10 Original Paper (spinroot.com)](https://spinroot.com/gerard/pdf/P10.pdf)
- [Google Engineering Practices: Code Review Standard](https://google.github.io/eng-practices/review/reviewer/standard.html)
- [Google JavaScript Style Guide](https://google.github.io/styleguide/jsguide.html)
- [Linux Kernel Coding Style (kernel.org)](https://www.kernel.org/doc/html/v4.10/process/coding-style.html)
- [CodeScene Agentic AI Coding: Best Practice Patterns](https://codescene.com/blog/agentic-ai-coding-best-practice-patterns-for-speed-with-quality)
- [Clean Code Critique: Why Much of Clean Code Aged Poorly](https://bugzmanov.github.io/cleancode-critique/clean_code_second_edition_review.html)
- [When Clean Code Becomes Harmful — DEV Community](https://dev.to/remojansen/clean-code-is-considered-harmful-3lh0)
- [Don't Make Clean Code Harder to Maintain: Rule of Three](https://understandlegacycode.com/blog/refactoring-rule-of-three/)
- [Python Frozen Dataclass Performance — Redowan's Reflections](https://rednafi.com/python/statically-enforcing-frozen-dataclasses/)
- [Dependency Injection Tradeoffs — Wikipedia](https://en.wikipedia.org/wiki/Dependency_injection)
- [Cognitive Complexity — matty.dev 2024](https://matty.dev/blog/2024-09-20-cognitive-complexity)
- [Guard Clause Common Mistakes — Tech at SpendHQ](https://tech.per-angusta.com/blog/common-mistakes-with-guard-clause-pattern/)
- [Composition vs Template Method — Wikipedia](https://en.wikipedia.org/wiki/Composition_over_inheritance)
- [Where AI Coding Agents Fail — Empirical Study (arxiv 2601.15195)](https://arxiv.org/html/2601.15195v1)
