from celery import Celery

from .config import settings

celery = Celery(
    "taskpulse",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks"],
)
celery.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
)