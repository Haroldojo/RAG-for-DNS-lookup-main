"""Bounded read-only HTTP status checks against public targets only."""

from __future__ import annotations

from time import perf_counter
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel

from app.cache.redis_cache import CacheBackend, CacheUnavailableError, decode, encode
from app.observability.runtime import event
from app.tools.network_safety import UnsafeTargetError, normalize_public_url


class HttpCheckResponse(BaseModel):
    requested_url: str
    final_url: str | None
    status_code: int | None
    redirect_count: int
    response_latency_ms: float
    content_type: str | None
    https_used: bool
    error_type: str | None = None
    cache_status: str = "bypassed"


class HttpDiagnosticService:
    def __init__(
        self,
        cache: CacheBackend | None = None,
        cache_ttl_seconds: int = 30,
        timeout_seconds: float = 5.0,
        max_redirects: int = 3,
        client: httpx.Client | None = None,
    ) -> None:
        self.cache = cache
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout_seconds = timeout_seconds
        self.max_redirects = max_redirects
        self.client = client or httpx.Client(
            follow_redirects=False,
            timeout=timeout_seconds,
            headers={"User-Agent": "support-copilot-diagnostic/1.0"},
            trust_env=False,
        )

    def check_http(self, url_or_domain: str) -> HttpCheckResponse:
        started = perf_counter()
        try:
            requested = normalize_public_url(url_or_domain)
        except UnsafeTargetError as error:
            event("security", "ssrf_target", "blocked", error_type="ssrf_blocked")
            return HttpCheckResponse(
                requested_url=url_or_domain,
                final_url=None,
                status_code=None,
                redirect_count=0,
                response_latency_ms=round((perf_counter() - started) * 1_000, 2),
                content_type=None,
                https_used=False,
                error_type="dns_failure"
                if str(error) == "dns_resolution_failed"
                else "ssrf_blocked",
            )
        key = f"http:v1:{requested}"
        if self.cache is not None:
            try:
                cached = self.cache.get(key)
                if cached:
                    result = HttpCheckResponse.model_validate(decode(cached))
                    result.cache_status = "hit"
                    return result
            except (CacheUnavailableError, ValueError, TypeError):
                pass
        current = requested
        redirects = 0
        try:
            while True:
                response = self.client.request("GET", current, headers={"Range": "bytes=0-0"})
                if (
                    response.status_code not in {301, 302, 303, 307, 308}
                    or "location" not in response.headers
                ):
                    result = HttpCheckResponse(
                        requested_url=requested,
                        final_url=str(response.url),
                        status_code=response.status_code,
                        redirect_count=redirects,
                        response_latency_ms=round((perf_counter() - started) * 1_000, 2),
                        content_type=response.headers.get("content-type"),
                        https_used=response.url.scheme == "https",
                        cache_status="miss" if self.cache else "bypassed",
                    )
                    break
                redirects += 1
                if redirects > self.max_redirects:
                    result = HttpCheckResponse(
                        requested_url=requested,
                        final_url=current,
                        status_code=response.status_code,
                        redirect_count=redirects,
                        response_latency_ms=round((perf_counter() - started) * 1_000, 2),
                        content_type=response.headers.get("content-type"),
                        https_used=current.startswith("https://"),
                        error_type="redirect_limit_exceeded",
                        cache_status="miss" if self.cache else "bypassed",
                    )
                    break
                current = normalize_public_url(urljoin(current, response.headers["location"]))
        except UnsafeTargetError:
            event("security", "ssrf_redirect", "blocked", error_type="ssrf_blocked")
            result = HttpCheckResponse(
                requested_url=requested,
                final_url=current,
                status_code=None,
                redirect_count=redirects,
                response_latency_ms=round((perf_counter() - started) * 1_000, 2),
                content_type=None,
                https_used=current.startswith("https://"),
                error_type="ssrf_blocked",
                cache_status="miss" if self.cache else "bypassed",
            )
        except httpx.ConnectTimeout:
            result = HttpCheckResponse(
                requested_url=requested,
                final_url=None,
                status_code=None,
                redirect_count=redirects,
                response_latency_ms=round((perf_counter() - started) * 1_000, 2),
                content_type=None,
                https_used=requested.startswith("https://"),
                error_type="timeout",
                cache_status="miss" if self.cache else "bypassed",
            )
        except httpx.ConnectError as error:
            text = str(error).lower()
            error_type = (
                "tls_failure" if "certificate" in text or "ssl" in text else "connection_failure"
            )
            result = HttpCheckResponse(
                requested_url=requested,
                final_url=None,
                status_code=None,
                redirect_count=redirects,
                response_latency_ms=round((perf_counter() - started) * 1_000, 2),
                content_type=None,
                https_used=requested.startswith("https://"),
                error_type=error_type,
                cache_status="miss" if self.cache else "bypassed",
            )
        if self.cache is not None and result.error_type not in {
            "timeout",
            "connection_failure",
            "tls_failure",
        }:
            try:
                self.cache.set(key, encode(result.model_dump()), self.cache_ttl_seconds)
            except CacheUnavailableError:
                pass
        return result
