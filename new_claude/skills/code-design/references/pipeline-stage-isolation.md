# Pipeline Stage Isolation & Composition

Each stage is a self-contained unit with typed input/output. Stages communicate only through bounded queues — never shared mutable state.

## Stage Protocol

```python
from typing import Protocol, TypeVar, Generic, runtime_checkable

InputT = TypeVar("InputT", contravariant=True)
OutputT = TypeVar("OutputT", covariant=True)
_SENTINEL = object()

@runtime_checkable
class Stage(Protocol[InputT, OutputT]):
    async def process(self, item: InputT) -> OutputT: ...
    async def startup(self) -> None: ...
    async def shutdown(self) -> None: ...
    def healthy(self) -> bool: ...
```

## Typed Stage I/O

Use `frozen=True` dataclasses for stage I/O — prevents mutation after creation, safe to share across fan-out:

```python
@dataclass(frozen=True)
class DetectionFrame:
    frame_id: int
    timestamp_ns: int
    detections: tuple[Detection, ...]  # tuple, not list — immutable
```

## Composition Patterns

| Pattern | Use Case | Backpressure Behavior |
|---------|----------|----------------------|
| **Linear** (A → B → C) | Sequential processing | Full queue blocks upstream |
| **Fan-out** (A → B, C) | Same frame to multiple processors | Slowest branch blocks all |
| **Fan-in** (A, B → C) | Merge parallel results | Accumulate by `frame_id` |
| **Conditional** (A → B or C) | Route by content | First matching predicate wins |

```python
# Type-safe pipeline composition via >> operator
pipeline = TypedPipeline(DetectionStage()) >> TrackingStage() >> OCRStage()
```

## AI Agent Mistakes

- Creating "mega-stages" that do detection + tracking + OCR in one function
- Coupling stages via direct method calls instead of queues
- Using unbounded `asyncio.Queue()` between stages
- Sharing mutable state between stages instead of passing immutable data through queues

**Related:** `pipeline-backpressure.md` for queue sizing between stages. `concurrency-patterns.md` for async task management within stages.

**Sources:** PEP 544 (Protocols), GStreamer Pads and Capabilities, NVIDIA DeepStream SDK Architecture
