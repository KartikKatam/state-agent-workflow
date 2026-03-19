# Testing Pipelines

## Stage Isolation: Pure Function + AsyncMock

```python
@pytest.mark.asyncio
async def test_detection_stage(fake_frame):
    model = AsyncMock()
    model.infer.return_value = [((0.1, 0.2, 0.5, 0.8), 0.91, 0)]
    stage = DetectionStage(model=model)
    results = await stage.process(fake_frame)
    assert results[0].frame_id == fake_frame.frame_id
```

## Backpressure Testing

```python
@pytest.mark.asyncio
async def test_queue_blocks_when_full():
    q = asyncio.Queue(maxsize=3)
    for i in range(3): await q.put(i)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.put(99), timeout=0.01)
```

## Deadlock Guard (Required for All Pipeline Tests)

```python
result = await asyncio.wait_for(pipeline.process_all(frames), timeout=5.0)
```

Every pipeline integration test must have a timeout. Deadlocks hang CI indefinitely without this.

## Deterministic Frame Generation

```python
async def deterministic_frames(count=10, seed=42):
    rng = np.random.default_rng(seed=seed)
    return [Frame(frame_id=i, image=rng.integers(0, 256, (480,640,3), dtype=np.uint8))
            for i in range(count)]
```

## Useful Testing Tools

- `looptime` — compress `asyncio.sleep()` in tests (no real waiting)
- `freezegun` — control wall-clock time
- `hypothesis` — property-based stage contract testing

## AI Agent Mistakes

- Testing with real camera/GPU (non-deterministic, slow, flaky)
- `asyncio.Queue()` with no maxsize in tests (hides backpressure bugs)
- No timeout guard on integration tests (deadlock hangs CI)
- `asyncio.run()` inside an async test (crashes: "event loop already running")

**Related:** `concurrency-patterns.md` for pytest-asyncio patterns and async test setup. `pipeline-error-handling.md` for testing failure isolation and dead letter queues.

**Sources:** pytest-asyncio docs, looptime docs
