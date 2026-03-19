# Pipeline Backpressure & Flow Control

Unbounded queues are a time bomb. Always set `maxsize`. For real-time: drop stale data. For batch: block the producer.

## Bounded Queues

```python
# WRONG — memory explosion under overload
queue = asyncio.Queue()

# RIGHT — bounded queue provides natural backpressure
queue = asyncio.Queue(maxsize=5)
```

## Drop-Oldest for Real-Time

```python
async def put_drop_oldest(q: asyncio.Queue, item: object) -> None:
    if q.full():
        try:
            q.get_nowait()  # discard stale
        except asyncio.QueueEmpty:
            pass
    await q.put(item)
```

## Queue Sizing

```
end_to_end_latency = queue_depth × processing_time_per_item
```

| Pipeline Type | maxsize | Drop Policy |
|--------------|---------|-------------|
| Real-time (camera → ML) | 1–2 | Drop oldest |
| Near-real-time | 2–5 | Drop oldest with metrics |
| Batch (file → analysis) | 15–30 | Block producer |

## Stateful Stages Must Not Drop

Tracking is stateful — dropping detection results corrupts track state. Only drop at the pipeline ingestion boundary (camera → first queue), not between stateful stages.

```python
# Camera thread: drop-oldest OK
# Detection → Tracking queue: blocking — tracker needs continuity
track_q: asyncio.Queue = asyncio.Queue(maxsize=10)
await track_q.put(detection)  # blocks if tracker can't keep up
```

## AI Agent Mistakes

- Using `asyncio.Queue()` with no maxsize (memory explosion under load)
- Blocking camera threads (stale frames accumulate, latency spikes)
- Dropping between stateful stages (corrupts state like trackers)
- Not measuring drop rate (silent data loss)

**Related:** `pipeline-metrics.md` for measuring drop rates and queue depth. `pipeline-stage-isolation.md` for queue placement between stages.

**Sources:** Armin Ronacher "I'm not feeling the async pressure", Reactive Streams Specification, RabbitMQ Queuing Theory
