import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import escalations as escalation_route
from app.database.service import EscalationNotFoundError, PersistenceError, PersistenceService
from app.llm.provider import LLMTimeoutError
from app.main import create_app
from app.tools.dns import DnsCheckResponse
from tests.test_diagnosis_graph import FakeDnsService, FakeHttpService, advanced_workflow, workflow
from tests.test_grounded_generation import StubProvider, make_workflow
from tests.test_persistence import make_service, sample_state


def test_low_confidence_unknown_triggers_pending_review() -> None:
    result = workflow().invoke("Hello there")

    assert result["needs_human_review"] is True
    assert result["escalation_status"] == "pending"
    assert "confidence_below_threshold" in result["escalation_reason"]


def test_conflict_and_dns_dependency_block_trigger_review() -> None:
    conflict = advanced_workflow(http=FakeHttpService(503)).invoke(
        "My website is down", "example.com"
    )
    dns = FakeDnsService()
    dns.check_dns = lambda domain: DnsCheckResponse(
        domain=domain, status="nxdomain", records={}, lookup_duration_ms=1, tool_execution_id="test"
    )  # type: ignore[method-assign]
    blocked = advanced_workflow(dns=dns, http=FakeHttpService()).invoke(
        "My website is down", "example.com"
    )

    assert "conflicting_evidence" in conflict["escalation_reason"]
    assert "dns_failure_blocked_downstream_diagnostics" in blocked["escalation_reason"]


def test_llm_failure_and_no_retrieval_evidence_trigger_review() -> None:
    result = make_workflow(StubProvider(LLMTimeoutError("timeout"))).invoke("What is an A record?")

    assert result["needs_human_review"] is True
    assert "llm_generation_failed_or_skipped" in result["escalation_reason"]


def test_high_confidence_diagnosis_does_not_require_review() -> None:
    result = workflow().invoke("What is an A record?")

    assert result["needs_human_review"] is False
    assert result["escalation_status"] == "not_required"


def test_review_approval_and_rejection_persisted() -> None:
    service, _ = make_service()
    first = service.persist_diagnosis(
        request_id="approve",
        query="unknown",
        state=sample_state(escalate=True),
        conversation_id=None,
    )
    second = service.persist_diagnosis(
        request_id="reject",
        query="unknown",
        state=sample_state(escalate=True),
        conversation_id=None,
    )
    first_escalation = service.get_diagnosis(first.diagnosis_run_id).escalations[0]  # type: ignore[union-attr]
    second_escalation = service.get_diagnosis(second.diagnosis_run_id).escalations[0]  # type: ignore[union-attr]

    approved = service.review_escalation(
        first_escalation.id, "approved", "Evidence supports the diagnosis."
    )
    rejected = service.review_escalation(second_escalation.id, "rejected", "Need more evidence.")

    assert approved.status == approved.review_decision == "approved"
    assert rejected.status == rejected.review_decision == "rejected"
    assert rejected.reviewed_at is not None and rejected.review_notes == "Need more evidence."


def test_invalid_escalation_and_review_decision_are_safe() -> None:
    service, _ = make_service()
    try:
        service.review_escalation("missing", "approved", None)
    except EscalationNotFoundError:
        pass
    else:
        raise AssertionError("Expected missing escalation")
    try:
        service.review_escalation("missing", "invalid", None)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected invalid decision")


def test_review_database_failure_is_structured() -> None:
    def unavailable():
        raise SQLAlchemyError("database unavailable")

    try:
        PersistenceService(unavailable).review_escalation("id", "approved", None)  # type: ignore[arg-type]
    except PersistenceError as error:
        assert str(error) == "review_persistence_failed"
    else:
        raise AssertionError("Expected review persistence failure")


def test_escalation_review_endpoints(monkeypatch) -> None:
    service, _ = make_service()
    stored = service.persist_diagnosis(
        request_id="endpoint",
        query="unknown",
        state=sample_state(escalate=True),
        conversation_id=None,
    )
    escalation = service.get_diagnosis(stored.diagnosis_run_id).escalations[0]  # type: ignore[union-attr]
    monkeypatch.setattr(escalation_route, "get_persistence_service", lambda: service)
    client = TestClient(create_app())

    assert client.get("/api/v1/escalations").status_code == 200
    assert client.get(f"/api/v1/escalations/{escalation.id}").json()["status"] == "pending"
    reviewed = client.post(
        f"/api/v1/escalations/{escalation.id}/review",
        json={"decision": "approved", "notes": "reviewed"},
    )

    assert reviewed.status_code == 200 and reviewed.json()["status"] == "approved"
    assert client.get("/api/v1/escalations/missing").status_code == 404
    assert (
        client.post(
            f"/api/v1/escalations/{escalation.id}/review", json={"decision": "invalid"}
        ).status_code
        == 422
    )


def test_escalation_scenario_dataset_contains_ten_scenarios() -> None:
    path = Path(__file__).parents[1] / "data" / "evaluation" / "escalation_scenarios.json"
    scenarios = json.loads(path.read_text())

    assert len(scenarios) >= 10
