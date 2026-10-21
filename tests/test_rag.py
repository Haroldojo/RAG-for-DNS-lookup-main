from app.rag.schemas import DocumentMetadata
from app.rag.text import chunk_text


def test_ingestion_preserves_required_metadata(retriever, sample_document) -> None:
    path, metadata = sample_document

    count = retriever.ingest_document(path, metadata)
    result = retriever.search_support_docs("What does DNS map?", top_k=1)

    assert count == 1
    assert result[0].document_id == "test-dns-v1"
    assert result[0].source == "tests/dns.md"
    assert result[0].chunk_index == 0


def test_retrieval_returns_most_relevant_chunk(retriever, sample_document) -> None:
    path, metadata = sample_document
    retriever.ingest_document(path, metadata)
    retriever.ingest_chunks(
        chunk_text(
            "An SSL certificate protects encrypted browser connections.",
            DocumentMetadata(
                document_id="test-ssl-v1",
                source="tests/ssl.md",
                category="ssl",
                topic="SSL",
            ),
            chunk_size=180,
            overlap=30,
        )
    )

    results = retriever.search_support_docs("DNS record TTL", top_k=2)

    assert results[0].document_id == "test-dns-v1"
    assert results[0].similarity_score > 0


def test_search_empty_collection_returns_empty_list(retriever) -> None:
    assert retriever.search_support_docs("DNS issue") == []


def test_search_rejects_blank_query(retriever) -> None:
    try:
        retriever.search_support_docs("   ")
    except ValueError as error:
        assert str(error) == "query must not be empty"
    else:
        raise AssertionError("Expected blank query to be rejected")
