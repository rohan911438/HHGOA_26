"""Phase 2K - route aggregation. `app/api/app.py` mounts `api_router`;
individual route modules never register themselves on the app directly."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import cases, health, investigations

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(investigations.router)
api_router.include_router(cases.router)

__all__ = ["api_router"]
