import socket
import ssl

import httpx
import pytest

from app.tools.http import HttpDiagnosticService
from app.tools.ssl import SslDiagnosticService
from tests.test_cache import MemoryCache


class RawSocket:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TlsSocket(RawSocket):
    def getpeercert(self):
        return {
            "notBefore": "Jan 01 00:00:00 2025 GMT",
            "notAfter": "Jan 01 00:00:00 2030 GMT",
            "issuer": ((("commonName", "Test CA"),),),
            "subject": ((("commonName", "example.com"),),),
        }


class Context:
    def wrap_socket(self, raw, server_hostname):
        return TlsSocket()


def test_valid_certificate(monkeypatch) -> None:
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: RawSocket())
    monkeypatch.setattr(ssl, "create_default_context", lambda: Context())

    result = SslDiagnosticService().check_ssl("example.com")

    assert result.connection_status == "success"
    assert result.certificate_valid is True
    assert result.hostname_match is True


def test_ssl_and_http_cache_hits(monkeypatch) -> None:
    cache = MemoryCache()
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: RawSocket())
    monkeypatch.setattr(ssl, "create_default_context", lambda: Context())
    ssl_service = SslDiagnosticService(cache=cache)
    assert ssl_service.check_ssl("example.com").cache_status == "miss"
    assert ssl_service.check_ssl("example.com").cache_status == "hit"
    public_dns(monkeypatch)
    service = HttpDiagnosticService(
        cache=cache,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request)),
            follow_redirects=False,
        ),
    )
    assert service.check_http("example.com").cache_status == "miss"
    assert service.check_http("example.com").cache_status == "hit"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("certificate has expired", "expired_certificate"),
        ("hostname mismatch", "hostname_mismatch"),
        ("certificate is not yet valid", "certificate_not_yet_valid"),
    ],
)
def test_certificate_validation_failures(monkeypatch, message: str, expected: str) -> None:
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: RawSocket())

    class FailingContext:
        def wrap_socket(self, raw, server_hostname):
            raise ssl.SSLCertVerificationError(1, message)

    monkeypatch.setattr(ssl, "create_default_context", lambda: FailingContext())
    result = SslDiagnosticService().check_ssl("example.com")

    assert result.error_type == expected


def test_ssl_timeout_and_dns_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        socket, "create_connection", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError())
    )
    assert SslDiagnosticService().check_ssl("example.com").error_type == "connection_timeout"
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(socket.gaierror()),
    )
    assert SslDiagnosticService().check_ssl("example.com").error_type == "dns_failure"


def public_dns(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))],
    )


def http_service(status: int, headers: dict[str, str] | None = None) -> HttpDiagnosticService:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, headers=headers or {}, request=request)
    )
    return HttpDiagnosticService(client=httpx.Client(transport=transport, follow_redirects=False))


@pytest.mark.parametrize("code", [200, 404, 500, 502, 503])
def test_http_statuses(monkeypatch, code: int) -> None:
    public_dns(monkeypatch)
    result = http_service(code, {"content-type": "text/html"}).check_http("example.com")

    assert result.status_code == code
    assert result.error_type is None


def test_http_redirect(monkeypatch) -> None:
    public_dns(monkeypatch)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return (
            httpx.Response(301, headers={"location": "https://www.example.com"}, request=request)
            if calls["count"] == 1
            else httpx.Response(200, request=request)
        )

    result = HttpDiagnosticService(
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    ).check_http("example.com")

    assert result.status_code == 200 and result.redirect_count == 1


def test_http_timeout_and_connection_failure(monkeypatch) -> None:
    public_dns(monkeypatch)
    timeout = HttpDiagnosticService(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ConnectTimeout("timeout"))
            ),
            follow_redirects=False,
        )
    )
    failure = HttpDiagnosticService(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused"))
            ),
            follow_redirects=False,
        )
    )

    assert timeout.check_http("example.com").error_type == "timeout"
    assert failure.check_http("example.com").error_type == "connection_failure"


@pytest.mark.parametrize(
    "target",
    [
        "http://localhost",
        "http://127.0.0.1",
        "http://10.0.0.1",
        "http://169.254.169.254",
        "ftp://example.com",
    ],
)
def test_ssrf_targets_are_blocked(target: str) -> None:
    result = HttpDiagnosticService().check_http(target)

    assert result.error_type == "ssrf_blocked"
