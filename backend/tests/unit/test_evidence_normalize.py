"""Offline tests for app/evidence/normalize.py.

No TigerGraph, no network - every input here is a plain, hand-constructed
Phase 2A result object (the same dataclasses app/tigergraph/queries.py
returns from a live query). These tests prove the normalization logic
itself is correct, independent of whether the live graph is reachable.
"""

from __future__ import annotations

from app.evidence.models import EvidenceType, QueryStatus, SignalQuality
from app.evidence.normalize import (
    error_evidence,
    normalize_network_pattern,
    normalize_shared_email,
    normalize_shared_entity,
    normalize_transaction_context,
    network_pattern_quality,
)
from app.tigergraph.queries import (
    NetworkSummary,
    SharedEmailActivity,
    SharedEntityActivity,
    TransactionContext,
)


def txn(**overrides) -> dict:
    base = {
        "transaction_id": "T1",
        "is_fraud": False,
        "transaction_amt": 100.0,
        "transaction_dt": 12345,
        "product_cd": "W",
    }
    base.update(overrides)
    return base


class TestTransactionContextEvidence:
    def test_full_context_lists_every_linked_entity(self):
        ctx = TransactionContext(
            transaction_id="T1",
            attributes={"is_fraud": False, "transaction_amt": 50.0, "product_cd": "W"},
            card={"id": "card-1", "type": "Card"},
            address={"id": "addr-1", "type": "Address"},
            purchaser_email={"id": "gmail.com", "type": "EmailDomain"},
            recipient_email=None,
            device=None,
        )
        ev = normalize_transaction_context(ctx)
        assert ev.evidence_type == EvidenceType.TRANSACTION_CONTEXT
        assert ev.status == QueryStatus.SUCCESS
        assert "a card" in ev.observation and "an address" in ev.observation
        assert ev.metrics["has_card"] is True
        assert ev.metrics["has_device"] is False

    def test_quality_is_UNKNOWN_not_a_fabricated_rating(self):
        """Transaction context is descriptive, not a relationship signal -
        it must not receive an invented LOW/MEDIUM/HIGH quality."""
        ctx = TransactionContext(
            transaction_id="T1", attributes={}, card=None, address=None,
            purchaser_email=None, recipient_email=None, device=None,
        )
        ev = normalize_transaction_context(ctx)
        assert ev.quality == SignalQuality.UNKNOWN

    def test_no_linked_entities_says_so_plainly(self):
        ctx = TransactionContext(
            transaction_id="T1", attributes={}, card=None, address=None,
            purchaser_email=None, recipient_email=None, device=None,
        )
        ev = normalize_transaction_context(ctx)
        assert "no linked" in ev.observation.lower()


class TestSharedEntityEvidence:
    def test_shared_card_success_carries_counts_and_related_ids(self):
        result = SharedEntityActivity(
            entity_type="Card",
            seed_transaction_id="T1",
            entity={"id": "card-1", "type": "Card"},
            related_transactions=[txn(transaction_id="T2", is_fraud=True), txn(transaction_id="T3")],
        )
        ev = normalize_shared_entity(result)
        assert ev.evidence_type == EvidenceType.SHARED_CARD
        assert ev.status == QueryStatus.SUCCESS
        assert ev.metrics["related_transaction_count"] == 2
        assert ev.metrics["related_fraud_count"] == 1
        assert ev.related_entities == ["T2", "T3"]  # sorted, deterministic
        assert "prove" not in (ev.observation or "").lower()  # never a verdict

    def test_address_quality_is_explicitly_LOW(self):
        """The dataset-derived finding from Phase 2A, encoded as a fixed,
        documented quality rating - not assigned per-instance."""
        result = SharedEntityActivity(
            entity_type="Address",
            seed_transaction_id="T1",
            entity={"id": "addr-1", "type": "Address"},
            related_transactions=[txn(transaction_id=f"T{i}") for i in range(2, 15)],
        )
        ev = normalize_shared_entity(result)
        assert ev.evidence_type == EvidenceType.SHARED_ADDRESS
        assert ev.quality == SignalQuality.LOW
        assert "coarse" in ev.quality_reason.lower()

    def test_no_linked_entity_is_EMPTY_not_ERROR(self):
        result = SharedEntityActivity(
            entity_type="Device", seed_transaction_id="T1", entity=None, related_transactions=[]
        )
        ev = normalize_shared_entity(result)
        assert ev.status == QueryStatus.EMPTY
        assert ev.error is None

    def test_linked_entity_but_zero_sharers_is_EMPTY(self):
        result = SharedEntityActivity(
            entity_type="Card",
            seed_transaction_id="T1",
            entity={"id": "card-1", "type": "Card"},
            related_transactions=[],
        )
        ev = normalize_shared_entity(result)
        assert ev.status == QueryStatus.EMPTY
        assert ev.metrics["related_transaction_count"] == 0

    def test_provenance_names_the_real_source_query(self):
        result = SharedEntityActivity(
            entity_type="Device",
            seed_transaction_id="T1",
            entity={"id": "dev-1", "type": "Device"},
            related_transactions=[txn(transaction_id="T2")],
        )
        ev = normalize_shared_entity(result)
        assert ev.provenance.source == "tigergraph"
        assert ev.provenance.source_query == "find_shared_device_activity"
        assert ev.provenance.transaction_id == "T1"
        assert ev.provenance.entity_id == "dev-1"


class TestSharedEmailEvidence:
    def test_combines_purchaser_and_recipient_into_one_evidence_item(self):
        result = SharedEmailActivity(
            seed_transaction_id="T1",
            purchaser_domain={"id": "gmail.com", "type": "EmailDomain"},
            via_purchaser_domain=[txn(transaction_id="T2")],
            recipient_domain={"id": "yahoo.com", "type": "EmailDomain"},
            via_recipient_domain=[txn(transaction_id="T3", is_fraud=True)],
        )
        ev = normalize_shared_email(result)
        assert ev.evidence_type == EvidenceType.SHARED_EMAIL_DOMAIN
        assert ev.metrics["purchaser_related_count"] == 1
        assert ev.metrics["recipient_related_count"] == 1
        assert ev.metrics["recipient_related_fraud_count"] == 1
        assert set(ev.related_entities) == {"T2", "T3"}

    def test_email_quality_is_LOW(self):
        result = SharedEmailActivity(
            seed_transaction_id="T1",
            purchaser_domain={"id": "gmail.com", "type": "EmailDomain"},
            via_purchaser_domain=[txn(transaction_id="T2")],
        )
        ev = normalize_shared_email(result)
        assert ev.quality == SignalQuality.LOW

    def test_no_domain_at_all_is_EMPTY(self):
        result = SharedEmailActivity(seed_transaction_id="T1", purchaser_domain=None)
        ev = normalize_shared_email(result)
        assert ev.status == QueryStatus.EMPTY


class TestNetworkPatternEvidence:
    def test_low_quality_when_address_dominates(self):
        """Locks in the real Phase 2A finding: Card=12, Address=312,
        EmailDomain_purchaser=20 for one seed transaction - address
        alone is ~86% of the total, well past the 75% LOW threshold."""
        summary = NetworkSummary(
            seed_transaction_id="T1",
            linked_entities={"Card": 1, "Address": 1, "EmailDomain_purchaser": 1,
                              "EmailDomain_recipient": 0, "Device": 0},
            related_by_entity_type={"Card": 12, "Address": 312, "EmailDomain_purchaser": 20,
                                     "EmailDomain_recipient": 0, "Device": 0},
        )
        quality, reason = network_pattern_quality(summary)
        assert quality == SignalQuality.LOW
        assert "address" in reason.lower() or "emaildomain" in reason.lower()

        ev = normalize_network_pattern(summary)
        assert ev.quality == SignalQuality.LOW
        assert ev.metrics["total_related_not_deduplicated"] == 344

    def test_medium_quality_when_card_dominates(self):
        summary = NetworkSummary(
            seed_transaction_id="T1",
            linked_entities={"Card": 1, "Address": 0, "EmailDomain_purchaser": 0,
                              "EmailDomain_recipient": 0, "Device": 1},
            related_by_entity_type={"Card": 50, "Address": 0, "EmailDomain_purchaser": 0,
                                     "EmailDomain_recipient": 0, "Device": 5},
        )
        quality, _ = network_pattern_quality(summary)
        assert quality == SignalQuality.MEDIUM

    def test_no_related_transactions_is_UNKNOWN_quality_and_EMPTY_status(self):
        summary = NetworkSummary(
            seed_transaction_id="T1",
            linked_entities={"Card": 0, "Address": 0, "EmailDomain_purchaser": 0,
                              "EmailDomain_recipient": 0, "Device": 0},
            related_by_entity_type={"Card": 0, "Address": 0, "EmailDomain_purchaser": 0,
                                     "EmailDomain_recipient": 0, "Device": 0},
        )
        ev = normalize_network_pattern(summary)
        assert ev.status == QueryStatus.EMPTY
        assert ev.quality == SignalQuality.UNKNOWN


class TestErrorEvidence:
    def test_error_status_is_distinct_from_empty(self):
        ev = error_evidence(
            EvidenceType.SHARED_DEVICE, "T1", "find_shared_device_activity", ConnectionError("boom")
        )
        assert ev.status == QueryStatus.ERROR
        assert ev.status != QueryStatus.EMPTY
        assert ev.error is not None
        assert ev.error.error_type == "ConnectionError"
        assert ev.observation is None  # no fact is claimed when the query never ran

    def test_error_quality_is_UNKNOWN_never_a_guess(self):
        ev = error_evidence(EvidenceType.SHARED_CARD, "T1", "find_shared_card_activity", RuntimeError("x"))
        assert ev.quality == SignalQuality.UNKNOWN


class TestEvidenceIdDeterminism:
    def test_same_inputs_produce_the_same_evidence_id(self):
        result = SharedEntityActivity(
            entity_type="Card",
            seed_transaction_id="T1",
            entity={"id": "card-1", "type": "Card"},
            related_transactions=[txn(transaction_id="T2")],
        )
        ev_a = normalize_shared_entity(result)
        ev_b = normalize_shared_entity(result)
        assert ev_a.evidence_id == ev_b.evidence_id
        assert ev_a.evidence_id == "T1:shared_card:card-1"  # no UUID, no timestamp

    def test_different_entities_produce_different_ids(self):
        r1 = SharedEntityActivity("Card", "T1", {"id": "card-1", "type": "Card"}, [])
        r2 = SharedEntityActivity("Card", "T1", {"id": "card-2", "type": "Card"}, [])
        assert normalize_shared_entity(r1).evidence_id != normalize_shared_entity(r2).evidence_id
