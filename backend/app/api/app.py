"""Phase 2K - the API application factory.

    uvicorn app.api.app:create_app --factory --reload

`create_app()` does no I/O and opens no TigerGraph/LLM connection - it
only builds the FastAPI app, registers routes and exception handlers.
Every external dependency (`TigerGraphClient`, the LLM client, the
`CaseStore`) is created lazily, on first request, by
`app/api/dependencies.py` - see that module's docstring.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import APIError, ErrorCode, InternalError
from app.api.routes import api_router
from app.config import get_settings
from app.logging import configure_logging, get_logger

logger = get_logger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="HHGoa Fraud Investigation API",
        version=settings.app_version,
        description=(
            "Serving boundary over the existing deterministic fraud-investigation "
            "backend and LangGraph agent. This API contains no investigation, "
            "uncertainty, policy, or case-management logic of its own - see "
            "docs/phase-2-api.md."
        ),
    )
    app.include_router(api_router)
    _register_exception_handlers(app)
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def _handle_api_error(_: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's own validation errors describe field name/type/location
        # only - never request body values that could carry sensitive
        # input - so this is safe to pass through, unlike a raw exception.
        safe_errors = [
            {"loc": list(e.get("loc", [])), "type": e.get("type"), "msg": e.get("msg")} for e in exc.errors()
        ]
        payload = {
            "error": {
                "code": ErrorCode.INVALID_REQUEST.value,
                "message": "Request validation failed.",
                "details": {"errors": safe_errors},
            }
        }
        return JSONResponse(status_code=400, content=payload)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Covers framework-level HTTP errors (e.g. an unknown path -> 404)
        # that never went through an APIError subclass, in the same envelope.
        code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.INVALID_REQUEST
        payload = {"error": {"code": code.value, "message": str(exc.detail), "details": {}}}
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        # Never leaks the exception message or a traceback to the client -
        # only a fixed, safe sentence. The real detail goes to the server
        # log only.
        logger.log(logging.ERROR, "unhandled exception in API request", exc_info=exc)
        fallback = InternalError("An internal error occurred.")
        return JSONResponse(status_code=fallback.http_status, content=fallback.to_payload())
