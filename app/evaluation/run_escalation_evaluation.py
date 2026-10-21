"""Run deterministic Phase 9 escalation scenarios with local fake tool evidence."""

import json
from pathlib import Path

from app.agents.diagnosis_graph import DiagnosisWorkflow
from app.llm.provider import GenerationResult, LLMTimeoutError
from app.llm.service import GroundedAnswerService
from app.rag.schemas import SearchResult
from app.tools.dns import DnsCheckResponse, DnsRecordResult
from app.tools.http import HttpCheckResponse
from app.tools.ssl import SslCheckResponse


class Retriever:
    def __init__(self, empty: bool = False) -> None:
        self.empty = empty

    def search_support_docs(self, query: str, top_k: int = 5) -> list[SearchResult]:
        return (
            []
            if self.empty
            else [
                SearchResult(
                    text="Support evidence",
                    similarity_score=0.9,
                    document_id="eval",
                    source="eval.md",
                    category="support",
                    topic="evaluation",
                    version="1",
                    chunk_index=0,
                )
            ]
        )


class Dns:
    def __init__(self, status: str = "ok") -> None:
        self.status = status

    def check_dns(self, domain: str) -> DnsCheckResponse:
        return DnsCheckResponse(
            domain=domain,
            status=self.status,
            records={"A": DnsRecordResult(record_type="A", status="ok", records=["203.0.113.10"])},
            lookup_duration_ms=1,
            tool_execution_id="eval",
        )


class Http:
    def __init__(self, status: int = 200, error: str | None = None) -> None:
        self.status, self.error = status, error

    def check_http(self, domain: str) -> HttpCheckResponse:
        if self.error == "raise":
            raise TimeoutError("simulated HTTP timeout")
        return HttpCheckResponse(
            requested_url=f"https://{domain}",
            final_url=f"https://{domain}",
            status_code=None if self.error else self.status,
            redirect_count=0,
            response_latency_ms=1,
            content_type="text/html",
            https_used=True,
            error_type=self.error,
        )


class Ssl:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def check_ssl(self, domain: str) -> SslCheckResponse:
        if self.fail:
            raise TimeoutError("simulated TLS timeout")
        return SslCheckResponse(
            domain=domain,
            connection_status="success",
            certificate_present=True,
            certificate_valid=True,
            hostname_match=True,
            latency_ms=1,
        )


class Provider:
    model_name = "evaluation-model"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def generate(self, system_prompt: str, user_prompt: str) -> GenerationResult:
        if self.fail:
            raise LLMTimeoutError("timeout")
        return GenerationResult(
            text="Diagnosis\nEvidence.\n\nEvidence observed\nEvidence.\n\nRecommended next steps\nReview.\n\nSources\n[1]\n\nEscalation status\nNone.",
            model_name=self.model_name,
        )


def run(name: str) -> dict:
    empty = name == "missing_support_documentation"
    provider = Provider(fail=name == "llm_provider_failure")
    dns_status = "nxdomain" if name == "dns_timeout" else "ok"
    http = (
        Http(503)
        if name in {"conflicting_dns_http", "ambiguous_root_cause"}
        else Http(error="raise")
        if name == "http_timeout"
        else Http()
    )
    queries = {
        "clear_documentation_issue": "What is an A record?",
        "missing_support_documentation": "What is an A record?",
        "llm_provider_failure": "What is an A record?",
        "ssl_tool_failure": "My SSL certificate has an error",
        "conflicting_dns_http": "My website is down",
        "dns_timeout": "My website is down",
        "http_timeout": "My website is down",
        "ambiguous_root_cause": "My website is down",
    }
    query = queries.get(name, "Hello there")
    domain = (
        "example.com"
        if name in queries
        and name != "clear_documentation_issue"
        and name != "missing_support_documentation"
        and name != "llm_provider_failure"
        else None
    )
    return DiagnosisWorkflow(
        Retriever(empty),
        Dns(dns_status),
        GroundedAnswerService(provider),
        ssl_service=Ssl(fail=name == "ssl_tool_failure"),
        http_service=http,
    ).invoke(query, domain)


def main() -> None:
    path = Path(__file__).parents[2] / "data" / "evaluation" / "escalation_scenarios.json"
    scenarios = json.loads(path.read_text(encoding="utf-8"))
    measured = [
        {
            "name": item["name"],
            "expected_escalation": item["expected_escalation"],
            "actual_escalation": (state := run(item["name"]))["needs_human_review"],
            "expected_reason": item["expected_reason"],
            "actual_reason": state.get("escalation_reason"),
        }
        for item in scenarios
    ]
    assert all(item["expected_escalation"] == item["actual_escalation"] for item in measured)
    assert all(
        item["expected_reason"] is None or item["expected_reason"] in (item["actual_reason"] or "")
        for item in measured
    )
    print({"scenarios_checked": len(measured), "passed": len(measured), "results": measured})


if __name__ == "__main__":
    main()
