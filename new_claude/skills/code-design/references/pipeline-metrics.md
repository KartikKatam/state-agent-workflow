# Pipeline Per-Stage Observability

Combine RED (Rate, Errors, Duration) and USE (Utilization, Saturation, Errors) per stage. Embed correlation IDs in the item, not thread-locals.

## What to Measure

```
stage.items_processed_total        # Counter
stage.items_dropped_total          # Counter (separate from errors!)
stage.errors_total                 # Counter
stage.processing_duration_seconds  # Histogram (p50/p95/p99)
stage.queue_depth                  # Gauge
pipeline.end_to_end_latency_seconds  # Histogram
```

## Sampled Instrumentation

```python
# Sample every 10th item for expensive metrics (OTel spans, structured logs)
# Count ALL items with cheap Prometheus counters
self._item_count += 1
ITEMS_PROCESSED.labels(stage=self.name).inc()  # always
if self._item_count % 10 == 0:
    PROCESSING_DURATION.labels(stage=self.name).observe(elapsed_s)
```

## Correlation ID in Item Wrapper

```python
@dataclass
class PipelineItem:
    payload: Any
    item_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    entry_ns: int = field(default_factory=time.perf_counter_ns)
```

Use `time.perf_counter_ns()` — integer nanoseconds, no float precision loss. Never `time.time()`.

## Bottleneck Detection

| State | Queue Depth | Utilization | Diagnosis |
|-------|-------------|-------------|-----------|
| Healthy | Stable | Moderate | Normal |
| **Saturated** | Growing | > 90% | This stage is the bottleneck |
| **Starved** | Empty | < 20% | Upstream is the bottleneck |

## AI Agent Mistakes

- Creating OTel spans on every item at 30fps (massive overhead)
- Using `time.time()` for latency (float precision loss at sub-ms)
- Adding `item_id` as a Prometheus label (cardinality explosion)
- Measuring only end-to-end latency without per-stage breakdown

**Related:** `logging-patterns.md` for structured logging and correlation IDs. `pipeline-backpressure.md` for interpreting queue depth signals.

**Sources:** Brendan Gregg USE Method, OpenTelemetry Python Instrumentation, Prometheus Naming Best Practices
