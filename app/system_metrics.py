"""Health of the machine and of the things the app depends on, read when Prometheus scrapes.

taskpulse_dependency_up{dependency}   1 if Redis / PostgreSQL answered a quick check, else 0
taskpulse_disk_used_ratio             disk used, 0..1
taskpulse_memory_used_ratio           memory used, 0..1
taskpulse_cpu_used_ratio              CPU busy, 0..1

Inside Docker Desktop, memory and CPU are those of the Linux VM that runs all containers,
and the disk is the container's own disk, which lives on that VM's disk.
Memory and CPU of just this process are already exported by prometheus_client
(process_resident_memory_bytes, process_cpu_seconds_total).
"""
import psutil
import redis
from opentelemetry.instrumentation.utils import suppress_instrumentation
from prometheus_client.core import REGISTRY, GaugeMetricFamily
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from .config import settings

CHECK_TIMEOUT_SECONDS = 1  # a dead dependency must not make the /metrics page hang

psutil.cpu_percent(interval=None)  # the first call always returns 0.0, so call it once now


def check_redis(url: str = None) -> bool:
    client = None
    try:
        client = redis.Redis.from_url(
            url or settings.redis_url,
            socket_connect_timeout=CHECK_TIMEOUT_SECONDS,
            socket_timeout=CHECK_TIMEOUT_SECONDS,
        )
        return bool(client.ping())
    except Exception:
        return False
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def check_postgres(url: str = None) -> bool:
    url = url or settings.database_url
    engine = None
    try:
        args = {"connect_timeout": CHECK_TIMEOUT_SECONDS} if url.startswith("postgresql") else {}
        engine = create_engine(url, poolclass=NullPool, connect_args=args)
        with suppress_instrumentation():
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        if engine is not None:
            engine.dispose()


class SystemCollector:
    def describe(self):
        return []

    def collect(self):
        deps = GaugeMetricFamily(
            "taskpulse_dependency_up", "1 if the dependency answered a quick check, else 0",
            labels=["dependency"],
        )
        deps.add_metric(["redis"], 1 if check_redis() else 0)
        deps.add_metric(["postgres"], 1 if check_postgres() else 0)
        yield deps

        try:
            yield GaugeMetricFamily("taskpulse_disk_used_ratio", "Disk used, 0 to 1",
                                    value=psutil.disk_usage("/").percent / 100)
            yield GaugeMetricFamily("taskpulse_memory_used_ratio", "Memory used, 0 to 1",
                                    value=psutil.virtual_memory().percent / 100)
            yield GaugeMetricFamily("taskpulse_cpu_used_ratio", "CPU busy, 0 to 1",
                                    value=psutil.cpu_percent(interval=None) / 100)
        except Exception:
            pass  # never break the whole /metrics page because of one reading


_registered = False


def register_system_collector():
    global _registered
    if not _registered:
        REGISTRY.register(SystemCollector())
        _registered = True
