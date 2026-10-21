from fastapi import APIRouter, HTTPException

from app.rag.bootstrap import get_retriever
from app.rag.schemas import SearchRequest, SearchResponse
from app.rag.service import SupportDocumentRetriever

router = APIRouter(prefix="/api/v1", tags=["retrieval"])


@router.post("/search", response_model=SearchResponse, summary="Search support-document chunks")
async def search_support_docs(
    request: SearchRequest,
) -> SearchResponse:
    try:
        # Construct only after request validation, so invalid input does not touch Qdrant.
        retriever: SupportDocumentRetriever = get_retriever()
        results = retriever.search_support_docs(request.query, request.top_k)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return SearchResponse(query=request.query, retrieved_chunks=results)
