"""FastAPI middleware that sets request-level logging context.

Scope: HTTP request lifecycle. For the SSE chat endpoint, the middleware sets
an initial context (user_id derived from the POST body happens inside the
chat router via LogContextManager, since the body is read there).

Paths listed in `skip_paths` bypass start/end logging — healthchecks and the
streaming chat endpoint, which shouldn't be wrapped by BaseHTTPMiddleware
because that can buffer the generator.
"""

import logging
import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.log.context import clear_log_context, generate_request_id, set_log_context


logger = logging.getLogger("app.log.request")


class LoggingMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        service: str | None = None,
        skip_paths: list[str] | None = None,
        skip_method_prefixes: list[tuple[str, str]] | None = None,
        log_requests: bool = True,
    ):
        super().__init__(app)
        self.service = service
        self.skip_paths = set(skip_paths or ["/api/health"])
        # Pairs of (METHOD, path_prefix) to skip — e.g. POST to /api/chat/sessions/
        # sends SSE, which BaseHTTPMiddleware buffers. The chat router sets its
        # own turn-level context instead.
        self.skip_method_prefixes = list(skip_method_prefixes or [])
        self.log_requests = log_requests

    def _should_skip(self, method: str, path: str) -> bool:
        if path in self.skip_paths:
            return True
        for m, prefix in self.skip_method_prefixes:
            if method == m and path.startswith(prefix):
                return True
        return False

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if self._should_skip(request.method, path):
            return await call_next(request)

        start = time.time()
        request_id = request.headers.get("X-Request-ID") or generate_request_id()

        set_log_context(
            request_id=request_id,
            user_id=request.headers.get("X-User-ID"),
            operation=f"{request.method} {path}",
        )

        if self.log_requests:
            logger.info(
                "request start",
                extra={"http_method": request.method, "http_path": path},
            )

        try:
            response = await call_next(request)
            duration_ms = (time.time() - start) * 1000
            response.headers["X-Request-ID"] = request_id
            if self.log_requests:
                logger.info(
                    f"request done status={response.status_code} duration_ms={duration_ms:.1f}",
                    extra={
                        "http_method": request.method,
                        "http_path": path,
                        "http_status": response.status_code,
                        "duration_ms": duration_ms,
                    },
                )
            return response
        except Exception as exc:
            duration_ms = (time.time() - start) * 1000
            logger.error(
                f"request failed error={exc} duration_ms={duration_ms:.1f}",
                extra={"http_method": request.method, "http_path": path, "duration_ms": duration_ms},
                exc_info=True,
            )
            raise
        finally:
            clear_log_context()
