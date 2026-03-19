# Observability Testing

Patterns for asserting on logs, traces, and internal decisions.

## caplog (stdlib logging)

```python
def test_decision_logged(caplog):
    with caplog.at_level(logging.DEBUG):
        result = function_under_test(input_data)
    assert "rejected candidate" in caplog.text
    assert "quality=0.3 < threshold=0.5" in caplog.text
```

## structlog

```python
from structlog.testing import capture_logs

def test_event_logged():
    with capture_logs() as logs:
        system.process(input)
    completed = [l for l in logs if l["event"] == "processing_complete"]
    assert len(completed) == 1
```

## Log Collector Fixture

```python
@pytest.fixture
def log_collector(caplog):
    """Structured log assertion helper."""
    caplog.set_level(logging.DEBUG)

    class LogCollector:
        @property
        def messages(self):
            return [r.message for r in caplog.records]

        def has_event(self, event_name: str) -> bool:
            return any(event_name in m for m in self.messages)

        @property
        def errors(self):
            return [r.message for r in caplog.records if r.levelno >= logging.ERROR]

    return LogCollector()
```

## When to Use Log Assertions

- Decision points where code chooses between paths
- Silent skips, fallbacks, error recovery
- Pipeline stage transitions
- Performance-sensitive timing
