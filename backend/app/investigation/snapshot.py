"""Phase 2D - the deterministic investigation result model.

    InvestigationService
            v
    ToolRegistry.execute() x 6           (app/investigation/registry.py)
            v
    Evidence items (Phase 2B, unchanged)
            v
    EvidenceBundle                       (app.evidence.aggregate.build_summary/deduplicate_evidence)
            v
    InvestigationSnapshot                (this module)

No fraud verdict lives here or anywhere in this phase. `InvestigationStatus`
describes whether the investigation itself ran to completion - COMPLETED /
PARTIAL / FAILED - never whether the transaction is fraudulent.
`EvidenceBundle.dataset_risk_score` (Phase 2B, reused unchanged) remains
the only place the dataset's binary label surfaces, and it is a dataset
fact, not an investigation conclusion.

Every field here is a plain Pydantic model, a str/float/int, a datetime,
or a list/dict of those - no TigerGraphConnection, Thread, Executor, or
raw exception ever appears on this model, so the whole snapshot is
JSON-serializable via `model_dump(mode="json")` for a future FastAPI
response, unchanged.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.evidence.models import Evidence, EvidenceBundle, QueryStatus
from app.investigation.errors import ToolErrorType
from app.investigation.schemas import InvestigationToolResult

# Fixed order - what "deterministic ordering" (Phase 2D §18) means in
# practice. A tool's actual completion time (sequential or concurrent)
# never affects where it appears in `tools_executed` / `tool_results` /
# `coverage.per_tool_status` on the final snapshot.
INVESTIGATION_PLAN: tuple[str, ...] = (
    "get_transaction_context",
    "find_shared_card_activity",
    "find_shared_device_activity",
    "find_shared_address_activity",
    "find_shared_email_activity",
    "investigate_transaction_network",
)


class InvestigationStatus(str, Enum):
    """Describes whether the investigation ran to completion - never a
    fraud determination. See InvestigationService._determine_status for
    the exact, documented logic that assigns this."""

    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ToolExecution(BaseModel):
    """A lightweight audit record of one tool invocation - what ran, not
    what it found (that's on `InvestigationToolResult.evidence` /
    `EvidenceBundle`, kept separate on purpose). Never carries a
    credential, a connection object, or a raw exception."""

    model_config = ConfigDict(frozen=True)

    tool_name: str
    status: QueryStatus
    started_at: datetime
    completed_at: datetime
    latency_ms: float
    result_count: int
    error_type: ToolErrorType | None = None


class CoverageSummary(BaseModel):
    """A deterministic count of what happened, not a probability."""

    model_config = ConfigDict(frozen=True)

    per_tool_status: dict[str, QueryStatus] = Field(default_factory=dict)
    successful_tools: int = 0
    empty_tools: int = 0
    failed_tools: int = 0
    total_tools: int = 0


class InvestigationSnapshot(BaseModel):
    """Everything gathered about one investigation run.

    `transaction_context` is a convenience pointer at the
    TRANSACTION_CONTEXT `Evidence` item already present in
    `evidence_bundle.evidence` - not a second copy of query logic.
    `tool_results` carries the full `InvestigationToolResult` (evidence,
    provenance, error) per tool for transparency; `tools_executed` /
    `execution_log` answer "what ran, in what order, how fast" without
    requiring a reader to dig through evidence to find that out.

    No `fraud_verdict` / `final_fraud_score` field exists on this model,
    by design - see the module docstring.
    """

    model_config = ConfigDict(frozen=True)

    investigation_id: str
    transaction_id: str

    started_at: datetime
    completed_at: datetime

    transaction_context: Evidence | None = None
    evidence_bundle: EvidenceBundle | None = None

    tools_executed: list[str] = Field(default_factory=list)
    tool_results: dict[str, InvestigationToolResult] = Field(default_factory=dict)
    execution_log: list[ToolExecution] = Field(default_factory=list)

    coverage: CoverageSummary
    warnings: list[str] = Field(default_factory=list)
    status: InvestigationStatus
