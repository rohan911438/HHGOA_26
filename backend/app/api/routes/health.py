"""Phase 2K - health endpoints.

`GET /health` is application-level only - no dependency call, always
fast, always 200 while the process is up. `GET /health/dependencies`
actually probes TigerGraph (via `TigerGraphClient.health()`, Phase 1,
unmodified - never raises, never leaks a credential) and reports whether
an LLM is configured (not connectivity-tested - a real LLM call costs
money/time and this project already treats "configured" vs. "healthy"
as different questions for TigerGraph too; the response says exactly
which it means for each dependency, never blurs the two).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_settings_dependency, get_tigergraph_client
from app.api.models import DependencyStatus, DetailedHealthResponse, HealthResponse
from app.config import Settings
from app.tigergraph.client import TigerGraphClient

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="hhgoa-fraud-agent")


@router.get("/health/dependencies", response_model=DetailedHealthResponse)
def health_dependencies(
    settings: Settings = Depends(get_settings_dependency),
    client: TigerGraphClient = Depends(get_tigergraph_client),
) -> DetailedHealthResponse:
    tg_status = client.health()
    dependencies = [
        DependencyStatus(
            name="tigergraph",
            checked=True,
            healthy=tg_status.connected,
            detail=tg_status.error or "reachable",
            latency_ms=tg_status.latency_ms,
        ),
        DependencyStatus(
            name="llm",
            checked=True,
            healthy=settings.llm_configured,
            detail=(
                "configured (connectivity not probed)"
                if settings.llm_configured
                else "not configured - OPENAI_API_KEY/OPENAI_MODEL unset"
            ),
        ),
    ]
    overall = "ok" if all(d.healthy for d in dependencies) else "degraded"
    return DetailedHealthResponse(status=overall, service="hhgoa-fraud-agent", dependencies=dependencies)
