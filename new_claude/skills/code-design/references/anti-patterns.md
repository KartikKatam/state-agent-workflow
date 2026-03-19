# Code Design Anti-Patterns

Common design failures with WRONG/RIGHT pairs. Each anti-pattern shows the tempting approach and why it fails, followed by the correct approach.

## Contents

- [1. God Function](#1-god-function) — multiple responsibilities in one function
- [2. Hidden Dependencies](#2-hidden-dependencies) — hard-wired internal resources
- [3. Premature Abstraction](#3-premature-abstraction) — abstracting before third use
- [4. Untestable Side Effects](#4-untestable-side-effects) — compute and act in same body
- [5. Leaky Abstraction](#5-leaky-abstraction) — exposed internal data structures
- [6. Boolean Parameter Explosion](#6-boolean-parameter-explosion) — flag args doubling code paths
- [7. Deep Nesting](#7-deep-nesting) — nested conditionals, quadratic complexity
- [8. Silent Error Swallowing](#8-silent-error-swallowing) — bare except, default permissive
- [9. AI Performance Anti-Patterns](#9-ai-performance-anti-patterns) — string concat, materialized generators, unbounded collections
- [10. Mutable Shared State](#10-mutable-shared-state) — invisible coupling via shared references
- [11. Untyped Public Interfaces](#11-untyped-public-interfaces) — missing annotations on public functions
- [Summary: Design Smell Detection](#summary-design-smell-detection) — 15-smell quick-scan table

## 1. God Function

A single function that handles multiple responsibilities. Requires combinatorial test coverage and breaks on any change to any responsibility.

```python
# WRONG — does three unrelated things
def process_data(filepath):
    # Read
    with open(filepath) as f:
        raw = json.load(f)
    # Transform
    results = []
    for item in raw["items"]:
        if item["score"] > 0.5:
            results.append({"id": item["id"], "value": item["score"] * 100})
    # Write
    with open("output.json", "w") as f:
        json.dump(results, f)

# RIGHT — each function does one thing
def load_items(filepath: str) -> list[dict]:
    with open(filepath) as f:
        return json.load(f)["items"]

def filter_and_transform(items: list[dict], threshold: float) -> list[dict]:
    return [
        {"id": item["id"], "value": item["score"] * 100}
        for item in items
        if item["score"] > threshold
    ]

def save_results(results: list[dict], filepath: str) -> None:
    with open(filepath, "w") as f:
        json.dump(results, f)
```

Why WRONG fails: To test the filter logic, you need a real file. To test with different thresholds, you need different files. Every test is an integration test.

Why RIGHT works: `filter_and_transform` is a pure function — test it with constructed data, no files needed.

## 2. Hidden Dependencies

Functions that internally create the resources they need. Untestable without monkeypatching or real infrastructure.

```python
# WRONG — creates its own connection internally
def get_user(user_id: int) -> User:
    db = Database("postgresql://prod-server/mydb")
    return db.query(User).filter_by(id=user_id).first()

# RIGHT — receives connection as parameter
def get_user(user_id: int, db: Database) -> User:
    return db.query(User).filter_by(id=user_id).first()
```

Why WRONG fails: Every test hits the production database. Test isolation is impossible.

Why RIGHT works: Tests pass a mock or test database. Fast, isolated, no infrastructure required.

## 3. Premature Abstraction

Creating abstractions before seeing enough use cases. The abstraction encodes the wrong pattern, then every new use case fights it.

```python
# WRONG — AbstractProcessorFactory after seeing ONE processor
class AbstractProcessor(ABC):
    @abstractmethod
    def preprocess(self, data): ...
    @abstractmethod
    def process(self, data): ...
    @abstractmethod
    def postprocess(self, data): ...

class ImageProcessor(AbstractProcessor):
    def preprocess(self, data):
        pass  # Empty — doesn't need preprocessing

# RIGHT — wait for three concrete cases, THEN extract
class ImageProcessor:
    def process(self, image): ...
    def postprocess(self, result): ...

class AudioProcessor:
    def normalize(self, audio): ...
    def process(self, audio): ...

class TextProcessor:
    def tokenize(self, text): ...
    def process(self, tokens): ...

# After seeing all three: the common pattern is just `process`.
# Extract only THAT — not the imagined pre/post lifecycle.
```

Why WRONG fails: The abstraction forces every processor into a three-step lifecycle. Only one needs all three. The others carry dead weight.

Why RIGHT works: Three concrete implementations reveal the real shared pattern — `process` only.

## 4. Untestable Side Effects

Functions that compute AND act in the same body. Testing the computation requires triggering the action.

```python
# WRONG — computes best detection AND saves it to disk
def find_and_save_best(detections: list[Detection], output_path: str) -> Detection:
    best = max(detections, key=lambda d: d.confidence)
    with open(output_path, "w") as f:
        json.dump(best.to_dict(), f)
    return best

# RIGHT — separate the decision from the action
def find_best(detections: list[Detection]) -> Detection:
    return max(detections, key=lambda d: d.confidence)

best = find_best(detections)
save_detection(best, output_path)  # only when needed
```

Why WRONG fails: Every test of the selection logic writes a file. Can't test either concern in isolation.

Why RIGHT works: `find_best` is pure — test with constructed data, no I/O.

## 5. Leaky Abstraction

A module that exposes its internal data structures. Consumers depend on internals, so internal changes break consumers.

```python
# WRONG — exposes internal deque to consumers
class TrackHistory:
    def __init__(self):
        self.positions = deque(maxlen=100)  # Leaked!

# RIGHT — expose operations, hide structure
class TrackHistory:
    def __init__(self, max_length: int = 100):
        self._positions: deque[Position] = deque(maxlen=max_length)

    def add(self, pos: Position) -> None:
        self._positions.append(pos)

    def latest(self, n: int = 1) -> list[Position]:
        return list(itertools.islice(reversed(self._positions), n))

    def __len__(self) -> int:
        return len(self._positions)
```

Why WRONG fails: Changing `deque` to `list` breaks every consumer that touches `.positions`.

Why RIGHT works: Consumers use `add()`, `latest()`, `len()`. Internal structure can change freely.

## 6. Boolean Parameter Explosion

Functions controlled by boolean flags. Each flag doubles the number of code paths and test cases.

```python
# WRONG — boolean flags create 2^3 = 8 code paths
def export_report(data, include_header=True, compress=False, validate=True):
    if include_header:
        data = add_header(data)
    if validate:
        check_schema(data)
    output = serialize(data)
    if compress:
        output = gzip.compress(output)
    return output

# RIGHT — separate functions, caller composes
def add_header(data: ReportData) -> ReportData: ...
def validate_report(data: ReportData) -> None: ...
def serialize(data: ReportData) -> bytes: ...
def compress(data: bytes) -> bytes: ...

data = add_header(raw_data)
validate_report(data)
output = compress(serialize(data))
```

Why WRONG fails: Testing all flag combinations requires 8 tests. Adding a fourth flag requires 16.

Why RIGHT works: Each function is tested independently. Composition is explicit at the call site.

## 7. Deep Nesting

Deeply nested conditionals that become impossible to follow. Cognitive complexity grows quadratically with nesting depth.

```python
# WRONG — nested validation (cognitive complexity: 6+, nesting depth: 3)
def process_request(request):
    if request is not None:
        if request.user.is_authenticated:
            if request.data.get("action") in VALID_ACTIONS:
                result = execute_action(request.data["action"], request.data)
                if result.success:
                    return {"status": "ok", "data": result.value}
    return {"status": "error"}

# RIGHT — guard clauses flatten to depth 0
def process_request(request):
    if request is None:
        return {"status": "error"}
    if not request.user.is_authenticated:
        return {"status": "error"}
    if request.data.get("action") not in VALID_ACTIONS:
        return {"status": "error"}
    result = execute_action(request.data["action"], request.data)
    if not result.success:
        return {"status": "error"}
    return {"status": "ok", "data": result.value}
```

Why WRONG fails: Each nesting level adds a mental stack frame the reader must track. At depth 3+, humans lose track of which branch they're in.

Why RIGHT works: Each guard clause is independent. The reader only needs to track one condition at a time. Happy path reads top-to-bottom.

## 8. Silent Error Swallowing

Catching exceptions and doing nothing. Hides bugs, security failures, and data corruption.

```python
# WRONG — bare except silently swallows everything
try:
    risky_operation()
except Exception:
    pass  # SystemExit, MemoryError, KeyboardInterrupt — all hidden

# WRONG — default permissive on failure
def check_permission(user, resource):
    is_allowed = True  # Dangerous default!
    try:
        is_allowed = auth_service.check(user, resource)
    except AuthServiceError:
        pass
    return is_allowed  # True even when auth service is down!

# RIGHT — specific exception, logged, fail-closed
def check_permission(user: User, resource: Resource) -> bool:
    is_allowed = False  # Default deny
    try:
        is_allowed = auth_service.check(user, resource)
    except AuthServiceError:
        logger.error("Auth service unavailable, denying access for user=%s", user.id)
    return is_allowed
```

Why WRONG fails: Silently swallowed errors are invisible. The system appears to work while silently corrupting data or granting unauthorized access.

Why RIGHT works: Specific exception type catches only expected failures. Logging provides forensic trail. Default deny prevents security bypass.

## 9. AI Performance Anti-Patterns

Patterns that AI coding agents produce at elevated rates (1.7x more issues per PR, up to 8x worse on performance — CodeRabbit/Veracode studies). Watch for these in your own output.

```python
# WRONG — string concatenation in loop (O(n^2) memory)
result = ""
for item in items:
    result += f"{item.name}: {item.value}\n"

# RIGHT — join (O(n))
result = "\n".join(f"{item.name}: {item.value}" for item in items)
```

```python
# WRONG — materializing full list when only iterating once (~400MB)
total = sum([x * x for x in range(10_000_000)])

# RIGHT — generator expression (~128 bytes)
total = sum(x * x for x in range(10_000_000))
```

```python
# WRONG — repeated regex compilation
for line in lines:
    if re.match(r"^\d{4}-\d{2}-\d{2}", line):  # Compiled every iteration
        process(line)

# RIGHT — compile once
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}")
for line in lines:
    if DATE_PATTERN.match(line):
        process(line)
```

```python
# WRONG — unbounded collection processing
def process_all(items: list[dict]) -> list[Result]:
    return [expensive_transform(item) for item in items]  # 10M items = OOM

# RIGHT — bounded processing
MAX_BATCH = 10_000

def process_all(items: list[dict]) -> list[Result]:
    if len(items) > MAX_BATCH:
        raise ValueError(f"Batch too large: {len(items)} > {MAX_BATCH}")
    return [expensive_transform(item) for item in items]
```

| Anti-Pattern | Fix | Frequency in AI Code |
|-------------|-----|---------------------|
| String concat in loops | `"".join(parts)` | High |
| Materializing large generators | Generator expression | Medium |
| Repeated regex compilation | `re.compile()` once | Medium |
| Unbounded collection processing | Add `MAX_BATCH` limits | High |
| N+1 queries (loop of DB calls) | Batch/eager load | High |
| O(n^2) list search | Use `dict`/`set` for lookup | Medium |

## 10. Mutable Shared State

Passing mutable objects between functions creates invisible coupling. Any holder can mutate the object, breaking other holders silently.

```python
# WRONG — mutable dataclass, shared reference mutated unexpectedly
@dataclass
class SensorConfig:
    thresholds: list[float]
    enabled: bool = True

config = SensorConfig(thresholds=[0.5, 0.8])
pipeline_a = Pipeline(config)
pipeline_b = Pipeline(config)
config.thresholds.append(0.3)  # Mutates config for BOTH pipelines!

# RIGHT — frozen dataclass, no accidental mutation
@dataclass(frozen=True)
class SensorConfig:
    thresholds: tuple[float, ...]  # Immutable collection
    enabled: bool = True

config = SensorConfig(thresholds=(0.5, 0.8))
# config.thresholds.append(0.3)  — AttributeError! Caught immediately.

# To "modify", create a new instance:
new_config = replace(config, enabled=False)
```

```python
# WRONG — returning mutable internal state
class DetectionHistory:
    def __init__(self):
        self._detections: list[Detection] = []

    def get_all(self) -> list[Detection]:
        return self._detections  # Caller can mutate our internal list!

# RIGHT — return immutable view or copy
class DetectionHistory:
    def __init__(self):
        self._detections: list[Detection] = []

    def get_all(self) -> tuple[Detection, ...]:
        return tuple(self._detections)  # Immutable — caller can't break us
```

Why WRONG fails: Bugs from shared mutable state are the hardest to debug — the mutation happens in one place, the symptom appears in another, and there's no traceback connecting them.

Why RIGHT works: Frozen dataclasses and tuples make mutation impossible. Bugs show up as `AttributeError` at the mutation site, not as mysterious wrong values three function calls later.

## 11. Untyped Public Interfaces

Public functions without type annotations force readers to read the body to understand the contract. They also prevent pyright from catching type mismatches before tests run.

```python
# WRONG — untyped, caller has to guess
def process_readings(readings, threshold, max_count):
    results = []
    for r in readings:
        if r.value > threshold:
            results.append(r)
    return results[:max_count]

# RIGHT — types document the contract, pyright validates callers
def filter_above_threshold(
    readings: Sequence[SensorReading],
    threshold: float,
    max_count: int,
) -> list[SensorReading]:
    return [r for r in readings if r.value > threshold][:max_count]
```

Why WRONG fails: Is `readings` a list? A generator? A numpy array? Is `threshold` an int or float? Does it return `None` on empty input? Every caller must read the body to answer these questions. Refactoring is dangerous because nothing checks that callers pass the right types.

Why RIGHT works: `Sequence[SensorReading]` tells callers exactly what's accepted (any sequence — list, tuple, etc). `float` for threshold is unambiguous. `list[SensorReading]` return type tells callers they get a concrete list. Pyright catches `filter_above_threshold(readings, "high", 5)` before any test runs.

## Summary: Design Smell Detection

| Smell | What It Looks Like | Fix |
|-------|--------------------|-----|
| "And" in description | "Reads config AND processes data AND writes output" | Split into separate functions |
| Hard-coded `import` in function body | `from mylib import heavy_thing` inside function | Inject as parameter |
| Abstract class with one implementation | `AbstractFoo` + `ConcreteFoo` | Delete abstract, use concrete directly |
| Empty override methods | `def preprocess(self): pass` | Abstraction doesn't fit — simplify |
| Public collection attributes | `self.items = []` | Private attribute + methods for access |
| Boolean parameter | `process(data, fast=True)` | Separate functions or strategy pattern |
| Nesting depth >= 3 | Nested if/for/try blocks | Guard clauses + extract method |
| Bare `except` or `except Exception: pass` | Silent error swallowing | Specific exception + logging |
| `result += str` in loop | String concatenation | `"".join()` |
| Comment explaining WHAT | `# Check if user is eligible` | Extract to `is_eligible(user)` |
| Implicit `None` return | Function falls through without `return` | Explicit `return None` |
| Mutable dataclass | `@dataclass` without `frozen=True` | Add `frozen=True`, use `replace()` to "modify" |
| Returning internal mutable state | `return self._items` | Return `tuple(self._items)` or copy |
| Untyped public function | `def process(data, threshold):` | Add parameter + return type annotations |
| Mutable default argument | `def f(items=[]):` | `def f(items: list | None = None):` |
