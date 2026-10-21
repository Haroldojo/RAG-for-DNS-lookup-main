from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from app.rag.embeddings import HashingEmbeddingProvider
from app.rag.schemas import DocumentMetadata
from app.rag.service import SupportDocumentRetriever


@pytest.fixture
def retriever() -> SupportDocumentRetriever:
    return SupportDocumentRetriever(
        client=QdrantClient(":memory:"),
        embedder=HashingEmbeddingProvider(),
        chunk_size=180,
        chunk_overlap=30,
    )


@pytest.fixture
def sample_document(tmp_path: Path) -> tuple[Path, DocumentMetadata]:
    document = tmp_path / "dns.md"
    document.write_text(
        "DNS maps domain names to IP addresses. DNS cache and TTL can delay a new record.",
        encoding="utf-8",
    )
    return document, DocumentMetadata(
        document_id="test-dns-v1",
        source="tests/dns.md",
        category="dns",
        topic="DNS basics",
        version="1.0",
    )
