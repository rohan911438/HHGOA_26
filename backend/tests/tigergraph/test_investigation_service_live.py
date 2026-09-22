"""Phase 2D - live end-to-end test: the deterministic investigation
service against the real, loaded HHGOA_FRAUD graph.

    InvestigationService.investigate_transaction()
            v
    ToolRegistry.execute() x 6
            v
    EvidenceBundle
            v
    InvestigationSnapshot

Uses the same known fixture transaction as
tests/tigergraph/test_investigation_live.py, and asserts the snapshot's
numbers agree exactly with those already-verified Phase 2A/2B/2C
results. These expected values are verification expectations for the
current live graph, not something production logic hardcodes.

Skips (does not fail) when TigerGraph is not currently reachable, using
this project's own diagnostics - same convention as the other live test
modules in this package.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.evidence.models import EvidenceType, QueryStatus
from app.investigation import InvestigationService, build_default_registry
from app.investigation.snapshot import INVESTIGATION_PLAN, InvestigationStatus
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


@pytest.fixture(scope="module")
def service() -> InvestigationService:
    return InvestigationService(build_default_registry())


class TestLiveInvestigationEndToEnd:
    def test_sequential_investigation_matches_verified_phase_2a_2b_2c_numbers(self, service, client):
        snap = service.investigate_transaction(KNOWN_TXN, client, concurrent=False)

        assert snap.status == InvestigationStatus.COMPLETED
        assert snap.tools_executed == list(INVESTIGATION_PLAN)
        assert snap.warnings == []

        by_type = {e.evidence_type: e for e in snap.evidence_bundle.evidence}

        assert snap.transaction_context is not None
        assert snap.transaction_context.status == QueryStatus.SUCCESS

        card_ev = by_type[EvidenceType.SHARED_CARD]
        assert card_ev.provenance.entity_id == KNOWN_CARD_KEY
        assert card_ev.metrics["related_transaction_count"] == 12

        addr_ev = by_type[EvidenceType.SHARED_ADDRESS]
        assert addr_ev.provenance.entity_id == KNOWN_ADDRESS_KEY
        assert addr_ev.metrics["related_transaction_count"] == 312
        assert addr_ev.quality.value == "LOW"

        device_ev = by_type[EvidenceType.SHARED_DEVICE]
        assert device_ev.status == QueryStatus.EMPTY

        email_ev = by_type[EvidenceType.SHARED_EMAIL_DOMAIN]
        assert email_ev.metrics["purchaser_domain"] == "sbcglobal.net"
        assert email_ev.metrics["purchaser_related_count"] == 20

        network_ev = by_type[EvidenceType.NETWORK_PATTERN]
        breakdown = network_ev.metrics["related_by_entity_type"]
        assert breakdown["Card"] == 12
        assert breakdown["Address"] == 312
        assert breakdown["EmailDomain_purchaser"] == 20
        assert breakdown["Device"] == 0

    def test_concurrent_investigation_produces_the_same_evidence_as_sequential(self, service, client):
        snap = service.investigate_transaction(KNOWN_TXN, client, concurrent=True)
        assert snap.status == InvestigationStatus.COMPLETED
        assert snap.tools_executed == list(INVESTIGATION_PLAN)
        # Deterministic ordering must hold live too, not just with mocks.
        assert [entry.tool_name for entry in snap.execution_log] == list(INVESTIGATION_PLAN)

        by_type = {e.evidence_type: e for e in snap.evidence_bundle.evidence}
        assert by_type[EvidenceType.SHARED_CARD].metrics["related_transaction_count"] == 12
        assert by_type[EvidenceType.SHARED_ADDRESS].metrics["related_transaction_count"] == 312

    def test_no_fraud_verdict_anywhere_in_a_real_snapshot(self, service, client):
        snap = service.investigate_transaction(KNOWN_TXN, client)
        dumped = snap.model_dump(mode="json")
        assert "fraud_verdict" not in dumped
        assert "final_fraud_score" not in dumped
        # The dataset label is present, but only as a dataset fact, never
        # relabeled as a conclusion.
        assert snap.evidence_bundle.dataset_risk_score in (0.0, 1.0)

    def test_snapshot_round_trips_through_json(self, service, client):
        import json

        snap = service.investigate_transaction(KNOWN_TXN, client)
        json.dumps(snap.model_dump(mode="json"))  # must not raise
