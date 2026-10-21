from typing import Any

from pydantic import BaseModel, Field, field_validator


class DocumentMetadata(BaseModel):
    document_id: str
    source: str
    category: str
    topic: str
    version: str = "1.0"
    tenant_id: str = "public"
    document_scope: str = "public"
    access_level: str = "standard"


class TextChunk(BaseModel):
    text: str
    metadata: DocumentMetadata
    chunk_index: int

    def payload(self) -> dict[str, Any]:
        return {**self.metadata.model_dump(), "chunk_index": self.chunk_index, "text": self.text}


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1_000)
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def query_must_not_be_whitespace(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("query must not be empty")
        return normalized


class SearchResult(BaseModel):
    text: str
    similarity_score: float
    document_id: str
    source: str
    category: str
    topic: str
    version: str
    chunk_index: int
    tenant_id: str = "public"
    document_scope: str = "public"
    access_level: str = "standard"


class SearchResponse(BaseModel):
    query: str
    retrieved_chunks: list[SearchResult]
