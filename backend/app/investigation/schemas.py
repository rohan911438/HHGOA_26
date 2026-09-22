"""Phase 2C - typed input and output contracts for investigation tools.

Input: every one of the six tools takes exactly one field, the seed
transaction's primary ID - `TransactionIdInput` defines it once;
per-tool subclasses exist only so each tool's JSON schema carries its
own name (useful once a LangGraph agent binds these as tool schemas),
not because the shape differs.

Output: `InvestigationToolResult.status` reuses `QueryStatus` from
app.evidence.models directly - SUCCESS/EMPTY/ERROR - rather than
inventing a second, incompatible status vocabulary. `evidence` and
`provenance` likewise reuse the Phase 2B `Evidence`/`Provenance` models
unchanged.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.evidence.models import Evidence, Provenance, QueryStatus
from app.investigation.errors import ToolErrorType

# strict=True rejects non-string types (e.g. an int transaction_id)
# rather than silently coercing them. strip_whitespace + min_length=1
# rejects "", None (via the required field) and whitespace-only strings.
# max_length is a sanity guard against obviously malformed input only -
# it does not assume this dataset's IDs are numeric or fixed-width.
TransactionId = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=128),
]


class TransactionIdInput(BaseModel):
    """Shared input shape for all six investigation tools."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    transaction_id: TransactionId = Field(
        ..., description="Primary ID of the seed Txn vertex to investigate."
    )


class TransactionContextInput(TransactionIdInput):
    """Input for get_transaction_context."""


class SharedCardInput(TransactionIdInput):
    """Input for find_shared_card_activity."""


class SharedDeviceInput(TransactionIdInput):
    """Input for find_shared_device_activity."""


class SharedAddressInput(TransactionIdInput):
    """Input for find_shared_address_activity."""


class SharedEmailInput(TransactionIdInput):
    """Input for find_shared_email_activity."""


class NetworkPatternInput(TransactionIdInput):
    """Input for investigate_transaction_network."""


# ---------------------------------------------------------------- output


class ToolError(BaseModel):
    """Present only when status is ERROR - mirrors app.evidence.models.QueryError
    but carries the tool layer's own normalized `ToolErrorType` rather than
    a raw exception class name."""

    model_config = ConfigDict(frozen=True)

    error_type: ToolErrorType
    message: str


class InvestigationToolResult(BaseModel):
    """What every investigation tool returns via the registry.

    `evidence` is the exact Phase 2B `Evidence` item the underlying query
    produced (None only when status is ERROR and the failure happened
    before any Evidence could be built, e.g. invalid input or a timeout).
    `summary` is always a short, human-readable one-liner - the
    observation on success, the error message on failure - never a
    fraud verdict.
    """

    model_config = ConfigDict(frozen=True)

    tool_name: str
    status: QueryStatus
    transaction_id: str
    evidence: Evidence | None = None
    summary: str
    provenance: Provenance | None = None
    latency_ms: float
    error: ToolError | None = None
