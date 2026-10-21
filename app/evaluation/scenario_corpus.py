"""Versioned, offline Phase 11 scenario corpus (100 support cases)."""

from __future__ import annotations

from itertools import cycle

DOC = {
    "dns_issue": "data/support_docs/dns-basics.md",
    "ssl_issue": "data/support_docs/ssl-issues.md",
    "website_down": "data/support_docs/common-hosting-errors.md",
    "hosting_issue": "data/support_docs/common-hosting-errors.md",
    "documentation_question": "data/support_docs/a-and-cname-records.md",
    "unknown": "data/support_docs/dns-basics.md",
}
TOOLS = {
    "dns_issue": ["dns_check", "search_support_docs"],
    "ssl_issue": ["dns_check", "ssl_check", "search_support_docs"],
    "website_down": ["dns_check", "http_check", "search_support_docs"],
    "hosting_issue": ["http_check", "search_support_docs"],
    "documentation_question": ["search_support_docs"],
    "unknown": ["search_support_docs"],
}


def _build(
    prefix: str,
    intent: str,
    queries: list[str],
    count: int,
    *,
    escalate: bool = False,
    root: str,
    error: str | None = None,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for index, query in zip(range(1, count + 1), cycle(queries), strict=False):
        domain = "example.com" if intent not in {"documentation_question", "unknown"} else None
        records.append(
            {
                "scenario_id": f"{prefix}-{index:03d}",
                "query": query,
                "domain": domain,
                "expected_intent": intent,
                "expected_tools": TOOLS[intent],
                "expected_skipped_tools": [],
                "expected_support_document": DOC[intent],
                "expected_escalation": escalate,
                "expected_error_type": error,
                "expected_root_cause_category": root,
            }
        )
    return records


def scenarios() -> list[dict[str, object]]:
    rows = []
    rows += _build(
        "dns",
        "dns_issue",
        [
            "My domain is not resolving after a nameserver update",
            "Visitors say the address cannot resolve",
            "Why is DNS propagation still failing?",
            "The MX and TXT changes are not visible",
            "Our CNAME stopped resolving today",
        ],
        20,
        root="dns_configuration",
    )
    rows += _build(
        "ssl",
        "ssl_issue",
        [
            "Browser says the SSL certificate expired",
            "There is a TLS hostname warning",
            "HTTPS certificate validation is failing",
            "The site reports a certificate mismatch",
        ],
        15,
        root="tls_certificate",
    )
    rows += _build(
        "web",
        "website_down",
        [
            "My website is down with a 503",
            "The site returns 502 bad gateway",
            "Visitors receive HTTP 500",
            "The homepage is unavailable after a redirect loop",
            "Why does the site intermittently return a 504?",
        ],
        20,
        root="website_availability",
    )
    rows += _build(
        "host",
        "hosting_issue",
        [
            "My hosting server shows a 404",
            "Why am I getting forbidden access?",
            "The hosting panel reports a server error",
            "A customer sees 403 on a page",
        ],
        15,
        root="hosting_configuration",
    )
    rows += _build(
        "docs",
        "documentation_question",
        [
            "What is an A record?",
            "How do I create a CNAME record?",
            "Explain nameservers in simple terms",
            "When should I use MX records?",
        ],
        10,
        root="documentation",
    )
    rows += _build(
        "ambiguous",
        "website_down",
        [
            "The site opens sometimes but fails randomly",
            "It works on my phone but not every browser",
            "The domain looks fine yet users report outages",
        ],
        10,
        escalate=True,
        root="ambiguous_multi_cause",
    )
    rows += _build(
        "scope",
        "unknown",
        [
            "Please reset my hosting password",
            "Can you deploy my application?",
            "Change the DNS record for me",
            "Why is my email campaign unpopular?",
            "Fix my billing invoice",
        ],
        10,
        escalate=True,
        root="out_of_scope",
    )
    # Controlled dependency failures used by reliability evaluation.
    rows[2]["expected_error_type"] = "dns_error"
    rows[20]["expected_error_type"] = "ssl_error"
    rows[35]["expected_error_type"] = "http_error"
    rows[90]["expected_error_type"] = "llm_error"
    for row in (rows[2], rows[20], rows[35], rows[90]):
        row["expected_escalation"] = True
    return rows
