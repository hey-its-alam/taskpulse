from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import Job
from .schemas import JobCreate, JobOut
from .tasks import run_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobOut, status_code=202)
def submit_job(body: JobCreate, db: Session = Depends(get_db)):
    job = Job(type=body.type, payload=body.payload, status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)
    run_job.delay(str(job.id))
    return job


@router.get("", response_model=list[JobOut])
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


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: UUID, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job