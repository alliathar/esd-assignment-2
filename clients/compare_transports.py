"""Send the same successful and failing bookings through HTTP and gRPC.

Shows that both versions share rules and data: a room booked over gRPC is
rejected over HTTP (409) and vice versa (ALREADY_EXISTS).

Usage: python -m clients.compare_transports [--output evidence/a2/transport-comparison.txt]
"""

import argparse
import json
import uuid

import grpc

from booking_proto import booking_pb2_grpc
from clients.grpc_booking_client import create_booking, describe
from clients.http_booking_client import Reporter, send


def http_create(base_url, room, duration, request_id):
    status, body, headers = send(
        base_url,
        "POST",
        "/v1/bookings",
        {"room": room, "duration_minutes": duration},
        {"X-Request-ID": request_id},
        timeout=2,
    )
    return f"HTTP {status} {json.dumps(body)} X-Request-ID={headers.get('X-Request-ID')}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--target", default="localhost:50051")
    parser.add_argument("--output")
    args = parser.parse_args()

    report = Reporter(args.output)
    prefix = uuid.uuid4().hex[:6]
    http_room = f"Compare-{prefix}-HTTP"
    grpc_room = f"Compare-{prefix}-gRPC"

    with grpc.insecure_channel(args.target) as channel:
        stub = booking_pb2_grpc.BookingServiceStub(channel)

        def via_grpc(label, room, duration):
            request_id = f"compare-{prefix}-{label}"
            report(f"gRPC {label:16} room={room} duration={duration}")
            report(f"     -> {describe(create_booking(stub, room, duration, 2.0, request_id))}")

        def via_http(label, room, duration):
            request_id = f"compare-{prefix}-{label}"
            report(f"HTTP {label:16} room={room} duration={duration}")
            report(f"     -> {http_create(args.base_url, room, duration, request_id)}")

        report("== Success ==")
        via_http("http-success", http_room, 60)
        via_grpc("grpc-success", grpc_room, 60)

        report("== Invalid input (duration 5) ==")
        via_http("http-invalid", f"{http_room}-x", 5)
        via_grpc("grpc-invalid", f"{grpc_room}-x", 5)

        report("== Same room through the other transport (shared database) ==")
        via_http("http-conflict", grpc_room, 60)
        via_grpc("grpc-conflict", http_room, 60)

    report.save()


if __name__ == "__main__":
    main()
