"""Phase 2B - aggregate_evidence(): the one entry point this phase adds.

    TigerGraph query output
            v
    Evidence normalization      (app/evidence/normalize.py)
            v
    Evidence quality            (QUALITY_BY_EVIDENCE_TYPE, network_pattern_quality)
            v
    Structured investigation signals   (Evidence items)
            v
    Aggregated evidence summary        (EvidenceBundle)

Calls each of the six Phase 2A query functions exactly once, independently
try/excepted so one failing query produces an ERROR-status Evidence item
rather than aborting the whole bundle or silently vanishing. No scoring,
no LLM, no LangGraph - see app/evidence/models.py's module docstring for
what is deliberately not here.

Determinism: every collection in the output is sorted by a stable key
before being returned (see `_sort_key` and the explicit `sorted(...)`
calls throughout normalize.py). Given the same TigerGraph results,
`aggregate_evidence` always produces the same normalized structure -
dict iteration order, wall-clock time, and random UUIDs never affect
semantic content anywhere in this module.
"""

from __future__ import annotations

from app.evidence.models import (
    Evidence,
    EvidenceBundle,
    EvidenceSummary,
    EvidenceType,
    QueryStatus,
)
from app.evidence.normalize import (
    error_evidence,
    normalize_network_pattern,
    normalize_shared_email,
    normalize_shared_entity,
    normalize_transaction_context,
)
from app.logging import InvestigationEvent, get_logger, log_event
from app.tigergraph import queries as q
from app.tigergraph.client import TigerGraphClient, TigerGraphError

logger = get_logger(__name__)

# (evidence_type, source_query name) - the three shared-entity Phase 2A
# capabilities this phase normalizes identically. Order is fixed and
# deterministic; it also controls dedup precedence ties (see
# deduplicate_evidence). Deliberately NOT storing the function object
# here: binding `q.find_shared_card_activity` at module-import time would
# capture that reference permanently, so monkeypatching `agg.q.xxx` in a
# test would never take effect - caught live by this phase's own test
# suite. `_run_shared_entity` looks the function up by name at call time
# instead, exactly like the other three query calls below already do.
_SHARED_ENTITY_QUERIES = (
    (EvidenceType.SHARED_CARD, "find_shared_card_activity"),
    (EvidenceType.SHARED_ADDRESS, "find_shared_address_activity"),
    (EvidenceType.SHARED_DEVICE, "find_shared_device_activity"),
)


def dataset_risk_score_from_is_fraud_label(is_fraud_label: bool | None) -> float | None:
    """PROJECT NOTE (also on EvidenceBundle.dataset_risk_score): IEEE-CIS
    (the current development fallback dataset) has no continuous risk
    score, only the binary isFraud label. This 0.0/1.0 mapping exists in
    exactly one place so both `aggregate_evidence` (Phase 2B) and
    `InvestigationService` (Phase 2D, which reuses already-normalized
    Evidence metrics rather than re-querying TigerGraph) agree on it."""
    return None if is_fraud_label is None else (1.0 if is_fraud_label else 0.0)


def _run_transaction_context(client: TigerGraphClient, transaction_id: str) -> tuple[Evidence, float | None]:
    """Returns the normalized evidence item and the dataset risk score
    extracted from it (None if the query failed)."""
    try:
        ctx = q.get_transaction_context(client, transaction_id)
    except (TigerGraphError, Exception) as exc:  # noqa: BLE001 - deliberately broad, see module docstring
        return (
            error_evidence(EvidenceType.TRANSACTION_CONTEXT, transaction_id, "get_transaction_context", exc),
            None,
        )
    evidence = normalize_transaction_context(ctx)
    dataset_risk_score = dataset_risk_score_from_is_fraud_label(ctx.attributes.get("is_fraud"))
    return evidence, dataset_risk_score


def _run_shared_entity(
    client: TigerGraphClient,
    transaction_id: str,
    evidence_type: EvidenceType,
    source_query: str,
) -> Evidence:
    fn = getattr(q, source_query)
    try:
        result = fn(client, transaction_id)
    except (TigerGraphError, Exception) as exc:  # noqa: BLE001
        return error_evidence(evidence_type, transaction_id, source_query, exc)
    return normalize_shared_entity(result)


def _run_shared_email(client: TigerGraphClient, transaction_id: str) -> Evidence:
    try:
        result = q.find_shared_email_activity(client, transaction_id)
    except (TigerGraphError, Exception) as exc:  # noqa: BLE001
        return error_evidence(EvidenceType.SHARED_EMAIL_DOMAIN, transaction_id, "find_shared_email_activity", exc)
    return normalize_shared_email(result)


def _run_network_pattern(client: TigerGraphClient, transaction_id: str) -> Evidence:
    try:
        result = q.investigate_transaction_network(client, transaction_id)
    except (TigerGraphError, Exception) as exc:  # noqa: BLE001
        return error_evidence(
            EvidenceType.NETWORK_PATTERN, transaction_id, "investigate_transaction_network", exc
        )
    return normalize_network_pattern(result)


# ---------------------------------------------------------------- dedup

_STATUS_RANK = {QueryStatus.SUCCESS: 0, QueryStatus.EMPTY: 1, QueryStatus.ERROR: 2}


def _more_complete(a: Evidence, b: Evidence) -> bool:
    """True if `a` should be kept over `b` when both share an evidence_id.
    Deterministic precedence, no timestamps: SUCCESS beats EMPTY beats
    ERROR; among equals, more related_entities/metrics wins; final
    tiebreak is the source_query name, alphabetically."""
    if _STATUS_RANK[a.status] != _STATUS_RANK[b.status]:
        return _STATUS_RANK[a.status] < _STATUS_RANK[b.status]
    if len(a.related_entities) != len(b.related_entities):
        return len(a.related_entities) > len(b.related_entities)
    if len(a.metrics) != len(b.metrics):
        return len(a.metrics) > len(b.metrics)
    return a.provenance.source_query < b.provenance.source_query


def deduplicate_evidence(items: list[Evidence]) -> list[Evidence]:
    """Collapse evidence items that share an evidence_id (same
    transaction, evidence_type, and entity) into one, keeping the most
    complete version. The dedup key is exactly `Evidence.evidence_id`,
    which is itself built only from transaction_id/evidence_type/entity_id
    - never a timestamp or a random value (see models.evidence_id)."""
    by_key: dict[str, Evidence] = {}
    for item in items:
        existing = by_key.get(item.evidence_id)
        if existing is None or _more_complete(item, existing):
            by_key[item.evidence_id] = item
    return [by_key[k] for k in sorted(by_key)]


# ---------------------------------------------------------------- summary


def build_summary(transaction_id: str, dataset_risk_score: float | None, evidence: list[Evidence]) -> EvidenceSummary:
    """Public so Phase 2D's InvestigationService can build an
    EvidenceBundle from tool_results it already collected via the
    registry, without re-querying TigerGraph through aggregate_evidence
    a second time. See app/investigation/service.py."""
    counts: dict[str, int] = {}
    status: dict[str, QueryStatus] = {}
    for item in evidence:
        counts[item.evidence_type.value] = item.metrics.get(
            "related_transaction_count",
            item.metrics.get("total_related_not_deduplicated", 0),
        )
        status[item.evidence_type.value] = item.status

    quality_notes = _quality_notes(evidence)

    return EvidenceSummary(
        transaction_id=transaction_id,
        dataset_risk_score=dataset_risk_score,
        evidence_counts=dict(sorted(counts.items())),
        query_status=dict(sorted(status.items())),
        quality_notes=quality_notes,
    )


def _quality_notes(evidence: list[Evidence]) -> list[str]:
    """Deterministic notes, sourced directly from each evidence item's own
    quality_reason - not re-derived text, and never LLM-generated (per
    this phase's explicit requirement). One note per LOW-quality SUCCESS
    observation, plus one for any ERROR-status item, in a stable order."""
    notes: list[str] = []
    for item in sorted(evidence, key=lambda e: e.evidence_id):
        if item.status == QueryStatus.SUCCESS and item.quality.value == "LOW" and item.quality_reason:
            notes.append(f"{item.evidence_type.value}: {item.quality_reason}")
        if item.status == QueryStatus.ERROR and item.error:
            notes.append(
                f"{item.evidence_type.value}: query failed ({item.error.error_type}) - "
                "evidence for this signal is unavailable, not confirmed absent."
            )
    return notes


# ---------------------------------------------------------------- entry point


def aggregate_evidence(client: TigerGraphClient, transaction_id: str) -> EvidenceBundle:
    log_event(
        logger, InvestigationEvent.GRAPH_QUERY, "aggregating evidence", transaction_id=transaction_id
    )

    ctx_evidence, dataset_risk_score = _run_transaction_context(client, transaction_id)

    shared_evidence = [
        _run_shared_entity(client, transaction_id, evidence_type, source_query)
        for evidence_type, source_query in _SHARED_ENTITY_QUERIES
    ]
    email_evidence = _run_shared_email(client, transaction_id)
    network_evidence = _run_network_pattern(client, transaction_id)

    all_evidence = deduplicate_evidence(
        [ctx_evidence, *shared_evidence, email_evidence, network_evidence]
    )

    summary = build_summary(transaction_id, dataset_risk_score, all_evidence)

    bundle = EvidenceBundle(
        transaction_id=transaction_id,
        dataset_risk_score=dataset_risk_score,
        evidence=all_evidence,
        evidence_summary=summary,
        data_quality_notes=summary.quality_notes,
    )

    log_event(
        logger,
        InvestigationEvent.GRAPH_RESULT,
        "evidence aggregated",
        transaction_id=transaction_id,
        evidence_count=len(all_evidence),
    )
    return bundle
