"""Fixed-window rate limit backed by Redis, with intentional fail-open behavior."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import time

from app.cache.redis_cache import CacheBackend, CacheUnavailableError
from app.core.logging import log_event

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int
    cache_available: bool


class FixedWindowRateLimiter:
    def __init__(self, cache: CacheBackend | None, limit: int, window_seconds: int) -> None:
        self.cache = cache
        self.limit = limit
        self.window_seconds = window_seconds

    def check(self, scope: str, client_id: str) -> RateLimitDecision:
        if self.cache is None:
            return RateLimitDecision(True, 0, False)
        window = int(time() // self.window_seconds)
        key = f"rate:v1:{scope}:{client_id}:{window}"
        retry_after = self.window_seconds - int(time() % self.window_seconds)
        try:
            count = self.cache.increment(key, self.window_seconds)
        except CacheUnavailableError as error:
            log_event(
                LOGGER,
                "rate_limit_decision",
                scope=scope,
                client_id=client_id,
                allowed=True,
                cache_available=False,
                error_type=type(error).__name__,
            )
            return RateLimitDecision(True, 0, False)
        allowed = count <= self.limit
        log_event(
            LOGGER,
            "rate_limit_decision",
            scope=scope,
            client_id=client_id,
            allowed=allowed,
            count=count,
            limit=self.limit,
            cache_available=True,
        )
        return RateLimitDecision(allowed, retry_after if not allowed else 0, True)
