"""Phase 2K - the HTTP serving boundary over the existing agent/backend.

    Frontend -> HTTP API -> Agent/LangGraph -> deterministic backend -> TigerGraph/CaseMemory

This package contains no investigation, uncertainty, policy, or
case-management logic - every route calls the existing application layer
(`AgentOrchestrator`, `CaseManager`, `CaseMemory`) and reshapes its
already-typed output. See docs/phase-2-api.md for the full design.
"""

from __future__ import annotations

from app.api.app import create_app

__all__ = ["create_app"]
