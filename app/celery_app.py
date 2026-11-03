from celery import Celery, signals
from prometheus_client import start_http_server

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


@signals.setup_logging.connect
def configure_logging(**kwargs):
    # Connecting to this signal tells Celery to skip its own log setup.
    from .logging_config import setup_logging

    setup_logging()


@signals.worker_init.connect
def on_worker_init(**kwargs):
    from .telemetry import setup_tracing

    setup_tracing("taskpulse-worker")
    start_http_server(8001)  # worker metrics at :8001/metrics