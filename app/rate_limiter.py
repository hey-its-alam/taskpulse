import logging
import time
from typing import Optional

import redis
from fastapi import HTTPException, Request, Response

from .config import settings

log = logging.getLogger("taskpulse.ratelimit")
_redis = redis.Redis.from_url(settings.redis_url)


def rate_limit(scope: str, limit: int, window: Optional[int] = None):
    # Fixed-window counter per client, stored in Redis.
    window = window or settings.rate_limit_window_seconds

    def dependency(request: Request, response: Response):
        client = request.headers.get("x-api-key") or (
            request.client.host if request.client else "unknown"
        )
        now = int(time.time())
        reset_in = window - (now % window)
        key = f"rl:{scope}:{client}:{now // window}"

        try:
            pipe = _redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, window + 1)
            count, _ = pipe.execute()
        except redis.RedisError:
            return  # fail open: if Redis is down, do not block users

        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - count))
        response.headers["X-RateLimit-Reset"] = str(reset_in)

        if count > limit:
            log.warning("rate limit hit scope=%s client=%s count=%s", scope, client, count)
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Slow down and retry later.",
                headers={
                    "Retry-After": str(reset_in),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

    return dependency