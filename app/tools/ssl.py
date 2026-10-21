"""Read-only TLS certificate inspection for public domains."""

from __future__ import annotations

import socket
import ssl
from datetime import UTC, datetime
from time import perf_counter

from pydantic import BaseModel

from app.cache.redis_cache import CacheBackend, CacheUnavailableError, decode, encode
from app.tools.dns import DnsCheckRequest


class SslCheckResponse(BaseModel):
    domain: str
    connection_status: str
    certificate_present: bool
    certificate_valid: bool | None
    issuer: str | None = None
    subject: str | None = None
    hostname_match: bool | None = None
    valid_from: str | None = None
    expires_at: str | None = None
    days_until_expiry: int | None = None
    error_type: str | None = None
    latency_ms: float
    cache_status: str = "bypassed"


def _name(value: object) -> str | None:
    if not value:
        return None
    parts = [f"{key}={item}" for group in value for key, item in group]
    return ", ".join(parts) or None


class SslDiagnosticService:
    def __init__(
        self,
        cache: CacheBackend | None = None,
        cache_ttl_seconds: int = 300,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.cache = cache
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout_seconds = timeout_seconds

    def check_ssl(self, domain: str) -> SslCheckResponse:
        validated = DnsCheckRequest(domain=domain).domain
        key = f"ssl:v1:{validated}"
        if self.cache is not None:
            try:
                cached = self.cache.get(key)
                if cached:
                    result = SslCheckResponse.model_validate(decode(cached))
                    result.cache_status = "hit"
                    return result
            except (CacheUnavailableError, ValueError, TypeError):
                pass
        started = perf_counter()
        try:
            context = ssl.create_default_context()
            with (
                socket.create_connection(
                    (validated, 443), timeout=self.timeout_seconds
                ) as raw_socket,
                context.wrap_socket(raw_socket, server_hostname=validated) as tls_socket,
            ):
                certificate = tls_socket.getpeercert()
            now = datetime.now(UTC)
            valid_from = datetime.strptime(
                certificate["notBefore"], "%b %d %H:%M:%S %Y %Z"
            ).replace(tzinfo=UTC)
            expires_at = datetime.strptime(certificate["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(
                tzinfo=UTC
            )
            result = SslCheckResponse(
                domain=validated,
                connection_status="success",
                certificate_present=True,
                certificate_valid=valid_from <= now <= expires_at,
                issuer=_name(certificate.get("issuer")),
                subject=_name(certificate.get("subject")),
                hostname_match=True,
                valid_from=valid_from.isoformat(),
                expires_at=expires_at.isoformat(),
                days_until_expiry=(expires_at - now).days,
                latency_ms=round((perf_counter() - started) * 1_000, 2),
                cache_status="miss" if self.cache else "bypassed",
            )
        except ssl.SSLCertVerificationError as error:
            message = str(error).lower()
            error_type = (
                "hostname_mismatch"
                if "hostname" in message
                else "expired_certificate"
                if "expired" in message
                else "certificate_not_yet_valid"
                if "not yet valid" in message
                else "certificate_verification_failed"
            )
            result = SslCheckResponse(
                domain=validated,
                connection_status="failed",
                certificate_present=True,
                certificate_valid=False,
                hostname_match=False if error_type == "hostname_mismatch" else None,
                error_type=error_type,
                latency_ms=round((perf_counter() - started) * 1_000, 2),
                cache_status="miss" if self.cache else "bypassed",
            )
        except socket.gaierror:
            result = SslCheckResponse(
                domain=validated,
                connection_status="failed",
                certificate_present=False,
                certificate_valid=None,
                error_type="dns_failure",
                latency_ms=round((perf_counter() - started) * 1_000, 2),
                cache_status="miss" if self.cache else "bypassed",
            )
        except TimeoutError:
            result = SslCheckResponse(
                domain=validated,
                connection_status="failed",
                certificate_present=False,
                certificate_valid=None,
                error_type="connection_timeout",
                latency_ms=round((perf_counter() - started) * 1_000, 2),
                cache_status="miss" if self.cache else "bypassed",
            )
        except ConnectionRefusedError:
            result = SslCheckResponse(
                domain=validated,
                connection_status="failed",
                certificate_present=False,
                certificate_valid=None,
                error_type="connection_refused",
                latency_ms=round((perf_counter() - started) * 1_000, 2),
                cache_status="miss" if self.cache else "bypassed",
            )
        except KeyError:
            result = SslCheckResponse(
                domain=validated,
                connection_status="failed",
                certificate_present=True,
                certificate_valid=None,
                error_type="malformed_certificate",
                latency_ms=round((perf_counter() - started) * 1_000, 2),
                cache_status="miss" if self.cache else "bypassed",
            )
        except (ssl.SSLError, ValueError):
            result = SslCheckResponse(
                domain=validated,
                connection_status="failed",
                certificate_present=False,
                certificate_valid=None,
                error_type="tls_handshake_failure",
                latency_ms=round((perf_counter() - started) * 1_000, 2),
                cache_status="miss" if self.cache else "bypassed",
            )
        if self.cache is not None and result.error_type not in {
            "connection_timeout",
            "connection_refused",
            "dns_failure",
        }:
            try:
                self.cache.set(key, encode(result.model_dump()), self.cache_ttl_seconds)
            except CacheUnavailableError:
                pass
        return result
