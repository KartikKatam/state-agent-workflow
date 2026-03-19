# Logging as a Design Concern

Logging is a first-class design decision, not an afterthought. AI agents consistently over-log (every function entry/exit), under-log (swallowing exceptions silently), and use f-strings that evaluate even when the level is disabled.

## Contents

- [Log Level Decision Matrix](#log-level-decision-matrix) — which level for which situation
- [AI Agent Logging Anti-Patterns](#ai-agent-logging-anti-patterns) — WRONG/RIGHT pairs for common mistakes
- [Structured Logging with structlog](#structured-logging-with-structlog) — production JSON config
- [What NOT to Log](#what-not-to-log) — security, PII, performance
- [Where to Place Log Calls](#where-to-place-log-calls) — architectural boundary rules
- [Testing Log Output](#testing-log-output) — caplog and structlog testing

## Log Level Decision Matrix

| Level | When to Use | Example |
|-------|-------------|---------|
| **DEBUG** | Internal state during development. Step-by-step diagnostics. | `logger.debug("Frame shape=%s, dtype=%s", frame.shape, frame.dtype)` |
| **INFO** | Significant business events that confirm expected flow. | `logger.info("Detection pipeline started", extra={"model": model_name})` |
| **WARNING** | Unexpected but recovered. Deprecated API, retry attempted, config fallback. | `logger.warning("Model timeout, retrying", extra={"attempt": 2})` |
| **ERROR** | A specific operation failed and could not complete. App continues. | `logger.error("API call failed after all retries", exc_info=True)` |
| **CRITICAL** | Fatal failure affecting the whole application. Requires immediate action. | `logger.critical("Database connection pool exhausted")` |

**Quick decision flow:**
- Useful only when actively diagnosing a bug? → **DEBUG**
- Confirms a meaningful business event happened? → **INFO**
- Something unexpected happened but we handled it? → **WARNING**
- A specific request/job/operation failed completely? → **ERROR**
- The application can no longer function? → **CRITICAL**

## AI Agent Logging Anti-Patterns

### 1. F-string in log calls (Ruff G004)

```python
# WRONG — f-string evaluated even when level is disabled
logger.debug(f"Processing frame {frame.shape}")  # Formats even if DEBUG is off

# RIGHT — lazy formatting, only evaluated when level is active
logger.debug("Processing frame %s", frame.shape)
```

### 2. Entry/exit on every function

```python
# WRONG — narrating execution at INFO floods logs
def process_frame(frame):
    logger.info("process_frame called")
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    logger.info("Grayscale conversion complete")
    result = detect_edges(gray)
    logger.info("process_frame returning")
    return result

# RIGHT — only the boundary event, at DEBUG
def process_frame(frame):
    logger.debug("Processing frame shape=%s", frame.shape)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return detect_edges(gray)
```

### 3. Swallowing exceptions silently

```python
# WRONG — exception disappears
try:
    result = call_external_api(payload)
except Exception:
    pass

# RIGHT — log with context, re-raise or handle explicitly
try:
    result = call_external_api(payload)
except requests.RequestException:
    logger.error("API call failed", exc_info=True, extra={"endpoint": url})
    raise
```

### 4. Wrong level for retries

```python
# WRONG — ERROR on first retry creates alert fatigue
for attempt in range(3):
    try:
        return call_service()
    except TransientError:
        logger.error("Service call failed, attempt %d", attempt)

# RIGHT — WARNING for expected retries, ERROR only on final failure
for attempt in range(3):
    try:
        return call_service()
    except TransientError:
        if attempt < 2:
            logger.warning("Retry attempt %d", attempt + 1)
        else:
            logger.error("All retries exhausted", exc_info=True)
            raise
```

### 5. Using print() instead of logging

```python
# WRONG — no level control, no structured output, no routing
print(f"User {user_id} logged in")

# RIGHT
logger.info("User login", extra={"user_id": user_id})
```

### 6. Missing exc_info=True on error logs

```python
# WRONG — logs the message but loses the traceback
except ValueError as e:
    logger.error("Validation failed: %s", e)

# RIGHT — includes full traceback in the log entry
except ValueError as e:
    logger.error("Validation failed: %s", e, exc_info=True)
```

## Structured Logging with structlog

### Production Configuration

```python
import logging
import structlog

def configure_logging(environment: str = "production") -> None:
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if environment == "production":
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
            processors=shared_processors + [structlog.dev.ConsoleRenderer()],
            wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
```

### Correlation IDs for Multi-Agent/Distributed Systems

```python
import structlog
from uuid import uuid4

# At request/job entry point:
structlog.contextvars.clear_contextvars()
structlog.contextvars.bind_contextvars(
    correlation_id=str(uuid4()),
    service="detection-pipeline",
)

# All subsequent logs in this context automatically include correlation_id
logger = structlog.get_logger()
logger.info("Processing started")
# Output: {"correlation_id": "abc-123", "service": "detection-pipeline", "event": "Processing started", ...}
```

## What NOT to Log

**Never log (security):**
- Passwords, API keys, tokens, secrets
- PII (names, emails, SSNs, phone numbers)
- Session IDs, auth tokens
- Credit card numbers, bank accounts
- Full request/response bodies that may contain any of the above

**Avoid logging (performance):**
- Inside tight loops (per-frame video processing, per-item batch processing)
- Large objects (`str(dataframe)`, full model weights)
- High-cardinality fields in hot paths (per-pixel values)

```python
# WRONG — logs PII and credential
logger.info("User %s authenticated with token %s", user.email, auth_token)

# RIGHT — log user ID only, never credential
logger.info("User authenticated", extra={"user_id": user.id})
```

## Where to Place Log Calls

Log at **architectural boundaries**, not inside internal functions.

```
HTTP Handler / API endpoint  ← log request received, response sent
  ↓
Service layer                ← log business events, external call results
  ↓
Repository / data access     ← log connection issues only (ERROR)
  ↓
Domain model / pure logic    ← NO LOGGING (pure functions have no side effects)
```

**Rules:**
- Service entry/exit: INFO
- External dependency calls (API, DB, model): DEBUG (success), WARNING (retry), ERROR (final failure)
- Domain logic: never log — pass results up, let the caller decide
- Errors: log at the level that handles them, not at every level they propagate through

## Testing Log Output

### With pytest caplog

```python
def test_logs_warning_on_retry(caplog):
    with caplog.at_level(logging.WARNING):
        call_with_retry()
    assert "retrying" in caplog.text
```

### With structlog testing

```python
import structlog.testing

@pytest.fixture
def log_output():
    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[cap])
    return cap

def test_logs_user_login(log_output):
    login(user_id="123")
    assert log_output.entries[0]["event"] == "User login"
    assert log_output.entries[0]["user_id"] == "123"
```

**Sources:** Python logging docs, structlog 25.5.0 docs, OWASP Logging Cheat Sheet, Google SRE Book, Ruff G004
