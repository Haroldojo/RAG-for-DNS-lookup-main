import json
from pathlib import Path

from qdrant_client import QdrantClient

from app.cache.redis_cache import get_cache_backend
from app.core.config import Settings, get_settings
from app.rag.embeddings import FastEmbedProvider
from app.rag.schemas import DocumentMetadata
from app.rag.service import SupportDocumentRetriever

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def get_retriever(settings: Settings | None = None) -> SupportDocumentRetriever:
    settings = settings or get_settings()
    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    return SupportDocumentRetriever(
        client=client,
        embedder=FastEmbedProvider(settings.rag_embedding_model),
        collection_name=settings.rag_collection_name,
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
        cache=get_cache_backend(),
        cache_ttl_seconds=settings.rag_cache_ttl_seconds,
        cache_version=settings.rag_cache_version,
    )


def ingest_directory(directory: Path | None = None) -> dict[str, int]:
    directory = directory or PROJECT_ROOT / "data" / "support_docs"
    retriever = get_retriever()
    total = 0
    for path in sorted(directory.glob("*")):
        if path.suffix.lower() not in {".md", ".txt", ".pdf"}:
            continue
        sidecar = path.with_suffix(".json")
        if not sidecar.exists():
            continue
        metadata = DocumentMetadata.model_validate(json.loads(sidecar.read_text(encoding="utf-8")))
        total += retriever.ingest_document(path, metadata)
    return {"collection": retriever.collection_name, "chunks_ingested": total}


if __name__ == "__main__":
    print(ingest_directory())
