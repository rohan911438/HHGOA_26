"""Phase 2H - the agent's typed, checkpoint-friendly state.

`AgentState` holds only data - the deterministic Pydantic models this
project already defines (`InvestigationSnapshot`, `UncertaintyAssessment`,
`PolicyDecision`, `CaseRecord`, `SimilarCaseResult`), plus a handful of
plain values (`str`, `int`, `bool`, enums, lists of small records). No
service object (`TigerGraphClient`, `CaseManager`, `LLMClient`, ...)
is ever stored in state - those are orchestrator dependencies, not data
(see orchestrator.py). This keeps `AgentState` JSON-serializable end to
end, suitable for LangGraph checkpointing.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.case.models import CaseRecord, CaseTrigger, SimilarCaseResult
from app.context.models import InvestigationContext
from app.evidence.models import Evidence
from app.investigation.snapshot import InvestigationSnapshot
from app.policy.models import PolicyDecision
from app.uncertainty.models import UncertaintyAssessment


class AgentStatus(str, Enum):
    """Never a fraud verdict - describes only how the orchestration run
    itself ended."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    LIMIT_REACHED = "LIMIT_REACHED"  # max_iterations hit - see orchestrator.py §19
    FAILED = "FAILED"  # a deterministic backend or LLM call failed - see orchestrator.py §20


class AgentEventType(str, Enum):
    """Structured observability events (Phase 2H §26). Never carries a
    secret or credential - only IDs/metadata (app.logging's redaction
    convention, reused in spirit here)."""

    AGENT_STARTED = "AGENT_STARTED"
    TOOL_REQUESTED = "TOOL_REQUESTED"
    TOOL_COMPLETED = "TOOL_COMPLETED"
    UNCERTAINTY_ASSESSED = "UNCERTAINTY_ASSESSED"
    CONTEXT_BUILT = "CONTEXT_BUILT"  # Phase 2I
    POLICY_EVALUATED = "POLICY_EVALUATED"
    CASE_UPDATED = "CASE_UPDATED"
    EVIDENCE_REQUESTED = "EVIDENCE_REQUESTED"
    AGENT_FINISHED = "AGENT_FINISHED"
    AGENT_FAILED = "AGENT_FAILED"


class AgentMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    event: AgentEventType
    message: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class RequestedEvidenceType(str, Enum):
    """Only evidence sources this project can actually represent as a
    controlled stub - never an invented source. None of these are
    fulfilled by a real adapter this phase (Phase 2H §7/§21): requesting
    one only records that a request was made, never that the evidence
    was obtained."""

    CUSTOMER_VALIDATION = "CUSTOMER_VALIDATION"
    STEP_UP_AUTHENTICATION = "STEP_UP_AUTHENTICATION"
    APPROVED_PARTY_REQUEST = "APPROVED_PARTY_REQUEST"
    ANALYST_REVIEW = "ANALYST_REVIEW"


class RequestedEvidenceStatus(str, Enum):
    REQUESTED = "REQUESTED"  # the only status this phase ever produces - no fulfillment adapter exists yet


class RequestedEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    requested_evidence_type: RequestedEvidenceType
    reason: str
    uncertainty_factor: str  # which UncertaintyAssessment fact motivated this - deterministically derived, never invented
    approval_required: bool = False
    status: RequestedEvidenceStatus = RequestedEvidenceStatus.REQUESTED
    requested_at: datetime


class AgentState(TypedDict, total=False):
    """LangGraph state. Every node function receives and returns a
    partial update to this shape - see orchestrator.py."""

    case_id: str | None
    transaction_id: str
    trigger: CaseTrigger

    investigation_snapshot: InvestigationSnapshot | None
    uncertainty_assessment: UncertaintyAssessment | None
    policy_decision: PolicyDecision | None
    case_record: CaseRecord | None
    retrieved_historical_cases: list[SimilarCaseResult]
    context: InvestigationContext | None  # Phase 2I - the grounded GraphRAG/context bundle shown to the LLM

    findings: list[str]  # deterministic Evidence.observation strings - never LLM-authored facts
    agent_messages: list[AgentMessage]
    requested_evidence: list[RequestedEvidence]

    iteration_count: int
    max_iterations: int
    tool_call_count: int
    max_tool_calls: int

    status: AgentStatus
    final_explanation: str | None
    error: str | None

    # The LLM's most recent control-flow decision, broken into plain
    # fields (not the `LLMDecision` Pydantic model) so this module never
    # has to import app.agent.llm - that module imports from here
    # (RequestedEvidenceType), and a state->llm import back would be
    # circular. The router (orchestrator.py) reads these directly.
    last_decision_action: str | None
    last_decision_evidence_type: RequestedEvidenceType | None
    last_decision_reason: str | None


class AgentInvestigationResult(BaseModel):
    """The final, structured, JSON-serializable output -
    API/frontend-ready, per Phase 2H §13. No `fraud_verdict` or
    `final_fraud_score` field exists here, by design, matching every
    prior phase's model."""

    model_config = ConfigDict(frozen=True)

    case_id: str | None
    transaction_id: str
    investigation_status: AgentStatus

    findings: list[str] = Field(default_factory=list)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    # Phase 2K - the raw Evidence items backing `findings`/`evidence_summary`,
    # exposed unmodified from InvestigationSnapshot.evidence_bundle so the
    # API layer (GET /cases/{case_id}/evidence) never needs to recompute or
    # re-query for what this orchestrator already gathered.
    evidence: list[Evidence] = Field(default_factory=list)

    uncertainty_assessment: UncertaintyAssessment | None = None
    policy_decision: PolicyDecision | None = None

    requested_evidence: list[RequestedEvidence] = Field(default_factory=list)
    historical_case_context: list[SimilarCaseResult] = Field(default_factory=list)
    context: InvestigationContext | None = None  # Phase 2I - the final, policy-inclusive grounded context

    final_explanation: str | None = None
    next_step: str  # deterministically derived from policy_decision/status - never LLM-invented

    iterations: int
    tool_call_count: int
    completed: bool

    agent_messages: list[AgentMessage] = Field(default_factory=list)
    error: str | None = None
