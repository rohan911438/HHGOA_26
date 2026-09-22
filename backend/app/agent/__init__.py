"""Phase 2H - the agent orchestrator: a LangGraph workflow composing the
existing deterministic backend (Phase 2D-2G, unmodified) with a narrow
LLM abstraction. The agent orchestrates; it does not replace
InvestigationService, UncertaintyEngine, PolicyEngine, or CaseManager -
see orchestrator.py's module docstring for the exact boundary.
"""

from __future__ import annotations

from app.agent.llm import (
    FakeLLMClient,
    LLMClient,
    LLMDecision,
    LLMDecisionAction,
    LLMNotConfiguredError,
    MalformedLLMClient,
    MalformedLLMOutputError,
    OpenAIChatClient,
    parse_llm_decision,
)
from app.agent.orchestrator import AgentOrchestrator
from app.agent.state import (
    AgentEventType,
    AgentInvestigationResult,
    AgentMessage,
    AgentState,
    AgentStatus,
    RequestedEvidence,
    RequestedEvidenceStatus,
    RequestedEvidenceType,
)

__all__ = [
    "AgentEventType",
    "AgentInvestigationResult",
    "AgentMessage",
    "AgentOrchestrator",
    "AgentState",
    "AgentStatus",
    "FakeLLMClient",
    "LLMClient",
    "LLMDecision",
    "LLMDecisionAction",
    "LLMNotConfiguredError",
    "MalformedLLMClient",
    "MalformedLLMOutputError",
    "OpenAIChatClient",
    "RequestedEvidence",
    "RequestedEvidenceStatus",
    "RequestedEvidenceType",
    "parse_llm_decision",
]
