"""Call the gRPC BookingService.CreateBooking directly through the generated stub.

Usage (from the project root):
  python -m clients.grpc_booking_client --room Study-Room-201 --duration 60
  python -m clients.grpc_booking_client --room Study-Room-201 --duration 5      # INVALID_ARGUMENT
  python -m clients.grpc_booking_client --demo --rounds 3                       # populate dashboards

Every call sets a deadline (default 2 s) and sends an x-request-id metadata
entry, which the server logs and echoes back for Kibana correlation.
"""

import argparse
import uuid

import grpc

from booking_proto import booking_pb2, booking_pb2_grpc


def create_booking(stub, room, duration, deadline, request_id=None, idempotency_key=""):
    """Return a dict describing the outcome; never raises for RPC errors."""
    request_id = request_id or f"grpc-{uuid.uuid4()}"
    request = booking_pb2.CreateBookingRequest(
        room=room,
        duration_minutes=duration,
        idempotency_key=idempotency_key,
    )
    try:
        response, call = stub.CreateBooking.with_call(
            request,
            timeout=deadline,
            metadata=(("x-request-id", request_id),),
        )
    except grpc.RpcError as error:
        return {
            "ok": False,
            "code": error.code().name,
            "details": error.details(),
            "request_id": request_id,
        }

    echoed = dict(call.initial_metadata()).get("x-request-id")
    return {
        "ok": True,
        "code": "OK",
        "booking_id": response.booking.id,
        "room": response.booking.room,
        "duration_minutes": response.booking.duration_minutes,
        "replayed": response.replayed,
        "request_id": echoed,
    }


def describe(result):
    if result["ok"]:
        replay = " (replayed)" if result["replayed"] else ""
        return (
            f"OK booking={result['booking_id']} room={result['room']} "
            f"duration={result['duration_minutes']}{replay} request_id={result['request_id']}"
        )
    return f"{result['code']}: {result['details']} request_id={result['request_id']}"


def run_demo(stub, deadline, rounds):
    """A repeatable mix of successes and failures for the Grafana panels."""
    for round_number in range(1, rounds + 1):
        prefix = f"gRPC-{uuid.uuid4().hex[:6]}"
        key = str(uuid.uuid4())
        cases = [
            ("success", dict(room=f"{prefix}-1", duration=60, idempotency_key=key)),
            ("success", dict(room=f"{prefix}-2", duration=90)),
            ("idempotent repeat", dict(room=f"{prefix}-1", duration=60, idempotency_key=key)),
            ("room occupied", dict(room=f"{prefix}-2", duration=30)),
            ("invalid duration", dict(room=f"{prefix}-3", duration=5)),
            ("key reused, new body", dict(room=f"{prefix}-4", duration=60, idempotency_key=key)),
        ]
        print(f"-- round {round_number}/{rounds} --")
        for label, case in cases:
            result = create_booking(stub, deadline=deadline, **case)
            print(f"{label:22} -> {describe(result)}")


def main():
    parser = argparse.ArgumentParser(description="gRPC CreateBooking client")
    parser.add_argument("--target", default="localhost:50051")
    parser.add_argument("--room", default="Study-Room-201")
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--deadline", type=float, default=2.0, help="seconds")
    parser.add_argument("--request-id")
    parser.add_argument("--idempotency-key", default="")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--rounds", type=int, default=1)
    args = parser.parse_args()

    with grpc.insecure_channel(args.target) as channel:
        stub = booking_pb2_grpc.BookingServiceStub(channel)
        if args.demo:
            run_demo(stub, args.deadline, args.rounds)
            return
        result = create_booking(
            stub,
            args.room,
            args.duration,
            args.deadline,
            args.request_id,
            args.idempotency_key,
        )
        print(describe(result))
        if not result["ok"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
