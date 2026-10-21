"""Internal simulated human-review API. It performs no external action."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.database.models import Escalation
from app.database.service import EscalationNotFoundError, PersistenceError
from app.database.session import get_persistence_service

router = APIRouter(prefix="/api/v1/escalations", tags=["human-review"])


class EscalationResponse(BaseModel):
    id: str
    diagnosis_run_id: str
    reason: str
    status: str
    created_at: datetime
    reviewed_at: datetime | None
    review_decision: str | None
    review_notes: str | None


class ReviewRequest(BaseModel):
    decision: Literal["approved", "rejected", "resolved"]
    notes: str | None = Field(default=None, max_length=1_000)


def response(item: Escalation) -> EscalationResponse:
    return EscalationResponse(
        id=item.id,
        diagnosis_run_id=item.diagnosis_run_id,
        reason=item.reason,
        status=item.status,
        created_at=item.created_at,
        reviewed_at=item.reviewed_at,
        review_decision=item.review_decision,
        review_notes=item.review_notes,
    )


def database_error(error: Exception) -> HTTPException:
    if isinstance(error, EscalationNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": "escalation_not_found"}
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "persistence_unavailable"}
    )


@router.get("", response_model=list[EscalationResponse])
async def list_escalations() -> list[EscalationResponse]:
    try:
        return [response(item) for item in get_persistence_service().list_escalations()]
    except PersistenceError as error:
        raise database_error(error) from error


@router.get("/{escalation_id}", response_model=EscalationResponse)
async def get_escalation(escalation_id: str) -> EscalationResponse:
    try:
        item = get_persistence_service().get_escalation(escalation_id)
    except PersistenceError as error:
        raise database_error(error) from error
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": "escalation_not_found"}
        )
    return response(item)


@router.post("/{escalation_id}/review", response_model=EscalationResponse)
async def review_escalation(escalation_id: str, request: ReviewRequest) -> EscalationResponse:
    try:
        return response(
            get_persistence_service().review_escalation(
                escalation_id, request.decision, request.notes
            )
        )
    except (PersistenceError, ValueError) as error:
        if isinstance(error, ValueError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "invalid_review"}
            ) from error
        raise database_error(error) from error
