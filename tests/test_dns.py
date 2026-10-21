import dns.resolver
import pytest

from app.tools.dns import RECORD_TYPES, DnsCheckRequest, DnsDiagnosticService, resolve_record


class FakeAnswer:
    def __init__(self, records: list[str] | None) -> None:
        self.rrset = records
        self._records = records or []

    def __iter__(self):
        return iter(self._records)


class FakeResolver:
    def __init__(self, outcomes: dict[str, object]) -> None:
        self.outcomes = outcomes

    def resolve(self, qname: str, rdtype: str, **kwargs: object) -> FakeAnswer:
        outcome = self.outcomes.get(rdtype, FakeAnswer(None))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome  # type: ignore[return-value]


def test_valid_domain_returns_all_supported_record_types() -> None:
    service = DnsDiagnosticService(
        FakeResolver({"A": FakeAnswer(["93.184.216.34"]), "NS": FakeAnswer(["ns1.example.net."])})
    )

    result = service.check_dns("Example.COM.", tool_execution_id="test-id")

    assert result.domain == "example.com"
    assert result.status == "ok"
    assert tuple(result.records) == RECORD_TYPES
    assert result.records["A"].records == ["93.184.216.34"]
    assert result.tool_execution_id == "test-id"


@pytest.mark.parametrize(
    "domain", ["", "localhost", "https://example.com", "bad_domain.com", "-bad.com"]
)
def test_invalid_domain_is_rejected(domain: str) -> None:
    with pytest.raises(ValueError, match="valid fully qualified"):
        DnsCheckRequest(domain=domain)


def test_nxdomain_is_classified_explicitly() -> None:
    service = DnsDiagnosticService(
        FakeResolver({record_type: dns.resolver.NXDOMAIN() for record_type in RECORD_TYPES})
    )

    result = service.check_dns("missing.example")

    assert result.status == "nxdomain"
    assert all(record.status == "nxdomain" for record in result.records.values())


def test_timeout_is_not_classified_as_missing_record() -> None:
    service = DnsDiagnosticService(FakeResolver({"A": dns.resolver.LifetimeTimeout()}))

    result = service.check_dns("slow.example")

    assert result.status == "partial_failure"
    assert result.records["A"].status == "timeout"
    assert result.records["A"].error == "DNS lookup timed out"


def test_missing_record_type_is_explicit() -> None:
    result = resolve_record(FakeResolver({"MX": FakeAnswer(None)}), "example.com", "MX")

    assert result.status == "no_record"
    assert result.records == []


def test_temporary_resolver_failure_is_explicit() -> None:
    result = resolve_record(
        FakeResolver({"TXT": dns.resolver.NoNameservers()}), "example.com", "TXT"
    )

    assert result.status == "resolver_failure"
    assert result.error == "No responding authoritative nameservers"
