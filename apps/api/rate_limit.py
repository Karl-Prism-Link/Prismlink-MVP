from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from math import ceil

from fastapi import HTTPException, Request, status

from core.config import get_settings


class InMemoryRateLimiter:
    """Small per-process limiter for development and single-instance pilot use.

    A production multi-instance deployment must replace this with a shared store
    such as Redis so limits are consistent across API workers.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def retry_after(self, key: str, *, limit: int, window_seconds: int) -> int | None:
        now = self._clock()
        with self._lock:
            entries = self._requests[key]
            cutoff = now - window_seconds
            while entries and entries[0] <= cutoff:
                entries.popleft()
            if len(entries) >= limit:
                return max(1, ceil(entries[0] + window_seconds - now))
            entries.append(now)
            return None


limiter = InMemoryRateLimiter()


def _client_key(request: Request, scope: str) -> str:
    client = request.client.host if request.client else "unknown"
    return f"{scope}:{client}"


async def enforce_auth_rate_limit(
    request: Request, *, scope: str, limit: int, window_seconds: int
) -> None:
    if get_settings().app_env == "test":
        return
    retry_after = limiter.retry_after(
        _client_key(request, scope), limit=limit, window_seconds=window_seconds
    )
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "RATE_LIMITED",
                "message": "Too many requests. Please try again later.",
            },
            headers={"Retry-After": str(retry_after)},
        )


async def limit_registration(request: Request) -> None:
    await enforce_auth_rate_limit(request, scope="register", limit=5, window_seconds=3600)


async def limit_login(request: Request) -> None:
    await enforce_auth_rate_limit(request, scope="login", limit=10, window_seconds=900)
