from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.models import Base, Conversation, DiagnosisRun, Message
from app.database.service import ConversationNotFoundError, PersistenceError, PersistenceService


def make_service() -> tuple[PersistenceService, sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    return PersistenceService(factory), factory


def sample_state(escalate: bool = False) -> dict:
    return {
        "intent": "dns_issue",
        "route_taken": "dns_and_rag",
        "confidence": 0.85,
        "needs_escalation": escalate,
        "domain": "example.com",
        "tools_called": ["dns_check", "search_support_docs"],
        "dns_result": {
            "status": "ok",
            "records": {"A": {"records": ["203.0.113.10"]}},
            "errors": [],
            "lookup_duration_ms": 5,
        },
        "retrieved_chunks": [
            {
                "document_id": "dns-basics-v1",
                "source": "dns-basics.md",
                "topic": "DNS basics",
                "version": "1.0",
            }
        ],
        "citations": [
            {
                "document_id": "dns-basics-v1",
                "source": "dns-basics.md",
                "topic": "DNS basics",
                "version": "1.0",
            }
        ],
        "answer": "Use the observed DNS evidence.",
        "errors": ["dns_check_failed: timeout"] if escalate else [],
        "generation_metadata": {
            "model_name": "fake",
            "status": "ok",
            "generation_latency_ms": 3,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "provider_error_type": None,
        },
    }


def test_creates_conversation_and_persists_complete_diagnosis() -> None:
    service, _ = make_service()
    stored = service.persist_diagnosis(
        request_id="req-1",
        query="My domain is not resolving",
        state=sample_state(),
        conversation_id=None,
    )
    conversation = service.require_conversation(stored.conversation_id)
    messages = service.get_messages(stored.conversation_id)
    diagnosis = service.get_diagnosis(stored.diagnosis_run_id)

    assert conversation.status == "open"
    assert [message.role for message in messages] == ["user", "assistant"]
    assert diagnosis is not None
    assert {tool.tool_name for tool in diagnosis.tool_runs} == {
        "dns_check",
        "search_support_docs",
        "generate_answer",
    }
    assert diagnosis.citations[0].document_id == "dns-basics-v1"


def test_escalation_and_existing_conversation_are_persisted() -> None:
    service, _ = make_service()
    conversation = service.create_conversation("req-create")
    stored = service.persist_diagnosis(
        request_id="req-2",
        query="Still failing",
        state=sample_state(escalate=True),
        conversation_id=conversation.id,
    )
    diagnosis = service.get_diagnosis(stored.diagnosis_run_id)

    assert stored.conversation_id == conversation.id
    assert diagnosis is not None and diagnosis.escalations[0].status == "pending"
    assert "dns_check_failed" in diagnosis.escalations[0].reason


def test_missing_conversation_is_explicit() -> None:
    service, _ = make_service()
    try:
        service.persist_diagnosis(
            request_id="req-3", query="test", state=sample_state(), conversation_id="missing"
        )
    except ConversationNotFoundError as error:
        assert str(error) == "conversation_not_found"
    else:
        raise AssertionError("Expected missing conversation failure")


def test_transaction_rolls_back_when_commit_fails() -> None:
    service, factory = make_service()
    session = factory()

    def break_commit(_: Session) -> None:
        raise SQLAlchemyError("commit failed")

    event.listen(session, "before_commit", break_commit)
    service.session_factory = lambda: session  # type: ignore[assignment]
    try:
        service.persist_diagnosis(
            request_id="req-4", query="test", state=sample_state(), conversation_id=None
        )
    except PersistenceError:
        pass
    else:
        raise AssertionError("Expected persistence failure")
    with factory() as verifier:
        assert verifier.scalar(select(func.count()).select_from(Conversation)) == 0
        assert verifier.scalar(select(func.count()).select_from(DiagnosisRun)) == 0
        assert verifier.scalar(select(func.count()).select_from(Message)) == 0


def test_database_unavailable_is_structured_error() -> None:
    def unavailable() -> Session:
        raise SQLAlchemyError("connection failed")

    service = PersistenceService(unavailable)  # type: ignore[arg-type]
    try:
        service.create_conversation()
    except PersistenceError as error:
        assert str(error) == "database_unavailable"
    else:
        raise AssertionError("Expected unavailable database failure")


def test_ssl_and_http_tool_summaries_are_persisted_without_raw_payloads() -> None:
    service, _ = make_service()
    state = sample_state()
    state.update(
        {
            "tools_called": ["ssl_check", "http_check", "search_support_docs"],
            "ssl_result": {
                "connection_status": "success",
                "certificate_valid": True,
                "hostname_match": True,
                "days_until_expiry": 42,
                "latency_ms": 8,
            },
            "http_result": {
                "requested_url": "https://example.com",
                "final_url": "https://example.com",
                "status_code": 503,
                "redirect_count": 0,
                "content_type": "text/html",
                "response_latency_ms": 12,
            },
        }
    )
    stored = service.persist_diagnosis(
        request_id="req-network", query="website down", state=state, conversation_id=None
    )
    diagnosis = service.get_diagnosis(stored.diagnosis_run_id)

    tool_runs = {item.tool_name: item for item in diagnosis.tool_runs}  # type: ignore[union-attr]
    assert tool_runs["ssl_check"].output_summary == {
        "certificate_valid": True,
        "hostname_match": True,
        "days_until_expiry": 42,
    }
    assert tool_runs["http_check"].output_summary["status_code"] == 503
