import time

import grpc
import pytest

from booking_proto import booking_pb2, booking_pb2_grpc
from grpc_service.server import build_server


@pytest.fixture
def stub(repository):
    server, port = build_server(repository, 0, max_workers=2)
    server.start()
    channel = grpc.insecure_channel(f"localhost:{port}")
    yield booking_pb2_grpc.BookingServiceStub(channel)
    channel.close()
    server.stop(None)


def create(stub, room="Room-1", duration=60, key="", request_id="test-request"):
    return stub.CreateBooking.with_call(
        booking_pb2.CreateBookingRequest(
            room=room, duration_minutes=duration, idempotency_key=key
        ),
        timeout=2,
        metadata=(("x-request-id", request_id),),
    )


def test_success_and_request_id_echo(stub, repository):
    response, call = create(stub, request_id="abc-123")
    assert response.booking.room == "Room-1"
    assert not response.replayed
    assert dict(call.initial_metadata())["x-request-id"] == "abc-123"
    assert repository.get_booking(response.booking.id)["room"] == "Room-1"


def test_invalid_argument(stub):
    with pytest.raises(grpc.RpcError) as error:
        create(stub, duration=5)
    assert error.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "duration_minutes" in error.value.details()


def test_already_exists_shares_rules_with_http(stub, repository):
    repository.create_booking("Room-1", 60)  # e.g. booked over HTTP
    with pytest.raises(grpc.RpcError) as error:
        create(stub, room="room-1")
    assert error.value.code() == grpc.StatusCode.ALREADY_EXISTS


def test_idempotent_repeat(stub):
    first, _ = create(stub, key="k-1")
    again, _ = create(stub, key="k-1")
    assert again.replayed and again.booking == first.booking
    with pytest.raises(grpc.RpcError) as error:
        create(stub, room="Room-2", key="k-1")
    assert error.value.code() == grpc.StatusCode.FAILED_PRECONDITION


def test_injected_fault_is_unavailable(stub, repository):
    repository.set_fault("create-booking", 0, 1, 1, False)
    with pytest.raises(grpc.RpcError) as error:
        create(stub)
    assert error.value.code() == grpc.StatusCode.UNAVAILABLE


def test_deadline_exceeded_does_not_store_booking(stub, repository):
    from prometheus_client import REGISTRY

    labels = {
        "grpc_service": "roombooking.v1.BookingService",
        "grpc_method": "CreateBooking",
        "grpc_code": "DEADLINE_EXCEEDED",
    }
    before = REGISTRY.get_sample_value("room_booking_grpc_server_handled_total", labels) or 0

    repository.set_fault("create-booking", 300, 1, 0, False)
    with pytest.raises(grpc.RpcError) as error:
        stub.CreateBooking(
            booking_pb2.CreateBookingRequest(room="Slow", duration_minutes=60),
            timeout=0.1,
        )
    assert error.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED

    time.sleep(0.5)  # let the server finish the abandoned call
    assert repository.count_active() == 0
    after = REGISTRY.get_sample_value("room_booking_grpc_server_handled_total", labels)
    assert after == before + 1


def test_metrics_recorded_with_bounded_labels(stub):
    from prometheus_client import REGISTRY

    create(stub, room="Metrics-Room")
    with pytest.raises(grpc.RpcError):
        create(stub, room="Metrics-Room")
    labels = {"grpc_service": "roombooking.v1.BookingService", "grpc_method": "CreateBooking"}
    assert REGISTRY.get_sample_value(
        "room_booking_grpc_server_handled_total", {**labels, "grpc_code": "ALREADY_EXISTS"}
    ) >= 1
    assert REGISTRY.get_sample_value(
        "room_booking_grpc_server_handling_seconds_count", labels
    ) >= 2
