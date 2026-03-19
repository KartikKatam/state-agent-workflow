# Pipeline Buffer Management

Copy at ingestion boundaries, share references through stages. Pre-allocate in the hot path.

## Memory Pool for Frame Buffers

```python
class FrameBufferPool:
    def __init__(self, pool_size: int, shape: tuple, dtype=np.uint8):
        self._pool: queue.Queue = queue.Queue()
        for _ in range(pool_size):
            self._pool.put(np.zeros(shape, dtype=dtype))

    @contextmanager
    def borrow(self):
        buf = self._pool.get(timeout=1.0)
        try:
            yield buf
        finally:
            self._pool.put(buf)
```

Pool sizing: `pool_size = pipeline_depth × max_in_flight_per_stage + 2`.

## Cross-Process Shared Memory

```python
from multiprocessing import shared_memory
shm = shared_memory.SharedMemory(create=True, size=frame_nbytes)
frame = np.ndarray(SHAPE, dtype=DTYPE, buffer=shm.buf)
# Consumer: attach by name, zero-copy
existing = shared_memory.SharedMemory(name=shm.name)
view = np.ndarray(SHAPE, dtype=DTYPE, buffer=existing.buf)
```

Use `SharedMemoryManager` context manager for automatic cleanup. Only owner calls `.unlink()`.

## Ring Buffer for Frame History

```python
class FrameRingBuffer:
    def __init__(self, capacity: int, frame_shape: tuple):
        self._storage = np.zeros((capacity, *frame_shape), dtype=np.uint8)
        self._head = 0
        self._count = 0

    def push(self, frame: np.ndarray) -> None:
        np.copyto(self._storage[self._head], frame)
        self._head = (self._head + 1) % len(self._storage)
        self._count = min(self._count + 1, len(self._storage))
```

## AI Agent Mistakes

- Allocating frame buffers per-frame (GC jitter at 30fps)
- `np.array(frame)` everywhere creating silent copies
- `pin_memory().to(device)` — redundant, actually slower
- Using `dtype=object` in shared memory

**Related:** `concurrency-patterns.md` for shared memory and cross-process patterns. `pipeline-metrics.md` for monitoring buffer pool utilization.

**Sources:** PyTorch Multiprocessing Best Practices, Game Programming Patterns (Double Buffer)
