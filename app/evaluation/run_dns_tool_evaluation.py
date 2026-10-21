"""Offline checks for DNS validation, error classifications and response schema."""

import json
from pathlib import Path

import dns.resolver

from app.tools.dns import RECORD_TYPES, DnsCheckRequest, DnsDiagnosticService

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _Answer:
    def __init__(self, records: list[str] | None) -> None:
        self.rrset = records
        self._records = records or []

    def __iter__(self):
        return iter(self._records)


class _Resolver:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome

    def resolve(self, qname: str, rdtype: str, **kwargs: object) -> _Answer:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome  # type: ignore[return-value]


def main() -> None:
    cases = json.loads(
        (PROJECT_ROOT / "data" / "evaluation" / "dns_diagnostic_cases.json").read_text()
    )
    print(f"Loaded {len(cases)} realistic DNS support scenarios.")
    valid = DnsDiagnosticService(_Resolver(_Answer(["203.0.113.10"]))).check_dns("example.com")
    nxdomain = DnsDiagnosticService(_Resolver(dns.resolver.NXDOMAIN())).check_dns("missing.example")
    timeout = DnsDiagnosticService(_Resolver(dns.resolver.LifetimeTimeout())).check_dns(
        "slow.example"
    )
    invalid_rejected = False
    try:
        DnsCheckRequest(domain="https://example.com")
    except ValueError:
        invalid_rejected = True

    checks = {
        "input_validation": invalid_rejected,
        "nxdomain_classification": nxdomain.status == "nxdomain",
        "timeout_classification": timeout.records["A"].status == "timeout",
        "structured_record_schema": tuple(valid.records) == RECORD_TYPES,
        "tool_execution_id": bool(valid.tool_execution_id),
    }
    for name, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'} | {name}")
    print(f"\nChecks passed: {sum(checks.values())}/{len(checks)}")


if __name__ == "__main__":
    main()
