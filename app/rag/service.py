import logging
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models

from app.cache.redis_cache import CacheBackend, CacheUnavailableError, decode, encode
from app.core.logging import log_event
from app.observability.runtime import event, metrics
from app.rag.embeddings import EmbeddingProvider
from app.rag.schemas import DocumentMetadata, SearchResult, TextChunk
from app.rag.text import chunk_text, read_document

LOGGER = logging.getLogger(__name__)


class SupportDocumentRetriever:
    """Minimal ingestion and retrieval service backed by one Qdrant collection."""

    def __init__(
        self,
        client: QdrantClient,
        embedder: EmbeddingProvider,
        collection_name: str = "support_docs",
        chunk_size: int = 800,
        chunk_overlap: int = 120,
        cache: CacheBackend | None = None,
        cache_ttl_seconds: int = 300,
        cache_version: str = "v1",
    ) -> None:
        self.client = client
        self.embedder = embedder
        self.collection_name = collection_name
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.cache = cache
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_version = cache_version

    def ensure_collection(self) -> None:
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.embedder.dimensions, distance=models.Distance.COSINE
                ),
            )

    def ingest_document(self, path: Path, metadata: DocumentMetadata) -> int:
        chunks = chunk_text(read_document(path), metadata, self.chunk_size, self.chunk_overlap)
        return self.ingest_chunks(chunks)

    def ingest_chunks(self, chunks: list[TextChunk]) -> int:
        self.ensure_collection()
        if not chunks:
            return 0
        vectors = self.embedder.embed([chunk.text for chunk in chunks])
        points = [
            models.PointStruct(
                id=str(uuid5(NAMESPACE_URL, f"{chunk.metadata.document_id}:{chunk.chunk_index}")),
                vector=vector,
                payload=chunk.payload(),
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self.client.upsert(collection_name=self.collection_name, points=points, wait=True)
        return len(points)

    def search_support_docs(
        self, query: str, top_k: int = 5, access_scope: str = "public"
    ) -> list[SearchResult]:
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        normalized_query = " ".join(query.lower().split())
        started = perf_counter()
        key_material = f"{self.cache_version}|{self.collection_name}|{self.embedder.__class__.__name__}|{self.chunk_size}|{self.chunk_overlap}|{access_scope}|{top_k}|{normalized_query}"
        cache_key = f"rag:{self.cache_version}:{sha256(key_material.encode()).hexdigest()}"
        if self.cache is not None:
            cache_started = perf_counter()
            try:
                cached = self.cache.get(cache_key)
                if cached:
                    log_event(
                        LOGGER,
                        "rag_cache",
                        cache_status="hit",
                        cache_latency_ms=round((perf_counter() - cache_started) * 1_000, 2),
                        ttl_seconds=self.cache_ttl_seconds,
                    )
                    event(
                        "rag",
                        "retrieve",
                        "ok",
                        query_hash=sha256(normalized_query.encode()).hexdigest()[:12],
                        top_k=top_k,
                        cache_status="hit",
                        document_ids=[str(item["document_id"]) for item in decode(cached)],
                        collection_version=self.cache_version,
                    )
                    metrics.increment("redis_cache_hits_total")
                    metrics.observe(
                        "retrieval_latency_ms", round((perf_counter() - started) * 1_000, 2)
                    )
                    return [SearchResult.model_validate(item) for item in decode(cached)]
                log_event(
                    LOGGER,
                    "rag_cache",
                    cache_status="miss",
                    cache_latency_ms=round((perf_counter() - cache_started) * 1_000, 2),
                    ttl_seconds=self.cache_ttl_seconds,
                )
                metrics.increment("redis_cache_misses_total")
            except (CacheUnavailableError, ValueError, TypeError) as error:
                log_event(
                    LOGGER,
                    "rag_cache",
                    cache_status="error",
                    cache_latency_ms=round((perf_counter() - cache_started) * 1_000, 2),
                    error_type=type(error).__name__,
                )
        if not self.client.collection_exists(self.collection_name):
            event(
                "rag",
                "retrieve",
                "ok",
                query_hash=sha256(normalized_query.encode()).hexdigest()[:12],
                top_k=top_k,
                cache_status="miss",
                document_ids=[],
                collection_version=self.cache_version,
                latency_ms=round((perf_counter() - started) * 1_000, 2),
            )
            return []
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=self.embedder.embed([query])[0],
            limit=top_k,
            with_payload=True,
            query_filter=models.Filter(
                should=[
                    models.FieldCondition(
                        key="document_scope", match=models.MatchAny(any=["public", access_scope])
                    )
                ]
            ),
        )
        results = [
            SearchResult(
                text=str(point.payload["text"]),
                similarity_score=point.score,
                document_id=str(point.payload["document_id"]),
                source=str(point.payload["source"]),
                category=str(point.payload["category"]),
                topic=str(point.payload["topic"]),
                version=str(point.payload["version"]),
                chunk_index=int(point.payload["chunk_index"]),
                tenant_id=str(point.payload.get("tenant_id", "public")),
                document_scope=str(point.payload.get("document_scope", "public")),
                access_level=str(point.payload.get("access_level", "standard")),
            )
            for point in response.points
        ]
        if self.cache is not None:
            try:
                self.cache.set(
                    cache_key,
                    encode([item.model_dump() for item in results]),
                    self.cache_ttl_seconds,
                )
            except CacheUnavailableError as error:
                log_event(
                    LOGGER, "rag_cache", cache_status="store_error", error_type=type(error).__name__
                )
        latency = round((perf_counter() - started) * 1_000, 2)
        event(
            "rag",
            "retrieve",
            "ok",
            query_hash=sha256(normalized_query.encode()).hexdigest()[:12],
            top_k=top_k,
            cache_status="miss",
            document_ids=[item.document_id for item in results],
            similarity_scores=[round(item.similarity_score, 4) for item in results],
            collection_version=self.cache_version,
            latency_ms=latency,
        )
        metrics.observe("retrieval_latency_ms", latency)
        return results
