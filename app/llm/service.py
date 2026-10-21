import logging
from time import perf_counter
from typing import Any

from app.core.logging import log_event
from app.llm.prompts import SYSTEM_PROMPT, build_generation_prompt
from app.llm.provider import GenerationResult, LLMProvider, LLMProviderError
from app.observability.runtime import event, metrics
from app.security.controls import sanitize_untrusted_document, validate_model_output

LOGGER = logging.getLogger(__name__)


def citations_from_chunks(chunks: list[dict[str, object]]) -> list[dict[str, str]]:
    """Create de-duplicated citations in application code, not from model output."""
    citations: list[dict[str, str]] = []
    seen: set[str] = set()
    for chunk in chunks:
        document_id = str(chunk["document_id"])
        if document_id not in seen:
            seen.add(document_id)
            citations.append(
                {
                    "document_id": document_id,
                    "source": str(chunk["source"]),
                    "topic": str(chunk["topic"]),
                    "version": str(chunk["version"]),
                }
            )
    return citations


def safe_fallback_answer(reason: str, citations: list[dict[str, str]]) -> str:
    sources = (
        "\n".join(f"- [{index + 1}] {item['source']}" for index, item in enumerate(citations))
        or "- None"
    )
    return f"""Diagnosis
The issue could not be diagnosed confidently. {reason}

Evidence observed
Only the structured evidence returned by the tools was retained.

Recommended next steps
Verify the reported evidence and escalate this case for human review.

Sources
{sources}

Escalation status
Escalation required."""


def attach_canonical_sources(answer: str, citations: list[dict[str, str]]) -> str:
    """Replace model-provided Sources text with citations generated from retrieved metadata."""
    before_sources = answer.split("\nSources\n", maxsplit=1)[0].rstrip()
    sources = (
        "\n".join(
            f"- [{index + 1}] {item['source']} ({item['topic']}, v{item['version']})"
            for index, item in enumerate(citations)
        )
        or "- No retrieved support sources."
    )
    escalation = "Escalation status\nSee the structured needs_escalation field."
    return f"{before_sources}\n\nSources\n{sources}\n\n{escalation}"


class GroundedAnswerService:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def generate(
        self,
        request_id: str,
        query: str,
        intent: str,
        route_taken: str,
        chunks: list[dict[str, object]],
        dns_result: dict[str, object] | None,
        errors: list[str],
        ssl_result: dict[str, object] | None = None,
        http_result: dict[str, object] | None = None,
    ) -> tuple[str, list[dict[str, str]], dict[str, Any], str | None]:
        chunks = [
            {**chunk, "text": sanitize_untrusted_document(str(chunk.get("text", "")))}
            for chunk in chunks
        ]
        citations = citations_from_chunks(chunks)
        context_size = sum(len(str(chunk.get("text", ""))) for chunk in chunks)
        dns_status = str((dns_result or {}).get("status", ""))
        dns_failed = (
            any(error.startswith("dns_check_failed:") for error in errors)
            or dns_status == "resolver_failure"
        )
        if dns_failed:
            metadata = {
                "model_name": self.provider.model_name,
                "status": "skipped",
                "input_context_chars": context_size,
                "provider_error_type": "dns_evidence_failure",
            }
            return (
                safe_fallback_answer(
                    "DNS diagnostics did not return usable records, so no DNS result is being assumed.",
                    citations,
                ),
                citations,
                metadata,
                "dns_evidence_failure",
            )
        if not chunks and not dns_result and not ssl_result and not http_result:
            metadata = {
                "model_name": self.provider.model_name,
                "status": "skipped",
                "input_context_chars": 0,
            }
            return (
                safe_fallback_answer(
                    "No relevant retrieved or diagnostic evidence was available.", citations
                ),
                citations,
                metadata,
                "insufficient_evidence",
            )
        prompt = build_generation_prompt(
            query,
            intent,
            route_taken,
            chunks,
            dns_result,
            ssl_result,
            http_result,
            citations,
            errors,
        )
        start = perf_counter()
        try:
            result: GenerationResult = self.provider.generate(SYSTEM_PROMPT, prompt)
        except LLMProviderError as error:
            latency_ms = round((perf_counter() - start) * 1_000, 2)
            error_type = type(error).__name__
            metadata = {
                "model_name": self.provider.model_name,
                "status": "failed",
                "provider_error_type": error_type,
                "generation_latency_ms": latency_ms,
                "input_context_chars": context_size,
            }
            log_event(LOGGER, "llm_generation_completed", request_id=request_id, **metadata)
            event(
                "llm",
                "generate",
                "failed",
                request_id=request_id,
                latency_ms=latency_ms,
                error_type=error_type,
                provider="groq",
                model_name=self.provider.model_name,
            )
            metrics.increment("llm_failures_total")
            metrics.observe("llm_generation_latency_ms", latency_ms)
            return (
                safe_fallback_answer(f"LLM generation was unavailable ({error_type}).", citations),
                citations,
                metadata,
                f"llm_failed: {error_type}",
            )
        latency_ms = round((perf_counter() - start) * 1_000, 2)
        metadata = {
            "model_name": result.model_name,
            "status": "ok",
            "generation_latency_ms": latency_ms,
            "input_context_chars": context_size,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "provider_error_type": None,
        }
        log_event(LOGGER, "llm_generation_completed", request_id=request_id, **metadata)
        event(
            "llm",
            "generate",
            "ok",
            request_id=request_id,
            latency_ms=latency_ms,
            provider="groq",
            model_name=result.model_name,
            input_tokens=result.prompt_tokens,
            output_tokens=result.completion_tokens,
            total_tokens=(result.prompt_tokens or 0) + (result.completion_tokens or 0),
        )
        metrics.observe("llm_generation_latency_ms", latency_ms)
        metrics.increment(
            "llm_tokens_total", (result.prompt_tokens or 0) + (result.completion_tokens or 0)
        )
        answer, blocked = validate_model_output(attach_canonical_sources(result.text, citations))
        if blocked:
            metadata["status"] = "blocked"
            metadata["provider_error_type"] = "unsafe_output"
            return (
                safe_fallback_answer(
                    "Generated output contained sensitive-looking content and was withheld.",
                    citations,
                ),
                citations,
                metadata,
                "unsafe_llm_output",
            )
        return answer, citations, metadata, None
