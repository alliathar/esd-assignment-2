"""Run repeatable baseline, slow-fault, and recovery booking stages."""

import argparse
import json
import math
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def call_api(base_url, method, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"

    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )

    started = time.perf_counter()
    try:
        with urlopen(request, timeout=10) as response:
            response.read()
            return response.status, time.perf_counter() - started
    except HTTPError as error:
        error.read()
        return error.code, time.perf_counter() - started


def set_fault(base_url, enabled):
    # A1 fault: 500 ms delay on every fifth create request.
    fault = {"delay_ms": 500, "every_nth": 5} if enabled else {}
    status, _ = call_api(
        base_url, "PUT", "/v1/admin/faults/create-booking", fault
    )
    if status != 200:
        raise RuntimeError(f"Could not set fault state: HTTP {status}")


def percentile(values, percentile_value):
    ordered = sorted(values)
    index = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return ordered[index]


def run_stage(base_url, name, fault_enabled, request_count, interval):
    set_fault(base_url, fault_enabled)
    latencies = []
    statuses = Counter()
    prefix = uuid.uuid4().hex[:8]

    print(f"Starting {name}: fault={fault_enabled}, requests={request_count}")
    stage_started = datetime.now(timezone.utc).isoformat()

    for number in range(1, request_count + 1):
        payload = {
            "room": f"{name[:4]}-{prefix}-{number}",
            "duration_minutes": 60,
        }
        status, latency = call_api(base_url, "POST", "/v1/bookings", payload)
        statuses[str(status)] += 1
        latencies.append(latency)
        time.sleep(interval)

    result = {
        "stage": name,
        "fault_enabled": fault_enabled,
        "started_at": stage_started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "requests": request_count,
        "statuses": dict(statuses),
        "latency_ms": {
            "average": round(sum(latencies) / len(latencies) * 1000, 3),
            "p50": round(percentile(latencies, 0.50) * 1000, 3),
            "p95": round(percentile(latencies, 0.95) * 1000, 3),
            "maximum": round(max(latencies) * 1000, 3),
        },
    }
    print(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Compare normal, injected-slow, and recovered booking traffic."
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--requests-per-stage", type=int, default=30)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--stage-gap", type=float, default=6.0)
    parser.add_argument(
        "--output",
        default="evidence/anomaly-results.json",
    )
    args = parser.parse_args()

    if args.requests_per_stage < 10:
        raise SystemExit("Use at least 10 requests per stage for a meaningful p95.")

    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "requests_per_stage": args.requests_per_stage,
            "interval_seconds": args.interval,
            "gap_seconds": args.stage_gap,
            "prometheus_scrape_seconds": 2,
        },
        "stages": [],
    }

    try:
        results["stages"].append(
            run_stage(
                args.base_url,
                "baseline",
                False,
                args.requests_per_stage,
                args.interval,
            )
        )
        time.sleep(args.stage_gap)
        results["stages"].append(
            run_stage(
                args.base_url,
                "slow_fault",
                True,
                args.requests_per_stage,
                args.interval,
            )
        )
        time.sleep(args.stage_gap)
        results["stages"].append(
            run_stage(
                args.base_url,
                "recovery",
                False,
                args.requests_per_stage,
                args.interval,
            )
        )
    except URLError as error:
        raise SystemExit(
            f"Could not reach {args.base_url}. Is docker compose running? {error}"
        ) from error
    finally:
        try:
            set_fault(args.base_url, False)
        except Exception:
            pass

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Saved experiment results to {output_path}")


if __name__ == "__main__":
    main()
