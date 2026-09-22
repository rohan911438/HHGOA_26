"""Phase 2E - the uncertainty model.

    InvestigationSnapshot (Phase 2D, unchanged)
            v
    UncertaintyEngine (this package - pure computation, no TigerGraph)
            v
    UncertaintyAssessment

Every concept here answers a question about the INVESTIGATION, never
about the TRANSACTION:

  evidence_coverage    - how much of the planned evidence did we obtain?
  signal_quality       - how reliable is the evidence we did obtain?
  signal_conflict      - does the evidence contradict itself?
  data_completeness    - are the underlying fields we did get actually populated?
  overall_uncertainty  - a documented, transparent combination of the above

None of these are, or feed into, a fraud probability, a bank risk score,
an agent confidence value, or a final verdict. The dataset's binary
`isFraud` label (`EvidenceBundle.dataset_risk_score`, Phase 2B) is never
read by this package - see engine.py's module docstring and
tests/unit/test_uncertainty_engine.py::TestDatasetLabelIsolation for the
explicit anti-leakage test.

All 0.0-1.0 values on this model are internal, project-derived
normalizations for comparing evidence-quality/coverage/conflict on a
common scale. They are not bank scores, not official HHGoa policy, and
not probabilities of anything. See docs/phase-2-uncertainty-model.md.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.evidence.models import QueryStatus


class UncertaintyLevel(str, Enum):
    """Describes how uncertain the INVESTIGATION is - never "how likely
    is fraud". Thresholds that produce this from `overall_uncertainty`
    are a documented PROJECT DEVELOPMENT HEURISTIC
    (UncertaintyEngine's low_uncertainty_max/high_uncertainty_min,
    configurable, not official HHGoa policy)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class MissingEvidenceReason(str, Enum):
    """Why a planned evidence source did not contribute usable evidence.
    EMPTY (query ran, found nothing) is deliberately NOT a reason here -
    it is not missing evidence at all (Phase 2E §16)."""

    QUERY_ERROR = "QUERY_ERROR"  # the tool ran and failed - status ERROR
    NOT_INVESTIGATED = "NOT_INVESTIGATED"  # the tool never ran at all (context failed first, or an overall investigation timeout skipped it)
    DATA_MISSING = "DATA_MISSING"  # the tool succeeded, but a specific underlying field this engine checks was null
    NOT_AVAILABLE = "NOT_AVAILABLE"  # reserved for an evidence source structurally incapable of returning data; unused by the current six tools


class MissingEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_type: str  # matches an app.investigation.snapshot.INVESTIGATION_PLAN tool name, or "transaction_context.<field>" for a DATA_MISSING field-level gap
    reason: MissingEvidenceReason
    source_status: QueryStatus | None = None  # None only when NOT_INVESTIGATED - there is no status to report
    impact: str  # deterministic, human-readable consequence - never a fraud claim


class ConflictType(str, Enum):
    """Only a type with an actual, implemented detection rule is ever
    emitted - see engine.py's four `_detect_*` functions. Do not
    manufacture a conflict merely because two counts differ."""

    SIGNAL_DISAGREEMENT = "SIGNAL_DISAGREEMENT"
    QUALITY_DISPARITY = "QUALITY_DISPARITY"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    DATA_INCONSISTENCY = "DATA_INCONSISTENCY"


class ConflictSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class EvidenceConflict(BaseModel):
    model_config = ConfigDict(frozen=True)

    conflict_id: str  # deterministic - f"{transaction_id}:{conflict_type}", never a UUID
    evidence_ids: list[str] = Field(default_factory=list)
    conflict_type: ConflictType
    description: str  # deterministic, template-generated - never LLM text
    severity: ConflictSeverity


class UncertaintyFactor(BaseModel):
    """One traceable input to `overall_uncertainty` - what it is, its
    value, and where it came from, so the eventual demo/reasoning layer
    can show its work rather than just a final number."""

    model_config = ConfigDict(frozen=True)

    factor: str  # "evidence_coverage" | "signal_quality" | "signal_conflict" | "data_completeness"
    value: float | None  # None when this factor could not be computed (excluded from overall_uncertainty, never treated as 0)
    source: str  # e.g. "investigation_snapshot.tool_results"
    detail: str = ""


class UncertaintyAssessment(BaseModel):
    """No `fraud_probability`, `final_fraud_score`, or `agent_confidence`
    field exists on this model, by design - see the module docstring.
    """

    model_config = ConfigDict(frozen=True)

    investigation_id: str
    transaction_id: str

    evidence_coverage: float  # 0.0-1.0, always computable - see engine.py
    signal_quality: float | None  # 0.0-1.0, None when no SUCCESS-status evidence carried a rated LOW/MEDIUM/HIGH quality
    signal_conflict: float  # 0.0-1.0, always computable (0.0 = no detected conflicts)
    data_completeness: float | None  # 0.0-1.0, None when transaction_context did not succeed

    overall_uncertainty: float | None  # 0.0-1.0, None only when the investigation itself FAILED (see engine.py)
    uncertainty_level: UncertaintyLevel

    missing_evidence: list[MissingEvidence] = Field(default_factory=list)
    conflicting_evidence: list[EvidenceConflict] = Field(default_factory=list)

    factors: list[UncertaintyFactor] = Field(default_factory=list)

    sufficient_for_next_stage: bool

    rationale: str
