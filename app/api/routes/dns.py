from fastapi import APIRouter, HTTPException

from app.cache.redis_cache import get_cache_backend
from app.core.config import get_settings
from app.tools.dns import DnsCheckRequest, DnsCheckResponse, DnsDiagnosticService

router = APIRouter(prefix="/api/v1/tools", tags=["diagnostics"])


def get_dns_service() -> DnsDiagnosticService:
    settings = get_settings()
    return DnsDiagnosticService(
        cache=get_cache_backend(), cache_ttl_seconds=settings.dns_cache_ttl_seconds
    )


@router.post("/dns-check", response_model=DnsCheckResponse, summary="Run read-only DNS diagnostics")
async def check_dns(request: DnsCheckRequest) -> DnsCheckResponse:
    try:
        return get_dns_service().check_dns(request.domain)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
