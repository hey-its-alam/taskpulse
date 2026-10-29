import random
import time
import uuid
from datetime import datetime, timezone

from .celery_app import celery
from .database import SessionLocal
from .models import Job

# (min, max) seconds of simulated work per job type
PROFILES = {
    "generate_report": (2.0, 6.0),
    "send_email": (0.2, 1.0),
    "resize_image": (0.5, 2.0),
}


def now():
    return datetime.now(timezone.utc)


@celery.task(name="run_job")
def run_job(job_id: str):
    db = SessionLocal()
    try:
        job = db.get(Job, uuid.UUID(job_id))
        if job is None:
            return
        job.status = "running"
        job.started_at = now()
        db.commit()

        low, high = PROFILES[job.type]
        time.sleep(random.uniform(low, high))

        job.status = "success"
        job.finished_at = now()
        db.commit()
    finally:
        db.close()