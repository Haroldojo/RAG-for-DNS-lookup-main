"""Read-oriented API for durable conversation and diagnosis history."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.database.models import Conversation, DiagnosisRun, Message
from app.database.service import ConversationNotFoundError, PersistenceError
from app.database.session import get_persistence_service

router = APIRouter(prefix="/api/v1", tags=["conversations"])


class ConversationResponse(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    status: str


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    created_at: datetime


class ToolRunResponse(BaseModel):
    id: str
    tool_name: str
    status: str
    input_summary: dict
    output_summary: dict
    latency_ms: int | None
    error_type: str | None
    created_at: datetime


class CitationResponse(BaseModel):
    id: str
    document_id: str
    source: str
    topic: str
    version: str


class EscalationResponse(BaseModel):
    id: str
    reason: str
    status: str
    created_at: datetime
    reviewed_at: datetime | None
    review_decision: str | None
    review_notes: str | None


class DiagnosisRunResponse(BaseModel):
    id: str
    conversation_id: str
    request_id: str
    intent: str
    route_taken: str
    confidence: float
    needs_escalation: bool
    created_at: datetime
    tool_runs: list[ToolRunResponse]
    citations: list[CitationResponse]
    escalations: list[EscalationResponse]


def conversation_response(item: Conversation) -> ConversationResponse:
    return ConversationResponse(
        id=item.id, created_at=item.created_at, updated_at=item.updated_at, status=item.status
    )


def message_response(item: Message) -> MessageResponse:
    return MessageResponse(
        id=item.id,
        conversation_id=item.conversation_id,
        role=item.role,
        content=item.content,
        created_at=item.created_at,
    )


def diagnosis_response(item: DiagnosisRun) -> DiagnosisRunResponse:
    return DiagnosisRunResponse(
        id=item.id,
        conversation_id=item.conversation_id,
        request_id=item.request_id,
        intent=item.intent,
        route_taken=item.route_taken,
        confidence=item.confidence,
        needs_escalation=item.needs_escalation,
        created_at=item.created_at,
        tool_runs=[
            ToolRunResponse(
                id=x.id,
                tool_name=x.tool_name,
                status=x.status,
                input_summary=x.input_summary,
                output_summary=x.output_summary,
                latency_ms=x.latency_ms,
                error_type=x.error_type,
                created_at=x.created_at,
            )
            for x in item.tool_runs
        ],
        citations=[
            CitationResponse(
                id=x.id,
                document_id=x.document_id,
                source=x.source,
                topic=x.topic,
                version=x.version,
            )
            for x in item.citations
        ],
        escalations=[
            EscalationResponse(
                id=x.id,
                reason=x.reason,
                status=x.status,
                created_at=x.created_at,
                reviewed_at=x.reviewed_at,
                review_decision=x.review_decision,
                review_notes=x.review_notes,
            )
            for x in item.escalations
        ],
    )


def persistence_http_error(error: Exception) -> HTTPException:
    if isinstance(error, ConversationNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": "conversation_not_found"}
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "persistence_unavailable"}
    )


@router.post(
    "/conversations", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED
)
async def create_conversation() -> ConversationResponse:
    try:
        return conversation_response(get_persistence_service().create_conversation())
    except PersistenceError as error:
        raise persistence_http_error(error) from error


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(conversation_id: str) -> ConversationResponse:
    try:
        return conversation_response(
            get_persistence_service().require_conversation(conversation_id)
        )
    except PersistenceError as error:
        raise persistence_http_error(error) from error


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageResponse])
async def get_messages(conversation_id: str) -> list[MessageResponse]:
    try:
        return [
            message_response(item)
            for item in get_persistence_service().get_messages(conversation_id)
        ]
    except PersistenceError as error:
        raise persistence_http_error(error) from error


@router.get("/diagnosis/{diagnosis_run_id}", response_model=DiagnosisRunResponse)
async def get_diagnosis(diagnosis_run_id: str) -> DiagnosisRunResponse:
    try:
        diagnosis = get_persistence_service().get_diagnosis(diagnosis_run_id)
    except PersistenceError as error:
        raise persistence_http_error(error) from error
    if diagnosis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": "diagnosis_run_not_found"}
        )
    return diagnosis_response(diagnosis)
