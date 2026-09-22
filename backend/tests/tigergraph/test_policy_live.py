"""Phase 2F - live pipeline test: the policy engine against a real
InvestigationSnapshot + UncertaintyAssessment from the loaded
HHGOA_FRAUD graph.

    InvestigationService (Phase 2D)
            v
    InvestigationSnapshot
            v
    UncertaintyEngine (Phase 2E)
            v
    UncertaintyAssessment
            v
    PolicyEngine.evaluate()          <- pure computation, no TigerGraph
            v
    PolicyDecision

Only the snapshot/assessment-producing half of this test needs
TigerGraph - PolicyEngine itself never touches it (see
tests/unit/test_policy_engine.py::TestArchitecturalBoundary for the
offline, TigerGraph-free proof). Marked `tigergraph` for the same reason
as the other live pipeline tests in this project.

Skips (does not fail) when TigerGraph is not currently reachable, using
this project's own diagnostics. Does not modify the existing live
investigation behavior - only reads the snapshot it produces.
"""

from __future__ import annotations

import time

import pytest

from app.config import get_settings
from app.investigation import InvestigationService, build_default_registry
from app.investigation.snapshot import InvestigationStatus
from app.policy import ApprovalRoute, PolicyEngine
from app.tigergraph.client import get_client
from app.tigergraph.diagnostics import all_passed, run_checks
from app.uncertainty import UncertaintyEngine

pytestmark = pytest.mark.tigergraph

KNOWN_TXN = "2987937"


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
def snapshot(client):
    service = InvestigationService(build_default_registry())
    return service.investigate_transaction(KNOWN_TXN, client)


@pytest.fixture(scope="module")
def assessment(snapshot):
    return UncertaintyEngine().assess(snapshot)


@pytest.fixture
def policy_engine() -> PolicyEngine:
    return PolicyEngine()


class TestLivePolicyPipeline:
    def test_snapshot_and_assessment_are_accepted(self, snapshot, assessment, policy_engine):
        assert snapshot.status == InvestigationStatus.COMPLETED
        decision = policy_engine.evaluate(snapshot, assessment)
        assert decision is not None

    def test_evidence_ids_trace_back_to_the_real_evidence_bundle(self, snapshot, assessment, policy_engine):
        decision = policy_engine.evaluate(snapshot, assessment)
        real_ids = {e.evidence_id for e in snapshot.evidence_bundle.evidence}
        assert set(decision.evidence_ids) <= real_ids
        assert set(decision.evidence_ids) == real_ids  # nothing dropped, nothing invented

    def test_no_dataset_label_leakage_on_a_real_decision(self, client, policy_engine):
        import copy

        service = InvestigationService(build_default_registry())
        snap = service.investigate_transaction(KNOWN_TXN, client)
        u = UncertaintyEngine().assess(snap)
        decision_real = policy_engine.evaluate(snap, u)

        real_label = snap.evidence_bundle.dataset_risk_score
        flipped = copy.deepcopy(snap)
        flipped_bundle = flipped.evidence_bundle.model_copy(
            update={"dataset_risk_score": 0.0 if real_label == 1.0 else 1.0}
        )
        flipped = flipped.model_copy(update={"evidence_bundle": flipped_bundle})
        u_flipped = UncertaintyEngine().assess(flipped)
        decision_flipped = policy_engine.evaluate(flipped, u_flipped)

        assert decision_real.model_dump(mode="json") == decision_flipped.model_dump(mode="json")

    def test_no_action_is_falsely_claimed_executed(self, snapshot, assessment, policy_engine):
        decision = policy_engine.evaluate(snapshot, assessment)
        assert decision.executable is False
        assert "no real-world action has been executed" in decision.rationale.lower()

    def test_decision_is_json_serializable(self, snapshot, assessment, policy_engine):
        import json

        json.dumps(policy_engine.evaluate(snapshot, assessment).model_dump(mode="json"))

    def test_recommendation_and_approval_are_reported_independently(self, snapshot, assessment, policy_engine):
        decision = policy_engine.evaluate(snapshot, assessment)
        assert isinstance(decision.approval_required, bool)
        if not decision.approval_required:
            assert decision.approval_route == ApprovalRoute.NONE

    def test_policy_basis_declares_the_development_heuristic_disclaimer(self, snapshot, assessment, policy_engine):
        decision = policy_engine.evaluate(snapshot, assessment)
        assert "PROJECT DEVELOPMENT HEURISTIC" in decision.policy_basis
        assert "NOT OFFICIAL HHGOA POLICY" in decision.policy_basis

    def test_engine_runtime_is_negligible_compared_to_the_investigation(self, snapshot, assessment, policy_engine):
        started = time.perf_counter()
        for _ in range(50):
            policy_engine.evaluate(snapshot, assessment)
        avg_ms = (time.perf_counter() - started) / 50 * 1000
        print(f"\nPolicyEngine.evaluate() avg latency over 50 runs: {avg_ms:.3f} ms")
        assert avg_ms < 50  # investigation itself measured in seconds (Phase 2D docs)


def test_live_example_report(snapshot, assessment, policy_engine):
    """Not a strict assertion test - prints the exact live example this
    phase's final report references, against the current graph state."""
    decision = policy_engine.evaluate(snapshot, assessment)
    print("\n--- Phase 2F live example: transaction", KNOWN_TXN, "---")
    print("uncertainty_level:", assessment.uncertainty_level.value)
    print("overall_uncertainty:", assessment.overall_uncertainty)
    print("recommended action:", decision.action.value)
    print("approval_required:", decision.approval_required)
    print("approval_route:", decision.approval_route.value)
    print("executable:", decision.executable)
    print("status:", decision.status.value)
    print("evidence_ids:", decision.evidence_ids)
    print("rationale:", decision.rationale)
    assert decision.executable is False
