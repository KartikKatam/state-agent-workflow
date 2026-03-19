# Code Design: Robotics-CV Specialization

Domain-specific design patterns for robotics and computer vision projects. Extends the core code-design skill with patterns for multi-stage processing pipelines, real-time data flow, buffer management, and GPU inference batching.

---

## Quick Reference (additions to core skill)

| Situation | Action |
|-----------|--------|
| Designing a multi-stage pipeline | Typed Stage Protocol, bounded queues between stages, immutable inter-stage data. See `references/pipeline-patterns.md` |
| Choosing queue size / drop policy | Real-time: maxsize 1–2, drop oldest. Batch: maxsize 15–30, block producer. See `references/pipeline-backpressure.md` |
| Adding a GPU/model inference stage | Dynamic batch collector with timeout + max size. See `references/pipeline-batching.md` |
| Managing frame buffers in hot path | Pre-allocate in a pool, borrow/return pattern. See `references/pipeline-buffers.md` |

## Pipeline Design Principle

**Design pipelines as isolated, composable stages.**
Multi-stage processing (detection → tracking → OCR → aggregation) requires each stage to be a self-contained unit with typed input/output, communicating only through bounded queues. Use `frozen=True` dataclasses for inter-stage data. Always set `maxsize` on queues — unbounded queues are a memory time bomb. For real-time: drop oldest at the ingestion boundary. For batch: block the producer. Each stage needs its own error boundary — one bad item must not kill the stage.

See `references/pipeline-patterns.md` for the routing guide to all pipeline patterns — it points to focused deep-dives for each pattern area:

| Pattern Area | Deep-Dive File |
|-------------|----------------|
| Stage boundaries, typed I/O, composition | `references/pipeline-stage-isolation.md` |
| Queue sizing, drop policies, backpressure | `references/pipeline-backpressure.md` |
| Frame buffer pools, shared memory, ring buffers | `references/pipeline-buffers.md` |
| Per-stage RED/USE metrics, bottleneck detection | `references/pipeline-metrics.md` |
| Hot-swapping models, feature flags, drain | `references/pipeline-reconfiguration.md` |
| Per-item error boundaries, circuit breakers | `references/pipeline-error-handling.md` |
| GPU batch collection, latency tradeoffs | `references/pipeline-batching.md` |
| Testing stages, backpressure, deadlock guards | `references/pipeline-testing.md` |

## Verification Checks (additions to Step 5)

- [ ] **Pipeline stages are isolated.** Each stage has typed input/output, communicates only through bounded queues. No shared mutable state between stages.
- [ ] **Queues are bounded.** Every `asyncio.Queue` has `maxsize` set. Drop policy is defined for real-time paths. Stateful stages (trackers) use blocking queues, not drop-oldest.
- [ ] **Frame buffers are pooled.** No per-frame allocation in the hot path. Buffers are borrowed from a pre-allocated pool and returned after use.
- [ ] **Each stage has its own error boundary.** Per-item try/except with `task_done()` in `finally`. One bad frame does not kill the stage.
