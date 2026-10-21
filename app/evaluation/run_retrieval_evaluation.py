"""Run the bundled retrieval questions against the five sample documents without a Qdrant server."""

import json
from pathlib import Path

from qdrant_client import QdrantClient

from app.rag.embeddings import FastEmbedProvider
from app.rag.schemas import DocumentMetadata
from app.rag.service import SupportDocumentRetriever

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    retriever = SupportDocumentRetriever(
        client=QdrantClient(":memory:"),
        embedder=FastEmbedProvider("BAAI/bge-small-en-v1.5"),
    )
    for path in sorted((PROJECT_ROOT / "data" / "support_docs").glob("*.md")):
        metadata = DocumentMetadata.model_validate(
            json.loads(path.with_suffix(".json").read_text())
        )
        retriever.ingest_document(path, metadata)

    questions = json.loads(
        (PROJECT_ROOT / "data" / "evaluation" / "retrieval_questions.json").read_text()
    )
    passed = 0
    for item in questions:
        result = retriever.search_support_docs(item["query"], top_k=1)[0]
        success = result.document_id == item["expected_document_id"]
        passed += success
        print(
            f"{'PASS' if success else 'FAIL'} | {result.similarity_score:.4f} | "
            f"{item['query']} | {result.document_id}"
        )
    print(f"\nRank-1 accuracy: {passed}/{len(questions)}")


if __name__ == "__main__":
    main()
