# Code Design Research — Exhaustive Compilation

## Executive Summary

This report synthesizes findings from 80+ authoritative sources across AI coding agent companies, established software engineering standards, open-source coding guidelines, security frameworks, industry code review practices, and academic research. The research was conducted via 8 parallel research sub-agents covering distinct domains, then synthesized into a unified reference.

**Highest-signal findings:**

1. **Test-driven verification is the #1 quality amplifier** for AI-generated code. Every major AI coding company (Anthropic, OpenAI, CodeScene, Google) independently converged on this.
2. **AI-generated code has measurable quality deficits**: 1.7x more issues per PR, 3x worse readability, up to 8x more performance problems, 45% security vulnerability rate (CodeRabbit, Veracode, GitClear studies).
3. **Cognitive complexity** (SonarSource model) is a better predictor of code understandability than cyclomatic complexity. Target: max 15 per function, ideally under 10.
4. **Guard clauses + extract method** are the two highest-impact refactoring techniques, reducing cognitive complexity by 40-70%.
5. **Dictionary dispatch** eliminates branching complexity entirely for value-based conditional logic (CC reduction: 60-90%).
6. **Constructor injection with Protocols** is the Python-idiomatic approach to dependency injection — no frameworks needed.
7. **Input validation at trust boundaries** is the single most impactful security practice, eliminating "the vast majority of software vulnerabilities" (CERT/SEI).
8. **75% of code review defects are maintainability issues**, not functional bugs (IEEE study). Invest review time in design and architecture, not style.
9. **Consensus thresholds**: functions 10-40 lines, max 3 parameters, max 3 nesting levels, cognitive complexity under 15.
10. **AI instruction files** (CLAUDE.md, AGENTS.md) should be under 300 lines, contain only universally-applicable rules, and be iterated based on actual mistakes.

---

## 1. Optimization & Efficiency

### 1.1 Algorithm Selection

**Principle**: Choose the simplest algorithm that meets performance requirements. Profile before optimizing.

**AI-agent-specific failure**: AI agents produce O(n^2) solutions where O(n) exists, particularly for string concatenation in loops and nested list searches (Augment Code finding: 1 in 8 performance patterns).

**Rules:**
- Use built-in data structures appropriate to access patterns: `dict` for O(1) lookup, `set` for membership testing, `list` for ordered sequences
- Prefer `collections.Counter`, `collections.defaultdict`, `itertools` over manual implementations
- Use generators for large/unbounded data processing instead of materializing full lists
- Profile with `cProfile`/`line_profiler` before optimizing — premature optimization adds complexity without measured benefit

**Source**: [Augment Code: 8 Failure Patterns](https://www.augmentcode.com/guides/debugging-ai-generated-code-8-failure-patterns-and-fixes)

### 1.2 Unnecessary Computation

```python
# WRONG: Repeated computation in loop
for item in items:
    config = load_config()  # Loaded every iteration!
    process(item, config)

# RIGHT: Compute once
config = load_config()
for item in items:
    process(item, config)
```

```python
# WRONG: Materializing when only iterating once
total = sum([x * x for x in range(10_000_000)])  # ~400MB list

# RIGHT: Generator expression
total = sum(x * x for x in range(10_000_000))  # ~128 bytes
```

### 1.3 Generator vs List Decision Matrix

| Scenario | Generator | List |
|----------|-----------|------|
| Passed to `sum()`, `all()`, `any()`, `min()`, `max()` | Yes | No |
| Iterated once | Yes | No |
| Need `len()`, indexing, or multiple iterations | No | Yes |
| Large/unbounded input | Yes | No |
| Small input (< 1000 items) | Either | Either |

### 1.4 Performance Anti-Patterns AI Agents Produce

| Anti-Pattern | Fix | Frequency |
|-------------|-----|-----------|
| String concatenation in loops | Use `"".join(parts)` | High |
| N+1 database queries | Batch/eager load | High |
| Materializing large generators | Use generator expressions | Medium |
| Repeated regex compilation | Compile once with `re.compile()` | Medium |
| Blocking I/O in async context | Use `asyncio`-compatible libraries | Medium |
| Unbounded collection processing | Add limits: `items[:MAX_ITEMS]` | High |

**Sources**: [Augment Code](https://www.augmentcode.com/guides/debugging-ai-generated-code-8-failure-patterns-and-fixes), [CodeRabbit Report](https://www.coderabbit.ai/blog/state-of-ai-vs-human-code-generation-report)

---

## 2. Complexity Reduction

### 2.1 Cognitive Complexity (SonarSource Model)

**What it measures**: How difficult code is to *understand* (not just how many paths exist).

**Three scoring rules:**
1. **+1 for each break in linear flow**: `if`, `elif`, `else`, `for`, `while`, `try/except`, ternary, recursion
2. **+1 nesting penalty per depth level**: Nested `if` inside `for` adds depth as additional penalty
3. **No increment for method calls**: Well-named function calls are FREE — this is why extraction works

**Why it's better than cyclomatic complexity:**
- `switch` with 10 cases: Cyclomatic = 10, Cognitive = 1
- Deeply nested `if-if-if`: Cyclomatic = 3, Cognitive = 6 (1+2+3 from nesting)
- Cognitive complexity weights nesting, which correlates with actual comprehension difficulty

**Default threshold: 15 per function, target under 10.**

**Tools**: `complexipy` (Rust CLI), `flake8-cognitive-complexity`, SonarQube/SonarCloud

**Sources**: [SonarSource Whitepaper](https://www.sonarsource.com/docs/CognitiveComplexity.pdf), [SonarSource Blog](https://www.sonarsource.com/blog/5-clean-code-tips-for-reducing-cognitive-complexity/)

### 2.2 Guard Clauses (Highest-Impact Technique)

Check preconditions at the top of a function and exit immediately if not met. The happy path stays at base indentation.

```python
# BEFORE — nested validation (Cognitive Complexity: 4, nesting depth: 3)
def process_order(order: dict) -> str:
    if order is not None:                          # +1
        if order.get("items"):                     # +2 (nesting=1)
            if order["status"] == "pending":       # +3 (nesting=2)
                total = sum(i["price"] for i in order["items"])
                return f"Order total: {total}"
    return "Invalid order"

# AFTER — guard clauses (Cognitive Complexity: 3, nesting depth: 0)
def process_order(order: dict) -> str:
    if order is None:                              # +1
        return "Invalid order"
    if not order.get("items"):                     # +1
        return "Invalid order"
    if order["status"] != "pending":               # +1
        return "Invalid order"
    total = sum(i["price"] for i in order["items"])
    return f"Order total: {total}"
```

**Impact**: Nesting penalty eliminated entirely. CC drops from 4 to 3 in this example, but the nesting depth drop (3 -> 0) is the real win.

**When to use**: Whenever you have 3+ nested if-else levels. Use `continue` for the loop variant.

**Sources**: [Refactoring Guru](https://refactoring.guru/replace-nested-conditional-with-guard-clauses), [Jeff Atwood: Flattening Arrow Code](https://blog.codinghorror.com/flattening-arrow-code/)

### 2.3 Extract Method (Second Highest-Impact Technique)

Move nested logic into well-named helper functions. Method calls are FREE in cognitive complexity scoring.

**Heuristics for when to extract:**

| Signal | Action |
|--------|--------|
| You write a comment explaining a code block | The comment text becomes the function name |
| Nesting depth >= 3 | Extract the inner block |
| Same logic appears in 2+ places | Extract to eliminate duplication |
| Low-level operations mixed with high-level orchestration | Separate levels (SLAP principle) |
| The extracted block would need <= 3 parameters | Good extraction boundary |

```python
# BEFORE — mixed abstraction levels
def generate_report(users: list[User], output_path: Path) -> None:
    active = [u for u in users if u.is_active and u.last_login > cutoff]
    rows = []
    for user in active:
        row = f"{user.name},{user.email},{user.last_login.isoformat()}"
        rows.append(row)
    header = "name,email,last_login\n"
    with open(output_path, "w") as f:
        f.write(header)
        f.write("\n".join(rows))

# AFTER — single level of abstraction per function (SLAP)
def generate_report(users: list[User], output_path: Path) -> None:
    active = filter_active_users(users)
    rows = format_as_csv(active)
    write_csv(rows, output_path)

def filter_active_users(users: list[User]) -> list[User]:
    return [u for u in users if u.is_active and u.last_login > cutoff]

def format_as_csv(users: list[User]) -> list[str]:
    return [f"{u.name},{u.email},{u.last_login.isoformat()}" for u in users]

def write_csv(rows: list[str], path: Path) -> None:
    header = "name,email,last_login"
    path.write_text(header + "\n" + "\n".join(rows))
```

**When NOT to extract:**
- Trivial one-liners used once (extracting `x = a + b` into `add(a, b)` adds indirection without clarity)
- When the extracted function would need 5+ parameters (wrong extraction boundary)
- Performance-critical inner loops (function call overhead in CPython)

**Sources**: [Martin Fowler](https://martinfowler.com/bliki/FunctionLength.html), [Refactoring Guru](https://refactoring.guru/extract-method)

### 2.4 Dictionary Dispatch (Eliminates Branching)

Replace value-based if/elif chains with dictionary lookups. Reduces cognitive complexity to 0.

```python
# BEFORE — long if-elif chain (Cognitive Complexity: 10+)
def get_tax_rate(state: str) -> float:
    if state == "CA":
        return 0.0725
    elif state == "NY":
        return 0.08
    elif state == "TX":
        return 0.0625
    # ... 8 more branches
    else:
        return 0.05

# AFTER — dictionary lookup (Cognitive Complexity: 0)
TAX_RATES: dict[str, float] = {
    "CA": 0.0725, "NY": 0.08, "TX": 0.0625,
    "FL": 0.06, "WA": 0.065, "OR": 0.0,
}

def get_tax_rate(state: str) -> float:
    return TAX_RATES.get(state, 0.05)
```

**Function dispatch variant:**
```python
COMMAND_HANDLERS: dict[str, Callable[[dict], Result]] = {
    "create": create_item,
    "update": update_item,
    "delete": delete_item,
}

def process_command(cmd: str, data: dict) -> Result:
    handler = COMMAND_HANDLERS.get(cmd)
    if handler is None:
        raise ValueError(f"Unknown command: {cmd}")
    return handler(data)
```

**When NOT to use:** Don't replace 2-3 branch `if/elif` with a dict — the overhead reduces readability for trivial cases. Use `match/case` when you need destructuring or guard conditions.

**Sources**: [Martin Heinz](https://martinheinz.dev/blog/90), [Better Stack Pattern Matching](https://betterstack.com/community/guides/scaling-python/python-pattern-matching/)

### 2.5 State Machine Simplification

When an entity has 3+ states with event-dependent transitions, replace nested conditionals with a transition table.

```python
# BEFORE — nested conditionals (Cognitive Complexity: 12+)
def handle_event(state: str, event: str) -> str:
    if state == "idle":
        if event == "start":
            return "running"
        elif event == "error":
            return "failed"
    elif state == "running":
        if event == "pause":
            return "paused"
        # ... more nesting
    return state

# AFTER — data-driven transition table (Cognitive Complexity: 0)
TRANSITIONS: dict[tuple[str, str], str] = {
    ("idle", "start"): "running",
    ("idle", "error"): "failed",
    ("running", "pause"): "paused",
    ("running", "complete"): "done",
    ("running", "error"): "failed",
    ("paused", "resume"): "running",
}

def handle_event(state: str, event: str) -> str:
    return TRANSITIONS.get((state, event), state)
```

**Sources**: [pytransitions](https://github.com/pytransitions/transitions), [python-statemachine](https://python-statemachine.readthedocs.io/)

### 2.6 Complexity Reduction Decision Matrix

| Symptom | Primary Technique | Typical CC Impact |
|---------|------------------|-------------------|
| Nesting depth >= 3 | Guard clauses + extraction | -40 to -70% |
| Function > 30 lines | Extract method (SLAP) | -30 to -60% |
| 5+ if/elif branches on same value | Dict lookup or match/case | -60 to -90% |
| State-dependent branching (3+ states) | State machine (dict/enum) | -70 to -90% |
| Complex boolean expression | De Morgan's + split into guards | -10 to -30% |
| Loop with nested conditionals | Comprehension or extract inner block | -20 to -40% |
| 6+ function parameters | Parameter Object (dataclass) | N/A (structural) |

---

## 3. Readability & Naming

### 3.1 Naming Conventions (Python)

| Entity | Convention | Example |
|--------|-----------|---------|
| Module | `lower_with_under` | `my_module.py` |
| Package | `lower_with_under` | `my_package/` |
| Class | `CapWords` | `MyClass` |
| Exception | `CapWords` + `Error` suffix | `ValidationError` |
| Function/Method | `lower_with_under` | `calculate_total()` |
| Constant | `CAPS_WITH_UNDER` | `MAX_RETRIES = 3` |
| Instance variable | `lower_with_under` | `self.first_name` |
| Internal/protected | `_leading_underscore` | `_internal_method()` |
| Throwaway | `_` | `for _ in range(10)` |

**Naming principles (cross-source consensus, 5+ sources agree):**
- **Describe intent, not mechanism**: `retry_until_connected()` not `do_loop()`
- **Use verb phrases for functions**: `validate_input_data()` not `data()`
- **Boolean predicates use is/has/can**: `is_valid()`, `has_permission()`, `can_delete()`
- **Avoid type in name**: `users` not `user_list`, `count` not `count_int`
- **Avoid vague names**: `route_incoming_request()` not `handle_stuff()`
- **Match comment text**: If you write "check if expired", name the function `is_expired()`
- **Length proportional to scope**: `i` for loop counters, `active_user_count` for module-level

**Sources**: [PEP 8](https://peps.python.org/pep-0008/), [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html), [Clean Code (Martin)](https://gist.github.com/wojteklu/73c6914cc446146b8b533c0988cf8d29)

### 3.2 Self-Documenting Code

**The comment philosophy (consensus across all sources):**
- Comments explain **WHY**, not **WHAT**
- If you write a comment explaining what code does, extract it into a well-named function instead
- Docstrings are required for public APIs, nontrivial functions, and non-obvious logic
- Never comment out code — delete it; version control preserves history
- TODO comments must link to tickets (Palantir practice)

```python
# WRONG — comment explains what
# Check if the user is eligible for discount
if user.years_active > 2 and user.total_purchases > 1000:
    apply_discount(user)

# RIGHT — self-documenting with named function
if is_eligible_for_discount(user):
    apply_discount(user)

def is_eligible_for_discount(user: User) -> bool:
    return user.years_active > 2 and user.total_purchases > 1000
```

**Google Python docstring standard:**
- Required sections: `Args`, `Returns`/`Yields`, `Raises`
- Summary line fits in 80 characters
- Must give enough info to call the function without reading its code

### 3.3 Return Statement Consistency

```python
# WRONG — inconsistent (implicit None return)
def foo(x):
    if x >= 0:
        return math.sqrt(x)
    # Falls through to implicit None — hides a bug

# RIGHT — all paths return explicitly
def foo(x):
    if x >= 0:
        return math.sqrt(x)
    return None
```

### 3.4 Comprehension Readability Threshold

| Characteristic | Use Comprehension | Use Loop |
|---------------|------------------|----------|
| Simple transform (`x.attr`) | Yes | |
| Single filter condition | Yes | |
| Nested loop (2 levels) | Maybe (if short) | Prefer loop |
| Nested loop (3+ levels) | Never | Yes |
| Multiple ternaries | Never | Yes |
| Side effects (print, append to external) | Never | Yes |

**Google Style Guide rule**: Limit comprehensions to a single `for` clause.

```python
# ANTI-PATTERN: Comprehension for side effects
[print(x) for x in items]  # Creates throwaway list of None

# CORRECT
for x in items:
    print(x)
```

**Sources**: [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html), [Trey Hunner: Overusing Comprehensions](https://treyhunner.com/2019/03/abusing-and-overusing-list-comprehensions-in-python/)

---

## 4. Modularity & Architecture

### 4.1 Dependency Injection (Python-Idiomatic)

Python's dynamic nature makes DI simpler than Java/C# — no frameworks needed. Use constructor injection with Protocols.

```python
# WRONG: Tight coupling
class UserService:
    def __init__(self):
        self.repository = UserRepository()  # HARD-CODED dependency

# RIGHT: Constructor injection with Protocol
from typing import Protocol

class UserRepository(Protocol):
    def find_by_id(self, user_id: int) -> User | None: ...
    def save(self, user: User) -> User: ...

class UserService:
    def __init__(self, repo: UserRepository):
        self.repo = repo  # Injected — testable, swappable
```

**Composition Root**: Wire all dependencies at the application entry point:

```python
def create_app() -> FastAPI:
    db = init_database()
    user_repo = UserRepository(db)
    user_svc = UserService(user_repo)
    app.include_router(UserHandler(user_svc).router)
    return app
```

**When to skip DI:**
- Simple dataclasses, pure utility functions, internal caches
- Rule of thumb: Start manual. Add a DI container only when you have 15+ services with complex graphs.

**Sources**: [Glukhov: DI Python Way](https://www.glukhov.org/post/2025/12/dependency-injection-in-python/), [ArjanCodes: DI Best Practices](https://arjancodes.com/blog/python-dependency-injection-best-practices/)

### 4.2 Protocol vs ABC

| Criterion | Protocol | ABC |
|-----------|----------|-----|
| Subtyping model | Structural (duck typing) | Nominal (explicit inheritance) |
| Third-party classes | Works without modification | Requires subclassing |
| Code reuse | No shared implementation | Can provide concrete methods |
| Runtime enforcement | Optional (`@runtime_checkable`) | Automatic (TypeError on instantiation) |
| Ideal use | DI interfaces, contracts | Frameworks requiring shared base logic |

**Key insight**: "ABCs belong to their subclasses. Protocols belong where they are used." Define Protocols near the consumer, not the provider.

**Sources**: [PEP 544](https://peps.python.org/pep-0544/), [Justin Ellis: ABC vs Protocol](https://jellis18.github.io/post/2022-01-11-abc-vs-protocol/)

### 4.3 Composition Over Inheritance

When a class needs to be specialized along multiple axes, inheritance leads to class explosion. Use the Decorator pattern for stackable composition.

```python
# RIGHT: Composable components
class FileLogger:
    def __init__(self, file):
        self.file = file
    def log(self, message):
        self.file.write(message + '\n')

class LogFilter:
    def __init__(self, pattern, logger):
        self.pattern = pattern
        self.logger = logger
    def log(self, message):
        if self.pattern in message:
            self.logger.log(message)

# Compose at runtime
log = LogFilter('Error', FileLogger(sys.stdout))
```

**When inheritance IS appropriate:**
- Template Method pattern (ABC with concrete + abstract methods for code reuse)
- Genuine single-axis "is-a" relationships that are stable
- Framework hooks where users must override specific methods

**Mixin rules (if you must use them):**
1. Never put state (instance attributes) in mixins
2. Never add `__init__` to mixins
3. Keep each mixin focused on one behavior
4. Prefer composition when boundaries between objects need to be clear

**Sources**: [Python Patterns Guide](https://python-patterns.guide/gang-of-four/composition-over-inheritance/), [Real Python](https://realpython.com/inheritance-composition-python/)

### 4.4 Module Boundary Design

**Recommended layout (src layout):**
```
project/
├── src/
│   └── my_package/
│       ├── __init__.py
│       ├── module_a.py
│       └── module_b.py
├── tests/
│   └── test_module_a.py
└── pyproject.toml
```

**Public API rules:**
1. Always define `__all__` — explicit is better than implicit
2. Import frequently-used items into `__init__.py` for clean external access
3. Use `_underscore` prefix for internal names
4. Keep `__init__.py` minimal — import and declare, do not implement

**When to split modules:**
- LCOM4 components > 1 (disconnected function clusters)
- Mixed responsibilities (parsing + API calls + formatting in one module)
- High Ca (many dependents) + low cohesion (priority refactoring)

**Sources**: [Hitchhiker's Guide to Python](https://docs.python-guide.org/writing/structure/), [pyOpenSci Guide](https://www.pyopensci.org/python-package-guide/package-structure-code/python-package-structure.html)

### 4.5 Adapter/Facade for External Dependencies

Wrap third-party APIs behind your own interface for testability and changeability.

```python
class GitHubClient:
    """Facade around GitHub REST API"""
    def get_release_date(self, owner, repo, version):
        resp = requests.get(f"{BASE_URL}/repos/{owner}/{repo}/releases/tags/{version}")
        resp.raise_for_status()
        return resp.json()["published_at"]

# Business logic depends on abstraction, not requests library
def generate_changelog(client: GitHubClient, owner, repo, version):
    release_dt = client.get_release_date(owner, repo, version)
    # ... pure business logic
```

**Key benefit**: When migrating from REST to GraphQL, only the facade changes. Business logic remains identical.

**Sources**: [Siv Scripts: Facade Pattern](https://alysivji.com/clean-architecture-with-the-facade-pattern.html), [Refactoring Guru: Adapter Pattern](https://refactoring.guru/design-patterns/adapter/python/example)

### 4.6 Testability Impact by Pattern

| Pattern | Testability Improvement | Mechanism |
|---------|------------------------|-----------|
| Constructor Injection | High | Swap real deps for mocks/fakes |
| Protocol interfaces | High | Define minimal contracts, easy to stub |
| Composition (vs inheritance) | High | Test components in isolation |
| Facade/Adapter | High | Isolate external dependencies behind testable boundary |
| Strategy (functions) | High | Pass test functions directly |
| `__all__` / module boundaries | Medium | Clear boundaries reduce accidental coupling |

---

## 5. Hardening & Security

### 5.1 Tier 1: MUST ENFORCE (Blocking Vulnerabilities)

| # | Rule | CWE | Detection Tool |
|---|------|-----|----------------|
| 1 | Never use `eval()`, `exec()` with untrusted input | CWE-94 | Bandit B307 |
| 2 | Always use parameterized queries (never f-string SQL) | CWE-89 | Bandit B608, Semgrep |
| 3 | Never use `shell=True` with user input in subprocess | CWE-78 | Bandit B602, Semgrep |
| 4 | Never use `pickle.loads()` on untrusted data | CWE-502 | Bandit B301 |
| 5 | Never hardcode secrets/credentials | CWE-798 | Bandit B105-B107 |
| 6 | Never use `assert` for security/input validation | CWE-617 | Bandit B101 |
| 7 | Always validate input at trust boundaries | CWE-20 | Manual review |
| 8 | Never use bare `except:` or `except Exception: pass` | CWE-397 | Ruff B001 |
| 9 | Always use `secrets` module for tokens (not `random`) | CWE-330 | Bandit B311 |
| 10 | Always validate file paths against traversal | CWE-22 | Semgrep |

### 5.2 Tier 2: SHOULD ENFORCE (Defense in Depth)

| # | Rule | CWE |
|---|------|-----|
| 11 | Initialize security booleans to `False` (default deny) | N/A |
| 12 | Use allowlisting over denylisting for input validation | CWE-184 |
| 13 | Log all authentication and authorization failures | CWE-778 |
| 14 | Never log sensitive data (passwords, tokens, PII) | CWE-532 |
| 15 | Use defensive copying for mutable shared state | N/A |
| 16 | Verify all AI-suggested dependencies exist and are secure | N/A |
| 17 | Implement rate limiting on all public endpoints | CWE-400 |
| 18 | Return minimal data from APIs (use DTOs, not models) | CWE-200 |
| 19 | Set `debug=False` in all production configurations | CWE-489 |
| 20 | Use TOCTOU-safe patterns (try/except over if-exists/then-open) | CWE-362 |

### 5.3 Input Validation Patterns

```python
# WRONG: No validation
def process_order(user_data):
    price = user_data["price"]
    return price * user_data["quantity"]

# RIGHT: Validate at trust boundary with Pydantic
from pydantic import BaseModel, Field
from typing import Annotated

class OrderRequest(BaseModel):
    price: Annotated[float, Field(gt=0, le=100000)]
    quantity: Annotated[int, Field(ge=1, le=10000)]
    product_id: str

def process_order(raw_data: dict) -> float:
    order = OrderRequest(**raw_data)  # Raises ValidationError if invalid
    return order.price * order.quantity
```

**Validation rules:**
1. Validate on server side — client-side is UX only
2. Use allowlisting — define what IS authorized
3. Canonicalize before validating — normalize Unicode, decode URL encoding
4. Reject, don't sanitize — safer to reject invalid input than clean it
5. Use anchored regex: always `^pattern$`

### 5.4 Error Handling Patterns

```python
# ANTI-PATTERN 1: Silent swallowing
try:
    risky_operation()
except Exception:
    pass  # BUG: Hides all errors including security failures

# ANTI-PATTERN 2: Default permissive on failure
def check_permission(user, resource):
    is_allowed = True  # DANGEROUS default!
    try:
        is_allowed = auth_service.check(user, resource)
    except AuthServiceError:
        pass
    return is_allowed  # True even when auth service is down!

# CORRECT: Fail-safe default deny
def check_permission(user: User, resource: Resource) -> bool:
    is_allowed = False  # Fail-safe: DENY
    try:
        is_allowed = auth_service.check(user, resource)
    except AuthServiceError:
        logger.error("Auth service unavailable, denying access")
    return is_allowed
```

**Error handling rules (consensus across 5+ sources):**
1. Never use bare `except:` — always catch specific types
2. Never silently swallow — at minimum `logger.exception()`
3. Default to deny/fail-closed for security-related errors
4. Preserve exception chains: `raise NewError() from original_err`
5. Minimize try block scope — only wrap the line that can raise
6. Use `finally` or context managers for cleanup
7. Don't leak implementation details in error responses

### 5.5 Defensive Copying

```python
# WRONG: Storing mutable reference directly
class UserConfig:
    def __init__(self, permissions: list[str]):
        self.permissions = permissions  # Stores reference, not copy!

perms = ["read"]
config = UserConfig(perms)
perms.append("admin")  # Mutates config.permissions too!

# RIGHT: Defensive copying
class UserConfig:
    def __init__(self, permissions: list[str]):
        self.permissions = list(permissions)  # Shallow copy on input

    @property
    def permissions_view(self) -> tuple[str, ...]:
        return tuple(self.permissions)  # Return immutable view
```

**Immutability tools:**
- `tuple` instead of `list`, `frozenset` instead of `set`
- `@dataclass(frozen=True)` for immutable data objects
- `types.MappingProxyType` for read-only dict views
- `typing.Final` for immutability intent

### 5.6 AI-Specific Security Concerns

| Pattern | Failure Rate | Mitigation |
|---------|-------------|------------|
| Slopsquatting (hallucinated packages) | 5-21% | Verify every package exists before installing |
| XSS in web code | 86% failure | Always encode output |
| Hardcoded secrets | Very high | Strip all literals; use env vars |
| Missing input validation | Root cause of injection | Validate at every trust boundary |
| Overly permissive CORS | Very high | Never set `*` for origins |
| Missing rate limiting | Very high | Always add to public endpoints |
| Excessive data exposure | High | Use DTOs; never return DB models directly |

**Sources**: [OWASP Secure Coding Practices](https://owasp.org/www-project-secure-coding-practices-quick-reference-guide/), [CWE Top 25 2024](https://cwe.mitre.org/top25/archive/2024/2024_cwe_top25.html), [OpenSSF Python Secure Coding Guide](https://best.openssf.org/Secure-Coding-Guide-for-Python/), [Veracode](https://www.veracode.com/blog/ai-generated-code-security-risks/), [Bandit](https://github.com/PyCQA/bandit)

---

## 6. Code Structure & Organization

### 6.1 Python-Specific Coding Rules (PEP 8 + Google + Ruff Consensus)

**Comparisons:**
```python
# Use identity for None
if x is not None:     # CORRECT
if x == None:         # WRONG
if x:                 # WRONG (catches False, 0, empty string too)

# Use falsy for sequence emptiness
if not seq:           # CORRECT (Pythonic)
if len(seq) == 0:     # WRONG
```

**Imports:**
- One import per line, never `import os, sys`
- Order: `__future__` > stdlib > third-party > local
- Use absolute imports, never relative
- Use `TYPE_CHECKING` block for type-only imports

**Type hints:**
```python
# Modern syntax (Python 3.10+)
def process(items: list[int], name: str | None = None) -> dict[str, int]:
    ...

# Prefer abstract types for parameters
def process(items: Sequence[int]) -> ...:  # Not list[int]
```

**Mutable default arguments:**
```python
# WRONG — shared mutable default
def add_item(item, items=[]):  # Created once, shared across calls!
    items.append(item)
    return items

# RIGHT
def add_item(item, items: list | None = None):
    if items is None:
        items = []
    items.append(item)
    return items
```

### 6.2 EAFP vs LBYL Decision Matrix

| Scenario | Recommendation | Why |
|----------|---------------|-----|
| Operations likely to fail | LBYL (check first) | Avoids exception overhead |
| Irrevocable operations with side effects | LBYL | Can't undo the operation |
| Operations unlikely to fail | EAFP (try/except) | Fast path is common path |
| File/network I/O | EAFP | Race conditions between check and operation |
| Dict/attribute access | EAFP | Respects duck typing |

### 6.3 Recommended Ruff Configuration

```toml
[tool.ruff.lint]
extend-select = [
    "F",   # Pyflakes — logical errors
    "E",   # pycodestyle errors
    "W",   # pycodestyle warnings
    "I",   # isort — import sorting
    "UP",  # pyupgrade — modernize syntax
    "B",   # bugbear — likely bugs
    "SIM", # simplify — more elegant patterns
    "C4",  # comprehensions — correct usage
    "RET", # return practices
    "PTH", # pathlib over os.path
    "TC",  # type checking imports
]
```

**Key Bugbear rules:**
| Rule | Detection | Why |
|------|-----------|-----|
| B001 | Bare `except:` | Catches system exits, memory errors |
| B006 | Mutable default arguments | Shared across calls |
| B904 | Missing `raise ... from err` | Loses traceback context |
| B905 | `zip()` without `strict=` | Silent length mismatch |

### 6.4 Logging Best Practices

| Practice | Why |
|----------|-----|
| Use `logging` module, not `print()` | Levels, rotation, multiple destinations |
| Use lazy formatting: `log.info("User %s", user_id)` | Avoids f-string evaluation when disabled |
| Include context: user IDs, request IDs | Enables correlation in production |
| Use structured logging (JSON) | Machine-readable for monitoring |
| Never log secrets | Passwords, tokens, PII |

**Sources**: [PEP 8](https://peps.python.org/pep-0008/), [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html), [Ruff Rules](https://docs.astral.sh/ruff/rules/), [flake8-bugbear](https://github.com/PyCQA/flake8-bugbear)

---

## 7. AI-Agent-Specific Guidance

### 7.1 What AI Coding Agents Get Wrong (Quantitative)

| Metric | AI PRs | Human PRs | Multiplier |
|--------|--------|-----------|------------|
| Issues per PR | 10.83 | 6.45 | **1.7x** |
| Readability issues | Higher | Lower | **3x+** |
| Performance (excessive I/O) | Higher | Lower | **~8x** |
| Security vulnerabilities | ~45% | ~20% | **~2x** |
| Formatting problems | Higher | Lower | **2.66x** |
| Logic & correctness issues | Higher | Lower | **1.75x** |
| Code complexity increase | +25.1% | baseline | (Cursor study) |

**Source**: [CodeRabbit Report](https://www.coderabbit.ai/blog/state-of-ai-vs-human-code-generation-report), [Veracode 2025](https://www.veracode.com/blog/ai-generated-code-security-risks/)

### 7.2 Eight Failure Patterns of AI-Generated Code

| # | Pattern | Fix |
|---|---------|-----|
| 1 | Hallucinated APIs (1 in 5 samples) | Verify imports in package registries |
| 2 | Security vulnerabilities that look functional | CodeQL/Bandit pre-commit |
| 3 | Performance anti-patterns (O(n^2) where O(n) exists) | Profile before committing |
| 4 | Error handling assumes happy paths | Structured error boundaries |
| 5 | Missing edge cases (empty, null, max, Unicode) | Test boundary conditions |
| 6 | Outdated library usage | Verify deprecation status |
| 7 | Data model mismatches | Validate with type system |
| 8 | Missing context dependencies (env vars, config) | Environment validation scripts |

**Quick triage (catches ~60% in 3 minutes):** Linter -> Type checker -> Existing tests

**Source**: [Augment Code](https://www.augmentcode.com/guides/debugging-ai-generated-code-8-failure-patterns-and-fixes)

### 7.3 Anthropic's Claude Code Best Practices

1. **Give Claude a way to verify its work** — THE single highest-leverage practice. Tests, screenshots, expected outputs.
2. **Explore first, then plan, then code** — Four-phase workflow: Explore -> Plan -> Implement -> Commit.
3. **Provide specific context** — file names, scenarios, existing patterns to follow.
4. **Keep CLAUDE.md concise** — Under 300 lines. For each line ask "Would removing this cause mistakes?" If not, cut it.
5. **Manage context aggressively** — `/clear` between tasks, subagents for investigation.
6. **Course-correct early** — After 2 failed corrections, `/clear` and rewrite the prompt.

**Source**: [Claude Code Best Practices](https://www.anthropic.com/engineering/claude-code-best-practices)

### 7.4 OpenAI Codex's Harness Engineering Principles

1. **Work depth-first** — Break goals into building blocks, use them to unlock complex tasks
2. **Give agents more context** — Organize and expose the right information
3. **Use agents to validate agents** — Custom skills for self-QA
4. **Iterate on prompts** — Treat prompts as code, version and improve
5. **Embed domain knowledge** — Build custom skills for domain operations

**Source**: [Harness Engineering (OpenAI)](https://openai.com/index/harness-engineering/)

### 7.5 Code Structure for Agent-Friendliness

From Armin Ronacher and Ben Houston's research:

1. **Flatten directory structures** — shallow, semantically-named; co-locate related functionality
2. **Eliminate re-exports and indirection** — direct imports help AI trace dependencies
3. **Enforce compile-time validation** — strong typing > runtime conventions
4. **Consistent file organization** — same naming/structure patterns (~30% reduction in AI comprehension time)
5. **Prefer code generation over dependencies** — reduces supply chain risk
6. **Use plain SQL** — AI produces excellent SQL; ORMs add indirection
7. **Type-driven development with discriminated unions** — guides AI to correct behavior

**Measurable impact**: Ben Houston improved AI success rate from ~60% to ~100% on route/command tasks when replacing convention-based exports with typesafe declarations.

**Sources**: [Armin Ronacher](https://lucumr.pocoo.org/2025/6/12/agentic-coding/), [Ben Houston](https://benhouston3d.com/blog/agentic-coding-best-practices)

### 7.6 Context Engineering Principles

1. **Less is more** — focused, relevant context outperforms comprehensive dumps
2. **Token dilution is real** — 7x improvement with 90% less tokens (Refine.dev study)
3. **Work in small increments** — break features into testable chunks
4. **Fresh context per task** — clear between unrelated tasks
5. **Instruction files under 300 lines** — bloated files cause rules to be ignored

**Source**: [Anthropic: Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents), [Refine.dev](https://refine.dev/blog/quality-code-generation/)

---

## 8. Industry Consensus Patterns

### 8.1 Patterns Agreed by 5+ Independent Sources

These patterns appeared across Clean Code, Google Style Guide, PEP 8, SonarQube, Airbnb, Ruff, Pylint, NASA, OWASP, and multiple company code review guides:

1. **Use descriptive, intention-revealing names** — the single most impactful readability practice
2. **Keep functions small and focused** — single responsibility; exact size varies but direction is universal
3. **Avoid mutable default arguments** — PEP 8, Google, Ruff B006, Pylint, SonarQube
4. **Catch specific exceptions, never bare except** — PEP 8, Google, Ruff B001, SonarQube
5. **Don't Repeat Yourself (DRY)** — duplicated code is "the most critical issue in software development"
6. **Minimize nesting depth** — guard clauses, early returns; all sources recommend flat > nested
7. **Use consistent naming conventions** — every style guide; `snake_case` for Python
8. **Remove dead code** — don't comment out; version control preserves history
9. **Explicit is better than implicit** — explicit returns, imports, types
10. **Code is read far more than written** — optimize for the reader

### 8.2 Patterns Agreed by 3-4 Sources

11. **Limit function parameters to 3** — use parameter objects for more
12. **Prefer composition over inheritance** — reduces coupling
13. **Use type annotations** — catches bugs early, serves as documentation
14. **Use guard clauses for preconditions** — reduces nesting, clarifies happy path
15. **Don't use flag (boolean) arguments** — split into separate functions
16. **Separate concerns** — each module/class has one job
17. **Use context managers for resources** — `with` statements prevent leaks
18. **Test your code** — tests are as important as production code
19. **Validate at trust boundaries** — OWASP, CERT, SANS all agree
20. **Default deny** — base access on permission, not exclusion

### 8.3 Code Review Consensus (Items Appearing in 6+ Company Guides)

| # | Checklist Item | Sources |
|---|---------------|---------|
| 1 | Code accomplishes stated purpose | Google, Microsoft, Palantir, Thoughtbot, Shopify, Uber |
| 2 | Keep PRs small (200-400 LOC) | Google, Shopify, LinkedIn, Netlify, SmartBear/Cisco |
| 3 | Tests included with code changes | Google, Microsoft, Palantir, Thoughtbot |
| 4 | Naming is clear and intention-revealing | Google, Microsoft, Palantir, Thoughtbot |
| 5 | Comments explain WHY, not WHAT | Google, Microsoft, Palantir |
| 6 | No unnecessary complexity / over-engineering | Google, Microsoft, Palantir |
| 7 | Error handling is graceful and explicit | Microsoft, Palantir, Uber |
| 8 | Follows established style guides | Google, Microsoft, Palantir, Thoughtbot |
| 9 | Edge cases handled | Google, Microsoft, Palantir |
| 10 | Use linters for style (don't waste human review time) | Thoughtbot, Uber, Code Review Pyramid |

### 8.4 The Code Review Pyramid (Priority Order)

From bottom (hardest to change later) to top (easiest to fix):

1. **API/Design** — public interfaces, contracts, architecture fit (Human only)
2. **Implementation** — correctness, security, performance (Mostly human)
3. **Documentation** — how to use the code (Human)
4. **Tests** — verification (Human + CI)
5. **Code Style** — formatting, naming (Fully automate)

**Key insight**: Most teams over-invest in style comments and under-invest in API/design review. 75% of code review defects are maintainability issues, not functional bugs.

**Sources**: [Google eng-practices](https://google.github.io/eng-practices/review/), [Microsoft Engineering Playbook](https://microsoft.github.io/code-with-engineering-playbook/), [Palantir](https://blog.palantir.com/code-review-best-practices-19e02780015f), [Code Review Pyramid](https://www.morling.dev/blog/the-code-review-pyramid/), [SmartBear/Cisco Study](https://static0.smartbear.co/support/media/resources/cc/book/code-review-cisco-case-study.pdf)

---

## 9. Measurable Thresholds

### 9.1 Consolidated Threshold Reference

| Metric | Conservative | Moderate | Aggressive | Primary Source |
|--------|-------------|----------|------------|----------------|
| **Cognitive complexity per function** | <= 10 | <= 15 | <= 25 | SonarSource |
| **Cyclomatic complexity per function** | <= 10 | <= 15 | <= 20 | McCabe/NIST |
| **Function length (lines)** | <= 20 | <= 40 | <= 60 | Clean Code / Google / NASA |
| **Function parameters** | <= 3 | <= 4 | <= 5 | Clean Code / SonarQube |
| **Nesting depth** | <= 2 | <= 3 | <= 4 | Linux Kernel / SonarQube |
| **Class methods** | <= 10 | <= 20 | <= 30 | PMD |
| **Class length (lines)** | <= 200 | <= 500 | <= 900 | Rule of 30 |
| **Local variables per function** | <= 5 | <= 7 | <= 10 | Linux Kernel |
| **Maintainability index** | >= 40 | >= 20 | >= 10 | Microsoft |
| **Line length** | 79 (PEP 8) | 88 (Black) | 120 | PEP 8 / Black |
| **PR size (LOC)** | 200 | 300 | 400 | SmartBear/Cisco / LinkedIn |
| **Code review session** | 30 min | 60 min | 90 min max | SmartBear/Cisco |
| **Review rate** | 200 LOC/hr | 300 LOC/hr | 400 max | SmartBear/Cisco |

### 9.2 Recommended Defaults for AI Coding Agent

Based on cross-source analysis, these are the recommended defaults — strict enough to catch AI's common mistakes, pragmatic enough to avoid false positives:

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| Cognitive complexity | **15** | SonarQube default; catches AI's nesting tendency |
| Function length | **40 lines** | Google guideline; strict enough without being extreme |
| Function parameters | **4** | Allows practical functions; triggers grouping for 5+ |
| Nesting depth | **3** | Chomsky/Weinberg finding; triggers guard clause refactoring |
| Line length | **88** | Black's default; good PEP 8 compromise |
| Comprehension complexity | **1 for clause** | Google rule; prevents unreadable comprehensions |

### 9.3 Quality Gate Configuration

```bash
#!/usr/bin/env bash
# scripts/gate.sh — All must pass before commit
set -euo pipefail

echo "=== Format ==="
ruff format --check .

echo "=== Lint ==="
ruff check .

echo "=== Type Check ==="
pyright

echo "=== Security ==="
bandit -r src/ -c pyproject.toml

echo "=== Tests ==="
pytest --tb=short -q

echo "=== All gates passed ==="
```

---

## 10. Coding Workflow & Documentation Best Practices

### 10.1 AI Instruction File Standards

Three major formats have emerged:

| Format | Tool | Key Characteristic |
|--------|------|--------------------|
| `CLAUDE.md` | Claude Code | Under 300 lines; hierarchical placement |
| `.cursor/rules/*.mdc` | Cursor | Under 500 lines; YAML frontmatter |
| `.github/copilot-instructions.md` | Copilot | Under 1000 lines; glob-scoped |

**What effective instruction files include (WHAT / WHY / HOW):**
1. **WHAT**: Tech stack, project structure, codebase map
2. **WHY**: Project purpose and what different parts accomplish
3. **HOW**: Build commands, verification methods, testing, compilation

**Rules for instruction files:**
- Only universally relevant content
- "Never send an LLM to do a linter's job" — use deterministic tools
- Prefer pointers to copies — reference files rather than embedding snippets
- Never auto-generate — manually craft every line
- Use emphasis ("IMPORTANT", "YOU MUST") for critical adherence
- Iterate based on actual mistakes, not speculation

### 10.2 Architecture Decision Records (ADRs)

- Use for any decision that is hard to reverse later
- Present-tense imperative filenames: `choose-database.md`
- One decision per record
- Include rationale and consequences
- Lifecycle: Initiating -> Researching -> Evaluating -> Implementing -> Maintaining -> Sunsetting
- Tools: [adr-tools](https://github.com/npryce/adr-tools), [MADR](https://github.com/adr/madr)

### 10.3 What Makes Coding Standard Docs Effective

Based on analysis of the most successful open-source style guides:

1. **Concise and scannable** — headings, bullets, tables
2. **Code examples for every rule** — both good AND bad patterns
3. **Reasoning behind each rule** — the "why"
4. **Enforced by tooling** — not just documentation
5. **Version controlled as living documents**
6. **Iterated based on actual mistakes**
7. **Progressive disclosure** — core rules first, details on demand

### 10.4 Emerging Trends (2025-2026)

- AI instruction files becoming standard project artifacts
- Spec-driven development ([spec-kit](https://github.com/github/spec-kit), 72.9k stars) gaining rapid adoption
- Context engineering as a core developer skill
- Multi-agent development patterns
- Deterministic hooks for guaranteed actions vs advisory rules

**Sources**: [Claude Code Docs](https://code.claude.com/docs/en/best-practices), [builder.io AGENTS.md](https://www.builder.io/blog/agents-md), [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)

---

## 11. Code Smells Catalog (Quick Reference)

### Within Classes

| Smell | Signal | Fix |
|-------|--------|-----|
| Long Method | > 40 lines or hard to summarize in one sentence | Extract method |
| Long Parameter List | > 3-4 parameters | Parameter Object (dataclass) |
| Duplicated Code | Same logic in 2+ places | Extract shared function |
| Dead Code | Never-executed paths | Delete (VCS preserves it) |
| Magic Numbers | Unexplained literals | Named constants |
| Comments explaining "what" | Comment describes code behavior | Make code self-documenting |
| Conditional Complexity | Large if/elif chains | Dict dispatch, polymorphism, state machine |
| Speculative Generality | Features for hypothetical future | Delete (YAGNI) |
| Flag Arguments | Boolean parameter directing behavior | Split into separate functions |

### Between Classes

| Smell | Signal | Fix |
|-------|--------|-----|
| Feature Envy | Method uses another class's data heavily | Move method to that class |
| God Class | Too many responsibilities | Split by SRP |
| Shotgun Surgery | One change requires editing many classes | Consolidate related logic |
| Inappropriate Intimacy | Classes know too much about each other | Reduce coupling; use interfaces |
| Primitive Obsession | Using primitives for complex concepts | Create domain objects |
| Data Clumps | Groups of data always appearing together | Encapsulate in class/dataclass |

**Sources**: [Martin Fowler](https://martinfowler.com/bliki/CodeSmell.html), [Coding Horror](https://blog.codinghorror.com/code-smells/), [Refactoring Catalog](https://refactoring.com/catalog/)

---

## 12. Sources (Comprehensive)

### AI Coding Agent Companies
- [Anthropic: Claude Code Best Practices](https://www.anthropic.com/engineering/claude-code-best-practices)
- [Claude Code Documentation](https://code.claude.com/docs/en/best-practices)
- [How Anthropic Teams Use Claude Code](https://www-cdn.anthropic.com/58284b19e702b49db9302d5b6f135ad8871e7658.pdf)
- [OpenAI: Codex Prompting Guide](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide/)
- [OpenAI: Harness Engineering](https://openai.com/index/harness-engineering/)
- [Google DeepMind: AlphaCode](https://deepmind.google/blog/competitive-programming-with-alphacode/)
- [Cursor Rules Documentation](https://cursor.com/docs/context/rules)
- [Windsurf Documentation](https://docs.windsurf.com/windsurf/getting-started)
- [Sourcegraph Cody Architecture](https://sourcegraph.com/blog/anatomy-of-a-coding-assistant)
- [Amazon Q Developer](https://aws.amazon.com/q/developer/)

### Software Engineering Standards
- [Clean Code Summary](https://gist.github.com/wojteklu/73c6914cc446146b8b533c0988cf8d29)
- [SOLID Principles in Python](https://realpython.com/solid-principles-python/)
- [Google Engineering Practices](https://google.github.io/eng-practices/review/)
- [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)
- [Microsoft Framework Design Guidelines](https://learn.microsoft.com/en-us/dotnet/standard/design-guidelines/)
- [NASA JPL Power of 10 Rules](https://spinroot.com/gerard/pdf/P10.pdf)
- [SEI CERT Top 10 Secure Coding](https://wiki.sei.cmu.edu/confluence/display/seccode/Top+10+Secure+Coding+Practices)
- [Linux Kernel Coding Style](https://docs.kernel.org/process/coding-style.html)

### Code Quality Metrics
- [SonarSource Cognitive Complexity Whitepaper](https://www.sonarsource.com/docs/CognitiveComplexity.pdf)
- [SonarSource: 5 Tips for Reducing CC](https://www.sonarsource.com/blog/5-clean-code-tips-for-reducing-cognitive-complexity/)
- [Martin Fowler: Function Length](https://martinfowler.com/bliki/FunctionLength.html)
- [Microsoft Maintainability Index](https://learn.microsoft.com/en-us/visualstudio/code-quality/code-metrics-maintainability-index-range-and-meaning)
- [Software by Science: Function Length Research](https://softwarebyscience.com/very-short-functions-are-a-code-smell-an-overview-of-the-science-on-function-length/)

### Python Standards & Tools
- [PEP 8](https://peps.python.org/pep-0008/)
- [PEP 20 — The Zen of Python](https://peps.python.org/pep-0020/)
- [PEP 544 — Protocols](https://peps.python.org/pep-0544/)
- [Ruff Rules Reference](https://docs.astral.sh/ruff/rules/)
- [flake8-bugbear](https://github.com/PyCQA/flake8-bugbear)
- [Bandit — Python Security Linter](https://github.com/PyCQA/bandit)
- [Black Formatter](https://github.com/psf/black)
- [Python Typing Best Practices](https://typing.python.org/en/latest/reference/best_practices.html)

### Security
- [OWASP Secure Coding Practices](https://owasp.org/www-project-secure-coding-practices-quick-reference-guide/)
- [OWASP Top 10:2021](https://owasp.org/Top10/2021/)
- [CWE Top 25 2024](https://cwe.mitre.org/top25/archive/2024/2024_cwe_top25.html)
- [OpenSSF Secure Coding Guide for Python](https://best.openssf.org/Secure-Coding-Guide-for-Python/)
- [Semgrep Python Rules](https://semgrep.dev/p/python)
- [Veracode: AI Code Security Risks](https://www.veracode.com/blog/ai-generated-code-security-risks/)
- [Apiiro: 4x Velocity 10x Vulnerabilities](https://apiiro.com/blog/4x-velocity-10x-vulnerabilities-ai-coding-assistants-are-shipping-more-risks/)

### Code Review
- [Google eng-practices](https://google.github.io/eng-practices/review/reviewer/looking-for.html)
- [Microsoft Engineering Playbook](https://microsoft.github.io/code-with-engineering-playbook/code-reviews/)
- [Palantir Code Review](https://blog.palantir.com/code-review-best-practices-19e02780015f)
- [Thoughtbot Code Review Guide](https://github.com/thoughtbot/guides/blob/main/code-review/README.md)
- [Netlify Feedback Ladders](https://www.netlify.com/blog/2020/03/05/feedback-ladders-how-we-encode-code-reviews-at-netlify/)
- [Shopify Great Code Reviews](https://shopify.engineering/great-code-reviews)
- [Meta Code Review Time](https://engineering.fb.com/2022/11/16/culture/meta-code-review-time-improving/)
- [Uber uReview](https://www.uber.com/blog/ureview/)
- [LinkedIn Code Ownership](https://www.linkedin.com/blog/engineering/developer-experience-productivity/scaling-collective-code-ownership-with-code-reviews)
- [Code Review Pyramid](https://www.morling.dev/blog/the-code-review-pyramid/)
- [Conventional Comments](https://conventionalcomments.org/)
- [SmartBear/Cisco Study](https://static0.smartbear.co/support/media/resources/cc/book/code-review-cisco-case-study.pdf)

### AI Code Quality Research
- [CodeRabbit: AI vs Human Code Report](https://www.coderabbit.ai/blog/state-of-ai-vs-human-code-generation-report)
- [GitClear AI Code Quality 2025](https://www.gitclear.com/ai_assistant_code_quality_2025_research)
- [Augment Code: 8 Failure Patterns](https://www.augmentcode.com/guides/debugging-ai-generated-code-8-failure-patterns-and-fixes)
- [Martin Fowler: AI Code Quality Assessment](https://martinfowler.com/articles/exploring-gen-ai/ccmenu-quality.html)
- [CodeScene: AI Coding Best Practice Patterns](https://codescene.com/blog/agentic-ai-coding-best-practice-patterns-for-speed-with-quality)

### Architecture & Modularity
- [Python Patterns: Composition Over Inheritance](https://python-patterns.guide/gang-of-four/composition-over-inheritance/)
- [Real Python: Inheritance and Composition](https://realpython.com/inheritance-composition-python/)
- [Better Stack: Python DI](https://betterstack.com/community/guides/scaling-python/python-dependency-injection/)
- [Refactoring Guru: Design Patterns Python](https://refactoring.guru/design-patterns/python)
- [Hitchhiker's Guide to Python: Structuring](https://docs.python-guide.org/writing/structure/)

### Complexity Reduction
- [Refactoring Guru: Guard Clauses](https://refactoring.guru/replace-nested-conditional-with-guard-clauses)
- [Jeff Atwood: Flattening Arrow Code](https://blog.codinghorror.com/flattening-arrow-code/)
- [Martin Heinz: Dictionary Dispatch](https://martinheinz.dev/blog/90)
- [complexipy (Rust CLI)](https://github.com/rohaquinlop/complexipy)
- [Trey Hunner: Overusing Comprehensions](https://treyhunner.com/2019/03/abusing-and-overusing-list-comprehensions-in-python/)

### Coding Workflows & Documentation
- [Anthropic: Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [builder.io: AGENTS.md Guide](https://www.builder.io/blog/agents-md)
- [awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)
- [awesome-cursorrules](https://github.com/PatrickJS/awesome-cursorrules)
- [GitHub spec-kit](https://github.com/github/spec-kit)
- [Armin Ronacher: Agentic Coding](https://lucumr.pocoo.org/2025/6/12/agentic-coding/)
- [Ben Houston: Agentic Coding Best Practices](https://benhouston3d.com/blog/agentic-coding-best-practices)
- [Addy Osmani: AI Coding Workflow](https://addyosmani.com/blog/ai-coding-workflow/)
- [Simon Willison: Agentic Engineering Patterns](https://simonwillison.net/2026/Feb/23/agentic-engineering-patterns/)
- [Forge Code: 12 Lessons](https://forgecode.dev/blog/ai-agent-best-practices/)

### Refactoring & Code Smells
- [Martin Fowler: Code Smell](https://martinfowler.com/bliki/CodeSmell.html)
- [Refactoring Catalog](https://refactoring.com/catalog/)
- [Code Smells Catalog (Luzkan)](https://luzkan.github.io/smells/)
- [Coding Horror: Code Smells](https://blog.codinghorror.com/code-smells/)
