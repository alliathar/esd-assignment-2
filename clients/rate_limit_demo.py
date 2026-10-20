"""Show the POST /v1/bookings rate limit: accepted calls, 429 + Retry-After,
and acceptance again once the window has passed.

Usage: python -m clients.rate_limit_demo [--output evidence/a2/rate-limit-demo.txt]
"""

import argparse
import time
import uuid

from clients.http_booking_client import Reporter, send


def post(base_url, room):
    status, body, headers = send(
        base_url,
        "POST",
        "/v1/bookings",
        {"room": room, "duration_minutes": 30},
        timeout=5,
    )
    return status, body, headers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--extra", type=int, default=3, help="calls to send beyond the limit")
    parser.add_argument("--output")
    args = parser.parse_args()

    report = Reporter(args.output)
    prefix = uuid.uuid4().hex[:6]
    created = []

    number = 0
    limit = None
    retry_after = None
    rejected = 0
    while rejected < args.extra:
        number += 1
        status, body, headers = post(args.base_url, f"Limit-{prefix}-{number}")
        limit = limit or headers.get("RateLimit-Limit")
        if status == 201:
            created.append(body["id"])
            report(
                f"request {number:2}: 201 Created   RateLimit-Remaining="
                f"{headers.get('RateLimit-Remaining')} (limit {headers.get('RateLimit-Limit')})"
            )
        elif status == 429:
            rejected += 1
            retry_after = int(headers["Retry-After"])
            report(
                f"request {number:2}: 429 Too Many Requests  Retry-After={retry_after}  "
                f"body={body['error']['message']}"
            )
        else:
            report(f"request {number:2}: unexpected HTTP {status} {body}")
            break

    if retry_after:
        report(f"waiting Retry-After = {retry_after}s ...")
        time.sleep(retry_after)
        status, body, headers = post(args.base_url, f"Limit-{prefix}-after-wait")
        if status == 201:
            created.append(body["id"])
        report(f"after waiting: HTTP {status}  RateLimit-Remaining={headers.get('RateLimit-Remaining')}")

    for booking_id in created:
        send(args.base_url, "DELETE", f"/v1/bookings/{booking_id}", timeout=5)
    report(f"cleaned up {len(created)} demo bookings")
    report.save()


if __name__ == "__main__":
    main()
