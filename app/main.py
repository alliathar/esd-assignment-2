import os
import time
import uuid
from threading import Lock

from fastapi import Depends, FastAPI, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from prometheus_client import Counter, Gauge, Histogram, Summary, generate_latest
from starlette.routing import Match

from app.errors import (
    RateLimitExceeded,
    error_response,
    error_responses,
    install_error_handlers,
)
from app.rate_limit import FixedWindowRateLimiter
from booking_core import rules
from booking_core.errors import BookingNotFound, InjectedFault, RoomOccupied
from booking_core.faults import CREATE_BOOKING, FaultInjector
from booking_core.repository import BookingRepository
from booking_core.telemetry import (
    BOOKING_EVENTS,
    IDEMPOTENT_REPLAYS,
    LATENCY_BUCKETS,
    write_log as write_service_log,
)

SERVICE_NAME = "room-booking-api"
BOOKINGS_PATH = "/v1/bookings"

app = FastAPI(
    title="Campus Room Booking API",
    version="2.0.0",
    description=(
        "Create, list, inspect, and cancel campus study-room bookings. "
        "All errors use the ErrorResponse format."
    ),
)
install_error_handlers(app)

repository = BookingRepository()
create_faults = FaultInjector(repository)
create_rate_limiter = FixedWindowRateLimiter(
    limit=int(os.getenv("RATE_LIMIT_REQUESTS", "30")),
    window_seconds=float(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "10")),
)

cardinality_lock = Lock()
cardinality_request_ids = set()

CARDINALITY_MODE = os.getenv("CARDINALITY_MODE", "safe").lower()
if CARDINALITY_MODE not in {"safe", "unsafe"}:
    raise RuntimeError("CARDINALITY_MODE must be 'safe' or 'unsafe'")


# Application metrics
HTTP_REQUESTS = Counter(
    "room_booking_http_requests_total",
    "Total HTTP requests",
    ["method", "route", "status"],
)

HTTP_DURATION = Histogram(
    "room_booking_http_request_duration_seconds",
    "HTTP request duration in seconds, measured by the server middleware",
    ["method", "route"],
    buckets=LATENCY_BUCKETS,
)

HTTP_IN_FLIGHT = Gauge(
    "room_booking_http_in_flight_requests",
    "HTTP requests currently being handled",
    ["method", "route"],
)

RATE_LIMITED = Counter(
    "room_booking_rate_limited_total",
    "Requests rejected with 429 by the rate limiter",
    ["route"],
)


# Business metrics
ACTIVE_BOOKINGS = Gauge(
    "room_booking_active_bookings",
    "Current number of active bookings in the shared database",
)


def _count_active_bookings():
    try:
        return repository.count_active()
    except Exception:
        return float("nan")


# Read from the database at scrape time, so bookings created over gRPC count.
ACTIVE_BOOKINGS.set_function(_count_active_bookings)

VALIDATION_DURATION = Summary(
    "room_booking_validation_duration_seconds",
    "Time spent checking booking rules and storing the booking",
)

# This deliberately changes shape between experiment modes. In unsafe mode,
# every request ID creates a new time series. In safe mode, all requests share
# one series. The experiment client is capped at 100 IDs.
if CARDINALITY_MODE == "unsafe":
    CARDINALITY_DEMO = Counter(
        "demo_requests_total",
        "Requests used by the bounded cardinality experiment",
        ["request_id"],
    )
else:
    CARDINALITY_DEMO = Counter(
        "demo_requests_total",
        "Requests used by the bounded cardinality experiment",
    )


class BookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room: str = Field(
        min_length=rules.ROOM_MIN_LENGTH,
        max_length=rules.ROOM_MAX_LENGTH,
        examples=["Study-Room-101"],
    )
    duration_minutes: int = Field(
        ge=rules.DURATION_MIN_MINUTES,
        le=rules.DURATION_MAX_MINUTES,
        examples=[60],
    )


class Booking(BaseModel):
    id: str
    room: str
    duration_minutes: int
    created_at: str


class BookingList(BaseModel):
    bookings: list[Booking]


class FaultConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delay_ms: int = Field(default=0, ge=0, le=10_000)
    every_nth: int = Field(default=1, ge=1, le=100)
    fail_next: int = Field(default=0, ge=0, le=100)
    fail_always: bool = False


def write_log(severity, message, request_id, **extra):
    write_service_log(SERVICE_NAME, severity, message, request_id, **extra)


def resolve_route(scope):
    """Return the route template, or 'unmatched' to keep the label bounded."""
    for route in app.router.routes:
        match, _ = route.matches(scope)
        if match is not Match.NONE:
            return route.path
    return "unmatched"


@app.middleware("http")
async def observe_request(request: Request, call_next):
    started = time.perf_counter()
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    route = resolve_route(request.scope)

    HTTP_IN_FLIGHT.labels(method=request.method, route=route).inc()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        status = 500
        write_log(
            "ERROR",
            "unhandled request error",
            request_id,
            method=request.method,
            route=route,
        )
        response = error_response(
            request, 500, "internal_error", "Internal server error"
        )
    finally:
        HTTP_IN_FLIGHT.labels(method=request.method, route=route).dec()

    duration = time.perf_counter() - started

    HTTP_REQUESTS.labels(
        method=request.method,
        route=route,
        status=str(status),
    ).inc()

    HTTP_DURATION.labels(
        method=request.method,
        route=route,
    ).observe(duration)

    write_log(
        "INFO" if status < 400 else "WARNING",
        "request completed",
        request_id,
        method=request.method,
        route=route,
        status=status,
        duration_ms=round(duration * 1000, 2),
    )

    response.headers["X-Request-ID"] = request_id
    return response


def enforce_create_rate_limit(request: Request, response: Response):
    client = request.client.host if request.client else "unknown"
    decision = create_rate_limiter.check(client)
    if not decision.allowed:
        RATE_LIMITED.labels(route=BOOKINGS_PATH).inc()
        write_log(
            "WARNING",
            "request rejected by rate limiter",
            request.state.request_id,
            event_type="rate_limited",
            route=BOOKINGS_PATH,
            retry_after_seconds=decision.retry_after,
        )
        raise RateLimitExceeded(decision)
    response.headers.update(decision.headers())


@app.get("/health")
def health():
    return {"status": "healthy", "cardinality_mode": CARDINALITY_MODE}


@app.get(BOOKINGS_PATH, response_model=BookingList)
def list_bookings():
    return {"bookings": repository.list_bookings()}


@app.post(
    BOOKINGS_PATH,
    status_code=201,
    response_model=Booking,
    dependencies=[Depends(enforce_create_rate_limit)],
    responses={
        201: {
            "description": (
                "Booking created, or the stored booking returned again for a "
                "repeated Idempotency-Key (then Idempotent-Replayed: true)."
            ),
            "headers": {
                "Location": {
                    "description": "URL of the booking",
                    "schema": {"type": "string"},
                },
            },
        },
        **error_responses(409, 422, 429, 503),
    },
)
def create_booking(
    booking: BookingRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        max_length=rules.IDEMPOTENCY_KEY_MAX_LENGTH,
        description=(
            "Client-chosen unique key. Repeating a request with the same key "
            "and body returns the original booking instead of creating another."
        ),
    ),
):
    request_id = request.state.request_id

    try:
        create_faults.apply()
    except InjectedFault:
        write_log(
            "WARNING",
            "injected create-booking failure",
            request_id,
            event_type="fault_injected",
        )
        raise

    started = time.perf_counter()
    try:
        stored, replayed = repository.create_booking(
            booking.room,
            booking.duration_minutes,
            idempotency_key,
        )
    except RoomOccupied:
        BOOKING_EVENTS.labels(action="create", result="rejected").inc()
        write_log(
            "WARNING",
            "booking rejected because room is occupied",
            request_id,
            event_type="booking_rejected",
            room=booking.room,
            reason="room_occupied",
        )
        raise
    finally:
        VALIDATION_DURATION.observe(time.perf_counter() - started)

    response.headers["Location"] = f"{BOOKINGS_PATH}/{stored['id']}"

    if replayed:
        IDEMPOTENT_REPLAYS.inc()
        response.headers["Idempotent-Replayed"] = "true"
        write_log(
            "INFO",
            "booking create replayed from idempotency key",
            request_id,
            event_type="booking_replayed",
            booking_id=stored["id"],
        )
        return stored

    BOOKING_EVENTS.labels(action="create", result="success").inc()
    write_log(
        "INFO",
        "booking created",
        request_id,
        event_type="booking_created",
        booking_id=stored["id"],
        room=stored["room"],
        duration_minutes=stored["duration_minutes"],
    )
    return stored


@app.get(
    BOOKINGS_PATH + "/{booking_id}",
    response_model=Booking,
    responses=error_responses(404),
)
def get_booking(booking_id: str):
    return repository.get_booking(booking_id)


@app.delete(
    BOOKINGS_PATH + "/{booking_id}",
    status_code=204,
    response_class=Response,
    responses=error_responses(404),
)
def cancel_booking(booking_id: str, request: Request):
    try:
        cancelled = repository.cancel_booking(booking_id)
    except BookingNotFound:
        BOOKING_EVENTS.labels(action="cancel", result="rejected").inc()
        write_log(
            "WARNING",
            "booking cancellation rejected",
            request.state.request_id,
            event_type="booking_cancel_rejected",
            booking_id=booking_id,
            reason="booking_not_found",
        )
        raise

    BOOKING_EVENTS.labels(action="cancel", result="success").inc()
    write_log(
        "INFO",
        "booking cancelled",
        request.state.request_id,
        event_type="booking_cancelled",
        booking_id=booking_id,
        room=cancelled["room"],
    )
    return Response(status_code=204)


@app.get("/v1/admin/faults/create-booking", response_model=FaultConfig)
def get_create_booking_fault():
    return repository.get_fault(CREATE_BOOKING)


@app.put(
    "/v1/admin/faults/create-booking",
    response_model=FaultConfig,
    responses=error_responses(422),
)
def configure_create_booking_fault(config: FaultConfig, request: Request):
    """Replace the fault settings for create-booking (HTTP and gRPC).

    Send all-default values (`{}`) to remove the fault.
    """
    stored = repository.set_fault(CREATE_BOOKING, **config.model_dump())
    enabled = bool(stored["delay_ms"] or stored["fail_next"] or stored["fail_always"])
    write_log(
        "WARNING" if enabled else "INFO",
        "create-booking fault configuration changed",
        request.state.request_id,
        event_type="fault_configuration_changed",
        **{f"fault_{name}": value for name, value in stored.items()},
    )
    return stored


@app.post("/demo/cardinality", responses=error_responses(429))
def cardinality_demo(request_id: str, request: Request):
    with cardinality_lock:
        if request_id not in cardinality_request_ids:
            if len(cardinality_request_ids) >= 100:
                return error_response(
                    request,
                    429,
                    "cardinality_cap_reached",
                    "Cardinality demo is capped at 100 unique IDs",
                )
            cardinality_request_ids.add(request_id)

    if CARDINALITY_MODE == "unsafe":
        CARDINALITY_DEMO.labels(request_id=request_id).inc()
    else:
        CARDINALITY_DEMO.inc()

    write_log(
        "INFO",
        "cardinality demonstration request recorded",
        request.state.request_id,
        event_type="cardinality_demo",
        cardinality_mode=CARDINALITY_MODE,
        unique_ids=len(cardinality_request_ids),
    )

    return {
        "mode": CARDINALITY_MODE,
        "unique_ids": len(cardinality_request_ids),
        "maximum_unique_ids": 100,
    }


@app.get("/metrics")
def metrics():
    return Response(
        content=generate_latest(),
        media_type="text/plain; version=0.0.4",
    )
