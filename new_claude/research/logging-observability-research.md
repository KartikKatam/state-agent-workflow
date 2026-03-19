# Logging and Observability: Production Python Systems

**Research Type**: Persistent — best practices and design guidance for AI coding agents
**Confidence**: High (0.88) — triangulated across official docs (Python, structlog, OpenTelemetry, OWASP), production engineering references (Google SRE), and community patterns
**Completed**: 2026-02-28
**Sources**: Python docs, structlog 25.5.0 docs, OWASP Logging Cheat Sheet, OpenTelemetry Python docs, Google SRE Book, BetterStack, Real Python, SigNoz, Dash0

---

## Summary

Logging is a first-class design concern, not an afterthought. AI coding agents consistently make the same mistakes: logging everything at INFO, formatting strings eagerly with f-strings, logging inside tight loops, and placing log calls in the wrong architectural layer. This document provides concrete WRONG/RIGHT pairs and decision rules for each concern area.

**The three most impactful rules:**
1. Use `%s`-style formatting (not f-strings) in log calls — f-strings are evaluated even when the level is disabled
2. Log at architectural boundaries (entry/exit of a service layer or agent tool call), not inside internal functions
3. Never log credentials, tokens, or PII — ever

---

## Section 1: Log Levels — Decision Matrix

### What Each Level Means

| Level | Numeric | When to Use | Production Default? |
|-------|---------|-------------|---------------------|
| DEBUG | 10 | Internal state, variable values during development. Step-by-step diagnostic traces. | No — filter out |
| INFO | 20 | Significant business events: request received, job started, resource created. Confirms expected flow. | Yes — emit |
| WARNING | 30 | Something unexpected but recoverable: deprecated API used, config missing with fallback, retry attempted. | Yes — emit |
| ERROR | 40 | A specific operation failed and could not complete. The application continues. | Yes — emit + alert |
| CRITICAL | 50 | A fatal failure affecting the whole application's ability to function. Requires immediate intervention. | Yes — emit + page |

### Decision Rules

```
Is this information useful only when actively diagnosing a bug?
  → DEBUG

Does this confirm a meaningful business event happened?
  → INFO

Did something unexpected happen, but we handled it?
  → WARNING

Did a specific request/job/operation fail completely?
  → ERROR

Is the application unable to continue operating?
  → CRITICAL
```

### What AI Agents Get Wrong

**Anti-pattern: Over-logging at INFO**

Every function call logged at INFO floods production logs, hiding real events and driving up storage cost. AI agents tend to narrate their own execution.

```python
# WRONG — every internal step at INFO
def process_frame(frame):
    logger.info("process_frame called")           # noise
    logger.info("Converting frame to grayscale")  # noise
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    logger.info("Grayscale conversion complete")  # noise
    result = detect_edges(gray)
    logger.info("process_frame returning")        # noise
    return result

# RIGHT — only the boundary event matters
def process_frame(frame):
    logger.debug("Processing frame shape=%s", frame.shape)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    result = detect_edges(gray)
    return result
```

**Anti-pattern: Under-logging errors (swallowing exceptions silently)**

```python
# WRONG — exception disappears
try:
    result = call_external_api(payload)
except Exception:
    pass  # silent failure — nobody knows this happened

# RIGHT — log the exception with context
try:
    result = call_external_api(payload)
except requests.RequestException as exc:
    logger.error(
        "External API call failed",
        exc_info=True,
        extra={"endpoint": endpoint, "payload_size": len(payload)},
    )
    raise
```

**Anti-pattern: Wrong level for retries**

```python
# WRONG — ERROR on first retry is too alarming
for attempt in range(3):
    try:
        return call_service()
    except TransientError:
        logger.error("Service call failed, attempt %d", attempt)  # wrong

# RIGHT — WARNING for expected retries, ERROR only on final failure
for attempt in range(3):
    try:
        return call_service()
    except TransientError as exc:
        if attempt < 2:
            logger.warning("Service call failed, retrying", extra={"attempt": attempt + 1})
        else:
            logger.error("Service call failed after all retries", exc_info=True)
            raise
```

### Production Log Level Defaults

```python
import logging

# Development
logging.basicConfig(level=logging.DEBUG)

# Production — suppress DEBUG noise
logging.basicConfig(level=logging.INFO)

# Third-party library noise control (common source of log spam)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("boto3").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
```

---

## Section 2: Structured Logging with structlog

### Why Structured Logging

Plain text logs are human-readable but machine-opaque. JSON structured logs are both — they render as readable text in development and are parseable by log aggregation systems (ELK, Datadog, CloudWatch) in production.

**Key fields every log entry should have:**
- `timestamp` — ISO 8601 UTC (e.g., `2026-02-28T14:30:00.123456Z`)
- `level` — log level name (info, error, etc.)
- `service` — service or application name
- `logger` — logger name (module path)
- `event` — the human-readable message
- `correlation_id` — request/job ID to trace across services
- `environment` — `production`, `staging`, `development`

### structlog Installation and Basic Configuration

```bash
pip install structlog
```

```python
# logging_config.py — configure once at application entry point
import logging
import sys
import structlog

def configure_logging(environment: str = "production") -> None:
    """Configure structlog for the application. Call once at startup."""

    shared_processors = [
        structlog.contextvars.merge_contextvars,       # pulls correlation_id etc. from context
        structlog.stdlib.add_logger_name,              # adds 'logger' field
        structlog.stdlib.add_log_level,                # adds 'level' field
        structlog.processors.TimeStamper(fmt="iso"),   # adds ISO 8601 timestamp
        structlog.processors.StackInfoRenderer(),
    ]

    if environment == "production":
        # JSON output for log aggregation
        structlog.configure(
            processors=shared_processors + [
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        # Human-readable colored output for development
        structlog.configure(
            processors=shared_processors + [
                structlog.dev.ConsoleRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
```

### Getting a Logger

```python
# In any module — get a bound logger
import structlog

log = structlog.get_logger()

# Log with structured context
log.info("agent_tool_called", tool="web_search", query="Python logging")
log.warning("retry_attempt", attempt=2, max_attempts=3, tool="web_search")
log.error("tool_failed", tool="web_search", exc_info=True)
```

**Example JSON output in production:**
```json
{
  "timestamp": "2026-02-28T14:30:00.123456Z",
  "level": "info",
  "logger": "agents.researcher",
  "event": "agent_tool_called",
  "tool": "web_search",
  "query": "Python logging",
  "correlation_id": "req_7f3a9b2c"
}
```

### Correlation IDs with contextvars

The `structlog.contextvars` module provides async-safe, thread-safe context propagation. Bind once per request; every subsequent log from any code in that request automatically includes the correlation ID.

```python
# middleware.py (FastAPI example)
import uuid
import structlog
from structlog.contextvars import clear_contextvars, bind_contextvars
from fastapi import Request

async def logging_middleware(request: Request, call_next):
    """Bind correlation ID for the lifetime of a request."""
    clear_contextvars()  # reset from any previous request in this context
    correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    bind_contextvars(
        correlation_id=correlation_id,
        method=request.method,
        path=request.url.path,
    )
    response = await call_next(request)
    return response
```

```python
# agent_executor.py — bind job-level context
from structlog.contextvars import clear_contextvars, bind_contextvars

async def run_agent_job(job_id: str, task: str) -> dict:
    clear_contextvars()
    bind_contextvars(job_id=job_id, task_type=task)

    log = structlog.get_logger()
    log.info("agent_job_started")          # automatically includes job_id, task_type
    result = await execute_task(task)
    log.info("agent_job_completed", result_size=len(result))
    return result
```

```python
# Use bound_contextvars as a context manager for temporary scoping
from structlog.contextvars import bound_contextvars

with bound_contextvars(step="tool_call", tool="context7"):
    log.info("tool_invoked")
    result = call_tool()
    log.info("tool_returned", result_length=len(result))
# correlation_id persists; step and tool are removed after the with block
```

### structlog Processor Chain — Explained

The processor chain transforms each log event in sequence. Order matters:

```
Event dict created
       ↓
merge_contextvars    ← inject correlation_id from async context
       ↓
add_logger_name      ← inject 'logger' field
       ↓
add_log_level        ← inject 'level' field
       ↓
TimeStamper          ← inject 'timestamp' in ISO format
       ↓
StackInfoRenderer    ← render stack info if present
       ↓
format_exc_info      ← format exception tracebacks (production)
       ↓
JSONRenderer         ← serialize dict to JSON string
       ↓
Output (stdout/file)
```

---

## Section 3: What NOT to Log

### OWASP Categories of Data to Never Log

**Credentials and secrets (NEVER):**
- Passwords (plaintext or hashed)
- API keys and tokens (OAuth, JWT, API keys)
- Session IDs or cookies
- Private cryptographic keys
- Database connection strings with passwords

**PII — personally identifiable information (NEVER without explicit compliance review):**
- Full names + email addresses in combination
- Social Security Numbers, passport numbers, government IDs
- Health information (PHI under HIPAA)
- Credit card / payment card data (PCI-DSS)
- Home addresses, precise geolocation
- Biometric data

**High-cardinality data in hot paths (AVOID for performance):**
- Full request/response bodies in high-traffic endpoints
- Binary data, base64-encoded payloads
- Large arrays or tensors (e.g., ML model weights, image pixel arrays)
- Per-frame data in video processing loops

### Sanitization Patterns

```python
# WRONG — logs the full Authorization header including the token
logger.info("Request received", headers=dict(request.headers))

# RIGHT — explicitly exclude sensitive headers
SAFE_HEADERS = {"content-type", "accept", "x-correlation-id", "user-agent"}
safe_headers = {k: v for k, v in request.headers.items() if k.lower() in SAFE_HEADERS}
log.info("request_received", headers=safe_headers)
```

```python
# WRONG — logs the password field
def create_user(username: str, password: str) -> User:
    logger.info("Creating user: %s with password: %s", username, password)  # CRITICAL BUG

# RIGHT — log only what's needed; never the secret
def create_user(username: str, password: str) -> User:
    log.info("user_creation_started", username=username)
    user = _create_in_db(username, password)
    log.info("user_creation_complete", username=username, user_id=user.id)
    return user
```

```python
# WRONG — full config dict may contain DATABASE_URL with credentials
logger.debug("App config: %s", config.__dict__)

# RIGHT — redact known sensitive keys
SENSITIVE_KEYS = {"password", "secret", "token", "key", "api_key", "database_url"}

def safe_config_repr(config: dict) -> dict:
    return {
        k: "***REDACTED***" if any(s in k.lower() for s in SENSITIVE_KEYS) else v
        for k, v in config.items()
    }

log.debug("app_config_loaded", config=safe_config_repr(config.__dict__))
```

### Performance: Never Log in Hot Paths

```python
# WRONG — logging inside every iteration of a processing loop
def process_video_stream(frames):
    for frame in frames:
        logger.debug("Processing frame %d", frame.index)   # 30x/sec = 2.6M logs/day
        result = analyze_frame(frame)
        logger.debug("Frame %d result: %s", frame.index, result)

# RIGHT — log at batch boundaries
def process_video_stream(frames):
    log.info("stream_processing_started", frame_count=len(frames))
    results = []
    for i, frame in enumerate(frames):
        results.append(analyze_frame(frame))
        if i % 1000 == 0:
            log.debug("stream_progress", processed=i, total=len(frames))
    log.info("stream_processing_complete", processed=len(results))
    return results
```

**Rule of thumb**: If a code path executes more than 100 times per second, logging in it requires explicit justification. Use metrics (counters/gauges) instead of logs for high-frequency data.

---

## Section 4: Performance Tracing

### Logging vs. Tracing vs. Metrics — When to Use Each

| Signal | Tool | Use For |
|--------|------|---------|
| **Logs** | structlog, logging | Discrete events, errors, state changes. Searchable. |
| **Traces** | OpenTelemetry | End-to-end request flow across services. Latency breakdown. |
| **Metrics** | Prometheus, OTel metrics | Aggregate counts, rates, histograms. Alert thresholds. |

**Decision rule:**
- "What happened?" → Logs
- "How long did each part take?" → Traces
- "How often is this happening?" → Metrics

Google SRE recommends the **Four Golden Signals** as the minimum metric set: latency, traffic, errors, saturation. Logs supplement these by providing the "why" behind anomalies in the golden signals.

### OpenTelemetry Python — Basic Setup

```bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp
```

```python
# tracing_config.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

def configure_tracing(service_name: str, otlp_endpoint: str) -> None:
    provider = TracerProvider()
    processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
```

### Timing Decorator Pattern

```python
# tracing.py
import functools
import time
from typing import Callable, TypeVar, ParamSpec
from opentelemetry import trace

P = ParamSpec("P")
R = TypeVar("R")

tracer = trace.get_tracer(__name__)


def traced(span_name: str | None = None) -> Callable:
    """Decorator to wrap a function in an OpenTelemetry span.

    Skips span creation if tracing is disabled (NonRecordingSpan),
    avoiding overhead in environments without a tracer configured.
    """
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        name = span_name or func.__qualname__

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with tracer.start_as_current_span(name) as span:
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as exc:
                    span.set_status(trace.StatusCode.ERROR, str(exc))
                    span.record_exception(exc)
                    raise

        return wrapper
    return decorator


# Manual timing without OTel (for quick profiling without full tracing setup)
def timed(log_threshold_ms: float = 100.0) -> Callable:
    """Log execution time only when it exceeds threshold — avoids log noise."""
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        import structlog
        _log = structlog.get_logger()

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start = time.perf_counter()
            result = func(*args, **kwargs)
            elapsed_ms = (time.perf_counter() - start) * 1000
            if elapsed_ms > log_threshold_ms:
                _log.warning(
                    "slow_operation",
                    function=func.__qualname__,
                    elapsed_ms=round(elapsed_ms, 2),
                    threshold_ms=log_threshold_ms,
                )
            return result

        return wrapper
    return decorator
```

```python
# Usage
@traced()
async def call_llm_api(prompt: str, model: str) -> str:
    """Traced LLM call — span appears in distributed trace."""
    return await anthropic_client.messages.create(...)


@timed(log_threshold_ms=500.0)
def load_vector_index(index_path: str):
    """Only logs if it takes more than 500ms — silence is the happy path."""
    return faiss.read_index(index_path)
```

### When to Trace vs. When to Skip

**Add a span when:**
- Crossing a service boundary (HTTP call, gRPC, queue publish)
- Calling an external API (LLM, database, cache, vector store)
- A user-visible operation with latency SLO (e.g., agent task execution)
- A background job with measurable duration (batch processing, indexing)

**Skip tracing when:**
- Internal helper functions called millions of times (sorting, string manipulation)
- The function is a trivial wrapper over an already-traced call
- You do not have a tracer configured (OTel's `NonRecordingSpan` is zero-cost but still adds import overhead)

---

## Section 5: Logging as an Architectural Design Decision

### Where Logging Calls Belong

**Log at architectural boundaries, not deep internals.**

```
[HTTP Handler] ← log request received/completed here
      ↓
[Service Layer] ← log business events here (order created, job queued)
      ↓
[Repository/Adapter] ← log I/O boundaries here (DB query executed, cache miss)
      ↓
[Domain Model] ← NO logging here (pure business logic)
```

**Why this matters:**
- Deep internal logging creates tight coupling between implementation and observability
- Domain models should be pure — no I/O, no logging dependencies
- Service layer logs capture intent ("payment processed"), not mechanism ("SQL INSERT executed")
- Adapters/repositories log the I/O facts ("DB query took 45ms")

```python
# WRONG — business logic buried with logging in a domain object
class Order:
    def apply_discount(self, pct: float) -> None:
        logger.info("Applying discount of %f%%", pct)  # domain models shouldn't log
        self.total *= (1 - pct / 100)
        logger.info("New total: %f", self.total)

# RIGHT — domain model is pure; service layer logs the event
class Order:
    def apply_discount(self, pct: float) -> None:
        self.total *= (1 - pct / 100)

class OrderService:
    def apply_discount(self, order_id: str, pct: float) -> None:
        order = self.repo.get(order_id)
        order.apply_discount(pct)
        self.repo.save(order)
        log.info("discount_applied", order_id=order_id, discount_pct=pct, new_total=order.total)
```

### Dependency Injection for Loggers

For testability and configurability, pass loggers as dependencies rather than calling `logging.getLogger()` inside every class.

```python
# WRONG — hard-coded logger, hard to replace in tests
class AgentExecutor:
    def __init__(self):
        self.logger = logging.getLogger(__name__)  # baked in

# RIGHT — inject the logger (or use structlog's bound logger pattern)
import structlog
from typing import Protocol

class Logger(Protocol):
    def info(self, event: str, **kw: object) -> None: ...
    def warning(self, event: str, **kw: object) -> None: ...
    def error(self, event: str, **kw: object) -> None: ...

class AgentExecutor:
    def __init__(self, logger: Logger | None = None) -> None:
        self._log = logger or structlog.get_logger(__name__)

    def execute(self, task: str) -> dict:
        self._log.info("task_started", task=task)
        result = self._run(task)
        self._log.info("task_completed", task=task)
        return result
```

With structlog, the preferred pattern is to call `structlog.get_logger()` at module level — structlog's global configuration makes this injectable-equivalent without manual DI, since the processor chain and output can be swapped in tests.

### Testing Logged Output

**With standard pytest caplog:**

```python
import logging
import pytest

def test_service_logs_event(caplog):
    service = MyService()
    with caplog.at_level(logging.INFO, logger="myapp.service"):
        service.process_order("order-123")

    assert any(
        r.levelname == "INFO" and "order_processed" in r.message
        for r in caplog.records
    )
```

**With structlog's testing utilities (preferred for structlog codebases):**

```python
import structlog
from structlog.testing import LogCapture
import pytest

@pytest.fixture
def log_output():
    return LogCapture()

@pytest.fixture(autouse=True)
def configure_structlog(log_output):
    structlog.configure(processors=[log_output])
    yield
    structlog.reset_defaults()

def test_agent_logs_correlation_id(log_output):
    executor = AgentExecutor()
    executor.execute_with_context(job_id="job-001", task="search")

    events = log_output.entries
    assert any(
        e["event"] == "task_started" and e.get("job_id") == "job-001"
        for e in events
    )
```

**With pytest-structlog plugin (simplest API):**

```python
def test_tool_logs_failure(log):
    with pytest.raises(ToolError):
        call_failing_tool()

    assert log.has("tool_failed", level="error")
    # Or check specific fields:
    assert {"event": "tool_failed", "tool": "web_search"} in log.events
```

**What to test vs. what to skip:**
- Test: error conditions log at ERROR with relevant context fields
- Test: success conditions log at INFO with key identifiers
- Skip: exact message wording (brittle, breaks on rewording)
- Skip: DEBUG messages (implementation detail — they change frequently)

---

## Section 6: AI Agent Logging Anti-Patterns

This section addresses the specific patterns AI coding agents produce when writing logging code.

### Anti-pattern 1: F-String Formatting in Log Calls

The most common AI agent mistake. F-strings are **always evaluated**, even when the log level means the message will never be emitted.

```python
# WRONG — f-string evaluated even if DEBUG is disabled in production
user_data = get_expensive_user_object()
logger.debug(f"Processing user: {user_data}")     # user_data.__str__() called regardless
logger.info(f"Query returned {len(results)} rows") # fine for INFO, but still poor practice
logger.error(f"Failed for user {user.id}: {exc}")  # should use exc_info=True instead

# RIGHT — percent-style defers evaluation; structlog uses keyword args
logger.debug("Processing user: %s", user_data)    # user_data.__str__() only if DEBUG enabled
logger.info("Query returned %d rows", len(results))
logger.error("Failed for user %s", user.id, exc_info=True)

# RIGHT with structlog — keyword args, no string formatting at all
log.debug("processing_user", user_id=user.id)
log.info("query_complete", row_count=len(results))
log.error("operation_failed", user_id=user.id, exc_info=True)
```

**Why this matters for agents:** AI agents often generate logging code for complex objects (tensors, model outputs, agent state dicts). These objects may have expensive `__repr__` or `__str__` methods. Eager f-string evaluation means every DEBUG log call is paying the full serialization cost on every production request.

Ruff rule `G004` and Pylint rule `W1203` both flag this pattern:
```toml
# pyproject.toml
[tool.ruff.lint]
select = ["G"]  # enable flake8-logging-format rules including G004
```

### Anti-pattern 2: Logging Every Function Entry and Exit

AI agents frequently produce this pattern when asked to "add logging" — they wrap every function with enter/exit logs.

```python
# WRONG — entry/exit logging on every function
def calculate_embedding(text: str) -> list[float]:
    logger.info("Entering calculate_embedding")     # noise
    logger.debug("Input text: %s", text)            # sensitive data risk
    result = model.encode(text)
    logger.info("Exiting calculate_embedding")      # noise
    return result

def chunk_document(doc: str, size: int) -> list[str]:
    logger.info("Entering chunk_document")
    chunks = [doc[i:i+size] for i in range(0, len(doc), size)]
    logger.info("Exiting chunk_document with %d chunks", len(chunks))
    return chunks

# RIGHT — log meaningful events at boundaries, use tracing for timing
@traced()  # OTel span captures duration automatically
def calculate_embedding(text: str) -> list[float]:
    return model.encode(text)

def chunk_document(doc: str, size: int) -> list[str]:
    chunks = [doc[i:i+size] for i in range(0, len(doc), size)]
    log.debug("document_chunked", doc_length=len(doc), chunk_size=size, chunk_count=len(chunks))
    return chunks
```

**When entry/exit logging IS appropriate:**
- Agent tool calls (crossing the agent/tool boundary)
- Long-running async jobs at their start and end
- Service layer methods that represent user-visible actions
- Retry handlers

**When to use tracing instead:**
- You need latency data, not just confirmation that a call happened
- The function is called many times per request (use sampling)

### Anti-pattern 3: Logging Mutable Objects

```python
# WRONG — logs the dict reference; if dict is mutated before emission, log is wrong
state = {"step": "planning", "candidates": []}
logger.info("Agent state: %s", state)   # state may have changed by emission time
state["candidates"].append("option_a")   # now state has changed

# Also wrong with structlog binding
log = structlog.get_logger().bind(state=state)  # binds reference, not snapshot
state["step"] = "executing"  # bound state dict is now mutated

# RIGHT — log a snapshot (copy) or log specific fields
log.info("agent_state_snapshot", step=state["step"], candidate_count=len(state["candidates"]))

# RIGHT — if you must log the dict, copy it
import copy
logger.info("Agent state: %s", copy.deepcopy(state))

# RIGHT with structlog — log primitive values, not the container
log.info("planning_started", candidate_count=0)
state["candidates"].append("option_a")
log.info("candidate_added", candidate="option_a", total_candidates=1)
```

**Why this matters:** Async Python code is especially prone to this. A coroutine may log a dict, yield control, allow another coroutine to mutate the dict, then the first coroutine's log is finally emitted with the mutated state.

### Anti-pattern 4: Using print() Instead of Logging

```python
# WRONG — unstructured, no level, no context, uncontrollable
def run_agent_step(step):
    print(f"Running step: {step}")
    result = execute(step)
    print(f"Step complete, result: {result}")

# RIGHT — structured, controllable, filterable
def run_agent_step(step: str) -> dict:
    log.info("agent_step_started", step=step)
    result = execute(step)
    log.info("agent_step_complete", step=step, result_keys=list(result.keys()))
    return result
```

### Anti-pattern 5: Concatenating Strings in Log Messages

```python
# WRONG — string concatenation always evaluated
logger.debug("Processing " + str(len(items)) + " items in batch " + batch_id)

# WRONG — equivalent problem with joining
logger.debug("Keys: " + ", ".join(keys))  # join() evaluated even if DEBUG disabled

# RIGHT — use %s or structlog keyword args
logger.debug("Processing %d items in batch %s", len(items), batch_id)
log.debug("processing_batch", item_count=len(items), batch_id=batch_id)
```

### Anti-pattern 6: Not Using exc_info

```python
# WRONG — exception logged as a string, losing the stack trace
try:
    result = risky_call()
except Exception as exc:
    logger.error("Operation failed: %s", str(exc))  # stack trace lost

# RIGHT — exc_info=True captures and formats the full traceback
try:
    result = risky_call()
except Exception:
    logger.error("Operation failed", exc_info=True)

# RIGHT with structlog
try:
    result = risky_call()
except Exception:
    log.error("operation_failed", exc_info=True)
```

### Anti-pattern 7: Configuring Logging in Library Code

```python
# WRONG — library code that configures the root logger
# (this breaks the calling application's logging setup)
import logging

logging.basicConfig(level=logging.DEBUG)  # NEVER in a library
logger = logging.getLogger(__name__)

def my_library_function():
    logger.info("Called")

# RIGHT — library code only creates a logger, never configures it
import logging

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())  # standard practice for libraries

def my_library_function():
    logger.info("Called")
```

---

## Section 7: Recommended Configuration Reference

### Minimal Production structlog Configuration

```python
# config/logging.py
import logging
import sys
import structlog
import os

def setup_logging() -> None:
    """Call once at application startup, before any other imports use logging."""
    env = os.getenv("ENVIRONMENT", "development")
    level = logging.INFO if env == "production" else logging.DEBUG

    # Configure stdlib logging to play nicely with structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    # Silence noisy libraries
    for lib in ["urllib3", "boto3", "botocore", "httpx", "httpcore"]:
        logging.getLogger(lib).setLevel(logging.WARNING)

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if env == "production":
        structlog.configure(
            processors=shared_processors + [
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        structlog.configure(
            processors=shared_processors + [
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
```

### Quick-Start Checklist for AI Agents Writing Logging Code

```
[ ] Use structlog.get_logger() not logging.getLogger()
[ ] Never use f-strings in log calls — use keyword args
[ ] Never log passwords, tokens, API keys, or PII
[ ] Log at service/adapter boundaries, not inside domain logic
[ ] Use exc_info=True when catching exceptions, not str(exc)
[ ] Use bind_contextvars() for correlation IDs in async code
[ ] Set third-party library loggers to WARNING in production
[ ] Use @traced() decorator for cross-service calls, not manual timing
[ ] Test error-level logging paths with structlog.testing.LogCapture
[ ] Add NullHandler to library loggers, never basicConfig in libraries
```

---

## Sources

- [Python Logging HOWTO — Python 3 Official Docs](https://docs.python.org/3/howto/logging.html)
- [Python logging module reference](https://docs.python.org/3/library/logging.html)
- [structlog 25.5.0 — Logging Best Practices](https://www.structlog.org/en/stable/logging-best-practices.html)
- [structlog 25.5.0 — Context Variables](https://www.structlog.org/en/stable/contextvars.html)
- [structlog 25.5.0 — Testing](https://www.structlog.org/en/stable/testing.html)
- [structlog 25.5.0 — Standard Library Integration](https://www.structlog.org/en/stable/standard-library.html)
- [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
- [OWASP Top 10:2025 — A09 Security Logging and Alerting Failures](https://owasp.org/Top10/2025/A09_2025-Security_Logging_and_Alerting_Failures/)
- [OpenTelemetry Python Instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/)
- [Using Decorators to Instrument Python Code With OpenTelemetry — Better Programming](https://betterprogramming.pub/using-decorators-to-instrument-python-code-with-opentelemetry-traces-d7f1c7d6f632)
- [Google SRE Book — Monitoring Distributed Systems](https://sre.google/sre-book/monitoring-distributed-systems/)
- [Logging vs. Tracing vs. Metrics — BetterStack](https://betterstack.com/community/guides/observability/logging-metrics-tracing/)
- [10 Best Practices for Logging in Python — BetterStack](https://betterstack.com/community/guides/logging/python/python-logging-best-practices/)
- [A Comprehensive Guide to Python Logging with Structlog — BetterStack](https://betterstack.com/community/guides/logging/structlog/)
- [Leveling Up Your Python Logs with Structlog — Dash0](https://www.dash0.com/guides/python-logging-with-structlog)
- [Python Logging Best Practices — SigNoz](https://signoz.io/guides/python-logging-best-practices/)
- [Ruff Rule G004 — logging-f-string](https://docs.astral.sh/ruff/rules/logging-f-string/)
- [Pylint W1203 — logging-fstring-interpolation](https://pylint.readthedocs.io/en/stable/user_guide/messages/warning/logging-fstring-interpolation.html)
- [Characterizing and Detecting Anti-Patterns in the Logging Code — IEEE](https://ieeexplore.ieee.org/document/7985651/)
- [pytest-structlog — PyPI](https://pypi.org/project/pytest-structlog/)
- [How to manage logging — pytest docs](https://docs.pytest.org/en/stable/how-to/logging.html)
- [Guide to Structured Logging in Python — New Relic](https://newrelic.com/blog/log/python-structured-logging)
- [Setting Up Structured Logging in FastAPI with structlog — Ouassim G.](https://ouassim.tech/notes/setting-up-structured-logging-in-fastapi-with-structlog/)
- [Logs vs Traces — Honeycomb](https://www.honeycomb.io/blog/understanding-logs-vs-traces)
- [Scrubbing Sensitive Data — Sentry for Python](https://docs.sentry.io/platforms/python/guides/logging/data-management/sensitive-data/)
