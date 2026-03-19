# Code Design Patterns

Proven approaches for writing modular, testable, upgradeable code.

## Contents

- [Dependency Injection Patterns](#dependency-injection-patterns) — constructor, parameter, config objects, composition root
- [Complexity Reduction Patterns](#complexity-reduction-patterns) — guard clauses, extract method / SLAP, dictionary dispatch
- [Interface Design Patterns](#interface-design-patterns) — pure functions, adapter, strategy
- [Error Handling Patterns](#error-handling-patterns) — specific exceptions, fail-closed, minimal try scope
- [Concern Decomposition Examples](#concern-decomposition-examples) — feature decomposition, consistent handler patterns
- [Naming Patterns](#naming-patterns) — functions, booleans, variables, avoid type-in-name
- [Immutability Patterns](#immutability-patterns) — frozen dataclasses, immutable collections, defensive copying
- [Type Annotation Patterns](#type-annotation-patterns) — public interfaces, Protocols, when to skip
- [Volatility Analysis](#volatility-analysis) — component volatility table, isolation test
- [Module Boundary Heuristics](#module-boundary-heuristics) — change frequency, dependency direction, data ownership
- [Test Description Template](#test-description-template) — What/How/Why/Considerations example

## Dependency Injection Patterns

### Constructor Injection with Protocols (Python-Idiomatic)

The default choice. Define the interface as a Protocol near the consumer, inject via constructor. No frameworks needed.

```python
from typing import Protocol

class ObjectDetector(Protocol):
    def detect(self, frame: np.ndarray) -> list[Detection]: ...

class DetectionPipeline:
    def __init__(self, detector: ObjectDetector, tracker: Tracker):
        self._detector = detector
        self._tracker = tracker
```

Key insight: "Protocols belong where they are used." Define Protocols near the consumer, not the provider. ABCs belong near the provider when you need shared implementation.

Tests create the object with test doubles — no monkeypatching, no global state.

### Parameter Injection (For Contextual Dependencies)

When a dependency varies per call rather than per instance.

```python
def process_frame(frame: np.ndarray, detector: ObjectDetector) -> list[Detection]:
    return detector.detect(frame)
```

### Configuration Objects (When Parameters Multiply)

When a function needs 4+ configuration values, bundle them into a dataclass.

```python
@dataclass
class PipelineConfig:
    min_confidence: float = 0.5
    max_detections: int = 100
    nms_threshold: float = 0.45

def run_pipeline(frames: list, config: PipelineConfig) -> Results: ...
```

Tests construct configs with explicit values — never relying on defaults.

### Composition Root

Wire all dependencies at the application entry point. Business logic never creates its own collaborators.

```python
def create_app() -> Application:
    db = init_database()
    user_repo = SqlUserRepository(db)
    user_svc = UserService(user_repo)
    return Application(user_svc)
```

When to skip DI: simple dataclasses, pure utility functions, internal caches. Start manual — add a DI container only when you have 15+ services with complex graphs.

## Complexity Reduction Patterns

### Guard Clauses (Highest-Impact Technique)

Check preconditions at the top, exit immediately if not met. Happy path stays at base indentation.

```python
# BEFORE — nested validation (cognitive complexity: 4, nesting depth: 3)
def process_order(order: dict) -> str:
    if order is not None:                          # +1
        if order.get("items"):                     # +2 (nesting=1)
            if order["status"] == "pending":       # +3 (nesting=2)
                total = sum(i["price"] for i in order["items"])
                return f"Order total: {total}"
    return "Invalid order"

# AFTER — guard clauses (cognitive complexity: 3, nesting depth: 0)
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

Impact: nesting depth drops from 3 to 0. Use `continue` for the loop variant.

### Extract Method / SLAP (Single Level of Abstraction Principle)

Move nested logic into well-named helpers. Function calls are FREE in cognitive complexity scoring.

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

# AFTER — one level of abstraction per function
def generate_report(users: list[User], output_path: Path) -> None:
    active = filter_active_users(users)
    rows = format_as_csv(active)
    write_csv(rows, output_path)
```

When to extract: nesting >= 3, a comment explains a block, low-level ops mixed with high-level orchestration, extracted block needs <= 3 parameters.

When NOT to extract: trivial one-liners used once, extracted function would need 5+ parameters (wrong boundary), performance-critical inner loops.

### Dictionary Dispatch (Eliminates Branching)

Replace value-based if/elif chains with dict lookups. Cognitive complexity drops to 0.

```python
# BEFORE — if/elif chain (cognitive complexity: 10+)
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

# AFTER — dict lookup (cognitive complexity: 0)
TAX_RATES: dict[str, float] = {
    "CA": 0.0725, "NY": 0.08, "TX": 0.0625,
}

def get_tax_rate(state: str) -> float:
    return TAX_RATES.get(state, 0.05)
```

Function dispatch variant for command/handler patterns:

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

Don't use for trivial 2-3 branch if/elif — dict adds overhead without clarity gain.

## Interface Design Patterns

### Input-Process-Output (Pure Functions)

The most testable structure. No side effects, no hidden state.

```python
def select_best_candidates(
    detections: list[Detection],
    min_confidence: float,
    max_count: int,
) -> list[Detection]:
    filtered = [d for d in detections if d.confidence >= min_confidence]
    return sorted(filtered, key=lambda d: d.confidence, reverse=True)[:max_count]
```

### Adapter Pattern (Isolate External Dependencies)

Wrap volatile external dependencies behind a stable interface.

```python
class YOLOv8Adapter:
    def __init__(self, model_path: str):
        self._model = YOLO(model_path)

    def detect(self, frame: np.ndarray) -> list[Detection]:
        results = self._model(frame)
        return [Detection.from_yolo(r) for r in results[0].boxes]
```

Swapping YOLO for another detector requires only a new adapter — zero changes to the pipeline.

### Strategy Pattern (Swap Algorithms)

When multiple approaches exist for the same task, parameterize the choice.

```python
class TrackAssociator(Protocol):
    def associate(self, tracks: list[Track], detections: list[Detection]) -> list[Match]: ...

# Pipeline doesn't know or care which algorithm runs
def update_tracks(associator: TrackAssociator, tracks, detections):
    matches = associator.associate(tracks, detections)
```

## Error Handling Patterns

### Specific Exceptions with Chain Preservation

```python
# WRONG
try:
    result = parse_config(raw_data)
except Exception:
    pass

# RIGHT — specific type, chain preserved, context logged
try:
    result = parse_config(raw_data)
except json.JSONDecodeError as e:
    logger.error("Config parse failed: %s", e)
    raise ConfigError(f"Invalid config format: {path}") from e
```

### Fail-Closed / Default Deny

```python
# WRONG — defaults to permitted on failure
def check_access(user: User, resource: Resource) -> bool:
    is_allowed = True  # Dangerous!
    try:
        is_allowed = auth_service.check(user, resource)
    except AuthServiceError:
        pass
    return is_allowed

# RIGHT — defaults to denied on failure
def check_access(user: User, resource: Resource) -> bool:
    is_allowed = False
    try:
        is_allowed = auth_service.check(user, resource)
    except AuthServiceError:
        logger.error("Auth service unavailable, denying access")
    return is_allowed
```

### Minimal Try Scope

Only wrap the line that can raise. Code that doesn't raise belongs outside the try block.

```python
# WRONG — entire function body in try
try:
    data = load_file(path)
    parsed = parse(data)
    result = transform(parsed)
    save(result)
except FileNotFoundError:
    ...

# RIGHT — only the line that raises
try:
    data = load_file(path)
except FileNotFoundError:
    logger.error("Missing: %s", path)
    return None

parsed = parse(data)
result = transform(parsed)
save(result)
```

## Concern Decomposition Examples

### Example: Feature with Mixed Responsibilities

Task: "Build an endpoint that accepts sensor data, validates it, computes rolling averages, and returns a summary."

**Concern decomposition:**
1. **Input parsing** — extract and type-convert raw request data
2. **Validation** — check data against config thresholds, reject invalid
3. **Computation** — rolling average calculation (pure math, no I/O)
4. **Response formatting** — shape the output for the API contract

**Resulting structure:**
```python
# Each concern is a separate function with a clear interface
def parse_sensor_input(raw: dict) -> SensorReading: ...
def validate_reading(reading: SensorReading, config: SensorConfig) -> None: ...  # raises
def compute_rolling_average(readings: list[SensorReading], window: int) -> float: ...
def format_summary(average: float, reading_count: int) -> dict: ...

# Orchestrator composes them — reads like a table of contents
def handle_sensor_data(raw: dict, config: SensorConfig, history: list[SensorReading]) -> dict:
    reading = parse_sensor_input(raw)
    validate_reading(reading, config)
    history.append(reading)
    average = compute_rolling_average(history, config.window_size)
    return format_summary(average, len(history))
```

Each function is independently testable. The orchestrator is trivially testable by mocking each step.

### Example: Consistent Patterns for Similar Operations

When you have multiple similar operations, make them structurally identical:

```python
# WRONG — each handler has a different shape
def handle_create(data):
    validate(data)
    result = db.insert(data)
    return {"id": result.id}

def handle_update(data):
    item = db.get(data["id"])
    if not item:
        raise NotFoundError()
    db.update(data)
    return db.get(data["id"]).to_dict()

def handle_delete(data):
    db.delete(data["id"])

# RIGHT — same structure for all handlers
def handle_create(data: dict, db: Database) -> dict:
    validated = validate_input(data, CREATE_SCHEMA)
    result = db.insert(validated)
    return format_response(result)

def handle_update(data: dict, db: Database) -> dict:
    validated = validate_input(data, UPDATE_SCHEMA)
    result = db.update(validated)
    return format_response(result)

def handle_delete(data: dict, db: Database) -> dict:
    validated = validate_input(data, DELETE_SCHEMA)
    result = db.delete(validated["id"])
    return format_response(result)
```

Each handler: validate → execute → format. A reader who understands one understands all three. Dependencies are injected consistently. Responses are formatted consistently.

## Naming Patterns

### Function Naming (Verb Phrases)

```python
# WRONG — vague, mechanism-focused
def process(data): ...           # Process how?
def handle_stuff(request): ...   # Handle what?
def do_loop(): ...               # Why?
def run(items): ...              # Run what on them?

# RIGHT — intent-revealing
def filter_active_users(users): ...
def validate_sensor_reading(reading): ...
def compute_rolling_average(values, window): ...
def retry_until_connected(client, max_attempts): ...
```

### Boolean Naming (`is_`/`has_`/`can_`)

```python
# WRONG
def check_valid(user): ...       # Returns bool but name suggests action
def expired(token): ...          # Adjective alone is ambiguous

# RIGHT
def is_valid(user) -> bool: ...
def has_permission(user, resource) -> bool: ...
def can_retry(attempt_count, max_retries) -> bool: ...
def is_expired(token) -> bool: ...
```

### Variable Name Length Proportional to Scope

```python
# Loop counter — single letter is fine
for i in range(10): ...
for x, y in coordinates: ...

# Local variable — short but descriptive
users = fetch_active_users()
avg = compute_mean(scores)

# Module-level — fully descriptive
active_user_count = len(fetch_active_users())
max_retry_attempts = config.get("retries", 3)
DEFAULT_CONFIDENCE_THRESHOLD = 0.5
```

### Avoid Type-in-Name

```python
# WRONG
user_list = get_users()
count_int = len(items)
name_str = user.name

# RIGHT
users = get_users()
count = len(items)
name = user.name
```

## Immutability Patterns

### Frozen Dataclasses (Default for Data Objects)

```python
from dataclasses import dataclass, replace

@dataclass(frozen=True)
class Detection:
    bbox: tuple[float, float, float, float]
    confidence: float
    class_id: int

# "Modify" by creating a new instance
updated = replace(detection, confidence=0.95)
```

Use `frozen=True` by default. Opt into mutable `@dataclass` only when:
- The object is a builder/accumulator being constructed incrementally
- The object is a cache that must update in-place for performance
- The object represents genuinely mutable state (counters, running statistics)

### Immutable Collections for Inter-Function Data

```python
# When passing data between functions, prefer immutable types:
def get_thresholds(config: Config) -> tuple[float, ...]:  # Not list
    return tuple(config.raw_thresholds)

def get_allowed_classes(config: Config) -> frozenset[str]:  # Not set
    return frozenset(config.class_names)

# When a function needs to build a collection internally, use list/set,
# then return as immutable:
def compute_scores(items: Sequence[Item]) -> tuple[float, ...]:
    scores = [score(item) for item in items]  # Mutable during construction
    return tuple(scores)                       # Immutable on return
```

### Defensive Copying When Mutability Is Required

```python
class TrackManager:
    def __init__(self, initial_tracks: list[Track]):
        self._tracks = list(initial_tracks)  # Copy on input

    @property
    def tracks(self) -> tuple[Track, ...]:
        return tuple(self._tracks)  # Immutable on output
```

## Type Annotation Patterns

### Public Interface Annotations

```python
# Parameters: abstract types (accept wide input)
# Returns: concrete types (caller knows exactly what they get)
def filter_detections(
    detections: Sequence[Detection],    # Accepts list, tuple, any sequence
    config: Mapping[str, float],        # Accepts dict, MappingProxy, etc.
    min_confidence: float,
) -> list[Detection]:                   # Caller knows it's a list
    return [d for d in detections if d.confidence >= min_confidence]
```

### Protocol for Dependency Injection

```python
from typing import Protocol

class FrameSource(Protocol):
    def next_frame(self) -> np.ndarray | None: ...
    def release(self) -> None: ...

# Any class with these methods satisfies the Protocol — no inheritance needed
class CameraSource:
    def next_frame(self) -> np.ndarray | None: ...
    def release(self) -> None: ...

class VideoFileSource:
    def next_frame(self) -> np.ndarray | None: ...
    def release(self) -> None: ...
```

### When to Skip Annotations

- Trivial internal helpers where types are obvious: `def _add(a, b): return a + b`
- Test functions (pytest doesn't use type info)
- Local lambdas in comprehensions

Always annotate: public functions, class `__init__`, module-level variables, return types.

## Volatility Analysis

When deciding what to abstract vs. leave concrete, assess how likely each component is to change:

| Component | Volatility | Action |
|-----------|-----------|--------|
| External APIs (model inference, cloud services) | High | Wrap in adapter with Protocol interface |
| Configuration format/source | Medium-high | Inject as typed config object |
| Business rules | Medium | Keep as functions, but test thoroughly |
| Core algorithms (math, data structures) | Low | Leave concrete — no interface needed |
| Language standard library | Very low | Use directly — never wrap `os.path` or `json` |

**The test:** "If I swap this component, how many files change?" If the answer is more than 2, it needs better isolation.

## Module Boundary Heuristics

| Heuristic | Keep Together | Separate |
|-----------|--------------|----------|
| **Change frequency** | Things that change at the same time | Things that change independently |
| **Dependency direction** | High-level depends on low-level | Never let low-level depend on high-level |
| **Data ownership** | Code that creates and validates data | Code that creates and displays data |
| **Failure isolation** | Components that recover together | Components where one failure shouldn't crash the other |

**One file = one concept.** If a file contains two unrelated classes, split it. If two files always change together, consider merging.

## Test Description Template

Use this format when logging test descriptions before red-phase (Step 1):

```
TEST: select_best_candidates returns top N above threshold
HOW:  Given 5 detections with mixed confidence, min_confidence=0.5, max_count=3,
      assert returns exactly 2 items (only 2 above threshold), ordered by score
WHY:  Without this, pipeline could pass low-confidence detections to tracking,
      causing false tracks and wasted compute
CONSIDERATIONS:
  - Edge: all below threshold -> empty list
  - Edge: exactly at threshold -> include (>= not >)
  - Edge: fewer above threshold than max_count -> return all qualifying
```
