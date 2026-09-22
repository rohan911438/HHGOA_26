"""Phase 2G - the case domain model.

    InvestigationSnapshot (Phase 2D) + UncertaintyAssessment (Phase 2E)
    + PolicyDecision (Phase 2F)
            v
    CaseManager.create_case()      (manager.py)
            v
    CaseRecord

Every `CaseRecord` and its nested models are frozen Pydantic models,
matching the immutability convention every prior phase in this project
uses (`Evidence`, `InvestigationSnapshot`, `UncertaintyAssessment`,
`PolicyDecision`). `CaseManager` never mutates a `CaseRecord` in place -
every update produces a new record via `model_copy(update=...)`, which
is then saved back to the `CaseStore`.

`CaseRecord` deliberately never stores the dataset's binary `isFraud`
label / `dataset_risk_score` - it is not a field on this model at all,
by construction, not merely by convention. `CaseRecord` also never
embeds the full `InvestigationSnapshot`/`EvidenceBundle` (which can hold
many `Evidence` items and tool_results); it references them by
`investigation_id` and `evidence_ids` instead, while embedding the
already-compact `UncertaintyAssessment` and `PolicyDecision` in full
(Phase 2G §4: "do not duplicate huge raw evidence payloads
unnecessarily. Prefer references where possible.").

`CaseOutcome` categories (CONFIRMED_FRAUD / CLEARED / UNRESOLVED /
FALSE_POSITIVE / UNKNOWN) are **not official HHGoa categories** - the
official HHGoa case dataset/policy artifacts are not available in the
current IEEE-CIS development fallback. Any outcome recorded in this
phase's tests is a **SYNTHETIC DEVELOPMENT CASE** (`is_synthetic=True`),
never presented as a real bank investigation.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.evidence.models import SignalQuality
from app.policy.models import ApprovalRoute, PolicyAction, PolicyDecision
from app.uncertainty.models import UncertaintyAssessment


class CaseStatus(str, Enum):
    """See manager.py's `LEGAL_TRANSITIONS` for the exact, enforced state
    machine. `CLOSED` is terminal - no transition leaves it."""

    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    PENDING_EVIDENCE = "PENDING_EVIDENCE"
    ACTION_RECOMMENDED = "ACTION_RECOMMENDED"
    PENDING_REVIEW = "PENDING_REVIEW"
    CLOSED = "CLOSED"


class CaseTrigger(str, Enum):
    """Why the case exists. UNKNOWN is the honest default when the
    caller does not (yet) have a real reason to distinguish one - this
    project has no customer-report intake or agent-request system built
    yet, so those two only ever appear when a caller explicitly asserts
    them."""

    FRAUD_SIGNAL = "FRAUD_SIGNAL"
    CUSTOMER_REPORT = "CUSTOMER_REPORT"
    ANALYST_REQUEST = "ANALYST_REQUEST"
    AGENT_REQUEST = "AGENT_REQUEST"
    UNKNOWN = "UNKNOWN"


class CaseFinding(BaseModel):
    """A structured finding. `quality` reuses Phase 2B's `SignalQuality`
    (the project's only already-supported categorical importance rating)
    rather than inventing a new fraud-flavored severity scale."""

    model_config = ConfigDict(frozen=True)

    finding_id: str
    description: str
    evidence_ids: list[str] = Field(default_factory=list)
    quality: SignalQuality | None = None
    created_at: datetime


class DecisionType(str, Enum):
    """Keeps three genuinely different kinds of decision distinguishable
    - a system recommendation is never conflated with a human choosing
    to act on it, or with someone approving/rejecting that choice."""

    SYSTEM_RECOMMENDATION = "SYSTEM_RECOMMENDATION"
    HUMAN_DECISION = "HUMAN_DECISION"
    APPROVAL = "APPROVAL"


class CaseDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str
    decision_type: DecisionType
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)
    actor: str  # e.g. "policy_engine", "analyst:jdoe", "system" - free text, always attributable
    approval_route: ApprovalRoute | None = None
    approved: bool | None = None  # only meaningful when decision_type == APPROVAL
    created_at: datetime


class CaseActionStatus(str, Enum):
    RECOMMENDED = "RECOMMENDED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"


class CaseAction(BaseModel):
    """`executed` is `False` until `CaseManager.record_action_executed`
    is explicitly called by a caller asserting the action happened
    somewhere else - `CaseManager` never performs any real-world action
    itself (there is no backend to perform one against, per Phase 2F).
    A `PolicyDecision.action` recommendation never becomes `executed`
    automatically."""

    model_config = ConfigDict(frozen=True)

    action_id: str
    action: PolicyAction
    status: CaseActionStatus
    rationale: str
    approval_required: bool
    approval_route: ApprovalRoute
    executed: bool = False
    actor: str
    created_at: datetime


class CaseOutcomeType(str, Enum):
    """NOT official HHGoa outcome categories - see module docstring."""

    CONFIRMED_FRAUD = "CONFIRMED_FRAUD"
    CLEARED = "CLEARED"
    UNRESOLVED = "UNRESOLVED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    UNKNOWN = "UNKNOWN"


class CaseOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome_type: CaseOutcomeType
    notes: str = ""
    recorded_at: datetime
    is_synthetic: bool = False  # True for any test/dev outcome - never claimed as real bank data


class CaseRecord(BaseModel):
    """The persistent, structured fraud case. JSON-serializable via
    `model_dump(mode="json")`, and round-trips through
    `model_validate(json.loads(...))` unchanged."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    status: CaseStatus
    trigger: CaseTrigger
    transaction_id: str
    investigation_id: str | None = None

    created_at: datetime
    updated_at: datetime

    evidence_ids: list[str] = Field(default_factory=list)
    evidence_types: list[str] = Field(default_factory=list)  # material evidence types only - see manager.py

    findings: list[CaseFinding] = Field(default_factory=list)
    decisions: list[CaseDecision] = Field(default_factory=list)
    actions: list[CaseAction] = Field(default_factory=list)
    outcome: CaseOutcome | None = None

    uncertainty_assessment: UncertaintyAssessment | None = None
    policy_decision: PolicyDecision | None = None

    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- case memory


class SimilarCaseResult(BaseModel):
    """One retrieved historical case. `similarity_score` and
    `matching_features` come from `app.case.memory`'s PROJECT
    DEVELOPMENT HEURISTIC similarity formula - never from `isFraud` or
    any dataset label. Exposes the case's recorded outcome without
    concluding anything from it - see memory.py's module docstring."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    similarity_score: float
    matching_features: list[str] = Field(default_factory=list)
    relevant_findings: list[CaseFinding] = Field(default_factory=list)
    previous_decisions: list[CaseDecision] = Field(default_factory=list)
    previous_actions: list[CaseAction] = Field(default_factory=list)
    outcome: CaseOutcome | None = None


class RecurringPattern(BaseModel):
    """A recurring INVESTIGATION/EVIDENCE pattern - never called a
    "fraud pattern" unless every related case's outcome actually
    confirms fraud, which this phase's synthetic test data does not
    claim to establish."""

    model_config = ConfigDict(frozen=True)

    pattern_id: str
    evidence_types: list[str] = Field(default_factory=list)  # sorted, deterministic
    occurrences: int
    related_case_ids: list[str] = Field(default_factory=list)
    historical_actions: list[PolicyAction] = Field(default_factory=list)
    historical_outcomes: list[CaseOutcomeType] = Field(default_factory=list)
