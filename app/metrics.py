import redis
from opentelemetry.instrumentation.utils import suppress_instrumentation
from prometheus_client import Counter, Gauge, Histogram
from prometheus_client.core import REGISTRY, GaugeMetricFamily
from sqlalchemy import func, select

from .config import settings
from .database import SessionLocal
from .models import Job

_redis = redis.Redis.from_url(settings.redis_url)

# ---- API side ----
JOBS_SUBMITTED = Counter("taskpulse_jobs_submitted", "Jobs accepted by the API", ["type"])
RATE_LIMITED = Counter("taskpulse_rate_limited", "Requests rejected with HTTP 429", ["scope"])
BACKPRESSURE = Counter("taskpulse_backpressure_rejections", "Jobs rejected: queue is full")

# ---- Worker side ----
JOBS_PROCESSED = Counter("taskpulse_jobs_processed", "Jobs finished by workers", ["type", "status"])
JOB_RETRIES = Counter("taskpulse_job_retries", "Job retry attempts", ["type"])
JOB_DURATION = Histogram(
    "taskpulse_job_duration_seconds", "Job execution time", ["type"],
    buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32),
)
ACTIVE_JOBS = Gauge("taskpulse_active_jobs", "Jobs running right now")


# ---- Queue depth (read straight from the Redis list) ----
def queue_depth() -> int:
    try:
        return int(_redis.llen("celery"))
    except redis.RedisError:
        return 0


QUEUE_DEPTH = Gauge("taskpulse_queue_depth", "Jobs waiting in the Redis queue")
QUEUE_DEPTH.set_function(queue_depth)


class JobStatusCollector:
    # Reads job counts per status from PostgreSQL on every Prometheus scrape.
    def describe(self):
        return []

    def collect(self):
        family = GaugeMetricFamily("taskpulse_jobs_by_status", "Jobs per status", labels=["status"])
        try:
            with suppress_instrumentation():  # keep this query out of the traces
                with SessionLocal() as db:
                    rows = db.execute(select(Job.status, func.count()).group_by(Job.status)).all()
            for status, count in rows:
                family.add_metric([status], count)
        except Exception:
            pass
        yield family


def register_db_collector():
    REGISTRY.register(JobStatusCollector())