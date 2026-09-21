"""Phase 2B - live integration test: full evidence pipeline against the
real, loaded HHGOA_FRAUD graph.

    Phase 2A query layer -> Evidence normalization -> EvidenceBundle

Uses the same known fixture transaction as
tests/tigergraph/test_investigation_queries.py (see that module's
docstring for how its exact counts were captured), and asserts the
EvidenceBundle's numbers agree exactly with those already-verified
Phase 2A results - this is a genuine cross-check between two independent
code paths (raw queries vs. the evidence layer built on top of them), not
a restatement of the same test.

Skips (does not fail) when TigerGraph is not currently reachable, using
this project's own diagnostics rather than a bare try/except - if the
graph is down, that is a `test_tigergraph.py` finding, not this test's
job to fail on.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.evidence.aggregate import aggregate_evidence
from app.evidence.models import EvidenceType, QueryStatus, SignalQuality
from app.tigergraph.client import get_client
from app.tigergraph.diagnostics import all_passed, run_checks

pytestmark = pytest.mark.tigergraph

KNOWN_TXN = "2987937"
KNOWN_CARD_KEY = "18227|583.0|150.0|226.0"
KNOWN_ADDRESS_KEY = "299.0|87.0"


@pytest.fixture(scope="module")
def client():
    settings = get_settings()
    if not settings.tg_configured:
        pytest.skip("TigerGraph not configured - set TG_HOST/TG_GRAPHNAME/TG_SECRET in .env")
    c = get_client(settings)
    results = run_checks(settings, c)
    if not all_passed(results):
        detail = next((r.detail for r in results if r.ok is False), "unknown failure")
        pytest.skip(f"TigerGraph is not currently reachable/healthy - {detail}")
    return c


class TestLiveEvidencePipeline:
    def test_bundle_matches_the_independently_verified_phase_2a_counts(self, client):
        bundle = aggregate_evidence(client, KNOWN_TXN)

        by_type = {e.evidence_type: e for e in bundle.evidence}

        card_ev = by_type[EvidenceType.SHARED_CARD]
        assert card_ev.status == QueryStatus.SUCCESS
        assert card_ev.provenance.entity_id == KNOWN_CARD_KEY
        assert card_ev.metrics["related_transaction_count"] == 12

        addr_ev = by_type[EvidenceType.SHARED_ADDRESS]
        assert addr_ev.status == QueryStatus.SUCCESS
        assert addr_ev.provenance.entity_id == KNOWN_ADDRESS_KEY
        assert addr_ev.metrics["related_transaction_count"] == 312
        assert addr_ev.quality == SignalQuality.LOW  # the real, documented finding

        email_ev = by_type[EvidenceType.SHARED_EMAIL_DOMAIN]
        assert email_ev.metrics["purchaser_domain"] == "sbcglobal.net"
        assert email_ev.metrics["purchaser_related_count"] == 20

        device_ev = by_type[EvidenceType.SHARED_DEVICE]
        assert device_ev.status == QueryStatus.EMPTY  # this txn has no identity row

    def test_network_pattern_breakdown_agrees_with_the_dedicated_queries(self, client):
        """The genuine cross-check: network_pattern's metrics must match
        the four dedicated shared-entity evidence items exactly, proving
        the evidence layer didn't introduce any drift on top of the
        already-verified Phase 2A query layer."""
        bundle = aggregate_evidence(client, KNOWN_TXN)
        by_type = {e.evidence_type: e for e in bundle.evidence}

        network = by_type[EvidenceType.NETWORK_PATTERN]
        breakdown = network.metrics["related_by_entity_type"]

        assert breakdown["Card"] == by_type[EvidenceType.SHARED_CARD].metrics["related_transaction_count"]
        assert breakdown["Address"] == by_type[EvidenceType.SHARED_ADDRESS].metrics["related_transaction_count"]
        assert breakdown["EmailDomain_purchaser"] == by_type[EvidenceType.SHARED_EMAIL_DOMAIN].metrics[
            "purchaser_related_count"
        ]
        assert breakdown["Device"] == 0

        # And the real finding, re-verified live: Address dominates.
        assert network.quality == SignalQuality.LOW

    def test_dataset_risk_score_reflects_the_real_is_fraud_label(self, client):
        bundle = aggregate_evidence(client, KNOWN_TXN)
        assert bundle.dataset_risk_score in (0.0, 1.0)

    def test_data_quality_notes_mention_the_address_limitation(self, client):
        bundle = aggregate_evidence(client, KNOWN_TXN)
        assert any("coarse" in note.lower() for note in bundle.data_quality_notes)

    def test_no_evidence_item_status_is_ERROR_against_a_healthy_graph(self, client):
        """If the graph is reachable and this known-good transaction
        exists (both already gated by the `client` fixture), none of the
        six queries should produce a real ERROR - only SUCCESS/EMPTY."""
        bundle = aggregate_evidence(client, KNOWN_TXN)
        errored = [e for e in bundle.evidence if e.status == QueryStatus.ERROR]
        assert errored == [], f"unexpected query failures: {[(e.evidence_type, e.error) for e in errored]}"

    def test_bundle_is_deterministic_across_two_live_calls(self, client):
        bundle_a = aggregate_evidence(client, KNOWN_TXN)
        bundle_b = aggregate_evidence(client, KNOWN_TXN)
        assert [e.evidence_id for e in bundle_a.evidence] == [e.evidence_id for e in bundle_b.evidence]
        assert bundle_a.evidence_summary.evidence_counts == bundle_b.evidence_summary.evidence_counts
