"""Request-scoped logging context via contextvars.

Populated once per HTTP request (or per chat turn inside the SSE handler) and
automatically attached to every log record by ContextFilter. When debugging a
session, filter logs by session_id or user_id to see the full trace.
"""

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LogContext:
    """Request/turn-scoped context stamped onto log records."""
    request_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    channel: str | None = None        # "chat" or "voice"
    turn_id: str | None = None        # unique per user message within a session
    operation: str | None = None      # HTTP method + path, or "chat:send"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in ("request_id", "session_id", "user_id", "channel", "turn_id", "operation"):
            val = getattr(self, name)
            if val:
                result[name] = val
        if self.extra:
            result.update(self.extra)
        return result


_LOG_CONTEXT: ContextVar[LogContext] = ContextVar("log_context", default=LogContext())


def get_log_context() -> LogContext:
    return _LOG_CONTEXT.get()


def set_log_context(
    request_id: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    channel: str | None = None,
    turn_id: str | None = None,
    operation: str | None = None,
    **extra: Any,
) -> LogContext:
    """Replace the current context. Returns the new LogContext."""
    ctx = LogContext(
        request_id=request_id or str(uuid.uuid4()),
        session_id=session_id,
        user_id=user_id,
        channel=channel,
        turn_id=turn_id,
        operation=operation,
        extra=extra,
    )
    _LOG_CONTEXT.set(ctx)
    return ctx


def update_log_context(**kwargs: Any) -> LogContext:
    """Mutate the current context without resetting unset fields."""
    ctx = get_log_context()
    for name in ("request_id", "session_id", "user_id", "channel", "turn_id", "operation"):
        if name in kwargs:
            setattr(ctx, name, kwargs.pop(name))
    ctx.extra.update(kwargs)
    _LOG_CONTEXT.set(ctx)
    return ctx


def clear_log_context() -> None:
    _LOG_CONTEXT.set(LogContext())


def generate_request_id() -> str:
    return str(uuid.uuid4())


class LogContextManager:
    """`with LogContextManager(...):` — scoped context, auto-cleared on exit.

    Use inside chat's event_stream to tag all logs of a turn, without leaking
    the context past the end of the stream.
    """

    def __init__(
        self,
        request_id: str | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        channel: str | None = None,
        turn_id: str | None = None,
        operation: str | None = None,
        **extra: Any,
    ):
        self._kwargs = dict(
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            channel=channel,
            turn_id=turn_id,
            operation=operation,
        )
        self._extra = extra
        self._token = None

    def __enter__(self) -> LogContext:
        ctx = LogContext(
            request_id=self._kwargs["request_id"] or generate_request_id(),
            session_id=self._kwargs["session_id"],
            user_id=self._kwargs["user_id"],
            channel=self._kwargs["channel"],
            turn_id=self._kwargs["turn_id"],
            operation=self._kwargs["operation"],
            extra=dict(self._extra),
        )
        self._token = _LOG_CONTEXT.set(ctx)
        return ctx

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._token is not None:
            _LOG_CONTEXT.reset(self._token)
        return False
