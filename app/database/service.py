"""Repository/service boundary for durable, transactionally consistent diagnosis history."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import log_event
from app.database.models import (
    Citation,
    Conversation,
    DiagnosisRun,
    Escalation,
    Message,
    ToolRun,
    utcnow,
)
from app.security.controls import redact_sensitive


class PersistenceError(RuntimeError):
    """A database write or read could not be completed safely."""


class ConversationNotFoundError(PersistenceError):
    pass


class EscalationNotFoundError(PersistenceError):
    pass


@dataclass(frozen=True)
class PersistedDiagnosis:
    conversation_id: str
    diagnosis_run_id: str


class PersistenceService:
    """Stores summaries after the graph completes; graph nodes do not depend on this class."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def _log(self, operation: str, started: float, success: bool, **fields: object) -> None:
        import logging

        log_event(
            logging.getLogger(__name__),
            "database_operation",
            operation=operation,
            duration_ms=round((perf_counter() - started) * 1000, 2),
            success=success,
            **fields,
        )

    def create_conversation(self, request_id: str | None = None) -> Conversation:
        started = perf_counter()
        try:
            with self.session_factory() as session, session.begin():
                conversation = Conversation()
                session.add(conversation)
                session.flush()
                session.expunge(conversation)
            self._log(
                "create_conversation",
                started,
                True,
                request_id=request_id,
                conversation_id=conversation.id,
            )
            return conversation
        except SQLAlchemyError as error:
            self._log(
                "create_conversation",
                started,
                False,
                request_id=request_id,
                error_type=type(error).__name__,
            )
            raise PersistenceError("database_unavailable") from error

    def require_conversation(
        self, conversation_id: str, request_id: str | None = None
    ) -> Conversation:
        started = perf_counter()
        try:
            with self.session_factory() as session:
                conversation = session.get(Conversation, conversation_id)
                if conversation is None:
                    self._log(
                        "get_conversation",
                        started,
                        False,
                        request_id=request_id,
                        conversation_id=conversation_id,
                        error_type="not_found",
                    )
                    raise ConversationNotFoundError("conversation_not_found")
                session.expunge(conversation)
            self._log(
                "get_conversation",
                started,
                True,
                request_id=request_id,
                conversation_id=conversation_id,
            )
            return conversation
        except ConversationNotFoundError:
            raise
        except SQLAlchemyError as error:
            self._log(
                "get_conversation",
                started,
                False,
                request_id=request_id,
                conversation_id=conversation_id,
                error_type=type(error).__name__,
            )
            raise PersistenceError("database_unavailable") from error

    def persist_diagnosis(
        self, *, request_id: str, query: str, state: dict, conversation_id: str | None
    ) -> PersistedDiagnosis:
        """Write one entire interaction atomically; only compact tool summaries are retained."""
        started = perf_counter()
        try:
            with self.session_factory() as session, session.begin():
                conversation = (
                    session.get(Conversation, conversation_id)
                    if conversation_id
                    else Conversation()
                )
                if conversation is None:
                    raise ConversationNotFoundError("conversation_not_found")
                if conversation_id is None:
                    session.add(conversation)
                    session.flush()
                run = DiagnosisRun(
                    conversation_id=conversation.id,
                    request_id=request_id,
                    intent=state["intent"],
                    route_taken=state["route_taken"],
                    confidence=state["confidence"],
                    needs_escalation=state["needs_escalation"],
                )
                session.add_all(
                    [
                        Message(
                            conversation_id=conversation.id,
                            role="user",
                            content=redact_sensitive(query),
                        ),
                        run,
                    ]
                )
                session.flush()
                session.add(
                    Message(
                        conversation_id=conversation.id,
                        role="assistant",
                        content=redact_sensitive(state["answer"]),
                    )
                )
                self._add_tool_summaries(session, run.id, state, query)
                for citation in state.get("citations", []):
                    session.add(
                        Citation(
                            diagnosis_run_id=run.id,
                            document_id=str(citation.get("document_id", "unknown")),
                            source=str(citation.get("source", "unknown")),
                            topic=str(citation.get("topic", "unknown")),
                            version=str(citation.get("version", "unknown")),
                        )
                    )
                if state.get("needs_escalation"):
                    reason = (
                        state.get("escalation_reason")
                        or "; ".join(state.get("errors", []))
                        or f"{state['intent']} requires follow-up"
                    )
                    session.add(
                        Escalation(diagnosis_run_id=run.id, reason=reason[:500], status="pending")
                    )
                conversation.updated_at = utcnow()
                session.flush()
                result = PersistedDiagnosis(conversation.id, run.id)
            self._log(
                "persist_diagnosis",
                started,
                True,
                request_id=request_id,
                conversation_id=result.conversation_id,
                diagnosis_run_id=result.diagnosis_run_id,
            )
            return result
        except ConversationNotFoundError:
            self._log(
                "persist_diagnosis",
                started,
                False,
                request_id=request_id,
                conversation_id=conversation_id,
                error_type="not_found",
            )
            raise
        except SQLAlchemyError as error:
            self._log(
                "persist_diagnosis",
                started,
                False,
                request_id=request_id,
                conversation_id=conversation_id,
                error_type=type(error).__name__,
            )
            raise PersistenceError("persistence_failed") from error

    @staticmethod
    def _add_tool_summaries(session: Session, run_id: str, state: dict, query: str) -> None:
        errors = " ".join(state.get("errors", []))
        chunks = state.get("retrieved_chunks", [])
        if "search_support_docs" in state.get("tools_called", []):
            session.add(
                ToolRun(
                    diagnosis_run_id=run_id,
                    tool_name="search_support_docs",
                    status="failed" if "retrieval_failed" in errors else "ok",
                    input_summary={"query_length": len(query)},
                    output_summary={
                        "result_count": len(chunks),
                        "document_ids": [
                            str(item.get("document_id", "unknown")) for item in chunks
                        ],
                    },
                    latency_ms=None,
                    error_type="retrieval_failed" if "retrieval_failed" in errors else None,
                )
            )
        dns = state.get("dns_result") or {}
        if "dns_check" in state.get("tools_called", []):
            session.add(
                ToolRun(
                    diagnosis_run_id=run_id,
                    tool_name="dns_check",
                    status=str(dns.get("status", "failed")),
                    input_summary={"domain": state.get("domain")},
                    output_summary={
                        "record_types": sorted((dns.get("records") or {}).keys()),
                        "error_count": len(dns.get("errors") or []),
                    },
                    latency_ms=dns.get("lookup_duration_ms"),
                    error_type="dns_check_failed" if "dns_check_failed" in errors else None,
                )
            )
        ssl = state.get("ssl_result") or {}
        if "ssl_check" in state.get("tools_called", []):
            session.add(
                ToolRun(
                    diagnosis_run_id=run_id,
                    tool_name="ssl_check",
                    status=str(ssl.get("connection_status", "failed")),
                    input_summary={"domain": state.get("domain")},
                    output_summary={
                        "certificate_valid": ssl.get("certificate_valid"),
                        "hostname_match": ssl.get("hostname_match"),
                        "days_until_expiry": ssl.get("days_until_expiry"),
                    },
                    latency_ms=ssl.get("latency_ms"),
                    error_type=ssl.get("error_type"),
                )
            )
        http = state.get("http_result") or {}
        if "http_check" in state.get("tools_called", []):
            session.add(
                ToolRun(
                    diagnosis_run_id=run_id,
                    tool_name="http_check",
                    status="failed" if http.get("error_type") else "success",
                    input_summary={"requested_url": http.get("requested_url")},
                    output_summary={
                        "final_url": http.get("final_url"),
                        "status_code": http.get("status_code"),
                        "redirect_count": http.get("redirect_count"),
                        "content_type": http.get("content_type"),
                    },
                    latency_ms=http.get("response_latency_ms"),
                    error_type=http.get("error_type"),
                )
            )
        metadata = state.get("generation_metadata", {})
        session.add(
            ToolRun(
                diagnosis_run_id=run_id,
                tool_name="generate_answer",
                status=str(metadata.get("status", "unknown")),
                input_summary={"context_chunk_count": len(chunks)},
                output_summary={
                    "model": metadata.get("model"),
                    "token_usage": metadata.get("token_usage"),
                },
                latency_ms=metadata.get("latency_ms"),
                error_type=metadata.get("provider_error_type"),
            )
        )

    def get_messages(self, conversation_id: str) -> list[Message]:
        self.require_conversation(conversation_id)
        with self.session_factory() as session:
            messages = list(
                session.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at, Message.id)
                )
            )
            for message in messages:
                session.expunge(message)
            return messages

    def get_diagnosis(self, diagnosis_run_id: str) -> DiagnosisRun | None:
        with self.session_factory() as session:
            run = session.get(DiagnosisRun, diagnosis_run_id)
            if run is None:
                return None
            _ = run.tool_runs, run.citations, run.escalations
            session.expunge(run)
            return run

    def list_escalations(self) -> list[Escalation]:
        try:
            with self.session_factory() as session:
                items = list(
                    session.scalars(select(Escalation).order_by(Escalation.created_at.desc()))
                )
                for item in items:
                    session.expunge(item)
                return items
        except SQLAlchemyError as error:
            raise PersistenceError("database_unavailable") from error

    def get_escalation(self, escalation_id: str) -> Escalation | None:
        try:
            with self.session_factory() as session:
                item = session.get(Escalation, escalation_id)
                if item is not None:
                    session.expunge(item)
                return item
        except SQLAlchemyError as error:
            raise PersistenceError("database_unavailable") from error

    def review_escalation(self, escalation_id: str, decision: str, notes: str | None) -> Escalation:
        if decision not in {"approved", "rejected", "resolved"}:
            raise ValueError("decision must be approved, rejected, or resolved")
        started = perf_counter()
        try:
            with self.session_factory() as session, session.begin():
                item = session.get(Escalation, escalation_id)
                if item is None:
                    raise EscalationNotFoundError("escalation_not_found")
                if item.status != "pending":
                    raise ValueError("only pending escalations can be reviewed")
                item.status = decision
                item.review_decision = decision
                item.review_notes = notes
                item.reviewed_at = utcnow()
                session.flush()
                session.expunge(item)
            self._log(
                "review_escalation", started, True, escalation_id=escalation_id, decision=decision
            )
            return item
        except EscalationNotFoundError:
            self._log(
                "review_escalation",
                started,
                False,
                escalation_id=escalation_id,
                error_type="not_found",
            )
            raise
        except ValueError:
            raise
        except SQLAlchemyError as error:
            self._log(
                "review_escalation",
                started,
                False,
                escalation_id=escalation_id,
                error_type=type(error).__name__,
            )
            raise PersistenceError("review_persistence_failed") from error
