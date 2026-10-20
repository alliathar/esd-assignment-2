# Evidence inventory

The following evidence was captured from the local Compose stack on 26
September 2026:

- `prometheus-targets.png` — all three scrape targets are UP.
- `grafana-normal.png` — dashboard during the baseline stage.
- `grafana-slow-fault.png` — dashboard while the slow fault is enabled.
- `grafana-recovery.png` — dashboard after recovery.
- `kibana-request-search.png` — request-ID search with structured fields.
- `kibana-error-search.png` — working warning search.
- `cardinality-unsafe.png` — `count(demo_requests_total)` equals 100.
- `cardinality-safe.png` — the same query equals 1.
- `anomaly-results.json` — exact client-side anomaly measurements.

Use a visible time range and include panel titles or query text in each capture.

## Assignment 2 (`evidence/a2/`, captured 20 October 2026)

- `http-client-demo.txt`: caller success, retry after 503, breaker open/blocked, recovery.
- `rate-limit-demo.txt`: 30 accepted, `429` + `Retry-After: 10`, accepted after waiting.
- `transport-comparison.txt`: success, invalid input, and cross-transport conflict over HTTP and gRPC.
- `grpc-client-demo.txt`: repeatable gRPC successes and failures used to fill the Grafana row.
- `grpc-deadline-demo.txt`: four overlapping slow calls (in-flight = 4) ending in `DEADLINE_EXCEEDED`.
- `openapi-validation.txt`, `openapi-contract-check.txt`: spec validation and live responses checked against it.
- Still to capture: `swagger-create-booking.png`, `prometheus-targets.png`,
  `grafana-http-vs-grpc.png`, `kibana-grpc-request-id.png`.
