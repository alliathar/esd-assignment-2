# Campus Room Booking — Assignment 2 (HTTP API, gRPC, Metrics)

This project extends the Assignment 1 room-booking service (tagged
`a1-submission` in git; its report is `report-assignment1.md`).

What is new:

- A cleaned-up, versioned HTTP API (`/v1/bookings`) with one error format,
  an OpenAPI document, `Idempotency-Key` support, and a rate limit.
- A Python caller with a timeout, retry with backoff, and a circuit breaker.
- A gRPC version of **create booking** in its own container. It calls the same
  `booking_core` rules against the same SQLite database as the HTTP API.
- Prometheus metrics for the gRPC service and a Grafana row comparing HTTP and gRPC.

The A2 report is [`report.md`](report.md).

## Requirements

- Docker Desktop with Docker Compose v2 (about 6 GB free RAM for the Elastic stack)
- Python 3.10+ on the host for the clients and tests:

```powershell
pip install -r requirements-dev.txt
```

## Start the system

```powershell
docker compose up -d --build --wait
docker compose ps
```

| Component | URL / address | Purpose |
|---|---|---|
| Booking HTTP API | <http://localhost:8000/docs> | Swagger UI generated from the OpenAPI document |
| OpenAPI document | <http://localhost:8000/openapi.json> · [`openapi/openapi.yaml`](openapi/openapi.yaml) | API contract |
| Booking gRPC service | `localhost:50051` | `roombooking.v1.BookingService/CreateBooking` |
| gRPC service metrics | <http://localhost:9102/metrics> | Scraped by Prometheus as `room-booking-grpc` |
| Prometheus targets | <http://localhost:9090/targets> | Four targets should be UP |
| Grafana | <http://localhost:3000> (`admin` / `admin`) | Dashboard **Room Booking Observability** |
| Kibana | <http://localhost:5601/app/discover> | Logs (data view `room-booking-logs-*`, time field `@timestamp`) |

Bookings are stored in `/data/bookings.db` on the `booking-data` volume, which
both booking containers mount. Unlike in A1, bookings survive restarts.

## HTTP API

| Method and path | Success | Errors |
|---|---|---|
| `POST /v1/bookings` (optional `Idempotency-Key` header) | 201 + `Location` | 409 room occupied, 422 invalid, 429 rate limited, 503 injected fault |
| `GET /v1/bookings` | 200 | — |
| `GET /v1/bookings/{id}` | 200 | 404 |
| `DELETE /v1/bookings/{id}` | 204 | 404 |
| `GET` / `PUT /v1/admin/faults/create-booking` | 200 | 422 |

Every error looks like:

```json
{"error": {"code": "room_occupied", "message": "Room already has an active booking",
           "request_id": "…", "details": []}}
```

Try it from PowerShell:

```powershell
$body = @{ room = "Study-Room-101"; duration_minutes = 60 } | ConvertTo-Json
$headers = @{ "Idempotency-Key" = [guid]::NewGuid().ToString() }
Invoke-WebRequest -Method Post -Uri http://localhost:8000/v1/bookings `
    -ContentType "application/json" -Body $body -Headers $headers
```

Sending the same request again with the same key returns the same booking
with `Idempotent-Replayed: true`. Without a key, a second booking for the same
room returns `409`.

**Rate limit:** `POST /v1/bookings` accepts 30 requests per 10-second window
per client IP address. After that it returns `429` with `Retry-After`. You can
change it with `RATE_LIMIT_REQUESTS` and `RATE_LIMIT_WINDOW_SECONDS` in
`docker-compose.yml`.

**Fault injection** applies to create-booking over both HTTP and gRPC, because
it is stored in the shared database:

```powershell
# 500 ms delay on every 5th create (the A1 experiment)
Invoke-RestMethod -Method Put -Uri http://localhost:8000/v1/admin/faults/create-booking `
    -ContentType "application/json" -Body '{"delay_ms": 500, "every_nth": 5}'
# remove every fault
Invoke-RestMethod -Method Put -Uri http://localhost:8000/v1/admin/faults/create-booking `
    -ContentType "application/json" -Body '{}'
```

## gRPC service

The contract is [`booking_proto/booking.proto`](booking_proto/booking.proto).
Regenerate the Python code after changing it (the Docker image also does this
at build time):

```powershell
python -m grpc_tools.protoc -I . --python_out=. --grpc_python_out=. --pyi_out=. booking_proto/booking.proto
# same command: scripts\generate_proto.ps1  or  sh scripts/generate_proto.sh
```

Call it with the Python client (run every client from the project root with `-m`):

```powershell
python -m clients.grpc_booking_client --room Study-Room-201 --duration 60
python -m clients.grpc_booking_client --room Study-Room-201 --duration 60   # ALREADY_EXISTS
python -m clients.grpc_booking_client --room Study-Room-202 --duration 5    # INVALID_ARGUMENT
python -m clients.grpc_booking_client --room Study-Room-203 --deadline 0.5 --request-id my-trace-1
```

## Demonstrations (Part A–C evidence)

| Command | Shows | Saved output |
|---|---|---|
| `python -m clients.http_booking_client --demo --output evidence/a2/http-client-demo.txt` | success, retry after 503, breaker OPEN and blocking, recovery | `evidence/a2/http-client-demo.txt` |
| `python -m clients.rate_limit_demo --output evidence/a2/rate-limit-demo.txt` | 201s, then 429 + `Retry-After`, then 201 after waiting | `evidence/a2/rate-limit-demo.txt` |
| `python -m clients.compare_transports --output evidence/a2/transport-comparison.txt` | the same success and failures over HTTP and gRPC; cross-transport conflicts | `evidence/a2/transport-comparison.txt` |
| `python -m clients.openapi_contract_check --output evidence/a2/openapi-contract-check.txt` | live responses checked against `openapi.yaml` | `evidence/a2/openapi-contract-check.txt` |
| `python -m clients.grpc_booking_client --demo --rounds 3` | repeatable gRPC successes and failures for the Grafana row | `evidence/a2/grpc-client-demo.txt` |
| `python experiments\load_test.py --duration 120` | mixed HTTP traffic (A1 script, updated paths) | — |

The HTTP demo changes the fault configuration and resets it at the end. If you
stop it part-way, reset the fault with the `PUT … -Body '{}'` command above.

The A1 experiments still work against the new paths:
`experiments\anomaly_experiment.py` and `experiments\cardinality_test.py`
(see `report-assignment1.md`).

## Tests

```powershell
python -m pytest
```

The 38 tests cover the shared rules and database, the HTTP API (valid,
invalid, repeated with and without a key, delete twice, rate limit, error
format, bounded route label), the gRPC service (success, each error code, the
deadline, request-ID echo, metrics), the circuit breaker, and the OpenAPI
document. After changing routes, regenerate the committed spec:

```powershell
python scripts\export_openapi.py
```

## Stop and clean up

```powershell
docker compose down        # stop; keep bookings, metrics and logs
docker compose down -v     # also delete all volumes, including booking-data (irreversible)
```

## Project layout

```text
booking_core/           shared rules, SQLite repository, fault injector, log/metric helpers
app/                    FastAPI HTTP API (main.py), error format, rate limiter
booking_proto/          booking.proto and generated booking_pb2*.py
grpc_service/           gRPC server and observability interceptor
clients/                HTTP caller (retry/breaker), gRPC client, demos, OpenAPI check
experiments/            A1 load, anomaly, and cardinality scripts (updated paths)
tests/                  pytest suite
openapi/openapi.yaml    exported OpenAPI 3.1 document
monitoring/             Prometheus, Grafana, Filebeat, and Kibana configuration
scripts/                proto generation and OpenAPI export
evidence/a2/            A2 outputs and screenshots
```

## Credits

- Assignment 1 and the course's Lab 1: Midnight Launch were the base for the
  Docker, Prometheus, Grafana, Filebeat, Elasticsearch, and Kibana setup.
- gRPC Python documentation (grpc.io) for the server, interceptors, deadlines,
  and status codes; FastAPI documentation for error handlers and OpenAPI.
- OpenAI Codex helped with the A1 scaffolding. Claude (Anthropic) helped
  design and write the A2 code, tests, and documentation. All behaviour and
  results should be reviewed and explained by the submitting student
  according to course policy.
