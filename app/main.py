from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.conversations import router as conversations_router
from app.api.routes.diagnose import router as diagnose_router
from app.api.routes.dns import router as dns_router
from app.api.routes.escalations import router as escalations_router
from app.api.routes.health import router as health_router
from app.api.routes.search import router as search_router
from app.core.config import get_settings
from app.core.logging import configure_logging


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    get_settings().validate_production()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Evidence-grounded support copilot API.",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(search_router)
    app.include_router(dns_router)
    app.include_router(diagnose_router)
    app.include_router(conversations_router)
    app.include_router(escalations_router)
    return app


app = create_app()
