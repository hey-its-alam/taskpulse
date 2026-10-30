from typing import Optional
from uuid import UUID

import redis
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import Job
from .rate_limiter import rate_limit
from .schemas import JobCreate, JobOut
from .tasks import run_job

router = APIRouter(prefix="/jobs", tags=["jobs"])
_redis = redis.Redis.from_url(settings.redis_url)


def queue_depth() -> int:
    try:
        return int(_redis.llen("celery"))
    except redis.RedisError:
        return 0


@router.post(
    "", response_model=JobOut, status_code=202,
    dependencies=[Depends(rate_limit("submit", settings.rate_limit_submit))],
)
def submit_job(body: JobCreate, db: Session = Depends(get_db)):
    # Backpressure: refuse new work when the queue is already too long.
    if queue_depth() >= settings.max_queue_depth:
        raise HTTPException(503, "Queue is full, try again later", headers={"Retry-After": "10"})

    job = Job(type=body.type, payload=body.payload, status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)
    run_job.delay(str(job.id))
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
    return job