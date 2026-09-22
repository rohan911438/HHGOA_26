"""Phase 2F - the policy / next-best-action domain model.

**POLICY STATUS: PROJECT DEVELOPMENT HEURISTIC - NOT OFFICIAL HHGOA
POLICY.** No action limit, approval route, or threshold in this package
is an official HHGoa rule - the official policy artifacts are not
available in the current development fallback dataset. See
docs/phase-2-policy-nba.md.

    InvestigationSnapshot (Phase 2D) + UncertaintyAssessment (Phase 2E)
            v
    PolicyEngine.evaluate()      (engine.py - pure computation, no TigerGraph, no LLM)
            v
    PolicyDecision

`PolicyEngine` never executes a real-world action. `PolicyDecision`
separates three distinct concepts that must never collapse into one:

  recommendation    - `action`, what the engine suggests
  authorization      - `approval_required` / `approval_route`, who (if
                        anyone) must sign off before `action` could ever
                        be carried out
  executability       - `executable`, whether this project has any real
                        backend capable of carrying `action` out at all
                        (currently: none - every action is a
                        recommendation/stub contract, never claimed as
                        performed)

Recommending BLOCK_TRANSACTION never means a transaction was blocked.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.uncertainty.models import UncertaintyLevel

POLICY_STATUS_DISCLAIMER = "PROJECT DEVELOPMENT HEURISTIC - NOT OFFICIAL HHGOA POLICY"


class PolicyAction(str, Enum):
    """The declared action vocabulary, matching the challenge's action
    space where compatible with what this project can ground in real
    evidence. Not every value is reachable by the current heuristic -
    see docs/phase-2-policy-nba.md for exactly which ones are, and why
    FILE_REPORT is declared but not currently reachable (filing a
    regulatory report needs an official threshold this project does not
    fabricate)."""

    ALLOW_TRANSACTION = "ALLOW_TRANSACTION"
    BLOCK_TRANSACTION = "BLOCK_TRANSACTION"
    MONITOR_ACCOUNT = "MONITOR_ACCOUNT"
    WARN_CUSTOMER = "WARN_CUSTOMER"
    CREATE_CASE = "CREATE_CASE"
    FILE_REPORT = "FILE_REPORT"  # declared for vocabulary completeness; not reachable this phase
    REQUEST_MORE_EVIDENCE = "REQUEST_MORE_EVIDENCE"
    ESCALATE_ANALYST = "ESCALATE_ANALYST"


class ApprovalRoute(str, Enum):
    """Project-defined development abstractions, not official HHGoa
    authorization routing."""

    NONE = "NONE"
    ANALYST = "ANALYST"
    SENIOR_ANALYST = "SENIOR_ANALYST"
    COMPLIANCE = "COMPLIANCE"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class PolicyStatus(str, Enum):
    """What kind of outcome this PolicyDecision represents."""

    RECOMMENDATION_READY = "RECOMMENDATION_READY"
    MORE_EVIDENCE_REQUIRED = "MORE_EVIDENCE_REQUIRED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    NOT_ACTIONABLE = "NOT_ACTIONABLE"


class PolicyDecision(BaseModel):
    """No `fraud_probability`, `fraud_verdict`, or similar field exists
    here, by design - this model recommends and classifies authorization
    requirements only. `executable` is `False` for every action produced
    by this phase: this project has no real backend (payment processor,
    customer messaging system, case management system, regulator filing
    API) capable of actually carrying out any of these actions yet.
    """

    model_config = ConfigDict(frozen=True)

    investigation_id: str
    transaction_id: str

    action: PolicyAction
    approval_required: bool
    approval_route: ApprovalRoute
    executable: bool
    requires_more_evidence: bool
    status: PolicyStatus

    uncertainty_level: UncertaintyLevel
    overall_uncertainty: float | None

    evidence_ids: list[str] = Field(default_factory=list)
    policy_basis: str = POLICY_STATUS_DISCLAIMER
    rationale: str
