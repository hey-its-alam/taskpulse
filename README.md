# TaskPulse

Distributed job processing platform with full observability: metrics, logs and traces, linked together in Grafana, with alerting. Load tested with k6 and failure tested with a worker crash.

## Stack
FastAPI, Celery, Redis, PostgreSQL, Prometheus, Loki, Grafana Alloy, Tempo, OpenTelemetry, Grafana, k6, Docker Compose, psutil

## Features
- Submit background jobs over HTTP (202 Accepted)
- Celery workers with retries and exponential backoff (2s, 4s, 8s)
- Job status tracking in PostgreSQL (pending, running, retrying, success, failed)
- Manual retry for failed jobs
- Per-client rate limiting (HTTP 429 with Retry-After)
- Queue backpressure (HTTP 503 when the queue is full)
- Prometheus metrics and a provisioned Grafana dashboard (queue depth, throughput, job duration, errors)
- Structured JSON logs with a fixed schema, request and job IDs, error details and memory fields, parsed by Grafana Alloy and searchable in Grafana
- One log line per API request with a hashed client identity, duration, status code and memory (see [Structured logging](#structured-logging))
- Distributed tracing with OpenTelemetry and Tempo (API, queue, worker and database in one trace)
- Logs and traces linked by trace ID in Grafana, in both directions
- Grafana alert rules for queue backlog, worker down and a high job failure ratio
- Failure reason classification for simulated, timeout, Redis, database, network, memory, disk, code and unknown failures
- System health metrics for CPU, memory, disk, Redis and PostgreSQL
- Separate Grafana Failure Diagnostics dashboard
- k6 load tests: steady load, rate limiting and a traffic spike
- Failure-mix testing with valid submissions, invalid job types and optional rate-limit traffic
- Failure testing with a written incident report (see [Failure testing](#failure-testing))

## Architecture

![Architecture](docs/architecture.png)

The same flow as text:
```
Client --> FastAPI (rate limit, save job) --> Redis queue --> Celery worker
                |                                                  |
                +------------------> PostgreSQL <------------------+

Metrics: API :8000/metrics and worker :8001/metrics --> Prometheus --> Grafana
Logs:    container stdout (JSON) --> Grafana Alloy --> Loki ------> Grafana
Traces:  OpenTelemetry (API and worker) --> Tempo ----------------> Grafana
```

## API
| Method | Endpoint | Purpose |
|---|---|---|
| POST | /jobs | Submit a job (response includes its `trace_id`) |
| GET | /jobs | List jobs, filter by status |
| GET | /jobs/{id} | Job details |
| POST | /jobs/{id}/retry | Retry a failed job |
| GET | /health | Health check |
| GET | /metrics | Prometheus metrics |

Job types: `generate_report`, `send_email`, `resize_image`.

## Rate limits
| Scope | Default | Environment variable |
|---|---|---|
| Submit jobs | 30 per minute per client | `RATE_LIMIT_SUBMIT` |
| Read jobs | 300 per minute per client | `RATE_LIMIT_READ` |
| Retry jobs | 10 per minute per client | `RATE_LIMIT_RETRY` |
| Queue length before HTTP 503 | 500 | `MAX_QUEUE_DEPTH` |

Clients are identified by the `X-API-Key` header, or by IP address if there is no header.
The limiter is a fixed-window counter in Redis. If Redis is down it fails open, so users are not blocked.

## Configuration
Set in `.env` (see `.env.example`).

| Variable | Purpose |
|---|---|
| `RATE_LIMIT_WINDOW_SECONDS` | Length of the rate limit window |
| `TASK_RATE_LIMIT` | Worker throttle, for example `120/m` |
| `FAILURE_RATE` | Share of simulated job failures (for failure testing) |
| `EXTRA_LATENCY_SECONDS` | Extra delay added to every job (for slowness testing) |

The Celery broker visibility timeout is set in `app/celery_app.py` (`broker_transport_options`, 120 seconds). See [Failure testing](#failure-testing) for why.

## Run
```
copy .env.example .env
docker compose up --build -d
```
(On Mac/Linux use `cp` instead of `copy`.)

Open http://localhost:8000/docs and submit a job.

## Tests
```
docker compose exec api pytest -q
```

## Project structure
```
taskpulse/
├── docker-compose.yml          # runs all 9 containers
├── Dockerfile                  # one image used by api AND worker
├── requirements.txt
├── .env.example                # copy to .env
├── .env                        # settings (not committed to git)
├── .gitignore
├── README.md
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app, /health, /metrics
│   ├── config.py               # reads settings from environment
│   ├── database.py             # SQLAlchemy engine and session
│   ├── models.py               # Job table
│   ├── schemas.py              # request/response validation
│   ├── routes.py               # /jobs endpoints
│   ├── rate_limiter.py         # Redis rate limiting (429)
│   ├── celery_app.py           # Celery config + worker startup hook
│   ├── tasks.py                # the job logic, retries, failures
│   ├── failure_reasons.py      # turns an exception into a failure reason
│   ├── system_metrics.py       # CPU, memory, disk, Redis and PostgreSQL health
│   ├── metrics.py              # Prometheus metrics
│   ├── logging_config.py       # structured JSON logs: fixed schema, context fields, redaction
│   ├── request_logging.py      # one structured log line per API request
│   ├── resource_usage.py       # process and system memory readings for log lines
│   └── telemetry.py            # OpenTelemetry setup
├── tests/
│   ├── __init__.py
│   ├── test_api.py
│   ├── test_failure_reasons.py
│   ├── test_logging_config.py
│   ├── test_request_logging.py
│   ├── test_system_metrics.py
│   └── test_task_failures.py
├── observability/
│   ├── prometheus/
│   │   └── prometheus.yml
│   ├── tempo/
│   │   └── tempo.yml
│   ├── alloy/
│   │   └── config.alloy        # parses JSON logs and ships them to Loki
│   └── grafana/
│       ├── provisioning/
│       │   ├── datasources/datasources.yml
│       │   └── dashboards/dashboards.yml
│       └── dashboards/
│           ├── taskpulse.json           # exported from Grafana
│           └── taskpulse-failures.json  # failure diagnostics dashboard
├── loadtest/
│   ├── baseline.js             # steady load at a chosen rate
│   ├── rate_limit.js           # one abusive client, one normal client
│   ├── spike.js                # sudden traffic spike and recovery
│   └── failure_mix.js          # valid, invalid and optional rate-limit traffic
└── docs/
    ├── architecture.png        # architecture diagram
    ├── incident-report.md      # jobs stuck after a worker crash
    ├── runbook.md              # operations runbook: checks, troubleshooting, recovery
    ├── worker-kill-evidence.txt  # raw output of that test
    └── screenshots/            # images used in this README
```

## Documentation

- [Operations Runbook](docs/runbook.md) — Setup checks, troubleshooting, alerts, logs, traces and incident recovery.
- [Incident Report](docs/incident-report.md) — Worker crash investigation, root cause and recovery.
- [Worker Kill Evidence](docs/worker-kill-evidence.txt) — Raw output from the worker crash test.

## Observability

### Metrics
Prometheus scrapes metrics from the API (`:8000/metrics`) and the worker (`:8001/metrics`).
The Grafana dashboard is loaded automatically from `observability/grafana/dashboards/taskpulse.json`.
It shows worker status, queue depth, throughput, job duration (p50 and p95), jobs by status, API request rate, error ratio and latency, retries, rate-limited requests, and recent worker warnings.

![Final dashboard](docs/screenshots/dashboard-final.png)

### Logs
Structured JSON logs are shipped to Loki by Grafana Alloy and searched in Grafana Explore.
This is the full history of one job: submitted, failed three times with exponential backoff, then succeeded.

![Job logs](docs/screenshots/logs-v1.png)

Useful queries:
```
{service="worker"} | json | level="WARNING"
{service=~"api|worker"} |= "JOB-ID"
{service="worker"} |= "reason=database_error"
```

### Structured logging

Every API and worker log line is one JSON object with a fixed set of keys, so Grafana can filter, count and graph log fields instead of searching text.

Example (a job that failed on its last attempt):

```json
{"ts": "2026-11-17T09:45:44.022+00:00", "level": "ERROR", "service": "worker", "logger": "taskpulse.worker",
 "event": "job_failed", "message": "job failed permanently id=4b0ea9ae... reason=simulated error=Simulated failure while running generate_report",
 "trace_id": "f8ae16e4c7e01c69a85e0fef752f6741", "job_id": "4b0ea9ae-...", "attempt": 4, "max_attempts": 4,
 "job_type": "generate_report", "reason": "simulated", "error_type": "RuntimeError",
 "error_message": "Simulated failure while running generate_report",
 "duration_ms": 4012.5, "memory_rss_mb": 88.4, "memory_delta_mb": 0.3, "system_memory_percent": 21.0}
```

**Fixed keys on every line:** `ts` (UTC, ISO 8601), `level`, `service` (`api` or `worker`), `logger`, `event`, `message`, `trace_id`.

**Added while something is running:** `request_id` and `client` during an API request; `job_id`, `job_type`, `attempt` and `max_attempts` during a job. Every line written in that time carries them, including lines from libraries.

**Errors add:** `reason`, `error_type`, `error_message` (up to 500 characters) and, for unhandled errors and worker crashes, `stack` (up to 4000 characters).

| Event | Service | Extra fields |
|---|---|---|
| `request_completed` | api | `method`, `path` (the route, such as `/jobs/{job_id}`), `status_code`, `duration_ms`, `process_rss_mb`, `system_memory_percent`, and `reason` for 404, 422, 429 and 5xx |
| `request_failed` | api | an unhandled exception: error fields and `stack` |
| `job_submitted`, `job_manual_retry` | api | `job_id`, `job_type` |
| `queue_full` | api | `queue_depth`, `queue_limit` |
| `enqueue_failed` | api | the job was saved but could not be queued: error fields |
| `job_started` | worker | `memory_rss_mb`, `system_memory_percent` |
| `job_retry_scheduled` | worker | error fields, `retry_in_seconds`, `duration_ms`, memory fields |
| `job_failed` | worker | error fields, `duration_ms`, memory fields |
| `job_succeeded` | worker | `duration_ms`, memory fields |
| `job_crashed` | worker | the database, Redis or the machine failed (not the job): error fields, `stack`, memory fields |

`reason` is always one word from a fixed list. Worker and system failures: `simulated`, `timeout`, `redis_error`, `database_error`, `network_error`, `out_of_memory`, `disk_full`, `code_error`, `unknown`. API answers: `not_found`, `invalid_request`, `rate_limited`, `server_error`, `queue_full`, `queue_unavailable`.

**Who and how much memory**

| Field | Meaning |
|---|---|
| `client` | `key:` plus the first 8 characters of the SHA-256 of the `X-API-Key`, or `ip:` plus the address when there is no key. The raw key is never logged. There are no user accounts, so this is the only caller identity. |
| `process_rss_mb` / `memory_rss_mb` | RAM held by that process (the API or one worker process) |
| `memory_delta_mb` | Change of the worker process's RAM during one job. Approximate: Python often keeps freed memory, so a small or zero change does not prove the job used no memory. |
| `system_memory_percent` | Memory used on the whole machine. Inside Docker Desktop this is the Linux VM that runs all the containers. |

Memory in an API log line belongs to the whole API process, because requests share it. It shows how much memory the API held while a client's request ran; it cannot say that this client used that memory.

**Privacy.** Fields whose names contain `password`, `secret`, `token`, `authorization`, `api_key` or `cookie` are replaced with `[redacted]`. `/health` and `/metrics` requests are not logged.

**Every response** carries an `X-Request-ID` header. A client can send its own, otherwise one is created. Use it to find every log line of one request.

**Parser (Grafana Alloy).** Before a line is stored in Loki, Alloy reads the JSON, uses the line's own `ts` as the log time, and turns four fields with few different values into Loki labels: `level`, `event`, `reason` and `job_type`. Fields with many different values (`job_id`, `request_id`, `client`, `trace_id`) are not labels, because every new label value creates a new Loki stream; read them at query time with `| json`. Lines that are not JSON pass through unchanged. Uvicorn's own logs are written through the same JSON formatter, and `request_completed` replaces its access line.

Useful queries (Grafana Explore, Loki):

```
{service="worker", event="job_failed"} | json
{service="worker", reason="database_error"}
{service="api", reason="rate_limited"} | json | line_format "{{.client}} {{.path}}"
{service="api", event="request_completed"} | json | duration_ms > 500
{service=~"api|worker"} | json | request_id="REQUEST-ID"
sum by (reason) (count_over_time({service=~"api|worker", reason!=""}[5m]))
avg by (job_type) (avg_over_time({service="worker", event="job_succeeded"} | json | unwrap memory_rss_mb | __error__="" [5m]))
topk(5, sum by (client) (count_over_time({service="api", event="request_completed"} | json [5m])))
```

Code: `app/logging_config.py` (formatter and context fields), `app/request_logging.py` (API middleware), `app/resource_usage.py` (memory readings), `observability/alloy/config.alloy` (parser). Tests: `tests/test_logging_config.py`, `tests/test_request_logging.py`, `tests/test_task_failures.py`.

### Traces
OpenTelemetry traces go to Tempo. One trace follows a job from the API, through the Redis queue, into the worker and the database.
The API answers in under 100 ms while the job runs in the background, and the trace shows where every millisecond went.

![Trace](docs/screenshots/trace-v1.png)

To find a trace, copy the `trace_id` from the `POST /jobs` response, open Grafana, go to Explore, choose Tempo, and paste it in the TraceQL tab.

### Logs and traces linked
Every JSON log line carries the `trace_id` of the request it belongs to, and Grafana links the two in both directions.
Click **View trace** on a log line to open its trace:

![Log to trace](docs/screenshots/log-to-trace.png)

Click the log icon on a span to see every log line of that trace:

![Trace to logs](docs/screenshots/trace-to-logs.png)

### Alerts
Alert rules are defined in Grafana (Alerting, Alert rules) and evaluated every minute.

| Alert | Query | Condition | Pending |
|---|---|---|---|
| Queue backlog | `max(taskpulse_queue_depth)` | above 100 | 2 min |
| Worker down | `min(up{job="worker"})` | below 1 | 1 min |
| High job failure ratio | `sum(rate(taskpulse_jobs_processed_total{status="failed"}[5m])) / sum(rate(taskpulse_jobs_processed_total[5m]))` | above 0.3 | 2 min |

To test them, stop the worker and send jobs:
```
docker compose stop worker
```
The "Worker down" and "Queue backlog" alerts go from Pending to Firing:

![Alerts firing](docs/screenshots/alerts-firing.png)

After `docker compose start worker` the queue drains and both alerts return to Normal:

![Alerts resolved](docs/screenshots/alerts-resolved.png)

### Failure diagnostics (Observability V2)
A separate dashboard shows why jobs fail, not only how many. It is provisioned from `observability/grafana/dashboards/taskpulse-failures.json` and kept apart from the main dashboard.

**Failure reasons.** Every failed attempt in the worker is classified into one fixed reason, counted in `taskpulse_job_failures_total{type,reason}` and written to the log line as `reason=...`.

| Reason | Meaning |
|---|---|
| `simulated` | Failure produced on purpose by `FAILURE_RATE` |
| `timeout` | A task or a dependency took too long |
| `redis_error` | Redis (the queue) was unreachable or returned an error |
| `database_error` | PostgreSQL was unreachable or returned an error |
| `network_error` | Connection refused, reset or DNS failure |
| `out_of_memory` | `MemoryError` |
| `disk_full` | No space left on the device |
| `code_error` | A bug in the code (wrong type, missing key, division by zero and similar) |
| `unknown` | Anything else |

Errors outside the simulated work, for example the database being unreachable while a job starts, are counted with a reason too and retried up to 3 times (2, 4 and 8 seconds).

If a job is saved but cannot be put on the queue (for example Redis is down), the job is marked `failed`, the API answers 503 "Queue unavailable", and `taskpulse_enqueue_failures_total` goes up.

**System health.** The API and the worker both export:

| Metric | Meaning |
|---|---|
| `taskpulse_dependency_up{dependency="redis"}` and `{dependency="postgres"}` | 1 if the dependency answered a quick check, otherwise 0 |
| `taskpulse_cpu_used_ratio` | CPU busy, 0 to 1 |
| `taskpulse_memory_used_ratio` | Memory used, 0 to 1 |
| `taskpulse_disk_used_ratio` | Disk used, 0 to 1 |

Inside Docker Desktop, CPU and memory describe the Linux VM that runs all the containers. Memory and CPU of each process are also available as `process_resident_memory_bytes` and `process_cpu_seconds_total`.

**Dashboard panels.** Failure attempts by reason, total failure attempts, enqueue failures, Redis and PostgreSQL health, CPU, memory and disk usage, job retry rate, and worker warning and error logs. Panels built from the structured logs: failure reasons, last errors, memory per job type and top clients.

### Services
| Service | URL |
|---|---|
| API docs | http://localhost:8000/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (default login `admin` / `admin`, set in `.env`) |
| Loki | http://localhost:3100 |
| Tempo | http://localhost:3200 |

## Load tests
The load tests use [k6](https://grafana.com/docs/k6/latest/set-up/install-k6/) and run from the host against `http://localhost:8000`. Change the target with `-e BASE_URL=...`.

The baseline, rate limit and spike scripts treat the HTTP statuses 202, 429 and 503 as expected answers. Any other status is counted in `jobs_unexpected_status`, and the test fails if that counter is above zero. `failure_mix.js` also expects 422 and has no pass or fail conditions.

| Script | What it does | How to run | Pass condition |
|---|---|---|---|
| `baseline.js` | Constant request rate. Every request uses a new `X-API-Key`, so the rate limiter does not interfere. | `k6 run -e RATE=5 loadtest/baseline.js` (`RATE` default 1 per second, `DURATION` default 2m) | p95 under 500 ms, p99 under 1000 ms, at least 99% of requests accepted (202), no unexpected status |
| `rate_limit.js` | One client sends far above the limit while a second client with its own key stays below it. | `k6 run loadtest/rate_limit.js` (`RATE` default 20 per second, `DURATION` default 30s) | The limiter returns 429 to the abusive client, and at least 99% of the normal client's requests are accepted |
| `spike.js` | 1 request/s, then a sudden jump to `PEAK` (default 100/s) in 10 s, held for 1 minute, then back to 1/s for `RECOVERY` (default 5m). About 6m50s in total. | `k6 run loadtest/spike.js` (use `-e PEAK=50` on a slow machine) | p95 under 2 s, no unexpected status, fewer than 50 dropped iterations |
| `failure_mix.js` | Valid job submissions, invalid job types (expected to be rejected with 422) and optional rate-limit traffic. | `k6 run -e JOB_RATE=1 -e BAD_RATE=1 -e HAMMER_RATE=0 -e DURATION=1m loadtest/failure_mix.js` | None. It reports a count per response type: `resp_202_accepted`, `resp_422_invalid`, `resp_429_rate_limited`, `resp_503_queue_full`, `resp_5xx_server_error`, `resp_0_network_error`, `resp_other` |

To make about half of the jobs end as `failed`, set `FAILURE_RATE=0.84` in `.env` and run `docker compose up -d --force-recreate worker`. A job gets 4 attempts (1 plus 3 retries), so with a failure chance of 0.84 per attempt about half of the jobs fail all 4 attempts.

### What the tests showed
- **Worker capacity.** Grafana showed the worker finishing about **85 jobs per minute** (about 1.4 jobs per second, 4 jobs at a time). Any sustained submit rate above that makes the queue grow.
- **Spike test (PEAK=100).** k6 completed 7,320 requests with none interrupted. Queue depth rose to about 495, just under the limit of 500, and the workers stayed UP. No rate limit (429) appeared, because every request used its own key. Afterwards the queue drained on its own and the database showed no pending or running jobs.

### Screenshots

**Baseline, 1 request per second**

![Grafana, baseline at 1 request per second](docs/screenshots/grafana-baseline-rate1-mid.png)

![k6 summary, baseline at 1 request per second](docs/screenshots/k6-baseline-rate1.png)

**Baseline, 5 requests per second**

![Grafana, baseline at 5 requests per second](docs/screenshots/grafana-baseline-rate5-mid.png)

**Baseline, 10 requests per second**

![Grafana, baseline at 10 requests per second](docs/screenshots/grafana-baseline-rate10-mid.png)

![Grafana, baseline at 10 requests per second, after the run](docs/screenshots/grafana-baseline-rate10-complete.png)

**Dashboard during baseline testing**

![Grafana during a baseline test](docs/screenshots/grafana-baseline-mid.png)

![Dashboard during baseline testing](docs/screenshots/dashboard-baseline.png)

![Dashboard details during baseline testing](docs/screenshots/dashboard-baseline-details.png)

**Rate limit test**

![Grafana during the rate limit test](docs/screenshots/grafana-rate-limit-mid.png)

**Spike test**

![Grafana during the spike](docs/screenshots/grafana-spike-peak.png)

## Failure testing
The worker container was killed during a 6 minute k6 run (2 requests per second) and started again after a few minutes.

- **While the worker was down:** the API kept answering, with **p95 20.87 ms and p99 27.73 ms** over 721 requests (715 accepted, 2 rejected with 503 when the queue reached its limit, 4 connection errors). The queue grew to about 500, and the Workers up panel showed DOWN.
- **After the worker came back:** throughput returned to about 85 jobs per minute and the queue drained.
- **Problem found:** 4 jobs stayed stuck (3 `running`, 1 `pending`) after the queue was empty, and the dashboard showed 0 active jobs. They were not lost from the database, but nothing was going to run them for up to an hour. With Redis as the broker and `acks_late` enabled, messages held by a dead worker only return to the queue after the visibility timeout, which defaults to 1 hour.
- **Fix:** `broker_transport_options={"visibility_timeout": 120}` in `app/celery_app.py`. After rebuilding, the 4 stuck jobs finished and `unacked` went back to 0. This was confirmed on those 4 jobs; a second worker kill after the fix has not been run yet.

![Worker killed](docs/screenshots/grafana-worker-kill.png)

![Worker back, queue draining](docs/screenshots/grafana-worker-recovered.png)

Full write-up with timeline, root cause and action items: [docs/incident-report.md](docs/incident-report.md). Raw output of the test: [docs/worker-kill-evidence.txt](docs/worker-kill-evidence.txt).

## Roadmap
- "Stuck jobs" dashboard panel and alert (jobs in `running` or `pending` for more than 5 minutes)
- Alerts on failure reasons and on dependency health
- Alerts on error logs from Loki (for example repeated `database_error` lines)
- Re-run the worker kill test after the visibility timeout fix, with a load below worker capacity
- Make tasks safe to run twice (a recovered job can run again with `acks_late`)
- Redis and PostgreSQL outage simulations
- Find the cause of the connection EOF errors seen by k6
- Pin dependency versions in `requirements.txt`