# Concurrency Patterns for Code Design

Concurrency is a structural decision, not an optimization. Choosing async vs threads vs multiprocessing shapes your entire module's interface, error handling, and testability. AI agents default to `asyncio.gather` for everything — ignoring cancellation, shared state, and the distinction between I/O-bound and CPU-bound work.

## Contents

- [Choosing the Right Model](#choosing-the-right-model) — async vs threads vs multiprocessing
- [Structured Concurrency](#structured-concurrency) — TaskGroup, never fire-and-forget
- [Shared State](#shared-state) — isolation, synchronization primitives
- [Common Anti-Patterns](#common-anti-patterns) — WRONG/RIGHT pairs
- [Testing Concurrent Code](#testing-concurrent-code) — deterministic async tests

## Choosing the Right Model

| Workload | Model | Why |
|----------|-------|-----|
| Network I/O (HTTP, DB, sockets) | `asyncio` | Cooperative scheduling, no thread overhead, scales to thousands of connections |
| File I/O, subprocess calls | `asyncio` or threads | `asyncio` with `run_in_executor` for blocking calls; threads for simple cases |
| CPU-bound computation (ML inference, image processing, compression) | `multiprocessing` or `concurrent.futures.ProcessPoolExecutor` | GIL prevents threads from parallelizing CPU work in CPython |
| Mixed I/O + CPU | Async event loop + process pool for CPU tasks | Keep I/O async, offload CPU to separate processes |
| Simple parallelism, few tasks | `concurrent.futures.ThreadPoolExecutor` | Simpler API than asyncio when you don't need thousands of concurrent tasks |

**The GIL rule:** In CPython, threads do NOT run Python code in parallel. They interleave. Threads help only when tasks block on I/O (the GIL is released during I/O waits). For CPU parallelism, use processes.

## Structured Concurrency

**Never fire-and-forget async tasks.** Every task must be owned, awaited, and have its exceptions handled. Unowned tasks silently swallow exceptions and leak resources.

```python
# WRONG — fire-and-forget, exception lost, no cancellation
async def handle_request(data):
    asyncio.create_task(send_notification(data))  # Who awaits this? Who catches errors?
    return {"status": "ok"}

# RIGHT — structured concurrency with TaskGroup (Python 3.11+)
async def handle_request(data):
    async with asyncio.TaskGroup() as tg:
        result_task = tg.create_task(process(data))
        notify_task = tg.create_task(send_notification(data))
    # Both tasks guaranteed complete or cancelled here
    return {"status": "ok", "result": result_task.result()}
```

**TaskGroup guarantees:**
1. All tasks complete before the `async with` block exits
2. If any task raises, all sibling tasks are cancelled
3. The group re-raises the first exception (wrapped in `ExceptionGroup` if multiple)
4. No orphaned tasks, no silent failures

**When TaskGroup doesn't fit:** Background workers that outlive a single request (e.g., periodic health checks, queue consumers) are the exception. Use `asyncio.create_task` but store the reference and handle shutdown explicitly.

```python
class ServiceLifecycle:
    def __init__(self):
        self._background_tasks: set[asyncio.Task] = set()

    def start_background(self, coro):
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def shutdown(self):
        for task in self._background_tasks:
            task.cancel()
        await asyncio.gather(*self._background_tasks, return_exceptions=True)
```

## Shared State

**Default: no shared mutable state.** Pass data through function arguments and return values. When shared state is unavoidable, isolate it behind a synchronization boundary.

### Synchronization Primitives

| Primitive | Use When |
|-----------|----------|
| `asyncio.Lock` | Protecting a shared resource from concurrent async access |
| `asyncio.Semaphore` | Limiting concurrent access (connection pools, rate limiting) |
| `asyncio.Event` | Signaling between tasks (one-time or repeated) |
| `asyncio.Queue` | Producer-consumer communication between tasks |
| `threading.Lock` | Protecting shared state in threaded code |
| `queue.Queue` | Thread-safe producer-consumer (blocks on empty/full) |

```python
# Semaphore as rate limiter — max 5 concurrent API calls
_api_semaphore = asyncio.Semaphore(5)

async def call_api(payload: dict) -> dict:
    async with _api_semaphore:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            return response.json()
```

```python
# Queue for producer-consumer — decouples detection from processing
async def detection_pipeline(frames: AsyncIterator[Frame]) -> None:
    queue: asyncio.Queue[Frame | None] = asyncio.Queue(maxsize=10)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(producer(frames, queue))
        tg.create_task(consumer(queue))

async def producer(frames, queue):
    async for frame in frames:
        await queue.put(frame)
    await queue.put(None)  # sentinel

async def consumer(queue):
    while (frame := await queue.get()) is not None:
        await process_frame(frame)
```

### Thread Safety for Mixed Sync/Async

When calling sync code from async or vice versa:

```python
# Running blocking I/O in async context
async def read_large_file(path: str) -> bytes:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, Path(path).read_bytes)

# Running async code from sync context (entry points only)
def main():
    asyncio.run(async_main())  # Never nest asyncio.run()
```

## Common Anti-Patterns

### 1. Async function that blocks the event loop

```python
# WRONG — time.sleep blocks the entire event loop
async def poll_sensor():
    while True:
        data = read_sensor()
        time.sleep(1.0)  # Blocks ALL other tasks for 1 second

# RIGHT — use async sleep, offload blocking reads
async def poll_sensor():
    loop = asyncio.get_running_loop()
    while True:
        data = await loop.run_in_executor(None, read_sensor)
        await asyncio.sleep(1.0)
```

### 2. Missing cancellation handling

```python
# WRONG — ignores cancellation, cleanup never happens
async def stream_data(ws):
    while True:
        data = await ws.recv()
        process(data)

# RIGHT — handle cancellation for cleanup
async def stream_data(ws):
    try:
        while True:
            data = await ws.recv()
            process(data)
    except asyncio.CancelledError:
        await ws.close()
        raise  # Always re-raise CancelledError
```

### 3. Threads for CPU-bound work

```python
# WRONG — threads don't parallelize CPU work due to GIL
with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(compute_features, images))  # Still sequential!

# RIGHT — processes for CPU parallelism
with ProcessPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(compute_features, images))
```

### 4. Global mutable state without synchronization

```python
# WRONG — race condition: concurrent updates can lose writes
_cache = {}

async def get_user(user_id: str) -> User:
    if user_id not in _cache:
        _cache[user_id] = await fetch_user(user_id)  # Two tasks can fetch simultaneously
    return _cache[user_id]

# RIGHT — lock protects the check-then-write
_cache = {}
_cache_lock = asyncio.Lock()

async def get_user(user_id: str) -> User:
    async with _cache_lock:
        if user_id not in _cache:
            _cache[user_id] = await fetch_user(user_id)
        return _cache[user_id]
```

### 5. Nested asyncio.run()

```python
# WRONG — crashes with "This event loop is already running"
async def handler():
    result = asyncio.run(other_async_func())  # Cannot nest!

# RIGHT — just await
async def handler():
    result = await other_async_func()
```

## Testing Concurrent Code

### pytest-asyncio for async tests

```python
import pytest

@pytest.mark.asyncio
async def test_concurrent_fetches():
    async with asyncio.TaskGroup() as tg:
        task1 = tg.create_task(fetch_data("a"))
        task2 = tg.create_task(fetch_data("b"))
    assert task1.result() is not None
    assert task2.result() is not None
```

### Making async tests deterministic

```python
# Control timing with asyncio.Event instead of real delays
@pytest.mark.asyncio
async def test_producer_consumer():
    queue = asyncio.Queue(maxsize=1)
    produced = []
    consumed = []

    async def producer():
        for i in range(3):
            await queue.put(i)
            produced.append(i)

    async def consumer():
        for _ in range(3):
            item = await queue.get()
            consumed.append(item)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(producer())
        tg.create_task(consumer())

    assert consumed == [0, 1, 2]
```

### Testing with anyio for backend-agnostic code

```python
import anyio
import pytest

@pytest.mark.anyio
async def test_with_timeout():
    with anyio.fail_after(5.0):
        result = await slow_operation()
    assert result is not None
```

**Sources:** Python asyncio docs, Python concurrent.futures docs, Trio structured concurrency docs, pytest-asyncio docs, CPython GIL documentation
