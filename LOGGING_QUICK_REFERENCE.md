# Logging Quick Reference Card

## Setup (1 line)

```python
from omniai.logging import get_logger
logger = get_logger(__name__)
```

## Common Logs

```python
# Success
logger.info("✅ Operation completed")

# Error
logger.error("❌ Operation failed: details")

# Warning
logger.warning("⚠️  Operation degraded: details")

# Debug
logger.debug("📊 State: key=value | key2=value2")

# Performance
logger.warning(f"⚠️  Slow operation: {duration_ms:.1f}ms (threshold: {threshold}ms)")
```

## Operation Tracing

```python
from omniai.logging import trace_operation

with trace_operation(logger, "Operation name", {"context": "value"}):
    # Do operation
    pass
# Auto-logs: ⏳ Starting, ✅ Completed (0.234s), or ❌ Failed
```

## Error with Context

```python
from omniai.logging import EngineError, ErrorMessages, Suggestions

try:
    operation()
except Exception as exc:
    error = EngineError(
        ErrorMessages.ENGINE_TIMEOUT.format(timeout=30),
        context="Details about what happened",
        suggestion=Suggestions.INCREASE_TIMEOUT,
    )
    logger.error(str(error))
    raise
```

## Request/Response Logging

```python
from omniai.logging import log_request, log_response
import time

log_request(logger, "POST", "/v1/messages", request_id="123")
start = time.time()

# Process...

log_response(logger, "/v1/messages", 200, (time.time() - start) * 1000, request_id="123")
```

## State Logging

```python
from omniai.logging import log_state

log_state(logger, "Engine", {
    "process_alive": True,
    "breaker_state": "closed",
    "in_flight": 3,
})
```

## Performance Metric

```python
from omniai.logging import log_performance_metric

log_performance_metric(
    logger,
    "Chat latency",
    duration_ms,
    unit="ms",
    threshold=2000,  # Warn if exceeds
)
```

## Error Classes

```python
from omniai.logging import (
    EngineError,          # Engine problems
    ModelError,           # Model problems
    NetworkError,         # Network problems
    DatabaseError,        # Database problems
    ValidationError,      # Validation problems
    ConfigurationError,   # Config problems
)
```

## Emoji Reference

| Emoji | Meaning |
|-------|---------|
| ✅ | Success |
| ❌ | Error |
| ⚠️ | Warning/Slow |
| 🔧 | Configuration |
| 🚀 | Starting |
| 🛑 | Stopping |
| ⏳ | In progress |
| 📊 | State/Metrics |
| 📞 | Function call |
| 📤 | Outgoing request |
| 📬 | Incoming request |
| 🔍 | Debugging |

## Common Messages

```python
from omniai.logging import ErrorMessages, Suggestions

# Use these instead of hardcoding
ErrorMessages.ENGINE_UNAVAILABLE
ErrorMessages.CONNECTION_FAILED
ErrorMessages.INVALID_CONFIG
ErrorMessages.TOKEN_LIMIT_EXCEEDED
ErrorMessages.PROCESS_DIED

Suggestions.CHECK_CONNECTIVITY
Suggestions.INCREASE_TIMEOUT
Suggestions.RESTART_ENGINE
Suggestions.CHECK_GPU
Suggestions.RUN_MIGRATIONS
```

## Testing Logs

```python
def test_operation(caplog):
    import logging
    with caplog.at_level(logging.INFO):
        perform_operation()

    # Check logs
    assert any("expected text" in r.message for r in caplog.records)
```

## Configure at Startup

```python
from omniai.logging import configure_logging
import logging

configure_logging(level=logging.INFO)  # or DEBUG, WARNING
```

## Suppress Noisy Modules

```python
import logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
```
