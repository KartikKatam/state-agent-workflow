# When to Break Design Rules

Rules exist to prevent specific failure modes. When the failure mode doesn't apply, neither does the rule. AI agents apply rules uniformly regardless of context — producing code that passes metrics but is harder to read.

## Contents

- [Function Length: The Real Metric](#function-length-the-real-metric) — cognitive complexity, not line count
- [Single Responsibility / Function Splitting](#single-responsibility--function-splitting) — when NOT to split
- [Dependency Injection](#dependency-injection) — when hard-wiring is correct
- [Immutability](#immutability) — builders, accumulators, performance
- [Extract Method / SLAP](#extract-method--slap) — wrong boundaries, performance
- [Type Annotations](#type-annotations) — tests, prototypes, inference
- [Guard Clauses](#guard-clauses) — symmetry, resource cleanup
- [Composition Over Inheritance](#composition-over-inheritance) — template method, frameworks
- [Rule of Three](#rule-of-three) — extract earlier for security, later for divergence

## Function Length: The Real Metric

**The metric is cognitive complexity (target: under 15), not line count.** SonarSource's cognitive complexity penalizes nesting and control flow that requires mental tracking. It does NOT penalize line count.

A 60-line function with linear logic has cognitive complexity near zero. A 15-line function with 4 nesting levels has high cognitive complexity.

The ~40-line soft limit is a proxy for functions that mix control flow with computation. It is not a hard rule.

**When long functions are acceptable:**

| Pattern | Why Long Is Better |
|---------|-------------------|
| State machines | States and transitions belong together. Splitting hides the state graph. |
| Data/dispatch tables | A 60-line lookup table has zero cognitive complexity. |
| Pipeline configs | Sequential steps with no branching read like a recipe. |
| Linear algorithms | No nesting = readable regardless of length. |

```python
# 70 lines, cognitive complexity ~8: one level of branching, no nesting
def handle_connection_event(event: ConnectionEvent, state: State) -> State:
    if state == State.DISCONNECTED:
        if event == ConnectionEvent.CONNECT:
            initiate_handshake()
            return State.CONNECTING
        return state

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

Splitting into `handle_disconnected_event`, `handle_connecting_event`, `handle_connected_event` adds 3 functions with no complexity reduction — the reader traces 4 functions to understand one state machine.

**The real extraction signal:** nesting depth and abstraction level mixing, not line count.

## Single Responsibility / Function Splitting

**The rule:** Each function has one reason to change.

**Exception: Simple scripts and glue code.** A 15-line script that reads, transforms, and writes does not benefit from 3 functions. The "reasons to change" are so coupled they are effectively one.

```python
# Over-split (worse for a simple internal utility)
def read_config(path): ...
def parse_config(raw): ...
def validate_config(parsed): ...
def apply_config(validated): ...

# Better: one function when concerns are tightly coupled
def load_and_apply_config(path):
    raw = Path(path).read_text()
    config = json.loads(raw)
    assert "timeout" in config, "missing timeout"
    _settings.update(config)
    return config
```

**Exception: High cohesion with no external consumers.** Internal module functions that will never be called separately should not be split — splitting creates an implicit API surface callers may depend on.

**Exception: Readability requires locality.** When logic is only meaningful in context of surrounding lines (error handling, state transitions), extraction forces the reader to jump.

## Dependency Injection

**The rule:** Inject dependencies for testability and flexibility.

**Exception: Pure functions.** Pure functions take all inputs as parameters. DI is already implicit — there's nothing to inject.

```python
# No DI needed — pure function, test directly
def calculate_discount(price: float, pct: float) -> float:
    return price * (1 - pct / 100)
```

**Exception: Internal utilities with fixed dependencies.** A logging helper using the standard library logger doesn't need DI — it will never be tested with a different logger.

**Exception: Script-level main functions.** Entry points that assemble components are allowed to hard-wire. DI at this layer adds ceremony for no benefit.

**The DI over-application signal:** "At its worst, DI turns compile-time errors into runtime errors, and you can't figure out where corrupt dependencies are coming from because your injector has it squirreled away behind several layers of obfuscation."

## Immutability

**The rule:** Use `frozen=True` dataclasses to prevent accidental mutation.

**Exception: Builder pattern.** Objects assembled step-by-step need mutability during construction.

```python
@dataclass
class QueryBuilder:
    filters: list = field(default_factory=list)

    def where(self, **kwargs) -> "QueryBuilder":
        self.filters.append(kwargs)  # mutable accumulation
        return self

    def build(self) -> FrozenQuery:
        return FrozenQuery(filters=tuple(self.filters))  # freeze when done
```

**Exception: Accumulators / result collectors.** Objects collecting results during computation (metrics counters, result lists) are semantically mutable. Copy-on-update is expensive and unidiomatic.

**Exception: Performance-critical hot paths.** Frozen dataclasses are ~2.4x slower to instantiate than mutable ones. In tight loops creating thousands of objects, this matters.

**The correct rule:** Objects representing **finished values** (results, records, config snapshots) should be frozen. Objects representing **ongoing computation** (accumulators, builders, state machines) should be mutable.

## Extract Method / SLAP

**The rule:** Extract to maintain single abstraction level per function.

**Exception: Performance-critical inner loops.** Function call overhead matters in tight loops (image processing, numerical computation). Keep the computation inline.

**Exception: Trivial one-liners.** Extracting `if x is None: return default` into `get_or_default(x, default)` adds a layer with no clarity gain.

**Exception: Wrong extraction boundary — the 5+ parameter signal.** If the extracted function requires 5+ parameters, the boundary is wrong. The extraction has split what belongs together.

```python
# Wrong: extraction needs all this context → wrong boundary
def _process_item(item, context, config, state, logger, metrics):
    ...

# Better: keep in caller where context is natural
```

**The correct test:** "Can I name this extracted function in 3–5 words?" If no clear name exists, the boundary is wrong. "Duplication is far cheaper than the wrong abstraction." (Sandi Metz)

## Type Annotations

**The rule:** Annotate all public function signatures and return types.

**Exception: Test files.** Tests have narrow scope and are read with their subject in context. Annotations often add visual noise. `# mypy: ignore-errors` at module level is documented practice.

**Exception: Trivial lambdas.** `key=lambda x: x.name` — the type is obvious from context.

**Exception: Internal helpers where mypy infers correctly.** Simple list comprehensions and generator expressions. Annotate where inference fails or intent is non-obvious.

**Exception: Prototype / sketch code.** Annotations add cost during exploration. Add them when the design stabilizes — lifecycle exception, not permanent.

## Guard Clauses

**The rule:** Early return for preconditions, keeping happy path flat.

**Exception: Resource cleanup without context managers.** Early returns skip cleanup. The solution is context managers (`with`), not avoiding early returns.

```python
# Wrong: early return leaks file handle
def process_file(path):
    f = open(path)
    if not valid_header(f):
        return None  # leaked!

# Right: context manager makes guard clause safe
def process_file(path):
    with open(path) as f:
        if not valid_header(f):
            return None
        ...
```

**Exception: Symmetric success/failure logic.** When both branches do substantial parallel work, if/else makes symmetry explicit. Guard clauses hide one branch.

**Exception: 6+ guard clauses.** The flow becomes scattered. This signals the function should be split, not that guards are wrong.

## Composition Over Inheritance

**The rule:** Prefer has-a over is-a.

**Exception: Template Method pattern.** Base class provides algorithm skeleton, subclasses fill in specific steps. Appropriate when the algorithm has many steps and only a few need overriding.

**Exception: Framework extension points.** Django views, pytest plugins, Flask blueprints are designed around inheritance. Fighting the pattern adds complexity.

**Exception: ABC / interface enforcement.** Python ABCs use inheritance to enforce contracts. Correct tool for the job.

**The correct test:** "Will I substitute a subclass for the base class (Liskov)?" If yes, inheritance is appropriate. If the relationship is "uses" not "is-a", use composition.

## Rule of Three

**The rule:** Don't extract until duplication appears a third time.

**Exception: Extract earlier for security-sensitive code.** Duplicated auth checks, input sanitization, or encryption code must never diverge. Extract after first duplication.

```python
# Two copies of a security check — extract NOW, don't wait for a third
def create_user(data: dict) -> User:
    sanitized = sanitize_html(data["bio"])  # copy 1
    ...

def update_user(user_id: int, data: dict) -> User:
    sanitized = sanitize_html(data["bio"])  # copy 2 — extract immediately
    ...
```

**Exception: Extract earlier when abstraction is obvious.** If two copies are identical and the name is immediately clear, extract after the second.

**Exception: Don't extract even at 3+ if copies have diverged.** Divergence is a signal they are not the same thing. Forcing them into one abstraction creates the wrong abstraction.

**Exception: Don't extract if it requires 5+ parameters.** Wrong boundary — the copies share context that doesn't factor cleanly.

**Sources:** SonarSource Cognitive Complexity, NASA Power of 10, Google eng-practices, CodeRabbit 2025, GitClear 2025, Linux kernel coding style, Sandi Metz ("Wrong Abstraction")
