# Resilience Patterns for Production Code

Patterns for handling failures in external dependencies — every external call can fail, hang, or degrade. AI agents consistently produce happy-path-only code.

## Contents

- [Retry with Exponential Backoff](#retry-with-exponential-backoff) — transient failures, tenacity/stamina
- [Circuit Breaker](#circuit-breaker) — sustained failures, pybreaker
- [Timeout Boundaries](#timeout-boundaries) — every external call needs one
- [Graceful Degradation](#graceful-degradation) — layered fallback hierarchy
- [Bulkhead](#bulkhead) — resource isolation per service
- [Composition Order](#composition-order) — how all five combine

## Retry with Exponential Backoff

Solves transient failures — temporary network blips, rate limits (429), server errors (500-504).

**When to retry vs. not:**

| Retry | Don't Retry |
|-------|-------------|
| HTTP 429 (rate limit) | HTTP 400 (bad request) |
| HTTP 500, 502, 503, 504 | HTTP 401, 403 (auth failure) |
| Connection timeout | HTTP 404 (not found) |
| DNS resolution failure | Business logic errors |
| Database pool exhaustion | Non-idempotent operations without idempotency key |

```python
# WRONG — no retry, no backoff, no timeout
def call_api(prompt: str) -> str:
    response = requests.post(API_URL, json={"prompt": prompt})
    return response.json()["result"]

# RIGHT — retry with tenacity
from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception_type

@retry(
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    wait=wait_random_exponential(multiplier=1, min=1, max=60),  # Full jitter
    stop=stop_after_attempt(5),
    reraise=True,
)
def call_api(prompt: str) -> str:
    response = requests.post(API_URL, json={"prompt": prompt}, timeout=(5, 30))
    if response.status_code in {429, 500, 502, 503, 504}:
        raise RetryableError(f"Status {response.status_code}")
    response.raise_for_status()
    return response.json()["result"]
```

**Recommended defaults:**

| Parameter | Default | Reasoning |
|-----------|---------|-----------|
| Attempts | 3-5 | More than 5 rarely helps — likely a real outage |
| Min wait | 1s | Don't hammer immediately |
| Max wait | 60s | Cap prevents indefinite waiting |
| Jitter | `wait_random_exponential` | **Required** — prevents thundering herd |
| Total timeout | 2-5 min | Hard ceiling across all retries |

**Jitter is not optional.** Without it, 100 clients all retry at exactly 2s, then 4s — synchronized waves that overwhelm the recovering service.

**AI agent mistakes:** No stop condition (infinite loops), retrying auth failures (401), retrying non-idempotent ops (double charges), no logging of retries.

## Circuit Breaker

When a dependency fails repeatedly, stop calling it. Retry alone makes sustained outages worse — you keep sending requests to a service that can't respond.

```
CLOSED (normal) → failure_count >= threshold → OPEN (blocking all calls)
OPEN → reset_timeout elapsed → HALF-OPEN (trial: allow one call)
HALF-OPEN → trial succeeds → CLOSED / trial fails → OPEN
```

```python
import pybreaker

# One breaker PER dependency — never share
db_breaker = pybreaker.CircuitBreaker(fail_max=5, reset_timeout=60, name="postgres")

@db_breaker
def _query_db(user_id: str) -> dict | None:
    with psycopg2.connect(DB_URL, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            return cur.fetchone()

def get_user(user_id: str) -> dict | None:
    try:
        return _query_db(user_id)
    except pybreaker.CircuitBreakerError:
        logger.warning("DB circuit open — returning cached data")
        return get_from_cache(user_id)  # Always define a fallback
```

**When to use which:**

| Scenario | Pattern |
|----------|---------|
| Occasional transient failure (< 10%) | Retry with backoff |
| Sustained high failure rate (> 30%) | Circuit breaker |
| Both risks | Circuit breaker wrapping retried function |
| Can degrade when dependency is down | Circuit breaker with fallback |

**AI agent mistakes:** One global breaker for all services, no fallback defined, thresholds too high (100 failures before protection).

## Timeout Boundaries

**Every external call must have a timeout. No exceptions.**

```python
# WRONG — three calls that can hang forever
response = requests.post(url, json=data)
result = subprocess.run(["ffmpeg", "-i", "in.mp4", "out.mp4"])
conn = psycopg2.connect(DATABASE_URL)

# RIGHT — explicit timeouts on every call
response = requests.post(url, json=data, timeout=(5.0, 30.0))  # (connect, read)

result = subprocess.run(
    ["ffmpeg", "-i", "in.mp4", "out.mp4"],
    timeout=120, capture_output=True, check=True,
)

conn = psycopg2.connect(
    DATABASE_URL,
    connect_timeout=5,
    options="-c statement_timeout=30000",  # ms
)
```

**Async (Python 3.11+):**

```python
async with asyncio.timeout(30.0):
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=data, timeout=httpx.Timeout(
            connect=5.0, read=25.0, write=5.0, pool=5.0,
        ))
```

**Recommended defaults:**

| Call Type | Connect | Read/Total |
|-----------|---------|------------|
| Fast REST API | 3-5s | 10-15s |
| Model inference | 5s | 60-120s |
| Database query | 3-5s | 10-30s |
| Subprocess | N/A | 30-120s |
| Health check | 1-2s | 5s |

**AI agent mistakes:** No timeout at all (most common), single scalar for requests (doesn't cover connect), not killing subprocess on timeout (`proc.kill()` is required).

## Graceful Degradation

When a component fails, what does the system do? Define a hierarchy:

```
Level 0: Full capability (primary model)
Level 1: Reduced quality (lighter model, cached result)
Level 2: Minimal capability (rule-based fallback, static response)
Level 3: Safe hold (stop and hold, notify human)
Level 4: Emergency stop (physical systems — stop all motion)
```

```python
class NavigationSystem:
    def compute_path(self, obstacles, target) -> tuple[Path, int]:
        # Level 0: Primary AI model
        try:
            return self._primary_model.plan(obstacles, target), 0
        except (ModelTimeoutError, ModelError) as e:
            logger.warning("Primary model failed: %s", e)

        # Level 1: Lighter model
        try:
            return self._lite_model.plan(obstacles, target), 1
        except Exception as e:
            logger.error("Lite model failed: %s", e)

        # Level 2: Rule-based (always available, deterministic)
        try:
            return self._rule_planner.plan(obstacles, target), 2
        except Exception:
            pass

        # Level 3: Safe hold
        logger.critical("All planners failed — hovering")
        return HoverInPlace(), 3
```

**Safety-critical rules (ISO 26262, DO-178C):**
1. Every component has a defined safe state (drones: hover; vehicles: pull over)
2. Degradation hierarchy is deterministic — fixed in code, not random
3. Current degradation level is always observable
4. Recovery from degradation requires explicit operator confirmation
5. Each fault removes one capability; catastrophic fault triggers emergency stop

**AI agent mistakes:** Binary error handling (crash or succeed), silent degradation (no logging), automatic upgrade without verifying root cause is resolved.

## Bulkhead

Isolate resource pools so one misbehaving dependency can't starve others.

```python
# WRONG — all services share one pool
executor = ThreadPoolExecutor(max_workers=20)

# RIGHT — separate pools per service
_payment_pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="payment")
_inventory_pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="inventory")
_notification_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="notify")
```

**Async variant:** Use `asyncio.Semaphore` per service to limit concurrent calls.

```python
_model_semaphore = asyncio.Semaphore(5)   # Max 5 concurrent model calls

async def call_model(prompt: str) -> str:
    async with _model_semaphore:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json={"prompt": prompt})
            return response.json()["text"]
```

## Composition Order

When combining patterns, apply from outside in:

```
Bulkhead → Circuit Breaker → Retry → Timeout → Graceful Degradation
```

The bulkhead limits concurrency. The circuit breaker prevents calls when the service is known-down. Retry handles transient failures within the circuit. Timeout prevents any single attempt from hanging. Graceful degradation provides fallback when all else fails.

**Sources:** AWS Builder's Library, tenacity docs, pybreaker docs, stamina docs, ISO 26262, DO-178C
