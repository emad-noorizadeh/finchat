"""Logging configuration — human-readable for dev, JSON for production.

Env vars:
    LOG_LEVEL      — DEBUG | INFO | WARNING | ERROR     (default: INFO)
    LOG_FORMAT     — "json" forces JSON; otherwise human-readable
    LOG_DIR        — if set, writes rotating info.log + error.log files
    SERVICE_NAME   — tag for JSON logs                  (default: "finchat")

Every log record is stamped with the current LogContext (request_id,
session_id, user_id, channel, turn_id, operation) so you can grep by any
of them to reconstruct a turn.
"""

import json
import logging
import os
from datetime import datetime, timezone
from logging.config import dictConfig

from app.log.context import get_log_context


_CONTEXT_FIELDS = ("request_id", "session_id", "user_id", "channel", "turn_id", "operation")


class JsonFormatter(logging.Formatter):
    """JSON lines. Structured enough for grep / jq / any log aggregator."""

    def __init__(self, service: str | None = None):
        super().__init__()
        self.service = service or os.getenv("SERVICE_NAME", "finchat")

    def format(self, record: logging.LogRecord) -> str:
        ctx = get_log_context().to_dict()
        entry: dict = {
            "severity": record.levelname,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "logger": record.name,
            "service": ctx.get("service") or self.service,
            "message": record.getMessage(),
        }
        for k in _CONTEXT_FIELDS:
            if ctx.get(k):
                entry[k] = ctx[k]
        for k, v in ctx.items():
            if k not in entry:
                entry[k] = v
        entry["sourceLocation"] = {
            "file": record.pathname,
            "line": record.lineno,
            "function": record.funcName,
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in entry or key.startswith("_"):
                continue
            if key in (
                "name", "msg", "args", "created", "filename", "funcName",
                "levelname", "levelno", "lineno", "module", "msecs",
                "pathname", "process", "processName", "relativeCreated",
                "stack_info", "exc_info", "exc_text", "thread", "threadName",
                "message", "asctime",
            ):
                continue
            entry[key] = value
        return json.dumps(entry, default=str)


class ContextFormatter(logging.Formatter):
    """Human-readable: `LEVEL [logger] time [context] message`.

    Context is a compact `[key=val …]` string so the line stays scannable.
    """

    def __init__(self, fmt: str | None = None):
        super().__init__(fmt or "%(levelname)-5s [%(name)s] %(asctime)s %(context)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        ctx = get_log_context()
        parts: list[str] = []
        if ctx.session_id:
            parts.append(f"session={ctx.session_id[:8]}")
        if ctx.user_id:
            parts.append(f"user={ctx.user_id}")
        if ctx.channel and ctx.channel != "chat":
            parts.append(f"ch={ctx.channel}")
        if ctx.turn_id:
            parts.append(f"turn={ctx.turn_id[:8]}")
        if ctx.request_id and not ctx.turn_id:
            parts.append(f"req={ctx.request_id[:8]}")
        record.context = f"[{' '.join(parts)}]" if parts else ""
        return super().format(record)


class ContextFilter(logging.Filter):
    """Attach the current LogContext fields to each record.

    Exposes them as record.<field> so format strings and downstream handlers
    (e.g. the JSON handler's .__dict__ scan) can pick them up.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = get_log_context()
        for k in _CONTEXT_FIELDS:
            setattr(record, k, getattr(ctx, k) or "")
        return True


def get_logging_config(
    log_level: str | None = None,
    json_format: bool | None = None,
    service: str | None = None,
    log_dir: str | None = None,
) -> dict:
    level = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper()
    if json_format is None:
        json_format = os.getenv("LOG_FORMAT", "").lower() == "json"
    service_name = service or os.getenv("SERVICE_NAME", "finchat")
    log_dir = log_dir or os.getenv("LOG_DIR")

    formatter_cfg = (
        {"()": JsonFormatter, "service": service_name}
        if json_format
        else {"()": ContextFormatter}
    )

    handlers: dict = {
        "default": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "default",
            "filters": ["context"],
        }
    }
    handler_list = ["default"]

    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        handlers["info_file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": os.path.join(log_dir, "info.log"),
            "level": "INFO",
            "formatter": "default",
            "filters": ["context"],
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
        }
        handlers["error_file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": os.path.join(log_dir, "error.log"),
            "level": "ERROR",
            "formatter": "default",
            "filters": ["context"],
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
        }
        handler_list.extend(["info_file", "error_file"])

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"context": {"()": ContextFilter}},
        "formatters": {"default": formatter_cfg},
        "handlers": handlers,
        "root": {"level": level, "handlers": handler_list},
        "loggers": {
            # App code — propagates to root
            "app": {"level": level, "handlers": handler_list, "propagate": False},
            # Uvicorn
            "uvicorn": {"level": "INFO", "handlers": handler_list, "propagate": False},
            "uvicorn.error": {"level": "INFO", "handlers": handler_list, "propagate": False},
            "uvicorn.access": {"level": "WARNING", "handlers": handler_list, "propagate": False},
            # Noisy third-parties
            "httpx": {"level": "WARNING", "handlers": handler_list, "propagate": False},
            "httpcore": {"level": "WARNING", "handlers": handler_list, "propagate": False},
            "openai": {"level": "WARNING", "handlers": handler_list, "propagate": False},
            "anthropic": {"level": "WARNING", "handlers": handler_list, "propagate": False},
            "chromadb": {"level": "WARNING", "handlers": handler_list, "propagate": False},
        },
    }


def setup_logging(
    log_level: str | None = None,
    json_format: bool | None = None,
    service: str | None = None,
    log_dir: str | None = None,
) -> None:
    """Apply the logging configuration. Call once, early — e.g. in app lifespan."""
    dictConfig(get_logging_config(log_level, json_format, service, log_dir))
    level = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper()
    if json_format is None:
        json_format = os.getenv("LOG_FORMAT", "").lower() == "json"
    fmt = "JSON" if json_format else "text"
    resolved_dir = log_dir or os.getenv("LOG_DIR")
    msg = f"logging configured: level={level} format={fmt} service={service or os.getenv('SERVICE_NAME', 'finchat')}"
    if resolved_dir:
        msg += f" log_dir={resolved_dir}"
    logging.getLogger("app.log").info(msg)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
