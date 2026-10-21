from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

from app.api.routes import search as search_route
from app.main import create_app
from app.rag.embeddings import HashingEmbeddingProvider
from app.rag.schemas import DocumentMetadata
from app.rag.service import SupportDocumentRetriever
from app.rag.text import chunk_text


def test_search_endpoint_returns_empty_list_when_no_collection(monkeypatch) -> None:
    client = TestClient(create_app())
    monkeypatch.setattr(
        search_route,
        "get_retriever",
        lambda: SupportDocumentRetriever(QdrantClient(":memory:"), HashingEmbeddingProvider()),
    )

    response = client.post("/api/v1/search", json={"query": "DNS issue"})

    assert response.status_code == 200
    assert response.json()["retrieved_chunks"] == []


def test_search_endpoint_rejects_invalid_input() -> None:
    client = TestClient(create_app())

    response = client.post("/api/v1/search", json={"query": " "})

    assert response.status_code == 422


def test_search_endpoint_returns_metadata(monkeypatch) -> None:
    retriever = SupportDocumentRetriever(QdrantClient(":memory:"), HashingEmbeddingProvider())
    retriever.ingest_chunks(
        chunk_text(
            "A CNAME aliases www to another hostname.",
            DocumentMetadata(
                document_id="cname-v1",
                source="test/cname.md",
                category="dns",
                topic="CNAME",
            ),
            chunk_size=180,
            overlap=30,
        )
    )
    monkeypatch.setattr(search_route, "get_retriever", lambda: retriever)
    client = TestClient(create_app())

    response = client.post("/api/v1/search", json={"query": "www alias", "top_k": 1})

    assert response.status_code == 200
    assert response.json()["retrieved_chunks"][0]["topic"] == "CNAME"
