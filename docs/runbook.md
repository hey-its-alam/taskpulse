# TaskPulse Operations Runbook

This runbook describes how to inspect and troubleshoot the TaskPulse job-processing and observability stack.

## 1. Service overview

TaskPulse uses the following services:

- **API:** FastAPI application; accepts job submissions and exposes API metrics.
- **Worker:** Celery worker; processes queued jobs and exposes worker metrics.
- **Redis:** Celery broker, queue storage, and rate-limit storage.
- **PostgreSQL:** Persistent job records.
- **Prometheus:** Scrapes API, worker, and Prometheus metrics.
- **Grafana:** Dashboards and alert rules.
- **Loki + Alloy:** Log collection and storage.
- **Tempo + OpenTelemetry:** Distributed traces.

Useful local URLs:

| Component | URL |
|---|---|
| API docs | http://localhost:8000/docs |
| API metrics | http://localhost:8000/metrics |
| Grafana | http://localhost:3000 |
| Prometheus | http://localhost:9090 |
| Loki | http://localhost:3100 |
| Tempo | http://localhost:3200 |
| Alloy | http://localhost:12345 |

The Alloy address is Alloy's default port. If the page does not open, check whether the port is published in `docker-compose.yml`.

Run Docker Compose commands from the project root (for example `D:\TaskPulse`).

## 2. First response: check the stack

Check whether the containers are running:

```powershell
docker compose ps
```

Check recent logs for a service:

```powershell
docker compose logs --tail 100 api
docker compose logs --tail 100 worker
docker compose logs --tail 100 redis
docker compose logs --tail 100 postgres
```

Check Prometheus scrape targets:

1. Open http://localhost:9090/targets.
2. Confirm the `api`, `worker`, and `prometheus` targets are UP.

Or query target health:

```powershell
(Invoke-RestMethod "http://localhost:9090/api/v1/query?query=up").data.result |
  Select-Object @{Name="Target";Expression={$_.metric.job}},
                @{Name="Status";Expression={$_.value[1]}}
```

A value of `1` means the target is up; `0` means it is down.

## 3. Worker down

**Symptoms**

- Grafana **Worker down** alert fires.
- Worker target is DOWN in Prometheus.
- Jobs remain `pending` and queue depth increases.
- Job throughput drops.

**Investigate**

```powershell
docker compose ps worker
docker compose logs --tail 100 worker
```

Check the Prometheus worker target at http://localhost:9090/targets.

**Recovery**

If the worker container is stopped and its configuration has not changed:

```powershell
docker compose start worker
```

If the container needs to be recreated after a configuration or environment change:

```powershell
docker compose up -d --force-recreate worker
```

Then verify:

```powershell
docker compose ps worker
```

Confirm the worker target returns to UP, logs show the worker started, and the queue begins draining. Do not assume recovery only because the container is running; check job statuses and throughput too.

## 4. Queue backlog / backpressure

**Symptoms**

- Grafana **Queue backlog** alert fires.
- Queue depth rises and job throughput is lower than the incoming job rate.
- When the configured queue maximum (500) is reached, the API can reject new submissions with HTTP 503.

**Investigate**

- Check the queue depth and worker throughput panels in the **TaskPulse Final** dashboard.
- Check worker status and logs.
- Check whether the worker is down, slow, retrying jobs, or receiving more work than it can process.

Useful commands:

```powershell
docker compose ps worker
docker compose logs --tail 100 worker
```

**Recovery**

- Restore the worker if it is down (see section 3).
- If the worker is healthy but the queue continues to grow, reduce the incoming test or load rate and investigate worker capacity before increasing limits.
- Do not repeatedly submit jobs while the queue is at its configured maximum.

The queue maximum is a protective backpressure limit; HTTP 503 at capacity is expected behavior.

## 5. Job failures and retries

**Symptoms**

- Jobs transition to `retrying` and may eventually become `failed`.
- Failure counters increase in Prometheus.
- Worker warning and error logs contain failure messages and trace IDs.

**Investigate**

1. Open the **TaskPulse Failure Diagnostics** dashboard.
2. Review failure counts grouped by job type and reason.
3. Inspect the worker warning and error logs (see section 8).
4. Copy the log's `trace_id` and search for that trace in Grafana Explore using Tempo.
5. Check the job record in PostgreSQL for its status, retry count, error message, and timestamps:

```powershell
docker compose exec postgres psql -U taskpulse -d taskpulse -P pager=off -c "SELECT id, type, status, retries, error_message, trace_id, started_at, finished_at FROM jobs WHERE id = 'PASTE-JOB-ID';"
```

To see why jobs ended as failed:

```powershell
docker compose exec postgres psql -U taskpulse -d taskpulse -P pager=off -c "SELECT error_message, count(*) FROM jobs WHERE status = 'failed' GROUP BY 1;"
```

To find the logs of one reason in Grafana Explore (Loki):

```logql
{service="worker"} | json | reason="database_error"
```

Field meanings and more queries are in section 8.

The project classifies failures into these fixed reasons:

- `simulated`
- `timeout`
- `redis_error`
- `database_error`
- `network_error`
- `out_of_memory`
- `disk_full`
- `code_error`
- `unknown`

The configured retry policy allows up to 3 retries after the initial attempt, with backoff delays of 2, 4, and 8 seconds. A job can therefore have up to 4 total attempts.

Do not infer the root cause from the reason label alone. Correlate the worker log, trace, and persisted job record.

**Where to look first for each reason**

These are the first checks, not tested fixes.

| Reason | Look at first |
|---|---|
| `simulated` | `FAILURE_RATE` in `.env`. Expected while failure testing is on. |
| `timeout` | Job duration p95 panel, `EXTRA_LATENCY_SECONDS` in `.env`, and the slowest span in the trace. |
| `redis_error` | `taskpulse_dependency_up{dependency="redis"}`, `docker compose ps redis`, and the Redis logs. |
| `database_error` | `taskpulse_dependency_up{dependency="postgres"}`, `docker compose ps postgres`, and the PostgreSQL logs. |
| `network_error` | Which service refused or reset the connection (see the log line), and whether that container is running. |
| `out_of_memory` | `taskpulse_memory_used_ratio`, `process_resident_memory_bytes`, and the Docker Desktop memory limit. |
| `disk_full` | `taskpulse_disk_used_ratio` and `docker system df`. |
| `code_error` | The traceback in the worker log, and what changed in the code recently. |
| `unknown` | The exception text in the log line. If it keeps happening, add a rule for it in `app/failure_reasons.py`. |

## 6. High job failure ratio alert

The Grafana alert **High job failure ratio** evaluates the proportion of failed processed jobs over a five-minute rate window. Its threshold is above 30%, with a two-minute pending period.

**Investigate**

1. Open Grafana → Alerting → Alert rules.
2. Check the alert state and health.
3. Open the **TaskPulse Failure Diagnostics** dashboard.
4. Inspect failure counts by reason and job type.
5. Correlate a representative failure log with its Tempo trace.

The query is defined in the alert rule itself (Grafana → Alerting → Alert rules). Open the rule to see exactly how it handles a missing failed series and a zero denominator. A Normal state means the threshold is not currently breached; it does not prove that every failure scenario has been tested.

## 7. Redis or PostgreSQL dependency health

The API and worker expose dependency health metrics for Redis and PostgreSQL.

Check the API metrics endpoint:

```powershell
(Invoke-WebRequest "http://localhost:8000/metrics" -UseBasicParsing).Content -split "`n" |
  Select-String "taskpulse_dependency_up"
```

Expected healthy values are `1` for Redis and PostgreSQL. A value of `0` indicates the corresponding dependency check failed.

Check service state and logs:

```powershell
docker compose ps redis postgres
docker compose logs --tail 100 redis
docker compose logs --tail 100 postgres
```

If a dependency is unavailable, inspect its container logs and health status before restarting it. Restart only the affected service when appropriate, then re-check the dependency metric and application logs.

The project uses fail-open behavior for Redis-backed API rate limiting when Redis is unavailable. This does not mean Redis is healthy or that Celery queue processing can continue normally without its broker.

**Expected behavior during an outage**

This is what the code is written to do. Redis and PostgreSQL outage tests have not been run yet, so treat it as expected, not verified.

- **Redis down:** `POST /jobs` saves the job, cannot queue it, marks it `failed` (`queue unavailable: ...`), answers HTTP 503 "Queue unavailable", and increases `taskpulse_enqueue_failures_total`. Rate limiting fails open. The worker cannot receive jobs.
- **PostgreSQL down:** requests that need the database return errors. A worker that cannot reach the database counts the failure with reason `database_error` and retries up to 3 times (2, 4 and 8 seconds). If the database stays down, the job row can stay in its last saved status.

## 8. Structured logging and failure diagnostics

This section describes how to investigate API requests, worker jobs, failures and memory usage using the structured JSON logs in Grafana and Loki.

### 8.1 Log collection flow

```text
FastAPI / Celery worker
        |
        v
Structured JSON logs (one JSON object per line)
        |
        v
Docker stdout
        |
        v
Grafana Alloy (parses the JSON lines)
        |
        v
Loki
        |
        v
Grafana Explore and dashboards
```

The Alloy configuration is maintained in `observability/alloy/config.alloy`.

### 8.2 API request logs

API request logs include:

- `request_id`: unique request identifier.
- `method`: HTTP method.
- `path`: request route.
- `status_code`: HTTP response status.
- `duration_ms`: request processing time.
- `client`: hashed client identifier.
- `process_rss_mb`: memory held by the API process.
- `memory_delta_mb`: memory change during the request.
- `system_memory_percent`: system memory utilization.
- `trace_id`: distributed trace identifier.

Client identifiers are hashed before logging. Raw API keys and IP addresses should not be written to structured logs.

To inspect API request logs, open Grafana → Explore → Loki:

```logql
{service="api"} | json | event="request_completed"
```

### 8.3 Worker job logs

Worker events provide visibility into job execution:

- `job_started`
- `job_retry_scheduled`
- `job_succeeded`
- `job_failed`
- `job_crashed`
- `job_resource_usage`

Important fields include:

- `job_id` and `job_type`
- `attempt` and `max_attempts`
- `duration_ms`
- `memory_rss_mb`
- `memory_before_mb`
- `memory_delta_mb`
- `system_memory_percent`
- `reason`, `error_type` and `error_message`
- `trace_id`

Useful Loki queries:

**Worker errors**

```logql
{service="worker"} | json | level="ERROR"
```

**Retries and permanent failures**

```logql
{service="worker"} | json | event=~"job_failed|job_retry_scheduled|job_crashed"
```

**Resource usage**

```logql
{service="worker"} | json | event="job_resource_usage"
```

**Logs for a particular job** (replace `JOB-ID` with the actual job ID)

```logql
{service="worker"} | json | job_id="JOB-ID"
```

**Logs of one failure reason**

```logql
{service="worker"} | json | reason="database_error"
```

### 8.4 Understanding memory fields

| Field | Meaning |
|---|---|
| `memory_rss_mb` | Worker process resident memory (RAM) at the measurement point |
| `memory_before_mb` | Memory recorded before job execution |
| `memory_delta_mb` | Change in process RSS during the job |
| `process_rss_mb` | API process resident memory |
| `system_memory_percent` | Overall system memory utilization |

A positive memory delta means process RSS increased during the measured interval. A negative delta means it decreased.

RSS is process memory, not memory exclusively allocated to an individual job or request. Read it as a process-level measurement around the work. Three limits to keep in mind:

- Python often keeps freed memory, so a small or zero delta does not prove that a job used no memory.
- The API handles several requests at the same time in one process, so the memory in an API log line cannot be assigned to a single client or request.
- Inside Docker Desktop, `system_memory_percent` describes the Linux VM that runs all the containers.

### 8.5 Grafana Failure Diagnostics dashboard

Open the **TaskPulse Failure Diagnostics** dashboard.

Use these panels during an investigation:

- **Failure Attempts by Reason:** identifies the recorded failure reasons.
- **Last Errors:** displays recent worker error logs.
- **Memory Usage per Job Type:** helps compare resource usage across job types.
- **Clients:** helps investigate client-related activity.
- **Worker Warning and Error Logs:** provides detailed worker events.

For a failure investigation:

1. Check the failure reason.
2. Open the Last Errors panel.
3. Inspect `job_id`, `attempt`, `error_type` and `error_message`.
4. Copy the `trace_id`.
5. Open Grafana → Explore → Tempo and search using that trace ID.
6. Compare the job's resource-usage event with its failure event.

Use the same trace ID to correlate the log and the trace. Some failures may not have a complete trace or all expected spans; record that as an observability gap rather than assuming the operation succeeded.

### 8.6 Troubleshooting missing logs

If expected logs do not appear in Grafana:

1. Check whether the API and worker containers are running:

   ```powershell
   docker compose ps api worker
   ```

2. Inspect recent container output:

   ```powershell
   docker compose logs --tail 100 api
   docker compose logs --tail 100 worker
   ```

3. Check Alloy:

   ```powershell
   docker compose logs --tail 100 alloy
   ```

4. Check the syntax of Alloy's configuration. This prints the formatted file; an error message means a syntax problem. It does not prove that the pipeline works, so also run the broad query in step 6.

   ```powershell
   docker compose exec alloy alloy fmt /etc/alloy/config.alloy
   ```

   The path is where the file is mounted inside the Alloy container; check the Alloy volume in `docker-compose.yml` if it differs.

5. Confirm Loki is running:

   ```powershell
   docker compose ps loki
   docker compose logs --tail 100 loki
   ```

6. Open Grafana Explore, select Loki, and check a broad query:

   ```logql
   {service="worker"}
   ```

If logs exist in Docker output but not in Loki, investigate Alloy discovery, parsing and forwarding before changing application logging code.

### 8.7 Log and metadata guidelines

- Keep indexed Loki labels low-cardinality, such as `level`, `event` and `reason`. Every new label value creates a new Loki stream.
- Keep identifiers such as `job_id`, `request_id`, `trace_id` and `client` out of the labels. They stay in the log line (or in structured metadata, if it is enabled in Alloy) and are read with `| json`.
- Do not log raw API keys, credentials or sensitive client information.
- Use `trace_id` to correlate logs with distributed traces.
- Do not assume a missing log means a job succeeded; verify its persisted status and worker events as well.

## 9. System resource metrics

The application exposes these system metrics:

- `taskpulse_cpu_used_ratio`
- `taskpulse_memory_used_ratio`
- `taskpulse_disk_used_ratio`

The dashboard may show measurements from both API and worker collectors. Check the `job` and `instance` labels before comparing values. Do not assume duplicate series are separate physical machines; both containers may report measurements for their host or container environment.

If resource usage is high, inspect the relevant container logs and workload before taking action. Do not deliberately exhaust disk or memory on a development machine as a test.

## 10. Worker crash and potentially stuck jobs

A previous controlled worker-kill incident showed that jobs could remain in PostgreSQL as `running` or `pending` while the Redis ready queue was empty and the worker was idle. The incident evidence recorded:

- `task_acks_late=True`.
- Redis `HLEN unacked` was 4 while `LLEN celery` was 0.
- Four job records remained stuck after the worker restarted.
- `broker_transport_options={"visibility_timeout": 120}` was added.
- After rebuilding and restarting, those four jobs completed and `unacked` returned to 0.

If a worker crash occurs and jobs appear stuck, inspect all three views rather than relying on queue depth alone:

1. Worker state and logs.
2. PostgreSQL job statuses and timestamps.
3. Redis ready queue and unacknowledged message counts.

Diagnostic commands:

```powershell
docker compose exec redis redis-cli LLEN celery
docker compose exec redis redis-cli HLEN unacked
docker compose exec postgres psql -U taskpulse -d taskpulse -P pager=off -c "SELECT status, count(*) FROM jobs GROUP BY status;"
```

The incident report explicitly records that a fresh worker-kill re-test after setting the 120-second visibility timeout had not yet been performed. Treat automatic recovery after a fresh crash as not fully re-verified until that test is completed.

Do not manually delete Redis messages or edit job rows as a first response. Preserve the evidence and investigate before attempting data repair.

## 11. Controlled load and failure testing

The failure-mix k6 script supports valid submissions, invalid job requests, and an optional rate-limit hammer scenario. A previously verified run used:

```powershell
k6 run -e JOB_RATE=1 -e BAD_RATE=1 -e HAMMER_RATE=0 -e DURATION=1m loadtest/failure_mix.js
```

That run produced 61 accepted (202) responses and 61 invalid (422) responses, with 0% unexpected HTTP request failures.

Run controlled tests only against the intended development environment. Keep `HAMMER_RATE=0` unless specifically testing rate limiting. Do not run destructive resource-exhaustion tests.

## 12. Known follow-up items

The incident report (`docs/incident-report.md`) records these follow-up items. They should remain visible until separately implemented and verified:

- Re-run the worker-kill test at a load below worker capacity and confirm jobs return within about two minutes.
- Add a stuck-jobs panel and alert for `running` or `pending` jobs older than five minutes.
- Check that `settings.task_rate_limit` cannot hold a task longer than the broker visibility timeout.
- Make tasks idempotent because late-acknowledged tasks may run more than once.
- Investigate the four `Post ... EOF` errors recorded by k6 during the incident.
- Add an alert for queue depth near the configured limit of 500.
- Pin dependency versions in `requirements.txt`.

These are not claimed as completed by this runbook. Update this section only after each item has been implemented and verified.

## 13. Related project documentation

- [README.md](../README.md) - project overview, setup, and feature summary.
- [incident-report.md](incident-report.md) - worker-crash incident analysis and action items.
- [worker-kill-evidence.txt](worker-kill-evidence.txt) - raw evidence from the 2026-11-13 worker-kill test.
- [screenshots/](screenshots/) - saved dashboard and incident screenshots.