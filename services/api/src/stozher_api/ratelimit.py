"""Per-key request limits (docs/api.md section 4): fixed one-minute windows, in memory.

The counters live in this process. With several API processes each would count separately, so the
effective limit is the configured one times the process count; a shared store comes with
multi-process deployments. `system` keys have a high limit so ingest is never throttled in normal use.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_LIMITS = {
    "public": 60,
    "reporter": 120,
    "responder": 300,
    "operator": 600,
    "admin": 600,
    "system": 6000,
}
WINDOW_S = 60


@dataclass(frozen=True)
class RateState:
    limit: int
    remaining: int
    reset_at: int  # unix seconds when the window ends
    allowed: bool
    retry_after: int  # seconds until the window ends, by the limiter's own clock

    def headers(self) -> dict[str, str]:
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset_at),
        }


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self._windows: dict[str, tuple[int, int]] = {}  # prefix -> (window start, requests)

    def hit(self, prefix: str, limit: int) -> RateState:
        now = int(self.clock())
        start = now - now % WINDOW_S
        begun, count = self._windows.get(prefix, (start, 0))
        if begun != start:
            count = 0
        count += 1
        self._windows[prefix] = (start, count)
        reset = start + WINDOW_S
        return RateState(limit, max(0, limit - count), reset, count <= limit, max(1, reset - now))
        # a rejected request still counts, so hammering a limited key does not earn it extra

    def forget_stale(self) -> None:
        cutoff = int(self.clock()) - 2 * WINDOW_S
        self._windows = {k: v for k, v in self._windows.items() if v[0] >= cutoff}


class RateHeadersMiddleware:
    """Adds X-RateLimit-* to every response of a request that authenticated (pure ASGI: safe for SSE)."""

    def __init__(self, app):  # type: ignore[no-untyped-def]
        self.app = app

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        scope.setdefault("state", {})

        async def send_with_headers(message):  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.start":
                state = scope["state"].get("ratelimit")
                if state is not None:
                    message.setdefault("headers", [])
                    message["headers"] = [
                        *message["headers"],
                        *((k.lower().encode(), v.encode()) for k, v in state.headers().items()),
                    ]
            await send(message)

        await self.app(scope, receive, send_with_headers)
