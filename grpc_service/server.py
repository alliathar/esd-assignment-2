"""gRPC version of the create-booking operation.

The handler calls booking_core directly against the shared SQLite database.
It does not call the HTTP API.

Run: python -m grpc_service.server
"""

import os
import signal
from concurrent import futures

import grpc
from prometheus_client import start_http_server

from booking_core import errors
from booking_core.faults import FaultInjector
from booking_core.repository import BookingRepository
from booking_core.telemetry import BOOKING_EVENTS, IDEMPOTENT_REPLAYS, write_log
from booking_proto import booking_pb2, booking_pb2_grpc
from grpc_service.interceptors import (
    SERVICE_NAME,
    ObservabilityInterceptor,
    current_request_id,
)

# Domain error -> gRPC status. The HTTP API maps the same errors to
# 422 / 409 / 422 / 503 in app/errors.py.
STATUS_FOR_ERROR = {
    errors.ValidationError: grpc.StatusCode.INVALID_ARGUMENT,
    errors.RoomOccupied: grpc.StatusCode.ALREADY_EXISTS,
    errors.IdempotencyKeyReused: grpc.StatusCode.FAILED_PRECONDITION,
    errors.InjectedFault: grpc.StatusCode.UNAVAILABLE,
}


def log(severity, message, **extra):
    write_log(SERVICE_NAME, severity, message, current_request_id.get(), **extra)


def describe(error):
    reasons = "; ".join(
        f"{detail['field']}: {detail['message']}" for detail in error.details
    )
    return f"{error.message} ({reasons})" if reasons else error.message


class BookingServicer(booking_pb2_grpc.BookingServiceServicer):
    def __init__(self, repository, faults=None):
        self.repository = repository
        self.faults = faults or FaultInjector(repository)

    def CreateBooking(self, request, context):
        try:
            self.faults.apply()
            # The client stops waiting at its deadline. Do not store a booking
            # nobody will receive; the client may retry with the same key.
            if not context.is_active():
                log("WARNING", "deadline passed before the booking was stored",
                    event_type="deadline_exceeded", room=request.room)
                context.abort(
                    grpc.StatusCode.DEADLINE_EXCEEDED,
                    "Deadline passed before the booking was stored",
                )
            booking, replayed = self.repository.create_booking(
                request.room,
                request.duration_minutes,
                request.idempotency_key or None,
            )
        except errors.BookingError as error:
            if isinstance(error, errors.RoomOccupied):
                BOOKING_EVENTS.labels(action="create", result="rejected").inc()
                log(
                    "WARNING",
                    "booking rejected because room is occupied",
                    event_type="booking_rejected",
                    room=request.room,
                    reason="room_occupied",
                )
            elif isinstance(error, errors.InjectedFault):
                log("WARNING", "injected create-booking failure",
                    event_type="fault_injected")
            context.abort(STATUS_FOR_ERROR[type(error)], describe(error))

        if replayed:
            IDEMPOTENT_REPLAYS.inc()
            log(
                "INFO",
                "booking create replayed from idempotency key",
                event_type="booking_replayed",
                booking_id=booking["id"],
            )
        else:
            BOOKING_EVENTS.labels(action="create", result="success").inc()
            log(
                "INFO",
                "booking created",
                event_type="booking_created",
                booking_id=booking["id"],
                room=booking["room"],
                duration_minutes=booking["duration_minutes"],
            )

        return booking_pb2.CreateBookingResponse(
            booking=booking_pb2.Booking(**booking),
            replayed=replayed,
        )


def build_server(repository, port, max_workers=10):
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        interceptors=[ObservabilityInterceptor()],
    )
    booking_pb2_grpc.add_BookingServiceServicer_to_server(
        BookingServicer(repository), server
    )
    bound_port = server.add_insecure_port(f"[::]:{port}")
    return server, bound_port


def serve():
    grpc_port = int(os.getenv("GRPC_PORT", "50051"))
    metrics_port = int(os.getenv("METRICS_PORT", "9102"))

    start_http_server(metrics_port)
    server, _ = build_server(BookingRepository(), grpc_port)
    server.start()
    write_log(
        SERVICE_NAME,
        "INFO",
        "grpc server started",
        None,
        grpc_port=grpc_port,
        metrics_port=metrics_port,
    )

    def stop(*_):
        server.stop(grace=5)

    signal.signal(signal.SIGTERM, stop)
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
