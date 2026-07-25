"""Structured logging and error handling for OmniAI.

Provides:
- Consistent logging format across all modules
- User-friendly error messages with context and suggestions
- Structured logging for tracing and debugging
- Performance metrics logging
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


# Configure root logger
def configure_logging(level: int = logging.INFO) -> None:
    """Configure structured logging for the application."""
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(handler)


# Get logger for a module
def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for a module."""
    return logging.getLogger(name)


# ---- Error Messages ----


class OmniAIError(Exception):
    """Base exception for all OmniAI errors with user-friendly messages."""

    def __init__(self, message: str, context: str | None = None, suggestion: str | None = None):
        self.message = message
        self.context = context
        self.suggestion = suggestion
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        """Format the error message for display."""
        parts = [f"❌ {self.message}"]
        if self.context:
            parts.append(f"   Context: {self.context}")
        if self.suggestion:
            parts.append(f"   💡 Try: {self.suggestion}")
        return "\n".join(parts)


class ConfigurationError(OmniAIError):
    """Configuration validation failed."""

    pass


class EngineError(OmniAIError):
    """Engine operation failed."""

    pass


class ModelError(OmniAIError):
    """Model inference failed."""

    pass


class GuardrailError(OmniAIError):
    """Guardrail check failed."""

    pass


class DatabaseError(OmniAIError):
    """Database operation failed."""

    pass


class NetworkError(OmniAIError):
    """Network operation failed."""

    pass


class ProcessError(OmniAIError):
    """Process management failed."""

    pass


class ValidationError(OmniAIError):
    """Data validation failed."""

    pass


# ---- Logging Context Managers ----


@contextmanager
def trace_operation(logger: logging.Logger, operation: str, context: dict[str, Any] | None = None):
    """Context manager for tracing operations with timing."""
    context_str = " | ".join(f"{k}={v}" for k, v in (context or {}).items())
    full_msg = f"{operation}" + (f" [{context_str}]" if context_str else "")

    logger.info(f"⏳ Starting: {full_msg}")
    start_time = time.time()

    try:
        yield
        duration = time.time() - start_time
        logger.info(f"✅ Completed: {full_msg} ({duration:.3f}s)")
    except Exception as exc:
        duration = time.time() - start_time
        logger.error(f"❌ Failed: {full_msg} after {duration:.3f}s - {type(exc).__name__}: {exc}")
        raise


@contextmanager
def trace_performance(logger: logging.Logger, operation: str, threshold_ms: float = 1000):
    """Context manager for performance monitoring with threshold warnings."""
    logger.debug(f"⏱️  Timing: {operation}")
    start_time = time.time()

    try:
        yield
        duration_ms = (time.time() - start_time) * 1000
        if duration_ms > threshold_ms:
            logger.warning(
                f"⚠️  Slow operation: {operation} took {duration_ms:.1f}ms "
                f"(threshold: {threshold_ms}ms)"
            )
        else:
            logger.debug(f"⏱️  {operation} completed in {duration_ms:.1f}ms")
    except Exception as exc:
        duration_ms = (time.time() - start_time) * 1000
        logger.error(
            f"❌ Operation failed: {operation} after {duration_ms:.1f}ms - "
            f"{type(exc).__name__}: {exc}"
        )
        raise


# ---- Logging Decorators ----


def log_operation(logger: logging.Logger | None = None):
    """Decorator to log function calls with arguments and return values."""

    def decorator(func: F) -> F:
        nonlocal logger
        if logger is None:
            logger = get_logger(func.__module__)

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            func_name = func.__qualname__
            arg_str = ", ".join(
                [repr(a)[:50] for a in args[1:]]  # Skip self
                + [f"{k}={repr(v)[:50]}" for k, v in kwargs.items()]
            )
            logger.debug(f"📞 Calling {func_name}({arg_str})")

            try:
                result = await func(*args, **kwargs)
                logger.debug(f"✅ {func_name} returned: {repr(result)[:100]}")
                return result
            except Exception as exc:
                logger.error(f"❌ {func_name} raised {type(exc).__name__}: {exc}")
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            func_name = func.__qualname__
            arg_str = ", ".join(
                [repr(a)[:50] for a in args[1:]]  # Skip self
                + [f"{k}={repr(v)[:50]}" for k, v in kwargs.items()]
            )
            logger.debug(f"📞 Calling {func_name}({arg_str})")

            try:
                result = func(*args, **kwargs)
                logger.debug(f"✅ {func_name} returned: {repr(result)[:100]}")
                return result
            except Exception as exc:
                logger.error(f"❌ {func_name} raised {type(exc).__name__}: {exc}")
                raise

        # Return async wrapper if function is async, otherwise sync
        if hasattr(func, "__wrapped__"):
            return async_wrapper if hasattr(func, "_is_coroutine") else sync_wrapper  # type: ignore
        return async_wrapper if func.__name__ == "async_wrapper" else sync_wrapper  # type: ignore

    return decorator


def log_exceptions(logger: logging.Logger | None = None):
    """Decorator to log exceptions with full traceback."""

    def decorator(func: F) -> F:
        nonlocal logger
        if logger is None:
            logger = get_logger(func.__module__)

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                logger.exception(f"❌ Exception in {func.__qualname__}: {exc}")
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                logger.exception(f"❌ Exception in {func.__qualname__}: {exc}")
                raise

        return async_wrapper if hasattr(func, "_is_coroutine") else sync_wrapper  # type: ignore

    return decorator


# ---- Error Message Builders ----


def format_error(
    title: str,
    message: str,
    context: dict[str, Any] | None = None,
    suggestion: str | None = None,
) -> str:
    """Format an error message for display."""
    parts = [f"❌ {title}", f"   {message}"]

    if context:
        parts.append("   Context:")
        for key, value in context.items():
            parts.append(f"     • {key}: {value}")

    if suggestion:
        parts.append(f"   💡 Suggestion: {suggestion}")

    return "\n".join(parts)


class ErrorMessages:
    """Standard error messages for common failures."""

    # Configuration errors
    INVALID_CONFIG = "Configuration validation failed"
    MISSING_REQUIRED_FIELD = "Missing required configuration field: {field}"
    INVALID_ENUM_VALUE = "Invalid enum value: {value}. Must be one of: {options}"
    DATABASE_URL_INVALID = "Invalid database URL format"

    # Engine errors
    ENGINE_START_FAILED = "Failed to start engine backend"
    ENGINE_UNAVAILABLE = "Engine is currently unavailable"
    ENGINE_TIMEOUT = "Engine request timed out after {timeout}s"
    PROCESS_SPAWN_FAILED = "Failed to spawn backend process"
    PROCESS_DIED = "Backend process died unexpectedly"
    DEVICE_NOT_FOUND = "GPU device not found: {device}"
    CUDA_ERROR = "CUDA initialization failed"
    MAX_RESTARTS_EXCEEDED = "Backend exceeded maximum restart attempts ({max_restarts})"

    # Model errors
    MODEL_NOT_FOUND = "Model not found: {model}"
    MODEL_LOAD_FAILED = "Failed to load model: {model}"
    INFERENCE_FAILED = "Model inference failed"
    RESPONSE_INVALID = "Model response is invalid or malformed"
    TOKEN_LIMIT_EXCEEDED = "Token limit exceeded. Tokens: {tokens}, Limit: {limit}"
    UNSUPPORTED_MODEL_TYPE = "Model type not supported: {model_type}"

    # Guardrail errors
    INJECTION_DETECTED = "Potential prompt injection detected"
    PII_DETECTED = "Personally identifiable information detected"
    CONTENT_BLOCKED = "Content blocked by guardrail policy"
    HARMFUL_CONTENT = "Harmful content detected"

    # Network errors
    CONNECTION_FAILED = "Failed to connect to {host}:{port}"
    CONNECTION_TIMEOUT = "Connection timed out after {timeout}s"
    DNS_RESOLUTION_FAILED = "DNS resolution failed for {host}"
    REQUEST_FAILED = "HTTP request failed with status {status}"
    RATE_LIMITED = "Rate limit exceeded. Retry after {retry_after}s"

    # Database errors
    CONNECTION_ERROR = "Database connection failed"
    QUERY_FAILED = "Database query failed"
    MIGRATION_FAILED = "Database migration failed"
    TRANSACTION_FAILED = "Database transaction failed"
    CONSTRAINT_VIOLATION = "Database constraint violated"

    # Validation errors
    INVALID_JSON = "Invalid JSON format"
    SCHEMA_MISMATCH = "Response does not match expected schema"
    TYPE_MISMATCH = "Type mismatch: expected {expected}, got {actual}"
    VALUE_OUT_OF_RANGE = "Value out of range: {value} (must be between {min} and {max})"
    INVALID_FORMAT = "Invalid format for {field}: {format}"

    # Process errors
    PROCESS_NOT_FOUND = "Process not found or already terminated"
    SIGNAL_FAILED = "Failed to send signal to process"
    LOG_FILE_ERROR = "Failed to open/write log file"
    PERMISSION_DENIED = "Permission denied"

    # Concurrency errors
    LOCK_TIMEOUT = "Failed to acquire lock after {timeout}s"
    DEADLOCK_DETECTED = "Potential deadlock detected"
    RESOURCE_EXHAUSTED = "Resource exhausted: {resource}"

    # File errors
    FILE_NOT_FOUND = "File not found: {path}"
    FILE_READ_ERROR = "Failed to read file: {path}"
    FILE_WRITE_ERROR = "Failed to write file: {path}"
    PERMISSION_ERROR = "Permission denied for file: {path}"


# ---- Suggestion Messages ----


class Suggestions:
    """Standard suggestions for common errors."""

    # Configuration
    CHECK_ENV_VARS = "Check that all required environment variables are set"
    CHECK_CONFIG_FILE = "Verify configuration file syntax and values"
    REVIEW_DOCS = "Review documentation for correct configuration"

    # Engine
    RESTART_ENGINE = "Try restarting the engine backend"
    CHECK_GPU = "Verify GPU is available and working correctly"
    CHECK_DEVICE_ID = "Verify GPU device ID is correct"
    INCREASE_TIMEOUT = "Increase request timeout value"
    CHECK_NETWORK = "Verify network connectivity to engine service"

    # Model
    DOWNLOAD_MODEL = "Download the required model weights"
    CHECK_MODEL_NAME = "Verify model name is correct and available"
    INCREASE_MEMORY = "Increase available GPU/system memory"
    REDUCE_BATCH_SIZE = "Reduce batch size or max tokens"

    # Network
    RETRY_LATER = "Wait a moment and try again"
    CHECK_CONNECTIVITY = "Verify internet connection and network configuration"
    CHECK_FIREWALL = "Check firewall rules and proxy settings"
    INCREASE_RETRY_TIMEOUT = "Increase retry timeout value"

    # Database
    CHECK_DATABASE_URL = "Verify database connection string is correct"
    CHECK_DATABASE_SERVER = "Verify database server is running and accessible"
    RESTART_DATABASE = "Restart the database server"
    CHECK_CREDENTIALS = "Verify database username and password"
    RUN_MIGRATIONS = "Run database migrations: alembic upgrade head"

    # Validation
    CHECK_JSON_FORMAT = "Verify JSON format is valid"
    VALIDATE_INPUT = "Validate input against schema"
    CHECK_TYPES = "Verify input data types match expected schema"


# ---- Structured Logging ----


def log_request(
    logger: logging.Logger,
    method: str,
    endpoint: str,
    client_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Log an incoming request."""
    parts = [f"📬 {method} {endpoint}"]
    if client_id:
        parts.append(f"client={client_id}")
    if request_id:
        parts.append(f"request_id={request_id}")
    logger.info(" | ".join(parts))


def log_response(
    logger: logging.Logger,
    endpoint: str,
    status_code: int,
    duration_ms: float,
    request_id: str | None = None,
) -> None:
    """Log an outgoing response."""
    status_emoji = "✅" if 200 <= status_code < 300 else "⚠️" if status_code < 400 else "❌"
    parts = [f"{status_emoji} {status_code} {endpoint} ({duration_ms:.1f}ms)"]
    if request_id:
        parts.append(f"request_id={request_id}")
    logger.info(" | ".join(parts))


def log_state(logger: logging.Logger, component: str, state: dict[str, Any]) -> None:
    """Log component state for debugging."""
    state_str = " | ".join(f"{k}={v}" for k, v in state.items())
    logger.debug(f"📊 {component} state: {state_str}")


def log_performance_metric(
    logger: logging.Logger,
    metric: str,
    value: float,
    unit: str = "ms",
    threshold: float | None = None,
) -> None:
    """Log a performance metric."""
    status = "✅" if threshold is None or value <= threshold else "⚠️"
    logger.info(
        f"{status} {metric}: {value:.2f}{unit}"
        + (f" (threshold: {threshold}{unit})" if threshold else "")
    )
