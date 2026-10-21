"""Offline deterministic routing evaluation; no model, DNS, or Qdrant service is required."""

import json
from pathlib import Path

from app.agents.diagnosis_graph import DiagnosisWorkflow
from app.llm.provider import GenerationResult
from app.llm.service import GroundedAnswerService
from app.rag.schemas import SearchResult
from app.tools.dns import DnsCheckResponse

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _Retriever:
    def search_support_docs(self, query: str, top_k: int = 5) -> list[SearchResult]:
        return [
            SearchResult(
                text="Evidence",
                similarity_score=0.9,
                document_id="evaluation-doc",
                source="evaluation",
                category="dns",
                topic="evaluation",
                version="1.0",
                chunk_index=0,
            )
        ]


class _Dns:
    def check_dns(self, domain: str) -> DnsCheckResponse:
        return DnsCheckResponse(
            domain=domain,
            status="ok",
            records={},
            lookup_duration_ms=0.1,
            tool_execution_id="evaluation-dns",
        )


class _LLM:
    model_name = "evaluation-model"

    def generate(self, system_prompt: str, user_prompt: str) -> GenerationResult:
        return GenerationResult(
            text="Diagnosis\nEvidence reviewed.\n\nEvidence observed\nAvailable evidence.\n\nRecommended next steps\nReview evidence.\n\nSources\n\nEscalation status\nSee state.",
            model_name=self.model_name,
        )


def main() -> None:
    scenarios = json.loads(
        (PROJECT_ROOT / "data" / "evaluation" / "routing_scenarios.json").read_text()
    )
    workflow = DiagnosisWorkflow(_Retriever(), _Dns(), GroundedAnswerService(_LLM()))
    passed = 0
    for scenario in scenarios:
        state = workflow.invoke(scenario["query"], scenario["domain"])
        success = (
            state["intent"] == scenario["expected_intent"]
            and state["route_taken"] == scenario["expected_route"]
        )
        passed += success
        print(
            f"{'PASS' if success else 'FAIL'} | route={state['route_taken']} | "
            f"tools={','.join(state['tools_called'])} | escalation={state['needs_escalation']} | {scenario['query']}"
        )
    print(f"\nRouting checks passed: {passed}/{len(scenarios)}")


if __name__ == "__main__":
    main()
