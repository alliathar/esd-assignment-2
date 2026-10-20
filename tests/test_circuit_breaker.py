import pytest

from clients.resilience import (
    CLOSED,
    HALF_OPEN,
    OPEN,
    CircuitBreaker,
    CircuitOpenError,
    backoff_seconds,
)


@pytest.fixture
def clock():
    now = [0.0]
    return now


def make_breaker(clock):
    return CircuitBreaker(failure_threshold=3, recovery_seconds=5, clock=lambda: clock[0])


def test_opens_after_threshold_and_blocks(clock):
    breaker = make_breaker(clock)
    for _ in range(2):
        breaker.record_failure()
    assert breaker.state == CLOSED
    breaker.record_failure()
    assert breaker.state == OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_success_resets_failure_count(clock):
    breaker = make_breaker(clock)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.state == CLOSED


def test_half_open_trial_success_closes(clock):
    breaker = make_breaker(clock)
    for _ in range(3):
        breaker.record_failure()
    clock[0] = 5
    assert breaker.state == HALF_OPEN
    breaker.before_call()
    with pytest.raises(CircuitOpenError):
        breaker.before_call()  # only one trial at a time
    breaker.record_success()
    assert breaker.state == CLOSED


def test_half_open_trial_failure_reopens(clock):
    breaker = make_breaker(clock)
    for _ in range(3):
        breaker.record_failure()
    clock[0] = 5
    breaker.before_call()
    breaker.record_failure()
    assert breaker.state == OPEN
    clock[0] = 9.9
    assert breaker.state == OPEN
    clock[0] = 10
    assert breaker.state == HALF_OPEN


def test_backoff_grows_exponentially():
    assert 0.25 <= backoff_seconds(1) <= 0.35
    assert 0.5 <= backoff_seconds(2) <= 0.6
