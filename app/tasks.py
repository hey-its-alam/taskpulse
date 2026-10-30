import logging
import random
import time
import uuid
from datetime import datetime, timezone

from .celery_app import celery
from .config import settings
from .database import SessionLocal
from .models import Job

log = logging.getLogger("taskpulse.worker")

# (min, max) seconds of simulated work per job type
PROFILES = {
    "generate_report": (2.0, 6.0),
    "send_email": (0.2, 1.0),
    "resize_image": (0.5, 2.0),
}


def simulate_work(job_type: str):
    low, high = PROFILES[job_type]
    time.sleep(random.uniform(low, high) + settings.extra_latency_seconds)
    if random.random() < settings.failure_rate:
        raise RuntimeError(f"Simulated failure while running {job_type}")


def now():
    return datetime.now(timezone.utc)


@celery.task(bind=True, name="run_job", max_retries=3, rate_limit=settings.task_rate_limit)
def run_job(self, job_id: str):
    db = SessionLocal()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        if job is None:
            log.error("job not found id=%s", job_id)
            return
        job.status = "running"
        job.started_at = job.started_at or now()
        db.commit()
        log.info("job started id=%s type=%s attempt=%s", job_id, job.type, self.request.retries + 1)

        try:
            simulate_work(job.type)
        except Exception as exc:
            if self.request.retries < self.max_retries:
                job.status = "retrying"
                job.retries = self.request.retries + 1
                job.error_message = str(exc)
                db.commit()
                log.warning("job will retry id=%s retry=%s error=%s", job_id, job.retries, exc)
                raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1))
            job.status = "failed"
            job.error_message = str(exc)
            job.finished_at = now()
            db.commit()
            log.error("job failed permanently id=%s error=%s", job_id, exc)
            return

        job.status = "success"
        job.error_message = None
        job.finished_at = now()
        db.commit()
        log.info("job succeeded id=%s type=%s", job_id, job.type)
    finally:
        db.close()