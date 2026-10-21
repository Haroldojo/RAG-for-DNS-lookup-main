"""Small deterministic baseline for grounded-generation behavior; no external LLM call occurs."""

import json
from pathlib import Path

from app.agents.diagnosis_graph import DiagnosisWorkflow
from app.llm.provider import (
    EmptyResponseError,
    GenerationResult,
    LLMTimeoutError,
    MissingAPIKeyError,
    RateLimitError,
)
from app.llm.service import GroundedAnswerService
from app.rag.schemas import SearchResult
from app.tools.dns import DnsCheckResponse, DnsRecordResult

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _Retriever:
    def __init__(self, empty: bool = False) -> None:
        self.empty = empty

    def search_support_docs(self, query: str, top_k: int = 5) -> list[SearchResult]:
        if self.empty:
            return []
        return [
            SearchResult(
                text="An A record maps a hostname to an IPv4 address.",
                similarity_score=0.9,
                document_id="a-record-v1",
                source="a-record.md",
                category="dns",
                topic="A records",
                version="1.0",
                chunk_index=0,
            )
        ]


class _Dns:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def check_dns(self, domain: str) -> DnsCheckResponse:
        if self.fail:
            raise RuntimeError("resolver unavailable")
        return DnsCheckResponse(
            domain=domain,
            status="ok",
            records={"A": DnsRecordResult(record_type="A", status="ok", records=["203.0.113.5"])},
            lookup_duration_ms=1,
            tool_execution_id="eval",
        )


class _Provider:
    model_name = "evaluation-model"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def generate(self, system_prompt: str, user_prompt: str) -> GenerationResult:
        if self.error:
            raise self.error
        return GenerationResult(
            text="Diagnosis\nEvidence reviewed.\n\nEvidence observed\nA record evidence.\n\nRecommended next steps\nReview it.\n\nSources\n- model-text\n\nEscalation status\nSee state.",
            model_name=self.model_name,
        )


def run(kind: str) -> bool:
    errors = {
        "missing_key": MissingAPIKeyError("missing key"),
        "timeout": LLMTimeoutError("timeout"),
        "rate_limit": RateLimitError("rate"),
        "empty_response": EmptyResponseError("empty"),
    }
    workflow = DiagnosisWorkflow(
        _Retriever(empty=kind == "no_evidence"),
        _Dns(fail=kind == "dns_failure"),
        GroundedAnswerService(_Provider(errors.get(kind))),
    )
    state = workflow.invoke(
        "My domain is not resolving"
        if kind in {"dns_success", "dns_failure"}
        else "What is an A record?",
        "example.com" if kind in {"dns_success", "dns_failure"} else None,
    )
    if kind == "citation":
        return (
            state["citations"][0]["source"] == "a-record.md" and "model-text" not in state["answer"]
        )
    if kind == "dns_success":
        return "203.0.113.5" in state["answer_context"].get("dns_status", "") or state[
            "dns_result"
        ]["records"]["A"]["records"] == ["203.0.113.5"]
    if kind == "dns_failure":
        return state["needs_escalation"] and any(
            error.startswith("dns_check_failed:") for error in state["errors"]
        )
    if kind == "no_evidence":
        return state["generation_metadata"]["status"] == "skipped" and state["needs_escalation"]
    if kind in errors:
        return state["generation_metadata"]["status"] == "failed" and state["needs_escalation"]
    if kind == "unknown":
        unknown = workflow.invoke("Hello")
        return unknown["needs_escalation"] and unknown["intent"] == "unknown"
    return bool(state["citations"])


def main() -> None:
    scenarios = json.loads(
        (PROJECT_ROOT / "data" / "evaluation" / "grounding_scenarios.json").read_text()
    )
    passed = 0
    for item in scenarios:
        success = run(item["kind"])
        passed += success
        print(f"{'PASS' if success else 'FAIL'} | {item['scenario']}")
    print(f"\nGrounding checks passed: {passed}/{len(scenarios)}")


if __name__ == "__main__":
    main()
