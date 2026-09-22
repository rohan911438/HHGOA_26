"""Phase 2C - the investigation tool registry.

    LangGraph Agent (future)
            v
    Investigation Tool Registry     (registry.py)
            v
    Six approved investigation tools (tools.py)
            v
    Phase 2A TigerGraph queries + Phase 2B evidence normalization
            v
    TigerGraph

The only import a future agent needs from this package is
`build_default_registry` - it should never import
`app.tigergraph.queries` or `app.tigergraph.client` directly. See
docs/phase-2-tool-registry.md for the full design writeup.
"""

from __future__ import annotations

from app.investigation.registry import ToolNotRegisteredError, ToolRegistry, build_default_registry
from app.investigation.schemas import InvestigationToolResult, ToolError
from app.investigation.service import InvestigationService
from app.investigation.snapshot import InvestigationSnapshot, InvestigationStatus

__all__ = [
    "InvestigationService",
    "InvestigationSnapshot",
    "InvestigationStatus",
    "InvestigationToolResult",
    "ToolError",
    "ToolNotRegisteredError",
    "ToolRegistry",
    "build_default_registry",
]
