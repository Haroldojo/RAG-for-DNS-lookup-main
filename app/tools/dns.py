"""Read-only DNS diagnostic tool. It never writes DNS records."""

import logging
import re
from time import perf_counter
from typing import Protocol
from uuid import uuid4

import dns.exception
import dns.resolver
from pydantic import BaseModel, Field, field_validator

from app.cache.redis_cache import CacheBackend, CacheUnavailableError, decode, encode
from app.core.logging import log_event

LOGGER = logging.getLogger(__name__)
RECORD_TYPES = ("A", "AAAA", "CNAME", "MX", "NS", "TXT")
DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}\.?$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\.?$", re.IGNORECASE
)


class DnsCheckRequest(BaseModel):
    domain: str = Field(max_length=253, examples=["example.com"])

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        domain = value.strip().rstrip(".").lower()
        if not DOMAIN_PATTERN.fullmatch(domain):
            raise ValueError("domain must be a valid fully qualified domain name")
        return domain


class DnsRecordResult(BaseModel):
    record_type: str
    status: str
    records: list[str] = Field(default_factory=list)
    error: str | None = None


class DnsCheckResponse(BaseModel):
    domain: str
    status: str
    records: dict[str, DnsRecordResult]
    errors: list[str] = Field(default_factory=list)
    lookup_duration_ms: float
    tool_execution_id: str
    cache_status: str = "bypassed"
    cache_latency_ms: float | None = None
    dns_tool_latency_ms: float | None = None


class Resolver(Protocol):
    def resolve(self, qname: str, rdtype: str, **kwargs: object) -> object: ...


def resolve_record(resolver: Resolver, domain: str, record_type: str) -> DnsRecordResult:
    """Resolve one supported record type into a predictable result object."""
    if record_type not in RECORD_TYPES:
        raise ValueError(f"Unsupported record type: {record_type}")
    try:
        answer = resolver.resolve(domain, record_type, raise_on_no_answer=False)
        if answer.rrset is None:
            return DnsRecordResult(record_type=record_type, status="no_record")
        return DnsRecordResult(
            record_type=record_type,
            status="ok",
            records=[str(record).strip() for record in answer],
        )
    except dns.resolver.NXDOMAIN:
        return DnsRecordResult(
            record_type=record_type, status="nxdomain", error="Domain does not exist"
        )
    except dns.resolver.NoAnswer:
        return DnsRecordResult(record_type=record_type, status="no_record")
    except dns.resolver.LifetimeTimeout:
        return DnsRecordResult(
            record_type=record_type, status="timeout", error="DNS lookup timed out"
        )
    except dns.resolver.NoNameservers:
        return DnsRecordResult(
            record_type=record_type,
            status="resolver_failure",
            error="No responding authoritative nameservers",
        )
    except dns.exception.DNSException as error:
        return DnsRecordResult(record_type=record_type, status="resolver_failure", error=str(error))


class DnsDiagnosticService:
    """Queries public DNS using a standard resolver; no mutation APIs are exposed."""

    def __init__(
        self,
        resolver: Resolver | None = None,
        cache: CacheBackend | None = None,
        cache_ttl_seconds: int = 60,
    ) -> None:
        self.resolver = resolver or dns.resolver.Resolver(configure=True)
        self.cache = cache
        self.cache_ttl_seconds = cache_ttl_seconds

    def check_dns(self, domain: str, tool_execution_id: str | None = None) -> DnsCheckResponse:
        validated_domain = DnsCheckRequest(domain=domain).domain
        execution_id = tool_execution_id or str(uuid4())
        cache_key = f"dns:v1:{validated_domain}:all"
        cache_started = perf_counter()
        if self.cache is not None:
            try:
                cached = self.cache.get(cache_key)
                cache_latency_ms = round((perf_counter() - cache_started) * 1_000, 2)
                if cached:
                    cached_response = DnsCheckResponse.model_validate(decode(cached))
                    cached_response.tool_execution_id = execution_id
                    cached_response.cache_status = "hit"
                    cached_response.cache_latency_ms = cache_latency_ms
                    cached_response.dns_tool_latency_ms = 0.0
                    log_event(
                        LOGGER,
                        "dns_cache",
                        tool_execution_id=execution_id,
                        domain=validated_domain,
                        cache_status="hit",
                        cache_latency_ms=cache_latency_ms,
                    )
                    return cached_response
                log_event(
                    LOGGER,
                    "dns_cache",
                    tool_execution_id=execution_id,
                    domain=validated_domain,
                    cache_status="miss",
                    cache_latency_ms=cache_latency_ms,
                )
            except (CacheUnavailableError, ValueError, TypeError) as error:
                cache_latency_ms = round((perf_counter() - cache_started) * 1_000, 2)
                log_event(
                    LOGGER,
                    "dns_cache",
                    tool_execution_id=execution_id,
                    domain=validated_domain,
                    cache_status="error",
                    cache_latency_ms=cache_latency_ms,
                    error_type=type(error).__name__,
                )
        else:
            cache_latency_ms = None
        start = perf_counter()
        log_event(
            LOGGER, "dns_check_started", tool_execution_id=execution_id, domain=validated_domain
        )
        results = {
            record_type: resolve_record(self.resolver, validated_domain, record_type)
            for record_type in RECORD_TYPES
        }
        duration_ms = round((perf_counter() - start) * 1_000, 2)
        statuses = {result.status for result in results.values()}
        if statuses == {"nxdomain"}:
            overall = "nxdomain"
        elif "timeout" in statuses or "resolver_failure" in statuses:
            overall = (
                "partial_failure"
                if "ok" in statuses or "no_record" in statuses
                else "resolver_failure"
            )
        elif "ok" in statuses:
            overall = "ok"
        else:
            overall = "no_records"
        errors = [
            f"{record_type}: {result.error}"
            for record_type, result in results.items()
            if result.error
        ]
        response = DnsCheckResponse(
            domain=validated_domain,
            status=overall,
            records=results,
            errors=errors,
            lookup_duration_ms=duration_ms,
            tool_execution_id=execution_id,
            cache_status="miss" if self.cache is not None else "bypassed",
            cache_latency_ms=cache_latency_ms,
            dns_tool_latency_ms=duration_ms,
        )
        if self.cache is not None and overall not in {"resolver_failure", "partial_failure"}:
            # Cache deterministic observations (including NXDOMAIN/no-record) briefly; never cache resolver outages.
            try:
                self.cache.set(cache_key, encode(response.model_dump()), self.cache_ttl_seconds)
            except CacheUnavailableError as error:
                log_event(
                    LOGGER,
                    "dns_cache",
                    tool_execution_id=execution_id,
                    domain=validated_domain,
                    cache_status="store_error",
                    error_type=type(error).__name__,
                )
        log_event(
            LOGGER,
            "dns_check_completed",
            tool_execution_id=execution_id,
            domain=validated_domain,
            status=overall,
            lookup_duration_ms=duration_ms,
        )
        return response
