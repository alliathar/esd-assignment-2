"""Retry backoff and a circuit breaker for the service callers."""

import random
import time

CLOSED = "CLOSED"
OPEN = "OPEN"
HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    def __init__(self, seconds_left):
        super().__init__(f"circuit open; next trial in {seconds_left:.1f}s")
        self.seconds_left = seconds_left


class CircuitBreaker:
    """CLOSED -> OPEN after `failure_threshold` consecutive failures.

    While OPEN, calls are refused without contacting the server. After
    `recovery_seconds` the breaker is HALF_OPEN and lets one trial call
    through: success closes it, failure opens it again.
    """

    def __init__(self, failure_threshold=3, recovery_seconds=5.0, clock=time.monotonic):
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.clock = clock
        self.consecutive_failures = 0
        self._opened_at = None
        self._trial_in_progress = False

    @property
    def state(self):
        if self._opened_at is None:
            return CLOSED
        if self.clock() - self._opened_at >= self.recovery_seconds:
            return HALF_OPEN
        return OPEN

    def before_call(self):
        state = self.state
        if state == OPEN:
            raise CircuitOpenError(
                self.recovery_seconds - (self.clock() - self._opened_at)
            )
        if state == HALF_OPEN:
            if self._trial_in_progress:
                raise CircuitOpenError(0)
            self._trial_in_progress = True

    def record_success(self):
        self.consecutive_failures = 0
        self._opened_at = None
        self._trial_in_progress = False

    def record_failure(self):
        if self.state == HALF_OPEN:
            self._open()
            return
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self._open()

    def _open(self):
        self._opened_at = self.clock()
        self._trial_in_progress = False


def backoff_seconds(retry_number, base=0.25, jitter=0.1):
    """Exponential backoff: 0.25 s, 0.5 s, 1 s, ... plus up to 0.1 s jitter."""
    return base * (2 ** (retry_number - 1)) + random.uniform(0, jitter)
