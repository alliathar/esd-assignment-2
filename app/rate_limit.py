"""Single-instance fixed-window rate limiter.

Each client (keyed by IP address) gets `limit` calls per window. The window
starts at that client's first call; when it ends the count resets, so
Retry-After is the number of seconds left in the current window.
"""

import math
import time
from dataclasses import dataclass
from threading import Lock


@dataclass
class Decision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    window_seconds: float

    def headers(self):
        return {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(self.remaining),
            "RateLimit-Reset": str(self.retry_after),
        }


class FixedWindowRateLimiter:
    def __init__(self, limit, window_seconds, clock=time.monotonic):
        self.limit = limit
        self.window_seconds = window_seconds
        self.clock = clock
        self._windows = {}
        self._lock = Lock()

    def check(self, client_key):
        now = self.clock()
        with self._lock:
            started, used = self._windows.get(client_key, (now, 0))
            if now - started >= self.window_seconds:
                started, used = now, 0

            allowed = used < self.limit
            if allowed:
                used += 1
            self._windows[client_key] = (started, used)

            if len(self._windows) > 10_000:
                self._forget_expired(now)

        seconds_left = started + self.window_seconds - now
        return Decision(
            allowed=allowed,
            limit=self.limit,
            remaining=max(0, self.limit - used),
            retry_after=max(1, math.ceil(seconds_left)),
            window_seconds=self.window_seconds,
        )

    def _forget_expired(self, now):
        expired = [
            key
            for key, (started, _) in self._windows.items()
            if now - started >= self.window_seconds
        ]
        for key in expired:
            del self._windows[key]
