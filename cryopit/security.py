"""Security primitives with no Flask dependency.

Keeping token verification and rate limiting independent of the HTTP framework
makes the production boundary easy to exercise in lightweight unit tests.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import threading
import time
from collections import defaultdict, deque

_TOKEN_VERSION = "v1"


def _bucket(now: float, ttl_seconds: int) -> int:
    return int(now // ttl_seconds)


def issue_csrf_token(user: str, secret: str, *, now: float | None = None,
                     ttl_seconds: int = 43200) -> str:
    """Issue a deterministic, time-bucketed HMAC token bound to ``user``."""
    now = time.time() if now is None else now
    bucket = _bucket(now, ttl_seconds)
    message = f"{_TOKEN_VERSION}\0{bucket}\0{user}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"{_TOKEN_VERSION}.{bucket}.{digest}"


def validate_csrf_token(token: str, user: str, secret: str, *,
                        now: float | None = None,
                        ttl_seconds: int = 43200) -> bool:
    """Accept the current token bucket and the immediately previous bucket.

    The overlap prevents a long field form from failing exactly when its token
    rotates. At the default 12-hour bucket, a token remains valid for no more
    than 24 hours and is unusable for any other owner.
    """
    if not isinstance(token, str):
        return False
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _TOKEN_VERSION:
        return False
    try:
        supplied_bucket = int(parts[1])
    except ValueError:
        return False
    digest = parts[2]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        return False
    now = time.time() if now is None else now
    current = _bucket(now, ttl_seconds)
    if supplied_bucket not in {current, current - 1}:
        return False
    expected = issue_csrf_token(
        user, secret, now=supplied_bucket * ttl_seconds, ttl_seconds=ttl_seconds
    )
    return hmac.compare_digest(token, expected)


class SlidingWindowLimiter:
    """Small per-process sliding-window limiter.

    The institutional proxy should still provide fleet-wide abuse controls.
    This limiter protects a normal single-process Waitress deployment and
    prevents accidental retry storms from overwhelming expensive endpoints.
    """

    def __init__(self, window_seconds: int = 60):
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, *, now: float | None = None) -> tuple[bool, int]:
        now = time.monotonic() if now is None else now
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry = max(1, math.ceil(events[0] + self.window_seconds - now))
                return False, retry
            events.append(now)
            # Opportunistically remove empty keys left by other identities.
            if len(self._events) > 10000:
                for old_key in [k for k, q in self._events.items() if not q][:1000]:
                    self._events.pop(old_key, None)
            return True, 0
