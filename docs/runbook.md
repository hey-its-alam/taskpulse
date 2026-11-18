# TaskPulse Operations Runbook

This runbook describes how to inspect and troubleshoot the TaskPulse
job-processing and observability stack.

## 1. Service overview

TaskPulse uses the following services:

-   **API**: FastAPI application; accepts job submissions and exposes
    API metrics.
-   **Worker**: Celery worker; processes queued jobs and exposes worker
    metrics.
-   **Redis**: Celery broker, queue storage, and rate-limit storage.
-   **PostgreSQL**: Persistent job records.
-   **Prometheus**: Scrapes API, worker, and Prometheus metrics.
-   **Grafana**: Dashboards and alert rules.
-   **Loki + Alloy**: Log collection and storage.
-   **Tempo + OpenTelemetry**: Distributed traces.

Useful local URLs:

  Component     URL
  ------------- ---------------------------------
  API docs      `http://localhost:8000/docs`
  API metrics   `http://localhost:8000/metrics`
  Grafana       `http://localhost:3000`
  Prometheus    `http://localhost:9090`
  Loki          `http://localhost:3100`
  Tempo         `http://localhost:3200`
  Alloy         `http://localhost:12345`

Run Docker Compose commands from the project root (`D:\\TaskPulse`).

## 2. First response: check the stack

Check whether the containers are running:

``` powershell
docker compose ps
```

Check recent logs for a service:

``` powershell
docker compose logs --tail 100 api
docker compose logs --tail 100 worker
docker compose logs --tail 100 redis
docker compose logs --tail 100 postgres
```

Check Prometheus scrape targets:

1.  Open `http://localhost:9090/targets`.
2.  Confirm the `api`, `worker`, and `prometheus` targets are `UP`.

Or query target health:

``` powershell
(Invoke-RestMethod "http://localhost:9090/api/v1/query?query=up").data.result |
Select-Object @{Name="Target";Expression={$_.metric.job}},
              @{Name="Status";Expression={$_.value[1]}}
```

A value of `1` means the target is up; `0` means it is down.

## 3. Worker down

**Symptoms**

-   Grafana `Worker down` alert fires.
-   Worker target is `DOWN` in Prometheus.
-   Jobs remain pending and queue depth increases.
-   Job throughput drops.

**Investigate**

``` powershell
docker compose ps worker
docker compose logs --tail 100 worker
```

Check Prometheus worker target at `http://localhost:9090/targets`.

**Recovery**

If the worker container is stopped and its configuration has not
changed:

``` powershell
docker compose start worker
```

If the container needs to be recreated after a configuration or
environment change:

``` powershell
docker compose up -d --force-recreate worker
```

Then verify:

``` powershell
docker compose ps worker
```

Confirm the worker target returns to `UP`, logs show the worker started,
and the queue begins draining. Do not assume recovery only because the
container is running; check job statuses and throughput too.

## 4. Queue backlog / backpressure

**Symptoms**

-   Grafana `Queue backlog` alert fires.
-   Queue depth rises and job throughput is lower than incoming job
    rate.
-   When the configured queue maximum (500) is reached, the API can
    reject new submissions with HTTP `503`.

**Investigate**

-   Check the queue depth and worker throughput panels in the
    `TaskPulse Final` dashboard.
-   Check worker status and logs.
-   Check whether the worker is down, slow, retrying jobs, or receiving
    more work than it can process.

Useful commands:

``` powershell
docker compose ps worker
docker compose logs --tail 100 worker
```

**Recovery**

-   Restore the worker if it is down (see section 3).
-   If the worker is healthy but the queue continues to grow, reduce the
    incoming test/load rate and investigate worker capacity before
    increasing limits.
-   Do not repeatedly submit jobs while the queue is at its configured
    maximum.

The queue maximum is a protective backpressure limit; HTTP `503` at
capacity is expected behavior.

## 5. Job failures and retries

**Symptoms**

-   Jobs transition to `retrying` and may eventually become `failed`.
-   Failure counters increase in Prometheus.
-   Worker warning/error logs contain failure messages and trace IDs.

**Investigate**

1.  Open the `TaskPulse Failure Diagnostics` dashboard.
2.  Review failure counts grouped by job type and reason.
3.  Inspect the worker warning/error logs.
4.  Copy the log's `trace_id` and search for that trace in Grafana
    Explore using Tempo.
5.  Check the job record in PostgreSQL for its status, retry count,
    error message, and timestamps.

The project classifies failures into these fixed reasons:

-   `simulated`
-   `timeout`
-   `redis_error`
-   `database_error`
-   `network_error`
-   `out_of_memory`
-   `disk_full`
-   `code_error`
-   `unknown`

The configured retry policy allows up to 3 retries after the initial
attempt, with backoff delays of 2, 4, and 8 seconds. A job can therefore
have up to 4 total attempts.

Do not infer the root cause from the reason label alone. Correlate the
worker log, trace, and persisted job record.

## 6. High job failure ratio alert

The Grafana alert `High job failure ratio` evaluates the proportion of
failed processed jobs over a five-minute rate window. Its threshold is
above 30%, with a two-minute pending period.

**Investigate**

1.  Open Grafana → Alerting → Alert rules.
2.  Check the alert state and health.
3.  Open the `TaskPulse Failure Diagnostics` dashboard.
4.  Inspect failure counts by reason and job type.
5.  Correlate a representative failure log with its Tempo trace.

The alert query treats a missing failed-series as zero and protects the
denominator from division by zero. A `Normal` state means the threshold
is not currently breached; it does not prove that every failure scenario
has been tested.

## 7. Redis or PostgreSQL dependency health

The API and worker expose dependency health metrics for Redis and
PostgreSQL.

Check the API metrics endpoint:

``` powershell
(Invoke-WebRequest "http://localhost:8000/metrics").Content |
Select-String "taskpulse_dependency_up"
```

Expected healthy values are `1` for Redis and PostgreSQL. A value of `0`
indicates the corresponding dependency check failed.

Check service state and logs:

``` powershell
docker compose ps redis postgres
docker compose logs --tail 100 redis
docker compose logs --tail 100 postgres
```

If a dependency is unavailable, inspect its container logs and health
status before restarting it. Restart only the affected service when
appropriate, then re-check the dependency metric and application logs.

The project uses fail-open behavior for Redis-backed API rate limiting
when Redis is unavailable. This does not mean Redis is healthy or that
Celery queue processing can continue normally without its broker.

## 8. Logs and distributed traces

For a failure investigation:

1.  Open Grafana and select the `TaskPulse Failure Diagnostics`
    dashboard.
2.  Find a relevant worker warning/error log.
3.  Copy its `trace_id`.
4.  Open Grafana → Explore and select Tempo.
5.  Search by the exact trace ID.
6.  Inspect available spans and their durations/errors.

Use the same trace ID to correlate the log and trace. Some failures may
not have a complete trace or all expected spans; record that as an
observability gap rather than assuming the operation succeeded.

## 9. System resource metrics

The application exposes these system metrics:

-   `taskpulse_cpu_used_ratio`
-   `taskpulse_memory_used_ratio`
-   `taskpulse_disk_used_ratio`

The dashboard may show measurements from both API and worker collectors.
Check the `job` and `instance` labels before comparing values. Do not
assume duplicate series are separate physical machines; both containers
may report measurements for their host/container environment.

If resource usage is high, inspect the relevant container logs and
workload before taking action. Do not deliberately exhaust disk or
memory on a development machine as a test.

## 10. Worker crash and potentially stuck jobs

A previous controlled worker-kill incident showed that jobs could remain
in PostgreSQL as `running` or `pending` while the Redis ready queue was
empty and the worker was idle. The incident evidence recorded:

-   `task_acks_late=True`.
-   Redis `HLEN unacked` was 4 while `LLEN celery` was 0.
-   Four job records remained stuck after the worker restarted.
-   `broker_transport_options={"visibility_timeout": 120}` was added.
-   After rebuilding/restarting, those four jobs completed and `unacked`
    returned to 0.

If a worker crash occurs and jobs appear stuck, inspect all three views
rather than relying on queue depth alone:

1.  Worker state and logs.
2.  PostgreSQL job statuses and timestamps.
3.  Redis ready queue and unacknowledged message counts.

Diagnostic commands:

``` powershell
docker compose exec redis redis-cli LLEN celery
docker compose exec redis redis-cli HLEN unacked
docker compose exec postgres psql -U taskpulse -d taskpulse -c "SELECT status, count(*) FROM jobs GROUP BY status;"
```

The incident report explicitly records that a fresh worker-kill re-test
after setting the 120-second visibility timeout had not yet been
performed. Treat automatic recovery after a fresh crash as not fully
re-verified until that test is completed.

Do not manually delete Redis messages or edit job rows as a first
response. Preserve the evidence and investigate before attempting data
repair.

## 11. Controlled load and failure testing

The failure-mix k6 script supports valid submissions, invalid job
requests, and an optional rate-limit hammer scenario. A previously
verified run used:

``` powershell
k6 run -e JOB_RATE=1 -e BAD_RATE=1 -e HAMMER_RATE=0 -e DURATION=1m loadtest/failure_mix.js
```

That run produced 61 accepted (`202`) responses and 61 invalid (`422`)
responses, with 0% unexpected HTTP request failures.

Run controlled tests only against the intended development environment.
Keep `HAMMER_RATE=0` unless specifically testing rate limiting. Do not
run destructive resource-exhaustion tests.

## 12. Known follow-up items

The incident report (`docs/incident-report.md`) records these follow-up
items. They should remain visible until separately implemented and
verified:

-   Re-run the worker-kill test at a load below worker capacity and
    confirm jobs return within about two minutes.
-   Add a stuck-jobs panel and alert for `running` or `pending` jobs
    older than five minutes.
-   Check that `settings.task_rate_limit` cannot hold a task longer than
    the broker visibility timeout.
-   Make tasks idempotent because late-acknowledged tasks may run more
    than once.
-   Investigate the four `Post ... EOF` errors recorded by k6 during the
    incident.
-   Add an alert for queue depth near the configured limit of 500.
-   Pin dependency versions in `requirements.txt`.

These are not claimed as completed by this runbook. Update this section
only after each item has been implemented and verified.

## 13. Related project documentation

-   `README.md` --- project overview, setup, and feature summary.
-   `docs/incident-report.md` --- worker-crash incident analysis and
    action items.
-   `docs/worker-kill-evidence.txt` --- raw evidence from the 2026-11-13
    worker-kill test.
-   `docs/screenshots/` --- saved dashboard and incident screenshots.
