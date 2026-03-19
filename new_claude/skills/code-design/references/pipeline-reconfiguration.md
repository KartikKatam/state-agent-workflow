# Pipeline Runtime Reconfiguration

Load the replacement before touching the live stage. Drain in-flight items before swapping. Feature flags are cheaper than graph mutations.

## Hot-Swap via Double Buffer

```python
class HotSwappableStage:
    def __init__(self, initial_fn):
        self._current = initial_fn
        self._swap_lock = asyncio.Lock()
        self._drain_event = asyncio.Event()
        self._drain_event.set()
        self._active = 0

    async def process(self, item):
        fn = self._current  # read once — no lock needed
        self._active += 1
        self._drain_event.clear()
        try:
            return await fn(item)
        finally:
            self._active -= 1
            if self._active == 0:
                self._drain_event.set()

    async def swap(self, new_fn):
        async with self._swap_lock:
            await self._drain_event.wait()  # drain in-flight
            self._current = new_fn          # atomic reference swap
```

## Feature Flags (Pass-Through Pattern)

```python
class FeatureFlaggedStage:
    def __init__(self, flag_getter, fn):
        self._flag = flag_getter
        self._fn = fn

    async def __call__(self, item):
        if not self._flag():
            return item  # pass-through — nanosecond cost
        return await self._fn(item)
```

## Graceful Drain: Three Phases

1. **Stop ingress** — set `_accepting = False`
2. **Drain** — `await asyncio.wait_for(drain_event.wait(), timeout=5.0)`
3. **Reconfigure** — swap function/model reference

## AI Agent Mistakes

- Swapping while items are in-flight (partial processing by two models)
- Removing stages from graph on toggle (expensive drain every time)
- No timeout on drain (deadlock risk)
- Using `threading.Lock` inside asyncio coroutines (blocks event loop)

**Related:** `concurrency-patterns.md` for async Lock/Event usage. `pipeline-error-handling.md` for handling failures during reconfiguration.

**Sources:** Game Programming Patterns (Double Buffer), GStreamer Pipeline Manipulation
