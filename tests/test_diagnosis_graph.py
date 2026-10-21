import json
from pathlib import Path

from app.agents.diagnosis_graph import DiagnosisWorkflow
from app.llm.provider import GenerationResult
from app.llm.service import GroundedAnswerService
from app.rag.schemas import SearchResult
from app.tools.dns import DnsCheckResponse, DnsRecordResult
from app.tools.http import HttpCheckResponse
from app.tools.ssl import SslCheckResponse


class FakeRetriever:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def search_support_docs(self, query: str, top_k: int = 5) -> list[SearchResult]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("Qdrant unavailable")
        return [
            SearchResult(
                text="DNS documentation",
                similarity_score=0.9,
                document_id="dns-basics-v1",
                source="test.md",
                category="dns",
                topic="DNS basics",
                version="1.0",
                chunk_index=0,
            )
        ]


class FakeDnsService:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def check_dns(self, domain: str) -> DnsCheckResponse:
        self.calls += 1
        if self.fail:
            raise RuntimeError("DNS resolver unavailable")
        return DnsCheckResponse(
            domain=domain,
            status="ok",
            records={"A": DnsRecordResult(record_type="A", status="ok", records=["203.0.113.10"])},
            lookup_duration_ms=1.0,
            tool_execution_id="test-dns-run",
        )


class FakeLLMProvider:
    model_name = "fake-grounded-model"

    def generate(self, system_prompt: str, user_prompt: str) -> GenerationResult:
        return GenerationResult(
            text="Diagnosis\nBased on [1], inspect the DNS configuration.\n\nEvidence observed\nEvidence was retrieved.\n\nRecommended next steps\nReview the record.\n\nSources\n- invented\n\nEscalation status\nNot required.",
            model_name=self.model_name,
            prompt_tokens=12,
            completion_tokens=18,
        )


def workflow(
    retriever: FakeRetriever | None = None, dns: FakeDnsService | None = None
) -> DiagnosisWorkflow:
    return DiagnosisWorkflow(
        retriever or FakeRetriever(),
        dns or FakeDnsService(),
        GroundedAnswerService(FakeLLMProvider()),
    )


class FakeSslService:
    def __init__(self, error_type: str | None = None) -> None:
        self.calls = 0
        self.error_type = error_type

    def check_ssl(self, domain: str) -> SslCheckResponse:
        self.calls += 1
        return SslCheckResponse(
            domain=domain,
            connection_status="failed" if self.error_type else "success",
            certificate_present=True,
            certificate_valid=not bool(self.error_type),
            hostname_match=not bool(self.error_type),
            error_type=self.error_type,
            latency_ms=1,
        )


class FakeHttpService:
    def __init__(self, status_code: int = 200, error_type: str | None = None) -> None:
        self.calls = 0
        self.status_code = status_code
        self.error_type = error_type

    def check_http(self, domain: str) -> HttpCheckResponse:
        self.calls += 1
        return HttpCheckResponse(
            requested_url=f"https://{domain}",
            final_url=f"https://{domain}",
            status_code=None if self.error_type else self.status_code,
            redirect_count=0,
            response_latency_ms=1,
            content_type="text/html",
            https_used=True,
            error_type=self.error_type,
        )


def advanced_workflow(
    dns: FakeDnsService | None = None,
    ssl: FakeSslService | None = None,
    http: FakeHttpService | None = None,
) -> DiagnosisWorkflow:
    return DiagnosisWorkflow(
        FakeRetriever(),
        dns or FakeDnsService(),
        GroundedAnswerService(FakeLLMProvider()),
        ssl or FakeSslService(),
        http or FakeHttpService(),
    )


def test_documentation_question_uses_rag_only() -> None:
    retriever, dns = FakeRetriever(), FakeDnsService()
    result = workflow(retriever, dns).invoke("How do I create a CNAME record?")

    assert result["intent"] == "documentation_question"
    assert result["route_taken"] == "rag_only"
    assert result["tools_called"] == ["search_support_docs"]
    assert dns.calls == 0
    assert result["needs_escalation"] is False


def test_dns_issue_uses_dns_and_rag() -> None:
    retriever, dns = FakeRetriever(), FakeDnsService()
    result = workflow(retriever, dns).invoke(
        "My domain is not resolving after I changed nameservers", "example.com"
    )

    assert result["intent"] == "dns_issue"
    assert result["route_taken"] == "dns_and_rag"
    assert result["tools_called"] == ["dns_check", "search_support_docs"]
    assert result["dns_result"]["status"] == "ok"
    assert result["needs_escalation"] is False


def test_unknown_uses_fallback_rag_and_escalates() -> None:
    result = workflow().invoke("Hello there")

    assert result["intent"] == "unknown"
    assert result["route_taken"] == "fallback_rag"
    assert result["tools_called"] == ["search_support_docs"]
    assert result["needs_escalation"] is True
    assert result["confidence"] == 0.25


def test_invalid_domain_is_safe_and_skips_dns() -> None:
    result = workflow().invoke("Domain not working", "not a domain")

    assert result["domain"] is None
    assert result["route_taken"] == "fallback_rag"
    assert "dns_check" not in result["tools_called"]
    assert any(error.startswith("invalid_domain:") for error in result["errors"])


def test_dns_failure_does_not_crash_graph() -> None:
    result = workflow(dns=FakeDnsService(fail=True)).invoke(
        "DNS propagation is failing", "example.com"
    )

    assert result["route_taken"] == "dns_and_rag"
    assert result["retrieved_chunks"]
    assert result["needs_escalation"] is True
    assert any(error.startswith("dns_check_failed:") for error in result["errors"])


def test_retrieval_failure_does_not_crash_graph() -> None:
    result = workflow(retriever=FakeRetriever(fail=True)).invoke("What is an A record?")

    assert result["route_taken"] == "rag_only"
    assert result["retrieved_chunks"] == []
    assert result["needs_escalation"] is True
    assert any(error.startswith("retrieval_failed:") for error in result["errors"])


def test_routing_dataset_contains_twelve_scenarios() -> None:
    data_path = Path(__file__).parents[1] / "data" / "evaluation" / "routing_scenarios.json"
    scenarios = json.loads(data_path.read_text())

    assert len(scenarios) >= 12


def test_diagnostic_scenario_dataset_contains_twenty_scenarios() -> None:
    data_path = Path(__file__).parents[1] / "data" / "evaluation" / "diagnostic_scenarios.json"
    scenarios = json.loads(data_path.read_text())

    assert len(scenarios) >= 20


def test_ssl_issue_routes_to_dns_ssl_and_rag() -> None:
    result = advanced_workflow(ssl=FakeSslService("expired_certificate")).invoke(
        "My SSL certificate has expired", "example.com"
    )

    assert result["intent"] == "ssl_issue"
    assert result["tools_called"] == ["dns_check", "ssl_check", "search_support_docs"]
    assert result["ssl_result"]["error_type"] == "expired_certificate"


def test_website_down_uses_http_after_dns() -> None:
    result = advanced_workflow(http=FakeHttpService(503)).invoke(
        "My website is down with 503", "example.com"
    )

    assert result["intent"] == "website_down"
    assert result["tools_called"] == ["dns_check", "http_check", "search_support_docs"]
    assert result["http_result"]["status_code"] == 503
    assert result["needs_escalation"] is True


def test_dns_failure_skips_dependent_tool() -> None:
    dns = FakeDnsService()
    dns.check_dns = lambda domain: DnsCheckResponse(  # type: ignore[method-assign]
        domain=domain, status="nxdomain", records={}, lookup_duration_ms=1, tool_execution_id="dns"
    )
    http = FakeHttpService()
    result = advanced_workflow(dns=dns, http=http).invoke("My website is down", "example.com")

    assert http.calls == 0
    assert "http_check: blocked_by_dns_failure" in result["tools_skipped"]


def test_conflicting_http_evidence_lowers_confidence() -> None:
    result = advanced_workflow(http=FakeHttpService(503)).invoke(
        "My website is down", "example.com"
    )

    assert result["answer_context"]["evidence_conflict"] is True
    assert result["needs_escalation"] is True
