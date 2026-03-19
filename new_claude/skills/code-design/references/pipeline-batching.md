# Pipeline Dynamic Batching

Collect items until `max_batch_size` OR `timeout`, whichever comes first. Use per-item futures to maintain identity.

## Batch Collector

```python
async def batch_collector(queue, model_fn, max_batch=32, timeout_s=0.05):
    loop = asyncio.get_running_loop()
    while True:
        batch, futures = [], []
        first_item, first_fut = await queue.get()
        batch.append(first_item); futures.append(first_fut)

        deadline = loop.time() + timeout_s
        while len(batch) < max_batch:
            remaining = deadline - loop.time()
            if remaining <= 0: break
            try:
                item, fut = await asyncio.wait_for(queue.get(), timeout=remaining)
                batch.append(item); futures.append(fut)
            except asyncio.TimeoutError: break

        results = await asyncio.to_thread(model_fn, batch)
        for fut, result in zip(futures, results):
            fut.set_result(result)
```

## Batch Size vs Latency

| Goal | timeout_s | max_batch_size |
|------|-----------|----------------|
| Minimize p50 latency | 5–20ms | Small (4–8) |
| Maximize throughput | 100–500ms | Large (32–64) |
| Balance (production default) | 50ms | 8–32 |

GPU batching is "free" until the roofline: `batch_threshold ≈ PeakFLOPS / (2 × MemBW)`.

## AI Agent Mistakes

- Independent timeouts per `queue.get()` (total wait compounds across items)
- Blocking inference on event loop (use `asyncio.to_thread` for CPU/GPU work)
- Global maximum padding for variable-length inputs (wastes memory/compute)

**Related:** `concurrency-patterns.md` for `asyncio.to_thread` and process pool patterns. `pipeline-backpressure.md` for queue sizing upstream of the batch collector.

**Sources:** NVIDIA Triton Dynamic Batching, Ray Serve Dynamic Request Batching, InferLine ML Inference Pipeline Composition
