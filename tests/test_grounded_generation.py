import pytest

from app.agents.diagnosis_graph import DiagnosisWorkflow
from app.llm.provider import EmptyResponseError, GenerationResult, LLMTimeoutError, RateLimitError
from app.llm.service import GroundedAnswerService
from tests.test_diagnosis_graph import FakeDnsService, FakeRetriever


class StubProvider:
    model_name = "stub-model"

    def __init__(self, result: GenerationResult | Exception | None = None) -> None:
        self.result = result or GenerationResult(
            text="Diagnosis\nUse [1].\n\nEvidence observed\nRetrieved evidence.\n\nRecommended next steps\nCheck it.\n\nSources\n- fabricated-source\n\nEscalation status\nNone.",
            model_name=self.model_name,
        )
        self.prompts: list[str] = []

    def generate(self, system_prompt: str, user_prompt: str) -> GenerationResult:
        self.prompts.append(user_prompt)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class EmptyRetriever(FakeRetriever):
    def search_support_docs(self, query: str, top_k: int = 5):
        self.calls += 1
        return []


def make_workflow(provider: StubProvider, retriever=None, dns=None) -> DiagnosisWorkflow:
    return DiagnosisWorkflow(
        retriever or FakeRetriever(), dns or FakeDnsService(), GroundedAnswerService(provider)
    )


def test_documentation_only_response_is_grounded() -> None:
    provider = StubProvider()
    state = make_workflow(provider).invoke("What is an A record?")

    assert state["intent"] == "documentation_question"
    assert state["answer"].startswith("Diagnosis")
    assert "test.md" in state["answer"]
    assert state["citations"][0]["document_id"] == "dns-basics-v1"


def test_dns_and_rag_response_receives_only_structured_evidence() -> None:
    provider = StubProvider()
    state = make_workflow(provider).invoke("My domain is not resolving", "example.com")

    assert state["dns_result"]["status"] == "ok"
    assert "203.0.113.10" in provider.prompts[0]
    assert "DNS documentation" in provider.prompts[0]


def test_dns_resolver_failure_preserves_error_and_escalates() -> None:
    state = make_workflow(StubProvider(), dns=FakeDnsService(fail=True)).invoke(
        "My domain is not resolving", "example.com"
    )

    assert any(error.startswith("dns_check_failed:") for error in state["errors"])
    assert state["needs_escalation"] is True
    assert "did not return usable records" in state["answer"]
    assert state["generation_metadata"]["status"] == "skipped"


def test_no_relevant_evidence_skips_provider_and_returns_safe_fallback() -> None:
    provider = StubProvider()
    state = make_workflow(provider, retriever=EmptyRetriever()).invoke("What is an A record?")

    assert provider.prompts == []
    assert state["generation_metadata"]["status"] == "skipped"
    assert state["needs_escalation"] is True
    assert "could not be diagnosed confidently" in state["answer"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (LLMTimeoutError("timeout"), "LLMTimeoutError"),
        (RateLimitError("rate limited"), "RateLimitError"),
        (EmptyResponseError("empty"), "EmptyResponseError"),
    ],
)
def test_provider_failures_return_safe_fallback(error: Exception, expected: str) -> None:
    state = make_workflow(StubProvider(error)).invoke("What is an A record?")

    assert state["generation_metadata"]["status"] == "failed"
    assert state["generation_metadata"]["provider_error_type"] == expected
    assert state["needs_escalation"] is True
    assert expected in state["answer"]


def test_missing_groq_api_key_returns_safe_fallback() -> None:
    from app.llm.groq_provider import GroqProvider

    provider = GroqProvider(None, "llama-3.1-8b-instant", 1.0, 0.1, 100)
    state = DiagnosisWorkflow(
        FakeRetriever(), FakeDnsService(), GroundedAnswerService(provider)
    ).invoke("What is an A record?")

    assert state["generation_metadata"]["provider_error_type"] == "MissingAPIKeyError"
    assert state["needs_escalation"] is True


def test_application_controls_citations_not_model_output() -> None:
    state = make_workflow(StubProvider()).invoke("What is an A record?")

    assert "fabricated-source" not in state["answer"]
    assert state["citations"] == [
        {
            "document_id": "dns-basics-v1",
            "source": "test.md",
            "topic": "DNS basics",
            "version": "1.0",
        }
    ]


def test_unknown_query_keeps_low_confidence_and_escalation() -> None:
    state = make_workflow(StubProvider()).invoke("Hello")

    assert state["intent"] == "unknown"
    assert state["needs_escalation"] is True
    assert state["confidence"] <= 0.25
