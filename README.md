# TaskPulse

Distributed job processing platform with full observability.

## Stack
FastAPI, Celery, Redis, PostgreSQL, Prometheus, Grafana, Docker Compose

## Features
- Submit background jobs over HTTP (202 Accepted)
- Celery workers with retries and exponential backoff (2s, 4s, 8s)
- Job status tracking in PostgreSQL (pending, running, retrying, success, failed)
- Manual retry for failed jobs
- Per-client rate limiting (HTTP 429 with Retry-After)
- Queue backpressure (HTTP 503 when the queue is full)
- Prometheus metrics and a Grafana dashboard (queue depth, throughput, active jobs)

## API
| Method | Endpoint | Purpose |
|---|---|---|
| POST | /jobs | Submit a job |
| GET | /jobs | List jobs, filter by status |
| GET | /jobs/{id} | Job details |
| POST | /jobs/{id}/retry | Retry a failed job |
| GET | /health | Health check |
| GET | /metrics | Prometheus metrics |

## Run
```
copy .env.example .env
docker compose up --build -d
```
(On Mac/Linux use `cp` instead of `copy`.)

## Tests
```
docker compose exec api pytest -q
```

## Observability
Prometheus scrapes metrics from the API (`:8000/metrics`) and the worker (`:8001/metrics`).
The Grafana dashboard shows queue depth, active jobs, request rate, throughput and jobs by status.

![Dashboard](docs/screenshots/dashboard-v1.png.png)

| Service | URL |
|---|---|
| API docs | http://localhost:8000/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |

## Roadmap
- Structured logs with Loki
- Distributed tracing with OpenTelemetry and Tempo
- Alerts, load tests and incident report