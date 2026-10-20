"""Logging and metric definitions shared by both services.

Each process imports this module and gets its own copy of the metrics, so
Prometheus tells them apart by the scrape job (room-booking-api or
room-booking-grpc), not by an extra label.
"""

import json
from datetime import datetime, timezone

from prometheus_client import Counter

# Shared so HTTP and gRPC latency panels can be compared bucket for bucket.
# Typical requests take 5-20 ms, so the lower buckets are finer than in A1.
LATENCY_BUCKETS = [0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5]

BOOKING_EVENTS = Counter(
    "room_booking_events_total",
    "Booking business events",
    ["action", "result"],
)

IDEMPOTENT_REPLAYS = Counter(
    "room_booking_idempotent_replays_total",
    "Create requests answered from a stored idempotency key instead of "
    "creating a new booking",
)


def write_log(service, severity, message, request_id, **extra):
    event = {
        "@timestamp": datetime.now(timezone.utc).isoformat(),
        "service": {"name": service},
        "log": {"level": severity.lower()},
        "severity": severity,
        "message": message,
        "request_id": request_id,
        **extra,
    }
    print(json.dumps(event), flush=True)
