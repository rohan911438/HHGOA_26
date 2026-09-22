"""Phase 2C - live integration test: the investigation tool registry
against the real, loaded HHGOA_FRAUD graph.

    ToolRegistry.execute()
            v
    one of the six approved InvestigationTools
            v
    Phase 2A query + Phase 2B normalizer
            v
    TigerGraph

Uses the same known fixture transaction as
tests/tigergraph/test_investigation_queries.py and
tests/tigergraph/test_evidence_live.py, and asserts the registry's
results agree exactly with those already-verified Phase 2A/2B numbers -
this proves the tool layer added in this phase introduces no drift on
top of already-verified code, not a restatement of the same test.

Every call in this file goes through `registry.execute()`, never through
`app.tigergraph.queries` or `app.evidence.aggregate` directly - that is
the property this phase exists to guarantee.

Skips (does not fail) when TigerGraph is not currently reachable, using
this project's own diagnostics - same convention as test_evidence_live.py.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.evidence.models import EvidenceType, QueryStatus
from app.investigation import build_default_registry
from app.investigation.registry import ToolNotRegisteredError
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
def registry():
    return build_default_registry()


class TestLiveRegistryEndToEnd:
    def test_transaction_context_through_the_registry(self, registry, client):
        res = registry.execute(
            "get_transaction_context", {"transaction_id": KNOWN_TXN}, client
        )
        assert res.status == QueryStatus.SUCCESS
        assert res.error is None
        assert res.evidence.evidence_type == EvidenceType.TRANSACTION_CONTEXT
        assert res.evidence.metrics["has_card"] is True
        assert res.evidence.metrics["has_address"] is True
        assert res.evidence.metrics["is_fraud_label"] in (True, False)
        assert res.latency_ms > 0

    def test_shared_card_through_the_registry_matches_phase_2a(self, registry, client):
        res = registry.execute(
            "find_shared_card_activity", {"transaction_id": KNOWN_TXN}, client
        )
        assert res.status == QueryStatus.SUCCESS
        assert res.evidence.provenance.entity_id == KNOWN_CARD_KEY
        assert res.evidence.metrics["related_transaction_count"] == 12

    def test_shared_device_through_the_registry_is_empty_not_error(self, registry, client):
        res = registry.execute(
            "find_shared_device_activity", {"transaction_id": KNOWN_TXN}, client
        )
        # This fixture transaction has no identity row - a real, verified
        # absence, not a failure (see test_evidence_live.py).
        assert res.status == QueryStatus.EMPTY
        assert res.error is None

    def test_shared_address_through_the_registry_preserves_low_quality(self, registry, client):
        res = registry.execute(
            "find_shared_address_activity", {"transaction_id": KNOWN_TXN}, client
        )
        assert res.status == QueryStatus.SUCCESS
        assert res.evidence.provenance.entity_id == KNOWN_ADDRESS_KEY
        assert res.evidence.metrics["related_transaction_count"] == 312
        assert res.evidence.quality.value == "LOW"

    def test_shared_email_through_the_registry(self, registry, client):
        res = registry.execute(
            "find_shared_email_activity", {"transaction_id": KNOWN_TXN}, client
        )
        assert res.status == QueryStatus.SUCCESS
        assert res.evidence.metrics["purchaser_domain"] == "sbcglobal.net"
        assert res.evidence.metrics["purchaser_related_count"] == 20

    def test_network_pattern_through_the_registry_matches_the_dedicated_tools(self, registry, client):
        network = registry.execute(
            "investigate_transaction_network", {"transaction_id": KNOWN_TXN}, client
        )
        card = registry.execute("find_shared_card_activity", {"transaction_id": KNOWN_TXN}, client)
        address = registry.execute("find_shared_address_activity", {"transaction_id": KNOWN_TXN}, client)
        email = registry.execute("find_shared_email_activity", {"transaction_id": KNOWN_TXN}, client)

        breakdown = network.evidence.metrics["related_by_entity_type"]
        assert breakdown["Card"] == card.evidence.metrics["related_transaction_count"]
        assert breakdown["Address"] == address.evidence.metrics["related_transaction_count"]
        assert breakdown["EmailDomain_purchaser"] == email.evidence.metrics["purchaser_related_count"]
        assert breakdown["Device"] == 0
        assert network.evidence.quality.value == "LOW"  # Address dominates, re-verified through the registry

    def test_all_six_tools_execute_through_the_registry_for_one_transaction(self, registry, client):
        names = [
            "get_transaction_context",
            "find_shared_card_activity",
            "find_shared_device_activity",
            "find_shared_address_activity",
            "find_shared_email_activity",
            "investigate_transaction_network",
        ]
        results = {name: registry.execute(name, {"transaction_id": KNOWN_TXN}, client) for name in names}
        errored = {name: r.error for name, r in results.items() if r.status == QueryStatus.ERROR}
        assert errored == {}, f"unexpected tool failures against a healthy graph: {errored}"

    def test_no_raw_gsql_tool_reachable_even_against_the_live_client(self, registry, client):
        with pytest.raises(ToolNotRegisteredError):
            registry.execute("execute_gsql", {"transaction_id": KNOWN_TXN}, client)

    def test_result_is_deterministic_across_two_live_calls(self, registry, client):
        res_a = registry.execute("find_shared_card_activity", {"transaction_id": KNOWN_TXN}, client)
        res_b = registry.execute("find_shared_card_activity", {"transaction_id": KNOWN_TXN}, client)
        assert res_a.status == res_b.status
        assert res_a.evidence.metrics == res_b.evidence.metrics
        assert res_a.evidence.evidence_id == res_b.evidence.evidence_id
