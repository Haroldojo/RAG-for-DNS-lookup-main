# Architecture roadmap

## Phase 2 retrieval boundary

1. Read `.md`, `.txt`, or `.pdf` support documents.
2. Normalize whitespace and split text with configurable size/overlap.
3. Create a local semantic embedding for each chunk.
4. Upsert vectors and metadata to the Qdrant collection `support_docs`.
5. Embed a search query and return the top-scoring chunks through `POST /api/v1/search`.

The API returns evidence only. It does not generate an answer, invoke an LLM, diagnose a live
domain, or change any external system.

## Phase 3 DNS tool boundary

```text
POST /api/v1/tools/dns-check → validate domain → read-only resolver → normalize six record types
→ structured result + execution ID → structured log
```

The tool exposes no provider credentials, DNS update operation, or write method. It returns public
DNS observations only. `nxdomain`, `no_record`, `timeout`, and `resolver_failure` are preserved as
separate states so later components do not confuse uncertainty with absence.

## Phase 4 orchestration boundary

```text
input validation → deterministic classification → conditional edge
  → RAG only | DNS then RAG | fallback RAG → context → escalation decision
```

`DiagnosisState` is the contract between nodes. It contains input, classification, records,
retrieval results, errors, route, tools called, confidence, and escalation. The graph never uses
an LLM to select a route and never generates a final answer. Tests inject failing retrieval/DNS
tools to confirm the workflow still returns inspectable state.

## Phase 5 grounded-generation boundary

```text
retrieved chunks + DNS tool result + route + errors
  → prompt builder → provider abstraction → answer text
  → application-controlled citations + generation metadata → escalation decision
```

The LLM interprets evidence but cannot supply canonical sources. The application derives citations
from retrieval metadata. Generation failure returns a safe fallback and retains existing evidence,
errors, and escalation.

## Phase 6 persistence boundary

```text
FastAPI request → persistence service → LangGraph (unchanged) → persistence repository → PostgreSQL
                                      ↓
                           structured diagnosis state
```

The graph has no database session or model imports. After it returns, one SQLAlchemy transaction
writes the user message, diagnosis run, assistant answer, compact DNS/RAG/generation summaries,
controlled citations, and any escalation. A database exception rolls the transaction back and the
API returns a structured persistence error; no durable identifiers are returned for an
uncommitted run.

PostgreSQL stores relational history, while Qdrant stores semantic vectors and document payloads.
Foreign keys enforce ownership (`conversation → messages/runs`, `run → tools/citations/escalations`)
and indexes cover status, conversation chronology, request IDs, and run children. Raw DNS payloads,
API keys, passwords, and connection strings are outside the persistence contract.

Schema changes are Alembic migrations (`alembic/versions/20261019_01_initial_persistence.py`), not
automatic production `create_all` calls. Tests create an isolated SQLite schema only as a test
fixture; runtime configuration targets PostgreSQL through `DATABASE_URL`.

## Phase 7 Redis cache/rate-limit boundary

```text
request → Redis GET → hit: return disposable snapshot
                   └→ miss/error: authoritative DNS or Qdrant → Redis SET with TTL

POST /diagnose → Redis INCR fixed-window counter → allow or 429
```

Redis is intentionally outside the durable transaction path. PostgreSQL remains the audit system
of record and Qdrant remains the similarity-search store. DNS stores a complete record snapshot
under `dns:v1:{domain}:all` for a short TTL; only non-outage results are cached. Retrieval uses a
hash of normalized query plus collection/index version, model/chunk settings, and `top_k`.

Cache faults (unavailable server, timeout, reset, malformed JSON) are observable and fail open to
DNS/Qdrant. Rate limiting also fails open during a Redis outage so the optional control plane cannot
make diagnosis unavailable. It uses configurable fixed windows per client IP and returns a
structured 429 with `Retry-After` when Redis is healthy and the limit is exceeded. No final LLM
responses, secrets, or user-confidential raw payloads are cached.

## Phase 8 network-evidence boundary

```text
intent → selected tools only → structured DNS / TLS / HTTP observations → evidence fusion → LLM
              │                         │
              └─ DNS failure ───────────┴→ explicit skipped dependent tools
```

TLS runs only after DNS for `ssl_issue`; website-down runs HTTP only after DNS; hosting runs a
bounded public HTTP check directly. The graph preserves every tool's `success`, `failed`, or
`skipped` status. Evidence fusion is a deterministic state-building step, not model reasoning: it
combines outputs and identifies uncertainty/conflict before answer generation.

HTTP target validation permits only public `http`/`https` targets and repeats it on redirects.
It blocks loopback, private, link-local, reserved, and cloud-metadata IPs to prevent SSRF. Results
are compact and no page body, sensitive request header, cookie, or secret enters PostgreSQL.

Redis extends cache-aside to a short SSL TTL (300 seconds) and very short HTTP TTL (30 seconds).
Transient network failures are not cached. Redis remains optional; source tools stay authoritative.

## Phase 9 human-review boundary

```text
diagnosis → evidence fusion → deterministic escalation policy
                              ├─ not_required → answer
                              └─ pending → human review endpoint → approved / rejected / resolved
```

The graph, not the LLM, applies escalation policy. It records low confidence, tool failure,
conflicting evidence, missing retrieval evidence, dependency blocks, generation failure, unknown
intent, and ambiguity. Pending preserves diagnostic evidence but prevents the result being treated
as a final resolution.

Escalations are PostgreSQL records updated atomically through the internal simulated review API.
Review decisions and notes are audit data; they trigger no external change. This becomes the future
approval boundary for DNS/hosting/SSL configuration changes, ticket creation, or service restarts.

## Phase 10 observability boundary

```text
API request_id + trace_id
  → LangGraph node timings → RAG/tool/LLM events → persistence/review events
  → safe in-process metrics + JSON-style logs
```

The request/trace pair provides a diagnostic join key; conversation and diagnosis-run identifiers are
added once known. Each graph node records start/end/status/latency without reasoning text. Tools record
only tool name, outcome, cache state, latency, failure category, and skip cause. RAG records a query
hash, retrieval settings, document IDs, scores, cache state, and collection version—not document bodies.
LLM records provider/model, latency, optional token counts, status, and normalized provider error.

`/health` is liveness-only. `/ready` checks configured dependencies and returns `degraded` rather than
changing process liveness. Redis, PostgreSQL, and Qdrant health are compact statuses; no URLs,
credentials, headers, prompts, or raw user data are exposed. `/metrics` stays in-process and small,
reporting counters plus average/P50/P95 latency. Telemetry is best-effort and fails open: an observability
fault cannot block diagnosis.

## Phase 11 evaluation boundary

```text
versioned scenario corpus → controlled workflow fixtures → metrics / failures / gates → JSON + CSV + Markdown
                                      └→ local Qdrant dense retrieval comparison
```

The evaluation command is a CI-safe offline regression gate. It separates mocked orchestration,
reliability, grounding, and latency measurements from local in-memory Qdrant retrieval measurements.
Scenarios encode expected intent/tool/escalation/evidence contracts. The report preserves confusion
matrices and representative misses rather than reporting only an aggregate score. Gates are set after
the first measured baseline and intentionally remain modest for the small five-document corpus.

One evidence-based classifier adjustment was made after the initial run: explicit out-of-scope write
requests are classified conservatively as `unknown`, and intermittent/outage wording is recognised as
`website_down` and escalated for multiple plausible causes. The same fixed corpus is rerun before and
after; it is not changed to hide those failures.

## Phase 12 security boundary

```text
untrusted user/document input → deterministic guardrails → read-only tools / scoped RAG
                                          └→ sanitized model output → persistence/logs
```

Injection detection, document sanitization, output-secret screening, PII minimization, scope filtering,
and tool permissions are deterministic controls outside the LLM. Security events use safe structured
telemetry and never retain raw attack payloads. The permission model permits only `READ_ONLY` DNS/SSL/HTTP/RAG
operations and reserves `WRITE_REQUIRES_APPROVAL` and `DESTRUCTIVE_BLOCKED` for future work.

Qdrant payloads carry `tenant_id`, `document_scope`, and `access_level`; searches filter to public plus
the caller's allowed scope, while scope also participates in the retrieval-cache key. This is a
future-compatible authorization boundary—not identity verification. The controlled red-team suite has
perfect benchmark block rates, which must not be interpreted as production-security certification.

## Phase 13 deployment boundary

```text
Internet → Nginx (80/443) → FastAPI (private Compose network)
                              ├─ PostgreSQL
                              ├─ Redis
                              └─ Qdrant
```

Only Nginx publishes a host port; FastAPI is exposed only to the Compose network and PostgreSQL, Redis,
and Qdrant are never public. `/health` supports container liveness while `/ready` reports dependency
degradation. A separately invoked migration job runs Alembic exactly once before application replicas
start. The CI workflow validates source, tests, evaluation gates, security gates, and Docker build;
deployment remains manual/approved CD.

## Data contract

Each Qdrant point contains one vector and payload fields:

| Field | Purpose |
| --- | --- |
| `document_id` | Stable ID for evaluation and deduplication |
| `source` | Original file path for citation/tracing |
| `category` / `topic` | Filtering and analysis labels |
| `version` | Document revision identifier |
| `chunk_index` | Position within the source document |
| `text` | Retrieved chunk shown to the caller |

## Incremental additions

| Phase | Capability | Done when |
| --- | --- | --- |
| 1 | API foundation | Health and readiness endpoints are tested. |
| 2 | Retrieval | Documents ingest into Qdrant and results include metadata. |
| 3 | DNS diagnostics | Read-only A/AAAA/CNAME/MX/NS/TXT checks have explicit failure states. |
| 4 | Orchestration | Deterministic LangGraph workflow selects RAG, DNS+RAG, or fallback. |
| 5 | Grounded generation | Groq/LLaMA writes evidence-bound responses with code-controlled citations. |
| 6 | Persistence | PostgreSQL records conversations, runs, summaries, citations, and escalations. |
| 7 | Cache/rate limiting | Redis cache-aside and fixed-window protection remain disposable and fail open. |
| 8 | Diagnostics/evidence fusion | SSRF-protected TLS/HTTP diagnostics contribute structured evidence. |
| 9 | Human review | Deterministic policy creates durable pending cases for approval/rejection. |
| 10 | Observability | Correlated safe logs, node/tool timings, metrics, and dependency-aware readiness. |
| 11 | Evaluation | 100-case offline corpus, retrieval comparison, reports, and CI regression gates. |
| 12 | Security | Guardrails, scoped retrieval, read-only tool permissions, and red-team regression gates. |
| 13 | Delivery | Docker/Compose, CI validation, Nginx/EC2 preparation, smoke tests, and runbook. |
| 8–10 | Reliability/evaluation | Failure paths and benchmark metrics are measured, not invented. |

