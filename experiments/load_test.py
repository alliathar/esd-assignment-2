"""Generate repeatable room-booking traffic for dashboard analysis."""

import argparse
import json
import time
from collections import Counter, deque
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def call_api(base_url, method, path, payload=None):
    data = None
    headers = {"Accept": "application/json"}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except HTTPError as error:
        body = error.read().decode("utf-8")
        return error.code, json.loads(body) if body else {}


def set_slow_fault(base_url, enabled):
    # A1 fault: 500 ms delay on every fifth create request.
    fault = {"delay_ms": 500, "every_nth": 5} if enabled else {}
    status, _ = call_api(
        base_url,
        "PUT",
        "/v1/admin/faults/create-booking",
        fault,
    )
    if status != 200:
        raise RuntimeError(f"Could not set slow fault: HTTP {status}")


def run_load(base_url, duration, interval, enable_fault):
    run_id = int(time.time())
    deadline = time.monotonic() + duration
    active_ids = deque()
    results = Counter()
    iteration = 0

    set_slow_fault(base_url, enable_fault)
    print(
        f"Generating traffic for {duration}s against {base_url} "
        f"(slow fault: {enable_fault})"
    )

    try:
        while time.monotonic() < deadline:
            iteration += 1
            booking = {
                "room": f"Load-{run_id}-{iteration}",
                "duration_minutes": 60,
            }

            status, created = call_api(
                base_url,
                "POST",
                "/v1/bookings",
                booking,
            )
            results[f"create_{status}"] += 1

            if status == 201:
                active_ids.append(created["id"])

            # Re-submit an occupied room to produce a controlled 409.
            if iteration % 4 == 0:
                duplicate_status, _ = call_api(
                    base_url,
                    "POST",
                    "/v1/bookings",
                    booking,
                )
                results[f"duplicate_{duplicate_status}"] += 1

            # Cancel older bookings so the active-bookings gauge moves down too.
            if iteration % 3 == 0 and active_ids:
                booking_id = active_ids.popleft()
                cancel_status, _ = call_api(
                    base_url,
                    "DELETE",
                    f"/v1/bookings/{booking_id}",
                )
                results[f"cancel_{cancel_status}"] += 1

            # Exercise the read endpoint as part of the traffic mix.
            if iteration % 2 == 0:
                list_status, _ = call_api(base_url, "GET", "/v1/bookings")
                results[f"list_{list_status}"] += 1

            if iteration % 10 == 0:
                print(f"cycles={iteration} results={dict(results)}")

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nLoad test stopped by user.")
    finally:
        if enable_fault:
            set_slow_fault(base_url, False)

    print(f"Finished after {iteration} cycles: {dict(results)}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate booking traffic for Prometheus and Grafana."
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Room-booking API URL",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=120,
        help="Test duration in seconds",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Delay between cycles in seconds",
    )
    parser.add_argument(
        "--fault",
        action="store_true",
        help="Enable the 500 ms every-fifth-create fault during the run",
    )
    args = parser.parse_args()

    try:
        run_load(
            args.base_url,
            args.duration,
            args.interval,
            args.fault,
        )
    except (URLError, ConnectionError) as error:
        raise SystemExit(
            f"Could not reach {args.base_url}. Is docker compose running? {error}"
        ) from error


if __name__ == "__main__":
    main()
