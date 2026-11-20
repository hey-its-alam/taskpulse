
import hashlib
import logging
import time
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware

from .resource_usage import get_memory_snapshot


log = logging.getLogger("taskpulse.api")


def get_client_hash(request):
    """
    Create an anonymous client identifier.
    Never log the raw API key or IP address.
    """
    api_key = request.headers.get("X-API-Key")

    if api_key:
        identity = f"api_key:{api_key}"
    elif request.client:
        identity = f"ip:{request.client.host}"
    else:
        identity = "unknown"

    return hashlib.sha256(identity.encode()).hexdigest()[:12]


class RequestLoggingMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request, call_next):
        request_id = str(uuid4())
        request.state.request_id = request_id

        start_time = time.perf_counter()
        memory_before = get_memory_snapshot()

        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code

            response.headers["X-Request-ID"] = request_id

            return response

        except Exception:
            log.exception(
                "API request failed",
                extra={
                    "service": "api",
                    "event": "request_failed",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": 500,
                    "client": get_client_hash(request),
                },
            )
            raise

        finally:
            duration_ms = round(
                (time.perf_counter() - start_time) * 1000, 2
            )

            memory_after = get_memory_snapshot()

            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)

            log.info(
                "API request completed",
                extra={
                    "service": "api",
                    "event": "request_completed",
                    "request_id": request_id,
                    "method": request.method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "client": get_client_hash(request),
                    "process_rss_mb": memory_after["process_rss_mb"],
                    "process_memory_delta_mb": round(
                        memory_after["process_rss_mb"]
                        - memory_before["process_rss_mb"],
                        2,
                    ),
                    "system_memory_percent": (
                        memory_after["system_memory_percent"]
                    ),
                },
            )
