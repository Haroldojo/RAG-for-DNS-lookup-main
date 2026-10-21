"""Inspectable deterministic orchestration for RAG and read-only diagnostics."""

from collections.abc import Callable
from time import perf_counter
from typing import Literal, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, field_validator

from app.llm.service import GroundedAnswerService
from app.observability.runtime import event, metrics
from app.rag.service import SupportDocumentRetriever
from app.security.controls import detect_prompt_injection
from app.tools.dns import DnsCheckRequest, DnsDiagnosticService
from app.tools.http import HttpDiagnosticService
from app.tools.ssl import SslDiagnosticService

Intent = Literal[
    "dns_issue", "ssl_issue", "website_down", "hosting_issue", "documentation_question", "unknown"
]


class DiagnosisState(TypedDict, total=False):
    user_query: str
    domain: str | None
    intent: Intent
    retrieved_chunks: list[dict[str, object]]
    dns_result: dict[str, object] | None
    ssl_result: dict[str, object] | None
    http_result: dict[str, object] | None
    answer_context: dict[str, object]
    confidence: float
    needs_escalation: bool
    errors: list[str]
    route_taken: str
    tools_called: list[str]
    tools_skipped: list[str]
    tool_statuses: dict[str, str]
    request_id: str
    trace_id: str
    node_timings_ms: dict[str, float]
    answer: str
    citations: list[dict[str, str]]
    generation_metadata: dict[str, object]
    needs_human_review: bool
    escalation_reason: str | None
    escalation_status: str
    review_notes: str | None
    reviewed_at: str | None
    security_blocked: bool


class DiagnoseRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1_000)
    domain: str | None = Field(default=None, max_length=253)
    conversation_id: str | None = Field(default=None, max_length=36)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("query must not be empty")
        return normalized


class DiagnoseResponse(BaseModel):
    conversation_id: str | None = None
    diagnosis_run_id: str | None = None
    query: str
    domain: str | None
    intent: Intent
    route_taken: str
    tools_called: list[str]
    tools_skipped: list[str]
    tool_statuses: dict[str, str]
    answer: str
    citations: list[dict[str, str]]
    retrieved_chunks: list[dict[str, object]]
    dns_result: dict[str, object] | None
    ssl_result: dict[str, object] | None
    http_result: dict[str, object] | None
    answer_context: dict[str, object]
    confidence: float
    needs_escalation: bool
    errors: list[str]
    generation_metadata: dict[str, object]
    needs_human_review: bool
    escalation_reason: str | None
    escalation_status: str
    review_notes: str | None
    reviewed_at: str | None
    request_id: str
    trace_id: str
    node_timings_ms: dict[str, float]


class DiagnosisWorkflow:
    def __init__(
        self,
        retriever: SupportDocumentRetriever,
        dns_service: DnsDiagnosticService,
        answer_service: GroundedAnswerService,
        ssl_service: SslDiagnosticService | None = None,
        http_service: HttpDiagnosticService | None = None,
        human_review_confidence_threshold: float = 0.7,
    ) -> None:
        self.retriever, self.dns_service, self.answer_service = (
            retriever,
            dns_service,
            answer_service,
        )
        self.ssl_service, self.http_service = ssl_service, http_service
        self.human_review_confidence_threshold = human_review_confidence_threshold
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(DiagnosisState)
        for name, node in (
            ("validate_input", self.validate_input),
            ("classify_issue", self.classify_issue),
            ("run_diagnostics", self.run_diagnostics),
            ("retrieve_docs", self.retrieve_docs),
            ("build_context", self.build_context),
            ("generate_answer", self.generate_answer),
            ("decide_escalation", self.decide_escalation),
            ("human_review_gate", self.human_review_gate),
        ):
            builder.add_node(name, self._instrument_node(name, node))
        builder.add_edge(START, "validate_input")
        for left, right in (
            ("validate_input", "classify_issue"),
            ("classify_issue", "run_diagnostics"),
            ("run_diagnostics", "retrieve_docs"),
            ("retrieve_docs", "build_context"),
            ("build_context", "generate_answer"),
            ("generate_answer", "decide_escalation"),
            ("decide_escalation", "human_review_gate"),
            ("human_review_gate", END),
        ):
            builder.add_edge(left, right)
        return builder.compile()

    @staticmethod
    def _instrument_node(
        name: str, node: Callable[[DiagnosisState], DiagnosisState]
    ) -> Callable[[DiagnosisState], DiagnosisState]:
        def wrapped(state: DiagnosisState) -> DiagnosisState:
            started = perf_counter()
            event(
                "langgraph",
                name,
                "started",
                trace_id=state.get("trace_id"),
                request_id=state.get("request_id"),
            )
            try:
                result = node(state)
            except Exception as error:
                latency = round((perf_counter() - started) * 1_000, 2)
                event(
                    "langgraph",
                    name,
                    "failed",
                    trace_id=state.get("trace_id"),
                    request_id=state.get("request_id"),
                    latency_ms=latency,
                    error_type=type(error).__name__,
                )
                metrics.increment("graph_node_failures_total")
                raise
            latency = round((perf_counter() - started) * 1_000, 2)
            result["node_timings_ms"] = {**state.get("node_timings_ms", {}), name: latency}
            event(
                "langgraph",
                name,
                "ok",
                trace_id=state.get("trace_id"),
                request_id=state.get("request_id"),
                latency_ms=latency,
            )
            metrics.observe(f"graph.node.{name}", latency)
            return result

        return wrapped

    def invoke(
        self,
        query: str,
        domain: str | None = None,
        request_id: str = "local-request",
        trace_id: str | None = None,
    ) -> DiagnosisState:
        return self.graph.invoke(
            {
                "user_query": query,
                "domain": domain,
                "errors": [],
                "tools_called": [],
                "tools_skipped": [],
                "tool_statuses": {},
                "retrieved_chunks": [],
                "dns_result": None,
                "ssl_result": None,
                "http_result": None,
                "request_id": request_id,
                "trace_id": trace_id or str(uuid4()),
                "node_timings_ms": {},
                "needs_human_review": False,
                "escalation_status": "not_required",
                "escalation_reason": None,
                "review_notes": None,
                "reviewed_at": None,
                "security_blocked": False,
            }
        )

    @staticmethod
    def validate_input(state: DiagnosisState) -> DiagnosisState:
        errors, domain = list(state.get("errors", [])), state.get("domain")
        security_blocked = detect_prompt_injection(state["user_query"])
        if security_blocked:
            errors.append("security_blocked: prompt_injection")
            event(
                "security",
                "direct_prompt_injection",
                "blocked",
                request_id=state.get("request_id"),
                trace_id=state.get("trace_id"),
                error_type="prompt_injection",
            )
        if domain:
            try:
                domain = DnsCheckRequest(domain=domain).domain
            except ValueError as error:
                errors.append(f"invalid_domain: {error}")
                domain = None
        return {"domain": domain, "errors": errors, "security_blocked": security_blocked}

    @staticmethod
    def classify_issue(state: DiagnosisState) -> DiagnosisState:
        query = state["user_query"].lower()
        if any(
            term in query
            for term in (
                "reset my",
                "deploy my",
                "change the dns",
                "billing invoice",
                "email campaign",
            )
        ):
            return {"intent": "unknown"}
        if any(
            term in query
            for term in (
                "how do",
                "what is",
                "when should",
                "explain",
                "guide",
                "documentation",
            )
        ):
            intent: Intent = "documentation_question"
        elif any(term in query for term in ("ssl", "tls", "certificate", "cert ", "https warning")):
            intent: Intent = "ssl_issue"
        elif any(
            term in query
            for term in (
                "website down",
                "website is down",
                "site down",
                "site is down",
                "website unavailable",
                "http 5",
                "502",
                "503",
                "504",
                "redirect loop",
                "http 500",
                "opens sometimes",
                "fails randomly",
                "report outages",
            )
        ):
            intent = "website_down"
        elif any(term in query for term in ("hosting", "server error", "forbidden", "404", "403")):
            intent = "hosting_issue"
        elif any(
            term in query
            for term in (
                "not resolving",
                "cannot resolve",
                "can't resolve",
                "dns error",
                "changed nameserver",
                "nameserver changed",
                "propagation",
                "a record",
                "cname",
                "mx",
                "txt",
            )
        ):
            intent = "dns_issue"
        else:
            intent = "unknown"
        return {"intent": intent}

    @staticmethod
    def _skip(state: DiagnosisState, tool: str, reason: str) -> None:
        state["tools_skipped"] = [*state.get("tools_skipped", []), f"{tool}: {reason}"]
        state["tool_statuses"] = {**state.get("tool_statuses", {}), tool: "skipped"}
        event(
            "tool",
            tool,
            "skipped",
            trace_id=state.get("trace_id"),
            request_id=state.get("request_id"),
            skipped_reason=reason,
        )

    def _dns(self, state: DiagnosisState) -> None:
        if not state.get("domain"):
            self._skip(state, "dns_check", "no_valid_domain")
            return
        state["tools_called"] = [*state.get("tools_called", []), "dns_check"]
        started = perf_counter()
        try:
            state["dns_result"] = self.dns_service.check_dns(state["domain"] or "").model_dump()
            state["tool_statuses"] = {**state["tool_statuses"], "dns_check": "success"}
        except Exception as error:  # noqa: BLE001
            state["errors"] = [*state.get("errors", []), f"dns_check_failed: {error}"]
            state["tool_statuses"] = {**state["tool_statuses"], "dns_check": "failed"}
        finally:
            latency = round((perf_counter() - started) * 1_000, 2)
            event(
                "tool",
                "dns_check",
                state["tool_statuses"].get("dns_check", "failed"),
                trace_id=state.get("trace_id"),
                request_id=state.get("request_id"),
                latency_ms=latency,
                cache_status=(state.get("dns_result") or {}).get("cache_status"),
            )
            metrics.observe("tool.dns_check", latency)

    @staticmethod
    def _dns_usable(state: DiagnosisState) -> bool:
        return (state.get("dns_result") or {}).get("status") == "ok"

    def _ssl(self, state: DiagnosisState) -> None:
        if not state.get("domain") or self.ssl_service is None:
            self._skip(
                state,
                "ssl_check",
                "no_valid_domain" if not state.get("domain") else "tool_not_configured",
            )
            return
        state["tools_called"] = [*state.get("tools_called", []), "ssl_check"]
        started = perf_counter()
        try:
            state["ssl_result"] = self.ssl_service.check_ssl(state["domain"] or "").model_dump()
            state["tool_statuses"] = {**state["tool_statuses"], "ssl_check": "success"}
        except Exception as error:  # noqa: BLE001
            state["errors"] = [*state.get("errors", []), f"ssl_check_failed: {error}"]
            state["tool_statuses"] = {**state["tool_statuses"], "ssl_check": "failed"}
        finally:
            latency = round((perf_counter() - started) * 1_000, 2)
            event(
                "tool",
                "ssl_check",
                state["tool_statuses"].get("ssl_check", "failed"),
                trace_id=state.get("trace_id"),
                request_id=state.get("request_id"),
                latency_ms=latency,
                cache_status=(state.get("ssl_result") or {}).get("cache_status"),
            )
            metrics.observe("tool.ssl_check", latency)

    def _http(self, state: DiagnosisState) -> None:
        if not state.get("domain") or self.http_service is None:
            self._skip(
                state,
                "http_check",
                "no_valid_domain" if not state.get("domain") else "tool_not_configured",
            )
            return
        state["tools_called"] = [*state.get("tools_called", []), "http_check"]
        started = perf_counter()
        try:
            state["http_result"] = self.http_service.check_http(state["domain"] or "").model_dump()
            state["tool_statuses"] = {**state["tool_statuses"], "http_check": "success"}
        except Exception as error:  # noqa: BLE001
            state["errors"] = [*state.get("errors", []), f"http_check_failed: {error}"]
            state["tool_statuses"] = {**state["tool_statuses"], "http_check": "failed"}
        finally:
            latency = round((perf_counter() - started) * 1_000, 2)
            event(
                "tool",
                "http_check",
                state["tool_statuses"].get("http_check", "failed"),
                trace_id=state.get("trace_id"),
                request_id=state.get("request_id"),
                latency_ms=latency,
                cache_status=(state.get("http_result") or {}).get("cache_status"),
            )
            metrics.observe("tool.http_check", latency)

    def run_diagnostics(self, state: DiagnosisState) -> DiagnosisState:
        intent = state["intent"]
        if intent == "dns_issue":
            self._dns(state)
        elif intent == "ssl_issue":
            self._dns(state)
            self._ssl(state) if self._dns_usable(state) else self._skip(
                state, "ssl_check", "blocked_by_dns_failure"
            )
        elif intent == "website_down":
            self._dns(state)
            self._http(state) if self._dns_usable(state) else self._skip(
                state, "http_check", "blocked_by_dns_failure"
            )
        elif intent == "hosting_issue":
            self._http(state)
        return {
            key: state[key]
            for key in (
                "dns_result",
                "ssl_result",
                "http_result",
                "tools_called",
                "tools_skipped",
                "tool_statuses",
                "errors",
            )
            if key in state
        }

    def retrieve_docs(self, state: DiagnosisState) -> DiagnosisState:
        tools = [*state.get("tools_called", []), "search_support_docs"]
        try:
            chunks = self.retriever.search_support_docs(state["user_query"], top_k=5)
            return {
                "retrieved_chunks": [chunk.model_dump() for chunk in chunks],
                "tools_called": tools,
                "tool_statuses": {
                    **state.get("tool_statuses", {}),
                    "search_support_docs": "success",
                },
            }
        except Exception as error:  # noqa: BLE001
            return {
                "retrieved_chunks": [],
                "tools_called": tools,
                "tool_statuses": {
                    **state.get("tool_statuses", {}),
                    "search_support_docs": "failed",
                },
                "errors": [*state.get("errors", []), f"retrieval_failed: {error}"],
            }

    @staticmethod
    def build_context(state: DiagnosisState) -> DiagnosisState:
        route = {
            "dns_issue": "dns_and_rag",
            "ssl_issue": "ssl_dns_and_rag",
            "website_down": "http_dns_and_rag",
            "hosting_issue": "http_and_rag",
            "documentation_question": "rag_only",
            "unknown": "fallback_rag",
        }[state["intent"]]
        dns, ssl, http = (
            state.get("dns_result") or {},
            state.get("ssl_result") or {},
            state.get("http_result") or {},
        )
        conflict = bool(
            dns.get("status") == "ok"
            and ssl.get("connection_status") == "success"
            and http.get("error_type")
        ) or bool(dns.get("status") == "ok" and (http.get("status_code") or 0) >= 500)
        return {
            "route_taken": route,
            "answer_context": {
                "intent": state["intent"],
                "route_taken": route,
                "retrieved_chunk_count": len(state.get("retrieved_chunks", [])),
                "evidence": {
                    "dns": dns.get("status"),
                    "ssl": ssl.get("error_type") or ssl.get("connection_status"),
                    "http": http.get("error_type") or http.get("status_code"),
                },
                "evidence_conflict": conflict,
                "tools_skipped": state.get("tools_skipped", []),
            },
        }

    def generate_answer(self, state: DiagnosisState) -> DiagnosisState:
        if state.get("security_blocked"):
            return {
                "answer": "Diagnosis\nThis request was blocked because it contains an unsupported instruction-overriding or secret-extraction attempt.\n\nEvidence observed\nNo untrusted instruction was executed.\n\nRecommended next steps\nAsk a support question without instructions to reveal secrets or override system controls.\n\nSources\n- None\n\nEscalation status\nEscalation required.",
                "citations": [],
                "generation_metadata": {
                    "model_name": "not_called",
                    "status": "blocked",
                    "provider_error_type": "prompt_injection",
                },
            }
        answer, citations, metadata, provider_error = self.answer_service.generate(
            request_id=state["request_id"],
            query=state["user_query"],
            intent=state["intent"],
            route_taken=state["route_taken"],
            chunks=state.get("retrieved_chunks", []),
            dns_result=state.get("dns_result"),
            ssl_result=state.get("ssl_result"),
            http_result=state.get("http_result"),
            errors=state.get("errors", []),
        )
        return {
            "answer": answer,
            "citations": citations,
            "generation_metadata": metadata,
            "errors": [*state.get("errors", []), *([provider_error] if provider_error else [])],
        }

    def decide_escalation(self, state: DiagnosisState) -> DiagnosisState:
        evidence = bool(
            state.get("retrieved_chunks")
            or state.get("dns_result")
            or state.get("ssl_result")
            or state.get("http_result")
        )
        if state["intent"] == "unknown":
            confidence, escalate = (0.25 if evidence else 0.1), True
        elif state.get("errors") or state.get("answer_context", {}).get("evidence_conflict"):
            confidence, escalate = (0.35 if evidence else 0.15), True
        elif state["intent"] == "dns_issue":
            confidence, escalate = (0.85 if evidence else 0.45), not evidence
        else:
            confidence, escalate = (0.8 if evidence else 0.4), not evidence
        if state.get("generation_metadata", {}).get("status") in {"failed", "skipped"}:
            confidence, escalate = min(confidence, 0.35), True
        reasons: list[str] = []
        ambiguous_symptoms = any(
            phrase in state.get("user_query", "").lower()
            for phrase in ("opens sometimes", "fails randomly", "looks fine yet")
        )
        if confidence < self.human_review_confidence_threshold:
            reasons.append("confidence_below_threshold")
        if state["intent"] == "unknown":
            reasons.append("unsupported_or_unknown_issue")
        if state.get("answer_context", {}).get("evidence_conflict"):
            reasons.append("conflicting_evidence")
        if ambiguous_symptoms:
            reasons.append("multiple_plausible_root_causes")
        if not state.get("retrieved_chunks"):
            reasons.append("no_relevant_rag_evidence")
        if any(status == "failed" for status in state.get("tool_statuses", {}).values()):
            reasons.append("required_diagnostic_tool_failed")
        if any("blocked_by_dns_failure" in item for item in state.get("tools_skipped", [])):
            reasons.append("dns_failure_blocked_downstream_diagnostics")
        if state.get("generation_metadata", {}).get("status") in {"failed", "skipped"}:
            reasons.append("llm_generation_failed_or_skipped")
        if state.get("errors"):
            reasons.append("diagnostic_error")
        if state.get("security_blocked"):
            reasons.append("security_policy_block")
        needs_review = escalate or bool(reasons)
        return {
            "confidence": confidence,
            "needs_escalation": needs_review,
            "needs_human_review": needs_review,
            "escalation_reason": "; ".join(dict.fromkeys(reasons)) or None,
            "escalation_status": "pending" if needs_review else "not_required",
        }

    @staticmethod
    def human_review_gate(state: DiagnosisState) -> DiagnosisState:
        if not state.get("needs_human_review"):
            return {}
        return {
            "answer": f"{state.get('answer', '')}\n\nHuman review required\nThis case is pending human review and must not be treated as resolved until a reviewer records a decision."
        }
