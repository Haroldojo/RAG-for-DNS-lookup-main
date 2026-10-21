from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import diagnose as diagnose_route
from app.cache.rate_limit import FixedWindowRateLimiter
from app.database.models import Base
from app.database.service import PersistenceService
from app.main import create_app
from tests.test_cache import MemoryCache
from tests.test_diagnosis_graph import workflow


def test_diagnose_endpoint_exposes_intermediate_results(monkeypatch) -> None:
    graph = workflow()
    monkeypatch.setattr(diagnose_route, "get_workflow", lambda: graph)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    persistence = PersistenceService(sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(diagnose_route, "get_persistence_service", lambda: persistence)

    response = TestClient(create_app()).post(
        "/api/v1/diagnose",
        json={"query": "My domain is not resolving", "domain": "example.com"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["route_taken"] == "dns_and_rag"
    assert body["tools_called"] == ["dns_check", "search_support_docs"]
    assert body["dns_result"]["status"] == "ok"
    assert body["conversation_id"]
    assert body["diagnosis_run_id"]


def test_diagnose_endpoint_rejects_blank_query() -> None:
    response = TestClient(create_app()).post("/api/v1/diagnose", json={"query": " "})

    assert response.status_code == 422


def test_history_endpoints_expose_persisted_run(monkeypatch) -> None:
    graph = workflow()
    monkeypatch.setattr(diagnose_route, "get_workflow", lambda: graph)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    persistence = PersistenceService(sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(diagnose_route, "get_persistence_service", lambda: persistence)

    from app.api.routes import conversations as conversations_route

    monkeypatch.setattr(conversations_route, "get_persistence_service", lambda: persistence)
    client = TestClient(create_app())
    result = client.post("/api/v1/diagnose", json={"query": "How do I create an A record?"}).json()

    messages = client.get(f"/api/v1/conversations/{result['conversation_id']}/messages")
    detail = client.get(f"/api/v1/diagnosis/{result['diagnosis_run_id']}")

    assert messages.status_code == 200 and [item["role"] for item in messages.json()] == [
        "user",
        "assistant",
    ]
    assert detail.status_code == 200
    assert detail.json()["citations"][0]["document_id"] == "dns-basics-v1"


def test_diagnose_rejects_unknown_conversation_before_graph(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    persistence = PersistenceService(sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(diagnose_route, "get_persistence_service", lambda: persistence)

    response = TestClient(create_app()).post(
        "/api/v1/diagnose",
        json={"query": "How do I create an A record?", "conversation_id": "missing"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "conversation_not_found"


def test_diagnose_rate_limit_returns_429(monkeypatch) -> None:
    graph = workflow()
    monkeypatch.setattr(diagnose_route, "get_workflow", lambda: graph)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    persistence = PersistenceService(sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(diagnose_route, "get_persistence_service", lambda: persistence)
    limiter = FixedWindowRateLimiter(MemoryCache(), limit=1, window_seconds=60)
    monkeypatch.setattr(diagnose_route, "get_rate_limiter", lambda: limiter)
    client = TestClient(create_app())

    assert (
        client.post("/api/v1/diagnose", json={"query": "How do I create an A record?"}).status_code
        == 200
    )
    response = client.post("/api/v1/diagnose", json={"query": "How do I create an A record?"})

    assert response.status_code == 429
    assert response.headers["retry-after"]
    assert response.json()["detail"]["code"] == "rate_limit_exceeded"
