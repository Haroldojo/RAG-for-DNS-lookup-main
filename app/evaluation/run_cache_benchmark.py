"""Local cache-aside benchmark; it uses a deterministic in-process test cache."""

from __future__ import annotations

from time import monotonic, perf_counter, sleep
from typing import ClassVar

from app.tools.dns import DnsDiagnosticService


class LocalCache:
    def __init__(self) -> None:
        self.values: dict[str, tuple[str, float]] = {}

    def get(self, key: str) -> str | None:
        value = self.values.get(key)
        if value is None or value[1] <= monotonic():
            self.values.pop(key, None)
            return None
        return value[0]

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self.values[key] = (value, monotonic() + ttl_seconds)

    def increment(self, key: str, ttl_seconds: int) -> int:
        value = int(self.get(key) or "0") + 1
        self.set(key, str(value), ttl_seconds)
        return value


class Answer:
    rrset: ClassVar[list[str]] = ["203.0.113.10"]

    def __iter__(self):
        return iter(self.rrset)


class DelayedResolver:
    def resolve(self, qname: str, rdtype: str, **kwargs: object) -> Answer:
        sleep(0.005)
        return Answer()


def elapsed_ms(callback) -> float:  # type: ignore[no-untyped-def]
    started = perf_counter()
    callback()
    return round((perf_counter() - started) * 1_000, 2)


def main() -> None:
    uncached = DnsDiagnosticService(DelayedResolver())
    cached = DnsDiagnosticService(DelayedResolver(), cache=LocalCache(), cache_ttl_seconds=60)
    uncached_ms = elapsed_ms(lambda: uncached.check_dns("example.com"))
    miss_ms = elapsed_ms(lambda: cached.check_dns("example.com"))
    hit_ms = elapsed_ms(lambda: cached.check_dns("example.com"))
    print(
        {
            "benchmark": "local deterministic cache-aside (in-memory cache, not a Redis server)",
            "uncached_dns_ms": uncached_ms,
            "cache_miss_dns_ms": miss_ms,
            "cache_hit_dns_ms": hit_ms,
        }
    )


if __name__ == "__main__":
    main()
