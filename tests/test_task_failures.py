from types import SimpleNamespace
from uuid import uuid4

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import exc as sa_exc

from app import tasks


class FakeSession:
    """Stands in for the database: no PostgreSQL needed."""

    def __init__(self, job, fail_get_times=0):
        self.job = job
        self.fail_get_times = fail_get_times
        self.closed = False

    def get(self, model, key):
        if self.fail_get_times > 0:
            self.fail_get_times -= 1
            raise sa_exc.OperationalError("SELECT", {}, Exception("database is down"))
        return self.job

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def make_job(job_type="send_email"):
    return SimpleNamespace(type=job_type, status="pending", started_at=None, finished_at=None,
                           retries=0, error_message=None)


def value(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(tasks.time, "sleep", lambda s: None)


def test_simulated_failures_are_counted_with_a_reason(monkeypatch):
    job = make_job()
    monkeypatch.setattr(tasks, "SessionLocal", lambda: FakeSession(job))
    monkeypatch.setattr(tasks.settings, "failure_rate", 1.0)
    before = value("taskpulse_job_failures_total", type="send_email", reason="simulated")
    failed_before = value("taskpulse_jobs_processed_total", type="send_email", status="failed")

    tasks.run_job.apply(args=[str(uuid4())])

    assert job.status == "failed"
    assert value("taskpulse_job_failures_total", type="send_email", reason="simulated") - before == 4
    assert value("taskpulse_jobs_processed_total", type="send_email", status="failed") - failed_before == 1


def test_a_working_job_succeeds(monkeypatch):
    job = make_job()
    monkeypatch.setattr(tasks, "SessionLocal", lambda: FakeSession(job))
    monkeypatch.setattr(tasks.settings, "failure_rate", 0.0)
    tasks.run_job.apply(args=[str(uuid4())])
    assert job.status == "success"


def test_database_outage_is_counted_and_retried(monkeypatch):
    job = make_job()
    sessions = []

    def factory():
        s = FakeSession(job, fail_get_times=100)
        sessions.append(s)
        return s

    monkeypatch.setattr(tasks, "SessionLocal", factory)
    before = value("taskpulse_job_failures_total", type="unknown", reason="database_error")
    result = tasks.run_job.apply(args=[str(uuid4())])

    assert result.failed()
    assert value("taskpulse_job_failures_total", type="unknown", reason="database_error") - before == 4
    assert all(s.closed for s in sessions)


def test_database_that_comes_back_lets_the_job_finish(monkeypatch):
    job = make_job()
    fails = {"left": 2}

    def factory():
        s = FakeSession(job)
        s.fail_get_times = 1 if fails["left"] > 0 else 0
        fails["left"] -= 1
        return s

    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks.settings, "failure_rate", 0.0)
    result = tasks.run_job.apply(args=[str(uuid4())])
    assert not result.failed()
    assert job.status == "success"


def test_timeout_failure_is_counted_and_retried(monkeypatch):
    job = make_job()
    monkeypatch.setattr(tasks, "SessionLocal", lambda: FakeSession(job))

    def raise_timeout(job_type):
        raise TimeoutError("Job execution timed out")

    monkeypatch.setattr(tasks, "simulate_work", raise_timeout)

    before = value(
        "taskpulse_job_failures_total",
        type="send_email",
        reason="timeout",
    )

    failed_before = value(
        "taskpulse_jobs_processed_total",
        type="send_email",
        status="failed",
    )

    result = tasks.run_job.apply(args=[str(uuid4())])

    assert result.successful()
    assert job.status == "failed"

    assert (
        value("taskpulse_job_failures_total",
              type="send_email",
              reason="timeout") - before
    ) == 4

    assert (
        value("taskpulse_jobs_processed_total",
              type="send_email",
              status="failed") - failed_before
    ) == 1

def test_redis_failure_is_classified_correctly():
    import redis.exceptions as redis_exc
    from app.failure_reasons import classify

    error = redis_exc.ConnectionError("Redis connection failed")

    assert classify(error) == "redis_error"


def test_network_failure_is_classified_correctly():
    from app.failure_reasons import classify

    error = ConnectionError("Network connection failed")

    assert classify(error) == "network_error"   

def test_code_failure_is_classified_correctly():
    from app.failure_reasons import classify

    error = ValueError("Invalid application value")

    assert classify(error) == "code_error"

def test_memory_failure_is_classified_correctly():
    from app.failure_reasons import classify

    error = MemoryError("Simulated memory exhaustion")

    assert classify(error) == "out_of_memory"

def test_disk_full_failure_is_classified_correctly():
    import errno
    from app.failure_reasons import classify

    error = OSError(errno.ENOSPC, "No space left on device")

    assert classify(error) == "disk_full"

def test_unknown_failure_is_classified_correctly():
    from app.failure_reasons import classify

    error = RuntimeError("Unexpected infrastructure issue")

    assert classify(error) == "unknown"