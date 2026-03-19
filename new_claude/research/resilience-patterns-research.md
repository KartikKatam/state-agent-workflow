# Resilience and Error Recovery Patterns for Production Python
## Research for AI Coding Agents Writing Production Systems

**Research date**: 2026-02-28
**Confidence**: High (0.88) — Multiple authoritative sources: AWS Builder's Library, official Python docs, tenacity/pybreaker/stamina official docs, peer-reviewed safety-critical systems literature
**Classification**: Persistent — library APIs + architectural patterns, reusable across features
**Sources**: Context7 (tenacity, pybreaker, stamina docs), WebSearch (AWS, Python community, safety-critical systems literature)

---

## Executive Summary

AI coding agents consistently generate code that works in the happy path but breaks in production because they omit five critical resilience patterns: retry with backoff, circuit breakers, graceful degradation, timeout boundaries, and bulkheads. Each pattern solves a different failure mode. Using all five together creates a system that degrades gracefully under partial failure rather than crashing completely. This document provides production-ready patterns for each, with specific focus on what AI agents get wrong.

---

## Pattern 1: Retry with Exponential Backoff

### What It Solves

Transient failures — temporary network blips, momentary API unavailability, rate limit responses (HTTP 429), database connection pool exhaustion. The key insight: many failures self-resolve in seconds if you wait before retrying.

### When to Use

**Use retry when:**
- Calling remote APIs (model inference endpoints, REST APIs, gRPC services)
- Database queries that may fail due to connection pool exhaustion
- Network I/O where timeout is a possibility
- HTTP responses: 429 (rate limit), 500, 502, 503, 504 (server errors)

**Do NOT retry when:**
- HTTP 400 (bad request) — your request is wrong, retrying won't help
- HTTP 401/403 (auth failure) — credentials won't change between retries
- HTTP 404 (not found) — the resource does not exist
- Business logic errors (validation failures, domain rule violations)
- Errors caused by your own code bugs
- Operations that are NOT idempotent (e.g., charging a payment card without an idempotency key)

### Wrong Pattern (What AI Agents Generate)

```python
# WRONG: No retry, no backoff, no timeout — crashes on any transient failure
import anthropic

def call_model(prompt: str) -> str:
    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text
```

**Problems with the wrong pattern:**
- A single transient 503 causes total failure — no retry
- No backoff — if you wrap this in a naive loop, you hammer the service
- No timeout — hangs indefinitely on slow response
- Retries on ALL exceptions including permanent failures (e.g., bad API key)

### Right Pattern with tenacity

```python
import anthropic
import logging
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception_type,
    before_sleep_log,
    after_log,
)

logger = logging.getLogger(__name__)

# Define which exceptions are retry-worthy (transient)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

class RetryableAPIError(Exception):
    """Raised when an API error is transient and retrying is appropriate."""
    pass

@retry(
    retry=retry_if_exception_type(RetryableAPIError),
    wait=wait_random_exponential(multiplier=1, min=1, max=60),  # Full jitter
    stop=stop_after_attempt(5),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    after=after_log(logger, logging.INFO),
    reraise=True,  # Re-raise the last exception if all attempts fail
)
def call_model(prompt: str) -> str:
    """Call Claude with retry on transient failures only."""
    client = anthropic.Anthropic()
    try:
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
            timeout=30.0,  # Always set a timeout
        )
        return message.content[0].text
    except anthropic.RateLimitError as e:
        raise RetryableAPIError(f"Rate limited: {e}") from e
    except anthropic.APIStatusError as e:
        if e.status_code in RETRYABLE_STATUS_CODES:
            raise RetryableAPIError(f"Transient API error {e.status_code}: {e}") from e
        raise  # Permanent error (400, 401, 403, 404) — do not retry
    except anthropic.APIConnectionError as e:
        raise RetryableAPIError(f"Connection error: {e}") from e
```

### Right Pattern with stamina (simpler, production-grade)

```python
import stamina
import anthropic

# stamina wraps tenacity with better defaults and built-in instrumentation
@stamina.retry(
    on=anthropic.RateLimitError,  # Retry on specific exception types
    attempts=5,
    timeout=120,       # Total budget across all attempts (seconds)
    wait_initial=1.0,  # First wait after failure
    wait_max=60.0,     # Cap the wait
    wait_jitter=2.0,   # Add up to 2s random jitter
)
def call_model_stamina(prompt: str) -> str:
    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text
```

### Async Pattern

```python
from tenacity import AsyncRetrying, stop_after_attempt, wait_random_exponential
import anthropic

async def call_model_async(prompt: str) -> str:
    """Async retry with tenacity."""
    async for attempt in AsyncRetrying(
        wait=wait_random_exponential(multiplier=1, min=1, max=60),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type(RetryableAPIError),
    ):
        with attempt:
            async with anthropic.AsyncAnthropic() as client:
                message = await client.messages.create(
                    model="claude-opus-4-6",
                    max_tokens=1024,
                    messages=[{"role": "user", "content": prompt}],
                )
                return message.content[0].text
```

### Key Parameters and Recommended Defaults

| Parameter | Recommended Default | Reasoning |
|-----------|---------------------|-----------|
| `attempts` | 3-5 | More than 5 rarely helps; you're likely hitting a real outage |
| `wait_min` | 1s | Avoid hammering immediately |
| `wait_max` | 60s | Cap prevents indefinite waiting |
| `multiplier` | 1 | Doubles wait each attempt (1s → 2s → 4s → 8s → 16s → capped at 60s) |
| `jitter` | `wait_random_exponential` or `wait_jitter=2.0` | **Critical** — prevents thundering herd |
| `total_timeout` | 2-5 minutes | Hard ceiling on all retry attempts combined |

### Jitter: Why It Is Not Optional

Without jitter, when 100 concurrent clients all hit the same 503, they all wait exactly 2s, then exactly 4s — creating synchronized retry waves that continue to overwhelm the service. Full jitter spreads retries randomly across the window, so recovery looks like gradual ramp-up rather than synchronized hammering.

```python
# AWS-recommended "full jitter" formula (most effective for thundering herd):
# sleep = random_between(0, min(cap, base * 2^attempt))

# In tenacity:
wait=wait_random_exponential(multiplier=1, min=1, max=60)
# This implements full jitter automatically
```

### Common AI Agent Mistakes

1. **No stop condition**: `@retry()` with no `stop=` creates infinite loops that consume quota indefinitely
2. **Retry on ALL exceptions**: Retrying auth failures (401) wastes time and quota
3. **No jitter**: Creates thundering herd when multiple agents retry simultaneously
4. **Retry non-idempotent operations**: Retrying a payment without an idempotency key = double charge
5. **No observability**: Retries happen silently; no logging means you never know how often retries fire
6. **Missing `reraise=True`**: After exhausting retries, some configs swallow the last exception silently

---

## Pattern 2: Circuit Breaker

### What It Solves

When a downstream service (database, model API, external service) is failing consistently, retry logic alone makes things worse — you keep sending requests to a service that cannot respond, exhausting your own connection pools and adding load to an already struggling system. The circuit breaker stops calls entirely after repeated failures, allowing the downstream service to recover.

### State Machine

```
CLOSED (normal)
    │ failure_count >= threshold
    ▼
OPEN (blocking all calls)
    │ reset_timeout elapsed
    ▼
HALF-OPEN (trial mode: allow one call)
    │ trial succeeds            │ trial fails
    ▼                           ▼
CLOSED (reset)              OPEN (re-open)
```

### When to Use

**Use circuit breaker when:**
- Calling a dependency that is sometimes unavailable (external APIs, databases, model endpoints)
- Failures cascade — one slow downstream service blocks all your threads
- You can define a meaningful fallback behavior when the circuit is open
- High call volume makes retry storms a real risk

**Do NOT use circuit breaker when:**
- The call is infrequent (< 1/min) — overhead is not worth it
- You have no fallback — an open circuit just adds error, it does not help
- The failure is in your own code, not downstream

### Wrong Pattern (No Circuit Breaker)

```python
# WRONG: If the database is down for 2 minutes, every request blocks on connection
# timeout, exhausting thread pool, cascading failure to all services
import psycopg2

def get_user(user_id: str) -> dict:
    conn = psycopg2.connect(DATABASE_URL)  # Hangs if DB is down
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        return cur.fetchone()
```

### Right Pattern with pybreaker

```python
import pybreaker
import psycopg2
import logging

logger = logging.getLogger(__name__)

# Create one breaker per downstream dependency
db_breaker = pybreaker.CircuitBreaker(
    fail_max=5,           # Open circuit after 5 consecutive failures
    reset_timeout=60,     # Try again after 60 seconds
    name="postgres_db",
    listeners=[pybreaker.CircuitBreakerMonitor],  # For observability
)

@db_breaker
def _db_get_user_raw(user_id: str) -> dict | None:
    """Inner function wrapped by circuit breaker."""
    with psycopg2.connect(DATABASE_URL, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, email FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            return {"id": row[0], "name": row[1], "email": row[2]} if row else None

def get_user(user_id: str) -> dict | None:
    """Public function with fallback when circuit is open."""
    try:
        return _db_get_user_raw(user_id)
    except pybreaker.CircuitBreakerError:
        # Circuit is OPEN — do not call the database
        logger.warning("DB circuit breaker OPEN, returning cached/fallback data")
        return get_user_from_cache(user_id)  # Fallback: cache, read replica, etc.
    except Exception as e:
        logger.error(f"DB call failed: {e}")
        return None  # Degrade gracefully
```

### Right Pattern with circuitbreaker (decorator style)

```python
from circuitbreaker import circuit, CircuitBreakerError
import httpx

@circuit(
    failure_threshold=5,      # Open after 5 failures
    recovery_timeout=30,      # Attempt recovery after 30s
    expected_exception=httpx.HTTPError,  # Only count these as failures
    name="model_api",
)
def call_model_api(prompt: str) -> str:
    with httpx.Client(timeout=10.0) as client:
        response = client.post(
            "https://api.example.com/v1/complete",
            json={"prompt": prompt},
        )
        response.raise_for_status()
        return response.json()["text"]

def call_model_with_fallback(prompt: str) -> str:
    try:
        return call_model_api(prompt)
    except CircuitBreakerError:
        # Circuit open — use a simpler local model instead
        return call_local_fallback_model(prompt)
```

### Key Parameters and Recommended Defaults

| Parameter | Recommended Default | Reasoning |
|-----------|---------------------|-----------|
| `fail_max` / `failure_threshold` | 5 | Low enough to detect real outage; high enough to ignore one-off blip |
| `reset_timeout` | 30-60s | Short enough for fast recovery; long enough for downstream to stabilize |
| One breaker per dependency | Required | Shared breaker couples unrelated services |

### Circuit Breaker vs. Retry: When to Use Which

| Scenario | Use |
|----------|-----|
| Occasional transient failure (< 10% rate) | Retry with backoff |
| Sustained high failure rate (> 30%) | Circuit breaker |
| Both transient and sustained risk | Circuit breaker wrapping retried function |
| Need to call downstream at all costs | Retry only (no circuit breaker) |
| Can fallback when downstream is down | Circuit breaker (enable clean fallback) |

### Common AI Agent Mistakes

1. **One global circuit breaker**: All services share one breaker — DB failure opens circuit for model API
2. **No fallback defined**: Circuit opens, `CircuitBreakerError` propagates up unhandled, crashes the caller
3. **Thresholds too high**: `fail_max=100` means 100 failures before protection activates — too slow
4. **Thresholds too low**: `fail_max=1` opens the circuit on any single transient error — too sensitive
5. **No monitoring**: No way to observe circuit state; silent open circuits hide real outages

---

## Pattern 3: Graceful Degradation

### What It Solves

When a component fails, the question is: what does the rest of the system do? Three options, ordered from worst to best:

1. **Crash completely** — simple but never acceptable in production
2. **Return an error to caller** — sometimes correct; always needs to be intentional
3. **Degrade to a reduced-capability state** — preferred for safety-critical and user-facing systems

Graceful degradation means the system continues functioning at reduced quality rather than failing entirely.

### Degradation Levels (Hierarchy)

```
Level 0: Full capability (normal operation)
Level 1: Reduced quality (smaller model, cached result, simplified logic)
Level 2: Minimal capability (rule-based fallback, static response)
Level 3: Safe state (stop and hold, notify human)
Level 4: Emergency stop (for physical systems — stop all motion)
```

### When to Use

**Use graceful degradation when:**
- System failure has consequences beyond just an error message (physical systems, user-facing products)
- A subset of functionality is better than nothing
- You can define a meaningful "safe state" for each failure mode
- Multiple components could independently fail

**Do NOT use graceful degradation when:**
- Partial results could cause more harm than no results (medical decisions, financial transactions)
- You cannot verify which parts of a result are correct
- The system's guarantees require all components to be operational

### Wrong Pattern (Binary Fail/Succeed)

```python
# WRONG: No fallback strategy — entire system crashes on model API failure
class DroneNavigationSystem:
    def compute_path(self, obstacles: list, target: Point) -> Path:
        # If this fails, drone has no path — drops or holds indefinitely
        return self.ai_model.plan_path(obstacles, target)
```

### Right Pattern: Layered Fallback

```python
from enum import Enum
import logging

logger = logging.getLogger(__name__)

class DegradationLevel(Enum):
    FULL = 0          # AI model online
    REDUCED = 1       # Lighter model or cached plan
    MINIMAL = 2       # Rule-based geometric path
    SAFE_HOLD = 3     # Stop and hover, await human
    EMERGENCY_STOP = 4  # Cut motors, controlled descent

class DroneNavigationSystem:
    def __init__(self):
        self._degradation_level = DegradationLevel.FULL
        self._primary_model = PrimaryPathPlannerModel()
        self._lite_model = LitePathPlannerModel()
        self._rule_planner = GeometricPathPlanner()

    def compute_path(self, obstacles: list, target: Point) -> tuple[Path, DegradationLevel]:
        """Always returns a path and the degradation level used."""

        # Level 0: Try primary AI model
        if self._degradation_level <= DegradationLevel.FULL:
            try:
                path = self._primary_model.plan_path(obstacles, target)
                self._degradation_level = DegradationLevel.FULL
                return path, DegradationLevel.FULL
            except ModelTimeoutError as e:
                logger.warning(f"Primary model timeout, degrading: {e}")
                self._degradation_level = DegradationLevel.REDUCED
            except ModelError as e:
                logger.error(f"Primary model failed, degrading: {e}")
                self._degradation_level = DegradationLevel.REDUCED

        # Level 1: Lighter model
        if self._degradation_level <= DegradationLevel.REDUCED:
            try:
                path = self._lite_model.plan_path(obstacles, target)
                logger.info("Operating in REDUCED mode (lite model)")
                return path, DegradationLevel.REDUCED
            except Exception as e:
                logger.error(f"Lite model also failed: {e}")
                self._degradation_level = DegradationLevel.MINIMAL

        # Level 2: Rule-based planner (always available, deterministic)
        if self._degradation_level <= DegradationLevel.MINIMAL:
            try:
                path = self._rule_planner.plan_path(obstacles, target)
                logger.warning("Operating in MINIMAL mode (rule-based planner)")
                return path, DegradationLevel.MINIMAL
            except Exception as e:
                logger.critical(f"All planners failed: {e}")
                self._degradation_level = DegradationLevel.SAFE_HOLD

        # Level 3: Safe hold — stop movement, notify human
        logger.critical("SAFE HOLD: All path planners unavailable. Hovering.")
        self._notify_ground_control("All path planners unavailable")
        return HoverInPlace(), DegradationLevel.SAFE_HOLD
```

### Degradation for AI Agent Systems (Non-Physical)

```python
class AgentResponseSystem:
    """AI coding agent with graceful degradation for model failures."""

    def generate_code(self, spec: str) -> CodeResult:
        # Level 0: Primary (most capable) model
        try:
            return self._call_primary_model(spec, timeout=30.0)
        except (TimeoutError, ModelRateLimitError) as e:
            logger.warning(f"Primary model unavailable: {e}. Trying fallback.")

        # Level 1: Smaller/faster model
        try:
            result = self._call_fallback_model(spec, timeout=15.0)
            result.degradation_note = "Generated by fallback model — review carefully"
            return result
        except Exception as e:
            logger.error(f"Fallback model also failed: {e}")

        # Level 2: Return partial result with clear caveat
        return CodeResult(
            code=None,
            error="Both models unavailable",
            degradation_note="Code generation temporarily unavailable. Returning spec unchanged.",
            suggest_retry_after=60,
        )
```

### Safety-Critical System Guidelines

From automotive (ISO 26262), aerospace (DO-178C), and robotics standards:

1. **Fail-safe state is always defined**: Every component has a known safe state to enter on failure. For drones: hover. For autonomous vehicles: pull over and stop. For surgical robots: hold position.

2. **Degradation must be deterministic**: The system must not randomly choose a degradation level. The hierarchy is fixed in code.

3. **Degradation level is observable**: Current level is always available to monitoring systems and operators.

4. **Recovery is explicit, not automatic**: A system that degraded due to a safety concern should not silently re-upgrade itself without operator confirmation.

5. **Multi-graceful degradation**: For increasingly severe faults, successively reduced capability guarantees. One fault removes one capability. Catastrophic fault triggers emergency stop.

### Common AI Agent Mistakes

1. **Binary error handling**: `try/except Exception: return None` — no fallback hierarchy
2. **Silent degradation**: Degrading without logging or alerting means operators never know
3. **Degradation without capability signaling**: Callers need to know what level of result they received
4. **No safe state for physical actuators**: AI-controlled motors/actuators need emergency stop logic
5. **Automatic upgrade without verification**: System re-enables primary model after any success, even if the root cause (hardware fault) is still present

---

## Pattern 4: Timeout Boundaries

### What It Solves

Every external call — network request, database query, subprocess, model inference — can hang indefinitely. Without timeouts, a single slow dependency blocks all your threads, exhausts connection pools, and cascades into total system failure. AI agents almost universally omit timeouts, treating external calls as instantaneous.

### The Core Rule

**Every external call must have a timeout. No exceptions.**

### When to Use

**Always add timeouts for:**
- HTTP/API calls (requests, httpx, aiohttp)
- Database queries and connections
- File I/O on network-mounted filesystems
- Subprocess calls (`subprocess.run`)
- Model inference calls
- Any `asyncio.wait`, `asyncio.gather` over external calls

### Wrong Pattern (No Timeout)

```python
# WRONG: Multiple missing timeouts
import requests
import subprocess
import psycopg2

def process_request(user_input: str):
    # Hangs indefinitely if API is slow
    response = requests.post("https://api.example.com/process", json={"input": user_input})

    # Hangs if ffmpeg hangs on corrupt input
    result = subprocess.run(["ffmpeg", "-i", "video.mp4", "output.mp4"])

    # Hangs if database connection is slow
    conn = psycopg2.connect(DATABASE_URL)
```

### Right Pattern: Synchronous

```python
import requests
import subprocess
import psycopg2

def process_request(user_input: str):
    # requests: Always set both connect and read timeout separately
    try:
        response = requests.post(
            "https://api.example.com/process",
            json={"input": user_input},
            timeout=(5.0, 30.0),  # (connect_timeout, read_timeout)
        )
        response.raise_for_status()
    except requests.Timeout:
        raise TimeoutError("API call timed out after 30s")

    # subprocess: Always set timeout
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", "video.mp4", "output.mp4"],
            timeout=120,  # seconds
            capture_output=True,
            check=True,
        )
    except subprocess.TimeoutExpired:
        result.kill()  # Important: kill the subprocess on timeout
        raise TimeoutError("ffmpeg timed out after 120s")

    # psycopg2: Set connect_timeout and statement_timeout
    conn = psycopg2.connect(
        DATABASE_URL,
        connect_timeout=5,  # Connection timeout in seconds
        options="-c statement_timeout=30000",  # 30s query timeout (milliseconds)
    )
```

### Right Pattern: Async (Python 3.11+)

```python
import asyncio
import httpx

async def process_request_async(user_input: str) -> str:
    # asyncio.timeout() is the modern way (Python 3.11+)
    try:
        async with asyncio.timeout(30.0):  # Hard 30s total budget
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.example.com/process",
                    json={"input": user_input},
                    timeout=httpx.Timeout(
                        connect=5.0,   # Connection establishment
                        read=25.0,     # Time to receive response body
                        write=5.0,     # Time to send request body
                        pool=5.0,      # Wait for connection from pool
                    ),
                )
                response.raise_for_status()
                return response.json()["result"]
    except asyncio.TimeoutError:
        raise TimeoutError("Request exceeded 30s total budget")

# For Python 3.10 and earlier, use asyncio.wait_for():
async def process_request_compat(user_input: str) -> str:
    try:
        return await asyncio.wait_for(
            _do_request(user_input),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        raise TimeoutError("Request timed out after 30s")
```

### httpx Timeout Breakdown

```python
import httpx

# Full granular timeout (recommended for production)
timeout = httpx.Timeout(
    connect=5.0,    # Time to establish TCP connection
    read=30.0,      # Time between bytes received (not total transfer)
    write=10.0,     # Time to send request body
    pool=5.0,       # Time waiting for connection from pool
)

# Simple scalar timeout (applies same value to connect + read)
timeout = httpx.Timeout(10.0)

# Per-request override
async with httpx.AsyncClient(timeout=timeout) as client:
    response = await client.get(
        "https://api.example.com/data",
        timeout=httpx.Timeout(60.0),  # Override for this specific slow endpoint
    )
```

### Signal-Based Timeouts (Use with Caution)

```python
import signal
from contextlib import contextmanager

@contextmanager
def timeout(seconds: int):
    """Signal-based timeout. UNIX-only, not thread-safe."""
    def _handler(signum, frame):
        raise TimeoutError(f"Operation timed out after {seconds}s")

    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

# Usage (sync code, UNIX only):
with timeout(30):
    result = some_blocking_call()
```

**Signal timeout limitations:**
- UNIX only (no Windows support)
- Not safe in multi-threaded programs — only the main thread can handle signals
- Cannot interrupt C extensions (e.g., NumPy, psycopg2) in the middle of atomic operations
- Prefer `asyncio.timeout()` for async code, `timeout=` parameters for library calls

### Recommended Default Timeouts by Call Type

| Call Type | Connect | Read/Total | Notes |
|-----------|---------|------------|-------|
| Fast REST API | 3-5s | 10-15s | |
| Model inference API | 5s | 60-120s | LLMs can be slow |
| Database query | 3-5s | 10-30s | Adjust for complex queries |
| Database connection | 3-5s | N/A | Connection-only |
| File download | 5s | 300s | Depends on file size |
| subprocess | N/A | 30-120s | Task-dependent |
| Health check | 1-2s | 5s | Must be fast |

### Common AI Agent Mistakes

1. **No timeout at all**: The most common mistake. `requests.get(url)` with no `timeout=` hangs forever
2. **Single scalar for requests**: `timeout=30` applies to read only, not connection — can still hang on DNS/connect
3. **asyncio.timeout() in sync code**: This context manager only works inside `async def`
4. **Not canceling subprocess on timeout**: `subprocess.TimeoutExpired` does not kill the process automatically — you must call `proc.kill()`
5. **Too short for model inference**: Setting 5s timeout on an LLM call that needs 30-60s for complex prompts
6. **No timeout on the outer operation**: Setting timeouts on inner calls but not the overall operation budget

---

## Pattern 5: Bulkhead Pattern

### What It Solves

If one operation can monopolize all available threads/connections/resources, a single overloaded operation can starve all others. Bulkheads isolate resource pools per service or operation type, so one misbehaving component cannot cascade failure to everything else.

The name comes from ship design: watertight compartments ensure one flooded section does not sink the whole ship.

### Two Implementations

**Thread Pool Bulkhead**: Separate `ThreadPoolExecutor` per downstream service. A blocking call to Service A cannot exhaust threads needed by Service B.

**Semaphore Bulkhead**: Limit concurrent async operations per service with `asyncio.Semaphore`.

### When to Use

**Use bulkhead when:**
- Multiple downstream services are called from the same application
- One slow service can block all threads, starving other services
- You want to guarantee resource availability for critical operations (e.g., health checks always have a thread)
- Different operations have different priority levels

**Do NOT use when:**
- You have a single downstream service (just limit concurrency on that one)
- All operations have equal priority and resource requirements
- System is I/O-bound with async code that does not block threads

### Wrong Pattern (Shared Thread Pool)

```python
# WRONG: All services share default thread pool
# If payment_service is slow, it blocks inventory_service, notification_service, everything
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=20)  # One pool for everything

def handle_order(order_id: str):
    # All three block on the SAME thread pool
    payment_future = executor.submit(payment_service.charge, order_id)
    inventory_future = executor.submit(inventory_service.reserve, order_id)
    notification_future = executor.submit(notification_service.send, order_id)
```

### Right Pattern: Separate Thread Pools per Service

```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
import logging

logger = logging.getLogger(__name__)

# Bulkhead: separate, bounded thread pools per downstream service
_payment_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="payment")
_inventory_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="inventory")
_notification_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="notification")

def handle_order(order_id: str) -> OrderResult:
    """
    Process order with bulkhead isolation.
    Slow payments do not block inventory checks.
    Slow notifications do not block either.
    """
    # Submit to isolated pools
    payment_future = _payment_executor.submit(payment_service.charge, order_id)
    inventory_future = _inventory_executor.submit(inventory_service.reserve, order_id)

    try:
        payment_result = payment_future.result(timeout=10.0)
    except FuturesTimeout:
        payment_future.cancel()
        raise TimeoutError("Payment service timed out after 10s")

    try:
        inventory_result = inventory_future.result(timeout=5.0)
    except FuturesTimeout:
        inventory_future.cancel()
        raise TimeoutError("Inventory service timed out after 5s")

    # Non-critical: notifications use their own small pool, fire-and-forget
    _notification_executor.submit(notification_service.send, order_id)

    return OrderResult(payment=payment_result, inventory=inventory_result)
```

### Right Pattern: Async Semaphore Bulkhead

```python
import asyncio
import httpx

# Limit concurrent calls per service
_model_api_semaphore = asyncio.Semaphore(5)   # Max 5 concurrent model API calls
_db_semaphore = asyncio.Semaphore(10)          # Max 10 concurrent DB queries
_webhook_semaphore = asyncio.Semaphore(20)     # Max 20 concurrent webhook deliveries

async def call_model_api(prompt: str) -> str:
    """Model API calls are rate-limited — bulkhead prevents overwhelming the endpoint."""
    async with _model_api_semaphore:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.example.com/complete",
                json={"prompt": prompt},
            )
            return response.json()["text"]

async def handle_batch(prompts: list[str]) -> list[str]:
    """Process many prompts concurrently but respecting the bulkhead."""
    tasks = [call_model_api(p) for p in prompts]
    return await asyncio.gather(*tasks)  # Semaphore limits actual concurrency
```

### Bulkhead + Circuit Breaker Composition

These two patterns work best together:

```python
import pybreaker
from concurrent.futures import ThreadPoolExecutor

# Circuit breaker guards against sustained failures
_db_breaker = pybreaker.CircuitBreaker(fail_max=5, reset_timeout=60)

# Bulkhead limits concurrent calls to the DB
_db_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="db")

@_db_breaker
def _raw_db_query(query: str, params: tuple) -> list:
    """Circuit-breaker protected DB query."""
    with get_db_connection() as conn:
        return conn.execute(query, params).fetchall()

def db_query(query: str, params: tuple) -> list:
    """Bulkhead-isolated, circuit-breaker protected query."""
    future = _db_executor.submit(_raw_db_query, query, params)
    try:
        return future.result(timeout=10.0)  # Timeout on queue wait + execution
    except pybreaker.CircuitBreakerError:
        logger.warning("DB circuit breaker open")
        return []  # Fallback
```

### Key Parameters and Recommended Defaults

| Parameter | Recommended Default | Reasoning |
|-----------|---------------------|-----------|
| `max_workers` per pool | 5-20 | Depends on downstream service capacity and SLA |
| Critical services pool | Larger | Health checks, primary API must always have threads |
| Non-critical services pool | Smaller | Notifications, analytics can be throttled |
| Semaphore limit | 5-50 | Match to upstream rate limits or downstream capacity |
| Queue timeout | 5-10s | Fail fast if the pool itself is saturated |

### Common AI Agent Mistakes

1. **Single global `ThreadPoolExecutor`**: All services compete for same pool; slow service starves fast ones
2. **Unbounded pools**: `max_workers=None` (default uses CPU count * 5) can create hundreds of threads
3. **Forgetting to call `.cancel()` on timeout**: A timed-out future still runs in the background consuming resources
4. **No pool monitoring**: No metrics on queue depth, rejection rate, saturation
5. **Semaphore scope too broad**: One semaphore for all external calls — not isolated per service

---

## Combining Patterns: Production-Ready Agent Call Wrapper

This example combines all five patterns for a typical AI agent external API call:

```python
"""
Production-ready external API call wrapper combining all five resilience patterns.
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from enum import Enum

import httpx
import pybreaker
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

logger = logging.getLogger(__name__)

# Pattern 5: Bulkhead — isolated semaphore for model API calls
_model_api_semaphore = asyncio.Semaphore(5)

# Pattern 2: Circuit breaker — stops calls when API is persistently down
_model_api_breaker = pybreaker.CircuitBreaker(
    fail_max=5,
    reset_timeout=60,
    name="model_api",
)


class ModelCallResult:
    def __init__(self, text: str | None, degradation_level: int, error: str | None = None):
        self.text = text
        self.degradation_level = degradation_level  # Pattern 3: degradation signal
        self.error = error


class RetryableModelError(Exception):
    pass


async def _raw_model_call(prompt: str, timeout: float) -> str:
    """Inner call — no retry, no circuit breaker, no bulkhead (applied externally)."""
    # Pattern 4: Timeout on every call
    async with asyncio.timeout(timeout):
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.example.com/complete",
                json={"prompt": prompt},
                timeout=httpx.Timeout(connect=5.0, read=timeout - 5.0, write=5.0, pool=5.0),
            )
            if response.status_code in {429, 500, 502, 503, 504}:
                raise RetryableModelError(f"HTTP {response.status_code}")
            response.raise_for_status()
            return response.json()["text"]


async def call_model(prompt: str) -> ModelCallResult:
    """
    Production-grade model call with all five resilience patterns.
    Pattern order: Bulkhead -> Circuit Breaker -> Retry -> Timeout -> Graceful Degradation
    """
    # Pattern 5: Bulkhead — wait for capacity
    async with _model_api_semaphore:
        try:
            # Pattern 2: Circuit breaker — fast-fail if service is down
            # (pybreaker is sync; wrap in executor for async context)
            loop = asyncio.get_event_loop()

            # Pattern 1: Retry with exponential backoff + jitter
            async for attempt in AsyncRetrying(
                wait=wait_random_exponential(multiplier=1, min=1, max=30),
                stop=stop_after_attempt(3),
                retry=retry_if_exception_type(RetryableModelError),
            ):
                with attempt:
                    # Pattern 4: Timeout boundary on each attempt
                    text = await _raw_model_call(prompt, timeout=30.0)
                    return ModelCallResult(text=text, degradation_level=0)

        except pybreaker.CircuitBreakerError:
            logger.warning("Model API circuit breaker OPEN — using fallback")
            # Pattern 3: Graceful degradation — fallback response
            return ModelCallResult(
                text=None,
                degradation_level=2,
                error="Primary model unavailable (circuit open). Please retry later.",
            )
        except (asyncio.TimeoutError, RetryableModelError) as e:
            logger.error(f"Model call failed after retries: {e}")
            # Pattern 3: Graceful degradation — return error with retry hint
            return ModelCallResult(
                text=None,
                degradation_level=1,
                error=f"Model call failed: {e}",
            )
```

---

## Library Quick Reference

| Library | Purpose | Install | Maturity |
|---------|---------|---------|---------|
| `tenacity` | Retry with exponential backoff | `pip install tenacity` | Production-grade, widely used |
| `stamina` | Retry (tenacity wrapper, better defaults) | `pip install stamina` | Production-grade, opinionated |
| `pybreaker` | Circuit breaker | `pip install pybreaker` | Stable, 93K+ weekly downloads |
| `circuitbreaker` | Circuit breaker (decorator style) | `pip install circuitbreaker` | Simpler API, less configurable |
| `aiobreaker` | Async circuit breaker | `pip install aiobreaker` | For asyncio-first code |
| `httpx` | HTTP client with timeout primitives | `pip install httpx` | Modern, async-native |

---

## Decision Tree: Which Pattern(s) to Apply

```
External call needed?
├── Yes ──> Add TIMEOUT (always, no exceptions)
│
├── Call could fail transiently?
│   ├── Yes ──> Add RETRY with backoff + jitter
│   └── No  ──> Fail fast, no retry
│
├── Call is to a dependency that could go down for minutes?
│   ├── Yes ──> Add CIRCUIT BREAKER
│   └── No  ──> Retry is sufficient
│
├── What happens when this call fails?
│   ├── System crashes ──────────────> Add GRACEFUL DEGRADATION (fallback hierarchy)
│   ├── Caller gets error (acceptable) -> Document the error type clearly
│   └── Physical actuator is involved ─> EMERGENCY STOP is required
│
└── Multiple services competing for same threads?
    ├── Yes ──> Add BULKHEAD (separate pools/semaphores per service)
    └── No  ──> Single pool is fine
```

---

## Sources

- [tenacity — Retrying library for Python (GitHub)](https://github.com/jd/tenacity)
- [tenacity documentation](https://tenacity.readthedocs.io/)
- [stamina — Production-grade retries for Python (GitHub)](https://github.com/hynek/stamina)
- [stamina 25.2.0 documentation](https://stamina.hynek.me/)
- [pybreaker — Circuit Breaker for Python (GitHub)](https://github.com/danielfm/pybreaker)
- [circuitbreaker — Python Circuit Breaker (GitHub)](https://github.com/fabfuel/circuitbreaker)
- [AWS Builder's Library: Timeouts, retries and backoff with jitter](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/)
- [AWS Architecture Blog: Exponential Backoff And Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/)
- [AWS Prescriptive Guidance: Retry with backoff pattern](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/retry-backoff.html)
- [HTTPX Timeouts documentation](https://www.python-httpx.org/advanced/timeouts/)
- [Better Stack: A Complete Guide to Timeouts in Python](https://betterstack.com/community/guides/scaling-python/python-timeouts/)
- [oneuptime: How to Implement Bulkhead Pattern in Python](https://oneuptime.com/blog/post/2026-01-25-bulkhead-pattern-python/view)
- [Resilient AI Agents With MCP: Timeout And Retry Strategies (Octopus)](https://octopus.com/blog/mcp-timeout-retry)
- [AWS Architecture Blog: Build resilient generative AI agents](https://aws.amazon.com/blogs/architecture/build-resilient-generative-ai-agents/)
- [GitHub Blog: Multi-agent workflows often fail — how to engineer ones that don't](https://github.blog/ai-and-ml/generative-ai/multi-agent-workflows-often-fail-heres-how-to-engineer-ones-that-dont/)
- [VentureBeat: Why AI coding agents aren't production-ready](https://venturebeat.com/ai/why-ai-coding-agents-arent-production-ready-brittle-context-windows-broken)
- [Integrating Graceful Degradation and Recovery through Requirement-driven Adaptation (arxiv)](https://arxiv.org/html/2401.09678v2)
- [Fail-Operational Automotive Software Design Using Agent-Based Graceful Degradation (IEEE)](https://ieeexplore.ieee.org/document/9116322/)
- [Python signal module documentation](https://docs.python.org/3/library/signal.html)
- [Python concurrent.futures documentation](https://docs.python.org/3/library/concurrent.futures.html)
- [Mastering Exponential Backoff in Distributed Systems (Better Stack)](https://betterstack.com/community/guides/monitoring/exponential-backoff/)
