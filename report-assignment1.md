# Assignment 1: Observability

All measurements in this report were collected from the local Compose stack on
26 September 2026. Times are stated in UTC; the Grafana screenshots display
the equivalent Asia/Karachi time (UTC+05:00).

## Part A — Project

### Problem and users

Campus students need a simple way to reserve study rooms, while campus staff
need to see whether the service is healthy and whether bookings succeed. A
failure can be technical, such as a slow HTTP request, or business-related,
such as attempts to reserve an already occupied room.

### Solution

The project is a FastAPI room-booking service. A user can create, list, and
cancel bookings. The application rejects duplicate active bookings for the
same room. Bookings are held in memory to keep the business application small;
the assignment focuses on the surrounding observability system rather than
database implementation.

The API is available through Swagger at `http://localhost:8000/docs`. The
complete environment starts with `docker compose up -d --build`. Usage,
experiments, and safe cleanup are documented in `README.md`.

### What works

- Create, list, and cancel bookings.
- Validate room names and durations from 15 to 180 minutes.
- Reject an already occupied room with HTTP 409.
- Add or propagate an `X-Request-ID` for every request.
- Emit application and business metrics.
- Emit structured JSON request and business-event logs.
- Inject a controlled 500 ms delay into every fifth create request.
- Run a bounded 100-ID cardinality demonstration.

## Part B — Metrics

Prometheus scrapes the application, Node Exporter, and Prometheus itself every
two seconds. Prometheus data persists in the `prometheus-data` volume for seven
days. Grafana reads Prometheus through its Docker-network address,
`http://prometheus:9090`.

### Application and business metrics

| Metric | Purpose | Type | Unit | Labels | Recorded in code | Grafana query / meaning |
|---|---|---|---|---|---|---|
| `room_booking_http_requests_total` | Count HTTP requests and failures | Counter | requests | `method`, `route`, `status` | `observe_request` middleware after each request | `sum by (status) (rate(room_booking_http_requests_total{route!~"/metrics\|/health"}[1m]))`; recent requests per second by HTTP status |
| `room_booking_http_request_duration_seconds` | Measure request latency and tail behavior | Histogram | seconds | `method`, `route`, `le` | `observe_request` calls `observe(duration)` | `histogram_quantile(0.95, sum by (le) (rate(room_booking_http_request_duration_seconds_bucket{route="/bookings",method="POST"}[5m])))`; estimated POST p95 over the last five minutes |
| `room_booking_events_total` | Count business actions and outcomes | Counter | events | `action`, `result` | Create/cancel endpoints | `sum by (action, result) (room_booking_events_total)`; cumulative successful and rejected business actions |
| `room_booking_active_bookings` | Show current outstanding bookings | Gauge | bookings | none | Incremented after creation and decremented after cancellation | `room_booking_active_bookings`; current active bookings |
| `room_booking_validation_duration_seconds` | Measure time spent checking booking rules | Summary | seconds | none | Create endpoint around the room-availability check | `room_booking_validation_duration_seconds_sum / clamp_min(room_booking_validation_duration_seconds_count, 1)`; average validation duration since process start |
| `demo_requests_total` | Demonstrate cardinality cost safely | Counter | requests | `request_id` only in unsafe mode | `/demo/cardinality` | `count(demo_requests_total)`; current number of series |

Python's Prometheus client Summary does not calculate configurable quantiles.
Therefore, the dashboard reports the Summary's average using `_sum / _count`
and obtains p95 from the Histogram. The p95 chart uses a five-minute `rate()`
window: each point estimates the duration at or below which 95% of POST booking
requests fell during the preceding five minutes. It is an estimate derived
from bucket boundaries, not an exact sorted percentile.

### Explored chart

The dashboard includes a self-explored **Booking Rejection Rate** chart:

```promql
100 *
(sum(rate(room_booking_events_total{result="rejected"}[5m])) or vector(0))
/
clamp_min(
  (sum(rate(room_booking_events_total[5m])) or vector(0)),
  0.000001
)
```

This converts business outcomes into a percentage. It distinguishes a healthy
API that is rejecting invalid business operations from a technically failing
API. A high rejection percentage can indicate users repeatedly selecting
occupied rooms even when HTTP latency and infrastructure utilization are low.

### Machine metrics

Node Exporter measures the machine named **Docker Desktop Linux VM**. It does
not represent the complete Windows host.

| Resource | Query | Interpretation |
|---|---|---|
| CPU | `100 * (1 - avg(rate(node_cpu_seconds_total{job="node-exporter",mode="idle"}[5m])))` | Percentage of non-idle CPU over five minutes |
| Memory | `100 * (1 - node_memory_MemAvailable_bytes{job="node-exporter"} / node_memory_MemTotal_bytes{job="node-exporter"})` | Percentage of memory not currently available |
| Disk | `100 * (1 - node_filesystem_avail_bytes{job="node-exporter",mountpoint="/var/lib"} / node_filesystem_size_bytes{job="node-exporter",mountpoint="/var/lib"})` | Used percentage of Docker Desktop's data filesystem |
| Network receive | `sum(rate(node_network_receive_bytes_total{job="node-exporter",device!="lo"}[5m]))` | Received bytes per second excluding loopback |
| Network transmit | `sum(rate(node_network_transmit_bytes_total{job="node-exporter",device!="lo"}[5m]))` | Sent bytes per second excluding loopback |

The versioned dashboard is
`monitoring/grafana/dashboards/room-booking-observability.json`.

All three Prometheus scrape targets were `UP` during the experiment:

![Prometheus targets showing the API, Node Exporter, and Prometheus as UP](evidence/prometheus-targets.png)

The complete dashboard under normal load is shown in the baseline evidence in
Part E.

## Part C — Logs

### Events and fields

The service logs both request completions and business events. Request logs are
useful for latency, status, and correlation. Business logs explain why a
booking was created, cancelled, or rejected. Fault-configuration and
cardinality-demo events make experiments auditable.

Every relevant event contains:

- `@timestamp`
- `service.name`
- `log.level` and `severity`
- `message`
- `request_id`
- event-specific fields such as `method`, `route`, `status`, `duration_ms`,
  `event_type`, `booking_id`, or `reason`

The application does not log secrets or personal information. Room identifiers
and generated booking/request IDs are operational values used by this local
demonstration.

One original application event, captured from the API container during the
metric trace at `2026-09-26T12:44:14.784483Z`, was:

```json
{
  "@timestamp": "2026-09-26T12:44:14.784483+00:00",
  "service": {"name": "room-booking-api"},
  "log": {"level": "info"},
  "severity": "INFO",
  "message": "request completed",
  "request_id": "report-metric-trace-20260926",
  "method": "POST",
  "route": "/bookings",
  "status": 201,
  "duration_ms": 6.19
}
```

Filebeat decoded the line and Elasticsearch stored the values as separate,
typed fields. The most relevant stored values were:

| Stored field | Stored value |
|---|---|
| `@timestamp` | `2026-09-26T12:44:14.784Z` |
| `service.name` | `room-booking-api` |
| `log.level` / `severity` | `info` / `INFO` |
| `message` | `request completed` |
| `request_id` | `report-metric-trace-20260926` |
| `method` / `route` / `status` | `POST` / `/bookings` / `201` |
| `duration_ms` | `6.19` |

### Collection and parsing

1. `write_log` serializes a Python dictionary as one JSON line on stdout.
2. Docker's `json-file` driver wraps and stores container stdout.
3. Filebeat Docker autodiscovery selects the application because it has the
   label `co.elastic.logs/enabled=true`.
4. Filebeat's container parser removes Docker's envelope.
5. `decode_json_fields` expands the application JSON into searchable fields.
6. Filebeat writes the event to `room-booking-logs-YYYY.MM.DD`.
7. Kibana's `room-booking-logs-*` data view makes the fields searchable.

### Storage, restart behavior, and deletion

Docker rotates the immediate container logs at three files of 10 MB each.
Filebeat's registry is stored in the `filebeat-data` named volume, so it
remembers its read location after a normal restart. Elasticsearch indices live
in the `elasticsearch-data` named volume and also survive container restarts.
This local classroom configuration has no automatic Elasticsearch deletion;
indices remain until the volume is deliberately removed with
`docker compose down -v`. Production would use an authenticated cluster and an
index lifecycle retention policy.

### Searches

```text
service.name: "room-booking-api"
```

```text
service.name: "room-booking-api" and log.level: "warning"
```

```text
event_type: "booking_rejected"
```

```text
request_id: "<copied request ID>"
```

Searching the request ID returned both the `booking_created` business event and
the `request completed` HTTP event, demonstrating correlation between the two:

![Kibana request-ID search showing parsed fields](evidence/kibana-request-search.png)

The warning search also returned fault-configuration events and rejected
operations:

![Kibana warning search](evidence/kibana-error-search.png)

## Part D — System Design

### Architecture diagram

```mermaid
flowchart LR
    User[Student / Experiment Client] -->|HTTP :8000| API[FastAPI Room Booking Service]

    API -->|In-memory CRUD| Memory[(Booking Dictionary)]
    API -->|GET /metrics| Metrics[Prometheus Metric Registry]
    API -->|JSON stdout| DockerLogs[(Docker JSON Logs)]

    Prom[Prometheus :9090] -->|Scrape every 2 s| Metrics
    Node[Node Exporter :9100] -->|CPU / memory / disk / network| Prom
    Prom -->|7-day TSDB volume| PromData[(prometheus-data)]
    Grafana[Grafana :3000] -->|PromQL| Prom

    Filebeat[Filebeat] -->|Read and decode| DockerLogs
    Filebeat -->|Indexed JSON events| ES[Elasticsearch :9200]
    ES --> ESData[(elasticsearch-data)]
    Kibana[Kibana :5601] -->|KQL / Discover| ES
```

All components share the Compose network. Only browser-facing ports are
published to the host. Named volumes separate persistent monitoring data from
replaceable containers.

### Failure behavior

| Failed component | Effect |
|---|---|
| Booking API | Users cannot create bookings; Prometheus reports its target down; old metrics and indexed logs remain available |
| Prometheus | New metrics are not stored and Grafana metric panels stop updating; the API and logging pipeline continue |
| Grafana | Visualization is unavailable, but Prometheus continues collecting data |
| Node Exporter | Application metrics continue; infrastructure panels stop receiving new samples |
| Filebeat | Application continues and Docker buffers a limited amount of logs; shipping resumes from the saved registry if logs have not rotated away |
| Elasticsearch | Filebeat cannot index new events; Kibana searches fail; API and metrics continue |
| Kibana | Log search UI is unavailable, but Elasticsearch can continue storing events |

### Following one metric

For a successful create request, `create_booking` calls
`ACTIVE_BOOKINGS.inc()`. The Prometheus Python client exposes the new gauge at
`/metrics` as `room_booking_active_bookings`. Prometheus requests that endpoint
within two seconds and writes the timestamped sample to its TSDB. Grafana runs
the PromQL query `room_booking_active_bookings`, reduces the result to its last
non-null value, and displays it in the Active Bookings panel.

The observed trace used request ID `report-metric-trace-20260926`. Before the
create request, the Prometheus query returned `0`. The application accepted the
request at `2026-09-26T12:44:14.784Z`; the API target was scraped at
`2026-09-26T12:44:16.080Z`, after which the query returned `1`. Because the
Grafana stat panel uses the same query and a last-non-null reduction, it also
displayed `1`.

### Following one log

The request middleware creates or propagates a request ID. `create_booking`
passes that ID and booking fields to `write_log`, which produces JSON stdout.
Docker wraps the line in its container log format. Filebeat removes that
envelope, decodes the inner JSON, adds Docker metadata, and sends the event to
Elasticsearch. Elasticsearch stores it in the current daily
`room-booking-logs-YYYY.MM.DD` index. Kibana retrieves it using
`request_id: "<ID>"`.

For the observed request `report-metric-trace-20260926`, the original request
line is reproduced in Part C. Filebeat decoded it and Elasticsearch stored it
in the backing index
`.ds-room-booking-logs-2026.09.26-2026.09.26-000001`. The Kibana KQL query
`request_id: "report-metric-trace-20260926"` returned two related documents:
the business event and the completed-request event. The transformation is
therefore Docker envelope plus JSON string to searchable top-level fields plus
Docker/Filebeat metadata.

### Known limitations

- Bookings disappear on API restart because there is no database.
- This is a local classroom stack with Elasticsearch security disabled.
- Node Exporter observes Docker Desktop's Linux VM rather than all Windows host
  resources.
- Elasticsearch indices have no automatic retention policy in this local
  configuration.

## Part E — Experiments

### 1. Slow-request anomaly

#### Repeatable method

```powershell
python experiments\anomaly_experiment.py --requests-per-stage 30 --interval 0.5
```

Prometheus scrapes every two seconds. Each stage lasts at least 15 seconds plus
request execution time, so each contains several scrapes. The stages use the
same number of booking requests:

1. Baseline with the fault disabled.
2. Fault with a 500 ms delay on every fifth create request.
3. Recovery after disabling the fault.

#### Prediction

- The POST p95 Histogram panel will rise during the fault stage.
- The Summary average for validation should remain approximately stable because
  the delay is injected before the measured validation block.
- Request throughput may decrease because the client waits for responses.
- Structured request logs for delayed requests will have larger `duration_ms`.
- HTTP success counts should remain similar because the injected problem adds
  latency rather than an error.

#### Measured results

The client writes exact results to `evidence/anomaly-results.json`.

| Stage | Client p95 | Grafana p95 | Status results | User effect |
|---|---:|---:|---|---|
| Baseline | 33.353 ms | 9.809 ms | 30 × HTTP 201 | Normal booking response |
| Slow fault | 528.904 ms | 750.189 ms | 30 × HTTP 201 | Every fifth create visibly slower |
| Recovery | 30.418 ms | 625.114 ms | 30 × HTTP 201 | Direct latency recovered; rolling chart still included fault samples |

The stage timestamps were:

| Stage | Start (UTC) | Finish (UTC) |
|---|---|---|
| Baseline | `2026-09-26T12:10:35.750952Z` | `2026-09-26T12:10:51.487203Z` |
| Slow fault | `2026-09-26T12:10:57.500725Z` | `2026-09-26T12:11:16.019591Z` |
| Recovery | `2026-09-26T12:11:22.033228Z` | `2026-09-26T12:11:37.552157Z` |

The client computes an exact nearest-rank percentile from the 30 requests in
one stage. Prometheus instead estimates p95 from fixed histogram buckets over a
rolling five-minute window. This explains both the different absolute values
and the deliberately slow-looking recovery: at the recovery checkpoint the
five-minute window still contained all six delayed fault requests. The client
p95 showed that new requests had already returned to normal. The Summary mean
remained in tens of microseconds because the 500 ms sleep occurs before the
timed validation block. Throughput temporarily fell and the slow request log
with ID `20669fa6-bed2-4cc7-91cd-dcdf21876dbe` recorded `duration_ms: 503.12`,
while every test request still succeeded.

Baseline:

![Grafana dashboard during the baseline stage](evidence/grafana-normal.png)

Injected fault:

![Grafana dashboard while every fifth create request was delayed](evidence/grafana-slow-fault.png)

Recovery:

![Grafana dashboard after the fault was disabled](evidence/grafana-recovery.png)

The slow log can be reproduced in Kibana with:

```text
request_id: "20669fa6-bed2-4cc7-91cd-dcdf21876dbe"
```

### 2. Cardinality explosion

#### Unsafe run

```powershell
$env:CARDINALITY_MODE = "unsafe"
docker compose up -d --force-recreate --wait room-booking-api
python experiments\cardinality_test.py --count 100
```

In unsafe mode, the metric declaration contains a `request_id` label. The
client generates exactly 100 different IDs. The query is:

```promql
count(demo_requests_total)
```

Expected current series count: 100. Observed: **100** at
`2026-09-26T12:43:40Z`.

![Prometheus unsafe-cardinality query showing 100 series](evidence/cardinality-unsafe.png)

#### Safe run

```powershell
$env:CARDINALITY_MODE = "safe"
docker compose up -d --force-recreate --wait room-booking-api
python experiments\cardinality_test.py --count 100
```

Safe mode removes the label from the counter. After another scrape, the same
100 requests update one current series. Expected current count: 1. Observed:
**1** at `2026-09-26T12:44:00Z`.

![Prometheus safe-cardinality query showing one series](evidence/cardinality-safe.png)

Old labelled series remain in historical Prometheus blocks until the seven-day
retention window removes them. A current instant query stops returning them
after Prometheus marks them stale, but removing the label does not immediately
delete stored history.

At larger scale, cardinality is multiplied across label values. A request ID is
effectively unbounded, so one time series per request increases TSDB memory,
index size, disk usage, and query work without helping aggregation. Request IDs
belong in logs, where they can be searched only when an individual request must
be investigated.

## References and Assistance

- Enterprise Software Development, Lab 1: Midnight Launch, used as a reference
  for the local observability stack.
- Prometheus client, Prometheus, Grafana, Elastic, and FastAPI official project
  documentation.
- OpenAI Codex assisted with scaffolding, debugging, configuration, and report
  structure. All commands, results, and explanations must be reviewed by the
  submitting student according to course policy.
