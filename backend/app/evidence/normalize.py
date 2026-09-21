"""Phase 2B - turn Phase 2A query results into Evidence objects.

One function per Phase 2A query shape. Each produces exactly one
`Evidence` item (or, for the error path, one ERROR-status item) - no
function here calls TigerGraph itself; that happens in aggregate.py,
which is also where a query's exception becomes an ERROR-status Evidence
item instead of an unhandled crash.

Every `observation` string here is built entirely from structured facts
already present on the Phase 2A result object - no LLM, no free text
generation, per the phase's explicit "do not use the LLM for this"
requirement. `interpretation` strings are similarly template-generated
from the same facts plus the static QUALITY_BY_EVIDENCE_TYPE table.
"""

from __future__ import annotations

from app.evidence.models import (
    Evidence,
    EvidenceType,
    Provenance,
    QueryError,
    QueryStatus,
    QUALITY_BY_EVIDENCE_TYPE,
    SignalQuality,
    evidence_id,
)
from app.tigergraph.queries import (
    NetworkSummary,
    SharedEmailActivity,
    SharedEntityActivity,
    TransactionContext,
)

_SHARED_ENTITY_TYPE_MAP: dict[str, EvidenceType] = {
    "Card": EvidenceType.SHARED_CARD,
    "Address": EvidenceType.SHARED_ADDRESS,
    "Device": EvidenceType.SHARED_DEVICE,
}

_SHARED_ENTITY_QUERY_MAP: dict[str, str] = {
    "Card": "find_shared_card_activity",
    "Address": "find_shared_address_activity",
    "Device": "find_shared_device_activity",
}


def _fraud_count(related_transactions: list[dict]) -> int:
    return sum(1 for t in related_transactions if t.get("is_fraud") is True)


def _related_txn_ids(related_transactions: list[dict]) -> list[str]:
    # Sorted for determinism - see the module docstring on aggregate.py
    # for why dict/set iteration order must never leak into output.
    return sorted(t["transaction_id"] for t in related_transactions if t.get("transaction_id"))


# ---------------------------------------------------------------- errors


def error_evidence(
    evidence_type: EvidenceType,
    transaction_id: str,
    source_query: str,
    exc: Exception,
    entity_id: str | None = None,
) -> Evidence:
    """The query could not be completed. This is a distinct fact from
    "the query succeeded and found nothing" (EMPTY) - conflating the two
    would hide a real system failure from whatever reasons over this
    evidence next."""
    return Evidence(
        evidence_id=evidence_id(transaction_id, evidence_type, entity_id),
        evidence_type=evidence_type,
        transaction_id=transaction_id,
        status=QueryStatus.ERROR,
        observation=None,
        interpretation=None,
        quality=SignalQuality.UNKNOWN,
        quality_reason="Query failed - no observation is available to assess.",
        related_entities=[],
        metrics={},
        error=QueryError(error_type=type(exc).__name__, message=str(exc)),
        provenance=Provenance(source_query=source_query, transaction_id=transaction_id),
    )


# ---------------------------------------------------------------- normalizers


def normalize_transaction_context(ctx: TransactionContext) -> Evidence:
    evidence_type = EvidenceType.TRANSACTION_CONTEXT
    quality, quality_reason = QUALITY_BY_EVIDENCE_TYPE[evidence_type]

    linked = []
    if ctx.card:
        linked.append("a card")
    if ctx.address:
        linked.append("an address")
    if ctx.purchaser_email:
        linked.append("a purchaser email domain")
    if ctx.recipient_email:
        linked.append("a recipient email domain")
    if ctx.device:
        linked.append("a device")

    if linked:
        observation = f"Transaction {ctx.transaction_id} has {', '.join(linked)} on file."
    else:
        observation = f"Transaction {ctx.transaction_id} has no linked card, address, email, or device."

    metrics = {
        "has_card": ctx.card is not None,
        "has_address": ctx.address is not None,
        "has_purchaser_email": ctx.purchaser_email is not None,
        "has_recipient_email": ctx.recipient_email is not None,
        "has_device": ctx.device is not None,
        "transaction_amt": ctx.attributes.get("transaction_amt"),
        "product_cd": ctx.attributes.get("product_cd"),
        # Present here for traceability only - the authoritative field
        # is EvidenceBundle.dataset_risk_score, not this metric.
        "is_fraud_label": ctx.attributes.get("is_fraud"),
    }

    return Evidence(
        evidence_id=evidence_id(ctx.transaction_id, evidence_type, None),
        evidence_type=evidence_type,
        transaction_id=ctx.transaction_id,
        status=QueryStatus.SUCCESS,
        observation=observation,
        interpretation=None,
        quality=quality,
        quality_reason=quality_reason,
        related_entities=[],
        metrics=metrics,
        provenance=Provenance(
            source_query="get_transaction_context", transaction_id=ctx.transaction_id
        ),
    )


def normalize_shared_entity(result: SharedEntityActivity) -> Evidence:
    """Shared card / address / device - identical shape, entity_type on
    the Phase 2A result selects which EvidenceType and quality rating
    apply."""
    evidence_type = _SHARED_ENTITY_TYPE_MAP[result.entity_type]
    quality, quality_reason = QUALITY_BY_EVIDENCE_TYPE[evidence_type]
    entity_id = result.entity["id"] if result.entity else None

    if result.entity is None:
        observation = f"Transaction {result.seed_transaction_id} has no linked {result.entity_type}."
        status = QueryStatus.EMPTY
    elif result.related_count == 0:
        observation = (
            f"Transaction {result.seed_transaction_id} has a linked {result.entity_type} "
            f"('{entity_id}'), but no other transaction shares it."
        )
        status = QueryStatus.EMPTY
    else:
        fraud_n = _fraud_count(result.related_transactions)
        observation = (
            f"{result.related_count} other transaction(s) share the same "
            f"{result.entity_type.lower()} identifier ('{entity_id}')."
        )
        status = QueryStatus.SUCCESS

    interpretation = None
    if status == QueryStatus.SUCCESS:
        interpretation = (
            f"{quality.value}-quality signal: {quality_reason}"
        )

    metrics: dict = {}
    if result.entity is not None:
        metrics["related_transaction_count"] = result.related_count
        metrics["related_fraud_count"] = _fraud_count(result.related_transactions)

    return Evidence(
        evidence_id=evidence_id(result.seed_transaction_id, evidence_type, entity_id),
        evidence_type=evidence_type,
        transaction_id=result.seed_transaction_id,
        status=status,
        observation=observation,
        interpretation=interpretation,
        quality=quality if status == QueryStatus.SUCCESS else SignalQuality.UNKNOWN,
        quality_reason=quality_reason,
        related_entities=_related_txn_ids(result.related_transactions),
        metrics=metrics,
        provenance=Provenance(
            source_query=_SHARED_ENTITY_QUERY_MAP[result.entity_type],
            transaction_id=result.seed_transaction_id,
            entity_id=entity_id,
        ),
    )


def normalize_shared_email(result: SharedEmailActivity) -> Evidence:
    evidence_type = EvidenceType.SHARED_EMAIL_DOMAIN
    quality, quality_reason = QUALITY_BY_EVIDENCE_TYPE[evidence_type]

    p_count = len(result.via_purchaser_domain)
    r_count = len(result.via_recipient_domain)
    total = p_count + r_count

    parts = []
    if result.purchaser_domain:
        parts.append(
            f"{p_count} other transaction(s) share the purchaser email domain "
            f"'{result.purchaser_domain['id']}'"
        )
    if result.recipient_domain:
        parts.append(
            f"{r_count} other transaction(s) share the recipient email domain "
            f"'{result.recipient_domain['id']}'"
        )

    if not parts:
        observation = f"Transaction {result.seed_transaction_id} has no linked email domain."
        status = QueryStatus.EMPTY
    else:
        observation = "; ".join(parts) + "."
        status = QueryStatus.SUCCESS if total > 0 else QueryStatus.EMPTY

    interpretation = None
    if status == QueryStatus.SUCCESS:
        interpretation = f"{quality.value}-quality signal: {quality_reason}"

    related = _related_txn_ids(result.via_purchaser_domain) + _related_txn_ids(
        result.via_recipient_domain
    )

    primary_entity_id = None
    if result.purchaser_domain:
        primary_entity_id = result.purchaser_domain["id"]
    elif result.recipient_domain:
        primary_entity_id = result.recipient_domain["id"]

    return Evidence(
        evidence_id=evidence_id(result.seed_transaction_id, evidence_type, primary_entity_id),
        evidence_type=evidence_type,
        transaction_id=result.seed_transaction_id,
        status=status,
        observation=observation,
        interpretation=interpretation,
        quality=quality if status == QueryStatus.SUCCESS else SignalQuality.UNKNOWN,
        quality_reason=quality_reason,
        related_entities=sorted(set(related)),
        metrics={
            "purchaser_domain": result.purchaser_domain["id"] if result.purchaser_domain else None,
            "purchaser_related_count": p_count,
            "purchaser_related_fraud_count": _fraud_count(result.via_purchaser_domain),
            "recipient_domain": result.recipient_domain["id"] if result.recipient_domain else None,
            "recipient_related_count": r_count,
            "recipient_related_fraud_count": _fraud_count(result.via_recipient_domain),
        },
        provenance=Provenance(
            source_query="find_shared_email_activity",
            transaction_id=result.seed_transaction_id,
            entity_id=primary_entity_id,
        ),
    )


def network_pattern_quality(summary: NetworkSummary) -> tuple[SignalQuality, str]:
    """PROJECT-DERIVED INTERPRETATION, computed deterministically: the
    network summary's quality depends on *which* entity types actually
    contributed to the fan-out, not a fixed rating. If the low-quality
    types (Address, EmailDomain) account for the large majority of the
    related-transaction count, the overall pattern is flagged LOW -
    mirroring the real, measured finding that Address alone can dominate
    a transaction's apparent "network" (docs/phase-2-graph-analysis.md).
    """
    counts = summary.related_by_entity_type
    total = sum(counts.values())
    if total == 0:
        return SignalQuality.UNKNOWN, "No related transactions found via any linking entity."

    low_quality_types = ("Address", "EmailDomain_purchaser", "EmailDomain_recipient")
    low_quality_total = sum(counts.get(t, 0) for t in low_quality_types)
    low_quality_share = low_quality_total / total

    if low_quality_share >= 0.75:
        return (
            SignalQuality.LOW,
            f"{low_quality_share:.0%} of the {total} related transactions were reached only "
            "via low-quality linking types (Address and/or EmailDomain), which produce high, "
            "coincidental sharing in this dataset.",
        )
    if low_quality_share <= 0.25:
        return (
            SignalQuality.MEDIUM,
            f"Most of the {total} related transactions were reached via higher-quality "
            "linking types (Card and/or Device), not the noisier Address/EmailDomain signals.",
        )
    return (
        SignalQuality.MEDIUM,
        f"Related transactions were reached via a mix of linking types "
        f"({low_quality_share:.0%} through the lower-quality Address/EmailDomain signals).",
    )


def normalize_network_pattern(summary: NetworkSummary) -> Evidence:
    evidence_type = EvidenceType.NETWORK_PATTERN
    quality, quality_reason = network_pattern_quality(summary)

    total = summary.total_related
    if total == 0:
        observation = (
            f"Transaction {summary.seed_transaction_id} has no other transactions reachable "
            "within 2 hops through any linking entity."
        )
        status = QueryStatus.EMPTY
    else:
        breakdown = ", ".join(
            f"{v} via {k}" for k, v in sorted(summary.related_by_entity_type.items()) if v > 0
        )
        observation = (
            f"Transaction {summary.seed_transaction_id} reaches up to {total} related "
            f"transaction-slots within 2 hops ({breakdown}). Not deduplicated - the same "
            "transaction reachable via more than one entity type is counted once per type."
        )
        status = QueryStatus.SUCCESS

    interpretation = f"{quality.value}-quality pattern: {quality_reason}" if status == QueryStatus.SUCCESS else None

    return Evidence(
        evidence_id=evidence_id(summary.seed_transaction_id, evidence_type, None),
        evidence_type=evidence_type,
        transaction_id=summary.seed_transaction_id,
        status=status,
        observation=observation,
        interpretation=interpretation,
        quality=quality,
        quality_reason=quality_reason,
        related_entities=[],
        metrics={
            "linked_entities": dict(sorted(summary.linked_entities.items())),
            "related_by_entity_type": dict(sorted(summary.related_by_entity_type.items())),
            "total_related_not_deduplicated": total,
        },
        provenance=Provenance(
            source_query="investigate_transaction_network",
            transaction_id=summary.seed_transaction_id,
        ),
    )
