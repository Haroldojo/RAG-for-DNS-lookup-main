from time import monotonic

from qdrant_client import QdrantClient

from app.cache.rate_limit import FixedWindowRateLimiter
from app.cache.redis_cache import CacheUnavailableError
from app.rag.embeddings import HashingEmbeddingProvider
from app.rag.schemas import DocumentMetadata
from app.rag.service import SupportDocumentRetriever
from app.rag.text import chunk_text
from app.tools.dns import DnsDiagnosticService
from tests.test_diagnosis_graph import FakeRetriever, workflow
from tests.test_dns import FakeAnswer, FakeResolver


class MemoryCache:
    def __init__(self) -> None:
        self.items: dict[str, tuple[str, float]] = {}
        self.get_calls = 0

    def get(self, key: str) -> str | None:
        self.get_calls += 1
        item = self.items.get(key)
        if item is None or item[1] <= monotonic():
            self.items.pop(key, None)
            return None
        return item[0]

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self.items[key] = (value, monotonic() + ttl_seconds)

    def increment(self, key: str, ttl_seconds: int) -> int:
        current = self.get(key)
        count = int(current or "0") + 1
        self.set(key, str(count), ttl_seconds)
        return count


class BrokenCache(MemoryCache):
    def get(self, key: str) -> str | None:
        raise CacheUnavailableError("redis_unavailable")


def test_dns_cache_miss_then_hit() -> None:
    cache = MemoryCache()
    resolver = FakeResolver({"A": FakeAnswer(["203.0.113.10"])})
    service = DnsDiagnosticService(resolver, cache=cache, cache_ttl_seconds=30)

    first = service.check_dns("example.com")
    second = service.check_dns("example.com")

    assert first.cache_status == "miss"
    assert second.cache_status == "hit"
    assert second.dns_tool_latency_ms == 0.0


def test_dns_cache_ttl_expiration_causes_new_lookup() -> None:
    cache = MemoryCache()
    service = DnsDiagnosticService(
        FakeResolver({"A": FakeAnswer(["203.0.113.10"])}), cache=cache, cache_ttl_seconds=0
    )

    assert service.check_dns("example.com").cache_status == "miss"
    assert service.check_dns("example.com").cache_status == "miss"


def test_malformed_dns_cache_is_bypassed() -> None:
    cache = MemoryCache()
    cache.set("dns:v1:example.com:all", "not-json", 30)
    result = DnsDiagnosticService(
        FakeResolver({"A": FakeAnswer(["203.0.113.10"])}), cache=cache
    ).check_dns("example.com")

    assert result.cache_status == "miss"
    assert result.records["A"].status == "ok"


def test_redis_unavailable_and_timeout_bypass_dns_cache() -> None:
    service = DnsDiagnosticService(
        FakeResolver({"A": FakeAnswer(["203.0.113.10"])}), cache=BrokenCache()
    )

    result = service.check_dns("example.com")

    assert result.records["A"].status == "ok"
    assert result.cache_status == "miss"


def test_retrieval_cache_key_includes_version() -> None:
    cache = MemoryCache()
    client = QdrantClient(":memory:")
    base = SupportDocumentRetriever(
        client, HashingEmbeddingProvider(), cache=cache, cache_version="v1"
    )
    base.ingest_chunks(
        chunk_text(
            "CNAME aliases a hostname.",
            DocumentMetadata(document_id="doc-v1", source="doc.md", category="dns", topic="CNAME"),
            180,
            30,
        )
    )
    assert base.search_support_docs("hostname alias")
    cached_calls = cache.get_calls
    assert base.search_support_docs("hostname alias")
    version_two = SupportDocumentRetriever(
        client, HashingEmbeddingProvider(), cache=cache, cache_version="v2"
    )
    assert version_two.search_support_docs("hostname alias")
    assert cache.get_calls == cached_calls + 2


def test_fixed_window_rate_limit_and_separate_clients() -> None:
    limiter = FixedWindowRateLimiter(MemoryCache(), limit=2, window_seconds=60)

    assert limiter.check("diagnose", "client-a").allowed
    assert limiter.check("diagnose", "client-a").allowed
    denied = limiter.check("diagnose", "client-a")
    assert not denied.allowed and denied.retry_after_seconds > 0
    assert limiter.check("diagnose", "client-b").allowed


def test_rate_limiter_fails_open_when_redis_is_unavailable() -> None:
    decision = FixedWindowRateLimiter(BrokenCache(), limit=1, window_seconds=60).check(
        "diagnose", "client-a"
    )

    assert decision.allowed and not decision.cache_available


def test_diagnosis_continues_when_dns_cache_is_unavailable() -> None:
    dns = DnsDiagnosticService(
        FakeResolver({"A": FakeAnswer(["203.0.113.10"])}), cache=BrokenCache()
    )

    result = workflow(retriever=FakeRetriever(), dns=dns).invoke(
        "My domain is not resolving", "example.com"
    )

    assert result["dns_result"]["status"] == "ok"
    assert result["route_taken"] == "dns_and_rag"
