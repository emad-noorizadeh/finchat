"""FinChat logging package.

Usage:
    from app.log import setup_logging, LogContextManager

    # at app startup
    setup_logging()

    # inside a request/turn handler
    with LogContextManager(session_id=..., user_id=..., turn_id=...):
        logger.info("...")

See app/log/config.py for env vars and formatters, app/log/context.py for
contextvars plumbing, and app/log/middleware.py for the FastAPI middleware.
"""

from app.log.config import (
    ContextFilter,
    ContextFormatter,
    JsonFormatter,
    get_logger,
    get_logging_config,
    setup_logging,
)
from app.log.context import (
    LogContext,
    LogContextManager,
    clear_log_context,
    generate_request_id,
    get_log_context,
    set_log_context,
    update_log_context,
)
from app.log.middleware import LoggingMiddleware


__all__ = [
    "setup_logging",
    "get_logger",
    "get_logging_config",
    "ContextFilter",
    "ContextFormatter",
    "JsonFormatter",
    "LogContext",
    "LogContextManager",
    "get_log_context",
    "set_log_context",
    "update_log_context",
    "clear_log_context",
    "generate_request_id",
    "LoggingMiddleware",
]
