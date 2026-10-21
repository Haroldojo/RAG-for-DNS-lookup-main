from app.rag.schemas import DocumentMetadata
from app.rag.text import chunk_text, clean_text


def test_clean_text_normalizes_whitespace() -> None:
    assert clean_text(" One\t two\r\n\r\n\r\nThree ") == "One two\n\nThree"


def test_chunking_preserves_metadata_and_overlap() -> None:
    chunks = chunk_text(
        "alpha " * 100,
        DocumentMetadata(document_id="doc", source="test", category="dns", topic="test"),
        chunk_size=180,
        overlap=30,
    )

    assert len(chunks) > 1
    assert chunks[0].metadata.document_id == "doc"
    assert chunks[1].chunk_index == 1
