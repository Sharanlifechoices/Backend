"""In-memory sliding-window rate limiter (single-instance deployment on Render).

Use as a dependency factory:  Depends(rate_limit("signup", limit=5, window=3600))
A second helper `check_key` lets handlers rate-limit by a body value such as email.
If the API is ever scaled to multiple instances, move the buckets to Redis.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.core.logging_setup import log_event

_log = logging.getLogger("rate_limit")
_buckets: dict[str, deque[float]] = defaultdict(deque)


def check_key(key: str, limit: int, window: int) -> None:
    now = time.monotonic()
    bucket = _buckets[key]
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(bucket) >= limit:
        retry_after = max(1, int(window - (now - bucket[0])))
        log_event(_log, "rate limit hit", {"key": key, "limit": limit, "window": window}, level=logging.WARNING)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    bucket.append(now)


def forwarded_client_ip(forwarded_for: str | None, direct_host: str | None) -> str:
    """Client IP behind a single trusted proxy (Render).

    The proxy APPENDS the address it saw, so the RIGHTMOST X-Forwarded-For entry is
    trustworthy; the leftmost is client-controlled and spoofable. If a CDN is ever put
    in front of Render, this must change to skip one more hop.
    """
    if forwarded_for:
        last = forwarded_for.split(",")[-1].strip()
        if last:
            return last
    return direct_host or "unknown"


def client_ip(request: Request) -> str:
    return forwarded_client_ip(
        request.headers.get("x-forwarded-for"), request.client.host if request.client else None
    )


def rate_limit(name: str, limit: int, window: int):
    """Per-IP limiter dependency."""

    def dependency(request: Request) -> None:
        check_key(f"{name}:ip:{client_ip(request)}", limit, window)

    return dependency


def reset_all() -> None:
    """Test helper."""
    _buckets.clear()
