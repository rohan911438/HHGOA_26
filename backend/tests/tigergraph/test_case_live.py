"""Phase 2G - live integration test: a complete case built from the real
InvestigationSnapshot + UncertaintyAssessment + PolicyDecision pipeline
against the loaded HHGOA_FRAUD graph.

    InvestigationService (Phase 2D)
            v
    InvestigationSnapshot
            v
    UncertaintyEngine (Phase 2E) -> UncertaintyAssessment
            v
    PolicyEngine (Phase 2F) -> PolicyDecision
            v
    CaseManager.create_case()          <- pure computation, no TigerGraph
            v
    CaseRecord

Only the pipeline through PolicyDecision needs TigerGraph -
CaseManager/CaseMemory themselves never touch it (see
tests/unit/test_case_management.py::TestArchitecturalBoundary). Does not
modify existing live investigation behavior - only consumes its output.
No values from Phase 2F's live result are hardcoded here; CaseManager
consumes whatever PolicyDecision it is actually given.

Skips (does not fail) when TigerGraph is not currently reachable, using
this project's own diagnostics.
"""

from __future__ import annotations

import time

import pytest

from app.case import CaseManager, CaseMemory, CaseStatus, CaseTrigger, InMemoryCaseStore
from app.config import get_settings
from app.investigation import InvestigationService, build_default_registry
from app.investigation.snapshot import InvestigationStatus
from app.policy import PolicyEngine
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
def pipeline(client):
    service = InvestigationService(build_default_registry())
    snapshot = service.investigate_transaction(KNOWN_TXN, client)
    assessment = UncertaintyEngine().assess(snapshot)
    decision = PolicyEngine().evaluate(snapshot, assessment)
    return snapshot, assessment, decision


@pytest.fixture
def store() -> InMemoryCaseStore:
    return InMemoryCaseStore()


@pytest.fixture
def manager(store) -> CaseManager:
    return CaseManager(store)


class TestLiveCasePipeline:
    def test_case_created_from_the_real_pipeline(self, pipeline, manager):
        snapshot, assessment, decision = pipeline
        assert snapshot.status == InvestigationStatus.COMPLETED

        case = manager.create_case(
            snapshot, uncertainty_assessment=assessment, policy_decision=decision, trigger=CaseTrigger.FRAUD_SIGNAL
        )
        assert case.status == CaseStatus.OPEN
        assert case.transaction_id == KNOWN_TXN
        assert case.investigation_id == snapshot.investigation_id
        assert case.policy_decision.action == decision.action  # whatever it actually is - not hardcoded here

    def test_traceability_from_case_back_to_evidence(self, pipeline, manager):
        snapshot, assessment, decision = pipeline
        case = manager.create_case(snapshot, uncertainty_assessment=assessment, policy_decision=decision)
        real_ids = {e.evidence_id for e in snapshot.evidence_bundle.evidence}
        assert set(case.evidence_ids) == real_ids
        assert set(decision.evidence_ids) <= real_ids

    def test_full_case_lifecycle_on_the_real_decision(self, pipeline, manager):
        snapshot, assessment, decision = pipeline
        case = manager.create_case(snapshot, uncertainty_assessment=assessment, policy_decision=decision)
        case = manager.add_system_recommendation(case.case_id, decision)
        case = manager.add_action_from_policy_decision(case.case_id, decision)
        case = manager.transition_status(case.case_id, CaseStatus.INVESTIGATING)
        case = manager.transition_status(case.case_id, CaseStatus.ACTION_RECOMMENDED)
        case = manager.transition_status(case.case_id, CaseStatus.PENDING_REVIEW)
        case = manager.transition_status(case.case_id, CaseStatus.CLOSED)
        assert case.status == CaseStatus.CLOSED
        assert case.actions[0].action == decision.action
        assert case.actions[0].executed is False  # recommendation only - never auto-executed

    def test_case_record_is_json_serializable(self, pipeline, manager):
        import json

        snapshot, assessment, decision = pipeline
        case = manager.create_case(snapshot, uncertainty_assessment=assessment, policy_decision=decision)
        json.dumps(case.model_dump(mode="json"))

    def test_case_memory_retrieval_works_on_a_real_case(self, pipeline, store):
        snapshot, assessment, decision = pipeline
        manager = CaseManager(store)
        case = manager.create_case(snapshot, uncertainty_assessment=assessment, policy_decision=decision)

        memory = CaseMemory(store)
        # Empty memory except the case itself - must not error.
        assert memory.retrieve_similar(case) == []
        assert memory.retrieve_by_transaction(KNOWN_TXN) == [case]

    def test_performance_is_negligible_versus_the_investigation(self, pipeline, store):
        snapshot, assessment, decision = pipeline
        manager = CaseManager(store)

        started = time.perf_counter()
        for i in range(20):
            manager.create_case(snapshot, uncertainty_assessment=assessment, policy_decision=decision)
        create_avg_ms = (time.perf_counter() - started) / 20 * 1000

        case = manager.create_case(snapshot, uncertainty_assessment=assessment, policy_decision=decision)
        started = time.perf_counter()
        for _ in range(20):
            manager.add_finding(case.case_id, description="synthetic", evidence_ids=case.evidence_ids[:1])
        update_avg_ms = (time.perf_counter() - started) / 20 * 1000

        started = time.perf_counter()
        for _ in range(20):
            case.model_dump(mode="json")
        serialize_avg_ms = (time.perf_counter() - started) / 20 * 1000

        memory = CaseMemory(store)
        started = time.perf_counter()
        for _ in range(20):
            memory.retrieve_similar(case)
        retrieve_avg_ms = (time.perf_counter() - started) / 20 * 1000

        print(f"\ncase creation avg: {create_avg_ms:.3f} ms")
        print(f"case update (add_finding) avg: {update_avg_ms:.3f} ms")
        print(f"case serialization avg: {serialize_avg_ms:.3f} ms")
        print(f"case memory retrieval avg: {retrieve_avg_ms:.3f} ms")
        assert create_avg_ms < 50
        assert update_avg_ms < 50
        assert serialize_avg_ms < 50
        assert retrieve_avg_ms < 50


def test_live_example_report(pipeline, manager):
    """Not a strict assertion test - prints the exact live example this
    phase's final report references, against the current graph state."""
    snapshot, assessment, decision = pipeline
    case = manager.create_case(
        snapshot, uncertainty_assessment=assessment, policy_decision=decision, trigger=CaseTrigger.FRAUD_SIGNAL
    )
    print("\n--- Phase 2G live example: transaction", KNOWN_TXN, "---")
    print("case_id:", case.case_id)
    print("case status:", case.status.value)
    print("evidence_ids:", case.evidence_ids)
    print("uncertainty_level:", assessment.uncertainty_level.value)
    print("policy action:", decision.action.value)
    print("approval_required:", decision.approval_required)
    print("approval_route:", decision.approval_route.value)
    print("executable:", decision.executable)
    assert case.investigation_id == snapshot.investigation_id
