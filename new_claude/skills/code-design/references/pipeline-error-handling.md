# Pipeline Error Handling & Failure Isolation

Per-item error boundary — one bad frame must not kill the stage. Always call `task_done()` in `finally`. Forward partial results downstream.

## Bulkhead: Per-Item Error Boundary

```python
# WRONG — exception kills entire stage
async def stage(in_q, out_q):
    while True:
        item = await in_q.get()
        result = await process(item)  # if this throws, stage dies
        await out_q.put(result)

# RIGHT — per-item isolation
async def stage(in_q, out_q, dlq):
    while True:
        item = await in_q.get()
        try:
            result = await asyncio.wait_for(process(item), timeout=2.0)
            await out_q.put(result)
        except Exception as exc:
            await dlq.put(DeadLetter(item, stage="ocr", error=exc))
        finally:
            in_q.task_done()  # ALWAYS — or q.join() deadlocks
```

## Circuit Breaker (Per-Stage Error Budget)

```
CLOSED → failure_rate >= 30% → OPEN → recovery_timeout → HALF_OPEN → probe succeeds → CLOSED
```

When circuit is OPEN, skip the stage and forward partial results. Detection without OCR is still useful for tracking.

## Partial Results Envelope

```python
class ResultQuality(Enum):
    FULL = "full"       # all stages succeeded
    DEGRADED = "degraded"  # optional stages failed
    FAILED = "failed"    # required stage failed

@dataclass
class PipelineItem:
    detections: list | None = None   # required
    ocr_text: dict | None = None     # optional
    stages_failed: dict[str, str] = field(default_factory=dict)
```

## AI Agent Mistakes

- `asyncio.TaskGroup` for long-lived stages (one crash cancels siblings)
- Retrying deterministic failures (`ValueError` — retrying won't help)
- Discarding entire result when one optional stage fails
- `task_done()` only on success path (deadlocks `q.join()`)

**Related:** `resilience-patterns.md` for full circuit breaker and retry implementations. `pipeline-metrics.md` for monitoring error rates per stage.

**Sources:** Azure Circuit Breaker Pattern, AWS Builders' Library, Bloomberg BlazingMQ Poison Pill Detection
