from fastapi import APIRouter, Depends
from pydantic import BaseModel
from qdrant_client import QdrantClient
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings, get_settings
from app.observability.runtime import event, metrics

router = APIRouter(tags=["service"])


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, str]


def dependency_checks(settings: Settings) -> dict[str, str]:
    """Dependencies are advisory; this endpoint never exposes their connection details."""
    checks = {"application": "ok"}
    dependencies = (
        (
            "postgresql",
            settings.database_url,
            lambda: create_engine(settings.database_url).connect().execute(text("SELECT 1")),
        ),
        (
            "redis",
            settings.redis_url,
            lambda: Redis.from_url(
                settings.redis_url, socket_timeout=settings.redis_socket_timeout_seconds
            ).ping(),
        ),
        (
            "qdrant",
            settings.qdrant_url,
            lambda: QdrantClient(
                url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=1
            ).get_collections(),
        ),
    )
    for name, configured, check in dependencies:
        if not configured:
            continue
        try:
            check()
            checks[name] = "ok"
        except (RedisError, SQLAlchemyError, OSError, RuntimeError, ValueError):
            checks[name] = "unavailable"
    if settings.groq_api_key:
        checks["llm_provider"] = "configured"
    return checks


@router.get("/health", response_model=HealthResponse, summary="Application liveness check")
async def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Report whether this application process is alive; no dependencies are queried."""
    return HealthResponse(status="ok", service=settings.app_name, environment=settings.app_env)


@router.get("/ready", response_model=ReadinessResponse, summary="Application readiness check")
async def readiness() -> ReadinessResponse:
    """Expose a degraded state without turning the liveness probe into a restart trigger."""
    settings = get_settings()
    checks = dependency_checks(settings)
    status = (
        "ready" if all(value in {"ok", "configured"} for value in checks.values()) else "degraded"
    )
    event("health", "readiness", status, dependency_checks=checks)
    return ReadinessResponse(status=status, checks=checks)


@router.get("/metrics", summary="Safe in-process operational metrics")
async def get_metrics() -> dict[str, object]:
    return metrics.snapshot()
