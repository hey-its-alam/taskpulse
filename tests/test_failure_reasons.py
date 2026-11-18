import errno
import socket

import pytest
import redis.exceptions as redis_exc
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import exc as sa_exc

from app.failure_reasons import REASONS, classify


@pytest.mark.parametrize(
    "exc, expected",
    [
        (RuntimeError("Simulated failure while running send_email"), "simulated"),
        (MemoryError(), "out_of_memory"),
        (OSError(errno.ENOSPC, "No space left on device"), "disk_full"),
        (redis_exc.ConnectionError("down"), "redis_error"),
        (redis_exc.TimeoutError("slow"), "redis_error"),
        (sa_exc.OperationalError("SELECT 1", {}, Exception("db down")), "database_error"),
        (TimeoutError(), "timeout"),
        (SoftTimeLimitExceeded(), "timeout"),
        (ConnectionRefusedError(), "network_error"),
        (ConnectionResetError(), "network_error"),
        (socket.gaierror(-2, "Name or service not known"), "network_error"),
        (KeyError("x"), "code_error"),
        (ZeroDivisionError(), "code_error"),
        (TypeError("bad"), "code_error"),
        (RuntimeError("something odd"), "unknown"),
    ],
)
def test_classify(exc, expected):
    assert classify(exc) == expected


def test_every_result_is_a_known_reason():
    for exc in (ValueError(), RuntimeError("x"), OSError("x"), redis_exc.RedisError()):
        assert classify(exc) in REASONS
