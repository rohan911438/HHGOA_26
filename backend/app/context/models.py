"""Phase 2I - the fraud-investigation GraphRAG / context domain model.

    InvestigationSnapshot (Phase 2D) + UncertaintyAssessment (Phase 2E)
    + PolicyDecision (Phase 2F, optional) + CaseMemory results (Phase 2G)
            v
    ContextBuilder.build()      (builder.py - pure computation, no TigerGraph, no LLM)
            v
    InvestigationContext

This is a **structured retrieval** GraphRAG layer, not a generic
vector-RAG system - no embeddings, no vector database, no document
chunking (Phase 2I §4). "Retrieval" here means: reuse the already-computed
Phase 2B `Evidence` items, Phase 2E `MissingEvidence` entries, and Phase
2G `SimilarCaseResult`/`RecurringPattern` results, and normalize them
into one grounded, typed, provenance-carrying structure the agent can
reason over.

Every `ContextItem` has a `provenance` string - if a builder function
cannot establish genuine provenance for a fact, it does not create a
`ContextItem` for it at all (Phase 2I §3). No `ContextItem` is ever
built from free text or an LLM - `content` is always a deterministic,
template-generated string sourced from an existing typed model field.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.evidence.models import SignalQuality
from app.policy.models import PolicyDecision
from app.uncertainty.models import UncertaintyAssessment

CONTEXT_FORMAT_VERSION = "v1"


class ContextItemType(str, Enum):
    """The four categories this phase's brief requires kept strictly
    distinct - never mixed into one undifferentiated "context" blob."""

    CURRENT_FACT = "CURRENT_FACT"
    HISTORICAL_CASE = "HISTORICAL_CASE"
    DERIVED_PATTERN = "DERIVED_PATTERN"
    MISSING_INFORMATION = "MISSING_INFORMATION"


class ContextItem(BaseModel):
    """One grounded, provenance-carrying piece of context.

    `content` is always a plain factual sentence built from structured
    fields already present on an existing Phase 2B/2E/2G model - never a
    stronger claim than the source data supports (Phase 2I §7: "12
    related transactions share the same card", never "this card is
    fraudulent"). `quality` reuses Phase 2B's `SignalQuality` unchanged
    for `CURRENT_FACT` items only - historical similarity never upgrades
    or downgrades it (Phase 2I §13). `relevance` is the historical
    similarity score or `None` for current facts (always maximally
    relevant to the current transaction by construction).
    """

    model_config = ConfigDict(frozen=True)

    context_id: str
    context_type: ContextItemType
    content: str
    source: str  # e.g. "phase_2b_evidence_model", "uncertainty_engine", "case_memory"
    provenance: str  # always non-empty - see module docstring
    evidence_ids: list[str] = Field(default_factory=list)
    case_ids: list[str] = Field(default_factory=list)
    quality: SignalQuality | None = None
    relevance: float | None = None


class InvestigationContext(BaseModel):
    """The grounded context handed to the Phase 2H agent. JSON-serializable
    via `model_dump(mode="json")` and round-trips unchanged.

    `uncertainty_assessment` and `policy_decision` are the exact Phase
    2E/2F objects, exposed unmodified - this layer never recalculates or
    alters either (Phase 2I §14/§15). `policy_decision` is `None` when
    context is built before policy evaluation has run (e.g. during the
    agent's mid-loop sufficiency check).
    """

    model_config = ConfigDict(frozen=True)

    context_format_version: str = CONTEXT_FORMAT_VERSION
    transaction_id: str
    investigation_id: str | None = None
    case_id: str | None = None
    generated_at: datetime

    current_evidence: list[ContextItem] = Field(default_factory=list)
    historical_cases: list[ContextItem] = Field(default_factory=list)
    recurring_patterns: list[ContextItem] = Field(default_factory=list)
    missing_information: list[ContextItem] = Field(default_factory=list)

    context_items: list[ContextItem] = Field(default_factory=list)

    uncertainty_assessment: UncertaintyAssessment | None = None
    policy_decision: PolicyDecision | None = None

    truncated: bool = False
    truncation_notes: list[str] = Field(default_factory=list)
