import json
import logging

from fastapi.testclient import TestClient

from app.api.routes import health as health_route
from app.core.config import Settings
from app.core.logging import log_event
from app.llm.provider import LLMTimeoutError
from app.main import create_app
from app.observability.runtime import begin_request, metrics
from tests.test_diagnosis_graph import FakeHttpService, advanced_workflow, workflow
from tests.test_grounded_generation import StubProvider, make_workflow


def test_request_trace_and_node_timing_propagate() -> None:
    state = workflow().invoke("What is an A record?", request_id="request-1", trace_id="trace-1")

    assert state["request_id"] == "request-1"
    assert state["trace_id"] == "trace-1"
    assert {"validate_input", "classify_issue", "retrieve_docs", "generate_answer"} <= set(
        state["node_timings_ms"]
    )


def test_tool_and_llm_observability_metrics_increment() -> None:
    state = advanced_workflow(http=FakeHttpService(503)).invoke(
        "My website is down", "example.com", request_id="request-2", trace_id="trace-2"
    )
    snapshot = metrics.snapshot()

    assert state["tool_statuses"]["http_check"] == "success"
    assert "tool.http_check" in snapshot["latency_ms"]


def test_llm_failure_is_recorded_without_crashing() -> None:
    state = make_workflow(StubProvider(LLMTimeoutError("timeout"))).invoke(
        "What is an A record?", request_id="request-3", trace_id="trace-3"
    )

    assert state["generation_metadata"]["status"] == "failed"
    assert metrics.snapshot()["counters"]["llm_failures_total"] >= 1


def test_dependency_health_can_be_degraded(monkeypatch) -> None:
    settings = Settings(database_url="postgresql://unavailable")
    monkeypatch.setattr(health_route, "get_settings", lambda: settings)

    body = TestClient(create_app()).get("/ready").json()

    assert body["status"] == "degraded"
    assert body["checks"]["postgresql"] == "unavailable"


def test_metrics_endpoint_and_safe_structured_logging(caplog) -> None:
    begin_request("request-4")
    with caplog.at_level(logging.INFO):
        log_event(logging.getLogger("test"), "event", api_key="secret", query="private request")
    body = TestClient(create_app()).get("/metrics").json()
    record = json.loads(caplog.records[-1].message)

    assert "counters" in body
    assert record["api_key"] == "[redacted]"
    assert record["query"] == "[redacted]"
