"""Small Redis boundary for disposable cache and rate-limit state."""

from __future__ import annotations

import json
import logging
from time import perf_counter
from typing import Protocol

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.logging import log_event

LOGGER = logging.getLogger(__name__)


class CacheUnavailableError(RuntimeError):
    pass


class CacheBackend(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str, ttl_seconds: int) -> None: ...

    def increment(self, key: str, ttl_seconds: int) -> int: ...


class RedisCache:
    def __init__(self, url: str, socket_timeout_seconds: float = 0.2) -> None:
        self.client = Redis.from_url(
            url, decode_responses=True, socket_timeout=socket_timeout_seconds
        )

    def _operation(self, operation: str, key: str, callback):  # type: ignore[no-untyped-def]
        started = perf_counter()
        try:
            value = callback()
            log_event(
                LOGGER,
                "redis_operation",
                operation=operation,
                key_prefix=key.split(":", 1)[0],
                success=True,
                latency_ms=round((perf_counter() - started) * 1_000, 2),
            )
            return value
        except RedisError as error:
            log_event(
                LOGGER,
                "redis_operation",
                operation=operation,
                key_prefix=key.split(":", 1)[0],
                success=False,
                latency_ms=round((perf_counter() - started) * 1_000, 2),
                error_type=type(error).__name__,
            )
            raise CacheUnavailableError("redis_unavailable") from error

    def get(self, key: str) -> str | None:
        return self._operation("get", key, lambda: self.client.get(key))

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._operation("set", key, lambda: self.client.set(key, value, ex=ttl_seconds))

    def increment(self, key: str, ttl_seconds: int) -> int:
        def increment() -> int:
            value = int(self.client.incr(key))
            if value == 1:
                self.client.expire(key, ttl_seconds)
            return value

        return self._operation("increment", key, increment)


def encode(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def decode(value: str) -> object:
    return json.loads(value)


def get_cache_backend() -> CacheBackend | None:
    settings = get_settings()
    if not settings.redis_url:
        return None
    return RedisCache(settings.redis_url, settings.redis_socket_timeout_seconds)
