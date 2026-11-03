import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .metrics import BACKPRESSURE, JOBS_SUBMITTED, queue_depth
from .models import Job
from .rate_limiter import rate_limit
from .schemas import JobCreate, JobOut
from .tasks import run_job

log = logging.getLogger("taskpulse.api")
router = APIRouter(prefix="/jobs", tags=["jobs"])


def current_trace_id() -> Optional[str]:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


@router.post(
    "", response_model=JobOut, status_code=202,
    dependencies=[Depends(rate_limit("submit", settings.rate_limit_submit))],
)
def submit_job(body: JobCreate, db: Session = Depends(get_db)):
    # Backpressure: refuse new work when the queue is already too long.
    if queue_depth() >= settings.max_queue_depth:
        BACKPRESSURE.inc()
        raise HTTPException(503, "Queue is full, try again later", headers={"Retry-After": "10"})

    job = Job(type=body.type, payload=body.payload, status="pending", trace_id=current_trace_id())
    db.add(job)
    db.commit()
    db.refresh(job)

    run_job.delay(str(job.id))
    JOBS_SUBMITTED.labels(type=job.type).inc()
    log.info("job submitted id=%s type=%s", job.id, job.type)
    return job


@router.get(
    "", response_model=list[JobOut],
    dependencies=[Depends(rate_limit("read", settings.rate_limit_read))],
)
def list_jobs(
    status: Optional[str] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    stmt = select(Job).order_by(Job.created_at.desc()).limit(limit).offset(offset)
    if status:
        stmt = stmt.where(Job.status == status)
    return db.execute(stmt).scalars().all()


@router.get(
    "/{job_id}", response_model=JobOut,
    dependencies=[Depends(rate_limit("read", settings.rate_limit_read))],
)
def get_job(job_id: UUID, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@router.post(
    "/{job_id}/retry", response_model=JobOut, status_code=202,
    dependencies=[Depends(rate_limit("retry", settings.rate_limit_retry))],
)
def retry_job(job_id: UUID, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status != "failed":
        raise HTTPException(409, "Only failed jobs can be retried")
    job.status = "pending"
    job.retries = 0
    job.error_message = None
    job.started_at = None
    job.finished_at = None
    db.commit()
    run_job.delay(str(job.id))
    log.info("job manually retried id=%s", job.id)
    return job