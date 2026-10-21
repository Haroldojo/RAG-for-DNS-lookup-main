"""Reproducible local/mocked Phase 11 evaluation and regression gate."""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from time import perf_counter

from qdrant_client import QdrantClient

from app.agents.diagnosis_graph import DiagnosisWorkflow
from app.evaluation.scenario_corpus import scenarios
from app.llm.provider import GenerationResult, LLMTimeoutError
from app.llm.service import GroundedAnswerService
from app.rag.embeddings import HashingEmbeddingProvider
from app.rag.schemas import DocumentMetadata, SearchResult
from app.rag.service import SupportDocumentRetriever
from app.rag.text import chunk_text
from app.tools.dns import DnsCheckResponse, DnsRecordResult
from app.tools.http import HttpCheckResponse
from app.tools.ssl import SslCheckResponse

ROOT = Path(__file__).parents[2]
OUT = ROOT / "data" / "evaluation" / "reports"


class ScenarioRetriever:
    def __init__(self, rows: list[dict[str, object]], fail: bool = False) -> None:
        self.by_query = {str(row["query"]): row for row in rows}
        self.fail = fail

    def search_support_docs(self, query: str, top_k: int = 5) -> list[SearchResult]:
        if self.fail:
            raise RuntimeError("qdrant unavailable")
        row = self.by_query[query]
        source = str(row["expected_support_document"])
        return [
            SearchResult(
                text="Controlled support evidence",
                similarity_score=0.9,
                document_id=source.removesuffix(".md"),
                source=source,
                category="support",
                topic="evaluation",
                version="1",
                chunk_index=0,
            )
        ]


class DNS:
    def __init__(self, fail: bool = False, blocked: bool = False) -> None:
        self.fail, self.blocked = fail, blocked

    def check_dns(self, domain: str) -> DnsCheckResponse:
        if self.fail:
            raise RuntimeError("dns resolver timeout")
        return DnsCheckResponse(
            domain=domain,
            status="nxdomain" if self.blocked else "ok",
            records={"A": DnsRecordResult(record_type="A", status="ok", records=["203.0.113.10"])},
            lookup_duration_ms=1,
            tool_execution_id="evaluation",
        )


class SSL:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def check_ssl(self, domain: str) -> SslCheckResponse:
        if self.fail:
            raise TimeoutError("tls timeout")
        return SslCheckResponse(
            domain=domain,
            connection_status="success",
            certificate_present=True,
            certificate_valid=True,
            hostname_match=True,
            latency_ms=1,
        )


class HTTP:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def check_http(self, domain: str) -> HttpCheckResponse:
        if self.fail:
            raise TimeoutError("http timeout")
        return HttpCheckResponse(
            requested_url=f"https://{domain}",
            final_url=f"https://{domain}",
            status_code=200,
            redirect_count=0,
            response_latency_ms=1,
            content_type="text/html",
            https_used=True,
        )


class Provider:
    model_name = "evaluation-model"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def generate(self, _system: str, _prompt: str) -> GenerationResult:
        if self.fail:
            raise LLMTimeoutError("timeout")
        return GenerationResult(
            "Diagnosis\nEvidence-grounded.\n\nEvidence observed\nRetrieved source.\n\nRecommended next steps\nReview evidence.\n\nSources\n[1]\n\nEscalation status\nStructured.",
            self.model_name,
            20,
            15,
        )


def workflow(rows: list[dict[str, object]], row: dict[str, object]) -> DiagnosisWorkflow:
    error = row.get("expected_error_type")
    return DiagnosisWorkflow(
        ScenarioRetriever(rows),
        DNS(fail=error == "dns_error"),
        GroundedAnswerService(Provider(fail=error == "llm_error")),
        SSL(fail=error == "ssl_error"),
        HTTP(fail=error == "http_error"),
    )


def legacy_intent(query: str) -> str:
    """Phase 10 classifier approximation; used only for the before/after report."""
    text = query.lower()
    if "http 5" in text or "503" in text or "502" in text or "504" in text:
        return "website_down"
    if "hosting" in text or "404" in text or "403" in text:
        return "hosting_issue"
    if "ssl" in text or "tls" in text or "certificate" in text:
        return "ssl_issue"
    if any(word in text for word in ("resolve", "dns", "nameserver", "mx", "txt", "cname")):
        return "dns_issue"
    if any(word in text for word in ("what is", "how do", "explain", "when should")):
        return "documentation_question"
    return "unknown"


def retrieval_metrics(
    rows: list[dict[str, object]], chunk_size: int
) -> tuple[dict[str, float], list[dict[str, object]]]:
    client = QdrantClient(":memory:")
    retriever = SupportDocumentRetriever(client, HashingEmbeddingProvider())
    for path in sorted((ROOT / "data" / "support_docs").glob("*.md")):
        metadata = DocumentMetadata.model_validate(
            json.loads(path.with_suffix(".json").read_text())
        )
        retriever.ingest_chunks(chunk_text(path.read_text(), metadata, chunk_size, 60))
    ranks = []
    p1 = p3 = recall = 0
    failures = []
    for row in rows:
        found = [item.source for item in retriever.search_support_docs(str(row["query"]), top_k=3)]
        expected = str(row["expected_support_document"])
        if found and found[0] == expected:
            p1 += 1
        if expected in found:
            p3 += 1
            recall += 1
            ranks.append(1 / (found.index(expected) + 1))
        else:
            ranks.append(0)
            failures.append(
                {
                    "query": row["query"],
                    "expected_document": expected,
                    "actual_documents": found,
                    "why": "dense retrieval did not return the labelled support document",
                }
            )
    n = len(rows)
    return {
        "precision_at_1": round(p1 / n, 4),
        "precision_at_3": round(p3 / n, 4),
        "recall_at_3": round(recall / n, 4),
        "mrr": round(mean(ranks), 4),
    }, failures


def main() -> None:
    rows = scenarios()
    results = []
    matrix: dict[str, Counter[str]] = defaultdict(Counter)
    latencies = []
    for row in rows:
        started = perf_counter()
        state = workflow(rows, row).invoke(
            str(row["query"]),
            row.get("domain") if isinstance(row.get("domain"), str) else None,
            request_id=str(row["scenario_id"]),
            trace_id=f"trace-{row['scenario_id']}",
        )
        latency = (perf_counter() - started) * 1000
        latencies.append(latency)
        actual_tools = state["tools_called"]
        expected_tools = list(row["expected_tools"])
        actual_intent = state["intent"]
        expected_intent = str(row["expected_intent"])
        matrix[expected_intent][actual_intent] += 1
        results.append(
            {
                "scenario_id": row["scenario_id"],
                "expected_intent": expected_intent,
                "actual_intent": actual_intent,
                "intent_correct": actual_intent == expected_intent,
                "expected_tools": expected_tools,
                "actual_tools": actual_tools,
                "tools_correct": set(actual_tools) == set(expected_tools),
                "expected_escalation": row["expected_escalation"],
                "actual_escalation": state["needs_escalation"],
                "escalation_correct": state["needs_escalation"] == row["expected_escalation"],
                "citation_correct": all(
                    c["source"] == row["expected_support_document"] for c in state["citations"]
                ),
                "unsupported_claim": "invented" in state["answer"].lower(),
                "latency_ms": round(latency, 2),
                "errors": state["errors"],
            }
        )
    n = len(results)
    intent_accuracy = sum(x["intent_correct"] for x in results) / n
    tool_rate = sum(x["tools_correct"] for x in results) / n
    escalation = sum(x["escalation_correct"] for x in results) / n
    should = [x for x in results if x["expected_escalation"]]
    actual = [x for x in results if x["actual_escalation"]]
    tp = sum(x["expected_escalation"] and x["actual_escalation"] for x in results)
    precision = tp / len(actual) if actual else 1
    recall = tp / len(should) if should else 1
    before = sum(legacy_intent(str(row["query"])) == row["expected_intent"] for row in rows) / n
    retrieval, retrieval_failures = retrieval_metrics(rows, 800)
    alternative_retrieval, _ = retrieval_metrics(rows, 320)
    summary = {
        "label": "local mocked orchestration + local in-memory dense retrieval",
        "scenario_count": n,
        "routing_accuracy_before": round(before, 4),
        "routing_accuracy_after": round(intent_accuracy, 4),
        "tool_selection_rate": round(tool_rate, 4),
        "escalation_accuracy": round(escalation, 4),
        "escalation_precision": round(precision, 4),
        "escalation_recall": round(recall, 4),
        "unsupported_claim_rate": round(sum(x["unsupported_claim"] for x in results) / n, 4),
        "citation_correctness": round(sum(x["citation_correct"] for x in results) / n, 4),
        "average_diagnosis_latency_ms": round(mean(latencies), 2),
        "p50_latency_ms": round(sorted(latencies)[n // 2], 2),
        "p95_latency_ms": round(sorted(latencies)[int(n * 0.95) - 1], 2),
        "retrieval_dense_chunk_800_baseline": retrieval,
        "retrieval_dense_chunk_320_alternative": alternative_retrieval,
        "confusion_matrix": {key: dict(value) for key, value in matrix.items()},
    }
    gates = {
        "routing_accuracy": summary["routing_accuracy_after"] >= 0.9,
        "retrieval_precision_at_1": retrieval["precision_at_1"] >= 0.35,
        "escalation_recall": summary["escalation_recall"] >= 0.85,
        "unsupported_claim_rate": summary["unsupported_claim_rate"] <= 0.02,
    }
    failures = [
        x
        for x in results
        if not x["intent_correct"] or not x["tools_correct"] or not x["escalation_correct"]
    ] + retrieval_failures
    unique_failures = []
    seen_failure_keys = set()
    for failure in failures:
        key = str(failure.get("query") or failure.get("scenario_id"))
        if key not in seen_failure_keys:
            seen_failure_keys.add(key)
            unique_failures.append(failure)
    failures = unique_failures[:10]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "phase11_summary.json").write_text(
        json.dumps({"summary": summary, "gates": gates, "failures": failures}, indent=2)
    )
    with (OUT / "phase11_results.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    markdown = (
        "# Phase 11 evaluation\n\n"
        + "\n".join(
            f"- **{key}**: {value}" for key, value in summary.items() if key != "confusion_matrix"
        )
        + f"\n\n## Gates\n\n{json.dumps(gates, indent=2)}\n\n## Confusion matrix\n\n```json\n{json.dumps(summary['confusion_matrix'], indent=2)}\n```\n\n## Failure cases\n\n```json\n{json.dumps(failures[:5], indent=2)}\n```\n"
    )
    (OUT / "phase11_report.md").write_text(markdown)
    print(
        json.dumps(
            {
                "summary": summary,
                "gates": gates,
                "failure_count": len(failures),
                "report_directory": str(OUT),
            },
            indent=2,
        )
    )
    if not all(gates.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
