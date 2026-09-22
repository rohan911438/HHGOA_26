"""Phase 2I - the fraud-investigation GraphRAG / context layer.

    Current Evidence (Phase 2B) + Historical CaseMemory (Phase 2G)
    + Uncertainty (Phase 2E) + Policy (Phase 2F, optional)
            v
    ContextBuilder.build() / build_for_case()
            v
    InvestigationContext
            v
    format_context_for_prompt()   -> versioned, deterministic dict for the agent

Structured retrieval, not a vector-RAG system - no embeddings, no vector
database (Phase 2I §4). No TigerGraph, no LLM in this package - see
builder.py's module docstring.
"""

from __future__ import annotations

from app.context.builder import ContextBuilder, ContextLimits
from app.context.formatter import format_context_for_prompt
from app.context.models import (
    CONTEXT_FORMAT_VERSION,
    ContextItem,
    ContextItemType,
    InvestigationContext,
)

__all__ = [
    "CONTEXT_FORMAT_VERSION",
    "ContextBuilder",
    "ContextItem",
    "ContextItemType",
    "ContextLimits",
    "InvestigationContext",
    "format_context_for_prompt",
]
