import time

from prometheus_client import CollectorRegistry, generate_latest

from app import system_metrics
from app.system_metrics import SystemCollector, check_postgres, check_redis


class FakeRedis:
    def ping(self):
        return True

    def close(self):
        pass


def test_dead_redis_is_reported_down_quickly():
    start = time.time()
    assert check_redis("redis://127.0.0.1:1/0") is False
    assert time.time() - start < 3


def test_dead_postgres_is_reported_down_quickly():
    start = time.time()
    assert check_postgres("postgresql+psycopg2://u:p@127.0.0.1:1/db") is False
    assert time.time() - start < 3


def test_working_redis_is_reported_up(monkeypatch):
    monkeypatch.setattr(system_metrics.redis.Redis, "from_url", staticmethod(lambda *a, **k: FakeRedis()))
    assert check_redis("redis://anything") is True


def test_working_database_is_reported_up():
    assert check_postgres("sqlite://") is True


def test_collector_exports_all_metrics(monkeypatch):
    monkeypatch.setattr(system_metrics, "check_redis", lambda url=None: True)
    monkeypatch.setattr(system_metrics, "check_postgres", lambda url=None: False)
    registry = CollectorRegistry()
    registry.register(SystemCollector())
    text = generate_latest(registry).decode()
    assert 'taskpulse_dependency_up{dependency="redis"} 1.0' in text
    assert 'taskpulse_dependency_up{dependency="postgres"} 0.0' in text
    for name in ("taskpulse_disk_used_ratio", "taskpulse_memory_used_ratio", "taskpulse_cpu_used_ratio"):
        assert name in text


def test_register_twice_is_safe():
    system_metrics.register_system_collector()
    system_metrics.register_system_collector()
