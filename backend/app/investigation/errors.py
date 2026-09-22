"""Phase 2C - normalized tool-layer error classification.

Turns whatever exception a tool's underlying TigerGraph call raised into
one of a small, fixed set of `ToolErrorType` values plus a safe message -
never a raw traceback, never a credential. Reuses
`app.tigergraph.diagnostics.classify_network` for TigerGraph-originated
failures (the same root-cause classification already proven live in
Phase 2B's own health tooling - see docs/phase-2-graph-analysis.md §9)
instead of re-deriving root-cause classification here.

EMPTY_RESULT is listed for completeness (the brief this was built against
names it explicitly) but is never actually produced by this layer: a
query that ran fine and found nothing is `QueryStatus.EMPTY` with no
error at all - collapsing that into an "error" would blur exactly the
distinction Phase 2B exists to preserve (app/evidence/models.py's
QueryStatus docstring).
"""

from __future__ import annotations

from enum import Enum

from app.tigergraph.client import TigerGraphError, TigerGraphQueryError, TigerGraphUnavailable
from app.tigergraph.diagnostics import classify_network


class ToolErrorType(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    EMPTY_RESULT = "EMPTY_RESULT"  # reserved; see module docstring - never emitted
    QUERY_ERROR = "QUERY_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    TIMEOUT = "TIMEOUT"
    TIGERGRAPH_UNAVAILABLE = "TIGERGRAPH_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# app.tigergraph.diagnostics.CATEGORIES -> ToolErrorType. GRAPH maps to
# QUERY_ERROR (the named graph itself is a query-time concern from a
# tool's point of view, not a connectivity one); SDK maps to
# INTERNAL_ERROR (pyTigerGraph raised something this project didn't
# anticipate, not a TigerGraph-side failure).
_CATEGORY_TO_ERROR_TYPE: dict[str, ToolErrorType] = {
    "AUTHENTICATION": ToolErrorType.AUTHENTICATION_ERROR,
    "NETWORK": ToolErrorType.TIGERGRAPH_UNAVAILABLE,
    "SERVER": ToolErrorType.TIGERGRAPH_UNAVAILABLE,
    "ENDPOINT": ToolErrorType.TIGERGRAPH_UNAVAILABLE,
    "GRAPH": ToolErrorType.QUERY_ERROR,
    "SDK": ToolErrorType.INTERNAL_ERROR,
    "CONFIGURATION": ToolErrorType.TIGERGRAPH_UNAVAILABLE,
}


def classify_exception(host: str, exc: Exception) -> tuple[ToolErrorType, str]:
    """Normalize any exception raised while executing a tool's TigerGraph
    call into a (ToolErrorType, safe message) pair. Never raises."""
    if isinstance(exc, TigerGraphUnavailable):
        category, detail = classify_network(host, exc)
        return _CATEGORY_TO_ERROR_TYPE.get(category, ToolErrorType.TIGERGRAPH_UNAVAILABLE), detail

    if isinstance(exc, TigerGraphQueryError):
        message = str(exc)
        if "does not exist" in message.lower():
            return ToolErrorType.NOT_FOUND, message
        return ToolErrorType.QUERY_ERROR, message

    if isinstance(exc, TigerGraphError):
        return ToolErrorType.TIGERGRAPH_UNAVAILABLE, str(exc)

    return ToolErrorType.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"
