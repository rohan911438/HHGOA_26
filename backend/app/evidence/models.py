"""Phase 2B - the structured evidence model.

The core design rule, straight from the spec this was built against:
observation, interpretation, provenance, and quality are four different
things and must never collapse into one number. `Evidence` keeps them as
four separate fields. Nothing in this module computes a fraud score -
that is explicitly out of scope for this layer.

Terminology used throughout, and what it means:

  FACT FROM DATASET        - directly observed in the real data (a count,
                              an attribute value, a column's documented
                              meaning from docs/dataset-analysis.md)
  PROJECT-DERIVED           - an interpretation this project assigns
  INTERPRETATION              (e.g. "this is a low-quality signal"),
                              never claimed as official HHGoa policy

See docs/phase-2-evidence-model.md for the full design writeup, including
the dataset_risk_score caveat below and the address/email quality
rationale.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvidenceType(str, Enum):
    """One entry per Phase 2A query capability. Not extended beyond what
    app/tigergraph/queries.py actually implements."""

    TRANSACTION_CONTEXT = "transaction_context"
    SHARED_CARD = "shared_card"
    SHARED_DEVICE = "shared_device"
    SHARED_ADDRESS = "shared_address"
    SHARED_EMAIL_DOMAIN = "shared_email_domain"
    NETWORK_PATTERN = "network_pattern"


class SignalQuality(str, Enum):
    """How useful/reliable an observation is as investigation evidence -
    NOT a fraud probability, and not assigned because a field happens to
    correlate with fraud. Assigned from the documented semantics of the
    underlying field (see QUALITY_BY_EVIDENCE_TYPE below and
    docs/dataset-analysis.md)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class QueryStatus(str, Enum):
    """SUCCESS and EMPTY are both a *working* query - EMPTY simply found
    nothing. ERROR means the query itself could not be completed. An
    uncertainty engine built later needs to tell these apart: "no shared
    device found" is not the same fact as "the device query failed."""

    SUCCESS = "SUCCESS"
    EMPTY = "EMPTY"
    ERROR = "ERROR"


# ---------------------------------------------------------------- quality
#
# PROJECT-DERIVED INTERPRETATION. Assigned once, per evidence type, from
# the documented semantics of the underlying graph entity - not from any
# per-instance fraud correlation. Grounded in real, measured facts from
# docs/dataset-analysis.md and docs/phase-2-graph-analysis.md:
#
#   - Card: card_key is a 4-field composite (card1/card2/card3/card5),
#     the closest thing this dataset has to a card identifier, but it is
#     an anonymized fingerprint, not a verified card number.
#   - Device: device_key is derived from DeviceInfo, which ranges from
#     highly specific build strings to generic values like "Windows" or
#     "iOS Device" (docs/tigergraph-schema.md notes this explicitly).
#     This layer does not yet distinguish by string specificity, so a
#     conservative MEDIUM applies uniformly - a documented limitation,
#     not a claim that all device matches are equally strong.
#   - Address: FACT FROM DATASET - addr1/addr2 are coarse, numeric-coded
#     region identifiers (docs/dataset-analysis.md), not precise street
#     addresses. Real, measured consequence (docs/phase-2-graph-analysis.md):
#     for one test transaction, shared Address reached 312 related
#     transactions vs. 12 for shared Card - an order of magnitude noisier.
#   - EmailDomain: FACT FROM DATASET - only the domain is ever disclosed
#     (never a full address), and the full dataset has only ~59 distinct
#     purchaser-domain values across 590,540 transactions
#     (docs/dataset-analysis.md's column profile). A shared domain like
#     "gmail.com" says almost nothing about a relationship.
#   - TransactionContext: not itself a relationship signal - it is the
#     seed's own descriptive data, so UNKNOWN applies rather than a
#     invented quality rating.
#   - NetworkPattern: quality depends on *which* entity types contributed
#     to the fan-out, computed deterministically per bundle - see
#     `network_pattern_quality()` in aggregate.py, not a fixed value here.

QUALITY_BY_EVIDENCE_TYPE: dict[EvidenceType, tuple[SignalQuality, str]] = {
    EvidenceType.SHARED_CARD: (
        SignalQuality.MEDIUM,
        "card_key is an anonymized 4-field composite fingerprint (card1/card2/card3/card5), "
        "the closest identifier this dataset provides for a card - not a verified card number, "
        "but more specific than a coarse regional code.",
    ),
    EvidenceType.SHARED_DEVICE: (
        SignalQuality.MEDIUM,
        "device_key is derived from DeviceInfo, which ranges from highly specific build "
        "strings to generic values like 'Windows' or 'iOS Device'. This layer does not yet "
        "distinguish by string specificity, so quality is a conservative uniform MEDIUM.",
    ),
    EvidenceType.SHARED_ADDRESS: (
        SignalQuality.LOW,
        "addr1/addr2 are coarse regional/address codes in the development dataset "
        "(docs/dataset-analysis.md), not precise physical addresses. Measured consequence: "
        "for one test transaction, shared Address linked to 312 other transactions vs. 12 "
        "for shared Card (docs/phase-2-graph-analysis.md) - high-cardinality sharing that is "
        "not equivalent to a unique physical address.",
    ),
    EvidenceType.SHARED_EMAIL_DOMAIN: (
        SignalQuality.LOW,
        "Only the email domain is ever disclosed, never a full address, and the full "
        "dataset has only ~59 distinct purchaser-domain values across 590,540 transactions "
        "(docs/dataset-analysis.md). A shared common domain (e.g. gmail.com) is weak evidence "
        "of any real relationship between two transactions.",
    ),
    EvidenceType.TRANSACTION_CONTEXT: (
        SignalQuality.UNKNOWN,
        "Descriptive context about the seed transaction itself, not a relationship signal - "
        "no quality rating applies.",
    ),
    # NETWORK_PATTERN is intentionally absent here: its quality is
    # computed per-bundle from which entity types actually contributed,
    # not a fixed value. See aggregate.py.
}


# ---------------------------------------------------------------- pieces


class Provenance(BaseModel):
    """Where this evidence item came from. Never carries a credential -
    only a source name, the query function that produced it, and the
    parameters used (which are transaction/entity IDs, not secrets)."""

    model_config = ConfigDict(frozen=True)

    source: str = "tigergraph"
    source_query: str
    transaction_id: str
    entity_id: str | None = None
    query_params: dict[str, Any] = Field(default_factory=dict)


class QueryError(BaseModel):
    """Present only when status is ERROR. Distinguishes an actual failure
    from a query that ran fine and found nothing (EMPTY)."""

    model_config = ConfigDict(frozen=True)

    error_type: str
    message: str


class Evidence(BaseModel):
    """One structured observation from one Phase 2A query.

    `observation` is a plain factual sentence describing what the graph
    found - never a verdict ("12 transactions share the same card
    identifier", never "this proves fraud"). `interpretation` is this
    project's own reading of what that observation means as evidence
    (e.g. why it's weak or strong), explicitly separate from the raw fact
    and always traceable to `quality_reason`.
    """

    model_config = ConfigDict(frozen=True)

    evidence_id: str  # deterministic - see evidence_id() below, never a UUID/timestamp
    evidence_type: EvidenceType
    transaction_id: str
    status: QueryStatus

    observation: str | None = None
    interpretation: str | None = None

    quality: SignalQuality = SignalQuality.UNKNOWN
    quality_reason: str | None = None

    related_entities: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)

    error: QueryError | None = None
    provenance: Provenance


def evidence_id(transaction_id: str, evidence_type: EvidenceType, entity_id: str | None) -> str:
    """Deterministic evidence key - the same (transaction, type, entity)
    triple always produces the same id, with no UUID or timestamp
    involved. This is also the deduplication key (see aggregate.py)."""
    return f"{transaction_id}:{evidence_type.value}:{entity_id or '-'}"


# ---------------------------------------------------------------- bundle


class EvidenceSummary(BaseModel):
    """Deterministic, compact summary of an EvidenceBundle - the shape a
    later reasoning layer would actually want to glance at first."""

    model_config = ConfigDict(frozen=True)

    transaction_id: str
    dataset_risk_score: float | None
    evidence_counts: dict[str, int] = Field(default_factory=dict)
    query_status: dict[str, QueryStatus] = Field(default_factory=dict)
    quality_notes: list[str] = Field(default_factory=list)


class EvidenceBundle(BaseModel):
    """Everything gathered about one transaction. No combined score - see
    the module docstring. `dataset_risk_score` and evidence-derived
    signals are kept deliberately separate; nothing here reduces them to
    one verdict."""

    model_config = ConfigDict(frozen=True)

    transaction_id: str

    # PROJECT NOTE, not a dataset fact: IEEE-CIS (the current development
    # fallback dataset) has no continuous risk-score column - only the
    # binary `isFraud` training label. dataset_risk_score is populated as
    # 1.0/0.0 from that label here, and is explicitly NOT the same concept
    # as a real bank's continuous risk score. If the official HHGoa
    # dataset (which the challenge describes as including real risk
    # scores) is obtained, this field would carry genuine continuous
    # values instead, with no change needed elsewhere in this model.
    dataset_risk_score: float | None

    evidence: list[Evidence] = Field(default_factory=list)
    evidence_summary: EvidenceSummary
    data_quality_notes: list[str] = Field(default_factory=list)
