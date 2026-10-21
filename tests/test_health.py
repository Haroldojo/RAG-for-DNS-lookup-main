from fastapi.testclient import TestClient

from app.main import create_app


def test_health_reports_liveness() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "Agentic DNS & Hosting Support Copilot"


def test_ready_reports_application_check() -> None:
    client = TestClient(create_app())

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"application": "ok"}}
