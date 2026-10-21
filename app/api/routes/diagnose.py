from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status

from app.agents.diagnosis_graph import DiagnoseRequest, DiagnoseResponse, DiagnosisWorkflow
from app.cache.rate_limit import FixedWindowRateLimiter
from app.cache.redis_cache import get_cache_backend
from app.core.config import get_settings
from app.database.service import ConversationNotFoundError, PersistenceError
from app.database.session import get_persistence_service
from app.llm.groq_provider import GroqProvider
from app.llm.service import GroundedAnswerService
from app.observability.runtime import begin_request, event, metrics
from app.rag.bootstrap import get_retriever
from app.tools.dns import DnsDiagnosticService
from app.tools.http import HttpDiagnosticService
from app.tools.ssl import SslDiagnosticService

router = APIRouter(prefix="/api/v1", tags=["orchestration"])


def get_workflow() -> DiagnosisWorkflow:
    settings = get_settings()
    provider = GroqProvider(
        api_key=settings.groq_api_key,
        model_name=settings.groq_model,
        timeout_seconds=settings.groq_timeout_seconds,
        temperature=settings.groq_temperature,
        max_tokens=settings.groq_max_tokens,
    )
    return DiagnosisWorkflow(
        get_retriever(),
        DnsDiagnosticService(
            cache=get_cache_backend(), cache_ttl_seconds=settings.dns_cache_ttl_seconds
        ),
        GroundedAnswerService(provider),
        SslDiagnosticService(
            cache=get_cache_backend(),
            cache_ttl_seconds=settings.ssl_cache_ttl_seconds,
            timeout_seconds=settings.ssl_timeout_seconds,
        ),
        HttpDiagnosticService(
            cache=get_cache_backend(),
            cache_ttl_seconds=settings.http_cache_ttl_seconds,
            timeout_seconds=settings.http_timeout_seconds,
            max_redirects=settings.http_max_redirects,
        ),
        human_review_confidence_threshold=settings.human_review_confidence_threshold,
    )


def get_rate_limiter() -> FixedWindowRateLimiter:
    settings = get_settings()
    return FixedWindowRateLimiter(
        get_cache_backend(), settings.rate_limit_requests, settings.rate_limit_window_seconds
    )


@router.post(
    "/diagnose",
    response_model=DiagnoseResponse,
    summary="Inspect the deterministic RAG/DNS workflow",
)
async def diagnose(request: DiagnoseRequest, http_request: Request) -> DiagnoseResponse:
    request_id = str(uuid4())
    trace = begin_request(request_id, conversation_id=request.conversation_id)
    metrics.increment("diagnosis_requests_total")
    event("api", "diagnose", "started", conversation_id=request.conversation_id)
    client_id = http_request.client.host if http_request.client else "unknown"
    decision = get_rate_limiter().check("diagnose", client_id)
    if not decision.allowed:
        metrics.increment("rate_limit_rejections_total")
        event("api", "diagnose", "rejected", error_type="rate_limit_exceeded")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "rate_limit_exceeded",
                "retry_after_seconds": decision.retry_after_seconds,
            },
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )
    try:
        persistence = get_persistence_service()
        if request.conversation_id:
            persistence.require_conversation(request.conversation_id, request_id=request_id)
    except ConversationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "conversation_not_found", "request_id": request_id},
        ) from error
    except PersistenceError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "persistence_unavailable", "request_id": request_id},
        ) from error

    state = get_workflow().invoke(
        request.query, request.domain, request_id=request_id, trace_id=trace["trace_id"]
    )
    try:
        stored = persistence.persist_diagnosis(
            request_id=request_id,
            query=request.query,
            state=state,
            conversation_id=request.conversation_id,
        )
    except ConversationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "conversation_not_found", "request_id": request_id},
        ) from error
    except PersistenceError as error:
        # A completed diagnosis is not represented as durable history when its transaction fails.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "persistence_failed", "request_id": request_id},
        ) from error
    metrics.increment("diagnoses_successful_total")
    if state["needs_escalation"]:
        metrics.increment("diagnoses_escalated_total")
    event(
        "api",
        "diagnose",
        "ok",
        conversation_id=stored.conversation_id,
        diagnosis_run_id=stored.diagnosis_run_id,
        escalated=state["needs_escalation"],
    )
    return DiagnoseResponse(
        conversation_id=stored.conversation_id,
        diagnosis_run_id=stored.diagnosis_run_id,
        query=state["user_query"],
        domain=state.get("domain"),
        intent=state["intent"],
        route_taken=state["route_taken"],
        tools_called=state["tools_called"],
        tools_skipped=state["tools_skipped"],
        tool_statuses=state["tool_statuses"],
        answer=state["answer"],
        citations=state["citations"],
        retrieved_chunks=state["retrieved_chunks"],
        dns_result=state.get("dns_result"),
        ssl_result=state.get("ssl_result"),
        http_result=state.get("http_result"),
        answer_context=state["answer_context"],
        confidence=state["confidence"],
        needs_escalation=state["needs_escalation"],
        needs_human_review=state["needs_human_review"],
        escalation_reason=state.get("escalation_reason"),
        escalation_status=state["escalation_status"],
        review_notes=state.get("review_notes"),
        reviewed_at=state.get("reviewed_at"),
        request_id=request_id,
        trace_id=state["trace_id"],
        node_timings_ms=state["node_timings_ms"],
        errors=state["errors"],
        generation_metadata=state["generation_metadata"],
    )
