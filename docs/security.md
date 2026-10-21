# Security assumptions and benchmark scope

## Safety controls

The copilot treats user input and retrieved documents as untrusted data. It applies deterministic controls outside the LLM for direct prompt injection, indirect document injection, secret/PII redaction, tenant/document scoping, output screening, and read-only tool permissions.

HTTP and TLS diagnostics enforce SSRF restrictions: only `http` and `https` are accepted; loopback, private, link-local, metadata, IPv4-mapped IPv6, and unsupported-scheme targets are blocked. Each redirect destination is revalidated. DNS, TLS, HTTP, and retrieval tools have no write permission.

Security events are structured and sanitized. API keys, passwords, headers, cookies, raw credentials, and unnecessary user content are excluded from logs, traces, persistence, and evaluation outputs.

## Controlled benchmark, not certification

`python -m app.evaluation.run_security_evaluation` executes 56 deterministic red-team scenarios. It covers prompt injection, secret-exfiltration attempts, unsupported tool use, tenant isolation, and SSRF. The defined gates passed in the local Phase 12 evaluation.

This is an offline controlled benchmark, not production penetration testing, a compliance audit, or a production security certification. A deployed system still needs threat modeling, authenticated identity/authorization, dependency patching, secrets management, monitoring, and periodic review.
