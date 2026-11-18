"""Turn an exception into one short, fixed reason word.

The reason is used as a Prometheus label and in log lines, so the list is kept small on purpose
(many different label values make Prometheus slow). Add a new word to REASONS only when you
also want a new line in Grafana.
"""
import errno

import redis.exceptions as redis_exc
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import exc as sa_exc

REASONS = (
    "simulated",       # the FAILURE_RATE test failure
    "timeout",         # something took too long
    "redis_error",     # Redis (queue) not reachable or errored
    "database_error",  # PostgreSQL not reachable or errored
    "network_error",   # connection refused, reset or DNS failure
    "out_of_memory",   # MemoryError
    "disk_full",       # no space left on device
    "code_error",      # a bug: wrong type, missing key, divide by zero...
    "unknown",
)


def classify(exc: BaseException) -> str:
    if "simulated failure" in str(exc).lower():
        return "simulated"
    if isinstance(exc, MemoryError):
        return "out_of_memory"
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.ENOSPC:
        return "disk_full"
    if isinstance(exc, redis_exc.RedisError):  # includes Redis timeouts
        return "redis_error"
    if isinstance(exc, sa_exc.SQLAlchemyError):  # includes OperationalError (DB down)
        return "database_error"
    if isinstance(exc, (TimeoutError, SoftTimeLimitExceeded)):
        return "timeout"
    if isinstance(exc, ConnectionError):  # refused, reset, aborted; also socket.gaierror below
        return "network_error"
    if isinstance(exc, OSError) and exc.__class__.__name__ == "gaierror":
        return "network_error"
    if isinstance(exc, (TypeError, KeyError, AttributeError, IndexError, NameError,
                        ValueError, ZeroDivisionError, AssertionError)):
        return "code_error"
    return "unknown"
