# OmniAI Structured Logging & Error Handling Guide

## Overview

OmniAI uses comprehensive structured logging and user-friendly error messages throughout the codebase. This guide explains how to add traces and error handling to any module.

---

## Log Levels & Emoji Indicators

All logs use emoji prefixes for quick visual scanning:

| Emoji | Level | Usage | Example |
|-------|-------|-------|---------|
| ✅ | INFO | Success completion | `✅ Engine started successfully` |
| ❌ | ERROR | Error/failure | `❌ Model inference failed` |
| ⚠️ | WARNING | Degraded state/slowness | `⚠️ Request took 2000ms (threshold: 1000ms)` |
| 🔧 | INFO | Configuration/setup | `🔧 Creating ModelEngine` |
| 🚀 | INFO | Starting operations | `🚀 Spawning backend process` |
| 🛑 | INFO | Stopping operations | `🛑 Stopping engine` |
| ⏳ | INFO | In-progress operations | `⏳ Starting engine` |
| 📊 | DEBUG | State/metrics | `📊 Engine health: process=true server=true` |
| 📞 | DEBUG | Function calls | `📞 Calling engine.chat_text(...)` |
| 📤 | DEBUG | Outgoing requests | `📤 Posting to /v1/chat/completions` |
| 📬 | INFO | Incoming requests | `📬 POST /v1/messages` |
| 🔍 | DEBUG | Detailed tracing | `🔍 Extracting JSON from response` |
| 💡 | INFO | Suggestions | `💡 Try: Increase request timeout` |

---

## Core Components

### 1. Structured Logging Setup

```python
from omniai.logging import get_logger, configure_logging

# Configure logging at app startup
configure_logging(level=logging.INFO)

# Get logger for your module
logger = get_logger(__name__)

# Now use logger throughout the module
logger.info("✅ Component initialized")
logger.debug("📊 Component state: running")
logger.error("❌ Error occurred: details")
```

### 2. Error Messages

All errors include three components:

```python
from omniai.logging import OmniAIError, EngineError, ErrorMessages, Suggestions

# Example: Engine startup failure
try:
    engine.start()
except Exception as e:
    error = EngineError(
        message=ErrorMessages.ENGINE_UNAVAILABLE,
        context=f"Failed to connect to {url}",
        suggestion=Suggestions.CHECK_CONNECTIVITY,
    )
    logger.error(str(error))
    raise
```

**Output:**
```
❌ Engine is currently unavailable
   Context: Failed to connect to http://localhost:8000
   💡 Try: Verify internet connection and network configuration
```

### 3. Trace Operations

Use context managers for operation tracing:

```python
from omniai.logging import trace_operation, trace_performance

# Trace operation with timing
with trace_operation(logger, "Loading model", {"model": "llama-7b"}):
    model = load_model("llama-7b")
    # Logs: ⏳ Starting: Loading model [model=llama-7b]
    # ... operation runs ...
    # Logs: ✅ Completed: Loading model [model=llama-7b] (0.234s)

# Performance monitoring
with trace_performance(logger, "Inference", threshold_ms=500):
    result = model.infer(prompt)
    # Logs: ⏱️  Timing: Inference
    # If > 500ms: ⚠️  Slow operation: Inference took 750.2ms
```

---

## Error Types & Messages

### Available Error Classes

```python
from omniai.logging import (
    OmniAIError,           # Base class
    ConfigurationError,    # Config validation
    EngineError,          # Engine operations
    ModelError,           # Model inference
    GuardrailError,       # Security checks
    DatabaseError,        # Database ops
    NetworkError,         # Network ops
    ProcessError,         # Process management
    ValidationError,      # Data validation
)
```

### Standard Error Messages

Error messages are organized by category:

```python
from omniai.logging import ErrorMessages, Suggestions

# Configuration errors
ErrorMessages.INVALID_CONFIG
ErrorMessages.MISSING_REQUIRED_FIELD
ErrorMessages.DATABASE_URL_INVALID

# Engine errors
ErrorMessages.ENGINE_START_FAILED
ErrorMessages.ENGINE_UNAVAILABLE
ErrorMessages.ENGINE_TIMEOUT
ErrorMessages.PROCESS_SPAWN_FAILED
ErrorMessages.MAX_RESTARTS_EXCEEDED

# Model errors
ErrorMessages.MODEL_NOT_FOUND
ErrorMessages.INFERENCE_FAILED
ErrorMessages.RESPONSE_INVALID
ErrorMessages.TOKEN_LIMIT_EXCEEDED

# Network errors
ErrorMessages.CONNECTION_FAILED
ErrorMessages.CONNECTION_TIMEOUT
ErrorMessages.REQUEST_FAILED
ErrorMessages.RATE_LIMITED

# Database errors
ErrorMessages.CONNECTION_ERROR
ErrorMessages.QUERY_FAILED
ErrorMessages.MIGRATION_FAILED

# Validation errors
ErrorMessages.INVALID_JSON
ErrorMessages.SCHEMA_MISMATCH
ErrorMessages.TYPE_MISMATCH
ErrorMessages.VALUE_OUT_OF_RANGE

# Suggestions
Suggestions.CHECK_ENV_VARS
Suggestions.RESTART_ENGINE
Suggestions.CHECK_CONNECTIVITY
Suggestions.RUN_MIGRATIONS
Suggestions.INCREASE_TIMEOUT
```

---

## Implementation Examples

### Example 1: Module Setup

```python
# omniai/mymodule/component.py
from omniai.logging import get_logger, trace_operation, ComponentError, ErrorMessages

logger = get_logger(__name__)

class MyComponent:
    def __init__(self, config):
        logger.info(f"🔧 Initializing MyComponent | config={config}")
        self.config = config

    def start(self):
        with trace_operation(logger, "Starting MyComponent"):
            try:
                self._do_startup()
                logger.info("✅ MyComponent started")
            except Exception as exc:
                error = ComponentError(
                    ErrorMessages.START_FAILED,
                    context=str(exc),
                    suggestion=Suggestions.CHECK_CONFIG,
                )
                logger.error(str(error))
                raise

    def process(self, data):
        logger.debug(f"📞 Processing data | size={len(data)}")
        try:
            result = self._process_impl(data)
            logger.debug(f"✅ Processing complete | result_size={len(result)}")
            return result
        except Exception as exc:
            logger.error(f"❌ Processing failed: {type(exc).__name__}: {exc}")
            raise
```

### Example 2: Error Handling

```python
from omniai.logging import ModelError, ErrorMessages, Suggestions

async def infer(model, prompt, max_tokens=100):
    logger.debug(f"📞 Calling model.infer | prompt_len={len(prompt)}")

    try:
        # Check token limit
        if len(prompt) + max_tokens > model.max_tokens:
            error = ModelError(
                ErrorMessages.TOKEN_LIMIT_EXCEEDED.format(
                    tokens=len(prompt) + max_tokens,
                    limit=model.max_tokens
                ),
                context=f"Prompt: {len(prompt)} tokens, Max tokens: {max_tokens}",
                suggestion=Suggestions.REDUCE_BATCH_SIZE,
            )
            logger.error(str(error))
            raise ValueError(str(error))

        # Call model
        with trace_operation(logger, "Model inference"):
            result = await model.generate(prompt, max_tokens=max_tokens)
            logger.debug(f"✅ Inference returned: {len(result)} tokens")
            return result

    except httpx.TimeoutException as exc:
        error = ModelError(
            ErrorMessages.INFERENCE_FAILED,
            context=f"Request timed out after {timeout}s",
            suggestion=Suggestions.INCREASE_TIMEOUT,
        )
        logger.error(str(error))
        raise

    except json.JSONDecodeError as exc:
        error = ModelError(
            ErrorMessages.RESPONSE_INVALID,
            context=f"Failed to parse model response: {exc}",
            suggestion=Suggestions.CHECK_CONNECTIVITY,
        )
        logger.error(str(error))
        raise
```

### Example 3: Request/Response Logging

```python
from omniai.logging import log_request, log_response

async def handle_request(request):
    request_id = str(uuid.uuid4())
    start_time = time.time()

    # Log incoming request
    log_request(
        logger,
        method=request.method,
        endpoint=request.url.path,
        client_id=request.headers.get("X-Client-ID"),
        request_id=request_id,
    )

    try:
        # Process request
        response = await process(request)
        status_code = response.status_code

    except Exception as exc:
        logger.error(f"❌ Request failed: {exc}")
        status_code = 500

    finally:
        # Log outgoing response
        duration_ms = (time.time() - start_time) * 1000
        log_response(
            logger,
            endpoint=request.url.path,
            status_code=status_code,
            duration_ms=duration_ms,
            request_id=request_id,
        )

        return response
```

### Example 4: State Logging

```python
from omniai.logging import log_state

def update_engine_state(engine, new_state):
    logger.debug(f"📊 Updating engine state")

    # Update state
    engine.state = new_state

    # Log new state for debugging
    log_state(
        logger,
        component="Engine",
        state={
            "process_alive": engine.adapter.is_alive(),
            "breaker_state": str(engine.breaker.state),
            "in_flight": engine.in_flight,
            "active_lora": engine.active_lora,
        }
    )
```

### Example 5: Performance Monitoring

```python
from omniai.logging import log_performance_metric

async def chat(messages, model):
    start = time.time()

    try:
        result = await model.generate(messages)
        duration_ms = (time.time() - start) * 1000

        # Log metric
        log_performance_metric(
            logger,
            metric="Chat completion time",
            value=duration_ms,
            unit="ms",
            threshold=2000,  # Warn if > 2s
        )

        return result
    except Exception as exc:
        duration_ms = (time.time() - start) * 1000
        logger.error(f"❌ Chat failed after {duration_ms:.1f}ms: {exc}")
        raise
```

---

## Adding Logging to Existing Code

### Step 1: Add Logger Import

```python
from omniai.logging import get_logger

logger = get_logger(__name__)
```

### Step 2: Add Operation Tracing

```python
# Before:
def start_engine():
    self.adapter.start()
    self.ready = True

# After:
def start_engine(self):
    with trace_operation(logger, "Starting engine"):
        self.adapter.start()
        self.ready = True
        logger.info("✅ Engine started")
```

### Step 3: Add Error Context

```python
# Before:
try:
    result = await self.client.post(url, json=data)
except Exception:
    raise

# After:
try:
    result = await self.client.post(url, json=data)
except httpx.TimeoutException as exc:
    error = EngineError(
        ErrorMessages.ENGINE_TIMEOUT.format(timeout=30),
        context=f"POST {url} timed out",
        suggestion=Suggestions.INCREASE_TIMEOUT,
    )
    logger.error(str(error))
    raise

except Exception as exc:
    logger.error(f"❌ Request failed: {type(exc).__name__}: {exc}")
    raise
```

### Step 4: Add Debug Logging

```python
def process_message(message):
    logger.debug(f"📞 Processing message | id={message.id} | size={len(message.content)}")

    try:
        result = handler(message)
        logger.debug(f"✅ Processing complete")
        return result
    except Exception as exc:
        logger.error(f"❌ Processing failed: {exc}")
        raise
```

---

## Best Practices

### 1. Use Appropriate Log Levels

```python
logger.debug("Detailed diagnostic info")      # Development/troubleshooting
logger.info("Important event")                # Normal operation
logger.warning("Degraded but working")        # Potential issues
logger.error("Failure but recoverable")       # Errors that don't crash app
logger.critical("System cannot continue")     # Fatal errors
```

### 2. Include Context

```python
# Bad: No context
logger.error("Request failed")

# Good: Includes what was being done and why
logger.error(
    f"Request to {url} failed: {type(exc).__name__}: {exc} | "
    f"retries={retries} | timeout={timeout}s"
)
```

### 3. Use Structured Data

```python
# Bad: Unstructured string
logger.info("processed message alice 100 tokens")

# Good: Structured key-value
logger.info(f"📞 Processing message | user=alice | tokens=100 | duration_ms=45")
```

### 4. Log Entry and Exit Points

```python
async def critical_operation():
    logger.info("🚀 Starting critical operation")
    try:
        result = await do_operation()
        logger.info("✅ Critical operation completed")
        return result
    except Exception as exc:
        logger.error(f"❌ Critical operation failed: {exc}")
        raise
```

### 5. Include IDs for Correlation

```python
logger.info(
    f"📬 POST /v1/messages | request_id={request_id} | session={session_id} | user={user_id}"
)
```

---

## Common Error Patterns

### Pattern 1: Configuration Validation

```python
from omniai.logging import ConfigurationError, ErrorMessages, Suggestions

def validate_config(config):
    if not config.get("api_key"):
        error = ConfigurationError(
            ErrorMessages.MISSING_REQUIRED_FIELD.format(field="api_key"),
            context="API key not set in environment or config",
            suggestion=Suggestions.CHECK_ENV_VARS,
        )
        logger.error(str(error))
        raise

    logger.info("✅ Configuration validated")
```

### Pattern 2: Retry with Exponential Backoff

```python
async def call_with_retry(url, max_retries=3):
    last_error = None

    for attempt in range(max_retries):
        try:
            logger.debug(f"📤 Attempt {attempt+1}/{max_retries}: POST {url}")
            return await client.post(url)
        except httpx.TimeoutException as exc:
            last_error = exc
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logger.warning(f"⚠️  Timeout, retrying in {wait_time}s")
                await asyncio.sleep(wait_time)
            continue

    error = NetworkError(
        ErrorMessages.REQUEST_FAILED.format(status="timeout"),
        context=f"All {max_retries} retries exhausted",
        suggestion=Suggestions.INCREASE_RETRY_TIMEOUT,
    )
    logger.error(str(error))
    raise
```

### Pattern 3: Resource Cleanup

```python
async def with_resource(create_resource, operation):
    resource = None
    try:
        logger.debug("🔧 Allocating resource")
        resource = create_resource()
        logger.debug("✅ Resource allocated")

        return await operation(resource)

    except Exception as exc:
        logger.error(f"❌ Operation failed: {exc}")
        raise

    finally:
        if resource:
            logger.debug("🧹 Cleaning up resource")
            await resource.cleanup()
            logger.debug("✅ Resource cleaned up")
```

---

## Testing with Logs

### Capturing Logs in Tests

```python
import logging

def test_engine_startup(caplog):
    with caplog.at_level(logging.INFO):
        engine.start()

    # Verify correct log messages
    assert any("Starting engine" in record.message for record in caplog.records)
    assert any("Engine started" in record.message for record in caplog.records)
```

### Checking for Errors

```python
def test_invalid_config(caplog):
    with caplog.at_level(logging.ERROR):
        with pytest.raises(ConfigurationError):
            create_engine(invalid_config)

    # Verify error was logged
    error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(error_logs) > 0
    assert "Configuration" in error_logs[0].message
```

---

## Troubleshooting

### Not Seeing Logs?

1. Check logging is configured:
   ```python
   from omniai.logging import configure_logging
   configure_logging(level=logging.DEBUG)
   ```

2. Check logger name matches module:
   ```python
   logger = get_logger(__name__)  # ✓ Correct
   logger = get_logger("omniai")   # ✗ Too broad
   ```

3. Check log level:
   ```python
   logger.debug("message")  # Won't show if level is INFO
   logger.info("message")   # Will show if level is INFO or below
   ```

### Too Many Logs?

Adjust log level:
```python
import logging
configure_logging(level=logging.WARNING)  # Only errors and warnings
```

Or suppress specific modules:
```python
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
```

---

## Summary

- ✅ Use structured logging with emoji indicators
- ✅ Provide context in error messages
- ✅ Include suggestions for resolution
- ✅ Log entry/exit points of operations
- ✅ Use trace_operation for timing
- ✅ Log state for debugging
- ✅ Include IDs for correlation
- ✅ Test that logs are generated correctly
