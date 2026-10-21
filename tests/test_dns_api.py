from fastapi.testclient import TestClient

from app.api.routes import dns as dns_route
from app.main import create_app
from app.tools.dns import DnsDiagnosticService
from tests.test_dns import FakeAnswer, FakeResolver


def test_dns_endpoint_returns_structured_response(monkeypatch) -> None:
    service = DnsDiagnosticService(FakeResolver({"A": FakeAnswer(["203.0.113.10"])}))
    monkeypatch.setattr(dns_route, "get_dns_service", lambda: service)
    client = TestClient(create_app())

    response = client.post("/api/v1/tools/dns-check", json={"domain": "example.com"})

    assert response.status_code == 200
    body = response.json()
    assert body["domain"] == "example.com"
    assert body["records"]["A"]["records"] == ["203.0.113.10"]
    assert "lookup_duration_ms" in body
    assert "tool_execution_id" in body


def test_dns_endpoint_rejects_invalid_domain_without_lookup() -> None:
    client = TestClient(create_app())

    response = client.post("/api/v1/tools/dns-check", json={"domain": "not a domain"})

    assert response.status_code == 422
