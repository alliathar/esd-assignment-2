"""Call POST /v1/bookings like another service would.

Reliability settings (defaults):
  timeout          1.0 s per attempt
  retries          at most 3 attempts per call; backoff 0.25 s, 0.5 s (+<=0.1 s jitter)
                   only on timeouts, connection errors, 502/503/504, and 429
                   (429 waits for Retry-After). Safe because each call sends one
                   Idempotency-Key that every attempt reuses.
  circuit breaker  opens after 3 consecutive failed attempts; refuses calls
                   for 5 s; then one trial call decides CLOSED or OPEN again.

Usage (from the project root):
  python -m clients.http_booking_client --room Study-Room-101 --duration 60
  python -m clients.http_booking_client --demo --output evidence/a2/http-client-demo.txt
"""

import argparse
import json
import socket
import time
import uuid
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from clients.resilience import CircuitBreaker, CircuitOpenError, backoff_seconds

RETRYABLE_STATUSES = {502, 503, 504}


class TransientError(Exception):
    pass


def send(base_url, method, path, payload=None, headers=None, timeout=1.0):
    """Return (status, body, headers); raise TransientError on timeout/network."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"

    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers=request_headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}, response.headers
    except HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw) if raw else {}, error.headers
    except (TimeoutError, socket.timeout) as error:
        raise TransientError(f"timed out after {timeout:g}s") from error
    except URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            raise TransientError(f"timed out after {timeout:g}s") from error
        raise TransientError(f"connection failed: {error.reason}") from error
    except ConnectionError as error:
        raise TransientError(f"connection failed: {error}") from error


class Reporter:
    def __init__(self, output_path=None):
        self.lines = []
        self.output_path = output_path

    def __call__(self, message=""):
        line = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {message}" if message else ""
        print(line, flush=True)
        self.lines.append(line)

    def save(self):
        if self.output_path:
            with open(self.output_path, "w", encoding="utf-8") as file:
                file.write("\n".join(self.lines) + "\n")
            print(f"Saved output to {self.output_path}")


class BookingHttpClient:
    def __init__(self, base_url, timeout=1.0, max_attempts=3, breaker=None, report=print):
        self.base_url = base_url
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.breaker = breaker or CircuitBreaker()
        self.report = report

    def create_booking(self, room, duration_minutes, label="call"):
        idempotency_key = str(uuid.uuid4())
        payload = {"room": room, "duration_minutes": duration_minutes}

        for attempt in range(1, self.max_attempts + 1):
            prefix = f"{label} attempt {attempt}/{self.max_attempts}"
            try:
                self.breaker.before_call()
            except CircuitOpenError as error:
                self.report(f"{prefix}: BLOCKED by breaker ({error}); server not contacted")
                return {"outcome": "blocked"}

            state = self.breaker.state
            try:
                status, body, headers = send(
                    self.base_url,
                    "POST",
                    "/v1/bookings",
                    payload,
                    {
                        "Idempotency-Key": idempotency_key,
                        "X-Request-ID": f"{label}-{attempt}-{idempotency_key[:8]}",
                    },
                    self.timeout,
                )
            except TransientError as error:
                self.breaker.record_failure()
                problem = str(error)
            else:
                if status == 429:
                    wait = int(headers.get("Retry-After", "1"))
                    self.report(f"{prefix} [{state}]: 429 rate limited; waiting Retry-After {wait}s")
                    time.sleep(wait)
                    continue
                if status in RETRYABLE_STATUSES:
                    self.breaker.record_failure()
                    problem = f"HTTP {status} {body.get('error', {}).get('code', '')}"
                else:
                    # The server answered; 4xx are caller mistakes, not outages.
                    self.breaker.record_success()
                    replayed = headers.get("Idempotent-Replayed") == "true"
                    detail = (
                        f"booking {body['id'][:8]} room={body['room']}"
                        + (" (Idempotent-Replayed: true)" if replayed else "")
                        if status == 201
                        else json.dumps(body.get("error", body))
                    )
                    self.report(f"{prefix} [{state}]: HTTP {status} {detail} -> breaker {self.breaker.state}")
                    outcome = "created" if status == 201 else "rejected"
                    return {"outcome": outcome, "status": status, "body": body}

            if attempt < self.max_attempts and self.breaker.state == "CLOSED":
                delay = backoff_seconds(attempt)
                self.report(
                    f"{prefix} [{state}]: {problem} -> failures="
                    f"{self.breaker.consecutive_failures}, retrying in {delay:.2f}s"
                )
                time.sleep(delay)
            else:
                self.report(
                    f"{prefix} [{state}]: {problem} -> failures="
                    f"{self.breaker.consecutive_failures}, breaker {self.breaker.state}"
                )
                if self.breaker.state != "CLOSED":
                    break

        return {"outcome": "failed"}


def set_fault(base_url, report, **fault):
    status, body, _ = send(
        base_url, "PUT", "/v1/admin/faults/create-booking", fault, timeout=5
    )
    if status != 200:
        raise SystemExit(f"Could not configure fault: HTTP {status} {body}")
    report(f"ADMIN fault set to {body}")


def run_demo(base_url, report):
    breaker = CircuitBreaker(failure_threshold=3, recovery_seconds=5.0)
    client = BookingHttpClient(base_url, timeout=1.0, max_attempts=3, breaker=breaker, report=report)
    prefix = uuid.uuid4().hex[:6]
    room = lambda n: f"Caller-{prefix}-{n}"

    report(
        "Settings: timeout=1.0s per attempt, max_attempts=3, backoff=0.25s*2^n (+jitter), "
        "breaker: open after 3 consecutive failures, recovery=5s, 1 half-open trial"
    )
    set_fault(base_url, report)

    report()
    report("== 1. Normal operation ==")
    client.create_booking(room(1), 60, "call-1")
    client.create_booking(room(2), 60, "call-2")

    report()
    report("== 2. Transient failure: the next request fails once with 503 ==")
    set_fault(base_url, report, fail_next=1)
    client.create_booking(room(3), 60, "call-3")

    report()
    report("== 3. Outage: every request is slow (2 s) and then fails ==")
    set_fault(base_url, report, delay_ms=2000, fail_always=True)
    client.create_booking(room(4), 60, "call-4")
    client.create_booking(room(5), 60, "call-5")
    client.create_booking(room(6), 60, "call-6")

    report()
    report("== 4. Recovery: fault removed; wait for the breaker's recovery period ==")
    set_fault(base_url, report)
    report(f"waiting {breaker.recovery_seconds:g}s (breaker {breaker.state})")
    time.sleep(breaker.recovery_seconds + 0.2)
    report(f"breaker is now {breaker.state}")
    client.create_booking(room(7), 60, "call-7")
    client.create_booking(room(8), 60, "call-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--room", default="Study-Room-101")
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--calls", type=int, default=1, help="calls to make (rooms get a numeric suffix)")
    parser.add_argument("--demo", action="store_true", help="run the success/retry/breaker/recovery scenario")
    parser.add_argument("--output", help="also save the printed lines to this file")
    args = parser.parse_args()

    report = Reporter(args.output)
    if args.demo:
        run_demo(args.base_url, report)
    else:
        client = BookingHttpClient(args.base_url, report=report)
        for number in range(1, args.calls + 1):
            room = args.room if args.calls == 1 else f"{args.room}-{number}"
            client.create_booking(room, args.duration, f"call-{number}")
    report.save()


if __name__ == "__main__":
    main()
