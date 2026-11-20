
import logging
import random
import time
import uuid
from datetime import datetime, timezone

from celery.exceptions import Retry

from .celery_app import celery
from .config import settings
from .database import SessionLocal
from .failure_reasons import classify
from .metrics import (
    ACTIVE_JOBS,
    JOB_DURATION,
    JOB_FAILURES,
    JOB_RETRIES,
    JOBS_PROCESSED,
)
from .models import Job
from .resource_usage import get_memory_snapshot

log = logging.getLogger("taskpulse.worker")

PROFILES = {
    "generate_report": (2.0, 6.0),
    "send_email": (0.2, 1.0),
    "resize_image": (0.5, 2.0),
}


def simulate_work(job_type: str):
    low, high = PROFILES[job_type]
    time.sleep(random.uniform(low, high) + settings.extra_latency_seconds)

    if random.random() < settings.failure_rate:
        raise RuntimeError(
            f"Simulated failure while running {job_type}"
        )


def now():
    return datetime.now(timezone.utc)


def log_job_event(level, event, message, **fields):
    """Write a structured worker event."""
    exc_info = fields.pop("exc_info", False)

    log.log(
        level,
        message,
        extra={
            "service": "worker",
            "event": event,
            **fields,
        },
        exc_info=exc_info,
    )


@celery.task(
    bind=True,
    name="run_job",
    max_retries=3,
    rate_limit=settings.task_rate_limit,
)
def run_job(self, job_id: str):
    db = SessionLocal()
    started = time.perf_counter()
    memory_before = get_memory_snapshot()

    job_type = "unknown"
    attempt = self.request.retries + 1
    max_attempts = self.max_retries + 1

    ACTIVE_JOBS.inc()

    try:
        job = db.get(Job, uuid.UUID(job_id))

        if job is None:
            log_job_event(
                logging.ERROR,
                "job_not_found",
                "Job not found",
                job_id=job_id,
                attempt=attempt,
            )
            return

        job_type = job.type
        job.status = "running"
        job.started_at = job.started_at or now()
        db.commit()

        log_job_event(
            logging.INFO,
            "job_started",
            "Job started",
            job_id=job_id,
            job_type=job_type,
            attempt=attempt,
            max_attempts=max_attempts,
        )

        try:
            simulate_work(job_type)

        except Exception as exc:
            reason = classify(exc)

            JOB_FAILURES.labels(
                type=job_type,
                reason=reason,
            ).inc()

            if self.request.retries < self.max_retries:
                job.status = "retrying"
                job.retries = self.request.retries + 1
                job.error_message = str(exc)
                db.commit()

                JOB_RETRIES.labels(type=job_type).inc()

                log_job_event(
                    logging.WARNING,
                    "job_retry_scheduled",
                    "Job will retry",
                    job_id=job_id,
                    job_type=job_type,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    reason=reason,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    countdown_seconds=2 ** attempt,
                )

                raise self.retry(
                    exc=exc,
                    countdown=2 ** attempt,
                )

            job.status = "failed"
            job.error_message = str(exc)
            job.finished_at = now()
            db.commit()

            JOBS_PROCESSED.labels(
                type=job_type,
                status="failed",
            ).inc()

            log_job_event(
                logging.ERROR,
                "job_failed",
                "Job failed permanently",
                job_id=job_id,
                job_type=job_type,
                attempt=attempt,
                max_attempts=max_attempts,
                reason=reason,
                error_type=type(exc).__name__,
                error_message=str(exc),
                exc_info=True,
            )
            return

        job.status = "success"
        job.error_message = None
        job.finished_at = now()
        db.commit()

        JOBS_PROCESSED.labels(
            type=job_type,
            status="success",
        ).inc()

        log_job_event(
            logging.INFO,
            "job_succeeded",
            "Job succeeded",
            job_id=job_id,
            job_type=job_type,
            attempt=attempt,
            max_attempts=max_attempts,
        )

    except Retry:
        raise

    except Exception as exc:
        reason = classify(exc)

        JOB_FAILURES.labels(
            type=job_type,
            reason=reason,
        ).inc()

        log_job_event(
            logging.ERROR,
            "job_crashed",
            "Job crashed due to an unexpected error",
            job_id=job_id,
            job_type=job_type,
            attempt=attempt,
            max_attempts=max_attempts,
            reason=reason,
            error_type=type(exc).__name__,
            error_message=str(exc),
            exc_info=True,
        )

        if self.request.retries < self.max_retries:
            JOB_RETRIES.labels(type=job_type).inc()

            log_job_event(
                logging.WARNING,
                "job_retry_scheduled",
                "Crashed job will retry",
                job_id=job_id,
                job_type=job_type,
                attempt=attempt,
                max_attempts=max_attempts,
                reason=reason,
                error_type=type(exc).__name__,
                error_message=str(exc),
                countdown_seconds=2 ** attempt,
            )

            raise self.retry(
                exc=exc,
                countdown=2 ** attempt,
            )

        JOBS_PROCESSED.labels(
            type=job_type,
            status="failed",
        ).inc()

        try:
            db.rollback()
            job = db.get(Job, uuid.UUID(job_id))

            if job is not None:
                job.status = "failed"
                job.error_message = f"{reason}: {exc}"
                job.finished_at = now()
                db.commit()

        except Exception:
            log.exception(
                "Could not mark job as failed",
                extra={
                    "service": "worker",
                    "event": "job_status_update_failed",
                    "job_id": job_id,
                    "job_type": job_type,
                },
            )

        raise

    finally:
        duration_seconds = time.perf_counter() - started
        duration_ms = round(duration_seconds * 1000, 2)

        memory_after = get_memory_snapshot()

        memory_delta_mb = round(
            memory_after["process_rss_mb"]
            - memory_before["process_rss_mb"],
            2,
        )

        log_job_event(
            logging.INFO,
            "job_resource_usage",
            "Job resource usage recorded",
            job_id=job_id,
            job_type=job_type,
            attempt=attempt,
            max_attempts=max_attempts,
            duration_ms=duration_ms,
            memory_rss_mb=memory_after["process_rss_mb"],
            memory_before_mb=memory_before["process_rss_mb"],
            memory_delta_mb=memory_delta_mb,
            system_memory_percent=(
                memory_after["system_memory_percent"]
            ),
        )

        JOB_DURATION.labels(type=job_type).observe(duration_seconds)
        ACTIVE_JOBS.dec()
        db.close()
