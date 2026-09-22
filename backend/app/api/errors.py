"""Phase 2K - the API's fixed error vocabulary.

Every error this API returns is one of the `ErrorCode` values below,
wrapped in the same envelope:

    {"error": {"code": "...", "message": "...", "details": {...}}}

`message` is always a safe, human-readable sentence - never a raw
exception string, traceback, or anything from `app.evidence.Provenance`
that could carry a query parameter verbatim. `APIError` and its
subclasses are the only exceptions route handlers are expected to raise
deliberately; `app.py`'s exception handlers also catch any other
unhandled exception and map it to `INTERNAL_ERROR` without leaking its
message.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    INVESTIGATION_FAILED = "INVESTIGATION_FAILED"
    AGENT_FAILED = "AGENT_FAILED"
    INVESTIGATION_LIMIT_REACHED = "INVESTIGATION_LIMIT_REACHED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class APIError(Exception):
    """Base class for every deliberately-raised API error. `http_status`
    is chosen per subclass below; never 2xx."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    http_status: int = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {"error": {"code": self.code.value, "message": self.message, "details": self.details}}


class InvalidRequestError(APIError):
    code = ErrorCode.INVALID_REQUEST
    http_status = 400


class NotFoundError(APIError):
    code = ErrorCode.NOT_FOUND
    http_status = 404


class InvestigationFailedError(APIError):
    """The deterministic backend (InvestigationService/UncertaintyEngine/
    PolicyEngine/CaseManager) raised - `AgentStatus.FAILED` for a reason
    other than the LLM call itself."""

    code = ErrorCode.INVESTIGATION_FAILED
    http_status = 502


class AgentFailedError(APIError):
    """The LLM call itself failed or returned unparseable output
    (`MalformedLLMOutputError`) - distinct from a deterministic-backend
    failure, matching orchestrator.py's own FAILED-cause distinction."""

    code = ErrorCode.AGENT_FAILED
    http_status = 502


class DependencyUnavailableError(APIError):
    """A required dependency (TigerGraph, the configured LLM) is not
    reachable or not configured - never silently substituted."""

    code = ErrorCode.DEPENDENCY_UNAVAILABLE
    http_status = 503


class InternalError(APIError):
    code = ErrorCode.INTERNAL_ERROR
    http_status = 500
