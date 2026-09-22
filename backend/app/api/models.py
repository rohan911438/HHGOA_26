"""Phase 2K - typed API request/response models.

Every response model here reuses the project's existing, already-frozen,
already-JSON-serializable domain models (`Evidence`, `CaseRecord`,
`UncertaintyAssessment`, `PolicyDecision`, `InvestigationContext`,
`SimilarCaseResult`) as field types rather than re-declaring their shape
- this is the API's typed contract layer, not a second copy of the
domain model. Nothing here recomputes a value any of those models
already carry.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agent.state import AgentStatus, RequestedEvidence
from app.case.models import CaseRecord, CaseTrigger, SimilarCaseResult
from app.context.models import InvestigationContext
from app.evidence.models import Evidence
from app.policy.models import PolicyDecision
from app.uncertainty.models import UncertaintyAssessment

# ---------------------------------------------------------------- health


class HealthResponse(BaseModel):
    status: str
    service: str


class DependencyStatus(BaseModel):
    """`checked` is False when the dependency was never actually probed
    (e.g. `/health` was called with a query flag that skips it) -
    distinct from `healthy=False`, which means it *was* probed and
    failed. Never claims a dependency is healthy without having called
    it (see routes/health.py)."""

    name: str
    checked: bool
    healthy: bool | None = None
    detail: str | None = None
    latency_ms: float | None = None


class DetailedHealthResponse(BaseModel):
    status: str
    service: str
    dependencies: list[DependencyStatus] = Field(default_factory=list)


# ---------------------------------------------------------------- investigations


class InvestigationRequest(BaseModel):
    transaction_id: str = Field(..., min_length=1, max_length=64)
    trigger: CaseTrigger = CaseTrigger.UNKNOWN


class InvestigationResponse(BaseModel):
    """The API's stable shape for one investigation outcome. Field names
    intentionally differ from `AgentInvestigationResult`'s internal ones
    where the API contract calls for a different name (`status` vs.
    `investigation_status`, `tool_calls` vs. `tool_call_count`); every
    value is copied through unmodified, nothing recomputed.

    `investigation_id` is `case_id` when a case was created; when the
    investigation failed before a case existed, it is instead the id
    this API layer generated to make the run retrievable at all via
    `GET /investigations/{investigation_id}` - see
    `app/api/registry.py`'s module docstring and
    `docs/phase-2-api.md`'s "Known limitations" section for why no
    separate investigation-record model exists.
    """

    model_config = ConfigDict(frozen=True)

    investigation_id: str
    case_id: str | None
    transaction_id: str
    status: AgentStatus
    completed: bool

    iterations: int
    tool_calls: int

    findings: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)

    uncertainty: UncertaintyAssessment | None = None
    policy_decision: PolicyDecision | None = None

    requested_evidence: list[RequestedEvidence] = Field(default_factory=list)
    historical_context: list[SimilarCaseResult] = Field(default_factory=list)
    context: InvestigationContext | None = None

    explanation: str | None = None
    next_step: str
    error: str | None = None


# ---------------------------------------------------------------- cases


class EvidenceResponse(BaseModel):
    """See `app/api/registry.py` - `detail_available=True` only when this
    API process itself ran the investigation that produced `case_id`."""

    case_id: str
    transaction_id: str
    detail_available: bool
    evidence: list[Evidence] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_types: list[str] = Field(default_factory=list)
    note: str | None = None


class HistoryEventType(str, Enum):
    """Only event types this API can actually derive from `CaseRecord`
    fields - see routes/cases.py. `EVIDENCE_ATTACHED` and
    `STATUS_CHANGED` are not included: `CaseRecord` does not separately
    timestamp either (only the current `status` and `evidence_ids` are
    kept, not a change log) - documented in docs/phase-2-api.md rather
    than fabricated here."""

    CASE_CREATED = "CASE_CREATED"
    FINDING_ADDED = "FINDING_ADDED"
    RECOMMENDATION_CREATED = "RECOMMENDATION_CREATED"
    APPROVAL_RECORDED = "APPROVAL_RECORDED"
    ACTION_RECORDED = "ACTION_RECORDED"
    OUTCOME_RECORDED = "OUTCOME_RECORDED"


class HistoryEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: HistoryEventType
    occurred_at: datetime
    description: str
    ref_id: str | None = None


class HistoryResponse(BaseModel):
    case_id: str
    events: list[HistoryEvent] = Field(default_factory=list)
    note: str


class SimilarCasesResponse(BaseModel):
    case_id: str
    similar_cases: list[SimilarCaseResult] = Field(default_factory=list)
    note: str


class ContextResponse(BaseModel):
    case_id: str
    available: bool
    context: InvestigationContext | None = None
    note: str | None = None


# ---------------------------------------------------------------- errors (see app/api/errors.py for the exceptions)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


__all__ = [
    "CaseRecord",  # re-exported: GET /cases/{case_id} returns this directly
    "ContextResponse",
    "DependencyStatus",
    "DetailedHealthResponse",
    "ErrorBody",
    "ErrorResponse",
    "EvidenceResponse",
    "HealthResponse",
    "HistoryEvent",
    "HistoryEventType",
    "HistoryResponse",
    "InvestigationRequest",
    "InvestigationResponse",
    "SimilarCasesResponse",
]
