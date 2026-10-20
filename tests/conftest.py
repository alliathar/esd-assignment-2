import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from booking_core.repository import BookingRepository  # noqa: E402


@pytest.fixture
def repository(tmp_path):
    return BookingRepository(str(tmp_path / "bookings.db"))


@pytest.fixture
def api(repository, monkeypatch):
    """HTTP test client wired to a temporary database and a fresh limiter."""
    from fastapi.testclient import TestClient

    from app import main
    from app.rate_limit import FixedWindowRateLimiter
    from booking_core.faults import FaultInjector

    monkeypatch.setattr(main, "repository", repository)
    monkeypatch.setattr(main, "create_faults", FaultInjector(repository))
    monkeypatch.setattr(main, "create_rate_limiter", FixedWindowRateLimiter(100, 10))
    return TestClient(main.app)
