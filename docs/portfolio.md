# Recruiter, demo, and interview guide

## Recruiter summary

Agentic DNS & Hosting Support Copilot is an evidence-driven support-diagnostics system for DNS, TLS/SSL, hosting, and website-access issues. It combines deterministic LangGraph routing, Qdrant RAG, read-only DNS/TLS/HTTP tools, and a grounded Groq/LLaMA layer. PostgreSQL preserves auditable conversation history while Redis handles disposable caching and rate limits. On a 100-scenario offline benchmark, routing improved from 71% to 97% and tool selection reached 97%; a separate 56-scenario controlled security suite validates its safety gates.

## GitHub presentation

**Repository description (under GitHub's limit)**

> Agentic DNS & hosting support copilot using LangGraph, RAG, FastAPI, Qdrant, Redis and PostgreSQL with grounded diagnostics, HITL escalation and security testing.

**Recommended topics**

`langgraph` · `rag` · `generative-ai` · `fastapi` · `qdrant` · `redis` · `postgresql` · `dns` · `ai-agents` · `llm` · `docker` · `github-actions`

## Screenshot plan

Do not use mock output. Capture these from a running local stack or the GitHub Actions page.

1. **Architecture:** README Mermaid diagram with the title and CI badge visible.
2. **API diagnosis:** Swagger UI request and structured `/api/v1/diagnose` response showing intent, tools, and citations.
3. **Website-down evidence:** a `website_down` response where DNS succeeds, HTTP shows `503`, and the answer distinguishes facts from recommendations.
4. **Human escalation:** an escalation record with `pending`, a clear deterministic reason, and review status.
5. **Evaluation:** terminal output from `python -m app.evaluation.run_full_evaluation` with the 100-scenario summary and gates.
6. **Security:** terminal output from `python -m app.evaluation.run_security_evaluation` showing the 56-scenario controlled benchmark and gates.
7. **CI:** GitHub Actions run page showing the `CI` workflow, commit, and green success result.

Avoid screenshots containing API keys, connection strings, real customer data, or raw request headers.

## 2–3 minute demo script

**0:00–0:20 — Problem**  
“Support teams often have to search documentation, inspect DNS, check certificates, and test HTTP separately. This copilot organizes that work into one evidence-driven diagnostic workflow.”

**0:20–0:45 — Architecture**  
“FastAPI accepts a request, deterministic LangGraph routing chooses only relevant read-only tools, Qdrant retrieves support evidence, and the LLM explains the combined evidence. The LLM does not control infrastructure.”

**0:45–1:30 — Live diagnosis**  
“I submit: ‘My website returns 503 after a hosting migration.’ The route selects DNS, HTTP, and RAG. The response shows each tool result, skipped tools if any, support chunks, citations, confidence, and the answer.”

**1:30–1:55 — Evidence and safety**  
“If DNS resolves but HTTP is 503, the system states that as observed evidence rather than claiming a definitive root cause. If evidence conflicts or a tool fails, deterministic escalation requires review.”

**1:55–2:15 — HITL and security**  
“Human review records pending, approved, or rejected outcomes. Tools are read-only; prompt injection, SSRF, secret redaction, and tenant scoping are enforced outside the model.”

**2:15–2:40 — Quality**  
“The project has 107 automated tests, a 100-scenario offline evaluation where routing improved from 71% to 97%, and a 56-scenario controlled security benchmark. GitHub Actions validates the repository.”

**2:40–3:00 — Close**  
“The next engineering focus is improving the small support corpus and retrieval quality, then validating the prepared deployment in a real environment.”

## Architecture explanations

### 30 seconds

“This is a support copilot for DNS, TLS, HTTP, and hosting issues. A deterministic LangGraph workflow chooses safe read-only tools and retrieves documentation from Qdrant. It fuses that evidence before an LLM writes a cited answer, then escalates low-confidence cases. PostgreSQL keeps history, Redis handles short-lived caching, and security checks remain outside the model.”

### 90 seconds

“A FastAPI request is validated and rate-limited, then a deterministic classifier selects RAG only or the smallest useful combination of DNS, TLS, and HTTP tools. DNS failure can skip dependent checks. Support documents are embedded and searched in Qdrant; all tool outputs remain structured. Evidence fusion produces the factual context for the Groq/LLaMA provider, while the application—not the model—creates citations. PostgreSQL persists a consistent diagnosis record and Redis accelerates disposable lookups. When evidence conflicts, a required tool fails, or confidence is low, policy creates a human-review escalation. Evaluation and red-team suites make the decisions measurable.”

### 3 minutes

Use the 90-second explanation, then add: “The important separation is that LangGraph orchestrates state and routing but is not coupled to persistence. The repository/service layer persists compact summaries transactionally after the graph returns. Qdrant is for semantic document vectors; PostgreSQL is for relational conversation/audit history; Redis is deliberately not a durable source of truth. The LLM receives only user query, route information, retrieved chunks, and diagnostic output. It cannot call tools or create citations. Prompt injection and SSRF are deterministic controls outside the model. For reliability, every node and tool emits safe structured observability data with request and trace IDs. The current retrieval result is the honest limitation: on a tiny five-document corpus, Precision@1 is 39%, with an alternate chunking setup reaching 43%. I would expand the corpus, test embeddings and reranking, and validate against more realistic labeled cases before claiming production quality.”

## Interview Q&A

**Why LangGraph?**  
It makes state, routing, tool dependencies, skipped steps, and errors explicit. That is easier to test and extend than a hidden sequence of conditional calls.

**Why deterministic routing rather than LLM-only routing?**  
Tool choice is a safety and reliability decision. Rules are inspectable, repeatable, and measurable; an LLM can be evaluated as an isolated future classifier instead of silently deciding production paths.

**Why RAG and Qdrant?**  
Support documentation changes and needs traceable sources. Qdrant stores chunk embeddings plus metadata for semantic retrieval and scoped filtering; it is not used for durable conversations.

**Why both Redis and PostgreSQL?**  
PostgreSQL holds durable relational history and audit records. Redis handles disposable cache entries and rate limits where speed matters and loss is acceptable.

**Why evidence fusion?**  
DNS, TLS, HTTP, and documents can disagree. Combining structured facts first prevents the LLM from mistaking a narrative for observed infrastructure state.

**How does DNS affect TLS and HTTP checks?**  
TLS and HTTP need a reachable hostname. If DNS fails, dependent checks are skipped and the blocked dependency is recorded rather than interpreted as an unrelated certificate or server failure.

**How is prompt injection handled?**  
Direct malicious input is detected before generation; retrieved documents are untrusted data and are sanitized. The model is not allowed to follow instructions in documents, and output is screened.

**How is SSRF prevented?**  
The HTTP/TLS tools allow only public `http`/`https` targets, block internal address ranges and metadata endpoints, and revalidate every redirect destination.

**Why HITL?**  
Escalation is distinct from failure: it preserves uncertainty safely. Deterministic policy requires review for low confidence, tool failures, conflicting evidence, or unknown issues.

**How did you evaluate routing?**  
A fixed 100-scenario offline corpus records expected intent, tools, skips, escalation, and error type. The evaluation produces confusion matrices and fails regression gates when results worsen.

**Why is Precision@1 currently low?**  
The corpus is intentionally tiny—five small documents—so semantic retrieval has limited coverage and nearby topics compete. I report 39% baseline and 43% alternate-chunking Precision@1 transparently.

**What happens if Groq fails?**  
The workflow preserves evidence, records the provider error, emits a safe fallback, and escalates where appropriate. It does not fabricate an answer.

**What happens if Redis fails?**  
Cache reads bypass Redis and use authoritative sources; rate limiting intentionally fails open in this baseline to preserve diagnosis availability, while the error is logged.

**How would you scale it?**  
Use multiple stateless app replicas behind a load balancer, managed PostgreSQL/Redis/Qdrant, a single migration job, monitored worker/concurrency limits, improved cache-stampede handling, and authenticated tenant identity before introducing more tools or write actions.
