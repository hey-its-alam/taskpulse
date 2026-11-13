# Incident Report: Jobs stuck after worker crash (invisible to the dashboard)

| | |
|---|---|
| **Date** | 2026-11-13 |
| **Type** | Controlled failure test (worker killed with `docker compose kill worker`) |
| **Severity** | Medium. No data was lost, but 4 jobs were stuck with no alert. |
| **Status** | Resolved for the 4 affected jobs. A fresh worker-kill re-test was not run (see section 9). |

## 1. Summary

During a failure-simulation test, the Celery worker container was killed while jobs were running, then started again after a few minutes. After the worker came back, the queue drained normally and the system looked healthy: Redis queue depth was 0, the worker was UP, and the dashboard showed 0 active jobs.

However, **4 jobs stayed stuck** in the database (3 as `running`, 1 as `pending`) and never finished. They were not in the queue and no worker was processing them. Nothing in the dashboard showed this.

Root cause (consistent with the evidence, see section 5): with Redis as the Celery broker and `task_acks_late=True`, messages that a dead worker had taken but not acknowledged stay in Redis `unacked` until the broker's visibility timeout expires. The default is 1 hour, so those jobs would not have been retried for up to an hour.

## 2. Impact

- **4 of 3,940 jobs** (about 0.1%) were stuck. Job IDs:
  - `2b841731-6727-4e44-bd82-20976e30231d` (generate_report, running)
  - `daa42824-dece-40b8-ba4c-ae2f22c2ba13` (send_email, running)
  - `ab66da18-47d2-47f0-a6ba-6a4d6b8245c4` (generate_report, running)
  - `dac1ec8d-0a45-45a8-9d5c-93008359822f` (resize_image, pending, never started)
- No jobs were lost from the database: before and after, the total was 3,940 (12 failed + 3,928 success after recovery).
- The API stayed fast during the whole test: **p95 = 20.87 ms, p99 = 27.73 ms** (k6, 721 requests).
- While the worker was down, the queue grew to about 500 (the configured maximum), so a few requests were rejected with 503 (k6 counted 2).
- Time stuck: from about 10:39 IST until the fix was deployed. The exact duration was not recorded; several status checks over a longer period all showed the same 4 jobs unchanged.

## 3. Timeline (IST; the database stores UTC, which is 5h30m earlier)

| Time (IST) | Event |
|---|---|
| 10:39 | Worker killed. The 3 `running` jobs had `started_at` between 10:39:04 and 10:39:07. |
| ~10:41 | Grafana: Workers up = DOWN, queue depth rising (283), throughput 0. Database: pending 151, running 3, retrying 3. |
| A few minutes later (exact time not recorded) | Worker started again with `docker compose start worker`. |
| ~10:45 | Queue reached about 500 and a short burst of 503 responses appeared. Worker back UP, throughput about 85 jobs/min, queue draining. |
| After the queue emptied (exact time not recorded) | Database still showed `running 3`, `pending 1`, and Redis `HLEN unacked` = 4, with `LLEN celery` = 0 and the worker idle. |
| Later (exact time not recorded) | The 4 stuck jobs identified (list in section 2). Repeated checks showed no change. |
| Later (exact time not recorded) | Fix deployed: `visibility_timeout=120` added, containers rebuilt and restarted. |
| A few minutes after the restart | Database: `running` and `pending` gone, `success` 3,928, `unacked` = 0. |

## 4. Detection

What showed the problem:

- **Database status query** (`SELECT status, count(*) FROM jobs GROUP BY status`): `running 3` and `pending 1` stayed constant while the queue was empty and the worker was idle.
- **Redis `HLEN unacked`** stayed at 4 with `LLEN celery` = 0.
- **Job rows**: `retries = 0`, `error_message` empty, `finished_at` empty. The jobs never failed. They just disappeared from the pipeline.

What did **not** show the problem (monitoring gap):

- The "Active jobs" panel showed 0, because it comes from worker metrics and the worker was dead.
- Queue depth showed 0 once the queue emptied.
- No error log was written for these jobs, so log-based alerting would not have caught it either.

## 5. Root cause

1. The worker was killed while 3 jobs were running and 1 job had been reserved but not started.
2. With `task_acks_late=True`, a message is acknowledged only after the task finishes. Un-acknowledged messages are kept by Redis in the `unacked` hash.
3. Celery/kombu with a Redis broker returns un-acknowledged messages to the queue only after the **visibility timeout**. The default is 1 hour, and the project did not set it.
4. The database still said `running`, so nothing in the app knew these jobs were orphaned.

How sure are we: the observed behavior (jobs stuck, `unacked` = 4, queue empty, worker idle) matches this mechanism, and the jobs recovered after the timeout was shortened and the worker restarted. The 1-hour default comes from Celery's documentation and was not waited out in this test.

Contributing factors:

- The test load (2 jobs/s) was **above worker capacity** (about 1.4 jobs/s measured, roughly 85 jobs/min). The queue was growing even before the worker was killed, so the downtime made it fill faster.
- Dashboard panels depend on the worker being alive.

## 6. Resolution

In `app/celery_app.py`, added to `celery.conf.update(...)`:

```python
broker_transport_options={"visibility_timeout": 120},
```

120 seconds was chosen because the longest job takes about 8 seconds and retry delays are 2, 4 and 8 seconds, so no healthy job should stay unacknowledged for 2 minutes.

After rebuilding and restarting, the 4 stuck jobs finished (confirmed only on this set of jobs, not yet on a fresh crash): `success` went from 3,924 to 3,928, `running` and `pending` disappeared, and `unacked` returned to 0.

## 7. What went well

- API latency stayed low while the worker was down (p95 20.87 ms).
- The queue limit worked: when the queue filled, the API returned 503 instead of crashing.
- When the worker returned, throughput went back to about 85 jobs/min on its own.
- Database state plus the Redis `unacked` hash made it possible to prove what happened.

## 8. What went badly

- A crash left jobs stuck with no alert and no error.
- The dashboard looked healthy while 4 jobs were stuck.
- A broker default (1 hour) was silently in effect and nobody had checked it.

## 9. Action items

| # | Action | Status |
|---|---|---|
| 1 | Set `visibility_timeout` to 120 s | Done |
| 2 | Re-run the worker-kill test with `RATE=1` (below worker capacity) on a fresh crash and confirm jobs come back on their own within about 2 minutes. Not run yet, so the fix is only confirmed on the 4 jobs from this incident. | Optional |
| 3 | Add a "Stuck jobs" panel and alert: jobs in `running` or `pending` for longer than 5 minutes | To do |
| 4 | Check `settings.task_rate_limit`: a task held back by the rate limiter must not wait longer than the visibility timeout, or it can be delivered twice | To do |
| 5 | Make tasks safe to run twice (idempotent). With `acks_late`, a recovered job can run again | To do |
| 6 | Find the cause of the 4 `Post ... EOF` errors seen by k6 (API logs showed no 500s) | To do |
| 7 | Add an alert for queue depth near the limit (500) and for workers DOWN | To do |
| 8 | Pin versions in `requirements.txt` (the build installed the newest versions of every library) | To do |

## 10. Evidence

Screenshots are in `docs/screenshots/`:

- `grafana-worker-kill.png`: Workers up = DOWN, queue growing
- `grafana-worker-recovered.png`: worker back, queue draining, short 503 burst
- k6 summary of the run: 721 requests, 715 accepted, 2 x 503, 4 connection EOF errors, p95 20.87 ms