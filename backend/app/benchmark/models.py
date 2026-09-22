"""Phase 2J - typed models for the official HHGoa benchmark adapter.

STATUS (see docs/phase-2-benchmark-report.md for the full record): the
official HHGOA_IEEE 20-case benchmark - transactions, bank fraud risk
scores, device/connection records, closed investigations, policy
material, five known fraud patterns, regulatory references, and an
answer format - has not been found anywhere in this repository or
development environment. `docs/phase-1-report.md` §1 has the original
search record; `discovery.py` re-checks it for this phase.

Nothing in this module invents that content. These models define the
shape a real adapter would normalize official files into; only
`discovery.py`'s `load_official_benchmark_cases()` would ever construct
a `BenchmarkCase`/`BenchmarkExpectedAnswer`, and only from a real file -
it raises rather than fabricating when none exists (see its docstring).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agent.state import AgentStatus
from app.case.models import CaseStatus, CaseTrigger
from app.policy.models import ApprovalRoute, PolicyAction
from app.uncertainty.models import UncertaintyLevel


class BenchmarkCase(BaseModel):
    """One case from the official benchmark, normalized into this
    project's agent input shape (a transaction_id + trigger). `raw`
    preserves the untouched source record. `official_case_id` is the
    source's own identifier, kept verbatim - never regenerated."""

    model_config = ConfigDict(frozen=True)

    official_case_id: str
    transaction_id: str
    trigger: CaseTrigger = CaseTrigger.UNKNOWN
    source_file: str
    raw: dict[str, Any] = Field(default_factory=dict)


class BenchmarkExpectedAnswer(BaseModel):
    """Populated only from fields the official source itself provides.
    Every field is optional because, absent real files, this project
    cannot know which of these the official benchmark actually
    specifies - an absent field means "the source did not say", never
    "assume a default"."""

    model_config = ConfigDict(frozen=True)

    official_case_id: str
    expected_fraud_pattern: str | None = None
    expected_action: PolicyAction | None = None
    expected_approval_route: ApprovalRoute | None = None
    expected_uncertainty_level: UncertaintyLevel | None = None
    expected_sar_required: bool | None = None
    expected_case_status: CaseStatus | None = None
    notes: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ComparisonCategory(str, Enum):
    """The 2J.3 comparison categories. Only ever populated when a
    `BenchmarkExpectedAnswer` field for that category is present."""

    EVIDENCE = "EVIDENCE"
    FRAUD_PATTERN = "FRAUD_PATTERN"
    UNCERTAINTY = "UNCERTAINTY"
    NEXT_BEST_ACTION = "NEXT_BEST_ACTION"
    APPROVAL_ROUTE = "APPROVAL_ROUTE"
    CASE_PROGRESSION = "CASE_PROGRESSION"
    SAR_REQUIREMENT = "SAR_REQUIREMENT"
    EXPLANATION_COMPLETENESS = "EXPLANATION_COMPLETENESS"


class ComparisonOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: ComparisonCategory
    expected: Any | None = None
    actual: Any | None = None
    # None when there was no official expected value to compare against -
    # distinct from False (compared, and did not match).
    match: bool | None = None


class BenchmarkResult(BaseModel):
    """One executed case's captured trace (2J.4's required fields).

    `is_official_benchmark_case` is the single field callers must check
    before treating a result as a scored official-benchmark outcome -
    every result produced while the official dataset is unavailable has
    this set to `False` (see runner.py): it is diagnostic infrastructure
    validation only, never a benchmark score.
    """

    model_config = ConfigDict(frozen=True)

    case_id: str
    official_case_id: str | None = None
    transaction_id: str
    is_official_benchmark_case: bool

    investigation_status: AgentStatus
    findings: list[str] = Field(default_factory=list)
    uncertainty_level: UncertaintyLevel | None = None
    historical_context_count: int = 0
    recommended_action: PolicyAction | None = None
    approval_required: bool | None = None
    approval_route: ApprovalRoute | None = None
    case_status: CaseStatus | None = None
    explanation: str | None = None
    requested_evidence_count: int = 0
    iteration_count: int
    tool_call_count: int
    execution_time_ms: float

    comparisons: list[ComparisonOutcome] = Field(default_factory=list)
    error: str | None = None

    executed_at: datetime
