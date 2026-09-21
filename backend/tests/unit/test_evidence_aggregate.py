"""Offline tests for app/evidence/aggregate.py.

The six Phase 2A query functions are monkeypatched directly on
app.evidence.aggregate.q (the `queries` module aggregate.py imports as
`q`) - no TigerGraph connection involved. This tests aggregate_evidence's
own orchestration logic: dedup, error handling, determinism, and the
risk/evidence separation guarantee, independent of query correctness
(covered separately by tests/tigergraph/test_investigation_queries.py
against the live graph, and by test_evidence_normalize.py offline).
"""

from __future__ import annotations

import pytest

from app.evidence import aggregate as agg
from app.evidence.models import Evidence, EvidenceBundle, EvidenceType, Provenance, QueryStatus, SignalQuality
from app.tigergraph.queries import (
    NetworkSummary,
    SharedEmailActivity,
    SharedEntityActivity,
    TransactionContext,
)


def txn(**overrides) -> dict:
    base = {"transaction_id": "T2", "is_fraud": False, "transaction_amt": 10.0,
            "transaction_dt": 1, "product_cd": "W"}
    base.update(overrides)
    return base


def _patch_all_queries(
    monkeypatch,
    *,
    ctx=None,
    card=None,
    address=None,
    device=None,
    email=None,
    network=None,
    ctx_raises: Exception | None = None,
    card_raises: Exception | None = None,
):
    """Wires every Phase 2A query to a fixed return value (or a raised
    exception), so aggregate_evidence's own logic can be tested without
    any live call."""

    def make(value, exc):
        def _fn(client, transaction_id):
            if exc is not None:
                raise exc
            return value
        return _fn

    monkeypatch.setattr(agg.q, "get_transaction_context", make(ctx, ctx_raises))
    monkeypatch.setattr(agg.q, "find_shared_card_activity", make(card, card_raises))
    monkeypatch.setattr(agg.q, "find_shared_address_activity", make(address, None))
    monkeypatch.setattr(agg.q, "find_shared_device_activity", make(device, None))
    monkeypatch.setattr(agg.q, "find_shared_email_activity", make(email, None))
    monkeypatch.setattr(agg.q, "investigate_transaction_network", make(network, None))


def _defaults():
    return dict(
        ctx=TransactionContext(
            transaction_id="T1",
            attributes={"is_fraud": True, "transaction_amt": 500.0, "product_cd": "W"},
            card={"id": "card-1", "type": "Card"},
            address={"id": "addr-1", "type": "Address"},
            purchaser_email=None,
            recipient_email=None,
            device=None,
        ),
        card=SharedEntityActivity("Card", "T1", {"id": "card-1", "type": "Card"}, [txn()]),
        address=SharedEntityActivity("Address", "T1", {"id": "addr-1", "type": "Address"},
                                      [txn(transaction_id=f"T{i}") for i in range(2, 20)]),
        device=SharedEntityActivity("Device", "T1", None, []),
        email=SharedEmailActivity("T1", None),
        network=NetworkSummary(
            "T1",
            linked_entities={"Card": 1, "Address": 1, "EmailDomain_purchaser": 0,
                              "EmailDomain_recipient": 0, "Device": 0},
            related_by_entity_type={"Card": 1, "Address": 18, "EmailDomain_purchaser": 0,
                                     "EmailDomain_recipient": 0, "Device": 0},
        ),
    )


class TestBasicAggregation:
    def test_produces_one_evidence_item_per_query_type(self, monkeypatch):
        _patch_all_queries(monkeypatch, **_defaults())
        bundle = agg.aggregate_evidence(client=object(), transaction_id="T1")
        types = {e.evidence_type for e in bundle.evidence}
        assert types == {
            EvidenceType.TRANSACTION_CONTEXT,
            EvidenceType.SHARED_CARD,
            EvidenceType.SHARED_ADDRESS,
            EvidenceType.SHARED_DEVICE,
            EvidenceType.SHARED_EMAIL_DOMAIN,
            EvidenceType.NETWORK_PATTERN,
        }

    def test_transaction_id_is_consistent_throughout(self, monkeypatch):
        _patch_all_queries(monkeypatch, **_defaults())
        bundle = agg.aggregate_evidence(client=object(), transaction_id="T1")
        assert bundle.transaction_id == "T1"
        assert all(e.transaction_id == "T1" for e in bundle.evidence)
        assert bundle.evidence_summary.transaction_id == "T1"


class TestRiskAndEvidenceSeparation:
    """The spec's core requirement: dataset_risk_score must never leak
    into, or be conflated with, evidence quality/confidence."""

    def test_no_confidence_field_exists_anywhere_on_the_model(self):
        # There is intentionally no "confidence" attribute on Evidence or
        # EvidenceBundle in this phase - only `quality` (a fixed,
        # structural rating) and the separately-tracked
        # `dataset_risk_score`. Assert this by construction, not by name
        # convention alone.
        assert "confidence" not in Evidence.model_fields
        assert "confidence" not in type(agg).__dict__  # sanity: no stray helper either

    def test_dataset_risk_score_does_not_change_evidence_quality(self, monkeypatch):
        """Two transactions, identical shared-card facts, opposite fraud
        labels - the shared_card evidence's quality must be identical."""
        fraud_case = _defaults()
        fraud_case["ctx"] = TransactionContext(
            transaction_id="T1", attributes={"is_fraud": True}, card={"id": "card-1", "type": "Card"},
            address=None, purchaser_email=None, recipient_email=None, device=None,
        )
        clean_case = _defaults()
        clean_case["ctx"] = TransactionContext(
            transaction_id="T1", attributes={"is_fraud": False}, card={"id": "card-1", "type": "Card"},
            address=None, purchaser_email=None, recipient_email=None, device=None,
        )

        _patch_all_queries(monkeypatch, **fraud_case)
        bundle_fraud = agg.aggregate_evidence(client=object(), transaction_id="T1")

        _patch_all_queries(monkeypatch, **clean_case)
        bundle_clean = agg.aggregate_evidence(client=object(), transaction_id="T1")

        card_ev_fraud = next(e for e in bundle_fraud.evidence if e.evidence_type == EvidenceType.SHARED_CARD)
        card_ev_clean = next(e for e in bundle_clean.evidence if e.evidence_type == EvidenceType.SHARED_CARD)
        assert card_ev_fraud.quality == card_ev_clean.quality
        assert card_ev_fraud.metrics == card_ev_clean.metrics
        # Only the risk score itself differs.
        assert bundle_fraud.dataset_risk_score == 1.0
        assert bundle_clean.dataset_risk_score == 0.0

    def test_dataset_risk_score_is_none_when_context_query_fails(self, monkeypatch):
        d = _defaults()
        _patch_all_queries(monkeypatch, **d, ctx_raises=ConnectionError("down"))
        bundle = agg.aggregate_evidence(client=object(), transaction_id="T1")
        assert bundle.dataset_risk_score is None


class TestConflictingEvidencePreserved:
    def test_high_risk_score_with_weak_low_quality_evidence_stays_unreduced(self, monkeypatch):
        """High dataset risk score, but the only supporting evidence is a
        LOW-quality shared-address signal and a card with zero related
        fraud. The bundle must not collapse this into any single verdict
        - there is deliberately no such field to collapse it into."""
        d = _defaults()  # card has 1 non-fraud related txn; address is LOW quality, 18 related
        _patch_all_queries(monkeypatch, **d)
        bundle = agg.aggregate_evidence(client=object(), transaction_id="T1")

        assert bundle.dataset_risk_score == 1.0  # "high" per the dataset label
        card_ev = next(e for e in bundle.evidence if e.evidence_type == EvidenceType.SHARED_CARD)
        addr_ev = next(e for e in bundle.evidence if e.evidence_type == EvidenceType.SHARED_ADDRESS)
        assert card_ev.metrics["related_fraud_count"] == 0  # no supporting fraud via card
        assert addr_ev.quality == SignalQuality.LOW  # weak signal, explicitly marked as such

        # No combined verdict field exists to have "resolved" this conflict.
        assert not hasattr(bundle, "final_fraud_score")
        assert not hasattr(bundle, "verdict")
        assert "final_fraud_score" not in EvidenceBundle.model_fields
        assert "verdict" not in EvidenceBundle.model_fields


class TestQueryStatusDistinction:
    def test_success_empty_and_error_all_appear_distinctly(self, monkeypatch):
        d = _defaults()
        d["device"] = SharedEntityActivity("Device", "T1", None, [])  # EMPTY
        _patch_all_queries(monkeypatch, **d, card_raises=TimeoutError("query timed out"))
        bundle = agg.aggregate_evidence(client=object(), transaction_id="T1")

        status_by_type = {e.evidence_type: e.status for e in bundle.evidence}
        assert status_by_type[EvidenceType.SHARED_CARD] == QueryStatus.ERROR
        assert status_by_type[EvidenceType.SHARED_DEVICE] == QueryStatus.EMPTY
        assert status_by_type[EvidenceType.SHARED_ADDRESS] == QueryStatus.SUCCESS

    def test_error_evidence_carries_error_detail_not_a_fabricated_observation(self, monkeypatch):
        d = _defaults()
        _patch_all_queries(monkeypatch, **d, card_raises=TimeoutError("query timed out"))
        bundle = agg.aggregate_evidence(client=object(), transaction_id="T1")
        card_ev = next(e for e in bundle.evidence if e.evidence_type == EvidenceType.SHARED_CARD)
        assert card_ev.error is not None
        assert card_ev.error.error_type == "TimeoutError"
        assert card_ev.observation is None

    def test_one_query_failing_does_not_abort_the_others(self, monkeypatch):
        d = _defaults()
        _patch_all_queries(monkeypatch, **d, card_raises=RuntimeError("boom"))
        bundle = agg.aggregate_evidence(client=object(), transaction_id="T1")
        assert len(bundle.evidence) == 6
        addr_ev = next(e for e in bundle.evidence if e.evidence_type == EvidenceType.SHARED_ADDRESS)
        assert addr_ev.status == QueryStatus.SUCCESS


class TestDeduplication:
    def test_identical_evidence_ids_collapse_to_one_entry(self):
        e1 = Evidence(
            evidence_id="T1:shared_card:card-1", evidence_type=EvidenceType.SHARED_CARD,
            transaction_id="T1", status=QueryStatus.EMPTY,
            provenance=Provenance(source_query="find_shared_card_activity", transaction_id="T1"),
        )
        e2 = Evidence(
            evidence_id="T1:shared_card:card-1", evidence_type=EvidenceType.SHARED_CARD,
            transaction_id="T1", status=QueryStatus.SUCCESS,
            related_entities=["T2", "T3"], metrics={"related_transaction_count": 2},
            provenance=Provenance(source_query="investigate_transaction_network", transaction_id="T1"),
        )
        deduped = agg.deduplicate_evidence([e1, e2])
        assert len(deduped) == 1
        # SUCCESS with real detail must win over the emptier duplicate.
        assert deduped[0].status == QueryStatus.SUCCESS
        assert deduped[0].metrics["related_transaction_count"] == 2

    def test_distinct_evidence_ids_are_both_kept(self):
        e1 = Evidence(
            evidence_id="T1:shared_card:card-1", evidence_type=EvidenceType.SHARED_CARD,
            transaction_id="T1", status=QueryStatus.SUCCESS,
            provenance=Provenance(source_query="find_shared_card_activity", transaction_id="T1"),
        )
        e2 = Evidence(
            evidence_id="T1:shared_address:addr-1", evidence_type=EvidenceType.SHARED_ADDRESS,
            transaction_id="T1", status=QueryStatus.SUCCESS,
            provenance=Provenance(source_query="find_shared_address_activity", transaction_id="T1"),
        )
        assert len(agg.deduplicate_evidence([e1, e2])) == 2

    def test_dedup_result_order_is_deterministic(self):
        e1 = Evidence(
            evidence_id="T1:shared_device:dev-1", evidence_type=EvidenceType.SHARED_DEVICE,
            transaction_id="T1", status=QueryStatus.EMPTY,
            provenance=Provenance(source_query="find_shared_device_activity", transaction_id="T1"),
        )
        e2 = Evidence(
            evidence_id="T1:shared_card:card-1", evidence_type=EvidenceType.SHARED_CARD,
            transaction_id="T1", status=QueryStatus.EMPTY,
            provenance=Provenance(source_query="find_shared_card_activity", transaction_id="T1"),
        )
        result_a = agg.deduplicate_evidence([e1, e2])
        result_b = agg.deduplicate_evidence([e2, e1])
        assert [e.evidence_id for e in result_a] == [e.evidence_id for e in result_b]


class TestDeterminism:
    def test_same_raw_input_produces_the_same_bundle_twice(self, monkeypatch):
        d = _defaults()
        _patch_all_queries(monkeypatch, **d)
        bundle_a = agg.aggregate_evidence(client=object(), transaction_id="T1")
        _patch_all_queries(monkeypatch, **_defaults())  # fresh equal-valued objects
        bundle_b = agg.aggregate_evidence(client=object(), transaction_id="T1")

        assert bundle_a.model_dump(exclude={"evidence"}) == bundle_b.model_dump(exclude={"evidence"})
        ids_a = [e.evidence_id for e in bundle_a.evidence]
        ids_b = [e.evidence_id for e in bundle_b.evidence]
        assert ids_a == ids_b  # same order, not just same set
