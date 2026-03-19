# Pipeline Design Patterns — Routing Guide

Patterns for designing multi-stage data processing pipelines — real-time CV/ML inference, ETL, streaming analytics. Python 3.11+, asyncio, stdlib-first.

**This is a routing file.** Each section summarizes a pattern area and points to its deep-dive. Load only the deep-dive relevant to your current task.

## Which Pattern Do You Need?

| Situation | Load |
|-----------|------|
| Designing stage boundaries, typed I/O contracts, composition (linear/fan-out/fan-in) | `pipeline-stage-isolation.md` |
| Queue sizing, drop policies, preventing memory explosion under load | `pipeline-backpressure.md` |
| Frame buffer pools, shared memory, ring buffers, zero-copy | `pipeline-buffers.md` |
| Per-stage RED/USE metrics, correlation IDs, bottleneck detection | `pipeline-metrics.md` |
| Hot-swapping models/stages, feature flags, graceful drain | `pipeline-reconfiguration.md` |
| Per-item error boundaries, circuit breakers, dead letter queues, partial results | `pipeline-error-handling.md` |
| GPU batch collection, batch size vs latency tradeoffs | `pipeline-batching.md` |
| Testing stages in isolation, backpressure tests, deadlock guards | `pipeline-testing.md` |

## Pattern Summaries

**Stage Isolation** — Each stage implements a typed `Stage[InputT, OutputT]` Protocol with `process`, `startup`, `shutdown`, `healthy`. Stages communicate only through bounded queues. Use `frozen=True` dataclasses for inter-stage data. Four composition shapes: linear, fan-out, fan-in, conditional.

**Backpressure** — Always set `maxsize` on queues. Real-time pipelines drop oldest frames at the ingestion boundary. Batch pipelines block the producer. Never drop between stateful stages (e.g., tracker needs continuity). Formula: `end_to_end_latency = queue_depth × processing_time_per_item`.

**Buffer Management** — Pre-allocate frame buffers in a pool. Copy at ingestion boundaries, share references through stages. Use `multiprocessing.shared_memory` for cross-process zero-copy. Ring buffers for frame history. Pool sizing: `pipeline_depth × max_in_flight_per_stage + 2`.

**Per-Stage Observability** — Measure RED (Rate, Errors, Duration) + USE (Utilization, Saturation, Errors) per stage. Sample expensive metrics (OTel spans) every Nth item; count all items with cheap counters. Use `time.perf_counter_ns()`, never `time.time()`. Embed correlation IDs in the item, not thread-locals.

**Runtime Reconfiguration** — Hot-swap via double buffer: load replacement, drain in-flight items, atomic reference swap. Feature flags as pass-through stages (nanosecond cost when disabled). Three-phase graceful drain: stop ingress → drain → reconfigure.

**Error Handling** — Per-item error boundary with `try/except/finally` and `task_done()` in `finally`. Dead letter queue for failed items. Circuit breaker per stage (30% failure rate threshold). Partial results envelope: `FULL | DEGRADED | FAILED` quality levels. Never use `TaskGroup` for long-lived stages.

**Dynamic Batching** — Batch collector fills until `max_batch_size` OR `timeout`, whichever first. Per-item futures maintain identity. GPU batching is "free" until the roofline. Production default: 50ms timeout, 8–32 batch size. Use `asyncio.to_thread` for blocking inference.

**Testing** — Test stages as pure functions with `AsyncMock` dependencies. Test backpressure with bounded queues and `wait_for`. Every integration test needs a timeout guard (deadlocks hang CI). Use deterministic seeded frame generation, `looptime` for time compression.

## Pipeline Architecture Decision Matrix

| Pipeline Shape | When to Use | Backpressure | Error Strategy |
|---------------|-------------|--------------|----------------|
| **Linear** (A→B→C) | Sequential dependencies | Bounded queue per edge | Drop item, continue |
| **Fan-out** (A→B,C) | Same data, parallel processing | Slowest branch blocks | Per-branch circuit breaker |
| **Fan-in** (A,B→C) | Merge parallel results | Accumulate by ID + timeout | Partial results OK |
| **Conditional** | Content-based routing | Per-branch queues | Default branch for unmatched |
| **Batched stage** | GPU inference | Batch collector with timeout | Per-item future resolution |
| **Skip-N** | Fixed rate mismatch | Producer-side subsampling | No error — deterministic drop |

## Common Pipeline Anti-Patterns (Quick Scan)

| Anti-Pattern | Why It's Wrong | Deep-Dive |
|-------------|----------------|-----------|
| Unbounded `asyncio.Queue()` | Memory explosion under load | `pipeline-backpressure.md` |
| Dropping between stateful stages | Corrupts tracker/accumulator state | `pipeline-backpressure.md` |
| "Mega-stage" doing multiple concerns | Untestable, can't isolate failures | `pipeline-stage-isolation.md` |
| Shared mutable state between stages | Race conditions, invisible coupling | `pipeline-stage-isolation.md` |
| Per-frame buffer allocation | GC jitter at 30fps, latency spikes | `pipeline-buffers.md` |
| `time.time()` for latency measurement | Float precision loss at sub-ms | `pipeline-metrics.md` |
| OTel spans on every item at 30fps | Massive overhead, drowns signal | `pipeline-metrics.md` |
| `item_id` as Prometheus label | Cardinality explosion | `pipeline-metrics.md` |
| `threading.Lock` in async code | Blocks the entire event loop | `pipeline-reconfiguration.md` |
| Swap while items in-flight | Partial processing by two models | `pipeline-reconfiguration.md` |
| `TaskGroup` for long-lived stages | One crash cancels all siblings | `pipeline-error-handling.md` |
| `task_done()` only on success path | Deadlocks `q.join()` | `pipeline-error-handling.md` |
| Blocking inference on event loop | Starves all other async tasks | `pipeline-batching.md` |
| No timeout on pipeline integration tests | Deadlocks hang CI indefinitely | `pipeline-testing.md` |

## Sources

- [NVIDIA DeepStream SDK Architecture](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_Overview.html)
- [NVIDIA Triton Dynamic Batching](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/batcher.html)
- [GStreamer Pads and Capabilities](https://gstreamer.freedesktop.org/documentation/application-development/basics/pads.html)
- [InferLine: ML Inference Pipeline Composition](https://arxiv.org/abs/1812.01776v1)
- [Armin Ronacher — I'm not feeling the async pressure](https://lucumr.pocoo.org/2020/1/1/async-pressure/)
- [Reactive Streams Specification](https://www.reactive-streams.org/)
- [Brendan Gregg: The USE Method](https://www.brendangregg.com/usemethod.html)
- [Azure Circuit Breaker Pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker)
- [AWS Builders' Library: Timeouts, Retries, Backoff with Jitter](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/)
- [Ray Serve Dynamic Request Batching](https://docs.ray.io/en/latest/serve/advanced-guides/dyn-req-batch.html)
- [Game Programming Patterns (Double Buffer)](https://gameprogrammingpatterns.com/double-buffer.html)
- [PyTorch Multiprocessing Best Practices](https://docs.pytorch.org/docs/stable/notes/multiprocessing.html)
- [Bloomberg BlazingMQ Poison Pill Detection](https://bloomberg.github.io/blazingmq/docs/features/poison_pill_detection/)
- [OpenTelemetry Python Instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/)
- [pytest-asyncio Documentation](https://pypi.org/project/pytest-asyncio/)
- [looptime](https://github.com/nolar/looptime)
