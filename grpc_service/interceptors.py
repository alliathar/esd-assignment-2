"""Server interceptor: request-ID correlation, JSON logs, and metrics.

It plays the role the HTTP middleware plays in app/main.py, so every RPC is
logged and measured the same way without touching each handler.
"""

import time
import uuid
from contextvars import ContextVar

import grpc
from prometheus_client import Counter, Gauge, Histogram

from booking_core.telemetry import LATENCY_BUCKETS, write_log

SERVICE_NAME = "room-booking-grpc"
REQUEST_ID_METADATA = "x-request-id"

# Lets the handler log with the request ID the interceptor resolved.
current_request_id = ContextVar("current_request_id", default=None)

GRPC_HANDLED = Counter(
    "room_booking_grpc_server_handled_total",
    "Completed RPCs by method and gRPC status code",
    ["grpc_service", "grpc_method", "grpc_code"],
)

GRPC_DURATION = Histogram(
    "room_booking_grpc_server_handling_seconds",
    "RPC handling time in seconds, measured on the server by the interceptor",
    ["grpc_service", "grpc_method"],
    buckets=LATENCY_BUCKETS,
)

GRPC_IN_FLIGHT = Gauge(
    "room_booking_grpc_server_in_flight",
    "RPCs currently being handled",
    ["grpc_service", "grpc_method"],
)


def split_method(full_method):
    """'/roombooking.v1.BookingService/CreateBooking' -> (service, method)."""
    service, _, method = full_method.lstrip("/").rpartition("/")
    return service, method


class ObservabilityInterceptor(grpc.ServerInterceptor):
    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)
        # Unknown methods return None (UNIMPLEMENTED) and are not labelled,
        # which keeps the method label limited to methods in the .proto.
        if handler is None or handler.unary_unary is None:
            return handler

        service, method = split_method(handler_call_details.method)
        inner = handler.unary_unary

        def observed(request, context):
            metadata = dict(context.invocation_metadata())
            request_id = metadata.get(REQUEST_ID_METADATA) or str(uuid.uuid4())
            context.send_initial_metadata(((REQUEST_ID_METADATA, request_id),))
            token = current_request_id.set(request_id)

            in_flight = GRPC_IN_FLIGHT.labels(grpc_service=service, grpc_method=method)
            in_flight.inc()
            started = time.perf_counter()
            code = grpc.StatusCode.OK
            try:
                response = inner(request, context)
                code = context.code() or grpc.StatusCode.OK
                return response
            except Exception:
                # context.abort() raises; its status is kept on the context.
                code = context.code() or grpc.StatusCode.UNKNOWN
                raise
            finally:
                duration = time.perf_counter() - started
                in_flight.dec()
                GRPC_HANDLED.labels(
                    grpc_service=service,
                    grpc_method=method,
                    grpc_code=code.name,
                ).inc()
                GRPC_DURATION.labels(
                    grpc_service=service,
                    grpc_method=method,
                ).observe(duration)
                write_log(
                    SERVICE_NAME,
                    "INFO" if code == grpc.StatusCode.OK else "WARNING",
                    "rpc completed",
                    request_id,
                    rpc={"service": service, "method": method},
                    grpc_code=code.name,
                    duration_ms=round(duration * 1000, 2),
                    peer=context.peer(),
                )
                current_request_id.reset(token)

        return grpc.unary_unary_rpc_method_handler(
            observed,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
