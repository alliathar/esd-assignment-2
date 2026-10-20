from app import main
from app.rate_limit import FixedWindowRateLimiter

BOOKING = {"room": "Study-Room-101", "duration_minutes": 60}


def assert_error_format(response, code):
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"]
    assert error["request_id"] == response.headers["X-Request-ID"]
    return error


def test_valid_request_creates_booking(api):
    response = api.post("/v1/bookings", json=BOOKING)
    assert response.status_code == 201
    booking = response.json()
    assert response.headers["Location"] == f"/v1/bookings/{booking['id']}"
    assert api.get(response.headers["Location"]).json() == booking


def test_invalid_input_returns_422_in_error_format(api):
    response = api.post(
        "/v1/bookings", json={"room": "", "duration_minutes": 5, "extra": 1}
    )
    assert response.status_code == 422
    error = assert_error_format(response, "validation_error")
    assert {detail["field"] for detail in error["details"]} == {
        "room", "duration_minutes", "extra",
    }


def test_blank_room_is_rejected_by_shared_rules(api):
    response = api.post("/v1/bookings", json={"room": "   ", "duration_minutes": 60})
    assert response.status_code == 422
    assert_error_format(response, "validation_error")


def test_repeated_request_with_same_key_is_not_duplicated(api):
    headers = {"Idempotency-Key": "retry-123"}
    first = api.post("/v1/bookings", json=BOOKING, headers=headers)
    second = api.post("/v1/bookings", json=BOOKING, headers=headers)
    assert first.status_code == second.status_code == 201
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    assert len(api.get("/v1/bookings").json()["bookings"]) == 1


def test_repeated_request_without_key_is_a_conflict(api):
    api.post("/v1/bookings", json=BOOKING)
    response = api.post("/v1/bookings", json=BOOKING)
    assert response.status_code == 409
    assert_error_format(response, "room_occupied")


def test_reused_key_with_different_body_is_rejected(api):
    headers = {"Idempotency-Key": "retry-123"}
    api.post("/v1/bookings", json=BOOKING, headers=headers)
    response = api.post(
        "/v1/bookings", json={**BOOKING, "room": "Other"}, headers=headers
    )
    assert response.status_code == 422
    assert_error_format(response, "idempotency_key_reused")


def test_delete_is_idempotent_in_effect(api):
    booking_id = api.post("/v1/bookings", json=BOOKING).json()["id"]
    assert api.delete(f"/v1/bookings/{booking_id}").status_code == 204
    repeat = api.delete(f"/v1/bookings/{booking_id}")
    assert repeat.status_code == 404
    assert_error_format(repeat, "booking_not_found")


def test_unknown_route_uses_error_format_and_bounded_label(api):
    response = api.get("/no-such-path/12345")
    assert response.status_code == 404
    assert_error_format(response, "not_found")
    metrics = api.get("/metrics").text
    assert 'route="unmatched"' in metrics
    assert "/no-such-path/12345" not in metrics


def test_rate_limit_returns_429_with_retry_after(api, monkeypatch):
    monkeypatch.setattr(main, "create_rate_limiter", FixedWindowRateLimiter(2, 10))
    statuses = [
        api.post("/v1/bookings", json={**BOOKING, "room": f"R-{n}"}).status_code
        for n in range(3)
    ]
    assert statuses == [201, 201, 429]

    limited = api.post("/v1/bookings", json={**BOOKING, "room": "R-9"})
    assert limited.status_code == 429
    assert 1 <= int(limited.headers["Retry-After"]) <= 10
    assert limited.headers["RateLimit-Remaining"] == "0"
    assert_error_format(limited, "rate_limited")


def test_rate_limit_window_resets():
    now = [0.0]
    limiter = FixedWindowRateLimiter(1, 10, clock=lambda: now[0])
    assert limiter.check("client").allowed
    blocked = limiter.check("client")
    assert not blocked.allowed and blocked.retry_after == 10
    now[0] = 7.5
    assert limiter.check("client").retry_after == 3
    now[0] = 10.0
    assert limiter.check("client").allowed
    assert limiter.check("other-client").allowed


def test_injected_fault_returns_503(api):
    api.put("/v1/admin/faults/create-booking", json={"fail_next": 1})
    failed = api.post("/v1/bookings", json=BOOKING)
    assert failed.status_code == 503
    assert_error_format(failed, "injected_fault")
    assert api.post("/v1/bookings", json=BOOKING).status_code == 201
