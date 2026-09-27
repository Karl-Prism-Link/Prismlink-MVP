from __future__ import annotations

import json
import logging
import time
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

request_logger = logging.getLogger("prism_link.request")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Emit one privacy-conscious structured log event for every HTTP response."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status": status_code,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                        "user_id": str(getattr(request.state, "user_id", "")) or None,
                    },
                    separators=(",", ":"),
                )
            )
